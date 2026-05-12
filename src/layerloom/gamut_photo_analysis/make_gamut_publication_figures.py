#!/usr/bin/env python3
"""Build publication-oriented summary figures from the current three gamut photos."""

from __future__ import annotations

import argparse
import math
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

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
    _convex_hull_points,
    _distinguishable_counts,
    _pairwise_delta_e00,
    _polygon_area,
)


@dataclass(frozen=True)
class Dataset:
    key: str
    label: str
    image_path: Path
    analysis_dir: Path
    measured_csv: Path
    raw_measured_csv: Path
    supports_expected: bool
    color: str


def _default_datasets(root: Path) -> list[Dataset]:
    simple_dir = root / "simple_example_manual_core065"
    full_dir = root / "example_gamut_manual_core065"
    wide_dir = root / "LayerLoom_figure5__auto_fit2400_core065"
    wide_sym = wide_dir / "symmetry_class_collapse"
    return [
        Dataset(
            key="simple",
            label="Simple CMY",
            image_path=root / "simple_example.png",
            analysis_dir=simple_dir,
            measured_csv=simple_dir / "tables" / "measured_regions.csv",
            raw_measured_csv=simple_dir / "tables" / "measured_regions.csv",
            supports_expected=True,
            color="#2a9d8f",
        ),
        Dataset(
            key="full",
            label="Full CMY",
            image_path=root / "example_gamut.png",
            analysis_dir=full_dir,
            measured_csv=full_dir / "tables" / "measured_regions.csv",
            raw_measured_csv=full_dir / "tables" / "measured_regions.csv",
            supports_expected=True,
            color="#1f77b4",
        ),
        Dataset(
            key="wide",
            label="Wide OVG/neutral mosaic\n(symmetry manifest)",
            image_path=root / "LayerLoom_figure5.png",
            analysis_dir=wide_dir,
            measured_csv=wide_sym / "tables" / "manifest_classes_collapsed_regions.csv",
            raw_measured_csv=wide_dir / "tables" / "all_measured_regions.csv",
            supports_expected=False,
            color="#d95f02",
        ),
    ]


def _load_rgb(path: Path) -> Image.Image:
    return Image.open(path).convert("RGB")


def _read_measured(dataset: Dataset) -> pd.DataFrame:
    df = pd.read_csv(dataset.measured_csv)
    if "measured_R" not in df.columns:
        raise ValueError(f"{dataset.measured_csv} is missing measured RGB/Lab columns")
    return df


def _read_raw_measured(dataset: Dataset) -> pd.DataFrame:
    return pd.read_csv(dataset.raw_measured_csv)


def _hull_area(df: pd.DataFrame) -> float:
    points = df[["measured_a", "measured_b"]].to_numpy(dtype=float)
    if len(points) < 3:
        return math.nan
    return float(_polygon_area(_convex_hull_points(points)))


def _distinguishable_summary(df: pd.DataFrame) -> pd.DataFrame:
    labels = (
        df["symmetry_key"].astype(str).tolist()
        if "symmetry_key" in df.columns
        else df.get("label", df.index.to_series()).astype(str).tolist()
    )
    pairwise = _pairwise_delta_e00(df[["measured_L", "measured_a", "measured_b"]].to_numpy(dtype=float), labels)
    return _distinguishable_counts(pairwise, labels)


def _scatter_ab(ax, df: pd.DataFrame, *, title: str, hull_color: str, point_size: float = 36.0) -> None:
    pts = df[["measured_a", "measured_b"]].to_numpy(dtype=float)
    rgb = df[["measured_R", "measured_G", "measured_B"]].to_numpy(dtype=float)
    ax.scatter(pts[:, 0], pts[:, 1], c=np.clip(rgb, 0, 1), s=point_size, edgecolor="0.14", linewidth=0.35)
    hull = _convex_hull_points(pts)
    if len(hull) >= 3:
        closed = np.vstack([hull, hull[0]])
        ax.plot(closed[:, 0], closed[:, 1], color=hull_color, lw=2.2)
        ax.fill(closed[:, 0], closed[:, 1], color=hull_color, alpha=0.08)
    ax.axhline(0, color="0.88", lw=0.8)
    ax.axvline(0, color="0.88", lw=0.8)
    ax.set_title(title)
    ax.set_xlabel("a*")
    ax.set_ylabel("b*")
    ax.set_aspect("equal", adjustable="box")


def _plot_workflow_montage(datasets: list[Dataset], out_path: Path) -> None:
    columns = _workflow_panel_specs()
    fig, axes = plt.subplots(
        len(datasets),
        len(columns),
        figsize=(22.0, 13.8),
        gridspec_kw={
            "width_ratios": [1.24, 1.24, 1.24, 1.55],
            "height_ratios": [1.12, 1.12, 0.86],
            "wspace": 0.10,
            "hspace": 0.18,
        },
    )
    for r, dataset in enumerate(datasets):
        for c, spec in enumerate(columns):
            ax = axes[r, c]
            path = spec["resolver"](dataset)
            if path.exists():
                ax.imshow(_load_rgb(path))
            else:
                ax.text(0.5, 0.5, f"Missing\n{path.name}", ha="center", va="center")
            ax.set_xticks([])
            ax.set_yticks([])
            if r == 0:
                ax.set_title(spec["title"], fontsize=13, pad=8)
            if c == 0:
                ax.set_ylabel(dataset.label, fontsize=13, labelpad=10)
            for spine in ax.spines.values():
                spine.set_linewidth(0.8)
                spine.set_edgecolor("0.25")
    fig.suptitle("Automated photo registration and measurement workflow", fontsize=17, y=0.985)
    fig.subplots_adjust(left=0.055, right=0.992, top=0.94, bottom=0.045)
    fig.savefig(out_path, dpi=300)
    plt.close(fig)


def _workflow_panel_specs() -> list[dict[str, object]]:
    return [
        {
            "title": "Raw/cropped photo",
            "slug": "raw_cropped_photo",
            "description": "Raw/cropped input photograph used for registration.",
            "resolver": lambda d: d.analysis_dir / "qc" / "00_analysis_input.png",
        },
        {
            "title": "Registered regions",
            "slug": "registered_regions",
            "description": "Expected region boundaries registered to the photograph.",
            "resolver": lambda d: d.analysis_dir / "qc" / ("01_mosaic_registered_regions.png" if d.key == "wide" else "03_region_overlay.png"),
        },
        {
            "title": "Sampled interiors",
            "slug": "sampled_interiors",
            "description": "Central-core sampling masks used to avoid seams, shadows, and region borders.",
            "resolver": lambda d: d.analysis_dir / "qc" / ("02_mosaic_sampling_overlay.png" if d.key == "wide" else "04_sampling_overlay.png"),
        },
        {
            "title": "Extracted colors / hull QC",
            "slug": "extracted_colors_hull_qc",
            "description": "Extracted per-region colors or measured CIE a*b* hull quality-control plot.",
            "resolver": lambda d: d.analysis_dir / ("figures/01_mosaic_measured_ab_hulls.png" if d.key == "wide" else "qc/05_extracted_color_grid.png"),
        },
    ]


def _export_workflow_panels(datasets: list[Dataset], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    panel_index = 0
    specs = _workflow_panel_specs()
    for dataset in datasets:
        dataset_slug = dataset.key if dataset.key != "wide" else "wide_ovg_neutral_mosaic"
        for spec in specs:
            panel_letter = chr(ord("A") + panel_index)
            panel_index += 1
            src = spec["resolver"](dataset)
            filename = f"{panel_letter}_{dataset_slug}_{spec['slug']}.png"
            dst = out_dir / filename
            if src.exists():
                shutil.copyfile(src, dst)
                with Image.open(dst) as im:
                    width, height = im.size
            else:
                width = height = math.nan
            rows.append(
                {
                    "panel": panel_letter,
                    "dataset": dataset.key,
                    "dataset_label": dataset.label.replace("\n", " "),
                    "panel_type": spec["slug"],
                    "panel_title": spec["title"],
                    "description": spec["description"],
                    "filename": filename,
                    "source_path": str(src),
                    "width_px": width,
                    "height_px": height,
                    "exists": bool(src.exists()),
                }
            )
    pd.DataFrame(rows).to_csv(out_dir / "panel_manifest.csv", index=False)


def _plot_ab_side_by_side(datasets: list[Dataset], measured: dict[str, pd.DataFrame], out_path: Path) -> None:
    fig, axes = plt.subplots(1, len(datasets), figsize=(5.3 * len(datasets), 5.6), squeeze=False)
    all_points = []
    for dataset in datasets:
        all_points.append(measured[dataset.key][["measured_a", "measured_b"]].to_numpy(dtype=float))
    stacked = np.vstack(all_points)
    x0, x1 = np.percentile(stacked[:, 0], [0.5, 99.5])
    y0, y1 = np.percentile(stacked[:, 1], [0.5, 99.5])
    pad = max(x1 - x0, y1 - y0) * 0.10

    for ax, dataset in zip(axes[0], datasets):
        df = measured[dataset.key]
        _scatter_ab(ax, df, title=f"{dataset.label}\n{len(df)} plotted colors", hull_color=dataset.color)
        ax.set_xlim(x0 - pad, x1 + pad)
        ax.set_ylim(y0 - pad, y1 + pad)
    fig.suptitle("Measured printed gamuts in CIE a*b*", fontsize=16)
    fig.tight_layout()
    fig.savefig(out_path, dpi=260)
    plt.close(fig)


def _plot_combined_hull(datasets: list[Dataset], measured: dict[str, pd.DataFrame], out_path: Path, *, include_keys: set[str] | None = None) -> None:
    selected = [d for d in datasets if include_keys is None or d.key in include_keys]
    fig, ax = plt.subplots(figsize=(7.2, 7.0))
    union = []
    for dataset in selected:
        df = measured[dataset.key]
        pts = df[["measured_a", "measured_b"]].to_numpy(dtype=float)
        rgb = df[["measured_R", "measured_G", "measured_B"]].to_numpy(dtype=float)
        union.append(pts)
        ax.scatter(pts[:, 0], pts[:, 1], c=np.clip(rgb, 0, 1), s=30, edgecolor=dataset.color, linewidth=0.6, alpha=0.92, label=dataset.label)
        hull = _convex_hull_points(pts)
        if len(hull) >= 3:
            closed = np.vstack([hull, hull[0]])
            ax.plot(closed[:, 0], closed[:, 1], color=dataset.color, lw=1.8)
    combined = np.vstack(union)
    hull = _convex_hull_points(combined)
    if len(hull) >= 3:
        closed = np.vstack([hull, hull[0]])
        ax.plot(closed[:, 0], closed[:, 1], color="black", lw=3.0, label="combined hull")
        ax.fill(closed[:, 0], closed[:, 1], color="0.1", alpha=0.06)
    ax.axhline(0, color="0.88", lw=0.8)
    ax.axvline(0, color="0.88", lw=0.8)
    ax.set_xlabel("a*")
    ax.set_ylabel("b*")
    ax.set_aspect("equal", adjustable="box")
    ax.set_title("Combined measured gamut space")
    ax.legend(fontsize=8, frameon=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=260)
    plt.close(fig)


def _plot_distinguishable_counts(datasets: list[Dataset], counts: dict[str, pd.DataFrame], out_path: Path) -> None:
    thresholds = [2.0, 5.0, 10.0]
    x = np.arange(len(thresholds))
    width = 0.24
    fig, ax = plt.subplots(figsize=(8.0, 5.0))
    for i, dataset in enumerate(datasets):
        df = counts[dataset.key].set_index("threshold_deltaE00")
        vals = [float(df.loc[t, "distinguishable_color_count"]) for t in thresholds]
        offsets = x + (i - (len(datasets) - 1) / 2) * width
        bars = ax.bar(offsets, vals, width=width, color=dataset.color, edgecolor="black", linewidth=0.7, label=dataset.label)
        for bar, val in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2, val + 1.0, f"{int(val)}", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels([f"ΔE00 < {t:g}" for t in thresholds])
    ax.set_ylabel("Effective distinguishable color count")
    ax.set_title("Distinguishable printed colors after symmetry-aware summarization")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="0.9", lw=0.8)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=260)
    plt.close(fig)


def _plot_uniformity(datasets: list[Dataset], raw_measured: dict[str, pd.DataFrame], out_path: Path) -> None:
    vals = []
    labels = []
    colors = []
    for dataset in datasets:
        df = raw_measured[dataset.key]
        if "within_region_deltaE00_median" not in df.columns:
            continue
        vals.append(df["within_region_deltaE00_median"].dropna().to_numpy(dtype=float))
        labels.append(dataset.label)
        colors.append(dataset.color)
    fig, ax = plt.subplots(figsize=(7.4, 4.8))
    parts = ax.boxplot(vals, labels=labels, patch_artist=True, showfliers=False)
    for patch, color in zip(parts["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.45)
    ax.set_ylabel("Within-region median ΔE00")
    ax.set_title("Patch uniformity / layer-line texture proxy")
    ax.grid(axis="y", color="0.9", lw=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out_path, dpi=260)
    plt.close(fig)


def _plot_hull_area(datasets: list[Dataset], measured: dict[str, pd.DataFrame], out_path: Path) -> None:
    vals = [_hull_area(measured[d.key]) for d in datasets]
    fig, ax = plt.subplots(figsize=(7.0, 4.7))
    bars = ax.bar(np.arange(len(datasets)), vals, color=[d.color for d in datasets], edgecolor="black", linewidth=0.8)
    for bar, val in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, val + max(vals) * 0.015, f"{val:,.0f}", ha="center", va="bottom", fontsize=9)
    ax.set_xticks(np.arange(len(datasets)))
    ax.set_xticklabels([d.label for d in datasets])
    ax.set_ylabel("Measured a*b* hull area")
    ax.set_title("Measured color-space coverage")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="0.9", lw=0.8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=260)
    plt.close(fig)


def _plot_expected_shift(datasets: list[Dataset], measured: dict[str, pd.DataFrame], out_path: Path) -> None:
    cmy = [d for d in datasets if d.supports_expected]
    fig, axes = plt.subplots(1, len(cmy), figsize=(5.7 * len(cmy), 5.5), squeeze=False)
    all_points = []
    for dataset in cmy:
        df = measured[dataset.key]
        all_points.append(df[["expected_a", "expected_b"]].to_numpy(dtype=float))
        all_points.append(df[["measured_a", "measured_b"]].to_numpy(dtype=float))
    stacked = np.vstack(all_points)
    x0, x1 = np.percentile(stacked[:, 0], [0.5, 99.5])
    y0, y1 = np.percentile(stacked[:, 1], [0.5, 99.5])
    pad = max(x1 - x0, y1 - y0) * 0.10

    for ax, dataset in zip(axes[0], cmy):
        df = measured[dataset.key]
        expected = df[["expected_a", "expected_b"]].to_numpy(dtype=float)
        actual = df[["measured_a", "measured_b"]].to_numpy(dtype=float)
        rgb = df[["measured_R", "measured_G", "measured_B"]].to_numpy(dtype=float)
        for exp, act in zip(expected, actual):
            ax.annotate("", xy=act, xytext=exp, arrowprops=dict(arrowstyle="->", color="0.35", lw=0.6, alpha=0.65))
        ax.scatter(expected[:, 0], expected[:, 1], s=22, c="white", edgecolor="0.2", linewidth=0.5, label="expected")
        ax.scatter(actual[:, 0], actual[:, 1], s=42, c=np.clip(rgb, 0, 1), edgecolor="0.15", linewidth=0.35, label="measured")
        ax.set_title(dataset.label)
        ax.set_xlabel("a*")
        ax.set_ylabel("b*")
        ax.set_xlim(x0 - pad, x1 + pad)
        ax.set_ylim(y0 - pad, y1 + pad)
        ax.axhline(0, color="0.88", lw=0.8)
        ax.axvline(0, color="0.88", lw=0.8)
        ax.set_aspect("equal", adjustable="box")
    axes[0, 0].legend(fontsize=8)
    fig.suptitle("Expected-to-measured color shifts for CMY gamuts", fontsize=15)
    fig.tight_layout()
    fig.savefig(out_path, dpi=260)
    plt.close(fig)


def _plot_error_summary(datasets: list[Dataset], measured: dict[str, pd.DataFrame], out_path: Path) -> None:
    cmy = [d for d in datasets if d.supports_expected]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.6))
    delta_vals = [measured[d.key]["deltaE00_to_expected"].dropna().to_numpy(dtype=float) for d in cmy]
    uniform_vals = [measured[d.key]["within_region_deltaE00_median"].dropna().to_numpy(dtype=float) for d in cmy]
    for ax, vals, ylabel, title in (
        (axes[0], delta_vals, "ΔE00 to expected", "Expected color error"),
        (axes[1], uniform_vals, "Within-region median ΔE00", "Uniformity"),
    ):
        parts = ax.boxplot(vals, labels=[d.label for d in cmy], patch_artist=True, showfliers=False)
        for patch, dataset in zip(parts["boxes"], cmy):
            patch.set_facecolor(dataset.color)
            patch.set_alpha(0.45)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(axis="y", color="0.9", lw=0.8)
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out_path, dpi=260)
    plt.close(fig)


def _plot_map_montage(datasets: list[Dataset], out_path: Path) -> None:
    cmy = [d for d in datasets if d.supports_expected]
    columns = _cmy_error_panel_specs()
    fig, axes = plt.subplots(len(cmy), len(columns), figsize=(14, 4.3 * len(cmy)))
    for r, dataset in enumerate(cmy):
        for c, spec in enumerate(columns):
            ax = axes[r, c]
            path = dataset.analysis_dir / spec["rel_path"]
            if path.exists():
                ax.imshow(_load_rgb(path))
            else:
                ax.text(0.5, 0.5, f"Missing\n{Path(spec['rel_path']).name}", ha="center", va="center")
            ax.set_xticks([])
            ax.set_yticks([])
            if r == 0:
                ax.set_title(spec["title"])
            if c == 0:
                ax.set_ylabel(dataset.label, fontsize=12)
    fig.suptitle("CMY expected-color error and uniformity maps", fontsize=15)
    fig.tight_layout()
    fig.savefig(out_path, dpi=220)
    plt.close(fig)


def _cmy_error_panel_specs() -> list[dict[str, str]]:
    return [
        {
            "title": "ΔE00 error map",
            "slug": "deltaE00_error_map",
            "rel_path": "figures/03_deltaE00_region_map.png",
            "description": "CIEDE2000 difference between each measured printed region and its expected nominal CMY color.",
        },
        {
            "title": "Uniformity map",
            "slug": "within_region_uniformity_map",
            "rel_path": "figures/04_uniformity_region_map.png",
            "description": "Within-region color variation, measured as median CIEDE2000 distance from sampled pixels to the region median.",
        },
        {
            "title": "ΔE00 map + histogram",
            "slug": "deltaE00_map_with_histogram",
            "rel_path": "figures/07_deltaE00_map_with_histogram.png",
            "description": "Spatial ΔE00 error map paired with the corresponding region-level error distribution.",
        },
    ]


def _export_cmy_error_panels(datasets: list[Dataset], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    panel_index = 0
    for dataset in [d for d in datasets if d.supports_expected]:
        for spec in _cmy_error_panel_specs():
            panel_letter = chr(ord("A") + panel_index)
            panel_index += 1
            src = dataset.analysis_dir / spec["rel_path"]
            filename = f"{panel_letter}_{dataset.key}_{spec['slug']}.png"
            dst = out_dir / filename
            if src.exists():
                shutil.copyfile(src, dst)
                with Image.open(dst) as im:
                    width, height = im.size
            else:
                width = height = math.nan
            rows.append(
                {
                    "panel": panel_letter,
                    "dataset": dataset.key,
                    "dataset_label": dataset.label.replace("\n", " "),
                    "panel_type": spec["slug"],
                    "panel_title": spec["title"],
                    "description": spec["description"],
                    "filename": filename,
                    "source_path": str(src),
                    "width_px": width,
                    "height_px": height,
                    "exists": bool(src.exists()),
                }
            )
    pd.DataFrame(rows).to_csv(out_dir / "panel_manifest.csv", index=False)


def _write_summary(datasets: list[Dataset], measured: dict[str, pd.DataFrame], counts: dict[str, pd.DataFrame], raw_measured: dict[str, pd.DataFrame], out_path: Path) -> None:
    rows = []
    for dataset in datasets:
        df = measured[dataset.key]
        c = counts[dataset.key].set_index("threshold_deltaE00")
        raw = raw_measured[dataset.key]
        rows.append(
            {
                "dataset": dataset.key,
                "label": dataset.label.replace("\n", " "),
                "plotted_region_count": int(len(df)),
                "raw_measured_region_count": int(len(raw)),
                "measured_ab_hull_area": _hull_area(df),
                "distinguishable_colors_deltaE00_lt_2": int(c.loc[2.0, "distinguishable_color_count"]),
                "distinguishable_colors_deltaE00_lt_5": int(c.loc[5.0, "distinguishable_color_count"]),
                "distinguishable_colors_deltaE00_lt_10": int(c.loc[10.0, "distinguishable_color_count"]),
                "median_within_region_deltaE00": float(raw["within_region_deltaE00_median"].median()) if "within_region_deltaE00_median" in raw.columns else math.nan,
                "median_deltaE00_to_expected": float(df["deltaE00_to_expected"].median()) if "deltaE00_to_expected" in df.columns else math.nan,
                "measured_csv": str(dataset.measured_csv),
                "raw_measured_csv": str(dataset.raw_measured_csv),
            }
        )
    pd.DataFrame(rows).to_csv(out_path, index=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(HERE), help="gamut_photo_analysis directory")
    parser.add_argument("--out-dir", default=str(HERE / "publication_gamut_figures_core065"))
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    datasets = _default_datasets(root)

    missing = [str(path) for d in datasets for path in (d.measured_csv, d.raw_measured_csv) if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing required analysis tables:\n" + "\n".join(missing))

    measured = {d.key: _read_measured(d) for d in datasets}
    raw_measured = {d.key: _read_raw_measured(d) for d in datasets}
    counts = {d.key: _distinguishable_summary(measured[d.key]) for d in datasets}

    _plot_workflow_montage(datasets, out_dir / "00_registration_workflow_three_inputs.png")
    _export_workflow_panels(datasets, out_dir / "00_registration_workflow_panels")
    _plot_ab_side_by_side(datasets, measured, out_dir / "01_measured_ab_hulls_three_inputs.png")
    _plot_combined_hull(datasets, measured, out_dir / "02_combined_ab_hull_all_three.png")
    _plot_combined_hull(datasets, measured, out_dir / "02b_combined_ab_hull_full_plus_wide.png", include_keys={"full", "wide"})
    _plot_distinguishable_counts(datasets, counts, out_dir / "03_distinguishable_color_counts.png")
    _plot_uniformity(datasets, raw_measured, out_dir / "04_within_region_uniformity.png")
    _plot_hull_area(datasets, measured, out_dir / "05_hull_area_bars.png")
    _plot_expected_shift(datasets, measured, out_dir / "06_expected_vs_measured_shifts_cmy.png")
    _plot_error_summary(datasets, measured, out_dir / "07_cmy_error_uniformity_summary.png")
    _plot_map_montage(datasets, out_dir / "08_cmy_error_uniformity_maps.png")
    _export_cmy_error_panels(datasets, out_dir / "08_cmy_error_uniformity_panels")
    _write_summary(datasets, measured, counts, raw_measured, out_dir / "publication_gamut_summary.csv")

    print(f"[done] wrote figures to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
