"""Weave a cylindrical image pattern through an exact solid mesh cross-section."""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import numpy as np
import trimesh
from PIL import Image
from scipy import ndimage
from scipy.optimize import linear_sum_assignment
from shapely import set_precision
from shapely import affinity
from shapely.geometry import GeometryCollection, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from layerloom.export import (
    LeafMeshSpec,
    write_metadata_assembly_3mf,
    write_orca_multipart_3mf,
)
from layerloom.palette_assignment import load_palette_entries
from layerloom.pattern_mapper import (
    ANGEL_FISH_CATALOG_URL,
    ANGEL_FISH_URL,
    _hex_to_rgb,
    _rgb_to_hex,
    prepare_tile,
    quantize_oklab,
    resolve_pattern,
    srgb_to_oklab,
)
from layerloom.polygon_primitives import (
    clean_polygonal,
    extrude_polygonal,
    iter_polygons,
)
from layerloom.tokens import COLOR_OBJECT_LABELS, TOKEN_HEX


RAINBOW_TOKENS = (
    "mmymy",   # red
    "myyy",    # orange
    "y",       # yellow
    "cyy",     # yellow-green
    "ccycy",   # green
    "cccycc",  # teal
    "c",       # cyan
    "ccm",     # blue-violet
    "m",       # magenta
)

NEUTRAL_TOKENS = (
    "cmy",     # warm neutral
    "cmcy",    # cool neutral
    "cmcmy",   # muted violet neutral
)

CURATED_RAINBOW_TOKENS = RAINBOW_TOKENS + NEUTRAL_TOKENS


def compact_touching_meshes(meshes: list[trimesh.Trimesh]) -> trimesh.Trimesh:
    """Union layer slabs so slicers do not see thousands of touching bodies."""
    if not meshes:
        raise ValueError("No meshes were supplied for compaction")
    if len(meshes) == 1:
        compacted = meshes[0].copy()
    else:
        compacted = trimesh.boolean.union(
            meshes,
            engine="manifold",
            check_volume=False,
        )
    if not isinstance(compacted, trimesh.Trimesh) or compacted.faces.size == 0:
        raise RuntimeError("Layer-slab compaction produced no mesh")
    compacted.remove_unreferenced_vertices()
    return compacted


def _curated_entries(palette_name: str, count: int) -> list[dict[str, Any]]:
    entries = load_palette_entries(palette_name)
    by_token = {entry["token"]: entry for entry in entries}
    selected = [dict(by_token[token]) for token in CURATED_RAINBOW_TOKENS if token in by_token]
    if count > len(selected):
        raise ValueError(f"Curated palette has only {len(selected)} available entries")
    return selected


def assign_clusters_to_curated_recipes(
    cluster_rgb: np.ndarray,
    palette_name: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    entries = _curated_entries(palette_name, len(cluster_rgb))
    if len(cluster_rgb) == 9:
        by_token = {entry["token"]: entry for entry in entries}
        rainbow = [
            dict(by_token[token])
            for token in ("mmymy", "y", "ccycy", "c", "ccm", "m")
        ]
        neutrals = [dict(by_token[token]) for token in NEUTRAL_TOKENS]
        source_lab = srgb_to_oklab(cluster_rgb)
        source_chroma = np.linalg.norm(source_lab[:, 1:3], axis=1)
        neutral_indices = np.argsort(source_chroma)[:3]
        chromatic_indices = np.asarray(
            [index for index in range(9) if index not in set(neutral_indices)],
            dtype=np.int64,
        )
        assigned: list[Optional[dict[str, Any]]] = [None] * 9
        for source_indices, candidates in (
            (neutral_indices, neutrals),
            (chromatic_indices, rainbow),
        ):
            target_rgb = np.asarray([_hex_to_rgb(entry["hex"]) for entry in candidates])
            cost = np.sum(
                (
                    source_lab[source_indices, None, :]
                    - srgb_to_oklab(target_rgb)[None, :, :]
                )
                ** 2,
                axis=2,
            )
            rows, columns = linear_sum_assignment(cost)
            for row, column in zip(rows, columns):
                assigned[int(source_indices[int(row)])] = dict(candidates[int(column)])
        resolved = [entry for entry in assigned if entry is not None]
        return [str(entry["token"]) for entry in resolved], resolved

    target_rgb = np.asarray([_hex_to_rgb(entry["hex"]) for entry in entries])
    cost = np.sum(
        (
            srgb_to_oklab(cluster_rgb)[:, None, :]
            - srgb_to_oklab(target_rgb)[None, :, :]
        )
        ** 2,
        axis=2,
    )
    rows, columns = linear_sum_assignment(cost)
    assigned: list[Optional[dict[str, Any]]] = [None] * len(cluster_rgb)
    for row, column in zip(rows, columns):
        assigned[int(row)] = dict(entries[int(column)])
    resolved = [entry for entry in assigned if entry is not None]
    return [str(entry["token"]) for entry in resolved], resolved


def _world_profile(mesh: trimesh.Trimesh, z: float):
    section = mesh.section(plane_origin=(0.0, 0.0, float(z)), plane_normal=(0.0, 0.0, 1.0))
    if section is None:
        return GeometryCollection()
    path_2d, transform = section.to_2D()
    coefficients = [
        transform[0, 0],
        transform[0, 1],
        transform[1, 0],
        transform[1, 1],
        transform[0, 3],
        transform[1, 3],
    ]
    polygons = [
        affinity.affine_transform(poly, coefficients)
        for poly in path_2d.polygons_full
        if not poly.is_empty
    ]
    return clean_polygonal(unary_union(polygons), min_area=1e-4)


def _load_primary_volume(path: Path) -> trimesh.Trimesh:
    mesh = trimesh.load(path, force="mesh", process=True)
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"{path} did not resolve to a triangle mesh")
    if mesh.is_volume:
        return mesh
    components = mesh.split(only_watertight=False)
    volumes = [component for component in components if component.is_volume]
    if not volumes:
        raise ValueError(f"{path} does not contain a closed solid component")
    return max(volumes, key=lambda component: len(component.faces))


def _circular_runs(values: np.ndarray) -> list[tuple[str, int, int]]:
    values = np.asarray(values)
    count = len(values)
    if count == 0:
        return []
    boundaries = np.flatnonzero(values != np.roll(values, 1))
    if len(boundaries) == 0:
        return [(str(values[0]), 0, count)]
    start = int(boundaries[0])
    ordered = np.concatenate([np.arange(start, count), np.arange(0, start)])
    runs: list[tuple[str, int, int]] = []
    run_start = 0
    for index in range(1, count + 1):
        if index == count or values[ordered[index]] != values[ordered[index - 1]]:
            absolute_start = int(ordered[run_start])
            runs.append((str(values[ordered[run_start]]), absolute_start, index - run_start))
            run_start = index
    return runs


def smooth_circular_runs(values: np.ndarray, minimum_bins: int) -> np.ndarray:
    out = np.asarray(values).copy()
    for _pass in range(8):
        runs = _circular_runs(out)
        short = [run for run in runs if run[2] < minimum_bins]
        if not short or len(runs) == 1:
            break
        changed = False
        for token, start, length in short:
            before = str(out[(start - 1) % len(out)])
            after = str(out[(start + length) % len(out)])
            replacement = before if before == after else after
            for offset in range(length):
                out[(start + offset) % len(out)] = replacement
            changed = True
        if not changed:
            break
    return out


def smooth_label_map_vertical_runs(
    labels: np.ndarray,
    *,
    minimum_rows: int,
) -> np.ndarray:
    """Remove vertically short label runs from a seamless pattern tile."""
    if minimum_rows <= 1:
        return np.asarray(labels).copy()
    cleaned = np.asarray(labels).copy()
    for column in range(cleaned.shape[1]):
        values = smooth_circular_runs(cleaned[:, column].astype(str), minimum_rows)
        cleaned[:, column] = values.astype(cleaned.dtype)
    return cleaned


def remove_small_label_components(
    labels: np.ndarray,
    *,
    minimum_pixels: int,
) -> np.ndarray:
    """Reassign isolated label islands while preserving long narrow ribbons."""
    if minimum_pixels <= 1:
        return np.asarray(labels).copy()
    labels = np.asarray(labels)
    invalid = np.zeros(labels.shape, dtype=bool)
    structure = np.ones((3, 3), dtype=np.uint8)
    for value in np.unique(labels):
        components, count = ndimage.label(labels == value, structure=structure)
        if count == 0:
            continue
        sizes = np.bincount(components.ravel())
        small_ids = np.flatnonzero((sizes < minimum_pixels) & (np.arange(len(sizes)) > 0))
        if len(small_ids):
            invalid |= np.isin(components, small_ids)
    if not np.any(invalid) or np.all(invalid):
        return labels.copy()
    _distance, nearest = ndimage.distance_transform_edt(
        invalid,
        return_distances=True,
        return_indices=True,
    )
    cleaned = labels.copy()
    cleaned[invalid] = labels[tuple(index[invalid] for index in nearest)]
    return cleaned


def _sector_polygon(
    start_bin: int,
    length: int,
    *,
    bins: int,
    seam_angle_deg: float,
    radius: float,
) -> Polygon:
    start = math.radians(seam_angle_deg) + 2 * math.pi * start_bin / bins
    end = start + 2 * math.pi * length / bins
    steps = max(2, int(math.ceil(length / 2)))
    angles = np.linspace(start, end, steps + 1)
    arc = [(radius * math.cos(angle), radius * math.sin(angle)) for angle in angles]
    return Polygon([(0.0, 0.0), *arc, (0.0, 0.0)])


def _sample_cluster_row(
    labels: np.ndarray,
    z: float,
    *,
    period_mm: float,
    angular_bins: int,
    seam_angle_deg: float,
) -> np.ndarray:
    height, width = labels.shape
    row = int(math.floor((1.0 - (z / period_mm) % 1.0) * height)) % height
    angles = np.arange(angular_bins, dtype=np.float64) / angular_bins
    columns = np.floor(angles * width).astype(np.int64) % width
    return labels[row, columns]


def _char_for_point(
    point,
    chars: np.ndarray,
    *,
    angular_bins: int,
    seam_angle_deg: float,
) -> str:
    angle = (math.atan2(point.y, point.x) - math.radians(seam_angle_deg)) % (2 * math.pi)
    index = int(math.floor(angle / (2 * math.pi) * angular_bins)) % angular_bins
    return str(chars[index])


def partition_profile(
    profile,
    chars: np.ndarray,
    *,
    angular_bins: int,
    seam_angle_deg: float,
    radius: float,
    preserve_component_area_mm2: float,
    minimum_piece_area_mm2: float,
) -> dict[str, Any]:
    runs = _circular_runs(chars)
    sectors = [
        (
            token,
            _sector_polygon(
                start,
                length,
                bins=angular_bins,
                seam_angle_deg=seam_angle_deg,
                radius=radius,
            ),
        )
        for token, start, length in runs
    ]
    grouped: dict[str, list[Any]] = defaultdict(list)

    for component in iter_polygons(profile):
        default_token = _char_for_point(
            component.representative_point(),
            chars,
            angular_bins=angular_bins,
            seam_angle_deg=seam_angle_deg,
        )
        if component.area <= preserve_component_area_mm2:
            grouped[default_token].append(component)
            continue

        pieces: list[tuple[str, Any]] = []
        for token, sector in sectors:
            clipped = clean_polygonal(component.intersection(sector), min_area=1e-5)
            for polygon in iter_polygons(clipped):
                pieces.append((token, polygon))
        large = [(token, polygon) for token, polygon in pieces if polygon.area >= minimum_piece_area_mm2]
        for token, polygon in pieces:
            if polygon.area >= minimum_piece_area_mm2:
                grouped[token].append(polygon)
                continue
            replacement = default_token
            best_contact = 0.0
            for candidate_token, candidate in large:
                contact = float(polygon.boundary.intersection(candidate.boundary).length)
                if contact > best_contact:
                    best_contact = contact
                    replacement = candidate_token
            grouped[replacement].append(polygon)

    result = {}
    for token, polygons in grouped.items():
        if not polygons:
            continue
        merged = unary_union(polygons)
        # Sector intersections can leave sub-micron duplicate vertices which
        # earcut cannot triangulate. This grid is far below printable detail.
        merged = set_precision(merged, grid_size=0.001, mode="valid_output")
        result[token] = clean_polygonal(merged, min_area=1e-5)
    return result


def build_image_weave(
    input_stl: Path,
    pattern_path: Path,
    output_3mf: Path,
    *,
    proof_3mf: Optional[Path],
    pattern_reference: str,
    scale: float,
    layer_height: float,
    pattern_period_mm: float,
    angular_bins: int,
    minimum_run_mm: float,
    minimum_vertical_run_mm: float,
    minimum_label_component_mm2: float,
    palette_name: str,
    recipe_count: int,
    detail_mm: float,
    crop_fraction: float,
    seam_angle_deg: float,
    base_height_mm: float,
    base_radius_fraction: float,
    base_filament: Optional[str],
    eye_filaments: Optional[tuple[str, str]],
    eye_mesh_paths: Optional[tuple[Path, Path]],
    preserve_component_area_mm2: float,
    minimum_piece_area_mm2: float,
    seed: int,
    verbose: bool,
) -> dict[str, Any]:
    mesh = _load_primary_volume(input_stl)
    direct_eye_meshes: list[tuple[str, str, trimesh.Trimesh]] = []
    if eye_mesh_paths is not None:
        if eye_filaments is None:
            raise ValueError("eye_mesh_paths requires eye_filaments")
        loaded_eyes = [_load_primary_volume(path) for path in eye_mesh_paths]
        trimmed = trimesh.boolean.difference([mesh, *loaded_eyes], engine="manifold")
        if not isinstance(trimmed, trimesh.Trimesh) or not trimmed.is_volume:
            raise ValueError("Could not subtract supplied eye inserts from the body")
        mesh = trimmed
        direct_eye_meshes = [
            ("eye_left", eye_filaments[0], loaded_eyes[0]),
            ("eye_right", eye_filaments[1], loaded_eyes[1]),
        ]

    mesh.apply_scale(scale)
    center_xy = (mesh.bounds[0, :2] + mesh.bounds[1, :2]) / 2.0
    translation = np.asarray((-center_xy[0], -center_xy[1], -mesh.bounds[0, 2]))
    mesh.apply_translation(translation)
    for _name, _filament, eye_mesh in direct_eye_meshes:
        eye_mesh.apply_scale(scale)
        eye_mesh.apply_translation(translation)

    tile = prepare_tile(
        Image.open(pattern_path),
        crop_fraction=crop_fraction,
        period_mm=pattern_period_mm,
        detail_mm=detail_mm,
    )
    labels, source_rgb, counts = quantize_oklab(tile, recipe_count, seed)
    vertical_mm_per_row = pattern_period_mm / labels.shape[0]
    minimum_vertical_rows = max(
        1,
        int(math.ceil(minimum_vertical_run_mm / vertical_mm_per_row)),
    )
    labels = smooth_label_map_vertical_runs(
        labels,
        minimum_rows=minimum_vertical_rows,
    )
    max_radius = float(np.max(np.linalg.norm(np.asarray(mesh.vertices)[:, :2], axis=1)))
    horizontal_mm_per_pixel = 2.0 * math.pi * max_radius / labels.shape[1]
    label_pixel_area_mm2 = vertical_mm_per_row * horizontal_mm_per_pixel
    minimum_label_component_pixels = max(
        1,
        int(math.ceil(minimum_label_component_mm2 / label_pixel_area_mm2)),
    )
    labels = remove_small_label_components(
        labels,
        minimum_pixels=minimum_label_component_pixels,
    )
    recipes, recipe_entries = assign_clusters_to_curated_recipes(source_rgb, palette_name)

    minimum_bins = max(1, int(math.ceil(minimum_run_mm / (2 * math.pi * max_radius) * angular_bins)))
    sector_radius = max_radius * 1.5 + 2.0
    base_radius_x = float(mesh.extents[0] * base_radius_fraction)
    base_radius_y = float(mesh.extents[1] * base_radius_fraction)
    base = affinity.scale(Point(0.0, 0.0).buffer(1.0, quad_segs=96), base_radius_x, base_radius_y)

    max_z = max(float(mesh.bounds[1, 2]), base_height_mm)
    layer_count = int(math.ceil(max_z / layer_height))
    meshes_by_filament: dict[str, list[trimesh.Trimesh]] = defaultdict(list)
    meshes_by_recipe: dict[int, list[trimesh.Trimesh]] = defaultdict(list)
    proof_direct_meshes: dict[tuple[str, str], list[trimesh.Trimesh]] = defaultdict(list)
    area_by_filament = defaultdict(float)
    piece_areas: list[float] = []
    empty_profile_layers: list[int] = []
    layer_profile_areas: list[float] = []
    coverage_errors: list[float] = []
    empty_target_layers: list[int] = []
    filament_layer_counts = defaultdict(int)
    started = time.time()

    for layer_index in range(layer_count):
        z_bottom = layer_index * layer_height
        z_top = min((layer_index + 1) * layer_height, max_z)
        z_center = (z_bottom + z_top) / 2.0
        profile = _world_profile(mesh, min(z_center, float(mesh.bounds[1, 2]) - 1e-7))
        source_profile_area = float(profile.area)
        target_profile = profile
        direct_regions: list[tuple[str, str, Any]] = []
        if z_bottom < base_height_mm:
            target_profile = clean_polygonal(unary_union([target_profile, base]), min_area=1e-4)
            if base_filament is None:
                profile = target_profile
            else:
                direct_regions.append(("base", base_filament, base))
                profile = clean_polygonal(profile.difference(base), min_area=1e-4)

        if eye_filaments is not None and eye_mesh_paths is None:
            x_extent, y_extent, z_extent = (float(value) for value in mesh.extents)
            eye_center_y = float(mesh.bounds[0, 1] + 0.18 * y_extent)
            eye_center_z = float(mesh.bounds[0, 2] + 0.83 * z_extent)
            eye_radius_x = 0.17 * x_extent
            eye_radius_y = 0.22 * y_extent
            eye_radius_z = 0.20 * z_extent
            z_phase = (z_center - eye_center_z) / eye_radius_z
            if abs(z_phase) < 1.0:
                section_scale = math.sqrt(max(0.0, 1.0 - z_phase * z_phase))
                eye_specs = (
                    ("eye_left", eye_filaments[0], -0.30 * x_extent),
                    ("eye_right", eye_filaments[1], 0.30 * x_extent),
                )
                for name, filament, eye_center_x in eye_specs:
                    mask = affinity.scale(
                        Point(eye_center_x, eye_center_y).buffer(1.0, quad_segs=64),
                        eye_radius_x * section_scale,
                        eye_radius_y * section_scale,
                    )
                    eye = clean_polygonal(profile.intersection(mask), min_area=1e-4)
                    if not eye.is_empty:
                        direct_regions.append((name, filament, eye))
                        profile = clean_polygonal(profile.difference(mask), min_area=1e-4)
        if profile.is_empty:
            partitioned = {}
        else:
            cluster_row = _sample_cluster_row(
                labels,
                z_center,
                period_mm=pattern_period_mm,
                angular_bins=angular_bins,
                seam_angle_deg=seam_angle_deg,
            )
            stable_clusters = smooth_circular_runs(cluster_row.astype(str), minimum_bins)
            partitioned = partition_profile(
                profile,
                stable_clusters,
                angular_bins=angular_bins,
                seam_angle_deg=seam_angle_deg,
                radius=sector_radius,
                preserve_component_area_mm2=preserve_component_area_mm2,
                minimum_piece_area_mm2=minimum_piece_area_mm2,
            )

        layer_area_by_filament = defaultdict(float)
        for cluster_text, geometry in partitioned.items():
            cluster = int(cluster_text)
            recipe = recipes[cluster]
            filament = recipe[layer_index % len(recipe)]
            for polygon in iter_polygons(geometry):
                piece_areas.append(float(polygon.area))
            layer_mesh = extrude_polygonal(
                geometry,
                height=max(1e-6, z_top - z_bottom),
                label=f"layer_{layer_index}_cluster_{cluster}_{filament}",
                min_area=1e-5,
            )
            layer_mesh.apply_translation((0.0, 0.0, z_bottom))
            meshes_by_filament[filament].append(layer_mesh)
            if proof_3mf is not None:
                meshes_by_recipe[cluster].append(layer_mesh.copy())
            area_by_filament[filament] += float(geometry.area)
            layer_area_by_filament[filament] += float(geometry.area)
        for name, filament, geometry in direct_regions:
            layer_mesh = extrude_polygonal(
                geometry,
                height=max(1e-6, z_top - z_bottom),
                label=f"layer_{layer_index}_{name}_{filament}",
                min_area=1e-5,
            )
            layer_mesh.apply_translation((0.0, 0.0, z_bottom))
            meshes_by_filament[filament].append(layer_mesh)
            if proof_3mf is not None:
                proof_direct_meshes[(name, filament)].append(layer_mesh.copy())
            area_by_filament[filament] += float(geometry.area)
            layer_area_by_filament[filament] += float(geometry.area)
        total_layer_area = source_profile_area
        if z_bottom < base_height_mm:
            total_layer_area = float(unary_union([_world_profile(
                mesh,
                min(z_center, float(mesh.bounds[1, 2]) - 1e-7),
            ), base]).area)
        layer_profile_areas.append(total_layer_area)
        if total_layer_area <= 1e-6:
            empty_profile_layers.append(layer_index)
        for filament, area in layer_area_by_filament.items():
            if area > 1e-6:
                filament_layer_counts[filament] += 1
        expected_area = float(target_profile.area)
        emitted_area = float(sum(layer_area_by_filament.values()))
        if expected_area > 1e-6 and emitted_area <= 1e-6:
            empty_target_layers.append(layer_index)
        coverage_errors.append(abs(expected_area - emitted_area))
        if verbose and ((layer_index + 1) % 50 == 0 or layer_index + 1 == layer_count):
            print(
                f"[image-weave] layers {layer_index + 1}/{layer_count} "
                f"elapsed={time.time() - started:.1f}s"
            )

    for name, filament, eye_mesh in direct_eye_meshes:
        meshes_by_filament[filament].append(eye_mesh)
        if proof_3mf is not None:
            proof_direct_meshes[(name, filament)].append(eye_mesh.copy())

    output_items: list[LeafMeshSpec] = []
    for filament in ("c", "m", "y"):
        layer_meshes = meshes_by_filament.get(filament, [])
        if not layer_meshes:
            continue
        combined = compact_touching_meshes(layer_meshes)
        output_items.append(
            LeafMeshSpec(
                label=COLOR_OBJECT_LABELS[filament],
                mesh=combined,
                metadata={
                    "source_hex": TOKEN_HEX[filament],
                    "stack_token": filament,
                    "filament": filament,
                },
            )
        )
    if not output_items:
        raise RuntimeError("No woven geometry was generated")
    assembly_bounds = np.stack(
        [item.mesh.bounds for item in output_items]
    )
    assembly_min = assembly_bounds[:, 0, :].min(axis=0)
    assembly_max = assembly_bounds[:, 1, :].max(axis=0)

    output_3mf.parent.mkdir(parents=True, exist_ok=True)
    write_orca_multipart_3mf(
        output_items,
        str(output_3mf),
        title="Angel Fish Fred Exact Image Weave",
    )
    if proof_3mf is not None:
        proof_items = []
        for cluster in range(recipe_count):
            cluster_meshes = meshes_by_recipe.get(cluster, [])
            if not cluster_meshes:
                continue
            combined = trimesh.util.concatenate(cluster_meshes)
            combined.remove_unreferenced_vertices()
            proof_items.append(
                LeafMeshSpec(
                    label=f"pattern_{cluster:02d}_{recipes[cluster]}",
                    mesh=combined,
                    metadata={
                        "source_hex": str(recipe_entries[cluster]["hex"]),
                        "stack_token": recipes[cluster],
                        "source_cluster": str(cluster),
                    },
                )
            )
        for (name, filament), direct_meshes in sorted(proof_direct_meshes.items()):
            combined = trimesh.util.concatenate(direct_meshes)
            combined.remove_unreferenced_vertices()
            proof_items.append(
                LeafMeshSpec(
                    label=f"direct_{name}_{filament}",
                    mesh=combined,
                    metadata={
                        "source_hex": TOKEN_HEX[filament],
                        "stack_token": filament,
                        "region": name,
                    },
                )
            )
        proof_3mf.parent.mkdir(parents=True, exist_ok=True)
        write_metadata_assembly_3mf(
            proof_items,
            str(proof_3mf),
            title="Angel Fish Fred Recipe Color Proof",
            include_display_colors=True,
        )

    manifest = {
        "input_stl": str(input_stl.resolve()),
        "pattern_source": pattern_reference,
        "pattern_catalog": ANGEL_FISH_CATALOG_URL,
        "output_3mf": str(output_3mf.resolve()),
        "proof_3mf": str(proof_3mf.resolve()) if proof_3mf is not None else None,
        "method": "exact_cross_section_cylindrical_image_weave",
        "output_packaging": {
            "format": "orca_multipart",
            "single_root_object": True,
            "touching_slab_compaction": True,
            "extruder_by_filament": {"c": 1, "m": 2, "y": 3},
        },
        "scale": scale,
        "layer_height_mm": layer_height,
        "layer_count": layer_count,
        "dimensions_mm": (assembly_max - assembly_min).tolist(),
        "pattern_period_mm": pattern_period_mm,
        "angular_bins": angular_bins,
        "minimum_run_mm": minimum_run_mm,
        "minimum_run_bins": minimum_bins,
        "minimum_vertical_run_mm": minimum_vertical_run_mm,
        "minimum_vertical_run_rows": minimum_vertical_rows,
        "minimum_label_component_mm2": minimum_label_component_mm2,
        "minimum_label_component_pixels": minimum_label_component_pixels,
        "detail_mm": detail_mm,
        "base_height_mm": base_height_mm,
        "base_radii_mm": [base_radius_x, base_radius_y],
        "base_filament": base_filament,
        "eye_filaments": list(eye_filaments) if eye_filaments is not None else None,
        "eye_meshes": [str(path.resolve()) for path in eye_mesh_paths] if eye_mesh_paths else None,
        "eye_mask": (
            "supplied_socket_inserts"
            if eye_mesh_paths is not None
            else ("fitted_ellipsoids" if eye_filaments is not None else None)
        ),
        "coverage": {
            "empty_profile_layer_count": len(empty_profile_layers),
            "empty_profile_layers": empty_profile_layers,
            "minimum_profile_area_mm2": float(min(layer_profile_areas)) if layer_profile_areas else 0.0,
        },
        "preserve_component_area_mm2": preserve_component_area_mm2,
        "minimum_piece_area_mm2": minimum_piece_area_mm2,
        "recipe_count": recipe_count,
        "recipes": [
            {
                "cluster": index,
                "source_hex": _rgb_to_hex(source_rgb[index]),
                "pixel_fraction": float(counts[index] / np.sum(counts)),
                "token": recipes[index],
                "target_hex": recipe_entries[index]["hex"],
            }
            for index in range(recipe_count)
        ],
        "filament_layer_area_mm2": dict(area_by_filament),
        "filament_nonempty_layer_counts": dict(filament_layer_counts),
        "coverage_audit": {
            "empty_target_layer_count": len(empty_target_layers),
            "empty_target_layers": empty_target_layers,
            "maximum_area_error_mm2": float(max(coverage_errors, default=0.0)),
            "p99_area_error_mm2": float(np.percentile(coverage_errors, 99))
            if coverage_errors
            else 0.0,
        },
        "piece_area_mm2": {
            "minimum": float(np.min(piece_areas)),
            "p10": float(np.percentile(piece_areas, 10)),
            "median": float(np.median(piece_areas)),
        },
        "elapsed_seconds": time.time() - started,
    }
    output_3mf.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_stl")
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--proof-output")
    parser.add_argument("--pattern", default=ANGEL_FISH_URL)
    parser.add_argument("--scale", type=float, default=2.5)
    parser.add_argument("--layer-height", type=float, default=0.08)
    parser.add_argument("--pattern-period-mm", type=float, default=100.0)
    parser.add_argument("--angular-bins", type=int, default=360)
    parser.add_argument("--minimum-run-mm", type=float, default=2.2)
    parser.add_argument(
        "--minimum-vertical-run-mm",
        type=float,
        default=0.0,
        help="Remove pattern runs shorter than this distance along Z before weaving.",
    )
    parser.add_argument(
        "--minimum-label-component-mm2",
        type=float,
        default=0.0,
        help="Reassign disconnected source-image regions smaller than this physical area.",
    )
    parser.add_argument("--palette", default="CMY")
    parser.add_argument("--recipe-count", type=int, default=9)
    parser.add_argument("--detail-mm", type=float, default=0.6)
    parser.add_argument("--crop-fraction", type=float, default=0.5)
    parser.add_argument("--seam-angle-deg", type=float, default=90.0)
    parser.add_argument("--base-height-mm", type=float, default=2.1)
    parser.add_argument("--base-radius-fraction", type=float, default=0.42)
    parser.add_argument(
        "--base-filament",
        choices=("c", "m", "y"),
        help="Reserve the added base as one direct filament instead of patterning it.",
    )
    parser.add_argument(
        "--eye-filaments",
        help="Direct filament pair for the established resculpted-Fred eye masks, such as c,m.",
    )
    parser.add_argument(
        "--eye-meshes",
        help="Comma-separated eye insert STL paths sharing the input body's coordinates.",
    )
    parser.add_argument("--preserve-component-area-mm2", type=float, default=24.0)
    parser.add_argument("--minimum-piece-area-mm2", type=float, default=2.0)
    parser.add_argument("--seed", type=int, default=2400)
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    output = Path(args.output).expanduser().resolve()
    proof_output = (
        Path(args.proof_output).expanduser().resolve()
        if args.proof_output
        else None
    )
    eye_filaments = None
    if args.eye_filaments:
        eye_values = tuple(value.strip().lower() for value in args.eye_filaments.split(","))
        if len(eye_values) != 2 or any(value not in {"c", "m", "y"} for value in eye_values):
            raise SystemExit("--eye-filaments must contain two comma-separated values from c,m,y")
        eye_filaments = (eye_values[0], eye_values[1])
    eye_mesh_paths = None
    if args.eye_meshes:
        eye_values = tuple(
            Path(value.strip()).expanduser().resolve()
            for value in args.eye_meshes.split(",")
            if value.strip()
        )
        if len(eye_values) != 2:
            raise SystemExit("--eye-meshes must contain exactly two comma-separated STL paths")
        if any(not path.is_file() for path in eye_values):
            raise SystemExit("--eye-meshes contains a path that does not exist")
        eye_mesh_paths = (eye_values[0], eye_values[1])
    pattern_path, reference = resolve_pattern(args.pattern, output.parent / "source")
    manifest = build_image_weave(
        Path(args.input_stl).expanduser().resolve(),
        pattern_path,
        output,
        proof_3mf=proof_output,
        pattern_reference=reference,
        scale=args.scale,
        layer_height=args.layer_height,
        pattern_period_mm=args.pattern_period_mm,
        angular_bins=args.angular_bins,
        minimum_run_mm=args.minimum_run_mm,
        minimum_vertical_run_mm=args.minimum_vertical_run_mm,
        minimum_label_component_mm2=args.minimum_label_component_mm2,
        palette_name=args.palette,
        recipe_count=args.recipe_count,
        detail_mm=args.detail_mm,
        crop_fraction=args.crop_fraction,
        seam_angle_deg=args.seam_angle_deg,
        base_height_mm=args.base_height_mm,
        base_radius_fraction=args.base_radius_fraction,
        base_filament=args.base_filament,
        eye_filaments=eye_filaments,
        eye_mesh_paths=eye_mesh_paths,
        preserve_component_area_mm2=args.preserve_component_area_mm2,
        minimum_piece_area_mm2=args.minimum_piece_area_mm2,
        seed=args.seed,
        verbose=args.verbose,
    )
    print(output)
    print(output.with_suffix(".manifest.json"))
    print(f"layers: {manifest['layer_count']}, elapsed: {manifest['elapsed_seconds']:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
