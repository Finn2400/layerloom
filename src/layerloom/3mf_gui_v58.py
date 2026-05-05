#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LayerLoom — 3MF GUI (v58 — Stability Snapshot)
----------------------------------------------

This file is a direct working snapshot of v57, including the exact
`stack_token` metadata matching path used for sphere-anchor assignments.
"""

from __future__ import annotations
import os, re, sys, io, json, zipfile, colorsys, math, hashlib, importlib, queue, time, tkinter as tk, gc
from collections import Counter
from tkinter import ttk, filedialog, messagebox
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple, Optional
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
GUI_LARGE_PACKAGE_MB = 128.0
GUI_LARGE_MODEL_XML_MB = 512.0
GUI_STREAMING_MODEL_XML_MB = 128.0
GUI_ID_STAMP_MODEL_XML_MB = 128.0


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

SYNTHETIC_PART_PREFIX_RE = re.compile(r"^(part|object)_\d+$", re.IGNORECASE)
INSTANCE_SUFFIX_RE = re.compile(r"__inst_(\d+)$", re.IGNORECASE)
TOKEN_PREFIX_RE = re.compile(r"^([cmykw]+)(?:__|$)", re.IGNORECASE)


def _grouped_color_assignment_for_name(name: Optional[str]) -> Optional[Dict[str, str]]:
    base = _normalize_for_match(name)
    for key, entry in GROUPED_COLOR_ASSIGNMENTS.items():
        if base == key or base.startswith(key):
            return dict(entry)
    return None


def _clean_model_display_name(path_or_name: Optional[str]) -> str:
    name = os.path.basename(str(path_or_name or "").strip())
    stem, ext = os.path.splitext(name)
    stem = re.sub(r"^(preview_id_|tmp_weave_)+", "", stem)
    stem = _strip_id_and_pat(stem).strip("_")
    return f"{stem}{ext}" if ext else stem


def _clean_part_display_name(name: Optional[str]) -> str:
    cleaned = _strip_id_and_pat(str(name or ""))
    cleaned = re.sub(r"^(preview_id_|tmp_weave_)+", "", cleaned)
    cleaned = cleaned.strip("_")
    return cleaned or str(name or "")


def _sanitize_part_label(name: Optional[str]) -> str:
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", "", str(name or ""))
    cleaned = cleaned.replace("\r", " ").replace("\n", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _looks_synthetic_part_display_name(name: Optional[str]) -> bool:
    cleaned = _clean_part_display_name(name)
    if not cleaned:
        return True
    if SYNTHETIC_PART_PREFIX_RE.fullmatch(cleaned):
        return True
    if "__inst_" in cleaned:
        return True
    return False


def _preferred_part_display_name(
    raw_name: Optional[str],
    import_name: Optional[str] = None,
    import_counts: Optional[Dict[str, int]] = None,
) -> str:
    cleaned = _clean_part_display_name(raw_name)
    pretty = _clean_part_display_name(import_name)
    if not pretty:
        return cleaned
    if cleaned == pretty:
        return pretty
    if _looks_synthetic_part_display_name(cleaned):
        return pretty
    if cleaned.startswith(pretty + "__"):
        return pretty
    return cleaned


def _extract_instance_suffix(name: Optional[str]) -> Optional[int]:
    cleaned = _clean_part_display_name(name)
    m = INSTANCE_SUFFIX_RE.search(cleaned)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _extract_pattern_token_from_name(name: Optional[str]) -> Optional[str]:
    cleaned = _clean_part_display_name(name).lower()
    m = TOKEN_PREFIX_RE.match(cleaned)
    if not m:
        return None
    token = m.group(1).lower()
    if token and set(token).issubset(set("cmykw")):
        return token
    return None


def _build_preferred_display_name_map(
    model_objects: List[Tuple[str, str]],
    oid_to_name: Dict[str, str],
    import_name_by_oid: Dict[str, str],
) -> Dict[str, str]:
    counts = Counter(
        _clean_part_display_name(label)
        for label in import_name_by_oid.values()
        if _clean_part_display_name(label)
    )
    seen: Counter[str] = Counter()
    out: Dict[str, str] = {}
    all_oids = [oid for oid, _raw in model_objects]
    for oid in oid_to_name:
        if oid not in all_oids:
            all_oids.append(oid)
    for oid in all_oids:
        raw = oid_to_name.get(oid, "")
        pretty = import_name_by_oid.get(oid)
        display = _preferred_part_display_name(raw, pretty, counts)
        clean_pretty = _clean_part_display_name(pretty)
        if clean_pretty and display == clean_pretty and counts.get(clean_pretty, 0) > 1:
            seen[clean_pretty] += 1
            inst = _extract_instance_suffix(raw)
            suffix = inst if inst is not None else seen[clean_pretty]
            display = f"{clean_pretty} ({suffix})"
        out[oid] = display
    return out


def _trimesh_to_polydata(mesh):
    if not (_PYVISTA_OK and _TRIMESH_OK):
        return None
    if mesh is None or len(getattr(mesh, "faces", [])) == 0:
        return None
    faces = np.hstack(
        [
            np.full((len(mesh.faces), 1), 3, dtype=np.int64),
            np.asarray(mesh.faces, dtype=np.int64),
        ]
    )
    return pv.PolyData(np.asarray(mesh.vertices, dtype=np.float64), faces.ravel())


@dataclass
class PreviewPartRecord:
    instance_key: str
    oid: str
    assignment_key: str
    actor_name: str
    display_name: str
    build_index: int
    source_name: str
    polydata: Optional[Any] = None


@dataclass
class PreviewManifest:
    records: List[PreviewPartRecord] = field(default_factory=list)
    by_instance_key: Dict[str, PreviewPartRecord] = field(default_factory=dict)
    actor_name_to_instance: Dict[str, str] = field(default_factory=dict)
    oid_to_instances: Dict[str, List[str]] = field(default_factory=dict)
    signature: Optional[Tuple[object, ...]] = None

    @classmethod
    def from_records(
        cls,
        records: List[PreviewPartRecord],
        *,
        signature: Optional[Tuple[object, ...]] = None,
    ) -> "PreviewManifest":
        manifest = cls(signature=signature)
        manifest.records = list(records)
        manifest.by_instance_key = {record.instance_key: record for record in records}
        manifest.actor_name_to_instance = {record.actor_name: record.instance_key for record in records}
        oid_to_instances: Dict[str, List[str]] = {}
        for record in records:
            oid_to_instances.setdefault(record.oid, []).append(record.instance_key)
        manifest.oid_to_instances = oid_to_instances
        return manifest

    def primary_instance_key(self, oid: str) -> Optional[str]:
        instances = self.oid_to_instances.get(oid) or []
        return instances[0] if instances else None

    def actor_names_for_oid(self, oid: str) -> List[str]:
        return [self.by_instance_key[key].actor_name for key in self.oid_to_instances.get(oid, []) if key in self.by_instance_key]


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

def _oid_to_stack_token_map(root: ET.Element) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for obj in _all_model_objects(root):
        oid = obj.get("id")
        if not oid:
            continue
        mg = obj.find("m:metadatagroup", NS)
        if mg is None:
            continue
        for md in mg.findall("m:metadata", NS):
            if _metadata_key(md.get("name")) != "stack_token":
                continue
            tok = str(md.text or "").strip().lower()
            if tok:
                out[oid] = tok
            break
    return out


def _oid_to_import_build_label_map(root: ET.Element) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for obj in _all_model_objects(root):
        oid = obj.get("id")
        if not oid:
            continue
        mg = obj.find("m:metadatagroup", NS)
        if mg is None:
            continue
        for md in mg.findall("m:metadata", NS):
            if _metadata_key(md.get("name")) != "import_source_build_label":
                continue
            label = _sanitize_part_label(md.text or "")
            if label:
                out[oid] = label
            break
    return out

def _read_model_summary_streaming(path: str) -> Dict[str, object]:
    summary: Dict[str, object] = {
        "oid_to_name": {},
        "source_hex_by_oid": {},
        "stack_token_by_oid": {},
        "import_name_by_oid": {},
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
                            "stack_token": None,
                            "import_build_label": None,
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
                    elif key == "stack_token":
                        tok = text.lower()
                        if tok:
                            current_object["stack_token"] = tok
                    elif key == "import_source_build_label":
                        label = _sanitize_part_label(text)
                        if label:
                            current_object["import_build_label"] = label
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
                        tok = current_object.get("stack_token")
                        if tok:
                            summary["stack_token_by_oid"][oid] = tok
                        import_name = current_object.get("import_build_label")
                        if import_name:
                            summary["import_name_by_oid"][oid] = str(import_name)
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
    info = _3mf_size_info(input_3mf)
    model_bytes = info.get("model_bytes") or 0
    if model_bytes >= GUI_ID_STAMP_MODEL_XML_MB * 1024.0 * 1024.0:
        _log("WARN", f"ID-stamping skipped for large model XML: {_bytes_to_mb(model_bytes):.1f} MB")
        return input_3mf
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
        self.actor_name_to_instance: Dict[str, str] = {}
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
        self._normal_click_press: Optional[np.ndarray] = None
        self._picker = None
        self._temp_transform_active = False
        self._temp_user_matrix = None
        self._observer_ids: List[int] = []
        self._picking_mode = "none"
        self._surface_place_cache: Dict[str, Dict[str, object]] = {}

    def _teardown_scene(self):
        self._active_drag = None
        self._normal_click_press = None
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
        self.actor_name_to_instance.clear()
        self.gizmo_actors.clear()
        self.point_label_actor = None
        self.surface_highlight_actor = None
        self._surface_place_cache.clear()

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

    def build_scene(
        self,
        file_path: str,
        names_in_order: List[str],
        on_pick=None,
        *,
        manifest: Optional[PreviewManifest] = None,
        mesh_cache: Optional[Dict[str, "pv.PolyData"]] = None,
    ):
        self.on_model_pick = on_pick
        p = self._ensure_plotter()
        self._teardown_scene()
        use_memory = bool(manifest and manifest.records and mesh_cache)
        _log("INFO", "[viewer] plotter created; loading memory" if use_memory else "[viewer] plotter created; loading cached actors")

        name_labels = []
        added = 0
        if use_memory:
            for record in manifest.records:
                poly = mesh_cache.get(record.instance_key)
                if poly is None:
                    _log("WARN", f"[build_scene] In-memory cache miss: '{record.instance_key}'")
                    continue
                try:
                    act = p.add_mesh(poly, name=record.actor_name, color="#d3d3d3", pickable=True, reset_camera=False)
                except Exception as e:
                    _log("ERROR", f"[build_scene] Failed to add in-memory mesh '{record.actor_name}': {e}")
                    continue
                if act.mapper:
                    act.mapper.scalar_visibility = False
                if act.prop:
                    act.prop.diffuse = 1.0
                    act.prop.specular = 0.0
                    act.prop.interpolation = "gouraud"
                added += 1
                name_labels.append((poly.center, record.display_name))
            self.actor_name_to_instance = dict(manifest.actor_name_to_instance)
            self.actors_by_name = {record.actor_name: p.actors[record.actor_name] for record in manifest.records if record.actor_name in p.actors}
        else:
            _log("DEBUG", f"[build_scene] Building from PLY cache dir: {self.temp_ply_dir}")
            _log("DEBUG", f"[build_scene] Attempting to load {len(names_in_order)} parts from build order...")

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

                act = p.add_mesh(poly, name=nm, color="#d3d3d3", pickable=True, reset_camera=False)
                if act.mapper:
                    act.mapper.scalar_visibility = False
                if act.prop:
                    act.prop.diffuse = 1.0
                    act.prop.specular = 0.0
                    act.prop.interpolation = "gouraud"
                added += 1
                name_labels.append((poly.center, _clean_part_display_name(nm)))

            self.actors_by_name = {nm: p.actors[nm] for nm in names_in_order if nm in p.actors}

        _log("INFO", f"[viewer] added {added} cached actor(s)")

        if added == 0:
            _log("WARN", "[build_scene] No actors were added from preview cache. Cache might be empty or build order is wrong.")

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

        if self.tool_mode in {"gizmo", "surface", "plate", "normal"}:
            self._ensure_raw_interaction_handlers()
        else:
            self._remove_raw_interaction_handlers()
        try:
            vtk = _get_vtk()
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
            self._picking_mode = "normal"
            _log("INFO", "[viewer] normal part picking enabled via raw cell picker")
        elif self.tool_mode in ("surface", "plate"):
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
        self._picking_mode = "plate"
        self._ensure_raw_interaction_handlers()
        _log("INFO", "[viewer] hull-face picking enabled via raw cell picker")

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
        candidates = [(x, y)]
        try:
            h = int(self.plotter.window_size[1])
        except Exception:
            h = None
        if h is not None:
            fy = max(0, h - y)
            if fy != y:
                candidates.append((x, fy))
        for px, py in candidates:
            self._picker.Pick(int(px), int(py), 0.0, renderer)
            actor = self._picker.GetActor()
            if actor is None:
                continue
            pick_pos = np.asarray(self._picker.GetPickPosition(), dtype=np.float64)
            cell_id = int(self._picker.GetCellId())
            return self._actor_name(actor), actor, cell_id, pick_pos
        return None, None, -1, None

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

    def _surface_place_entry(self, actor_name: str, actor):
        cached = self._surface_place_cache.get(actor_name)
        if cached is not None:
            return cached
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

        cached = {
            "hull_tm": hull_tm,
            "face_centroids": face_centroids,
            "face_normals": face_normals,
            "face_areas": face_areas,
        }
        self._surface_place_cache[actor_name] = cached
        return cached

    def _surface_delta_from_point(self, actor, picked_point: np.ndarray, actor_name: Optional[str] = None):
        actor_name = actor_name or self._actor_name(actor)
        if not actor_name:
            return None
        entry = self._surface_place_entry(actor_name, actor)
        if not entry:
            return None

        hull_tm = entry["hull_tm"]
        face_centroids = np.asarray(entry["face_centroids"], dtype=np.float64)
        face_normals = np.asarray(entry["face_normals"], dtype=np.float64)
        face_areas = np.asarray(entry["face_areas"], dtype=np.float64)

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

    def _surface_pick_result_at_display(self, display_pos):
        name, actor, _cell_id, pick_pos = self._pick_at_display(display_pos)
        if not name or actor is None or pick_pos is None:
            return None
        if name not in self.actors_by_name:
            return None
        result = self._surface_delta_from_point(actor, np.asarray(pick_pos, dtype=np.float64), actor_name=name)
        if not result:
            return None
        delta, patch_poly = result
        return name, delta, patch_poly

    def _update_surface_hover(self, display_pos):
        result = self._surface_pick_result_at_display(display_pos)
        if not result:
            self._clear_surface_highlight()
            try:
                self.plotter.render()
            except Exception:
                pass
            return
        _name, _delta, patch_poly = result
        self._highlight_surface_patch(patch_poly)

    def _commit_surface_pick_at_display(self, display_pos):
        result = self._surface_pick_result_at_display(display_pos)
        if not result:
            return
        _name, delta, patch_poly = result
        self._highlight_surface_patch(patch_poly)
        if callable(self.on_surface_commit):
            self.on_surface_commit(delta)

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
        if self.tool_mode == "normal":
            self._normal_click_press = np.asarray(self._current_event_xy(), dtype=np.float64)
            return
        if self.tool_mode != "gizmo" or self._active_drag is not None:
            return
        pos = self._current_event_xy()
        name, _actor, _cell_id, pick_pos = self._pick_at_display(pos)
        if not name or not name.startswith("__gizmo_") or pick_pos is None:
            return
        match = re.fullmatch(r"__gizmo_([xyz])__", str(name))
        if not match:
            return
        self._start_gizmo_drag(match.group(1), pick_pos, pos)

    def _on_mouse_move(self, *_args):
        if self._active_drag is None:
            if self.tool_mode in {"surface", "plate"}:
                self._update_surface_hover(self._current_event_xy())
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
            if self.tool_mode in {"surface", "plate"}:
                self._commit_surface_pick_at_display(self._current_event_xy())
                return
            if self.tool_mode == "normal":
                release = np.asarray(self._current_event_xy(), dtype=np.float64)
                press = self._normal_click_press
                self._normal_click_press = None
                if press is not None and np.linalg.norm(release - press) <= 4.5:
                    self._handle_left_click(tuple(release))
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

    def _exact_palette_entry_for_token(self, token: str) -> Optional[Dict[str, str]]:
        tok = str(token or "").strip().lower()
        if not tok:
            return None
        path = PALETTE_FILES.get(self.current_palette_name)
        if not path:
            return None
        try:
            all_entries = _load_palette_file(path, limit_two=False)
        except Exception:
            return None
        for entry in all_entries:
            if str(entry.get("token", "")).strip().lower() == tok:
                return entry
        return None

    def _set_assignment_for_oid(self, oid: str, entry: Dict[str, str], source_hex: Optional[str] = None):
        data = {"token": entry["token"], "hex": entry["hex"]}
        if source_hex:
            data["source_hex"] = source_hex
        self.assignments[oid] = data

        if self.tree.exists(oid):
            vals = list(self.tree.item(oid, "values"))
            vals[1], vals[2] = entry["hex"], entry["token"]
            self.tree.item(oid, values=tuple(vals))

    def _apply_source_metadata_assignments(
        self,
        source_hex_by_oid: Dict[str, str],
        stack_token_by_oid: Optional[Dict[str, str]] = None,
    ) -> int:
        if not self.current_palette and not stack_token_by_oid:
            _log("WARN", "No palette entries available for source-color matching.")
            return 0

        matched = 0
        exact = 0
        for oid, name in self.model_objects:
            stack_token = (stack_token_by_oid or {}).get(oid)
            if not stack_token:
                stack_token = _extract_pat(name)
            if stack_token:
                exact_entry = self._exact_palette_entry_for_token(stack_token)
                if exact_entry:
                    source_hex = source_hex_by_oid.get(oid)
                    if not source_hex:
                        source_hex = self._pending_source_hex_by_name.get(_normalize_for_match(name))
                    self._set_assignment_for_oid(oid, exact_entry, source_hex=source_hex)
                    matched += 1
                    exact += 1
                    continue

            inferred_token = _extract_pattern_token_from_name(
                getattr(self, "oid_to_import_name", {}).get(oid)
                or getattr(self, "oid_to_display_name", {}).get(oid)
                or name
            )
            if inferred_token:
                exact_entry = self._exact_palette_entry_for_token(inferred_token)
                if exact_entry:
                    source_hex = source_hex_by_oid.get(oid)
                    if not source_hex:
                        source_hex = self._pending_source_hex_by_name.get(_normalize_for_match(name))
                    self._set_assignment_for_oid(oid, exact_entry, source_hex=source_hex)
                    matched += 1
                    exact += 1
                    continue

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
            if exact:
                _log(
                    "INFO",
                    f"Auto-matched {matched} part(s) from source metadata using palette '{self.current_palette_name}' "
                    f"({exact} exact token, {matched - exact} nearest-color)."
                )
            else:
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

    def _build_assignment_metadata_updates(self) -> Dict[str, Dict[str, str]]:
        out: Dict[str, Dict[str, str]] = {}
        for oid, info in self.assignments.items():
            token = info.get("token")
            if not token:
                continue
            metadata = {"stack_token": token}
            source_hex = info.get("source_hex") or info.get("hex")
            if source_hex:
                metadata["source_hex"] = source_hex
            out[oid] = metadata
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
            metadata_updates=self._build_assignment_metadata_updates() if include_assignments else None,
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
            stack_token_by_oid = dict(summary.get("stack_token_by_oid", {}))
            import_name_by_oid = dict(summary.get("import_name_by_oid", {}))
            ordered = list(summary["ordered"])
            real_oids = set(summary["real_oids"])
            model_objects = list(summary["model_objects"])
        else:
            self.oid_to_name = _oid_to_name_map(root)
            source_hex_by_oid = _oid_to_source_hex_map(root)
            stack_token_by_oid = _oid_to_stack_token_map(root)
            import_name_by_oid = _oid_to_import_build_label_map(root)
            ordered = _build_items_order(root)
            real_oids = _mesh_oid_set(root)
            model_objects = []
            for o in _all_model_objects(root):
                oid = o.get("id")
                if not oid or oid not in real_oids:
                    continue
                name = o.get("name") or f"object_{oid}"
                model_objects.append((oid, name))
        self.oid_to_import_name = dict(import_name_by_oid)
        self.oid_to_display_name = _build_preferred_display_name_map(model_objects, self.oid_to_name, import_name_by_oid)
        self.name_to_oid = {v: k for k, v in self.oid_to_name.items()}

        self.tree.delete(*self.tree.get_children())
        self.model_objects = []
        for oid, name in model_objects:
            self.tree.insert("", "end", iid=oid, values=(self.oid_to_display_name.get(oid, _clean_part_display_name(name)), "", ""))
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

        self._apply_source_metadata_assignments(source_hex_by_oid, stack_token_by_oid)
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
# Qt v58 single-window shell
# ─────────────────────────────────────────────────────────────────────
try:
    from qtpy import QtCore, QtGui, QtWidgets
    from pyvistaqt import QtInteractor

    _QT_OK = True
except Exception as e:
    _QT_OK = False
    QtCore = QtGui = QtWidgets = None
    QtInteractor = None
    _log("WARN", f"Qt single-window UI unavailable: {e}")


class CollapsibleSection(QtWidgets.QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.toggle_btn = QtWidgets.QToolButton(text=title, checkable=True, checked=True)
        self.toggle_btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.toggle_btn.setArrowType(QtCore.Qt.DownArrow)
        self.toggle_btn.setStyleSheet(
            "QToolButton { border: none; font-weight: 600; color: #e8e8e8; padding: 4px 0; }"
        )
        self.toggle_btn.toggled.connect(self._on_toggled)

        self.content = QtWidgets.QWidget()
        self.content_layout = QtWidgets.QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 6, 0, 0)
        self.content_layout.setSpacing(6)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.toggle_btn)
        outer.addWidget(self.content)

    def _on_toggled(self, checked: bool):
        self.content.setVisible(checked)
        self.toggle_btn.setArrowType(QtCore.Qt.DownArrow if checked else QtCore.Qt.RightArrow)


class PaletteSwatchButton(QtWidgets.QToolButton):
    def __init__(self, entry: Dict[str, str], parent=None):
        super().__init__(parent)
        self.entry = dict(entry)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setAutoRaise(True)
        self.setIconSize(QtCore.QSize(52, 52))
        self.setFixedSize(70, 82)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.setText(self.entry["token"])
        self.setStyleSheet(
            "QToolButton { color: #f2f2f2; padding: 1px; border: none; font-size: 9px; font-weight: 400; }"
            "QToolButton:hover { background: rgba(255, 255, 255, 0.08); border-radius: 6px; }"
        )
        pix = QtGui.QPixmap(52, 52)
        pix.fill(QtGui.QColor(self.entry["hex"]))
        painter = QtGui.QPainter(pix)
        painter.setPen(QtGui.QPen(QtGui.QColor("#8a8a8a")))
        painter.drawRect(0, 0, 51, 51)
        painter.end()
        self.setIcon(QtGui.QIcon(pix))


class OverlayHostWidget(QtWidgets.QWidget):
    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.owner._position_viewer_overlays()


class EmbeddedPVViewer(PVWindow):
    class _MouseEventFilter(QtCore.QObject):
        def __init__(self, owner, parent=None):
            super().__init__(parent)
            self.owner = owner

        def eventFilter(self, obj, event):
            owner = self.owner
            if obj is owner.plotter and owner.tool_mode == "normal":
                try:
                    etype = event.type()
                    if etype == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
                        owner._normal_click_press = np.array([float(event.pos().x()), float(event.pos().y())], dtype=np.float64)
                    elif etype == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.LeftButton:
                        release = np.array([float(event.pos().x()), float(event.pos().y())], dtype=np.float64)
                        press = owner._normal_click_press
                        owner._normal_click_press = None
                        if press is not None and np.linalg.norm(release - press) <= 4.5:
                            owner._handle_left_click((float(release[0]), float(release[1])))
                except Exception:
                    pass
            return False

    def __init__(self, parent=None):
        super().__init__()
        self.plotter = QtInteractor(parent)
        self._configured = False
        self._layer_height = 0.2
        self._layer_scale = 1.0
        self._layer_preview_actors: List[object] = []
        self._static_plate_overlay_built = False
        self._event_filter = self._MouseEventFilter(self, self.plotter)
        self.plotter.installEventFilter(self._event_filter)

    def widget(self):
        return self.plotter

    def _ensure_plotter(self):
        p = self.plotter
        if self._configured:
            return p
        p.set_background("#f6f6f7")
        try:
            p.enable_anti_aliasing()
        except Exception:
            pass
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
                color="#cfd5dd",
                opacity=0.82,
                show_edges=True,
                edge_color="#9099a3",
                lighting=False,
                pickable=False,
                reset_camera=False,
            )
        except Exception as e:
            _log("WARN", f"[viewer] build plate overlay unavailable: {e}")
        self._configured = True
        self._rebuild_static_plate_overlay()
        return p

    def _clear_layer_preview(self):
        if not self.plotter:
            self._layer_preview_actors = []
            return
        for actor in list(self._layer_preview_actors):
            try:
                self.plotter.remove_actor(actor, reset_camera=False)
            except Exception:
                pass
        self._layer_preview_actors = []

    def _read_plate_ascii_lines(self) -> List[str]:
        path = "/Users/finn/Downloads/benchy_ascii_with_text.txt"
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                raw_lines = f.read().splitlines()
        except Exception as e:
            _log("WARN", f"[viewer] ASCII plate art unavailable at {path}: {e}")
            return []
        cells = [
            (r, c)
            for r, line in enumerate(raw_lines)
            for c, ch in enumerate(line)
            if not ch.isspace()
        ]
        if not cells:
            return []
        min_r = min(r for r, _ in cells)
        max_r = max(r for r, _ in cells)
        min_c = min(c for _, c in cells)
        max_c = max(c for _, c in cells)
        return [line[min_c : max_c + 1] for line in raw_lines[min_r : max_r + 1]]

    def _make_raster_quad_mesh(
        self,
        cells,
        *,
        x0: float,
        y0: float,
        cell_w: float,
        cell_h: float,
        rows: int,
        z: float,
        fill: float = 0.86,
    ):
        points = []
        faces = []
        pad_x = cell_w * (1.0 - fill) * 0.5
        pad_y = cell_h * (1.0 - fill) * 0.5
        for row, col in cells:
            x_left = x0 + (col * cell_w) + pad_x
            x_right = x0 + ((col + 1) * cell_w) - pad_x
            # Source row 0 is the top of the art, so map it to higher plate Y.
            y_bottom = y0 + ((rows - 1 - row) * cell_h) + pad_y
            y_top = y0 + ((rows - row) * cell_h) - pad_y
            start = len(points)
            points.extend(
                [
                    (x_left, y_bottom, z),
                    (x_right, y_bottom, z),
                    (x_right, y_top, z),
                    (x_left, y_top, z),
                ]
            )
            faces.extend([4, start, start + 1, start + 2, start + 3])
        if not points:
            return None
        return pv.PolyData(np.asarray(points, dtype=np.float64), np.asarray(faces, dtype=np.int64))

    def _make_line_segments_mesh(self, segments):
        points = []
        lines = []
        for a, b in segments:
            start = len(points)
            points.extend([a, b])
            lines.extend([2, start, start + 1])
        if not points:
            return None
        mesh = pv.PolyData(np.asarray(points, dtype=np.float64))
        mesh.lines = np.asarray(lines, dtype=np.int64)
        return mesh

    def _bitmap_glyphs(self):
        return {
            "0": ["111", "101", "101", "101", "111"],
            "1": ["010", "110", "010", "010", "111"],
            "2": ["111", "001", "111", "100", "111"],
            "8": ["111", "101", "111", "101", "111"],
            ".": ["0", "0", "0", "0", "1"],
            "m": ["00000", "11010", "10101", "10101", "10101"],
            " ": ["0", "0", "0", "0", "0"],
        }

    def _bitmap_text_cells(self, text: str):
        glyphs = self._bitmap_glyphs()
        cells = []
        cursor = 0
        rows = 5
        for ch in str(text):
            glyph = glyphs.get(ch.lower(), glyphs[" "])
            width = max(len(row) for row in glyph)
            for r, row in enumerate(glyph):
                for c, mark in enumerate(row):
                    if mark != "0" and not mark.isspace():
                        cells.append((r, cursor + c))
            cursor += width + 1
        return cells, rows, max(cursor - 1, 0)

    def _make_bitmap_text_mesh(self, text: str, *, x0: float, y0: float, cell: float, z: float):
        cells, rows, _cols = self._bitmap_text_cells(text)
        return self._make_raster_quad_mesh(
            cells,
            x0=x0,
            y0=y0,
            cell_w=cell,
            cell_h=cell,
            rows=rows,
            z=z,
            fill=0.9,
        )

    def _add_static_ruler_overlay(self, actors: List[object], *, z: float) -> None:
        ruler_length = 12.0
        ruler_height = 6.0
        start_x = PLATE_WIDTH_MM - 103.0
        start_y = 8.0
        spacing_x = 32.0
        samples = [0.08, 0.12, 0.20]
        for idx, layer_h in enumerate(samples):
            x0 = start_x + idx * spacing_x
            y0 = start_y
            segments = [
                ((x0, y0, z), (x0 + ruler_length, y0, z)),
                ((x0, y0 + ruler_height, z), (x0 + ruler_length, y0 + ruler_height, z)),
                ((x0, y0, z), (x0, y0 + ruler_height, z)),
                ((x0 + ruler_length, y0, z), (x0 + ruler_length, y0 + ruler_height, z)),
            ]
            tick_count = min(180, int(math.floor(ruler_length / layer_h)))
            for tick in range(tick_count + 1):
                x = x0 + tick * layer_h
                if x > x0 + ruler_length + 1e-9:
                    break
                major = tick % 5 == 0
                tick_len = ruler_height if major else ruler_height * 0.72
                segments.append(((x, y0, z), (x, y0 + tick_len, z)))
            mesh = self._make_line_segments_mesh(segments)
            if mesh is not None:
                actor = self.plotter.add_mesh(
                    mesh,
                    name=f"__layer_static_ruler_{idx}__",
                    color="#000000",
                    line_width=1.7,
                    opacity=1.0,
                    lighting=False,
                    pickable=False,
                    reset_camera=False,
                )
                actors.append(actor)
            label = f"{layer_h:.2f} mm"
            label_mesh = self._make_bitmap_text_mesh(label, x0=x0 - 1.0, y0=y0 + ruler_height + 2.0, cell=0.62, z=z + 0.005)
            if label_mesh is not None:
                actor = self.plotter.add_mesh(
                    label_mesh,
                    name=f"__layer_static_ruler_label_{idx}__",
                    color="#000000",
                    opacity=1.0,
                    lighting=False,
                    smooth_shading=False,
                    pickable=False,
                    reset_camera=False,
                )
                actors.append(actor)

    def _rebuild_static_plate_overlay(self):
        if not self.plotter or not self._configured:
            return
        self._clear_layer_preview()
        try:
            z = 0.075
            actors: List[object] = []
            lines = self._read_plate_ascii_lines()
            if lines:
                rows = len(lines)
                target_height = 52.0
                cell_h = min(target_height / max(rows, 1), 1.05)
                # Monospace ASCII cells are visually taller than wide; square
                # plate quads make the source art look stretched in X.
                cell_w = cell_h * 0.72
                x0 = 8.0
                y0 = 6.0
                for ch, color in (("0", "#000000"), ("1", "#1f78ff")):
                    cells = [
                        (r, c)
                        for r, line in enumerate(lines)
                        for c, mark in enumerate(line)
                        if mark == ch
                    ]
                    mesh = self._make_raster_quad_mesh(
                        cells,
                        x0=x0,
                        y0=y0,
                        cell_w=cell_w,
                        cell_h=cell_h,
                        rows=rows,
                        z=z,
                        fill=0.88,
                    )
                    if mesh is None:
                        continue
                    actor = self.plotter.add_mesh(
                        mesh,
                        name=f"__plate_ascii_{ch}__",
                        color=color,
                        opacity=0.95,
                        lighting=False,
                        smooth_shading=False,
                        pickable=False,
                        reset_camera=False,
                    )
                    actors.append(actor)
            self._add_static_ruler_overlay(actors, z=z + 0.01)
            self._layer_preview_actors = actors
            self._static_plate_overlay_built = True
            self.plotter.render()
        except Exception as e:
            _log("WARN", f"[viewer] static plate overlay unavailable: {e}")

    def _rebuild_layer_preview(self):
        self._rebuild_static_plate_overlay()

    def set_layer_preview(self, layer_height: float, scale: float):
        self._layer_height = max(float(layer_height), 0.001)
        self._layer_scale = max(float(scale), 0.001)
        # Static plate overlay intentionally does not change with selected layer height.
        return

    def _make_plate_text_mesh(self, text: str, *, target_width: Optional[float] = None, target_height: Optional[float] = None):
        mesh = pv.Text3D(text, depth=0.01)
        bounds = mesh.bounds
        width = max(float(bounds[1] - bounds[0]), 1e-6)
        height = max(float(bounds[3] - bounds[2]), 1e-6)
        scale_x = 1.0
        scale_y = 1.0
        scale_z = 1.0
        if target_width is not None:
            scale_x = float(target_width) / width
        if target_height is not None:
            scale_y = float(target_height) / height
        if target_width is None and target_height is not None:
            scale_x = scale_y
        if target_height is None and target_width is not None:
            scale_y = scale_x
        scale_z = min(scale_x, scale_y)
        return mesh.scale([scale_x, scale_y, scale_z], inplace=False)

    def _make_layer_reference_grid(self):
        # Compatibility shim for older callers; v61 uses the static overlay.
        self._rebuild_static_plate_overlay()
        return None

    def _visible_scene_bounds(self) -> Optional[Tuple[float, float, float, float, float, float]]:
        bounds = []
        try:
            for actor in self.actors_by_name.values():
                if actor is None:
                    continue
                b = actor.GetBounds()
                if not b:
                    continue
                vals = tuple(float(v) for v in b)
                if all(math.isfinite(v) for v in vals):
                    bounds.append(vals)
        except Exception:
            pass
        if not bounds:
            return (0.0, PLATE_WIDTH_MM, 0.0, PLATE_DEPTH_MM, 0.0, 1.0)
        return (
            min(b[0] for b in bounds),
            max(b[1] for b in bounds),
            min(b[2] for b in bounds),
            max(b[3] for b in bounds),
            min(b[4] for b in bounds),
            max(b[5] for b in bounds),
        )

    def _set_default_loaded_camera(self):
        if not self.plotter:
            return
        try:
            b = self._visible_scene_bounds()
            if not b:
                return
            center = np.array(
                [
                    (b[0] + b[1]) * 0.5,
                    (b[2] + b[3]) * 0.5,
                    (b[4] + b[5]) * 0.5,
                ],
                dtype=np.float64,
            )
            span_x = max(float(b[1] - b[0]), PLATE_WIDTH_MM * 0.65, 1.0)
            span_y = max(float(b[3] - b[2]), PLATE_DEPTH_MM * 0.65, 1.0)
            span_z = max(float(b[5] - b[4]), 20.0)
            distance = max(span_x, span_y) * 1.42 + span_z * 0.55
            # Front-facing with a shallow top-down tilt: easier to read than
            # raw reset_camera(), but still shows model height and plate depth.
            camera = (
                float(center[0]),
                float(center[1] - distance),
                float(center[2] + distance * 0.42),
            )
            focal = (float(center[0]), float(center[1]), float(center[2]))
            self.plotter.camera_position = [camera, focal, (0.0, 0.0, 1.0)]
            try:
                self.plotter.camera.SetViewAngle(32.0)
            except Exception:
                pass
            self.plotter.reset_camera_clipping_range()
            self.plotter.render()
        except Exception as e:
            _log("WARN", f"[viewer] default camera setup failed: {e}")

    def build_scene(self, file_path: str, names_in_order: List[str], on_pick=None, *, manifest=None, mesh_cache=None):
        super().build_scene(file_path, names_in_order, on_pick=on_pick, manifest=manifest, mesh_cache=mesh_cache)
        if self.point_label_actor is not None:
            try:
                self.plotter.remove_actor(self.point_label_actor, reset_camera=False)
            except Exception:
                pass
            self.point_label_actor = None
            try:
                self.plotter.render()
            except Exception:
                pass
        self._rebuild_layer_preview()
        self._set_default_loaded_camera()

    def close(self):
        self._teardown_scene()
        try:
            self.plotter.close()
        except Exception:
            pass
        gc.collect()


class QtAssignColorsApp(QtWidgets.QMainWindow):
    def __init__(self):
        if not _QT_OK:
            raise RuntimeError("Qt UI dependencies are unavailable.")
        super().__init__()
        set_log_level("INFO")
        self.setWindowTitle(APP_TITLE)
        self.resize(1540, 980)
        self.setMinimumSize(1100, 720)
        self.setStyleSheet(
            """
            QMainWindow { background: #1f1f1f; color: #e9e9e9; }
            QWidget { color: #e9e9e9; font-size: 12px; }
            QFrame#sidebarFrame { background: #232323; border-left: 1px solid #3a3a3a; }
            QFrame#overlayFrame { background: rgba(30, 30, 30, 175); border-radius: 8px; }
            QLabel#muted { color: #b7b7b7; }
            QLabel#titleValue { color: #ffffff; font-weight: 600; }
            QGroupBox { border: 1px solid #414141; border-radius: 8px; margin-top: 6px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #f0f0f0; }
            QTreeWidget, QAbstractItemView { background: #151515; alternate-background-color: #191919; border: 1px solid #3c3c3c; }
            QTreeWidget::item:selected, QAbstractItemView::item:selected { background: #39526a; color: #ffffff; }
            QHeaderView::section { background: #2b2b2b; color: #f0f0f0; border: none; padding: 4px; }
            QScrollArea, QComboBox, QDoubleSpinBox, QSpinBox, QLineEdit { background: #171717; border: 1px solid #3b3b3b; border-radius: 4px; padding: 3px; }
            QToolBar { spacing: 6px; }
            QToolBar, QMenuBar, QStatusBar { background: #202020; border: none; }
            QPushButton, QToolButton, QCheckBox { padding: 4px 8px; }
            QToolButton:checked { background: rgba(88, 139, 191, 0.35); border-radius: 4px; }
            """
        )
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)

        self.temp_ply_dir = os.path.join(tempfile.gettempdir(), "layerloom_ply_cache")
        self._clear_ply_cache()

        self.base_file = None
        self.current_file = None
        self.transformed_preview_file = None
        self.model_objects: List[Tuple[str, str]] = []
        self.assignments: Dict[str, Dict[str, str]] = {}
        self.oid_to_name: Dict[str, str] = {}
        self.name_to_oid: Dict[str, str] = {}
        self._names_in_build_order: List[str] = []
        self._last_picked_oid: Optional[str] = None
        self._pending_source_hex_by_name: Dict[str, str] = {}
        self._last_transform_plan: Optional[TransformPlan] = None
        self._ply_cache_signature = None
        self._selection_origin = None
        self._setting_transform_widgets = False
        self.transform_dirty = False
        self.transform_is_default = True
        self.large_model_mode = False
        self.large_model_reason = ""
        self.large_model_info: Dict[str, Optional[float]] = {}
        self.pending_rotation_matrix = np.eye(3, dtype=np.float64)
        self.applied_rotation_matrix = np.eye(3, dtype=np.float64)
        self.applied_scale = 1.0
        self.viewer_tool_mode = "normal"
        self.current_palette_name = "Normal"
        self.current_palette: List[Dict[str, str]] = []

        self.viewer = EmbeddedPVViewer()
        self._build_ui()
        self._load_palette(self.current_palette_name)
        self._refresh_transform_readout()
        self._refresh_status_summary()
        _log("INFO", "[startup] v58 main window ready")

    # ---------- UI ----------
    def _build_ui(self):
        self._build_menus()
        self._build_toolbar()

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        self.setCentralWidget(splitter)

        self.viewer_host = OverlayHostWidget(self)
        viewer_layout = QtWidgets.QVBoxLayout(self.viewer_host)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        viewer_layout.addWidget(self.viewer.widget())
        self.viewer_empty_hint = QtWidgets.QLabel("To begin, please load a 3MF or GLB file.", self.viewer_host)
        self.viewer_empty_hint.setAlignment(QtCore.Qt.AlignCenter)
        self.viewer_empty_hint.setWordWrap(True)
        self.viewer_empty_hint.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.viewer_empty_hint.setStyleSheet(
            "color: #8b9299; background: rgba(246, 246, 247, 0.84); "
            "border: 1px solid rgba(110, 118, 125, 0.35); border-radius: 10px; padding: 14px 18px;"
        )
        splitter.addWidget(self.viewer_host)

        sidebar = QtWidgets.QFrame()
        sidebar.setObjectName("sidebarFrame")
        sidebar.setMinimumWidth(455)
        sidebar.setMaximumWidth(660)
        splitter.addWidget(sidebar)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 2)

        side_layout = QtWidgets.QVBoxLayout(sidebar)
        side_layout.setContentsMargins(14, 14, 14, 14)
        side_layout.setSpacing(12)
        splitter_right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter_right.setChildrenCollapsible(False)
        side_layout.addWidget(splitter_right, 1)

        parts_box = QtWidgets.QGroupBox("Parts")
        parts_layout = QtWidgets.QVBoxLayout(parts_box)
        parts_layout.setContentsMargins(6, 8, 6, 6)
        self.parts_table = QtWidgets.QTreeWidget()
        self.parts_table.setColumnCount(3)
        self.parts_table.setHeaderLabels(["Part", "Hex", "Pattern"])
        self.parts_table.setAlternatingRowColors(True)
        self.parts_table.setRootIsDecorated(False)
        self.parts_table.setUniformRowHeights(True)
        self.parts_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.parts_table.header().setStretchLastSection(False)
        self.parts_table.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.parts_table.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.parts_table.header().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.parts_table.itemSelectionChanged.connect(self._on_parts_selection_changed)
        parts_layout.addWidget(self.parts_table)
        splitter_right.addWidget(parts_box)

        lower_right = QtWidgets.QWidget()
        lower_layout = QtWidgets.QVBoxLayout(lower_right)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(10)
        splitter_right.addWidget(lower_right)

        selected_box = QtWidgets.QGroupBox("Selected Part")
        selected_layout = QtWidgets.QHBoxLayout(selected_box)
        selected_layout.setContentsMargins(10, 10, 10, 8)
        self.selected_swatch = QtWidgets.QLabel()
        self.selected_swatch.setFixedSize(28, 28)
        self.selected_swatch.setStyleSheet("background: transparent; border: 1px solid #626262; border-radius: 4px;")
        selected_layout.addWidget(self.selected_swatch, 0, QtCore.Qt.AlignTop)
        selected_text_col = QtWidgets.QVBoxLayout()
        self.selected_name_label = QtWidgets.QLabel("No part selected")
        self.selected_name_label.setObjectName("titleValue")
        self.selected_hex_label = QtWidgets.QLabel("Hex: —")
        self.selected_token_label = QtWidgets.QLabel("Pattern: —")
        for lbl in (self.selected_hex_label, self.selected_token_label):
            lbl.setObjectName("muted")
        selected_text_col.addWidget(self.selected_name_label)
        selected_text_col.addWidget(self.selected_hex_label)
        selected_text_col.addWidget(self.selected_token_label)
        selected_layout.addLayout(selected_text_col, 1)
        lower_layout.addWidget(selected_box)

        palette_box = QtWidgets.QGroupBox("Palette")
        palette_layout = QtWidgets.QVBoxLayout(palette_box)
        palette_layout.setContentsMargins(8, 10, 8, 8)
        lower_layout.addWidget(palette_box, 1)

        palette_row = QtWidgets.QHBoxLayout()
        self.palette_combo = QtWidgets.QComboBox()
        self.palette_combo.addItems(list(PALETTE_FILES.keys()))
        self.palette_combo.setCurrentText(self.current_palette_name)
        self.palette_combo.currentTextChanged.connect(self._on_palette_change)
        self.palette_count_label = QtWidgets.QLabel("0 colors")
        self.palette_count_label.setObjectName("muted")
        palette_row.addWidget(self.palette_combo, 1)
        palette_row.addWidget(self.palette_count_label)
        palette_layout.addLayout(palette_row)

        filter_row = QtWidgets.QHBoxLayout()
        filter_row.setSpacing(8)
        self.limit_two_checkbox = QtWidgets.QCheckBox("Two-color only")
        self.limit_two_checkbox.toggled.connect(self._on_two_color_toggle)
        self.add_black_checkbox = QtWidgets.QCheckBox("Add K")
        self.add_black_checkbox.toggled.connect(self._on_bw_toggle)
        self.add_white_checkbox = QtWidgets.QCheckBox("Add W")
        self.add_white_checkbox.toggled.connect(self._on_bw_toggle)
        filter_row.addWidget(self.limit_two_checkbox)
        filter_row.addWidget(self.add_black_checkbox)
        filter_row.addWidget(self.add_white_checkbox)
        filter_row.addStretch(1)
        palette_layout.addLayout(filter_row)

        req_row = QtWidgets.QHBoxLayout()
        req_row.setSpacing(6)
        self.req_buttons: Dict[str, QtWidgets.QToolButton] = {}
        for letter in ("C", "M", "Y", "K", "W"):
            btn = QtWidgets.QToolButton()
            btn.setText(letter)
            btn.setCheckable(True)
            btn.setAutoRaise(False)
            btn.toggled.connect(self._on_req_letters_toggle)
            req_row.addWidget(btn)
            self.req_buttons[letter.lower()] = btn
        req_row.addStretch(1)
        palette_layout.addLayout(req_row)

        self.palette_scroll = QtWidgets.QScrollArea()
        self.palette_scroll.setWidgetResizable(True)
        self.palette_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.palette_inner = QtWidgets.QWidget()
        self.palette_grid = QtWidgets.QGridLayout(self.palette_inner)
        self.palette_grid.setContentsMargins(0, 0, 0, 0)
        self.palette_grid.setHorizontalSpacing(8)
        self.palette_grid.setVerticalSpacing(10)
        self.palette_scroll.setWidget(self.palette_inner)
        palette_layout.addWidget(self.palette_scroll, 1)

        self.rot_x_spin = self._make_rotation_spin()
        self.rot_y_spin = self._make_rotation_spin()
        self.rot_z_spin = self._make_rotation_spin()
        self.scale_spin = QtWidgets.QDoubleSpinBox()
        self.scale_spin.setDecimals(4)
        self.scale_spin.setRange(0.0001, 1000.0)
        self.scale_spin.setValue(1.0)
        self.scale_spin.setSingleStep(0.1)

        self._build_viewer_overlays()

        for spin in (self.rot_x_spin, self.rot_y_spin, self.rot_z_spin, self.scale_spin):
            spin.valueChanged.connect(self._on_transform_value_change)

        self.status_bar = QtWidgets.QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")
        splitter.setSizes([1030, 540])
        splitter_right.setSizes([235, 645])

    def _build_menus(self):
        menu = self.menuBar()
        file_menu = menu.addMenu("File")
        file_menu.addAction("Open 3MF…", self._on_open)
        file_menu.addAction("Open GLB…", self._on_open_glb)
        file_menu.addSeparator()
        file_menu.addAction("Save Labeled 3MF…", self._on_save)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)

        tools_menu = menu.addMenu("Tools")
        tools_menu.addAction("Preview 3D", self._on_preview)
        tools_menu.addAction("Weave", self._on_weave)

        debug_menu = menu.addMenu("Debug")
        debug_menu.addAction("Dump Map", self._dump_assignments)

    def _build_toolbar(self):
        toolbar = QtWidgets.QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setIconSize(QtCore.QSize(16, 16))
        self.addToolBar(toolbar)

        open_action = QtWidgets.QAction("Open 3MF", self)
        open_action.triggered.connect(self._on_open)
        open_glb_action = QtWidgets.QAction("Open GLB", self)
        open_glb_action.triggered.connect(self._on_open_glb)
        save_action = QtWidgets.QAction("Save Labeled 3MF", self)
        save_action.triggered.connect(self._on_save)
        preview_action = QtWidgets.QAction("Preview 3D", self)
        preview_action.triggered.connect(self._on_preview)
        weave_action = QtWidgets.QAction("Weave", self)
        weave_action.triggered.connect(self._on_weave)

        toolbar.addAction(open_action)
        toolbar.addAction(open_glb_action)
        toolbar.addAction(save_action)
        toolbar.addSeparator()
        toolbar.addAction(preview_action)
        toolbar.addAction(weave_action)
        toolbar.addSeparator()

        self.glb_colors_spin = QtWidgets.QSpinBox()
        self.glb_colors_spin.setRange(2, 512)
        self.glb_colors_spin.setValue(20)
        self.glb_colors_spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.glb_colors_spin.setFixedWidth(58)

        self.layer_height_spin = QtWidgets.QDoubleSpinBox()
        self.layer_height_spin.setDecimals(3)
        self.layer_height_spin.setRange(0.01, 10.0)
        self.layer_height_spin.setSingleStep(0.01)
        self.layer_height_spin.setValue(0.2)
        self.layer_height_spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.layer_height_spin.setFixedWidth(72)

        toolbar.addWidget(self._toolbar_label("GLB colors"))
        toolbar.addWidget(self.glb_colors_spin)
        toolbar.addSeparator()
        toolbar.addWidget(self._toolbar_label("Layer height"))
        toolbar.addWidget(self.layer_height_spin)
        toolbar.addSeparator()

        self.toolbar_status_label = QtWidgets.QLabel("No model loaded")
        self.toolbar_status_label.setObjectName("muted")
        toolbar.addWidget(self.toolbar_status_label)
        self.layer_height_spin.valueChanged.connect(self._refresh_layer_height_preview)
        self._refresh_layer_height_preview()

    def _toolbar_label(self, text: str):
        label = QtWidgets.QLabel(text)
        label.setObjectName("muted")
        return label

    def _build_viewer_overlays(self):
        self.viewer_top_overlay = QtWidgets.QFrame(self.viewer_host)
        self.viewer_top_overlay.setObjectName("overlayFrame")
        top_outer = QtWidgets.QVBoxLayout(self.viewer_top_overlay)
        top_outer.setContentsMargins(10, 8, 10, 8)
        top_outer.setSpacing(8)

        action_row = QtWidgets.QHBoxLayout()
        action_row.setSpacing(6)
        self.rotate_tool_btn = QtWidgets.QToolButton(text="Rotate")
        self.rotate_tool_btn.clicked.connect(self._activate_rotate_gizmo)
        self.place_tool_btn = QtWidgets.QToolButton(text="Place on Plate")
        self.place_tool_btn.clicked.connect(self._activate_place_on_plate)
        self.place_on_plate_btn = self.place_tool_btn
        self.cancel_tool_btn = QtWidgets.QToolButton(text="Cancel")
        self.cancel_tool_btn.clicked.connect(lambda: self._set_viewer_tool_mode("normal"))
        self.apply_transform_btn = QtWidgets.QToolButton(text="Apply")
        self.apply_transform_btn.clicked.connect(self._apply_transform_preview)
        self.reset_transform_btn = QtWidgets.QToolButton(text="Reset")
        self.reset_transform_btn.clicked.connect(self._reset_transform_to_default)
        for btn in (
            self.rotate_tool_btn,
            self.place_tool_btn,
            self.cancel_tool_btn,
            self.apply_transform_btn,
            self.reset_transform_btn,
        ):
            action_row.addWidget(btn)
        action_row.addStretch(1)
        top_outer.addLayout(action_row)

        transform_row = QtWidgets.QHBoxLayout()
        transform_row.setSpacing(6)
        for label_text, spin in (
            ("X", self.rot_x_spin),
            ("Y", self.rot_y_spin),
            ("Z", self.rot_z_spin),
            ("Scale", self.scale_spin),
        ):
            lbl = QtWidgets.QLabel(label_text)
            lbl.setObjectName("muted")
            transform_row.addWidget(lbl)
            spin.setFixedWidth(72 if label_text != "Scale" else 84)
            transform_row.addWidget(spin)
        transform_row.addStretch(1)
        top_outer.addLayout(transform_row)

        self.transform_info_label = QtWidgets.QLabel("Open a 3MF or GLB to begin.")
        self.transform_info_label.setWordWrap(True)
        self.transform_info_label.setObjectName("muted")
        top_outer.addWidget(self.transform_info_label)

        self.camera_overlay = QtWidgets.QFrame(self.viewer_host)
        self.camera_overlay.setObjectName("overlayFrame")
        cam_layout = QtWidgets.QHBoxLayout(self.camera_overlay)
        cam_layout.setContentsMargins(8, 6, 8, 6)
        cam_layout.setSpacing(4)
        for label, callback in (
            ("Top", lambda: self._set_camera_view("top")),
            ("Bottom", lambda: self._set_camera_view("bottom")),
            ("Front", lambda: self._set_camera_view("front")),
            ("Back", lambda: self._set_camera_view("back")),
            ("Left", lambda: self._set_camera_view("left")),
            ("Right", lambda: self._set_camera_view("right")),
            ("Iso", lambda: self._set_camera_view("iso")),
        ):
            btn = QtWidgets.QToolButton()
            btn.setText(label)
            btn.clicked.connect(callback)
            cam_layout.addWidget(btn)
        self.camera_overlay.adjustSize()
        self.viewer_top_overlay.adjustSize()
        self._position_viewer_overlays()

    def _position_viewer_overlays(self):
        if not hasattr(self, "camera_overlay"):
            return
        margin = 14
        host_rect = self.viewer_host.rect()
        self.viewer_top_overlay.adjustSize()
        self.camera_overlay.adjustSize()
        max_w = max(340, host_rect.width() - (margin * 3) - self.camera_overlay.width())
        self.viewer_top_overlay.setMaximumWidth(max_w)
        self.viewer_top_overlay.resize(min(max_w, self.viewer_top_overlay.sizeHint().width()), self.viewer_top_overlay.sizeHint().height())
        self.viewer_top_overlay.move(margin, margin)
        self.camera_overlay.move(
            max(margin, host_rect.width() - self.camera_overlay.width() - margin),
            margin,
        )
        if hasattr(self, "viewer_empty_hint"):
            max_w = min(520, max(260, host_rect.width() - 140))
            self.viewer_empty_hint.setMaximumWidth(max_w)
            self.viewer_empty_hint.adjustSize()
            hint_w = min(max_w, self.viewer_empty_hint.sizeHint().width())
            hint_h = self.viewer_empty_hint.sizeHint().height()
            self.viewer_empty_hint.resize(hint_w, hint_h)
            self.viewer_empty_hint.move(
                max(20, int((host_rect.width() - hint_w) * 0.5)),
                max(20, int((host_rect.height() - hint_h) * 0.5)),
            )

    def _set_viewer_hint(self, text: Optional[str]):
        if not hasattr(self, "viewer_empty_hint"):
            return
        if text:
            self.viewer_empty_hint.setText(text)
            self.viewer_empty_hint.show()
        else:
            self.viewer_empty_hint.hide()
        self._position_viewer_overlays()
    
    def _refresh_layer_height_preview(self):
        # The build-plate reference is now static; the spinbox still controls weave/export.
        return

    def _make_rotation_spin(self):
        spin = QtWidgets.QDoubleSpinBox()
        spin.setDecimals(3)
        spin.setRange(-9999.0, 9999.0)
        spin.setSingleStep(5.0)
        return spin

    # ---------- dialog helpers ----------
    def _warn(self, title: str, text: str):
        QtWidgets.QMessageBox.warning(self, title, text)

    def _error(self, title: str, text: str):
        QtWidgets.QMessageBox.critical(self, title, text)

    def _info(self, title: str, text: str):
        QtWidgets.QMessageBox.information(self, title, text)

    def _confirm(self, title: str, text: str) -> bool:
        return QtWidgets.QMessageBox.question(self, title, text) == QtWidgets.QMessageBox.Yes

    # ---------- palette logic ----------
    def _on_palette_change(self, value: str):
        self._load_palette(value)

    def _on_two_color_toggle(self, _checked: bool):
        self._load_palette(self.current_palette_name)

    def _on_bw_toggle(self, _checked: bool):
        self._load_palette(self.current_palette_name)

    def _required_letters(self) -> set:
        return {letter for letter, btn in self.req_buttons.items() if btn.isChecked()}

    def _load_palette(self, name: str):
        path = PALETTE_FILES.get(name) or "dummy"
        entries = _load_palette_file(path, limit_two=self.limit_two_checkbox.isChecked())
        entries = _filter_by_bw_visibility(
            entries,
            add_k=self.add_black_checkbox.isChecked(),
            add_w=self.add_white_checkbox.isChecked(),
        )
        entries = self._filter_by_required_letters(entries, self._required_letters())
        self.current_palette_name = name
        self.current_palette = entries
        self._render_palette_grid()
        self._refresh_status_summary()

    def _render_palette_grid(self):
        while self.palette_grid.count():
            item = self.palette_grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.palette_count_label.setText(f"{len(self.current_palette)} colors")
        if not self.current_palette:
            empty = QtWidgets.QLabel("No palette entries match these filters.")
            empty.setObjectName("muted")
            self.palette_grid.addWidget(empty, 0, 0)
            return
        cols = 6
        for idx, entry in enumerate(self.current_palette):
            btn = PaletteSwatchButton(entry)
            btn.clicked.connect(lambda _checked=False, e=entry: self._assign_selected(e))
            row, col = divmod(idx, cols)
            self.palette_grid.addWidget(btn, row, col)
        self.palette_grid.setRowStretch((len(self.current_palette) // cols) + 1, 1)

    def _filter_by_required_letters(self, entries, required: set):
        if not required:
            return entries
        return [e for e in entries if all(ch in (e.get("token", "").lower()) for ch in required)]

    def _on_req_letters_toggle(self, _checked: bool):
        self._load_palette(self.current_palette_name)

    # ---------- backend logic ----------
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

    def _exact_palette_entry_for_token(self, token: str) -> Optional[Dict[str, str]]:
        tok = str(token or "").strip().lower()
        if not tok:
            return None
        path = PALETTE_FILES.get(self.current_palette_name)
        if not path:
            return None
        try:
            all_entries = _load_palette_file(path, limit_two=False)
        except Exception:
            return None
        for entry in all_entries:
            if str(entry.get("token", "")).strip().lower() == tok:
                return entry
        return None

    def _item_for_oid(self, oid: str):
        matches = self.parts_table.findItems("", QtCore.Qt.MatchContains)
        for i in range(self.parts_table.topLevelItemCount()):
            item = self.parts_table.topLevelItem(i)
            if item.data(0, QtCore.Qt.UserRole) == oid:
                return item
        return None

    def _set_assignment_for_oid(self, oid: str, entry: Dict[str, str], source_hex: Optional[str] = None):
        data = {"token": entry["token"], "hex": entry["hex"]}
        if source_hex:
            data["source_hex"] = source_hex
        self.assignments[oid] = data
        item = self._item_for_oid(oid)
        if item is not None:
            item.setText(1, entry["hex"])
            item.setText(2, entry["token"])
        if self._current_selected_oid() == oid:
            self._update_selected_part_summary(oid)

    def _apply_source_metadata_assignments(
        self,
        source_hex_by_oid: Dict[str, str],
        stack_token_by_oid: Optional[Dict[str, str]] = None,
    ) -> int:
        if not self.current_palette and not stack_token_by_oid:
            _log("WARN", "No palette entries available for source-color matching.")
            return 0
        matched = 0
        exact = 0
        for oid, name in self.model_objects:
            stack_token = (stack_token_by_oid or {}).get(oid)
            if not stack_token:
                stack_token = _extract_pat(name)
            if stack_token:
                exact_entry = self._exact_palette_entry_for_token(stack_token)
                if exact_entry:
                    source_hex = source_hex_by_oid.get(oid) or self._pending_source_hex_by_name.get(_normalize_for_match(name))
                    self._set_assignment_for_oid(oid, exact_entry, source_hex=source_hex)
                    matched += 1
                    exact += 1
                    continue
            inferred_token = _extract_pattern_token_from_name(
                getattr(self, "oid_to_import_name", {}).get(oid)
                or getattr(self, "oid_to_display_name", {}).get(oid)
                or name
            )
            if inferred_token:
                exact_entry = self._exact_palette_entry_for_token(inferred_token)
                if exact_entry:
                    source_hex = source_hex_by_oid.get(oid) or self._pending_source_hex_by_name.get(_normalize_for_match(name))
                    self._set_assignment_for_oid(oid, exact_entry, source_hex=source_hex)
                    matched += 1
                    exact += 1
                    continue
            source_hex = source_hex_by_oid.get(oid) or self._pending_source_hex_by_name.get(_normalize_for_match(name))
            if not source_hex:
                continue
            entry = self._nearest_palette_entry(source_hex)
            if not entry:
                continue
            self._set_assignment_for_oid(oid, entry, source_hex=source_hex)
            matched += 1
        self._pending_source_hex_by_name = {}
        if matched:
            if exact:
                _log(
                    "INFO",
                    f"Auto-matched {matched} part(s) from source metadata using palette '{self.current_palette_name}' "
                    f"({exact} exact token, {matched - exact} nearest-color).",
                )
            else:
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
            return "Rotate"
        if mode == "surface":
            return "Place on Plate"
        return "Normal"

    def _set_transform_values(self, rot_x: float, rot_y: float, rot_z: float, scale: float):
        self._setting_transform_widgets = True
        try:
            self.rot_x_spin.setValue(float(rot_x))
            self.rot_y_spin.setValue(float(rot_y))
            self.rot_z_spin.setValue(float(rot_z))
            self.scale_spin.setValue(float(scale))
        finally:
            self._setting_transform_widgets = False
        self._on_transform_value_change()

    def _sync_rotation_fields_from_matrix(self, rotation: np.ndarray):
        rot_x, rot_y, rot_z = rotation_matrix_to_euler_xyz(rotation)
        self._setting_transform_widgets = True
        try:
            self.rot_x_spin.setValue(float(rot_x))
            self.rot_y_spin.setValue(float(rot_y))
            self.rot_z_spin.setValue(float(rot_z))
        finally:
            self._setting_transform_widgets = False

    def _get_transform_values(self, *, raise_on_error: bool = False) -> Optional[Tuple[float, float, float, float]]:
        try:
            values = (
                float(self.rot_x_spin.value()),
                float(self.rot_y_spin.value()),
                float(self.rot_z_spin.value()),
                float(self.scale_spin.value()),
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

    def _refresh_transform_readout(self, plan: Optional[TransformPlan] = None):
        if not self.base_file:
            self.transform_info_label.setText("Open a 3MF or GLB to begin.")
            self.transform_info_label.setStyleSheet("color: #a8a8a8;")
            self._update_transform_ui_state()
            self._refresh_status_summary()
            return

        values = self._get_transform_values()
        if values is None:
            self.transform_info_label.setText("Transform values invalid. Rotations must be numeric and Scale must be > 0.")
            self.transform_info_label.setStyleSheet("color: #ffb347;")
            self._update_transform_ui_state()
            self._refresh_status_summary()
            return

        plan = plan or self._last_transform_plan
        if plan is None:
            text = f"Plate {PLATE_WIDTH_MM:.0f} × {PLATE_DEPTH_MM:.0f} mm | transform ready to apply"
            if self.large_model_mode:
                text += f" | safety mode ({self.large_model_reason})"
            self.transform_info_label.setText(text)
            self.transform_info_label.setStyleSheet(f"color: {'#ffb347' if self.large_model_mode else '#9fd3a8'};")
            self._update_transform_ui_state()
            self._refresh_status_summary()
            return

        dims = plan.transformed_bounds.size
        fits = dims[0] <= PLATE_WIDTH_MM + 1e-6 and dims[1] <= PLATE_DEPTH_MM + 1e-6
        status = "fits plate" if fits else "warning: footprint exceeds plate"
        text = f"Size {dims[0]:.1f} × {dims[1]:.1f} × {dims[2]:.1f} mm | {status}"
        if self.viewer_tool_mode == "surface":
            text += " | click a highlighted convex-hull face to place on plate"
        elif self.viewer_tool_mode == "gizmo":
            text += " | drag the colored rotation rings in the viewer"
        if self.transform_dirty:
            text += " | preview pending apply"
        if self.large_model_mode:
            text += f" | safety mode ({self.large_model_reason})"
        self.transform_info_label.setText(text)
        self.transform_info_label.setStyleSheet(
            f"color: {'#9fd3a8' if fits and not self.transform_dirty and not self.large_model_mode else '#ffb347'};"
        )
        self._update_transform_ui_state()
        self._refresh_status_summary()

    def _update_transform_ui_state(self):
        values = self._get_transform_values()
        can_apply = bool(self.base_file) and not self.large_model_mode and values is not None and self.transform_dirty
        can_reset = bool(self.base_file) and (values is None or self.transform_dirty or not self.transform_is_default)
        self.apply_transform_btn.setEnabled(can_apply)
        self.reset_transform_btn.setEnabled(can_reset)
        place_enabled = bool(self.base_file) and not self.large_model_mode and values is not None
        self.place_on_plate_btn.setEnabled(place_enabled)
        self.rotate_tool_btn.setEnabled(place_enabled)
        self.place_tool_btn.setEnabled(place_enabled)
        self.cancel_tool_btn.setEnabled(self.viewer_tool_mode != "normal")

    def _on_transform_value_change(self, *_args):
        if self._setting_transform_widgets:
            return
        values = self._get_transform_values()
        if values is not None:
            self.pending_rotation_matrix = rotation_matrix_xyz(values[0], values[1], values[2])
        self._update_transform_flags()
        self._refresh_layer_height_preview()
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

    def _build_assignment_metadata_updates(self) -> Dict[str, Dict[str, str]]:
        out: Dict[str, Dict[str, str]] = {}
        for oid, info in self.assignments.items():
            token = info.get("token")
            if not token:
                continue
            metadata = {"stack_token": token}
            source_hex = info.get("source_hex") or info.get("hex")
            if source_hex:
                metadata["source_hex"] = source_hex
            out[oid] = metadata
        return out

    def _prepare_for_heavy_preview_build(self):
        if self.viewer:
            try:
                self.viewer._teardown_scene()
            except Exception as e:
                _log("WARN", f"[preview] viewer teardown failed before rebuild: {e}")
        self._clear_ply_cache()
        gc.collect()

    def _resolve_oid_for_preview_node(self, node_name: str, geom_name: Optional[str] = None) -> Optional[str]:
        candidates: List[str] = []
        for raw in (node_name, geom_name):
            if raw:
                candidates.append(str(raw))
        for raw in candidates:
            oid = _oid_from_labeled_name(raw)
            if oid:
                return oid
        for raw in candidates:
            oid = self.name_to_oid.get(raw)
            if oid:
                return oid
        norm_to_oid = {_normalize_for_match(name): oid for oid, name in self.oid_to_name.items()}
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
                if candidate_norm and norm.startswith(candidate_norm):
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
            metadata_updates=self._build_assignment_metadata_updates() if include_assignments else None,
        )
        _perf_log("transform write", t_write, extra=os.path.basename(output_path))
        return plan

    def _refresh_preview_from_current_file(self, *, force: bool = False):
        if not self.current_file or (self.large_model_mode and not force):
            return
        self._export_ply_cache()
        self._build_or_rebuild_viewer()
        self._tint_viewer_from_assignments()
        gc.collect()

    def _apply_rotation_delta(self, delta_rotation: np.ndarray):
        current = orthonormalize_rotation(self.pending_rotation_matrix)
        new_rotation = orthonormalize_rotation(np.asarray(delta_rotation, dtype=np.float64) @ current)
        self.pending_rotation_matrix = new_rotation
        self._sync_rotation_fields_from_matrix(new_rotation)
        self._update_transform_flags()
        self._refresh_transform_readout()
        self._apply_transform_preview(quiet=False)

    def _on_surface_rotation(self, delta_rotation: np.ndarray):
        self._set_viewer_tool_mode("normal")
        self._apply_rotation_delta(delta_rotation)

    def _sync_viewer_tool_mode(self):
        if not self.viewer or not self._last_transform_plan:
            self._refresh_status_summary()
            self._update_transform_ui_state()
            return
        dims = self._last_transform_plan.transformed_bounds.size
        radius = max(float(max(dims)) * 0.72, 5.0)
        self.viewer.set_callbacks(
            on_rotation_commit=self._apply_rotation_delta,
            on_surface_commit=self._on_surface_rotation,
        )
        self.viewer.set_tool_mode(
            self.viewer_tool_mode,
            center=self._last_transform_plan.transformed_bounds.center,
            radius=radius,
        )
        self._update_transform_ui_state()
        self._refresh_status_summary()
        self._refresh_transform_readout(self._last_transform_plan)

    def _set_viewer_tool_mode(self, mode: str):
        self.viewer_tool_mode = mode
        self._sync_viewer_tool_mode()

    def _activate_rotate_gizmo(self):
        if not self.base_file:
            self._warn("Rotate", "Open a .3mf first.")
            return
        if self.large_model_mode:
            self._warn("Rotate", f"Rotate is disabled in large-file safety mode.\n\n{self.large_model_reason}")
            return
        if not self._ensure_transform_preview_current(quiet=False):
            return
        self._set_viewer_tool_mode("gizmo")

    def _activate_place_on_plate(self):
        if not self.base_file:
            self._warn("Place on Plate", "Open a .3mf first.")
            return
        if self.large_model_mode:
            self._warn("Place on Plate", f"Place on Plate is disabled in large-file safety mode.\n\n{self.large_model_reason}")
            return
        if not self._ensure_transform_preview_current(quiet=False):
            return
        self._set_viewer_tool_mode("surface")

    def _apply_transform_preview(self, quiet: bool = False) -> bool:
        if not self.base_file:
            if not quiet:
                self._warn("Transform", "Open a .3mf first.")
            return False
        if self.large_model_mode:
            msg = f"Preview materialization is disabled in large-file safety mode.\n\n{self.large_model_reason}"
            if quiet:
                _log("WARN", msg)
            else:
                self._warn("Transform", msg)
            return False

        preview_path = self._preview_temp_path("transform_preview")
        try:
            plan = self._materialize_current_model(preview_path, include_assignments=False)
        except Exception as e:
            if quiet:
                _log("WARN", f"Transform preview failed: {e}")
            else:
                self._error("Transform", f"Failed to apply transform:\n{e}")
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
        if not self.base_file or self.large_model_mode:
            return True
        if self.transform_dirty or not self.transformed_preview_file or self.current_file != self.transformed_preview_file:
            return self._apply_transform_preview(quiet=quiet)
        return True

    def _reset_transform_to_default(self):
        if not self.base_file:
            return
        self.pending_rotation_matrix = np.eye(3, dtype=np.float64)
        self._set_transform_values(0.0, 0.0, 0.0, 1.0)
        if self.large_model_mode:
            self.applied_rotation_matrix = np.eye(3, dtype=np.float64)
            self.applied_scale = 1.0
            self._update_transform_flags()
            self._refresh_transform_readout()
            return
        self._apply_transform_preview(quiet=False)

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
        self._set_transform_values(0.0, 0.0, 0.0, 1.0)
        self._reload_current()

    # ---------- file ops ----------
    def _on_open(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open 3MF", "", "3MF Files (*.3mf)")
        if not path:
            return
        self._pending_source_hex_by_name = {}
        stamped = _stamp_ids_only(path)
        self._load_canonical_model(stamped)
        _log("INFO", f"Opened: {path}")
        if stamped != path:
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

    def _run_glb_import_pipeline(self, glb_path: str):
        if not os.path.isfile(GLB_SPLIT_SCRIPT):
            raise RuntimeError(f"GLB split script not found:\n{GLB_SPLIT_SCRIPT}")
        t0 = time.perf_counter()
        target_colors = int(self.glb_colors_spin.value())
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
            repair_cmd = [sys.executable, GLB_REPAIR_SCRIPT, split_3mf, "--out", repaired_3mf, "--quiet"]
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
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open GLB/GLTF", "", "GLB/GLTF Files (*.glb *.gltf)")
        if not path:
            return
        try:
            import_3mf, manifest_map, repair_warning, color_levels = self._run_glb_import_pipeline(path)
        except Exception as e:
            self._error("Open GLB", f"Failed to import GLB:\n{e}")
            return
        self._pending_source_hex_by_name = manifest_map
        stamped = _stamp_ids_only(import_3mf)
        self._load_canonical_model(stamped)
        _log("INFO", f"Opened GLB: {path}")
        _log("INFO", f"GLB import produced 3MF: {import_3mf} (target_colors={self.glb_colors_spin.value()}, color_levels={color_levels})")
        if repair_warning:
            _log("WARN", f"[glb-import] {repair_warning}")
            if not self.large_model_mode:
                self.status_bar.showMessage("GLB imported; repair warning logged.", 5000)
        elif not self.large_model_mode:
            self.status_bar.showMessage(f"GLB imported and auto-matched using palette '{self.current_palette_name}'.", 5000)

    def _clear_ply_cache(self):
        try:
            if os.path.isdir(self.temp_ply_dir):
                shutil.rmtree(self.temp_ply_dir)
            os.makedirs(self.temp_ply_dir, exist_ok=True)
            self._ply_cache_signature = None
        except Exception as e:
            _log("ERROR", f"Failed to clear PLY cache: {e}")

    def _export_ply_cache(self):
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
            t_load = time.perf_counter()
            scene = trimesh.load_scene(self.current_file, process=False)
            _perf_log("preview scene load", t_load, extra=os.path.basename(self.current_file))
        except Exception as e:
            _log("ERROR", f"trimesh.load_scene failed: {e}")
            self._error("Preview Cache", f"trimesh failed to load the 3MF scene: {e}")
            return

        if isinstance(scene, trimesh.Trimesh):
            scene = trimesh.Scene(scene)

        exported_count = 0
        export_time = 0.0
        for node_name in scene.graph.nodes_geometry:
            try:
                transform, geom_name = scene.graph[node_name]
                mesh = scene.geometry[geom_name].copy()
                mesh.apply_transform(transform)
                oid = self._resolve_oid_for_preview_node(str(node_name), str(geom_name))
                if not oid:
                    _log("WARN", f"Could not map preview node '{node_name}' to an OID. Skipping export.")
                    continue
                correct_name = self.oid_to_name.get(oid)
                if not correct_name:
                    _log("WARN", f"Mapped preview node '{node_name}' to OID '{oid}', but oid_to_name has no entry. Skipping.")
                    continue
                safe_name = re.sub(r'[\\/*?:"<>|]', "_", correct_name)
                ply_path = os.path.join(self.temp_ply_dir, f"{safe_name}.ply")
                t_export = time.perf_counter()
                mesh.export(ply_path)
                export_time += time.perf_counter() - t_export
                exported_count += 1
                del mesh
            except Exception as e:
                _log("ERROR", f"Failed to export mesh node '{node_name}': {e}")
        scene = None
        gc.collect()
        _log("INFO", f"PLY cache export complete. Exported {exported_count} files.")
        if exported_count > 0:
            self._ply_cache_signature = cache_sig
        _perf_log("preview cache export", total_t0, extra=f"ply_files={exported_count} export_only={export_time:.3f}s")
        if exported_count == 0 and len(self._names_in_build_order) > 0:
            self._error("Preview Cache", "Failed to export any parts to the PLY cache. The 3D preview will be empty.")

    def _reload_current(self):
        source_file = self.base_file or self.current_file
        if not source_file:
            return
        t_reload = time.perf_counter()
        previous_sel = self._current_selected_oid() or self._last_picked_oid
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
            self._error("Open 3MF", str(e))
            return

        self.assignments = {}
        self._last_picked_oid = None
        if self.large_model_mode:
            self.oid_to_name = dict(summary["oid_to_name"])
            source_hex_by_oid = dict(summary["source_hex_by_oid"])
            stack_token_by_oid = dict(summary.get("stack_token_by_oid", {}))
            import_name_by_oid = dict(summary.get("import_name_by_oid", {}))
            ordered = list(summary["ordered"])
            real_oids = set(summary["real_oids"])
            model_objects = list(summary["model_objects"])
        else:
            self.oid_to_name = _oid_to_name_map(root)
            source_hex_by_oid = _oid_to_source_hex_map(root)
            stack_token_by_oid = _oid_to_stack_token_map(root)
            import_name_by_oid = _oid_to_import_build_label_map(root)
            ordered = _build_items_order(root)
            real_oids = _mesh_oid_set(root)
            model_objects = []
            for o in _all_model_objects(root):
                oid = o.get("id")
                if not oid or oid not in real_oids:
                    continue
                model_objects.append((oid, o.get("name") or f"object_{oid}"))
        self.oid_to_import_name = dict(import_name_by_oid)
        self.oid_to_display_name = _build_preferred_display_name_map(model_objects, self.oid_to_name, import_name_by_oid)
        self.name_to_oid = {v: k for k, v in self.oid_to_name.items()}

        self.parts_table.clear()
        self.parts_table.setHeaderLabels(["Part", "Hex", "Pattern"])
        self.model_objects = []
        for oid, raw_name in model_objects:
            item = QtWidgets.QTreeWidgetItem([self.oid_to_display_name.get(oid, _clean_part_display_name(raw_name)), "", ""])
            item.setData(0, QtCore.Qt.UserRole, oid)
            self.parts_table.addTopLevelItem(item)
            self.model_objects.append((oid, raw_name))

        viewer_names = [self.oid_to_name.get(oid, f"object_{oid}") for oid in ordered if oid in real_oids]
        all_real_names = [n for _, n in self.model_objects]
        seen = set(viewer_names)
        viewer_names += [n for n in all_real_names if n not in seen]
        self._names_in_build_order = viewer_names

        _log(
            "INFO",
            f"[xml] total objects: {len(self.oid_to_name)}; with mesh: {len(real_oids)}; "
            f"build items: {len(ordered)}; viewer names: {len(self._names_in_build_order)}",
        )

        for oid, name in self.model_objects:
            entry = _grouped_color_assignment_for_name(name)
            if entry:
                self._set_assignment_for_oid(oid, entry)
        self._apply_source_metadata_assignments(source_hex_by_oid, stack_token_by_oid)

        if previous_sel:
            self._select_part_by_oid(previous_sel, flash=False)

        if self.large_model_mode:
            self.current_file = source_file
            self.transformed_preview_file = None
            self.viewer_tool_mode = "normal"
            if self.viewer:
                try:
                    self.viewer._teardown_scene()
                except Exception as e:
                    _log("WARN", f"[large-model] viewer teardown failed: {e}")
            pkg_mb = _bytes_to_mb(self.large_model_info.get("package_bytes"))
            model_mb = _bytes_to_mb(self.large_model_info.get("model_bytes"))
            self.transform_info_label.setText(
                f"Large-file safety mode active | package {pkg_mb:.1f} MB | model XML {model_mb:.1f} MB | 3D preview skipped"
            )
            self.transform_info_label.setStyleSheet("color: #ffb347;")
            self._set_viewer_hint("3D preview skipped in large-file safety mode.")
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
        self._refresh_status_summary()
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
            self._warn("Save", "Open a .3mf first.")
            return
        if not self._ensure_transform_preview_current(quiet=False):
            return
        out, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save Labeled 3MF", "", "3MF files (*.3mf)")
        if not out:
            return
        try:
            plan = self._materialize_current_model(out, include_assignments=True)
            self._last_transform_plan = plan
            self.transform_dirty = False
            self._refresh_transform_readout(plan)
            _log("INFO", f"Saved labeled 3MF: {out}")
            self.status_bar.showMessage(f"Saved labeled 3MF: {os.path.basename(out)}", 5000)
        except Exception as e:
            self._error("Save", f"Failed to write 3MF:\n{e}")

    def _run_weave_subprocess(self, input_3mf: str, *, step: float, output_path: str) -> Optional[str]:
        try:
            cmd = [sys.executable, "-m", "layerloom.weave", "-i", input_3mf, "-o", output_path]
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
                self._error("Weave Error", proc.stderr or "Weave failed.")
                return None
            if os.path.exists(output_path):
                return output_path
            for line in reversed((proc.stdout or "").splitlines()):
                if "→" in line and line.strip().endswith(".3mf"):
                    alt = line.split("→")[-1].strip()
                    if os.path.exists(alt):
                        return alt
            self._info("Weave", "Weave completed, but output not found.")
            return None
        except Exception as e:
            self._error("Weave", f"Weave failed: {e}")
            return None

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
                _log("INFO", f"OID {obj.get('id'):>4}  NAME {obj.get('name', '')}")
        except Exception as e:
            _log("WARN", f"{title} failed: {e}")

    def _postweave_pat_audit(self, path: str, title="[post-weave PAT check]"):
        self._preweave_pat_audit(path, title=title)

    def _on_weave(self):
        if not self.base_file:
            self._warn("Weave", "Open a .3mf first.")
            return
        if not self._ensure_transform_preview_current(quiet=False):
            return
        out_dir = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose output folder for woven 3MF")
        if not out_dir:
            return
        base = os.path.splitext(os.path.basename(self.base_file))[0]
        base = re.sub(r"^(preview_id_|tmp_weave_)+", "", base)
        final_woven_path = os.path.join(out_dir, f"{base}_woven.3mf")
        if os.path.exists(final_woven_path):
            if not self._confirm("Overwrite?", f"File already exists:\n{final_woven_path}\n\nOverwrite it?"):
                return
        try:
            tmp_out = os.path.join(tempfile.gettempdir(), f"tmp_weave_{base}.3mf")
            plan = self._materialize_current_model(tmp_out, include_assignments=True)
            self._last_transform_plan = plan
            self.transform_dirty = False
            self._refresh_transform_readout(plan)
            _log("INFO", f"[weave] temp labeled 3MF → {tmp_out}")
        except Exception as e:
            self._error("Weave", f"Failed to write temp labeled 3MF:\n{e}")
            return
        self._preweave_pat_audit(tmp_out, "[pre-weave PAT check]")
        woven = self._run_weave_subprocess(tmp_out, step=self.layer_height_spin.value(), output_path=final_woven_path)
        if not (woven and os.path.exists(woven)):
            return
        is_large_woven, woven_reason, _woven_info = _large_3mf_reason(woven)
        if is_large_woven:
            _log("WARN", f"[weave] large woven file entering auto-preview safe mode: {woven_reason}")
            self.transform_info_label.setText(f"Woven model written; rebuilding preview in safety mode ({woven_reason}).")
            self.transform_info_label.setStyleSheet("color: #ffb347;")
            self._prepare_for_heavy_preview_build()
            self._pending_source_hex_by_name = {}
            stamped_woven = _stamp_ids_only(woven)
            self._load_canonical_model(stamped_woven)
            self._refresh_preview_from_current_file(force=True)
            _log("INFO", f"[weave] woven model loaded in large-file safety mode → {woven}")
            self.status_bar.showMessage(f"Weave complete in safety mode: {os.path.basename(woven)}", 5000)
            return
        self._postweave_pat_audit(woven, "[post-weave PAT check]")
        stamped_woven = _stamp_ids_only(woven)
        if stamped_woven != woven:
            _log("INFO", f"Previewing ID-stamped *woven* temp copy: {stamped_woven}")
        self._load_canonical_model(stamped_woven)
        _log("INFO", f"[weave] woven model loaded → {woven}")
        self.status_bar.showMessage(f"Weave complete: {os.path.basename(woven)}", 5000)

    def _on_preview(self):
        if not (self.base_file or self.current_file) or not (_TRIMESH_OK and _PYVISTA_OK):
            return
        if self.large_model_mode:
            _log("WARN", f"[large-model] manual preview requested: {self.large_model_reason}")
            self.transform_info_label.setText(
                f"Large-file safety mode active | building manual preview on demand ({self.large_model_reason})"
            )
            self.transform_info_label.setStyleSheet("color: #ffb347;")
            self._prepare_for_heavy_preview_build()
            self._refresh_preview_from_current_file(force=True)
            return
        if not self._ensure_transform_preview_current(quiet=False):
            return
        self._build_or_rebuild_viewer()

    # ---------- viewer + selection ----------
    def _set_camera_view(self, which: str):
        plotter = self.viewer.plotter
        try:
            if which == "top":
                plotter.view_xy()
            elif which == "bottom":
                plotter.view_xy(negative=True)
            elif which == "front":
                plotter.view_xz()
            elif which == "back":
                plotter.view_xz(negative=True)
            elif which == "left":
                plotter.view_yz(negative=True)
            elif which == "right":
                plotter.view_yz()
            else:
                plotter.view_isometric()
            plotter.render()
        except Exception as e:
            _log("WARN", f"[viewer] camera preset '{which}' failed: {e}")

    def _flash_actor_by_name(self, name: str, oid: str = None):
        if not self.viewer or not name:
            return
        original_hex = "#d3d3d3"
        if oid:
            entry = self.assignments.get(oid)
            if entry and entry.get("hex"):
                original_hex = entry.get("hex")
        flash_hex = "#FF0000" if original_hex.upper() != "#FF0000" else "#00FF00"
        self.viewer.set_color_by_name(name, flash_hex)

        def _restore():
            if not self.viewer:
                return
            if oid and self._current_selected_oid() != oid:
                return
            self.viewer.set_color_by_name(name, original_hex)

        QtCore.QTimer.singleShot(250, _restore)

    def _build_or_rebuild_viewer(self):
        def _picked(name):
            self._handle_viewer_pick_event(str(name))
        try:
            self._last_picked_oid = None
            self.viewer.build_scene(self.current_file, self._names_in_build_order, on_pick=_picked)
            self._sync_viewer_tool_mode()
            self._set_viewer_hint(None)
        except Exception as e:
            self._error("Preview", str(e))

    def _handle_viewer_pick_event(self, name: str):
        _log("DEBUG", f"3D Pick Event: PyVista actor name='{name}'")
        oid = _oid_from_labeled_name(name) or self.name_to_oid.get(name)
        if oid:
            self._last_picked_oid = oid
            self._select_part_by_oid(oid, flash=True, origin="table")
        else:
            _log("WARN", f"3D Pick -> FAILED to map '{name}' to an OID.")

    def _current_selected_oid(self) -> Optional[str]:
        item = self.parts_table.currentItem()
        if not item:
            return None
        return item.data(0, QtCore.Qt.UserRole)

    def _select_part_by_oid(self, oid: str, *, flash: bool, origin: str = "table"):
        item = self._item_for_oid(oid)
        if item is None:
            return
        self._selection_origin = origin
        try:
            self.parts_table.setCurrentItem(item)
        finally:
            self._selection_origin = None
        self.parts_table.scrollToItem(item)
        self._last_picked_oid = oid
        self._update_selected_part_summary(oid)
        if flash and self.viewer:
            raw_name = self.oid_to_name.get(oid)
            if raw_name:
                self._flash_actor_by_name(raw_name, oid)

    def _on_parts_selection_changed(self):
        oid = self._current_selected_oid()
        if not oid:
            self._update_selected_part_summary(None)
            return
        self._last_picked_oid = oid
        self._update_selected_part_summary(oid)
        if self._selection_origin == "viewer":
            return
        raw_name = self.oid_to_name.get(oid)
        if raw_name and self.viewer:
            self._flash_actor_by_name(raw_name, oid)

    def _update_selected_part_summary(self, oid: Optional[str]):
        if not oid or oid not in self.oid_to_name:
            self.selected_name_label.setText("No part selected")
            self.selected_hex_label.setText("Hex: —")
            self.selected_token_label.setText("Pattern: —")
            self.selected_swatch.setStyleSheet("background: transparent; border: 1px solid #626262; border-radius: 4px;")
            return
        raw_name = self.oid_to_name.get(oid, "")
        info = self.assignments.get(oid, {})
        display_name = getattr(self, "oid_to_display_name", {}).get(oid, _clean_part_display_name(raw_name))
        self.selected_name_label.setText(display_name)
        self.selected_hex_label.setText(f"Hex: {info.get('hex', '—')}")
        self.selected_token_label.setText(f"Pattern: {info.get('token', '—')}")
        hex_color = info.get("hex")
        if hex_color:
            self.selected_swatch.setStyleSheet(
                f"background: {hex_color}; border: 1px solid #626262; border-radius: 4px;"
            )
        else:
            self.selected_swatch.setStyleSheet("background: transparent; border: 1px solid #626262; border-radius: 4px;")

    def _assign_selected(self, entry: Dict[str, str]):
        oid = self._current_selected_oid() or self._last_picked_oid
        if not oid:
            _log("WARN", "No OID selected (neither in table nor via 3D pick). Assignment aborted.")
            return
        self.assignments[oid] = dict(entry)
        item = self._item_for_oid(oid)
        if item is not None:
            item.setText(1, entry["hex"])
            item.setText(2, entry["token"])
        nm = self.oid_to_name.get(oid)
        _log("INFO", f"Assigned Part: {nm} -> {entry['hex']} ({entry['token']})")
        self._update_selected_part_summary(oid)
        if self.viewer and nm:
            self.viewer.set_color_by_name(nm, entry["hex"])

    # ---------- summary / lifecycle ----------
    def _refresh_status_summary(self):
        model_text = _clean_model_display_name(self.base_file or self.current_file) if (self.base_file or self.current_file) else "No model loaded"
        mode_text = self._tool_mode_label()
        summary = f"{model_text}  |  Parts: {len(self.model_objects)}  |  Palette: {self.current_palette_name}  |  Mode: {mode_text}"
        if self.large_model_mode:
            summary += "  |  Safety mode"
        self.toolbar_status_label.setText(summary)
        self.setWindowTitle(f"{APP_TITLE} — {model_text}" if model_text else APP_TITLE)

    def closeEvent(self, event):
        _log("INFO", "Exiting. Cleaning up PLY cache...")
        try:
            if os.path.isdir(self.temp_ply_dir):
                shutil.rmtree(self.temp_ply_dir)
            self._ply_cache_signature = None
        except Exception as e:
            _log("WARN", f"Could not clean cache on exit: {e}")
        if self.viewer:
            self.viewer.close()
        gc.collect()
        super().closeEvent(event)


def main():
    global PALETTES_DIR, PALETTE_FILES
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        script_dir = os.getcwd()

    PALETTES_DIR = os.path.join(script_dir, "palettes")
    if not os.path.isdir(PALETTES_DIR):
        _log("WARN", f"'palettes' directory not found at {PALETTES_DIR}. Using dummy palette data.")
        PALETTES_DIR = ""
    PALETTE_FILES = {
        "Simple": os.path.join(PALETTES_DIR, "simple_palette.json"),
        "Normal": os.path.join(PALETTES_DIR, "normal_palette.json"),
        "Full": os.path.join(PALETTES_DIR, "full_palette.json"),
    }

    try:
        if hasattr(sys, "_MEIPASS") and trimesh is not None:
            trimesh.constants.GLTF_VALIDATOR = os.path.join(sys._MEIPASS, "gltf_validator")
    except Exception:
        pass

    if not _QT_OK:
        raise RuntimeError("Qt dependencies are unavailable; cannot launch v58.")

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("LayerLoom")
    window = QtAssignColorsApp()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
