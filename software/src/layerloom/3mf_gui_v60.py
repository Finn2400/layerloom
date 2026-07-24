#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LayerLoom v60
-------------

Qt single-window UI with universal 3MF import normalization and a unified
embedded-viewer interaction controller.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import os
import re
import sys
import tempfile
import time
from typing import Optional

try:
    from layerloom.normalize_3mf_import import normalize_3mf_import
except Exception:
    from normalize_3mf_import import normalize_3mf_import


_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_V58_PATH = os.path.join(_THIS_DIR, "3mf_gui_v58.py")
_SPEC = importlib.util.spec_from_file_location("layerloom_gui_v58_runtime", _V58_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Could not load v58 base module from {_V58_PATH}")
_V58 = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _V58
_SPEC.loader.exec_module(_V58)


@dataclass
class PickedPart:
    instance_key: str
    actor_name: str
    oid: Optional[str]
    world_point: Optional[object]
    cell_id: int


@dataclass
class RotationDragState:
    axis_name: str
    axis_vector: object
    center_display: object
    prev_angle_deg: float
    total_angle_deg: float
    screen_to_world_sign: float
    rotation: object
    last_display: object


class InteractiveQtInteractor(_V58.QtInteractor):
    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._owner = owner

    def mousePressEvent(self, event):
        if event.button() == _V58.QtCore.Qt.LeftButton and self._owner.tool_mode in {"gizmo", "surface", "plate"}:
            self._owner._qt_mouse_press(event)
            event.accept()
            return
        super().mousePressEvent(event)
        self._owner._qt_mouse_press(event, use_interactor_pos=True)

    def mouseMoveEvent(self, event):
        if self._owner.tool_mode in {"gizmo", "surface", "plate"}:
            self._owner._qt_mouse_move(event)
            event.accept()
            return
        super().mouseMoveEvent(event)
        self._owner._qt_mouse_move(event)

    def mouseReleaseEvent(self, event):
        if event.button() == _V58.QtCore.Qt.LeftButton and self._owner.tool_mode in {"gizmo", "surface", "plate"}:
            self._owner._qt_mouse_release(event)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self._owner._qt_mouse_release(event, use_interactor_pos=True)


class EmbeddedPVViewerV60(_V58.EmbeddedPVViewer):
    def __init__(self, parent=None):
        _V58.PVWindow.__init__(self)
        self.plotter = InteractiveQtInteractor(self, parent)
        self._configured = False
        self._layer_height = 0.16
        self._layer_scale = 1.0
        self._layer_preview_actors = []
        self._rotation_feedback_label = None
        self._hover_gizmo_axis = None
        self._active_gizmo_axis = None
        self.on_rotation_preview = None
        self.on_rotation_status = None

    def build_scene(self, file_path: str, names_in_order, on_pick=None, *, manifest=None, mesh_cache=None):
        super().build_scene(
            file_path,
            names_in_order,
            on_pick=on_pick,
            manifest=manifest,
            mesh_cache=mesh_cache,
        )
        if self.actors_by_name:
            for actor_name in self.actors_by_name.keys():
                self.actor_name_to_instance.setdefault(actor_name, actor_name)

    def set_tool_mode(self, mode: str, *, center=None, radius=None):
        self.tool_mode = (mode or "normal").lower()
        if center is not None:
            self.gizmo_center = _V58.np.asarray(center, dtype=_V58.np.float64).reshape(3)
        if radius is not None and _V58.math.isfinite(radius):
            self.gizmo_radius = max(float(radius), 1.0)

        self._clear_builtin_picking()
        self._remove_raw_interaction_handlers()

        try:
            vtk = _V58._get_vtk()
            if self.tool_mode in {"gizmo", "surface", "plate"}:
                self.plotter.iren.interactor.SetInteractorStyle(vtk.vtkInteractorStyleUser())
            else:
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

        if self._temp_transform_active or self._active_drag is not None:
            self._clear_temp_transform()
        if self.plotter:
            self.plotter.render()

    def set_callbacks(
        self,
        on_pick=None,
        on_rotation_commit=None,
        on_surface_commit=None,
        on_rotation_preview=None,
        on_rotation_status=None,
    ):
        super().set_callbacks(
            on_pick=on_pick,
            on_rotation_commit=on_rotation_commit,
            on_surface_commit=on_surface_commit,
        )
        if on_rotation_preview is not None:
            self.on_rotation_preview = on_rotation_preview
        if on_rotation_status is not None:
            self.on_rotation_status = on_rotation_status

    def _pick_part_at_display(self, display_pos) -> Optional[PickedPart]:
        name, _actor, cell_id, pick_pos = self._pick_at_display(display_pos)
        if name is None or name not in self.actors_by_name:
            return None
        instance_key = self.actor_name_to_instance.get(name, name)
        oid = _V58._oid_from_labeled_name(instance_key) or _V58._oid_from_labeled_name(name)
        return PickedPart(
            instance_key=str(instance_key),
            actor_name=str(name),
            oid=str(oid) if oid else None,
            world_point=pick_pos,
            cell_id=int(cell_id),
        )

    def _handle_left_click(self, display_pos):
        if self._active_drag is not None or self.tool_mode != "normal":
            return False
        picked = self._pick_part_at_display(display_pos)
        if picked is None:
            return False
        if callable(self.on_model_pick):
            self.on_model_pick(picked)
        return True

    def _event_display_pos(self, event, *, use_interactor_pos: bool):
        if use_interactor_pos:
            try:
                x, y = self._current_event_xy()
                return _V58.np.array([float(x), float(y)], dtype=_V58.np.float64)
            except Exception:
                pass
        return _V58.np.array([float(event.pos().x()), float(event.pos().y())], dtype=_V58.np.float64)

    def _gizmo_axis_from_name(self, name) -> Optional[str]:
        if not name:
            return None
        match = re.fullmatch(r"__gizmo_([xyz])__", str(name))
        return match.group(1) if match else None

    def _display_pos_qt(self, world_point):
        display = _V58.np.asarray(self._display_pos(world_point), dtype=_V58.np.float64)
        try:
            display[1] = float(self.plotter.window_size[1]) - display[1]
        except Exception:
            pass
        return display

    def _sample_gizmo_ring_display(self, axis_name: str, samples: int = 96):
        center = _V58.np.asarray(self.gizmo_center, dtype=_V58.np.float64)
        radius = max(float(self.gizmo_radius), 1.0)
        t = _V58.np.linspace(0.0, 2.0 * _V58.math.pi, int(samples), endpoint=False)
        if axis_name == "x":
            pts = _V58.np.column_stack([
                _V58.np.zeros_like(t),
                _V58.np.cos(t) * radius,
                _V58.np.sin(t) * radius,
            ])
        elif axis_name == "y":
            pts = _V58.np.column_stack([
                _V58.np.cos(t) * radius,
                _V58.np.zeros_like(t),
                _V58.np.sin(t) * radius,
            ])
        else:
            pts = _V58.np.column_stack([
                _V58.np.cos(t) * radius,
                _V58.np.sin(t) * radius,
                _V58.np.zeros_like(t),
            ])
        world_pts = pts + center
        display_pts = _V58.np.vstack([self._display_pos_qt(pt)[:2] for pt in world_pts])
        return world_pts, display_pts

    def _pick_gizmo_ring_at_display(self, display_pos, *, tolerance_px: float = 96.0):
        if not self.plotter or not self.gizmo_actors:
            return None, None, float("inf")
        mouse = _V58.np.asarray(display_pos, dtype=_V58.np.float64).reshape(2)
        best_axis = None
        best_point = None
        best_dist = float("inf")
        for axis_name in ("x", "y", "z"):
            try:
                world_pts, display_pts = self._sample_gizmo_ring_display(axis_name)
            except Exception:
                continue
            if display_pts.size == 0:
                continue
            dists = _V58.np.linalg.norm(display_pts - mouse[None, :], axis=1)
            idx = int(_V58.np.argmin(dists))
            dist = float(dists[idx])
            if dist < best_dist:
                best_axis = axis_name
                best_point = world_pts[idx]
                best_dist = dist
        if best_axis is None or best_dist > tolerance_px:
            return None, None, best_dist
        return best_axis, best_point, best_dist

    def _axis_vector(self, axis_name: str):
        if axis_name == "x":
            return _V58.np.array([1.0, 0.0, 0.0], dtype=_V58.np.float64)
        if axis_name == "y":
            return _V58.np.array([0.0, 1.0, 0.0], dtype=_V58.np.float64)
        if axis_name == "z":
            return _V58.np.array([0.0, 0.0, 1.0], dtype=_V58.np.float64)
        return None

    def _screen_angle_deg(self, display_pos, center_display) -> float:
        vec = _V58.np.asarray(display_pos, dtype=_V58.np.float64).reshape(2) - _V58.np.asarray(
            center_display, dtype=_V58.np.float64
        ).reshape(2)
        if _V58.np.linalg.norm(vec) <= 1e-6:
            return 0.0
        return float(_V58.math.degrees(_V58.math.atan2(float(vec[1]), float(vec[0]))))

    def _unwrap_angle_delta(self, current_deg: float, previous_deg: float) -> float:
        delta = float(current_deg - previous_deg)
        while delta > 180.0:
            delta -= 360.0
        while delta < -180.0:
            delta += 360.0
        return delta

    def _screen_to_world_sign(self, axis_vector, pick_point, center_display, start_display) -> float:
        try:
            start_angle = self._screen_angle_deg(start_display, center_display)
            test_rot = _V58.axis_angle_rotation(axis_vector, 8.0)
            center = _V58.np.asarray(self.gizmo_center, dtype=_V58.np.float64)
            radial = _V58.np.asarray(pick_point, dtype=_V58.np.float64) - center
            test_point = center + test_rot @ radial
            test_display = self._display_pos_qt(test_point)[:2]
            test_delta = self._unwrap_angle_delta(self._screen_angle_deg(test_display, center_display), start_angle)
            return 1.0 if test_delta >= 0.0 else -1.0
        except Exception:
            return 1.0

    def _set_gizmo_axis_emphasis(self, hover_axis=None, active_axis=None):
        self._hover_gizmo_axis = hover_axis
        self._active_gizmo_axis = active_axis
        base_colors = {"x": "#ff5b5b", "y": "#45c66a", "z": "#4d8cff"}
        active_colors = {"x": "#ff9b9b", "y": "#7cff9c", "z": "#8fbcff"}
        for axis_name, actor in list(self.gizmo_actors.items()):
            try:
                prop = actor.GetProperty() if hasattr(actor, "GetProperty") else getattr(actor, "prop", None)
                if prop is None:
                    continue
                color = active_colors.get(axis_name) if axis_name == active_axis else base_colors.get(axis_name)
                rgb = _V58._hex_to_rgb01(color or "#ffffff")
                prop.SetColor(float(rgb[0]), float(rgb[1]), float(rgb[2]))
                if hover_axis is None and active_axis is None:
                    opacity = 0.95
                else:
                    opacity = 1.0 if axis_name in {hover_axis, active_axis} else 0.42
                prop.SetOpacity(float(opacity))
            except Exception:
                pass
        try:
            self.plotter.render()
        except Exception:
            pass

    def _ensure_rotation_feedback_label(self):
        if self._rotation_feedback_label is not None:
            return self._rotation_feedback_label
        try:
            label = _V58.QtWidgets.QLabel(self.plotter)
            label.setStyleSheet(
                "QLabel { background: rgba(20, 20, 20, 205); color: #ffffff; "
                "border: 1px solid rgba(255,255,255,90); border-radius: 6px; "
                "padding: 4px 8px; font-weight: 700; }"
            )
            label.hide()
            self._rotation_feedback_label = label
            return label
        except Exception:
            return None

    def _show_rotation_feedback(self, axis_name: str, angle_deg: float, display_pos):
        label = self._ensure_rotation_feedback_label()
        text = f"{axis_name.upper()} {angle_deg:+.1f} deg"
        if label is not None:
            try:
                label.setText(text)
                label.adjustSize()
                x = int(display_pos[0]) + 14
                y = int(display_pos[1]) + 14
                label.move(max(4, x), max(4, y))
                label.show()
                label.raise_()
            except Exception:
                pass
        if callable(self.on_rotation_status):
            self.on_rotation_status(text)

    def _hide_rotation_feedback(self):
        if self._rotation_feedback_label is not None:
            try:
                self._rotation_feedback_label.hide()
            except Exception:
                pass
        if callable(self.on_rotation_status):
            self.on_rotation_status("")

    def _start_gizmo_drag(self, axis_name: str, pick_point, display_pos):
        axis = self._axis_vector(axis_name)
        if axis is None:
            return
        center = _V58.np.asarray(self.gizmo_center, dtype=_V58.np.float64)
        center_disp = self._display_pos_qt(center)[:2]
        display = _V58.np.asarray(display_pos[:2], dtype=_V58.np.float64)
        start_angle = self._screen_angle_deg(display, center_disp)
        sign = self._screen_to_world_sign(axis, pick_point, center_disp, display)
        self._active_drag = RotationDragState(
            axis_name=axis_name,
            axis_vector=axis,
            center_display=center_disp,
            prev_angle_deg=start_angle,
            total_angle_deg=0.0,
            screen_to_world_sign=sign,
            rotation=_V58.np.eye(3, dtype=_V58.np.float64),
            last_display=display,
        )
        self._set_gizmo_axis_emphasis(active_axis=axis_name)
        self._show_rotation_feedback(axis_name, 0.0, display)
        self._set_point_labels_visible(False)
        try:
            vtk = _V58._get_vtk()
            self.plotter.iren.interactor.SetInteractorStyle(vtk.vtkInteractorStyleUser())
        except Exception:
            pass

    def _qt_mouse_press(self, event, *, use_interactor_pos: bool = False):
        if event.button() != _V58.QtCore.Qt.LeftButton:
            return
        pos = self._event_display_pos(event, use_interactor_pos=use_interactor_pos)
        if self.tool_mode == "normal":
            self._normal_click_press = pos
            return
        if self.tool_mode != "gizmo" or self._active_drag is not None:
            return
        name, _actor, _cell_id, pick_pos = self._pick_at_display((float(pos[0]), float(pos[1])))
        axis_name = self._gizmo_axis_from_name(name)
        if axis_name is None or pick_pos is None:
            axis_name, pick_pos, _dist = self._pick_gizmo_ring_at_display((float(pos[0]), float(pos[1])))
        if axis_name is None or pick_pos is None:
            _V58._log("INFO", "[gizmo] click missed rotation rings")
            return
        _V58._log("INFO", f"[gizmo] drag start axis={axis_name}")
        self._start_gizmo_drag(axis_name, pick_pos, (int(pos[0]), int(pos[1])))

    def _qt_mouse_move(self, event):
        pos = (float(event.pos().x()), float(event.pos().y()))
        if self._active_drag is None:
            if self.tool_mode == "gizmo":
                axis_name, _pick_pos, _dist = self._pick_gizmo_ring_at_display(pos, tolerance_px=110.0)
                self._set_gizmo_axis_emphasis(hover_axis=axis_name)
                if callable(self.on_rotation_status):
                    self.on_rotation_status(
                        f"Drag {axis_name.upper()} rotation ring" if axis_name else "Drag a rotation ring"
                    )
                return
            if self.tool_mode in {"surface", "plate"}:
                self._update_surface_hover(pos)
            return
        arr = _V58.np.asarray(pos, dtype=_V58.np.float64)
        current_angle = self._screen_angle_deg(arr, self._active_drag.center_display)
        screen_delta = self._unwrap_angle_delta(current_angle, self._active_drag.prev_angle_deg)
        self._active_drag.prev_angle_deg = current_angle
        self._active_drag.last_display = arr
        world_delta = screen_delta * float(self._active_drag.screen_to_world_sign)
        if abs(world_delta) <= 1e-4:
            return
        self._active_drag.total_angle_deg += world_delta
        self._active_drag.rotation = _V58.orthonormalize_rotation(
            _V58.axis_angle_rotation(self._active_drag.axis_vector, self._active_drag.total_angle_deg)
        )
        if callable(self.on_rotation_preview):
            self.on_rotation_preview(
                self._active_drag.rotation,
                self._active_drag.axis_name,
                float(self._active_drag.total_angle_deg),
            )
        else:
            self._apply_temp_transform(self._active_drag.rotation)
        self._show_rotation_feedback(self._active_drag.axis_name, self._active_drag.total_angle_deg, arr)

    def _qt_mouse_release(self, event, *, use_interactor_pos: bool = False):
        if event.button() != _V58.QtCore.Qt.LeftButton:
            return
        pos = self._event_display_pos(event, use_interactor_pos=use_interactor_pos)
        if self._active_drag is not None:
            rotation = _V58.orthonormalize_rotation(self._active_drag.rotation)
            used_preview_callback = callable(self.on_rotation_preview)
            self._active_drag = None
            self._set_point_labels_visible(True)
            self._hide_rotation_feedback()
            self._set_gizmo_axis_emphasis()
            if not used_preview_callback:
                self._clear_temp_transform()
            if callable(self.on_rotation_commit) and not _V58.np.allclose(
                rotation, _V58.np.eye(3), atol=1e-6, rtol=0.0
            ):
                _V58._log("INFO", "[gizmo] drag commit")
                self.on_rotation_commit(rotation)
            return
        if self.tool_mode in {"surface", "plate"}:
            self._commit_surface_pick_at_display((float(pos[0]), float(pos[1])))
            return
        if self.tool_mode != "normal":
            return
        press = self._normal_click_press
        self._normal_click_press = None
        if press is not None and _V58.np.linalg.norm(pos - press) <= 4.5:
            self._handle_left_click((float(pos[0]), float(pos[1])))


_V58.EmbeddedPVViewer = EmbeddedPVViewerV60


class QtAssignColorsApp(_V58.QtAssignColorsApp):
    def __init__(self):
        self._selection_sync_guard = False
        super().__init__()
        self.palette_grid.setHorizontalSpacing(6)
        self.palette_grid.setVerticalSpacing(8)
        self.palette_grid.setContentsMargins(0, 0, 0, 0)

    def _normalize_incoming_3mf(self, path: str, title: str) -> str | None:
        try:
            result = normalize_3mf_import(path)
        except Exception as e:
            self._error(title, f"Failed to normalize 3MF import:\n{e}")
            return None
        for warning in result.warnings[:8]:
            _V58._log("WARN", f"[3mf-normalize] {warning}")
        if len(result.warnings) > 8:
            _V58._log("WARN", f"[3mf-normalize] … {len(result.warnings) - 8} additional warning(s)")
        return result.normalized_path

    def _auto_place_glb_import(self, path: str) -> str:
        t0 = time.perf_counter()
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.splitext(os.path.basename(path))[0]) or "glb_import"
        token = hashlib.md5(os.path.abspath(path).encode("utf-8")).hexdigest()[:10]
        placed = os.path.join(tempfile.gettempdir(), f"layerloom_glb_placed_{base}_{token}.3mf")
        try:
            plan = _V58.compute_transform_plan(
                path,
                scale=1.0,
                orientation_matrix=_V58.np.eye(3, dtype=_V58.np.float64),
                plate_width=_V58.PLATE_WIDTH_MM,
                plate_depth=_V58.PLATE_DEPTH_MM,
                center_xy=True,
            )
            _V58.write_transformed_3mf(path, placed, plan.global_matrix)
            dims = plan.transformed_bounds.size
            _V58._perf_log(
                "glb auto-place",
                t0,
                extra=(
                    f"{os.path.basename(placed)} "
                    f"bounds={dims[0]:.2f}x{dims[1]:.2f}x{dims[2]:.2f}mm"
                ),
            )
            return placed
        except Exception as e:
            _V58._log("WARN", f"[glb-import] Auto-place failed; using normalized placement: {e}")
            return path

    def _on_open(self):
        path, _ = _V58.QtWidgets.QFileDialog.getOpenFileName(self, "Open 3MF", "", "3MF Files (*.3mf)")
        if not path:
            return
        normalized = self._normalize_incoming_3mf(path, "Open 3MF")
        if not normalized:
            return
        self._pending_source_hex_by_name = {}
        stamped = _V58._stamp_ids_only(normalized)
        self._load_canonical_model(stamped)
        _V58._log("INFO", f"Opened: {path}")
        if normalized != path:
            _V58._log("INFO", f"[3mf-normalize] canonical temp → {normalized}")
        if stamped != normalized:
            _V58._log("INFO", f"Previewing ID-stamped temp copy: {stamped}")

    def _on_open_glb(self):
        path, _ = _V58.QtWidgets.QFileDialog.getOpenFileName(self, "Open GLB/GLTF", "", "GLB/GLTF Files (*.glb *.gltf)")
        if not path:
            return
        try:
            import_3mf, manifest_map, repair_warning, color_levels = self._run_glb_import_pipeline(path)
        except Exception as e:
            self._error("Open GLB", f"Failed to import GLB:\n{e}")
            return
        normalized = self._normalize_incoming_3mf(import_3mf, "Open GLB")
        if not normalized:
            return
        self._pending_source_hex_by_name = manifest_map
        stamped = _V58._stamp_ids_only(normalized)
        placed = self._auto_place_glb_import(stamped)
        placed_on_plate = placed != stamped
        self._load_canonical_model(placed)
        _V58._log("INFO", f"Opened GLB: {path}")
        _V58._log(
            "INFO",
            f"GLB import produced 3MF: {import_3mf} (target_colors={self.glb_colors_spin.value()}, color_levels={color_levels})",
        )
        if normalized != import_3mf:
            _V58._log("INFO", f"[3mf-normalize] canonical temp → {normalized}")
        if placed_on_plate:
            _V58._log("INFO", f"[glb-import] auto-placed on build plate → {placed}")
        if repair_warning:
            _V58._log("WARN", f"[glb-import] {repair_warning}")
            if not self.large_model_mode:
                self.status_bar.showMessage("GLB imported; repair warning logged.", 5000)
        elif not self.large_model_mode:
            status = (
                "GLB imported, placed on plate, and auto-matched"
                if placed_on_plate
                else "GLB imported and auto-matched"
            )
            self.status_bar.showMessage(
                f"{status} using palette '{self.current_palette_name}'.",
                5000,
            )

    def _build_or_rebuild_viewer(self):
        def _picked(picked):
            self._handle_viewer_pick_event(picked)

        try:
            self._last_picked_oid = None
            self.viewer.build_scene(self.current_file, self._names_in_build_order, on_pick=_picked)
            self._sync_viewer_tool_mode()
            if hasattr(self, "_set_viewer_hint"):
                self._set_viewer_hint(None)
        except Exception as e:
            self._error("Preview", str(e))

    def _resolve_picked_oid(self, picked) -> Optional[str]:
        if isinstance(picked, PickedPart):
            if picked.oid:
                return picked.oid
            candidates = [picked.instance_key, picked.actor_name]
        else:
            raw = str(picked)
            candidates = [raw]
        for candidate in candidates:
            oid = _V58._oid_from_labeled_name(candidate)
            if oid:
                return oid
            oid = self.name_to_oid.get(candidate)
            if oid:
                return oid
        return None

    def set_selected_part(self, oid: str, *, source: str, flash: bool = True, scroll: bool = True) -> bool:
        item = self._item_for_oid(oid)
        if item is None:
            return False
        current_item = self.parts_table.currentItem()
        if current_item is not item:
            self._selection_sync_guard = True
            self._selection_origin = source
            try:
                self.parts_table.setCurrentItem(item)
            finally:
                self._selection_origin = None
                self._selection_sync_guard = False
        if scroll:
            self.parts_table.scrollToItem(item)
        self._last_picked_oid = oid
        self._update_selected_part_summary(oid)
        if flash and self.viewer:
            raw_name = self.oid_to_name.get(oid)
            if raw_name:
                self._flash_actor_by_name(raw_name, oid)
        return True

    def _handle_viewer_pick_event(self, picked):
        oid = self._resolve_picked_oid(picked)
        if not oid:
            _V58._log("WARN", f"3D Pick -> FAILED to map '{picked}' to an OID.")
            return
        _V58._log("INFO", f"[viewer] selected oid={oid}")
        self.set_selected_part(oid, source="viewer", flash=True, scroll=True)

    def _on_parts_selection_changed(self):
        if self._selection_sync_guard:
            return
        oid = self._current_selected_oid()
        if not oid:
            self._update_selected_part_summary(None)
            return
        self.set_selected_part(oid, source="table", flash=True, scroll=False)


def main():
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        script_dir = os.getcwd()

    _V58.APP_TITLE = "LayerLoom — Color Assigner v60"
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
        raise RuntimeError("Qt dependencies are unavailable; cannot launch v60.")

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
