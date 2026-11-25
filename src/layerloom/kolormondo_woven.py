#!/usr/bin/env python3
"""
Generate a printable, LayerLoom-ready sliceform color globe.

The mechanical topology is reconstructed from the Kolormondo Mini's published
product material and US 8,681,171: five horizontal discs interlock with six
semi-circular meridian wedges.  Exact Mini dielines and its proprietary CMYK
table are not public, so all fabrication tolerances and the 19 x 6 x 5 color
sampling in this module are explicit, parametric design decisions.
"""

from __future__ import annotations

import argparse
import colorsys
import csv
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import trimesh
from shapely import affinity
from shapely.geometry import GeometryCollection, MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union

try:
    from layerloom.export import LeafMeshSpec, write_metadata_assembly_3mf
    from layerloom.palette_assignment import load_palette_entries, nearest_palette_entry
    from layerloom.polygon_primitives import (
        assembled_disc_transform,
        assembled_wedge_transform,
        clean_polygonal,
        extrude_polygonal,
        iter_polygons,
        make_disc_profile,
        make_wedge_profile,
        meridian_cell,
        transformed_copy,
    )
except Exception:
    from export import LeafMeshSpec, write_metadata_assembly_3mf
    from palette_assignment import load_palette_entries, nearest_palette_entry
    from polygon_primitives import (
        assembled_disc_transform,
        assembled_wedge_transform,
        clean_polygonal,
        extrude_polygonal,
        iter_polygons,
        make_disc_profile,
        make_wedge_profile,
        meridian_cell,
        transformed_copy,
    )


OFFICIAL_PRODUCT_URL = "https://kolormondo.com/products/mini-colour-globe-by-kolormondo"
OFFICIAL_BUILD_URL = "https://kolormondo.com/pages/about-us-1"
PATENT_URL = "https://patents.google.com/patent/US8681171B2/en"
ASSEMBLY_MANUAL_URL = (
    "https://www.betapedagog.se/pub_docs/files/Smakprover/"
    "KM_F%C3%A4rgboken_Version1c_120831.pdf"
)


@dataclass(frozen=True)
class GlobeConfig:
    diameter_mm: float = 120.0
    plate_thickness_mm: float = 2.0
    slot_clearance_mm: float = 0.25
    axis_clearance_mm: float = 0.25
    slot_split_fraction: float = 0.50
    slot_mouth_flare_mm: float = 0.30
    slot_lead_in_mm: float = 2.0
    level_fractions: Tuple[float, ...] = (-0.8, -0.4, 0.0, 0.4, 0.8)
    wedge_count: int = 6
    hue_cells: int = 19
    chroma_cells: int = 6
    palette: str = "Normal"
    plate_width_mm: float = 256.0
    plate_depth_mm: float = 256.0
    plate_margin_mm: float = 5.0
    part_gap_mm: float = 8.0
    circle_segments: int = 192
    round_segments: int = 10

    @property
    def radius_mm(self) -> float:
        return self.diameter_mm * 0.5

    @property
    def slot_width_mm(self) -> float:
        return self.plate_thickness_mm + self.slot_clearance_mm

    @property
    def axis_relief_mm(self) -> float:
        effective_width = self.plate_thickness_mm + self.axis_clearance_mm
        return math.sqrt(3.0) * 0.5 * effective_width

    @property
    def levels_mm(self) -> Tuple[float, ...]:
        return tuple(self.radius_mm * value for value in self.level_fractions)

    @property
    def disc_radii_mm(self) -> Tuple[float, ...]:
        radius = self.radius_mm
        return tuple(math.sqrt(max(0.0, radius * radius - z * z)) for z in self.levels_mm)

    @property
    def wedge_angles_deg(self) -> Tuple[float, ...]:
        return tuple(360.0 * index / self.wedge_count for index in range(self.wedge_count))

    def validate(self) -> None:
        if self.diameter_mm <= 0.0:
            raise ValueError("diameter_mm must be > 0")
        if self.plate_thickness_mm <= 0.0:
            raise ValueError("plate_thickness_mm must be > 0")
        if self.slot_clearance_mm <= 0.0:
            raise ValueError("slot_clearance_mm must be > 0 for rigid FDM plates")
        if self.axis_clearance_mm < 0.0:
            raise ValueError("axis_clearance_mm must be >= 0")
        if self.wedge_count < 3:
            raise ValueError("wedge_count must be >= 3")
        if len(self.level_fractions) != 5:
            raise ValueError("this reconstruction expects five latitude levels")
        if any(abs(value) >= 1.0 for value in self.level_fractions):
            raise ValueError("level fractions must lie strictly inside the sphere")
        if tuple(sorted(self.level_fractions)) != self.level_fractions:
            raise ValueError("level_fractions must be strictly increasing")
        if self.hue_cells < 3 or self.chroma_cells < 2:
            raise ValueError("hue_cells >= 3 and chroma_cells >= 2 are required")


@dataclass
class ColorCell:
    label: str
    physical_part_id: str
    family: str
    mesh: trimesh.Trimesh
    geometry_2d: Polygon | MultiPolygon | GeometryCollection
    token: str
    source_hex: str
    target_hex: str
    metadata: Dict[str, str]

    def leaf_spec(self, transform: Optional[np.ndarray] = None, **extra: object) -> LeafMeshSpec:
        mesh = self.mesh if transform is None else transformed_copy(self.mesh, transform)
        metadata = dict(self.metadata)
        metadata.update({str(key): str(value) for key, value in extra.items()})
        return LeafMeshSpec(self.label, mesh, metadata)


@dataclass
class PhysicalPart:
    part_id: str
    family: str
    index: int
    profile: Polygon | MultiPolygon | GeometryCollection
    mesh: trimesh.Trimesh
    cells: List[ColorCell]
    assembly_transform: np.ndarray
    sphere_radius_mm: float
    level_z_mm: Optional[float] = None
    angle_deg: Optional[float] = None
    print_plate: int = 0
    print_transform: np.ndarray = field(default_factory=lambda: np.eye(4, dtype=np.float64))

    @property
    def bounds_2d(self) -> Tuple[float, float, float, float]:
        return tuple(float(value) for value in self.profile.bounds)


@dataclass(frozen=True)
class FitReport:
    checked_pairs: int
    colliding_pairs: Tuple[Tuple[str, str, float], ...]
    minimum_clearance_mm: float


def _safe_token(value: object) -> str:
    token = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    return token or "cmy"


def _rgb_to_hex(rgb: Sequence[float]) -> str:
    values = [max(0, min(255, int(round(float(channel) * 255.0)))) for channel in rgb]
    return "#{:02x}{:02x}{:02x}".format(*values)


def _color_target(hue_deg: float, chroma: float, level_fraction: float) -> str:
    """
    Build a pedagogical hue/chroma/value target before palette quantization.

    The neutral axis follows 10% gray at z/R=-0.8, 50% at the equator, and
    90% at z/R=+0.8.  Outer colors tint toward white or shade toward black.
    """
    chroma = max(0.0, min(1.0, float(chroma)))
    level_fraction = max(-1.0, min(1.0, float(level_fraction)))
    pure = np.asarray(colorsys.hsv_to_rgb((hue_deg % 360.0) / 360.0, 1.0, 1.0))
    if level_fraction >= 0.0:
        toned = (1.0 - level_fraction) * pure + level_fraction
    else:
        toned = (1.0 + level_fraction) * pure
    neutral = np.full(3, 0.5 + 0.5 * level_fraction)
    return _rgb_to_hex((1.0 - chroma) * neutral + chroma * toned)


class PaletteMatcher:
    def __init__(self, palette: str):
        self.entries = load_palette_entries(palette)
        if not self.entries:
            raise ValueError(f"palette {palette!r} contains no usable entries")
        self.cache: Dict[str, Dict[str, Any]] = {}

    def match(self, target_hex: str) -> Dict[str, Any]:
        key = target_hex.lower()
        if key not in self.cache:
            entry = nearest_palette_entry(self.entries, key)
            if entry is None:
                raise ValueError(f"could not match target color {target_hex}")
            self.cache[key] = dict(entry)
        return dict(self.cache[key])


def _cell_metadata(
    *,
    part_id: str,
    family: str,
    token: str,
    source_hex: str,
    target_hex: str,
    fields: Mapping[str, object],
) -> Dict[str, str]:
    metadata = {
        "physical_part_id": part_id,
        "part_family": family,
        "stack_token": token,
        "source_hex": source_hex,
        "target_hex": target_hex,
        "color_model": "reconstructed_hue_chroma_value",
    }
    metadata.update({str(key): str(value) for key, value in fields.items()})
    return metadata


def _partition_error(
    profile: Polygon | MultiPolygon | GeometryCollection,
    cell_geometries: Iterable[Polygon | MultiPolygon | GeometryCollection],
) -> float:
    unioned = unary_union([geometry for geometry in cell_geometries if not geometry.is_empty])
    return float(profile.symmetric_difference(unioned).area)


def _build_disc(
    *,
    config: GlobeConfig,
    matcher: PaletteMatcher,
    index: int,
    level_z: float,
    radius: float,
) -> PhysicalPart:
    slot_angles = config.wedge_angles_deg
    profile = make_disc_profile(
        radius=radius,
        slot_angles_deg=slot_angles,
        slot_width=config.slot_width_mm,
        slot_split_fraction=config.slot_split_fraction,
        mouth_flare=config.slot_mouth_flare_mm,
        lead_in=config.slot_lead_in_mm,
        circle_segments=config.circle_segments,
        round_segments=config.round_segments,
    )
    sign = "m" if level_z < -1e-9 else ("p" if level_z > 1e-9 else "z")
    part_id = f"disc_{index + 1:02d}_{sign}{abs(level_z):05.1f}"
    mesh = extrude_polygonal(
        profile,
        height=config.plate_thickness_mm,
        label=part_id,
    )

    cells: List[ColorCell] = []
    circle_quads = max(16, config.circle_segments // 4)
    sector_radius = radius * 1.10
    sectors = []
    for hue_index in range(config.hue_cells):
        start = 2.0 * math.pi * hue_index / config.hue_cells
        end = 2.0 * math.pi * (hue_index + 1) / config.hue_cells
        sectors.append(
            Polygon(
                [
                    (0.0, 0.0),
                    (sector_radius * math.cos(start), sector_radius * math.sin(start)),
                    (sector_radius * math.cos(end), sector_radius * math.sin(end)),
                ]
            )
        )
    rings = []
    for chroma_index in range(config.chroma_cells):
        inner = radius * chroma_index / config.chroma_cells
        outer = radius * (chroma_index + 1) / config.chroma_cells
        outer_disc = Point(0.0, 0.0).buffer(outer, quad_segs=circle_quads)
        if inner <= 1e-12:
            rings.append(outer_disc)
        else:
            inner_disc = Point(0.0, 0.0).buffer(inner, quad_segs=circle_quads)
            rings.append(outer_disc.difference(inner_disc))

    for hue_index in range(config.hue_cells):
        start = 2.0 * math.pi * hue_index / config.hue_cells
        end = 2.0 * math.pi * (hue_index + 1) / config.hue_cells
        physical_angle_deg = math.degrees((start + end) * 0.5)
        hue_deg = (physical_angle_deg + 300.0) % 360.0
        for chroma_index in range(config.chroma_cells):
            region = sectors[hue_index].intersection(rings[chroma_index])
            clipped = clean_polygonal(profile.intersection(region))
            if clipped.is_empty:
                continue
            chroma = chroma_index / (config.chroma_cells - 1)
            target_hex = _color_target(hue_deg, chroma, level_z / config.radius_mm)
            entry = matcher.match(target_hex)
            token = _safe_token(entry["token"])
            source_hex = str(entry["hex"]).lower()
            label = (
                f"{part_id}_h{hue_index:02d}_c{chroma_index:02d}"
                f"__PAT_{token}__"
            )
            cell_mesh = extrude_polygonal(
                clipped,
                height=config.plate_thickness_mm,
                label=label,
            )
            metadata = _cell_metadata(
                part_id=part_id,
                family="disc",
                token=token,
                source_hex=source_hex,
                target_hex=target_hex,
                fields={
                    "disc_index": index,
                    "level_z_mm": f"{level_z:.6f}",
                    "level_fraction": f"{level_z / config.radius_mm:.6f}",
                    "hue_index": hue_index,
                    "hue_deg": f"{hue_deg:.6f}",
                    "chroma_index": chroma_index,
                    "chroma_fraction": f"{chroma:.6f}",
                },
            )
            cells.append(
                ColorCell(
                    label=label,
                    physical_part_id=part_id,
                    family="disc",
                    mesh=cell_mesh,
                    geometry_2d=clipped,
                    token=token,
                    source_hex=source_hex,
                    target_hex=target_hex,
                    metadata=metadata,
                )
            )

    error = _partition_error(profile, (cell.geometry_2d for cell in cells))
    if error > 1e-3:
        raise ValueError(f"{part_id} color partition misses {error:.6g} mm²")

    return PhysicalPart(
        part_id=part_id,
        family="disc",
        index=index,
        profile=profile,
        mesh=mesh,
        cells=cells,
        assembly_transform=assembled_disc_transform(level_z, config.plate_thickness_mm),
        sphere_radius_mm=config.radius_mm,
        level_z_mm=level_z,
    )


def _value_boundaries(config: GlobeConfig) -> Tuple[float, ...]:
    levels = config.levels_mm
    mids = tuple((a + b) * 0.5 for a, b in zip(levels, levels[1:]))
    return (-config.radius_mm, *mids, config.radius_mm)


def _build_wedge(
    *,
    config: GlobeConfig,
    matcher: PaletteMatcher,
    index: int,
    angle_deg: float,
    shared_profile: Polygon | MultiPolygon | GeometryCollection,
) -> PhysicalPart:
    part_id = f"wedge_{index + 1:02d}_a{angle_deg:03.0f}"
    mesh = extrude_polygonal(
        shared_profile,
        height=config.plate_thickness_mm,
        label=part_id,
    )
    boundaries = _value_boundaries(config)
    cumulative_chroma: List[Polygon | MultiPolygon | GeometryCollection] = [
        GeometryCollection()
    ]
    for boundary_index in range(1, config.chroma_cells):
        cumulative_chroma.append(
            meridian_cell(
                sphere_radius=config.radius_mm,
                z_min=-config.radius_mm,
                z_max=config.radius_mm,
                chroma_min=0.0,
                chroma_max=boundary_index / config.chroma_cells,
                z_steps=config.circle_segments,
            )
        )
    cumulative_chroma.append(
        box(
            0.0,
            -config.radius_mm - 1.0,
            config.radius_mm + 1.0,
            config.radius_mm + 1.0,
        )
    )
    chroma_bands = [
        clean_polygonal(cumulative_chroma[index + 1].difference(cumulative_chroma[index]))
        for index in range(config.chroma_cells)
    ]
    hue_deg = (angle_deg + 300.0) % 360.0
    cells: List[ColorCell] = []
    for value_index, level_z in enumerate(config.levels_mm):
        z_min = boundaries[value_index]
        z_max = boundaries[value_index + 1]
        value_strip = box(
            0.0,
            z_min,
            config.radius_mm + 1.0,
            z_max,
        )
        for chroma_index in range(config.chroma_cells):
            region = value_strip.intersection(chroma_bands[chroma_index])
            clipped = clean_polygonal(shared_profile.intersection(region))
            if clipped.is_empty:
                continue
            chroma = chroma_index / (config.chroma_cells - 1)
            target_hex = _color_target(hue_deg, chroma, level_z / config.radius_mm)
            entry = matcher.match(target_hex)
            token = _safe_token(entry["token"])
            source_hex = str(entry["hex"]).lower()
            label = (
                f"{part_id}_v{value_index:02d}_c{chroma_index:02d}"
                f"__PAT_{token}__"
            )
            cell_mesh = extrude_polygonal(
                clipped,
                height=config.plate_thickness_mm,
                label=label,
            )
            metadata = _cell_metadata(
                part_id=part_id,
                family="wedge",
                token=token,
                source_hex=source_hex,
                target_hex=target_hex,
                fields={
                    "wedge_index": index,
                    "wedge_angle_deg": f"{angle_deg:.6f}",
                    "value_index": value_index,
                    "level_z_mm": f"{level_z:.6f}",
                    "level_fraction": f"{level_z / config.radius_mm:.6f}",
                    "hue_deg": f"{hue_deg:.6f}",
                    "chroma_index": chroma_index,
                    "chroma_fraction": f"{chroma:.6f}",
                },
            )
            cells.append(
                ColorCell(
                    label=label,
                    physical_part_id=part_id,
                    family="wedge",
                    mesh=cell_mesh,
                    geometry_2d=clipped,
                    token=token,
                    source_hex=source_hex,
                    target_hex=target_hex,
                    metadata=metadata,
                )
            )

    error = _partition_error(shared_profile, (cell.geometry_2d for cell in cells))
    if error > 1e-3:
        raise ValueError(f"{part_id} color partition misses {error:.6g} mm²")

    return PhysicalPart(
        part_id=part_id,
        family="wedge",
        index=index,
        profile=shared_profile,
        mesh=mesh,
        cells=cells,
        assembly_transform=assembled_wedge_transform(angle_deg, config.plate_thickness_mm),
        sphere_radius_mm=config.radius_mm,
        angle_deg=angle_deg,
    )


def build_parts(config: GlobeConfig) -> List[PhysicalPart]:
    """Generate all eleven physical pieces and their color-cell meshes."""
    config.validate()
    matcher = PaletteMatcher(config.palette)
    parts = [
        _build_disc(
            config=config,
            matcher=matcher,
            index=index,
            level_z=level_z,
            radius=radius,
        )
        for index, (level_z, radius) in enumerate(zip(config.levels_mm, config.disc_radii_mm))
    ]
    wedge_profile = make_wedge_profile(
        radius=config.radius_mm,
        axis_relief=config.axis_relief_mm,
        level_z=config.levels_mm,
        local_radii=config.disc_radii_mm,
        slot_width=config.slot_width_mm,
        slot_split_fraction=config.slot_split_fraction,
        mouth_flare=config.slot_mouth_flare_mm,
        lead_in=config.slot_lead_in_mm,
        circle_segments=config.circle_segments,
        round_segments=config.round_segments,
    )
    parts.extend(
        _build_wedge(
            config=config,
            matcher=matcher,
            index=index,
            angle_deg=angle,
            shared_profile=wedge_profile,
        )
        for index, angle in enumerate(config.wedge_angles_deg)
    )
    assign_print_layout(parts, config)
    return parts


def _translation(x: float, y: float, z: float = 0.0) -> np.ndarray:
    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, 3] = [float(x), float(y), float(z)]
    return matrix


def assign_print_layout(parts: Sequence[PhysicalPart], config: GlobeConfig) -> None:
    """Pack four discs, one disc, and six wedges onto three 256 mm plates."""
    discs = [part for part in parts if part.family == "disc"]
    wedges = [part for part in parts if part.family == "wedge"]
    if len(discs) != 5 or len(wedges) != config.wedge_count:
        raise ValueError("unexpected physical part count")

    by_index = {part.index: part for part in discs}
    margin = config.plate_margin_mm
    gap = config.part_gap_mm

    # Plate 1: equator + one medium + both small discs.
    placements = {
        2: (margin + config.disc_radii_mm[2], margin + config.disc_radii_mm[2]),
        1: (
            margin + 2.0 * config.disc_radii_mm[2] + gap + config.disc_radii_mm[1],
            margin + config.disc_radii_mm[1],
        ),
        0: (
            margin + config.disc_radii_mm[0],
            margin + 2.0 * config.disc_radii_mm[2] + gap + config.disc_radii_mm[0],
        ),
        4: (
            margin + 3.0 * config.disc_radii_mm[0] + gap,
            margin + 2.0 * config.disc_radii_mm[2] + gap + config.disc_radii_mm[4],
        ),
    }
    for disc_index, (cx, cy) in placements.items():
        part = by_index[disc_index]
        part.print_plate = 1
        part.print_transform = _translation(cx, cy)

    # Plate 2: the remaining medium disc.
    remaining = by_index[3]
    remaining.print_plate = 2
    remaining.print_transform = _translation(
        config.plate_width_mm * 0.5,
        config.plate_depth_mm * 0.5,
    )

    # Plate 3: a 3 x 2 grid of identical wedge bounding boxes.
    min_x, min_y, max_x, max_y = wedges[0].bounds_2d
    width = max_x - min_x
    height = max_y - min_y
    col_gap = max(5.0, gap)
    row_gap = config.plate_depth_mm - 2.0 * margin - 2.0 * height
    if row_gap < 4.0:
        raise ValueError(
            f"wedge rows do not fit plate depth {config.plate_depth_mm:g} mm "
            f"(computed row gap {row_gap:g} mm)"
        )
    for index, part in enumerate(wedges):
        col = index % 3
        row = index // 3
        tx = margin + col * (width + col_gap) - min_x
        ty = margin + row * (height + row_gap) - min_y
        part.print_plate = 3
        part.print_transform = _translation(tx, ty)

    for part in parts:
        transformed = transformed_copy(part.mesh, part.print_transform)
        lower, upper = transformed.bounds[:, :2]
        if np.any(lower < -1e-6) or upper[0] > config.plate_width_mm + 1e-6 or upper[1] > config.plate_depth_mm + 1e-6:
            raise ValueError(
                f"{part.part_id} exceeds plate {part.print_plate}: "
                f"bounds={lower.tolist()}..{upper.tolist()}"
            )


def _representative_color(part: PhysicalPart) -> str:
    if part.family == "disc":
        fraction = float(part.level_z_mm or 0.0) / part.sphere_radius_mm
        gray = max(0.05, min(0.95, 0.5 + 0.5 * fraction))
        return _rgb_to_hex((gray, gray, gray))
    return _color_target(float(part.angle_deg or 0.0) + 300.0, 1.0, 0.0)


def _solid_leaf(part: PhysicalPart, transform: np.ndarray, **extra: object) -> LeafMeshSpec:
    color = _representative_color(part)
    metadata = {
        "physical_part_id": part.part_id,
        "part_family": part.family,
        "source_hex": color,
        "mechanical_solid": "true",
    }
    metadata.update({str(key): str(value) for key, value in extra.items()})
    return LeafMeshSpec(part.part_id, transformed_copy(part.mesh, transform), metadata)


def _export_stls(parts: Sequence[PhysicalPart], output_dir: Path) -> None:
    stl_dir = output_dir / "mechanical_stl"
    stl_dir.mkdir(parents=True, exist_ok=True)
    for part in parts:
        part.mesh.export(stl_dir / f"{part.part_id}.stl")


def _fit_coupon_profiles(
    config: GlobeConfig,
) -> List[Tuple[str, Polygon | MultiPolygon | GeometryCollection, Dict[str, str]]]:
    profiles: List[Tuple[str, Polygon | MultiPolygon | GeometryCollection, Dict[str, str]]] = []
    tongue = box(0.0, 0.0, 28.0, 10.0)
    profiles.append(
        (
            "fit_tongue_nominal",
            tongue,
            {"kind": "tongue", "thickness_mm": f"{config.plate_thickness_mm:.3f}"},
        )
    )
    for clearance in (
        max(0.05, config.slot_clearance_mm - 0.10),
        config.slot_clearance_mm,
        config.slot_clearance_mm + 0.10,
    ):
        width = config.plate_thickness_mm + clearance
        center_x = 12.0
        root_y = 8.0
        receiver = box(0.0, 0.0, 24.0, 20.0)
        slot = unary_union(
            [
                box(center_x - width * 0.5, root_y, center_x + width * 0.5, 21.0),
                Point(center_x, root_y).buffer(width * 0.5, quad_segs=8),
            ]
        )
        profile = clean_polygonal(receiver.difference(slot))
        suffix = int(round(clearance * 100.0))
        profiles.append(
            (
                f"fit_receiver_clearance_{suffix:02d}",
                profile,
                {
                    "kind": "receiver",
                    "clearance_mm": f"{clearance:.3f}",
                    "slot_width_mm": f"{width:.3f}",
                },
            )
        )
    return profiles


def _export_fit_coupons(config: GlobeConfig, output_dir: Path) -> None:
    coupon_dir = output_dir / "fit_coupons"
    coupon_dir.mkdir(parents=True, exist_ok=True)
    specs: List[LeafMeshSpec] = []
    x_cursor = 5.0
    for label, profile, metadata in _fit_coupon_profiles(config):
        mesh = extrude_polygonal(
            profile,
            height=config.plate_thickness_mm,
            label=label,
        )
        mesh.export(coupon_dir / f"{label}.stl")
        placed = transformed_copy(mesh, _translation(x_cursor - profile.bounds[0], 5.0 - profile.bounds[1]))
        specs.append(
            LeafMeshSpec(
                label,
                placed,
                {
                    **metadata,
                    "physical_part_id": label,
                    "source_hex": "#808080",
                },
            )
        )
        x_cursor += profile.bounds[2] - profile.bounds[0] + 8.0
    write_metadata_assembly_3mf(
        specs,
        str(coupon_dir / "fit_coupon_set.3mf"),
        title="Kolormondo slot fit coupons",
        include_display_colors=True,
    )


def _export_3mf_files(parts: Sequence[PhysicalPart], output_dir: Path) -> None:
    assembled_solids = [
        _solid_leaf(part, part.assembly_transform, layout="assembled")
        for part in parts
    ]
    write_metadata_assembly_3mf(
        assembled_solids,
        str(output_dir / "assembled_mechanical_preview.3mf"),
        title="Woven color globe mechanical assembly",
        validate_solids=True,
    )

    assembled_cells = [
        cell.leaf_spec(
            part.assembly_transform,
            layout="assembled",
            print_orientation_warning="preview_only",
        )
        for part in parts
        for cell in part.cells
    ]
    write_metadata_assembly_3mf(
        assembled_cells,
        str(output_dir / "assembled_color_preview.3mf"),
        title="Woven color globe cell assembly",
        include_display_colors=True,
    )

    for plate_index in (1, 2, 3):
        plate_parts = [part for part in parts if part.print_plate == plate_index]
        solid_specs = [
            _solid_leaf(
                part,
                part.print_transform,
                layout="flat_print",
                print_plate=plate_index,
            )
            for part in plate_parts
        ]
        write_metadata_assembly_3mf(
            solid_specs,
            str(output_dir / f"plate_{plate_index:02d}_mechanical.3mf"),
            title=f"Woven color globe mechanical plate {plate_index}",
            validate_solids=True,
        )

        cell_specs = [
            cell.leaf_spec(
                part.print_transform,
                layout="flat_print",
                print_plate=plate_index,
                weave_visibility_warning="broad_faces_show_final_layer_when_printed_flat",
            )
            for part in plate_parts
            for cell in part.cells
        ]
        write_metadata_assembly_3mf(
            cell_specs,
            str(output_dir / f"plate_{plate_index:02d}_woven_source.3mf"),
            title=f"Woven color globe LayerLoom source plate {plate_index}",
            include_display_colors=True,
        )


def _write_cells_csv(parts: Sequence[PhysicalPart], output_dir: Path) -> None:
    fieldnames = [
        "label",
        "physical_part_id",
        "family",
        "token",
        "source_hex",
        "target_hex",
        "area_mm2",
        "volume_mm3",
        "print_plate",
        "metadata_json",
    ]
    with open(output_dir / "color_cells.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for part in parts:
            for cell in part.cells:
                writer.writerow(
                    {
                        "label": cell.label,
                        "physical_part_id": cell.physical_part_id,
                        "family": cell.family,
                        "token": cell.token,
                        "source_hex": cell.source_hex,
                        "target_hex": cell.target_hex,
                        "area_mm2": f"{float(cell.geometry_2d.area):.8f}",
                        "volume_mm3": f"{abs(float(cell.mesh.volume)):.8f}",
                        "print_plate": part.print_plate,
                        "metadata_json": json.dumps(cell.metadata, sort_keys=True),
                    }
                )


def _part_manifest(part: PhysicalPart) -> Dict[str, object]:
    min_x, min_y, max_x, max_y = part.bounds_2d
    return {
        "part_id": part.part_id,
        "family": part.family,
        "index": part.index,
        "level_z_mm": part.level_z_mm,
        "angle_deg": part.angle_deg,
        "profile_area_mm2": float(part.profile.area),
        "volume_mm3": abs(float(part.mesh.volume)),
        "bounds_2d_mm": [min_x, min_y, max_x, max_y],
        "cell_count": len(part.cells),
        "watertight": bool(part.mesh.is_watertight),
        "is_volume": bool(part.mesh.is_volume),
        "print_plate": part.print_plate,
        "print_transform": np.asarray(part.print_transform).tolist(),
        "assembly_transform": np.asarray(part.assembly_transform).tolist(),
    }


def _write_manifest(
    config: GlobeConfig,
    parts: Sequence[PhysicalPart],
    output_dir: Path,
    fit_report: Optional[FitReport],
) -> None:
    disc_cell_count = sum(len(part.cells) for part in parts if part.family == "disc")
    wedge_cell_count = sum(len(part.cells) for part in parts if part.family == "wedge")
    data = {
        "design": "LayerLoom woven sliceform color globe",
        "status": "parametric reconstruction; exact commercial dielines unavailable",
        "config": asdict(config),
        "derived": {
            "radius_mm": config.radius_mm,
            "levels_mm": list(config.levels_mm),
            "disc_radii_mm": list(config.disc_radii_mm),
            "disc_diameters_mm": [2.0 * value for value in config.disc_radii_mm],
            "slot_width_mm": config.slot_width_mm,
            "axis_relief_mm": config.axis_relief_mm,
            "disc_color_cells": disc_cell_count,
            "wedge_cross_section_cells": wedge_cell_count,
            "total_leaf_meshes": disc_cell_count + wedge_cell_count,
        },
        "research_sources": {
            "official_product": OFFICIAL_PRODUCT_URL,
            "official_assembly": OFFICIAL_BUILD_URL,
            "patent_schematics": PATENT_URL,
            "historical_assembly_manual": ASSEMBLY_MANUAL_URL,
        },
        "confirmed_facts": [
            "120 mm assembled diameter",
            "five circular pieces and six wedge pieces",
            "flat-packed, slotted, rebuildable sliceform construction",
            "white north pole, black south pole, chromatic equator, neutral center axis",
        ],
        "reconstruction_assumptions": [
            "latitude centers at z/R = -0.8, -0.4, 0, 0.4, 0.8",
            "complementary slot depths split at one half local radius",
            "19 hue by 6 chroma by 5 value disc sampling to make 570 color cells",
            "nearest LayerLoom Normal-palette recipes replace the unpublished commercial CMYK values",
            "finite-thickness axis relief replaces the flexible-cardstock convergence at the center",
        ],
        "fit_report": asdict(fit_report) if fit_report else None,
        "parts": [_part_manifest(part) for part in parts],
    }
    with open(output_dir / "manifest.json", "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)


def _svg_path(geometry: Polygon | MultiPolygon | GeometryCollection) -> str:
    commands: List[str] = []
    for polygon in iter_polygons(geometry):
        for ring in [polygon.exterior, *polygon.interiors]:
            coords = list(ring.coords)
            if not coords:
                continue
            commands.append(f"M {coords[0][0]:.5f},{-coords[0][1]:.5f}")
            commands.extend(f"L {x:.5f},{-y:.5f}" for x, y in coords[1:])
            commands.append("Z")
    return " ".join(commands)


def _write_dieline_svg(config: GlobeConfig, parts: Sequence[PhysicalPart], output_dir: Path) -> None:
    representatives = [
        next(part for part in parts if part.family == "disc" and part.index == index)
        for index in (2, 1, 0)
    ]
    representatives.append(next(part for part in parts if part.family == "wedge"))
    placements = [(70.0, 70.0), (210.0, 70.0), (325.0, 70.0), (415.0, 70.0)]
    labels = [
        "Equator disc Ø120.00",
        f"Medium disc Ø{2 * config.disc_radii_mm[1]:.2f} (print 2)",
        f"Small disc Ø{2 * config.disc_radii_mm[0]:.2f} (print 2)",
        "Meridian wedge (print 6)",
    ]
    elements = []
    for part, (x, y), label in zip(representatives, placements, labels):
        moved = affinity.translate(part.profile, xoff=x, yoff=-y)
        elements.append(
            f'<path d="{_svg_path(moved)}" fill="none" stroke="#111" '
            f'stroke-width="0.25" fill-rule="evenodd"/>'
        )
        elements.append(
            f'<text x="{x:.3f}" y="{y + 72.0:.3f}" text-anchor="middle" '
            f'font-family="sans-serif" font-size="5">{label}</text>'
        )
    note = (
        f"t={config.plate_thickness_mm:.2f} mm; "
        f"slot={config.slot_width_mm:.2f} mm; "
        f"axis relief={config.axis_relief_mm:.2f} mm"
    )
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="500mm" height="165mm" '
        'viewBox="0 0 500 165">\n'
        '<rect width="500" height="165" fill="white"/>\n'
        + "\n".join(elements)
        + f'\n<text x="250" y="158" text-anchor="middle" font-family="sans-serif" '
        f'font-size="5">{note}</text>\n</svg>\n'
    )
    (output_dir / "dielines.svg").write_text(svg, encoding="utf-8")


def _render_previews(config: GlobeConfig, parts: Sequence[PhysicalPart], output_dir: Path) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.collections import PatchCollection
        from matplotlib.patches import Polygon as MplPolygon
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    except Exception as exc:
        print(f"[kolormondo] skipping PNG previews: {exc}")
        return

    # Dimensioned profile overview.
    fig, axes = plt.subplots(1, 4, figsize=(15, 4.2))
    choices = [
        next(part for part in parts if part.family == "disc" and part.index == index)
        for index in (2, 1, 0)
    ]
    choices.append(next(part for part in parts if part.family == "wedge"))
    titles = ["Equator Ø120", "Medium Ø109.98", "Small Ø72", "Meridian wedge"]
    for axis, part, title in zip(axes, choices, titles):
        for polygon in iter_polygons(part.profile):
            xy = np.asarray(polygon.exterior.coords)
            axis.fill(xy[:, 0], xy[:, 1], color="#dce8f4", edgecolor="#1f3349", linewidth=0.8)
            for hole in polygon.interiors:
                hxy = np.asarray(hole.coords)
                axis.fill(hxy[:, 0], hxy[:, 1], color="white")
        axis.set_title(title)
        axis.set_aspect("equal", "box")
        axis.axis("off")
    fig.suptitle(
        f"Sliceform profiles — t={config.plate_thickness_mm:.2f} mm, "
        f"slot={config.slot_width_mm:.2f} mm, axis relief={config.axis_relief_mm:.2f} mm"
    )
    fig.tight_layout()
    fig.savefig(output_dir / "profiles_preview.png", dpi=220, bbox_inches="tight")
    plt.close(fig)

    # Five reconstructed hue/chroma/value discs.
    disc_parts = [part for part in parts if part.family == "disc"]
    fig, axes = plt.subplots(1, 5, figsize=(17, 3.8))
    for axis, part in zip(axes, disc_parts):
        patches = []
        colors = []
        for cell in part.cells:
            for polygon in iter_polygons(cell.geometry_2d):
                patches.append(MplPolygon(np.asarray(polygon.exterior.coords), closed=True))
                colors.append(cell.source_hex)
        collection = PatchCollection(patches, facecolors=colors, edgecolors="#ffffff", linewidths=0.12)
        axis.add_collection(collection)
        min_x, min_y, max_x, max_y = part.bounds_2d
        axis.set_xlim(min_x - 2, max_x + 2)
        axis.set_ylim(min_y - 2, max_y + 2)
        axis.set_aspect("equal", "box")
        axis.set_title(f"z={part.level_z_mm:+.0f} mm")
        axis.axis("off")
    fig.suptitle("570 reconstructed disc cells, quantized to LayerLoom palette recipes")
    fig.tight_layout()
    fig.savefig(output_dir / "disc_color_map_preview.png", dpi=220, bbox_inches="tight")
    plt.close(fig)

    # 3-D mechanical assembly.
    fig = plt.figure(figsize=(8.2, 7.2))
    axis = fig.add_subplot(111, projection="3d")
    for part in parts:
        mesh = transformed_copy(part.mesh, part.assembly_transform)
        triangles = np.asarray(mesh.triangles)
        if len(triangles) > 1200:
            triangles = triangles[:: max(1, len(triangles) // 1200)]
        collection = Poly3DCollection(
            triangles,
            facecolor=_representative_color(part),
            edgecolor="none",
            linewidth=0.0,
            alpha=0.52 if part.family == "disc" else 0.76,
        )
        axis.add_collection3d(collection)
    radius = config.radius_mm * 1.08
    axis.set_xlim(-radius, radius)
    axis.set_ylim(-radius, radius)
    axis.set_zlim(-radius, radius)
    axis.set_box_aspect((1, 1, 1))
    axis.view_init(elev=24, azim=-50)
    axis.set_axis_off()
    axis.set_title("Assembled 11-piece rigid sliceform reconstruction")
    fig.tight_layout()
    fig.savefig(output_dir / "assembly_preview.png", dpi=240, bbox_inches="tight")
    plt.close(fig)


def validate_assembled_fit(
    parts: Sequence[PhysicalPart],
    *,
    intersection_tolerance_mm3: float = 1e-4,
    nominal_clearance_mm: float = 0.0,
) -> FitReport:
    """
    Use manifold booleans on overlapping AABBs to detect unintended volume.
    """
    assembled = [
        (part.part_id, transformed_copy(part.mesh, part.assembly_transform))
        for part in parts
    ]
    collisions: List[Tuple[str, str, float]] = []
    checked = 0
    for left_index, (left_name, left_mesh) in enumerate(assembled):
        for right_name, right_mesh in assembled[left_index + 1 :]:
            overlap_min = np.maximum(left_mesh.bounds[0], right_mesh.bounds[0])
            overlap_max = np.minimum(left_mesh.bounds[1], right_mesh.bounds[1])
            if np.any(overlap_max <= overlap_min):
                continue
            checked += 1
            try:
                result = trimesh.boolean.intersection(
                    [left_mesh, right_mesh],
                    engine="manifold",
                    check_volume=False,
                )
                volume = 0.0 if result is None or result.is_empty else abs(float(result.volume))
            except Exception:
                # A conservative fallback: report the AABB overlap volume so
                # validation cannot silently pass without a boolean engine.
                volume = float(np.prod(np.maximum(0.0, overlap_max - overlap_min)))
            if volume > intersection_tolerance_mm3:
                collisions.append((left_name, right_name, volume))
    return FitReport(
        checked_pairs=checked,
        colliding_pairs=tuple(collisions),
        minimum_clearance_mm=float(nominal_clearance_mm),
    )


def export_design(
    config: GlobeConfig,
    *,
    output_dir: Path,
    validate_collisions: bool = True,
    render_previews: bool = True,
) -> Tuple[List[PhysicalPart], Optional[FitReport]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    parts = build_parts(config)

    fit_report = (
        validate_assembled_fit(
            parts,
            nominal_clearance_mm=min(
                config.slot_clearance_mm,
                config.axis_clearance_mm,
            ),
        )
        if validate_collisions
        else None
    )
    if fit_report and fit_report.colliding_pairs:
        details = ", ".join(
            f"{left}/{right}={volume:.6g}mm³"
            for left, right, volume in fit_report.colliding_pairs[:8]
        )
        raise ValueError(f"assembled parts collide: {details}")

    _export_stls(parts, output_dir)
    _export_fit_coupons(config, output_dir)
    _export_3mf_files(parts, output_dir)
    _write_cells_csv(parts, output_dir)
    _write_dieline_svg(config, parts, output_dir)
    if render_previews:
        _render_previews(config, parts, output_dir)
    _write_manifest(config, parts, output_dir, fit_report)
    return parts, fit_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a printable, LayerLoom-ready 11-piece sliceform color globe."
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/kolormondo_woven_v1",
        help="Output directory (default: %(default)s)",
    )
    parser.add_argument("--diameter-mm", type=float, default=120.0)
    parser.add_argument("--thickness-mm", type=float, default=2.0)
    parser.add_argument("--slot-clearance-mm", type=float, default=0.25)
    parser.add_argument("--axis-clearance-mm", type=float, default=0.25)
    parser.add_argument("--palette", default="Normal")
    parser.add_argument("--skip-collision-check", action="store_true")
    parser.add_argument("--skip-previews", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config = GlobeConfig(
        diameter_mm=args.diameter_mm,
        plate_thickness_mm=args.thickness_mm,
        slot_clearance_mm=args.slot_clearance_mm,
        axis_clearance_mm=args.axis_clearance_mm,
        palette=args.palette,
    )
    parts, fit_report = export_design(
        config,
        output_dir=Path(args.output_dir),
        validate_collisions=not args.skip_collision_check,
        render_previews=not args.skip_previews,
    )
    cell_count = sum(len(part.cells) for part in parts)
    print(
        f"[kolormondo] generated {len(parts)} physical parts, {cell_count} color cells, "
        f"{len(fit_report.colliding_pairs) if fit_report else 'unchecked'} collisions"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
