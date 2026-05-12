#!/usr/bin/env python3
"""
Estimate Figure-5-style mosaic panel landmarks from the photographed frame.

This is intentionally conservative: it starts from an existing rough panel
manifest, detects the gray/white printed frame and separator bars, fits each
large-panel side to those pixels, then writes a refined landmark manifest plus
QC overlays.
"""

from __future__ import annotations

import argparse
import math
import tempfile
from pathlib import Path
from typing import Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PIL import Image, ImageFilter


LANDMARKS = ("V1", "V2", "V3", "M12", "M23", "M31")


def _resize_to_max_dim(image: Image.Image, max_dim: int) -> tuple[Image.Image, float]:
    if max_dim <= 0:
        return image, 1.0
    w, h = image.size
    scale = min(float(max_dim) / max(w, h), 1.0)
    if scale >= 1.0:
        return image, 1.0
    return image.resize((int(round(w * scale)), int(round(h * scale))), Image.Resampling.LANCZOS), scale


def _parse_landmarks(text: str) -> dict[str, np.ndarray]:
    points: dict[str, np.ndarray] = {}
    for chunk in str(text or "").split(";"):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        label, coord = chunk.split(":", 1)
        xy = [v.strip() for v in coord.split(",")]
        if len(xy) != 2:
            continue
        points[label.strip().upper()] = np.array([float(xy[0]), float(xy[1])], dtype=float)
    missing = [label for label in LANDMARKS if label not in points]
    if missing:
        raise ValueError(f"Missing landmarks: {', '.join(missing)}")
    return {label: points[label] for label in LANDMARKS}


def _format_landmarks(points: dict[str, np.ndarray]) -> str:
    return ";".join(f"{label}:{points[label][0]:.1f},{points[label][1]:.1f}" for label in LANDMARKS)


def _row_scale(row: pd.Series, analysis_max_dim: int) -> float:
    raw = str(row.get("coordinate_max_dim", "")).strip()
    if not raw:
        return 1.0
    try:
        ref = float(raw)
    except ValueError:
        return 1.0
    return float(analysis_max_dim) / ref if ref > 0 else 1.0


def _frame_mask(rgb: np.ndarray) -> np.ndarray:
    arr = rgb.astype(float)
    mx = arr.max(axis=2)
    mn = arr.min(axis=2)
    mean = arr.mean(axis=2)
    sat = (mx - mn) / np.maximum(mx, 1.0)
    # The printed frame/separators are low-chroma light gray. This mask avoids
    # most colored regions while still catching the pale separator bars.
    mask = (sat < 0.085) & (mean > 85.0) & (mean < 248.0) & np.any(arr < 248.0, axis=2)
    image = Image.fromarray(mask.astype(np.uint8) * 255, mode="L")
    image = image.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    return np.asarray(image) > 0


def _points_near_segment(mask: np.ndarray, p0: np.ndarray, p1: np.ndarray, radius: float) -> np.ndarray:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return np.empty((0, 2), dtype=float)
    pts = np.column_stack([xs.astype(float), ys.astype(float)])
    v = p1 - p0
    length2 = float(np.dot(v, v))
    if length2 <= 1e-9:
        return np.empty((0, 2), dtype=float)
    t = ((pts - p0) @ v) / length2
    proj = p0 + t[:, None] * v
    dist = np.linalg.norm(pts - proj, axis=1)
    keep = (t >= -0.08) & (t <= 1.08) & (dist <= radius)
    return pts[keep]


def _fit_line_tls(points: np.ndarray, fallback_a: np.ndarray, fallback_b: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    if len(points) < 30:
        direction = fallback_b - fallback_a
        norm = np.linalg.norm(direction)
        return fallback_a.copy(), direction / max(norm, 1e-9), int(len(points))
    centroid = points.mean(axis=0)
    centered = points - centroid
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    direction = vt[0]
    fallback_dir = fallback_b - fallback_a
    if np.dot(direction, fallback_dir) < 0:
        direction = -direction
    return centroid, direction / max(np.linalg.norm(direction), 1e-9), int(len(points))


def _line_intersection(a0: np.ndarray, ad: np.ndarray, b0: np.ndarray, bd: np.ndarray) -> np.ndarray:
    mat = np.column_stack([ad, -bd])
    rhs = b0 - a0
    det = float(np.linalg.det(mat))
    if abs(det) < 1e-9:
        return 0.5 * (a0 + b0)
    t, _ = np.linalg.solve(mat, rhs)
    return a0 + t * ad


def _fit_panel(points: dict[str, np.ndarray], mask: np.ndarray, radius: float) -> tuple[dict[str, np.ndarray], dict[str, int]]:
    side_defs = {
        "S12": ("V1", "V2"),
        "S23": ("V2", "V3"),
        "S31": ("V3", "V1"),
    }
    lines: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    counts: dict[str, int] = {}
    for side, (a, b) in side_defs.items():
        pts = _points_near_segment(mask, points[a], points[b], radius)
        origin, direction, count = _fit_line_tls(pts, points[a], points[b])
        lines[side] = (origin, direction)
        counts[side] = count

    v1 = _line_intersection(*lines["S12"], *lines["S31"])
    v2 = _line_intersection(*lines["S12"], *lines["S23"])
    v3 = _line_intersection(*lines["S23"], *lines["S31"])
    refined = {
        "V1": v1,
        "V2": v2,
        "V3": v3,
        # These are estimated as side-midpoints after side fitting. The
        # interactive editor remains the right tool if printed midpoint seams
        # are visibly shifted by perspective or print warping.
        "M12": 0.5 * (v1 + v2),
        "M23": 0.5 * (v2 + v3),
        "M31": 0.5 * (v3 + v1),
    }
    return refined, counts


def _plot_overlay(
    rgb: np.ndarray,
    original_rows: Sequence[tuple[str, dict[str, np.ndarray]]],
    refined_rows: Sequence[tuple[str, dict[str, np.ndarray], dict[str, int]]],
    out_path: Path,
) -> None:
    fig, ax = plt.subplots(figsize=(16, 7))
    ax.imshow(rgb)
    for name, pts in original_rows:
        outer = np.vstack([pts["V1"], pts["V2"], pts["V3"], pts["V1"]])
        ax.plot(outer[:, 0], outer[:, 1], color="white", lw=1.2, ls="--", alpha=0.75)
        ax.plot(outer[:, 0], outer[:, 1], color="0.15", lw=0.45, ls="--", alpha=0.75)
    for name, pts, counts in refined_rows:
        outer = np.vstack([pts["V1"], pts["V2"], pts["V3"], pts["V1"]])
        mids = np.vstack([pts["M12"], pts["M23"], pts["M31"], pts["M12"]])
        ax.plot(outer[:, 0], outer[:, 1], color="#00ffff", lw=2.0)
        ax.plot(mids[:, 0], mids[:, 1], color="#ff4d4d", lw=1.2)
        for label, point in pts.items():
            ax.scatter([point[0]], [point[1]], s=42, color="#ffdd00", edgecolor="black", linewidth=0.6, zorder=5)
            ax.text(
                point[0] + 3,
                point[1],
                label,
                fontsize=6,
                color="black",
                va="center",
                bbox=dict(facecolor="white", alpha=0.7, edgecolor="none", pad=0.5),
            )
        centroid = np.vstack([pts["V1"], pts["V2"], pts["V3"]]).mean(axis=0)
        ax.text(
            centroid[0],
            centroid[1],
            f"{name}\nfit px: {counts['S12']}/{counts['S23']}/{counts['S31']}",
            ha="center",
            va="center",
            fontsize=7,
            color="black",
            bbox=dict(facecolor="white", alpha=0.68, edgecolor="none", pad=1.0),
        )
    ax.set_title("Automatic panel landmark guesses: dashed seed, cyan fitted outer triangle, red derived midpoint triangle")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(out_path, dpi=220)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--seed-manifest", required=True, help="Rough panel manifest with landmarks.")
    parser.add_argument("--out-manifest", required=True)
    parser.add_argument("--out-overlay", required=True)
    parser.add_argument("--analysis-max-dim", type=int, default=2400)
    parser.add_argument("--fit-radius", type=float, default=18.0)
    args = parser.parse_args()

    image = Image.open(args.image).convert("RGB")
    image, _ = _resize_to_max_dim(image, args.analysis_max_dim)
    rgb = np.asarray(image)
    mask = _frame_mask(rgb)

    seed = pd.read_csv(args.seed_manifest, dtype=str).fillna("")
    out = seed.copy()
    original_for_plot = []
    refined_for_plot = []
    fit_rows = []
    for idx, row in seed.iterrows():
        if not str(row.get("landmarks", "")).strip():
            continue
        scale = _row_scale(row, args.analysis_max_dim)
        raw_points = _parse_landmarks(str(row["landmarks"]))
        seed_points = {label: point * scale for label, point in raw_points.items()}
        refined, counts = _fit_panel(seed_points, mask, args.fit_radius)
        manifest_points = {label: point / max(scale, 1e-9) for label, point in refined.items()}
        out.loc[idx, "landmarks"] = _format_landmarks(manifest_points)
        if not str(out.loc[idx].get("coordinate_max_dim", "")).strip():
            out.loc[idx, "coordinate_max_dim"] = str(args.analysis_max_dim)
        original_for_plot.append((str(row.get("triangle_name", idx)), seed_points))
        refined_for_plot.append((str(row.get("triangle_name", idx)), refined, counts))
        fit_rows.append(
            {
                "triangle_name": str(row.get("triangle_name", idx)),
                "side12_pixels": counts["S12"],
                "side23_pixels": counts["S23"],
                "side31_pixels": counts["S31"],
                "landmarks": _format_landmarks(manifest_points),
            }
        )

    out_manifest = Path(args.out_manifest).expanduser().resolve()
    out_manifest.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_manifest, index=False)

    out_overlay = Path(args.out_overlay).expanduser().resolve()
    out_overlay.parent.mkdir(parents=True, exist_ok=True)
    _plot_overlay(rgb, original_for_plot, refined_for_plot, out_overlay)
    pd.DataFrame(fit_rows).to_csv(out_overlay.with_suffix(".fit_counts.csv"), index=False)
    print(f"[done] wrote {out_manifest}")
    print(f"[done] wrote {out_overlay}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
