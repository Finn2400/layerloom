"""Plot the recorded optimization comparison without rerunning geometry."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from optimized_benchmark import METHODS

LABELS = {"cut": "3D cuts", "repair_cut": "Mesh repair + cuts",
          "contour": "Original 2D", "contour_optimized": "Optimized 2D"}
CASES = {"cubes": "Tutorial cubes", "benchy": "Benchy",
         "hollow_tube": "Intact hollow tube", "hollow_tube_gap020": "Tube with 0.020 mm seam"}
STAGES = (("repair_s", "Mesh repair", "#94a3b8"),
          ("layers_s", "Source bands / contours only for optimized", "#2563eb"),
          ("assembly_s", "Final color assembly", "#f59e0b"))


def plot(report, output):
    labels = report["protocol"].get("plot_labels", LABELS)
    cells = [(case, height) for height in report["protocol"]["heights_mm"]
             for case in report["protocol"]["cases"]]
    rows = math.ceil(len(cells) / 2)
    fig, axes = plt.subplots(rows, 2, figsize=(14, 3.5 * rows + 1.6), squeeze=False)
    fig.patch.set_facecolor("#f8fafc")
    for ax, (case, height) in zip(axes.flat, cells):
        ax.set_facecolor("#f8fafc")
        maximum = 0
        totals = {}
        for y, method in enumerate(METHODS):
            runs = [run["result"] for run in report["runs"] if run["case"] == case
                    and run["height_mm"] == height and run["method"] == method
                    and run["status"] == "completed"]
            summary = next(row for row in report["summary"] if row["case"] == case
                           and row["height_mm"] == height and row["method"] == method)
            if not runs:
                ax.text(0, y, "No completed measurements", va="center", color="#b91c1c")
                continue
            runs.sort(key=lambda run: run["timings"]["core_s"])
            # Average the central two runs for an even sample count. This makes
            # the stacked stages add exactly to the median complete runtime.
            middle = runs[(len(runs) - 1) // 2:len(runs) // 2 + 1]
            total = sum(run["timings"]["core_s"] for run in middle) / len(middle)
            totals[method] = total
            low, high = runs[0]["timings"]["core_s"], runs[-1]["timings"]["core_s"]
            passed = (summary["all_outputs_equal_original"]
                      and summary["all_final_geometry_checks_pass"]
                      and summary["all_frozen_controls_verified"])
            left = 0
            for name, _, color in STAGES:
                value = sum(run["timings"][name] for run in middle) / len(middle)
                ax.barh(y, value, left=left, height=.56, color=color,
                        hatch=None if passed else "//", edgecolor="white", linewidth=.6)
                left += value
            ax.errorbar(total, y, xerr=[[max(0, total - low)], [max(0, high - total)]],
                        fmt="none", ecolor="#0f172a", capsize=3, linewidth=1.2)
            ax.annotate(f" {total:.2f} s", (high, y), xytext=(5, 0), textcoords="offset points",
                        va="center", fontsize=10, weight="bold" if method == "contour_optimized" else "normal")
            maximum = max(maximum, high)
        speed = (totals.get("contour", 0) / totals["contour_optimized"]
                 if totals.get("contour_optimized") else None)
        suffix = f"  ·  {speed:.2f}× faster 2D" if speed else ""
        ax.set_title(f"{CASES[case]}{suffix}", loc="left", fontsize=12, weight="bold", pad=14)
        ax.set_xlim(0, maximum * 1.25)
        ax.set_yticks(range(len(METHODS)), [labels[m] for m in METHODS], fontsize=10)
        ax.invert_yaxis()
        ax.set_xlabel(f"Complete geometry processing (seconds) · {height:g} mm layers", fontsize=9)
        ax.grid(axis="x", color="#cbd5e1", linewidth=.5)
        ax.set_axisbelow(True)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.spines["bottom"].set_color("#cbd5e1")
        ax.tick_params(axis="y", length=0)
    for ax in list(axes.flat)[len(cells):]:
        ax.set_visible(False)
    fig.suptitle(report["protocol"].get("plot_title", "One extrusion per final region: faster 2D repair, identical geometry"),
                 fontsize=17, weight="bold", x=.045, ha="left", y=.98)
    fig.text(.045, .932, f"{report['protocol']['repetitions']} fresh serial runs per case/method; balanced order; "
             "prior 2D implementation frozen. Panel scales differ.", fontsize=10, color="#475569")
    fig.legend(handles=[Patch(facecolor=color, label=("Source bands / contours (2D)"
                           if name == "layers_s" and "previous" in report["protocol"] else label))
                        for name, label, color in STAGES],
               loc="lower center", bbox_to_anchor=(.5, .087), ncol=3, frameon=False, fontsize=9)
    fig.text(.045, .025,
             "Bars: median total, central-run stage breakdown. Whiskers: observed min–max. Hatching: known geometry defects remain.\n"
             "All new outputs match the prior audited geometry exactly; Benchy includes the prior supplemental prism proof.\n"
             "Includes intrinsic final checks. Excludes loading, export, external audits, slicer processing, and printing.",
             fontsize=9, color="#475569")
    fig.tight_layout(rect=(.025, .15, .99, .90), h_pad=2.0, w_pad=2.4)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160, facecolor=fig.get_facecolor())
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    plot(json.loads(args.input.read_text()), args.output or args.input.with_name("optimized_comparison.png"))
