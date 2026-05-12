#!/usr/bin/env python3
"""
Region-aware analysis for photographed LayerLoom CMY gamut prints.

This script registers a photographed triangular gamut to the exact Voronoi
regions used to generate the print, measures each region in CIE Lab space, and
writes inspectable intermediate outputs plus publication-oriented figures.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "layerloom_matplotlib_cache"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PatchCollection
from matplotlib.patches import Polygon, Rectangle
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
LAYERLOOM_DIR = HERE.parent
if str(LAYERLOOM_DIR) not in sys.path:
    sys.path.insert(0, str(LAYERLOOM_DIR))

import exact_voronoi_regions_to_3mf as exact_regions  # noqa: E402
import layer_perm_cmy_visualizer as vis  # noqa: E402
from palette_utils import corrected_hex, resolve_preset_parameters  # noqa: E402


THEORY_CORNERS = {
    "C": np.array([0.0, 0.0], dtype=float),
    "M": np.array([1.0, 0.0], dtype=float),
    "Y": np.array([0.5, vis.SQRT3_2], dtype=float),
}
CORNER_ORDER = ("C", "M", "Y")


def _hex_to_rgb01(hex_code: str) -> Tuple[float, float, float]:
    s = str(hex_code).strip().lstrip("#")
    if len(s) != 6:
        raise ValueError(f"Expected 6-digit hex color, got {hex_code!r}")
    return tuple(int(s[i : i + 2], 16) / 255.0 for i in (0, 2, 4))


def _rgb01_to_hex(rgb: Sequence[float]) -> str:
    vals = [int(np.clip(float(v), 0.0, 1.0) * 255 + 0.5) for v in rgb[:3]]
    return "#{:02x}{:02x}{:02x}".format(*vals)


def _rgb_to_hsv(rgb: np.ndarray) -> np.ndarray:
    arr = np.asarray(rgb, dtype=float)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    mx = np.max(arr, axis=-1)
    mn = np.min(arr, axis=-1)
    diff = mx - mn
    h = np.zeros_like(mx)
    mask = diff > 1e-12
    rmask = mask & (mx == r)
    gmask = mask & (mx == g)
    bmask = mask & (mx == b)
    h[rmask] = ((g[rmask] - b[rmask]) / diff[rmask]) % 6.0
    h[gmask] = (b[gmask] - r[gmask]) / diff[gmask] + 2.0
    h[bmask] = (r[bmask] - g[bmask]) / diff[bmask] + 4.0
    h = h / 6.0
    s = np.zeros_like(mx)
    np.divide(diff, mx, out=s, where=mx > 1e-12)
    v = mx
    return np.stack([h, s, v], axis=-1)


def _rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    arr = np.asarray(rgb, dtype=float)
    linear = np.where(arr <= 0.04045, arr / 12.92, ((arr + 0.055) / 1.055) ** 2.4)
    matrix = np.array(
        [
            [0.4124564, 0.3575761, 0.1804375],
            [0.2126729, 0.7151522, 0.0721750],
            [0.0193339, 0.1191920, 0.9503041],
        ],
        dtype=float,
    )
    xyz = linear @ matrix.T
    white = np.array([0.95047, 1.00000, 1.08883], dtype=float)
    xyz_scaled = xyz / white
    delta = 6.0 / 29.0
    f = np.where(xyz_scaled > delta**3, np.cbrt(xyz_scaled), xyz_scaled / (3 * delta**2) + 4.0 / 29.0)
    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def _delta_e00(lab_a: np.ndarray, lab_b: np.ndarray) -> np.ndarray:
    a = np.asarray(lab_a, dtype=float).reshape(-1, 3)
    b = np.asarray(lab_b, dtype=float).reshape(-1, 3)
    if b.shape[0] == 1 and a.shape[0] > 1:
        b = np.repeat(b, a.shape[0], axis=0)
    if a.shape[0] == 1 and b.shape[0] > 1:
        a = np.repeat(a, b.shape[0], axis=0)
    if a.shape[0] != b.shape[0]:
        raise ValueError("Lab arrays must be same length or one must be length 1")

    L1, a1, b1 = a[:, 0], a[:, 1], a[:, 2]
    L2, a2, b2 = b[:, 0], b[:, 1], b[:, 2]

    C1 = np.sqrt(a1 * a1 + b1 * b1)
    C2 = np.sqrt(a2 * a2 + b2 * b2)
    Cbar = 0.5 * (C1 + C2)
    Cbar7 = Cbar**7
    G = 0.5 * (1.0 - np.sqrt(Cbar7 / (Cbar7 + 25.0**7 + 1e-30)))

    a1p = (1.0 + G) * a1
    a2p = (1.0 + G) * a2
    C1p = np.sqrt(a1p * a1p + b1 * b1)
    C2p = np.sqrt(a2p * a2p + b2 * b2)

    h1p = (np.degrees(np.arctan2(b1, a1p)) + 360.0) % 360.0
    h2p = (np.degrees(np.arctan2(b2, a2p)) + 360.0) % 360.0
    h1p = np.where(C1p <= 1e-12, 0.0, h1p)
    h2p = np.where(C2p <= 1e-12, 0.0, h2p)

    dLp = L2 - L1
    dCp = C2p - C1p
    dh = h2p - h1p
    dh = np.where(dh > 180.0, dh - 360.0, dh)
    dh = np.where(dh < -180.0, dh + 360.0, dh)
    dh = np.where((C1p * C2p) <= 1e-12, 0.0, dh)
    dHp = 2.0 * np.sqrt(C1p * C2p) * np.sin(np.radians(dh) / 2.0)

    Lbarp = 0.5 * (L1 + L2)
    Cbarp = 0.5 * (C1p + C2p)
    hsum = h1p + h2p
    hdiff = np.abs(h1p - h2p)
    hbarp = np.where(
        (C1p * C2p) <= 1e-12,
        hsum,
        np.where(hdiff <= 180.0, 0.5 * hsum, np.where(hsum < 360.0, 0.5 * (hsum + 360.0), 0.5 * (hsum - 360.0))),
    )

    T = (
        1.0
        - 0.17 * np.cos(np.radians(hbarp - 30.0))
        + 0.24 * np.cos(np.radians(2.0 * hbarp))
        + 0.32 * np.cos(np.radians(3.0 * hbarp + 6.0))
        - 0.20 * np.cos(np.radians(4.0 * hbarp - 63.0))
    )
    delta_theta = 30.0 * np.exp(-((hbarp - 275.0) / 25.0) ** 2)
    Cbarp7 = Cbarp**7
    Rc = 2.0 * np.sqrt(Cbarp7 / (Cbarp7 + 25.0**7 + 1e-30))
    Sl = 1.0 + (0.015 * (Lbarp - 50.0) ** 2) / np.sqrt(20.0 + (Lbarp - 50.0) ** 2)
    Sc = 1.0 + 0.045 * Cbarp
    Sh = 1.0 + 0.015 * Cbarp * T
    Rt = -np.sin(np.radians(2.0 * delta_theta)) * Rc

    return np.sqrt(
        (dLp / Sl) ** 2
        + (dCp / Sc) ** 2
        + (dHp / Sh) ** 2
        + Rt * (dCp / Sc) * (dHp / Sh)
    )


def _cross_2d(o: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    return float((a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]))


def _convex_hull_points(points_xy: np.ndarray) -> np.ndarray:
    pts = np.asarray(points_xy, dtype=float)
    if len(pts) <= 1:
        return pts
    pts = np.unique(pts, axis=0)
    pts = pts[np.lexsort((pts[:, 1], pts[:, 0]))]
    if len(pts) <= 2:
        return pts

    lower: List[np.ndarray] = []
    for p in pts:
        while len(lower) >= 2 and _cross_2d(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper: List[np.ndarray] = []
    for p in reversed(pts):
        while len(upper) >= 2 and _cross_2d(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    return np.asarray(lower[:-1] + upper[:-1], dtype=float)


def _polygon_area(points_xy: np.ndarray) -> float:
    pts = np.asarray(points_xy, dtype=float)
    if len(pts) < 3:
        return 0.0
    x = pts[:, 0]
    y = pts[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _pairwise_distance_matrix(points: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=float)
    if len(pts) == 0:
        return np.zeros((0, 0), dtype=float)
    diff = pts[:, None, :] - pts[None, :, :]
    return np.sqrt(np.sum(diff * diff, axis=2))


def _token_fractions(token: str) -> Tuple[float, float, float]:
    clean = "".join(ch for ch in str(token).lower() if ch in "cmy")
    n = max(len(clean), 1)
    return clean.count("c") / n, clean.count("m") / n, clean.count("y") / n


def _ensure_dirs(out_dir: Path) -> Dict[str, Path]:
    dirs = {
        "root": out_dir,
        "qc": out_dir / "qc",
        "figures": out_dir / "figures",
        "tables": out_dir / "tables",
        "masks": out_dir / "masks",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def _parse_manual_corners(text: str) -> Dict[str, np.ndarray]:
    """
    Parse either:
      C:x,y;M:x,y;Y:x,y
    or:
      x,y;x,y;x,y  (in C, M, Y order)
    """
    raw = [chunk.strip() for chunk in str(text or "").split(";") if chunk.strip()]
    if len(raw) != 3:
        raise ValueError("--corners must contain exactly three points")

    parsed: Dict[str, np.ndarray] = {}
    unlabeled: List[np.ndarray] = []
    for idx, chunk in enumerate(raw):
        label = None
        coord_text = chunk
        if ":" in chunk:
            label_text, coord_text = chunk.split(":", 1)
            label = label_text.strip().upper()
            if label not in CORNER_ORDER:
                raise ValueError("Corner labels must be C, M, and Y")
        bits = [b.strip() for b in coord_text.split(",")]
        if len(bits) != 2:
            raise ValueError(f"Bad corner coordinate: {chunk!r}")
        point = np.array([float(bits[0]), float(bits[1])], dtype=float)
        if label:
            parsed[label] = point
        else:
            unlabeled.append(point)

    if unlabeled:
        if parsed:
            raise ValueError("Use either all labeled or all unlabeled --corners")
        return {label: point for label, point in zip(CORNER_ORDER, unlabeled)}
    if set(parsed) != set(CORNER_ORDER):
        raise ValueError("Labeled --corners must include C, M, and Y")
    return parsed


def _parse_crop(text: Optional[str]) -> Optional[Tuple[int, int, int, int]]:
    if not text:
        return None
    raw = str(text).replace(",", " ").split()
    if len(raw) != 4:
        raise ValueError("--crop expects four values: x0,y0,x1,y1")
    x0, y0, x1, y1 = [int(round(float(v))) for v in raw]
    if x1 <= x0 or y1 <= y0:
        raise ValueError("--crop must satisfy x1 > x0 and y1 > y0")
    return x0, y0, x1, y1


def _apply_crop(rgb: np.ndarray, crop: Optional[Tuple[int, int, int, int]]) -> np.ndarray:
    if crop is None:
        return rgb
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = crop
    x0 = max(0, min(w, x0))
    x1 = max(0, min(w, x1))
    y0 = max(0, min(h, y0))
    y1 = max(0, min(h, y1))
    if x1 <= x0 or y1 <= y0:
        raise ValueError("Crop falls outside the image.")
    return rgb[y0:y1, x0:x1].copy()


def _resize_for_analysis(rgb: np.ndarray, max_dim: int) -> Tuple[np.ndarray, float]:
    if max_dim <= 0:
        return rgb, 1.0
    h, w = rgb.shape[:2]
    largest = max(h, w)
    if largest <= max_dim:
        return rgb, 1.0
    scale = float(max_dim) / float(largest)
    new_w = max(int(round(w * scale)), 1)
    new_h = max(int(round(h * scale)), 1)
    image = Image.fromarray(np.clip(rgb * 255.0, 0, 255).astype(np.uint8), mode="RGB")
    resized = image.resize((new_w, new_h), Image.Resampling.BILINEAR)
    return np.asarray(resized, dtype=np.float32) / 255.0, scale


def _auto_crop_from_color(
    rgb: np.ndarray,
    *,
    min_saturation: float,
    min_value: float,
    max_value: float,
    pad_px: int,
) -> Optional[Tuple[int, int, int, int]]:
    hsv = _rgb_to_hsv(rgb)
    mask = (
        (hsv[:, :, 1] >= min_saturation)
        & (hsv[:, :, 2] >= min_value)
        & (hsv[:, :, 2] <= max_value)
    )
    mask = _binary_closing(mask, 2)
    yy, xx = np.nonzero(mask)
    if len(xx) == 0:
        return None
    h, w = rgb.shape[:2]
    pad = max(int(pad_px), 0)
    x0 = max(int(xx.min()) - pad, 0)
    x1 = min(int(xx.max()) + pad + 1, w)
    y0 = max(int(yy.min()) - pad, 0)
    y1 = min(int(yy.max()) + pad + 1, h)
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _binary_pil_filter(mask: np.ndarray, radius: int, filter_cls) -> np.ndarray:
    if radius <= 0:
        return np.asarray(mask, dtype=bool)
    source = np.asarray(mask, dtype=bool)
    coords = np.column_stack(np.nonzero(source))
    if len(coords) == 0:
        return np.zeros_like(source, dtype=bool)

    h, w = source.shape
    pad = int(radius) + 2
    y0 = max(int(coords[:, 0].min()) - pad, 0)
    y1 = min(int(coords[:, 0].max()) + pad + 1, h)
    x0 = max(int(coords[:, 1].min()) - pad, 0)
    x1 = min(int(coords[:, 1].max()) + pad + 1, w)

    size = int(radius) * 2 + 1
    image = Image.fromarray(source[y0:y1, x0:x1].astype(np.uint8) * 255, mode="L")
    filtered = np.asarray(image.filter(filter_cls(size))) > 0

    out = np.zeros_like(source, dtype=bool)
    out[y0:y1, x0:x1] = filtered
    return out


def _binary_dilation(mask: np.ndarray, radius: int) -> np.ndarray:
    return _binary_pil_filter(mask, radius, ImageFilter.MaxFilter)


def _binary_erosion(mask: np.ndarray, radius: int) -> np.ndarray:
    return _binary_pil_filter(mask, radius, ImageFilter.MinFilter)


def _binary_closing(mask: np.ndarray, radius: int) -> np.ndarray:
    return _binary_erosion(_binary_dilation(mask, radius), radius)


def _binary_opening(mask: np.ndarray, radius: int) -> np.ndarray:
    return _binary_dilation(_binary_erosion(mask, radius), radius)


def _build_color_mask(
    rgb: np.ndarray,
    *,
    min_saturation: float,
    min_value: float,
    max_value: float,
    min_chroma: float,
    cleanup_radius: int,
) -> np.ndarray:
    hsv = _rgb_to_hsv(rgb)
    lab = _rgb_to_lab(rgb)
    chroma = np.sqrt(lab[:, :, 1] ** 2 + lab[:, :, 2] ** 2)
    mask = (
        (hsv[:, :, 1] >= min_saturation)
        & (hsv[:, :, 2] >= min_value)
        & (hsv[:, :, 2] <= max_value)
        & (chroma >= min_chroma)
    )
    if cleanup_radius > 0:
        mask = _binary_closing(mask, cleanup_radius)
        mask = _binary_opening(mask, cleanup_radius)
    return np.asarray(mask, dtype=bool)


def _largest_component(mask: np.ndarray) -> np.ndarray:
    # Fast default: use the full cleaned color mask. If the photo contains
    # multiple disconnected gamuts or colored distractions, pass --corners.
    if not bool(np.asarray(mask, dtype=bool).any()):
        raise RuntimeError("No colored connected component was detected.")
    return np.asarray(mask, dtype=bool)


def _boundary_points_xy(mask: np.ndarray) -> np.ndarray:
    edge = mask & ~_binary_erosion(mask, 1)
    yy, xx = np.nonzero(edge)
    if len(xx) == 0:
        raise RuntimeError("Could not find a boundary contour for the detected triangle.")
    points = np.column_stack([xx, yy]).astype(float)
    if len(points) > 12000:
        idx = np.linspace(0, len(points) - 1, 12000).round().astype(int)
        points = points[idx]
    return points


def _max_area_triangle(points_xy: np.ndarray, *, max_points: int = 160) -> np.ndarray:
    if len(points_xy) < 3:
        raise RuntimeError("Not enough boundary points to detect a triangle.")
    hull_points = _convex_hull_points(points_xy)
    if len(hull_points) > max_points:
        idx = np.linspace(0, len(hull_points) - 1, max_points).round().astype(int)
        hull_points = hull_points[idx]

    best_area = -1.0
    best = None
    n = len(hull_points)
    for i in range(n - 2):
        pi = hull_points[i]
        for j in range(i + 1, n - 1):
            pj = hull_points[j]
            vj = pj - pi
            for k in range(j + 1, n):
                pk = hull_points[k]
                area2 = abs(float(vj[0] * (pk[1] - pi[1]) - vj[1] * (pk[0] - pi[0])))
                if area2 > best_area:
                    best_area = area2
                    best = np.array([pi, pj, pk], dtype=float)
    if best is None:
        raise RuntimeError("Triangle corner search failed.")
    return best


def _mean_rgb_near(rgb: np.ndarray, xy: Sequence[float], radius: int = 18) -> np.ndarray:
    x, y = float(xy[0]), float(xy[1])
    h, w = rgb.shape[:2]
    x0, x1 = max(int(x - radius), 0), min(int(x + radius + 1), w)
    y0, y1 = max(int(y - radius), 0), min(int(y + radius + 1), h)
    patch = rgb[y0:y1, x0:x1]
    if patch.size == 0:
        return np.array([0.0, 0.0, 0.0])
    return patch.reshape(-1, 3).mean(axis=0)


def _assign_cmy_corners(rgb: np.ndarray, triangle_xy: np.ndarray) -> Tuple[Dict[str, np.ndarray], pd.DataFrame]:
    colors = np.array([_mean_rgb_near(rgb, p) for p in triangle_xy], dtype=float)
    # Heuristic primary scores. This is intentionally simple and inspectable.
    scores = np.column_stack(
        [
            colors[:, 1] + colors[:, 2] - colors[:, 0],  # cyan
            colors[:, 0] + colors[:, 2] - colors[:, 1],  # magenta
            colors[:, 0] + colors[:, 1] - colors[:, 2],  # yellow
        ]
    )
    best_perm = None
    best_score = -np.inf
    for perm in itertools.permutations(range(3)):
        score = sum(scores[corner_idx, label_idx] for corner_idx, label_idx in enumerate(perm))
        if score > best_score:
            best_score = score
            best_perm = perm
    assert best_perm is not None
    labels_for_corners = [CORNER_ORDER[label_idx] for label_idx in best_perm]
    corners = {label: triangle_xy[i] for i, label in enumerate(labels_for_corners)}
    rows = []
    for i, label in enumerate(labels_for_corners):
        rows.append(
            {
                "detected_corner_index": i,
                "assigned_label": label,
                "x_px": triangle_xy[i, 0],
                "y_px": triangle_xy[i, 1],
                "mean_R": colors[i, 0],
                "mean_G": colors[i, 1],
                "mean_B": colors[i, 2],
                "cyan_score": scores[i, 0],
                "magenta_score": scores[i, 1],
                "yellow_score": scores[i, 2],
            }
        )
    return corners, pd.DataFrame(rows)


def _affine_transform_from_corners(corners: Dict[str, np.ndarray]) -> Callable[[np.ndarray], np.ndarray]:
    src = np.vstack([THEORY_CORNERS[label] for label in CORNER_ORDER])
    dst = np.vstack([corners[label] for label in CORNER_ORDER])
    src_aug = np.column_stack([src, np.ones(3)])
    matrix = np.linalg.solve(src_aug, dst)

    def transform(points_xy: np.ndarray) -> np.ndarray:
        pts = np.asarray(points_xy, dtype=float)
        return np.column_stack([pts, np.ones(len(pts))]) @ matrix

    return transform


def _polygon_mask(shape_hw: Tuple[int, int], poly_xy: np.ndarray) -> np.ndarray:
    h, w = shape_hw
    pts = np.asarray(poly_xy, dtype=float)
    if pts.size == 0:
        return np.zeros((h, w), dtype=bool)

    x0 = max(int(math.floor(float(np.min(pts[:, 0])))) - 2, 0)
    x1 = min(int(math.ceil(float(np.max(pts[:, 0])))) + 3, w)
    y0 = max(int(math.floor(float(np.min(pts[:, 1])))) - 2, 0)
    y1 = min(int(math.ceil(float(np.max(pts[:, 1])))) + 3, h)
    if x0 >= x1 or y0 >= y1:
        return np.zeros((h, w), dtype=bool)

    local = Image.new("L", (x1 - x0, y1 - y0), 0)
    local_pts = [(float(x - x0), float(y - y0)) for x, y in pts]
    ImageDraw.Draw(local).polygon(local_pts, fill=255)

    mask = np.zeros((h, w), dtype=bool)
    mask[y0:y1, x0:x1] = np.asarray(local) > 0
    return mask


def _shrink_polygon_about_centroid(poly_xy: np.ndarray, fraction: float) -> np.ndarray:
    pts = np.asarray(poly_xy, dtype=float)
    if len(pts) == 0:
        return pts
    frac = float(np.clip(fraction, 0.05, 1.0))
    centroid = pts.mean(axis=0)
    return centroid + frac * (pts - centroid)


def _erode_with_fallback(mask: np.ndarray, radius: int, min_pixels: int) -> Tuple[np.ndarray, int]:
    for r in range(max(int(radius), 0), -1, -1):
        if r > 0:
            eroded = _binary_erosion(mask, r)
        else:
            eroded = mask
        if int(eroded.sum()) >= min_pixels or r == 0:
            return np.asarray(eroded, dtype=bool), r
    return mask, 0


def _robust_lab_inlier_mask(
    lab: np.ndarray,
    candidate_mask: np.ndarray,
    *,
    min_pixels: int,
    trim_percentile: float,
) -> Tuple[np.ndarray, int, float]:
    coords = np.column_stack(np.nonzero(candidate_mask))
    if len(coords) == 0:
        return candidate_mask, 0, float("nan")
    if len(coords) < max(int(min_pixels) * 2, 100):
        return candidate_mask, 0, float("nan")

    pix_lab = lab[coords[:, 0], coords[:, 1]]
    median_lab = np.median(pix_lab, axis=0)
    distances = _delta_e00(pix_lab, median_lab)
    cutoff = float(np.percentile(distances, np.clip(float(trim_percentile), 0.0, 100.0)))
    keep = distances <= cutoff
    if int(keep.sum()) < int(min_pixels):
        return candidate_mask, 0, cutoff

    inlier_mask = np.zeros(candidate_mask.shape, dtype=bool)
    kept = coords[keep]
    inlier_mask[kept[:, 0], kept[:, 1]] = True
    return inlier_mask, int(len(coords) - len(kept)), cutoff


def _safe_hull_area(points_2d: np.ndarray) -> float:
    if len(points_2d) < 3:
        return float("nan")
    try:
        return _polygon_area(_convex_hull_points(points_2d))
    except Exception:
        return float("nan")


def _pairwise_delta_e00(lab_points: np.ndarray, labels: Sequence[str]) -> pd.DataFrame:
    rows = []
    for i in range(len(lab_points)):
        for j in range(i + 1, len(lab_points)):
            de00 = float(_delta_e00(lab_points[i], lab_points[j])[0])
            de76 = float(np.linalg.norm(lab_points[i] - lab_points[j]))
            rows.append(
                {
                    "region_i": labels[i],
                    "region_j": labels[j],
                    "deltaE00": de00,
                    "deltaE76": de76,
                }
            )
    return pd.DataFrame(rows)


def _component_count_under_threshold(pairwise: pd.DataFrame, labels: Sequence[str], threshold: float) -> int:
    parent = {label: label for label in labels}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    if not pairwise.empty:
        for row in pairwise.itertuples(index=False):
            if float(row.deltaE00) < threshold:
                union(str(row.region_i), str(row.region_j))
    return len({find(label) for label in labels})


def _build_expected_regions(args: argparse.Namespace) -> Tuple[pd.DataFrame, List[List[Tuple[float, float]]]]:
    resolved = resolve_preset_parameters(
        args.preset,
        max_height=args.max_height,
        max_run=args.max_run,
        distinct_lte=args.distinct_lte,
    )
    stacks = exact_regions.build_stacks(
        resolved["max_height"],
        resolved["max_run"],
        resolved["distinct_lte"],
    )
    sites, _, regions = exact_regions.build_regions(stacks)
    rows = []
    for idx, (stack, site, poly) in enumerate(zip(stacks, sites, regions), start=1):
        token = "".join(stack).lower()
        expected_hex = corrected_hex(token)
        expected_rgb = np.array(_hex_to_rgb01(expected_hex), dtype=float)
        expected_lab = _rgb_to_lab(expected_rgb.reshape(1, 1, 3)).reshape(3)
        f_c, f_m, f_y = _token_fractions(token)
        rows.append(
            {
                "region_index": idx,
                "label": f"gamut_{idx:03d}_{token}",
                "stack_token": token,
                "fC": f_c,
                "fM": f_m,
                "fY": f_y,
                "site_x": site[0],
                "site_y": site[1],
                "expected_hex": expected_hex,
                "expected_R": expected_rgb[0],
                "expected_G": expected_rgb[1],
                "expected_B": expected_rgb[2],
                "expected_L": expected_lab[0],
                "expected_a": expected_lab[1],
                "expected_b": expected_lab[2],
                "polygon_xy": json.dumps(poly),
                "max_height": resolved["max_height"],
                "max_run": resolved["max_run"],
                "distinct_lte": resolved["distinct_lte"],
                "preset": resolved["preset"],
            }
        )
    return pd.DataFrame(rows), regions


def _save_input_copy(image_path: Path, out_path: Path) -> None:
    shutil.copyfile(image_path, out_path)


def _save_rgb_image(rgb: np.ndarray, out_path: Path) -> None:
    Image.fromarray(np.clip(rgb * 255.0, 0, 255).astype(np.uint8), mode="RGB").save(out_path)


def _plot_crop_overview(
    rgb_full: np.ndarray,
    crop: Optional[Tuple[int, int, int, int]],
    out_path: Path,
) -> None:
    fig, ax = plt.subplots(figsize=(13, 7))
    ax.imshow(rgb_full)
    if crop is not None:
        x0, y0, x1, y1 = crop
        ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, edgecolor="#00ffff", linewidth=3))
        ax.text(
            x0,
            max(0, y0 - 12),
            f"crop: {x0},{y0},{x1},{y1}",
            ha="left",
            va="bottom",
            color="black",
            fontsize=11,
            bbox=dict(facecolor="white", edgecolor="#00ffff", alpha=0.8, pad=2),
        )
    else:
        ax.text(
            0.01,
            0.03,
            "no crop",
            transform=ax.transAxes,
            color="black",
            fontsize=12,
            bbox=dict(facecolor="white", edgecolor="black", alpha=0.8, pad=3),
        )
    ax.set_title("Full input and selected analysis crop")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(out_path, dpi=220)
    plt.close(fig)


def _plot_corner_guide(rgb: np.ndarray, out_path: Path, *, title: str = "Corner coordinate guide") -> None:
    h, w = rgb.shape[:2]
    fig_w = 16 if w >= h else 10
    fig_h = max(6, fig_w * h / max(w, 1))
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.imshow(rgb)
    tick_step = max(50, int(round(max(w, h) / 12 / 50.0)) * 50)
    x_ticks = list(range(0, w + 1, tick_step))
    y_ticks = list(range(0, h + 1, tick_step))
    ax.set_xticks(x_ticks)
    ax.set_yticks(y_ticks)
    ax.grid(color="white", linewidth=1.2, alpha=0.85)
    ax.grid(color="black", linewidth=0.35, alpha=0.75)
    ax.tick_params(axis="both", labelsize=9, colors="black")
    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.set_xlabel("x pixel")
    ax.set_ylabel("y pixel")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(out_path, dpi=220)
    plt.close(fig)


def _plot_mask_preview(rgb: np.ndarray, mask: np.ndarray, component: np.ndarray, out_path: Path) -> None:
    overlay = rgb.copy()
    overlay[mask] = 0.55 * overlay[mask] + 0.45 * np.array([0.0, 0.8, 1.0])
    overlay[component] = 0.50 * overlay[component] + 0.50 * np.array([1.0, 0.9, 0.0])
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    axes[0].imshow(rgb)
    axes[0].set_title("Input")
    axes[1].imshow(mask, cmap="gray")
    axes[1].set_title("Colored-pixel mask")
    axes[2].imshow(overlay)
    axes[2].set_title("Mask overlay; largest component in yellow")
    for ax in axes:
        ax.axis("off")
    fig.tight_layout()
    fig.savefig(out_path, dpi=220)
    plt.close(fig)


def _plot_corners(rgb: np.ndarray, corners: Dict[str, np.ndarray], out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 8))
    ax.imshow(rgb)
    pts = np.vstack([corners[label] for label in CORNER_ORDER])
    closed = np.vstack([pts, pts[0]])
    ax.plot(closed[:, 0], closed[:, 1], color="white", lw=3)
    ax.plot(closed[:, 0], closed[:, 1], color="black", lw=1)
    colors = {"C": "#00ffff", "M": "#ff00ff", "Y": "#ffff00"}
    for label, xy in corners.items():
        ax.scatter([xy[0]], [xy[1]], s=160, color=colors[label], edgecolor="black", lw=1.5)
        ax.text(
            xy[0],
            xy[1],
            f" {label}",
            color="black",
            fontsize=16,
            fontweight="bold",
            va="center",
            ha="left",
            bbox=dict(facecolor="white", edgecolor="black", alpha=0.75, pad=2),
        )
    ax.set_title("Detected registration corners")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(out_path, dpi=220)
    plt.close(fig)


def _plot_region_overlay(
    rgb: np.ndarray,
    expected: pd.DataFrame,
    regions_img: Sequence[np.ndarray],
    out_path: Path,
) -> None:
    fig, ax = plt.subplots(figsize=(12, 10))
    ax.imshow(rgb)
    for row, poly in zip(expected.itertuples(index=False), regions_img):
        patch = Polygon(poly, closed=True, fill=False, edgecolor="white", linewidth=1.5)
        ax.add_patch(patch)
        patch2 = Polygon(poly, closed=True, fill=False, edgecolor="black", linewidth=0.35)
        ax.add_patch(patch2)
        cx, cy = poly.mean(axis=0)
        ax.text(cx, cy, str(row.region_index), ha="center", va="center", fontsize=5, color="black")
    ax.set_title("Expected Voronoi regions registered to photo")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(out_path, dpi=260)
    plt.close(fig)


def _plot_sampling_overlay(
    rgb: np.ndarray,
    label_image: np.ndarray,
    measured: pd.DataFrame,
    out_path: Path,
) -> None:
    overlay = rgb.copy() * 0.68 + 0.32
    color_by_region = {
        int(row.region_index): np.array([row.measured_R, row.measured_G, row.measured_B], dtype=float)
        for row in measured.itertuples(index=False)
    }
    mask = label_image > 0
    colored = np.zeros_like(rgb)
    for region_index, color in color_by_region.items():
        colored[label_image == region_index] = color
    overlay[mask] = 0.30 * overlay[mask] + 0.70 * colored[mask]
    fig, ax = plt.subplots(figsize=(12, 10))
    ax.imshow(overlay)
    ax.set_title(f"Registered interior sampling regions ({len(measured)} measured regions)")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(out_path, dpi=260)
    plt.close(fig)


def _plot_palette(measured: pd.DataFrame, out_path: Path) -> None:
    ordered = measured.sort_values(["hue_angle_degrees", "region_index"]).reset_index(drop=True)
    n = len(ordered)
    fig_h = 3.0
    fig_w = max(8.0, n * 0.18)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    for i, row in ordered.iterrows():
        exp_rgb = (row.expected_R, row.expected_G, row.expected_B)
        meas_rgb = (row.measured_R, row.measured_G, row.measured_B)
        ax.add_patch(Rectangle((i, 1.0), 0.95, 0.8, facecolor=exp_rgb, edgecolor="black", linewidth=0.2))
        ax.add_patch(Rectangle((i, 0.0), 0.95, 0.8, facecolor=meas_rgb, edgecolor="black", linewidth=0.2))
    ax.text(-0.7, 1.4, "Expected", ha="right", va="center", fontsize=10)
    ax.text(-0.7, 0.4, "Measured", ha="right", va="center", fontsize=10)
    ax.set_xlim(-1.2, n)
    ax.set_ylim(-0.2, 2.0)
    ax.axis("off")
    ax.set_title("Expected vs measured region colors, hue-sorted")
    fig.tight_layout()
    fig.savefig(out_path, dpi=260)
    plt.close(fig)


def _plot_ab_gamut(measured: pd.DataFrame, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.2, 7.0))
    expected_ab = measured[["expected_a", "expected_b"]].to_numpy()
    measured_ab = measured[["measured_a", "measured_b"]].to_numpy()
    expected_rgb = measured[["expected_R", "expected_G", "expected_B"]].to_numpy()
    measured_rgb = measured[["measured_R", "measured_G", "measured_B"]].to_numpy()

    for exp, meas in zip(expected_ab, measured_ab):
        ax.annotate(
            "",
            xy=meas,
            xytext=exp,
            arrowprops=dict(arrowstyle="->", color="0.35", lw=0.7, shrinkA=2, shrinkB=2),
        )
    ax.scatter(expected_ab[:, 0], expected_ab[:, 1], c=expected_rgb, s=35, marker="o", edgecolor="black", linewidth=0.25, label="Expected")
    ax.scatter(measured_ab[:, 0], measured_ab[:, 1], c=measured_rgb, s=52, marker="s", edgecolor="black", linewidth=0.25, label="Measured")

    for points, color, label in (
        (expected_ab, "black", "Expected hull"),
        (measured_ab, "#1f77b4", "Measured hull"),
    ):
        if len(points) >= 3:
            try:
                hp = _convex_hull_points(points)
                hp = np.vstack([hp, hp[0]])
                ax.plot(hp[:, 0], hp[:, 1], color=color, lw=1.2, alpha=0.8, label=label)
            except Exception:
                pass

    ax.axhline(0, color="0.85", lw=0.8)
    ax.axvline(0, color="0.85", lw=0.8)
    ax.set_xlabel("a*")
    ax.set_ylabel("b*")
    ax.set_title("Printed gamut: expected vs measured CIE a*b*")
    ax.set_aspect("equal", adjustable="box")
    ax.legend(frameon=False, loc="best")
    fig.tight_layout()
    fig.savefig(out_path, dpi=300)
    plt.close(fig)


def _plot_measured_ab_gamut(measured: pd.DataFrame, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.2, 7.0))
    expected_ab = measured[["expected_a", "expected_b"]].to_numpy()
    measured_ab = measured[["measured_a", "measured_b"]].to_numpy()
    measured_rgb = measured[["measured_R", "measured_G", "measured_B"]].to_numpy()

    if len(expected_ab) >= 3:
        hp = _convex_hull_points(expected_ab)
        hp = np.vstack([hp, hp[0]])
        ax.plot(hp[:, 0], hp[:, 1], color="0.65", lw=1.4, linestyle="--", label="Expected hull")
    if len(measured_ab) >= 3:
        hp = _convex_hull_points(measured_ab)
        hp = np.vstack([hp, hp[0]])
        ax.fill(hp[:, 0], hp[:, 1], color="#1f77b4", alpha=0.10)
        ax.plot(hp[:, 0], hp[:, 1], color="#1f77b4", lw=1.8, label="Measured hull")

    ax.scatter(measured_ab[:, 0], measured_ab[:, 1], c=measured_rgb, s=68, marker="o", edgecolor="black", linewidth=0.35)
    ax.axhline(0, color="0.88", lw=0.8)
    ax.axvline(0, color="0.88", lw=0.8)
    ax.set_xlabel("a*")
    ax.set_ylabel("b*")
    ax.set_title("Measured printed gamut in CIE a*b*")
    ax.set_aspect("equal", adjustable="box")
    ax.legend(frameon=False, loc="best")
    fig.tight_layout()
    fig.savefig(out_path, dpi=300)
    plt.close(fig)


def _distinguishable_counts(pairwise: pd.DataFrame, labels: Sequence[str]) -> pd.DataFrame:
    rows = []
    for threshold in (2.0, 5.0, 10.0):
        count = _component_count_under_threshold(pairwise, labels, threshold)
        rows.append(
            {
                "threshold_deltaE00": threshold,
                "distinguishable_color_count": count,
                "theoretical_region_count": len(labels),
                "merged_region_count": len(labels) - count,
            }
        )
    return pd.DataFrame(rows)


def _plot_distinguishable_counts(counts: pd.DataFrame, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(6.0, 4.5))
    x = np.arange(len(counts))
    vals = counts["distinguishable_color_count"].to_numpy(dtype=float)
    bars = ax.bar(x, vals, color=["#3264a8", "#38a3a5", "#f28e2b"], edgecolor="black", linewidth=0.8)
    theoretical = int(counts["theoretical_region_count"].iloc[0]) if not counts.empty else 0
    if theoretical:
        ax.axhline(theoretical, color="0.2", linestyle="--", lw=1.0)
        ax.text(len(counts) - 0.48, theoretical + 0.7, f"{theoretical} theoretical", ha="right", va="bottom", fontsize=9)
    for bar, val in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.8, f"{int(val)}", ha="center", va="bottom", fontsize=11)
    ax.set_xticks(x)
    ax.set_xticklabels([f"DeltaE00 < {g:g}" for g in counts["threshold_deltaE00"]])
    ax.set_ylabel("Effective distinguishable colors")
    ax.set_title("Distinguishable printed color count")
    ax.set_ylim(0, max(theoretical, float(vals.max()) if len(vals) else 1) * 1.14)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out_path, dpi=300)
    plt.close(fig)


def _plot_extracted_color_grid(measured: pd.DataFrame, out_path: Path) -> None:
    ordered = measured.sort_values(["hue_angle_degrees", "region_index"]).reset_index(drop=True)
    ncols = min(11, max(1, len(ordered)))
    nrows = int(math.ceil(len(ordered) / ncols))
    fig, ax = plt.subplots(figsize=(max(8.0, ncols * 0.8), max(2.6, nrows * 0.72)))
    for i, row in ordered.iterrows():
        c = i % ncols
        r = i // ncols
        y = nrows - 1 - r
        ax.add_patch(Rectangle((c, y), 0.92, 0.92, facecolor=(row.measured_R, row.measured_G, row.measured_B), edgecolor="black", linewidth=0.35))
        ax.text(c + 0.46, y - 0.10, str(int(row.region_index)), ha="center", va="top", fontsize=6, color="0.2")
    ax.set_xlim(-0.1, ncols)
    ax.set_ylim(-0.35, nrows)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("Extracted per-region printed colors")
    fig.tight_layout()
    fig.savefig(out_path, dpi=280)
    plt.close(fig)


def _plot_deltae_map_with_hist(
    measured: pd.DataFrame,
    regions: Sequence[Sequence[Tuple[float, float]]],
    out_path: Path,
) -> None:
    fig = plt.figure(figsize=(10.2, 5.6))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.15, 0.85], wspace=0.25)
    ax_map = fig.add_subplot(gs[0, 0])
    ax_hist = fig.add_subplot(gs[0, 1])

    patches = []
    values = []
    row_by_index = measured.set_index("region_index")
    for idx, poly in enumerate(regions, start=1):
        if idx not in row_by_index.index:
            continue
        patches.append(Polygon(np.asarray(poly, dtype=float), closed=True))
        values.append(float(row_by_index.loc[idx, "deltaE00_to_expected"]))
    collection = PatchCollection(patches, cmap="magma", edgecolor="white", linewidth=0.35)
    collection.set_array(np.asarray(values, dtype=float))
    ax_map.add_collection(collection)
    tri = np.vstack([THEORY_CORNERS[k] for k in CORNER_ORDER] + [THEORY_CORNERS["C"]])
    ax_map.plot(tri[:, 0], tri[:, 1], color="black", lw=0.8)
    ax_map.set_xlim(-0.03, 1.03)
    ax_map.set_ylim(-0.03, vis.SQRT3_2 + 0.03)
    ax_map.set_aspect("equal", adjustable="box")
    ax_map.axis("off")
    ax_map.set_title("DeltaE00 error map")
    cbar = fig.colorbar(collection, ax=ax_map, fraction=0.048, pad=0.02)
    cbar.set_label("DeltaE00")

    vals = measured["deltaE00_to_expected"].dropna().to_numpy()
    ax_hist.hist(vals, bins=18, color="#22577a", edgecolor="white")
    for x, label in ((2, "2"), (5, "5"), (10, "10")):
        ax_hist.axvline(x, color="0.25", lw=1.0, linestyle="--")
        ax_hist.text(x, ax_hist.get_ylim()[1] * 0.96, label, ha="center", va="top", fontsize=8, color="0.2")
    ax_hist.set_xlabel("DeltaE00 to expected")
    ax_hist.set_ylabel("Region count")
    ax_hist.set_title("Error distribution")
    ax_hist.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Where the printed gamut deviates from the expected palette", y=0.99)
    fig.tight_layout()
    fig.savefig(out_path, dpi=300)
    plt.close(fig)


def _plot_workflow_figure(panel_paths: Sequence[Path], panel_titles: Sequence[str], out_path: Path) -> None:
    n = len(panel_paths)
    ncols = 3 if n > 3 else n
    nrows = int(math.ceil(n / max(ncols, 1)))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.7 * ncols, 4.2 * nrows))
    axes_flat = np.asarray(axes).reshape(-1)
    for ax, path, title in zip(axes_flat, panel_paths, panel_titles):
        if path.exists():
            ax.imshow(Image.open(path).convert("RGB"))
        else:
            ax.text(0.5, 0.5, f"Missing\n{path.name}", ha="center", va="center")
        ax.set_title(title, fontsize=10)
        ax.axis("off")
    for ax in axes_flat[n:]:
        ax.axis("off")
    fig.suptitle("Automated photo registration and color extraction workflow", y=0.98, fontsize=14)
    fig.tight_layout()
    fig.savefig(out_path, dpi=260, bbox_inches="tight")
    plt.close(fig)


def _plot_region_metric_map(
    measured: pd.DataFrame,
    regions: Sequence[Sequence[Tuple[float, float]]],
    metric: str,
    out_path: Path,
    *,
    title: str,
    cmap: str,
    cbar_label: str,
) -> None:
    fig, ax = plt.subplots(figsize=(7.2, 6.4))
    patches = []
    values = []
    row_by_index = measured.set_index("region_index")
    for idx, poly in enumerate(regions, start=1):
        if idx not in row_by_index.index:
            continue
        patches.append(Polygon(np.asarray(poly, dtype=float), closed=True))
        values.append(float(row_by_index.loc[idx, metric]))
    collection = PatchCollection(patches, cmap=cmap, edgecolor="white", linewidth=0.35)
    collection.set_array(np.asarray(values, dtype=float))
    ax.add_collection(collection)
    tri = np.vstack([THEORY_CORNERS[k] for k in CORNER_ORDER] + [THEORY_CORNERS["C"]])
    ax.plot(tri[:, 0], tri[:, 1], color="black", lw=0.8)
    ax.set_xlim(-0.03, 1.03)
    ax.set_ylim(-0.03, vis.SQRT3_2 + 0.03)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    ax.set_title(title)
    cbar = fig.colorbar(collection, ax=ax, fraction=0.046, pad=0.02)
    cbar.set_label(cbar_label)
    fig.tight_layout()
    fig.savefig(out_path, dpi=300)
    plt.close(fig)


def _plot_metric_histograms(measured: pd.DataFrame, out_path: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    axes[0].hist(measured["deltaE00_to_expected"].dropna(), bins=20, color="#22577a", edgecolor="white")
    axes[0].set_xlabel("DeltaE00 to expected")
    axes[0].set_ylabel("Region count")
    axes[0].set_title("Accuracy")
    axes[1].hist(measured["within_region_deltaE00_median"].dropna(), bins=20, color="#38a3a5", edgecolor="white")
    axes[1].set_xlabel("Within-region median DeltaE00")
    axes[1].set_ylabel("Region count")
    axes[1].set_title("Uniformity")
    fig.tight_layout()
    fig.savefig(out_path, dpi=260)
    plt.close(fig)


def _analyze_regions(
    rgb: np.ndarray,
    lab: np.ndarray,
    color_mask: np.ndarray,
    expected: pd.DataFrame,
    regions: Sequence[Sequence[Tuple[float, float]]],
    transform: Callable[[np.ndarray], np.ndarray],
    *,
    erosion_px: int,
    min_region_pixels: int,
    max_pixels_per_region: int,
    sample_core_fraction: float,
    lab_trim_percentile: float,
    random_seed: int,
) -> Tuple[pd.DataFrame, np.ndarray, List[np.ndarray]]:
    rng = np.random.default_rng(random_seed)
    rows = []
    label_image = np.zeros(rgb.shape[:2], dtype=np.uint16)
    transformed_regions: List[np.ndarray] = []

    for row, poly in zip(expected.itertuples(index=False), regions):
        poly_xy = np.asarray(poly, dtype=float)
        poly_img = transform(poly_xy)
        transformed_regions.append(poly_img)

        raw_mask = _polygon_mask(rgb.shape[:2], poly_img)
        requested_core_fraction = float(np.clip(sample_core_fraction, 0.05, 1.0))
        fallback_fractions = [requested_core_fraction, 0.70, 0.85, 1.0]
        fallback_fractions = sorted({float(np.clip(v, requested_core_fraction, 1.0)) for v in fallback_fractions})
        candidate_mask = raw_mask
        core_mask = raw_mask
        core_fraction_used = 1.0
        erosion_used = 0
        for fraction in fallback_fractions:
            core_poly = _shrink_polygon_about_centroid(poly_img, fraction)
            core_mask_try = _polygon_mask(rgb.shape[:2], core_poly)
            interior_try, erosion_try = _erode_with_fallback(core_mask_try, erosion_px, min_region_pixels)
            if int(interior_try.sum()) >= min_region_pixels or fraction >= 1.0:
                core_mask = core_mask_try
                candidate_mask = interior_try if int(interior_try.sum()) >= min_region_pixels else core_mask_try
                core_fraction_used = fraction
                erosion_used = erosion_try
                break
        sample_mask, trimmed_pixels, lab_trim_cutoff = _robust_lab_inlier_mask(
            lab,
            candidate_mask,
            min_pixels=min_region_pixels,
            trim_percentile=lab_trim_percentile,
        )

        coords = np.column_stack(np.nonzero(sample_mask))
        if len(coords) > max_pixels_per_region:
            idx = rng.choice(len(coords), size=max_pixels_per_region, replace=False)
            coords_sample = coords[idx]
        else:
            coords_sample = coords

        if len(coords_sample) == 0:
            continue

        pix_lab = lab[coords_sample[:, 0], coords_sample[:, 1]]
        pix_rgb = rgb[coords_sample[:, 0], coords_sample[:, 1]]

        measured_lab_median = np.median(pix_lab, axis=0)
        measured_lab_mean = np.mean(pix_lab, axis=0)
        measured_rgb = np.median(pix_rgb, axis=0)
        expected_lab = np.array([row.expected_L, row.expected_a, row.expected_b], dtype=float)
        de00_to_expected = float(_delta_e00(measured_lab_median, expected_lab)[0])
        de76_to_expected = float(np.linalg.norm(measured_lab_median - expected_lab))

        if len(pix_lab) > 1:
            de_to_median = _delta_e00(pix_lab, measured_lab_median)
            within_median = float(np.median(de_to_median))
            within_p90 = float(np.percentile(de_to_median, 90))
            within_sd = float(np.std(de_to_median))
        else:
            within_median = within_p90 = within_sd = float("nan")

        label_image[candidate_mask] = int(row.region_index)
        rows.append(
            {
                **row._asdict(),
                "sample_pixels": int(len(coords_sample)),
                "raw_region_pixels": int(raw_mask.sum()),
                "core_region_pixels": int(core_mask.sum()),
                "candidate_mask_pixels": int(candidate_mask.sum()),
                "sample_mask_pixels": int(sample_mask.sum()),
                "old_color_mask_pixels": int((candidate_mask & color_mask).sum()),
                "old_color_mask_fraction": float((candidate_mask & color_mask).sum() / max(int(candidate_mask.sum()), 1)),
                "sample_core_fraction_requested": requested_core_fraction,
                "sample_core_fraction_used": core_fraction_used,
                "erosion_px_used": int(erosion_used),
                "lab_trim_percentile": float(lab_trim_percentile),
                "lab_trim_cutoff_deltaE00": lab_trim_cutoff,
                "lab_trimmed_pixels": int(trimmed_pixels),
                "lab_trimmed_fraction": float(trimmed_pixels / max(int(candidate_mask.sum()), 1)),
                "measured_hex": _rgb01_to_hex(measured_rgb),
                "measured_R": measured_rgb[0],
                "measured_G": measured_rgb[1],
                "measured_B": measured_rgb[2],
                "measured_L": measured_lab_median[0],
                "measured_a": measured_lab_median[1],
                "measured_b": measured_lab_median[2],
                "measured_L_mean": measured_lab_mean[0],
                "measured_a_mean": measured_lab_mean[1],
                "measured_b_mean": measured_lab_mean[2],
                "deltaE00_to_expected": de00_to_expected,
                "deltaE76_to_expected": de76_to_expected,
                "within_region_deltaE00_median": within_median,
                "within_region_deltaE00_p90": within_p90,
                "within_region_deltaE00_sd": within_sd,
                "hue_angle_degrees": (math.degrees(math.atan2(measured_lab_median[2], measured_lab_median[1])) + 360.0) % 360.0,
                "measured_chroma": float(np.sqrt(measured_lab_median[1] ** 2 + measured_lab_median[2] ** 2)),
            }
        )

    return pd.DataFrame(rows), label_image, transformed_regions


def _summarize(measured: pd.DataFrame, pairwise: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    expected_ab = measured[["expected_a", "expected_b"]].to_numpy()
    measured_ab = measured[["measured_a", "measured_b"]].to_numpy()
    measured_lab = measured[["measured_L", "measured_a", "measured_b"]].to_numpy()
    labels = measured["label"].astype(str).tolist()
    expected_hull = _safe_hull_area(expected_ab)
    measured_hull = _safe_hull_area(measured_ab)
    if len(measured_lab) >= 2:
        d = _pairwise_distance_matrix(measured_lab)
        d[d == 0] = np.nan
        nearest = np.nanmin(d, axis=1)
    else:
        nearest = np.array([np.nan])
    rows = {
        "image": str(Path(args.image).resolve()),
        "target_name": args.target_name,
        "n_regions_expected": int(args._n_regions_expected),
        "n_regions_measured": int(len(measured)),
        "preset": args.preset,
        "max_height": args.max_height,
        "max_run": args.max_run,
        "distinct_lte": args.distinct_lte,
        "median_deltaE00_to_expected": float(measured["deltaE00_to_expected"].median()),
        "mean_deltaE00_to_expected": float(measured["deltaE00_to_expected"].mean()),
        "p90_deltaE00_to_expected": float(measured["deltaE00_to_expected"].quantile(0.90)),
        "median_within_region_deltaE00": float(measured["within_region_deltaE00_median"].median()),
        "p90_within_region_deltaE00": float(measured["within_region_deltaE00_p90"].median()),
        "expected_ab_hull_area": expected_hull,
        "measured_ab_hull_area": measured_hull,
        "measured_to_expected_ab_hull_ratio": measured_hull / expected_hull if expected_hull and np.isfinite(expected_hull) else np.nan,
        "mean_nearest_neighbor_deltaE76": float(np.nanmean(nearest)),
        "median_nearest_neighbor_deltaE76": float(np.nanmedian(nearest)),
        "min_pairwise_deltaE00": float(pairwise["deltaE00"].min()) if not pairwise.empty else np.nan,
        "median_pairwise_deltaE00": float(pairwise["deltaE00"].median()) if not pairwise.empty else np.nan,
    }
    for threshold in (2.3, 5.0, 10.0):
        key = str(threshold).replace(".", "p")
        rows[f"effective_color_components_deltaE00_lt_{key}"] = _component_count_under_threshold(pairwise, labels, threshold)
    return pd.DataFrame([rows])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Analyze one photographed LayerLoom printed gamut triangle.")
    p.add_argument("--image", required=True, help="Photo of the printed gamut.")
    p.add_argument("--out-dir", help="Output directory. Defaults to <image_stem>__region_analysis beside the image.")
    p.add_argument("--target-name", default="target", help="Human-readable name for this triangle/subtriangle.")
    p.add_argument(
        "--crop",
        help="Optional crop in full-image pixel coordinates: x0,y0,x1,y1. Overrides automatic color-mask cropping.",
    )
    p.add_argument(
        "--no-auto-crop",
        action="store_true",
        help="Analyze the full image when --crop is not supplied. By default, the script crops to the colored print with padding.",
    )
    p.add_argument(
        "--auto-crop-pad",
        type=int,
        default=80,
        help="Padding in pixels around the automatically detected colored print crop.",
    )
    p.add_argument(
        "--max-analysis-dim",
        type=int,
        default=2400,
        help="Resize the cropped image so its longest side is at most this many pixels. Use 0 to keep full resolution.",
    )
    p.add_argument(
        "--guide-only",
        action="store_true",
        help="Only write input/crop/corner-guide QC images, then exit. Useful before choosing manual corners.",
    )
    p.add_argument("--preset", choices=["simple", "normal", "full"], default="normal")
    p.add_argument("--max-height", type=int)
    p.add_argument("--max-run", type=int)
    p.add_argument("--distinct-lte", type=int)
    p.add_argument(
        "--corners",
        help="Optional manual image corners in C,M,Y order: 'x,y;x,y;x,y' or labeled 'C:x,y;M:x,y;Y:x,y'.",
    )
    p.add_argument("--min-saturation", type=float, default=0.12)
    p.add_argument("--min-value", type=float, default=0.05)
    p.add_argument("--max-value", type=float, default=0.99)
    p.add_argument("--min-chroma", type=float, default=7.0)
    p.add_argument("--cleanup-radius", type=int, default=2)
    p.add_argument("--erosion-px", type=int, default=8)
    p.add_argument("--min-region-pixels", type=int, default=40)
    p.add_argument("--max-pixels-per-region", type=int, default=12000)
    p.add_argument(
        "--sample-core-fraction",
        type=float,
        default=0.55,
        help="Measure the central fraction of each registered region polygon. Use 1.0 for full-region sampling.",
    )
    p.add_argument(
        "--lab-trim-percentile",
        type=float,
        default=98.0,
        help="Keep this percentile of pixels nearest each region median in Lab space. Set 100 to disable robust trimming.",
    )
    p.add_argument("--random-seed", type=int, default=1)
    return p


def _normalize_args(args: argparse.Namespace) -> argparse.Namespace:
    resolved = resolve_preset_parameters(
        args.preset,
        max_height=args.max_height,
        max_run=args.max_run,
        distinct_lte=args.distinct_lte,
    )
    args.preset = resolved["preset"]
    args.max_height = resolved["max_height"]
    args.max_run = resolved["max_run"]
    args.distinct_lte = resolved["distinct_lte"]
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _normalize_args(build_parser().parse_args(argv))
    image_path = Path(args.image).expanduser().resolve()
    if not image_path.exists():
        raise FileNotFoundError(image_path)
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else image_path.with_name(f"{image_path.stem}__region_analysis")
    dirs = _ensure_dirs(out_dir)

    img = Image.open(image_path).convert("RGB")
    rgb_full = np.asarray(img, dtype=np.float32) / 255.0
    requested_crop = _parse_crop(args.crop)
    auto_crop = None
    if requested_crop is None and not args.no_auto_crop:
        auto_crop = _auto_crop_from_color(
            rgb_full,
            min_saturation=args.min_saturation,
            min_value=args.min_value,
            max_value=args.max_value,
            pad_px=args.auto_crop_pad,
        )
    crop = requested_crop if requested_crop is not None else auto_crop
    rgb_cropped = _apply_crop(rgb_full, crop)
    rgb, analysis_scale = _resize_for_analysis(rgb_cropped, args.max_analysis_dim)
    lab = _rgb_to_lab(rgb)

    _save_input_copy(image_path, dirs["qc"] / "00_full_input.png")
    _save_rgb_image(rgb, dirs["qc"] / "00_analysis_input.png")
    _plot_crop_overview(rgb_full, crop, dirs["qc"] / "00_crop_overview.png")
    _plot_corner_guide(rgb, dirs["qc"] / "00_corner_coordinate_guide.png", title=f"Corner guide: {args.target_name}")
    if args.guide_only:
        config = {
            "image": str(image_path),
            "out_dir": str(out_dir),
            "target_name": args.target_name,
            "requested_crop": requested_crop,
            "auto_crop": auto_crop,
            "applied_crop": crop,
            "analysis_scale": analysis_scale,
            "analysis_image_shape_hw": list(rgb.shape[:2]),
            "guide_only": True,
            "note": "Use the analysis-image x,y coordinates from qc/00_corner_coordinate_guide.png for --corners.",
        }
        (dirs["root"] / "analysis_config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        print(f"[done] guide-only outputs: {out_dir}")
        print("[hint] use analysis-image coordinates from qc/00_corner_coordinate_guide.png with --corners 'C:x,y;M:x,y;Y:x,y'")
        return 0

    color_mask = _build_color_mask(
        rgb,
        min_saturation=args.min_saturation,
        min_value=args.min_value,
        max_value=args.max_value,
        min_chroma=args.min_chroma,
        cleanup_radius=args.cleanup_radius,
    )
    component = _largest_component(color_mask)
    _plot_mask_preview(rgb, color_mask, component, dirs["qc"] / "01_mask_preview.png")

    if args.corners:
        corners = _parse_manual_corners(args.corners)
        corner_debug = pd.DataFrame(
            [
                {"assigned_label": label, "x_px": corners[label][0], "y_px": corners[label][1], "source": "manual"}
                for label in CORNER_ORDER
            ]
        )
    else:
        points = _boundary_points_xy(component)
        triangle = _max_area_triangle(points)
        corners, corner_debug = _assign_cmy_corners(rgb, triangle)
        corner_debug["source"] = "auto"
    corner_debug.to_csv(dirs["tables"] / "registration_corners.csv", index=False)
    _plot_corners(rgb, corners, dirs["qc"] / "02_registration_corners.png")

    transform = _affine_transform_from_corners(corners)
    expected, regions = _build_expected_regions(args)
    args._n_regions_expected = len(expected)
    expected.to_csv(dirs["tables"] / "expected_regions.csv", index=False)

    measured, label_image, regions_img = _analyze_regions(
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
        random_seed=args.random_seed,
    )
    if measured.empty:
        raise RuntimeError("No regions were measured. Check registration and mask QC images.")

    measured.insert(0, "target_name", args.target_name)
    measured.to_csv(dirs["tables"] / "measured_regions.csv", index=False)
    np.savez_compressed(dirs["masks"] / "region_sample_label_image.npz", label_image=label_image)
    Image.fromarray(np.clip(label_image * (65535 // max(int(label_image.max()), 1)), 0, 65535).astype(np.uint16)).save(
        dirs["masks"] / "region_sample_label_image.png"
    )

    pairwise = _pairwise_delta_e00(
        measured[["measured_L", "measured_a", "measured_b"]].to_numpy(),
        measured["label"].astype(str).tolist(),
    )
    pairwise.to_csv(dirs["tables"] / "pairwise_deltaE00.csv", index=False)
    distinguishable = _distinguishable_counts(pairwise, measured["label"].astype(str).tolist())
    distinguishable.to_csv(dirs["tables"] / "distinguishable_color_counts.csv", index=False)
    summary = _summarize(measured, pairwise, args)
    summary.to_csv(dirs["tables"] / "summary.csv", index=False)

    config = {
        "image": str(image_path),
        "out_dir": str(out_dir),
        "target_name": args.target_name,
        "requested_crop": requested_crop,
        "auto_crop": auto_crop,
        "applied_crop": crop,
        "analysis_scale": analysis_scale,
        "analysis_image_shape_hw": list(rgb.shape[:2]),
        "corner_coordinate_space": "analysis image (after crop and optional resize)",
        "preset": args.preset,
        "max_height": args.max_height,
        "max_run": args.max_run,
        "distinct_lte": args.distinct_lte,
        "mask": {
            "min_saturation": args.min_saturation,
            "min_value": args.min_value,
            "max_value": args.max_value,
            "min_chroma": args.min_chroma,
            "cleanup_radius": args.cleanup_radius,
        },
        "sampling": {
            "erosion_px": args.erosion_px,
            "min_region_pixels": args.min_region_pixels,
            "max_pixels_per_region": args.max_pixels_per_region,
            "sample_core_fraction": args.sample_core_fraction,
            "lab_trim_percentile": args.lab_trim_percentile,
            "random_seed": args.random_seed,
        },
    }
    (dirs["root"] / "analysis_config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

    _plot_region_overlay(rgb, expected, regions_img, dirs["qc"] / "03_region_overlay.png")
    _plot_sampling_overlay(rgb, label_image, measured, dirs["qc"] / "04_sampling_overlay.png")
    _plot_extracted_color_grid(measured, dirs["qc"] / "05_extracted_color_grid.png")
    _plot_palette(measured, dirs["figures"] / "01_expected_vs_measured_palette.png")
    _plot_ab_gamut(measured, dirs["figures"] / "02_expected_vs_measured_ab_gamut.png")
    _plot_measured_ab_gamut(measured, dirs["figures"] / "02b_measured_printed_ab_gamut.png")
    _plot_region_metric_map(
        measured,
        regions,
        "deltaE00_to_expected",
        dirs["figures"] / "03_deltaE00_region_map.png",
        title="Color error by printed region",
        cmap="magma",
        cbar_label="DeltaE00 to expected",
    )
    _plot_region_metric_map(
        measured,
        regions,
        "within_region_deltaE00_median",
        dirs["figures"] / "04_uniformity_region_map.png",
        title="Within-region color variation",
        cmap="viridis",
        cbar_label="Median within-region DeltaE00",
    )
    _plot_metric_histograms(measured, dirs["figures"] / "05_metric_histograms.png")
    _plot_distinguishable_counts(distinguishable, dirs["figures"] / "06_distinguishable_color_count.png")
    _plot_deltae_map_with_hist(measured, regions, dirs["figures"] / "07_deltaE00_map_with_histogram.png")
    _plot_workflow_figure(
        [
            dirs["qc"] / "00_analysis_input.png",
            dirs["qc"] / "02_registration_corners.png",
            dirs["qc"] / "03_region_overlay.png",
            dirs["qc"] / "04_sampling_overlay.png",
            dirs["qc"] / "05_extracted_color_grid.png",
        ],
        [
            "Raw analysis image",
            "Registered triangle",
            "Expected regions",
            "Central samples",
            "Extracted colors",
        ],
        dirs["figures"] / "00_automated_photo_registration_workflow.png",
    )

    print(f"[done] analyzed {len(measured)} / {len(expected)} regions")
    print(f"[done] outputs: {out_dir}")
    print(summary.T.to_string(header=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
