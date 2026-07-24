#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Publication-oriented plotting and summary utilities for LayerLoom benchmarks.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import matplotlib

matplotlib.use("Agg")

from matplotlib import pyplot as plt
from matplotlib import patches
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd

from benchmark_common import (
    DISTINCT_COLOR_COUNTS,
    LAYER_HEIGHTS,
    PRINTERS,
    ensure_dir,
    read_csv,
    write_csv,
)

METHOD_ORDER = ["woven_prism", "stacked_cells", "coplanar_grid"]
METHOD_LABELS = {
    "woven_prism": "Woven prism",
    "stacked_cells": "Stacked cells",
    "coplanar_grid": "Coplanar grid",
    "monolith": "Single-color monolith",
}
METHOD_COLORS = {
    "woven_prism": "#d95f02",
    "stacked_cells": "#1b9e77",
    "coplanar_grid": "#7570b3",
    "monolith": "#666666",
}
TOKEN_COLORS = {
    "c": "#3ac8dc",
    "m": "#c31996",
    "y": "#ffdf00",
    "k": "#141414",
}
PREFERRED_REP_LAYER_HEIGHT = 0.10


def _coerce_numeric(df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_metrics_df(path: Path) -> pd.DataFrame:
    df = pd.DataFrame(read_csv(path))
    numeric_cols = [
        "layer_height",
        "distinct_colors",
        "expected_layers",
        "total_layers",
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
    ]
    _coerce_numeric(df, numeric_cols)
    if "source_kind" not in df.columns:
        df["source_kind"] = ""
    if "notes" not in df.columns:
        df["notes"] = ""
    if "total_layers" not in df.columns:
        df["total_layers"] = np.nan

    # Derive missing quantities conservatively.
    waste_missing = df["waste_filament_g"].isna()
    df.loc[waste_missing, "waste_filament_g"] = (
        df.loc[waste_missing, "purged_filament_g"].fillna(0.0)
        + df.loc[waste_missing, "tower_filament_g"].fillna(0.0)
    )

    frac_missing = df["waste_fraction"].isna() & df["total_filament_g"].gt(0)
    df.loc[frac_missing, "waste_fraction"] = (
        df.loc[frac_missing, "waste_filament_g"] / df.loc[frac_missing, "total_filament_g"]
    )

    throughput_missing = df["throughput_g_per_hr"].isna() & df["model_filament_g"].gt(0) & df["total_time_min"].gt(0)
    df.loc[throughput_missing, "throughput_g_per_hr"] = (
        df.loc[throughput_missing, "model_filament_g"] / (df.loc[throughput_missing, "total_time_min"] / 60.0)
    )

    layer_count = df["total_layers"].where(df["total_layers"].gt(0), df["expected_layers"])
    tpl_missing = df["time_per_layer_sec"].isna() & layer_count.gt(0) & df["total_time_min"].gt(0)
    df.loc[tpl_missing, "time_per_layer_sec"] = (
        df.loc[tpl_missing, "total_time_min"] * 60.0 / layer_count[tpl_missing]
    )
    df["resolved_layers"] = layer_count

    breakdown = df["total_time_min"] - df["prepare_time_min"].fillna(0.0) - df["model_time_min"].fillna(0.0)
    df["overhead_time_min"] = breakdown.clip(lower=0.0)
    df["waste_filament_g"] = df["waste_filament_g"].fillna(0.0)
    df["waste_fraction"] = df["waste_fraction"].fillna(0.0)
    df["throughput_g_per_hr"] = df["throughput_g_per_hr"].replace([np.inf, -np.inf], np.nan)
    df["method_label"] = df["method"].map(METHOD_LABELS).fillna(df["method"])
    df["printer"] = pd.Categorical(df["printer"], categories=list(PRINTERS), ordered=True)
    df["layer_height"] = pd.Categorical(df["layer_height"], categories=list(LAYER_HEIGHTS), ordered=True)
    return df


def slope_for_group(group: pd.DataFrame, xcol: str, ycol: str) -> Tuple[float | None, float | None]:
    work = group[[xcol, ycol]].dropna()
    if len(work) < 2 or work[xcol].nunique() < 2:
        return None, None
    slope, intercept = np.polyfit(work[xcol].to_numpy(dtype=float), work[ycol].to_numpy(dtype=float), 1)
    return float(slope), float(intercept)


def save_figure(fig: plt.Figure, out_base: Path) -> None:
    fig.savefig(out_base.with_suffix(".png"), dpi=220, bbox_inches="tight")
    fig.savefig(out_base.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


def add_shared_legend(fig: plt.Figure, include_baseline: bool = False) -> None:
    handles = [
        Line2D([0], [0], color=METHOD_COLORS[m], marker="o", lw=2.2, label=METHOD_LABELS[m])
        for m in METHOD_ORDER
    ]
    if include_baseline:
        handles.append(Line2D([0], [0], color=METHOD_COLORS["monolith"], lw=1.8, ls="--", label="Single-color baseline"))
    fig.legend(handles=handles, loc="upper center", ncol=len(handles), frameon=False, bbox_to_anchor=(0.5, 1.01))


def facet_line_plot(
    df: pd.DataFrame,
    ycol: str,
    ylabel: str,
    title: str,
    out_base: Path,
    *,
    include_baseline: bool = True,
    with_fit: bool = False,
) -> None:
    fig, axes = plt.subplots(len(LAYER_HEIGHTS), len(PRINTERS), figsize=(11.4, 11.0), sharex=True)
    for row_idx, lh in enumerate(LAYER_HEIGHTS):
        for col_idx, printer in enumerate(PRINTERS):
            ax = axes[row_idx, col_idx]
            facet = df[(df["printer"] == printer) & (df["layer_height"].astype(float) == lh)]
            for method in METHOD_ORDER:
                method_df = facet[(facet["method"] == method) & facet[ycol].notna()].sort_values("distinct_colors")
                if method_df.empty:
                    continue
                x = method_df["distinct_colors"].to_numpy(dtype=float)
                y = method_df[ycol].to_numpy(dtype=float)
                ax.plot(x, y, marker="o", lw=2.2, ms=5.5, color=METHOD_COLORS[method], label=METHOD_LABELS[method])
                if with_fit and len(np.unique(x)) >= 2:
                    slope, intercept = np.polyfit(x, y, 1)
                    fit_x = np.linspace(x.min(), x.max(), 30)
                    fit_y = fit_x * slope + intercept
                    ax.plot(fit_x, fit_y, color=METHOD_COLORS[method], lw=1.0, ls="--", alpha=0.7)

            if include_baseline:
                baseline = facet[(facet["method"] == "monolith") & (facet["distinct_colors"] == 1) & facet[ycol].notna()]
                if not baseline.empty:
                    baseline_y = float(baseline.iloc[0][ycol])
                    ax.axhline(baseline_y, color=METHOD_COLORS["monolith"], ls="--", lw=1.5, alpha=0.85)

            if row_idx == 0:
                ax.set_title(printer, fontsize=11, pad=10)
            if col_idx == 0:
                ax.set_ylabel(f"{ylabel}\nLayer height {lh:.2f} mm", fontsize=10)
            ax.set_xticks(list(DISTINCT_COLOR_COUNTS))
            ax.set_xlim(0.8, 4.2)
            ax.grid(True, axis="y", alpha=0.20, lw=0.6)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
    for ax in axes[-1, :]:
        ax.set_xlabel("Distinct colors", fontsize=10)
    fig.suptitle(title, fontsize=14, y=0.985)
    add_shared_legend(fig, include_baseline=include_baseline)
    save_figure(fig, out_base)


def choose_representative_rows(df: pd.DataFrame) -> pd.DataFrame:
    rows: List[pd.Series] = []
    for printer in PRINTERS:
        for method in METHOD_ORDER:
            candidates = df[
                (df["printer"] == printer)
                & (df["method"] == method)
                & (df["distinct_colors"] == 4)
            ].copy()
            if candidates.empty:
                continue
            preferred = candidates[candidates["layer_height"].astype(float) == PREFERRED_REP_LAYER_HEIGHT]
            if not preferred.empty:
                rows.append(preferred.iloc[0])
            else:
                rows.append(candidates.sort_values("layer_height").iloc[0])
    if not rows:
        return df.iloc[0:0].copy()
    return pd.DataFrame(rows)


def representative_stacked_bars(
    df: pd.DataFrame,
    parts: List[Tuple[str, str, str]],
    title: str,
    ylabel: str,
    out_base: Path,
) -> None:
    rep = choose_representative_rows(df)
    fig, axes = plt.subplots(1, len(PRINTERS), figsize=(10.8, 4.8), sharey=True)
    if len(PRINTERS) == 1:
        axes = [axes]
    for idx, printer in enumerate(PRINTERS):
        ax = axes[idx]
        facet = rep[rep["printer"] == printer].copy()
        facet["method"] = pd.Categorical(facet["method"], categories=METHOD_ORDER, ordered=True)
        facet = facet.sort_values("method")
        x = np.arange(len(facet))
        bottom = np.zeros(len(facet))
        for key, label, color in parts:
            values = facet[key].fillna(0.0).to_numpy(dtype=float)
            ax.bar(x, values, bottom=bottom, color=color, width=0.68, label=label)
            bottom += values
        ax.set_title(printer, fontsize=11)
        ax.set_xticks(x)
        ax.set_xticklabels([METHOD_LABELS[m] for m in facet["method"]], rotation=18, ha="right")
        ax.grid(True, axis="y", alpha=0.20, lw=0.6)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        selected_lh = sorted(set(facet["layer_height"].astype(float).round(2)))
        if selected_lh:
            ax.text(0.02, 0.98, f"4 colors, layer {selected_lh[0]:.2f} mm", transform=ax.transAxes, va="top", ha="left", fontsize=8, color="#555555")
    axes[0].set_ylabel(ylabel, fontsize=10)
    fig.suptitle(title, fontsize=14, y=0.98)
    fig.legend(loc="upper center", ncol=len(parts), frameon=False, bbox_to_anchor=(0.5, 1.01))
    save_figure(fig, out_base)


def marginal_slope_heatmaps(df: pd.DataFrame, out_base: Path) -> pd.DataFrame:
    records: List[Dict[str, object]] = []
    for printer in PRINTERS:
        for lh in LAYER_HEIGHTS:
            facet = df[(df["printer"] == printer) & (df["layer_height"].astype(float) == lh)]
            for method in METHOD_ORDER:
                group = facet[facet["method"] == method]
                time_slope, _ = slope_for_group(group, "filament_change_count", "total_time_min")
                waste_slope, _ = slope_for_group(group, "filament_change_count", "waste_filament_g")
                records.append({
                    "printer": printer,
                    "layer_height": lh,
                    "method": method,
                    "method_label": METHOD_LABELS[method],
                    "time_min_per_switch": time_slope,
                    "waste_g_per_switch": waste_slope,
                })
    slope_df = pd.DataFrame(records)

    fig, axes = plt.subplots(2, len(PRINTERS), figsize=(12.0, 5.8))
    metrics = [
        ("time_min_per_switch", "Minutes per additional switch"),
        ("waste_g_per_switch", "Waste grams per additional switch"),
    ]
    for row_idx, (metric, row_title) in enumerate(metrics):
        for col_idx, printer in enumerate(PRINTERS):
            ax = axes[row_idx, col_idx]
            facet = slope_df[slope_df["printer"] == printer].copy()
            table = (
                facet.pivot(index="method_label", columns="layer_height", values=metric)
                .reindex(index=[METHOD_LABELS[m] for m in METHOD_ORDER], columns=list(LAYER_HEIGHTS))
            )
            arr = table.to_numpy(dtype=float)
            masked = np.ma.masked_invalid(arr)
            im = ax.imshow(masked, cmap="viridis", aspect="auto")
            ax.set_title(printer if row_idx == 0 else "", fontsize=11)
            ax.set_xticks(range(len(LAYER_HEIGHTS)))
            ax.set_xticklabels([f"{lh:.2f}" for lh in LAYER_HEIGHTS])
            ax.set_yticks(range(len(METHOD_ORDER)))
            ax.set_yticklabels([METHOD_LABELS[m] for m in METHOD_ORDER])
            for i in range(arr.shape[0]):
                for j in range(arr.shape[1]):
                    value = arr[i, j]
                    if np.isnan(value):
                        txt = "NA"
                        color = "white"
                    else:
                        txt = f"{value:.2f}"
                        color = "white" if value > np.nanmax(arr) * 0.45 else "black"
                    ax.text(j, i, txt, ha="center", va="center", color=color, fontsize=8)
            if col_idx == 0:
                ax.set_ylabel(row_title, fontsize=10)
            if row_idx == len(metrics) - 1:
                ax.set_xlabel("Layer height (mm)", fontsize=10)
            fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.suptitle("Marginal switching cost by method, printer, and layer height", fontsize=14, y=0.995)
    save_figure(fig, out_base)
    return slope_df


def schematic_figure(out_base: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(8.8, 7.6))
    panel_order = [
        ("woven_prism", METHOD_LABELS["woven_prism"]),
        ("stacked_cells", METHOD_LABELS["stacked_cells"]),
        ("coplanar_grid", METHOD_LABELS["coplanar_grid"]),
        ("monolith", METHOD_LABELS["monolith"]),
    ]
    for ax, (method, label) in zip(axes.flatten(), panel_order):
        ax.set_aspect("equal")
        ax.axis("off")
        if method == "woven_prism":
            tri = np.array([[0.12, 0.10], [0.88, 0.10], [0.50, 0.86]])
            ax.add_patch(patches.Polygon(tri, closed=True, fc="#f2f2f2", ec="#333333", lw=1.2))
            stripe_colors = [TOKEN_COLORS["c"], TOKEN_COLORS["m"], TOKEN_COLORS["y"], TOKEN_COLORS["k"]] * 4
            y0, y1 = 0.10, 0.86
            for idx, color in enumerate(stripe_colors[:12]):
                yy = y0 + (idx / 12.0) * (y1 - y0)
                hh = (y1 - y0) / 12.0
                shrink = 0.38 * ((yy - y0) / (y1 - y0))
                left = 0.12 + shrink
                right = 0.88 - shrink
                ax.add_patch(patches.Rectangle((left, yy), right - left, hh, fc=color, ec="none", alpha=0.90))
        elif method == "stacked_cells":
            colors = [TOKEN_COLORS["c"], TOKEN_COLORS["m"], TOKEN_COLORS["y"], TOKEN_COLORS["k"]] * 3
            for idx, color in enumerate(colors[:12]):
                ax.add_patch(
                    patches.Rectangle((0.32, 0.08 + idx * 0.06), 0.36, 0.055, fc=color, ec="#333333", lw=0.4)
                )
        elif method == "coplanar_grid":
            colors = [TOKEN_COLORS["c"], TOKEN_COLORS["m"], TOKEN_COLORS["y"], TOKEN_COLORS["k"]] * 3
            idx = 0
            for r in range(3):
                for c in range(4):
                    ax.add_patch(
                        patches.Rectangle(
                            (0.12 + c * 0.19, 0.18 + (2 - r) * 0.19),
                            0.16,
                            0.16,
                            fc=colors[idx],
                            ec="#333333",
                            lw=0.4,
                        )
                    )
                    idx += 1
        elif method == "monolith":
            ax.add_patch(patches.Rectangle((0.22, 0.18), 0.56, 0.56, fc=TOKEN_COLORS["c"], ec="#333333", lw=1.0))
        ax.text(0.5, 0.98, label, ha="center", va="top", fontsize=12, transform=ax.transAxes)

    fig.text(
        0.5,
        0.03,
        "All benchmark cases use matched total volume (12 × 15 mm cells) and balanced color fractions.",
        ha="center",
        fontsize=10,
    )
    fig.suptitle("Benchmark family schematic", fontsize=14, y=0.99)
    save_figure(fig, out_base)


def derived_summary_table(df: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "job_id",
        "case_id",
        "printer",
        "layer_height",
        "method",
        "method_label",
        "distinct_colors",
        "token_family",
        "expected_layers",
        "resolved_layers",
        "prepare_time_min",
        "model_time_min",
        "overhead_time_min",
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
        "metrics_source",
        "metrics_file",
        "notes",
    ]
    return df[[c for c in cols if c in df.columns]].copy()


def summary_markdown(df: pd.DataFrame, slopes: pd.DataFrame, out_path: Path) -> None:
    analyzable = df[df["total_time_min"].notna()].copy()
    lines = [
        "# Benchmark Summary",
        "",
        f"- Total benchmark rows: **{len(df)}**",
        f"- Rows with total-time estimates: **{len(analyzable)}**",
        "",
    ]
    if not analyzable.empty:
        grouped = (
            analyzable[analyzable["method"].isin(METHOD_ORDER)]
            .groupby("method")["total_time_min"]
            .mean()
            .sort_values(ascending=False)
        )
        if not grouped.empty:
            slowest = grouped.index[0]
            fastest = grouped.index[-1]
            lines.extend(
                [
                    f"- Highest mean total print time among multi-material methods: **{METHOD_LABELS[slowest]}** ({grouped.iloc[0]:.2f} min).",
                    f"- Lowest mean total print time among multi-material methods: **{METHOD_LABELS[fastest]}** ({grouped.iloc[-1]:.2f} min).",
                ]
            )
        waste_group = (
            analyzable[analyzable["method"].isin(METHOD_ORDER)]
            .groupby("method")["waste_fraction"]
            .mean()
            .sort_values(ascending=False)
        )
        if not waste_group.empty:
            lines.append(
                f"- Highest mean waste fraction: **{METHOD_LABELS[waste_group.index[0]]}** ({100.0 * waste_group.iloc[0]:.1f}%)."
            )
        throughput_group = (
            analyzable[analyzable["method"].isin(METHOD_ORDER)]
            .groupby("method")["throughput_g_per_hr"]
            .mean()
            .sort_values(ascending=False)
        )
        if not throughput_group.empty:
            lines.append(
                f"- Highest mean throughput: **{METHOD_LABELS[throughput_group.index[0]]}** ({throughput_group.iloc[0]:.2f} g/hr)."
            )
        lines.append("")

    valid_slopes = slopes.dropna(subset=["time_min_per_switch", "waste_g_per_switch"])
    if not valid_slopes.empty:
        top_time = valid_slopes.sort_values("time_min_per_switch", ascending=False).iloc[0]
        top_waste = valid_slopes.sort_values("waste_g_per_switch", ascending=False).iloc[0]
        lines.extend(
            [
                "## Marginal switching costs",
                "",
                f"- Largest time cost per additional switch: **{top_time['method_label']}** on **{top_time['printer']}** at **{float(top_time['layer_height']):.2f} mm** ({top_time['time_min_per_switch']:.3f} min/switch).",
                f"- Largest waste cost per additional switch: **{top_waste['method_label']}** on **{top_waste['printer']}** at **{float(top_waste['layer_height']):.2f} mm** ({top_waste['waste_g_per_switch']:.3f} g/switch).",
            ]
        )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_csv(path: Path, df: pd.DataFrame) -> None:
    df.to_csv(path, index=False)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate benchmark plots and derived summaries from LayerLoom slicer metrics.")
    p.add_argument("--metrics-csv", required=True, help="Canonical benchmark metrics CSV.")
    p.add_argument("--out-dir", required=True, help="Directory for figures and summary tables.")
    return p


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = ensure_dir(args.out_dir)
    figures_dir = ensure_dir(out_dir / "figures")
    tables_dir = ensure_dir(out_dir / "tables")
    summaries_dir = ensure_dir(out_dir / "summaries")

    df = load_metrics_df(Path(args.metrics_csv))
    derived = derived_summary_table(df)
    export_csv(tables_dir / "benchmark_metrics_derived.csv", derived)

    schematic_figure(figures_dir / "benchmark_schematic")
    facet_line_plot(df, "total_time_min", "Total print time (min)", "Total print time vs distinct colors", figures_dir / "total_time_vs_colors")
    facet_line_plot(df, "waste_filament_g", "Waste filament (g)", "Waste vs distinct colors", figures_dir / "waste_vs_colors")
    facet_line_plot(df, "waste_fraction", "Waste fraction", "Waste fraction vs distinct colors", figures_dir / "waste_fraction_vs_colors")
    facet_line_plot(df, "filament_change_count", "Filament changes", "Filament changes vs distinct colors", figures_dir / "filament_changes_vs_colors", include_baseline=False, with_fit=True)
    facet_line_plot(df, "throughput_g_per_hr", "Throughput (g/hr)", "Effective throughput vs distinct colors", figures_dir / "throughput_vs_colors")
    facet_line_plot(df, "time_per_layer_sec", "Time per layer (s)", "Time per layer vs distinct colors", figures_dir / "time_per_layer_vs_colors")

    representative_stacked_bars(
        df,
        [
            ("prepare_time_min", "Prepare", "#d9d9d9"),
            ("model_time_min", "Model", "#4daf4a"),
            ("overhead_time_min", "Overhead", "#984ea3"),
        ],
        "Representative time breakdown (4-color cases)",
        "Minutes",
        figures_dir / "time_breakdown_representative",
    )
    representative_stacked_bars(
        df,
        [
            ("model_filament_g", "Model", "#4daf4a"),
            ("purged_filament_g", "Purged", "#e41a1c"),
            ("tower_filament_g", "Tower", "#377eb8"),
        ],
        "Representative filament breakdown (4-color cases)",
        "Filament (g)",
        figures_dir / "filament_breakdown_representative",
    )

    slopes = marginal_slope_heatmaps(df, figures_dir / "marginal_cost_per_switch")
    export_csv(tables_dir / "benchmark_switch_slopes.csv", slopes)
    summary_markdown(df, slopes, summaries_dir / "benchmark_summary.md")

    print(f"[bench-plots] figures:   {figures_dir}")
    print(f"[bench-plots] tables:    {tables_dir}")
    print(f"[bench-plots] summaries: {summaries_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
