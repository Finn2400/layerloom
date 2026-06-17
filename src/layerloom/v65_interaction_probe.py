"""Visible Qt/VTK interaction probe for LayerLoom v65.

This is a developer diagnostic for the v65 viewer interaction layer. It uses a
real visible Qt window, explicit Qt mouse events, and PyVista/VTK framebuffer
screenshots. Do not run it with Qt's offscreen platform; VTK's Cocoa render
window can crash before LayerLoom interaction code is reached.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time
import traceback
from pathlib import Path

import numpy as np


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", nargs="?", help="Optional 3MF path. Defaults to the packaged GUI example.")
    parser.add_argument("--cycles", type=int, default=3, help="Move/camera/rotate cycles to run.")
    parser.add_argument(
        "--screenshots-dir",
        default="/private/tmp/layerloom_v65_interaction_probe",
        help="Directory for VTK framebuffer screenshots.",
    )
    parser.add_argument("--no-screenshots", action="store_true", help="Skip VTK screenshots.")
    parser.add_argument("--profile", action="store_true", help="Enable LAYERLOOM_VIEWER_PROFILE logging.")
    parser.add_argument("--width", type=int, default=1300, help="Initial window width.")
    parser.add_argument("--height", type=int, default=900, help="Initial window height.")
    return parser.parse_args(argv)


def _load_v65_module():
    module_path = Path(__file__).resolve().with_name("3mf_gui_v65.py")
    spec = importlib.util.spec_from_file_location("layerloom_gui_v65_probe_runtime", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load v65 module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _process_events(app, *, ms: int = 20) -> None:
    deadline = time.time() + max(float(ms), 0.0) / 1000.0
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.002)


def _qpointf(qtcore, point) -> object:
    point = np.asarray(point, dtype=np.float64).reshape(2)
    return qtcore.QPointF(float(point[0]), float(point[1]))


def _send_mouse_event(app, qtcore, qtgui, widget, event_type, pos, button, buttons) -> None:
    qpos = _qpointf(qtcore, pos)
    event = qtgui.QMouseEvent(event_type, qpos, qpos, qpos, button, buttons, qtcore.Qt.NoModifier)
    app.sendEvent(widget, event)
    _process_events(app, ms=8)


def _drag(app, qtcore, qtgui, widget, start, end, *, steps: int = 10) -> None:
    start = np.asarray(start, dtype=np.float64).reshape(2)
    end = np.asarray(end, dtype=np.float64).reshape(2)
    _send_mouse_event(
        app,
        qtcore,
        qtgui,
        widget,
        qtcore.QEvent.MouseButtonPress,
        start,
        qtcore.Qt.LeftButton,
        qtcore.Qt.LeftButton,
    )
    for index in range(1, max(int(steps), 1) + 1):
        pos = start + (end - start) * (index / max(int(steps), 1))
        _send_mouse_event(
            app,
            qtcore,
            qtgui,
            widget,
            qtcore.QEvent.MouseMove,
            pos,
            qtcore.Qt.NoButton,
            qtcore.Qt.LeftButton,
        )
    _send_mouse_event(
        app,
        qtcore,
        qtgui,
        widget,
        qtcore.QEvent.MouseButtonRelease,
        end,
        qtcore.Qt.LeftButton,
        qtcore.Qt.NoButton,
    )
    _process_events(app, ms=30)


def _actor_matrix(viewer, actor) -> np.ndarray:
    try:
        matrix = viewer._actor_user_matrix(actor)
    except Exception:
        matrix = None
    if matrix is None:
        return np.eye(4, dtype=np.float64)
    return np.asarray(matrix, dtype=np.float64).reshape(4, 4)


def _transform_point(matrix: np.ndarray, point) -> np.ndarray:
    homogeneous = matrix @ np.asarray([point[0], point[1], point[2], 1.0], dtype=np.float64)
    return homogeneous[:3] / max(abs(float(homogeneous[3])), 1e-12)


def _first_model_hit_qt(viewer) -> np.ndarray:
    for _name, actor in viewer.actors_by_name.items():
        matrix = _actor_matrix(viewer, actor)
        points = actor.GetMapper().GetInput().GetPoints()
        count = min(int(points.GetNumberOfPoints()), 900)
        step = max(count // 120, 1)
        for index in range(0, count, step):
            world = _transform_point(matrix, np.asarray(points.GetPoint(index), dtype=np.float64))
            qt_pos = np.asarray(viewer._display_pos_qt(world)[:2], dtype=np.float64)
            vtk_pos = viewer._qt_to_vtk_display(qt_pos)
            picked_name = viewer._pick_model_at_vtk_display_exact(vtk_pos)[0]
            if picked_name is not None:
                return qt_pos
    raise RuntimeError("Could not find a confirmed pickable model surface point.")


def _first_empty_qt(viewer) -> np.ndarray:
    width = int(viewer.plotter.width())
    height = int(viewer.plotter.height())
    for y in np.linspace(height * 0.1, height * 0.9, 6):
        for x in np.linspace(width * 0.1, width * 0.9, 6):
            vtk_pos = viewer._qt_to_vtk_display((x, y))
            picked_name = viewer._pick_model_at_vtk_display_exact(vtk_pos)[0]
            if picked_name is None:
                return np.asarray([x, y], dtype=np.float64)
    return np.asarray([width * 0.1, height * 0.1], dtype=np.float64)


def _first_ring_qt(viewer) -> np.ndarray:
    for axis in ("z", "x", "y"):
        _world_points, display_points = viewer._sample_gizmo_ring_display(axis, samples=144)
        for candidate in display_points:
            axis_name, _pick, _dist = viewer._pick_gizmo_ring_at_display(
                candidate,
                tolerance_px=viewer.ROTATION_RING_PRESS_TOLERANCE_PX,
            )
            if axis_name is not None:
                return np.asarray(candidate, dtype=np.float64)
    raise RuntimeError("Could not find a confirmed pickable gimbal ring point.")


def _camera_state(viewer) -> np.ndarray:
    camera = viewer.plotter.camera
    return np.asarray(list(camera.position) + list(camera.focal_point) + list(camera.up), dtype=np.float64)


def _current_xy(window, viewer) -> np.ndarray:
    target = getattr(window, "pending_target_center_xy", None)
    if target is not None:
        return np.asarray(target, dtype=np.float64).reshape(2).copy()
    plan = getattr(window, "_last_transform_plan", None)
    if plan is not None:
        return np.asarray(plan.transformed_bounds.center[:2], dtype=np.float64).copy()
    center, _radius = window._viewer_object_center_and_radius()
    return np.asarray(center[:2], dtype=np.float64).copy()


def _save_screenshot(viewer, screenshot_dir: Path | None, name: str) -> None:
    if screenshot_dir is None:
        return
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    path = screenshot_dir / f"{name}.png"
    viewer.plotter.screenshot(str(path))
    print(f"probe: screenshot={path}", flush=True)


def _load_model(window, gui_module, model_path: str | None) -> None:
    if model_path:
        path = Path(model_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        window._pending_source_hex_by_name = {}
        stamped = gui_module._V58._stamp_ids_only(str(path))
        window._load_canonical_model(stamped)
    else:
        window._on_load_example()


def _run_probe(args: argparse.Namespace) -> int:
    if os.environ.get("QT_QPA_PLATFORM", "").strip().lower() == "offscreen":
        raise RuntimeError("Do not run this probe with QT_QPA_PLATFORM=offscreen; use a visible Qt session.")
    if args.profile:
        os.environ["LAYERLOOM_VIEWER_PROFILE"] = "1"

    gui_module = _load_v65_module()
    qtwidgets = gui_module._V58.QtWidgets
    qtcore = gui_module._V58.QtCore
    qtgui = gui_module._V58.QtGui
    app = qtwidgets.QApplication.instance() or qtwidgets.QApplication([])
    window = gui_module.QtAssignColorsApp()
    window.resize(int(args.width), int(args.height))
    window.show()

    screenshot_dir = None if args.no_screenshots else Path(args.screenshots_dir)

    def run() -> None:
        try:
            print("probe: load", flush=True)
            _load_model(window, gui_module, args.model)
            _process_events(app, ms=250)
            window._on_preview()
            _process_events(app, ms=250)

            viewer = window.viewer
            if viewer is None or not getattr(viewer, "actors_by_name", None):
                raise RuntimeError("Viewer did not build any actors.")
            print(
                f"probe: ready actors={len(viewer.actors_by_name)} "
                f"size={viewer.plotter.width()}x{viewer.plotter.height()} rss={viewer._profile_rss_mb():.1f}MB",
                flush=True,
            )
            _save_screenshot(viewer, screenshot_dir, "loaded")

            total_camera_delta = 0.0
            cycles = max(int(args.cycles), 1)
            for cycle in range(1, cycles + 1):
                window._set_viewer_tool_mode("move")
                _process_events(app, ms=40)
                model_start = _first_model_hit_qt(viewer)
                xy_before = _current_xy(window, viewer)
                _drag(
                    app,
                    qtcore,
                    qtgui,
                    viewer.plotter,
                    model_start,
                    model_start + np.asarray([22.0 + cycle, 9.0], dtype=np.float64),
                    steps=8,
                )
                xy_after = _current_xy(window, viewer)

                empty_start = _first_empty_qt(viewer)
                camera_before = _camera_state(viewer)
                _drag(
                    app,
                    qtcore,
                    qtgui,
                    viewer.plotter,
                    empty_start,
                    empty_start + np.asarray([30.0, 4.0], dtype=np.float64),
                    steps=8,
                )
                camera_after = _camera_state(viewer)
                total_camera_delta += float(np.linalg.norm(camera_after - camera_before))

                window._set_viewer_tool_mode("gizmo")
                _process_events(app, ms=40)
                ring_start = _first_ring_qt(viewer)
                rotation_before = np.asarray(window.pending_rotation_matrix, dtype=np.float64).copy()
                _drag(
                    app,
                    qtcore,
                    qtgui,
                    viewer.plotter,
                    ring_start,
                    ring_start + np.asarray([25.0, 14.0], dtype=np.float64),
                    steps=8,
                )
                rotation_after = np.asarray(window.pending_rotation_matrix, dtype=np.float64).copy()

                print(
                    f"probe: cycle={cycle} rss={viewer._profile_rss_mb():.1f}MB "
                    f"move_delta={np.linalg.norm(xy_after - xy_before):.3f} "
                    f"camera_delta={np.linalg.norm(camera_after - camera_before):.3f} "
                    f"rotation_delta={np.linalg.norm(rotation_after - rotation_before):.3f}",
                    flush=True,
                )
                if cycle == 1:
                    _save_screenshot(viewer, screenshot_dir, "after_cycle_01")

            _save_screenshot(viewer, screenshot_dir, "final")
            print(
                f"probe: success cycles={cycles} total_camera_delta={total_camera_delta:.3f} "
                f"final_rss={viewer._profile_rss_mb():.1f}MB",
                flush=True,
            )
            qtcore.QTimer.singleShot(200, app.quit)
        except Exception:
            traceback.print_exc()
            app.exit(2)

    qtcore.QTimer.singleShot(0, run)
    exec_func = app.exec if hasattr(app, "exec") else app.exec_
    return int(exec_func())


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return _run_probe(args)
    except Exception:
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
