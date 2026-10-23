"""Map a 2D pattern onto an STL and export LayerLoom-ready vertex colors."""

from __future__ import annotations

import argparse
import itertools
import json
import math
import ssl
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np
import trimesh
from PIL import Image, ImageFilter
from scipy import ndimage
from scipy.cluster.vq import kmeans2

from layerloom.palette_assignment import load_palette_entries, nearest_palette_entry


ANGEL_FISH_URL = (
    "https://images.squarespace-cdn.com/content/v1/"
    "68f05ce247524d0f8fd3d753/c1ee76d0-8b44-41bb-a70a-198931a4a426/IMG_7480.jpg"
)
ANGEL_FISH_CATALOG_URL = "https://www.foxallenbindery.com/acrylic-marbling"


@dataclass(frozen=True)
class PatternConfig:
    color_mode: str
    palette: str
    max_colors: int
    pattern_period_mm: float
    scale: float
    source_scale: float
    seam_angle_deg: float
    detail_mm: float
    crop_fraction: float
    seed: int
    geometry_mode: str
    voxel_mm: float
    base_height_mm: float
    base_radius_fraction: float
    min_component_voxels: int
    color_smoothing_passes: int


def srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    values = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0)
    return np.where(
        values <= 0.04045,
        values / 12.92,
        ((values + 0.055) / 1.055) ** 2.4,
    )


def linear_to_srgb(rgb: np.ndarray) -> np.ndarray:
    values = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0)
    return np.where(
        values <= 0.0031308,
        values * 12.92,
        1.055 * np.power(values, 1.0 / 2.4) - 0.055,
    )


def srgb_to_oklab(rgb: np.ndarray) -> np.ndarray:
    linear = srgb_to_linear(rgb)
    lms = linear @ np.asarray(
        [
            [0.4122214708, 0.2119034982, 0.0883024619],
            [0.5363325363, 0.6806995451, 0.2817188376],
            [0.0514459929, 0.1073969566, 0.6299787005],
        ],
        dtype=np.float64,
    )
    lms = np.cbrt(np.maximum(lms, 0.0))
    return lms @ np.asarray(
        [
            [0.2104542553, 1.9779984951, 0.0259040371],
            [0.7936177850, -2.4285922050, 0.7827717662],
            [-0.0040720468, 0.4505937099, -0.8086757660],
        ],
        dtype=np.float64,
    )


def oklab_to_srgb(lab: np.ndarray) -> np.ndarray:
    values = np.asarray(lab, dtype=np.float64)
    lms_root = values @ np.asarray(
        [
            [1.0, 1.0, 1.0],
            [0.3963377774, -0.1055613458, -0.0894841775],
            [0.2158037573, -0.0638541728, -1.2914855480],
        ],
        dtype=np.float64,
    )
    linear = (lms_root**3) @ np.asarray(
        [
            [4.0767416621, -1.2684380046, -0.0041960863],
            [-3.3077115913, 2.6097574011, -0.7034186147],
            [0.2309699292, -0.3413193965, 1.7076147010],
        ],
        dtype=np.float64,
    )
    return np.clip(linear_to_srgb(linear), 0.0, 1.0)


def _hex_to_rgb(hex_color: str) -> np.ndarray:
    value = hex_color.lstrip("#")
    return np.asarray([int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4)], dtype=np.float64)


def _rgb_to_hex(rgb: np.ndarray) -> str:
    values = np.clip(np.rint(np.asarray(rgb) * 255.0), 0, 255).astype(np.uint8)
    return "#" + "".join(f"{int(value):02x}" for value in values[:3])


def _download_pattern(url: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "LayerLoom pattern mapper"})
    context = ssl.create_default_context()
    try:
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    try:
        with urllib.request.urlopen(request, timeout=60, context=context) as response:
            destination.write_bytes(response.read())
    except Exception as exc:
        raise RuntimeError(f"Could not download pattern image from {url}: {exc}") from exc
    return destination


def resolve_pattern(pattern: str, cache_dir: Path) -> tuple[Path, str]:
    if pattern.startswith(("https://", "http://")):
        suffix = Path(pattern.split("?", 1)[0]).suffix or ".jpg"
        path = cache_dir / f"source_pattern{suffix}"
        return _download_pattern(pattern, path), pattern
    path = Path(pattern).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    return path, str(path)


def prepare_tile(image: Image.Image, *, crop_fraction: float, period_mm: float, detail_mm: float) -> Image.Image:
    image = image.convert("RGB")
    side = max(32, int(round(min(image.size) * crop_fraction)))
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side))

    array = np.asarray(image, dtype=np.float32)
    array = np.roll(array, image.width // 2, axis=1)
    seam = image.width // 2
    band = max(4, int(round(image.width * 0.04)))
    blurred = np.asarray(
        Image.fromarray(array.astype(np.uint8)).filter(ImageFilter.GaussianBlur(radius=max(1.0, band / 5))),
        dtype=np.float32,
    )
    mask = np.zeros((image.height, image.width, 1), dtype=np.float32)
    x0, x1 = max(0, seam - band), min(image.width, seam + band)
    phase = np.linspace(-1.0, 1.0, x1 - x0, dtype=np.float32)
    mask[:, x0:x1, 0] = np.cos(phase * math.pi / 2.0) ** 2
    array = array * (1.0 - mask) + blurred * mask

    pixels = max(32, int(math.ceil(period_mm / detail_mm)))
    tile = Image.fromarray(np.clip(array, 0, 255).astype(np.uint8))
    tile = tile.resize((pixels, pixels), Image.Resampling.LANCZOS)
    return tile.filter(ImageFilter.MedianFilter(size=3))


def quantize_oklab(image: Image.Image, max_colors: int, seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rgb = np.asarray(image.convert("RGB"), dtype=np.float64) / 255.0
    flat_lab = srgb_to_oklab(rgb.reshape(-1, 3))
    rng = np.random.default_rng(seed)
    sample_count = min(120_000, len(flat_lab))
    chroma = np.linalg.norm(flat_lab[:, 1:3], axis=1)
    perceptual_weight = 0.2 + 3.0 * chroma + 1.25 * np.abs(flat_lab[:, 0] - 0.62)
    perceptual_weight /= np.sum(perceptual_weight)
    sample = flat_lab[rng.choice(len(flat_lab), sample_count, replace=False, p=perceptual_weight)]
    centers, _labels = kmeans2(sample, max_colors, iter=40, minit="++", seed=rng)

    distances = np.sum((flat_lab[:, None, :] - centers[None, :, :]) ** 2, axis=2)
    labels = np.argmin(distances, axis=1)
    counts = np.bincount(labels, minlength=max_colors)
    order = np.argsort(centers[:, 0])
    inverse = np.empty_like(order)
    inverse[order] = np.arange(len(order))
    labels = inverse[labels]
    centers = centers[order]
    counts = counts[order]
    center_rgb = oklab_to_srgb(centers)
    return labels.reshape(rgb.shape[:2]), center_rgb, counts


def _circular_distance(a: float, b: np.ndarray) -> np.ndarray:
    return np.abs(np.angle(np.exp(1j * (b - a))))


def choose_wide_palette(source_rgb: np.ndarray, palette_name: str) -> tuple[np.ndarray, list[dict[str, Any]]]:
    entries = load_palette_entries(palette_name)
    palette_rgb = np.asarray([_hex_to_rgb(entry["hex"]) for entry in entries])
    palette_lab = srgb_to_oklab(palette_rgb)
    palette_hue = np.mod(np.arctan2(palette_lab[:, 2], palette_lab[:, 1]), 2 * math.pi)
    palette_chroma = np.linalg.norm(palette_lab[:, 1:3], axis=1)

    source_lab = srgb_to_oklab(source_rgb)
    source_hue = np.mod(np.arctan2(source_lab[:, 2], source_lab[:, 1]), 2 * math.pi)
    hue_order = np.argsort(source_hue)
    desired_hue = np.empty(len(source_rgb), dtype=np.float64)
    desired_hue[hue_order] = np.linspace(0.0, 2 * math.pi, len(source_rgb), endpoint=False)

    selected: list[int] = []
    selected_entries: list[dict[str, Any]] = []
    lightness_order = np.argsort(source_lab[:, 0])
    for rank, index in enumerate(lightness_order):
        lightness_cost = 20.0 * (palette_lab[:, 0] - source_lab[index, 0]) ** 2
        hue_cost = 0.45 * _circular_distance(desired_hue[index], palette_hue) ** 2
        chroma_reward = 0.12 * palette_chroma
        cost = lightness_cost + hue_cost - chroma_reward
        if selected:
            cost[np.asarray(selected, dtype=np.int64)] = np.inf
        if rank == 0:
            available = np.ones(len(entries), dtype=bool)
            available[np.asarray(selected, dtype=np.int64)] = False
            chosen = int(np.flatnonzero(available)[np.argmin(palette_lab[available, 0])])
        elif rank == len(source_rgb) - 1:
            available = np.ones(len(entries), dtype=bool)
            available[np.asarray(selected, dtype=np.int64)] = False
            chosen = int(np.flatnonzero(available)[np.argmax(palette_lab[available, 0])])
        else:
            previous_lightness = palette_lab[selected[-1], 0]
            cost[palette_lab[:, 0] < previous_lightness - 1e-6] = np.inf
            chosen = int(np.argmin(cost))
        selected.append(chosen)
        item = dict(entries[chosen])
        item["source_cluster"] = int(index)
        selected_entries.append(item)

    remapped = np.empty_like(source_rgb)
    for entry in selected_entries:
        remapped[entry["source_cluster"]] = _hex_to_rgb(entry["hex"])
    selected_entries.sort(key=lambda item: item["source_cluster"])
    return remapped, selected_entries


def choose_direct_cmy_palette(source_rgb: np.ndarray, palette_name: str) -> tuple[np.ndarray, list[dict[str, Any]]]:
    if len(source_rgb) != 3:
        raise ValueError("direct-cmy mode requires exactly three quantized colors")
    entries = load_palette_entries(palette_name)
    primaries = []
    for token in ("c", "m", "y"):
        match = next((entry for entry in entries if entry.get("token") == token), None)
        if match is None:
            raise ValueError(f"Palette {palette_name!r} does not contain direct token {token!r}")
        primaries.append(dict(match))

    source_lab = srgb_to_oklab(source_rgb)
    primary_rgb = np.asarray([_hex_to_rgb(entry["hex"]) for entry in primaries])
    primary_lab = srgb_to_oklab(primary_rgb)
    best_permutation = min(
        itertools.permutations(range(3)),
        key=lambda permutation: float(
            np.sum(
                (source_lab - primary_lab[np.asarray(permutation, dtype=np.int64)]) ** 2
            )
        ),
    )
    mapped = primary_rgb[np.asarray(best_permutation, dtype=np.int64)]
    selected: list[dict[str, Any]] = []
    for source_cluster, primary_index in enumerate(best_permutation):
        item = dict(primaries[primary_index])
        item["source_cluster"] = source_cluster
        selected.append(item)
    return mapped, selected


def _sample_tile(tile_rgb: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    height, width = tile_rgb.shape[:2]
    x = np.floor(np.mod(u, 1.0) * width).astype(np.int64) % width
    y = np.floor(np.mod(1.0 - v, 1.0) * height).astype(np.int64) % height
    return tile_rgb[y, x]


def map_vertex_colors(
    vertices_mm: np.ndarray,
    normals: np.ndarray,
    tile_rgb: np.ndarray,
    *,
    period_mm: float,
    seam_angle_deg: float,
) -> np.ndarray:
    x, y, z = vertices_mm.T
    theta = np.arctan2(y, x)
    seam = math.radians(seam_angle_deg)
    angle = np.mod(theta - seam, 2 * math.pi)
    repeat_count = max(1, int(round((2 * math.pi * np.percentile(np.hypot(x, y), 90)) / period_mm)))
    cylindrical_u = angle / (2 * math.pi) * repeat_count
    cylindrical_v = z / period_mm

    planar_u = x / period_mm + 0.5
    planar_v = y / period_mm + 0.5
    cylindrical = _sample_tile(tile_rgb, cylindrical_u, cylindrical_v)
    planar = _sample_tile(tile_rgb, planar_u, planar_v)
    top_weight = np.clip((np.abs(normals[:, 2]) - 0.65) / 0.25, 0.0, 1.0)[:, None]
    blended = cylindrical * (1.0 - top_weight) + planar * top_weight

    palette = np.unique(tile_rgb.reshape(-1, 3), axis=0)
    blended_lab = srgb_to_oklab(blended)
    palette_lab = srgb_to_oklab(palette)
    out = np.empty_like(blended)
    for start in range(0, len(blended), 50_000):
        chunk = blended_lab[start : start + 50_000]
        nearest = np.argmin(np.sum((chunk[:, None, :] - palette_lab[None, :, :]) ** 2, axis=2), axis=1)
        out[start : start + len(chunk)] = palette[nearest]
    return out


def _cylindrical_tile_colors(
    points_mm: np.ndarray,
    tile_rgb: np.ndarray,
    *,
    period_mm: float,
    seam_angle_deg: float,
) -> np.ndarray:
    x, y, z = points_mm.T
    theta = np.arctan2(y, x)
    seam = math.radians(seam_angle_deg)
    angle = np.mod(theta - seam, 2 * math.pi)
    radius = np.percentile(np.hypot(x, y), 90)
    repeat_count = max(1, int(round((2 * math.pi * radius) / period_mm)))
    return _sample_tile(tile_rgb, angle / (2 * math.pi) * repeat_count, z / period_mm)


def _absorb_small_color_components(
    labels: np.ndarray,
    occupancy: np.ndarray,
    *,
    min_voxels: int,
) -> np.ndarray:
    out = np.asarray(labels, dtype=np.int16).copy()
    structure = ndimage.generate_binary_structure(3, 1)
    for _pass in range(3):
        changed = 0
        for color in np.unique(out[occupancy]):
            components, count = ndimage.label(occupancy & (out == color), structure=structure)
            if count == 0:
                continue
            sizes = np.bincount(components.reshape(-1))
            for component in np.flatnonzero((sizes < min_voxels) & (np.arange(len(sizes)) > 0)):
                cells = np.argwhere(components == component)
                neighbor_colors: list[np.ndarray] = []
                for axis in range(3):
                    for delta in (-1, 1):
                        adjacent = cells.copy()
                        adjacent[:, axis] += delta
                        valid = np.all((adjacent >= 0) & (adjacent < np.asarray(out.shape)), axis=1)
                        adjacent = adjacent[valid]
                        if len(adjacent):
                            values = out[tuple(adjacent.T)]
                            occupied_values = occupancy[tuple(adjacent.T)]
                            values = values[occupied_values & (values != color)]
                            if len(values):
                                neighbor_colors.append(values)
                if not neighbor_colors:
                    continue
                neighbors = np.concatenate(neighbor_colors)
                replacement = int(np.bincount(neighbors.astype(np.int64)).argmax())
                out[components == component] = replacement
                changed += 1
        if changed == 0:
            break
    return out


def _smooth_color_volume(labels: np.ndarray, occupancy: np.ndarray, passes: int) -> np.ndarray:
    out = np.asarray(labels, dtype=np.int16).copy()
    colors = np.unique(out[occupancy])
    kernel = np.ones((3, 3, 3), dtype=np.int16)
    for _pass in range(passes):
        votes = np.stack(
            [
                ndimage.convolve((out == color).astype(np.int16), kernel, mode="constant", cval=0)
                for color in colors
            ],
            axis=0,
        )
        out[occupancy] = colors[np.argmax(votes[:, occupancy], axis=0)]
    return out


_VOXEL_FACES = (
    ((1, 0, 0), ((1, -1, -1), (1, 1, -1), (1, 1, 1), (1, -1, 1))),
    ((-1, 0, 0), ((-1, -1, -1), (-1, -1, 1), (-1, 1, 1), (-1, 1, -1))),
    ((0, 1, 0), ((-1, 1, -1), (-1, 1, 1), (1, 1, 1), (1, 1, -1))),
    ((0, -1, 0), ((-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1))),
    ((0, 0, 1), ((-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1))),
    ((0, 0, -1), ((-1, -1, -1), (-1, 1, -1), (1, 1, -1), (1, -1, -1))),
)


def voxel_union_mesh(cells: np.ndarray, pitch: float) -> tuple[np.ndarray, np.ndarray]:
    cell_set = {tuple(int(value) for value in cell) for cell in np.asarray(cells)}
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    vertex_map: dict[tuple[int, int, int], int] = {}

    for cell in sorted(cell_set):
        for direction, corners in _VOXEL_FACES:
            neighbor = tuple(cell[axis] + direction[axis] for axis in range(3))
            if neighbor in cell_set:
                continue
            quad: list[int] = []
            for corner in corners:
                doubled = tuple(2 * cell[axis] + corner[axis] for axis in range(3))
                vertex_index = vertex_map.get(doubled)
                if vertex_index is None:
                    vertex_index = len(vertices)
                    vertex_map[doubled] = vertex_index
                    vertices.append(tuple(value * pitch / 2.0 for value in doubled))
                quad.append(vertex_index)
            faces.append((quad[0], quad[1], quad[2]))
            faces.append((quad[0], quad[2], quad[3]))
    return np.asarray(vertices, dtype=np.float64), np.asarray(faces, dtype=np.uint32)


def build_voxel_color_partition(
    mesh: trimesh.Trimesh,
    tile_rgb: np.ndarray,
    *,
    config: PatternConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    voxel_grid = mesh.voxelized(config.voxel_mm).fill()
    occupied_points = np.asarray(voxel_grid.points, dtype=np.float64)
    occupied_indices = np.rint(occupied_points / config.voxel_mm).astype(np.int64)

    extents = np.ptp(np.asarray(mesh.vertices), axis=0)
    radius_x = extents[0] * config.base_radius_fraction
    radius_y = extents[1] * config.base_radius_fraction
    x_values = np.arange(math.floor(-radius_x / config.voxel_mm), math.ceil(radius_x / config.voxel_mm) + 1)
    y_values = np.arange(math.floor(-radius_y / config.voxel_mm), math.ceil(radius_y / config.voxel_mm) + 1)
    z_values = np.arange(0, max(1, math.ceil(config.base_height_mm / config.voxel_mm)))
    xx, yy, zz = np.meshgrid(x_values, y_values, z_values, indexing="ij")
    ellipse = (xx * config.voxel_mm / radius_x) ** 2 + (yy * config.voxel_mm / radius_y) ** 2 <= 1.0
    base_indices = np.c_[xx[ellipse], yy[ellipse], zz[ellipse]]
    all_indices = np.unique(np.vstack([occupied_indices, base_indices]), axis=0)

    index_min = all_indices.min(axis=0)
    shifted = all_indices - index_min
    shape = tuple((shifted.max(axis=0) + 1).tolist())
    occupancy = np.zeros(shape, dtype=bool)
    occupancy[tuple(shifted.T)] = True

    points = all_indices.astype(np.float64) * config.voxel_mm
    point_colors = _cylindrical_tile_colors(
        points,
        tile_rgb,
        period_mm=config.pattern_period_mm,
        seam_angle_deg=config.seam_angle_deg,
    )
    palette_rgb, color_ids = np.unique(tile_rgb.reshape(-1, 3), axis=0, return_inverse=True)
    palette_lab = srgb_to_oklab(palette_rgb)
    point_lab = srgb_to_oklab(point_colors)
    point_labels = np.argmin(
        np.sum((point_lab[:, None, :] - palette_lab[None, :, :]) ** 2, axis=2),
        axis=1,
    ).astype(np.int16)
    label_grid = np.full(shape, -1, dtype=np.int16)
    label_grid[tuple(shifted.T)] = point_labels
    label_grid = _smooth_color_volume(
        label_grid,
        occupancy,
        config.color_smoothing_passes,
    )
    label_grid = _absorb_small_color_components(
        label_grid,
        occupancy,
        min_voxels=config.min_component_voxels,
    )

    combined_vertices: list[np.ndarray] = []
    combined_faces: list[np.ndarray] = []
    combined_colors: list[np.ndarray] = []
    component_counts: dict[str, int] = {}
    vertex_offset = 0
    connectivity = ndimage.generate_binary_structure(3, 1)
    for color_index in np.unique(label_grid[occupancy]):
        color_volume = occupancy & (label_grid == color_index)
        components, component_count = ndimage.label(color_volume, structure=connectivity)
        component_meshes: list[trimesh.Trimesh] = []
        for component_id, component_slice in enumerate(ndimage.find_objects(components), start=1):
            if component_slice is None:
                continue
            component = components[component_slice] == component_id
            padded_component = np.pad(component, 1, mode="constant", constant_values=False)
            component_mesh = trimesh.voxel.ops.matrix_to_marching_cubes(
                padded_component,
                pitch=config.voxel_mm,
            )
            slice_start = np.asarray([axis_slice.start for axis_slice in component_slice], dtype=np.float64)
            component_mesh.apply_translation(
                (index_min.astype(np.float64) + slice_start - 1.0) * config.voxel_mm
            )
            component_meshes.append(component_mesh)
        part_mesh = trimesh.util.concatenate(component_meshes)
        part_vertices = np.asarray(part_mesh.vertices, dtype=np.float64)
        part_faces = np.asarray(part_mesh.faces, dtype=np.uint32)
        component_counts[str(int(color_index))] = int(component_count)
        combined_vertices.append(part_vertices)
        combined_faces.append(part_faces + vertex_offset)
        combined_colors.append(np.repeat(palette_rgb[int(color_index)][None, :], len(part_vertices), axis=0))
        vertex_offset += len(part_vertices)

    vertices = np.vstack(combined_vertices)
    vertices[:, 2] -= vertices[:, 2].min()
    return (
        vertices,
        np.vstack(combined_faces),
        np.vstack(combined_colors),
        {
            "voxel_count": int(np.sum(occupancy)),
            "voxel_shape": list(shape),
            "color_component_counts": component_counts,
            "max_color_components": max(component_counts.values(), default=0),
        },
    )


def write_glb(
    output_path: Path,
    vertices: np.ndarray,
    faces: np.ndarray,
    colors_srgb: np.ndarray,
    *,
    name: str,
) -> None:
    from pygltflib import (
        ARRAY_BUFFER,
        ELEMENT_ARRAY_BUFFER,
        FLOAT,
        UNSIGNED_INT,
        Accessor,
        Asset,
        Buffer,
        BufferView,
        GLTF2,
        Material,
        Mesh,
        Node,
        PbrMetallicRoughness,
        Primitive,
        Scene,
    )
    from pygltflib import Attributes

    positions = np.asarray(vertices, dtype="<f4")
    colors = np.c_[srgb_to_linear(colors_srgb), np.ones(len(colors_srgb))].astype("<f4")
    indices = np.asarray(faces, dtype="<u4").reshape(-1)
    chunks = [positions.tobytes(), colors.tobytes(), indices.tobytes()]
    offsets: list[int] = []
    blob = bytearray()
    for chunk in chunks:
        while len(blob) % 4:
            blob.append(0)
        offsets.append(len(blob))
        blob.extend(chunk)

    gltf = GLTF2(
        asset=Asset(version="2.0", generator="LayerLoom pattern mapper"),
        scene=0,
        scenes=[Scene(nodes=[0])],
        nodes=[Node(mesh=0, name=name)],
        meshes=[
            Mesh(
                name=name,
                primitives=[
                    Primitive(
                        attributes=Attributes(POSITION=0, COLOR_0=1),
                        indices=2,
                        material=0,
                    )
                ],
            )
        ],
        materials=[
            Material(
                name="LayerLoom vertex colors",
                doubleSided=True,
                pbrMetallicRoughness=PbrMetallicRoughness(
                    baseColorFactor=[1.0, 1.0, 1.0, 1.0],
                    metallicFactor=0.0,
                    roughnessFactor=0.72,
                ),
            )
        ],
        buffers=[Buffer(byteLength=len(blob))],
        bufferViews=[
            BufferView(buffer=0, byteOffset=offsets[0], byteLength=len(chunks[0]), target=ARRAY_BUFFER),
            BufferView(buffer=0, byteOffset=offsets[1], byteLength=len(chunks[1]), target=ARRAY_BUFFER),
            BufferView(
                buffer=0,
                byteOffset=offsets[2],
                byteLength=len(chunks[2]),
                target=ELEMENT_ARRAY_BUFFER,
            ),
        ],
        accessors=[
            Accessor(
                bufferView=0,
                componentType=FLOAT,
                count=len(positions),
                type="VEC3",
                min=positions.min(axis=0).astype(float).tolist(),
                max=positions.max(axis=0).astype(float).tolist(),
            ),
            Accessor(bufferView=1, componentType=FLOAT, count=len(colors), type="VEC4"),
            Accessor(
                bufferView=2,
                componentType=UNSIGNED_INT,
                count=len(indices),
                type="SCALAR",
                min=[int(indices.min())],
                max=[int(indices.max())],
            ),
        ],
    )
    gltf.set_binary_blob(bytes(blob))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    gltf.save_binary(str(output_path))


def _cluster_manifest(
    source_rgb: np.ndarray,
    output_rgb: np.ndarray,
    counts: np.ndarray,
    palette_name: str,
    wide_entries: Optional[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    total = int(np.sum(counts))
    records: list[dict[str, Any]] = []
    entries = load_palette_entries(palette_name)
    for index, (source, output, count) in enumerate(zip(source_rgb, output_rgb, counts)):
        match = nearest_palette_entry(entries, _rgb_to_hex(output))
        record = {
            "cluster": index,
            "source_hex": _rgb_to_hex(source),
            "mapped_hex": _rgb_to_hex(output),
            "pixel_count": int(count),
            "area_fraction": float(count / total),
            "layerloom_token": match.get("token") if match else None,
            "layerloom_hex": match.get("hex") if match else None,
        }
        if wide_entries:
            selected = wide_entries[index]
            record["selected_token"] = selected.get("token")
            record["selected_hex"] = selected.get("hex")
        records.append(record)
    return records


def build_patterned_glb(
    input_stl: Path,
    pattern_path: Path,
    output_glb: Path,
    config: PatternConfig,
    *,
    source_reference: str,
) -> dict[str, Any]:
    mesh = trimesh.load(input_stl, force="mesh", process=True)
    if not isinstance(mesh, trimesh.Trimesh):
        raise RuntimeError(f"Expected one mesh in {input_stl}")
    physical_vertices = np.asarray(mesh.vertices, dtype=np.float64) * config.scale
    center_xy = (physical_vertices[:, :2].min(axis=0) + physical_vertices[:, :2].max(axis=0)) / 2.0
    physical_vertices[:, :2] -= center_xy
    physical_vertices[:, 2] -= physical_vertices[:, 2].min()

    tile = prepare_tile(
        Image.open(pattern_path),
        crop_fraction=config.crop_fraction,
        period_mm=config.pattern_period_mm,
        detail_mm=config.detail_mm,
    )
    labels, source_rgb, counts = quantize_oklab(tile, config.max_colors, config.seed)
    wide_entries: Optional[list[dict[str, Any]]] = None
    if config.color_mode == "wide":
        mapped_rgb, wide_entries = choose_wide_palette(source_rgb, config.palette)
    elif config.color_mode == "direct-cmy":
        mapped_rgb, wide_entries = choose_direct_cmy_palette(source_rgb, config.palette)
    else:
        mapped_rgb = source_rgb
    tile_rgb = mapped_rgb[labels]

    mesh.vertices = physical_vertices
    geometry_stats: dict[str, Any] = {}
    if config.geometry_mode == "voxel-solid":
        output_vertices, output_faces, vertex_colors, geometry_stats = build_voxel_color_partition(
            mesh,
            tile_rgb,
            config=config,
        )
    else:
        output_vertices = physical_vertices
        output_faces = np.asarray(mesh.faces, dtype=np.uint32)
        normals = np.asarray(mesh.vertex_normals, dtype=np.float64)
        vertex_colors = map_vertex_colors(
            physical_vertices,
            normals,
            tile_rgb,
            period_mm=config.pattern_period_mm,
            seam_angle_deg=config.seam_angle_deg,
        )
    glb_vertices = output_vertices / config.source_scale
    write_glb(
        output_glb,
        glb_vertices,
        output_faces,
        vertex_colors,
        name=f"Fred Angel Fish {config.color_mode}",
    )

    prepared_path = output_glb.with_name(f"{output_glb.stem}_pattern.png")
    Image.fromarray(np.clip(tile_rgb * 255.0, 0, 255).astype(np.uint8)).save(prepared_path)
    physical_extents = np.ptp(output_vertices, axis=0)
    manifest = {
        "input_stl": str(input_stl.resolve()),
        "pattern_source": source_reference,
        "pattern_catalog": ANGEL_FISH_CATALOG_URL,
        "output_glb": str(output_glb.resolve()),
        "prepared_pattern": str(prepared_path.resolve()),
        "color_mode": config.color_mode,
        "palette": config.palette,
        "max_colors": config.max_colors,
        "projection": "cylindrical_with_planar_caps",
        "pattern_period_mm": config.pattern_period_mm,
        "detail_mm": config.detail_mm,
        "crop_fraction": config.crop_fraction,
        "model_scale": config.scale,
        "source_scale": config.source_scale,
        "glb_units_per_mm": 1.0 / config.source_scale,
        "seam_angle_deg": config.seam_angle_deg,
        "geometry_mode": config.geometry_mode,
        "voxel_mm": config.voxel_mm,
        "base_height_mm": config.base_height_mm,
        "base_radius_fraction": config.base_radius_fraction,
        "min_component_voxels": config.min_component_voxels,
        "color_smoothing_passes": config.color_smoothing_passes,
        "vertex_count": int(len(output_vertices)),
        "face_count": int(len(output_faces)),
        "watertight": bool(mesh.is_watertight),
        "physical_extents_mm": physical_extents.tolist(),
        "glb_extents": (physical_extents / config.source_scale).tolist(),
        "clusters": _cluster_manifest(source_rgb, mapped_rgb, counts, config.palette, wide_entries),
        "geometry_stats": geometry_stats,
    }
    manifest_path = output_glb.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def render_previews(glb_path: Path, preview_dir: Path, blender: str) -> None:
    worker = Path(__file__).with_name("blender_vertex_color_preview.py")
    command = [
        blender,
        "--background",
        "--python",
        str(worker),
        "--",
        str(glb_path),
        str(preview_dir),
    ]
    subprocess.run(command, check=True)
    missing = [
        preview_dir / f"{glb_path.stem}_{view}.png"
        for view in ("front", "rear", "side")
        if not (preview_dir / f"{glb_path.stem}_{view}.png").is_file()
    ]
    if missing:
        raise RuntimeError(f"Blender did not produce expected previews: {', '.join(map(str, missing))}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_stl", help="Input STL model")
    parser.add_argument("-o", "--output", required=True, help="Output GLB")
    parser.add_argument("--pattern", default=ANGEL_FISH_URL, help="Pattern image path or URL")
    parser.add_argument("--color-mode", choices=("source", "wide", "direct-cmy"), default="source")
    parser.add_argument("--palette", default="CMY", help="LayerLoom palette name or JSON path")
    parser.add_argument("--max-colors", type=int, default=16)
    parser.add_argument("--projection", choices=("cylindrical",), default="cylindrical")
    parser.add_argument(
        "--geometry-mode",
        choices=("voxel-solid", "surface"),
        default="voxel-solid",
        help="Volumetric color cells are printable; surface mode is for diagnostics only",
    )
    parser.add_argument("--pattern-period-mm", type=float, default=80.0)
    parser.add_argument("--scale", type=float, default=2.5)
    parser.add_argument("--source-scale", type=float, default=0.1)
    parser.add_argument("--seam-angle-deg", type=float, default=90.0, help="Rear seam direction in XY")
    parser.add_argument("--detail-mm", type=float, default=0.4)
    parser.add_argument("--crop-fraction", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=2400)
    parser.add_argument("--voxel-mm", type=float, default=0.7)
    parser.add_argument("--base-height-mm", type=float, default=2.1)
    parser.add_argument("--base-radius-fraction", type=float, default=0.42)
    parser.add_argument("--min-component-voxels", type=int, default=96)
    parser.add_argument("--color-smoothing-passes", type=int, default=2)
    parser.add_argument("--preview-dir", help="Render front, rear, and side previews with Blender")
    parser.add_argument(
        "--blender",
        default="/Applications/Blender.app/Contents/MacOS/blender",
        help="Blender executable used only for previews",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_colors < 2:
        raise SystemExit("--max-colors must be at least 2")
    if args.color_mode == "direct-cmy" and args.max_colors != 3:
        raise SystemExit("--color-mode direct-cmy requires --max-colors 3")
    if not (0.1 <= args.crop_fraction <= 1.0):
        raise SystemExit("--crop-fraction must be between 0.1 and 1.0")
    if (
        args.pattern_period_mm <= 0
        or args.detail_mm <= 0
        or args.scale <= 0
        or args.source_scale <= 0
        or args.voxel_mm <= 0
        or args.base_height_mm < 0
    ):
        raise SystemExit("scale and physical-size options must be positive")
    if not (0 < args.base_radius_fraction <= 0.5):
        raise SystemExit("--base-radius-fraction must be between 0 and 0.5")
    if args.min_component_voxels < 1:
        raise SystemExit("--min-component-voxels must be at least 1")
    if args.color_smoothing_passes < 0:
        raise SystemExit("--color-smoothing-passes cannot be negative")

    output = Path(args.output).expanduser().resolve()
    pattern_path, reference = resolve_pattern(args.pattern, output.parent / "source")
    config = PatternConfig(
        color_mode=args.color_mode,
        palette=args.palette,
        max_colors=args.max_colors,
        pattern_period_mm=args.pattern_period_mm,
        scale=args.scale,
        source_scale=args.source_scale,
        seam_angle_deg=args.seam_angle_deg,
        detail_mm=args.detail_mm,
        crop_fraction=args.crop_fraction,
        seed=args.seed,
        geometry_mode=args.geometry_mode,
        voxel_mm=args.voxel_mm,
        base_height_mm=args.base_height_mm,
        base_radius_fraction=args.base_radius_fraction,
        min_component_voxels=args.min_component_voxels,
        color_smoothing_passes=args.color_smoothing_passes,
    )
    manifest = build_patterned_glb(
        Path(args.input_stl).expanduser().resolve(),
        pattern_path,
        output,
        config,
        source_reference=reference,
    )
    if args.preview_dir:
        render_previews(output, Path(args.preview_dir).expanduser().resolve(), args.blender)
    print(output)
    print(output.with_suffix(".manifest.json"))
    print(f"physical extents: {manifest['physical_extents_mm']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
