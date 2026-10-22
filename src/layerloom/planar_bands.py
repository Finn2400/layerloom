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
    """XY grid, gap closure, and mesh contour displacement budgets, in mm."""

    precision: float = 1e-5
    max_gap: float = 0.03
    contour_tolerance: float = 0.001

    def __post_init__(self):
        if not math.isfinite(self.precision) or self.precision <= 0:
            raise ValueError("precision must be finite and positive")
        if not math.isfinite(self.max_gap) or self.max_gap < 0:
            raise ValueError("max_gap must be finite and nonnegative")
        if not math.isfinite(self.contour_tolerance) or self.contour_tolerance < 0:
            raise ValueError("contour_tolerance must be finite and nonnegative")


@dataclass
class ProfileResult:
    region: object
    diagnostics: dict
    ok: bool


@dataclass
class LayerProfile:
    """Recovered source contour on a nominal layer; not an exported solid."""

    index: int
    low: float
    high: float
    sample_z: float
    profile: ProfileResult


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


def _boundaries_within_tolerance(first, second, tolerance):
    """Conservatively bound every boundary point, including segment interiors.

    GEOS's Hausdorff distance samples line segments. The polygonal round buffers
    lie inside the exact distance band, so mutual coverage also checks points
    between samples without relying on that sampled estimate as an upper bound.
    """
    return first.covered_by(second.buffer(tolerance)) and second.covered_by(first.buffer(tolerance))


def _closed_mesh_profile(mesh, z, config, failed, raw_lines):
    """Recover only bounded defects in otherwise complete ordered section loops.

    Noding quantized linework can turn tiny self-crossings or near-touching
    vertices into branches. The source section still supplies ordered closed
    loops. Simplify those loops without grid rounding, but accept the result
    only after measuring its actual displacement and area change. No loop may
    disappear, split, or acquire a hole, and distinct loops must retain nesting.
    This is a mesh-section fallback, not a general crossing-line fill policy.
    """
    if config.contour_tolerance == 0:
        return failed
    # Splitting by face connectivity first preserves separate topological fans
    # at geometric contacts. A single Path3D would weld equal XY coordinates
    # and can invent connecting chords between point-touching solids.
    paths = [component.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
             for component in mesh.split(only_watertight=False, repair=False)]
    paths = [path for path in paths if path is not None]
    if not paths:
        return failed
    rings = [Polygon(np.asarray(loop)[:, :2]) for path in paths for loop in path.discrete]
    if not rings or any(p.is_empty or p.area <= 0 for p in rings):
        return failed

    tolerance = config.contour_tolerance
    candidates, changes = [], []
    for original in rings:
        reference = original if original.is_valid else shapely.make_valid(original)
        area_budget = tolerance * original.length + math.pi * tolerance ** 2
        candidate = None
        factors = (0.0,) if original.is_valid else (1.0, 0.5, 0.25, 0.125, 0.0625)
        for factor in factors:
            proposed = original.simplify(tolerance * factor, preserve_topology=True)
            if not proposed.is_valid:
                proposed = original.simplify(tolerance * factor, preserve_topology=False)
            if proposed.geom_type != "Polygon" or proposed.is_empty or not proposed.is_valid or proposed.interiors:
                continue
            displacement = float(shapely.hausdorff_distance(original.boundary, proposed.boundary, densify=0.25))
            area_change = float(reference.symmetric_difference(proposed).area)
            if (displacement <= tolerance and area_change <= area_budget
                    and _boundaries_within_tolerance(original.boundary, proposed.boundary, tolerance)):
                candidate = proposed
                break
        if candidate is None:
            return failed
        candidates.append(candidate)
        changes.append({"source_valid": bool(original.is_valid),
                        "source_vertices": len(original.exterior.coords) - 1,
                        "result_vertices": len(candidate.exterior.coords) - 1,
                        "max_deviation_mm": displacement,
                        "simplification_tolerance_mm": tolerance * factor,
                        "symmetric_difference_mm2": area_change,
                        "area_budget_mm2": float(area_budget)})

    # Every loop remains a loop, and its relationship to other loops is checked
    # before parity filling. A fit must not merge two islands or close a cavity.
    for i, candidate in enumerate(candidates):
        for j in range(i):
            other = candidates[j]
            contact = candidate.boundary.intersection(other.boundary)
            source_contact = rings[i].boundary.intersection(rings[j].boundary)
            if candidate.overlaps(other):
                return failed
            if not contact.is_empty and (contact.geom_type not in {"Point", "MultiPoint"}
                                         or source_contact.geom_type not in {"Point", "MultiPoint"}):
                return failed
            if contact.is_empty != source_contact.is_empty:
                return failed
            source_contains = rings[i].area > rings[j].area and bool(rings[i].contains(rings[j].representative_point()))
            source_inside = rings[j].area > rings[i].area and bool(rings[j].contains(rings[i].representative_point()))
            if source_contains != bool(candidate.contains(other)):
                return failed
            if source_inside != bool(other.contains(candidate)):
                return failed

    region = Polygon()
    for candidate in candidates:
        region = region.symmetric_difference(candidate)
    if region.is_empty or not region.is_valid:
        return failed
    # Trimesh may report a tiny backtracking spur as a dangling entity. Never
    # simply discard it: compare the final boundary with *all* raw segments,
    # including entities omitted from closed_paths, under the same distance cap.
    raw_boundary = shapely.multilinestrings(raw_lines[:, :, :2])
    global_deviation = float(shapely.hausdorff_distance(raw_boundary, region.boundary, densify=0.25))
    if (global_deviation > tolerance
            or not _boundaries_within_tolerance(raw_boundary, region.boundary, tolerance)):
        return failed
    d = dict(failed.diagnostics)
    d.update(status="contour_repaired", recovery_method="bounded ordered-loop simplification",
             fill_rule="even-odd ordered loops; bounded mesh contour repair",
             strict_diagnostics=dict(failed.diagnostics),
             strict_status=failed.diagnostics["status"],
             strict_branching_vertices=failed.diagnostics["branching_vertices"],
             strict_open_endpoints=failed.diagnostics["open_endpoints"],
             bridges=[], max_bridge_length=0.0, max_snap_displacement=0.0,
             branching_vertices=0, open_endpoints=0, cut_length=0.0, dangle_length=0.0,
             invalid_rings=0, contour_tolerance=tolerance, contour_repairs=changes,
             source_dangling_entities=sum(len(path.dangling) for path in paths),
             max_contour_deviation_mm=global_deviation,
             contour_deviation_bound_mm=tolerance,
             contour_bound_check="mutual coverage by inscribed distance-band buffers",
             contour_symmetric_difference_mm2=sum(c["symmetric_difference_mm2"] for c in changes),
             area=float(region.area), components=len(shapely.get_parts(region)),
             holes=sum(len(p.interiors) for p in shapely.get_parts(region)))
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
    if not result.ok:
        result = _closed_mesh_profile(mesh, z, config, result, lines)
    result.diagnostics["sample_z"] = float(z)
    return result


def prepare_export_region(region):
    """Quantize XY before triangulation to the actual XML/float32 output lattice.

    Triangulating at double precision and rounding afterwards can flatten a
    narrow cap triangle. This performs that unavoidable representation change
    first. Coordinate collisions are normalized only when all changed area
    stays in the original boundary's measured rounding band. Component and
    hole changes are reported; a whole component may not disappear.
    """
    if region.is_empty:
        return region, {"quantization_area_error_mm2": 0.0,
                        "max_xy_displacement_mm": 0.0}
    if not region.is_valid or region.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError("extrusion requires valid polygonal geometry")
    # Clear an inherited GEOS precision model before changing coordinates or
    # measuring errors: it could otherwise resnap output to an earlier grid.
    region = shapely.set_precision(region, 0)
    source_parts = list(shapely.get_parts(region))

    def quantize(geometry):
        coordinates = shapely.get_coordinates(geometry)
        rounded = np.round(coordinates, 9).astype(np.float32).astype(np.float64)
        # A second pass makes decimal serialization stable close to zero.
        rounded = np.round(rounded, 9).astype(np.float32).astype(np.float64)
        if not np.isfinite(rounded).all():
            raise ValueError("polygon coordinates exceed the float32 export range")
        return shapely.set_coordinates(geometry, rounded), float(
            np.linalg.norm(rounded - coordinates, axis=1).max())

    def polygon_parts(geometry):
        if geometry.geom_type == "Polygon":
            return [geometry] if not geometry.is_empty else []
        if hasattr(geometry, "geoms"):
            return [polygon for part in geometry.geoms for polygon in polygon_parts(part)]
        return []

    output, displacement = quantize(region)
    normalization_passes = 0
    for _ in range(3):
        if output.is_valid and not output.is_empty and output.area > 0:
            break
        polygons = polygon_parts(shapely.make_valid(output))
        if not polygons:
            raise ValueError("polygon collapses at float32 export precision")
        output, _ = quantize(shapely.union_all(polygons, grid_size=0))
        normalization_passes += 1
    if output.is_empty or not output.is_valid or output.area <= 0:
        raise ValueError("polygon remains invalid at float32 export precision")
    output_parts = list(shapely.get_parts(output))
    if any(part.intersection(output).area <= 0 for part in source_parts):
        raise ValueError("polygon component collapses at float32 export precision")
    changed = region.symmetric_difference(output)
    # A buffer bounds changed *filled area*, including a disappearing narrow
    # slit. A Hausdorff bound would misreport that slit as a large movement.
    numerical_slack = max(float(np.max(np.abs(shapely.get_coordinates(region)))) *
                          np.finfo(float).eps * 64, 1e-12)
    # GEOS approximates circular buffer arcs with inscribed chords. Its fillet
    # segment count rounds to the nearest integer, so a short join can use an
    # angular step up to 1.5 times the nominal quadrant step. Compensate that
    # worst-case sagitta rather than rejecting points at the measured radius.
    # See GEOS OffsetSegmentGenerator::addDirectedFillet (nSegs = angle/step+.5).
    arc_segments = 32
    buffer_radius = (displacement + numerical_slack) / math.cos(3 * math.pi / (8 * arc_segments))
    boundary_band = region.boundary.buffer(buffer_radius, quad_segs=arc_segments)
    outside = changed.difference(boundary_band).area
    if outside > numerical_slack ** 2 or changed.area > boundary_band.area + numerical_slack ** 2:
        raise ValueError("float32 normalization exceeds the measured rounding boundary band")
    contact_pairs = shapely.STRtree(output_parts).query(output_parts, predicate="intersects")
    component_contacts = int(np.count_nonzero(contact_pairs[0] < contact_pairs[1]))
    return output, {
        "quantization_area_error_mm2": float(changed.area),
        "max_xy_displacement_mm": displacement,
        "quantization_normalization_passes": normalization_passes,
        "quantization_components_before": len(source_parts),
        "quantization_components_after": len(output_parts),
        "quantization_holes_before": sum(len(part.interiors) for part in source_parts),
        "quantization_holes_after": sum(len(part.interiors) for part in output_parts),
        "quantization_boundary_band_area_mm2": float(boundary_band.area),
        "quantization_buffer_chord_allowance_mm": float(buffer_radius - displacement - numerical_slack),
        "geometric_component_contacts": component_contacts,
    }


def _split_cap_vertex_fans(vertices, faces):
    """Give disconnected triangle fans distinct indices at a geometric contact.

    A valid planar polygon can have a hole touching its shell at one point.
    Coordinate welding joins the resulting vertical edges into a nonmanifold
    edge. Splitting their indices preserves the footprint but does not remove
    the geometric contact; callers receive an explicit contact count.
    """
    from collections import defaultdict

    incident = defaultdict(list)
    for face_index, face in enumerate(faces):
        for vertex in face:
            incident[int(vertex)].append(face_index)
    points, faces = vertices.tolist(), faces.copy()
    splits = 0
    for vertex, face_indices in incident.items():
        edge_faces = defaultdict(list)
        for face_index in face_indices:
            for other in faces[face_index]:
                if other != vertex:
                    edge_faces[int(other)].append(face_index)
        neighbors = defaultdict(set)
        for shared in edge_faces.values():
            for face_index in shared:
                neighbors[face_index].update(shared)
        unseen, components = set(face_indices), []
        while unseen:
            stack, component = [unseen.pop()], []
            while stack:
                face_index = stack.pop()
                component.append(face_index)
                for other in neighbors[face_index] & unseen:
                    unseen.remove(other)
                    stack.append(other)
            components.append(component)
        for component in components[1:]:
            replacement = len(points)
            points.append(points[vertex])
            for face_index in component:
                faces[face_index][faces[face_index] == vertex] = replacement
            splits += 1
    return np.asarray(points, dtype=float), faces, splits


def _extrude_indexed_cap(vertices, faces, low, high):
    """Extrude cap topology without a coordinate-welding postprocess."""
    a = vertices[faces[:, 1]] - vertices[faces[:, 0]]
    b = vertices[faces[:, 2]] - vertices[faces[:, 0]]
    cross = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
    if np.any(cross == 0):
        raise ValueError("triangulator produced a collapsed cap triangle")
    faces = faces.copy()
    faces[cross < 0] = faces[cross < 0, ::-1]
    vertices, faces, splits = _split_cap_vertex_fans(vertices, faces)
    count = len(vertices)
    edges = np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]))
    _, inverse, incidence = np.unique(np.sort(edges, axis=1), axis=0,
                                      return_inverse=True, return_counts=True)
    if np.any(incidence > 2):
        raise ValueError("triangulated cap has a nonmanifold edge")
    boundary = edges[incidence[inverse] == 1]
    left, right = boundary.T
    sides = np.vstack((np.column_stack((left, right, right + count)),
                       np.column_stack((left, right + count, left + count))))
    points = np.vstack((np.column_stack((vertices, np.full(count, low))),
                        np.column_stack((vertices, np.full(count, high)))))
    triangles = np.vstack((faces[:, ::-1], faces + count, sides))
    mesh = trimesh.Trimesh(vertices=points, faces=triangles, process=False)
    return mesh, splits


def _cdt_cap_arrays(triangulated):
    """Read all CDT triangle coordinates without creating Python ring objects.

    GEOS returns closed, two-dimensional triangles in collection order. Keep
    that exact order when dropping each closing coordinate, so this fast path
    produces the same indexed cap as reading every exterior ring individually.
    """
    triangles = shapely.get_parts(triangulated)
    if (not len(triangles)
            or np.any(shapely.get_type_id(triangles) != shapely.GeometryType.POLYGON)
            or np.any(shapely.get_num_coordinates(triangles) != 4)
            or np.any(shapely.has_z(triangles))):
        raise ValueError("constrained triangulator must return closed 2D triangles")
    coordinates = shapely.get_coordinates(triangles, include_z=False).reshape(-1, 4, 2)
    if not np.array_equal(coordinates[:, 0], coordinates[:, -1]):
        raise ValueError("constrained triangulator returned an open triangle ring")
    points = coordinates[:, :3].reshape(-1, 2)
    vertices, inverse = np.unique(points, axis=0, return_inverse=True)
    return vertices, inverse.reshape(-1, 3)


def extrude_profile(region, low: float, high: float) -> trimesh.Trimesh | None:
    """Build closed bands on the output coordinate lattice, retaining holes.

    Mesh metadata records the representation error and any geometric point
    contacts preserved with separate topological vertex fans. Those contacts
    are not claimed to be a clean geometric union.
    """
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        raise ValueError("band limits must be finite and increasing")
    if region.is_empty:
        return None
    region, diagnostics = prepare_export_region(region)
    meshes = []
    splits = 0
    cdt = getattr(shapely, "constrained_delaunay_triangles", None)
    for polygon in shapely.get_parts(region):
        if cdt is not None:
            triangulated = cdt(polygon)
            vertices, faces = _cdt_cap_arrays(triangulated)
        else:
            # Keep basic inputs usable with Shapely 2.0. Validation below still
            # rejects any degenerate triangulation instead of dropping faces.
            vertices, faces = trimesh.creation.triangulate_polygon(polygon, engine="earcut")
        mesh, contacts = _extrude_indexed_cap(vertices, faces, low, high)
        if not mesh.is_volume:
            raise ValueError("polygon extrusion did not produce a closed positive volume")
        exported = np.round(np.asarray(mesh.vertices), 9).astype(np.float32).astype(float)
        triangle = exported[mesh.faces]
        area_vector = np.cross(triangle[:, 1] - triangle[:, 0],
                               triangle[:, 2] - triangle[:, 0])
        if np.any(np.all(area_vector == 0, axis=1)):
            raise ValueError("extruded triangle collapses at float32 export precision")
        if not np.isclose(mesh.volume, polygon.area * (high - low), rtol=1e-8, atol=1e-12):
            raise ValueError("triangulation changed the polygon's filled area")
        meshes.append(mesh)
        splits += contacts
    result = trimesh.util.concatenate(meshes)
    result.metadata["extrusion"] = {
        **diagnostics,
        "triangulator": "constrained_delaunay" if cdt is not None else "earcut",
        "split_vertex_fans": splits,
        "geometric_point_contacts": bool(splits or diagnostics["geometric_component_contacts"]),
    }
    return result


def reconstruct_layer_profiles(mesh: trimesh.Trimesh, layer_height: float, z0: float | None = None,
                               config: RepairConfig = RepairConfig()) -> list[LayerProfile]:
    """Recover bounded source contours without constructing intermediate solids.

    The sample grid is authoritative. Short features between samples can vanish;
    a partial top layer can extend beyond the source by up to half a layer.
    No automatic switch between this backend and capped 3D clipping is performed.
    A successful profile is not a claim that its individual extrusion is valid.
    Callers assembling material regions must validate their final extruded meshes.
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
        result.append(LayerProfile(k, low, high, sample_z, profile))
    return result


def reconstruct_layers(mesh: trimesh.Trimesh, layer_height: float, z0: float | None = None,
                       config: RepairConfig = RepairConfig()) -> list[BandResult]:
    """Reconstruct full nominal bands; retain every failure for caller diagnostics.

    This compatibility API still extrudes and validates every source-part band.
    Material assembly can use ``reconstruct_layer_profiles`` to avoid extruding
    source bands that would be discarded after their contours are partitioned.
    """
    result = []
    for layer in reconstruct_layer_profiles(mesh, layer_height, z0=z0, config=config):
        band = None
        if layer.profile.ok:
            try:
                band = extrude_profile(layer.profile.region, layer.low, layer.high)
            except Exception as exc:
                layer.profile.ok = False
                layer.profile.diagnostics.update(status="extrusion_failed", error=str(exc))
        result.append(BandResult(layer.index, layer.low, layer.high, layer.sample_z, layer.profile, band))
    return result
