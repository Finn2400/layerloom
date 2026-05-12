#!/usr/bin/env python3
"""
Analyze a photo containing multiple LayerLoom gamut triangles.

This is a thin orchestration layer around analyze_printed_gamut.py.  Each row
in a manifest describes one triangular sub-gamut by its C/M/Y registration
corners.  The script analyzes every sub-gamut independently, then aggregates
the measured colors into combined tables and CIE a*b* hull plots.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Sequence

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "layerloom_matplotlib_cache"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PIL import Image

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from analyze_printed_gamut import (  # noqa: E402
    _affine_transform_from_corners,
    _analyze_regions,
    _build_color_mask,
    _build_expected_regions,
    _convex_hull_points,
    _distinguishable_counts,
    _pairwise_delta_e00,
    _parse_manual_corners,
    _plot_corner_guide,
    _resize_for_analysis,
    _rgb_to_lab,
    _safe_hull_area,
)


CORNER_LABELS = ("C", "M", "Y")
PANEL_LANDMARK_LABELS = ("V1", "V2", "V3", "M12", "M23", "M31")


def _ensure_dirs(out_dir: Path) -> dict[str, Path]:
    dirs = {
        "root": out_dir,
        "qc": out_dir / "qc",
        "figures": out_dir / "figures",
        "tables": out_dir / "tables",
        "per_triangle": out_dir / "per_triangle",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def _safe_name(value: object) -> str:
    text = str(value).strip() or "triangle"
    keep = []
    for ch in text:
        keep.append(ch if ch.isalnum() or ch in ("-", "_") else "_")
    return "".join(keep).strip("_") or "triangle"


def _write_manifest_template(path: Path) -> None:
    rows = [
        {
            "group_name": "big_triangle_01",
            "triangle_name": "big01_panel",
            "preset": "simple",
            "max_height": "",
            "max_run": "",
            "distinct_lte": "",
            "corners": "",
            "landmarks": "V1:x,y;V2:x,y;V3:x,y;M12:x,y;M23:x,y;M31:x,y",
            "coordinate_max_dim": "",
            "notes": "One row per large panel. Use coordinates from qc/00_corner_coordinate_guide.png.",
        },
    ]
    pd.DataFrame(rows).to_csv(path, index=False)


def _load_manifest(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str).fillna("")
    if "triangle_name" not in df.columns:
        raise ValueError("Manifest is missing required column: triangle_name")
    if "corners" not in df.columns and "landmarks" not in df.columns:
        raise ValueError("Manifest must include either a corners column or a landmarks column")
    if "group_name" not in df.columns:
        df["group_name"] = "mosaic"
    if "preset" not in df.columns:
        df["preset"] = "normal"
    for col in ("corners", "landmarks", "coordinate_max_dim", "max_height", "max_run", "distinct_lte", "notes"):
        if col not in df.columns:
            df[col] = ""
    df = df[df["triangle_name"].astype(str).str.strip() != ""].copy()
    has_registration = df["corners"].astype(str).str.strip().ne("") | df["landmarks"].astype(str).str.strip().ne("")
    df = df[has_registration].copy()
    if df.empty:
        raise ValueError("Manifest has no triangle rows.")
    return df


def _parse_labeled_points(text: str, required_labels: Sequence[str]) -> dict[str, np.ndarray]:
    points: dict[str, np.ndarray] = {}
    for chunk in str(text or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError(f"Expected labeled coordinate like 'V1:x,y', got {chunk!r}")
        label, coords = chunk.split(":", 1)
        label = label.strip().upper()
        bits = [b.strip() for b in coords.split(",")]
        if len(bits) != 2:
            raise ValueError(f"Bad coordinate for {label}: {coords!r}")
        points[label] = np.array([float(bits[0]), float(bits[1])], dtype=float)
    missing = [label for label in required_labels if label not in points]
    if missing:
        raise ValueError(f"Missing required landmarks: {', '.join(missing)}")
    return {label: points[label] for label in required_labels}


def _format_corners(corners: dict[str, np.ndarray]) -> str:
    return ";".join(f"{label}:{corners[label][0]:.3f},{corners[label][1]:.3f}" for label in CORNER_LABELS)


def _coordinate_scale_for_row(row: pd.Series, analysis_coordinate_max_dim: int) -> float:
    raw = str(row.get("coordinate_max_dim", "")).strip()
    if not raw:
        return 1.0
    ref = float(raw)
    if ref <= 0:
        return 1.0
    return float(analysis_coordinate_max_dim) / ref


def _scaled_points(points: dict[str, np.ndarray], scale: float) -> dict[str, np.ndarray]:
    return {label: np.asarray(point, dtype=float) * float(scale) for label, point in points.items()}


def _expand_panel_landmark_row(row: pd.Series, analysis_coordinate_max_dim: int) -> list[dict[str, str]]:
    scale = _coordinate_scale_for_row(row, analysis_coordinate_max_dim)
    landmarks = _scaled_points(_parse_labeled_points(str(row["landmarks"]), PANEL_LANDMARK_LABELS), scale)
    subtris = [
        ("corner_1", {"C": landmarks["V1"], "M": landmarks["M12"], "Y": landmarks["M31"]}),
        ("corner_2", {"C": landmarks["M12"], "M": landmarks["V2"], "Y": landmarks["M23"]}),
        ("corner_3", {"C": landmarks["M31"], "M": landmarks["M23"], "Y": landmarks["V3"]}),
        ("center", {"C": landmarks["M12"], "M": landmarks["M23"], "Y": landmarks["M31"]}),
    ]
    rows = []
    for sub_name, corners in subtris:
        out = dict(row)
        out["panel_name"] = str(row["triangle_name"])
        out["triangle_name"] = f"{row['triangle_name']}__{sub_name}"
        out["sub_gamut_name"] = sub_name
        out["corners"] = _format_corners(corners)
        out["landmarks"] = ""
        out["source_registration"] = "panel_landmarks"
        out["coordinate_scale_applied"] = f"{scale:.8g}"
        rows.append(out)
    return rows


def _scale_corner_row(row: pd.Series, analysis_coordinate_max_dim: int) -> dict[str, str]:
    out = dict(row)
    scale = _coordinate_scale_for_row(row, analysis_coordinate_max_dim)
    if scale != 1.0:
        corners = _scaled_points(_parse_labeled_points(str(row["corners"]), CORNER_LABELS), scale)
        out["corners"] = _format_corners(corners)
    out["panel_name"] = str(row.get("panel_name", "") or row["triangle_name"])
    out["sub_gamut_name"] = str(row.get("sub_gamut_name", "") or "")
    out["source_registration"] = "sub_gamut_corners"
    out["coordinate_scale_applied"] = f"{scale:.8g}"
    return out


def _expand_manifest_for_analysis(manifest: pd.DataFrame, analysis_coordinate_max_dim: int) -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    for _, row in manifest.iterrows():
        if str(row.get("landmarks", "")).strip():
            rows.extend(_expand_panel_landmark_row(row, analysis_coordinate_max_dim))
        elif str(row.get("corners", "")).strip():
            rows.append(_scale_corner_row(row, analysis_coordinate_max_dim))
    if not rows:
        raise ValueError("No analyzable rows after manifest expansion.")
    return pd.DataFrame(rows).fillna("")


def _run_single_triangle_subprocess(
    *,
    image_path: Path,
    row: pd.Series,
    out_dir: Path,
    max_analysis_dim: int,
    sample_core_fraction: float,
    lab_trim_percentile: float,
    erosion_px: int,
    min_region_pixels: int,
    max_pixels_per_region: int,
) -> None:
    script = HERE / "analyze_printed_gamut.py"
    cmd = [
        sys.executable,
        str(script),
        "--image",
        str(image_path),
        "--out-dir",
        str(out_dir),
        "--target-name",
        str(row["triangle_name"]),
        "--preset",
        str(row.get("preset") or "normal"),
        "--corners",
        str(row["corners"]),
        "--no-auto-crop",
        "--max-analysis-dim",
        str(max_analysis_dim),
        "--sample-core-fraction",
        str(sample_core_fraction),
        "--lab-trim-percentile",
        str(lab_trim_percentile),
        "--erosion-px",
        str(erosion_px),
        "--min-region-pixels",
        str(min_region_pixels),
        "--max-pixels-per-region",
        str(max_pixels_per_region),
    ]
    for key in ("max_height", "max_run", "distinct_lte"):
        value = str(row.get(key, "")).strip()
        if value:
            cmd.extend([f"--{key.replace('_', '-')}", value])
    subprocess.run(cmd, check=True)


def _analyze_single_triangle_in_process(
    *,
    rgb: np.ndarray,
    lab: np.ndarray,
    color_mask: np.ndarray,
    row: pd.Series,
    sample_core_fraction: float,
    lab_trim_percentile: float,
    erosion_px: int,
    min_region_pixels: int,
    max_pixels_per_region: int,
    random_seed: int,
) -> tuple[pd.DataFrame, np.ndarray, list[np.ndarray], pd.DataFrame]:
    ns = argparse.Namespace(
        preset=str(row.get("preset") or "normal"),
        max_height=int(row["max_height"]) if str(row.get("max_height", "")).strip() else None,
        max_run=int(row["max_run"]) if str(row.get("max_run", "")).strip() else None,
        distinct_lte=int(row["distinct_lte"]) if str(row.get("distinct_lte", "")).strip() else None,
    )
    expected, regions = _build_expected_regions(ns)
    corners = _parse_manual_corners(str(row["corners"]))
    transform = _affine_transform_from_corners(corners)
    measured, label_image, regions_img = _analyze_regions(
        rgb,
        lab,
        color_mask,
        expected,
        regions,
        transform,
        erosion_px=erosion_px,
        min_region_pixels=min_region_pixels,
        max_pixels_per_region=max_pixels_per_region,
        sample_core_fraction=sample_core_fraction,
        lab_trim_percentile=lab_trim_percentile,
        random_seed=random_seed,
    )
    measured.insert(0, "group_name", str(row.get("group_name", "mosaic") or "mosaic"))
    measured.insert(1, "triangle_name", str(row["triangle_name"]))
    measured.insert(2, "panel_name", str(row.get("panel_name", "") or row["triangle_name"]))
    measured.insert(3, "sub_gamut_name", str(row.get("sub_gamut_name", "") or ""))
    measured.insert(4, "source_registration", str(row.get("source_registration", "") or ""))
    if measured.empty:
        return measured, label_image, regions_img, expected
    measured.insert(5, "global_label", measured["triangle_name"].astype(str) + "::" + measured["label"].astype(str))
    return measured, label_image, regions_img, expected


def _plot_panel_landmarks(
    rgb: np.ndarray,
    manifest: pd.DataFrame,
    out_path: Path,
    *,
    analysis_coordinate_max_dim: int,
) -> None:
    fig, ax = plt.subplots(figsize=(16, 7))
    ax.imshow(rgb)
    for _, row in manifest.iterrows():
        if not str(row.get("landmarks", "")).strip():
            continue
        scale = _coordinate_scale_for_row(row, analysis_coordinate_max_dim)
        landmarks = _scaled_points(_parse_labeled_points(str(row["landmarks"]), PANEL_LANDMARK_LABELS), scale)
        outer = np.vstack([landmarks["V1"], landmarks["V2"], landmarks["V3"], landmarks["V1"]])
        mids = np.vstack([landmarks["M12"], landmarks["M23"], landmarks["M31"], landmarks["M12"]])
        ax.plot(outer[:, 0], outer[:, 1], color="white", lw=2.2)
        ax.plot(outer[:, 0], outer[:, 1], color="black", lw=0.65)
        ax.plot(mids[:, 0], mids[:, 1], color="#00ffff", lw=1.2)
        for label, point in landmarks.items():
            ax.scatter([point[0]], [point[1]], s=36, color="#ffdd00", edgecolor="black", linewidth=0.6, zorder=4)
            ax.text(
                point[0],
                point[1],
                label,
                ha="left",
                va="center",
                fontsize=6,
                color="black",
                bbox=dict(facecolor="white", alpha=0.65, edgecolor="none", pad=0.7),
            )
        centroid = np.vstack(list(landmarks.values())).mean(axis=0)
        ax.text(
            centroid[0],
            centroid[1],
            str(row["triangle_name"]),
            ha="center",
            va="center",
            fontsize=7,
            color="black",
            bbox=dict(facecolor="white", alpha=0.65, edgecolor="none", pad=1),
        )
    ax.set_title("Panel landmarks and derived sub-gamut seams")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(out_path, dpi=260)
    plt.close(fig)


def _plot_mosaic_registered_regions(
    rgb: np.ndarray,
    all_regions: Sequence[tuple[str, str, np.ndarray]],
    out_path: Path,
) -> None:
    fig, ax = plt.subplots(figsize=(16, 7))
    ax.imshow(rgb)
    for group_name, triangle_name, poly in all_regions:
        hp = np.asarray(poly, dtype=float)
        patch = plt.Polygon(hp, closed=True, fill=False, edgecolor="white", linewidth=0.7)
        ax.add_patch(patch)
        ax.add_patch(plt.Polygon(hp, closed=True, fill=False, edgecolor="black", linewidth=0.18))
    # Label each sub-gamut once at its first region centroid.
    seen = set()
    for _, triangle_name, poly in all_regions:
        if triangle_name in seen:
            continue
        seen.add(triangle_name)
        hp = np.asarray(poly, dtype=float)
        cx, cy = hp.mean(axis=0)
        ax.text(
            cx,
            cy,
            triangle_name.split("__")[-1],
            ha="center",
            va="center",
            fontsize=5,
            color="black",
            bbox=dict(facecolor="white", alpha=0.55, edgecolor="none", pad=1),
        )
    ax.set_title("Mosaic registered sub-gamut regions")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(out_path, dpi=260)
    plt.close(fig)


def _plot_mosaic_sampling_overlay(
    rgb: np.ndarray,
    all_label_image: np.ndarray,
    measured: pd.DataFrame,
    out_path: Path,
) -> None:
    overlay = rgb.copy() * 0.68 + 0.32
    colors = measured[["measured_R", "measured_G", "measured_B"]].to_numpy(dtype=float)
    for idx, color in enumerate(colors, start=1):
        mask = all_label_image == idx
        if bool(mask.any()):
            overlay[mask] = 0.30 * overlay[mask] + 0.70 * color
    fig, ax = plt.subplots(figsize=(16, 7))
    ax.imshow(overlay)
    ax.set_title(f"Mosaic central sampling regions ({len(measured)} measured regions)")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(out_path, dpi=260)
    plt.close(fig)


def _plot_mosaic_ab_hulls(measured: pd.DataFrame, out_path: Path, *, title: str) -> None:
    fig, ax = plt.subplots(figsize=(8.2, 7.2))
    groups = list(dict.fromkeys(measured["group_name"].astype(str)))
    cmap = plt.get_cmap("tab10")

    all_ab = measured[["measured_a", "measured_b"]].to_numpy(dtype=float)
    all_rgb = measured[["measured_R", "measured_G", "measured_B"]].to_numpy(dtype=float)

    if len(all_ab) >= 3:
        hp = _convex_hull_points(all_ab)
        hp = np.vstack([hp, hp[0]])
        ax.fill(hp[:, 0], hp[:, 1], color="0.5", alpha=0.07, label="All measured hull")
        ax.plot(hp[:, 0], hp[:, 1], color="black", lw=1.8)

    for idx, group in enumerate(groups):
        sub = measured[measured["group_name"].astype(str) == group]
        pts = sub[["measured_a", "measured_b"]].to_numpy(dtype=float)
        color = cmap(idx % 10)
        if len(pts) >= 3:
            hp = _convex_hull_points(pts)
            hp = np.vstack([hp, hp[0]])
            ax.plot(hp[:, 0], hp[:, 1], color=color, lw=1.5, alpha=0.95, label=f"{group} hull")

    ax.scatter(all_ab[:, 0], all_ab[:, 1], c=all_rgb, s=52, edgecolor="black", linewidth=0.25, zorder=5)
    ax.axhline(0, color="0.88", lw=0.8)
    ax.axvline(0, color="0.88", lw=0.8)
    ax.set_xlabel("a*")
    ax.set_ylabel("b*")
    ax.set_title(title)
    ax.set_aspect("equal", adjustable="box")
    ax.legend(frameon=False, fontsize=8, loc="best")
    fig.tight_layout()
    fig.savefig(out_path, dpi=320)
    plt.close(fig)


def _plot_triangle_hull_bars(group_summary: pd.DataFrame, out_path: Path) -> None:
    ordered = group_summary.sort_values("measured_ab_hull_area", ascending=False).reset_index(drop=True)
    fig, ax = plt.subplots(figsize=(max(7.0, 0.5 * len(ordered)), 4.5))
    x = np.arange(len(ordered))
    vals = ordered["measured_ab_hull_area"].to_numpy(dtype=float)
    bars = ax.bar(x, vals, color="#22577a", edgecolor="black", linewidth=0.7)
    for bar, val in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, val, f"{val:.0f}", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(ordered["group_name"].astype(str), rotation=35, ha="right")
    ax.set_ylabel("Measured a*b* hull area")
    ax.set_title("Measured gamut hull area by triangle group")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out_path, dpi=320)
    plt.close(fig)


def _summarize_groups(measured: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for group, sub in measured.groupby("group_name", sort=False):
        measured_ab = sub[["measured_a", "measured_b"]].to_numpy(dtype=float)
        expected_ab = sub[["expected_a", "expected_b"]].to_numpy(dtype=float)
        labels = sub["global_label"].astype(str).tolist()
        pairwise = _pairwise_delta_e00(sub[["measured_L", "measured_a", "measured_b"]].to_numpy(), labels)
        row = {
            "group_name": group,
            "triangle_count": int(sub["triangle_name"].nunique()),
            "region_count": int(len(sub)),
            "measured_ab_hull_area": _safe_hull_area(measured_ab),
            "expected_ab_hull_area": _safe_hull_area(expected_ab),
            "median_deltaE00_to_expected": float(sub["deltaE00_to_expected"].median()),
            "median_within_region_deltaE00": float(sub["within_region_deltaE00_median"].median()),
            "min_pairwise_deltaE00": float(pairwise["deltaE00"].min()) if not pairwise.empty else np.nan,
        }
        for threshold in (2.0, 5.0, 10.0):
            row[f"distinguishable_colors_deltaE00_lt_{threshold:g}"] = int(
                _distinguishable_counts(pairwise, labels)
                .set_index("threshold_deltaE00")
                .loc[threshold, "distinguishable_color_count"]
            )
        rows.append(row)
    return pd.DataFrame(rows)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Analyze a multi-triangle LayerLoom gamut photo.")
    p.add_argument("--image", required=True, help="Photo containing multiple triangular gamuts.")
    p.add_argument("--manifest", help="CSV describing each sub-gamut triangle and its C/M/Y corners.")
    p.add_argument("--out-dir", help="Output directory. Defaults to <image_stem>__mosaic_analysis.")
    p.add_argument("--guide-only", action="store_true", help="Only write a coordinate guide and manifest template.")
    p.add_argument("--max-analysis-dim", type=int, default=2400)
    p.add_argument("--sample-core-fraction", type=float, default=0.55)
    p.add_argument("--lab-trim-percentile", type=float, default=98.0)
    p.add_argument("--erosion-px", type=int, default=8)
    p.add_argument("--min-region-pixels", type=int, default=40)
    p.add_argument("--max-pixels-per-region", type=int, default=12000)
    p.add_argument(
        "--write-per-triangle-reports",
        action="store_true",
        help="Also run the full single-triangle analyzer for every manifest row. Slow, but creates detailed per-triangle figures.",
    )
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    image_path = Path(args.image).expanduser().resolve()
    if not image_path.exists():
        raise FileNotFoundError(image_path)
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else image_path.with_name(f"{image_path.stem}__mosaic_analysis")
    dirs = _ensure_dirs(out_dir)

    rgb_full = np.asarray(Image.open(image_path).convert("RGB"), dtype=np.float32) / 255.0
    rgb, analysis_scale = _resize_for_analysis(rgb_full, args.max_analysis_dim)
    lab = _rgb_to_lab(rgb)
    color_mask = _build_color_mask(
        rgb,
        min_saturation=0.12,
        min_value=0.05,
        max_value=0.99,
        min_chroma=7.0,
        cleanup_radius=2,
    )
    Image.fromarray(np.clip(rgb * 255, 0, 255).astype(np.uint8), mode="RGB").save(dirs["qc"] / "00_analysis_input.png")
    _plot_corner_guide(rgb, dirs["qc"] / "00_corner_coordinate_guide.png", title="Mosaic corner coordinate guide")

    template_path = dirs["root"] / "mosaic_manifest_template.csv"
    if args.guide_only:
        _write_manifest_template(template_path)
        config = {
            "image": str(image_path),
            "out_dir": str(out_dir),
            "analysis_scale": analysis_scale,
            "analysis_image_shape_hw": list(rgb.shape[:2]),
            "corner_coordinate_space": "analysis image after optional resize; use qc/00_corner_coordinate_guide.png",
        }
        (dirs["root"] / "analysis_config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        print(f"[done] guide-only outputs: {out_dir}")
        print(f"[done] manifest template: {template_path}")
        return 0

    if not args.manifest:
        raise ValueError("--manifest is required unless --guide-only is used.")
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest = _load_manifest(manifest_path)
    manifest.to_csv(dirs["tables"] / "mosaic_manifest_input.csv", index=False)
    analysis_coordinate_max_dim = max(rgb.shape[:2])
    expanded_manifest = _expand_manifest_for_analysis(manifest, analysis_coordinate_max_dim)
    expanded_manifest.to_csv(dirs["tables"] / "mosaic_manifest_used.csv", index=False)
    _plot_panel_landmarks(
        rgb,
        manifest,
        dirs["qc"] / "01_panel_landmarks.png",
        analysis_coordinate_max_dim=analysis_coordinate_max_dim,
    )

    measured_tables = []
    summary_tables = []
    all_regions_for_qc: list[tuple[str, str, np.ndarray]] = []
    aggregate_label_image = np.zeros(rgb.shape[:2], dtype=np.uint16)
    global_region_index = 1
    for index, row in expanded_manifest.iterrows():
        triangle_name = _safe_name(row["triangle_name"])
        triangle_out = dirs["per_triangle"] / triangle_name
        print(f"[mosaic] analyzing {triangle_name} ({index + 1}/{len(expanded_manifest)})")
        measured, label_image, regions_img, expected = _analyze_single_triangle_in_process(
            rgb=rgb,
            lab=lab,
            color_mask=color_mask,
            row=row,
            sample_core_fraction=args.sample_core_fraction,
            lab_trim_percentile=args.lab_trim_percentile,
            erosion_px=args.erosion_px,
            min_region_pixels=args.min_region_pixels,
            max_pixels_per_region=args.max_pixels_per_region,
            random_seed=1 + int(index),
        )
        if measured.empty:
            print(f"[mosaic] warning: no measured regions for {triangle_name}; check corner coordinates/min-region-pixels", flush=True)
            continue
        if args.write_per_triangle_reports:
            _run_single_triangle_subprocess(
                image_path=image_path,
                row=row,
                out_dir=triangle_out,
                max_analysis_dim=args.max_analysis_dim,
                sample_core_fraction=args.sample_core_fraction,
                lab_trim_percentile=args.lab_trim_percentile,
                erosion_px=args.erosion_px,
                min_region_pixels=args.min_region_pixels,
                max_pixels_per_region=args.max_pixels_per_region,
            )
        triangle_out.mkdir(parents=True, exist_ok=True)
        (triangle_out / "tables").mkdir(parents=True, exist_ok=True)
        measured.to_csv(triangle_out / "tables" / "measured_regions.csv", index=False)
        expected.to_csv(triangle_out / "tables" / "expected_regions.csv", index=False)
        measured_tables.append(measured)

        for poly in regions_img:
            all_regions_for_qc.append((str(row.get("group_name", "mosaic") or "mosaic"), str(row["triangle_name"]), poly))
        for local_idx in range(1, int(label_image.max()) + 1):
            local_mask = label_image == local_idx
            if bool(local_mask.any()):
                aggregate_label_image[local_mask] = global_region_index
                global_region_index += 1

        labels = measured["global_label"].astype(str).tolist()
        pairwise_local = _pairwise_delta_e00(measured[["measured_L", "measured_a", "measured_b"]].to_numpy(), labels)
        summary_tables.append(
            pd.DataFrame(
                [
                    {
                        "group_name": str(row.get("group_name", "mosaic") or "mosaic"),
                        "triangle_name": str(row["triangle_name"]),
                        "panel_name": str(row.get("panel_name", "") or row["triangle_name"]),
                        "sub_gamut_name": str(row.get("sub_gamut_name", "") or ""),
                        "n_regions_expected": int(len(expected)),
                        "n_regions_measured": int(len(measured)),
                        "preset": str(row.get("preset") or "normal"),
                        "median_deltaE00_to_expected": float(measured["deltaE00_to_expected"].median()),
                        "median_within_region_deltaE00": float(measured["within_region_deltaE00_median"].median()),
                        "measured_ab_hull_area": _safe_hull_area(measured[["measured_a", "measured_b"]].to_numpy()),
                        "min_pairwise_deltaE00": float(pairwise_local["deltaE00"].min()) if not pairwise_local.empty else np.nan,
                    }
                ]
            )
        )

    all_measured = pd.concat(measured_tables, ignore_index=True)
    all_summary = pd.concat(summary_tables, ignore_index=True)
    all_measured.to_csv(dirs["tables"] / "all_measured_regions.csv", index=False)
    all_summary.to_csv(dirs["tables"] / "all_triangle_summaries.csv", index=False)
    counts = (
        all_measured.groupby(["group_name", "panel_name", "triangle_name", "sub_gamut_name"], dropna=False)
        .agg(
            measured_region_count=("region_index", "count"),
            total_sample_pixels=("sample_pixels", "sum"),
            median_sample_pixels=("sample_pixels", "median"),
            median_deltaE00_to_expected=("deltaE00_to_expected", "median"),
            median_within_region_deltaE00=("within_region_deltaE00_median", "median"),
        )
        .reset_index()
    )
    counts.to_csv(dirs["tables"] / "sub_gamut_sample_counts.csv", index=False)

    labels = all_measured["global_label"].astype(str).tolist()
    all_pairwise = _pairwise_delta_e00(all_measured[["measured_L", "measured_a", "measured_b"]].to_numpy(), labels)
    all_pairwise.to_csv(dirs["tables"] / "all_pairwise_deltaE00.csv", index=False)
    _distinguishable_counts(all_pairwise, labels).to_csv(dirs["tables"] / "all_distinguishable_color_counts.csv", index=False)

    group_summary = _summarize_groups(all_measured)
    group_summary.to_csv(dirs["tables"] / "group_summary.csv", index=False)
    np.savez_compressed(dirs["root"] / "mosaic_sample_label_image.npz", label_image=aggregate_label_image)

    overall = {
        "image": str(image_path),
        "manifest": str(manifest_path),
        "panel_or_manifest_row_count": int(len(manifest)),
        "triangle_count": int(len(expanded_manifest)),
        "region_count": int(len(all_measured)),
        "group_count": int(all_measured["group_name"].nunique()),
        "measured_ab_hull_area": _safe_hull_area(all_measured[["measured_a", "measured_b"]].to_numpy()),
        "expected_ab_hull_area": _safe_hull_area(all_measured[["expected_a", "expected_b"]].to_numpy()),
        "median_deltaE00_to_expected": float(all_measured["deltaE00_to_expected"].median()),
        "median_within_region_deltaE00": float(all_measured["within_region_deltaE00_median"].median()),
        "min_pairwise_deltaE00": float(all_pairwise["deltaE00"].min()) if not all_pairwise.empty else math.nan,
    }
    pd.DataFrame([overall]).to_csv(dirs["tables"] / "overall_summary.csv", index=False)

    _plot_mosaic_ab_hulls(all_measured, dirs["figures"] / "01_mosaic_measured_ab_hulls.png", title="Measured printed gamut hulls across mosaic")
    _plot_triangle_hull_bars(group_summary, dirs["figures"] / "02_group_hull_area_bars.png")
    _plot_mosaic_registered_regions(rgb, all_regions_for_qc, dirs["qc"] / "01_mosaic_registered_regions.png")
    _plot_mosaic_sampling_overlay(rgb, aggregate_label_image, all_measured, dirs["qc"] / "02_mosaic_sampling_overlay.png")

    config = {
        "image": str(image_path),
        "manifest": str(manifest_path),
        "out_dir": str(out_dir),
        "analysis_scale": analysis_scale,
        "analysis_image_shape_hw": list(rgb.shape[:2]),
        "analysis_coordinate_max_dim": analysis_coordinate_max_dim,
        "corner_coordinate_space": "analysis image after optional resize; use qc/00_corner_coordinate_guide.png",
        "manifest_input_rows": int(len(manifest)),
        "expanded_sub_gamut_rows": int(len(expanded_manifest)),
        "sample_core_fraction": args.sample_core_fraction,
        "lab_trim_percentile": args.lab_trim_percentile,
    }
    (dirs["root"] / "analysis_config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

    print(f"[done] analyzed {len(expanded_manifest)} sub-gamuts; {len(all_measured)} total regions")
    print(f"[done] outputs: {out_dir}")
    print(pd.DataFrame([overall]).T.to_string(header=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
