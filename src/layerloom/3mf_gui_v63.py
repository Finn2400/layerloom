#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LayerLoom v63
-------------

v62 palette behavior plus whole-model Move and refined Rotate interactions in
the existing embedded build-plate viewer.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from typing import Dict, Optional


_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_V62_PATH = os.path.join(_THIS_DIR, "3mf_gui_v62.py")
_SPEC = importlib.util.spec_from_file_location("layerloom_gui_v62_runtime_for_v63", _V62_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Could not load v62 base module from {_V62_PATH}")
_V62 = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _V62
_SPEC.loader.exec_module(_V62)
_V58 = _V62._V58
_BASE_VIEWER = _V58.EmbeddedPVViewer


class V63InteractiveQtInteractor(_V58.QtInteractor):
    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._owner = owner

    def mousePressEvent(self, event):
        if event.button() == _V58.QtCore.Qt.RightButton and self._owner._handle_tool_cancel():
            event.accept()
            return
        if event.button() == _V58.QtCore.Qt.LeftButton and self._owner.tool_mode == "move":
            if self._owner._qt_mouse_press(event):
                event.accept()
                return
            super().mousePressEvent(event)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._owner._active_move is not None:
            self._owner._qt_mouse_move(event)
            event.accept()
            return
        super().mouseMoveEvent(event)
        if self._owner.tool_mode == "move":
            self._owner._qt_mouse_move(event)

    def mouseReleaseEvent(self, event):
        if event.button() == _V58.QtCore.Qt.LeftButton and self._owner._active_move is not None:
            self._owner._qt_mouse_release(event)
            event.accept()
            return
        super().mouseReleaseEvent(event)

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


class EmbeddedPVViewerV63(_BASE_VIEWER):
    def __init__(self, parent=None):
        _V58.PVWindow.__init__(self)
        self.plotter = V63InteractiveQtInteractor(self, parent)
        self._configured = False
        self._layer_height = 0.16
        self._layer_scale = 1.0
        self._layer_preview_actors = []
        self._rotation_feedback_label = None
        self._hover_gizmo_axis = None
        self._active_gizmo_axis = None
        self.on_rotation_preview = None
        self.on_rotation_status = None
        self.on_move_commit = None
        self.on_tool_cancel = None
        self._active_move = None
        self._camera_drag_forwarded = False
        self._cursor_kind = "default"
        self.selection_outline_actor = None
        self.plate_boundary_actor = None
        self.plate_margin_actor = None
        self._whole_model_highlight = False
        self._v63_observer_ids = []

    def _ensure_plotter(self):
        plotter = super()._ensure_plotter()
        self._ensure_plate_boundary()
        return plotter

    def _teardown_scene(self):
        self._clear_selection_outline()
        self._whole_model_highlight = False
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
                    name="__v63_plate_boundary__",
                    color="#4d5966",
                    line_width=3,
                    lighting=False,
                    pickable=False,
                    reset_camera=False,
                )
            if margin_mesh is not None:
                self.plate_margin_actor = self.plotter.add_mesh(
                    margin_mesh,
                    name="__v63_plate_margin__",
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
        on_rotation_preview=None,
        on_rotation_status=None,
        on_move_commit=None,
        on_tool_cancel=None,
    ):
        super().set_callbacks(
            on_pick=on_pick,
            on_rotation_commit=on_rotation_commit,
            on_surface_commit=on_surface_commit,
            on_rotation_preview=on_rotation_preview,
            on_rotation_status=on_rotation_status,
        )
        if on_move_commit is not None:
            self.on_move_commit = on_move_commit
        if on_tool_cancel is not None:
            self.on_tool_cancel = on_tool_cancel

    def set_tool_mode(self, mode: str, *, center=None, radius=None):
        self.tool_mode = (mode or "normal").lower()
        if center is not None:
            self.gizmo_center = _V58.np.asarray(center, dtype=_V58.np.float64).reshape(3)
        if radius is not None and _V58.math.isfinite(radius):
            self.gizmo_radius = max(float(radius), 1.0)

        self._clear_builtin_picking()
        self._remove_raw_interaction_handlers()
        self._ensure_experimental_interaction_observers()
        self._restore_move_preview()
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

    def _v63_has_active_drag(self) -> bool:
        return self._active_move is not None or self._active_drag is not None

    def _ensure_experimental_interaction_observers(self):
        if not self.plotter or self._v63_observer_ids:
            return
        try:
            vtk = _V58._get_vtk()
            if self._picker is None:
                self._picker = vtk.vtkCellPicker()
                self._picker.SetTolerance(0.0005)
            self._v63_observer_ids = [
                self.plotter.iren.add_observer("LeftButtonPressEvent", self._on_left_button_press),
                self.plotter.iren.add_observer("MouseMoveEvent", self._on_mouse_move),
                self.plotter.iren.add_observer("LeftButtonReleaseEvent", self._on_left_button_release),
                self.plotter.iren.add_observer("RightButtonPressEvent", self._on_right_button_press),
                self.plotter.iren.add_observer("KeyPressEvent", self._on_key_press),
            ]
        except Exception:
            self._v63_observer_ids = []

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

    def _apply_move_preview(self, delta_xy) -> None:
        if self._active_move is None:
            return
        mat = _V58.np.eye(4, dtype=_V58.np.float64)
        mat[0, 3] = float(delta_xy[0])
        mat[1, 3] = float(delta_xy[1])
        base_matrices: Dict[str, Optional[object]] = self._active_move.get("base_matrices", {})
        for name, actor in self.actors_by_name.items():
            base = base_matrices.get(name)
            preview = mat if base is None else mat @ base
            try:
                actor.SetUserMatrix(self._vtk_matrix(preview))
            except Exception:
                pass
        try:
            self._rebuild_selection_outline()
            self.plotter.render()
        except Exception:
            pass

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
        self._set_point_labels_visible(True)
        self._set_camera_drag_enabled(True)
        self._set_viewer_cursor("default")
        try:
            self._rebuild_selection_outline()
            self.plotter.render()
        except Exception:
            pass

    def _start_move_drag(self, display_pos, *, qt_coords: bool = True) -> bool:
        start = self._display_to_plate_point(display_pos, qt_coords=qt_coords)
        if start is None:
            return False
        base_matrices = {name: self._actor_user_matrix(actor) for name, actor in self.actors_by_name.items()}
        self._active_move = {
            "start": _V58.np.asarray(start, dtype=_V58.np.float64),
            "delta_xy": _V58.np.zeros(2, dtype=_V58.np.float64),
            "base_matrices": base_matrices,
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
        delta_xy = _V58.np.asarray([delta[0], delta[1]], dtype=_V58.np.float64)
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

    def _update_hover_cursor(self, display_pos) -> None:
        if self._active_move is not None:
            self._set_viewer_cursor("drag")
            return
        if self._active_drag is not None:
            self._set_viewer_cursor("drag")
            return
        if self.tool_mode == "move":
            picked = self._pick_part_at_display(display_pos)
            self._set_viewer_cursor("move" if picked is not None else "default")
            return
        if self.tool_mode == "gizmo":
            axis_name, _pick_pos, _dist = self._pick_gizmo_ring_at_display(display_pos, tolerance_px=110.0)
            self._set_gizmo_axis_emphasis(hover_axis=axis_name)
            self._set_viewer_cursor("grab" if axis_name else "default")
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
            name, _actor, _cell_id, pick_pos = self._pick_at_display(pos)
            axis_name = self._gizmo_axis_from_name(name)
            if axis_name is None or pick_pos is None:
                axis_name, pick_pos, _dist = self._pick_gizmo_ring_at_display(pos, tolerance_px=160.0)
            if axis_name is None or pick_pos is None:
                self._set_camera_drag_enabled(True)
                self._camera_drag_forwarded = True
                self._forward_camera_left_press()
                return
            self._start_gizmo_drag(axis_name, pick_pos, pos)
            self._set_viewer_cursor("drag")
            return
        if self.tool_mode in {"surface", "plate"}:
            return

    def _on_mouse_move(self, *_args):
        if self._v63_has_active_drag() and not self._left_button_is_down():
            self._finish_pointer_interaction()
            return
        pos = self._current_event_xy()
        if self._active_move is not None:
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
        if self._active_drag is not None:
            rotation = _V58.orthonormalize_rotation(self._active_drag.rotation)
            used_preview_callback = callable(self.on_rotation_preview)
            self._active_drag = None
            self._set_point_labels_visible(True)
            self._hide_rotation_feedback()
            self._set_gizmo_axis_emphasis()
            if not used_preview_callback:
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
        if had_move:
            self._finish_move_drag()
        if had_rotation:
            self._active_drag = None
            self._clear_temp_transform()
            self._hide_rotation_feedback()
            self._set_gizmo_axis_emphasis()
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
            picked = self._pick_part_at_display(vtk_display)
            if picked is None:
                self._set_camera_drag_enabled(True)
                self._set_viewer_cursor("default")
                return False
            if callable(self.on_model_pick):
                self.on_model_pick(picked)
            if self._start_move_drag(vtk_display, qt_coords=False):
                return True
            self._set_camera_drag_enabled(True)
            self._set_viewer_cursor("default")
            return False
        if self.tool_mode == "gizmo":
            axis_name, pick_pos, _dist = self._pick_gizmo_ring_at_display(qt_display, tolerance_px=160.0)
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
            self._start_gizmo_drag(axis_name, pick_pos, (int(qt_pos[0]), int(qt_pos[1])))
            self._set_viewer_cursor("drag")
            return True
        if self.tool_mode in {"surface", "plate"}:
            return True
        return bool(super()._qt_mouse_press(event, use_interactor_pos=use_interactor_pos) or False)

    def _qt_mouse_move(self, event):
        if self._active_move is not None:
            pos = self._qt_to_vtk_display((float(event.pos().x()), float(event.pos().y())))
            return self._update_move_drag(pos, qt_coords=False)
        pos = (float(event.pos().x()), float(event.pos().y()))
        if self._active_drag is not None:
            self._set_viewer_cursor("drag")
            super()._qt_mouse_move(event)
            return True
        if self.tool_mode == "move":
            picked = None
            for candidate, _flag in self._event_display_candidates(event):
                picked = self._pick_part_at_display((float(candidate[0]), float(candidate[1])))
                if picked is not None:
                    break
            self._set_viewer_cursor("move" if picked is not None else "default")
            return False
        if self.tool_mode == "gizmo":
            axis_name, _pick_pos, _dist = self._pick_gizmo_ring_at_display(pos, tolerance_px=110.0)
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
        if self._active_drag is not None:
            if callable(self.on_rotation_preview):
                try:
                    self.on_rotation_preview(_V58.np.eye(3, dtype=_V58.np.float64), "", 0.0)
                except Exception:
                    pass
            else:
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
                name="__v63_selection_outline__",
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

    def set_plate_warning(self, warning: bool):
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
            if self.plotter:
                self.plotter.render()
        except Exception:
            pass


_V58.EmbeddedPVViewer = EmbeddedPVViewerV63


class QtAssignColorsApp(_V62.QtAssignColorsApp):
    def __init__(self):
        self.pending_target_center_xy = None
        self.applied_target_center_xy = None
        super().__init__()
        self.setWindowTitle("LayerLoom — Color Assigner v63")

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

    def _apply_actor_transform(self, rotation):
        if self.viewer is None or not getattr(self.viewer, "actors_by_name", None):
            return
        values = self._get_transform_values()
        if values is None:
            return
        scale = max(float(values[3]), 1e-9)
        rotation = _V58.orthonormalize_rotation(rotation)
        center = self._streaming_transform_center()
        mat = _V58.np.eye(4, dtype=_V58.np.float64)
        rot_scale = _V58.np.eye(4, dtype=_V58.np.float64)
        rot_scale[:3, :3] = rotation * scale
        mat[:3, 3] = center
        mat = mat @ rot_scale
        mat[:3, 3] = mat[:3, 3] - rot_scale[:3, :3] @ center
        if self.pending_target_center_xy is not None:
            target = self._copy_xy(self.pending_target_center_xy)
            mat[0, 3] += float(target[0] - center[0])
            mat[1, 3] += float(target[1] - center[1])
        min_z = self._preview_min_z_after_actor_matrix(mat)
        if min_z is not None:
            mat[2, 3] -= float(min_z)
        try:
            vtk_mat = self.viewer._vtk_matrix(mat)
        except Exception:
            return
        for actor in self.viewer.actors_by_name.values():
            try:
                actor.SetUserMatrix(vtk_mat)
            except Exception:
                pass
        try:
            self.viewer.plotter.render()
        except Exception:
            pass

    def _preview_min_z_after_actor_matrix(self, matrix):
        mins = []
        mat = _V58.np.asarray(matrix, dtype=_V58.np.float64)
        for actor in getattr(self.viewer, "actors_by_name", {}).values():
            try:
                dataset = self.viewer._actor_dataset(actor)
                if dataset is None:
                    continue
                b = dataset.bounds
                corners = _V58.np.asarray(
                    [
                        [x, y, z, 1.0]
                        for x in (float(b[0]), float(b[1]))
                        for y in (float(b[2]), float(b[3]))
                        for z in (float(b[4]), float(b[5]))
                    ],
                    dtype=_V58.np.float64,
                )
                transformed = (mat @ corners.T).T[:, :3]
                mins.append(float(_V58.np.min(transformed[:, 2])))
            except Exception:
                pass
        if not mins:
            return None
        return min(mins)

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
                on_rotation_preview=self._preview_rotation_delta,
                on_rotation_status=self._set_rotation_status,
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

    def _apply_rotation_delta(self, delta_rotation):
        current = _V58.orthonormalize_rotation(self.pending_rotation_matrix)
        self.pending_rotation_matrix = _V58.orthonormalize_rotation(
            _V58.np.asarray(delta_rotation, dtype=_V58.np.float64) @ current
        )
        self._sync_rotation_fields_from_matrix(self.pending_rotation_matrix)
        self._clamp_pending_xy()
        self._update_transform_flags()
        self._refresh_transform_readout(self._last_transform_plan)
        if self.large_model_mode or getattr(self, "streaming_preview_mode", False):
            self._apply_streaming_actor_transform()
            self._refresh_plate_warning(self._last_transform_plan)
            return
        self._apply_transform_preview(quiet=False)

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

    _V58.APP_TITLE = "LayerLoom — Color Assigner v63"
    _V58.PALETTES_DIR = os.path.join(script_dir, "palettes")
    if not os.path.isdir(_V58.PALETTES_DIR):
        _V58._log("WARN", f"'palettes' directory not found at {_V58.PALETTES_DIR}. Using dummy palette data.")
        _V58.PALETTES_DIR = ""
    _V58.PALETTE_FILES = {
        "Simple": os.path.join(_V58.PALETTES_DIR, "simple_palette.json"),
        "Normal": os.path.join(_V58.PALETTES_DIR, "normal_palette.json"),
        "Full": os.path.join(_V58.PALETTES_DIR, "full_palette.json"),
    }

    try:
        if hasattr(sys, "_MEIPASS") and _V58.trimesh is not None:
            _V58.trimesh.constants.GLTF_VALIDATOR = os.path.join(sys._MEIPASS, "gltf_validator")
    except Exception:
        pass

    if not _V58._QT_OK:
        raise RuntimeError("Qt dependencies are unavailable; cannot launch v63.")

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
