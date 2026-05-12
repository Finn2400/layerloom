#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Orca-first slicing harness and metrics collector for LayerLoom benchmarks.

This script is intentionally split into:
  1. deterministic job + template generation
  2. best-effort structured parsing from exported slicer artifacts
  3. a manual fallback path when slicer exports do not expose every metric
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from xml.etree import ElementTree as ET
from typing import Dict, Iterable, Iterator, List, Tuple

from benchmark_common import (
    JOB_COLUMNS,
    METRIC_COLUMNS,
    ensure_dir,
    layer_height_token,
    parse_duration_minutes,
    read_csv,
    safe_slug,
    write_csv,
)

TEXT_EXTENSIONS = {
    ".gcode", ".bgcode", ".txt", ".json", ".config", ".ini", ".log", ".csv", ".xml",
}

AUTOMATION_COLUMNS = JOB_COLUMNS + ["group_key", "staged_input_path", "export_path"]
DEFAULT_ORCA_UI_PROFILE = {
    "app_name": "OrcaSlicer",
    "slice_button_names": ["Slice plate", "Slice Plate"],
    "export_button_names": ["Export G-code file", "Export G-code", "Export Gcode file"],
    "activate_delay_sec": 1.0,
    "load_delay_sec": 2.5,
    "post_slice_click_delay_sec": 0.8,
    "slice_timeout_sec": 60.0,
    "save_dialog_delay_sec": 1.0,
    "post_export_delay_sec": 2.0,
    "confirm_group_message": (
        "Set OrcaSlicer to the matching printer + layer height for this group before clicking Continue."
    ),
}
DEFAULT_ORCA_API_PROFILE = {
    "base_url": "http://127.0.0.1:3000",
    "slice_async_path": "/slice-async",
    "poll_interval_sec": 2.0,
    "job_timeout_sec": 900.0,
    "delete_completed_jobs": True,
    "printers": {
        "X1C": {
            "printer_profile_path": "/absolute/path/to/x1c_printer.json",
            "filament_profile_path": "/absolute/path/to/generic_pla_filament.json",
            "filament_profile_paths": [],
            "preset_profiles": {
                "0.08": "/absolute/path/to/x1c_process_0p08.json",
                "0.10": "/absolute/path/to/x1c_process_0p10.json",
                "0.12": "/absolute/path/to/x1c_process_0p12.json",
            },
        },
        "U1": {
            "printer_profile_path": "/absolute/path/to/u1_printer.json",
            "filament_profile_path": "/absolute/path/to/generic_pla_filament.json",
            "filament_profile_paths": [],
            "preset_profiles": {
                "0.08": "/absolute/path/to/u1_process_0p08.json",
                "0.10": "/absolute/path/to/u1_process_0p10.json",
                "0.12": "/absolute/path/to/u1_process_0p12.json",
            },
        },
    },
}

BENCHMARK_BEFORE_LAYER_CHANGE_GCODE = ";BENCHMARK_BEFORE_LAYER_CHANGE\nG92 E0\n;[layer_z]\n"
MULTIPART_MIME_OVERRIDES = {
    ".3mf": "model/3mf",
    ".stl": "model/stl",
    ".step": "model/step",
    ".stp": "model/step",
    ".json": "application/json",
}

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PROD_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
BAMBU_NS = "http://schemas.bambulab.com/package/2021"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

ET.register_namespace("", CORE_NS)
ET.register_namespace("p", PROD_NS)
ET.register_namespace("BambuStudio", BAMBU_NS)

EXTRUDER_BY_TOKEN = {
    "c": 1,
    "m": 2,
    "y": 3,
    "k": 4,
    "w": 5,
}

REGEX_PATTERNS = {
    "prepare_time_min": [
        re.compile(r"Prepare time:\s*([^\r\n]+)", re.IGNORECASE),
        re.compile(r"prepare_time(?:_min)?\s*[:=]\s*([^\r\n]+)", re.IGNORECASE),
    ],
    "model_time_min": [
        re.compile(r"model printing time:\s*([^;\r\n]+)", re.IGNORECASE),
        re.compile(r"Model printing time:\s*([^\r\n]+)", re.IGNORECASE),
        re.compile(r"model_print(?:ing)?_time(?:_min)?\s*[:=]\s*([^\r\n]+)", re.IGNORECASE),
    ],
    "total_time_min": [
        re.compile(r"total estimated time:\s*([^;\r\n]+)", re.IGNORECASE),
        re.compile(r"Total time:\s*([^\r\n]+)", re.IGNORECASE),
        re.compile(r"estimated printing time.*?=\s*([^\r\n]+)", re.IGNORECASE),
        re.compile(r"total_time(?:_min)?\s*[:=]\s*([^\r\n]+)", re.IGNORECASE),
    ],
    "filament_change_count": [
        re.compile(r"Filament change times:\s*([0-9]+)", re.IGNORECASE),
        re.compile(r"filament_change(?:_count|_times)?\s*[:=]\s*([0-9]+)", re.IGNORECASE),
    ],
    "total_layers": [
        re.compile(r"total layer number:\s*([0-9]+)", re.IGNORECASE),
        re.compile(r"total layers?\s*[:=]\s*([0-9]+)", re.IGNORECASE),
        re.compile(r";\s*total_layers\s*=\s*([0-9]+)", re.IGNORECASE),
        re.compile(r";\s*layer num/total_layer_count:\s*[0-9]+/([0-9]+)", re.IGNORECASE),
    ],
    "total_filament_g": [
        re.compile(r"total filament.*?([0-9]+(?:\.[0-9]+)?)\s*g", re.IGNORECASE),
        re.compile(r"filament used \[g\]\s*=\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE),
        re.compile(r"total_filament_g\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE),
    ],
    "model_filament_g": [
        re.compile(r"model\s+([0-9]+(?:\.[0-9]+)?)\s*g", re.IGNORECASE),
        re.compile(r"model_filament_g\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE),
    ],
    "purged_filament_g": [
        re.compile(r"purged\s+([0-9]+(?:\.[0-9]+)?)\s*g", re.IGNORECASE),
        re.compile(r"purged_filament_g\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE),
    ],
    "tower_filament_g": [
        re.compile(r"tower\s+([0-9]+(?:\.[0-9]+)?)\s*g", re.IGNORECASE),
        re.compile(r"tower_filament_g\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE),
    ],
}


def iter_text_blobs(path: Path) -> Iterator[Tuple[str, str]]:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path, "r") as zf:
            for name in zf.namelist():
                suffix = Path(name).suffix.lower()
                if suffix not in TEXT_EXTENSIONS and not name.lower().endswith(".gcode.3mf"):
                    continue
                try:
                    raw = zf.read(name)
                except Exception:
                    continue
                try:
                    text = raw.decode("utf-8")
                except Exception:
                    text = raw.decode("utf-8", "ignore")
                if text.strip():
                    yield name, text
        return
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        text = path.read_text(encoding="utf-8", errors="ignore")
    if text.strip():
        yield path.name, text


def parse_scalar_metrics_from_text(text: str) -> Dict[str, object]:
    out: Dict[str, object] = {}
    for key, patterns in REGEX_PATTERNS.items():
        for pattern in patterns:
            m = pattern.search(text)
            if not m:
                continue
            raw = m.group(1).strip()
            if key.endswith("_time_min"):
                val = parse_duration_minutes(raw)
            elif key in {"filament_change_count", "total_layers"}:
                val = int(raw)
            else:
                try:
                    val = float(raw)
                except Exception:
                    val = None
            if val is not None:
                out[key] = val
                break
    return out


def parse_project_metadata(text: str) -> Dict[str, object]:
    out: Dict[str, object] = {}
    try:
        data = json.loads(text)
    except Exception:
        return out
    if isinstance(data, dict):
        if "default_print_profile" in data:
            out["print_profile"] = data.get("default_print_profile")
        if "curr_bed_type" in data:
            out["bed_type"] = data.get("curr_bed_type")
        if "filament_settings_id" in data:
            out["filament_profiles"] = ",".join(data.get("filament_settings_id", []))
    return out


def parse_plate_json(text: str) -> Dict[str, object]:
    out: Dict[str, object] = {}
    try:
        data = json.loads(text)
    except Exception:
        return out
    if not isinstance(data, dict):
        return out
    bbox_objects = data.get("bbox_objects") or []
    tower = next((obj for obj in bbox_objects if str(obj.get("name", "")).lower() == "wipe_tower"), None)
    model = [obj for obj in bbox_objects if str(obj.get("name", "")).lower() != "wipe_tower"]
    if tower:
        out["has_wipe_tower"] = True
        if "area" in tower:
            out["wipe_tower_area"] = tower["area"]
    if model:
        out["object_count_in_plate"] = len(model)
        if "layer_height" in model[0]:
            out["slice_layer_height"] = float(model[0]["layer_height"])
    return out


def parse_metrics_file(path: Path) -> Dict[str, object]:
    combined: Dict[str, object] = {"metrics_source": "parsed", "metrics_file": str(path)}
    notes: List[str] = []
    for name, text in iter_text_blobs(path):
        metrics = parse_scalar_metrics_from_text(text)
        for key, value in metrics.items():
            combined.setdefault(key, value)
        lower_name = name.lower()
        if lower_name.endswith("project_settings.config"):
            for key, value in parse_project_metadata(text).items():
                combined.setdefault(key, value)
        elif lower_name.endswith("plate_1.json"):
            for key, value in parse_plate_json(text).items():
                combined.setdefault(key, value)
        if metrics:
            notes.append(f"{name}:parsed")
    combined["notes"] = "; ".join(notes)
    return combined


def derive_metrics(row: Dict[str, object]) -> Dict[str, object]:
    model_g = row.get("model_filament_g")
    purge_g = row.get("purged_filament_g")
    tower_g = row.get("tower_filament_g")
    total_g = row.get("total_filament_g")
    total_time = row.get("total_time_min")
    total_layers = row.get("total_layers")
    expected_layers = row.get("expected_layers")

    if row.get("waste_filament_g") in ("", None):
        if purge_g is not None or tower_g is not None:
            row["waste_filament_g"] = float(purge_g or 0.0) + float(tower_g or 0.0)
    if row.get("waste_fraction") in ("", None):
        waste = row.get("waste_filament_g")
        if waste is not None and total_g not in (None, "", 0, 0.0):
            row["waste_fraction"] = float(waste) / float(total_g)
    if row.get("throughput_g_per_hr") in ("", None):
        if model_g not in (None, "", 0, 0.0) and total_time not in (None, "", 0, 0.0):
            row["throughput_g_per_hr"] = float(model_g) / (float(total_time) / 60.0)
    if row.get("time_per_layer_sec") in ("", None):
        layer_count = total_layers if total_layers not in (None, "", 0, 0.0) else expected_layers
        if layer_count not in (None, "", 0, 0.0) and total_time not in (None, "", 0, 0.0):
            row["time_per_layer_sec"] = float(total_time) * 60.0 / float(layer_count)
    return row


def load_job_rows(path: Path) -> List[Dict[str, object]]:
    rows = read_csv(path)
    out: List[Dict[str, object]] = []
    for row in rows:
        out.append({
            **row,
            "layer_height": float(row["layer_height"]),
            "distinct_colors": int(row["distinct_colors"]),
            "expected_layers": int(row["expected_layers"]),
        })
    return out


def write_json(path: Path, payload: Dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def group_key_for_job(row: Dict[str, object]) -> str:
    return f"{str(row['printer']).lower()}__lh_{layer_height_token(float(row['layer_height']))}"


def stage_input_copy(source_path: Path, dest_path: Path) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, dest_path)


def _identity_matrix() -> List[List[float]]:
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _parse_3mf_transform(raw: str | None) -> List[List[float]]:
    if not raw:
        return _identity_matrix()
    parts = [float(x) for x in raw.replace(",", " ").split() if x.strip()]
    if len(parts) != 12:
        return _identity_matrix()
    return [
        [parts[0], parts[1], parts[2], parts[9]],
        [parts[3], parts[4], parts[5], parts[10]],
        [parts[6], parts[7], parts[8], parts[11]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _matmul4(a: List[List[float]], b: List[List[float]]) -> List[List[float]]:
    out = [[0.0] * 4 for _ in range(4)]
    for r in range(4):
        for c in range(4):
            out[r][c] = sum(a[r][k] * b[k][c] for k in range(4))
    return out


def _transform_vertices(
    vertices: List[Tuple[float, float, float]],
    matrix: List[List[float]],
) -> List[Tuple[float, float, float]]:
    out: List[Tuple[float, float, float]] = []
    for x, y, z in vertices:
        out.append((
            matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z + matrix[0][3],
            matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z + matrix[1][3],
            matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z + matrix[2][3],
        ))
    return out


def _object_metadata_map(obj: ET.Element) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for md in obj.findall(f"{{{CORE_NS}}}metadata"):
        key = str(md.get("name", "")).strip()
        if key:
            out[key] = (md.text or "").strip()
    return out


def _infer_token_for_orca_assignment(name: str, metadata: Dict[str, str]) -> str | None:
    stack_token = str(metadata.get("stack_token", "")).strip().lower()
    if stack_token in EXTRUDER_BY_TOKEN:
        return stack_token
    source_hex = str(metadata.get("source_hex", "")).strip().lower()
    if source_hex in {"#3ac8dc", "3ac8dc"}:
        return "c"
    if source_hex in {"#c31996", "c31996"}:
        return "m"
    if source_hex in {"#ffdf00", "ffdf00"}:
        return "y"
    if source_hex in {"#141414", "141414"}:
        return "k"
    if source_hex in {"#f5f5f5", "f5f5f5"}:
        return "w"
    lowered = name.strip().lower()
    alias_map = {
        "all_cyan": "c",
        "all_magenta": "m",
        "all_yellow": "y",
        "all_black": "k",
        "all_white": "w",
    }
    for prefix, token in alias_map.items():
        if lowered.startswith(prefix):
            return token
    m = re.search(r"(?:^|_)([cmykw])(?:$|_)", lowered)
    if m:
        token = m.group(1)
        if token in EXTRUDER_BY_TOKEN:
            return token
    if lowered in EXTRUDER_BY_TOKEN:
        return lowered
    return None


def _float_str(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _matrix4_identity_string() -> str:
    return "1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"


def _matrix3mf_translation_string(x: float, y: float, z: float) -> str:
    return f"1 0 0 0 1 0 0 0 1 {_float_str(x)} {_float_str(y)} {_float_str(z)}"


def _indent_xml(elem: ET.Element, level: int = 0) -> None:
    indent = "\n" + (" " * level)
    child_indent = "\n" + (" " * (level + 1))
    children = list(elem)
    if children:
        if not elem.text or not elem.text.strip():
            elem.text = child_indent
        for child in children:
            _indent_xml(child, level + 1)
        if not children[-1].tail or not children[-1].tail.strip():
            children[-1].tail = indent
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = indent


def _build_child_model_xml(
    *,
    child_object_id: int,
    object_uuid: str,
    vertices: List[Tuple[float, float, float]],
    triangles: List[Tuple[int, int, int]],
) -> bytes:
    model = ET.Element(f"{{{CORE_NS}}}model", {
        "unit": "millimeter",
        "{http://www.w3.org/XML/1998/namespace}lang": "en-US",
        "requiredextensions": "p",
    })
    ET.SubElement(model, f"{{{CORE_NS}}}metadata", {"name": "BambuStudio:3mfVersion"}).text = "1"
    resources = ET.SubElement(model, f"{{{CORE_NS}}}resources")
    obj = ET.SubElement(resources, f"{{{CORE_NS}}}object", {
        "id": str(child_object_id),
        "type": "model",
        f"{{{PROD_NS}}}UUID": object_uuid,
    })
    mesh = ET.SubElement(obj, f"{{{CORE_NS}}}mesh")
    vertices_el = ET.SubElement(mesh, f"{{{CORE_NS}}}vertices")
    for x, y, z in vertices:
        ET.SubElement(vertices_el, f"{{{CORE_NS}}}vertex", {
            "x": _float_str(x),
            "y": _float_str(y),
            "z": _float_str(z),
        })
    triangles_el = ET.SubElement(mesh, f"{{{CORE_NS}}}triangles")
    for v1, v2, v3 in triangles:
        ET.SubElement(triangles_el, f"{{{CORE_NS}}}triangle", {
            "v1": str(v1),
            "v2": str(v2),
            "v3": str(v3),
        })
    ET.SubElement(model, f"{{{CORE_NS}}}build")
    _indent_xml(model, level=0)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(model, encoding="utf-8")


def rewrite_staged_3mf_for_orca_assignments(path: Path) -> bool:
    if path.suffix.lower() != ".3mf" or not zipfile.is_zipfile(path):
        return False

    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())
        if "Metadata/model_settings.config" in names:
            return False
        if "3D/3dmodel.model" not in names:
            return False
        root_xml = zf.read("3D/3dmodel.model")

    root = ET.fromstring(root_xml)
    root_meta_pairs: List[Tuple[str, str]] = []
    for md in root.findall(f"{{{CORE_NS}}}metadata"):
        name = str(md.get("name", "")).strip()
        if name:
            root_meta_pairs.append((name, (md.text or "").strip()))
    root_meta = dict(root_meta_pairs)
    title = root_meta.get("Title", path.stem)

    resources = root.find(f"{{{CORE_NS}}}resources")
    build = root.find(f"{{{CORE_NS}}}build")
    if resources is None or build is None:
        return False

    object_map: Dict[str, ET.Element] = {}
    for obj in resources.findall(f"{{{CORE_NS}}}object"):
        oid = obj.get("id")
        if oid:
            object_map[oid] = obj

    parts: List[Dict[str, object]] = []

    def collect_parts(object_id: str, parent_transform: List[List[float]]) -> None:
        obj = object_map.get(object_id)
        if obj is None:
            return
        metadata = _object_metadata_map(obj)
        name = str(obj.get("name") or metadata.get("Title") or metadata.get("Name") or f"object_{object_id}")
        mesh = obj.find(f"{{{CORE_NS}}}mesh")
        if mesh is not None:
            vertices_el = mesh.find(f"{{{CORE_NS}}}vertices")
            triangles_el = mesh.find(f"{{{CORE_NS}}}triangles")
            if vertices_el is None or triangles_el is None:
                return
            vertices = [
                (
                    float(v.get("x", "0")),
                    float(v.get("y", "0")),
                    float(v.get("z", "0")),
                )
                for v in vertices_el.findall(f"{{{CORE_NS}}}vertex")
            ]
            triangles = [
                (
                    int(t.get("v1", "0")),
                    int(t.get("v2", "0")),
                    int(t.get("v3", "0")),
                )
                for t in triangles_el.findall(f"{{{CORE_NS}}}triangle")
            ]
            world_vertices = _transform_vertices(vertices, parent_transform)
            parts.append({
                "name": name,
                "metadata": metadata,
                "vertices": world_vertices,
                "triangles": triangles,
            })
            return

        comps = obj.find(f"{{{CORE_NS}}}components")
        if comps is None:
            return
        for comp in comps.findall(f"{{{CORE_NS}}}component"):
            child_id = comp.get("objectid")
            if not child_id:
                continue
            child_transform = _parse_3mf_transform(comp.get("transform"))
            collect_parts(child_id, _matmul4(parent_transform, child_transform))

    for item in build.findall(f"{{{CORE_NS}}}item"):
        if item.get("printable") == "0":
            continue
        object_id = item.get("objectid")
        if not object_id:
            continue
        item_transform = _parse_3mf_transform(item.get("transform"))
        collect_parts(object_id, item_transform)

    if len(parts) < 2:
        return False

    part_records: List[Dict[str, object]] = []
    for index, part in enumerate(parts, start=1):
        name = str(part["name"])
        metadata = dict(part["metadata"])
        token = _infer_token_for_orca_assignment(name, metadata)
        if token is None:
            return False
        extruder = EXTRUDER_BY_TOKEN.get(token)
        if extruder is None:
            return False
        vertices = list(part["vertices"])
        if not vertices:
            return False
        xs = [v[0] for v in vertices]
        ys = [v[1] for v in vertices]
        zs = [v[2] for v in vertices]
        center = (
            (min(xs) + max(xs)) / 2.0,
            (min(ys) + max(ys)) / 2.0,
            (min(zs) + max(zs)) / 2.0,
        )
        centered_vertices = [
            (vx - center[0], vy - center[1], vz - center[2]) for vx, vy, vz in vertices
        ]
        child_object_id = (index * 2) - 1
        wrapper_object_id = index * 2
        child_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path.name}::child::{index}"))
        wrapper_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path.name}::wrapper::{index}"))
        build_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path.name}::build::{index}"))
        part_records.append({
            "index": index,
            "name": name,
            "token": token,
            "extruder": extruder,
            "metadata": metadata,
            "face_count": len(part["triangles"]),
            "center": center,
            "vertices": centered_vertices,
            "triangles": list(part["triangles"]),
            "child_object_id": child_object_id,
            "wrapper_object_id": wrapper_object_id,
            "child_uuid": child_uuid,
            "wrapper_uuid": wrapper_uuid,
            "build_uuid": build_uuid,
            "child_model_name": f"3D/Objects/object_{index}.model",
            "source_object_id": index - 1,
        })

    model = ET.Element(f"{{{CORE_NS}}}model", {
        "unit": "millimeter",
        "{http://www.w3.org/XML/1998/namespace}lang": "en-US",
        "requiredextensions": "p",
    })
    seen_bambu_version = False
    for key, value in root_meta_pairs:
        if key == "BambuStudio:3mfVersion":
            seen_bambu_version = True
        ET.SubElement(model, f"{{{CORE_NS}}}metadata", {"name": key}).text = value
    if not seen_bambu_version:
        ET.SubElement(model, f"{{{CORE_NS}}}metadata", {"name": "BambuStudio:3mfVersion"}).text = "1"

    resources_el = ET.SubElement(model, f"{{{CORE_NS}}}resources")
    for record in part_records:
        wrapper = ET.SubElement(resources_el, f"{{{CORE_NS}}}object", {
            "id": str(record["wrapper_object_id"]),
            "type": "model",
            f"{{{PROD_NS}}}UUID": str(record["wrapper_uuid"]),
        })
        comps = ET.SubElement(wrapper, f"{{{CORE_NS}}}components")
        ET.SubElement(comps, f"{{{CORE_NS}}}component", {
            "objectid": str(record["child_object_id"]),
            "transform": "1 0 0 0 1 0 0 0 1 0 0 0",
            f"{{{PROD_NS}}}path": f"/{record['child_model_name']}",
            f"{{{PROD_NS}}}UUID": str(record["child_uuid"]),
        })

    build_el = ET.SubElement(model, f"{{{CORE_NS}}}build", {
        f"{{{PROD_NS}}}UUID": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path.name}::root-build")),
    })
    for record in part_records:
        cx, cy, cz = record["center"]
        ET.SubElement(build_el, f"{{{CORE_NS}}}item", {
            "objectid": str(record["wrapper_object_id"]),
            "transform": _matrix3mf_translation_string(cx, cy, cz),
            "printable": "1",
            f"{{{PROD_NS}}}UUID": str(record["build_uuid"]),
        })

    rel_root = ET.Element(f"{{{REL_NS}}}Relationships")
    for record in part_records:
        ET.SubElement(rel_root, f"{{{REL_NS}}}Relationship", {
            "Target": f"/{record['child_model_name']}",
            "Id": f"rel-{record['index']}",
            "Type": "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel",
        })

    config_root = ET.Element("config")
    for record in part_records:
        object_el = ET.SubElement(config_root, "object", {"id": str(record["wrapper_object_id"])})
        ET.SubElement(object_el, "metadata", {"key": "name", "value": str(record["name"])})
        ET.SubElement(object_el, "metadata", {"key": "extruder", "value": str(record["extruder"])})
        ET.SubElement(object_el, "metadata", {"face_count": str(record["face_count"])})
        part_el = ET.SubElement(object_el, "part", {
            "id": str(record["child_object_id"]),
            "subtype": "normal_part",
        })
        ET.SubElement(part_el, "metadata", {"key": "name", "value": str(record["name"])})
        ET.SubElement(part_el, "metadata", {"key": "matrix", "value": _matrix4_identity_string()})
        ET.SubElement(part_el, "metadata", {"key": "source_file", "value": path.name})
        ET.SubElement(part_el, "metadata", {"key": "source_object_id", "value": str(record["source_object_id"])})
        ET.SubElement(part_el, "metadata", {"key": "source_volume_id", "value": "0"})
        cx, cy, cz = record["center"]
        ET.SubElement(part_el, "metadata", {"key": "source_offset_x", "value": _float_str(cx)})
        ET.SubElement(part_el, "metadata", {"key": "source_offset_y", "value": _float_str(cy)})
        ET.SubElement(part_el, "metadata", {"key": "source_offset_z", "value": _float_str(cz)})
        ET.SubElement(part_el, "mesh_stat", {
            "face_count": str(record["face_count"]),
            "edges_fixed": "0",
            "degenerate_facets": "0",
            "facets_removed": "0",
            "facets_reversed": "0",
            "backwards_edges": "0",
        })

    plate_el = ET.SubElement(config_root, "plate")
    ET.SubElement(plate_el, "metadata", {"key": "plater_id", "value": "1"})
    ET.SubElement(plate_el, "metadata", {"key": "plater_name", "value": ""})
    ET.SubElement(plate_el, "metadata", {"key": "locked", "value": "false"})
    ET.SubElement(plate_el, "metadata", {"key": "filament_map_mode", "value": "Auto For Flush"})
    for idx, record in enumerate(part_records, start=1):
        inst = ET.SubElement(plate_el, "model_instance")
        ET.SubElement(inst, "metadata", {"key": "object_id", "value": str(record["wrapper_object_id"])})
        ET.SubElement(inst, "metadata", {"key": "instance_id", "value": "0"})
        ET.SubElement(inst, "metadata", {"key": "identify_id", "value": str(193 + ((idx - 1) * 22))})

    assemble_el = ET.SubElement(config_root, "assemble")
    for record in part_records:
        cx, cy, cz = record["center"]
        ET.SubElement(assemble_el, "assemble_item", {
            "object_id": str(record["wrapper_object_id"]),
            "instance_id": "0",
            "transform": _matrix3mf_translation_string(cx, cy, cz),
            "offset": "0 0 0",
        })

    slice_info_root = ET.Element("config")
    header = ET.SubElement(slice_info_root, "header")
    ET.SubElement(header, "header_item", {"key": "X-BBL-Client-Type", "value": "slicer"})
    ET.SubElement(header, "header_item", {"key": "X-BBL-Client-Version", "value": "02.05.03.61"})

    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
        ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
        '</Types>\n'
    ).encode("utf-8")
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        ' <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
        '</Relationships>\n'
    ).encode("utf-8")

    _indent_xml(model, level=0)
    _indent_xml(rel_root, level=0)
    _indent_xml(config_root, level=0)
    _indent_xml(slice_info_root, level=0)

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("3D/3dmodel.model", b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(model, encoding="utf-8"))
        zf.writestr("3D/_rels/3dmodel.model.rels", b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rel_root, encoding="utf-8"))
        zf.writestr("Metadata/model_settings.config", b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(config_root, encoding="utf-8"))
        zf.writestr("Metadata/slice_info.config", b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(slice_info_root, encoding="utf-8"))
        zf.writestr("Metadata/filament_sequence.json", json.dumps({"plate_1": {"nozzle_sequence": [], "optimal_assignment": [], "sequence": []}}))
        for record in part_records:
            zf.writestr(
                str(record["child_model_name"]),
                _build_child_model_xml(
                    child_object_id=int(record["child_object_id"]),
                    object_uuid=str(record["child_uuid"]),
                    vertices=list(record["vertices"]),
                    triangles=list(record["triangles"]),
                ),
            )
    tmp_path.replace(path)
    return True


def group_same_color_build_items_as_assemblies(path: Path) -> bool:
    if path.suffix.lower() != ".3mf" or not zipfile.is_zipfile(path):
        return False
    with zipfile.ZipFile(path, "r") as zf:
        if "3D/3dmodel.model" not in zf.namelist():
            return False
        root = ET.fromstring(zf.read("3D/3dmodel.model"))
        original_entries = {name: zf.read(name) for name in zf.namelist() if name != "3D/3dmodel.model"}

    resources = root.find(f"{{{CORE_NS}}}resources")
    build = root.find(f"{{{CORE_NS}}}build")
    if resources is None or build is None:
        return False

    object_map: Dict[str, ET.Element] = {}
    token_by_object_id: Dict[str, str] = {}
    for obj in resources.findall(f"{{{CORE_NS}}}object"):
        oid = obj.get("id")
        if not oid:
            continue
        object_map[oid] = obj
        metadata = _object_metadata_map(obj)
        token = _infer_token_for_orca_assignment(str(obj.get("name") or metadata.get("Title") or metadata.get("Name") or ""), metadata)
        if token:
            token_by_object_id[oid] = token

    build_items = build.findall(f"{{{CORE_NS}}}item")
    groups: Dict[str, List[ET.Element]] = {}
    passthrough: List[ET.Element] = []
    for item in build_items:
        oid = item.get("objectid", "")
        token = token_by_object_id.get(oid)
        if token:
            groups.setdefault(token, []).append(item)
        else:
            passthrough.append(item)

    multi_item_groups = {token: items for token, items in groups.items() if len(items) > 1}
    if not multi_item_groups:
        return False

    existing_ids = [int(obj.get("id", "0")) for obj in resources.findall(f"{{{CORE_NS}}}object") if str(obj.get("id", "")).isdigit()]
    next_id = (max(existing_ids) + 1) if existing_ids else 1
    color_names = {"c": "all_cyan", "m": "all_magenta", "y": "all_yellow", "k": "all_black", "w": "all_white"}

    for token, items in sorted(multi_item_groups.items()):
        assembly_id = str(next_id)
        next_id += 1
        assembly_name = color_names.get(token, f"all_{token}")
        assembly_obj = ET.SubElement(resources, f"{{{CORE_NS}}}object", {
            "id": assembly_id,
            "name": assembly_name,
            "type": "model",
        })
        ET.SubElement(assembly_obj, f"{{{CORE_NS}}}metadata", {"name": "Title"}).text = assembly_name
        ET.SubElement(assembly_obj, f"{{{CORE_NS}}}metadata", {"name": "Name"}).text = assembly_name
        ET.SubElement(assembly_obj, f"{{{CORE_NS}}}metadata", {"name": "stack_token"}).text = token
        components = ET.SubElement(assembly_obj, f"{{{CORE_NS}}}components")
        for item in items:
            oid = item.get("objectid", "")
            attrs = {"objectid": oid}
            transform = item.get("transform")
            if transform:
                attrs["transform"] = transform
            ET.SubElement(components, f"{{{CORE_NS}}}component", attrs)

    for item in list(build):
        build.remove(item)

    for item in passthrough:
        build.append(item)

    grouped_tokens = set(multi_item_groups)
    for obj in resources.findall(f"{{{CORE_NS}}}object"):
        oid = obj.get("id", "")
        token = token_by_object_id.get(oid)
        if token and token in grouped_tokens and groups[token]:
            continue
    for token in sorted(grouped_tokens):
        assembly_name = color_names.get(token, f"all_{token}")
        assembly_obj = next(
            (obj for obj in resources.findall(f"{{{CORE_NS}}}object") if obj.get("name") == assembly_name and obj.find(f"{{{CORE_NS}}}components") is not None),
            None,
        )
        if assembly_obj is None:
            continue
        ET.SubElement(build, f"{{{CORE_NS}}}item", {"objectid": assembly_obj.get("id", ""), "printable": "1"})

    _indent_xml(root, level=0)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("3D/3dmodel.model", b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8"))
        for name, data in original_entries.items():
            zf.writestr(name, data)
    tmp_path.replace(path)
    return True


def collapse_same_color_mesh_objects_to_single_meshes(path: Path) -> bool:
    if path.suffix.lower() != ".3mf" or not zipfile.is_zipfile(path):
        return False
    with zipfile.ZipFile(path, "r") as zf:
        if "3D/3dmodel.model" not in zf.namelist():
            return False
        root = ET.fromstring(zf.read("3D/3dmodel.model"))
        original_entries = {name: zf.read(name) for name in zf.namelist() if name != "3D/3dmodel.model"}

    resources = root.find(f"{{{CORE_NS}}}resources")
    build = root.find(f"{{{CORE_NS}}}build")
    if resources is None or build is None:
        return False

    mesh_groups: Dict[str, Dict[str, object]] = {}
    mesh_count = 0
    for obj in resources.findall(f"{{{CORE_NS}}}object"):
        mesh = obj.find(f"{{{CORE_NS}}}mesh")
        if mesh is None:
            continue
        mesh_count += 1
        metadata = _object_metadata_map(obj)
        name = str(obj.get("name") or metadata.get("Title") or metadata.get("Name") or "")
        token = _infer_token_for_orca_assignment(name, metadata)
        if token is None:
            continue
        vertices_el = mesh.find(f"{{{CORE_NS}}}vertices")
        triangles_el = mesh.find(f"{{{CORE_NS}}}triangles")
        if vertices_el is None or triangles_el is None:
            continue
        vertices = [
            (
                float(v.get("x", "0")),
                float(v.get("y", "0")),
                float(v.get("z", "0")),
            )
            for v in vertices_el.findall(f"{{{CORE_NS}}}vertex")
        ]
        triangles = [
            (
                int(t.get("v1", "0")),
                int(t.get("v2", "0")),
                int(t.get("v3", "0")),
            )
            for t in triangles_el.findall(f"{{{CORE_NS}}}triangle")
        ]
        group = mesh_groups.setdefault(token, {
            "name": {
                "c": "all_cyan",
                "m": "all_magenta",
                "y": "all_yellow",
                "k": "all_black",
                "w": "all_white",
            }.get(token, f"all_{token}"),
            "hex": metadata.get("source_hex", ""),
            "vertices": [],
            "triangles": [],
        })
        base_index = len(group["vertices"])  # type: ignore[index]
        group["vertices"].extend(vertices)  # type: ignore[index]
        group["triangles"].extend([(a + base_index, b + base_index, c + base_index) for a, b, c in triangles])  # type: ignore[index]

    if not mesh_groups:
        return False
    if len(mesh_groups) == mesh_count and mesh_count <= 1:
        return False

    root_meta_pairs: List[Tuple[str, str]] = []
    for md in root.findall(f"{{{CORE_NS}}}metadata"):
        name = str(md.get("name", "")).strip()
        if name:
            root_meta_pairs.append((name, (md.text or "").strip()))

    new_resources = ET.Element(f"{{{CORE_NS}}}resources")
    new_build = ET.Element(f"{{{CORE_NS}}}build")
    next_id = 1
    for token in sorted(mesh_groups):
        group = mesh_groups[token]
        obj = ET.SubElement(new_resources, f"{{{CORE_NS}}}object", {
            "id": str(next_id),
            "name": str(group["name"]),
            "type": "model",
        })
        ET.SubElement(obj, f"{{{CORE_NS}}}metadata", {"name": "stack_token"}).text = token
        if str(group["hex"]).strip():
            ET.SubElement(obj, f"{{{CORE_NS}}}metadata", {"name": "source_hex"}).text = str(group["hex"])
        mesh = ET.SubElement(obj, f"{{{CORE_NS}}}mesh")
        vertices_el = ET.SubElement(mesh, f"{{{CORE_NS}}}vertices")
        for x, y, z in group["vertices"]:  # type: ignore[index]
            ET.SubElement(vertices_el, f"{{{CORE_NS}}}vertex", {
                "x": _float_str(float(x)),
                "y": _float_str(float(y)),
                "z": _float_str(float(z)),
            })
        triangles_el = ET.SubElement(mesh, f"{{{CORE_NS}}}triangles")
        for a, b, c in group["triangles"]:  # type: ignore[index]
            ET.SubElement(triangles_el, f"{{{CORE_NS}}}triangle", {
                "v1": str(int(a)),
                "v2": str(int(b)),
                "v3": str(int(c)),
            })
        ET.SubElement(new_build, f"{{{CORE_NS}}}item", {"objectid": str(next_id)})
        next_id += 1

    new_root = ET.Element(f"{{{CORE_NS}}}model", {
        "unit": root.get("unit", "millimeter"),
        "{http://www.w3.org/XML/1998/namespace}lang": root.get("{http://www.w3.org/XML/1998/namespace}lang", "en-US"),
    })
    for key, value in root_meta_pairs:
        ET.SubElement(new_root, f"{{{CORE_NS}}}metadata", {"name": key}).text = value
    new_root.append(new_resources)
    new_root.append(new_build)
    _indent_xml(new_root, level=0)

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("3D/3dmodel.model", b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(new_root, encoding="utf-8"))
        for name, data in original_entries.items():
            zf.writestr(name, data)
    tmp_path.replace(path)
    return True


def relabel_monolith_baseline_3mf(path: Path) -> bool:
    if path.suffix.lower() != ".3mf" or not zipfile.is_zipfile(path):
        return False
    m = re.search(r"bench_monolith_c([0-9]+)_([a-z]+)__printer_", path.name)
    if not m:
        return False
    distinct_colors = int(m.group(1))
    token_family = m.group(2)
    with zipfile.ZipFile(path, "r") as zf:
        if "3D/3dmodel.model" not in zf.namelist():
            return False
        root = ET.fromstring(zf.read("3D/3dmodel.model"))
        original_entries = {name: zf.read(name) for name in zf.namelist() if name != "3D/3dmodel.model"}

    title = f"bench_monolith_baseline__for_c{distinct_colors}_{token_family}"
    for md in root.findall(f"{{{CORE_NS}}}metadata"):
        if md.get("name") == "Title":
            md.text = title
    resources = root.find(f"{{{CORE_NS}}}resources")
    if resources is None:
        return False
    objects = resources.findall(f"{{{CORE_NS}}}object")
    for obj in objects:
        obj.set("name", f"monolith_baseline__for_c{distinct_colors}_{token_family}__PAT_c__")
        existing = {md.get('name'): md for md in obj.findall(f'{{{CORE_NS}}}metadata')}
        if "benchmark_reference_role" in existing:
            existing["benchmark_reference_role"].text = "single_color_baseline"
        else:
            ET.SubElement(obj, f"{{{CORE_NS}}}metadata", {"name": "benchmark_reference_role"}).text = "single_color_baseline"

    _indent_xml(root, level=0)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("3D/3dmodel.model", b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8"))
        for name, data in original_entries.items():
            zf.writestr(name, data)
    tmp_path.replace(path)
    return True


def build_bundle(jobs_csv: Path, out_dir: Path) -> Dict[str, Path]:
    jobs = load_job_rows(jobs_csv)
    bundle_dir = ensure_dir(out_dir)
    staged_root = ensure_dir(bundle_dir / "staged_inputs")
    exports_root = ensure_dir(bundle_dir / "exports")
    groups_root = ensure_dir(bundle_dir / "groups")
    manifests_root = ensure_dir(bundle_dir / "manifests")

    runnable_rows: List[Dict[str, object]] = []
    skipped_rows: List[Dict[str, object]] = []
    grouped: Dict[str, List[Dict[str, object]]] = {}

    for job in jobs:
        group_key = group_key_for_job(job)
        source_kind = str(job.get("source_kind", ""))
        source_path = Path(str(job["slice_input_path"]))
        if source_kind in {"woven_missing", "woven_pending"} or not source_path.exists():
            skipped = {key: job.get(key, "") for key in JOB_COLUMNS}
            skipped["notes"] = f"Skipped for bundle generation ({source_kind or 'missing source'})"
            skipped_rows.append(skipped)
            continue

        staged_dir = ensure_dir(staged_root / group_key)
        exports_dir = ensure_dir(exports_root / group_key)
        staged_name = f"{job['export_stem']}{source_path.suffix or '.3mf'}"
        staged_path = staged_dir / staged_name
        stage_input_copy(source_path, staged_path)
        if (
            staged_path.suffix.lower() == ".3mf"
            and int(job.get("distinct_colors", 1)) > 1
            and str(job.get("source_kind", "")).startswith(("source_3mf", "woven_3mf", "woven_identity"))
        ):
            try:
                rewritten = rewrite_staged_3mf_for_orca_assignments(staged_path)
                if rewritten:
                    note = str(job.get("notes", "")).strip()
                    job["notes"] = "; ".join([n for n in [note, "orca_assigned_3mf"] if n])
            except Exception as exc:
                note = str(job.get("notes", "")).strip()
                job["notes"] = "; ".join([n for n in [note, f"orca_assign_failed={exc}"] if n])

        row = {key: job.get(key, "") for key in JOB_COLUMNS}
        row["group_key"] = group_key
        row["staged_input_path"] = str(staged_path)
        row["export_path"] = str(exports_dir / f"{job['export_stem']}.gcode")
        runnable_rows.append(row)
        grouped.setdefault(group_key, []).append(row)

    for group_key, rows in grouped.items():
        rows = sorted(rows, key=lambda r: (r["method"], int(r["distinct_colors"]), str(r["job_id"])))
        write_csv(groups_root / f"{group_key}.csv", rows, AUTOMATION_COLUMNS)

    write_csv(manifests_root / "automation_jobs.csv", runnable_rows, AUTOMATION_COLUMNS)
    write_csv(manifests_root / "skipped_jobs.csv", skipped_rows, JOB_COLUMNS)
    manual_metrics_csv = manifests_root / "manual_metrics_prefilled.csv"
    prefilled_rows: List[Dict[str, object]] = []
    for row in runnable_rows:
        metric_row = {k: "" for k in METRIC_COLUMNS}
        for key in JOB_COLUMNS:
            if key in metric_row:
                metric_row[key] = row.get(key, "")
        metric_row["metrics_source"] = "manual"
        metric_row["metrics_file"] = ""
        metric_row["notes"] = row.get("notes", "")
        prefilled_rows.append(metric_row)
    write_csv(manual_metrics_csv, prefilled_rows, METRIC_COLUMNS)
    write_json(bundle_dir / "orca_ui_profile.template.json", DEFAULT_ORCA_UI_PROFILE)
    write_json(bundle_dir / "orca_api_profile.template.json", DEFAULT_ORCA_API_PROFILE)

    readme = [
        "# Orca Benchmark Bundle",
        "",
        "## What you do",
        "1. Pick one group CSV from `groups/`.",
        "2. In OrcaSlicer, manually choose that group's printer and layer height once.",
        "3. Run the matching automation command for that group, or work through the rows manually.",
        "4. After each export batch, run `collect` to parse what can be parsed automatically.",
        "5. Fill any remaining missing values in the prefilled metrics CSV.",
        "",
        "## Files",
        "",
        "- `groups/*.csv`: one slicer run group per printer/layer-height.",
        "- `staged_inputs/`: renamed source files so export filenames stay deterministic.",
        "- `exports/`: default export folders, already grouped.",
        "- `manifests/automation_jobs.csv`: all runnable jobs in one table.",
        "- `manifests/manual_metrics_prefilled.csv`: the sheet to fill in as you go.",
        "- `manifests/skipped_jobs.csv`: jobs omitted because slice input was not ready.",
        "- `orca_ui_profile.template.json`: one-time Orca button-name + timing config.",
        "- `orca_api_profile.template.json`: one-time Orca API config with printer/process profile paths.",
        "",
        "## Minimal manual loop",
        "",
        "For one group:",
        "1. Open the group CSV.",
        "2. Set printer + layer height in Orca once.",
        "3. Slice/export each staged input in order.",
        "4. Record only the fields still missing after parsing:",
        "   - `prepare_time_min`",
        "   - `model_time_min`",
        "   - `total_time_min`",
        "   - `filament_change_count`",
        "   - `model_filament_g`",
        "   - `purged_filament_g`",
        "   - `tower_filament_g`",
        "   - `total_filament_g`",
        "   - `total_layers` (if visible)",
        "",
        "## API-assisted loop (recommended if you run `orca-slicer-api` locally)",
        "",
        "1. Copy `orca_api_profile.template.json` to a real config and fill in the exported Orca profile JSON paths.",
        "2. Run `api-run-group` for one group CSV at a time.",
        "3. Run `collect` on the bundle `exports/` folder to parse whatever the downloaded exports expose.",
        "4. Run `overlay-metrics` so your manual sheet is auto-filled as much as possible.",
        "5. Open the overlaid CSV and type only the still-missing values.",
        "",
    ]
    (bundle_dir / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")

    return {
        "bundle_dir": bundle_dir,
        "groups_dir": groups_root,
        "staged_dir": staged_root,
        "exports_dir": exports_root,
        "jobs_csv": manifests_root / "automation_jobs.csv",
        "manual_metrics_csv": manual_metrics_csv,
        "skipped_csv": manifests_root / "skipped_jobs.csv",
        "ui_profile": bundle_dir / "orca_ui_profile.template.json",
        "api_profile": bundle_dir / "orca_api_profile.template.json",
        "readme": bundle_dir / "README.md",
    }


def api_url_join(base_url: str, path: str) -> str:
    if not base_url.endswith("/"):
        base_url += "/"
    return urllib.parse.urljoin(base_url, path.lstrip("/"))


def layer_height_key(value: float) -> str:
    return f"{float(value):.2f}"


def build_multipart_form_data(
    fields: Dict[str, str],
    files: List[Tuple[str, Path]],
) -> Tuple[bytes, str]:
    boundary = f"----LayerLoomBench{uuid.uuid4().hex}"
    body = io.BytesIO()
    for name, value in fields.items():
        body.write(f"--{boundary}\r\n".encode("utf-8"))
        body.write(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
        )
        body.write(str(value).encode("utf-8"))
        body.write(b"\r\n")
    for field_name, file_path in files:
        filename = file_path.name
        mime = MULTIPART_MIME_OVERRIDES.get(file_path.suffix.lower())
        if not mime:
            mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        body.write(f"--{boundary}\r\n".encode("utf-8"))
        body.write(
            (
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{filename}"\r\n'
            ).encode("utf-8")
        )
        body.write(f"Content-Type: {mime}\r\n\r\n".encode("utf-8"))
        body.write(file_path.read_bytes())
        body.write(b"\r\n")
    body.write(f"--{boundary}--\r\n".encode("utf-8"))
    return body.getvalue(), f"multipart/form-data; boundary={boundary}"


def http_json(method: str, url: str, *, headers: Dict[str, str] | None = None, data: bytes | None = None) -> Dict[str, object]:
    req = urllib.request.Request(url, method=method.upper(), headers=headers or {}, data=data)
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def api_post_slice_async(
    base_url: str,
    slice_async_path: str,
    model_file: Path,
    printer_profile: Path,
    preset_profile: Path,
    filament_profiles: List[Path],
    *,
    multicolor_one_plate: bool = False,
) -> Dict[str, object]:
    url = api_url_join(base_url, slice_async_path)
    fields: Dict[str, str] = {}
    if multicolor_one_plate:
        fields["multicolorOnePlate"] = "1"
    files: List[Tuple[str, Path]] = [
        ("file", model_file),
        ("printerProfile", printer_profile),
        ("presetProfile", preset_profile),
    ]
    files.extend(("filamentProfile", filament_profile) for filament_profile in filament_profiles)
    payload, content_type = build_multipart_form_data(
        fields,
        files,
    )
    return http_json(
        "POST",
        url,
        headers={"Content-Type": content_type, "Accept": "application/json"},
        data=payload,
    )


def api_poll_until_complete(
    base_url: str,
    status_path: str,
    *,
    poll_interval_sec: float,
    timeout_sec: float,
) -> Dict[str, object]:
    status_url = api_url_join(base_url, status_path)
    deadline = time.time() + timeout_sec
    last_status: Dict[str, object] = {}
    while time.time() < deadline:
        last_status = http_json("GET", status_url, headers={"Accept": "application/json"})
        status = str(last_status.get("status", ""))
        if status in {"completed", "failed"}:
            return last_status
        time.sleep(poll_interval_sec)
    raise TimeoutError(f"Timed out waiting for Orca API job after {timeout_sec:.1f}s")


def parse_content_disposition_filename(header_value: str | None) -> str | None:
    if not header_value:
        return None
    match = re.search(r'filename="([^"]+)"', header_value)
    if match:
        return match.group(1)
    match = re.search(r"filename=([^;]+)", header_value)
    if match:
        return match.group(1).strip()
    return None


def choose_download_path(export_path: Path, filename_hint: str | None) -> Path:
    hint = filename_hint or ""
    if hint.endswith(".gcode.3mf"):
        suffix = ".gcode.3mf"
    else:
        suffix = "".join(Path(hint).suffixes)
    if not suffix:
        suffix = export_path.suffix or ".bin"
    return export_path.with_suffix(suffix)


def api_download_result(
    base_url: str,
    download_path: str,
    export_path: Path,
) -> Tuple[Path, Dict[str, str]]:
    url = api_url_join(base_url, download_path)
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=300) as resp:
        filename_hint = parse_content_disposition_filename(resp.headers.get("Content-Disposition"))
        dest = choose_download_path(export_path, filename_hint)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.read())
        headers = {str(k): str(v) for k, v in resp.headers.items()}
    return dest, headers


def api_delete_job(base_url: str, status_path: str) -> None:
    try:
        http_json("DELETE", api_url_join(base_url, status_path))
    except Exception:
        return


def resolve_api_profile_paths(
    api_cfg: Dict[str, object],
    printer: str,
    layer_height: float,
    *,
    distinct_colors: int = 1,
    require_exists: bool = True,
) -> Tuple[Path, Path, List[Path]]:
    printers = api_cfg.get("printers")
    if not isinstance(printers, dict) or printer not in printers:
        raise KeyError(f"No Orca API printer profile block found for printer '{printer}'")
    block = printers[printer]
    if not isinstance(block, dict):
        raise ValueError(f"Invalid Orca API printer config for '{printer}'")
    printer_profile = Path(str(block.get("printer_profile_path", "")))
    filament_profile_paths_raw = block.get("filament_profile_paths")
    filament_profile_paths: List[Path] = []
    if isinstance(filament_profile_paths_raw, list):
        filament_profile_paths = [
            Path(str(item)) for item in filament_profile_paths_raw if str(item).strip()
        ]
    elif filament_profile_paths_raw not in (None, ""):
        filament_profile_paths = [Path(str(filament_profile_paths_raw))]
    if not filament_profile_paths:
        filament_profile = Path(str(block.get("filament_profile_path", "")))
        if str(filament_profile).strip():
            filament_profile_paths = [filament_profile]
    preset_profiles = block.get("preset_profiles")
    if not isinstance(preset_profiles, dict):
        raise ValueError(f"Invalid preset_profiles block for '{printer}'")
    preset_profile = Path(str(preset_profiles.get(layer_height_key(layer_height), "")))
    for label, path in (("printer_profile_path", printer_profile),):
        if not str(path):
            raise FileNotFoundError(
                f"Missing {label} for printer={printer} layer_height={layer_height_key(layer_height)}: {path}"
            )
    if not filament_profile_paths:
        raise FileNotFoundError(
            f"Missing filament profile path(s) for printer={printer} layer_height={layer_height_key(layer_height)}"
        )
    if not str(preset_profile):
        raise FileNotFoundError(
            f"Missing preset_profile_path for printer={printer} layer_height={layer_height_key(layer_height)}: {preset_profile}"
        )
    desired_count = max(1, int(distinct_colors))
    if len(filament_profile_paths) < desired_count:
        last = filament_profile_paths[-1]
        filament_profile_paths = filament_profile_paths + [last] * (desired_count - len(filament_profile_paths))
    else:
        filament_profile_paths = filament_profile_paths[:desired_count]
    if require_exists:
        for label, path in (("printer_profile_path", printer_profile), ("preset_profile_path", preset_profile)):
            if not path.exists():
                raise FileNotFoundError(
                    f"Missing {label} for printer={printer} layer_height={layer_height_key(layer_height)}: {path}"
                )
        for idx, path in enumerate(filament_profile_paths, start=1):
            if not path.exists():
                raise FileNotFoundError(
                    f"Missing filament_profile_path[{idx}] for printer={printer} layer_height={layer_height_key(layer_height)}: {path}"
                )
    return printer_profile, preset_profile, filament_profile_paths


def prepare_benchmark_safe_api_profiles(
    printer_profile: Path,
    preset_profile: Path,
    filament_profiles: List[Path],
    out_dir: Path,
    *,
    printer: str,
    layer_height: float,
) -> Tuple[Path, Path, List[Path]]:
    """
    Generate uploaded copies of the slicer profiles so benchmark runs stay
    reproducible and do not mutate the user's live Orca profiles.

    Orca's headless CLI path can reject some vendor machine profiles unless an
    unconditional `G92 E0` exists in the layer-change path. We inject that into
    a generated copy only when it is missing.
    """

    target_dir = out_dir / safe_slug(printer.lower()) / f"lh_{layer_height_token(layer_height)}"
    ensure_dir(target_dir)

    printer_dest = target_dir / f"printer__{safe_slug(printer_profile.stem)}.json"
    preset_dest = target_dir / f"preset__{safe_slug(preset_profile.stem)}.json"

    printer_data = json.loads(printer_profile.read_text(encoding="utf-8"))
    if not isinstance(printer_data, dict):
        raise ValueError(f"Expected JSON object in printer profile: {printer_profile}")

    before_layer = printer_data.get("before_layer_change_gcode")
    has_layer_reset = isinstance(before_layer, str) and "G92 E0" in before_layer
    if not has_layer_reset:
        chunks: List[str] = []
        if isinstance(before_layer, str) and before_layer.strip():
            chunks.append(before_layer.rstrip())
        chunks.append(BENCHMARK_BEFORE_LAYER_CHANGE_GCODE.rstrip())
        printer_data["before_layer_change_gcode"] = "\n".join(chunks).strip() + "\n"
        printer_data["layerloom_benchmark_patch"] = "added_before_layer_change_gcode_G92_E0"

    printer_dest.write_text(json.dumps(printer_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    shutil.copy2(preset_profile, preset_dest)
    filament_dests: List[Path] = []
    for idx, filament_profile in enumerate(filament_profiles, start=1):
        filament_dest = target_dir / f"filament_{idx:02d}__{safe_slug(filament_profile.stem)}.json"
        shutil.copy2(filament_profile, filament_dest)
        filament_dests.append(filament_dest)
    return printer_dest, preset_dest, filament_dests


def metadata_to_metric_fields(metadata: Dict[str, object]) -> Dict[str, object]:
    out: Dict[str, object] = {}
    print_time = metadata.get("printTime")
    filament_used_g = metadata.get("filamentUsedG")
    filament_used_mm = metadata.get("filamentUsedMm")
    if print_time not in (None, ""):
        out["total_time_min"] = float(print_time) / 60.0
    if filament_used_g not in (None, ""):
        out["total_filament_g"] = float(filament_used_g)
    if filament_used_mm not in (None, ""):
        out["notes"] = f"filament_used_mm={filament_used_mm}"
    return out


def download_headers_to_metric_fields(headers: Dict[str, str]) -> Dict[str, object]:
    out: Dict[str, object] = {}
    print_time = headers.get("X-Print-Time-Seconds")
    filament_used_g = headers.get("X-Filament-Used-G")
    filament_used_mm = headers.get("X-Filament-Used-Mm")
    if print_time not in (None, ""):
        out["total_time_min"] = float(print_time) / 60.0
    if filament_used_g not in (None, ""):
        out["total_filament_g"] = float(filament_used_g)
    if filament_used_mm not in (None, ""):
        out["notes"] = f"filament_used_mm={filament_used_mm}"
    return out


def _run_osascript(lines: List[str], argv: List[str] | None = None) -> subprocess.CompletedProcess:
    cmd = ["osascript"]
    for line in lines:
        cmd.extend(["-e", line])
    if argv:
        cmd.extend(argv)
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def _osascript_activate(app_name: str) -> None:
    _run_osascript([
        'on run argv',
        'set appName to item 1 of argv',
        'tell application appName to activate',
        'end run',
    ], [app_name])


def _osascript_display_prompt(message: str) -> None:
    _run_osascript([
        'on run argv',
        'set msg to item 1 of argv',
        'display dialog msg buttons {"Cancel", "Continue"} default button "Continue"',
        'end run',
    ], [message])


def _osascript_button_exists(app_name: str, button_name: str) -> bool:
    proc = _run_osascript([
        'on run argv',
        'set appName to item 1 of argv',
        'set buttonName to item 2 of argv',
        'tell application "System Events"',
        'tell process appName',
        'set frontmost to true',
        'try',
        'set _b to first button of entire contents of front window whose name is buttonName',
        'return "1"',
        'on error',
        'return "0"',
        'end try',
        'end tell',
        'end tell',
        'end run',
    ], [app_name, button_name])
    return proc.stdout.strip() == "1"


def _osascript_click_button(app_name: str, button_name: str) -> bool:
    proc = _run_osascript([
        'on run argv',
        'set appName to item 1 of argv',
        'set buttonName to item 2 of argv',
        'tell application "System Events"',
        'tell process appName',
        'set frontmost to true',
        'try',
        'click (first button of entire contents of front window whose name is buttonName)',
        'return "1"',
        'on error',
        'return "0"',
        'end try',
        'end tell',
        'end tell',
        'end run',
    ], [app_name, button_name])
    return proc.stdout.strip() == "1"


def _click_first_matching_button(app_name: str, names: List[str]) -> str | None:
    for name in names:
        if _osascript_click_button(app_name, name):
            return name
    return None


def _wait_for_button(app_name: str, names: List[str], timeout_sec: float) -> str | None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        for name in names:
            if _osascript_button_exists(app_name, name):
                return name
        time.sleep(0.5)
    return None


def _save_dialog_to_folder(app_name: str, export_dir: Path) -> None:
    _run_osascript([
        'on run argv',
        'set appName to item 1 of argv',
        'set exportDir to item 2 of argv',
        'tell application appName to activate',
        'delay 0.2',
        'tell application "System Events"',
        'keystroke "G" using {command down, shift down}',
        'delay 0.5',
        'keystroke exportDir',
        'key code 36',
        'delay 0.5',
        'key code 36',
        'end tell',
        'end run',
    ], [app_name, str(export_dir)])


def run_group(group_csv: Path, ui_profile_path: Path, *, dry_run: bool = False, limit: int | None = None) -> Path:
    rows = read_csv(group_csv)
    ui = json.loads(ui_profile_path.read_text(encoding="utf-8"))
    app_name = str(ui["app_name"])
    slice_names = list(ui["slice_button_names"])
    export_names = list(ui["export_button_names"])
    activate_delay = float(ui.get("activate_delay_sec", 1.0))
    load_delay = float(ui.get("load_delay_sec", 2.5))
    post_slice_click_delay = float(ui.get("post_slice_click_delay_sec", 0.8))
    slice_timeout = float(ui.get("slice_timeout_sec", 60.0))
    save_dialog_delay = float(ui.get("save_dialog_delay_sec", 1.0))
    post_export_delay = float(ui.get("post_export_delay_sec", 2.0))
    prompt = str(ui.get("confirm_group_message", "Confirm Orca settings before continuing."))

    run_rows: List[Dict[str, object]] = []
    if not dry_run:
        _osascript_display_prompt(prompt)
        _osascript_activate(app_name)
        time.sleep(activate_delay)

    for idx, row in enumerate(rows, start=1):
        if limit is not None and idx > limit:
            break
        staged_input = Path(row["staged_input_path"])
        export_path = Path(row["export_path"])
        export_dir = export_path.parent
        export_dir.mkdir(parents=True, exist_ok=True)
        result = {
            "job_id": row["job_id"],
            "group_key": row["group_key"],
            "staged_input_path": str(staged_input),
            "export_path": str(export_path),
            "status": "pending",
            "notes": "",
        }
        try:
            if dry_run:
                result["status"] = "dry_run"
                run_rows.append(result)
                continue
            subprocess.run(["open", "-a", app_name, str(staged_input)], check=True)
            time.sleep(load_delay)
            _osascript_activate(app_name)
            time.sleep(activate_delay)
            clicked = _click_first_matching_button(app_name, slice_names)
            if not clicked:
                raise RuntimeError("Could not find Slice button in OrcaSlicer.")
            time.sleep(post_slice_click_delay)
            visible_export = _wait_for_button(app_name, export_names, slice_timeout)
            if not visible_export:
                raise RuntimeError("Timed out waiting for Export button after slicing.")
            clicked_export = _click_first_matching_button(app_name, export_names)
            if not clicked_export:
                raise RuntimeError("Could not click Export button in OrcaSlicer.")
            time.sleep(save_dialog_delay)
            _save_dialog_to_folder(app_name, export_dir)
            time.sleep(post_export_delay)
            result["status"] = "export_triggered"
            result["notes"] = f"slice={clicked}; export={clicked_export}"
        except Exception as exc:
            result["status"] = "failed"
            result["notes"] = str(exc)
        run_rows.append(result)

    run_log = group_csv.with_name(group_csv.stem + "__run_log.csv")
    write_csv(run_log, run_rows, ["job_id", "group_key", "staged_input_path", "export_path", "status", "notes"])
    return run_log


def api_run_group(
    group_csv: Path,
    api_profile_path: Path,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    out_csv: Path | None = None,
) -> Tuple[Path, Path]:
    rows = read_csv(group_csv)
    api_cfg = json.loads(api_profile_path.read_text(encoding="utf-8"))
    base_url = str(api_cfg.get("base_url", "http://127.0.0.1:3000"))
    slice_async_path = str(api_cfg.get("slice_async_path", "/slice-async"))
    poll_interval_sec = float(api_cfg.get("poll_interval_sec", 2.0))
    timeout_sec = float(api_cfg.get("job_timeout_sec", 900.0))
    delete_completed_jobs = bool(api_cfg.get("delete_completed_jobs", True))
    generated_profiles_root = group_csv.parent.parent / "generated_api_profiles"
    ensure_dir(generated_profiles_root)
    upload_profile_cache: Dict[Tuple[str, str, int], Tuple[Path, Path, List[Path]]] = {}

    metrics_rows: List[Dict[str, object]] = []
    run_rows: List[Dict[str, object]] = []
    for idx, row in enumerate(rows, start=1):
        if limit is not None and idx > limit:
            break
        staged_input = Path(str(row["staged_input_path"]))
        export_path = Path(str(row["export_path"]))
        printer = str(row["printer"])
        layer_height = float(row["layer_height"])
        distinct_colors = int(row["distinct_colors"])
        base_metric = {key: row.get(key, "") for key in JOB_COLUMNS}
        base_metric.update({k: "" for k in METRIC_COLUMNS if k not in base_metric})
        base_metric["metrics_source"] = "missing"
        base_metric["metrics_file"] = ""
        base_metric["notes"] = ""

        run_result: Dict[str, object] = {
            "job_id": row["job_id"],
            "group_key": row["group_key"],
            "staged_input_path": str(staged_input),
            "export_path": str(export_path),
            "request_id": "",
            "status": "pending",
            "notes": "",
        }

        try:
            printer_profile, preset_profile, filament_profiles = resolve_api_profile_paths(
                api_cfg, printer, layer_height, distinct_colors=distinct_colors, require_exists=not dry_run
            )
            if dry_run:
                run_result["status"] = "dry_run"
                run_result["notes"] = (
                    f"printer={printer_profile.name}; preset={preset_profile.name}; filaments={len(filament_profiles)}"
                )
                base_metric["metrics_source"] = "orca_api_dry_run"
                metrics_rows.append(base_metric)
                run_rows.append(run_result)
                continue

            cache_key = (printer, layer_height_key(layer_height), distinct_colors)
            if cache_key not in upload_profile_cache:
                upload_profile_cache[cache_key] = prepare_benchmark_safe_api_profiles(
                    printer_profile,
                    preset_profile,
                    filament_profiles,
                    generated_profiles_root,
                    printer=printer,
                    layer_height=layer_height,
                )
            upload_printer_profile, upload_preset_profile, upload_filament_profiles = upload_profile_cache[cache_key]

            submitted = api_post_slice_async(
                base_url,
                slice_async_path,
                staged_input,
                upload_printer_profile,
                upload_preset_profile,
                upload_filament_profiles,
                multicolor_one_plate=distinct_colors > 1,
            )
            request_id = str(submitted.get("requestId", ""))
            status_path = str(submitted.get("statusUrl", f"{slice_async_path.rstrip('/')}/{request_id}"))
            run_result["request_id"] = request_id
            run_result["status"] = str(submitted.get("status", "submitted"))

            final_status = api_poll_until_complete(
                base_url,
                status_path,
                poll_interval_sec=poll_interval_sec,
                timeout_sec=timeout_sec,
            )
            status = str(final_status.get("status", ""))
            run_result["status"] = status
            if status != "completed":
                message = str(final_status.get("message", "Orca API job failed"))
                run_result["notes"] = message
                base_metric["metrics_source"] = "orca_api_failed"
                base_metric["notes"] = message
                metrics_rows.append(base_metric)
                run_rows.append(run_result)
                continue

            metadata = final_status.get("metadata") or {}
            if not isinstance(metadata, dict):
                metadata = {}
            download_path = str(final_status.get("downloadUrl", f"{status_path.rstrip('/')}/result"))
            downloaded, download_headers = api_download_result(base_url, download_path, export_path)
            run_result["notes"] = f"downloaded={downloaded.name}"

            base_metric.update(metadata_to_metric_fields(metadata))
            header_metrics = download_headers_to_metric_fields(download_headers)
            for key, value in header_metrics.items():
                if base_metric.get(key) in ("", None):
                    base_metric[key] = value
            parsed = parse_metrics_file(downloaded)
            for key, value in parsed.items():
                if base_metric.get(key) in ("", None):
                    base_metric[key] = value
            base_metric["metrics_source"] = "orca_api"
            base_metric["metrics_file"] = str(downloaded)
            notes = [str(base_metric.get("notes", "")).strip(), f"request_id={request_id}"]
            base_metric["notes"] = "; ".join([n for n in notes if n])
            derive_metrics(base_metric)
            metrics_rows.append(base_metric)
            run_rows.append(run_result)

            if delete_completed_jobs:
                api_delete_job(base_url, status_path)
        except Exception as exc:
            run_result["status"] = "failed"
            run_result["notes"] = str(exc)
            base_metric["metrics_source"] = "orca_api_failed"
            base_metric["notes"] = str(exc)
            metrics_rows.append(base_metric)
            run_rows.append(run_result)

    run_log = group_csv.with_name(group_csv.stem + "__api_run_log.csv")
    metrics_csv = out_csv or group_csv.with_name(group_csv.stem + "__api_metrics.csv")
    write_csv(
        run_log,
        run_rows,
        ["job_id", "group_key", "staged_input_path", "export_path", "request_id", "status", "notes"],
    )
    write_csv(metrics_csv, metrics_rows, METRIC_COLUMNS)
    return run_log, metrics_csv


def write_manual_template(jobs_csv: Path, out_csv: Path) -> None:
    jobs = load_job_rows(jobs_csv)
    rows: List[Dict[str, object]] = []
    for row in jobs:
        out = {key: row.get(key, "") for key in JOB_COLUMNS}
        out.update({k: "" for k in METRIC_COLUMNS if k not in out})
        out["metrics_source"] = "manual"
        out["notes"] = ""
        rows.append(out)
    write_csv(out_csv, rows, METRIC_COLUMNS)


def collect_exports(jobs_csv: Path, exports_dir: Path, out_csv: Path) -> None:
    jobs = load_job_rows(jobs_csv)
    export_lookup: Dict[str, Path] = {}
    for path in exports_dir.rglob("*"):
        if not path.is_file():
            continue
        export_lookup[path.stem] = path
        export_lookup[path.name] = path

    rows: List[Dict[str, object]] = []
    for job in jobs:
        stem = str(job["export_stem"])
        matched = None
        for key, path in export_lookup.items():
            if key.startswith(stem):
                matched = path
                break
        row = {key: job.get(key, "") for key in JOB_COLUMNS}
        row.update({k: "" for k in METRIC_COLUMNS if k not in row})
        row["metrics_source"] = "missing"
        row["notes"] = ""
        if matched is not None:
            parsed = parse_metrics_file(matched)
            row.update(parsed)
        derive_metrics(row)
        rows.append(row)
    write_csv(out_csv, rows, METRIC_COLUMNS)


def merge_manual(jobs_csv: Path, manual_csv: Path, out_csv: Path) -> None:
    jobs = load_job_rows(jobs_csv)
    manual_rows = {row["job_id"]: row for row in read_csv(manual_csv)}
    rows: List[Dict[str, object]] = []
    for job in jobs:
        row = {key: job.get(key, "") for key in JOB_COLUMNS}
        row.update({k: "" for k in METRIC_COLUMNS if k not in row})
        manual = manual_rows.get(str(job["job_id"]), {})
        row.update(manual)
        row["metrics_source"] = manual.get("metrics_source", "manual")
        if row.get("prepare_time_min"):
            row["prepare_time_min"] = float(row["prepare_time_min"])
        if row.get("model_time_min"):
            row["model_time_min"] = float(row["model_time_min"])
        if row.get("total_time_min"):
            row["total_time_min"] = float(row["total_time_min"])
        if row.get("filament_change_count"):
            row["filament_change_count"] = int(float(row["filament_change_count"]))
        if row.get("total_layers"):
            row["total_layers"] = int(float(row["total_layers"]))
        for key in ("model_filament_g", "purged_filament_g", "tower_filament_g", "total_filament_g"):
            if row.get(key) not in ("", None):
                row[key] = float(row[key])
        derive_metrics(row)
        rows.append(row)
    write_csv(out_csv, rows, METRIC_COLUMNS)


def overlay_metrics(base_csv: Path, overlay_csv: Path, out_csv: Path) -> None:
    base_rows = read_csv(base_csv)
    overlay_rows = {row["job_id"]: row for row in read_csv(overlay_csv)}
    rows: List[Dict[str, object]] = []
    for base in base_rows:
        merged = dict(base)
        overlay = overlay_rows.get(str(base.get("job_id", "")), {})
        for key in METRIC_COLUMNS:
            if key == "job_id":
                continue
            value = overlay.get(key, "")
            if value not in ("", None):
                merged[key] = value
        rows.append(merged)
    write_csv(out_csv, rows, METRIC_COLUMNS)


def write_batch_instructions(jobs_csv: Path, out_md: Path) -> None:
    jobs = load_job_rows(jobs_csv)
    lines = [
        "# Orca Benchmark Batch Instructions",
        "",
        "For each job below:",
        "1. Open the `slice_input_path` in OrcaSlicer.",
        "2. Load the matching printer/process profile.",
        "3. Set the requested layer height.",
        "4. Slice the plate.",
        "5. Export using the suggested `export_stem`.",
        "6. Place the exported file in the chosen exports directory.",
        "",
        "| export_stem | printer | layer_height | method | distinct_colors | slice_input_path |",
        "|---|---:|---:|---|---:|---|",
    ]
    for row in jobs:
        lines.append(
            f"| {row['export_stem']} | {row['printer']} | {row['layer_height']} | {row['method']} | {row['distinct_colors']} | {row['slice_input_path']} |"
        )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Collect slicer-estimate benchmark metrics (Orca-first).")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("template", help="Create a manual metrics CSV template from benchmark jobs.")
    sp.add_argument("--jobs-csv", required=True)
    sp.add_argument("--out-csv", required=True)

    sp = sub.add_parser("collect", help="Collect metrics by parsing exported slicer artifacts.")
    sp.add_argument("--jobs-csv", required=True)
    sp.add_argument("--exports-dir", required=True)
    sp.add_argument("--out-csv", required=True)

    sp = sub.add_parser("merge-manual", help="Merge manually entered metrics into the canonical metrics CSV.")
    sp.add_argument("--jobs-csv", required=True)
    sp.add_argument("--manual-csv", required=True)
    sp.add_argument("--out-csv", required=True)

    sp = sub.add_parser("instructions", help="Write an Orca batch checklist markdown file.")
    sp.add_argument("--jobs-csv", required=True)
    sp.add_argument("--out-md", required=True)

    sp = sub.add_parser("bundle", help="Create a grouped Orca run bundle with staged inputs and prefilled manifests.")
    sp.add_argument("--jobs-csv", required=True)
    sp.add_argument("--out-dir", required=True)

    sp = sub.add_parser("run-group", help="Run one grouped Orca batch using AppleScript/System Events.")
    sp.add_argument("--group-csv", required=True)
    sp.add_argument("--ui-profile", required=True)
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--limit", type=int, default=None)

    sp = sub.add_parser("api-run-group", help="Run one grouped Orca batch through a local orca-slicer-api server.")
    sp.add_argument("--group-csv", required=True)
    sp.add_argument("--api-profile", required=True)
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--limit", type=int, default=None)
    sp.add_argument("--out-csv", default=None)

    sp = sub.add_parser("overlay-metrics", help="Overlay parsed/API metrics onto a prefilled manual metrics sheet.")
    sp.add_argument("--base-csv", required=True)
    sp.add_argument("--overlay-csv", required=True)
    sp.add_argument("--out-csv", required=True)

    sp = sub.add_parser("parse-export", help="Parse a single exported slicer file and print JSON-ish key=value rows.")
    sp.add_argument("export_file")

    return p


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "template":
        write_manual_template(Path(args.jobs_csv), Path(args.out_csv))
        return 0
    if args.cmd == "collect":
        collect_exports(Path(args.jobs_csv), Path(args.exports_dir), Path(args.out_csv))
        return 0
    if args.cmd == "merge-manual":
        merge_manual(Path(args.jobs_csv), Path(args.manual_csv), Path(args.out_csv))
        return 0
    if args.cmd == "instructions":
        write_batch_instructions(Path(args.jobs_csv), Path(args.out_md))
        return 0
    if args.cmd == "bundle":
        out = build_bundle(Path(args.jobs_csv), Path(args.out_dir))
        print(f"bundle_dir={out['bundle_dir']}")
        print(f"groups_dir={out['groups_dir']}")
        print(f"jobs_csv={out['jobs_csv']}")
        print(f"manual_metrics_csv={out['manual_metrics_csv']}")
        print(f"skipped_csv={out['skipped_csv']}")
        print(f"ui_profile={out['ui_profile']}")
        print(f"api_profile={out['api_profile']}")
        print(f"readme={out['readme']}")
        return 0
    if args.cmd == "run-group":
        run_log = run_group(
            Path(args.group_csv),
            Path(args.ui_profile),
            dry_run=bool(args.dry_run),
            limit=args.limit,
        )
        print(f"run_log={run_log}")
        return 0
    if args.cmd == "api-run-group":
        run_log, metrics_csv = api_run_group(
            Path(args.group_csv),
            Path(args.api_profile),
            dry_run=bool(args.dry_run),
            limit=args.limit,
            out_csv=Path(args.out_csv) if args.out_csv else None,
        )
        print(f"run_log={run_log}")
        print(f"metrics_csv={metrics_csv}")
        return 0
    if args.cmd == "overlay-metrics":
        overlay_metrics(Path(args.base_csv), Path(args.overlay_csv), Path(args.out_csv))
        return 0
    if args.cmd == "parse-export":
        parsed = parse_metrics_file(Path(args.export_file))
        for key in sorted(parsed.keys()):
            print(f"{key}={parsed[key]}")
        return 0
    raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit(main())
