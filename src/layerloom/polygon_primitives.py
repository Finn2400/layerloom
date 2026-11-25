"""
Robust 2-D profile helpers for flat-extruded LayerLoom objects.

The functions in this module deliberately keep fabrication geometry in
Shapely until the final extrusion step.  This makes rounded slots, concave
outlines, and clipped color cells much safer than triangle-fan extrusion.
"""

from __future__ import annotations

import math
from typing import Iterable, Iterator, Sequence

import numpy as np
import trimesh
from shapely import affinity
from shapely.geometry import GeometryCollection, MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union


PolygonLike = Polygon | MultiPolygon | GeometryCollection


def iter_polygons(geometry: PolygonLike) -> Iterator[Polygon]:
    """Yield non-empty polygon components from a Shapely geometry."""
    if geometry.is_empty:
        return
    if isinstance(geometry, Polygon):
        yield geometry
        return
    for child in getattr(geometry, "geoms", ()):
        if isinstance(child, Polygon):
            if not child.is_empty:
                yield child
        elif isinstance(child, (MultiPolygon, GeometryCollection)):
            yield from iter_polygons(child)


def clean_polygonal(geometry: PolygonLike, *, min_area: float = 1e-5) -> PolygonLike:
    """
    Repair minor polygon defects and discard components too small to print.
    """
    if geometry.is_empty:
        return GeometryCollection()
    repaired = geometry if geometry.is_valid else geometry.buffer(0)
    kept = [poly for poly in iter_polygons(repaired) if float(poly.area) >= min_area]
    if not kept:
        return GeometryCollection()
    return kept[0] if len(kept) == 1 else MultiPolygon(kept)


def _radial_open_slot(
    *,
    root_radius: float,
    mouth_radius: float,
    width: float,
    mouth_flare: float,
    lead_in: float,
    round_segments: int,
) -> PolygonLike:
    """
    Make a slot along +X that opens beyond ``mouth_radius``.

    ``root_radius`` is the center of the rounded root, so the deepest point is
    one half slot-width farther inward.
    """
    half = width * 0.5
    overshoot = max(width, 0.5)
    root = Point(root_radius, 0.0).buffer(half, quad_segs=round_segments)
    body = box(root_radius, -half, mouth_radius + overshoot, half)
    if mouth_flare <= 0.0 or lead_in <= 0.0:
        return unary_union([root, body])
    flare_start = max(root_radius, mouth_radius - lead_in)
    flare = Polygon(
        [
            (flare_start, -half),
            (mouth_radius + overshoot, -(half + mouth_flare)),
            (mouth_radius + overshoot, half + mouth_flare),
            (flare_start, half),
        ]
    )
    return unary_union([root, body, flare])


def make_disc_profile(
    *,
    radius: float,
    slot_angles_deg: Sequence[float],
    slot_width: float,
    slot_split_fraction: float = 0.5,
    mouth_flare: float = 0.30,
    lead_in: float = 2.0,
    circle_segments: int = 192,
    round_segments: int = 10,
) -> PolygonLike:
    """Create a circular plate with rounded, inward-opening radial slots."""
    if radius <= 0.0:
        raise ValueError("radius must be > 0")
    if slot_width <= 0.0:
        raise ValueError("slot_width must be > 0")
    if not 0.0 < slot_split_fraction < 1.0:
        raise ValueError("slot_split_fraction must be between 0 and 1")

    disc = Point(0.0, 0.0).buffer(radius, quad_segs=max(16, circle_segments // 4))
    root_radius = radius * slot_split_fraction
    slots = [
        affinity.rotate(
            _radial_open_slot(
                root_radius=root_radius,
                mouth_radius=radius,
                width=slot_width,
                mouth_flare=mouth_flare,
                lead_in=lead_in,
                round_segments=round_segments,
            ),
            angle,
            origin=(0.0, 0.0),
            use_radians=False,
        )
        for angle in slot_angles_deg
    ]
    return clean_polygonal(disc.difference(unary_union(slots)))


def _horizontal_open_slot(
    *,
    axis_relief: float,
    root_radius: float,
    center_z: float,
    width: float,
    mouth_flare: float,
    lead_in: float,
    round_segments: int,
) -> PolygonLike:
    """Make a slot that opens through a meridian wedge's straight inner edge."""
    half = width * 0.5
    overshoot = max(width, 0.5)
    root = Point(root_radius, center_z).buffer(half, quad_segs=round_segments)
    body = box(axis_relief - overshoot, center_z - half, root_radius, center_z + half)
    if mouth_flare <= 0.0 or lead_in <= 0.0:
        return unary_union([root, body])
    flare_end = min(root_radius, axis_relief + lead_in)
    flare = Polygon(
        [
            (axis_relief - overshoot, center_z - half - mouth_flare),
            (flare_end, center_z - half),
            (flare_end, center_z + half),
            (axis_relief - overshoot, center_z + half + mouth_flare),
        ]
    )
    return unary_union([root, body, flare])


def make_wedge_profile(
    *,
    radius: float,
    axis_relief: float,
    level_z: Sequence[float],
    local_radii: Sequence[float],
    slot_width: float,
    slot_split_fraction: float = 0.5,
    mouth_flare: float = 0.30,
    lead_in: float = 2.0,
    circle_segments: int = 192,
    round_segments: int = 10,
) -> PolygonLike:
    """
    Create a semi-circular meridian plate with five rounded horizontal slots.

    Local X is radial distance from the globe axis and local Y is globe Z.
    The straight edge is truncated at ``axis_relief`` so finite-thickness
    wedges do not collide around the axis.
    """
    if len(level_z) != len(local_radii):
        raise ValueError("level_z and local_radii must have the same length")
    if axis_relief <= 0.0 or axis_relief >= radius:
        raise ValueError("axis_relief must be > 0 and < radius")

    circle = Point(0.0, 0.0).buffer(radius, quad_segs=max(16, circle_segments // 4))
    half_disc = circle.intersection(
        box(axis_relief, -radius - 1.0, radius + 1.0, radius + 1.0)
    )
    slots = []
    for z, local_radius in zip(level_z, local_radii):
        root_radius = float(local_radius) * slot_split_fraction
        if root_radius <= axis_relief:
            raise ValueError(
                f"slot root {root_radius:g} is inside axis relief {axis_relief:g}"
            )
        slots.append(
            _horizontal_open_slot(
                axis_relief=axis_relief,
                root_radius=root_radius,
                center_z=float(z),
                width=slot_width,
                mouth_flare=mouth_flare,
                lead_in=lead_in,
                round_segments=round_segments,
            )
        )
    return clean_polygonal(half_disc.difference(unary_union(slots)))


def annular_sector(
    *,
    inner_radius: float,
    outer_radius: float,
    start_angle_rad: float,
    end_angle_rad: float,
    arc_steps: int = 10,
) -> Polygon:
    """Return one closed annular-sector cell."""
    if outer_radius <= inner_radius or inner_radius < 0.0:
        raise ValueError("expected 0 <= inner_radius < outer_radius")
    steps = max(2, int(arc_steps))
    outer_angles = np.linspace(start_angle_rad, end_angle_rad, steps + 1)
    outer = [
        (outer_radius * math.cos(a), outer_radius * math.sin(a))
        for a in outer_angles
    ]
    if inner_radius <= 1e-12:
        points = [(0.0, 0.0), *outer, (0.0, 0.0)]
    else:
        inner_angles = np.linspace(end_angle_rad, start_angle_rad, steps + 1)
        inner = [
            (inner_radius * math.cos(a), inner_radius * math.sin(a))
            for a in inner_angles
        ]
        points = [*outer, *inner]
    return Polygon(points)


def meridian_cell(
    *,
    sphere_radius: float,
    z_min: float,
    z_max: float,
    chroma_min: float,
    chroma_max: float,
    z_steps: int = 12,
) -> Polygon:
    """
    Return one cell in a half-disc cross-section.

    Constant-chroma boundaries follow
    ``x = chroma * sqrt(R² - z²)``, so each row expresses chroma as radial
    distance from the neutral axis rather than Euclidean distance from the
    sphere center.
    """
    if not -sphere_radius <= z_min < z_max <= sphere_radius:
        raise ValueError("invalid z interval")
    # A small value above 1.0 is useful for the outermost clipping cell: its
    # sampled curve then safely covers the exact circular master profile.
    if not 0.0 <= chroma_min < chroma_max <= 1.10:
        raise ValueError("invalid chroma interval")
    zs = np.linspace(z_min, z_max, max(2, int(z_steps)) + 1)

    def radius_at(z: float) -> float:
        return math.sqrt(max(0.0, sphere_radius * sphere_radius - z * z))

    outer = [(chroma_max * radius_at(float(z)), float(z)) for z in zs]
    inner = [
        (chroma_min * radius_at(float(z)), float(z))
        for z in reversed(zs)
    ]
    return Polygon([*outer, *inner])


def extrude_polygonal(
    geometry: PolygonLike,
    *,
    height: float,
    label: str,
    min_area: float = 1e-5,
) -> trimesh.Trimesh:
    """Extrude every polygon component and return one validated multi-body mesh."""
    if height <= 0.0:
        raise ValueError("height must be > 0")
    cleaned = clean_polygonal(geometry, min_area=min_area)
    meshes = []
    for poly in iter_polygons(cleaned):
        mesh = trimesh.creation.extrude_polygon(poly, height=height, engine="earcut")
        mesh.remove_unreferenced_vertices()
        try:
            trimesh.repair.fix_normals(mesh)
        except Exception:
            pass
        if mesh.is_watertight and not mesh.is_volume:
            mesh.invert()
        if not mesh.is_watertight or not mesh.is_volume:
            raise ValueError(
                f"invalid extrusion for {label}: "
                f"watertight={mesh.is_watertight} volume={mesh.is_volume}"
            )
        meshes.append(mesh)
    if not meshes:
        raise ValueError(f"empty extrusion for {label}")
    result = meshes[0] if len(meshes) == 1 else trimesh.util.concatenate(meshes)
    result.remove_unreferenced_vertices()
    if not result.is_watertight or not result.is_volume:
        raise ValueError(
            f"invalid combined extrusion for {label}: "
            f"watertight={result.is_watertight} volume={result.is_volume}"
        )
    return result


def transformed_copy(mesh: trimesh.Trimesh, matrix: np.ndarray) -> trimesh.Trimesh:
    """Return a transformed copy without mutating the source mesh."""
    result = mesh.copy()
    result.apply_transform(np.asarray(matrix, dtype=np.float64))
    return result


def assembled_disc_transform(level_z: float, thickness: float) -> np.ndarray:
    """Place a flat disc horizontally, centered on its requested globe level."""
    matrix = np.eye(4, dtype=np.float64)
    matrix[2, 3] = float(level_z) - float(thickness) * 0.5
    return matrix


def assembled_wedge_transform(angle_deg: float, thickness: float) -> np.ndarray:
    """
    Map local wedge coordinates ``(radius, globe_z, extrusion)`` into world XYZ.
    """
    angle = math.radians(float(angle_deg))
    c = math.cos(angle)
    s = math.sin(angle)
    matrix = np.array(
        [
            [c, 0.0, -s, s * thickness * 0.5],
            [s, 0.0, c, -c * thickness * 0.5],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    return matrix
