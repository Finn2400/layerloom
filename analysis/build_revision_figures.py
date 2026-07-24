#!/usr/bin/env python3
"""Archived helpers for revision figure development.

The submitted main-text figures are preserved as source artwork and this script
intentionally does not write to the main ``figures/`` directory when run.

The data used for Fig. 5 are copied verbatim from the supplementary benchmark
table.  X1C and U1 values are completed-print measurements; XL values are
slicer estimates, which are marked in the rendered figure.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Polygon, Rectangle


INK = "#15242b"
MUTED = "#586a73"
GRID = "#d7e0e3"
CYAN = "#14a6c8"
MAGENTA = "#cc2b83"
YELLOW = "#f0c419"
SLATE = "#6e7f89"
PALE = "#eef4f5"


def panel_label(axis: plt.Axes, label: str) -> None:
    axis.text(
        0.0,
        1.0,
        label,
        transform=axis.transAxes,
        fontsize=12,
        fontweight="bold",
        color=INK,
        va="top",
        ha="left",
    )


def rounded_box(axis: plt.Axes, xy: tuple[float, float], width: float, height: float, *, color: str) -> None:
    axis.add_patch(
        Rectangle(
            xy,
            width,
            height,
            linewidth=1.15,
            edgecolor=INK,
            facecolor=color,
            joinstyle="round",
        )
    )


def stage_input(axis: plt.Axes) -> None:
    panel_label(axis, "A")
    axis.text(0.50, 0.92, "Colored input", ha="center", va="top", fontsize=10.5, fontweight="bold", color=INK)
    rounded_box(axis, (0.18, 0.18), 0.64, 0.50, color=PALE)
    axis.add_patch(Polygon([(0.30, 0.30), (0.48, 0.56), (0.72, 0.38), (0.55, 0.23)], closed=True, facecolor=CYAN, edgecolor=INK, lw=0.7))
    axis.add_patch(Polygon([(0.48, 0.56), (0.67, 0.54), (0.72, 0.38)], closed=True, facecolor=MAGENTA, edgecolor=INK, lw=0.7))
    axis.add_patch(Polygon([(0.30, 0.30), (0.48, 0.56), (0.42, 0.23)], closed=True, facecolor=YELLOW, edgecolor=INK, lw=0.7))
    axis.text(0.50, 0.08, "3MF or GLB\nregions", ha="center", va="center", fontsize=7.8, color=MUTED)


def stage_token(axis: plt.Axes) -> None:
    panel_label(axis, "B")
    axis.text(0.50, 0.92, "Assign stack tokens", ha="center", va="top", fontsize=10.5, fontweight="bold", color=INK)
    axis.text(0.50, 0.76, "source color  →  printable recipe", ha="center", va="center", fontsize=8.0, color=MUTED)
    labels = [("C", CYAN), ("M", MAGENTA), ("Y", YELLOW), ("C", CYAN), ("M", MAGENTA)]
    for index, (label, color) in enumerate(labels):
        x = 0.12 + 0.16 * index
        rounded_box(axis, (x, 0.38), 0.13, 0.17, color=color)
        axis.text(x + 0.065, 0.465, label, ha="center", va="center", fontsize=8.5, fontweight="bold", color=INK)
    axis.text(0.50, 0.25, "Example token:  C M Y C M", ha="center", va="center", fontsize=8.5, color=INK)
    axis.text(0.50, 0.09, "canonical recipe and\nrepeatable layer fractions", ha="center", va="center", fontsize=7.4, color=MUTED)


def stage_bands(axis: plt.Axes) -> None:
    panel_label(axis, "C")
    axis.text(0.50, 0.92, "Reconstruct Z-bands", ha="center", va="top", fontsize=10.5, fontweight="bold", color=INK)
    x0, y0, width, height = 0.29, 0.15, 0.42, 0.56
    for index, color in enumerate([CYAN, MAGENTA, YELLOW, CYAN, MAGENTA]):
        y = y0 + index * height / 5
        axis.add_patch(Rectangle((x0, y), width, height / 5, facecolor=color, edgecolor="white", lw=0.9))
    axis.add_patch(Rectangle((x0, y0), width, height, fill=False, edgecolor=INK, lw=1.05))
    axis.add_patch(FancyArrowPatch((0.81, 0.18), (0.81, 0.70), arrowstyle="<->", mutation_scale=11, lw=1.0, color=INK))
    axis.text(0.88, 0.44, "Z", fontsize=9, color=INK, va="center")
    axis.text(0.50, 0.08, "one assignment\nper weave-height band", ha="center", va="center", fontsize=7.4, color=MUTED)


def stage_output(axis: plt.Axes) -> None:
    panel_label(axis, "D")
    axis.text(0.50, 0.92, "Slicer-ready 3MF", ha="center", va="top", fontsize=10.5, fontweight="bold", color=INK)
    columns = [(0.20, CYAN, "C"), (0.44, MAGENTA, "M"), (0.68, YELLOW, "Y")]
    for x, color, label in columns:
        for y in (0.22, 0.38, 0.54):
            axis.add_patch(Rectangle((x, y), 0.13, 0.10, facecolor=color, edgecolor=INK, lw=0.55))
        axis.text(x + 0.065, 0.15, label, ha="center", va="center", fontsize=9, fontweight="bold", color=INK)
    axis.text(0.50, 0.08, "grouped by\nphysical filament", ha="center", va="center", fontsize=7.6, color=MUTED)


def build_workflow_figure(path: Path) -> None:
    figure, axes = plt.subplots(1, 4, figsize=(7.25, 2.25), constrained_layout=True)
    for axis, drawer in zip(axes, (stage_input, stage_token, stage_bands, stage_output)):
        axis.set_xlim(0, 1)
        axis.set_ylim(0, 1)
        axis.axis("off")
        drawer(axis)
    for source, target in zip(axes[:-1], axes[1:]):
        figure.add_artist(
            FancyArrowPatch(
                (source.get_position().x1, 0.50),
                (target.get_position().x0, 0.50),
                transform=figure.transFigure,
                arrowstyle="-|>",
                mutation_scale=12,
                color=MUTED,
                lw=1.2,
            )
        )
    figure.savefig(path, dpi=600, bbox_inches="tight", facecolor="white")
    plt.close(figure)


BENCHMARK = {
    "Bambu X1C": {"time": [330, 324, 360, 600, 609], "material": [85.451, 86.121, 85.456, 157.860, 155.249], "style": "-", "marker": "o", "provenance": "Measured"},
    "Snapmaker U1": {"time": [103, 103, 104, 133, 133], "material": [20.102, 20.479, 20.369, 24.813, 24.804], "style": "-", "marker": "s", "provenance": "Measured"},
    "Prusa XL": {"time": [186, 185, 185, 217, 217], "material": [24.130, 24.120, 24.120, 28.300, 28.300], "style": "--", "marker": "^", "provenance": "Slicer estimate"},
}
PRINTER_COLORS = {"Bambu X1C": "#1f77b4", "Snapmaker U1": "#ef7c00", "Prusa XL": "#4e9b63"}


def plot_benchmark_panel(axis: plt.Axes, metric: str, ylabel: str, letter: str) -> None:
    panel_label(axis, letter)
    x_values = list(range(1, 6))
    for printer, values in BENCHMARK.items():
        scale = 60.0 if metric == "time" else 1.0
        data = [entry / scale for entry in values[metric]]
        axis.plot(
            x_values,
            data,
            color=PRINTER_COLORS[printer],
            linestyle=values["style"],
            marker=values["marker"],
            markersize=5.5,
            linewidth=1.8,
            label=f"{printer} ({values['provenance'].lower()})",
        )
    axis.set_ylabel(ylabel, fontsize=9.5)
    axis.set_xticks(x_values, [f"A{x}" for x in x_values])
    axis.tick_params(labelsize=8.5)
    axis.grid(axis="y", color=GRID, linewidth=0.7)
    axis.spines[["top", "right"]].set_visible(False)


def build_benchmark_figure(path: Path) -> None:
    figure, axes = plt.subplots(2, 1, figsize=(7.15, 4.0), sharex=True, constrained_layout=True)
    plot_benchmark_panel(axes[0], "time", "Print time (h)", "A")
    plot_benchmark_panel(axes[1], "material", "Total filament used (g)", "B")
    axes[1].set_xlabel("Arrangement", fontsize=9.5)
    axes[0].legend(loc="upper left", fontsize=7.6, frameon=False, ncol=3, bbox_to_anchor=(0.06, 1.05))
    axes[1].text(
        0.00,
        -0.43,
        "A1: direct-color baseline  |  A2-A3: woven colors using the same active channels  |  A4-A5: added physical channel",
        transform=axes[1].transAxes,
        ha="left",
        va="top",
        fontsize=7.4,
        color=MUTED,
    )
    figure.savefig(path, dpi=600, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def main() -> None:
    print("No main-text figures generated: submitted artwork is intentionally retained.")


if __name__ == "__main__":
    main()
