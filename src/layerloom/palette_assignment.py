"""Shared palette assignment helpers for LayerLoom headless and GUI-adjacent flows."""

from __future__ import annotations

import colorsys
import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

import numpy as np

from layerloom.calibrated_palette import (
    CALIBRATED_CMY_NORMAL_FILENAME,
    CALIBRATED_CMY_NORMAL_NAME,
    nearest_calibrated_palette_entry,
    normalize_hex,
    palette_uses_calibrated_lab,
)
from layerloom.tokens import COLOR_OBJECT_LABELS, PAT_TAG_RE, TOKEN_HEX
from layerloom.transform_3mf import write_transformed_3mf


CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
NS = {"m": CORE_NS}
M = lambda tag: f"{{{CORE_NS}}}{tag}"

PALETTES_DIR = Path(__file__).resolve().with_name("palettes")
PALETTE_FILES = {
    "Simple": PALETTES_DIR / "simple_palette.json",
    "Normal": PALETTES_DIR / "normal_palette.json",
    "Full": PALETTES_DIR / "full_palette.json",
    CALIBRATED_CMY_NORMAL_NAME: PALETTES_DIR / CALIBRATED_CMY_NORMAL_FILENAME,
}

ID_RE = re.compile(r"__ID_([A-Za-z0-9\-]+)__")
END_RE = re.compile(r"(__E\d+)$")


@dataclass(frozen=True)
class PaletteAssignmentResult:
    output_3mf: str
    assignments: dict[str, dict[str, str]]
    matched_count: int


def _sort_by_hue(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(entry: Mapping[str, Any]) -> float:
        try:
            h, _s, _v = colorsys.rgb_to_hsv(*[int(str(entry["hex"])[i:i + 2], 16) / 255 for i in (1, 3, 5)])
            return h
        except Exception:
            return 0.0
    return sorted(entries, key=key)


def _dedupe_equivalent_tokens(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from collections import Counter

    def signature(token: str):
        return tuple(sorted(Counter((token or "").lower()).items()))

    def run_key(token: str):
        token = (token or "").lower()
        if not token:
            return (999, 999, 0, 0, "")
        runs = []
        cur = token[0]
        n = 1
        for ch in token[1:]:
            if ch == cur:
                n += 1
            else:
                runs.append(n)
                cur = ch
                n = 1
        runs.append(n)
        return (max(runs), sum(max(0, run - 2) for run in runs), -sum(1 for run in runs if run == 2), -len(runs), token)

    best: dict[tuple[tuple[str, int], ...], tuple[tuple[Any, ...], dict[str, Any]]] = {}
    for entry in entries:
        token = str(entry.get("token") or "").lower()
        sig = signature(token)
        key = run_key(token)
        if sig not in best or key < best[sig][0]:
            best[sig] = (key, entry)
    return [entry for _key, entry in best.values()]


def _flatten_palette(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict) and "entries" in data:
        return _flatten_palette(data["entries"])
    if isinstance(data, list):
        out: list[dict[str, Any]] = []
        for item in data:
            out.extend(_flatten_palette(item))
        return out
    if isinstance(data, dict):
        return [dict(data)]
    return []


def load_palette_entries(name_or_path: str = "Normal") -> list[dict[str, Any]]:
    path = PALETTE_FILES.get(name_or_path, Path(name_or_path))
    if not Path(path).exists():
        choices = ", ".join(PALETTE_FILES)
        raise FileNotFoundError(f"Unknown palette '{name_or_path}'. Expected one of {choices} or a JSON path.")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    out: list[dict[str, Any]] = []
    seen = set()
    for entry in _flatten_palette(data):
        token = str(entry.get("token") or "").strip().lower()
        hx = normalize_hex(entry.get("hex"))
        if not token or not hx:
            continue
        item = dict(entry)
        item["token"] = token
        item["hex"] = hx
        nominal = normalize_hex(item.get("nominal_hex"))
        if nominal:
            item["nominal_hex"] = nominal
        key = (token, hx)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return _sort_by_hue(_dedupe_equivalent_tokens(out))


def _hex_to_rgb01(hex_code: str) -> tuple[float, float, float]:
    hx = normalize_hex(hex_code)
    if hx is None:
        raise ValueError(f"invalid hex color: {hex_code!r}")
    return tuple(int(hx[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


def nearest_palette_entry(entries: list[dict[str, Any]], source_hex: str) -> Optional[dict[str, Any]]:
    if palette_uses_calibrated_lab(entries):
        match = nearest_calibrated_palette_entry(entries, source_hex)
        if match:
            return dict(match)
    try:
        src = np.asarray(_hex_to_rgb01(source_hex), dtype=np.float64)
    except Exception:
        return None
    best = None
    best_dist = None
    for entry in entries:
        try:
            rgb = np.asarray(_hex_to_rgb01(entry["hex"]), dtype=np.float64)
        except Exception:
            continue
        dist = float(np.sum((src - rgb) ** 2))
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best = dict(entry)
    return best


def _find_model_xml_name(zf: zipfile.ZipFile) -> Optional[str]:
    if "3D/3dmodel.model" in zf.namelist():
        return "3D/3dmodel.model"
    for name in zf.namelist():
        if name.startswith("3D/") and name.lower().endswith(".model"):
            return name
    return None


def _metadata_key(name: Optional[str]) -> str:
    s = str(name or "").strip()
    return s.split(":")[-1] if s else ""


def _object_metadata(obj: ET.Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for md in obj.findall("m:metadata", NS):
        key = _metadata_key(md.get("name"))
        text = str(md.text or "").strip()
        if key and text:
            out[key] = text
    for mg in obj.findall("m:metadatagroup", NS):
        for md in mg.findall("m:metadata", NS):
            key = _metadata_key(md.get("name"))
            text = str(md.text or "").strip()
            if key and text:
                out[key] = text
    return out


def _mesh_objects(root: ET.Element) -> list[ET.Element]:
    resources = root.find("m:resources", NS)
    if resources is None:
        return []
    out = []
    for obj in resources.findall("m:object", NS):
        obj_type = (obj.get("type") or "").strip().lower()
        if (not obj_type or obj_type == "model") and obj.find("m:mesh", NS) is not None:
            out.append(obj)
    return out


def _strip_id_and_pat(name: str) -> str:
    return PAT_TAG_RE.sub("", ID_RE.sub("", name or ""))


def _extract_pat(name: str) -> Optional[str]:
    match = PAT_TAG_RE.search(name or "")
    return match.group(1).lower() if match else None


def _preserve_end_suffix(name: str) -> tuple[str, str]:
    match = END_RE.search(name or "")
    if not match:
        return name or "", ""
    return name[: match.start()], name[match.start():]


def make_labeled_name_with_id(base: str, oid: str, token: Optional[str]) -> str:
    head, suffix = _preserve_end_suffix(_strip_id_and_pat(base or ""))
    tagged = f"{head}__ID_{oid}__"
    if token:
        tagged += f"__PAT_{token}__"
    return tagged + suffix


def _exact_entry_by_token(entries: list[dict[str, Any]], token: Optional[str]) -> Optional[dict[str, Any]]:
    token = str(token or "").strip().lower()
    if not token:
        return None
    for entry in entries:
        if str(entry.get("token") or "").lower() == token:
            return dict(entry)
    return None


def _grouped_color_assignment(name: Optional[str]) -> Optional[dict[str, str]]:
    base = _strip_id_and_pat(str(name or "")).lower()
    for label, token in ((v, k) for k, v in COLOR_OBJECT_LABELS.items()):
        if base == label or base.startswith(label):
            return {"token": token, "hex": TOKEN_HEX.get(token, "")}
    return None


def collect_source_metadata(input_3mf: str) -> dict[str, dict[str, str]]:
    with zipfile.ZipFile(input_3mf, "r") as zf:
        model_name = _find_model_xml_name(zf)
        if not model_name:
            raise RuntimeError("No 3D/*.model found in 3MF.")
        root = ET.fromstring(zf.read(model_name))
    out: dict[str, dict[str, str]] = {}
    for obj in _mesh_objects(root):
        oid = obj.get("id")
        if not oid:
            continue
        meta = _object_metadata(obj)
        meta.setdefault("name", obj.get("name") or f"object_{oid}")
        pat = _extract_pat(meta["name"])
        if pat:
            meta.setdefault("stack_token", pat)
        out[oid] = meta
    return out


def assign_palette_to_3mf(
    input_3mf: str,
    output_3mf: str,
    *,
    palette: str = "Normal",
) -> PaletteAssignmentResult:
    entries = load_palette_entries(palette)
    source = collect_source_metadata(input_3mf)
    assignments: dict[str, dict[str, str]] = {}
    name_updates: dict[str, str] = {}
    metadata_updates: dict[str, dict[str, str]] = {}

    for oid, meta in source.items():
        name = meta.get("name") or f"object_{oid}"
        entry = None
        grouped = _grouped_color_assignment(name)
        if grouped:
            entry = _exact_entry_by_token(entries, grouped.get("token")) or grouped
        if entry is None:
            entry = _exact_entry_by_token(entries, meta.get("stack_token"))
        source_hex = normalize_hex(meta.get("source_hex")) or normalize_hex(meta.get("matched_source_hex"))
        matched_source_hex = source_hex
        if entry is None and source_hex:
            entry = nearest_palette_entry(entries, source_hex)
        if entry is None:
            raise RuntimeError(f"Could not assign a palette token to object {oid} ({name!r}); no stack_token or source_hex match.")

        token = str(entry["token"]).lower()
        assigned_hex = str(entry.get("hex") or "").lower()
        assignment = {"token": token, "hex": assigned_hex}
        if matched_source_hex:
            assignment["matched_source_hex"] = matched_source_hex
        assignments[oid] = assignment
        name_updates[oid] = make_labeled_name_with_id(name, oid, token)
        metadata = {"stack_token": token}
        if assigned_hex:
            metadata["source_hex"] = assigned_hex
        if matched_source_hex and matched_source_hex != assigned_hex:
            metadata["matched_source_hex"] = matched_source_hex
        if entry.get("calibrated") and assigned_hex:
            metadata["calibrated_hex"] = assigned_hex
        if entry.get("nominal_hex"):
            metadata["nominal_hex"] = str(entry["nominal_hex"])
        metadata_updates[oid] = metadata

    os.makedirs(os.path.dirname(os.path.abspath(output_3mf)) or ".", exist_ok=True)
    write_transformed_3mf(
        input_3mf,
        output_3mf,
        np.eye(4, dtype=np.float64),
        name_updates=name_updates,
        metadata_updates=metadata_updates,
    )
    return PaletteAssignmentResult(os.path.abspath(output_3mf), assignments, len(assignments))
