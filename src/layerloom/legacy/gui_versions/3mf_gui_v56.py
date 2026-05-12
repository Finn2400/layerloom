#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LayerLoom — 3MF GUI (v14.0-Clean — Production Ready)
---------------------------------------------------

What's new
----------
• Disabled verbose VTK and [DEBUG] logging by default.
• Cleaned out all [DEBUG-COLOR] and [DEBUG-SYNC] print statements.
• Refined shading: All models now load with shading on by default
  for a better initial appearance.
• Added "Flash-on-Pick": Clicking a part in the 3D view now
  flashes it, just like clicking the tree.
• Retains the robust PLY-cache system and all weave/save features.
"""

from __future__ import annotations
import os, re, sys, io, json, zipfile, colorsys, math, hashlib, importlib, queue, time, tkinter as tk, gc
from tkinter import ttk, filedialog, messagebox
import xml.etree.ElementTree as ET
from typing import Dict, List, Tuple, Optional
import tempfile, subprocess
import shutil, glob  # <-- Added for PLY Roundtrip

try:
    import resource
except Exception:
    resource = None

try:
    from layerloom.transform_3mf import (
        compute_transform_plan,
        write_transformed_3mf,
        TransformPlan,
        rotation_matrix_xyz,
        rotation_matrix_to_euler_xyz,
        orthonormalize_rotation,
        axis_angle_rotation,
        align_vectors_rotation,
        embed_rotation_matrix,
    )
except Exception:
    from transform_3mf import (
        compute_transform_plan,
        write_transformed_3mf,
        TransformPlan,
        rotation_matrix_xyz,
        rotation_matrix_to_euler_xyz,
        orthonormalize_rotation,
        axis_angle_rotation,
        align_vectors_rotation,
        embed_rotation_matrix,
    )

# ─────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────
LOG_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
LOG_LEVEL = LOG_LEVELS["INFO"]  # default: INFO (was DEBUG)


def _log(level: str, *args):
    lvl = LOG_LEVELS.get(level.upper(), 999)
    if lvl >= LOG_LEVEL:
        print(f"[{level}]", *args)


def set_log_level(name: str = "INFO"):  # <-- default INFO
    global LOG_LEVEL
    LOG_LEVEL = LOG_LEVELS.get(name.upper(), LOG_LEVELS["INFO"])
    _log("INFO", "Log level set to", name)


def _rss_mb() -> Optional[float]:
    if resource is None:
        return None
    try:
        raw = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        return None
    if sys.platform == "darwin":
        return raw / (1024.0 * 1024.0)
    return raw / 1024.0


def _perf_log(label: str, started_at: float, *, level: str = "INFO", extra: str = "") -> None:
    elapsed = time.perf_counter() - started_at
    rss = _rss_mb()
    msg = f"[perf] {label}: {elapsed:.3f}s"
    if rss is not None:
        msg += f" | rss≈{rss:.1f} MB"
    if extra:
        msg += f" | {extra}"
    _log(level, msg)


# ─────────────────────────────────────────────────────────────────────
# Optional deps
# ─────────────────────────────────────────────────────────────────────
_TRIMESH_OK = _PYVISTA_OK = False
try:
    import numpy as np, trimesh

    _TRIMESH_OK = True
    _log("DEBUG", "trimesh+numpy available")
except Exception as e:
    _log("WARN", "trimesh unavailable:", e)
    np = None
    trimesh = None

try:
    import pyvista as pv
    from pyvistaqt import BackgroundPlotter

    # This enables the highest level of logging from the underlying VTK library
    # pv.vtk_verbosity('max') # <-- DISABLED for clean output
    _log("INFO", "PyVista (VTK) internal logging is now OFF.")

    _PYVISTA_OK = True
    _log("DEBUG", "pyvista/pyvistaqt available")
except Exception as e:
    _log("WARN", "pyvista/pyvistaqt unavailable:", e)
    pv = None
    BackgroundPlotter = None

APP_TITLE = "LayerLoom — Color Assigner"  # <-- Updated Title
CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
NS = {"m": CORE_NS}
M = lambda t: f"{{{CORE_NS}}}{t}"
HEX_RE = re.compile(r"^#?[0-9A-Fa-f]{6}$")
SUPPORT_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "supporting_scripts"))
GLB_SPLIT_SCRIPT = os.path.join(SUPPORT_SCRIPTS_DIR, "glb_split_by_vertexcolor_v3_3mf.py")
GLB_REPAIR_SCRIPT = os.path.join(SUPPORT_SCRIPTS_DIR, "3mf_meshlab_repair_3mf.py")
PLATE_WIDTH_MM = 256.0
PLATE_DEPTH_MM = 256.0
_VTK_MOD = None
GUI_LARGE_PACKAGE_MB = 64.0
GUI_LARGE_MODEL_XML_MB = 128.0


def _get_vtk():
    global _VTK_MOD
    if _VTK_MOD is None:
        _VTK_MOD = importlib.import_module("vtk")
    return _VTK_MOD


def _bytes_to_mb(num_bytes: Optional[int]) -> float:
    if not num_bytes:
        return 0.0
    return float(num_bytes) / (1024.0 * 1024.0)


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _3mf_size_info(path: str) -> Dict[str, Optional[float]]:
    info: Dict[str, Optional[float]] = {
        "package_bytes": None,
        "model_bytes": None,
        "model_compressed_bytes": None,
        "model_name": None,
    }
    try:
        info["package_bytes"] = os.path.getsize(path)
    except Exception:
        pass
    try:
        with zipfile.ZipFile(path, "r") as zf:
            model_name = _find_model_xml_name(zf)
            info["model_name"] = model_name
            if model_name:
                zi = zf.getinfo(model_name)
                info["model_bytes"] = zi.file_size
                info["model_compressed_bytes"] = zi.compress_size
    except Exception:
        pass
    return info


def _large_3mf_reason(path: str) -> Tuple[bool, str, Dict[str, Optional[float]]]:
    info = _3mf_size_info(path)
    reasons: List[str] = []
    package_bytes = info.get("package_bytes") or 0
    model_bytes = info.get("model_bytes") or 0
    if package_bytes >= GUI_LARGE_PACKAGE_MB * 1024.0 * 1024.0:
        reasons.append(f"package size {_bytes_to_mb(package_bytes):.1f} MB")
    if model_bytes >= GUI_LARGE_MODEL_XML_MB * 1024.0 * 1024.0:
        reasons.append(f"model XML size {_bytes_to_mb(model_bytes):.1f} MB")
    return bool(reasons), ", ".join(reasons), info


# ─────────────────────────────────────────────────────────────────────
# XML helpers
# ─────────────────────────────────────────────────────────────────────
def _find_model_xml_name(zf: zipfile.ZipFile) -> Optional[str]:
    for n in zf.namelist():
        if n.startswith("3D/") and n.lower().endswith(".model"):
            return n
    return None

def _read_model_root(zf: zipfile.ZipFile, model_name: str) -> ET.Element:
    return ET.fromstring(zf.read(model_name))

def _xml_bytes(root: ET.Element) -> bytes:
    buf = io.BytesIO()
    ET.ElementTree(root).write(buf, encoding="utf-8", xml_declaration=True)
    return buf.getvalue()

def _all_model_objects(root: ET.Element) -> List[ET.Element]:
    res = root.find("m:resources", NS)
    if res is None:
        return []
    out = []
    for obj in res.findall("m:object", NS):
        obj_type = (obj.get("type") or "").strip().lower()
        if not obj_type or obj_type == "model":
            out.append(obj)
    return out

def _build_items_order(root: ET.Element) -> List[str]:
    build = root.find("m:build", NS)
    if build is None:
        return []
    return [it.get("objectid") for it in build.findall("m:item", NS) if it.get("objectid")]

def _oid_to_name_map(root: ET.Element) -> Dict[str, str]:
    res = root.find("m:resources", NS)
    if res is None:
        return {}
    return {o.get("id",""): (o.get("name") or f"object_{o.get('id','')}")
            for o in res.findall("m:object", NS)}

def _mesh_oid_set(root: ET.Element) -> set:
    """Return only object IDs that actually have a <mesh> (filter out component-only objects)."""
    res = root.find("m:resources", NS)
    if res is None:
        return set()
    s = set()
    for o in res.findall("m:object", NS):
        if o.find("m:mesh", NS) is not None:
            oid = o.get("id")
            if oid:
                s.add(oid)
    return s

# ─────────────────────────────────────────────────────────────────────
# Tag helpers (ID + PAT + end-suffix preservation)
# ─────────────────────────────────────────────────────────────────────
ID_RE  = re.compile(r"__ID_([A-Za-z0-9\-]+)__")
PAT_RE = re.compile(r"__PAT_([cmykwCMYKW]+)__")
END_RE = re.compile(r"(__E\d+)$")

def _strip_id_and_pat(s: str) -> str:
    s = ID_RE.sub("", s or "")
    s = PAT_RE.sub("", s)
    return s

def _extract_pat(s: str) -> Optional[str]:
    m = PAT_RE.search(s or "")
    return m.group(1).lower() if m else None

def _preserve_end_suffix(s: str) -> Tuple[str, str]:
    """Return (base_without_E, preserved_E_suffix_if_any)."""
    m = END_RE.search(s or "")
    if not m:
        return s or "", ""
    i = m.start()
    return (s[:i], s[i:])

def _make_labeled_name_with_id(base: str, oid: str, token: Optional[str]) -> str:
    base_no_tags = _strip_id_and_pat(base or "")
    head, e_suffix = _preserve_end_suffix(base_no_tags)
    tagged = f"{head}__ID_{oid}__"
    if token:
        tagged += f"__PAT_{token}__"
    return tagged + e_suffix

def _normalize_for_match(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\s+", "", s)
    s = _strip_id_and_pat(s)
    s = END_RE.sub("", s)
    return s

GROUPED_COLOR_ASSIGNMENTS = {
    "all_cyan": {"token": "c", "hex": "#00ffff"},
    "all_magenta": {"token": "m", "hex": "#ff00ff"},
    "all_yellow": {"token": "y", "hex": "#ffff00"},
    "all_black": {"token": "k", "hex": "#000000"},
    "all_white": {"token": "w", "hex": "#ffffff"},
}


def _grouped_color_assignment_for_name(name: Optional[str]) -> Optional[Dict[str, str]]:
    base = _normalize_for_match(name)
    for key, entry in GROUPED_COLOR_ASSIGNMENTS.items():
        if base == key or base.startswith(key):
            return dict(entry)
    return None


def _oid_from_labeled_name(name: str) -> Optional[str]:
    """Parse __ID_<oid>__ out of an object/actor name."""
    m = ID_RE.search(name or "")
    return m.group(1) if m else None

def _normalize_hex(hx: Optional[str]) -> Optional[str]:
    s = str(hx or "").strip()
    if not s or not HEX_RE.fullmatch(s):
        return None
    if not s.startswith("#"):
        s = "#" + s
    return s.lower()

def _target_colors_to_levels(target_colors: int) -> int:
    target_colors = max(2, int(target_colors))
    return max(2, int(math.ceil(target_colors ** (1.0 / 3.0))))

def _metadata_key(name: Optional[str]) -> str:
    s = str(name or "").strip()
    return s.split(":")[-1] if s else ""

def _oid_to_source_hex_map(root: ET.Element) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for obj in _all_model_objects(root):
        oid = obj.get("id")
        if not oid:
            continue
        mg = obj.find("m:metadatagroup", NS)
        if mg is None:
            continue
        for md in mg.findall("m:metadata", NS):
            if _metadata_key(md.get("name")) != "source_hex":
                continue
            hx = _normalize_hex(md.text)
            if hx:
                out[oid] = hx
            break
    return out

def _read_model_summary_streaming(path: str) -> Dict[str, object]:
    summary: Dict[str, object] = {
        "oid_to_name": {},
        "source_hex_by_oid": {},
        "ordered": [],
        "real_oids": set(),
        "model_objects": [],
    }
    with zipfile.ZipFile(path, "r") as zf:
        model_name = _find_model_xml_name(zf)
        if not model_name:
            raise RuntimeError("No 3D/*.model found")
        with zf.open(model_name, "r") as model_fp:
            current_object = None
            for event, elem in ET.iterparse(model_fp, events=("start", "end")):
                tag = _strip_ns(elem.tag)
                if event == "start":
                    if tag == "object":
                        current_object = {
                            "id": elem.get("id") or "",
                            "name": elem.get("name") or "",
                            "type": (elem.get("type") or "").strip().lower(),
                            "has_mesh": False,
                            "source_hex": None,
                        }
                    elif tag == "mesh" and current_object is not None:
                        current_object["has_mesh"] = True
                    continue

                if tag == "metadata" and current_object is not None:
                    key = _metadata_key(elem.get("name"))
                    text = (elem.text or "").strip()
                    if key in ("Title", "Name") and text and not current_object["name"]:
                        current_object["name"] = text
                    elif key == "source_hex":
                        hx = _normalize_hex(text)
                        if hx:
                            current_object["source_hex"] = hx
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
                        hx = current_object.get("source_hex")
                        if hx:
                            summary["source_hex_by_oid"][oid] = hx
                    current_object = None
                elem.clear()
    return summary

def _read_source_hex_manifest(path: str) -> Dict[str, str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        _log("WARN", f"Could not read GLB color manifest '{path}': {e}")
        return {}

    out: Dict[str, str] = {}
    for obj in data.get("objects", []):
        name = str(obj.get("name", "")).strip()
        hx = _normalize_hex(obj.get("source_hex"))
        if name and hx:
            out[_normalize_for_match(name)] = hx
    return out

# ─────────────────────────────────────────────────────────────────────
# Stamping routines
# ─────────────────────────────────────────────────────────────────────
def _stamp_object_metadata(obj_el: ET.Element, new_label: str) -> None:
    obj_el.set("name", new_label)
    for key in ("Title", "Name"):
        md = obj_el.find(f"m:metadata[@name='{key}']", NS)
        if md is None:
            md = ET.SubElement(obj_el, M("metadata"), {"name": key})
        md.text = new_label

def _sync_build_partnumbers(root: ET.Element) -> None:
    build = root.find("m:build", NS)
    res = root.find("m:resources", NS)
    if not build or not res:
        return
    idmap = {o.get("id"): o for o in res.findall("m:object", NS)}
    for item in build.findall("m:item", NS):
        ref = item.get("objectid")
        if ref and ref in idmap:
            nm = idmap[ref].get("name")
            if nm:
                item.set("partnumber", nm)

def _stamp_ids_only(input_3mf: str) -> str:
    """
    Create a temp-stamped .3mf where each object's name includes __ID_<oid>__,
    preserving any existing __PAT_*__ and trailing __E###.
    """
    tmp_out = os.path.join(tempfile.gettempdir(), f"preview_id_{os.path.basename(input_3mf)}")
    is_large, reason, _info = _large_3mf_reason(input_3mf)
    if is_large:
        _log("WARN", f"ID-stamping skipped in large-file safety mode: {reason}")
        return input_3mf
    try:
        with zipfile.ZipFile(input_3mf, "r") as zin:
            model_name = _find_model_xml_name(zin)
            if not model_name:
                return input_3mf
            root = ET.fromstring(zin.read(model_name))
            others = {n: zin.read(n) for n in zin.namelist() if n != model_name}

        for obj in _all_model_objects(root):
            oid = obj.get("id")
            if not oid:
                continue
            old = obj.get("name") or f"object_{oid}"
            existing_pat = _extract_pat(old)
            new_label = _make_labeled_name_with_id(old, oid, existing_pat)
            _stamp_object_metadata(obj, new_label)

        _sync_build_partnumbers(root)

        with zipfile.ZipFile(tmp_out, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for n, data in others.items():
                zout.writestr(n, data)
            zout.writestr(model_name, _xml_bytes(root))
        return tmp_out
    except Exception as e:
        _log("WARN", "ID-stamping failed, using original:", e)
        return input_3mf

def _rewrite_model_labels(input_3mf: str, output_3mf: str, assignments: Dict[str, Dict[str, str]]) -> None:
    """
    Persist choices by stamping each <object> name with __ID_<oid>__ and __PAT_<token>__ (if assigned).
    """
    with zipfile.ZipFile(input_3mf, "r") as zin:
        model_name = _find_model_xml_name(zin)
        if not model_name:
            raise RuntimeError("No 3D/*.model found")
        root = ET.fromstring(zin.read(model_name))
        others = {n: zin.read(n) for n in zin.namelist() if n != model_name}

    for obj in _all_model_objects(root):
        oid = obj.get("id")
        if not oid:
            continue
        base = obj.get("name") or f"object_{oid}"
        token = assignments.get(oid, {}).get("token", _extract_pat(base))
        new_name = _make_labeled_name_with_id(base, oid, token)
        _stamp_object_metadata(obj, new_name)

    _sync_build_partnumbers(root)

    with zipfile.ZipFile(output_3mf, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for n, data in others.items():
            zout.writestr(n, data)
        zout.writestr(model_name, _xml_bytes(root))

# ─────────────────────────────────────────────────────────────────────
# Weave runner (+ diagnostics)
# ─────────────────────────────────────────────────────────────────────
# def _run_weave(input_3mf: str, step: float = 0.2) -> Optional[str]:
#     try:
#         out_dir = os.path.dirname(os.path.abspath(input_3mf))
#         base = os.path.splitext(os.path.basename(input_3mf))[0]
#         out_path = os.path.join(out_dir, f"{base}_woven.3mf")
#         cmd = [sys.executable, "-m", "layerloom.weave", "-i", input_3mf, "-o", out_path]
#         if step is not None:
#             cmd += ["--step", str(step)]
#         _log("INFO", "[weave] running:", " ".join(cmd))
#         proc = subprocess.run(cmd, capture_output=True, text=True)
#         if proc.stdout:
#             _log("DEBUG", "[weave stdout]\n" + proc.stdout)
#         if proc.stderr:
#             _log("DEBUG", "[weave stderr]\n" + proc.stderr)
#         if proc.returncode != 0:
#             messagebox.showerror("Weave Error", proc.stderr or "Weave failed.")
#             return None
#         if os.path.exists(out_path):
#             return out_path
#         # Fallback: sniff alternative target from stdout
#         for line in reversed((proc.stdout or "").splitlines()):
#             if "→" in line and line.strip().endswith(".3mf"):
#                 alt = line.split("→")[-1].strip()
#                 if os.path.exists(alt):
#                     return alt
#         messagebox.showinfo("Weave", "Weave completed, but output not found.")
#         return None
#     except Exception as e:
#         messagebox.showerror("Weave", f"Weave failed: {e}")
#         return None

def _run_weave(input_3mf: str, step: float = 0.2, output_path: Optional[str] = None) -> Optional[str]:
    try:
        if output_path:
            out_path = output_path
        else:
            out_dir = os.path.dirname(os.path.abspath(input_3mf))
            base = os.path.splitext(os.path.basename(input_3mf))[0]
            out_path = os.path.join(out_dir, f"{base}_woven.3mf")

        cmd = [sys.executable, "-m", "layerloom.weave", "-i", input_3mf, "-o", out_path]
        if step is not None:
            cmd += ["--step", str(step)]

        _log("INFO", "[weave] running:", " ".join(cmd))
        t0 = time.perf_counter()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        _perf_log("subprocess weave", t0, extra=f"returncode={proc.returncode}")

        if proc.stdout:
            _log("DEBUG", "[weave stdout]\n" + proc.stdout)
        if proc.stderr:
            _log("DEBUG", "[weave stderr]\n" + proc.stderr)

        if proc.returncode != 0:
            messagebox.showerror("Weave Error", proc.stderr or "Weave failed.")
            return None

        if os.path.exists(out_path):
            return out_path

        # Fallback: sniff alternative target from stdout
        for line in reversed((proc.stdout or "").splitlines()):
            if "→" in line and line.strip().endswith(".3mf"):
                alt = line.split("→")[-1].strip()
                if os.path.exists(alt):
                    return alt

        messagebox.showinfo("Weave", "Weave completed, but output not found.")
        return None
    except Exception as e:
        messagebox.showerror("Weave", f"Weave failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────
# PyVista viewer (NOW USES PLY CACHE)
# ─────────────────────────────────────────────────────────────────────
class PVWindow:
    def __init__(self):
        if not (_TRIMESH_OK and _PYVISTA_OK):
            raise RuntimeError("Requires trimesh + pyvista + pyvistaqt")
        self.plotter = None
        self.actors_by_name: Dict[str, "pv.Actor"] = {}
        self.temp_ply_dir = os.path.join(tempfile.gettempdir(), "layerloom_ply_cache")
        self.tool_mode = "normal"
        self.on_model_pick = None
        self.on_rotation_commit = None
        self.on_surface_commit = None
        self.gizmo_actors: Dict[str, "pv.Actor"] = {}
        self.gizmo_center = np.zeros(3, dtype=np.float64)
        self.gizmo_radius = 1.0
        self.point_label_actor = None
        self.surface_highlight_actor = None
        self._active_drag = None
        self._picker = None
        self._temp_transform_active = False
        self._temp_user_matrix = None
        self._observer_ids: List[int] = []
        self._picking_mode = "none"

    def _teardown_scene(self):
        self._active_drag = None
        self._temp_transform_active = False
        self._temp_user_matrix = None
        self._remove_raw_interaction_handlers()
        if self.plotter:
            self._clear_builtin_picking()
            self._clear_gizmo()
            self._clear_surface_highlight()
            if self.point_label_actor is not None:
                try:
                    self.plotter.remove_actor(self.point_label_actor, reset_camera=False)
                except Exception:
                    pass
            for actor in list(self.actors_by_name.values()):
                try:
                    self.plotter.remove_actor(actor, reset_camera=False)
                except Exception:
                    pass
            try:
                self.plotter.render()
            except Exception:
                pass
        self.actors_by_name.clear()
        self.gizmo_actors.clear()
        self.point_label_actor = None
        self.surface_highlight_actor = None

    def _ensure_plotter(self):
        if self.plotter is not None:
            try:
                self.plotter.app_window.show()
                self.plotter.app_window.raise_()
                self.plotter.app_window.activateWindow()
            except Exception:
                pass
            return self.plotter
        _log("INFO", "[viewer] creating BackgroundPlotter")
        p = self.plotter = BackgroundPlotter(title="LayerLoom — Viewer", window_size=(600, 600))
        try:
            self.plotter.app_window.show()
            self.plotter.app_window.raise_()
            self.plotter.app_window.activateWindow()
            self.plotter.app_window.move(860, 0)
        except Exception:
            pass

        p.set_background("white")
        p.enable_anti_aliasing()
        try:
            p.show_axes()
        except Exception as e:
            _log("WARN", f"[viewer] axes overlay unavailable: {e}")
        try:
            plate = pv.Plane(
                center=(PLATE_WIDTH_MM * 0.5, PLATE_DEPTH_MM * 0.5, 0.0),
                direction=(0.0, 0.0, 1.0),
                i_size=PLATE_WIDTH_MM,
                j_size=PLATE_DEPTH_MM,
                i_resolution=8,
                j_resolution=8,
            )
            p.add_mesh(
                plate,
                name="__build_plate__",
                color="#f3f3f3",
                opacity=0.28,
                show_edges=True,
                edge_color="#b8b8b8",
                lighting=False,
                pickable=False,
                reset_camera=False,
            )
        except Exception as e:
            _log("WARN", f"[viewer] build plate overlay unavailable: {e}")
        return p

    def close(self):
        self._teardown_scene()
        if self.plotter:
            try:
                self.plotter.close()
            except Exception:
                pass
        self.plotter = None
        gc.collect()

    def build_scene(self, file_path: str, names_in_order: List[str], on_pick=None):
        self.on_model_pick = on_pick
        p = self._ensure_plotter()
        self._teardown_scene()
        _log("INFO", "[viewer] plotter created; loading cached actors")

        name_labels = []
        added = 0
        
        _log("DEBUG", f"[build_scene] Building from PLY cache dir: {self.temp_ply_dir}")
        _log("DEBUG", f"[build_scene] Attempting to load {len(names_in_order)} parts from build order...")

        # --- NEW: Load meshes from individual PLY files ---
        for nm in names_in_order:
            ply_path = os.path.join(self.temp_ply_dir, f"{nm}.ply")
            if not os.path.exists(ply_path):
                _log("WARN", f"[build_scene] Cache miss: Could not find '{ply_path}'")
                continue
            
            try:
                poly = pv.read(ply_path)
            except Exception as e:
                _log("ERROR", f"[build_scene] Failed to read PLY '{ply_path}': {e}")
                continue

            # Add the mesh *using its name*. This is the guaranteed match.
            act = p.add_mesh(poly, name=nm, color="#d3d3d3", pickable=True, reset_camera=False)
            if act.mapper:
                act.mapper.scalar_visibility = False
            if act.prop:
                act.prop.diffuse = 1.0
                act.prop.specular = 0.0
            
            # --- ADDED: Set default shading for a better look ---
            act.prop.interpolation = 'gouraud'
            
            added += 1
            
            # record centroid + displayed label
            c = poly.center
            # Just label it with its full name, or a stripped version
            label_txt = _strip_id_and_pat(nm)
            name_labels.append((c, label_txt))

        _log("DEBUG", f"[build_scene] Successfully loaded and added {added} actors from PLY cache.")
        _log("INFO", f"[viewer] added {added} cached actor(s)")

        if added == 0:
            _log("WARN", "[build_scene] No actors were added from PLY cache. Cache might be empty or build order is wrong.")

        # Registry and mapping printout
        self.actors_by_name = {nm: p.actors[nm] for nm in names_in_order if nm in p.actors}
        
        _log("DEBUG", "\n[viewer-map] PyVista actors ↔ XML names (Final Map):")
        if not self.actors_by_name:
            _log("DEBUG", "!! WARNING: 'self.actors_by_name' map is EMPTY.")
            
        for nm in self.actors_by_name:
            oid = _oid_from_labeled_name(nm)
            _log("DEBUG", f"   MAP KEY: '{nm:40s}'  →  OID={oid or '?'}  (Actor: {self.actors_by_name[nm]})")

        # Check for discrepancies
        xml_names_set = set(names_in_order)
        pv_actor_names_set = set(p.actors.keys())
        
        in_xml_not_in_pv = xml_names_set - pv_actor_names_set
        if in_xml_not_in_pv:
            _log("DEBUG", f"!! MISMATCH: {len(in_xml_not_in_pv)} names were in the XML build order but NOT found in PyVista's actors:")
            for n in sorted(list(in_xml_not_in_pv)): _log("DEBUG", f"      - {n} (Was its PLY file missing?)")
            
        in_pv_not_in_xml = pv_actor_names_set - xml_names_set
        if in_pv_not_in_xml:
            _log("DEBUG", f"!! INFO: {len(in_pv_not_in_xml)} names were in PyVista's actors but NOT in the XML build order (e.g., labels):")
            for n in sorted(list(in_pv_not_in_xml)): _log("DEBUG", f"      - {n}")

        # --- Label each part in the 3D view ---
        if name_labels:
            points = [c for c, _ in name_labels]
            texts = [t for _, t in name_labels]
            self.point_label_actor = p.add_point_labels(
                points,
                texts,
                font_size=10,
                text_color="black",
                point_color=None,
                render_points_as_spheres=False,
            )
            _log("INFO", f"[viewer] added {len(texts)} point label(s)")

        p.reset_camera()
        p.render()
        _log("INFO", "[viewer] initial render complete")

    def set_color_by_name(self, name: str, hexv: str, force_flash: bool = False):
        act = self.actors_by_name.get(name)
        if not act:
            _log("WARN", f"set_color_by_name: No actor found for key '{name}'")
            return
        try:
            rgb = _hex_to_rgb01(hexv)
        except Exception as e:
            _log("WARN", f"set_color_by_name: Could not convert HEX '{hexv}'. Error: {e}")
            return

        if act.prop:
            act.prop.color = rgb
            act.prop.diffuse = 1.0
            act.prop.specular = 0.0
            act.prop.interpolation = 'gouraud'
            self.plotter.render()
        else:
            _log("WARN", f"set_color_by_name: Actor '{name}' has no 'prop' attribute.")

    def bulk_tint(self, n2h: Dict[str, str]):
        for nm, hx in n2h.items():
            self.set_color_by_name(nm, hx)

    def set_callbacks(self, on_pick=None, on_rotation_commit=None, on_surface_commit=None):
        if on_pick is not None:
            self.on_model_pick = on_pick
        if on_rotation_commit is not None:
            self.on_rotation_commit = on_rotation_commit
        if on_surface_commit is not None:
            self.on_surface_commit = on_surface_commit

    def set_tool_mode(self, mode: str, *, center: Optional[np.ndarray] = None, radius: Optional[float] = None):
        self.tool_mode = (mode or "normal").lower()
        if center is not None:
            self.gizmo_center = np.asarray(center, dtype=np.float64).reshape(3)
        if radius is not None and math.isfinite(radius):
            self.gizmo_radius = max(float(radius), 1.0)

        self._configure_picking_mode()

        if self.tool_mode == "gizmo":
            self._ensure_raw_interaction_handlers()
        else:
            self._remove_raw_interaction_handlers()

        if self.tool_mode == "gizmo":
            self._clear_surface_highlight()
            self._rebuild_gizmo()
        else:
            self._clear_gizmo()
            if self.tool_mode != "surface":
                self._clear_surface_highlight()
        if self._temp_transform_active or self._active_drag is not None:
            self._clear_temp_transform()
        if self.plotter:
            self.plotter.render()

    def _clear_builtin_picking(self):
        if not self.plotter:
            return
        try:
            getattr(type(self.plotter), "disable_picking")(self.plotter)
        except Exception:
            pass
        for key in ("_picking_text", "_point_picking_text", "_picking_message", "_surf_picking_message"):
            actor = getattr(self.plotter, key, None)
            if actor is None:
                continue
            try:
                self.plotter.remove_actor(actor, reset_camera=False)
            except Exception:
                pass
        try:
            self.plotter.remove_actor("_picked_point", reset_camera=False)
        except Exception:
            pass
        self._picking_mode = "none"

    def _configure_picking_mode(self):
        if not self.plotter:
            return
        self._clear_builtin_picking()
        if self.tool_mode == "normal":
            self._enable_normal_mesh_picking()
        elif self.tool_mode == "surface":
            self._enable_surface_place_picking()

    def _enable_normal_mesh_picking(self):
        if not self.plotter:
            return

        def _picked(dset):
            if self.tool_mode != "normal":
                return
            for name, actor in self.actors_by_name.items():
                if actor.mapper and getattr(actor.mapper, "input", None) is dset:
                    if callable(self.on_model_pick):
                        self.on_model_pick(name)
                    break

        try:
            getattr(type(self.plotter), "enable_mesh_picking")(
                self.plotter,
                callback=_picked,
                left_clicking=True,
                show=False,
                show_message=False,
            )
            self._picking_mode = "normal"
            _log("INFO", "[viewer] normal mesh picking enabled")
        except Exception as e:
            _log("WARN", f"[viewer] normal mesh picking unavailable: {e}")

    def _enable_surface_place_picking(self):
        if not self.plotter:
            return

        plotter_cls = type(self.plotter)
        enable_surface = getattr(plotter_cls, "enable_surface_picking", None)
        enable_point = getattr(plotter_cls, "enable_point_picking", None)

        def _picked(point, picker=None):
            if self.tool_mode != "surface":
                return
            try:
                self._handle_surface_pick(point, picker)
            except Exception as e:
                _log("WARN", f"[viewer] surface place failed: {e}")

        try:
            if callable(enable_surface):
                enable_surface(
                    self.plotter,
                    callback=_picked,
                    left_clicking=True,
                    show_message="Click a visible outer side to place the model on that hull side.",
                    color="#ff8c00",
                    opacity=0.25,
                )
                picker_kind = "surface"
            elif callable(enable_point):
                picker_kind = "cell"
                try:
                    from pyvista.plotting.picking import PickerType

                    picker_kind = PickerType.CELL
                except Exception:
                    picker_kind = "cell"
                enable_point(
                    self.plotter,
                    callback=_picked,
                    picker=picker_kind,
                    use_picker=True,
                    left_clicking=True,
                    show_message="Click a visible outer side to place the model on that hull side.",
                    show_point=False,
                    color="#ff8c00",
                    tolerance=0.02,
                    clear_on_no_selection=True,
                )
            else:
                raise AttributeError("no compatible picking helper is available")
            self._picking_mode = "surface"
            _log("INFO", f"[viewer] hull-side picking enabled via {picker_kind}")
        except Exception as e:
            _log("WARN", f"[viewer] hull-side picking unavailable: {e}")

    def _ensure_raw_interaction_handlers(self):
        if not self.plotter or self._observer_ids:
            return
        try:
            vtk = _get_vtk()
        except Exception:
            return
        if self._picker is None:
            self._picker = vtk.vtkCellPicker()
            self._picker.SetTolerance(0.0005)
        try:
            self._observer_ids = [
                self.plotter.iren.add_observer("LeftButtonPressEvent", self._on_left_button_press),
                self.plotter.iren.add_observer("MouseMoveEvent", self._on_mouse_move),
                self.plotter.iren.add_observer("LeftButtonReleaseEvent", self._on_left_button_release),
            ]
        except Exception:
            self._observer_ids = []

    def _remove_raw_interaction_handlers(self):
        if not self.plotter or not self._observer_ids:
            self._observer_ids = []
            return
        interactor = getattr(self.plotter.iren, "interactor", None)
        for obs_id in self._observer_ids:
            try:
                if interactor is not None:
                    interactor.RemoveObserver(obs_id)
                else:
                    self.plotter.iren.remove_observer(obs_id)
            except Exception:
                pass
        self._observer_ids = []

    def _clear_gizmo(self):
        if not self.plotter:
            self.gizmo_actors.clear()
            return
        for actor in list(self.gizmo_actors.values()):
            try:
                self.plotter.remove_actor(actor, reset_camera=False)
            except Exception:
                pass
        self.gizmo_actors.clear()

    def _clear_surface_highlight(self):
        if self.plotter and self.surface_highlight_actor is not None:
            try:
                self.plotter.remove_actor(self.surface_highlight_actor, reset_camera=False)
            except Exception:
                pass
        self.surface_highlight_actor = None

    def _make_ring(self, axis_name: str, center: np.ndarray, radius: float):
        t = np.linspace(0.0, 2.0 * math.pi, 181)
        if axis_name == "x":
            pts = np.column_stack([np.zeros_like(t), np.cos(t) * radius, np.sin(t) * radius])
        elif axis_name == "y":
            pts = np.column_stack([np.cos(t) * radius, np.zeros_like(t), np.sin(t) * radius])
        else:
            pts = np.column_stack([np.cos(t) * radius, np.sin(t) * radius, np.zeros_like(t)])
        pts = pts + np.asarray(center, dtype=np.float64)
        ring = pv.lines_from_points(pts, close=True)
        tube = ring.tube(radius=max(radius * 0.02, 0.35), n_sides=18)
        return tube

    def _rebuild_gizmo(self):
        if not self.plotter:
            return
        self._clear_gizmo()
        axis_colors = {"x": "#ff5b5b", "y": "#45c66a", "z": "#4d8cff"}
        for axis_name, color in axis_colors.items():
            mesh = self._make_ring(axis_name, self.gizmo_center, self.gizmo_radius)
            actor = self.plotter.add_mesh(
                mesh,
                name=f"__gizmo_{axis_name}__",
                color=color,
                opacity=0.95,
                lighting=False,
                pickable=True,
                reset_camera=False,
            )
            self.gizmo_actors[axis_name] = actor

    def _display_pos(self, world_point: np.ndarray) -> np.ndarray:
        renderer = self.plotter.renderer
        renderer.SetWorldPoint(float(world_point[0]), float(world_point[1]), float(world_point[2]), 1.0)
        renderer.WorldToDisplay()
        x, y, z = renderer.GetDisplayPoint()
        return np.array([x, y, z], dtype=np.float64)

    def _pick_at_display(self, display_pos: Tuple[float, float]):
        if not self.plotter or not self._picker:
            return None, None, -1, None
        x, y = int(display_pos[0]), int(display_pos[1])
        renderer = self.plotter.renderer
        self._picker.Pick(x, y, 0.0, renderer)
        actor = self._picker.GetActor()
        if actor is None:
            return None, None, -1, None
        pick_pos = np.asarray(self._picker.GetPickPosition(), dtype=np.float64)
        cell_id = int(self._picker.GetCellId())
        return self._actor_name(actor), actor, cell_id, pick_pos

    def _actor_name(self, actor) -> Optional[str]:
        for name, candidate in self.actors_by_name.items():
            if self._same_vtk_object(candidate, actor):
                return name
        for axis_name, candidate in self.gizmo_actors.items():
            if self._same_vtk_object(candidate, actor):
                return f"__gizmo_{axis_name}__"
        return None

    def _same_vtk_object(self, left, right) -> bool:
        if left is None or right is None:
            return False
        if left is right:
            return True
        try:
            if left == right:
                return True
        except Exception:
            pass
        try:
            return left.GetAddressAsString("") == right.GetAddressAsString("")
        except Exception:
            return False

    def _actor_name_from_dataset(self, dataset) -> Optional[str]:
        if dataset is None:
            return None
        try:
            wrapped = pv.wrap(dataset)
        except Exception:
            wrapped = dataset
        for name, actor in self.actors_by_name.items():
            if self._same_vtk_object(self._actor_dataset(actor), wrapped):
                return name
        return None

    def _current_event_xy(self) -> Tuple[int, int]:
        try:
            return self.plotter.iren.get_event_position()
        except Exception:
            return self.plotter.iren.interactor.GetEventPosition()

    def _set_point_labels_visible(self, visible: bool):
        if self.point_label_actor is None:
            return
        try:
            self.point_label_actor.SetVisibility(bool(visible))
        except Exception:
            pass

    def _actor_dataset(self, actor):
        if actor is None:
            return None
        mapper = getattr(actor, "mapper", None)
        if mapper is None and hasattr(actor, "GetMapper"):
            try:
                mapper = actor.GetMapper()
            except Exception:
                mapper = None
        if mapper is None:
            return None
        dataset = getattr(mapper, "input", None)
        if dataset is None and hasattr(mapper, "GetInput"):
            try:
                dataset = mapper.GetInput()
            except Exception:
                dataset = None
        if dataset is None:
            return None
        try:
            return pv.wrap(dataset)
        except Exception:
            return None

    def _vtk_matrix(self, matrix: np.ndarray):
        vtk = _get_vtk()
        vtk_mat = vtk.vtkMatrix4x4()
        for i in range(4):
            for j in range(4):
                vtk_mat.SetElement(i, j, float(matrix[i, j]))
        return vtk_mat

    def _apply_temp_transform(self, rotation3: np.ndarray):
        if not self.plotter:
            return
        rot4 = embed_rotation_matrix(rotation3)
        center = np.asarray(self.gizmo_center, dtype=np.float64)
        mat = np.eye(4, dtype=np.float64)
        mat[:3, 3] = center
        mat = mat @ rot4
        mat[:3, 3] = mat[:3, 3] - rot4[:3, :3] @ center
        vtk_mat = self._vtk_matrix(mat)
        self._temp_user_matrix = vtk_mat
        self._temp_transform_active = True
        for actor in self.actors_by_name.values():
            actor.SetUserMatrix(vtk_mat)
        self.plotter.render()

    def _clear_temp_transform(self):
        if not self.plotter:
            return
        if not self._temp_transform_active and self._active_drag is None:
            self._set_point_labels_visible(True)
            return
        for actor in self.actors_by_name.values():
            try:
                actor.SetUserMatrix(None)
                continue
            except Exception:
                pass
            try:
                actor.SetUserTransform(None)
            except Exception:
                pass
        self._set_point_labels_visible(True)
        self._active_drag = None
        self._temp_transform_active = False
        self._temp_user_matrix = None
        try:
            self.plotter.iren.enable_trackball_style()
        except Exception:
            pass
        self.plotter.render()

    def _start_gizmo_drag(self, axis_name: str, pick_point: np.ndarray, display_pos: Tuple[int, int]):
        center = np.asarray(self.gizmo_center, dtype=np.float64)
        if axis_name == "x":
            axis = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        elif axis_name == "y":
            axis = np.array([0.0, 1.0, 0.0], dtype=np.float64)
        else:
            axis = np.array([0.0, 0.0, 1.0], dtype=np.float64)
        radial = np.asarray(pick_point, dtype=np.float64) - center
        radial = radial - axis * np.dot(radial, axis)
        if np.linalg.norm(radial) <= 1e-9:
            return
        radial = radial / np.linalg.norm(radial)
        tangent = np.cross(axis, radial)
        tangent = tangent / max(np.linalg.norm(tangent), 1e-12)
        center_disp = self._display_pos(center)
        pick_disp = self._display_pos(pick_point)
        tangent_disp = self._display_pos(pick_point + tangent * max(self.gizmo_radius * 0.3, 1.0)) - pick_disp
        tangent_disp = tangent_disp[:2]
        tangent_n = np.linalg.norm(tangent_disp)
        if tangent_n <= 1e-6:
            tangent_disp = np.array([1.0, 0.0], dtype=np.float64)
        else:
            tangent_disp = tangent_disp / tangent_n
        ring_radius_px = max(float(np.linalg.norm((pick_disp - center_disp)[:2])), 24.0)
        self._active_drag = {
            "axis": axis_name,
            "axis_vector": axis,
            "prev_display": np.asarray(display_pos[:2], dtype=np.float64),
            "tangent_display": tangent_disp,
            "ring_radius_px": ring_radius_px,
            "rotation": np.eye(3, dtype=np.float64),
        }
        self._set_point_labels_visible(False)
        try:
            vtk = _get_vtk()
            self.plotter.iren.interactor.SetInteractorStyle(vtk.vtkInteractorStyleUser())
        except Exception:
            pass

    def _highlight_surface_patch(self, patch_poly):
        self._clear_surface_highlight()
        if patch_poly is None:
            return
        try:
            self.surface_highlight_actor = self.plotter.add_mesh(
                patch_poly,
                color="#ff8c00",
                opacity=0.55,
                lighting=False,
                pickable=False,
                reset_camera=False,
            )
            self.plotter.render()
        except Exception as e:
            _log("WARN", f"surface highlight failed: {e}")

    def _poly_to_trimesh(self, poly):
        if poly is None:
            return None
        tri = poly.triangulate()
        faces_raw = np.asarray(tri.faces)
        if faces_raw.size == 0:
            return None
        faces = faces_raw.reshape(-1, 4)[:, 1:4]
        if faces.size == 0:
            return None
        return trimesh.Trimesh(vertices=np.asarray(tri.points), faces=faces, process=False)

    def _trimesh_to_pv_poly(self, mesh):
        if mesh is None or len(mesh.faces) == 0:
            return None
        faces = np.hstack([np.full((len(mesh.faces), 1), 3, dtype=np.int64), np.asarray(mesh.faces, dtype=np.int64)])
        return pv.PolyData(np.asarray(mesh.vertices, dtype=np.float64), faces.ravel())

    def _surface_delta_from_point(self, actor, picked_point: np.ndarray):
        poly = self._actor_dataset(actor)
        if poly is None:
            return None
        base_tm = self._poly_to_trimesh(poly)
        if base_tm is None or len(base_tm.faces) == 0:
            return None

        hull_tm = base_tm
        try:
            hull_candidate = base_tm.convex_hull
            if hull_candidate is not None and len(hull_candidate.faces) > 0:
                hull_tm = hull_candidate
        except Exception:
            pass

        face_centroids = np.asarray(hull_tm.triangles_center, dtype=np.float64)
        face_normals = np.asarray(hull_tm.face_normals, dtype=np.float64)
        face_areas = np.asarray(hull_tm.area_faces, dtype=np.float64)
        if len(face_centroids) == 0:
            return None

        point = np.asarray(picked_point, dtype=np.float64).reshape(3)
        seed_idx = int(np.argmin(np.sum((face_centroids - point) ** 2, axis=1)))
        seed_normal = face_normals[seed_idx]
        normal_cos = np.clip(face_normals @ seed_normal, -1.0, 1.0)
        region_mask = normal_cos >= math.cos(math.radians(28.0))
        if not np.any(region_mask):
            region_mask[seed_idx] = True

        weights = np.maximum(face_areas[region_mask], 1e-12)
        weighted_normal = np.sum(face_normals[region_mask] * weights[:, None], axis=0)
        normal_norm = np.linalg.norm(weighted_normal)
        if normal_norm <= 1e-9:
            weighted_normal = seed_normal
            normal_norm = np.linalg.norm(weighted_normal)
        patch_normal = weighted_normal / max(normal_norm, 1e-12)

        patch_faces = np.asarray(hull_tm.faces[region_mask], dtype=np.int64)
        patch_tm = trimesh.Trimesh(vertices=np.asarray(hull_tm.vertices, dtype=np.float64), faces=patch_faces, process=False)
        patch_poly = self._trimesh_to_pv_poly(patch_tm)
        delta = align_vectors_rotation(patch_normal, np.array([0.0, 0.0, -1.0], dtype=np.float64))
        return delta, patch_poly

    def _handle_surface_pick(self, picked_point, picker=None):
        dataset = None
        if picker is not None:
            try:
                dataset = picker.GetDataSet()
            except Exception:
                dataset = None
        if dataset is None:
            dataset = getattr(self.plotter, "picked_mesh", None)
        if dataset is None:
            dataset = getattr(self.plotter, "_picked_mesh", None)
        name = self._actor_name_from_dataset(dataset)
        if not name:
            return
        actor = self.actors_by_name.get(name)
        if actor is None:
            return
        result = self._surface_delta_from_point(actor, np.asarray(picked_point, dtype=np.float64))
        if not result:
            return
        delta, patch_poly = result
        self._highlight_surface_patch(patch_poly)
        if callable(self.on_surface_commit):
            self.on_surface_commit(delta)

    def _handle_left_click(self, display_pos):
        if self._active_drag is not None:
            return
        name, actor, cell_id, _pick_pos = self._pick_at_display(display_pos)
        if name is None:
            return
        if self.tool_mode != "normal":
            return
        if name in self.actors_by_name and callable(self.on_model_pick):
            self.on_model_pick(name)

    def _on_left_button_press(self, *_args):
        if self.tool_mode != "gizmo" or self._active_drag is not None:
            return
        pos = self._current_event_xy()
        name, _actor, _cell_id, pick_pos = self._pick_at_display(pos)
        if not name or not name.startswith("__gizmo_") or pick_pos is None:
            return
        self._start_gizmo_drag(name.split("_")[-1].strip("_"), pick_pos, pos)

    def _on_mouse_move(self, *_args):
        if self._active_drag is None:
            return
        pos = np.asarray(self._current_event_xy(), dtype=np.float64)
        delta = pos - self._active_drag["prev_display"]
        self._active_drag["prev_display"] = pos
        pixels_along = float(np.dot(delta, self._active_drag["tangent_display"]))
        if abs(pixels_along) <= 1e-4:
            return
        angle_deg = pixels_along * (180.0 / math.pi) / self._active_drag["ring_radius_px"]
        delta_rot = axis_angle_rotation(self._active_drag["axis_vector"], angle_deg)
        self._active_drag["rotation"] = orthonormalize_rotation(delta_rot @ self._active_drag["rotation"])
        self._apply_temp_transform(self._active_drag["rotation"])

    def _on_left_button_release(self, *_args):
        if self._active_drag is None:
            if self.tool_mode in {"normal", "surface"}:
                self._handle_left_click(self._current_event_xy())
            return
        rotation = orthonormalize_rotation(self._active_drag["rotation"])
        self._clear_temp_transform()
        if callable(self.on_rotation_commit) and not np.allclose(rotation, np.eye(3), atol=1e-6, rtol=0.0):
            self.on_rotation_commit(rotation)

# ─────────────────────────────────────────────────────────────────────
# Palette helpers
# ─────────────────────────────────────────────────────────────────────
PALETTES_DIR = os.path.join(os.path.dirname(__file__), "palettes")
PALETTE_FILES = {
    "Simple": os.path.join(PALETTES_DIR, "simple_palette.json"),
    "Normal": os.path.join(PALETTES_DIR, "normal_palette.json"),
    "Full":   os.path.join(PALETTES_DIR, "full_palette.json"),
}

# def _sort_by_hue(entries):
#     def hue_key(e):
#         try:
#             h,_,_ = colorsys.rgb_to_hsv(*[int(e["hex"][i:i+2],16)/255 for i in (1,3,5)])
#             return h
#         except Exception:
#             return 0
#     return sorted(entries, key=hue_key)
#
# def _filter_two_color(entries):
#     return [e for e in entries if len(set(e["token"])) <= 2]


def _sort_by_hue(entries):
    def hue_key(e):
        try:
            h,_,_ = colorsys.rgb_to_hsv(*[int(e["hex"][i:i+2],16)/255 for i in (1,3,5)])
            return h
        except Exception:
            return 0
    return sorted(entries, key=hue_key)


def _dedupe_equivalent_tokens_prefer_balanced_runs(entries):
    """
    Remove redundant palette entries whose tokens contain the same multiset of letters
    (same counts, order ignored). If two tokens are equivalent, prefer the one that
    avoids long runs, e.g. prefer 'cyymyy' over 'cymyyy'.

    Examples this fixes:
      cymyyy  (has 'yyy')  -> dropped if cyymyy exists
      cyymyy  (has 'yy'+'yy') -> kept
      cccmcy  -> dropped if ccmccy exists
      ccmccy  -> kept
      cmmmym  -> dropped if cmmymm exists
      cmmymm  -> kept
    """
    from collections import Counter

    def _signature(tok: str):
        # Order-insensitive counts: (('c',1),('m',1),('y',4)) etc.
        return tuple(sorted(Counter((tok or "").lower()).items()))

    def _run_preference_key(tok: str):
        t = (tok or "").lower()
        if not t:
            return (999, 999, 0, 0, "")

        # run-length encode
        runs = []
        cur = t[0]
        ln = 1
        for ch in t[1:]:
            if ch == cur:
                ln += 1
            else:
                runs.append(ln)
                cur = ch
                ln = 1
        runs.append(ln)

        max_run = max(runs)
        over2 = sum(max(0, r - 2) for r in runs)   # penalize 3+ in a row
        pairs = sum(1 for r in runs if r == 2)     # reward 2-length groups
        # Prefer: smaller max_run, fewer 3+ runs, more pairs, more alternation
        return (max_run, over2, -pairs, -len(runs), t)

    best_by_sig = {}
    for e in entries:
        tok = (e.get("token") or "").lower()
        sig = _signature(tok)
        key = _run_preference_key(tok)
        prev = best_by_sig.get(sig)
        if prev is None or key < prev[0]:
            best_by_sig[sig] = (key, e)

    return [e for _, e in best_by_sig.values()]


def _filter_two_color(entries):
    return [e for e in entries if len(set(e["token"])) <= 2]



# def _load_palette_file(path, limit_two=False):
#     # Dummy palette if files are missing, for debugging
#     dummy_palette = [
#         {"token": "c", "hex": "#00FFFF"},
#         {"token": "m", "hex": "#FF00FF"},
#         {"token": "y", "hex": "#FFFF00"},
#         {"token": "k", "hex": "#000000"},
#         {"token": "w", "hex": "#FFFFFF"},
#     ]
#     try:
#         with open(path, "r") as f:
#             data = json.load(f)
#     except Exception as e:
#         _log("ERROR", "Palette read failed:", e)
#         # return []
#         _log("WARN", "Using dummy palette for debugging.")
#         data = dummy_palette
#
#     def flatten(e):
#         if isinstance(e, dict) and "entries" in e: return sum([flatten(x) for x in e["entries"]], [])
#         if isinstance(e, list): return sum([flatten(x) for x in e], [])
#         return [e] if isinstance(e, dict) else []
#     out=[]; seen=set()
#     for e in flatten(data):
#         tok=str(e.get("token","")).strip().lower(); hx=str(e.get("hex","")).strip()
#         if tok and hx:
#             if not hx.startswith("#"): hx="#"+hx
#             if (tok,hx) not in seen:
#                 seen.add((tok,hx)); out.append({"token":tok,"hex":hx})
#     out=_sort_by_hue(out)
#     return _filter_two_color(out) if limit_two else out

def _load_palette_file(path, limit_two=False):
    # Dummy palette if files are missing, for debugging
    dummy_palette = [
        {"token": "c", "hex": "#00FFFF"},
        {"token": "m", "hex": "#FF00FF"},
        {"token": "y", "hex": "#FFFF00"},
        {"token": "k", "hex": "#000000"},
        {"token": "w", "hex": "#FFFFFF"},
    ]
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except Exception as e:
        _log("ERROR", "Palette read failed:", e)
        # return []
        _log("WARN", "Using dummy palette for debugging.")
        data = dummy_palette

    def flatten(e):
        if isinstance(e, dict) and "entries" in e:
            return sum([flatten(x) for x in e["entries"]], [])
        if isinstance(e, list):
            return sum([flatten(x) for x in e], [])
        return [e] if isinstance(e, dict) else []

    out = []
    seen = set()
    for e in flatten(data):
        tok = str(e.get("token", "")).strip().lower()
        hx = str(e.get("hex", "")).strip()
        if tok and hx:
            if not hx.startswith("#"):
                hx = "#" + hx
            if (tok, hx) not in seen:
                seen.add((tok, hx))
                out.append({"token": tok, "hex": hx})

    # De-dupe equivalent tokens (same letter counts), prefer balanced runs (no 'yyy' if 'yy'+'yy' exists).
    out = _dedupe_equivalent_tokens_prefer_balanced_runs(out)

    out = _sort_by_hue(out)
    return _filter_two_color(out) if limit_two else out



def _hex_to_rgb01(hx: str):
    hx = hx.lstrip("#")
    r,g,b = (int(hx[i:i+2],16) for i in (0,2,4))
    return (r/255,g/255,b/255)

def _filter_by_bw_visibility(entries, add_k: bool, add_w: bool):
    """Show/hide entries based on tokens containing black ('k') and/or white ('w')."""
    allowed_letters = set("cmykw")
    show_k = add_k
    show_w = add_w
    def keep(tok: str) -> bool:
        s = set(tok.lower())
        if not s.issubset(allowed_letters): return False
        if 'k' in s and not show_k: return False
        if 'w' in s and not show_w: return False
        if not show_k and not show_w and ('k' in s or 'w' in s): return False
        return True
    return [e for e in entries if keep(e["token"])]

# ─────────────────────────────────────────────────────────────────────
# GUI
# ─────────────────────────────────────────────────────────────────────
class AssignColorsApp(tk.Tk):
    def __init__(self):
        super().__init__()
        set_log_level("INFO") # <-- Set default log level
        self.title(APP_TITLE)
        self.geometry("860x800"); self.minsize(600,640)
        self.configure(bg="#1e1e1e")

        # --- NEW: PLY Cache Dir ---
        self.temp_ply_dir = os.path.join(tempfile.gettempdir(), "layerloom_ply_cache") # <-- Updated
        self._clear_ply_cache() # Start clean # <-- Updated
        self.protocol("WM_DELETE_WINDOW", self._on_exit) # Clean up on close

        self.base_file = None
        self.current_file=None
        self.transformed_preview_file = None
        self.model_objects: List[Tuple[str,str]] = []  # list of (oid, name)
        self.assignments: Dict[str, Dict[str,str]] = {}  # oid -> {"token","hex"}
        self.oid_to_name: Dict[str,str] = {}
        self.name_to_oid: Dict[str,str] = {}
        self._names_in_build_order: List[str] = []
        self._last_picked_oid: Optional[str] = None
        self._pending_source_hex_by_name: Dict[str, str] = {}
        self._last_transform_plan: Optional[TransformPlan] = None
        self._setting_transform_vars = False
        self.transform_dirty = False
        self.transform_is_default = True
        self.large_model_mode = False
        self.large_model_reason = ""
        self.large_model_info: Dict[str, Optional[float]] = {}
        self.pending_rotation_matrix = np.eye(3, dtype=np.float64)
        self.applied_rotation_matrix = np.eye(3, dtype=np.float64)
        self.applied_scale = 1.0
        self.viewer_tool_mode = "normal"
        self._viewer_event_queue: "queue.SimpleQueue[Tuple[str, object]]" = queue.SimpleQueue()
        self._ply_cache_signature = None

        self.current_palette_name="Normal"; self.current_palette=[]
        self.limit_two_colors=tk.BooleanVar(value=False)
        self.add_black = tk.BooleanVar(value=False)
        self.add_white = tk.BooleanVar(value=False)
        self.glb_target_colors = tk.IntVar(value=20)
        self.rot_x_var = tk.DoubleVar(value=0.0)
        self.rot_y_var = tk.DoubleVar(value=0.0)
        self.rot_z_var = tk.DoubleVar(value=0.0)
        self.scale_var = tk.DoubleVar(value=1.0)
        self.transform_info_var = tk.StringVar(value="No model loaded.")
        self.tool_mode_var = tk.StringVar(value="Mode: Normal")

        # required-letter filters (AND)
        self.req_c = tk.BooleanVar(value=False)
        self.req_m = tk.BooleanVar(value=False)
        self.req_y = tk.BooleanVar(value=False)
        self.req_k = tk.BooleanVar(value=False)
        self.req_w = tk.BooleanVar(value=False)

        self.viewer: Optional[PVWindow] = None
        self._build_ui()
        for var in (self.rot_x_var, self.rot_y_var, self.rot_z_var, self.scale_var):
            var.trace_add("write", self._on_transform_value_change)
        self._load_palette(self.current_palette_name)
        self.after(80, self._finish_startup)
        self.after(40, self._drain_viewer_events)



    # ---------- UI ----------
    def _build_ui(self):
        top=ttk.Frame(self); top.pack(fill="x",padx=10,pady=8)

        ttk.Button(top,text="Open 3MF…",command=self._on_open).pack(side="left")
        ttk.Button(top,text="Open GLB…",command=self._on_open_glb).pack(side="left",padx=(8,0))
        tk.Label(top, text="GLB colors:", bg="#1e1e1e", fg="#ccc").pack(side="left", padx=(8, 2))
        ttk.Entry(top, textvariable=self.glb_target_colors, width=5).pack(side="left")
        ttk.Button(top,text="Preview 3D",command=self._on_preview).pack(side="left",padx=8)
        ttk.Button(top,text="Dump Map",command=self._dump_assignments).pack(side="left",padx=(8,0))

        ttk.Button(top,text="Save Labeled 3MF…",command=self._on_save).pack(side="right")
        ttk.Button(top,text="Weave",command=self._on_weave).pack(side="right", padx=(0,8))
        self.step_var = tk.DoubleVar(value=0.2)
        ttk.Label(top, text="Layer height:", background="#1e1e1e", foreground="#ccc").pack(side="right", padx=(6, 2))
        ttk.Entry(top, textvariable=self.step_var, width=5).pack(side="right")

        ttk.Label(self,text="Tip: Click a part in the 3D window to select it; then click a swatch to assign.",
                  foreground="#666",background="#1e1e1e").pack(fill="x",padx=10,pady=(0,4))

        tf_wrap = tk.Frame(self, bg="#1e1e1e")
        tf_wrap.pack(fill="x", padx=10, pady=(0, 6))
        tf_box = tk.Frame(tf_wrap, bg="#2a2a2a", highlightbackground="#444", highlightthickness=1)
        tf_box.pack(fill="x")
        tk.Label(tf_box, text="Transform", bg="#2a2a2a", fg="#f2f2f2").grid(row=0, column=0, padx=(10, 8), pady=8, sticky="w")

        tk.Label(tf_box, text="Rotate X", bg="#2a2a2a", fg="#ccc").grid(row=0, column=1, padx=(0, 4))
        ttk.Button(tf_box, text="-90", width=4, command=lambda: self._nudge_rotation("x", -90.0)).grid(row=0, column=2, padx=(0, 2))
        ttk.Entry(tf_box, textvariable=self.rot_x_var, width=7).grid(row=0, column=3, padx=(0, 2))
        ttk.Button(tf_box, text="+90", width=4, command=lambda: self._nudge_rotation("x", 90.0)).grid(row=0, column=4, padx=(0, 10))

        tk.Label(tf_box, text="Rotate Y", bg="#2a2a2a", fg="#ccc").grid(row=0, column=5, padx=(0, 4))
        ttk.Button(tf_box, text="-90", width=4, command=lambda: self._nudge_rotation("y", -90.0)).grid(row=0, column=6, padx=(0, 2))
        ttk.Entry(tf_box, textvariable=self.rot_y_var, width=7).grid(row=0, column=7, padx=(0, 2))
        ttk.Button(tf_box, text="+90", width=4, command=lambda: self._nudge_rotation("y", 90.0)).grid(row=0, column=8, padx=(0, 10))

        tk.Label(tf_box, text="Rotate Z", bg="#2a2a2a", fg="#ccc").grid(row=0, column=9, padx=(0, 4))
        ttk.Button(tf_box, text="-90", width=4, command=lambda: self._nudge_rotation("z", -90.0)).grid(row=0, column=10, padx=(0, 2))
        ttk.Entry(tf_box, textvariable=self.rot_z_var, width=7).grid(row=0, column=11, padx=(0, 2))
        ttk.Button(tf_box, text="+90", width=4, command=lambda: self._nudge_rotation("z", 90.0)).grid(row=0, column=12, padx=(0, 10))

        tk.Label(tf_box, text="Scale", bg="#2a2a2a", fg="#ccc").grid(row=0, column=13, padx=(0, 4))
        ttk.Entry(tf_box, textvariable=self.scale_var, width=7).grid(row=0, column=14, padx=(0, 10))
        self.apply_transform_btn = ttk.Button(tf_box, text="Apply Transform", command=self._apply_transform_preview)
        self.apply_transform_btn.grid(row=0, column=15, padx=(0, 6))
        self.reset_transform_btn = ttk.Button(tf_box, text="Reset", command=self._reset_transform_to_default)
        self.reset_transform_btn.grid(row=0, column=16, padx=(0, 10))

        self.transform_info_label = tk.Label(
            tf_box,
            textvariable=self.transform_info_var,
            bg="#2a2a2a",
            fg="#9fd3a8",
            anchor="w",
        )
        self.transform_info_label.grid(row=1, column=0, columnspan=17, sticky="we", padx=10, pady=(0, 8))
        tool_row = tk.Frame(tf_box, bg="#2a2a2a")
        tool_row.grid(row=2, column=0, columnspan=17, sticky="we", padx=10, pady=(0, 8))
        self.rotate_gizmo_btn = ttk.Button(tool_row, text="Rotate Gizmo", command=self._activate_rotate_gizmo)
        self.rotate_gizmo_btn.pack(side="left")
        self.place_surface_btn = ttk.Button(tool_row, text="Place on Surface", command=self._activate_place_on_surface)
        self.place_surface_btn.pack(side="left", padx=(8, 0))
        self.cancel_tool_btn = ttk.Button(tool_row, text="Cancel Tool", command=lambda: self._set_viewer_tool_mode("normal"))
        self.cancel_tool_btn.pack(side="left", padx=(8, 0))
        tk.Label(tool_row, textvariable=self.tool_mode_var, bg="#2a2a2a", fg="#8fc8ff").pack(side="left", padx=(12, 0))
        tf_box.grid_columnconfigure(17, weight=1)

        paned=ttk.Panedwindow(self,orient="horizontal"); paned.pack(fill="both",expand=True,padx=10,pady=6)
        # left table
        left=ttk.Frame(paned); paned.add(left,weight=2)
        cols=("part","color","pattern")
        self.tree=ttk.Treeview(left,columns=cols,show="headings",selectmode="browse")
        for c,t,w in zip(cols,["Part (name)","Hex","Pattern"],[260,110,110]):
            self.tree.heading(c,text=t); self.tree.column(c,width=w,anchor="center")
        vsb=ttk.Scrollbar(left,orient="vertical",command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0,column=0,sticky="nsew"); vsb.grid(row=0,column=1,sticky="ns")
        left.rowconfigure(0,weight=1); left.columnconfigure(0,weight=1)

        # ==============================================================
        # <<< --- SYNCHRONICITY TEST BINDING --- >>>
        # ==============================================================
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        # ==============================================================

        # right palette
        right=tk.Frame(paned,bg="#ddd"); paned.add(right,weight=2)
        row_top=tk.Frame(right,bg="#ddd"); row_top.pack(fill="x")
        tk.Label(row_top,text="Palette:",bg="#ddd",fg="#000").pack(side="left",padx=(0,6))
        self.palette_var=tk.StringVar(value=self.current_palette_name)
        palette_cb=ttk.Combobox(row_top,textvariable=self.palette_var,
                     values=list(PALETTE_FILES.keys()),state="readonly",width=12)
        palette_cb.bind("<<ComboboxSelected>>", self._on_palette_change)
        palette_cb.pack(side="left")
        cb_two=ttk.Checkbutton(row_top,text="Limit to two-color blend",
                               variable=self.limit_two_colors,command=self._on_two_color_toggle)
        cb_two.pack(side="left",padx=(12,0))
        cb_k = ttk.Checkbutton(row_top, text="Add black (K)",
                               variable=self.add_black, command=self._on_bw_toggle)
        cb_k.pack(side="left", padx=(12, 0))
        cb_w = ttk.Checkbutton(row_top, text="Add white (W)",
                               variable=self.add_white, command=self._on_bw_toggle)
        cb_w.pack(side="left", padx=(8, 0))
        # required-letter filters
        ttk.Checkbutton(row_top, text="has C",
                        variable=self.req_c,
                        command=self._on_req_letters_toggle).pack(side="left", padx=(12, 0))

        ttk.Checkbutton(row_top, text="has M",
                        variable=self.req_m,
                        command=self._on_req_letters_toggle).pack(side="left")

        ttk.Checkbutton(row_top, text="has Y",
                        variable=self.req_y,
                        command=self._on_req_letters_toggle).pack(side="left")

        ttk.Checkbutton(row_top, text="has K",
                        variable=self.req_k,
                        command=self._on_req_letters_toggle).pack(side="left")

        ttk.Checkbutton(row_top, text="has W",
                        variable=self.req_w,
                        command=self._on_req_letters_toggle).pack(side="left")

        self.palette_count_label=tk.Label(right,text="Palette: 0 colors",bg="#ddd",fg="#000")
        self.palette_count_label.pack(anchor="w",pady=(8,4),padx=4)
        self.palette_canvas=tk.Canvas(right,bg="#ddd",highlightthickness=0,height=360)
        self.palette_scroll=ttk.Scrollbar(right,orient="vertical",command=self.palette_canvas.yview)
        self.palette_canvas.configure(yscrollcommand=self.palette_scroll.set)
        self.palette_inner=tk.Frame(self.palette_canvas,bg="#ddd")
        self.palette_canvas.create_window((0,0),window=self.palette_inner,anchor="nw")
        self.palette_canvas.pack(side="left",fill="both",expand=True)
        self.palette_scroll.pack(side="right",fill="y")
        self.palette_inner.bind("<Configure>",
            lambda e:self.palette_canvas.configure(scrollregion=(0,0,e.width,e.height+20)))

    def _finish_startup(self):
        try:
            self.update_idletasks()
            self.deiconify()
            self.lift()
            self.focus_force()
            self.attributes("-topmost", True)
            self.after(150, lambda: self.attributes("-topmost", False))
            _log("INFO", "[startup] main window ready")
        except Exception as e:
            _log("WARN", f"[startup] could not raise main window: {e}")

    def _enqueue_viewer_event(self, kind: str, payload):
        try:
            self._viewer_event_queue.put((kind, payload))
        except Exception as e:
            _log("WARN", f"[viewer-queue] enqueue failed for {kind}: {e}")

    def _drain_viewer_events(self):
        try:
            while True:
                kind, payload = self._viewer_event_queue.get_nowait()
                if kind == "pick":
                    self._handle_viewer_pick_event(str(payload))
                elif kind == "rotation":
                    self._apply_queued_rotation_delta(np.asarray(payload, dtype=np.float64))
                elif kind == "surface":
                    self._apply_queued_surface_rotation(np.asarray(payload, dtype=np.float64))
        except queue.Empty:
            pass
        except Exception as e:
            _log("WARN", f"[viewer-queue] drain failed: {e}")
        finally:
            self.after(40, self._drain_viewer_events)



    # ---------- palette logic ----------
    def _on_palette_change(self, _evt=None):
        self._load_palette(self.palette_var.get())

    def _on_two_color_toggle(self):
        self._load_palette(self.current_palette_name)

    def _on_bw_toggle(self):
        self._load_palette(self.current_palette_name)

    def _load_palette(self,name):
        # Create a dummy path if it doesn't exist, so _load_palette_file uses its dummy data
        path=PALETTE_FILES.get(name)
        if not path:
             _log("WARN", f"No palette file found for '{name}'.")
             path = "dummy" # _load_palette_file will handle this

        limit_two = self.limit_two_colors.get()
        entries=_load_palette_file(path, limit_two=limit_two)
        entries=_filter_by_bw_visibility(entries, add_k=self.add_black.get(), add_w=self.add_white.get())
        # AND required-letter filter
        required = set()
        if self.req_c.get(): required.add("c")
        if self.req_m.get(): required.add("m")
        if self.req_y.get(): required.add("y")
        if self.req_k.get(): required.add("k")
        if self.req_w.get(): required.add("w")

        entries = self._filter_by_required_letters(entries, required)

        self.current_palette_name=name; self.current_palette=entries
        self._render_palette_grid()

    def _render_palette_grid(self):
        for w in self.palette_inner.winfo_children(): w.destroy()
        entries=self.current_palette; self.palette_count_label.config(text=f"Palette: {len(entries)} colors")
        if not entries:
            tk.Label(self.palette_inner,text="No palette loaded",bg="#ddd",fg="#333").pack(padx=16,pady=16)
            return
        cell,pad,cols=34,8,6
        for i,e in enumerate(entries):
            r,c=divmod(i,cols)
            f=tk.Frame(self.palette_inner,bg="#ddd"); f.grid(row=r,column=c,padx=pad,pady=(pad,pad//2))
            sw=tk.Canvas(f,width=cell,height=cell,bg="#ddd",highlightthickness=1,highlightbackground="#666")
            sw.pack()
            try: sw.create_rectangle(0,0,cell,cell,fill=e["hex"],outline="#ddd",width=1)
            except tk.TclError: sw.create_rectangle(0,0,cell,cell,fill="#999",outline="#ddd",width=1)
            tk.Label(f,text=e["token"],fg="#000",bg="#ddd",font=("TkDefaultFont",10),bd=0,highlightthickness=0)\
                .pack(pady=(3,0))
            sw.bind("<Button-1>",lambda _e,entry=e:self._assign_selected(entry))
        self.palette_inner.update_idletasks()

    def _filter_by_required_letters(self, entries, required: set):
        if not required:
            return entries
        return [e for e in entries
                if all(ch in (e.get("token", "").lower()) for ch in required)]

    def _on_req_letters_toggle(self):
        self._load_palette(self.current_palette_name)

    # ---------- file ops ----------
    def _on_open(self):
        p=filedialog.askopenfilename(title="Open 3MF",filetypes=[("3MF","*.3mf")])
        if not p: return
        self._pending_source_hex_by_name = {}
        stamped = _stamp_ids_only(p)
        self._load_canonical_model(stamped)
        _log("INFO", f"Opened: {p}")
        if stamped != p:
            _log("INFO", f"Previewing ID-stamped temp copy: {stamped}")

    def _run_external_tool(self, label: str, cmd: List[str]) -> subprocess.CompletedProcess:
        _log("INFO", f"[{label}] running: {' '.join(cmd)}")
        t0 = time.perf_counter()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.stdout:
            _log("DEBUG", f"[{label} stdout]\n{proc.stdout}")
        if proc.stderr:
            _log("DEBUG", f"[{label} stderr]\n{proc.stderr}")
        _perf_log(f"subprocess {label}", t0, level="INFO", extra=f"returncode={proc.returncode}")
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"{label} failed")
        return proc

    def _nearest_palette_entry(self, source_hex: str) -> Optional[Dict[str, str]]:
        try:
            sr, sg, sb = _hex_to_rgb01(source_hex)
        except Exception:
            return None

        best = None
        best_dist = None
        for entry in self.current_palette:
            try:
                pr, pg, pb = _hex_to_rgb01(entry["hex"])
            except Exception:
                continue
            dist = (sr - pr) ** 2 + (sg - pg) ** 2 + (sb - pb) ** 2
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best = entry
        return best

    def _set_assignment_for_oid(self, oid: str, entry: Dict[str, str], source_hex: Optional[str] = None):
        data = {"token": entry["token"], "hex": entry["hex"]}
        if source_hex:
            data["source_hex"] = source_hex
        self.assignments[oid] = data

        if self.tree.exists(oid):
            vals = list(self.tree.item(oid, "values"))
            vals[1], vals[2] = entry["hex"], entry["token"]
            self.tree.item(oid, values=tuple(vals))

    def _apply_source_hex_assignments(self, source_hex_by_oid: Dict[str, str]) -> int:
        if not self.current_palette:
            _log("WARN", "No palette entries available for source-color matching.")
            return 0

        matched = 0
        for oid, name in self.model_objects:
            source_hex = source_hex_by_oid.get(oid)
            if not source_hex:
                source_hex = self._pending_source_hex_by_name.get(_normalize_for_match(name))
            if not source_hex:
                continue

            entry = self._nearest_palette_entry(source_hex)
            if not entry:
                continue
            self._set_assignment_for_oid(oid, entry, source_hex=source_hex)
            matched += 1

        self._pending_source_hex_by_name = {}
        if matched:
            _log("INFO", f"Auto-matched {matched} part(s) from source colors using palette '{self.current_palette_name}'.")
        return matched

    def _tint_viewer_from_assignments(self):
        if not self.viewer:
            return
        n2h = {}
        for oid, info in self.assignments.items():
            nm = self.oid_to_name.get(oid)
            hx = info.get("hex")
            if nm and hx:
                n2h[nm] = hx
        if n2h:
            self.viewer.bulk_tint(n2h)

    def _rotation_matrices_equal(self, a: np.ndarray, b: np.ndarray, tol: float = 1e-7) -> bool:
        return np.allclose(np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64), atol=tol, rtol=0.0)

    def _tool_mode_label(self, mode: Optional[str] = None) -> str:
        mode = (mode or self.viewer_tool_mode or "normal").lower()
        if mode == "gizmo":
            return "Mode: Rotate Gizmo"
        if mode == "surface":
            return "Mode: Place on Surface"
        return "Mode: Normal"

    def _set_transform_vars(self, rot_x: float, rot_y: float, rot_z: float, scale: float):
        self._setting_transform_vars = True
        try:
            self.rot_x_var.set(float(rot_x))
            self.rot_y_var.set(float(rot_y))
            self.rot_z_var.set(float(rot_z))
            self.scale_var.set(float(scale))
        finally:
            self._setting_transform_vars = False
        self._on_transform_value_change()

    def _sync_rotation_fields_from_matrix(self, rotation: np.ndarray):
        rot_x, rot_y, rot_z = rotation_matrix_to_euler_xyz(rotation)
        self._setting_transform_vars = True
        try:
            self.rot_x_var.set(float(rot_x))
            self.rot_y_var.set(float(rot_y))
            self.rot_z_var.set(float(rot_z))
        finally:
            self._setting_transform_vars = False

    def _get_transform_values(self, *, raise_on_error: bool = False) -> Optional[Tuple[float, float, float, float]]:
        try:
            values = (
                float(self.rot_x_var.get()),
                float(self.rot_y_var.get()),
                float(self.rot_z_var.get()),
                float(self.scale_var.get()),
            )
        except Exception as e:
            if raise_on_error:
                raise RuntimeError("Rotate X/Y/Z and Scale must be numeric.") from e
            return None

        if not all(math.isfinite(v) for v in values):
            if raise_on_error:
                raise RuntimeError("Transform values must be finite numbers.")
            return None
        if values[3] <= 0:
            if raise_on_error:
                raise RuntimeError("Scale must be greater than 0.")
            return None
        return values

    def _current_scale_value(self, *, raise_on_error: bool = False) -> Optional[float]:
        values = self._get_transform_values(raise_on_error=raise_on_error)
        return None if values is None else float(values[3])

    def _update_transform_flags(self):
        scale = self._current_scale_value()
        self.transform_is_default = (
            scale is not None
            and abs(scale - 1.0) <= 1e-9
            and self._rotation_matrices_equal(self.pending_rotation_matrix, np.eye(3, dtype=np.float64))
        )
        self.transform_dirty = bool(self.base_file) and (
            scale is None
            or abs(scale - float(self.applied_scale)) > 1e-9
            or not self._rotation_matrices_equal(self.pending_rotation_matrix, self.applied_rotation_matrix)
        )

    def _set_transform_button_state(self, button, enabled: bool):
        if not button:
            return
        if enabled:
            button.state(["!disabled"])
        else:
            button.state(["disabled"])

    def _update_transform_ui_state(self):
        values = self._get_transform_values()
        can_apply = bool(self.base_file) and not self.large_model_mode and values is not None and self.transform_dirty
        can_reset = bool(self.base_file) and (
            values is None or self.transform_dirty or not self.transform_is_default
        )
        self._set_transform_button_state(getattr(self, "apply_transform_btn", None), can_apply)
        self._set_transform_button_state(getattr(self, "reset_transform_btn", None), can_reset)

    def _refresh_transform_readout(self, plan: Optional[TransformPlan] = None):
        if not self.base_file:
            self.transform_info_var.set("No model loaded.")
            if hasattr(self, "transform_info_label"):
                self.transform_info_label.configure(fg="#999")
            self.tool_mode_var.set(self._tool_mode_label("normal"))
            self._update_transform_ui_state()
            return

        values = self._get_transform_values()
        if values is None:
            self.transform_info_var.set("Transform values invalid. Rotate entries must be numeric and Scale must be > 0.")
            if hasattr(self, "transform_info_label"):
                self.transform_info_label.configure(fg="#ffb347")
            self.tool_mode_var.set(self._tool_mode_label())
            self._update_transform_ui_state()
            return

        plan = plan or self._last_transform_plan
        if plan is None:
            text = f"Plate {PLATE_WIDTH_MM:.0f} x {PLATE_DEPTH_MM:.0f} mm | transform ready to apply"
            if self.large_model_mode:
                text += f" | large-file safety mode ({self.large_model_reason})"
            self.transform_info_var.set(text)
            if hasattr(self, "transform_info_label"):
                self.transform_info_label.configure(fg="#ffb347" if self.large_model_mode else "#9fd3a8")
            self.tool_mode_var.set(self._tool_mode_label())
            self._update_transform_ui_state()
            return

        dims = plan.transformed_bounds.size
        fits = dims[0] <= PLATE_WIDTH_MM + 1e-6 and dims[1] <= PLATE_DEPTH_MM + 1e-6
        status = "fits plate" if fits else "warning: footprint exceeds plate"
        text = (
            f"Size {dims[0]:.1f} x {dims[1]:.1f} x {dims[2]:.1f} mm"
            f" | {status} ({PLATE_WIDTH_MM:.0f} x {PLATE_DEPTH_MM:.0f} mm)"
        )
        if self.transform_dirty:
            text += " | preview pending apply"
        if self.large_model_mode:
            text += f" | large-file safety mode ({self.large_model_reason})"
        self.transform_info_var.set(text)
        if hasattr(self, "transform_info_label"):
            self.transform_info_label.configure(
                fg="#9fd3a8" if fits and not self.transform_dirty and not self.large_model_mode else "#ffb347"
            )
        self.tool_mode_var.set(self._tool_mode_label())
        self._update_transform_ui_state()

    def _on_transform_value_change(self, *_args):
        if self._setting_transform_vars:
            return
        values = self._get_transform_values()
        if values is not None:
            self.pending_rotation_matrix = rotation_matrix_xyz(values[0], values[1], values[2])
        self._update_transform_flags()
        self._refresh_transform_readout()

    def _preview_temp_path(self, suffix: str) -> str:
        base = os.path.splitext(os.path.basename(self.base_file or self.current_file or "model"))[0]
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", base) or "model"
        token = hashlib.md5((self.base_file or self.current_file or base).encode("utf-8")).hexdigest()[:10]
        return os.path.join(tempfile.gettempdir(), f"layerloom_{suffix}_{base}_{token}.3mf")

    def _build_name_updates(self) -> Dict[str, str]:
        out = {}
        for oid, base in self.oid_to_name.items():
            token = self.assignments.get(oid, {}).get("token", _extract_pat(base))
            out[oid] = _make_labeled_name_with_id(base, oid, token)
        return out

    def _prepare_for_heavy_preview_build(self):
        if self.viewer:
            try:
                self.viewer.close()
            except Exception as e:
                _log("WARN", f"[preview] viewer close failed before rebuild: {e}")
            self.viewer = None
        self._clear_ply_cache()
        gc.collect()

    def _resolve_oid_for_preview_node(self, node_name: str, geom_name: Optional[str] = None) -> Optional[str]:
        candidates: List[str] = []
        for raw in (node_name, geom_name):
            if raw:
                candidates.append(str(raw))

        # Direct ID-bearing paths first.
        for raw in candidates:
            oid = _oid_from_labeled_name(raw)
            if oid:
                return oid

        # Exact raw-name matches next.
        for raw in candidates:
            oid = self.name_to_oid.get(raw)
            if oid:
                return oid

        norm_to_oid = { _normalize_for_match(name): oid for oid, name in self.oid_to_name.items() }
        sorted_names = sorted(self.oid_to_name.items(), key=lambda item: len(item[1]), reverse=True)

        for raw in candidates:
            norm = _normalize_for_match(raw)
            if not norm:
                continue
            oid = norm_to_oid.get(norm)
            if oid:
                return oid
            for candidate_oid, candidate_name in sorted_names:
                candidate_norm = _normalize_for_match(candidate_name)
                if not candidate_norm:
                    continue
                if norm.startswith(candidate_norm):
                    suffix = norm[len(candidate_norm):]
                    if suffix and re.fullmatch(r"[0-9a-f]{6,}", suffix):
                        return candidate_oid
        return None

    def _materialize_current_model(self, output_path: str, *, include_assignments: bool) -> TransformPlan:
        if not self.base_file:
            raise RuntimeError("Open a .3mf first.")

        scale = self._current_scale_value(raise_on_error=True)
        t_plan = time.perf_counter()
        plan = compute_transform_plan(
            self.base_file,
            scale=scale,
            orientation_matrix=self.pending_rotation_matrix,
            plate_width=PLATE_WIDTH_MM,
            plate_depth=PLATE_DEPTH_MM,
        )
        _perf_log("transform plan", t_plan, extra=f"include_assignments={include_assignments}")
        t_write = time.perf_counter()
        write_transformed_3mf(
            self.base_file,
            output_path,
            plan.global_matrix,
            name_updates=self._build_name_updates() if include_assignments else None,
        )
        _perf_log("transform write", t_write, extra=os.path.basename(output_path))
        return plan

    def _refresh_preview_from_current_file(self, *, force: bool = False):
        if not self.current_file or (self.large_model_mode and not force):
            return
        self._export_ply_cache()
        if _TRIMESH_OK and _PYVISTA_OK:
            if not self.viewer:
                self.viewer = PVWindow()
            self._build_or_rebuild_viewer()
        self._tint_viewer_from_assignments()
        gc.collect()

    def _apply_queued_rotation_delta(self, delta_rotation: np.ndarray):
        current = orthonormalize_rotation(self.pending_rotation_matrix)
        new_rotation = orthonormalize_rotation(np.asarray(delta_rotation, dtype=np.float64) @ current)
        self.pending_rotation_matrix = new_rotation
        scale = self._current_scale_value()
        scale = 1.0 if scale is None else scale
        self._sync_rotation_fields_from_matrix(new_rotation)
        self._update_transform_flags()
        self._refresh_transform_readout()
        self._apply_transform_preview(quiet=False)

    def _queue_rotation_delta(self, delta_rotation: np.ndarray):
        self._enqueue_viewer_event("rotation", np.asarray(delta_rotation, dtype=np.float64))

    def _apply_queued_surface_rotation(self, delta_rotation: np.ndarray):
        self._set_viewer_tool_mode("normal")
        self._apply_queued_rotation_delta(delta_rotation)

    def _on_surface_rotation(self, delta_rotation: np.ndarray):
        self._enqueue_viewer_event("surface", np.asarray(delta_rotation, dtype=np.float64))

    def _sync_viewer_tool_mode(self):
        if not self.viewer or not self._last_transform_plan:
            self.tool_mode_var.set(self._tool_mode_label())
            return
        dims = self._last_transform_plan.transformed_bounds.size
        radius = max(float(max(dims)) * 0.72, 5.0)
        self.viewer.set_callbacks(
            on_pick=None,
            on_rotation_commit=self._queue_rotation_delta,
            on_surface_commit=self._on_surface_rotation,
        )
        self.viewer.set_tool_mode(
            self.viewer_tool_mode,
            center=self._last_transform_plan.transformed_bounds.center,
            radius=radius,
        )
        self.tool_mode_var.set(self._tool_mode_label())
        self._update_transform_ui_state()

    def _set_viewer_tool_mode(self, mode: str):
        self.viewer_tool_mode = mode
        self._sync_viewer_tool_mode()

    def _activate_rotate_gizmo(self):
        if not self.base_file:
            messagebox.showwarning("Rotate Gizmo", "Open a .3mf first.")
            return
        if self.large_model_mode:
            messagebox.showwarning(
                "Rotate Gizmo",
                f"Rotate Gizmo is disabled in large-file safety mode.\n\n{self.large_model_reason}",
            )
            return
        if not self._ensure_transform_preview_current(quiet=False):
            return
        if not self.viewer:
            self.viewer = PVWindow()
            self._build_or_rebuild_viewer()
        self._set_viewer_tool_mode("gizmo")

    def _activate_place_on_surface(self):
        if not self.base_file:
            messagebox.showwarning("Place on Surface", "Open a .3mf first.")
            return
        if self.large_model_mode:
            messagebox.showwarning(
                "Place on Surface",
                f"Place on Surface is disabled in large-file safety mode.\n\n{self.large_model_reason}",
            )
            return
        if not self._ensure_transform_preview_current(quiet=False):
            return
        if not self.viewer:
            self.viewer = PVWindow()
            self._build_or_rebuild_viewer()
        self._set_viewer_tool_mode("surface")

    def _apply_transform_preview(self, quiet: bool = False) -> bool:
        if not self.base_file:
            if not quiet:
                messagebox.showwarning("Transform", "Open a .3mf first.")
            return False
        if self.large_model_mode:
            msg = f"Preview materialization is disabled in large-file safety mode.\n\n{self.large_model_reason}"
            if quiet:
                _log("WARN", msg)
            else:
                messagebox.showwarning("Transform", msg)
            return False

        preview_path = self._preview_temp_path("transform_preview")
        try:
            plan = self._materialize_current_model(preview_path, include_assignments=False)
        except Exception as e:
            if quiet:
                _log("WARN", f"Transform preview failed: {e}")
            else:
                messagebox.showerror("Transform", f"Failed to apply transform:\n{e}")
            return False

        self.transformed_preview_file = preview_path
        self.current_file = preview_path
        self._last_transform_plan = plan
        self.applied_rotation_matrix = orthonormalize_rotation(self.pending_rotation_matrix)
        scale = self._current_scale_value()
        self.applied_scale = 1.0 if scale is None else scale
        self._update_transform_flags()
        self._refresh_transform_readout(plan)
        self._refresh_preview_from_current_file()
        return True

    def _ensure_transform_preview_current(self, quiet: bool = False) -> bool:
        if not self.base_file:
            return True
        if self.large_model_mode:
            return True
        if self.transform_dirty or not self.transformed_preview_file or self.current_file != self.transformed_preview_file:
            return self._apply_transform_preview(quiet=quiet)
        return True

    def _reset_transform_to_default(self):
        if not self.base_file:
            return
        self.pending_rotation_matrix = np.eye(3, dtype=np.float64)
        self._set_transform_vars(0.0, 0.0, 0.0, 1.0)
        if self.large_model_mode:
            self.applied_rotation_matrix = np.eye(3, dtype=np.float64)
            self.applied_scale = 1.0
            self._update_transform_flags()
            self._refresh_transform_readout()
            return
        self._apply_transform_preview(quiet=False)

    def _nudge_rotation(self, axis: str, delta: float):
        values = self._get_transform_values()
        if values is None:
            messagebox.showerror("Transform", "Rotate X/Y/Z must be numeric and Scale must be greater than 0.")
            return
        rot_x, rot_y, rot_z, scale = values
        if axis == "x":
            rot_x += delta
        elif axis == "y":
            rot_y += delta
        else:
            rot_z += delta
        self._set_transform_vars(rot_x, rot_y, rot_z, scale)

    def _load_canonical_model(self, stamped_path: str):
        self.base_file = stamped_path
        self.current_file = stamped_path
        self.transformed_preview_file = None
        self._last_transform_plan = None
        self.pending_rotation_matrix = np.eye(3, dtype=np.float64)
        self.applied_rotation_matrix = np.eye(3, dtype=np.float64)
        self.applied_scale = 1.0
        self.transform_dirty = False
        self.transform_is_default = True
        self.viewer_tool_mode = "normal"
        self._set_transform_vars(0.0, 0.0, 0.0, 1.0)
        self._reload_current()

    def _run_glb_import_pipeline(self, glb_path: str) -> Tuple[str, Dict[str, str], Optional[str], int]:
        if not os.path.isfile(GLB_SPLIT_SCRIPT):
            raise RuntimeError(f"GLB split script not found:\n{GLB_SPLIT_SCRIPT}")
        t0 = time.perf_counter()

        try:
            target_colors = int(self.glb_target_colors.get())
        except Exception:
            raise RuntimeError("GLB colors must be an integer.")
        if target_colors < 2:
            raise RuntimeError("GLB colors must be at least 2.")

        color_levels = _target_colors_to_levels(target_colors)
        tmp_dir = os.path.join(tempfile.gettempdir(), "layerloom_glb_import")
        os.makedirs(tmp_dir, exist_ok=True)

        base = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.splitext(os.path.basename(glb_path))[0]) or "import"
        split_3mf = os.path.join(tmp_dir, f"{base}_split.3mf")
        manifest_out = os.path.join(tmp_dir, f"{base}_split.colors.json")
        repaired_3mf = os.path.join(tmp_dir, f"{base}_repaired.3mf")

        split_cmd = [
            sys.executable,
            GLB_SPLIT_SCRIPT,
            glb_path,
            "--out",
            split_3mf,
            "--manifest-out",
            manifest_out,
            "--color-levels",
            str(color_levels),
        ]
        self._run_external_tool("glb-split", split_cmd)
        manifest_map = _read_source_hex_manifest(manifest_out) if os.path.exists(manifest_out) else {}

        repair_warning = None
        import_3mf = split_3mf
        if os.path.isfile(GLB_REPAIR_SCRIPT):
            repair_cmd = [
                sys.executable,
                GLB_REPAIR_SCRIPT,
                split_3mf,
                "--out",
                repaired_3mf,
                "--quiet",
            ]
            try:
                self._run_external_tool("3mf-repair", repair_cmd)
                import_3mf = repaired_3mf
            except Exception as e:
                repair_warning = f"Repair failed; continuing with unrepaired 3MF.\n\n{e}"
                _log("WARN", repair_warning)
        else:
            repair_warning = f"Repair script not found; continuing with unrepaired 3MF.\n\n{GLB_REPAIR_SCRIPT}"
            _log("WARN", repair_warning)

        _perf_log("glb import pipeline", t0, extra=os.path.basename(import_3mf))
        return import_3mf, manifest_map, repair_warning, color_levels

    def _on_open_glb(self):
        p = filedialog.askopenfilename(
            title="Open GLB/GLTF",
            filetypes=[("GLB/GLTF", ("*.glb", "*.gltf")), ("GLB", "*.glb"), ("GLTF", "*.gltf")],
        )
        if not p:
            return

        try:
            import_3mf, manifest_map, repair_warning, color_levels = self._run_glb_import_pipeline(p)
        except Exception as e:
            messagebox.showerror("Open GLB", f"Failed to import GLB:\n{e}")
            return

        self._pending_source_hex_by_name = manifest_map
        stamped = _stamp_ids_only(import_3mf)
        self._load_canonical_model(stamped)
        _log("INFO", f"Opened GLB: {p}")
        _log("INFO", f"GLB import produced 3MF: {import_3mf} (target_colors={self.glb_target_colors.get()}, color_levels={color_levels})")

        if repair_warning:
            _log("WARN", f"[glb-import] {repair_warning}")
            if not self.large_model_mode:
                self.transform_info_var.set("GLB imported; repair warning logged.")
        elif not self.large_model_mode:
            self.transform_info_var.set(
                f"GLB imported and auto-matched using palette '{self.current_palette_name}'."
            )

    # --- NEW: PLY Cache Helpers ---
    def _clear_ply_cache(self):
        _log("DEBUG", f"Clearing PLY cache at: {self.temp_ply_dir}")
        try:
            if os.path.isdir(self.temp_ply_dir):
                shutil.rmtree(self.temp_ply_dir)
            os.makedirs(self.temp_ply_dir)
            self._ply_cache_signature = None
            _log("DEBUG", "PLY cache cleared and recreated.")
        except Exception as e:
            _log("ERROR", f"Failed to clear PLY cache: {e}")

    def _on_exit(self):
        _log("INFO", "Exiting. Cleaning up PLY cache...")
        try:
            if os.path.isdir(self.temp_ply_dir):
                shutil.rmtree(self.temp_ply_dir)
            self._ply_cache_signature = None
            _log("INFO", "Cache cleaned.")
        except Exception as e:
            _log("WARN", f"Could not clean cache on exit: {e}")

        if self.viewer:
            self.viewer.close()
        gc.collect()

        self.destroy()

    def _export_ply_cache(self):
        """NEW: Loads 3MF with trimesh and exports all parts to PLYs."""
        if not self.current_file or not _TRIMESH_OK:
            return

        total_t0 = time.perf_counter()
        try:
            st = os.stat(self.current_file)
            cache_sig = (
                os.path.abspath(self.current_file),
                int(st.st_size),
                int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))),
                tuple(self._names_in_build_order),
            )
        except Exception:
            cache_sig = None

        if cache_sig and cache_sig == self._ply_cache_signature:
            expected = []
            for nm in self._names_in_build_order:
                safe_nm = re.sub(r'[\\/*?:"<>|]', "_", nm)
                expected.append(os.path.join(self.temp_ply_dir, f"{safe_nm}.ply"))
            if expected and all(os.path.exists(p) for p in expected):
                _log("INFO", f"[preview] reusing existing PLY cache for {os.path.basename(self.current_file)}")
                return

        self._clear_ply_cache()
        _log("INFO", f"Loading 3MF with trimesh from: {self.current_file}")

        try:
            # Use `process=False` to ensure we get raw node names
            t_load = time.perf_counter()
            scene = trimesh.load_scene(self.current_file, process=False)
            _perf_log("preview scene load", t_load, extra=os.path.basename(self.current_file))
        except Exception as e:
            _log("ERROR", f"trimesh.load_scene failed: {e}")
            messagebox.showerror("Cache Error", f"trimesh failed to load the 3MF scene: {e}")
            return

        if isinstance(scene, trimesh.Trimesh):
            scene = trimesh.Scene(scene)

        _log("DEBUG", f"Scene graph has {len(scene.graph.nodes_geometry)} geometry nodes.")
        exported_count = 0
        export_time = 0.0

        # We must iterate the geometry nodes to get the *correct* names
        for node_name in scene.graph.nodes_geometry:
            try:
                transform, geom_name = scene.graph[node_name]
                mesh = scene.geometry[geom_name].copy()
                mesh.apply_transform(transform)

                # The node_name from trimesh might have suffixes (e.g., ...68119537796e)
                trimesh_node_name = str(node_name)
                trimesh_geom_name = str(geom_name)

                oid = self._resolve_oid_for_preview_node(trimesh_node_name, trimesh_geom_name)
                if not oid:
                    _log(
                        "WARN",
                        f"Could not map preview node '{trimesh_node_name}' (geom '{trimesh_geom_name}') to an OID. Skipping export.",
                    )
                    continue

                correct_name = self.oid_to_name.get(oid)
                if not correct_name:
                    _log(
                        "WARN",
                        f"Mapped preview node '{trimesh_node_name}' to OID '{oid}', but oid_to_name has no entry. Skipping.",
                    )
                    continue

                # Ensure name is filesystem-safe (it should be, but good practice)
                safe_name = re.sub(r'[\\/*?:"<>|]',"_", correct_name)
                if safe_name != correct_name:
                     _log("WARN", f"Sanitized unsafe part name: '{correct_name}' -> '{safe_name}'")

                ply_path = os.path.join(self.temp_ply_dir, f"{safe_name}.ply")
                t_export = time.perf_counter()
                mesh.export(ply_path) # trimesh infers from extension
                export_time += time.perf_counter() - t_export
                _log("DEBUG", f"Exported cache PLY: {safe_name}.ply")
                exported_count += 1
                del mesh

            except Exception as e:
                _log("ERROR", f"Failed to export mesh node '{node_name}' (geom: '{geom_name}'): {e}")

        scene = None
        gc.collect()
        _log("INFO", f"PLY cache export complete. Exported {exported_count} files.")
        if exported_count > 0:
            self._ply_cache_signature = cache_sig
        _perf_log("preview cache export", total_t0, extra=f"ply_files={exported_count} export_only={export_time:.3f}s")
        if exported_count == 0 and len(self._names_in_build_order) > 0:
             _log("ERROR", "PLY CACHE IS EMPTY but build order is not. Preview will fail.")
             messagebox.showerror("Cache Error", "Failed to export any parts to the PLY cache. Check 3MF structure. The 3D preview will be empty.")


    def _reload_current(self):
        source_file = self.base_file or self.current_file
        if not source_file:
            return
        t_reload = time.perf_counter()
        previous_sel = None
        try:
            current_sel = self.tree.selection()
            if current_sel:
                previous_sel = current_sel[0]
        except Exception:
            previous_sel = None
        if not previous_sel:
            previous_sel = self._last_picked_oid
        self.large_model_mode, self.large_model_reason, self.large_model_info = _large_3mf_reason(source_file)

        root = None
        summary = None
        try:
            t_xml = time.perf_counter()
            if self.large_model_mode:
                summary = _read_model_summary_streaming(source_file)
                _perf_log("reload xml streaming summary", t_xml, extra=os.path.basename(source_file))
            else:
                with zipfile.ZipFile(source_file, "r") as zf:
                    m = _find_model_xml_name(zf)
                    root = _read_model_root(zf, m)
                _perf_log("reload xml parse", t_xml, extra=os.path.basename(source_file))
        except Exception as e:
            messagebox.showerror("Open 3MF", str(e))
            return

        self.assignments = {}
        self._last_picked_oid = None
        if self.large_model_mode:
            self.oid_to_name = dict(summary["oid_to_name"])
            source_hex_by_oid = dict(summary["source_hex_by_oid"])
            ordered = list(summary["ordered"])
            real_oids = set(summary["real_oids"])
            model_objects = list(summary["model_objects"])
        else:
            self.oid_to_name = _oid_to_name_map(root)
            source_hex_by_oid = _oid_to_source_hex_map(root)
            ordered = _build_items_order(root)
            real_oids = _mesh_oid_set(root)
            model_objects = []
            for o in _all_model_objects(root):
                oid = o.get("id")
                if not oid or oid not in real_oids:
                    continue
                name = o.get("name") or f"object_{oid}"
                model_objects.append((oid, name))
        self.name_to_oid = {v: k for k, v in self.oid_to_name.items()}

        self.tree.delete(*self.tree.get_children())
        self.model_objects = []
        for oid, name in model_objects:
            self.tree.insert("", "end", iid=oid, values=(name, "", ""))
            self.model_objects.append((oid, name))
        if previous_sel and self.tree.exists(previous_sel):
            self.tree.selection_set(previous_sel)
            self.tree.see(previous_sel)
            self._last_picked_oid = previous_sel

        viewer_names = [self.oid_to_name.get(oid, f"object_{oid}") for oid in ordered if oid in real_oids]
        all_real_names = [n for _, n in self.model_objects]
        seen = set(viewer_names)
        viewer_names += [n for n in all_real_names if n not in seen]
        self._names_in_build_order = viewer_names

        _log("INFO", f"[xml] total objects: {len(self.oid_to_name)}; with mesh: {len(real_oids)}; "
              f"build items: {len(ordered)}; viewer names: {len(self._names_in_build_order)}")

        for oid, name in self.model_objects:
            entry = _grouped_color_assignment_for_name(name)
            if entry:
                self._set_assignment_for_oid(oid, entry)

        self._apply_source_hex_assignments(source_hex_by_oid)
        if self.large_model_mode:
            self.current_file = source_file
            self.transformed_preview_file = None
            self.viewer_tool_mode = "normal"
            if self.viewer:
                try:
                    self.viewer.close()
                except Exception as e:
                    _log("WARN", f"[large-model] viewer close failed: {e}")
                self.viewer = None
                gc.collect()
            pkg_mb = _bytes_to_mb(self.large_model_info.get("package_bytes"))
            model_mb = _bytes_to_mb(self.large_model_info.get("model_bytes"))
            self.transform_info_var.set(
                f"Large-file safety mode active | package {pkg_mb:.1f} MB | model XML {model_mb:.1f} MB | "
                "3D preview and auto-reload skipped"
            )
            if hasattr(self, "transform_info_label"):
                self.transform_info_label.configure(fg="#ffb347")
            _log("WARN", f"[large-model] safe mode enabled for {os.path.basename(source_file)}: {self.large_model_reason}")
        else:
            if self.base_file:
                if not self._apply_transform_preview(quiet=True):
                    self.current_file = source_file
                    self._refresh_preview_from_current_file()
            else:
                self.current_file = source_file
                self._refresh_preview_from_current_file()
        self._refresh_transform_readout()
        _perf_log("reload current", t_reload, extra=f"objects={len(self.model_objects)} viewer_names={len(self._names_in_build_order)}")

    def _dump_assignments(self):
        print("\n[assignments dump]")
        if not self.assignments:
            print("(none)")
            return
        for oid, info in self.assignments.items():
            nm = self.oid_to_name.get(oid, f"object_{oid}")
            print(f"OID {oid:>4}  NAME {nm}  TOKEN {info.get('token')}  HEX {info.get('hex')}")

    def _on_save(self):
        if not self.base_file:
            messagebox.showwarning("Save", "Open a .3mf first.")
            return
        if not self._ensure_transform_preview_current(quiet=False):
            return
        out = filedialog.asksaveasfilename(
            title="Save Labeled 3MF",
            defaultextension=".3mf",
            filetypes=[("3MF files", "*.3mf")]
        )
        if not out:
            return
        try:
            plan = self._materialize_current_model(out, include_assignments=True)
            self._last_transform_plan = plan
            self._applied_transform_signature = self._get_transform_values() or self._applied_transform_signature
            self.transform_dirty = False
            self._refresh_transform_readout(plan)
            _log("INFO", f"Saved labeled 3MF: {out}")
            self.transform_info_var.set(f"Saved labeled 3MF: {os.path.basename(out)}")
            if hasattr(self, "transform_info_label"):
                self.transform_info_label.configure(fg="#cfd8dc")
        except Exception as e:
            messagebox.showerror("Save", f"Failed to write 3MF:\n{e}")

    def _preweave_pat_audit(self, path: str, title="[pre-weave PAT check]"):
        try:
            _log("INFO", title)
            is_large, reason, _info = _large_3mf_reason(path)
            if is_large:
                summary = _read_model_summary_streaming(path)
                objs = list(summary["model_objects"])
                _log("WARN", f"{title} using streaming summary in large-file safety mode: {reason}")
                for oid, nm in objs[:25]:
                    _log("INFO", f"OID {oid:>4}  NAME {nm}")
                if len(objs) > 25:
                    _log("INFO", f"... {len(objs) - 25} additional object(s) omitted from audit log")
                return

            with zipfile.ZipFile(path, "r") as zf:
                m = _find_model_xml_name(zf)
                root = _read_model_root(zf, m)
            for obj in _all_model_objects(root):
                nm = obj.get("name", "")
                _log("INFO", f"OID {obj.get('id'):>4}  NAME {nm}")
        except Exception as e:
            _log("WARN", f"{title} failed: {e}")

    def _postweave_pat_audit(self, path: str, title="[post-weave PAT check]"):
        self._preweave_pat_audit(path, title=title)

    # def _on_weave(self):
    #     if not self.current_file:
    #         messagebox.showwarning("Weave", "Open a .3mf first.")
    #         return
    #     # Write a temp labeled file using current assignments
    #     try:
    #         tmp_out = os.path.join(tempfile.gettempdir(), f"tmp_weave_{os.path.basename(self.current_file)}")
    #         _rewrite_model_labels(self.current_file, tmp_out, self.assignments)
    #         _log("INFO", f"[weave] temp labeled 3MF → {tmp_out}")
    #     except Exception as e:
    #         messagebox.showerror("Weave", f"Failed to write temp labeled 3MF:\n{e}")
    #         return

    def _on_weave(self):
        if not self.base_file:
            messagebox.showwarning("Weave", "Open a .3mf first.")
            return

        if not self._ensure_transform_preview_current(quiet=False):
            return

        # Ask where the FINAL woven output should be written
        out_dir = filedialog.askdirectory(title="Choose output folder for woven 3MF")
        if not out_dir:
            return

        # Derive a sane base name (avoid stacking preview_id_/tmp_weave_ prefixes)
        base = os.path.splitext(os.path.basename(self.base_file))[0]
        base = re.sub(r"^(preview_id_|tmp_weave_)+", "", base)

        final_woven_path = os.path.join(out_dir, f"{base}_woven.3mf")
        if os.path.exists(final_woven_path):
            if not messagebox.askyesno("Overwrite?", f"File already exists:\n{final_woven_path}\n\nOverwrite it?"):
                return

        # Write a temp labeled file using current assignments (weave input)
        try:
            tmp_out = os.path.join(tempfile.gettempdir(), f"tmp_weave_{base}.3mf")
            plan = self._materialize_current_model(tmp_out, include_assignments=True)
            self._last_transform_plan = plan
            self._applied_transform_signature = self._get_transform_values() or self._applied_transform_signature
            self.transform_dirty = False
            self._refresh_transform_readout(plan)
            _log("INFO", f"[weave] temp labeled 3MF → {tmp_out}")
        except Exception as e:
            messagebox.showerror("Weave", f"Failed to write temp labeled 3MF:\n{e}")
            return

        # Audit BEFORE weaving
        self._preweave_pat_audit(tmp_out, "[pre-weave PAT check]")

        # Run weave, but write the FINAL output to the chosen folder
        woven = _run_weave(tmp_out, step=self.step_var.get(), output_path=final_woven_path)
        if not (woven and os.path.exists(woven)):
            return

        is_large_woven, woven_reason, _woven_info = _large_3mf_reason(woven)
        if is_large_woven:
            _log("WARN", f"[weave] large woven file entering auto-preview safe mode: {woven_reason}")
            self.transform_info_var.set(
                f"Woven model written; rebuilding preview in large-file safety mode ({woven_reason})."
            )
            if hasattr(self, "transform_info_label"):
                self.transform_info_label.configure(fg="#ffb347")
            self._prepare_for_heavy_preview_build()
            self._pending_source_hex_by_name = {}
            stamped_woven = _stamp_ids_only(woven)
            self._load_canonical_model(stamped_woven)
            self._refresh_preview_from_current_file(force=True)
            _log("INFO", f"[weave] woven model loaded in large-file safety mode → {woven}")
            self.transform_info_var.set(
                f"Weave complete | auto-preview rebuilt in safety mode: {os.path.basename(woven)}"
            )
            if hasattr(self, "transform_info_label"):
                self.transform_info_label.configure(fg="#ffb347")
            return

        # Audit AFTER weaving
        self._postweave_pat_audit(woven, "[post-weave PAT check]")

        # We need to ID-stamp the new woven file before loading it
        stamped_woven = _stamp_ids_only(woven)
        if stamped_woven != woven:
             _log("INFO", f"Previewing ID-stamped *woven* temp copy: {stamped_woven}")

        # Load woven model for inspection
        self._load_canonical_model(stamped_woven)
        _log("INFO", f"[weave] woven model loaded → {woven}")
        self.transform_info_var.set(f"Weave complete: {os.path.basename(woven)}")
        if hasattr(self, "transform_info_label"):
            self.transform_info_label.configure(fg="#cfd8dc")

    def _on_preview(self):
        if not (self.base_file or self.current_file) or not (_TRIMESH_OK and _PYVISTA_OK): return
        if self.large_model_mode:
            _log("WARN", f"[large-model] manual preview requested: {self.large_model_reason}")
            self.transform_info_var.set(
                f"Large-file safety mode active | building manual preview on demand ({self.large_model_reason})"
            )
            if hasattr(self, "transform_info_label"):
                self.transform_info_label.configure(fg="#ffb347")
            self._prepare_for_heavy_preview_build()
            self._refresh_preview_from_current_file(force=True)
            return
        if not self._ensure_transform_preview_current(quiet=False):
            return
        if not self.viewer: self.viewer=PVWindow()
        self._build_or_rebuild_viewer()

    # --- NEW: Centralized flash logic ---
    def _flash_actor_by_name(self, name: str, oid: str = None):
        """Flashes an actor and restores its color."""
        if not self.viewer or not name:
            return

        # If OID is provided, use it to get the "true" color.
        original_hex = "#d3d3d3" # Default grey
        if oid:
            entry = self.assignments.get(oid)
            if entry and entry.get("hex"):
                original_hex = entry.get("hex")

        flash_hex = "#FF0000" # Red
        if original_hex.upper() == flash_hex:
            flash_hex = "#00FF00" # Green flash

        _log("DEBUG", f"Flashing actor '{name}' {flash_hex}.")

        # 1. Flash to new color
        self.viewer.set_color_by_name(name, flash_hex, force_flash=True)

        # 2. Schedule restore
        def _restore_color():
            if not self.viewer: return

            # If this was a tree-select, only restore if still selected.
            if oid:
                current_sel = self.tree.selection()
                if not current_sel or current_sel[0] != oid:
                    _log("DEBUG", f"Restore skipped for '{name}'. Selection changed.")
                    return

            _log("DEBUG", f"Restoring actor '{name}' to original color: {original_hex}")
            self.viewer.set_color_by_name(name, original_hex)

        self.after(250, _restore_color) # 250ms flash

    def _build_or_rebuild_viewer(self):
        def _picked(name):
            self._enqueue_viewer_event("pick", str(name))

        try:
            self._last_picked_oid = None
            # The 'self.current_file' argument is now ignored by build_scene
            # It only uses the cache, driven by self._names_in_build_order
            self.viewer.build_scene(self.current_file, self._names_in_build_order, on_pick=_picked)
            self._sync_viewer_tool_mode()
        except Exception as e:
            messagebox.showerror("Preview",str(e))

    def _handle_viewer_pick_event(self, name: str):
        # derive OID from actor name (__ID_<oid>__)—ground truth
        _log("DEBUG", f"3D Pick Event: PyVista actor name='{name}'")
        oid = _oid_from_labeled_name(name) or self.name_to_oid.get(name)
        if oid:
            _log("DEBUG", f"3D Pick -> Mapped to OID='{oid}'")
            self._last_picked_oid = oid
            if self.tree.exists(oid):
                self.tree.selection_set(oid)
                self.tree.see(oid)
                self._flash_actor_by_name(name, oid)
            else:
                _log("WARN", f"3D Pick -> OID '{oid}' not found in tree. Deselecting.")
                self.tree.selection_set("")
        else:
            _log("WARN", f"3D Pick -> !! FAILED to map '{name}' to an OID.")

    def _on_tree_select(self, event=None):
        """Flashes the 3D actor when its GUI row is clicked."""
        sel = self.tree.selection()
        if not sel: return
        oid = sel[0]
        if not self.tree.exists(oid): return
        self._last_picked_oid = oid
        if not self.viewer: return

        name = self.oid_to_name.get(oid)
        if not name: return

        _log("DEBUG", f"Tree row for OID '{oid}' (Name: {name}) selected.")
        self._flash_actor_by_name(name, oid)

    def _assign_selected(self, entry: Dict[str,str]):
        """Assigns the clicked palette swatch to the selected part."""

        # Prefer explicit row selection; otherwise use last-picked OID from viewer
        sel=self.tree.selection()
        oid = sel[0] if sel else self._last_picked_oid

        if not oid or not self.tree.exists(oid):
            _log("WARN", "No OID selected (neither in tree nor via 3D pick). Assignment aborted.")
            return

        _log("DEBUG", f"Target OID: '{oid}'")
        self.assignments[oid]=entry

        vals=list(self.tree.item(oid,"values"))
        vals[1],vals[2]=entry["hex"],entry["token"]
        self.tree.item(oid,values=tuple(vals))

        # This is the critical lookup.
        nm=self.oid_to_name.get(oid,vals[0])

        _log("INFO", f"Assigned Part: {nm} -> {entry['hex']} ({entry['token']})")

        if self.viewer:
            self.viewer.set_color_by_name(nm,entry["hex"])

# ─────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────
def main():
    # This is needed for the dummy palette logic to find the 'palettes' dir
    # if you run it as a script.
    global PALETTES_DIR
    try:
        # __file__ might not be defined (e.g., in interactive)
        script_dir = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        script_dir = os.getcwd() # Fallback to current working directory

    PALETTES_DIR = os.path.join(script_dir, "palettes")
    if not os.path.isdir(PALETTES_DIR):
        _log("WARN", f"'palettes' directory not found at {PALETTES_DIR}. Using dummy palette data.")
        PALETTES_DIR = "" # Will trigger dummy data in _load_palette

    # Update PALETTE_FILES paths
    global PALETTE_FILES
    PALETTE_FILES = {
        "Simple": os.path.join(PALETTES_DIR, "simple_palette.json"),
        "Normal": os.path.join(PALETTES_DIR, "normal_palette.json"),
        "Full":   os.path.join(PALETTES_DIR, "full_palette.json"),
    }

    # Ensure trimesh can find its dependencies if bundled (e.g., with pyinstaller)
    try:
        if hasattr(sys, '_MEIPASS'):
            trimesh.constants.GLTF_VALIDATOR = os.path.join(sys._MEIPASS, 'gltf_validator')
    except Exception:
        pass

    AssignColorsApp().mainloop()

if __name__=="__main__":
    main()
