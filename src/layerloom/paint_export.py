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


def export_native_painted_3mf(document: Any, path: str | Path, *,
                              center_on_plate: bool = True,
                              progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """Export exact surface facets and RGB filament paint using slicer metadata.

    This explicit route preserves the source exterior and facet paint. The
    slicer determines color-region depth; no layered material geometry is made.
    Closed input geometry is required, and no repair is performed implicitly.
    """
    from layerloom.paint_native import export_native_painted_3mf as native_export
    return native_export(document, path, center_on_plate=center_on_plate, progress=progress)


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


def _source_geometry(document: Any, assignments: Mapping[str, Mapping[str, Any]]):
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
        source_rows.append({"key": str(item.key), "name": label,
                            "triangles": int(len(mesh.faces)),
                            "components": int(mesh.body_count),
                            "component_z_bounds_mm": [part.bounds[:, 2].tolist()
                                                      for part in mesh.split(only_watertight=False)],
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
    surface, source, face_recipes, source_rows = _source_geometry(document, assignments)
    bounds = np.asarray(surface.bounds)
    z0, zmax = bounds[:, 2]
    layers = int(math.ceil((zmax - z0) / layer_height))
    midpoints = (z0 + np.arange(layers) * layer_height +
                 np.minimum(zmax, z0 + (np.arange(layers) + 1) * layer_height)) / 2
    for row in source_rows:
        for low, high in row["component_z_bounds_mm"]:
            if not np.any((midpoints > low) & (midpoints < high)):
                raise ValueError(f"{row['name']}: a disconnected component falls between layer midpoints. "
                                 "Reduce layer_height to retain every component.")
    estimated = layers * int(np.prod(np.ceil((bounds[1, :2] - bounds[0, :2]) / pitch) + 2))
    if estimated > max_cells:
        raise ValueError(f"Export would inspect approximately {estimated:,} grid cells; limit is {max_cells:,}. "
                         "Increase pitch/layer height, reduce model size, or explicitly raise max_cells.")
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
    if not by_filament:
        raise ValueError("No printable layers were generated")

    items, material_rows, final_solids = [], [], []
    used_tokens = _material_order(by_filament)
    extruders = {token: i + 1 for i, token in enumerate(used_tokens)}
    for token in used_tokens:
        solid = mf.Manifold.batch_boolean(by_filament[token], mf.OpType.Add).translate(translation)
        if solid.status() != mf.Error.NoError or solid.num_tri() == 0:
            raise ValueError(f"Could not union printable {token} regions")
        original_solid = solid
        numerical_tolerance = 0.0
        for numerical_tolerance in (0.0, precision / 10, precision / 2):
            if numerical_tolerance:
                # Boolean triangulation can leave a nearly collinear triangle
                # that collapses in a slicer's float32 coordinates. Manifold's
                # bounded simplification retains a subset of original vertices;
                # it moves surfaces by less than this declared sub-grid bound.
                # It is only attempted for measured collapsed triangles, and
                # all geometry and inter-material checks are repeated below.
                solid = original_solid.simplify(numerical_tolerance)
            data = solid.to_mesh()
            mesh = trimesh.Trimesh(np.asarray(data.vert_properties)[:, :3],
                                  np.asarray(data.tri_verts), process=False)
            center = np.asarray(mesh.bounds).mean(axis=0)
            encoded = mesh.copy()
            encoded.vertices = (np.asarray(mesh.vertices) - center).astype(np.float32).astype(np.float64)
            defects = mesh_defects(encoded)
            placed_defects = mesh_defects(encoded, center + (128, 128, 0))
            if not any(defects.values()) and not any(placed_defects.values()):
                break
            if not (defects["zero_area_triangles"] or placed_defects["zero_area_triangles"]):
                break
        if any(defects.values()) or any(placed_defects.values()):
            raise ValueError(f"Filament {token} fails exported geometry checks: {defects}; "
                             f"placed float32: {placed_defects}. Try a coarser paint pitch/layer height.")
        metadata = {"source_hex": _material_hex(token), "stack_token": token,
                    "filament": token, "paint_policy": "nearest_surface_full_depth"}
        items.append(LeafMeshSpec(_material_label(token), mesh, metadata))
        final_solids.append(solid)
        material_rows.append({"token": token, "extruder": extruders[token], "hex": _material_hex(token),
                              "triangles": int(len(mesh.faces)), "volume_mm3": float(mesh.volume),
                              "components": int(mesh.body_count), "defects": defects,
                              "numerical_simplification_tolerance_mm": numerical_tolerance,
                              "placed_float32_defects": placed_defects})
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
        "boundary_snap_volume_change_mm3": sampled_volume - midpoint_source_volume,
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
            "Features thinner than a layer can be missed by midpoint sampling.",
            "Use this layer height for first and subsequent slicer layers; disable adaptive layers.",
            "Geometry validation only; slicer preview and physical color proof remain necessary.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="layerloom_paint_", dir=output.parent) as temporary:
        staged = Path(temporary) / output.name
        write_orca_multipart_3mf(items, str(staged), title=output.stem,
                                 extruder_by_token={token.lower(): slot for token, slot in extruders.items()})
        _brand_geometry_archive(staged, used_tokens)
        os.replace(staged, output)
    report_path = output.with_suffix(".paint-report.json")
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
