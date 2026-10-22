"""Prototype surface-paint to printable, material-assigned 3MF conversion.

Paint is extended through the source volume from its nearest surface triangle.
Each horizontal layer retains the source midpoint cross-section to the declared
coordinate precision; the material boundary is sampled on an XY grid. The
resulting exterior is layer-stepped. Source meshes stay intact.
"""

from __future__ import annotations

from collections import defaultdict
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping
from xml.etree import ElementTree as ET
import zipfile

import manifold3d as mf
import numpy as np
import shapely
import trimesh

from layerloom.export import LeafMeshSpec, write_orca_multipart_3mf
from layerloom.palette_assignment import load_palette_entries, nearest_palette_entry
from layerloom.surface_field_weave import mesh_defects, planar_region, polygons
from layerloom.tokens import ALL_TOKENS, COLOR_OBJECT_LABELS, TOKEN_HEX, token_is_valid


def _hex(rgb: np.ndarray) -> str:
    return "#" + "".join(f"{int(c):02X}" for c in rgb[:3])


def _material_order(materials) -> list[str]:
    present = set(materials)
    return [token for token in ALL_TOKENS if token in present] + sorted(present.difference(ALL_TOKENS))


def _material_hex(token: str) -> str:
    return token.upper() if token.startswith("#") else TOKEN_HEX[token]


def _material_label(token: str) -> str:
    return f"source_{token[1:].upper()}" if token.startswith("#") else COLOR_OBJECT_LABELS[token]


def _brand_geometry_archive(path: Path, tokens: list[str]) -> None:
    """Identify this as geometry with part assignments, without printer presets.

    Advertising a Bambu project version without project_settings.config makes
    current slicer CLIs enter project migration with missing settings and crash.
    Generic application metadata retains multipart/extruder metadata while the
    slicer supplies its own explicit printer, process, and filament profiles.
    """
    rewritten = path.with_suffix(".branded.3mf")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(rewritten, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "3D/3dmodel.model":
                root = ET.fromstring(content)
                for child in list(root):
                    if child.tag.rsplit("}", 1)[-1] != "metadata":
                        continue
                    if child.get("name") == "Application":
                        child.text = "LayerLoom Paint Prototype"
                    elif child.get("name") == "BambuStudio:3mfVersion":
                        root.remove(child)
                content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            target.writestr(info, content)
        # These arrays describe slot colors only. Printer, process and physical
        # material presets remain a separate slicer choice.
        target.writestr("Metadata/project_settings.config", json.dumps({
            "filament_colour": [_material_hex(token) for token in tokens],
            "filament_settings_id": [f"LayerLoom {_material_label(token)}" for token in tokens],
        }, indent=2))
    os.replace(rewritten, path)


def assign_palette(document: Any, palette_name: str = "Normal", *,
                   mode: str = "woven",
                   recipe_overrides: Mapping[str, str] | None = None) -> dict[str, dict[str, Any]]:
    """Map distinct edited RGB colors to palette entries, without editing paint.

    In ``source`` mode every edited RGB is its own physical filament, with no
    palette conversion. In ``direct`` mode only one-filament entries are eligible. Alpha does not
    change printable material; export rejects non-opaque colors explicitly.
    """
    if mode not in {"woven", "direct", "source"}:
        raise ValueError("mode must be 'woven', 'direct', or 'source'")
    entries = [] if mode == "source" else load_palette_entries(palette_name)
    if mode == "direct":
        entries = [entry for entry in entries if len(str(entry["token"])) == 1]
    if not entries and mode != "source":
        raise ValueError("The selected palette has no eligible print recipes")
    arrays = [np.asarray(mesh.face_rgba, dtype=np.uint8)[:, :3]
              for mesh in document.meshes if len(mesh.faces)]
    if not arrays:
        raise ValueError("The paint document contains no triangle meshes")
    if mode == "source":
        if recipe_overrides:
            raise ValueError("Source-color export preserves each RGB and does not accept recipe overrides")
        return {source: {"token": source, "hex": source, "source_hex": source}
                for source in (_hex(rgb) for rgb in np.unique(np.concatenate(arrays), axis=0))}
    overrides = {str(key).upper(): str(value).lower()
                 for key, value in (recipe_overrides or {}).items()}
    by_token = {str(entry["token"]): entry for entry in entries}
    result = {}
    for rgb in np.unique(np.concatenate(arrays), axis=0):
        source = _hex(rgb)
        override = overrides.get(source)
        if override is not None:
            if not token_is_valid(override) or (mode == "direct" and len(override) != 1):
                raise ValueError(f"Invalid {mode} recipe override {override!r} for {source}")
            if override not in by_token:
                raise ValueError(f"Recipe {override!r} is not in the selected {mode} palette")
            entry = dict(by_token[override])
        else:
            entry = nearest_palette_entry(entries, source)
            if entry is None:
                raise ValueError(f"No palette recipe for {source}")
        result[source] = {**entry, "source_hex": source}
    return result


def _as_manifold(mesh: trimesh.Trimesh, label: str) -> mf.Manifold:
    solid = mf.Manifold(mf.Mesh(
        np.ascontiguousarray(mesh.vertices, dtype=np.float32),
        np.ascontiguousarray(mesh.faces, dtype=np.uint32),
    ))
    if solid.status() != mf.Error.NoError or solid.num_tri() == 0:
        raise ValueError(f"{label}: geometry is not a usable closed solid ({solid.status()})")
    return solid


def _source_geometry(document: Any, assignments: Mapping[str, Mapping[str, Any]], *,
                     component_details: list | None = None):
    surfaces, solids, recipes = [], [], []
    source_rows = []
    for item in document.meshes:
        vertices = np.asarray(item.vertices, dtype=np.float64)
        faces = np.asarray(item.faces, dtype=np.int64)
        rgba = np.asarray(item.face_rgba)
        label = str(item.name or item.key)
        if vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all():
            raise ValueError(f"{label}: vertices must be finite XYZ coordinates")
        if faces.ndim != 2 or faces.shape[1] != 3 or not len(faces):
            raise ValueError(f"{label}: a non-empty triangular mesh is required")
        if np.any(faces < 0) or np.any(faces >= len(vertices)):
            raise ValueError(f"{label}: triangle index outside the vertex array")
        if rgba.shape != (len(faces), 4):
            raise ValueError(f"{label}: one RGBA color is required per triangle")
        if np.any(rgba[:, 3] != 255):
            raise ValueError(f"{label}: transparent paint has no defined printable material; make it opaque")
        vertex_count = len(vertices)
        # Importers may duplicate vertices at paint/UV seams. Welding leaves
        # triangle order intact, so face paint keeps its identity. Only exact
        # coordinate matches are merged: decimal rounding can merge distinct
        # vertices that remain separate at a slicer's float32 boundary.
        welded, inverse = np.unique(vertices, axis=0, return_inverse=True)
        mesh = trimesh.Trimesh(vertices=welded, faces=inverse[faces], process=False)
        mesh.remove_unreferenced_vertices()
        if not mesh.is_watertight or not mesh.is_winding_consistent:
            raise ValueError(f"{label}: paint export requires a closed, consistently oriented solid; "
                             "repair the intact source before exporting")
        if mesh.volume < 0:
            mesh.invert()
        if not mesh.is_volume:
            raise ValueError(f"{label}: mesh has no positive solid volume")
        defects = mesh_defects(mesh)
        if any(defects.values()):
            raise ValueError(f"{label}: source fails float32 geometry checks: {defects}")
        surfaces.append(mesh)
        solids.append(_as_manifold(mesh, label))
        recipes.extend(str(assignments[_hex(rgb)]["token"]) for rgb in rgba)
        # Surface-connected shells include negative cavity walls. They are
        # retained in the source solid, but are not disconnected positive
        # objects that a layer-midpoint preservation check should reject.
        groups = trimesh.graph.connected_components(mesh.face_adjacency,
                    nodes=np.arange(len(mesh.faces)), min_len=1)
        shell_rows = []
        for component_index, face_ids in enumerate(groups):
            part = mesh.submesh([face_ids], append=True, repair=False)
            local = np.asarray(part.triangles) - np.asarray(part.bounds).mean(axis=0)
            signed_volume = float(np.einsum("ij,ij->i", local[:, 0],
                                  np.cross(local[:, 1], local[:, 2])).sum() / 6)
            shell_rows.append({"component": component_index,
                               "signed_volume_mm3": signed_volume,
                               "bounds_mm": part.bounds.tolist()})
            if component_details is not None and signed_volume > 0:
                component_details.append({"key": str(item.key), "name": label,
                    "component": component_index, "mesh": part,
                    "signed_volume_mm3": signed_volume,
                    "recipes": sorted({str(assignments[_hex(rgb)]["token"]) for rgb in rgba[face_ids]})})
        source_rows.append({"key": str(item.key), "name": label,
                            "triangles": int(len(mesh.faces)),
                            "components": len(groups),
                            "positive_components": sum(row["signed_volume_mm3"] > 0 for row in shell_rows),
                            "negative_cavity_shells": sum(row["signed_volume_mm3"] < 0 for row in shell_rows),
                            "zero_volume_shells": sum(row["signed_volume_mm3"] == 0 for row in shell_rows),
                            "component_z_bounds_mm": [[row["bounds_mm"][0][2], row["bounds_mm"][1][2]]
                                                      for row in shell_rows if row["signed_volume_mm3"] > 0],
                            "surface_shells": shell_rows,
                            "welded_vertices": int(vertex_count - len(mesh.vertices)),
                            "volume_mm3": float(mesh.volume)})
    if not surfaces:
        raise ValueError("The paint document contains no solid meshes")
    surface = trimesh.util.concatenate(surfaces)
    source = mf.Manifold.batch_boolean(solids, mf.OpType.Add)
    if source.status() != mf.Error.NoError or source.num_tri() == 0:
        raise ValueError("Could not combine source solids")
    source_sum = sum(row["volume_mm3"] for row in source_rows)
    overlap = max(0.0, source_sum - float(source.volume()))
    if overlap > max(1e-5, source_sum * 1e-6):
        raise ValueError("Overlapping source solids have ambiguous surface paint. "
                         "Resolve their overlap before exporting (all disjoint components are supported).")
    return surface, source, np.asarray(recipes, dtype=object), source_rows


def _region_solid(region: Any, low: float, high: float) -> mf.Manifold:
    contours = []
    for polygon in polygons(region):
        contours.append(np.asarray(polygon.exterior.coords[:-1], dtype=np.float64))
        contours.extend(np.asarray(ring.coords[:-1], dtype=np.float64)
                        for ring in polygon.interiors)
    if not contours:
        return mf.Manifold()
    return mf.CrossSection(contours, mf.FillRule.EvenOdd).extrude(high - low).translate((0, 0, low))


def _nearest_faces(surface: trimesh.Trimesh, points: np.ndarray) -> np.ndarray:
    """Nearest triangles with stable lowest-face-ID ties on interior bisectors."""
    candidates = trimesh.proximity.nearby_faces(surface, points)
    sizes = np.asarray([len(group) for group in candidates], dtype=np.int64)
    starts = np.r_[0, np.cumsum(sizes)[:-1]]
    indices = np.concatenate(candidates)
    queries = np.repeat(points, sizes, axis=0)
    closest = trimesh.triangles.closest_point(np.asarray(surface.triangles)[indices], queries)
    squared = np.sum((queries - closest) ** 2, axis=1)
    minimum = np.minimum.reduceat(squared, starts)
    tolerance = max(1e-14, float(np.max(surface.extents)) ** 2 * 1e-14)
    tied = squared <= np.repeat(minimum + tolerance, sizes)
    return np.minimum.reduceat(np.where(tied, indices, len(surface.faces)), starts)


def _polygonal(geometry: Any, precision: float):
    """Drop boundary-only intersections before subsequent GEOS booleans."""
    return shapely.union_all(list(polygons(geometry)), grid_size=precision)


def _layer_domain(cross_section: Any, *, precision: float, layer_index: int):
    """Snap layer coordinates only after checking that no source island vanishes.

    Coverage checks against an already-snapped domain cannot detect an island
    that disappeared during snapping. Compare each unsnapped island explicitly;
    otherwise a narrow, separate solid could be omitted from a successful export.
    """
    original = planar_region(cross_section, precision=0.0)
    domain = shapely.set_precision(original, precision)
    if original.is_empty:
        return domain
    # Drop the precision model, not coordinates, for the overlap measurement.
    # This prevents the measurement itself from rounding a tiny island away.
    snapped = shapely.set_precision(domain, 0.0)
    for island in polygons(original):
        if shapely.intersection(island, snapped).area <= 0:
            raise ValueError(
                f"Layer {layer_index}: XY precision {precision:g} mm would remove "
                "a disconnected source component. Reduce the export precision "
                "value to retain it; no output was written."
            )
    return domain


def _partition_layer(surface: trimesh.Trimesh, face_recipes: np.ndarray,
                     domain: Any, *, z: float, layer_index: int, pitch: float,
                     origin: np.ndarray, precision: float, interface_offset: float,
                     source_colors: bool = False):
    """Clip a complete grid to the footprint, then color every surviving cell.

    Sampling each clipped cell at an interior point retains narrow/disconnected
    pieces even when no regular grid center lies inside them.
    """
    xmin, ymin, xmax, ymax = domain.bounds
    lo = np.floor((np.asarray((xmin, ymin)) - origin) / pitch).astype(int)
    hi = np.ceil((np.asarray((xmax, ymax)) - origin) / pitch).astype(int)
    xs = origin[0] + np.arange(lo[0], hi[0] + 1) * pitch
    ys = origin[1] + np.arange(lo[1], hi[1] + 1) * pitch
    xx, yy = np.meshgrid(xs[:-1], ys[:-1], indexing="xy")
    ux, uy = np.meshgrid(xs[1:], ys[1:], indexing="xy")
    cells = shapely.box(xx.ravel(), yy.ravel(), ux.ravel(), uy.ravel())
    cells = shapely.intersection(cells, domain, grid_size=precision)
    cells = cells[shapely.area(cells) > 0]
    # A clipped square can contain several disconnected islands. Give each its
    # own sample so a thin component cannot borrow a neighbor's surface color.
    pieces = [polygon for cell in cells for polygon in polygons(cell)]
    if not pieces:
        raise ValueError("Non-empty layer lost all cells at the selected precision")
    samples = shapely.point_on_surface(pieces)
    xy = shapely.get_coordinates(samples)
    points = np.c_[xy, np.full(len(xy), z)]
    face_indices = []
    for start in range(0, len(points), 4096):
        face_indices.append(_nearest_faces(surface, points[start:start + 4096]))
    selected = face_recipes[np.concatenate(face_indices)]
    materials = (selected if source_colors else
                 np.asarray([recipe[layer_index % len(recipe)] for recipe in selected], dtype=object))
    regions = {}
    pieces = np.asarray(pieces, dtype=object)
    for token in sorted(set(materials)):
        regions[token] = shapely.union_all(pieces[materials == token], grid_size=precision)
    # A categorical grid can have diagonal point contacts. A fixed, very small
    # interface priority resolves these into an overlap-free partition: earlier
    # materials gain up to interface_offset, later materials give up that strip.
    # Applying the same priority to every layer also resolves contacts where
    # adjacent slabs would otherwise meet only along an edge. The outside
    # silhouette is preserved, and no intentional clearance/air gap is added.
    if interface_offset and len(regions) > 1:
        remaining = domain
        regularized = {}
        ordered = _material_order(regions)
        for i, token in enumerate(ordered):
            if i == len(ordered) - 1:
                region = remaining
            else:
                expanded = shapely.buffer(regions[token], interface_offset, join_style="mitre")
                region = shapely.intersection(expanded, remaining, grid_size=precision)
            region = _polygonal(region, precision)
            if region.is_empty:
                raise ValueError(f"Layer {layer_index}: material {token} is thinner than interface tolerance")
            regularized[token] = region
            remaining = _polygonal(shapely.difference(remaining, region, grid_size=precision), precision)
        regions = regularized
    united = shapely.union_all(list(regions.values()), grid_size=precision)
    # Grid intersections round the outline by at most the declared precision.
    # Measure that movement without snapping it away, and separately reject any
    # gap away from the outside boundary (including hole boundaries).
    actual = shapely.set_precision(united, 0)
    original = shapely.set_precision(domain, 0)
    delta = shapely.symmetric_difference(actual, original)
    coverage = float(delta.area)
    interior_gap = float(shapely.difference(delta, original.boundary.buffer(precision * 2)).area)
    overlap = max(0.0, sum(float(region.area) for region in regions.values()) - float(united.area))
    tolerance = max(1e-8, float(domain.area) * 1e-8)
    if interior_gap > tolerance or overlap > tolerance:
        raise ValueError(f"Layer {layer_index} has gaps/overlap after paint partitioning")
    return regions, len(pieces), coverage, overlap, interior_gap, float(united.area)


def _solid_component_count(solid: mf.Manifold) -> int:
    """Count indexed connected shells without materializing individual solids."""
    faces = np.asarray(solid.to_mesh().tri_verts, dtype=np.int64)
    if not len(faces):
        return 0
    edges = np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]]))
    labels = trimesh.graph.connected_component_labels(edges)
    return int(labels.max()) + 1


def _interior_sample(mesh: trimesh.Trimesh) -> np.ndarray:
    """Find a strictly interior sample without rounding a microscopic shell.

    Work in normalized coordinates and verify the oriented solid angle. This
    avoids a Boolean constructor deleting tiny, nearly coplanar components.
    """
    center = np.asarray(mesh.bounds).mean(axis=0)
    scale = float(np.max(mesh.extents))
    triangles = (np.asarray(mesh.triangles) - center) / scale
    cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normals = cross / np.maximum(np.linalg.norm(cross, axis=1)[:, None], 1e-300)
    candidates = [triangles.mean(axis=(0, 1))]
    for epsilon in (1e-5, 1e-7, 1e-9, 1e-11, 1e-13):
        candidates.extend(triangles.mean(axis=1) - normals * epsilon)
    for point in candidates:
        vectors = triangles - point
        lengths = np.linalg.norm(vectors, axis=2)
        numerator = np.einsum("ij,ij->i", vectors[:, 0], np.cross(vectors[:, 1], vectors[:, 2]))
        denominator = (np.prod(lengths, axis=1) +
            np.einsum("ij,ij->i", vectors[:, 0], vectors[:, 1]) * lengths[:, 2] +
            np.einsum("ij,ij->i", vectors[:, 1], vectors[:, 2]) * lengths[:, 0] +
            np.einsum("ij,ij->i", vectors[:, 2], vectors[:, 0]) * lengths[:, 1])
        winding = float(np.arctan2(numerator, denominator).sum() / (2 * np.pi))
        if abs(winding - 1) < 1e-5:
            return point * scale + center
    raise ValueError("Could not find a verified interior sample for a sub-layer component")


def _sub_layer_parts(surface, face_recipes, details, midpoints, z0, zmax, layer_height, mode):
    """Retain missed positive components as physical mesh geometry.

    Each clipped piece is smaller than the declared layer spacing. Its material
    is sampled once, as for a clipped XY paint cell; the exact exterior remains.
    """
    retained = []
    for detail in details:
        mesh = detail["mesh"]
        lower, upper = np.asarray(mesh.bounds)[:, 2]
        if np.any((midpoints > lower) & (midpoints < upper)):
            continue
        first = max(0, int(math.floor((lower - z0) / layer_height)))
        last = min(len(midpoints) - 1, int(math.ceil((upper - z0) / layer_height)) - 1)
        for layer in range(first, last + 1):
            low, high = z0 + layer * layer_height, min(zmax, z0 + (layer + 1) * layer_height)
            part = mesh
            if lower < low or upper > high:
                # Clipping is only needed if a missed object crosses a layer
                # boundary. Normalize first so the engine's scale tolerance
                # cannot erase an otherwise printable small positive object.
                center = np.asarray(mesh.bounds).mean(axis=0)
                scale = float(np.max(mesh.extents))
                solid = mf.Manifold(mf.Mesh64(np.ascontiguousarray((mesh.vertices-center)/scale),
                                              np.ascontiguousarray(mesh.faces, dtype=np.uint64)))
                solid = solid.trim_by_plane((0, 0, 1), (low-center[2])/scale)
                solid = solid.trim_by_plane((0, 0, -1), -(high-center[2])/scale)
                data = solid.to_mesh64()
                if solid.status() != mf.Error.NoError or solid.num_tri() == 0:
                    raise ValueError("Could not retain a sub-layer component across a layer boundary")
                part = trimesh.Trimesh(np.asarray(data.vert_properties)[:, :3]*scale+center,
                                       np.asarray(data.tri_verts), process=False)
            sample = _interior_sample(part)
            face = int(_nearest_faces(surface, sample[None, :])[0])
            recipe = str(face_recipes[face])
            token = recipe if mode == "source" else recipe[layer % len(recipe)]
            retained.append({"mesh": part, "token": token, "audit": {
                "source_key": detail["key"], "source_component": detail["component"],
                "source_recipes": detail["recipes"], "sample_xyz_mm": sample.tolist(),
                "sampled_global_source_face": face, "sampled_recipe": recipe,
                "layer_index": layer, "filament": token,
                "original_triangles": len(part.faces), "original_bounds_mm": part.bounds.tolist(),
                "policy": "Exact sub-layer exterior; one verified interior nearest-surface paint sample at global layer phase"}})
    return retained


def _correct_filament_vertices(mesh: trimesh.Trimesh, maximum: float):
    """Correct only measured float32 collisions, preserving indexed topology."""
    from layerloom.paint_repair import _precision_regularize
    from layerloom.paint_sub_layer import _encoded_checks
    baseline = np.asarray(mesh.vertices).copy()
    faces = np.asarray(mesh.faces).copy()
    result = mesh.copy()
    repairs = []
    for attempt in range(6):
        center, encoded, placed, defects, placed_defects, volume, placed_volume = _encoded_checks(result)
        if not any(defects.values()) and not any(placed_defects.values()):
            break
        fixed, detail = _precision_regularize(encoded.vertices, faces,
            [np.zeros(3), center + (128, 128, 0)], maximum)
        result.vertices = fixed + center
        repairs.append(detail)
        if np.linalg.norm(result.vertices-baseline, axis=1).max(initial=0) > maximum:
            raise ValueError("Local filament correction exceeds its displacement limit")
    else:
        raise ValueError("Local filament correction did not survive decimal XML serialization")
    displacement = float(np.linalg.norm(result.vertices-baseline, axis=1).max(initial=0))
    placed_displacement = float(np.linalg.norm(placed-baseline-(128, 128, 0), axis=1).max(initial=0))
    if max(displacement, placed_displacement) > maximum:
        raise ValueError("Encoded filament correction exceeds its displacement limit")
    if not np.array_equal(result.faces, faces):
        raise AssertionError("Local filament correction changed face topology")
    return result, {"policy": "Correct only vertices implicated in float32 collisions or zero-area faces; no global expansion",
        "topology_and_face_count_preserved": True,
        "coordinate_encoding": "Nine-decimal XML vertices and transform, then local and placed float32",
        "maximum_vertex_displacement_mm": displacement,
        "maximum_placed_float32_displacement_mm": placed_displacement,
        "displacement_limit_mm": maximum,
        "source_volume_mm3": float(mesh.volume), "output_volume_mm3": float(result.volume),
        "encoded_defects": defects, "placed_defects": placed_defects, "repairs": repairs}


def _finalize_filament(solid: mf.Manifold, token: str, precision: float):
    """Validate one physical filament, retaining every disconnected region."""
    if solid.status() != mf.Error.NoError or solid.num_tri() == 0:
        raise ValueError(f"Could not union printable {token} regions")
    original_solid = solid
    original_components = _solid_component_count(original_solid)
    placed_extrema = np.asarray(solid.bounding_box(), dtype=float).reshape(2, 3) + (128, 128, 0)
    placed_ulp = float(np.max(np.abs(np.spacing(placed_extrema.astype(np.float32)))))
    cleanup_limit = max(precision / 2, placed_ulp * 4)
    cleanup_candidates = sorted({0.0, precision / 10, precision / 2,
                                 placed_ulp, placed_ulp * 2, cleanup_limit})
    numerical_tolerance = 0.0
    for numerical_tolerance in cleanup_candidates:
        if numerical_tolerance:
            # Boolean triangulation can leave a nearly collinear triangle
            # that collapses in a slicer's float32 coordinates. Manifold's
            # bounded simplification retains a subset of original vertices;
            # it moves surfaces by less than this declared numerical bound.
            # A fine XY grid can be smaller than the float32 spacing at the
            # slicer plate position, so include a measured 1–4 ULP bound.
            # Never accept simplification that removes a positive component.
            # All geometry and inter-material checks are repeated below.
            solid = original_solid.simplify(numerical_tolerance)
            if _solid_component_count(solid) != original_components:
                continue
        data = solid.to_mesh()
        mesh = trimesh.Trimesh(np.asarray(data.vert_properties)[:, :3],
                              np.asarray(data.tri_verts), process=False)
        from layerloom.paint_sub_layer import _encoded_checks
        _, _, _, defects, placed_defects, _, _ = _encoded_checks(mesh)
        if not any(defects.values()) and not any(placed_defects.values()):
            break
        if not (defects["zero_area_triangles"] or placed_defects["zero_area_triangles"]):
            break
    vertex_correction = None
    if any(defects.values()) or any(placed_defects.values()):
        # A point-contact collision can require moving one implicated vertex;
        # simplifying further may delete a disconnected material region. Use
        # bounded local correction of the original indexed geometry instead.
        original_data = original_solid.to_mesh64()
        original_mesh = trimesh.Trimesh(np.asarray(original_data.vert_properties)[:, :3],
                                       np.asarray(original_data.tri_verts), process=False)
        try:
            mesh, vertex_correction = _correct_filament_vertices(original_mesh,
                                                max(cleanup_limit, placed_ulp * 16))
        except ValueError as error:
            raise ValueError(f"Filament {token} could not pass bounded float32 correction: {error}") from error
        if int(mesh.body_count) != original_components:
            raise ValueError(f"Filament {token}: local correction changed the number of components")
        solid = mf.Manifold(mf.Mesh64(np.ascontiguousarray(mesh.vertices),
                                      np.ascontiguousarray(mesh.faces, dtype=np.uint64)))
        numerical_tolerance = 0.0
        defects = vertex_correction["encoded_defects"]
        placed_defects = vertex_correction["placed_defects"]
    audit = {"triangles": int(len(mesh.faces)), "volume_mm3": float(mesh.volume),
             "components": int(mesh.body_count), "defects": defects,
             "numerical_simplification_tolerance_mm": numerical_tolerance,
             "numerical_simplification_limit_mm": cleanup_limit,
             "placed_float32_ulp_mm": placed_ulp,
             "components_before_numerical_cleanup": original_components,
             "components_after_numerical_cleanup": int(mesh.body_count),
             "placed_float32_defects": placed_defects,
             "local_vertex_correction": vertex_correction}
    return mesh, solid, audit


def export_painted_3mf(document: Any, path: str | Path, *, layer_height: float = 0.2,
                       pitch: float = 0.5, palette_name: str = "Normal", mode: str = "woven",
                       recipe_overrides: Mapping[str, str] | None = None,
                       progress: Callable[[int, int], None] | None = None,
                       max_cells: int = 2_000_000, precision: float = 0.001,
                       interface_offset: float = 0.001) -> dict[str, Any]:
    """Export a painted solid as one aligned multipart 3MF; return its audit.

    ``pitch`` controls XY material detail, ``layer_height`` controls the stepped
    exterior. Surface paint extends through the entire volume by nearest-face
    distance, with stable lowest-face-ID tie breaking at equal distances.
    Parts share one centering/grounding translation. No source mesh is mutated.
    ``progress`` receives completed and total layer counts. A JSON audit is
    written alongside the 3MF. This function does not slice or physically print.
    """
    if not callable(getattr(mf.Manifold, "simplify", None)):
        raise RuntimeError("Paint export requires manifold3d 3.2 or newer. "
                           "Update LayerLoom's dependencies before exporting.")
    for name, value in (("layer_height", layer_height), ("pitch", pitch), ("precision", precision)):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    if precision >= min(pitch, layer_height) / 10:
        raise ValueError("precision must be smaller than one tenth of pitch and layer height")
    if not math.isfinite(interface_offset) or not 0 <= interface_offset < min(pitch, layer_height) / 10:
        raise ValueError("interface_offset must be nonnegative and below one tenth of pitch and layer height")
    if int(max_cells) <= 0:
        raise ValueError("max_cells must be positive")
    output = Path(path).expanduser().resolve()
    assignments = assign_palette(document, palette_name, mode=mode, recipe_overrides=recipe_overrides)
    component_details = []
    surface, source, face_recipes, source_rows = _source_geometry(
        document, assignments, component_details=component_details)
    bounds = np.asarray(surface.bounds)
    z0, zmax = bounds[:, 2]
    layers = int(math.ceil((zmax - z0) / layer_height))
    # Bound work before allocating one item per layer. Tiny positive layer
    # heights must fail predictably instead of exhausting memory in arange.
    estimated = layers * math.prod(int(math.ceil(extent / pitch)) + 2
                                  for extent in bounds[1, :2] - bounds[0, :2])
    if estimated > max_cells:
        raise ValueError(f"Export would inspect approximately {estimated:,} grid cells; limit is {max_cells:,}. "
                         "Increase pitch/layer height, reduce model size, or explicitly raise max_cells.")
    midpoints = (z0 + np.arange(layers) * layer_height +
                 np.minimum(zmax, z0 + (np.arange(layers) + 1) * layer_height)) / 2
    retained = _sub_layer_parts(surface, face_recipes, component_details, midpoints,
                                z0, zmax, layer_height, mode)
    translation = np.array((-(bounds[0, 0] + bounds[1, 0]) / 2,
                            -(bounds[0, 1] + bounds[1, 1]) / 2, -z0))
    by_filament = defaultdict(list)
    sampled_volume = midpoint_source_volume = 0.0
    max_coverage = max_overlap = 0.0
    max_interior_gap = 0.0
    sampled_cells = nonempty_layers = 0
    for layer in range(layers):
        low, high = z0 + layer * layer_height, min(zmax, z0 + (layer + 1) * layer_height)
        z = (low + high) / 2
        domain = _layer_domain(source.slice(z), precision=precision, layer_index=layer)
        if not domain.is_empty:
            nonempty_layers += 1
            regions, cells, coverage, overlap, interior_gap, partition_area = _partition_layer(
                surface, face_recipes, domain, z=z, layer_index=layer, pitch=pitch,
                origin=bounds[0, :2], precision=precision, interface_offset=interface_offset,
                source_colors=mode == "source")
            sampled_cells += cells
            if sampled_cells > max_cells:
                raise ValueError("Export exceeded max_cells after splitting disconnected grid pieces")
            midpoint_source_volume += float(domain.area) * (high - low)
            sampled_volume += partition_area * (high - low)
            max_coverage, max_overlap = max(max_coverage, coverage), max(max_overlap, overlap)
            max_interior_gap = max(max_interior_gap, interior_gap)
            for token, region in regions.items():
                part = _region_solid(region, low, high)
                if part.status() != mf.Error.NoError or part.num_tri() == 0:
                    raise ValueError(f"Layer {layer}: could not extrude filament {token}")
                by_filament[token].append(part)
        if progress is not None:
            progress(layer + 1, layers)
    if not by_filament and not retained:
        raise ValueError("No printable layers were generated")

    items, material_rows, final_solids = [], [], []
    used_tokens = _material_order(set(by_filament) | {part["token"] for part in retained})
    extruders = {token: i + 1 for i, token in enumerate(used_tokens)}
    for token in used_tokens:
        if not by_filament[token]:
            material_rows.append({"token": token, "extruder": extruders[token], "hex": _material_hex(token),
                "triangles": 0, "volume_mm3": 0.0, "components": 0, "defects": {},
                "numerical_simplification_tolerance_mm": 0.0, "placed_float32_defects": {}})
            continue
        solid = mf.Manifold.batch_boolean(by_filament[token], mf.OpType.Add).translate(translation)
        mesh, solid, material_audit = _finalize_filament(solid, token, precision)
        metadata = {"source_hex": _material_hex(token), "stack_token": token,
                    "filament": token, "paint_policy": "nearest_surface_full_depth"}
        items.append(LeafMeshSpec(_material_label(token), mesh, metadata))
        final_solids.append(solid)
        material_rows.append({"token": token, "extruder": extruders[token],
                              "hex": _material_hex(token), **material_audit})
    layer_sampled_volume = sampled_volume
    retained_volume = 0.0
    retained_rows = []
    if retained:
        from layerloom.paint_sub_layer import prepare_sub_layer_mesh
        rows_by_token = {row["token"]: row for row in material_rows}
        for index, extra in enumerate(retained):
            mesh, audit = prepare_sub_layer_mesh(extra["mesh"], translation)
            token = extra["token"]
            # Separate parts preserve microscopic positive shells which a
            # Boolean union at the whole-model scale can silently remove.
            items.append(LeafMeshSpec(f"{_material_label(token)}_sub_layer_{index + 1}", mesh,
                {"source_hex": _material_hex(token), "stack_token": token,
                 "filament": token, "paint_policy": "sub_layer_interior_sample"}))
            row = rows_by_token[token]
            volume = float(audit["encoded_centered_signed_volume_mm3"])
            row["triangles"] += len(mesh.faces)
            row["volume_mm3"] += volume
            row["components"] += 1
            row["retained_sub_layer_parts"] = row.get("retained_sub_layer_parts", 0) + 1
            retained_volume += volume
            retained_rows.append({**extra["audit"], **audit, "extruder": extruders[token]})
            # This is only the overlap calculation; emitted geometry above is
            # never taken back from a Boolean that might erase a tiny shell.
            final_solids.append(mf.Manifold(mf.Mesh64(np.ascontiguousarray(mesh.vertices),
                                    np.ascontiguousarray(mesh.faces, dtype=np.uint64))))
    sampled_volume += retained_volume
    final_union = mf.Manifold.batch_boolean(final_solids, mf.OpType.Add)
    output_volume = sum(row["volume_mm3"] for row in material_rows)
    union_volume = float(final_union.volume())
    tolerance = max(1e-5, sampled_volume * 1e-5)
    if abs(output_volume - sampled_volume) > tolerance or abs(output_volume - union_volume) > tolerance:
        raise ValueError("Final printable solids changed layer coverage or overlap; export was cancelled "
                         f"(sampled={sampled_volume:.9g}, parts={output_volume:.9g}, "
                         f"union={union_volume:.9g} mm³, tolerance={tolerance:.9g})")
    source_volume = float(source.volume())
    output_bounds = np.stack([item.mesh.bounds for item in items])
    output_bounds = np.stack((output_bounds[:, 0].min(axis=0), output_bounds[:, 1].max(axis=0)))
    report = {
        "format": "layerloom-paint-export-v1", "output_3mf": str(output),
        "mode": mode, "palette_name": palette_name, "paint_policy": "nearest_surface_full_depth",
        "layer_height_mm": float(layer_height), "paint_pitch_mm": float(pitch),
        "geometry_precision_mm": float(precision), "layers": layers,
        "material_interface_offset_mm": float(interface_offset),
        "material_interface_priority": _material_order(str(entry["token"]) for entry in assignments.values()) if mode == "source" else list(ALL_TOKENS),
        "nonempty_layers": nonempty_layers, "sampled_cells": sampled_cells,
        "source_meshes": source_rows, "source_bounds_mm": bounds.tolist(),
        "output_bounds_mm": output_bounds.tolist(),
        "shared_translation_mm": translation.tolist(), "slicer_plate_translation_mm": [128, 128, 0],
        "source_volume_mm3": source_volume, "sampled_volume_mm3": sampled_volume,
        "midpoint_source_volume_mm3": midpoint_source_volume,
        "layer_sampled_volume_mm3": layer_sampled_volume,
        "retained_sub_layer_volume_mm3": retained_volume,
        "retained_sub_layer_parts": retained_rows,
        "boundary_snap_volume_change_mm3": layer_sampled_volume - midpoint_source_volume,
        "output_volume_mm3": output_volume,
        "relative_volume_change": (output_volume - source_volume) / source_volume,
        "maximum_layer_coverage_error_mm2": max_coverage, "maximum_layer_overlap_mm2": max_overlap,
        "maximum_internal_layer_gap_mm2": max_interior_gap,
        "output_overlap_mm3": max(0.0, output_volume - union_volume),
        "assignments": assignments, "filaments": material_rows,
        "validation": "closed solids, float32 triangles, per-layer coverage, final material overlap",
        "limitations": [
            "Exterior sampled at layer midpoints; this is layer-stepped geometry.",
            "Full-depth nearest-surface paint; no shell/core thickness control.",
            "Paint boundaries are limited by source triangles and XY pitch.",
            "Material interfaces use the reported tiny priority offset to avoid point/edge contacts.",
            "Positive objects missed by midpoint sampling are retained separately, with one interior paint sample per layer piece.",
            "Thin features within a larger connected object still follow midpoint sampling.",
            "Use this layer height for first and subsequent slicer layers; disable adaptive layers.",
            "Geometry validation only; slicer preview and physical color proof remain necessary.",
        ],
    }
    report_path = output.with_suffix(".paint-report.json")
    report["report_path"] = str(report_path)
    report["report_written"] = True
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="layerloom_paint_", dir=output.parent) as temporary:
        staged = Path(temporary) / output.name
        staged_report = Path(temporary) / report_path.name
        write_orca_multipart_3mf(items, str(staged), title=output.stem,
                                 extruder_by_token={token.lower(): slot for token, slot in extruders.items()})
        _brand_geometry_archive(staged, used_tokens)
        # Serialize both files before publishing either. The report is optional
        # after geometry publication: a Windows file lock or permissions race
        # must not make a completed printable export look like a failed export.
        staged_report.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        os.replace(staged, output)
        try:
            os.replace(staged_report, report_path)
        except OSError as exc:
            report["report_written"] = False
            report["warnings"] = [f"The 3MF was exported, but its audit report could not be saved: {exc}. "
                                  "Any existing report is unchanged."]
    return report
