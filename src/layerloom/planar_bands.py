"""Experimental fixed-layer reconstruction from bounded repairs of XY contours.

This module does not repair the source surface. It recovers a polygon at each
printer-layer midpoint and extrudes that polygon over the nominal layer span.
Open chains and branching intersections are rejected rather than filled by an
unbounded heuristic. Surface-only color patches need a separate material policy.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPoint, Polygon
from scipy.spatial import cKDTree
import trimesh


@dataclass(frozen=True)
class RepairConfig:
    """XY grid spacing and maximum explicitly permitted gap, in input units (mm)."""

    precision: float = 1e-5
    max_gap: float = 0.03

    def __post_init__(self):
        if not math.isfinite(self.precision) or self.precision <= 0:
            raise ValueError("precision must be finite and positive")
        if not math.isfinite(self.max_gap) or self.max_gap < 0:
            raise ValueError("max_gap must be finite and nonnegative")


@dataclass
class ProfileResult:
    region: object
    diagnostics: dict
    ok: bool


@dataclass
class BandResult:
    index: int
    low: float
    high: float
    sample_z: float
    profile: ProfileResult
    mesh: trimesh.Trimesh | None


def _segments(geometry):
    """Flatten noded linework to elementary segments, retaining intersection nodes."""
    result = []
    for line in shapely.get_parts(geometry):
        if line.geom_type != "LineString":
            continue
        coords = np.asarray(line.coords, dtype=float)[:, :2]
        result.extend(np.stack((coords[:-1], coords[1:]), axis=1))
    return np.asarray(result, dtype=float).reshape(-1, 2, 2)


def _graph(segments):
    if not len(segments):
        return np.empty((0, 2)), np.empty(0, dtype=int)
    vertices, inverse = np.unique(segments.reshape(-1, 2), axis=0, return_inverse=True)
    return vertices, np.bincount(inverse, minlength=len(vertices))


def _node(segments, precision):
    return shapely.union_all([LineString(s) for s in segments], grid_size=precision)


def _odd_crossings(point, segments):
    """Half-open horizontal ray rule, independent of source triangle winding."""
    x, y = point.x, point.y
    a, b = segments[:, 0], segments[:, 1]
    crossing = (a[:, 1] > y) != (b[:, 1] > y)
    a, b = a[crossing], b[crossing]
    x_hit = a[:, 0] + (y - a[:, 1]) * (b[:, 0] - a[:, 0]) / (b[:, 1] - a[:, 1])
    return int(np.count_nonzero(x_hit > x)) % 2 == 1


def recover_profile(segments, config: RepairConfig = RepairConfig()) -> ProfileResult:
    """Recover a filled region from raw XY segments, preserving nested holes.

    Endpoints move by at most ``precision / sqrt(2)`` during initial rounding.
    Only endpoints with exactly one candidate within ``max_gap`` are bridged,
    and only if the bridge intersects existing linework at its endpoints alone.
    This is a bounded geometric assumption, not proof of the intended surface.
    Ambiguous/open results return an empty region with ``ok=False``.
    """
    raw = np.asarray(segments, dtype=float)
    if raw.size == 0:
        raw = np.empty((0, 2, 2))
    if raw.ndim != 3 or raw.shape[1:] != (2, 2) or not np.isfinite(raw).all():
        raise ValueError("segments must be finite with shape (n, 2, 2)")
    d = dict(status="empty", input_segments=len(raw), precision=config.precision,
             max_gap=config.max_gap, fill_rule="even-odd; branching rejected",
             duplicate_segments=0, collapsed_segments=0, max_snap_displacement=0.0,
             bridges=[], max_bridge_length=0.0, open_endpoints=0,
             branching_vertices=0, ambiguous_endpoints=0, rejected_bridges=0,
             cut_length=0.0, dangle_length=0.0, invalid_rings=0,
             area=0.0, components=0, holes=0)
    if not len(raw):
        return ProfileResult(Polygon(), d, True)
    snapped = np.rint(raw / config.precision) * config.precision
    if not np.isfinite(snapped).all():
        raise ValueError("coordinates exceed the precision grid's numeric range")
    d["max_snap_displacement"] = float(np.linalg.norm(snapped - raw, axis=2).max())
    vertices, inverse = np.unique(snapped.reshape(-1, 2), axis=0, return_inverse=True)
    edges = np.sort(inverse.reshape(-1, 2), axis=1)
    nonzero = edges[:, 0] != edges[:, 1]
    d["collapsed_segments"] = int(np.count_nonzero(~nonzero))
    unique_edges = np.unique(edges[nonzero], axis=0)
    d["duplicate_segments"] = int(np.count_nonzero(nonzero) - len(unique_edges))
    if not len(unique_edges):
        d["status"] = "collapsed"
        return ProfileResult(Polygon(), d, False)

    linework = _node(vertices[unique_edges], config.precision)
    noded = _segments(linework)
    nodes, degrees = _graph(noded)
    d["branching_vertices"] = int(np.count_nonzero(degrees > 2))
    ends = nodes[degrees == 1]
    if not d["branching_vertices"] and len(ends) and config.max_gap > 0:
        pairs = sorted(cKDTree(ends).query_pairs(config.max_gap))
        candidates = np.zeros(len(ends), dtype=int)
        for a, b in pairs:
            candidates[a] += 1
            candidates[b] += 1
        d["ambiguous_endpoints"] = int(np.count_nonzero(candidates > 1))
        for a, b in pairs:
            if candidates[a] != 1 or candidates[b] != 1:
                continue
            bridge = LineString([ends[a], ends[b]])
            extra = bridge.intersection(linework).difference(MultiPoint([ends[a], ends[b]]))
            if not extra.is_empty:
                d["rejected_bridges"] += 1
                continue
            d["bridges"].append([ends[a].tolist(), ends[b].tolist()])
            d["max_bridge_length"] = max(d["max_bridge_length"], float(bridge.length))
            linework = shapely.union_all([linework, bridge], grid_size=config.precision)
        noded = _segments(linework)
        nodes, degrees = _graph(noded)

    d["open_endpoints"] = int(np.count_nonzero(degrees == 1))
    d["branching_vertices"] = int(np.count_nonzero(degrees > 2))
    polygons, cuts, dangles, invalid = shapely.polygonize_full(shapely.get_parts(linework))
    d["cut_length"] = float(cuts.length)
    d["dangle_length"] = float(dangles.length)
    d["invalid_rings"] = len(shapely.get_parts(invalid))
    if d["branching_vertices"] or d["open_endpoints"] or not cuts.is_empty or not dangles.is_empty or not invalid.is_empty:
        d["status"] = "branching" if d["branching_vertices"] else "unresolved"
        return ProfileResult(Polygon(), d, False)

    kept = [p for p in shapely.get_parts(polygons) if _odd_crossings(p.representative_point(), noded)]
    region = shapely.union_all(kept, grid_size=config.precision)
    if region.is_empty or not region.is_valid:
        d["status"] = "invalid_region"
        return ProfileResult(Polygon(), d, False)
    pieces = list(shapely.get_parts(region))
    d.update(status="repaired" if d["bridges"] else "ok", area=float(region.area),
             components=len(pieces), holes=sum(len(p.interiors) for p in pieces))
    return ProfileResult(region, d, True)


def section_profile(mesh: trimesh.Trimesh, z: float,
                    config: RepairConfig = RepairConfig()) -> ProfileResult:
    """Intersect triangles directly; source topology and face winding may be broken."""
    if not math.isfinite(z):
        raise ValueError("section height must be finite")
    if not np.isfinite(mesh.vertices).all():
        raise ValueError("mesh coordinates must be finite")
    lines = trimesh.intersections.mesh_plane(mesh, plane_normal=[0, 0, 1],
                                             plane_origin=[0, 0, z])
    result = recover_profile(lines[:, :, :2], config)
    result.diagnostics["sample_z"] = float(z)
    return result


def extrude_profile(region, low: float, high: float) -> trimesh.Trimesh | None:
    """Build watertight positive-volume slabs, including polygon holes."""
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        raise ValueError("band limits must be finite and increasing")
    if region.is_empty:
        return None
    if not region.is_valid or region.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError("extrusion requires valid polygonal geometry")
    meshes = []
    for polygon in shapely.get_parts(region):
        mesh = trimesh.creation.extrude_polygon(polygon, height=high - low, engine="earcut")
        mesh.apply_translation([0, 0, low])
        if not mesh.is_volume:
            raise ValueError("polygon extrusion did not produce a closed positive volume")
        meshes.append(mesh)
    return trimesh.util.concatenate(meshes)


def reconstruct_layers(mesh: trimesh.Trimesh, layer_height: float, z0: float | None = None,
                       config: RepairConfig = RepairConfig()) -> list[BandResult]:
    """Reconstruct full nominal bands; retain every failure for caller diagnostics.

    The sample grid is authoritative. Short features between samples can vanish;
    a partial top layer can extend beyond the source by up to half a layer.
    No automatic switch between this backend and capped 3D clipping is performed.
    """
    if not math.isfinite(layer_height) or layer_height <= 0:
        raise ValueError("layer_height must be finite and positive")
    if not len(mesh.faces) or not np.isfinite(mesh.vertices).all():
        raise ValueError("mesh must have faces and finite vertices")
    minz, maxz = (float(v) for v in mesh.bounds[:, 2])
    if z0 is None:
        z0 = minz
    if not math.isfinite(z0):
        raise ValueError("z0 must be finite")
    if maxz <= minz:
        raise ValueError("mesh must have positive Z extent")
    first = math.floor((minz - z0) / layer_height)
    last = math.ceil((maxz - z0) / layer_height)
    result = []
    for k in range(first, last):
        low, high = z0 + k * layer_height, z0 + (k + 1) * layer_height
        sample_z = z0 + (k + 0.5) * layer_height
        profile = section_profile(mesh, sample_z, config)
        if profile.region.is_empty and profile.ok:
            profile.diagnostics["sampling_warning"] = (
                "No contour at the nominal midpoint; sublayer features may be missed."
            )
        band = None
        if profile.ok:
            try:
                band = extrude_profile(profile.region, low, high)
            except Exception as exc:
                profile.ok = False
                profile.diagnostics.update(status="extrusion_failed", error=str(exc))
        result.append(BandResult(k, low, high, sample_z, profile, band))
    return result
