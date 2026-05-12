#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LayerLoom v61
-------------

Qt single-window UI with v60 interaction/import behavior plus a streaming
large-model preview path. Large normalized 3MFs still get a preview; v61 avoids
the old full trimesh scene load and redundant identity transform materializing.
"""

from __future__ import annotations

import gc
import importlib.util
import os
import re
import struct
import sys
import time
from typing import Dict, List, Optional, Tuple
import zipfile
import xml.etree.ElementTree as ET

try:
    from layerloom.normalize_3mf_import import normalize_3mf_import, _is_layerloom_normalized
    from layerloom.transform_3mf import _parse_tf_3mf
except Exception:
    from normalize_3mf_import import normalize_3mf_import, _is_layerloom_normalized
    from transform_3mf import _parse_tf_3mf


_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_V60_PATH = os.path.join(_THIS_DIR, "3mf_gui_v60.py")
_SPEC = importlib.util.spec_from_file_location("layerloom_gui_v60_runtime", _V60_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Could not load v60 base module from {_V60_PATH}")
_V60 = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _V60
_SPEC.loader.exec_module(_V60)
_V58 = _V60._V58

PickedPart = _V60.PickedPart


STREAMING_PREVIEW_MODEL_XML_MB = float(getattr(_V58, "GUI_STREAMING_MODEL_XML_MB", 128.0))


class _StreamingObjectInfo:
    def __init__(self, *, oid: str, name: str, vertex_count: int, triangle_count: int):
        self.oid = oid
        self.name = name
        self.vertex_count = vertex_count
        self.triangle_count = triangle_count


def _model_xml_size_bytes(path: str) -> int:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            model_name = _V58._find_model_xml_name(zf)
            if not model_name:
                return 0
            return int(zf.getinfo(model_name).file_size)
    except Exception:
        return 0


def _streaming_threshold_bytes() -> float:
    return STREAMING_PREVIEW_MODEL_XML_MB * 1024.0 * 1024.0


class QtAssignColorsApp(_V60.QtAssignColorsApp):
    def __init__(self):
        self._preview_actor_oid_by_name: Dict[str, str] = {}
        self._streaming_counts_by_oid: Dict[str, _StreamingObjectInfo] = {}
        self._streaming_counts_path: Optional[str] = None
        self.streaming_preview_mode = False
        self.streaming_preview_reason = ""
        self._rotation_preview_base_matrix = None
        super().__init__()
        self.setWindowTitle("LayerLoom — Color Assigner v61")

    def _process_qt_events(self) -> None:
        try:
            app = _V58.QtWidgets.QApplication.instance()
            if app is not None:
                app.processEvents()
        except Exception:
            pass

    def _normalize_incoming_3mf(self, path: str, title: str) -> str | None:
        t0 = time.perf_counter()
        result_path = super()._normalize_incoming_3mf(path, title)
        _V58._perf_log("v61 normalize import", t0, extra=os.path.basename(path))
        return result_path

    def _transform_is_default_now(self) -> bool:
        try:
            scale = self._current_scale_value()
        except Exception:
            return False
        if scale is None:
            return False
        rotation = getattr(self, "pending_rotation_matrix", None)
        if rotation is None:
            return False
        return bool(
            abs(float(scale) - 1.0) <= 1e-9
            and _V58.np.allclose(rotation, _V58.np.eye(3), atol=1e-9, rtol=0.0)
        )

    def _reload_current(self):
        source_file = self.base_file or self.current_file
        if not source_file:
            return

        t_reload = time.perf_counter()
        previous_sel = self._current_selected_oid() or self._last_picked_oid
        self.large_model_mode, self.large_model_reason, self.large_model_info = _V58._large_3mf_reason(source_file)
        model_bytes = int((self.large_model_info or {}).get("model_bytes") or _model_xml_size_bytes(source_file))
        use_streaming_metadata = self.large_model_mode or model_bytes >= _streaming_threshold_bytes()
        self.streaming_preview_mode = bool(use_streaming_metadata)
        self.streaming_preview_reason = f"model XML {_V58._bytes_to_mb(model_bytes):.1f} MB"

        root = None
        summary = None
        try:
            t_xml = time.perf_counter()
            if use_streaming_metadata:
                summary, counts = self._read_streaming_summary_with_counts(source_file)
                self._streaming_counts_by_oid = counts
                self._streaming_counts_path = source_file
                _V58._perf_log(
                    "v61 reload xml streaming summary+counts",
                    t_xml,
                    extra=f"{os.path.basename(source_file)} objects={len(counts)}",
                )
            else:
                self._streaming_counts_by_oid = {}
                self._streaming_counts_path = None
                with zipfile.ZipFile(source_file, "r") as zf:
                    model_name = _V58._find_model_xml_name(zf)
                    root = _V58._read_model_root(zf, model_name)
                _V58._perf_log("v61 reload xml parse", t_xml, extra=os.path.basename(source_file))
        except Exception as e:
            self._error("Open 3MF", str(e))
            return

        self.assignments = {}
        self._last_picked_oid = None
        self._preview_actor_oid_by_name = {}
        if use_streaming_metadata:
            self.oid_to_name = dict(summary["oid_to_name"])
            source_hex_by_oid = dict(summary["source_hex_by_oid"])
            stack_token_by_oid = dict(summary.get("stack_token_by_oid", {}))
            import_name_by_oid = dict(summary.get("import_name_by_oid", {}))
            ordered = list(summary["ordered"])
            real_oids = set(summary["real_oids"])
            model_objects = list(summary["model_objects"])
        else:
            self.oid_to_name = _V58._oid_to_name_map(root)
            source_hex_by_oid = _V58._oid_to_source_hex_map(root)
            stack_token_by_oid = _V58._oid_to_stack_token_map(root)
            import_name_by_oid = _V58._oid_to_import_build_label_map(root)
            ordered = _V58._build_items_order(root)
            real_oids = _V58._mesh_oid_set(root)
            model_objects = []
            for obj in _V58._all_model_objects(root):
                oid = obj.get("id")
                if not oid or oid not in real_oids:
                    continue
                model_objects.append((oid, obj.get("name") or f"object_{oid}"))

        self.oid_to_import_name = dict(import_name_by_oid)
        self.oid_to_display_name = _V58._build_preferred_display_name_map(
            model_objects,
            self.oid_to_name,
            import_name_by_oid,
        )
        self.name_to_oid = {v: k for k, v in self.oid_to_name.items()}

        self.parts_table.clear()
        self.parts_table.setHeaderLabels(["Part", "Hex", "Pattern"])
        self.model_objects = []
        for oid, raw_name in model_objects:
            display = self.oid_to_display_name.get(oid, _V58._clean_part_display_name(raw_name))
            item = _V58.QtWidgets.QTreeWidgetItem([display, "", ""])
            item.setData(0, _V58.QtCore.Qt.UserRole, oid)
            self.parts_table.addTopLevelItem(item)
            self.model_objects.append((oid, raw_name))

        viewer_names = [self.oid_to_name.get(oid, f"object_{oid}") for oid in ordered if oid in real_oids]
        all_real_names = [name for _, name in self.model_objects]
        seen = set(viewer_names)
        viewer_names += [name for name in all_real_names if name not in seen]
        self._names_in_build_order = viewer_names
        self._preview_actor_oid_by_name = {
            self.oid_to_name.get(oid, f"object_{oid}"): oid for oid in real_oids
        }

        _V58._log(
            "INFO",
            f"[xml] total objects: {len(self.oid_to_name)}; with mesh: {len(real_oids)}; "
            f"build items: {len(ordered)}; viewer names: {len(self._names_in_build_order)}",
        )

        for oid, name in self.model_objects:
            entry = _V58._grouped_color_assignment_for_name(name)
            if entry:
                self._set_assignment_for_oid(oid, entry)
        self._apply_source_metadata_assignments(source_hex_by_oid, stack_token_by_oid)

        if previous_sel:
            self._select_part_by_oid(previous_sel, flash=False)

        if use_streaming_metadata:
            pkg_mb = _V58._bytes_to_mb((self.large_model_info or {}).get("package_bytes"))
            model_mb = _V58._bytes_to_mb(model_bytes)
            self.transform_info_label.setText(
                f"Streaming large-model preview | package {pkg_mb:.1f} MB | model XML {model_mb:.1f} MB"
            )
            self.transform_info_label.setStyleSheet("color: #8fd3ff;")
            self.status_bar.showMessage("Streaming large-model preview…", 5000)

        if self.base_file and not self._transform_is_default_now() and not self.large_model_mode:
            if not self._apply_transform_preview(quiet=True):
                self.current_file = source_file
                self.transformed_preview_file = None
                self._refresh_preview_from_current_file(force=True)
        else:
            self.current_file = source_file
            self.transformed_preview_file = None
            self.viewer_tool_mode = "normal"
            self._refresh_preview_from_current_file(force=True)

        self._refresh_transform_readout()
        if use_streaming_metadata:
            self._set_streaming_preview_readout(model_bytes)
        self._refresh_status_summary()
        _V58._perf_log(
            "v61 reload current",
            t_reload,
            extra=f"objects={len(self.model_objects)} viewer_names={len(self._names_in_build_order)} streaming={use_streaming_metadata}",
        )

    def _set_streaming_preview_readout(self, model_bytes: int) -> None:
        pkg_mb = _V58._bytes_to_mb((self.large_model_info or {}).get("package_bytes"))
        model_mb = _V58._bytes_to_mb(model_bytes)
        self.transform_info_label.setText(
            f"Streaming large-model preview | package {pkg_mb:.1f} MB | model XML {model_mb:.1f} MB"
        )
        self.transform_info_label.setStyleSheet("color: #8fd3ff;")

    def _refresh_status_summary(self):
        super()._refresh_status_summary()
        if not self.streaming_preview_mode:
            return
        try:
            current = self.toolbar_status_label.text()
            current = current.replace("  |  Safety mode", "")
            if "Streaming preview" not in current:
                current += "  |  Streaming preview"
            self.toolbar_status_label.setText(current)
        except Exception:
            pass

    def _hide_gizmo_overlay(self):
        self._rotation_preview_base_matrix = None
        if getattr(self, "viewer_tool_mode", "normal") != "normal":
            self.viewer_tool_mode = "normal"
        if self.viewer is not None:
            try:
                self.viewer.set_tool_mode("normal")
            except Exception:
                try:
                    self.viewer._clear_gizmo()
                except Exception:
                    pass
        self._update_transform_ui_state()
        self._refresh_status_summary()

    def _ensure_transform_preview_current(self, quiet: bool = False) -> bool:
        if not self.base_file:
            return True
        if self.streaming_preview_mode or self.large_model_mode:
            self.current_file = self.base_file
            self.transformed_preview_file = None
            self._apply_streaming_actor_transform()
            return True
        if self._transform_is_default_now() and not self.transform_dirty:
            self.current_file = self.base_file
            self.transformed_preview_file = None
            return True
        return super()._ensure_transform_preview_current(quiet=quiet)

    def _apply_transform_preview(self, quiet: bool = False) -> bool:
        if self.streaming_preview_mode or self.large_model_mode:
            if not self.base_file:
                if not quiet:
                    self._warn("Transform", "Open a .3mf first.")
                return False
            self.current_file = self.base_file
            self.transformed_preview_file = None
            self._apply_streaming_actor_transform()
            self.applied_rotation_matrix = _V58.orthonormalize_rotation(self.pending_rotation_matrix)
            scale = self._current_scale_value()
            self.applied_scale = 1.0 if scale is None else scale
            self._update_transform_flags()
            self._refresh_transform_readout(self._last_transform_plan)
            self._hide_gizmo_overlay()
            return True
        return super()._apply_transform_preview(quiet=quiet)

    def _on_transform_value_change(self, *_args):
        if self._setting_transform_widgets:
            return
        values = self._get_transform_values()
        if values is not None:
            self.pending_rotation_matrix = _V58.rotation_matrix_xyz(values[0], values[1], values[2])
        self._rotation_preview_base_matrix = None
        self._update_transform_flags()
        self._refresh_layer_height_preview()
        if self.viewer is not None:
            self._apply_streaming_actor_transform()
        self._refresh_transform_readout(self._last_transform_plan)

    def _apply_rotation_delta(self, delta_rotation):
        self._rotation_preview_base_matrix = None
        current = _V58.orthonormalize_rotation(self.pending_rotation_matrix)
        self.pending_rotation_matrix = _V58.orthonormalize_rotation(
            _V58.np.asarray(delta_rotation, dtype=_V58.np.float64) @ current
        )
        self._sync_rotation_fields_from_matrix(self.pending_rotation_matrix)
        self._update_transform_flags()
        self._apply_streaming_actor_transform()
        self._refresh_transform_readout(self._last_transform_plan)

    def _preview_rotation_delta(self, delta_rotation, axis_name: str, angle_deg: float):
        if self._rotation_preview_base_matrix is None:
            self._rotation_preview_base_matrix = _V58.orthonormalize_rotation(self.pending_rotation_matrix)
        preview_rotation = _V58.orthonormalize_rotation(
            _V58.np.asarray(delta_rotation, dtype=_V58.np.float64) @ self._rotation_preview_base_matrix
        )
        self._apply_actor_transform(preview_rotation)
        self._sync_rotation_fields_from_matrix(preview_rotation)
        self._refresh_transform_readout(self._last_transform_plan)

    def _set_rotation_status(self, text: str):
        try:
            if text:
                self.status_bar.showMessage(text)
            else:
                self.status_bar.clearMessage()
        except Exception:
            pass

    def _reset_transform_to_default(self):
        self._hide_gizmo_overlay()
        return super()._reset_transform_to_default()

    def _streaming_transform_center(self):
        if self.viewer is not None:
            mins = []
            maxs = []
            for actor in getattr(self.viewer, "actors_by_name", {}).values():
                try:
                    dataset = self.viewer._actor_dataset(actor)
                    if dataset is not None:
                        bounds = _V58.np.asarray(dataset.bounds, dtype=_V58.np.float64)
                        mins.append([bounds[0], bounds[2], bounds[4]])
                        maxs.append([bounds[1], bounds[3], bounds[5]])
                except Exception:
                    pass
            if mins and maxs:
                min_corner = _V58.np.min(_V58.np.asarray(mins, dtype=_V58.np.float64), axis=0)
                max_corner = _V58.np.max(_V58.np.asarray(maxs, dtype=_V58.np.float64), axis=0)
                return 0.5 * (min_corner + max_corner)
        if self._last_transform_plan is not None:
            try:
                return _V58.np.asarray(self._last_transform_plan.transformed_bounds.center, dtype=_V58.np.float64)
            except Exception:
                pass
        return _V58.np.zeros(3, dtype=_V58.np.float64)

    def _viewer_object_center_and_radius(self):
        center = self._streaming_transform_center()
        radius = 5.0
        if self.viewer is not None:
            try:
                mins = []
                maxs = []
                for actor in getattr(self.viewer, "actors_by_name", {}).values():
                    dataset = self.viewer._actor_dataset(actor)
                    if dataset is None:
                        continue
                    bounds = _V58.np.asarray(dataset.bounds, dtype=_V58.np.float64)
                    mins.append([bounds[0], bounds[2], bounds[4]])
                    maxs.append([bounds[1], bounds[3], bounds[5]])
                if mins and maxs:
                    min_corner = _V58.np.min(_V58.np.asarray(mins, dtype=_V58.np.float64), axis=0)
                    max_corner = _V58.np.max(_V58.np.asarray(maxs, dtype=_V58.np.float64), axis=0)
                    center = 0.5 * (min_corner + max_corner)
                    radius = max(float(_V58.np.max(max_corner - min_corner)) * 0.72, 5.0)
            except Exception:
                pass
        return center, radius

    def _sync_viewer_tool_mode(self):
        if self.viewer is not None:
            if not self.viewer:
                self._refresh_status_summary()
                self._update_transform_ui_state()
                return
            center, radius = self._viewer_object_center_and_radius()
            self.viewer.set_callbacks(
                on_rotation_commit=self._apply_rotation_delta,
                on_surface_commit=self._on_surface_rotation,
                on_rotation_preview=self._preview_rotation_delta,
                on_rotation_status=self._set_rotation_status,
            )
            self.viewer.set_tool_mode(self.viewer_tool_mode, center=center, radius=radius)
            self._update_transform_ui_state()
            self._refresh_status_summary()
            self._refresh_transform_readout(self._last_transform_plan)
            return
        return super()._sync_viewer_tool_mode()

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

    def _apply_streaming_actor_transform(self):
        self._apply_actor_transform(self.pending_rotation_matrix)

    def _refresh_preview_from_current_file(self, *, force: bool = False):
        if not self.current_file:
            return
        self.viewer_tool_mode = "normal"
        if self.streaming_preview_mode or self.large_model_mode:
            if self.viewer:
                try:
                    self.viewer._teardown_scene()
                except Exception as e:
                    _V58._log("WARN", f"[preview] viewer teardown failed before streaming rebuild: {e}")
            gc.collect()
        self._export_ply_cache()
        self._build_or_rebuild_viewer()
        self._tint_viewer_from_assignments()
        gc.collect()

    def _is_streaming_preview_candidate(self) -> Tuple[bool, str]:
        if not self.current_file:
            return False, ""
        model_bytes = _model_xml_size_bytes(self.current_file)
        if model_bytes < _streaming_threshold_bytes():
            return False, ""
        try:
            if not _is_layerloom_normalized(self.current_file):
                return False, "large non-normalized 3MF; using standard preview path"
        except Exception:
            return False, "could not verify normalized 3MF; using standard preview path"
        return True, f"model XML {_V58._bytes_to_mb(model_bytes):.1f} MB"

    def _ply_cache_signature_for_current_file(self):
        try:
            st = os.stat(self.current_file)
            return (
                "v61-stream-build-transform",
                os.path.abspath(self.current_file),
                int(st.st_size),
                int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))),
                tuple(self._names_in_build_order),
            )
        except Exception:
            return None

    def _expected_cache_paths(self) -> List[str]:
        return [os.path.join(self.temp_ply_dir, f"{name}.ply") for name in self._names_in_build_order]

    def _export_ply_cache(self):
        should_stream, reason = self._is_streaming_preview_candidate()
        if not should_stream:
            if reason:
                _V58._log("INFO", f"[preview] {reason}")
            return super()._export_ply_cache()

        total_t0 = time.perf_counter()
        cache_sig = self._ply_cache_signature_for_current_file()
        if cache_sig and cache_sig == self._ply_cache_signature:
            expected = self._expected_cache_paths()
            if expected and all(os.path.exists(path) for path in expected):
                _V58._log("INFO", f"[preview] reusing streaming PLY cache for {os.path.basename(self.current_file)}")
                return

        self._clear_ply_cache()
        self._preview_actor_oid_by_name = {}
        _V58._log("INFO", f"[preview] streaming normalized 3MF to PLY cache: {reason}")
        self.status_bar.showMessage("Streaming large-model preview cache…", 5000)
        self._process_qt_events()

        try:
            t_count = time.perf_counter()
            if self._streaming_counts_path == self.current_file and self._streaming_counts_by_oid:
                counts = dict(self._streaming_counts_by_oid)
                _V58._perf_log("v61 preview streaming count pass", t_count, extra=f"reused objects={len(counts)}")
            else:
                counts = self._stream_normalized_object_counts(self.current_file)
                _V58._perf_log("v61 preview streaming count pass", t_count, extra=f"objects={len(counts)}")
            t_write = time.perf_counter()
            exported_count = self._stream_normalized_objects_to_ply(self.current_file, counts)
            _V58._perf_log("v61 preview streaming write pass", t_write, extra=f"ply_files={exported_count}")
        except Exception as e:
            _V58._log("ERROR", f"[preview] streaming PLY export failed: {e}")
            self._error("Preview Cache", f"Streaming preview export failed:\n{e}")
            return

        if exported_count > 0:
            self._ply_cache_signature = cache_sig
            self.status_bar.showMessage(f"Streaming preview cache ready: {exported_count} part(s).", 5000)
        else:
            self._error("Preview Cache", "Streaming preview did not export any parts.")
        _V58._perf_log("v61 preview cache export", total_t0, extra=f"ply_files={exported_count}")

    def _read_streaming_summary_with_counts(
        self,
        path: str,
    ) -> Tuple[Dict[str, object], Dict[str, _StreamingObjectInfo]]:
        summary: Dict[str, object] = {
            "oid_to_name": {},
            "source_hex_by_oid": {},
            "stack_token_by_oid": {},
            "import_name_by_oid": {},
            "ordered": [],
            "real_oids": set(),
            "model_objects": [],
        }
        counts: Dict[str, _StreamingObjectInfo] = {}
        with zipfile.ZipFile(path, "r") as zf:
            model_name = _V58._find_model_xml_name(zf)
            if not model_name:
                raise RuntimeError("No 3D/*.model found")
            with zf.open(model_name, "r") as model_fp:
                current_object = None
                seen = 0
                for event, elem in ET.iterparse(model_fp, events=("start", "end")):
                    tag = _V58._strip_ns(elem.tag)
                    if event == "start":
                        if tag == "object":
                            current_object = {
                                "id": elem.get("id") or "",
                                "name": elem.get("name") or "",
                                "type": (elem.get("type") or "").strip().lower(),
                                "has_mesh": False,
                                "source_hex": None,
                                "stack_token": None,
                                "import_build_label": None,
                                "vertices": 0,
                                "triangles": 0,
                            }
                        elif tag == "mesh" and current_object is not None:
                            current_object["has_mesh"] = True
                        continue

                    if tag == "metadata" and current_object is not None:
                        key = _V58._metadata_key(elem.get("name"))
                        text = (elem.text or "").strip()
                        if key in ("Title", "Name") and text and not current_object["name"]:
                            current_object["name"] = text
                        elif key == "source_hex":
                            hx = _V58._normalize_hex(text)
                            if hx:
                                current_object["source_hex"] = hx
                        elif key == "stack_token":
                            tok = text.lower()
                            if tok:
                                current_object["stack_token"] = tok
                        elif key == "import_source_build_label":
                            label = _V58._sanitize_part_label(text)
                            if label:
                                current_object["import_build_label"] = label
                    elif current_object is not None and tag == "vertex":
                        current_object["vertices"] += 1
                    elif current_object is not None and tag == "triangle":
                        current_object["triangles"] += 1
                    elif tag == "item":
                        oid = elem.get("objectid")
                        if oid:
                            summary["ordered"].append(oid)
                    elif tag == "object" and current_object is not None:
                        oid = str(current_object["id"] or "")
                        obj_type = str(current_object["type"] or "")
                        has_mesh = bool(current_object["has_mesh"])
                        if oid and has_mesh and (not obj_type or obj_type == "model"):
                            name = str(current_object["name"] or f"object_{oid}")
                            summary["oid_to_name"][oid] = name
                            summary["real_oids"].add(oid)
                            summary["model_objects"].append((oid, name))
                            counts[oid] = _StreamingObjectInfo(
                                oid=oid,
                                name=name,
                                vertex_count=int(current_object.get("vertices") or 0),
                                triangle_count=int(current_object.get("triangles") or 0),
                            )
                            hx = current_object.get("source_hex")
                            if hx:
                                summary["source_hex_by_oid"][oid] = hx
                            tok = current_object.get("stack_token")
                            if tok:
                                summary["stack_token_by_oid"][oid] = tok
                            import_name = current_object.get("import_build_label")
                            if import_name:
                                summary["import_name_by_oid"][oid] = str(import_name)
                        current_object = None
                    seen += 1
                    if seen % 200000 == 0:
                        self._process_qt_events()
                    elem.clear()
        return summary, counts

    def _stream_normalized_object_counts(self, path: str) -> Dict[str, _StreamingObjectInfo]:
        out: Dict[str, _StreamingObjectInfo] = {}
        with zipfile.ZipFile(path, "r") as zf:
            model_name = _V58._find_model_xml_name(zf)
            if not model_name:
                raise RuntimeError("No 3D/*.model found")
            with zf.open(model_name, "r") as fp:
                current = None
                seen = 0
                for event, elem in ET.iterparse(fp, events=("start", "end")):
                    tag = _V58._strip_ns(elem.tag)
                    if event == "start":
                        if tag == "object":
                            oid = elem.get("id") or ""
                            current = {
                                "oid": oid,
                                "name": elem.get("name") or (f"object_{oid}" if oid else "object"),
                                "has_mesh": False,
                                "vertices": 0,
                                "triangles": 0,
                            }
                        elif tag == "mesh" and current is not None:
                            current["has_mesh"] = True
                        continue

                    if current is not None:
                        if tag == "metadata":
                            key = _V58._metadata_key(elem.get("name"))
                            text = (elem.text or "").strip()
                            if key in ("Title", "Name") and text:
                                current["name"] = text
                        elif tag == "vertex":
                            current["vertices"] += 1
                        elif tag == "triangle":
                            current["triangles"] += 1
                        elif tag == "object":
                            oid = str(current.get("oid") or "")
                            if oid and bool(current.get("has_mesh")):
                                name = self.oid_to_name.get(oid, str(current.get("name") or f"object_{oid}"))
                                out[oid] = _StreamingObjectInfo(
                                    oid=oid,
                                    name=name,
                                    vertex_count=int(current.get("vertices") or 0),
                                    triangle_count=int(current.get("triangles") or 0),
                                )
                            current = None
                    seen += 1
                    if seen % 200000 == 0:
                        self._process_qt_events()
                    elem.clear()
        return out

    def _stream_normalized_build_transforms(self, path: str) -> Dict[str, _V58.np.ndarray]:
        out: Dict[str, _V58.np.ndarray] = {}
        with zipfile.ZipFile(path, "r") as zf:
            model_name = _V58._find_model_xml_name(zf)
            if not model_name:
                return out
            with zf.open(model_name, "r") as fp:
                seen = 0
                for event, elem in ET.iterparse(fp, events=("end",)):
                    tag = _V58._strip_ns(elem.tag)
                    if tag == "item":
                        oid = elem.get("objectid") or ""
                        if oid and oid not in out:
                            out[oid] = _parse_tf_3mf(elem.get("transform"))
                    seen += 1
                    if seen % 200000 == 0:
                        self._process_qt_events()
                    elem.clear()
        return out

    def _stream_normalized_objects_to_ply(
        self,
        path: str,
        counts: Dict[str, _StreamingObjectInfo],
    ) -> int:
        target_oids = set(counts)
        exported = 0
        current_oid = ""
        current_name = ""
        current_file = None
        current_info: Optional[_StreamingObjectInfo] = None
        current_transform = _V58.np.eye(4, dtype=_V58.np.float64)
        build_transforms = self._stream_normalized_build_transforms(path)
        event_count = 0

        def close_current() -> None:
            nonlocal current_file
            if current_file is not None:
                current_file.close()
                current_file = None

        with zipfile.ZipFile(path, "r") as zf:
            model_name = _V58._find_model_xml_name(zf)
            if not model_name:
                raise RuntimeError("No 3D/*.model found")
            with zf.open(model_name, "r") as fp:
                for event, elem in ET.iterparse(fp, events=("start", "end")):
                    tag = _V58._strip_ns(elem.tag)
                    if event == "start":
                        if tag == "object":
                            current_oid = elem.get("id") or ""
                            current_info = counts.get(current_oid)
                            current_name = current_info.name if current_info else (elem.get("name") or f"object_{current_oid}")
                            current_transform = build_transforms.get(
                                current_oid,
                                _V58.np.eye(4, dtype=_V58.np.float64),
                            )
                            if current_info and current_oid in target_oids:
                                ply_path = os.path.join(self.temp_ply_dir, f"{current_name}.ply")
                                current_file = open(ply_path, "wb", buffering=1024 * 1024)
                                header = (
                                    "ply\n"
                                    "format binary_little_endian 1.0\n"
                                    f"element vertex {current_info.vertex_count}\n"
                                    "property float x\n"
                                    "property float y\n"
                                    "property float z\n"
                                    f"element face {current_info.triangle_count}\n"
                                    "property list uchar int vertex_indices\n"
                                    "end_header\n"
                                )
                                current_file.write(header.encode("ascii"))
                        continue

                    if current_file is not None:
                        if tag == "vertex":
                            x = float(elem.get("x") or 0.0)
                            y = float(elem.get("y") or 0.0)
                            z = float(elem.get("z") or 0.0)
                            tx, ty, tz, _tw = current_transform @ _V58.np.array(
                                [x, y, z, 1.0],
                                dtype=_V58.np.float64,
                            )
                            current_file.write(struct.pack("<3f", float(tx), float(ty), float(tz)))
                        elif tag == "triangle":
                            a = int(elem.get("v1") or 0)
                            b = int(elem.get("v2") or 0)
                            c = int(elem.get("v3") or 0)
                            current_file.write(struct.pack("<Biii", 3, a, b, c))
                        elif tag == "object":
                            close_current()
                            if current_oid:
                                self._preview_actor_oid_by_name[current_name] = current_oid
                            exported += 1
                            current_oid = ""
                            current_name = ""
                            current_info = None
                            current_transform = _V58.np.eye(4, dtype=_V58.np.float64)
                    elif tag == "object":
                        current_oid = ""
                        current_name = ""
                        current_info = None
                        current_transform = _V58.np.eye(4, dtype=_V58.np.float64)

                    event_count += 1
                    if event_count % 200000 == 0:
                        self._process_qt_events()
                    elem.clear()
        close_current()
        return exported

    def _resolve_picked_oid(self, picked) -> Optional[str]:
        candidates: List[str] = []
        if isinstance(picked, PickedPart):
            if picked.oid:
                return picked.oid
            candidates = [picked.instance_key, picked.actor_name]
        else:
            candidates = [str(picked)]
        for candidate in candidates:
            oid = self._preview_actor_oid_by_name.get(str(candidate))
            if oid:
                return oid
        return super()._resolve_picked_oid(picked)

    def _build_or_rebuild_viewer(self):
        t0 = time.perf_counter()
        super()._build_or_rebuild_viewer()
        _V58._perf_log("v61 viewer build", t0, extra=os.path.basename(self.current_file or ""))

    def _on_save(self):
        self._hide_gizmo_overlay()
        return super()._on_save()

    def _on_weave(self):
        self._hide_gizmo_overlay()
        return super()._on_weave()


def main():
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        script_dir = os.getcwd()

    _V58.APP_TITLE = "LayerLoom — Color Assigner v61"
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
        raise RuntimeError("Qt dependencies are unavailable; cannot launch v61.")

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
