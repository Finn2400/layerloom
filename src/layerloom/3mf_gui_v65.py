#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LayerLoom v65
-------------

Previous palette behavior plus a single viewer-local transform source of truth
for whole-model Move/Rotate interactions.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from typing import Dict, Optional

try:
    import resource
except Exception:
    resource = None


_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_V62_PATH = os.path.join(_THIS_DIR, "3mf_gui_v62.py")
_SPEC = importlib.util.spec_from_file_location("layerloom_gui_v62_runtime_for_v65", _V62_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Could not load v62 base module from {_V62_PATH}")
_V62 = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _V62
_SPEC.loader.exec_module(_V62)
_V58 = _V62._V58
_BASE_VIEWER = _V58.EmbeddedPVViewer


class V65InteractiveQtInteractor(_V58.QtInteractor):
    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._owner = owner

    def mousePressEvent(self, event):
        if event.button() == _V58.QtCore.Qt.RightButton and self._owner._handle_tool_cancel():
            event.accept()
            return
        if event.button() == _V58.QtCore.Qt.LeftButton and self._owner.tool_mode in {"move", "gizmo"}:
            if self._owner._qt_mouse_press(event):
                event.accept()
                return
            if self._owner.tool_mode == "move":
                self._owner._move_camera_passthrough = True
            if self._owner.tool_mode == "gizmo":
                self._owner._gizmo_camera_passthrough = True
            super().mousePressEvent(event)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._owner._active_move is not None:
            self._owner._qt_mouse_move(event)
            event.accept()
            return
        if self._owner._active_rotation is not None:
            self._owner._qt_mouse_move(event)
            event.accept()
            return
        if getattr(self._owner, "_move_camera_passthrough", False) or getattr(
            self._owner, "_gizmo_camera_passthrough", False
        ):
            super().mouseMoveEvent(event)
            return
        super().mouseMoveEvent(event)
        if self._owner.tool_mode == "move":
            self._owner._qt_mouse_move(event)

    def mouseReleaseEvent(self, event):
        if event.button() == _V58.QtCore.Qt.LeftButton and self._owner._active_move is not None:
            self._owner._qt_mouse_release(event)
            event.accept()
            return
        if event.button() == _V58.QtCore.Qt.LeftButton and self._owner._active_rotation is not None:
            self._owner._qt_mouse_release(event)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if event.button() == _V58.QtCore.Qt.LeftButton:
            self._owner._clear_hover_model_pick_cache()
            self._owner._move_camera_passthrough = False
            self._owner._gizmo_camera_passthrough = False

    def mouseDoubleClickEvent(self, event):
        self.mousePressEvent(event)

    def mouseReleaseEventDefault(self, event):
        super().mouseReleaseEvent(event)

    def mousePressEventDefault(self, event):
        super().mousePressEvent(event)

    def mouseMoveEventDefault(self, event):
        super().mouseMoveEvent(event)

    def contextMenuEvent(self, event):
        if self._owner._handle_tool_cancel():
            event.accept()
            return
        super().contextMenuEvent(event)

    def keyPressEvent(self, event):
        try:
            if event.key() == _V58.QtCore.Qt.Key_Escape and self._owner._handle_escape():
                event.accept()
                return
        except Exception:
            pass
        super().keyPressEvent(event)


class EmbeddedPVViewerV65(_BASE_VIEWER):
    ROTATION_RING_HOVER_TOLERANCE_PX = 150.0
    ROTATION_RING_PRESS_TOLERANCE_PX = 220.0

    def __init__(self, parent=None):
        _V58.PVWindow.__init__(self)
        self.plotter = V65InteractiveQtInteractor(self, parent)
        self._configured = False
        self._layer_height = 0.16
        self._layer_scale = 1.0
        self._layer_preview_actors = []
        self._rotation_feedback_label = None
        self._hover_gizmo_axis = None
        self._active_gizmo_axis = None
        self.on_rotation_status = None
        self.on_move_commit = None
        self.on_tool_cancel = None
        self._active_move = None
        self._active_rotation = None
        self._move_camera_passthrough = False
        self._gizmo_camera_passthrough = False
        self._camera_drag_forwarded = False
        self._cursor_kind = "default"
        self.selection_outline_actor = None
        self.plate_boundary_actor = None
        self.plate_margin_actor = None
        self._whole_model_highlight = False
        self._v65_observer_ids = []
        self._plate_warning_state = None
        self._hover_model_pick_cache = None
        self._hover_model_pick_last_at = 0.0
        self._hover_model_pick_interval_s = 0.04
        self._hover_model_pick_slop_px = 10.0
        self._profile_enabled = os.environ.get("LAYERLOOM_VIEWER_PROFILE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self._profile_state = {}

    def _ensure_plotter(self):
        plotter = super()._ensure_plotter()
        self._ensure_plate_boundary()
        return plotter

    def _teardown_scene(self):
        self._clear_selection_outline()
        self._whole_model_highlight = False
        self._plate_warning_state = None
        self._clear_hover_model_pick_cache()
        super()._teardown_scene()

    def _line_segments_mesh(self, segments):
        points = []
        lines = []
        for a, b in segments:
            start = len(points)
            points.extend([a, b])
            lines.extend([2, start, start + 1])
        if not points:
            return None
        mesh = _V58.pv.PolyData(_V58.np.asarray(points, dtype=_V58.np.float64))
        mesh.lines = _V58.np.asarray(lines, dtype=_V58.np.int64)
        return mesh

    def _ensure_plate_boundary(self):
        if not self.plotter or self.plate_boundary_actor is not None:
            return
        z = 0.16
        w = float(_V58.PLATE_WIDTH_MM)
        d = float(_V58.PLATE_DEPTH_MM)
        margin = 8.0
        boundary = self._line_segments_mesh(
            [
                ((0.0, 0.0, z), (w, 0.0, z)),
                ((w, 0.0, z), (w, d, z)),
                ((w, d, z), (0.0, d, z)),
                ((0.0, d, z), (0.0, 0.0, z)),
            ]
        )
        margin_mesh = self._line_segments_mesh(
            [
                ((margin, margin, z + 0.01), (w - margin, margin, z + 0.01)),
                ((w - margin, margin, z + 0.01), (w - margin, d - margin, z + 0.01)),
                ((w - margin, d - margin, z + 0.01), (margin, d - margin, z + 0.01)),
                ((margin, d - margin, z + 0.01), (margin, margin, z + 0.01)),
            ]
        )
        try:
            if boundary is not None:
                self.plate_boundary_actor = self.plotter.add_mesh(
                    boundary,
                    name="__v65_plate_boundary__",
                    color="#4d5966",
                    line_width=3,
                    lighting=False,
                    pickable=False,
                    reset_camera=False,
                )
            if margin_mesh is not None:
                self.plate_margin_actor = self.plotter.add_mesh(
                    margin_mesh,
                    name="__v65_plate_margin__",
                    color="#7c8792",
                    line_width=1,
                    opacity=0.75,
                    lighting=False,
                    pickable=False,
                    reset_camera=False,
                )
        except Exception:
            self.plate_boundary_actor = None
            self.plate_margin_actor = None

    def set_callbacks(
        self,
        on_pick=None,
        on_rotation_commit=None,
        on_surface_commit=None,
        on_rotation_status=None,
        on_move_commit=None,
        on_tool_cancel=None,
    ):
        super().set_callbacks(
            on_pick=on_pick,
            on_rotation_commit=on_rotation_commit,
            on_surface_commit=on_surface_commit,
            on_rotation_status=on_rotation_status,
        )
        if on_move_commit is not None:
            self.on_move_commit = on_move_commit
        if on_tool_cancel is not None:
            self.on_tool_cancel = on_tool_cancel

    def set_tool_mode(self, mode: str, *, center=None, radius=None):
        self.tool_mode = (mode or "normal").lower()
        self._clear_hover_model_pick_cache()
        if center is not None:
            self.gizmo_center = _V58.np.asarray(center, dtype=_V58.np.float64).reshape(3)
        if radius is not None and _V58.math.isfinite(radius):
            self.gizmo_radius = max(float(radius), 1.0)

        self._clear_builtin_picking()
        self._remove_raw_interaction_handlers()
        self._ensure_experimental_interaction_observers()
        self._restore_move_preview()
        self._active_rotation = None
        if self._temp_transform_active or self._active_drag is not None:
            self._clear_temp_transform()

        try:
            self.plotter.iren.enable_trackball_style()
        except Exception:
            pass

        if self.tool_mode == "gizmo":
            self._clear_surface_highlight()
            self._rebuild_gizmo()
        else:
            self._clear_gizmo()
            if self.tool_mode not in {"surface", "plate"}:
                self._clear_surface_highlight()
        self.set_whole_model_highlight(self.tool_mode in {"move", "gizmo"})
        self._set_viewer_cursor("default")
        if self.plotter:
            self.plotter.render()

    def _make_ring(self, axis_name: str, center, radius: float):
        t = _V58.np.linspace(0.0, 2.0 * _V58.math.pi, 181)
        if axis_name == "x":
            pts = _V58.np.column_stack([_V58.np.zeros_like(t), _V58.np.cos(t) * radius, _V58.np.sin(t) * radius])
        elif axis_name == "y":
            pts = _V58.np.column_stack([_V58.np.cos(t) * radius, _V58.np.zeros_like(t), _V58.np.sin(t) * radius])
        else:
            pts = _V58.np.column_stack([_V58.np.cos(t) * radius, _V58.np.sin(t) * radius, _V58.np.zeros_like(t)])
        ring = _V58.pv.lines_from_points(pts + _V58.np.asarray(center, dtype=_V58.np.float64), close=True)
        return ring.tube(radius=max(float(radius) * 0.006, 0.16), n_sides=10)

    def _v65_has_active_drag(self) -> bool:
        return self._active_move is not None or self._active_drag is not None or self._active_rotation is not None

    def _ensure_experimental_interaction_observers(self):
        if not self.plotter or self._v65_observer_ids:
            return
        try:
            vtk = _V58._get_vtk()
            if self._picker is None:
                self._picker = vtk.vtkCellPicker()
                self._picker.SetTolerance(0.0005)
            self._v65_observer_ids = [
                self.plotter.iren.add_observer("LeftButtonPressEvent", self._on_left_button_press),
                self.plotter.iren.add_observer("MouseMoveEvent", self._on_mouse_move),
                self.plotter.iren.add_observer("LeftButtonReleaseEvent", self._on_left_button_release),
                self.plotter.iren.add_observer("RightButtonPressEvent", self._on_right_button_press),
                self.plotter.iren.add_observer("KeyPressEvent", self._on_key_press),
            ]
        except Exception:
            self._v65_observer_ids = []

    def _display_pos_qt(self, world_point):
        display = _V58.np.asarray(self._display_pos(world_point), dtype=_V58.np.float64)
        try:
            dpr = float(self.plotter.devicePixelRatioF())
        except Exception:
            dpr = 1.0
        dpr = max(dpr, 1e-9)
        try:
            qt_height = float(self.plotter.height())
        except Exception:
            try:
                qt_height = float(self.plotter.window_size[1]) / dpr
            except Exception:
                qt_height = float(display[1])
        display[0] = display[0] / dpr
        display[1] = qt_height - (display[1] / dpr)
        return display

    def _set_viewer_cursor(self, kind: str) -> None:
        if self._cursor_kind == kind:
            return
        self._cursor_kind = kind
        try:
            cursors = {
                "move": _V58.QtCore.Qt.SizeAllCursor,
                "rotate": _V58.QtCore.Qt.OpenHandCursor,
                "grab": _V58.QtCore.Qt.OpenHandCursor,
                "drag": _V58.QtCore.Qt.ClosedHandCursor,
            }
            if kind in cursors:
                self.plotter.setCursor(_V58.QtGui.QCursor(cursors[kind]))
            else:
                self.plotter.unsetCursor()
        except Exception:
            pass

    def _qt_to_vtk_display(self, display_pos):
        pos = _V58.np.asarray(display_pos, dtype=_V58.np.float64).reshape(2)
        try:
            dpr = float(self.plotter.devicePixelRatioF())
        except Exception:
            dpr = 1.0
        dpr = max(dpr, 1e-9)
        try:
            qt_height = float(self.plotter.height())
        except Exception:
            try:
                qt_height = float(self.plotter.window_size[1]) / dpr
            except Exception:
                qt_height = float(pos[1])
        return float(pos[0] * dpr), float((qt_height - pos[1]) * dpr)

    def _display_to_plate_point(self, display_pos, *, qt_coords: bool = True):
        if not self.plotter:
            return None
        renderer = self.plotter.renderer
        if qt_coords:
            x, y = self._qt_to_vtk_display(display_pos)
        else:
            x, y = float(display_pos[0]), float(display_pos[1])
        points = []
        for z in (0.0, 1.0):
            renderer.SetDisplayPoint(x, y, z)
            renderer.DisplayToWorld()
            world = _V58.np.asarray(renderer.GetWorldPoint(), dtype=_V58.np.float64)
            if abs(float(world[3])) <= 1e-12:
                return None
            points.append(world[:3] / world[3])
        p0, p1 = points
        ray = p1 - p0
        if abs(float(ray[2])) <= 1e-12:
            return None
        return p0 + ray * (-p0[2] / ray[2])

    def _interactor_display_pos(self):
        try:
            x, y = self._current_event_xy()
            return _V58.np.array([float(x), float(y)], dtype=_V58.np.float64)
        except Exception:
            return None

    def _matrix_to_array(self, vtk_matrix):
        if vtk_matrix is None:
            return None
        arr = _V58.np.eye(4, dtype=_V58.np.float64)
        for i in range(4):
            for j in range(4):
                arr[i, j] = float(vtk_matrix.GetElement(i, j))
        return arr

    def _actor_user_matrix(self, actor):
        try:
            return self._matrix_to_array(actor.GetUserMatrix())
        except Exception:
            return None

    def _set_camera_drag_enabled(self, enabled: bool) -> None:
        try:
            if enabled:
                self.plotter.iren.enable_trackball_style()
            else:
                vtk = _V58._get_vtk()
                self.plotter.iren.interactor.SetInteractorStyle(vtk.vtkInteractorStyleUser())
        except Exception:
            pass

    def _camera_interactor_style(self):
        try:
            return self.plotter.iren.interactor.GetInteractorStyle()
        except Exception:
            return None

    def _forward_camera_left_press(self) -> None:
        style = self._camera_interactor_style()
        if style is None:
            return
        try:
            style.OnLeftButtonDown()
        except Exception:
            pass

    def _forward_camera_left_release(self) -> None:
        style = self._camera_interactor_style()
        if style is None:
            return
        try:
            style.OnLeftButtonUp()
        except Exception:
            pass

    def _left_button_is_down(self) -> bool:
        try:
            return bool(_V58.QtWidgets.QApplication.mouseButtons() & _V58.QtCore.Qt.LeftButton)
        except Exception:
            return True

    def _event_display_candidates(self, event, *, use_interactor_pos: bool = False):
        raw = []
        for flag in (use_interactor_pos, False, True):
            try:
                pos = self._event_display_pos(event, use_interactor_pos=flag)
                raw.append((pos, flag))
            except Exception:
                pass
        out = []
        seen = set()
        for pos, flag in raw:
            arr = _V58.np.asarray(pos, dtype=_V58.np.float64).reshape(2)
            key = (round(float(arr[0]), 3), round(float(arr[1]), 3), bool(flag))
            xy_key = (key[0], key[1])
            if xy_key in seen:
                continue
            seen.add(xy_key)
            out.append((arr, bool(flag)))
        return out

    def _render_drag_preview(self, *, force: bool = False) -> None:
        if not self.plotter:
            return
        try:
            self.plotter.render()
        except Exception:
            pass

    def _profile_start(self):
        if not self._profile_enabled:
            return None
        try:
            return _V58.time.perf_counter()
        except Exception:
            return None

    def _profile_rss_mb(self) -> float:
        try:
            rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            return rss / (1024.0 * 1024.0) if sys.platform == "darwin" else rss / 1024.0
        except Exception:
            return 0.0

    def _profile_finish(self, kind: str, started, *, actor_count: int = 0) -> None:
        if started is None:
            return
        try:
            elapsed_ms = (_V58.time.perf_counter() - float(started)) * 1000.0
            state = self._profile_state.setdefault(
                kind,
                {"count": 0, "total_ms": 0.0, "last_log": 0.0},
            )
            state["count"] += 1
            state["total_ms"] += elapsed_ms
            now = _V58.time.perf_counter()
            if state["count"] % 60 != 0 and now - state["last_log"] < 2.0:
                return
            state["last_log"] = now
            avg_ms = state["total_ms"] / max(int(state["count"]), 1)
            _V58._log(
                "INFO",
                (
                    f"[viewer-profile] {kind}: frames={state['count']} "
                    f"last={elapsed_ms:.2f}ms avg={avg_ms:.2f}ms "
                    f"actors={actor_count} rss≈{self._profile_rss_mb():.1f}MB"
                ),
            )
        except Exception:
            pass

    def _clear_hover_model_pick_cache(self) -> None:
        self._hover_model_pick_cache = None
        self._hover_model_pick_last_at = 0.0

    def _cached_hover_model_pick(self, display_pos, *, max_age_s: float = 0.18, max_dist_px: float = 12.0):
        cache = self._hover_model_pick_cache
        if not cache:
            return None
        try:
            now = _V58.time.perf_counter()
            pos = _V58.np.asarray(display_pos, dtype=_V58.np.float64).reshape(2)
            cached_pos = _V58.np.asarray(cache.get("pos"), dtype=_V58.np.float64).reshape(2)
            if now - float(cache.get("time", 0.0)) > float(max_age_s):
                return None
            if float(_V58.np.linalg.norm(pos - cached_pos)) > float(max_dist_px):
                return None
            return cache.get("result")
        except Exception:
            return None

    def _remember_hover_model_pick(self, display_pos, result) -> None:
        try:
            self._hover_model_pick_cache = {
                "pos": _V58.np.asarray(display_pos, dtype=_V58.np.float64).reshape(2).copy(),
                "time": _V58.time.perf_counter(),
                "result": result,
            }
            self._hover_model_pick_last_at = self._hover_model_pick_cache["time"]
        except Exception:
            self._clear_hover_model_pick_cache()

    def _model_screen_bounds_contains_vtk(self, display_pos, *, padding_px: float = 18.0) -> bool:
        try:
            bounds = self._current_model_bounds()
            if not bounds:
                return True
            xmin, xmax, ymin, ymax, zmin, zmax = [float(v) for v in bounds]
            corners = [
                (x, y, z)
                for x in (xmin, xmax)
                for y in (ymin, ymax)
                for z in (zmin, zmax)
            ]
            display_points = _V58.np.asarray([self._display_pos(corner)[:2] for corner in corners], dtype=_V58.np.float64)
            if display_points.size == 0 or not _V58.np.all(_V58.np.isfinite(display_points)):
                return True
            pos = _V58.np.asarray(display_pos, dtype=_V58.np.float64).reshape(2)
            lower = display_points.min(axis=0) - float(padding_px)
            upper = display_points.max(axis=0) + float(padding_px)
            return bool(lower[0] <= pos[0] <= upper[0] and lower[1] <= pos[1] <= upper[1])
        except Exception:
            return True

    def _pick_model_for_press(self, display_pos):
        cached = self._cached_hover_model_pick(display_pos)
        if cached is not None and cached[0] is not None:
            return cached
        if not self._model_screen_bounds_contains_vtk(display_pos, padding_px=24.0):
            result = (None, None, -1, None)
            self._remember_hover_model_pick(display_pos, result)
            return result
        result = self._pick_model_at_vtk_display_exact(display_pos)
        self._remember_hover_model_pick(display_pos, result)
        return result

    def _pick_model_for_hover(self, display_pos):
        if not self._model_screen_bounds_contains_vtk(display_pos, padding_px=28.0):
            result = (None, None, -1, None)
            self._remember_hover_model_pick(display_pos, result)
            return result
        return "__model_hover__", None, -1, None

    def _pick_model_at_vtk_display_exact(self, display_pos):
        if not self.plotter or self._picker is None:
            return None, None, -1, None
        x, y = int(display_pos[0]), int(display_pos[1])
        profile_started = self._profile_start()
        try:
            self._picker.Pick(x, y, 0.0, self.plotter.renderer)
            actor = self._picker.GetActor()
        except Exception:
            self._profile_finish("model-pick", profile_started, actor_count=len(getattr(self, "actors_by_name", {})))
            return None, None, -1, None
        if actor is None:
            self._profile_finish("model-pick", profile_started, actor_count=len(getattr(self, "actors_by_name", {})))
            return None, None, -1, None
        name = self._actor_name(actor)
        if name is None or name not in getattr(self, "actors_by_name", {}):
            self._profile_finish("model-pick", profile_started, actor_count=len(getattr(self, "actors_by_name", {})))
            return None, None, -1, None
        try:
            pick_pos = _V58.np.asarray(self._picker.GetPickPosition(), dtype=_V58.np.float64)
        except Exception:
            pick_pos = None
        try:
            cell_id = int(self._picker.GetCellId())
        except Exception:
            cell_id = -1
        self._profile_finish("model-pick", profile_started, actor_count=len(getattr(self, "actors_by_name", {})))
        return str(name), actor, cell_id, pick_pos

    def _clamp_move_delta_xy(self, delta_xy):
        delta = _V58.np.asarray(delta_xy, dtype=_V58.np.float64).reshape(2).copy()
        if self._active_move is None:
            return delta
        bounds = self._active_move.get("base_bounds")
        if not bounds:
            return delta
        margin = 8.0
        plate_w = float(_V58.PLATE_WIDTH_MM)
        plate_d = float(_V58.PLATE_DEPTH_MM)
        xmin, xmax, ymin, ymax = [float(v) for v in bounds[:4]]
        size_x = xmax - xmin
        size_y = ymax - ymin
        usable_x = max(plate_w - 2.0 * margin, 1e-9)
        usable_y = max(plate_d - 2.0 * margin, 1e-9)
        if size_x > usable_x + 1e-6:
            delta[0] = plate_w * 0.5 - ((xmin + xmax) * 0.5)
        else:
            if xmin + delta[0] < margin:
                delta[0] = margin - xmin
            if xmax + delta[0] > plate_w - margin:
                delta[0] = (plate_w - margin) - xmax
        if size_y > usable_y + 1e-6:
            delta[1] = plate_d * 0.5 - ((ymin + ymax) * 0.5)
        else:
            if ymin + delta[1] < margin:
                delta[1] = margin - ymin
            if ymax + delta[1] > plate_d - margin:
                delta[1] = (plate_d - margin) - ymax
        return delta

    def _move_preview_inside_plate(self, delta_xy) -> bool:
        if self._active_move is None:
            return True
        bounds = self._active_move.get("base_bounds")
        if not bounds:
            return True
        delta = _V58.np.asarray(delta_xy, dtype=_V58.np.float64).reshape(2)
        return bool(
            bounds[0] + delta[0] >= -1e-6
            and bounds[2] + delta[1] >= -1e-6
            and bounds[1] + delta[0] <= float(_V58.PLATE_WIDTH_MM) + 1e-6
            and bounds[3] + delta[1] <= float(_V58.PLATE_DEPTH_MM) + 1e-6
        )

    def _bounds_corners(self, bounds):
        b = tuple(float(v) for v in bounds)
        return _V58.np.asarray(
            [
                [x, y, z, 1.0]
                for x in (b[0], b[1])
                for y in (b[2], b[3])
                for z in (b[4], b[5])
            ],
            dtype=_V58.np.float64,
        )

    def _bounds_from_corners(self, corners, matrix=None):
        pts = _V58.np.asarray(corners, dtype=_V58.np.float64)
        if matrix is not None:
            pts = (_V58.np.asarray(matrix, dtype=_V58.np.float64) @ pts.T).T
        pts = pts[:, :3]
        mn = _V58.np.min(pts, axis=0)
        mx = _V58.np.max(pts, axis=0)
        return float(mn[0]), float(mx[0]), float(mn[1]), float(mx[1]), float(mn[2]), float(mx[2])

    def _plate_adjust_for_bounds(self, bounds):
        margin = 8.0
        plate_w = float(_V58.PLATE_WIDTH_MM)
        plate_d = float(_V58.PLATE_DEPTH_MM)
        xmin, xmax, ymin, ymax, zmin, _zmax = [float(v) for v in bounds]
        size_x = xmax - xmin
        size_y = ymax - ymin
        usable_x = max(plate_w - 2.0 * margin, 1e-9)
        usable_y = max(plate_d - 2.0 * margin, 1e-9)
        dx = 0.0
        dy = 0.0
        if size_x > usable_x + 1e-6:
            dx = plate_w * 0.5 - ((xmin + xmax) * 0.5)
        else:
            if xmin < margin:
                dx += margin - xmin
            if xmax + dx > plate_w - margin:
                dx -= (xmax + dx) - (plate_w - margin)
        if size_y > usable_y + 1e-6:
            dy = plate_d * 0.5 - ((ymin + ymax) * 0.5)
        else:
            if ymin < margin:
                dy += margin - ymin
            if ymax + dy > plate_d - margin:
                dy -= (ymax + dy) - (plate_d - margin)
        return _V58.np.asarray([dx, dy, -zmin], dtype=_V58.np.float64)

    def _bounds_inside_plate(self, bounds) -> bool:
        b = tuple(float(v) for v in bounds)
        return bool(
            b[0] >= -1e-6
            and b[2] >= -1e-6
            and b[1] <= float(_V58.PLATE_WIDTH_MM) + 1e-6
            and b[3] <= float(_V58.PLATE_DEPTH_MM) + 1e-6
        )

    def _translation_matrix(self, offset):
        mat = _V58.np.eye(4, dtype=_V58.np.float64)
        mat[:3, 3] = _V58.np.asarray(offset, dtype=_V58.np.float64).reshape(3)
        return mat

    def _rotation_about_point_matrix(self, rotation, center):
        out = _V58.np.eye(4, dtype=_V58.np.float64)
        rot4 = _V58.np.eye(4, dtype=_V58.np.float64)
        rot4[:3, :3] = _V58.orthonormalize_rotation(rotation)
        center = _V58.np.asarray(center, dtype=_V58.np.float64).reshape(3)
        out = self._translation_matrix(center) @ rot4 @ self._translation_matrix(-center)
        return out

    def _apply_move_preview(self, delta_xy) -> None:
        if self._active_move is None:
            return
        profile_started = self._profile_start()
        delta_xy = self._clamp_move_delta_xy(delta_xy)
        self._active_move["delta_xy"] = delta_xy
        mat = _V58.np.eye(4, dtype=_V58.np.float64)
        mat[0, 3] = float(delta_xy[0])
        mat[1, 3] = float(delta_xy[1])
        base_matrices: Dict[str, Optional[object]] = self._active_move.get("base_matrices", {})
        vtk_matrix_cache = {}
        for name, actor in self.actors_by_name.items():
            base = base_matrices.get(name)
            preview = mat if base is None else mat @ base
            key = None if base is None else preview.tobytes()
            vtk_mat = vtk_matrix_cache.get(key)
            if vtk_mat is None:
                vtk_mat = self._vtk_matrix(preview)
                vtk_matrix_cache[key] = vtk_mat
            try:
                actor.SetUserMatrix(vtk_mat)
            except Exception:
                pass
        try:
            if self.selection_outline_actor is not None:
                outline_mat = vtk_matrix_cache.get(None)
                if outline_mat is None:
                    outline_mat = self._vtk_matrix(mat)
                self.selection_outline_actor.SetUserMatrix(outline_mat)
            self.set_plate_warning(not self._move_preview_inside_plate(delta_xy), render=False)
            self._render_drag_preview()
        except Exception:
            pass
        self._profile_finish("move-preview", profile_started, actor_count=len(self.actors_by_name))

    def _restore_move_preview(self) -> None:
        if self._active_move is None:
            return
        base_matrices: Dict[str, Optional[object]] = self._active_move.get("base_matrices", {})
        for name, actor in self.actors_by_name.items():
            base = base_matrices.get(name)
            try:
                actor.SetUserMatrix(None if base is None else self._vtk_matrix(base))
            except Exception:
                pass
        self._active_move = None
        self._clear_hover_model_pick_cache()
        self._set_point_labels_visible(True)
        self._set_camera_drag_enabled(True)
        self._set_viewer_cursor("default")
        try:
            if self.selection_outline_actor is not None:
                self.selection_outline_actor.SetUserMatrix(None)
            self._rebuild_selection_outline()
            self.plotter.render()
        except Exception:
            pass

    def _start_move_drag(self, display_pos, *, qt_coords: bool = True) -> bool:
        start = self._display_to_plate_point(display_pos, qt_coords=qt_coords)
        if start is None:
            return False
        base_matrices = {name: self._actor_user_matrix(actor) for name, actor in self.actors_by_name.items()}
        base_bounds = self._current_model_bounds()
        self._active_move = {
            "start": _V58.np.asarray(start, dtype=_V58.np.float64),
            "delta_xy": _V58.np.zeros(2, dtype=_V58.np.float64),
            "base_matrices": base_matrices,
            "base_bounds": base_bounds,
            "qt_coords": bool(qt_coords),
        }
        self._set_point_labels_visible(False)
        self._set_camera_drag_enabled(False)
        self._set_viewer_cursor("drag")
        return True

    def _update_move_drag(self, display_pos, *, qt_coords: bool = True) -> bool:
        if self._active_move is None:
            return False
        point = self._display_to_plate_point(display_pos, qt_coords=qt_coords)
        if point is None:
            return True
        delta = _V58.np.asarray(point, dtype=_V58.np.float64) - self._active_move["start"]
        delta_xy = self._clamp_move_delta_xy([delta[0], delta[1]])
        self._active_move["delta_xy"] = delta_xy
        self._apply_move_preview(delta_xy)
        return True

    def _finish_move_drag(self) -> bool:
        if self._active_move is None:
            return False
        delta_xy = _V58.np.asarray(self._active_move.get("delta_xy", [0.0, 0.0]), dtype=_V58.np.float64)
        moved = _V58.np.linalg.norm(delta_xy) > 1e-7
        if callable(self.on_move_commit) and moved:
            effective_delta = self.on_move_commit(delta_xy)
            if effective_delta is not None:
                self._apply_move_preview(_V58.np.asarray(effective_delta, dtype=_V58.np.float64).reshape(2))
            self._active_move = None
            self._clear_hover_model_pick_cache()
            self._set_point_labels_visible(True)
            self._set_camera_drag_enabled(True)
            self._set_viewer_cursor("default")
            try:
                self._rebuild_selection_outline()
                self.plotter.render()
            except Exception:
                pass
            return True
        self._restore_move_preview()
        return True

    def _axis_vector(self, axis_name: str):
        axis = str(axis_name).lower()
        if axis == "x":
            return _V58.np.asarray([1.0, 0.0, 0.0], dtype=_V58.np.float64)
        if axis == "y":
            return _V58.np.asarray([0.0, 1.0, 0.0], dtype=_V58.np.float64)
        return _V58.np.asarray([0.0, 0.0, 1.0], dtype=_V58.np.float64)

    def _rotation_fallback_radial(self, axis_vec):
        best = None
        best_norm = -1.0
        for candidate in (
            _V58.np.asarray([1.0, 0.0, 0.0], dtype=_V58.np.float64),
            _V58.np.asarray([0.0, 1.0, 0.0], dtype=_V58.np.float64),
            _V58.np.asarray([0.0, 0.0, 1.0], dtype=_V58.np.float64),
        ):
            radial = candidate - axis_vec * float(_V58.np.dot(candidate, axis_vec))
            norm = float(_V58.np.linalg.norm(radial))
            if norm > best_norm:
                best = radial / max(norm, 1e-12)
                best_norm = norm
        return best

    def _start_rotation_drag(self, axis_name: str, pick_point, display_pos) -> bool:
        bounds = self._current_model_bounds()
        if not bounds:
            return False
        center = _V58.np.asarray(
            [
                0.5 * (bounds[0] + bounds[1]),
                0.5 * (bounds[2] + bounds[3]),
                0.5 * (bounds[4] + bounds[5]),
            ],
            dtype=_V58.np.float64,
        )
        axis_vec = self._axis_vector(axis_name)
        radial = _V58.np.asarray(pick_point, dtype=_V58.np.float64).reshape(3) - center
        radial = radial - axis_vec * float(_V58.np.dot(radial, axis_vec))
        radial_norm = float(_V58.np.linalg.norm(radial))
        if radial_norm <= 1e-9:
            radial = self._rotation_fallback_radial(axis_vec)
            radial_norm = max(float(_V58.np.linalg.norm(radial)), 1.0)
        radial = radial / max(radial_norm, 1e-12)
        tangent = _V58.np.cross(axis_vec, radial)
        tangent_norm = float(_V58.np.linalg.norm(tangent))
        if tangent_norm <= 1e-9:
            return False
        tangent = tangent / tangent_norm
        reference_point = center + radial * radial_norm
        reference_display = self._display_pos(reference_point)
        tangent_display = self._display_pos(reference_point + tangent * max(radial_norm * 0.2, 1.0)) - reference_display
        tangent_2d = _V58.np.asarray(tangent_display[:2], dtype=_V58.np.float64)
        tangent_2d_norm = float(_V58.np.linalg.norm(tangent_2d))
        if tangent_2d_norm <= 1e-9:
            return False
        tangent_2d = tangent_2d / tangent_2d_norm
        center_display = self._display_pos(center)
        ring_radius_px = max(float(_V58.np.linalg.norm(reference_display[:2] - center_display[:2])), 24.0)
        self._active_rotation = {
            "axis": axis_name,
            "axis_vector": axis_vec,
            "base_matrices": {name: self._actor_user_matrix(actor) for name, actor in self.actors_by_name.items()},
            "base_bounds": bounds,
            "base_corners": self._bounds_corners(bounds),
            "center": center,
            "prev_display": _V58.np.asarray(display_pos, dtype=_V58.np.float64),
            "tangent_display": tangent_2d,
            "ring_radius_px": ring_radius_px,
            "angle": 0.0,
            "rotation": _V58.np.eye(3, dtype=_V58.np.float64),
            "matrix": _V58.np.eye(4, dtype=_V58.np.float64),
        }
        self._set_point_labels_visible(False)
        self._set_camera_drag_enabled(False)
        self._set_viewer_cursor("drag")
        if callable(self.on_rotation_status):
            self.on_rotation_status(f"Rotate {axis_name.upper()}: drag ring; hold Shift to snap")
        return True

    def _update_rotation_drag(self, display_pos) -> bool:
        if self._active_rotation is None:
            return False
        drag = self._active_rotation
        pos = _V58.np.asarray(display_pos, dtype=_V58.np.float64)
        delta = pos - drag["prev_display"]
        drag["prev_display"] = pos
        pixels_along = float(_V58.np.dot(delta[:2], drag["tangent_display"]))
        drag["angle"] += pixels_along * (180.0 / _V58.math.pi) / float(drag["ring_radius_px"])
        angle = float(drag["angle"])
        try:
            if _V58.QtWidgets.QApplication.keyboardModifiers() & _V58.QtCore.Qt.ShiftModifier:
                angle = round(angle / 15.0) * 15.0
        except Exception:
            pass
        rotation = _V58.orthonormalize_rotation(_V58.axis_angle_rotation(drag["axis_vector"], angle))
        drag["rotation"] = rotation
        self._apply_rotation_preview(rotation)
        self._show_rotation_feedback(drag["axis"], angle, pos)
        return True

    def _apply_rotation_preview(self, rotation) -> None:
        if self._active_rotation is None:
            return
        profile_started = self._profile_start()
        drag = self._active_rotation
        rotation = _V58.orthonormalize_rotation(rotation)
        raw_matrix = self._rotation_about_point_matrix(rotation, drag["center"])
        raw_bounds = self._bounds_from_corners(drag["base_corners"], raw_matrix)
        adjust = self._plate_adjust_for_bounds(raw_bounds)
        matrix = self._translation_matrix(adjust) @ raw_matrix
        drag["matrix"] = matrix
        final_bounds = self._bounds_from_corners(drag["base_corners"], matrix)
        base_matrices: Dict[str, Optional[object]] = drag.get("base_matrices", {})
        vtk_matrix_cache = {}
        for name, actor in self.actors_by_name.items():
            base = base_matrices.get(name)
            preview = matrix if base is None else matrix @ base
            key = None if base is None else preview.tobytes()
            vtk_mat = vtk_matrix_cache.get(key)
            if vtk_mat is None:
                vtk_mat = self._vtk_matrix(preview)
                vtk_matrix_cache[key] = vtk_mat
            try:
                actor.SetUserMatrix(vtk_mat)
            except Exception:
                pass
        try:
            if self.selection_outline_actor is not None:
                outline_mat = vtk_matrix_cache.get(None)
                if outline_mat is None:
                    outline_mat = self._vtk_matrix(matrix)
                self.selection_outline_actor.SetUserMatrix(outline_mat)
            self.set_plate_warning(not self._bounds_inside_plate(final_bounds), render=False)
            self._render_drag_preview()
        except Exception:
            pass
        self._profile_finish("rotate-preview", profile_started, actor_count=len(self.actors_by_name))

    def _finish_rotation_drag(self) -> bool:
        if self._active_rotation is None:
            return False
        active = self._active_rotation
        rotation = _V58.orthonormalize_rotation(active.get("rotation", _V58.np.eye(3)))
        target_center_xy = None
        try:
            final_bounds = self._bounds_from_corners(active["base_corners"], active["matrix"])
            target_center_xy = _V58.np.asarray(
                [0.5 * (final_bounds[0] + final_bounds[1]), 0.5 * (final_bounds[2] + final_bounds[3])],
                dtype=_V58.np.float64,
            )
        except Exception:
            target_center_xy = None
        self._active_rotation = None
        self._clear_hover_model_pick_cache()
        self._set_point_labels_visible(True)
        self._hide_rotation_feedback()
        self._set_gizmo_axis_emphasis()
        self._set_camera_drag_enabled(True)
        self._set_viewer_cursor("default")
        if callable(self.on_rotation_commit) and not _V58.np.allclose(
            rotation, _V58.np.eye(3), atol=1e-6, rtol=0.0
        ):
            try:
                self.on_rotation_commit(rotation, target_center_xy)
            except TypeError:
                self.on_rotation_commit(rotation)
        try:
            if self.selection_outline_actor is not None:
                self.selection_outline_actor.SetUserMatrix(None)
            self._rebuild_selection_outline()
            if self.tool_mode == "gizmo":
                self._rebuild_gizmo()
            self.plotter.render()
        except Exception:
            pass
        return True

    def _update_hover_cursor(self, display_pos) -> None:
        if self._active_move is not None:
            self._set_viewer_cursor("drag")
            return
        if self._active_drag is not None or self._active_rotation is not None:
            self._set_viewer_cursor("drag")
            return
        if self.tool_mode == "move":
            name, _actor, _cell_id, _pick_pos = self._pick_model_for_hover(display_pos)
            self._set_viewer_cursor("move" if name is not None else "default")
            return
        if self.tool_mode == "gizmo":
            axis_name, _pick_pos, _dist = self._pick_gizmo_ring_at_display(
                display_pos,
                tolerance_px=self.ROTATION_RING_HOVER_TOLERANCE_PX,
            )
            self._set_gizmo_axis_emphasis(hover_axis=axis_name)
            self._set_viewer_cursor("rotate" if axis_name else "default")
            return
        self._set_viewer_cursor("default")

    def _on_left_button_press(self, *_args):
        pos = self._current_event_xy()
        if self.tool_mode == "normal":
            self._normal_click_press = _V58.np.asarray(pos, dtype=_V58.np.float64)
            return
        if self.tool_mode == "move":
            return
        if self.tool_mode == "gizmo":
            if getattr(self, "_gizmo_camera_passthrough", False) or self._active_rotation is not None:
                return
            name, _actor, _cell_id, pick_pos = self._pick_at_display(pos)
            axis_name = self._gizmo_axis_from_name(name)
            if axis_name is None or pick_pos is None:
                axis_name, pick_pos, _dist = self._pick_gizmo_ring_at_display(
                    pos,
                    tolerance_px=self.ROTATION_RING_PRESS_TOLERANCE_PX,
                )
            if axis_name is None or pick_pos is None:
                self._set_camera_drag_enabled(True)
                self._camera_drag_forwarded = True
                self._forward_camera_left_press()
                return
            self._start_rotation_drag(axis_name, pick_pos, pos)
            return
        if self.tool_mode in {"surface", "plate"}:
            return

    def _on_mouse_move(self, *_args):
        if (
            getattr(self, "_move_camera_passthrough", False)
            or getattr(self, "_gizmo_camera_passthrough", False)
        ) and self._left_button_is_down():
            return
        if self._v65_has_active_drag() and not self._left_button_is_down():
            self._finish_pointer_interaction()
            return
        pos = self._current_event_xy()
        if self._active_move is not None:
            return
        if self._active_rotation is not None:
            self._update_rotation_drag(pos)
            return
        if self._active_drag is not None:
            # Reuse the v60/prototype ring-drag math, but drive it with VTK event coordinates.
            fake = type("_Evt", (), {"pos": lambda _self: type("_P", (), {"x": lambda _p: pos[0], "y": lambda _p: pos[1]})()})()
            self._qt_mouse_move(fake)
            return
        if self.tool_mode in {"surface", "plate"}:
            self._update_surface_hover(pos)
            return
        self._update_hover_cursor(pos)

    def _on_left_button_release(self, *_args):
        if self._camera_drag_forwarded:
            self._camera_drag_forwarded = False
            self._forward_camera_left_release()
            return
        if self._active_move is not None:
            return
        if self._active_rotation is not None:
            self._finish_rotation_drag()
            return
        if self._active_drag is not None:
            rotation = _V58.orthonormalize_rotation(self._active_drag.rotation)
            self._active_drag = None
            self._set_point_labels_visible(True)
            self._hide_rotation_feedback()
            self._set_gizmo_axis_emphasis()
            self._clear_temp_transform()
            self._set_camera_drag_enabled(True)
            self._set_viewer_cursor("default")
            if callable(self.on_rotation_commit) and not _V58.np.allclose(
                rotation, _V58.np.eye(3), atol=1e-6, rtol=0.0
            ):
                self.on_rotation_commit(rotation)
            return
        if self.tool_mode in {"surface", "plate"}:
            self._commit_surface_pick_at_display(self._current_event_xy())
            return
        if self.tool_mode == "normal":
            release = _V58.np.asarray(self._current_event_xy(), dtype=_V58.np.float64)
            press = self._normal_click_press
            self._normal_click_press = None
            if press is not None and _V58.np.linalg.norm(release - press) <= 4.5:
                self._handle_left_click(tuple(release))

    def _on_right_button_press(self, *_args):
        self._handle_tool_cancel()

    def _on_key_press(self, *_args):
        try:
            key = str(self.plotter.iren.interactor.GetKeySym()).lower()
        except Exception:
            key = ""
        if key in {"escape", "esc"}:
            self._handle_escape()

    def _finish_pointer_interaction(self):
        had_move = self._active_move is not None
        had_rotation = self._active_drag is not None
        had_live_rotation = self._active_rotation is not None
        if had_move:
            self._finish_move_drag()
        if had_live_rotation:
            self._finish_rotation_drag()
        if had_rotation:
            self._active_drag = None
            self._clear_temp_transform()
            self._hide_rotation_feedback()
            self._set_gizmo_axis_emphasis()
        self._clear_hover_model_pick_cache()
        self._set_camera_drag_enabled(True)
        self._set_viewer_cursor("default")

    def _qt_mouse_press(self, event, *, use_interactor_pos: bool = False):
        if event.button() != _V58.QtCore.Qt.LeftButton:
            return False
        candidates = self._event_display_candidates(event, use_interactor_pos=use_interactor_pos)
        if not candidates:
            return False
        qt_pos = candidates[0][0]
        qt_display = (float(qt_pos[0]), float(qt_pos[1]))
        if self.tool_mode == "move":
            vtk_display = self._qt_to_vtk_display(qt_display)
            name, _actor, _cell_id, _pick_pos = self._pick_model_for_press(vtk_display)
            if name is None:
                self._active_move = None
                self._move_camera_passthrough = True
                self._clear_hover_model_pick_cache()
                self._set_camera_drag_enabled(True)
                self._set_viewer_cursor("default")
                return False
            if callable(self.on_model_pick):
                self.on_model_pick(name)
            self._move_camera_passthrough = False
            if self._start_move_drag(vtk_display, qt_coords=False):
                return True
            self._set_camera_drag_enabled(True)
            self._set_viewer_cursor("default")
            return False
        if self.tool_mode == "gizmo":
            vtk_display = self._qt_to_vtk_display(qt_display)
            axis_name, pick_pos, _dist = self._pick_gizmo_ring_at_display(
                qt_display,
                tolerance_px=self.ROTATION_RING_PRESS_TOLERANCE_PX,
            )
            if axis_name is None or pick_pos is None:
                for pos, _flag in candidates:
                    display = (float(pos[0]), float(pos[1]))
                    name, _actor, _cell_id, candidate_pick = self._pick_at_display(display)
                    axis_name = self._gizmo_axis_from_name(name)
                    if axis_name is not None and candidate_pick is not None:
                        pick_pos = candidate_pick
                        break
            if axis_name is None or pick_pos is None:
                self._set_camera_drag_enabled(True)
                return False
            self._gizmo_camera_passthrough = False
            return self._start_rotation_drag(axis_name, pick_pos, vtk_display)
        if self.tool_mode in {"surface", "plate"}:
            return True
        return bool(super()._qt_mouse_press(event, use_interactor_pos=use_interactor_pos) or False)

    def _qt_mouse_move(self, event):
        if self._active_move is not None:
            pos = self._qt_to_vtk_display((float(event.pos().x()), float(event.pos().y())))
            return self._update_move_drag(pos, qt_coords=False)
        if self._active_rotation is not None:
            pos = self._qt_to_vtk_display((float(event.pos().x()), float(event.pos().y())))
            return self._update_rotation_drag(pos)
        pos = (float(event.pos().x()), float(event.pos().y()))
        if self._active_drag is not None:
            self._set_viewer_cursor("drag")
            super()._qt_mouse_move(event)
            return True
        if self.tool_mode == "move":
            vtk_display = self._qt_to_vtk_display((float(event.pos().x()), float(event.pos().y())))
            name, _actor, _cell_id, _pick_pos = self._pick_model_for_hover(vtk_display)
            self._set_viewer_cursor("move" if name is not None else "default")
            return False
        if self.tool_mode == "gizmo":
            axis_name, _pick_pos, _dist = self._pick_gizmo_ring_at_display(
                pos,
                tolerance_px=self.ROTATION_RING_HOVER_TOLERANCE_PX,
            )
            self._set_gizmo_axis_emphasis(hover_axis=axis_name)
            self._set_viewer_cursor("grab" if axis_name else "default")
            return False
        if self.tool_mode in {"surface", "plate"}:
            super()._qt_mouse_move(event)
            return True
        self._set_viewer_cursor("default")
        super()._qt_mouse_move(event)
        return False

    def _qt_mouse_release(self, event, *, use_interactor_pos: bool = False):
        if event.button() != _V58.QtCore.Qt.LeftButton:
            return False
        if self._active_move is not None:
            return self._finish_move_drag()
        if self._active_rotation is not None:
            return self._finish_rotation_drag()
        if self._active_drag is not None:
            super()._qt_mouse_release(event, use_interactor_pos=use_interactor_pos)
            self._set_viewer_cursor("default")
            try:
                self.plotter.iren.enable_trackball_style()
            except Exception:
                pass
            return True
        if self.tool_mode in {"surface", "plate"}:
            super()._qt_mouse_release(event, use_interactor_pos=use_interactor_pos)
            return True
        return bool(super()._qt_mouse_release(event, use_interactor_pos=use_interactor_pos) or False)

    def _handle_tool_cancel(self) -> bool:
        handled = False
        if self._active_move is not None:
            self._restore_move_preview()
            handled = True
        if self._active_rotation is not None:
            self._finish_rotation_drag()
            handled = True
        if self._active_drag is not None:
            self._clear_temp_transform()
            self._active_drag = None
            self._set_point_labels_visible(True)
            self._hide_rotation_feedback()
            self._set_gizmo_axis_emphasis()
            handled = True
        if self.tool_mode != "normal":
            if callable(self.on_tool_cancel):
                self.on_tool_cancel()
            else:
                self.set_tool_mode("normal")
            handled = True
        self._set_viewer_cursor("default")
        return handled

    def _handle_escape(self) -> bool:
        return self._handle_tool_cancel()

    def _current_model_bounds(self):
        bounds = []
        for actor in getattr(self, "actors_by_name", {}).values():
            try:
                b = actor.GetBounds()
                if b:
                    vals = tuple(float(v) for v in b)
                    if all(_V58.math.isfinite(v) for v in vals):
                        bounds.append(vals)
            except Exception:
                pass
        if not bounds:
            return None
        return (
            min(b[0] for b in bounds),
            max(b[1] for b in bounds),
            min(b[2] for b in bounds),
            max(b[3] for b in bounds),
            min(b[4] for b in bounds),
            max(b[5] for b in bounds),
        )

    def _clear_selection_outline(self):
        if self.plotter and self.selection_outline_actor is not None:
            try:
                self.plotter.remove_actor(self.selection_outline_actor, reset_camera=False)
            except Exception:
                pass
        self.selection_outline_actor = None

    def _rebuild_selection_outline(self):
        self._clear_selection_outline()
        if not self.plotter or not self._whole_model_highlight:
            return
        b = self._current_model_bounds()
        if not b:
            return
        if (b[1] - b[0]) <= 1e-9 or (b[3] - b[2]) <= 1e-9 or (b[5] - b[4]) <= 1e-9:
            return
        try:
            outline = _V58.pv.Cube(bounds=b).extract_all_edges()
            self.selection_outline_actor = self.plotter.add_mesh(
                outline,
                name="__v65_selection_outline__",
                color="#f2c94c",
                line_width=4,
                lighting=False,
                pickable=False,
                reset_camera=False,
            )
        except Exception:
            self.selection_outline_actor = None

    def set_whole_model_highlight(self, enabled: bool):
        self._whole_model_highlight = bool(enabled)
        for actor in getattr(self, "actors_by_name", {}).values():
            try:
                actor.prop.show_edges = bool(enabled)
                actor.prop.edge_color = "#f2c94c" if enabled else "#343a42"
                actor.prop.line_width = 1.4 if enabled else 0.6
            except Exception:
                pass
        if enabled:
            self._rebuild_selection_outline()
        else:
            self._clear_selection_outline()

    def set_plate_warning(self, warning: bool, *, render: bool = True):
        warning = bool(warning)
        if self._plate_warning_state is warning:
            return
        self._plate_warning_state = warning
        color = "#d65a31" if warning else "#4d5966"
        try:
            if self.plate_boundary_actor is not None:
                self.plate_boundary_actor.prop.color = _V58._hex_to_rgb01(color)
        except Exception:
            try:
                self.plate_boundary_actor.prop.color = color
            except Exception:
                pass
        try:
            if render and self.plotter:
                self.plotter.render()
        except Exception:
            pass

    def set_camera_view(self, view: str):
        if not self.plotter:
            return
        bounds = self._current_model_bounds()
        if bounds:
            center = _V58.np.asarray(
                [
                    0.5 * (bounds[0] + bounds[1]),
                    0.5 * (bounds[2] + bounds[3]),
                    0.5 * (bounds[4] + bounds[5]),
                ],
                dtype=_V58.np.float64,
            )
            span = max(
                bounds[1] - bounds[0],
                bounds[3] - bounds[2],
                bounds[5] - bounds[4],
                float(_V58.PLATE_WIDTH_MM),
                float(_V58.PLATE_DEPTH_MM),
                20.0,
            )
        else:
            center = _V58.np.asarray(
                [float(_V58.PLATE_WIDTH_MM) * 0.5, float(_V58.PLATE_DEPTH_MM) * 0.5, 8.0],
                dtype=_V58.np.float64,
            )
            span = max(float(_V58.PLATE_WIDTH_MM), float(_V58.PLATE_DEPTH_MM), 20.0)
        distance = float(span) * 1.65
        cx, cy, cz = [float(v) for v in center]
        view = (view or "iso").lower()
        if view == "top":
            camera = (cx, cy, cz + distance)
            up = (0.0, 1.0, 0.0)
        elif view == "front":
            camera = (cx, cy - distance, cz + distance * 0.12)
            up = (0.0, 0.0, 1.0)
        elif view == "right":
            camera = (cx + distance, cy, cz + distance * 0.12)
            up = (0.0, 0.0, 1.0)
        elif view == "left":
            camera = (cx - distance, cy, cz + distance * 0.12)
            up = (0.0, 0.0, 1.0)
        else:
            camera = (cx + distance * 0.78, cy - distance * 0.86, cz + distance * 0.58)
            up = (0.0, 0.0, 1.0)
        try:
            self.plotter.camera_position = [camera, (cx, cy, cz), up]
            self.plotter.camera.SetViewAngle(32.0)
            self.plotter.reset_camera_clipping_range()
            self.plotter.render()
        except Exception:
            pass


_V58.EmbeddedPVViewer = EmbeddedPVViewerV65


class QtAssignColorsApp(_V62.QtAssignColorsApp):
    def __init__(self):
        self.pending_target_center_xy = None
        self.applied_target_center_xy = None
        super().__init__()
        self.setWindowTitle("LayerLoom — Color Assigner v65")

    def _copy_xy(self, value):
        if value is None:
            return None
        return _V58.np.asarray(value, dtype=_V58.np.float64).reshape(2).copy()

    def _xy_equal(self, left, right, tol: float = 1e-7) -> bool:
        if left is None and right is None:
            return True
        if left is None or right is None:
            return False
        return bool(_V58.np.allclose(self._copy_xy(left), self._copy_xy(right), atol=tol, rtol=0.0))

    def _build_viewer_overlays(self):
        super()._build_viewer_overlays()
        try:
            self.cancel_tool_btn.setText("View")
            self.cancel_tool_btn.setToolTip("Return to selection and camera navigation.")
            self.place_tool_btn.setText("Place")
            self.place_tool_btn.setToolTip("Pick a face to place it on the build plate.")
            self.cancel_tool_btn.setCheckable(True)
            self.rotate_tool_btn.setCheckable(True)
            self.place_tool_btn.setCheckable(True)
        except Exception:
            pass
        self.move_tool_btn = _V58.QtWidgets.QToolButton(text="Move")
        self.move_tool_btn.setCheckable(True)
        self.move_tool_btn.clicked.connect(self._activate_move_tool)
        self.auto_place_btn = _V58.QtWidgets.QToolButton(text="Auto")
        self.auto_place_btn.setToolTip("Scale down if needed, center on the build plate, and drop to Z=0.")
        self.auto_place_btn.clicked.connect(self._auto_place_model)
        self.fit_plate_btn = _V58.QtWidgets.QToolButton(text="Fit")
        self.fit_plate_btn.setToolTip("Scale down to fit within the build-plate margin; never enlarges.")
        self.fit_plate_btn.clicked.connect(self._fit_model_to_plate)
        self.center_plate_btn = _V58.QtWidgets.QToolButton(text="Center")
        self.center_plate_btn.setToolTip("Center the model footprint on the build plate.")
        self.center_plate_btn.clicked.connect(self._center_model_on_plate)
        self.drop_plate_btn = _V58.QtWidgets.QToolButton(text="Drop")
        self.drop_plate_btn.setToolTip("Ground the current transform on the top of the build plate.")
        self.drop_plate_btn.clicked.connect(self._drop_model_to_plate)
        try:
            action_row = self.viewer_top_overlay.layout().itemAt(0).layout()
            ordered = (
                self.cancel_tool_btn,
                self.move_tool_btn,
                self.rotate_tool_btn,
                self.place_tool_btn,
                self.auto_place_btn,
                self.fit_plate_btn,
                self.center_plate_btn,
                self.drop_plate_btn,
                self.reset_transform_btn,
            )
            for btn in ordered + (self.apply_transform_btn,):
                action_row.removeWidget(btn)
            for idx, btn in enumerate(ordered):
                action_row.insertWidget(idx, btn)
            self.apply_transform_btn.hide()
            self.viewer_top_overlay.adjustSize()
            self._position_viewer_overlays()
        except Exception:
            pass
        self._refresh_tool_button_states()

    def _tool_mode_label(self, mode: Optional[str] = None) -> str:
        mode = (mode or self.viewer_tool_mode or "normal").lower()
        if mode == "move":
            return "Move"
        return super()._tool_mode_label(mode)

    def _refresh_tool_button_states(self):
        mode = (getattr(self, "viewer_tool_mode", "normal") or "normal").lower()
        for attr, checked in (
            ("cancel_tool_btn", mode == "normal"),
            ("move_tool_btn", mode == "move"),
            ("rotate_tool_btn", mode == "gizmo"),
            ("place_tool_btn", mode == "surface"),
        ):
            btn = getattr(self, attr, None)
            if btn is None:
                continue
            try:
                blocked = btn.blockSignals(True)
                btn.setChecked(bool(checked))
                btn.blockSignals(blocked)
            except Exception:
                pass

    def _set_viewer_tool_mode(self, mode: str):
        result = super()._set_viewer_tool_mode(mode)
        self._refresh_tool_button_states()
        return result

    def _transform_is_default_now(self) -> bool:
        return bool(super()._transform_is_default_now() and self.pending_target_center_xy is None)

    def _update_transform_flags(self):
        super()._update_transform_flags()
        target_dirty = not self._xy_equal(self.pending_target_center_xy, self.applied_target_center_xy)
        self.transform_is_default = bool(self.transform_is_default and self.pending_target_center_xy is None)
        self.transform_dirty = bool(self.transform_dirty or (bool(self.base_file) and target_dirty))

    def _refresh_transform_readout(self, plan=None):
        super()._refresh_transform_readout(plan)
        if not self.base_file or self.viewer_tool_mode != "move" or not hasattr(self, "transform_info_label"):
            return
        text = self.transform_info_label.text()
        tip = "drag the model to move; drag empty space to navigate"
        if tip not in text:
            self.transform_info_label.setText(f"{text} | {tip}")

    def _update_transform_ui_state(self):
        super()._update_transform_ui_state()
        if not hasattr(self, "move_tool_btn"):
            return
        values = self._get_transform_values()
        enabled = bool(self.base_file) and not self.large_model_mode and values is not None
        for attr in ("move_tool_btn", "auto_place_btn", "fit_plate_btn", "center_plate_btn", "drop_plate_btn"):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setEnabled(enabled)
        try:
            self.cancel_tool_btn.setEnabled(bool(self.base_file))
        except Exception:
            pass
        self._refresh_tool_button_states()

    def _plate_margin_mm(self) -> float:
        return 8.0

    def _plate_center_xy(self):
        return _V58.np.asarray(
            [_V58.PLATE_WIDTH_MM * 0.5, _V58.PLATE_DEPTH_MM * 0.5],
            dtype=_V58.np.float64,
        )

    def _usable_plate_size_xy(self):
        margin = self._plate_margin_mm()
        return _V58.np.asarray(
            [
                max(float(_V58.PLATE_WIDTH_MM) - 2.0 * margin, 1e-9),
                max(float(_V58.PLATE_DEPTH_MM) - 2.0 * margin, 1e-9),
            ],
            dtype=_V58.np.float64,
        )

    def _set_scale_value(self, scale: float) -> None:
        scale = max(float(scale), 1e-9)
        self._setting_transform_widgets = True
        try:
            self.scale_spin.setValue(scale)
        finally:
            self._setting_transform_widgets = False

    def _pending_transform_plan(self, *, target_center_xy=None, scale=None):
        if not self.base_file:
            return None
        use_scale = self._current_scale_value(raise_on_error=True) if scale is None else float(scale)
        return _V58.compute_transform_plan(
            self.base_file,
            scale=use_scale,
            orientation_matrix=self.pending_rotation_matrix,
            plate_width=_V58.PLATE_WIDTH_MM,
            plate_depth=_V58.PLATE_DEPTH_MM,
            center_xy=False,
            target_center_xy=self.pending_target_center_xy if target_center_xy is None else target_center_xy,
        )

    def _footprint_inside_plate(self, plan) -> bool:
        if plan is None:
            return True
        b = plan.transformed_bounds
        return bool(
            b.min_corner[0] >= -1e-6
            and b.min_corner[1] >= -1e-6
            and b.max_corner[0] <= float(_V58.PLATE_WIDTH_MM) + 1e-6
            and b.max_corner[1] <= float(_V58.PLATE_DEPTH_MM) + 1e-6
        )

    def _clamped_target_for_plan(self, target_xy, plan):
        if plan is None:
            return self._copy_xy(target_xy)
        target = self._copy_xy(target_xy)
        bounds = plan.transformed_bounds
        size = bounds.size[:2]
        margin = self._plate_margin_mm()
        usable = self._usable_plate_size_xy()
        if _V58.np.any(size > usable + 1e-6):
            return self._plate_center_xy()
        min_xy = _V58.np.asarray([margin, margin], dtype=_V58.np.float64)
        max_xy = _V58.np.asarray(
            [float(_V58.PLATE_WIDTH_MM) - margin, float(_V58.PLATE_DEPTH_MM) - margin],
            dtype=_V58.np.float64,
        )
        low_center = min_xy + size * 0.5
        high_center = max_xy - size * 0.5
        return _V58.np.minimum(_V58.np.maximum(target, low_center), high_center)

    def _clamp_pending_xy(self) -> None:
        plan = self._pending_transform_plan()
        if plan is None:
            return
        target = self.pending_target_center_xy
        if target is None:
            target = plan.transformed_bounds.center[:2]
        target = self._clamped_target_for_plan(target, plan)
        self.pending_target_center_xy = target

    def _refresh_plate_warning(self, plan=None) -> None:
        plan = plan or self._last_transform_plan
        if self.viewer is None or plan is None:
            return
        try:
            self.viewer.set_plate_warning(not self._footprint_inside_plate(plan))
        except Exception:
            pass

    def _apply_placement_transform(self, *, message: Optional[str] = None) -> bool:
        self._clamp_pending_xy()
        ok = self._apply_transform_preview(quiet=False)
        if ok and message:
            try:
                self.status_bar.showMessage(message, 5000)
            except Exception:
                pass
        return ok

    def _auto_place_model(self):
        if not self._can_place_transform("Auto Place"):
            return
        scale = self._current_scale_value(raise_on_error=True)
        current = self._pending_transform_plan(scale=scale)
        factor = 1.0
        if current is not None:
            size = _V58.np.maximum(current.transformed_bounds.size[:2], 1e-12)
            usable = self._usable_plate_size_xy()
            factor = min(1.0, float(min(usable[0] / size[0], usable[1] / size[1])))
        if factor < 1.0 - 1e-12:
            scale *= factor
            self._set_scale_value(scale)
        self.pending_target_center_xy = self._plate_center_xy()
        self._apply_placement_transform(
            message=(
                f"Auto placed and scaled to {scale:.4g}"
                if factor < 1.0 - 1e-12
                else "Auto placed on build plate"
            )
        )

    def _fit_model_to_plate(self):
        if not self._can_place_transform("Fit To Plate"):
            return
        scale = self._current_scale_value(raise_on_error=True)
        current = self._pending_transform_plan(scale=scale)
        factor = 1.0
        if current is not None:
            size = _V58.np.maximum(current.transformed_bounds.size[:2], 1e-12)
            usable = self._usable_plate_size_xy()
            factor = min(1.0, float(min(usable[0] / size[0], usable[1] / size[1])))
        if factor < 1.0 - 1e-12:
            scale *= factor
            self._set_scale_value(scale)
        self.pending_target_center_xy = self._plate_center_xy()
        self._apply_placement_transform(
            message="Already fits plate" if factor >= 1.0 - 1e-12 else f"Scaled by {factor:.4g} to fit"
        )

    def _center_model_on_plate(self):
        if not self._can_place_transform("Center"):
            return
        self.pending_target_center_xy = self._plate_center_xy()
        self._apply_placement_transform(message="Centered on build plate")

    def _drop_model_to_plate(self):
        if not self._can_place_transform("Drop"):
            return
        # compute_transform_plan always grounds the transformed min Z at the plate.
        self._apply_placement_transform(message="Dropped to build plate")

    def _can_place_transform(self, title: str) -> bool:
        if not self.base_file:
            self._warn(title, "Open a .3mf first.")
            return False
        if self.large_model_mode:
            self._warn(title, f"{title} is disabled in large-file safety mode.\n\n{self.large_model_reason}")
            return False
        try:
            self._current_scale_value(raise_on_error=True)
        except Exception as exc:
            self._warn(title, str(exc))
            return False
        return True

    def _materialize_current_model(self, output_path: str, *, include_assignments: bool):
        if not self.base_file:
            raise RuntimeError("Open a .3mf first.")
        scale = self._current_scale_value(raise_on_error=True)
        t_plan = _V58.time.perf_counter()
        plan = _V58.compute_transform_plan(
            self.base_file,
            scale=scale,
            orientation_matrix=self.pending_rotation_matrix,
            plate_width=_V58.PLATE_WIDTH_MM,
            plate_depth=_V58.PLATE_DEPTH_MM,
            center_xy=False,
            target_center_xy=self.pending_target_center_xy,
        )
        _V58._perf_log("transform plan", t_plan, extra=f"include_assignments={include_assignments}")
        t_write = _V58.time.perf_counter()
        _V58.write_transformed_3mf(
            self.base_file,
            output_path,
            plan.global_matrix,
            name_updates=self._build_name_updates() if include_assignments else None,
            metadata_updates=self._build_assignment_metadata_updates() if include_assignments else None,
        )
        _V58._perf_log("transform write", t_write, extra=os.path.basename(output_path))
        return plan

    def _apply_transform_preview(self, quiet: bool = False) -> bool:
        ok = super()._apply_transform_preview(quiet=quiet)
        if ok:
            self.applied_target_center_xy = self._copy_xy(self.pending_target_center_xy)
            self._update_transform_flags()
            self._refresh_transform_readout(self._last_transform_plan)
            self._refresh_plate_warning(self._last_transform_plan)
        return ok

    def _refresh_preview_from_current_file(self, *, force: bool = False):
        mode_before = getattr(self, "viewer_tool_mode", "normal")
        super()._refresh_preview_from_current_file(force=force)
        if mode_before in {"move", "gizmo", "surface"} and self.viewer is not None and self.base_file and not self.large_model_mode:
            self.viewer_tool_mode = mode_before
            self._sync_viewer_tool_mode()

    def _sync_viewer_tool_mode(self):
        if self.viewer is not None:
            center, radius = self._viewer_object_center_and_radius()
            if self.pending_target_center_xy is not None:
                center = _V58.np.asarray(center, dtype=_V58.np.float64).copy()
                target = self._copy_xy(self.pending_target_center_xy)
                center[0] = float(target[0])
                center[1] = float(target[1])
            self.viewer.set_callbacks(
                on_rotation_commit=self._apply_rotation_delta,
                on_surface_commit=self._on_surface_rotation,
                on_move_commit=self._apply_move_delta,
                on_tool_cancel=lambda: self._set_viewer_tool_mode("normal"),
            )
            self.viewer.set_tool_mode(self.viewer_tool_mode, center=center, radius=radius)
            self._update_transform_ui_state()
            self._refresh_status_summary()
            self._refresh_transform_readout(self._last_transform_plan)
            self._refresh_plate_warning(self._last_transform_plan)
            return
        return super()._sync_viewer_tool_mode()

    def _activate_move_tool(self):
        if not self.base_file:
            self._warn("Move", "Open a .3mf first.")
            return
        if self.large_model_mode:
            self._warn("Move", f"Move is disabled in large-file safety mode.\n\n{self.large_model_reason}")
            return
        if not self._ensure_transform_preview_current(quiet=False):
            return
        self._set_viewer_tool_mode("move")

    def _apply_move_delta(self, delta_xy):
        delta = _V58.np.asarray(delta_xy, dtype=_V58.np.float64).reshape(2)
        if self.pending_target_center_xy is not None:
            current_xy = self._copy_xy(self.pending_target_center_xy)
        elif self._last_transform_plan is not None:
            current_xy = _V58.np.asarray(self._last_transform_plan.transformed_bounds.center[:2], dtype=_V58.np.float64)
        else:
            center, _radius = self._viewer_object_center_and_radius()
            current_xy = _V58.np.asarray(center[:2], dtype=_V58.np.float64)
        desired_xy = current_xy + delta
        desired_plan = self._pending_transform_plan(target_center_xy=desired_xy)
        self.pending_target_center_xy = self._clamped_target_for_plan(desired_xy, desired_plan)
        self._update_transform_flags()
        pending_plan = self._pending_transform_plan()
        if pending_plan is not None:
            self._last_transform_plan = pending_plan
        self._refresh_transform_readout(self._last_transform_plan)
        self._refresh_plate_warning(self._last_transform_plan)
        return self.pending_target_center_xy - current_xy

    def _apply_rotation_delta(self, delta_rotation, target_center_xy=None):
        current = _V58.orthonormalize_rotation(self.pending_rotation_matrix)
        self.pending_rotation_matrix = _V58.orthonormalize_rotation(
            _V58.np.asarray(delta_rotation, dtype=_V58.np.float64) @ current
        )
        if target_center_xy is not None:
            self.pending_target_center_xy = self._copy_xy(target_center_xy)
        self._sync_rotation_fields_from_matrix(self.pending_rotation_matrix)
        self._clamp_pending_xy()
        self._update_transform_flags()
        pending_plan = self._pending_transform_plan()
        if pending_plan is not None:
            self._last_transform_plan = pending_plan
        self._refresh_transform_readout(self._last_transform_plan)
        self._refresh_plate_warning(self._last_transform_plan)

    def _reset_transform_to_default(self):
        self.pending_target_center_xy = None
        result = super()._reset_transform_to_default()
        if self.large_model_mode:
            self.applied_target_center_xy = None
        return result

    def _load_canonical_model(self, stamped_path: str):
        self.pending_target_center_xy = None
        self.applied_target_center_xy = None
        return super()._load_canonical_model(stamped_path)


def main():
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        script_dir = os.getcwd()

    _V58.APP_TITLE = "LayerLoom — Color Assigner v65"
    _V58.PALETTES_DIR = os.path.join(script_dir, "palettes")
    if not os.path.isdir(_V58.PALETTES_DIR):
        _V58._log("WARN", f"'palettes' directory not found at {_V58.PALETTES_DIR}. Using dummy palette data.")
        _V58.PALETTES_DIR = ""
    _V58.PALETTE_FILES = {
        "Simple": os.path.join(_V58.PALETTES_DIR, "simple_palette.json"),
        "Normal": os.path.join(_V58.PALETTES_DIR, "normal_palette.json"),
        "Full": os.path.join(_V58.PALETTES_DIR, "full_palette.json"),
        _V58.CALIBRATED_CMY_NORMAL_NAME: os.path.join(
            _V58.PALETTES_DIR,
            _V58.CALIBRATED_CMY_NORMAL_FILENAME,
        ),
    }

    try:
        if hasattr(sys, "_MEIPASS") and _V58.trimesh is not None:
            _V58.trimesh.constants.GLTF_VALIDATOR = os.path.join(sys._MEIPASS, "gltf_validator")
    except Exception:
        pass

    if not _V58._QT_OK:
        raise RuntimeError("Qt dependencies are unavailable; cannot launch v65.")

    app = _V58.QtWidgets.QApplication.instance() or _V58.QtWidgets.QApplication(sys.argv)
    app.setApplicationName("LayerLoom")
    app.setStyle("Fusion")

    palette = _V58.QtGui.QPalette()
    palette.setColor(_V58.QtGui.QPalette.Window, _V58.QtGui.QColor(31, 31, 31))
    palette.setColor(_V58.QtGui.QPalette.WindowText, _V58.QtGui.QColor(233, 233, 233))
    palette.setColor(_V58.QtGui.QPalette.Base, _V58.QtGui.QColor(23, 23, 23))
    palette.setColor(_V58.QtGui.QPalette.AlternateBase, _V58.QtGui.QColor(30, 30, 30))
    palette.setColor(_V58.QtGui.QPalette.ToolTipBase, _V58.QtGui.QColor(35, 35, 35))
    palette.setColor(_V58.QtGui.QPalette.ToolTipText, _V58.QtGui.QColor(240, 240, 240))
    palette.setColor(_V58.QtGui.QPalette.Text, _V58.QtGui.QColor(233, 233, 233))
    palette.setColor(_V58.QtGui.QPalette.Button, _V58.QtGui.QColor(43, 43, 43))
    palette.setColor(_V58.QtGui.QPalette.ButtonText, _V58.QtGui.QColor(233, 233, 233))
    palette.setColor(_V58.QtGui.QPalette.BrightText, _V58.QtGui.QColor(255, 255, 255))
    palette.setColor(_V58.QtGui.QPalette.Highlight, _V58.QtGui.QColor(57, 82, 106))
    palette.setColor(_V58.QtGui.QPalette.HighlightedText, _V58.QtGui.QColor(255, 255, 255))
    palette.setColor(_V58.QtGui.QPalette.Disabled, _V58.QtGui.QPalette.Text, _V58.QtGui.QColor(176, 176, 176))
    palette.setColor(_V58.QtGui.QPalette.Disabled, _V58.QtGui.QPalette.ButtonText, _V58.QtGui.QColor(176, 176, 176))
    palette.setColor(_V58.QtGui.QPalette.Disabled, _V58.QtGui.QPalette.WindowText, _V58.QtGui.QColor(176, 176, 176))
    palette.setColor(_V58.QtGui.QPalette.Disabled, _V58.QtGui.QPalette.HighlightedText, _V58.QtGui.QColor(220, 220, 220))
    app.setPalette(palette)

    window = QtAssignColorsApp()
    window.setStyleSheet(
        window.styleSheet()
        + """
        QLabel { color: #e9e9e9; }
        QPushButton:disabled, QToolButton:disabled, QCheckBox:disabled {
            color: #b0b0b0;
            background: #4a4a4a;
            border: 1px solid #666666;
        }
        QComboBox:disabled, QDoubleSpinBox:disabled, QSpinBox:disabled, QLineEdit:disabled {
            color: #b8b8b8;
            background: #2a2a2a;
            border: 1px solid #555555;
        }
        QTreeWidget, QAbstractItemView, QHeaderView::section {
            color: #f0f0f0;
        }
        """
    )
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
