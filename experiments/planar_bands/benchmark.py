#!/usr/bin/env python3
"""Synthetic geometry comparison; run with the repository src on PYTHONPATH."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
import numpy as np
from shapely.geometry import Polygon, box
import trimesh

from layerloom.planar_bands import RepairConfig, extrude_profile, section_profile
from layerloom.strata import _slice_between


def cube(height=1.0):
    mesh = trimesh.creation.box(extents=(1, 1, height))
    mesh.apply_translation((0.5, 0.5, height / 2))
    return mesh


def broken_walls(width):
    left, right = 0.5 - width / 2, 0.5 + width / 2
    segments = np.array([
        [[0, 0], [1, 0]], [[1, 0], [1, 1]], [[1, 1], [right, 1]],
        [[left, 1], [0, 1]], [[0, 1], [0, 0]],
    ], dtype=float)
    vertices, faces = [], []
    for a, b in segments:
        offset = len(vertices)
        vertices.extend([[*a, 0], [*b, 0], [*b, 1], [*a, 1]])
        faces.extend([[offset, offset + 1, offset + 2], [offset, offset + 2, offset + 3]])
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)


def fixtures():
    uncapped = cube()
    uncapped.update_faces(np.abs(uncapped.face_normals[:, 2]) < 0.5)
    hollow = trimesh.creation.annulus(r_min=0.2, r_max=0.5, height=1, sections=48)
    hollow.apply_translation((0.5, 0.5, 0.5))
    angle = np.arange(48) * 2 * np.pi / 48
    circle = np.column_stack((np.cos(angle), np.sin(angle)))
    thin = cube(0.02)
    thin.apply_translation((0, 0, 0.02))
    sphere = trimesh.creation.icosphere(subdivisions=2, radius=0.5)
    sphere.apply_translation((0.5, 0.5, 0.5))
    path = sphere.section(plane_origin=(0, 0, 0.1), plane_normal=(0, 0, 1))
    return [
        ("closed_box", "Closed box", cube(), box(0, 0, 1, 1), True,
         "Reference: both methods should reproduce the square footprint."),
        ("missing_caps", "Missing top + bottom", uncapped, box(0, 0, 1, 1), True,
         "No global solid exists, but the sampled contour is complete."),
        ("small_gap", "0.02 mm side opening", broken_walls(0.02), box(0, 0, 1, 1), True,
         "Opening is below the explicit 0.03 mm bridge budget."),
        ("large_gap", "0.06 mm side opening", broken_walls(0.06), None, False,
         "Opening exceeds the bridge budget and must be rejected."),
        ("hollow_tube", "Hollow tube", hollow, Polygon(circle * 0.5 + 0.5, [circle * 0.2 + 0.5]), True,
         "The interior cavity must survive recovery and extrusion."),
        ("sphere", "Curved surface", sphere, Polygon(np.asarray(path.discrete[0])[:, :2]), True,
         "Midpoint contour retained; the output is deliberately stepped."),
        ("thin_between_samples", "Thin feature between samples", thin, Polygon(), True,
         "A feature at z=0.02–0.04 mm misses z=0.10 mm; empty output is a sampling limitation."),
    ]


def lines(mesh, z):
    if mesh is None:
        return np.empty((0, 2, 2))
    segments = trimesh.intersections.mesh_plane(mesh, plane_normal=(0, 0, 1), plane_origin=(0, 0, z))
    return np.asarray(segments)[:, :, :2] if len(segments) else np.empty((0, 2, 2))


def draw_region(ax, region):
    if region.is_empty:
        return
    polygons = [region] if region.geom_type == "Polygon" else list(region.geoms)
    for polygon in polygons:
        xy = np.asarray(polygon.exterior.coords)
        ax.fill(xy[:, 0], xy[:, 1], color="#15803d", alpha=0.25)
        ax.plot(xy[:, 0], xy[:, 1], color="#15803d", lw=1.2)
        for hole in polygon.interiors:
            xy = np.asarray(hole.coords)
            ax.fill(xy[:, 0], xy[:, 1], color="white")
            ax.plot(xy[:, 0], xy[:, 1], color="#15803d", lw=1.2)


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    config = RepairConfig(precision=1e-5, max_gap=0.03)
    low, high, z = 0.0, 0.2, 0.1
    cases = fixtures()
    figure, axes = plt.subplots(len(cases), 3, figsize=(10, 2.05 * len(cases)), squeeze=False)
    rows = []
    for row, (name, title, source, expected, expected_ok, note) in enumerate(cases):
        start = time.perf_counter()
        result = section_profile(source, z, config)
        recovered = extrude_profile(result.region, low, high) if result.ok else None
        elapsed = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        baseline_error = None
        try:
            baseline = _slice_between(source.copy(), low, high, z_gap=0.001)
        except Exception as exc:
            baseline = None
            baseline_error = f"{type(exc).__name__}: {exc}"
        baseline_elapsed = (time.perf_counter() - start) * 1000
        record = {
            "fixture": name, "note": note, "input_watertight": bool(source.is_watertight),
            "input_positive_volume": bool(source.is_volume), "expected_recovery_ok": expected_ok,
            "recovery_ok": bool(result.ok), "expected_behavior_observed": bool(result.ok == expected_ok),
            "source_section_segments": len(lines(source, z)), "recovered_area_mm2": float(result.region.area),
            "reference_area_mm2": None if expected is None else float(expected.area),
            "symmetric_difference_mm2": None if expected is None or not result.ok else float(result.region.symmetric_difference(expected).area),
            "reconstructed_positive_volume": bool(recovered is not None and recovered.is_volume),
            "baseline_returned_mesh": baseline is not None,
            "baseline_positive_volume": bool(baseline is not None and baseline.is_volume),
            "baseline_error": baseline_error, "elapsed_ms_single_run": elapsed,
            "baseline_elapsed_ms_single_run": baseline_elapsed, "diagnostics": result.diagnostics,
        }
        if recovered is not None:
            path = output / f"{name}.stl"
            recovered.export(path)
            restored = trimesh.load(path, force="mesh", process=True)
            rt = section_profile(restored, z, config)
            record.update({"stl": path.name, "roundtrip_positive_volume": bool(restored.is_volume),
                           "roundtrip_profile_ok": bool(rt.ok),
                           "roundtrip_symmetric_difference_mm2": float(rt.region.symmetric_difference(result.region).area)})
        if expected is not None and result.ok:
            record["expected_behavior_observed"] &= record["symmetric_difference_mm2"] <= 1e-4
            if not expected.is_empty:
                record["expected_behavior_observed"] &= bool(
                    record["reconstructed_positive_volume"]
                    and record.get("roundtrip_positive_volume")
                    and record.get("roundtrip_profile_ok")
                    and record.get("roundtrip_symmetric_difference_mm2", float("inf")) <= 1e-4)
        rows.append(record)
        for ax in axes[row]:
            ax.set_xlim(-0.07, 1.07)
            ax.set_ylim(-0.07, 1.07)
            ax.set_aspect("equal")
            ax.set_xticks([0, 0.5, 1])
            ax.set_yticks([0, 0.5, 1])
            ax.tick_params(labelsize=7)
        source_lines = lines(source, z)
        axes[row, 0].add_collection(LineCollection(source_lines, colors="#475569", linewidths=1.4))
        axes[row, 0].set_ylabel(title, fontsize=9)
        axes[row, 0].set_title(f"Input: {len(source_lines)} section segments", fontsize=9)
        if name == "thin_between_samples":
            axes[row, 0].text(0.5, 0.5, "Feature exists below\nthe sampling plane", ha="center", va="center", fontsize=9)
            axes[row, 2].text(0.5, 0.5, "Thin solid retained\nbelow sampling plane", ha="center", va="center", fontsize=9)
        draw_region(axes[row, 1], result.region)
        state = "recovered" if recovered is not None else ("empty sample" if result.ok else "rejected")
        axes[row, 1].set_title(f"2D path: {state}", fontsize=9)
        if not result.ok:
            axes[row, 1].add_collection(LineCollection(source_lines, colors="#dc2626", linewidths=1.2))
            axes[row, 1].text(0.5, 0.5, "Opening exceeds\nrepair budget", ha="center", va="center", fontsize=9)
        axes[row, 2].add_collection(LineCollection(lines(baseline, z), colors="#b45309", linewidths=1.4))
        baseline_state = "valid solid" if record["baseline_positive_volume"] else ("not a valid solid" if baseline is not None else "no band")
        axes[row, 2].set_title(f"3D cutter: {baseline_state}", fontsize=9)
    figure.suptitle("Fixed 0.20 mm bands • contours sampled at z = 0.10 mm\n"
                    "0.03 mm maximum bridge; cavities retained; large gaps rejected", fontsize=13)
    figure.tight_layout(rect=(0, 0, 1, 0.965))
    figure.savefig(output / "comparison.png", dpi=150, facecolor="white")
    plt.close(figure)
    report = {
        "layer_height_mm": high - low, "sample_z_mm": z, "precision_mm": 1e-5, "max_gap_mm": 0.03,
        "scope": "Synthetic geometry and STL round-trip checks; no downstream slicer or printer run.",
        "baseline": "Committed headless-branch strata.py in isolated worktree; unrelated dirty primary-checkout changes are excluded.",
        "sampling_limit": "Unsampled thin features may disappear; sampling does not infer interior colors.",
        "timing_note": "Single executions include initialization and are not a performance benchmark.",
        "fixtures": rows,
    }
    def json_scalar(value):
        return value.item() if isinstance(value, np.generic) else str(value)
    (output / "report.json").write_text(json.dumps(report, indent=2, default=json_scalar) + "\n")
    for record in rows:
        print(f"{record['fixture']}: 2D_ok={record['recovery_ok']} "
              f"2D_solid={record['reconstructed_positive_volume']} "
              f"3D_solid={record['baseline_positive_volume']} area_error={record['symmetric_difference_mm2']}")
    print(output / "report.json")
    print(output / "comparison.png")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "results")
    result = run(parser.parse_args().output.resolve())
    if not all(fixture["expected_behavior_observed"] for fixture in result["fixtures"]):
        raise SystemExit("One or more geometry expectations failed; inspect report.json")
