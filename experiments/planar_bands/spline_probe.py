"""Compare bounded linear cleanup and periodic cubic interpolation on Benchy.

This is an experiment, not part of the reconstruction backend. Its spline
distance measurements apply to the sampled polygonal approximation, not an
analytic bound over the continuous cubic curve.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.interpolate import CubicSpline
import scipy
import shapely
from shapely.geometry import LinearRing, MultiPolygon, Polygon

from layerloom.ingest import read_parts


CASES = [(0, 103), (6, 16), (6, 35), (6, 82), (7, 27)]


def vertex_count(region):
    return sum(len(p.exterior.coords) - 1 + sum(len(h.coords) - 1 for h in p.interiors)
               for p in shapely.get_parts(region))


def topology(region):
    parts = list(shapely.get_parts(region))
    return {"components": len(parts), "holes": sum(len(p.interiors) for p in parts)}


def cubic_ring(ring, samples_per_edge=8):
    points = np.asarray(ring.coords, dtype=float)
    keep = np.r_[True, np.linalg.norm(np.diff(points, axis=0), axis=1) > 1e-10]
    points = points[keep]
    if not np.array_equal(points[0], points[-1]):
        points = np.vstack((points, points[0]))
    distances = np.linalg.norm(np.diff(points, axis=0), axis=1)
    knots = np.r_[0.0, np.cumsum(distances)]
    spline = CubicSpline(knots, points, bc_type="periodic")
    samples = np.concatenate([np.linspace(a, b, samples_per_edge, endpoint=False)
                              for a, b in zip(knots[:-1], knots[1:])])
    coordinates = spline(np.r_[samples, knots[-1]])
    coordinates[-1] = coordinates[0]
    return coordinates


def cubic_region(region, samples_per_edge=8):
    polygons = [Polygon(cubic_ring(p.exterior, samples_per_edge),
                        [cubic_ring(h, samples_per_edge) for h in p.interiors])
                for p in shapely.get_parts(region)]
    return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)


def uniform_cubic_region(region, pitch=0.02):
    def fit(ring):
        count = max(16, int(np.ceil(ring.length / pitch)))
        distances = np.linspace(0, ring.length, count, endpoint=False)
        coordinates = shapely.get_coordinates(shapely.line_interpolate_point(ring, distances))
        return cubic_ring(LinearRing(coordinates), samples_per_edge=4)
    polygons = [Polygon(fit(p.exterior), [fit(h) for h in p.interiors])
                for p in shapely.get_parts(region)]
    return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)


def reference_from_mesh(mesh, z, cleanup_bound):
    """Retain closed source loops, accepting only small measured cleanup changes."""
    section = mesh.section(plane_origin=(0, 0, z), plane_normal=(0, 0, 1))
    rings = [Polygon(np.asarray(points)[:, :2]) for points in section.discrete]
    region = Polygon()
    evidence = []
    for ring in rings:
        clean = ring.simplify(cleanup_bound, preserve_topology=False)
        distance = float(ring.boundary.hausdorff_distance(clean.boundary))
        if clean.geom_type != "Polygon" or not clean.is_valid or distance > cleanup_bound * (1 + 1e-8):
            raise ValueError("The reference ring exceeds the explicit cleanup bound")
        evidence.append({"input_valid": bool(ring.is_valid),
                         "input_validity_reason": shapely.is_valid_reason(ring),
                         "input_vertices": len(ring.exterior.coords) - 1,
                         "reference_vertices": len(clean.exterior.coords) - 1,
                         "boundary_hausdorff_mm": distance,
                         "signed_area_change_mm2": float(clean.area - ring.area)})
        region = region.symmetric_difference(clean)
    if not region.is_valid:
        raise ValueError("Reference loop combination is invalid")
    return region, evidence


def metrics(reference, candidate, budget):
    valid = bool(candidate.is_valid)
    distance = float(reference.boundary.hausdorff_distance(candidate.boundary))
    same_topology = topology(reference) == topology(candidate)
    return {"valid": valid, "validity_reason": shapely.is_valid_reason(candidate),
            "vertices": vertex_count(candidate), "topology": topology(candidate),
            "topology_matches": same_topology,
            "boundary_hausdorff_mm": distance,
            "signed_area_change_mm2": float(candidate.area - reference.area),
            "symmetric_difference_mm2": float(reference.symmetric_difference(candidate).area) if valid else None,
            "accepted_under_budget": valid and same_topology and distance <= budget}


def draw_region(ax, region, **kwargs):
    for polygon in shapely.get_parts(region):
        for ring in [polygon.exterior, *polygon.interiors]:
            points = np.asarray(ring.coords)
            ax.plot(points[:, 0], points[:, 1], **kwargs)


def run(input_path, output, cleanup_bound=0.0001, simplify_tolerance=0.005, budget=0.01):
    parts = read_parts([str(input_path)])
    z0 = min(mesh.bounds[0, 2] for _, mesh in parts)
    report = {"input": str(input_path.resolve()), "source_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
              "scipy_version": scipy.__version__, "shapely_version": shapely.__version__,
              "layer_height_mm": 0.2, "reference_cleanup_bound_mm": cleanup_bound,
              "linear_simplification_tolerance_mm": simplify_tolerance,
              "acceptance_boundary_budget_mm": budget, "spline_samples_per_control_edge": 8,
              "uniform_cubic_control_pitch_mm": 0.02, "uniform_cubic_samples_per_control_edge": 4,
              "methods": {"linear": "GEOS simplify with preserve_topology=True",
                          "cubic": "Periodic parametric cubic through reference contour points, chord-length parameterization",
                          "simplified_cubic": "Same cubic through linearly simplified control points",
                          "uniform_cubic": "Periodic cubic after uniform arclength resampling at <=0.02 mm spacing"},
              "measurement_limit": "Distances concern sampled cubic polygons; they are not certified continuous-curve bounds. No source color-boundary consistency or layer-to-layer interpolation is established.",
              "sources": ["https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.CubicSpline.html",
                          "https://shapely.readthedocs.io/en/2.1.2/reference/shapely.simplify.html"],
              "cases": []}
    figure, axes = plt.subplots(len(CASES), 3, figsize=(15, 2.6 * len(CASES)))
    for row, (part_index, layer_index) in enumerate(CASES):
        z = float(z0 + (layer_index + 0.5) * 0.2)
        label, mesh = parts[part_index]
        reference, evidence = reference_from_mesh(mesh, z, cleanup_bound)
        linear = reference.simplify(simplify_tolerance, preserve_topology=True)
        candidates = {"linear": linear, "cubic": cubic_region(reference),
                      "simplified_cubic": cubic_region(linear),
                      "uniform_cubic": uniform_cubic_region(reference)}
        case = {"part": label, "part_index": part_index, "layer_index": layer_index,
                "sample_z_mm": z, "source_ring_cleanup": evidence,
                "reference_vertices": vertex_count(reference),
                "reference_area_mm2": float(reference.area), "reference_topology": topology(reference),
                "candidates": {name: metrics(reference, candidate, budget)
                               for name, candidate in candidates.items()}}
        report["cases"].append(case)
        for col, name in enumerate(("linear", "simplified_cubic", "uniform_cubic")):
            ax = axes[row, col]
            draw_region(ax, reference, color="#64748b", linewidth=2, label="Reference")
            draw_region(ax, candidates[name], color={"linear": "#15803d", "simplified_cubic": "#c2410c", "uniform_cubic": "#2563eb"}[name], linewidth=0.9)
            ax.set_aspect("equal")
            m = case["candidates"][name]
            ax.set_title(f"Part {part_index + 1}, layer {layer_index} · {name.replace('_', ' ')}\n"
                         f"drift {m['boundary_hausdorff_mm']:.4g} mm · {'valid' if m['valid'] else 'INVALID'} · {m['vertices']} vertices", fontsize=9)
            ax.tick_params(labelsize=7)
    figure.suptitle("Benchy contour interpolation: dense anchors constrain cubic overshoot at a cost\n"
                    "Gray: reference; green: linear cleanup; orange: sparse cubic; blue: uniform cubic", fontsize=12)
    figure.tight_layout(rect=(0, 0, 1, 0.96))
    output.mkdir(parents=True, exist_ok=True)
    figure.savefig(output / "spline_comparison.png", dpi=160)
    plt.close(figure)
    report["summary"] = {name: {"accepted": sum(c["candidates"][name]["accepted_under_budget"] for c in report["cases"]),
                                 "invalid": sum(not c["candidates"][name]["valid"] for c in report["cases"]),
                                 "maximum_boundary_hausdorff_mm": max(c["candidates"][name]["boundary_hausdorff_mm"] for c in report["cases"]),
                                 "reference_vertices_total": sum(c["reference_vertices"] for c in report["cases"]),
                                 "candidate_vertices_total": sum(c["candidates"][name]["vertices"] for c in report["cases"])}
                         for name in candidates}
    (output / "spline_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "results")
    args = parser.parse_args()
    run(args.input, args.output)
