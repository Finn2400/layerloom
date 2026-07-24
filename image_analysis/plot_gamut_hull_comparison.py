#!/usr/bin/env python3
"""Create a side-by-side CIE a*b* hull comparison from measured region tables."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from analyze_printed_gamut import _convex_hull_points


DATASET_COLORS = {
    "simple": "#2a9d8f",
    "full": "#1f77b4",
    "normal": "#1f77b4",
    "wide": "#d95f02",
    "ovg": "#d95f02",
    "mosaic": "#d95f02",
    "figure": "#d95f02",
}


def _parse_dataset(text: str) -> tuple[str, Path]:
    if "=" not in text:
        raise ValueError("Datasets must be LABEL=/path/to/all_measured_regions.csv")
    label, path = text.split("=", 1)
    return label.strip(), Path(path).expanduser().resolve()


def _plot_one(ax, label: str, csv_path: Path) -> None:
    df = pd.read_csv(csv_path)
    if "measured_a" not in df.columns or "measured_b" not in df.columns:
        raise ValueError(f"{csv_path} does not look like a measured regions table")
    points = df[["measured_a", "measured_b"]].to_numpy(dtype=float)
    colors = df[["measured_R", "measured_G", "measured_B"]].to_numpy(dtype=float)
    ax.scatter(points[:, 0], points[:, 1], c=np.clip(colors, 0, 1), s=30, edgecolor="0.15", linewidth=0.35)
    hull = _convex_hull_points(points)
    if len(hull) >= 3:
        closed = np.vstack([hull, hull[0]])
        ax.plot(closed[:, 0], closed[:, 1], color="black", lw=2.2)
        ax.fill(closed[:, 0], closed[:, 1], color="0.2", alpha=0.05)
    ax.axhline(0, color="0.86", lw=0.8)
    ax.axvline(0, color="0.86", lw=0.8)
    ax.set_title(f"{label}\n{len(df)} regions")
    ax.set_xlabel("a*")
    ax.set_ylabel("b*")
    ax.set_aspect("equal", adjustable="box")


def _dataset_color(label: str) -> str:
    low = label.lower()
    for key, color in DATASET_COLORS.items():
        if key in low:
            return color
    return "#333333"


def _plot_combined(datasets: list[tuple[str, Path]], out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.2, 7.0))
    all_points = []
    union_points = []
    for label, path in datasets:
        df = pd.read_csv(path)
        points = df[["measured_a", "measured_b"]].to_numpy(dtype=float)
        colors = df[["measured_R", "measured_G", "measured_B"]].to_numpy(dtype=float)
        all_points.append(points)
        union_points.append(points)
        ax.scatter(
            points[:, 0],
            points[:, 1],
            c=np.clip(colors, 0, 1),
            s=30,
            edgecolor=_dataset_color(label),
            linewidth=0.75,
            alpha=0.92,
            label=f"{label} points ({len(df)})",
        )
        hull = _convex_hull_points(points)
        if len(hull) >= 3:
            closed = np.vstack([hull, hull[0]])
            ax.plot(closed[:, 0], closed[:, 1], color=_dataset_color(label), lw=2.0, label=f"{label} hull")

    combined = np.vstack(union_points)
    hull = _convex_hull_points(combined)
    if len(hull) >= 3:
        closed = np.vstack([hull, hull[0]])
        ax.plot(closed[:, 0], closed[:, 1], color="black", lw=3.0, label="combined hull")
        ax.fill(closed[:, 0], closed[:, 1], color="0.1", alpha=0.05)

    stacked = np.vstack(all_points)
    x0, x1 = np.percentile(stacked[:, 0], [0.5, 99.5])
    y0, y1 = np.percentile(stacked[:, 1], [0.5, 99.5])
    pad = max(x1 - x0, y1 - y0) * 0.10
    ax.set_xlim(x0 - pad, x1 + pad)
    ax.set_ylim(y0 - pad, y1 + pad)
    ax.axhline(0, color="0.86", lw=0.8)
    ax.axvline(0, color="0.86", lw=0.8)
    ax.set_xlabel("a*")
    ax.set_ylabel("b*")
    ax.set_aspect("equal", adjustable="box")
    ax.set_title("Combined measured gamut space")
    ax.legend(loc="best", fontsize=8, frameon=True)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=260)
    plt.close(fig)
    print(f"[done] wrote {out_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        action="append",
        required=True,
        help="Dataset as LABEL=/path/to/all_measured_regions.csv or measured_regions.csv. Repeat for each panel.",
    )
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--combined-out",
        help="Optional single-panel overlay/union hull figure using all provided datasets.",
    )
    args = parser.parse_args()

    datasets = [_parse_dataset(item) for item in args.dataset]
    fig, axes = plt.subplots(1, len(datasets), figsize=(5.2 * len(datasets), 5.5), squeeze=False)
    for ax, (label, path) in zip(axes[0], datasets):
        _plot_one(ax, label, path)

    all_points = []
    for _, path in datasets:
        df = pd.read_csv(path)
        all_points.append(df[["measured_a", "measured_b"]].to_numpy(dtype=float))
    stacked = np.vstack(all_points)
    x0, x1 = np.percentile(stacked[:, 0], [0.5, 99.5])
    y0, y1 = np.percentile(stacked[:, 1], [0.5, 99.5])
    pad = max(x1 - x0, y1 - y0) * 0.08
    for ax in axes[0]:
        ax.set_xlim(x0 - pad, x1 + pad)
        ax.set_ylim(y0 - pad, y1 + pad)

    fig.suptitle("Measured printed gamuts in CIE a*b*", fontsize=16)
    fig.tight_layout()
    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=260)
    plt.close(fig)
    print(f"[done] wrote {out_path}")
    if args.combined_out:
        _plot_combined(datasets, Path(args.combined_out).expanduser().resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
