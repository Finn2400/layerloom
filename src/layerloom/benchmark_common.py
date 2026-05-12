#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shared constants and helpers for LayerLoom benchmarking.
"""

from __future__ import annotations

import csv
import math
import os
import re
from pathlib import Path
from typing import Iterable, List, Dict

PRINTERS = ("X1C", "U1")
LAYER_HEIGHTS = (0.08, 0.10, 0.12)
DISTINCT_COLOR_COUNTS = (1, 2, 3, 4)
METHODS = ("monolith", "woven_prism", "stacked_cells", "coplanar_grid")

CELL_SIZE_MM = 15.0
CELL_COUNT = 12
TOTAL_VOLUME_MM3 = CELL_COUNT * (CELL_SIZE_MM ** 3)
WOVEN_PRISM_HEIGHT_MM = 28.8
WOVEN_PRISM_SIDE_MM = 37.5
WOVEN_PRISM_DIMS_MM = (WOVEN_PRISM_SIDE_MM, WOVEN_PRISM_SIDE_MM, WOVEN_PRISM_HEIGHT_MM)
COPLANAR_DIMS_MM = (CELL_SIZE_MM * 3.0, CELL_SIZE_MM * 4.0, CELL_SIZE_MM)
STACKED_DIMS_MM = (CELL_SIZE_MM, CELL_SIZE_MM, CELL_SIZE_MM * CELL_COUNT)

TOKEN_FAMILIES = {
    1: "c",
    2: "cm",
    3: "cmy",
    4: "cmyk",
}

TOKEN_HEX = {
    "c": "#3ac8dc",
    "m": "#c31996",
    "y": "#ffdf00",
    "k": "#141414",
    "w": "#f5f5f5",
}

CASE_COLUMNS = [
    "case_id",
    "method",
    "distinct_colors",
    "token_family",
    "object_count",
    "total_volume_mm3",
    "dims_x_mm",
    "dims_y_mm",
    "dims_z_mm",
    "source_kind",
    "source_path",
    "notes",
]

JOB_COLUMNS = [
    "job_id",
    "case_id",
    "printer",
    "layer_height",
    "method",
    "distinct_colors",
    "token_family",
    "slice_input_path",
    "source_kind",
    "expected_layers",
    "export_stem",
    "notes",
]

METRIC_COLUMNS = [
    "job_id",
    "case_id",
    "printer",
    "layer_height",
    "method",
    "distinct_colors",
    "token_family",
    "slice_input_path",
    "source_kind",
    "expected_layers",
    "total_layers",
    "export_stem",
    "metrics_source",
    "metrics_file",
    "prepare_time_min",
    "model_time_min",
    "total_time_min",
    "filament_change_count",
    "model_filament_g",
    "purged_filament_g",
    "tower_filament_g",
    "total_filament_g",
    "waste_filament_g",
    "waste_fraction",
    "throughput_g_per_hr",
    "time_per_layer_sec",
    "notes",
]


def ensure_dir(path: os.PathLike | str) -> Path:
    out = Path(path)
    out.mkdir(parents=True, exist_ok=True)
    return out


def safe_slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "item"


def layer_height_token(layer_height: float) -> str:
    return f"{layer_height:.2f}".replace(".", "p")


def format_float(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def parse_duration_minutes(raw: str | None) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    total = 0.0
    matched = False
    for num, unit in re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*([hms])", text.lower()):
        matched = True
        val = float(num)
        if unit == "h":
            total += val * 60.0
        elif unit == "m":
            total += val
        elif unit == "s":
            total += val / 60.0
    if matched:
        return total
    if re.fullmatch(r"\d+:\d+:\d+", text):
        h, m, s = [int(x) for x in text.split(":")]
        return h * 60.0 + m + (s / 60.0)
    if re.fullmatch(r"\d+:\d+", text):
        m, s = [int(x) for x in text.split(":")]
        return m + (s / 60.0)
    try:
        return float(text)
    except Exception:
        return None


def ceil_layers(height_mm: float, layer_height_mm: float) -> int:
    return int(math.ceil(float(height_mm) / float(layer_height_mm) - 1e-9))


def token_sequence(token_family: str, count: int = CELL_COUNT) -> List[str]:
    seq: List[str] = []
    family = token_family.strip().lower()
    if not family:
        return seq
    for idx in range(count):
        seq.append(family[idx % len(family)])
    return seq


def write_csv(path: os.PathLike | str, rows: Iterable[Dict[str, object]], fieldnames: List[str]) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def read_csv(path: os.PathLike | str) -> List[Dict[str, str]]:
    with open(path, "r", newline="") as f:
        return list(csv.DictReader(f))
