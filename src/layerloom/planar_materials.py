"""Bounded resolution of microscopic seams between fixed-layer materials."""
from __future__ import annotations

import math

import shapely
from shapely.geometry import Polygon


def partition_materials(token_regions, *, precision=1e-5, seam_tolerance=0.001):
    """Return material regions, diagnostics, and any blocking errors.

    All operands share one XY precision grid. Overlap is assignable only when it
    lies in the boundary tolerance band of BOTH materials. Alphabetical token
    priority deterministically owns those microscopic cells; substantial overlap
    remains an error. Boolean area residuals below 1% of one precision-grid
    cell are reported; larger overlaps or union changes fail. This does not infer
    missing interior colors from open surface patches.
    """
    if not math.isfinite(precision) or precision <= 0:
        raise ValueError("precision must be positive and finite")
    if not math.isfinite(seam_tolerance) or seam_tolerance < 0:
        raise ValueError("seam_tolerance must be finite and nonnegative")
    diagnostics = dict(precision_mm=precision, seam_tolerance_mm=seam_tolerance,
                       ownership="alphabetical token priority for bounded seam overlaps only",
                       overlaps=[], input_grid_adjustments=[], material_changes=[], union_error_mm2=0.0,
                       max_boolean_residual_overlap_mm2=0.0)
    errors = []
    regions = {}
    for token, pieces in sorted(token_regions.items()):
        raw = shapely.union_all([shapely.set_precision(p, 0) for p in pieces], grid_size=0)
        # Union before snapping so equal-material internal edges cannot round
        # apart and create artificial gaps. Clear the precision model after
        # this one snap; subsequent Booleans must not resnap intersections.
        region = shapely.set_precision(shapely.set_precision(raw, precision), 0)
        changed = raw.symmetric_difference(region)
        outside = changed.difference(raw.boundary.buffer(2 * precision)).area
        diagnostics["input_grid_adjustments"].append(dict(token=token,
            changed_area_mm2=float(changed.area), outside_grid_budget_mm2=float(outside),
            components_before=len(shapely.get_parts(raw)),
            components_after=len(shapely.get_parts(region)),
            holes_before=sum(len(p.interiors) for p in shapely.get_parts(raw)),
            holes_after=sum(len(p.interiors) for p in shapely.get_parts(region))))
        if outside > precision ** 2 * 0.01:
            errors.append(f"material {token} precision-grid change exceeds boundary budget")
        if raw.area > 0 and region.is_empty:
            errors.append(f"precision grid would erase material {token}")
        elif any(part.intersection(region).area <= 0 for part in shapely.get_parts(raw)):
            errors.append(f"precision grid would erase a component of material {token}")
        regions[token] = region
    tokens = sorted(regions)
    for i, token in enumerate(tokens):
        for other in tokens[i + 1:]:
            overlap = shapely.intersection(regions[token], regions[other], grid_size=0)
            if overlap.area == 0:
                continue
            allowed = seam_tolerance > 0
            for name in (token, other):
                boundary_band = regions[name].boundary.buffer(seam_tolerance)
                if overlap.difference(boundary_band).area > precision ** 2 * 0.01:
                    allowed = False
            diagnostics["overlaps"].append(dict(tokens=[token, other], area_mm2=float(overlap.area),
                                                resolved=bool(allowed)))
            if not allowed:
                errors.append(f"material overlap {token}/{other} ({overlap.area:g} mm²) exceeds seam tolerance")
    if errors:
        return regions, diagnostics, errors

    # Snap inputs once. Re-snapping each intermediate Boolean can remove a
    # different tiny triangle at a seam and change the shared outer footprint.
    domain = shapely.union_all(list(regions.values()), grid_size=0)
    claimed = Polygon()
    result = {}
    for token, region in regions.items():
        piece = shapely.difference(region, claimed, grid_size=0)
        changed = region.symmetric_difference(piece)
        if not region.is_empty and piece.is_empty:
            errors.append(f"seam assignment would erase material {token}")
        if changed.area and changed.difference(region.boundary.buffer(seam_tolerance)).area > precision ** 2 * 0.01:
            errors.append(f"material {token} changes beyond its seam tolerance")
        diagnostics["material_changes"].append(dict(token=token,
            reassigned_area_mm2=float(changed.area),
            set_hausdorff_mm=float(shapely.hausdorff_distance(region, piece)) if not piece.is_empty else None))
        if not piece.is_empty:
            result[token] = piece
            claimed = shapely.union_all([claimed, piece], grid_size=0)
    diagnostics["union_error_mm2"] = float(domain.symmetric_difference(claimed).area)
    if diagnostics["union_error_mm2"] > precision ** 2 * 0.01:
        errors.append("material partition changed the shared layer footprint")
    for i, token in enumerate(result):
        for other in list(result)[i + 1:]:
            residual = float(result[token].intersection(result[other]).area)
            diagnostics["max_boolean_residual_overlap_mm2"] = max(
                diagnostics["max_boolean_residual_overlap_mm2"], residual)
            if residual > precision ** 2 * 0.01:
                errors.append(f"material overlap {token}/{other} remains after seam assignment")
    return result, diagnostics, errors
