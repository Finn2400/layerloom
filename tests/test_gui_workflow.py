"""Real Qt/VTK workflow; opt in on a machine with a working graphics display.

Run with ``LAYERLOOM_GUI_INTEGRATION=1 python -m pytest tests/test_gui_workflow.py``.
Linux CI runs this under Xvfb with Mesa. No viewer or processing backend is mocked.
Only native file/message dialogs are replaced so the run remains unattended.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest


@pytest.mark.gui
@pytest.mark.skipif(
    os.environ.get("LAYERLOOM_GUI_INTEGRATION") != "1",
    reason="real Qt/VTK workflow requires LAYERLOOM_GUI_INTEGRATION=1 and a display",
)
def test_gui_workflow(tmp_path):
    # Qt/VTK can abort in native code. Keep those failures, caches, and event-loop
    # state separate from pytest and any other copy of the user's application.
    artifact_root = Path(os.environ.get("LAYERLOOM_GUI_ARTIFACT_DIR", str(tmp_path / "artifacts")))
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifact_dir = Path(tempfile.mkdtemp(prefix="run-", dir=artifact_root)).resolve()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    env = dict(os.environ, TMPDIR=str(runtime_dir), TEMP=str(runtime_dir), TMP=str(runtime_dir))
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONFAULTHANDLER"] = "1"
    env["MPLCONFIGDIR"] = str(runtime_dir / "matplotlib")
    command = [sys.executable, str(Path(__file__).resolve()), "--run-workflow", str(artifact_dir)]
    log_path = artifact_dir / "gui-workflow.log"
    with log_path.open("w", encoding="utf-8") as output:
        try:
            # Hosted software renderers can be much slower than a local GPU;
            # this is a correctness test, not a rendering speed benchmark.
            result = subprocess.run(command, env=env, stdout=output, stderr=subprocess.STDOUT, timeout=600)
        except subprocess.TimeoutExpired:
            pytest.fail(f"GUI workflow timed out; artifacts: {artifact_dir}")
    log = log_path.read_text(encoding="utf-8", errors="replace")
    assert result.returncode == 0, f"GUI workflow failed ({result.returncode}); artifacts: {artifact_dir}\n{log[-18000:]}"
    report = json.loads((artifact_dir / "gui-workflow.json").read_text(encoding="utf-8"))
    assert report["status"] == "ok"
    assert not report["slot_errors"], report["slot_errors"]
    assert report["rendered_model_pixels"] > 500
    assert report["loaded_parts"] == report["saved_parts"] == 8
    assert report["woven_color_groups"] == 3
    assert report["woven_layer_planes"] > 10


def _run_workflow(artifact_dir):
    """Execute through the production launcher and real widget signals."""
    import copy
    import traceback
    from unittest.mock import patch
    import zipfile
    import xml.etree.ElementTree as ET

    import numpy as np
    from PyQt5 import QtCore, QtGui, QtTest, QtWidgets

    from layerloom import gui_app
    from layerloom.example_assets import find_example_file
    from layerloom.ingest import extract_pat_token, read_parts

    gui_app._validate_gui_environment()
    artifact_dir = Path(artifact_dir)
    gui = gui_app._load_current_gui_module()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["LayerLoom GUI integration"])
    report = {"status": "running", "completed_steps": []}
    errors = []
    warnings = []
    slot_errors = []
    exit_status = [1]

    def save_report():
        (artifact_dir / "gui-workflow.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    def exception_hook(exc_type, exc, tb):
        slot_errors.append("".join(traceback.format_exception(exc_type, exc, tb)))

    sys.excepthook = exception_hook

    def pump():
        QtTest.QTest.qWait(60)
        assert not slot_errors, "Unhandled Qt callback exception:\n" + "\n".join(slot_errors)

    def step(name):
        assert not slot_errors, "Unhandled Qt callback exception:\n" + "\n".join(slot_errors)
        assert not errors, f"Unexpected GUI errors: {errors}"
        assert not warnings, f"Unexpected GUI warnings: {warnings}"
        report["completed_steps"].append(name)
        print(f"GUI step passed: {name}", flush=True)
        save_report()

    def toolbar_action(window, text):
        toolbar = window.findChildren(QtWidgets.QToolBar)[0]
        actions = [a for a in toolbar.actions() if a.text() == text]
        assert len(actions) == 1, (text, [a.text() for a in actions])
        return actions[0]

    def action(window, text):
        toolbar_action(window, text).trigger()
        pump()

    def click(widget):
        assert widget.isVisible() and widget.isEnabled(), f"Unavailable control: {widget}"
        QtTest.QTest.mouseClick(widget, QtCore.Qt.LeftButton)
        pump()

    def enter_number(spin, value):
        spin.setFocus()
        spin.selectAll()
        # Locale-aware entry exercises the actual editor rather than setting the
        # internal transform directly (decimal separators differ across hosts).
        QtTest.QTest.keyClicks(spin, spin.locale().toString(float(value), "f", spin.decimals()))
        QtTest.QTest.keyClick(spin, QtCore.Qt.Key_Return)
        pump()
        assert abs(spin.value() - value) < 1e-6

    def bounds(parts):
        return np.array([np.min([m.bounds[0] for _, m in parts], axis=0),
                         np.max([m.bounds[1] for _, m in parts], axis=0)])

    def screenshot(window, label):
        plotter = window.viewer.plotter
        plotter.render()
        pump()
        viewport_path = str(artifact_dir / f"{label}-viewport.png")
        if isinstance(plotter, QtWidgets.QOpenGLWidget):
            # pyvistaqt >= 0.13 renders into a Qt-owned framebuffer. Capture
            # that displayed surface through Qt, which owns its GL context;
            # VTK's standalone-window screenshot API cannot always make this
            # borrowed context current on Windows. This still tests the actual
            # model pixels below, including a hidden-geometry control frame.
            frame = plotter.grabFramebuffer().convertToFormat(QtGui.QImage.Format_RGB888)
            context = plotter.context()
            report["qt_opengl"] = {
                "widget_valid": plotter.isValid(),
                "context_valid": context is not None and context.isValid(),
                "version": ([context.format().majorVersion(), context.format().minorVersion()]
                            if context is not None else None),
                "opengl_es": context.isOpenGLES() if context is not None else None,
            }
            assert not frame.isNull(), f"Qt viewport framebuffer is unavailable: {report['qt_opengl']}"
            assert frame.save(viewport_path)
            rows = np.frombuffer(frame.bits().asstring(frame.byteCount()), dtype=np.uint8)
            pixels = rows.reshape(frame.height(), frame.bytesPerLine())[:, :frame.width() * 3]
            pixels = pixels.reshape(frame.height(), frame.width(), 3).copy()
            report["render_capture"] = "Qt framebuffer"
        else:
            # Older pyvistaqt owns a native VTK window instead.
            pixels = np.asarray(plotter.screenshot(viewport_path))
            report["render_capture"] = "VTK render window"
        assert pixels.ndim == 3 and min(pixels.shape[:2]) > 100
        # QWidget.grab cannot reliably read the native VTK child surface on
        # macOS. Keep framebuffer evidence and Qt control evidence separate.
        sidebar = window.findChild(QtWidgets.QWidget, "sidebarFrame")
        assert sidebar is not None
        assert sidebar.grab().save(str(artifact_dir / f"{label}-sidebar.png"))
        toolbar = window.findChildren(QtWidgets.QToolBar)[0]
        assert toolbar.grab().save(str(artifact_dir / f"{label}-toolbar.png"))
        return pixels.copy()

    def workflow():
        window = None
        try:
            windows = [w for w in app.topLevelWidgets() if isinstance(w, gui.QtAssignColorsApp)]
            assert len(windows) == 1, "Production launcher did not create exactly one main window"
            window = windows[0]
            window._error = lambda title, message: errors.append((title, message))
            window._warn = lambda title, message: warnings.append((title, message))
            pump()
            assert window.isVisible()
            assert window.parts_table.topLevelItemCount() == 0
            step("production launcher creates visible empty window")

            action(window, "Load Example")
            assert window.parts_table.topLevelItemCount() == 8
            assert len(window.viewer.actors_by_name) == 8
            report["loaded_parts"] = 8
            original_parts = read_parts([window.base_file])
            original_bounds = bounds(original_parts)
            assert len(original_parts) == 8
            before = screenshot(window, "benchy-loaded")
            actors = list(window.viewer.actors_by_name.values())
            for actor in actors:
                actor.SetVisibility(False)
            hidden = screenshot(window, "benchy-hidden-control")
            for actor in actors:
                actor.SetVisibility(True)
            assert before.shape == hidden.shape
            report["rendered_model_pixels"] = int(np.count_nonzero(np.any(before != hidden, axis=2)))
            assert report["rendered_model_pixels"] > 500, "Viewport did not render model geometry"
            window.viewer.plotter.render()
            step("packaged Benchy loads and actual model geometry renders")

            item = window.parts_table.topLevelItem(0)
            window.parts_table.scrollToItem(item)
            QtTest.QTest.mouseClick(window.parts_table.viewport(), QtCore.Qt.LeftButton,
                                    pos=window.parts_table.visualItemRect(item).center())
            pump()
            assert window.parts_table.currentItem() is item
            oid = item.data(0, QtCore.Qt.UserRole)
            old_assignments = copy.deepcopy(window.assignments)
            swatches = window.palette_inner.findChildren(gui._V58.PaletteSwatchButton)
            swatch = next(s for s in swatches if len(set(s.entry["token"])) > 1
                          and s.entry["token"] != old_assignments.get(oid, {}).get("token"))
            window.palette_scroll.ensureWidgetVisible(swatch)
            pump()
            click(swatch)
            token, color = swatch.entry["token"], swatch.entry["hex"]
            assert item.text(2) == token and item.text(1).lower() == color.lower()
            assert token in window.selected_token_label.text()
            assert all(window.assignments[k] == v for k, v in old_assignments.items() if k != oid)
            # Selection flash restores after 250 ms; check the settled actor color.
            QtTest.QTest.qWait(300)
            actor = window.viewer.actors_by_name[window.oid_to_name[oid]]
            expected_rgb = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            np.testing.assert_allclose(actor.GetProperty().GetColor(), expected_rgb, atol=1 / 255)
            step("part selection and palette click update only selected part and rendered color")

            for button, mode in ((window.move_tool_btn, "move"),
                                 (window.rotate_tool_btn, "gizmo"),
                                 (window.cancel_tool_btn, "normal")):
                click(button)
                assert window.viewer_tool_mode == mode
                assert button.isChecked()
            enter_number(window.layer_height_spin, 0.20)
            enter_number(window.scale_spin, 1.25)
            enter_number(window.rot_z_spin, 90.0)
            click(window.center_plate_btn)
            screenshot(window, "benchy-transformed")
            step("tool modes, layer height, rotation, scaling, and center controls work")

            save_path = artifact_dir / "Benchý transformed and labeled.3mf"
            with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(save_path), "3MF")):
                action(window, "Save Labeled 3MF")
            assert save_path.is_file()
            saved_parts = read_parts([str(save_path)])
            report["saved_parts"] = len(saved_parts)
            assert len(saved_parts) == 8
            saved_bounds = bounds(saved_parts)
            np.testing.assert_allclose(saved_bounds[1] - saved_bounds[0],
                                       (original_bounds[1] - original_bounds[0])[[1, 0, 2]] * 1.25,
                                       rtol=1e-6, atol=1e-4)
            np.testing.assert_allclose(saved_bounds[:, :2].mean(axis=0),
                                       [gui._V58.PLATE_WIDTH_MM / 2, gui._V58.PLATE_DEPTH_MM / 2], atol=1e-4)
            assert abs(saved_bounds[0, 2]) < 1e-5
            assert sum(len(mesh.faces) for _, mesh in saved_parts) == sum(len(mesh.faces) for _, mesh in original_parts)
            with zipfile.ZipFile(save_path) as archive:
                model = next(n for n in archive.namelist() if n.lower().endswith(".model"))
                xml = ET.fromstring(archive.read(model))
            ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
            obj = xml.find(f"m:resources/m:object[@id='{oid}']", ns)
            assert obj is not None and extract_pat_token(obj.get("name")) == token
            metadata = {e.get("name"): e.text for e in obj.findall("m:metadata", ns)}
            assert metadata["stack_token"] == token
            assert metadata["source_hex"].lower() == color.lower()
            step("saved Unicode-path 3MF retains all geometry, intended transform, and color recipe")

            with patch.object(QtWidgets.QFileDialog, "getOpenFileName", return_value=(str(save_path), "3MF")):
                action(window, "Open 3MF")
            assert window.parts_table.topLevelItemCount() == 8
            assert window.assignments[oid]["token"] == token
            np.testing.assert_allclose(bounds(read_parts([window.base_file])), saved_bounds, atol=1e-5)
            screenshot(window, "benchy-reopened")
            step("saved model reopens with matching geometry and recipe")

            old_file = window.base_file
            old_assignments = copy.deepcopy(window.assignments)
            with patch.object(QtWidgets.QFileDialog, "getOpenFileName", return_value=("", "")):
                action(window, "Open 3MF")
            with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=("", "")):
                action(window, "Save Labeled 3MF")
            with patch.object(QtWidgets.QFileDialog, "getExistingDirectory", return_value=""):
                action(window, "Weave")
            assert window.base_file == old_file and window.assignments == old_assignments
            step("cancel open, save, and weave dialogs preserves loaded model")

            cubes = find_example_file("tutorial_cmy_cubes.3mf")
            assert cubes is not None
            with patch.object(QtWidgets.QFileDialog, "getOpenFileName", return_value=(str(cubes), "3MF")):
                action(window, "Open 3MF")
            cube_parts = read_parts([window.base_file])
            enter_number(window.layer_height_spin, 0.20)
            output_dir = artifact_dir / "woven outputs"
            output_dir.mkdir(exist_ok=True)
            heartbeats = []
            heartbeat_timer = QtCore.QTimer()
            heartbeat_timer.setInterval(20)
            heartbeat_timer.timeout.connect(lambda: heartbeats.append(True))
            with patch.object(QtWidgets.QFileDialog, "getExistingDirectory", return_value=str(output_dir)):
                heartbeat_timer.start()
                try:
                    # Check while the actual action is in progress, before pump()
                    # could disguise a blocked event loop by delivering a timer.
                    weave = toolbar_action(window, "Weave")
                    weave.trigger()
                    # This notification expires after five seconds. Capture it
                    # before another event-loop turn or slow output validation.
                    completion_message = window.status_bar.currentMessage()
                finally:
                    heartbeat_timer.stop()
            report["weave_event_loop_heartbeats"] = len(heartbeats)
            assert heartbeats, "Weave blocks the GUI event loop"
            pump()
            woven_files = list(output_dir.glob("*.3mf"))
            assert len(woven_files) == 1
            woven_parts = read_parts([str(woven_files[0])])
            report["woven_color_groups"] = len(woven_parts)
            assert {name for name, _ in woven_parts} == {"all_cyan", "all_magenta", "all_yellow"}
            assert all(np.isfinite(mesh.vertices).all() and len(mesh.faces) > 0 for _, mesh in woven_parts)
            # The legacy slicer intentionally deducts a 0.001 mm inter-band
            # clearance, including half of that at the model's bottom/top.
            woven_bounds = bounds(woven_parts)
            expected_bounds = bounds(cube_parts)
            expected_bounds[0, 2] += 0.0005
            expected_bounds[1, 2] -= 0.0005
            np.testing.assert_allclose(woven_bounds, expected_bounds, atol=1e-5)
            layer_z = np.unique(np.concatenate([mesh.vertices[:, 2] for _, mesh in woven_parts]))
            report["woven_layer_planes"] = len(layer_z)
            assert len(layer_z) > 10
            distance_to_layer = np.abs(layer_z - np.round(layer_z / 0.20) * 0.20)
            np.testing.assert_allclose(distance_to_layer, 0.0005, atol=1e-5)
            report["woven_assignments"] = {str(k): v.get("token") for k, v in window.assignments.items()}
            assert set(report["woven_assignments"].values()) == set("cmy")
            report["weave_completion_message"] = completion_message
            assert "Weave complete" in completion_message
            screenshot(window, "cubes-woven")
            step("real weave subprocess exports and reloads colored cube bands")

            old_file = window.base_file
            old_assignments = copy.deepcopy(window.assignments)
            old_count = window.parts_table.topLevelItemCount()
            invalid_path = artifact_dir / "invalid model.3mf"
            invalid_path.write_text("This is not a 3MF archive.\n", encoding="utf-8")
            with patch.object(QtWidgets.QFileDialog, "getOpenFileName", return_value=(str(invalid_path), "3MF")):
                action(window, "Open 3MF")
            assert errors, "Invalid model should produce a user-facing error"
            report["invalid_input_error"] = errors.pop()
            assert window.base_file == old_file, "Invalid open replaced the last usable model"
            assert window.assignments == old_assignments
            assert window.parts_table.topLevelItemCount() == old_count
            screenshot(window, "invalid-open-recovered")
            step("invalid input reports error and keeps the previous model usable")

            # A second window/dialog can keep Qt alive after the model window
            # closes. Pending selection feedback must not access its renderer.
            oid, name = next(iter(window.oid_to_name.items()))
            window._flash_actor_by_name(name, oid)
            app.setQuitOnLastWindowClosed(False)
            window.close()
            window = None
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
            QtTest.QTest.qWait(350)
            step("closing during selection feedback leaves no delayed Qt errors")

            report["status"] = "ok"
            exit_status[0] = 0
        except BaseException:
            report["status"] = "failed"
            report["traceback"] = traceback.format_exc()
            print(report["traceback"], file=sys.stderr, flush=True)
        finally:
            report["errors"] = errors
            report["warnings"] = warnings
            report["slot_errors"] = slot_errors
            save_report()
            if window is not None:
                window.close()
            app.exit(exit_status[0])

    QtCore.QTimer.singleShot(0, workflow)
    gui.main()
    return exit_status[0]


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--run-workflow":
        raise SystemExit("Run through pytest, or pass --run-workflow ARTIFACT_DIRECTORY")
    raise SystemExit(_run_workflow(sys.argv[2]))
