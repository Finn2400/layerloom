"""Plot the recorded serial benchmark; never reruns geometry methods."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch


METHODS = ("cut", "repair_cut", "contour")
LABELS = {"cut": "3D cuts", "repair_cut": "Mesh repair + 3D cuts", "contour": "2D contours"}
CASES = {"cubes": "Tutorial cubes", "benchy": "Benchy", "hollow_tube": "Intact hollow tube",
         "hollow_tube_gap020": "Hollow tube · 0.020 mm seam"}
STAGES = (("repair_s", "Mesh repair", "#94a3b8"),
          ("layers_s", "Source-part layer bands", "#2563eb"),
          ("assembly_s", "Native color assembly", "#f59e0b"))


def plot(report, output, prism_audit=None):
    cells = [(case, height) for height in report["protocol"]["heights_mm"]
             for case in report["protocol"]["cases"]]
    rows = math.ceil(len(cells) / 2)
    fig, axes = plt.subplots(rows, 2, figsize=(13, 3.15 * rows + 1.4), squeeze=False)
    fig.patch.set_facecolor("#f8fafc")
    for ax, (case, height) in zip(axes.flat, cells):
        ax.set_facecolor("#f8fafc")
        max_time = 0
        for y, method in enumerate(METHODS):
            runs = [run["result"] for run in report["runs"] if run["case"] == case
                    and run["height_mm"] == height and run["method"] == method
                    and run["status"] == "completed"]
            summary = next(row for row in report["summary"] if row["case"] == case
                           and row["height_mm"] == height and row["method"] == method)
            if not runs:
                ax.text(0, y, "Process failed", va="center", color="#b91c1c")
                continue
            runs.sort(key=lambda run: run["timings"]["core_s"])
            median_run = runs[len(runs) // 2]
            total = median_run["timings"]["core_s"]
            low, high = runs[0]["timings"]["core_s"], runs[-1]["timings"]["core_s"]
            passed = (summary["geometry_status"] == "ok"
                      and summary["audit_applies_to_all_completed_repeats"])
            prism_resolved = bool(prism_audit and prism_audit.get("passed")
                                  and case == prism_audit.get("case")
                                  and method == prism_audit.get("method")
                                  and summary["audit_applies_to_all_completed_repeats"]
                                  and all(run["source"]["sha256"] == prism_audit.get("source_sha256")
                                          and run["fingerprints"]["native_parts_sha256"]
                                          == prism_audit.get("measured_native_parts_sha256") for run in runs))
            outcome_label = "checks pass" if passed else ("prism OK*" if prism_resolved else "checks FAIL")
            passed = passed or prism_resolved
            left = 0
            for name, label, color in STAGES:
                value = median_run["timings"][name]
                ax.barh(y, value, left=left, height=.52, color=color,
                        hatch=None if passed else "//", edgecolor="white", linewidth=.6)
                left += value
            ax.errorbar(total, y, xerr=[[total - low], [high - total]], fmt="none",
                        ecolor="#0f172a", capsize=3, linewidth=1.2)
            ax.annotate(f" {total:.2f} s · {outcome_label}",
                        (high, y), xytext=(5, 0), textcoords="offset points", va="center",
                        fontsize=9, color="#334155" if passed else "#b91c1c")
            max_time = max(max_time, high)
        ax.set_xlim(0, max_time * 1.70)
        ax.set_yticks(range(3), [LABELS[m] for m in METHODS], fontsize=9)
        ax.invert_yaxis()
        ax.set_title(f"{CASES[case]} · {height:g} mm", loc="left", fontsize=12, weight="bold", pad=14)
        ax.set_xlabel("Geometry processing time (seconds)", fontsize=9)
        ax.grid(axis="x", color="#cbd5e1", linewidth=.5)
        ax.set_axisbelow(True)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.spines["bottom"].set_color("#cbd5e1")
        ax.tick_params(axis="y", length=0)
    for ax in list(axes.flat)[len(cells):]:
        ax.set_visible(False)
    fig.suptitle("Repair and layer creation: controlled local comparison", fontsize=17,
                 weight="bold", x=.05, ha="left", y=.98)
    fig.text(.05, .925, f"{report['protocol']['repetitions']} serial fresh-process runs per case/method; panel axes differ. "
             "Hatched bars have unresolved geometry-check failures.", fontsize=10, color="#475569")
    fig.legend(handles=[Patch(facecolor=color, label=label) for _, label, color in STAGES],
               loc="lower center", bbox_to_anchor=(.5, .075), ncol=3, frameon=False, fontsize=10)
    supplement_note = ("\n*Benchy contours: 11 generic-section failures checked using exact prism caps and matching geometry hashes."
                       if prism_audit and prism_audit.get("passed") else "")
    fig.text(.05, .025, "Bars show stage times from the median-total run; whiskers show observed total min–max.\n"
             "Excludes input loading, export, common external audits, slicer processing, and printing." + supplement_note,
             fontsize=9, color="#475569")
    fig.tight_layout(rect=(.025, .14, .99, .90), h_pad=2.1, w_pad=2.2)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160, facecolor=fig.get_facecolor())
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--prism-audit", type=Path)
    args = parser.parse_args()
    plot(json.loads(args.input.read_text()), args.output or args.input.with_name("controlled_comparison.png"),
         json.loads(args.prism_audit.read_text()) if args.prism_audit else None)
