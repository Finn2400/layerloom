#!/usr/bin/env python3
"""Analyze calibrated individual LayerLoom expanded-gamut triangle photos.

The filename convention for ordinary gamut photos is:

    DSC_####abc.tif

where a, b, c are the bottom-left, top, and bottom-right triangle anchors.
The special CMY baseline files ``cmysimpleDSC_####.tif`` and
``cmyfullDSC_####.tif`` are also supported. Benchy photos are intentionally
excluded; this script only analyzes triangular gamut photographs.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import os
import re
import sys
import tempfile
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Callable, Sequence

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "layerloom_matplotlib_cache"))
warnings.filterwarnings("ignore", message="Mean of empty slice", category=RuntimeWarning)

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.path import Path as MplPath
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
LAYERLOOM_DIR = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(LAYERLOOM_DIR) not in sys.path:
    sys.path.insert(0, str(LAYERLOOM_DIR))

import exact_voronoi_regions_to_3mf as exact_regions  # noqa: E402
import layer_perm_cmy_visualizer as vis  # noqa: E402
from analyze_printed_gamut import (  # noqa: E402
    CORNER_ORDER,
    THEORY_CORNERS,
    _affine_transform_from_corners,
    _analyze_regions,
    _binary_closing,
    _binary_opening,
    _boundary_points_xy,
    _build_color_mask,
    _convex_hull_points,
    _delta_e00,
    _distinguishable_counts,
    _max_area_triangle,
    _pairwise_delta_e00,
    _polygon_area,
    _resize_for_analysis,
    _rgb_to_hsv,
    _rgb_to_lab,
)
PHOTO_PALETTE_HEX = {
    "c": "#3ac8dc",
    "m": "#c31996",
    "y": "#ffdf00",
    "k": "#141414",
    "w": "#f5f5f5",
    "n": "#8a8f92",
    "l": "#c8c9c2",
    "o": "#ff7a1a",
    "v": "#7257ff",
    "g": "#20bf63",
}
PHOTO_PALETTE_NAMES = {
    "c": "cyan",
    "m": "magenta",
    "y": "yellow",
    "k": "black",
    "w": "white",
    "n": "neutral_gray",
    "l": "light_gray",
    "o": "orange",
    "v": "violet",
    "g": "green",
}
VALID_ANCHORS = set(PHOTO_PALETTE_HEX)
LOCAL_TO_ROLE = {"c": "bottom_left", "m": "bottom_right", "y": "top"}
LOCAL_TO_ANCHOR_INDEX = {"c": 0, "y": 1, "m": 2}
ROLE_TO_CORNER = {"bottom_left": "C", "bottom_right": "M", "top": "Y"}
CONDITIONS = {
    "two_color": {"max_height": 6, "max_run": 2, "distinct_lte": 2, "label": "Two-color"},
    "full": {"max_height": 6, "max_run": 5, "distinct_lte": 5, "label": "Full"},
}


def _hex_to_rgb01(hex_code: str) -> np.ndarray:
    s = hex_code.strip().lstrip("#")
    return np.array([int(s[i : i + 2], 16) / 255.0 for i in (0, 2, 4)], dtype=float)


def _rgb01_to_hex(rgb: Sequence[float]) -> str:
    vals = [int(np.clip(float(v), 0.0, 1.0) * 255 + 0.5) for v in rgb[:3]]
    return "#{:02x}{:02x}{:02x}".format(*vals)


def _safe_name(text: object) -> str:
    out = []
    for ch in str(text):
        out.append(ch if ch.isalnum() or ch in ("-", "_") else "_")
    return "".join(out).strip("_") or "item"


def _parse_image_label(path: Path) -> dict[str, object] | None:
    stem = path.stem.lower()
    m = re.match(r"^(?P<prefix>[a-z]+)?dsc_(?P<num>\d+)(?P<suffix>[a-z]+)?$", stem)
    if not m:
        return None
    num = int(m.group("num"))
    prefix = m.group("prefix") or ""
    suffix = m.group("suffix") or ""

    if prefix == "cmysimple":
        return {
            "source_path": str(path),
            "filename": path.name,
            "photo_id": f"DSC_{num}",
            # Historical CMY baseline files are named by palette family, not
            # corner order. Physically they are bottom-left cyan, top yellow,
            # bottom-right magenta.
            "corner_label": "cym",
            "filename_label": "cmy",
            "condition": "two_color",
            "condition_source": "filename_prefix",
            "capture_number": num,
        }
    if prefix == "cmyfull":
        return {
            "source_path": str(path),
            "filename": path.name,
            "photo_id": f"DSC_{num}",
            "corner_label": "cym",
            "filename_label": "cmy",
            "condition": "full",
            "condition_source": "filename_prefix",
            "capture_number": num,
        }

    label = f"{prefix}{suffix}"
    if len(label) != 3 or any(ch not in VALID_ANCHORS for ch in label):
        return None
    if num >= 4640:
        return None
    if num <= 4570:
        condition = "full"
    elif num >= 4574:
        condition = "two_color"
    else:
        return None
    return {
        "source_path": str(path),
        "filename": path.name,
        "photo_id": f"DSC_{num}",
        "corner_label": label,
        "filename_label": label,
        "condition": condition,
        "condition_source": "capture_number",
        "capture_number": num,
    }


def _palette_family(corner_label: str) -> str:
    anchors = set(corner_label)
    cmy = set("cmy")
    neutral = set("kwnl")
    ovg = set("ovg")
    has_neutral = bool(anchors & neutral)
    has_ovg = bool(anchors & ovg)
    if anchors <= cmy:
        return "CMY"
    if has_neutral and has_ovg:
        return "neutral+OVG"
    if has_neutral:
        return "CMY+neutral"
    if has_ovg:
        return "CMY+OVG"
    return "other"


def build_manifest(input_dir: Path) -> pd.DataFrame:
    rows = []
    for path in sorted(input_dir.glob("*.tif")):
        parsed = _parse_image_label(path)
        if parsed is None:
            continue
        corner_label = str(parsed["corner_label"])
        parsed["bottom_left_anchor"] = corner_label[0]
        parsed["top_anchor"] = corner_label[1]
        parsed["bottom_right_anchor"] = corner_label[2]
        parsed["palette_family"] = _palette_family(corner_label)
        parsed["analysis_id"] = f"{parsed['condition']}__{corner_label}__{Path(str(parsed['source_path'])).stem}"
        rows.append(parsed)
    if not rows:
        raise RuntimeError(f"No labeled gamut triangle photos found in {input_dir}")
    return pd.DataFrame(rows).sort_values(["condition", "capture_number", "filename"]).reset_index(drop=True)


def _expected_rgb_for_token(real_token: str) -> np.ndarray:
    rgbs = [_hex_to_rgb01(PHOTO_PALETTE_HEX[ch]) for ch in real_token]
    return np.mean(np.vstack(rgbs), axis=0)


def build_expected_regions(corner_label: str, condition: str) -> tuple[pd.DataFrame, list[list[tuple[float, float]]]]:
    spec = CONDITIONS[condition]
    stacks = exact_regions.build_stacks(spec["max_height"], spec["max_run"], spec["distinct_lte"])
    sites, _, regions = exact_regions.build_regions(stacks)
    rows = []
    for idx, (stack, site, poly) in enumerate(zip(stacks, sites, regions), start=1):
        local_token = "".join(stack).lower()
        real_token = "".join(corner_label[LOCAL_TO_ANCHOR_INDEX[ch]] for ch in local_token)
        expected_rgb = _expected_rgb_for_token(real_token)
        expected_lab = _rgb_to_lab(expected_rgb.reshape(1, 1, 3)).reshape(3)
        n = len(local_token)
        f_bl = local_token.count("c") / n
        f_br = local_token.count("m") / n
        f_top = local_token.count("y") / n
        rows.append(
            {
                "region_index": idx,
                "label": f"{corner_label}_{condition}_{idx:03d}_{real_token}",
                "corner_label": corner_label,
                "condition": condition,
                "local_stack_token": local_token,
                "stack_token": real_token,
                "f_bottom_left": f_bl,
                "f_top": f_top,
                "f_bottom_right": f_br,
                f"f_{corner_label[0]}": f_bl,
                f"f_{corner_label[1]}": f_top,
                f"f_{corner_label[2]}": f_br,
                "site_x": site[0],
                "site_y": site[1],
                "expected_hex": _rgb01_to_hex(expected_rgb),
                "expected_R": expected_rgb[0],
                "expected_G": expected_rgb[1],
                "expected_B": expected_rgb[2],
                "expected_L": expected_lab[0],
                "expected_a": expected_lab[1],
                "expected_b": expected_lab[2],
                "polygon_xy": json.dumps(poly),
                "max_height": spec["max_height"],
                "max_run": spec["max_run"],
                "distinct_lte": spec["distinct_lte"],
                "preset": condition,
            }
        )
    return pd.DataFrame(rows), regions


def _foreground_mask(rgb: np.ndarray) -> np.ndarray:
    hsv = _rgb_to_hsv(rgb)
    lab = _rgb_to_lab(rgb)
    chroma_lab = np.sqrt(lab[:, :, 1] ** 2 + lab[:, :, 2] ** 2)
    h, w = rgb.shape[:2]
    pad = max(8, int(min(h, w) * 0.035))
    border = np.concatenate(
        [
            rgb[:pad].reshape(-1, 3),
            rgb[-pad:].reshape(-1, 3),
            rgb[:, :pad].reshape(-1, 3),
            rgb[:, -pad:].reshape(-1, 3),
        ],
        axis=0,
    )
    bg = np.median(border, axis=0)
    diff = np.linalg.norm(rgb - bg[None, None, :], axis=2)
    luma = rgb.mean(axis=2)
    bg_luma = float(bg.mean())
    mask = (
        ((hsv[:, :, 1] > 0.060) & (hsv[:, :, 2] < 0.985) & (hsv[:, :, 2] > 0.08))
        | ((chroma_lab > 7.0) & (hsv[:, :, 2] < 0.990))
        | ((diff > 0.16) & (luma < bg_luma - 0.035))
        | (luma < bg_luma - 0.22)
    )
    border_px = max(4, int(min(h, w) * 0.01))
    mask[:border_px, :] = False
    mask[-border_px:, :] = False
    mask[:, :border_px] = False
    mask[:, -border_px:] = False
    mask = _binary_closing(mask, 3)
    mask = _binary_opening(mask, 1)
    return np.asarray(mask, dtype=bool)


def _triangle_detection_mask(rgb: np.ndarray, corner_label: str) -> np.ndarray:
    if "w" not in corner_label:
        return _foreground_mask(rgb)

    # White vertices can disappear into the light-box background, so for those
    # images detect only the visibly colored printed regions. This avoids the
    # translucent stand/shadow becoming part of the fitted triangle.
    color_mask = _build_color_mask(
        rgb,
        min_saturation=0.035,
        min_value=0.04,
        max_value=0.995,
        min_chroma=5.0,
        cleanup_radius=2,
    )
    color_mask = _binary_closing(color_mask, 4)
    if int(color_mask.sum()) > 500:
        return np.asarray(color_mask, dtype=bool)
    return _foreground_mask(rgb)


def _sample_rgb_near_vertex(rgb: np.ndarray, vertex: np.ndarray, centroid: np.ndarray, radius: int = 24) -> np.ndarray:
    point = 0.78 * np.asarray(vertex, dtype=float) + 0.22 * np.asarray(centroid, dtype=float)
    x, y = float(point[0]), float(point[1])
    h, w = rgb.shape[:2]
    x0, x1 = max(int(x - radius), 0), min(int(x + radius + 1), w)
    y0, y1 = max(int(y - radius), 0), min(int(y + radius + 1), h)
    patch = rgb[y0:y1, x0:x1]
    if patch.size == 0:
        return np.array([0.0, 0.0, 0.0], dtype=float)
    hsv = _rgb_to_hsv(patch)
    use = (hsv[:, :, 2] < 0.98) | (hsv[:, :, 1] > 0.04)
    if int(use.sum()) >= 10:
        patch = patch[use]
    else:
        patch = patch.reshape(-1, 3)
    return np.median(patch, axis=0)


def _position_role_for_vertices(vertices: np.ndarray) -> dict[str, int]:
    top_idx = int(np.argmin(vertices[:, 1]))
    remaining = [i for i in range(3) if i != top_idx]
    remaining = sorted(remaining, key=lambda i: vertices[i, 0])
    return {"bottom_left": remaining[0], "top": top_idx, "bottom_right": remaining[1]}


def _infer_equilateral_top(bottom_left: np.ndarray, bottom_right: np.ndarray) -> np.ndarray:
    base = np.asarray(bottom_right, dtype=float) - np.asarray(bottom_left, dtype=float)
    midpoint = (np.asarray(bottom_left, dtype=float) + np.asarray(bottom_right, dtype=float)) / 2.0
    upward_perp = np.array([base[1], -base[0]], dtype=float)
    return midpoint + (math.sqrt(3.0) / 2.0) * upward_perp


def _equilateral_third_point(a: np.ndarray, b: np.ndarray, *, role: str) -> np.ndarray:
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    midpoint = (a + b) / 2.0
    vector = b - a
    perp = np.array([-vector[1], vector[0]], dtype=float)
    candidates = [
        midpoint + (math.sqrt(3.0) / 2.0) * perp,
        midpoint - (math.sqrt(3.0) / 2.0) * perp,
    ]
    if role == "bottom_left":
        return min(candidates, key=lambda p: (p[0], -p[1]))
    if role == "bottom_right":
        return max(candidates, key=lambda p: (p[0], p[1]))
    if role == "top":
        return min(candidates, key=lambda p: p[1])
    raise ValueError(f"Unknown triangle role: {role}")


def _fit_top_white_triangle_from_mask(mask: np.ndarray) -> dict[str, np.ndarray] | None:
    rows = []
    for y in np.where(mask.any(axis=1))[0]:
        xs = np.where(mask[y])[0]
        if len(xs) > 10:
            rows.append((float(y), float(xs.min()), float(xs.max()), float(xs.max() - xs.min())))
    if len(rows) < 30:
        return None

    widths = np.array([row[3] for row in rows], dtype=float)
    max_width = float(np.max(widths))
    if max_width <= 0:
        return None

    base_rows = [row for row in rows if row[3] >= 0.97 * max_width]
    if not base_rows:
        base_rows = [max(rows, key=lambda row: row[3])]
    base_y = float(np.median([row[0] for row in base_rows]))

    side_rows = [
        row
        for row in rows
        if 0.15 * max_width <= row[3] <= 0.985 * max_width
        and row[0] <= base_y
    ]
    if len(side_rows) < 20:
        side_rows = [row for row in rows if row[0] <= base_y]
    if len(side_rows) < 10:
        return None

    y = np.array([row[0] for row in side_rows], dtype=float)
    left_x = np.array([row[1] for row in side_rows], dtype=float)
    right_x = np.array([row[2] for row in side_rows], dtype=float)
    left_fit = np.polyfit(y, left_x, 1)
    right_fit = np.polyfit(y, right_x, 1)
    denom = float(left_fit[0] - right_fit[0])
    if abs(denom) < 1e-6:
        return None

    top_y = float((right_fit[1] - left_fit[1]) / denom)
    top_x = float(np.polyval(left_fit, top_y))
    if not np.isfinite(top_x) or not np.isfinite(top_y):
        return None
    # Keep obviously unstable fits from exploding outside the photograph.
    if top_y > base_y - 20 or top_y < -0.25 * mask.shape[0]:
        return None

    return {
        "bottom_left": np.array([float(np.polyval(left_fit, base_y)), base_y], dtype=float),
        "top": np.array([top_x, top_y], dtype=float),
        "bottom_right": np.array([float(np.polyval(right_fit, base_y)), base_y], dtype=float),
    }


def _infer_low_contrast_anchor_points(
    vertices: np.ndarray,
    position_assignment: dict[str, int],
    corner_label: str,
    mask: np.ndarray,
) -> tuple[dict[str, np.ndarray], dict[str, int], dict[str, str]]:
    """Infer white-corner vertices from the visible equilateral triangle geometry.

    White plastic on the white light-box background can make the printed tip
    nearly invisible. In that case a color/contrast contour usually ends at the
    nearest colored boundary rather than the physical triangle vertex. Filename
    labels still define which geometric role is white, so we anchor the visible
    colored vertices by position and reconstruct the missing low-contrast tip.
    """
    role_points = {
        role: np.asarray(vertices[idx], dtype=float).copy()
        for role, idx in position_assignment.items()
    }
    role_vertex_indices = dict(position_assignment)
    inference_notes = {role: "" for role in role_points}
    role_to_anchor = {
        "bottom_left": corner_label[0],
        "top": corner_label[1],
        "bottom_right": corner_label[2],
    }
    low_contrast_roles = [role for role, anchor in role_to_anchor.items() if anchor == "w"]
    if not low_contrast_roles:
        return role_points, role_vertex_indices, inference_notes

    if low_contrast_roles == ["top"]:
        fitted = _fit_top_white_triangle_from_mask(mask)
        if fitted is not None:
            role_points.update(fitted)
            role_vertex_indices["top"] = -1
            inference_notes["top"] = "inferred_from_visible_side_edge_intersection"
            return role_points, role_vertex_indices, inference_notes

    for role in low_contrast_roles:
        if role == "top":
            role_points["top"] = _infer_equilateral_top(role_points["bottom_left"], role_points["bottom_right"])
        elif role == "bottom_left":
            role_points["bottom_left"] = _equilateral_third_point(role_points["top"], role_points["bottom_right"], role=role)
        elif role == "bottom_right":
            role_points["bottom_right"] = _equilateral_third_point(role_points["bottom_left"], role_points["top"], role=role)
        role_vertex_indices[role] = -1
        inference_notes[role] = "inferred_from_visible_equilateral_geometry"

    return role_points, role_vertex_indices, inference_notes


def _registration_from_manual_corners(
    rgb: np.ndarray,
    corner_label: str,
    role_points: dict[str, np.ndarray],
    mask: np.ndarray,
    vertices: np.ndarray,
    *,
    override_source: str,
) -> tuple[dict[str, np.ndarray], pd.DataFrame]:
    roles = ("bottom_left", "top", "bottom_right")
    assigned_points = np.vstack([role_points[role] for role in roles])
    centroid = assigned_points.mean(axis=0)
    sampled_by_role = {
        role: _sample_rgb_near_vertex(rgb, role_points[role], centroid)
        for role in roles
    }
    expected_role_rgb = {
        "bottom_left": _hex_to_rgb01(PHOTO_PALETTE_HEX[corner_label[0]]),
        "top": _hex_to_rgb01(PHOTO_PALETTE_HEX[corner_label[1]]),
        "bottom_right": _hex_to_rgb01(PHOTO_PALETTE_HEX[corner_label[2]]),
    }
    expected_role_lab = {
        role: _rgb_to_lab(rgb_value.reshape(1, 1, 3)).reshape(3)
        for role, rgb_value in expected_role_rgb.items()
    }
    sampled_lab_by_role = {
        role: _rgb_to_lab(sampled_by_role[role].reshape(1, 1, 3)).reshape(3)
        for role in roles
    }
    rows = []
    score = 0.0
    role_index = {"bottom_left": 0, "top": 1, "bottom_right": 2}
    for role in roles:
        xy = np.asarray(role_points[role], dtype=float)
        delta = float(_delta_e00(sampled_lab_by_role[role], expected_role_lab[role])[0])
        score += delta
        rows.append(
            {
                "role": role,
                "local_corner_label": ROLE_TO_CORNER[role],
                "anchor_token": corner_label[role_index[role]],
                "vertex_index": -2,
                "x_px": float(xy[0]),
                "y_px": float(xy[1]),
                "sample_R": float(sampled_by_role[role][0]),
                "sample_G": float(sampled_by_role[role][1]),
                "sample_B": float(sampled_by_role[role][2]),
                "anchor_deltaE00": delta,
                "position_vertex_index": -2,
                "position_disagrees": 0,
                "inferred_geometry": 1,
                "inference_note": f"manual_override:{override_source}",
            }
        )
    reg = pd.DataFrame(rows)
    reg["registration_score_deltaE00_sum"] = score
    reg["mask_pixels"] = int(mask.sum())
    reg["triangle_area_px2"] = float(_polygon_area(assigned_points))
    reg["orientation_permuted"] = 0
    reg["used_white_vertex_inference"] = int("w" in corner_label)
    reg["used_manual_registration_override"] = 1
    corners = {
        ROLE_TO_CORNER[role]: role_points[role]
        for role in roles
    }
    return corners, reg


def detect_registration(
    rgb: np.ndarray,
    corner_label: str,
    manual_corners: dict[str, np.ndarray] | None = None,
    *,
    override_source: str = "",
) -> tuple[dict[str, np.ndarray], pd.DataFrame, np.ndarray, np.ndarray]:
    mask = _triangle_detection_mask(rgb, corner_label)
    points = _boundary_points_xy(mask)
    vertices = _max_area_triangle(points)
    if manual_corners is not None:
        corners, reg = _registration_from_manual_corners(
            rgb,
            corner_label,
            manual_corners,
            mask,
            vertices,
            override_source=override_source or "manual_registration_overrides.csv",
        )
        return corners, reg, mask, vertices

    centroid = vertices.mean(axis=0)
    expected_role_rgb = {
        "bottom_left": _hex_to_rgb01(PHOTO_PALETTE_HEX[corner_label[0]]),
        "top": _hex_to_rgb01(PHOTO_PALETTE_HEX[corner_label[1]]),
        "bottom_right": _hex_to_rgb01(PHOTO_PALETTE_HEX[corner_label[2]]),
    }
    expected_role_lab = {
        role: _rgb_to_lab(rgb_value.reshape(1, 1, 3)).reshape(3)
        for role, rgb_value in expected_role_rgb.items()
    }
    roles = ("bottom_left", "top", "bottom_right")
    position_assignment = _position_role_for_vertices(vertices)
    uses_inferred_geometry = "w" in corner_label

    if uses_inferred_geometry:
        role_points, role_vertex_indices, inference_notes = _infer_low_contrast_anchor_points(
            vertices,
            position_assignment,
            corner_label,
            mask,
        )
        assigned_points = np.vstack([role_points[role] for role in roles])
        assigned_centroid = assigned_points.mean(axis=0)
        sampled_by_role = {
            role: _sample_rgb_near_vertex(rgb, role_points[role], assigned_centroid)
            for role in roles
        }
        sampled_lab_by_role = {
            role: _rgb_to_lab(sampled_by_role[role].reshape(1, 1, 3)).reshape(3)
            for role in roles
        }
        best_score = sum(float(_delta_e00(sampled_lab_by_role[role], expected_role_lab[role])[0]) for role in roles)
    else:
        sampled_rgb = np.vstack([_sample_rgb_near_vertex(rgb, vertex, centroid) for vertex in vertices])
        sampled_lab = _rgb_to_lab(sampled_rgb.reshape(1, 3, 3)).reshape(3, 3)
        best_assignment = None
        best_score = float("inf")
        for perm in itertools.permutations(range(3)):
            score = 0.0
            for role, vertex_idx in zip(roles, perm):
                score += float(_delta_e00(sampled_lab[vertex_idx], expected_role_lab[role])[0])
            if score < best_score:
                best_score = score
                best_assignment = dict(zip(roles, perm))
        assert best_assignment is not None
        role_points = {role: np.asarray(vertices[int(best_assignment[role])], dtype=float) for role in roles}
        role_vertex_indices = {role: int(best_assignment[role]) for role in roles}
        inference_notes = {role: "" for role in roles}
        sampled_by_role = {role: sampled_rgb[int(role_vertex_indices[role])] for role in roles}
        sampled_lab_by_role = {role: sampled_lab[int(role_vertex_indices[role])] for role in roles}

    rows = []
    for role in roles:
        vertex_idx = int(role_vertex_indices[role])
        xy = np.asarray(role_points[role], dtype=float)
        expected_lab = expected_role_lab[role]
        delta = float(_delta_e00(sampled_lab_by_role[role], expected_lab)[0])
        rows.append(
            {
                "role": role,
                "local_corner_label": ROLE_TO_CORNER[role],
                "anchor_token": corner_label[{"bottom_left": 0, "top": 1, "bottom_right": 2}[role]],
                "vertex_index": vertex_idx,
                "x_px": float(xy[0]),
                "y_px": float(xy[1]),
                "sample_R": float(sampled_by_role[role][0]),
                "sample_G": float(sampled_by_role[role][1]),
                "sample_B": float(sampled_by_role[role][2]),
                "anchor_deltaE00": delta,
                "position_vertex_index": int(position_assignment[role]),
                "position_disagrees": int(position_assignment[role] != vertex_idx),
                "inferred_geometry": int(vertex_idx < 0),
                "inference_note": inference_notes[role],
            }
        )
    corners = {
        ROLE_TO_CORNER[role]: role_points[role]
        for role in roles
    }
    reg = pd.DataFrame(rows)
    reg["registration_score_deltaE00_sum"] = best_score
    reg["mask_pixels"] = int(mask.sum())
    reg["triangle_area_px2"] = float(_polygon_area(vertices))
    reg["orientation_permuted"] = int(any(position_assignment[role] != role_vertex_indices[role] for role in roles if role_vertex_indices[role] >= 0))
    reg["used_white_vertex_inference"] = int(uses_inferred_geometry)
    reg["used_manual_registration_override"] = 0
    return corners, reg, mask, vertices


def _save_rgb_png(rgb: np.ndarray, out_path: Path) -> None:
    Image.fromarray(np.clip(rgb * 255, 0, 255).astype(np.uint8), mode="RGB").save(out_path)


def _plot_registration_qc(
    rgb: np.ndarray,
    corners: dict[str, np.ndarray],
    reg: pd.DataFrame,
    mask: np.ndarray,
    regions_img: Sequence[np.ndarray],
    out_path: Path,
    title: str,
) -> None:
    fig, ax = plt.subplots(figsize=(8, 6))
    overlay = rgb.copy()
    overlay[mask] = 0.72 * overlay[mask] + 0.28 * np.array([0.0, 0.85, 1.0])
    ax.imshow(overlay)
    pts = np.vstack([corners[label] for label in CORNER_ORDER])
    closed = np.vstack([pts, pts[0]])
    ax.plot(closed[:, 0], closed[:, 1], color="white", lw=3)
    ax.plot(closed[:, 0], closed[:, 1], color="black", lw=1)
    for poly in regions_img:
        arr = np.asarray(poly, dtype=float)
        arr = np.vstack([arr, arr[0]])
        ax.plot(arr[:, 0], arr[:, 1], color="white", lw=0.45, alpha=0.72)
        ax.plot(arr[:, 0], arr[:, 1], color="black", lw=0.16, alpha=0.45)
    color_for_corner = {"C": "#00d0ff", "Y": "#ffea00", "M": "#ff00a8"}
    for row in reg.itertuples(index=False):
        xy = np.array([row.x_px, row.y_px], dtype=float)
        corner = str(row.local_corner_label)
        ax.scatter([xy[0]], [xy[1]], s=90, color=color_for_corner[corner], edgecolor="black", lw=0.8)
        ax.text(
            xy[0],
            xy[1],
            f" {row.role}:{row.anchor_token}",
            ha="left",
            va="center",
            fontsize=8,
            color="black",
            bbox=dict(facecolor="white", edgecolor="black", alpha=0.74, pad=1),
        )
    ax.set_title(title, fontsize=9)
    ax.axis("off")
    fig.tight_layout(pad=0.2)
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def _plot_sampling_overlay(rgb: np.ndarray, label_image: np.ndarray, measured: pd.DataFrame, out_path: Path, title: str) -> None:
    overlay = rgb.copy() * 0.66 + 0.34
    colored = np.zeros_like(rgb)
    for row in measured.itertuples(index=False):
        color = np.array([row.measured_R, row.measured_G, row.measured_B], dtype=float)
        colored[label_image == int(row.region_index)] = color
    mask = label_image > 0
    overlay[mask] = 0.30 * overlay[mask] + 0.70 * colored[mask]
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.imshow(overlay)
    ax.set_title(title, fontsize=9)
    ax.axis("off")
    fig.tight_layout(pad=0.2)
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def _short_qc_label(path: Path) -> str:
    stem = path.stem
    stem = stem.replace("__registration_qc", "").replace("__sampling_qc", "")
    m = re.match(r"^(?P<condition>two_color|full)__(?P<label>[a-z]{3})__(?P<photo>.+)$", stem)
    if not m:
        return stem[:44]
    photo = m.group("photo")
    photo = re.sub(r"^DSC_(\d+).*", r"DSC_\1", photo)
    photo = re.sub(r"^([a-z]{3})DSC_(\d+).*", r"DSC_\2", photo)
    condition = "two" if m.group("condition") == "two_color" else "full"
    return f"{condition} {m.group('label')} {photo}"


def _make_contact_sheet(image_paths: Sequence[Path], out_path: Path, *, thumb_w: int = 260, cols: int = 5) -> None:
    if not image_paths:
        return
    thumbs = []
    for path in image_paths:
        im = Image.open(path).convert("RGB")
        h = max(1, round(im.height * thumb_w / im.width))
        im = im.resize((thumb_w, h), Image.Resampling.LANCZOS)
        thumbs.append((path.name, im))
    label_h = 34
    cell_h = max(im.height for _, im in thumbs) + label_h
    rows = math.ceil(len(thumbs) / cols)
    sheet = Image.new("RGB", (cols * thumb_w, rows * cell_h), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (name, im) in enumerate(thumbs):
        x = (i % cols) * thumb_w
        y = (i // cols) * cell_h
        draw.text((x + 5, y + 5), _short_qc_label(Path(name)), fill=(0, 0, 0))
        sheet.paste(im, (x, y + label_h))
    sheet.save(out_path)


def _load_image(path: Path, max_dim: int) -> tuple[np.ndarray, float]:
    rgb0 = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32) / 255.0
    return _resize_for_analysis(rgb0, max_dim)


def _load_manual_overrides(path: Path | None) -> dict[str, dict[str, object]]:
    if path is None or not path.exists():
        return {}
    df = pd.read_csv(path)
    if df.empty:
        return {}
    required = {
        "bottom_left_x",
        "bottom_left_y",
        "top_x",
        "top_y",
        "bottom_right_x",
        "bottom_right_y",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Manual override file is missing required columns: {sorted(missing)}")
    overrides: dict[str, dict[str, object]] = {}
    key_columns = [col for col in ("analysis_id", "filename") if col in df.columns]
    if not key_columns:
        raise ValueError("Manual override file must include at least one of: analysis_id, filename")
    for row in df.itertuples(index=False):
        role_points = {
            "bottom_left": np.array([float(row.bottom_left_x), float(row.bottom_left_y)], dtype=float),
            "top": np.array([float(row.top_x), float(row.top_y)], dtype=float),
            "bottom_right": np.array([float(row.bottom_right_x), float(row.bottom_right_y)], dtype=float),
        }
        entry = {
            "points": role_points,
            "image_width": float(getattr(row, "image_width", np.nan)),
            "image_height": float(getattr(row, "image_height", np.nan)),
        }
        for key_col in key_columns:
            key_value = getattr(row, key_col, "")
            if isinstance(key_value, str) and key_value:
                overrides[key_value] = entry
    return overrides


def _manual_override_for_row(
    row: pd.Series,
    overrides: dict[str, dict[str, object]],
    image_shape: tuple[int, int],
) -> tuple[dict[str, np.ndarray] | None, str]:
    for key in (str(row.analysis_id), str(row.filename)):
        if key in overrides:
            entry = overrides[key]
            points = entry.get("points", entry)
            assert isinstance(points, dict)
            scaled = {role: np.asarray(points[role], dtype=float).copy() for role in ("bottom_left", "top", "bottom_right")}
            original_w = float(entry.get("image_width", np.nan)) if isinstance(entry, dict) else float("nan")
            original_h = float(entry.get("image_height", np.nan)) if isinstance(entry, dict) else float("nan")
            current_h, current_w = image_shape
            if np.isfinite(original_w) and np.isfinite(original_h) and original_w > 0 and original_h > 0:
                sx = current_w / original_w
                sy = current_h / original_h
                if abs(sx - 1.0) > 1e-6 or abs(sy - 1.0) > 1e-6:
                    for role in scaled:
                        scaled[role] = scaled[role] * np.array([sx, sy], dtype=float)
            return scaled, key
    return None, ""


def analyze_one(row: pd.Series, dirs: dict[str, Path], args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame, Path]:
    image_path = Path(str(row.source_path))
    rgb, scale = _load_image(image_path, args.max_analysis_dim)
    lab = _rgb_to_lab(rgb)
    manual_corners, override_source = _manual_override_for_row(
        row,
        getattr(args, "manual_registration_overrides", {}),
        rgb.shape[:2],
    )
    corners, reg, mask, _vertices = detect_registration(
        rgb,
        str(row.corner_label),
        manual_corners=manual_corners,
        override_source=override_source,
    )
    transform = _affine_transform_from_corners(corners)
    expected, regions = build_expected_regions(str(row.corner_label), str(row.condition))
    regions_img = [transform(np.asarray(poly, dtype=float)) for poly in regions]
    color_mask = _build_color_mask(
        rgb,
        min_saturation=0.035,
        min_value=0.04,
        max_value=0.995,
        min_chroma=4.0,
        cleanup_radius=1,
    )
    measured, label_image, transformed_regions = _analyze_regions(
        rgb,
        lab,
        color_mask,
        expected,
        regions,
        transform,
        erosion_px=args.erosion_px,
        min_region_pixels=args.min_region_pixels,
        max_pixels_per_region=args.max_pixels_per_region,
        sample_core_fraction=args.sample_core_fraction,
        lab_trim_percentile=args.lab_trim_percentile,
        random_seed=args.random_seed + int(row.capture_number),
    )
    for key, value in row.to_dict().items():
        measured[key] = value
        reg[key] = value
    measured["analysis_scale"] = scale
    reg["analysis_scale"] = scale
    stem = _safe_name(str(row.analysis_id))
    expected.to_csv(dirs["per_photo"] / f"{stem}__expected_regions.csv", index=False)
    measured.to_csv(dirs["per_photo"] / f"{stem}__measured_regions.csv", index=False)
    reg.to_csv(dirs["per_photo"] / f"{stem}__registration.csv", index=False)
    _save_rgb_png(rgb, dirs["per_photo"] / f"{stem}__analysis_input.png")
    registration_qc_path = dirs["qc"] / f"{stem}__registration_qc.png"
    _plot_registration_qc(
        rgb,
        corners,
        reg,
        mask,
        transformed_regions,
        registration_qc_path,
        title=f"{row.condition} {row.corner_label} {row.filename}",
    )
    _plot_sampling_overlay(
        rgb,
        label_image,
        measured,
        dirs["qc"] / f"{stem}__sampling_qc.png",
        title=f"{row.condition} {row.corner_label} sampled interiors",
    )
    return measured, reg, registration_qc_path


def _collapse_replicates(all_measured: pd.DataFrame) -> pd.DataFrame:
    group_cols = ["condition", "corner_label", "region_index", "local_stack_token", "stack_token", "palette_family"]
    numeric_cols = [
        col
        for col in all_measured.columns
        if pd.api.types.is_numeric_dtype(all_measured[col])
        and col not in {"capture_number", "region_index"}
    ]
    rows = []
    for key, group in all_measured.groupby(group_cols, dropna=False):
        row = dict(zip(group_cols, key))
        row["n_replicates"] = int(len(group))
        row["source_files"] = ";".join(sorted(group["filename"].astype(str).unique()))
        for col in numeric_cols:
            row[col] = float(group[col].median())
        row["region_index"] = int(key[2])
        rows.append(row)
    return pd.DataFrame(rows)


def _repeatability(all_measured: pd.DataFrame) -> pd.DataFrame:
    rows = []
    key_cols = ["condition", "corner_label", "region_index"]
    for key, group in all_measured.groupby(key_cols):
        if len(group) < 2:
            continue
        labs = group[["measured_L", "measured_a", "measured_b"]].to_numpy(dtype=float)
        files = group["filename"].astype(str).tolist()
        for i in range(len(group) - 1):
            for j in range(i + 1, len(group)):
                rows.append(
                    {
                        "condition": key[0],
                        "corner_label": key[1],
                        "region_index": key[2],
                        "file_i": files[i],
                        "file_j": files[j],
                        "deltaE00": float(_delta_e00(labs[i], labs[j])[0]),
                    }
                )
    return pd.DataFrame(rows)


def _safe_hull_area(df: pd.DataFrame) -> float:
    if len(df) < 3:
        return float("nan")
    points = df[["measured_a", "measured_b"]].to_numpy(dtype=float)
    return float(_polygon_area(_convex_hull_points(points)))


def _nearest_neighbor_coverage_metrics(
    df: pd.DataFrame,
    *,
    grid_step: float = 2.0,
    chunk_size: int = 4096,
) -> dict[str, float]:
    """Quantify how densely measured colors populate their own a*b* hull.

    Convex-hull area measures outer chromatic extent. This grid-based metric
    samples the interior of that hull and asks how far each sampled location is
    from the nearest measured printed color in CIE a*b* coordinates. Smaller
    nearest-neighbor distances indicate denser coverage within the same extent.
    """
    if len(df) < 3:
        return {
            "coverage_grid_step_ab": grid_step,
            "coverage_grid_points": 0,
            "median_nearest_ab_distance": float("nan"),
            "mean_nearest_ab_distance": float("nan"),
            "p90_nearest_ab_distance": float("nan"),
            "max_nearest_ab_distance": float("nan"),
        }
    points = df[["measured_a", "measured_b"]].to_numpy(dtype=float)
    hull = _convex_hull_points(points)
    if len(hull) < 3 or _polygon_area(hull) <= 0:
        return {
            "coverage_grid_step_ab": grid_step,
            "coverage_grid_points": 0,
            "median_nearest_ab_distance": float("nan"),
            "mean_nearest_ab_distance": float("nan"),
            "p90_nearest_ab_distance": float("nan"),
            "max_nearest_ab_distance": float("nan"),
        }
    min_a, min_b = np.floor(hull.min(axis=0) / grid_step) * grid_step
    max_a, max_b = np.ceil(hull.max(axis=0) / grid_step) * grid_step
    aa = np.arange(min_a, max_a + grid_step * 0.5, grid_step)
    bb = np.arange(min_b, max_b + grid_step * 0.5, grid_step)
    grid_a, grid_b = np.meshgrid(aa, bb)
    grid = np.column_stack([grid_a.ravel(), grid_b.ravel()])
    inside = MplPath(hull).contains_points(grid, radius=1e-9)
    grid = grid[inside]
    if len(grid) == 0:
        return {
            "coverage_grid_step_ab": grid_step,
            "coverage_grid_points": 0,
            "median_nearest_ab_distance": float("nan"),
            "mean_nearest_ab_distance": float("nan"),
            "p90_nearest_ab_distance": float("nan"),
            "max_nearest_ab_distance": float("nan"),
        }

    nearest_sq = np.empty(len(grid), dtype=float)
    for start in range(0, len(grid), chunk_size):
        chunk = grid[start : start + chunk_size]
        diff = chunk[:, None, :] - points[None, :, :]
        nearest_sq[start : start + len(chunk)] = np.min(np.sum(diff * diff, axis=2), axis=1)
    nearest = np.sqrt(nearest_sq)
    return {
        "coverage_grid_step_ab": grid_step,
        "coverage_grid_points": int(len(grid)),
        "median_nearest_ab_distance": float(np.median(nearest)),
        "mean_nearest_ab_distance": float(np.mean(nearest)),
        "p90_nearest_ab_distance": float(np.percentile(nearest, 90)),
        "max_nearest_ab_distance": float(np.max(nearest)),
    }


def _summary_tables(collapsed: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_rows = []
    count_rows = []

    def add_group(condition: str, family: str, group: pd.DataFrame) -> None:
        labels = [
            f"{condition}:{family}:{row.corner_label}:{row.region_index}:{row.stack_token}"
            for row in group.itertuples(index=False)
        ]
        pairwise = _pairwise_delta_e00(group[["measured_L", "measured_a", "measured_b"]].to_numpy(dtype=float), labels)
        distinguishable = _distinguishable_counts(pairwise, labels)
        for drow in distinguishable.itertuples(index=False):
            count_rows.append(
                {
                    "condition": condition,
                    "palette_family": family,
                    "threshold_deltaE00": drow.threshold_deltaE00,
                    "distinguishable_count": drow.distinguishable_color_count,
                    "n_colors": drow.theoretical_region_count,
                    "merged_region_count": drow.merged_region_count,
                }
            )
        summary_row = {
            "condition": condition,
            "palette_family": family,
            "n_regions": int(len(group)),
            "n_corner_labels": int(group["corner_label"].nunique()),
            "measured_ab_hull_area": _safe_hull_area(group),
            "median_deltaE00_to_expected": float(group["deltaE00_to_expected"].median()),
            "median_within_region_deltaE00": float(group["within_region_deltaE00_median"].median()),
        }
        summary_row.update(_nearest_neighbor_coverage_metrics(group))
        summary_rows.append(summary_row)

    for (condition, family), group in collapsed.groupby(["condition", "palette_family"], dropna=False):
        add_group(str(condition), str(family), group)
    for condition, group in collapsed.groupby("condition", dropna=False):
        add_group(str(condition), "CMY+neutral+OVG", group)
    return pd.DataFrame(summary_rows), pd.DataFrame(count_rows)


def _plot_hulls(collapsed: pd.DataFrame, out_path: Path, *, title: str, group_col: str) -> None:
    if collapsed.empty:
        fig, ax = plt.subplots(figsize=(5.2, 4.8))
        ax.text(0.5, 0.5, "No data selected", ha="center", va="center")
        ax.set_axis_off()
        fig.suptitle(title)
        fig.tight_layout()
        fig.savefig(out_path, dpi=300)
        plt.close(fig)
        return
    groups = list(collapsed.groupby(group_col, dropna=False))
    if group_col == "palette_family" and collapsed[group_col].nunique(dropna=False) > 1:
        groups.append(("CMY+neutral+OVG", collapsed))
    n = len(groups)
    cols = min(4, max(1, n))
    rows = math.ceil(n / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(5.2 * cols, 4.8 * rows), squeeze=False)
    for ax in axes.ravel():
        ax.axis("off")
    display_names = {
        "CMY+neutral+OVG": "CMY+OVG+neutral",
    }
    for ax, (name, group) in zip(axes.ravel(), groups):
        ax.axis("on")
        rgb = group[["measured_R", "measured_G", "measured_B"]].to_numpy(dtype=float)
        pts = group[["measured_a", "measured_b"]].to_numpy(dtype=float)
        ax.scatter(pts[:, 0], pts[:, 1], c=np.clip(rgb, 0, 1), s=18, edgecolor="0.2", linewidth=0.2)
        hull = _convex_hull_points(pts)
        if len(hull) >= 3:
            closed = np.vstack([hull, hull[0]])
            ax.plot(closed[:, 0], closed[:, 1], color="black", lw=1.4)
        ax.axhline(0, color="0.88", lw=0.8)
        ax.axvline(0, color="0.88", lw=0.8)
        ax.set_aspect("equal", adjustable="box")
        display_name = display_names.get(str(name), str(name))
        ax.set_title(f"{display_name} (N={len(group)}, hull={_safe_hull_area(group):.0f})")
        ax.set_xlabel("a*")
        ax.set_ylabel("b*")
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(out_path, dpi=300)
    plt.close(fig)


def _plot_condition_overlay(collapsed: pd.DataFrame, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 7))
    colors = {"two_color": "#2a9d8f", "full": "#d95f02"}
    for condition, group in collapsed.groupby("condition"):
        pts = group[["measured_a", "measured_b"]].to_numpy(dtype=float)
        ax.scatter(pts[:, 0], pts[:, 1], s=12, alpha=0.35, color=colors.get(condition, "0.4"), label=f"{condition} points")
        hull = _convex_hull_points(pts)
        if len(hull) >= 3:
            closed = np.vstack([hull, hull[0]])
            ax.plot(closed[:, 0], closed[:, 1], color=colors.get(condition, "0.4"), lw=2.2, label=f"{condition} hull")
    ax.axhline(0, color="0.88", lw=0.8)
    ax.axvline(0, color="0.88", lw=0.8)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("a*")
    ax.set_ylabel("b*")
    ax.set_title("Measured expanded gamut: two-color vs full layouts")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=300)
    plt.close(fig)


def _plot_cmy_vs_expanded(collapsed: pd.DataFrame, out_path: Path) -> None:
    conditions = [c for c in ("two_color", "full") if c in set(collapsed["condition"])]
    fig, axes = plt.subplots(1, len(conditions), figsize=(7.2 * max(1, len(conditions)), 6.4), squeeze=False)
    for ax, condition in zip(axes.ravel(), conditions):
        subset = collapsed[collapsed["condition"] == condition]
        groups = {
            "CMY only": subset[subset["palette_family"] == "CMY"],
            "Expanded measured set": subset,
        }
        hull_colors = {"CMY only": "#222222", "Expanded measured set": "#d95f02"}
        for label, group in groups.items():
            if group.empty:
                continue
            pts = group[["measured_a", "measured_b"]].to_numpy(dtype=float)
            if label == "Expanded measured set":
                rgb = group[["measured_R", "measured_G", "measured_B"]].to_numpy(dtype=float)
                ax.scatter(pts[:, 0], pts[:, 1], c=np.clip(rgb, 0, 1), s=10, alpha=0.38, edgecolor="none")
            hull = _convex_hull_points(pts)
            if len(hull) >= 3:
                closed = np.vstack([hull, hull[0]])
                ax.plot(closed[:, 0], closed[:, 1], color=hull_colors[label], lw=2.0, label=f"{label} hull")
        ax.axhline(0, color="0.88", lw=0.8)
        ax.axvline(0, color="0.88", lw=0.8)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlabel("a*")
        ax.set_ylabel("b*")
        ax.set_title(f"{CONDITIONS[condition]['label']} condition")
        ax.legend(fontsize=8)
    fig.suptitle("Measured CIE a*b* hulls: CMY-only vs expanded photo set")
    fig.tight_layout()
    fig.savefig(out_path, dpi=300)
    plt.close(fig)


COMPARISON_ORDER = [
    ("two_color", "CMY"),
    ("full", "CMY"),
    ("two_color", "CMY+neutral"),
    ("full", "CMY+neutral"),
    ("two_color", "CMY+OVG"),
    ("full", "CMY+OVG"),
    ("two_color", "CMY+neutral+OVG"),
    ("full", "CMY+neutral+OVG"),
]


def _comparison_rank(condition: object, family: object) -> int:
    key = (str(condition), str(family))
    if key in COMPARISON_ORDER:
        return COMPARISON_ORDER.index(key)
    return len(COMPARISON_ORDER)


def _comparison_label(condition: object, family: object) -> str:
    condition_label = "two-color" if str(condition) == "two_color" else str(condition)
    family_label = {
        "CMY": "CMY",
        "CMY+neutral": "CMY + neutral",
        "CMY+OVG": "CMY + OVG",
        "CMY+neutral+OVG": "CMY + OVG + neutral",
    }.get(str(family), str(family).replace("+", " + "))
    return f"{family_label} {condition_label}"


def _ordered_summary(summary: pd.DataFrame) -> pd.DataFrame:
    data = summary.copy()
    data["_comparison_rank"] = [
        _comparison_rank(row.condition, row.palette_family)
        for row in data.itertuples(index=False)
    ]
    data = data.sort_values(["_comparison_rank", "condition", "palette_family"]).reset_index(drop=True)
    return data.drop(columns=["_comparison_rank"])


def _plot_summary_bars(summary: pd.DataFrame, counts: pd.DataFrame, out_dir: Path) -> None:

    fig, ax = plt.subplots(figsize=(8.8, 5.4))
    data = _ordered_summary(summary)
    labels = [_comparison_label(r.condition, r.palette_family) for r in data.itertuples(index=False)]
    y = np.arange(len(data))
    ax.barh(y, data["measured_ab_hull_area"], color="#4c78a8")
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("Measured a*b* hull area")
    ax.set_title("Measured chromatic hull area by palette family")
    ax.grid(axis="x", color="0.9", linewidth=0.8)
    fig.tight_layout()
    fig.savefig(out_dir / "04_palette_family_hull_area_bars.png", dpi=300)
    plt.close(fig)

    pivot = counts.pivot_table(
        index=["condition", "palette_family"],
        columns="threshold_deltaE00",
        values="distinguishable_count",
        aggfunc="first",
    ).reset_index()
    for threshold in (2.0, 5.0, 10.0):
        if threshold not in pivot.columns:
            pivot[threshold] = 0
    pivot["_comparison_rank"] = [
        _comparison_rank(row.condition, row.palette_family)
        for row in pivot.itertuples(index=False)
    ]
    pivot = pivot.sort_values(["_comparison_rank", "condition", "palette_family"]).reset_index(drop=True)
    labels = [_comparison_label(r.condition, r.palette_family) for r in pivot.itertuples(index=False)]
    base_10 = pivot[10.0].to_numpy(dtype=float)
    extra_5 = np.maximum(pivot[5.0].to_numpy(dtype=float) - base_10, 0)
    extra_2 = np.maximum(pivot[2.0].to_numpy(dtype=float) - pivot[5.0].to_numpy(dtype=float), 0)

    fig, ax = plt.subplots(figsize=(9.4, 5.8))
    y = np.arange(len(pivot))
    ax.barh(y, base_10, color="#9ecae1", label="distinguishable at ΔE00 < 10")
    ax.barh(y, extra_5, left=base_10, color="#4292c6", label="additional at ΔE00 < 5")
    ax.barh(y, extra_2, left=base_10 + extra_5, color="#084594", label="additional at ΔE00 < 2")
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("Effective distinguishable color count")
    ax.set_title("Distinguishable measured colors by ΔE00 threshold")
    ax.grid(axis="x", color="0.9", linewidth=0.8)
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_dir / "05_distinguishable_color_counts.png", dpi=300)
    plt.close(fig)


def _plot_coverage_metrics(summary: pd.DataFrame, out_dir: Path) -> None:
    data = _ordered_summary(summary)
    data["label"] = [_comparison_label(r.condition, r.palette_family) for r in data.itertuples(index=False)]

    fig, ax = plt.subplots(figsize=(8.8, 5.4))
    y = np.arange(len(data))
    ax.barh(y, data["p90_nearest_ab_distance"], color="#e07a5f")
    ax.set_yticks(y)
    ax.set_yticklabels(data["label"])
    ax.invert_yaxis()
    ax.set_xlabel("90th percentile nearest-color distance in a*b*")
    ax.set_title("Coverage density within measured convex hull")
    ax.grid(axis="x", color="0.9", linewidth=0.8)
    fig.tight_layout()
    fig.savefig(out_dir / "07_nearest_neighbor_coverage_distance.png", dpi=300)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.2, 5.8))
    condition_colors = {"full": "#4c78a8", "two_color": "#59a14f"}
    family_markers = {
        "CMY": "o",
        "CMY+OVG": "s",
        "CMY+neutral": "^",
        "CMY+neutral+OVG": "D",
    }
    for row in data.itertuples(index=False):
        color = condition_colors.get(str(row.condition), "0.35")
        marker = family_markers.get(str(row.palette_family), "o")
        ax.scatter(
            row.measured_ab_hull_area,
            row.p90_nearest_ab_distance,
            s=95,
            color=color,
            marker=marker,
            edgecolor="black",
            linewidth=0.6,
        )
        ax.text(
            row.measured_ab_hull_area,
            row.p90_nearest_ab_distance,
            f" {_comparison_label(row.condition, row.palette_family)}",
            fontsize=7,
            va="center",
        )
    ax.set_xlabel("Measured a*b* convex-hull area")
    ax.set_ylabel("90th percentile nearest-color distance in a*b*")
    ax.set_title("Gamut extent versus within-hull coverage density")
    ax.grid(color="0.9", linewidth=0.8)
    fig.tight_layout()
    fig.savefig(out_dir / "08_hull_area_vs_coverage_density.png", dpi=300)
    plt.close(fig)


def _max_run_length(token: Sequence[str]) -> int:
    if not token:
        return 0
    longest = 1
    current = 1
    previous = token[0]
    for ch in token[1:]:
        if ch == previous:
            current += 1
        else:
            longest = max(longest, current)
            current = 1
            previous = ch
    return max(longest, current)


def _fraction_signature(token: Sequence[str], alphabet: Sequence[str]) -> tuple[tuple[str, int, int], ...]:
    counts = {ch: int(token.count(ch)) for ch in alphabet}
    used = [count for count in counts.values() if count > 0]
    if not used:
        return tuple()
    divisor = len(token)
    for count in used:
        divisor = math.gcd(divisor, count)
    denominator = len(token) // divisor
    return tuple(
        (ch, count // divisor, denominator)
        for ch, count in counts.items()
        if count > 0
    )


def _theoretical_recipe_count(
    alphabet: str,
    *,
    max_height: int,
    max_run: int,
    distinct_lte: int,
) -> int:
    """Count unique layer-fraction recipes for analysis-only alphabets.

    This intentionally does not call LayerLoom's core token registry, because the
    photo analysis uses ``l`` as a light-gray anchor without making it a public
    weaving token.
    """
    alphabet_tuple = tuple(alphabet)
    signatures: set[tuple[tuple[str, int, int], ...]] = set()
    for height in range(1, max_height + 1):
        for token in itertools.product(alphabet_tuple, repeat=height):
            if len(set(token)) > distinct_lte:
                continue
            if _max_run_length(token) > max_run:
                continue
            signatures.add(_fraction_signature(token, alphabet_tuple))
    return len(signatures)


def _write_recipe_count_table(out_path: Path) -> None:
    rows = []
    alphabets = {
        "CMY": "cmy",
        "CMY+neutral": "cmykwnl",
        "CMY+OVG": "cmyovg",
        "CMY+neutral+OVG": "cmykwnlovg",
    }
    for condition, spec in CONDITIONS.items():
        for family, alphabet in alphabets.items():
            recipe_count = _theoretical_recipe_count(
                alphabet,
                max_height=spec["max_height"],
                max_run=spec["max_run"],
                distinct_lte=spec["distinct_lte"],
            )
            rows.append(
                {
                    "condition": condition,
                    "palette_family": family,
                    "alphabet": alphabet,
                    "theoretical_recipe_count": recipe_count,
                    "max_height": spec["max_height"],
                    "max_run": spec["max_run"],
                    "distinct_lte": spec["distinct_lte"],
                }
            )
    pd.DataFrame(rows).to_csv(out_path, index=False)


def _ensure_dirs(out_dir: Path) -> dict[str, Path]:
    dirs = {
        "root": out_dir,
        "tables": out_dir / "tables",
        "figures": out_dir / "figures",
        "qc": out_dir / "qc",
        "per_photo": out_dir / "per_photo",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze expanded-gamut triangle photos.")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=LAYERLOOM_DIR / "LayerLoomPhotos100NCZ_5" / "centered_4x3_tif",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=LAYERLOOM_DIR / "LayerLoomPhotos100NCZ_5" / "expanded_gamut_analysis",
    )
    parser.add_argument("--max-analysis-dim", type=int, default=1800)
    parser.add_argument("--sample-core-fraction", type=float, default=0.65)
    parser.add_argument("--lab-trim-percentile", type=float, default=98.0)
    parser.add_argument("--erosion-px", type=int, default=8)
    parser.add_argument("--min-region-pixels", type=int, default=40)
    parser.add_argument("--max-pixels-per-region", type=int, default=12000)
    parser.add_argument("--random-seed", type=int, default=1729)
    parser.add_argument("--limit", type=int, default=0, help="Optional first-N manifest rows for validation.")
    parser.add_argument("--labels", type=str, default="", help="Optional comma-separated corner labels to include.")
    parser.add_argument("--conditions", type=str, default="", help="Optional comma-separated conditions to include.")
    parser.add_argument(
        "--corner-overrides",
        type=Path,
        default=None,
        help=(
            "Optional CSV of manual registration corners in analysis-image pixel "
            "coordinates. Columns: filename or analysis_id, bottom_left_x, "
            "bottom_left_y, top_x, top_y, bottom_right_x, bottom_right_y."
        ),
    )
    args = parser.parse_args(argv)

    dirs = _ensure_dirs(args.out_dir)
    override_path = args.corner_overrides
    if override_path is None:
        default_override = args.out_dir / "manual_registration_overrides.csv"
        if default_override.exists():
            override_path = default_override
    args.manual_registration_overrides = _load_manual_overrides(override_path)
    if args.manual_registration_overrides:
        print(f"[overrides] loaded {len(args.manual_registration_overrides)} registration override key(s) from {override_path}")
    manifest = build_manifest(args.input_dir)
    if args.labels.strip():
        wanted = {x.strip().lower() for x in args.labels.split(",") if x.strip()}
        manifest = manifest[manifest["corner_label"].isin(wanted)].copy()
    if args.conditions.strip():
        wanted_conditions = {x.strip().lower() for x in args.conditions.split(",") if x.strip()}
        manifest = manifest[manifest["condition"].isin(wanted_conditions)].copy()
    if args.limit > 0:
        manifest = manifest.head(args.limit).copy()
    if manifest.empty:
        raise RuntimeError("No manifest rows selected for analysis.")
    manifest.to_csv(dirs["tables"] / "expanded_gamut_manifest.csv", index=False)

    measured_parts = []
    reg_parts = []
    registration_qc_files = []
    for row in manifest.itertuples(index=False):
        measured, reg, registration_qc_path = analyze_one(pd.Series(row._asdict()), dirs, args)
        measured_parts.append(measured)
        reg_parts.append(reg)
        registration_qc_files.append(registration_qc_path)
        print(f"[analyzed] {row.condition} {row.corner_label} {row.filename}: {len(measured)} regions")

    all_measured = pd.concat(measured_parts, ignore_index=True)
    all_reg = pd.concat(reg_parts, ignore_index=True)
    collapsed = _collapse_replicates(all_measured)
    repeatability = _repeatability(all_measured)
    summary, counts = _summary_tables(collapsed)

    all_measured.to_csv(dirs["tables"] / "all_measured_regions.csv", index=False)
    all_reg.to_csv(dirs["tables"] / "registration_summary.csv", index=False)
    collapsed.to_csv(dirs["tables"] / "collapsed_region_medians.csv", index=False)
    repeatability.to_csv(dirs["tables"] / "duplicate_repeatability.csv", index=False)
    summary.to_csv(dirs["tables"] / "condition_family_summary.csv", index=False)
    counts.to_csv(dirs["tables"] / "distinguishable_counts.csv", index=False)
    _write_recipe_count_table(dirs["tables"] / "theoretical_recipe_counts.csv")

    _make_contact_sheet(registration_qc_files, dirs["figures"] / "01_registration_qc_contact_sheet.png", thumb_w=250, cols=5)
    _plot_hulls(collapsed[collapsed["condition"] == "two_color"], dirs["figures"] / "02_measured_ab_hulls_two_color.png", title="Two-color measured a*b* hulls", group_col="palette_family")
    _plot_hulls(collapsed[collapsed["condition"] == "full"], dirs["figures"] / "03_measured_ab_hulls_full.png", title="Full-condition measured a*b* hulls", group_col="palette_family")
    _plot_condition_overlay(collapsed, dirs["figures"] / "03b_two_color_vs_full_overlay.png")
    _plot_summary_bars(summary, counts, dirs["figures"])
    _plot_cmy_vs_expanded(collapsed, dirs["figures"] / "06_cmy_vs_expanded_ab_hulls.png")
    _plot_coverage_metrics(summary, dirs["figures"])

    config = {
        key: (str(value) if isinstance(value, Path) else value)
        for key, value in vars(args).items()
        if key != "manual_registration_overrides"
    }
    config["input_dir"] = str(args.input_dir)
    config["out_dir"] = str(args.out_dir)
    config["corner_overrides"] = str(override_path) if override_path is not None else ""
    config["n_manual_registration_override_keys"] = int(len(args.manual_registration_overrides))
    config["n_manifest_rows"] = int(len(manifest))
    config["n_measured_rows"] = int(len(all_measured))
    (dirs["root"] / "analysis_config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"[done] wrote expanded gamut analysis -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
