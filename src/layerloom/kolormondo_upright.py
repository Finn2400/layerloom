#!/usr/bin/env python3
"""
Generate upright, fixture-supported LayerLoom sources for the color globe.

The flat mechanical generator in :mod:`layerloom.kolormondo_woven` remains the
source of truth for profiles, slots, and color cells.  This module rotates every
color-bearing broad face into a vertical plane before export:

* discs stand on their unslotted -90 degree rim tangent;
* wedges stand on their long, straight axial edge;
* a removable cradle or shoe stabilizes each part without touching its broad
  color faces;
* fixture bodies use ``__PAT_w__`` so a complete source bundle can pass through
  LayerLoom without silently receiving the default CMY recipe.

The generated source files are deliberately one physical part per print.  Tall,
thin color parts should not be printed simultaneously.
"""

from __future__ import annotations

import argparse
import colorsys
import csv
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import trimesh
from shapely.geometry import Polygon, box

try:
    from layerloom.export import LeafMeshSpec, write_metadata_assembly_3mf
    from layerloom.kolormondo_woven import (
        GlobeConfig,
        PhysicalPart,
        build_parts,
    )
    from layerloom.polygon_primitives import extrude_polygonal, transformed_copy
except Exception:
    from export import LeafMeshSpec, write_metadata_assembly_3mf
    from kolormondo_woven import GlobeConfig, PhysicalPart, build_parts
    from polygon_primitives import extrude_polygonal, transformed_copy


@dataclass(frozen=True)
class UprightConfig:
    """Fabrication settings for the removable upright-print fixtures."""

    layer_height_mm: float = 0.12
    plate_width_mm: float = 256.0
    plate_depth_mm: float = 256.0
    plate_center_x_mm: float = 128.0
    plate_center_y_mm: float = 128.0
    fixture_token: str = "w"
    fixture_hex: str = "#f5f5f5"
    fixture_base_height_mm: float = 0.80
    fixture_interface_gap_mm: float = 0.24
    fixture_face_clearance_mm: float = 0.35
    fixture_rail_half_depth_mm: float = 9.0
    fixture_wall_thickness_mm: float = 1.20
    fixture_tie_height_mm: float = 0.24
    fixture_tie_width_mm: float = 2.0
    disc_cradle_half_depth_mm: float = 1.15
    disc_cradle_fraction: float = 1.0 / math.sqrt(2.0)
    calibration_columns: int = 13
    calibration_cell_mm: float = 6.0

    def validate(self) -> None:
        if self.layer_height_mm <= 0.0:
            raise ValueError("layer_height_mm must be > 0")
        if self.fixture_base_height_mm <= 0.0:
            raise ValueError("fixture_base_height_mm must be > 0")
        if self.fixture_interface_gap_mm <= 0.0:
            raise ValueError("fixture_interface_gap_mm must be > 0")
        if self.fixture_face_clearance_mm <= 0.0:
            raise ValueError("fixture_face_clearance_mm must be > 0")
        if self.fixture_tie_height_mm <= 0.0:
            raise ValueError("fixture_tie_height_mm must be > 0")
        if len(self.fixture_token) != 1:
            raise ValueError("fixture_token must be one LayerLoom filament letter")
        if self.calibration_columns < 2 or self.calibration_cell_mm < 3.0:
            raise ValueError("calibration grid is too small")


@dataclass(frozen=True)
class FixtureComponent:
    label: str
    role: str
    mesh: trimesh.Trimesh


@dataclass
class UprightPart:
    physical_part: PhysicalPart
    transform: np.ndarray
    color_specs: List[LeafMeshSpec]
    fixture_components: List[FixtureComponent]

    @property
    def part_id(self) -> str:
        return self.physical_part.part_id

    @property
    def mechanical_mesh(self) -> trimesh.Trimesh:
        return transformed_copy(self.physical_part.mesh, self.transform)


@dataclass(frozen=True)
class UprightValidation:
    part_id: str
    color_leaf_count: int
    fixture_component_count: int
    part_bounds_mm: Tuple[Tuple[float, float, float], Tuple[float, float, float]]
    setup_bounds_mm: Tuple[Tuple[float, float, float], Tuple[float, float, float]]
    broad_face_normal: Tuple[float, float, float]
    broad_face_dot_build_z: float
    part_bed_contact: bool
    fixture_bed_contact: bool
    within_build_plate_xy: bool
    all_color_leaves_are_volumes: bool
    all_fixture_components_are_volumes: bool
    fixture_intrusion_mm3: float


def _box_mesh(
    *,
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    z_min: float,
    z_max: float,
) -> trimesh.Trimesh:
    extents = np.asarray(
        [x_max - x_min, y_max - y_min, z_max - z_min],
        dtype=np.float64,
    )
    if np.any(extents <= 0.0):
        raise ValueError(f"invalid box extents {extents.tolist()}")
    mesh = trimesh.creation.box(extents=extents)
    mesh.apply_translation(
        [
            0.5 * (x_min + x_max),
            0.5 * (y_min + y_max),
            0.5 * (z_min + z_max),
        ]
    )
    return mesh


def _fixture_component(
    part_id: str,
    role: str,
    index: int,
    mesh: trimesh.Trimesh,
) -> FixtureComponent:
    if not mesh.is_watertight or not mesh.is_volume:
        raise ValueError(
            f"invalid fixture solid {part_id}/{role}/{index}: "
            f"watertight={mesh.is_watertight}, volume={mesh.is_volume}"
        )
    return FixtureComponent(
        label=f"{part_id}__removable_fixture_{role}_{index:02d}",
        role=role,
        mesh=mesh,
    )


def disc_upright_transform(
    radius_mm: float,
    thickness_mm: float,
    *,
    center_x_mm: float,
    center_y_mm: float,
) -> np.ndarray:
    """
    Map local disc ``(x, y, thickness)`` to global ``(X, Z, -Y)``.

    The unslotted local -Y tangent lands at global Z=0 and the broad-face
    normal becomes global ±Y.
    """
    return np.asarray(
        [
            [1.0, 0.0, 0.0, center_x_mm],
            [0.0, 0.0, -1.0, center_y_mm + 0.5 * thickness_mm],
            [0.0, 1.0, 0.0, radius_mm],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )


def wedge_upright_transform(
    axis_relief_mm: float,
    thickness_mm: float,
    *,
    center_x_mm: float,
    center_y_mm: float,
) -> np.ndarray:
    """
    Put the wedge's long axial edge on Z=0.

    Local globe-Z becomes global X, local radial distance becomes global Z,
    and the broad-face normal becomes global ±Y.
    """
    return np.asarray(
        [
            [0.0, 1.0, 0.0, center_x_mm],
            [0.0, 0.0, -1.0, center_y_mm + 0.5 * thickness_mm],
            [1.0, 0.0, 0.0, -axis_relief_mm],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )


def _upright_transform(
    part: PhysicalPart,
    globe: GlobeConfig,
    upright: UprightConfig,
) -> np.ndarray:
    if part.family == "disc":
        radius = max(
            abs(float(part.profile.bounds[1])),
            abs(float(part.profile.bounds[3])),
        )
        return disc_upright_transform(
            radius,
            globe.plate_thickness_mm,
            center_x_mm=upright.plate_center_x_mm,
            center_y_mm=upright.plate_center_y_mm,
        )
    return wedge_upright_transform(
        globe.axis_relief_mm,
        globe.plate_thickness_mm,
        center_x_mm=upright.plate_center_x_mm,
        center_y_mm=upright.plate_center_y_mm,
    )


def _color_specs(
    part: PhysicalPart,
    transform: np.ndarray,
    upright: UprightConfig,
) -> List[LeafMeshSpec]:
    orientation = (
        "disc_on_unslotted_rim"
        if part.family == "disc"
        else "wedge_on_straight_axial_edge"
    )
    specs: List[LeafMeshSpec] = []
    for cell in part.cells:
        metadata = dict(cell.metadata)
        metadata.update(
            {
                "layout": "upright_color_weave",
                "print_orientation": orientation,
                "broad_faces_vertical": "true",
                "recommended_layer_height_mm": f"{upright.layer_height_mm:.3f}",
                "fixture_included_in_bundle": "true",
            }
        )
        specs.append(
            LeafMeshSpec(
                cell.label,
                transformed_copy(cell.mesh, transform),
                metadata,
            )
        )
    return specs


def _ramp_mesh(
    *,
    x_values: Sequence[float],
    top_values: Sequence[float],
    y_center: float,
    half_depth: float,
    label: str,
) -> trimesh.Trimesh:
    if len(x_values) < 2 or len(x_values) != len(top_values):
        raise ValueError("ramp samples are inconsistent")
    points = [(float(x_values[0]), 0.0)]
    points.extend(
        (float(x), float(z))
        for x, z in zip(x_values, top_values)
    )
    points.append((float(x_values[-1]), 0.0))
    profile = Polygon(points)
    local = extrude_polygonal(
        profile,
        height=2.0 * half_depth,
        label=label,
    )
    matrix = np.asarray(
        [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, y_center - half_depth],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    mesh = transformed_copy(local, matrix)
    if mesh.is_watertight and not mesh.is_volume:
        mesh.invert()
    if not mesh.is_watertight or not mesh.is_volume:
        raise ValueError(f"invalid cradle ramp {label}")
    return mesh


def _disc_fixture(
    part: PhysicalPart,
    globe: GlobeConfig,
    upright: UprightConfig,
) -> List[FixtureComponent]:
    part_id = part.part_id
    radius = max(
        abs(float(part.profile.bounds[1])),
        abs(float(part.profile.bounds[3])),
    )
    cx = upright.plate_center_x_mm
    cy = upright.plate_center_y_mm
    face = 0.5 * globe.plate_thickness_mm
    gap_y = upright.fixture_face_clearance_mm
    rail_depth = upright.fixture_rail_half_depth_mm
    base_h = upright.fixture_base_height_mm
    wall_t = upright.fixture_wall_thickness_mm
    x_cradle = radius * upright.disc_cradle_fraction
    outer_half_x = x_cradle + 4.0
    wall_half_x = x_cradle + 2.0
    inner_y = face + gap_y
    wall_height = base_h + (radius - math.sqrt(radius * radius - x_cradle * x_cradle)) + 3.0

    components: List[FixtureComponent] = []

    def add(role: str, mesh: trimesh.Trimesh) -> None:
        index = 1 + sum(component.role == role for component in components)
        components.append(_fixture_component(part_id, role, index, mesh))

    add(
        "base_rail",
        _box_mesh(
            x_min=cx - outer_half_x,
            x_max=cx + outer_half_x,
            y_min=cy + inner_y,
            y_max=cy + rail_depth,
            z_min=0.0,
            z_max=base_h,
        ),
    )
    add(
        "base_rail",
        _box_mesh(
            x_min=cx - outer_half_x,
            x_max=cx + outer_half_x,
            y_min=cy - rail_depth,
            y_max=cy - inner_y,
            z_min=0.0,
            z_max=base_h,
        ),
    )
    crossbar_w = 3.5
    for x_min, x_max in (
        (cx - outer_half_x, cx - outer_half_x + crossbar_w),
        (cx + outer_half_x - crossbar_w, cx + outer_half_x),
    ):
        add(
            "end_crossbar",
            _box_mesh(
                x_min=x_min,
                x_max=x_max,
                y_min=cy - rail_depth,
                y_max=cy + rail_depth,
                z_min=0.0,
                z_max=base_h,
            ),
        )
    add(
        "keeper_wall",
        _box_mesh(
            x_min=cx - wall_half_x,
            x_max=cx + wall_half_x,
            y_min=cy + inner_y,
            y_max=cy + inner_y + wall_t,
            z_min=base_h,
            z_max=wall_height,
        ),
    )
    add(
        "keeper_wall",
        _box_mesh(
            x_min=cx - wall_half_x,
            x_max=cx + wall_half_x,
            y_min=cy - inner_y - wall_t,
            y_max=cy - inner_y,
            z_min=base_h,
            z_max=wall_height,
        ),
    )

    # Two conformal ramps sit one interface layer below the lower circular rim.
    top_floor = 0.40
    contact_height = upright.fixture_interface_gap_mm + top_floor
    x_start = math.sqrt(max(0.0, 2.0 * radius * contact_height - contact_height**2))
    sample_count = 36
    positive_x = np.linspace(x_start, x_cradle, sample_count)

    def edge_height(x_value: float) -> float:
        return radius - math.sqrt(max(0.0, radius * radius - x_value * x_value))

    positive_top = [
        edge_height(float(x)) - upright.fixture_interface_gap_mm
        for x in positive_x
    ]
    negative_x = [-float(x) for x in reversed(positive_x)]
    negative_top = list(reversed(positive_top))
    add(
        "conformal_cradle",
        _ramp_mesh(
            x_values=[cx + float(x) for x in negative_x],
            top_values=negative_top,
            y_center=cy,
            half_depth=upright.disc_cradle_half_depth_mm,
            label=f"{part_id}_left_cradle",
        ),
    )
    add(
        "conformal_cradle",
        _ramp_mesh(
            x_values=[cx + float(x) for x in positive_x],
            top_values=positive_top,
            y_center=cy,
            half_depth=upright.disc_cradle_half_depth_mm,
            label=f"{part_id}_right_cradle",
        ),
    )

    # First-layer ties connect the part to alternate keeper rails.  They touch
    # the broad face at a tiny edge region and snap away with the fixture.
    tie_h = upright.fixture_tie_height_mm
    tie_w = upright.fixture_tie_width_mm
    for tie_index, x_offset in enumerate((-2.5, 2.5)):
        if tie_index % 2 == 0:
            y_min, y_max = cy + face, cy + inner_y
        else:
            y_min, y_max = cy - inner_y, cy - face
        add(
            "breakaway_tie",
            _box_mesh(
                x_min=cx + x_offset - 0.5 * tie_w,
                x_max=cx + x_offset + 0.5 * tie_w,
                y_min=y_min,
                y_max=y_max,
                z_min=0.0,
                z_max=tie_h,
            ),
        )
    return components


def _straight_edge_fixture(
    *,
    part_id: str,
    x_min: float,
    x_max: float,
    tie_centers: Sequence[float],
    globe: GlobeConfig,
    upright: UprightConfig,
    wall_height: float = 8.0,
) -> List[FixtureComponent]:
    cx = upright.plate_center_x_mm
    cy = upright.plate_center_y_mm
    face = 0.5 * globe.plate_thickness_mm
    inner_y = face + upright.fixture_face_clearance_mm
    rail_depth = upright.fixture_rail_half_depth_mm
    base_h = upright.fixture_base_height_mm
    wall_t = upright.fixture_wall_thickness_mm
    outer_pad = 4.0
    outer_min = x_min - outer_pad
    outer_max = x_max + outer_pad
    components: List[FixtureComponent] = []

    def add(role: str, mesh: trimesh.Trimesh) -> None:
        index = 1 + sum(component.role == role for component in components)
        components.append(_fixture_component(part_id, role, index, mesh))

    for y_min, y_max in (
        (cy + inner_y, cy + rail_depth),
        (cy - rail_depth, cy - inner_y),
    ):
        add(
            "base_rail",
            _box_mesh(
                x_min=outer_min,
                x_max=outer_max,
                y_min=y_min,
                y_max=y_max,
                z_min=0.0,
                z_max=base_h,
            ),
        )
    crossbar_w = 3.5
    for left, right in (
        (outer_min, outer_min + crossbar_w),
        (outer_max - crossbar_w, outer_max),
    ):
        add(
            "end_crossbar",
            _box_mesh(
                x_min=left,
                x_max=right,
                y_min=cy - rail_depth,
                y_max=cy + rail_depth,
                z_min=0.0,
                z_max=base_h,
            ),
        )
    for y_min, y_max in (
        (cy + inner_y, cy + inner_y + wall_t),
        (cy - inner_y - wall_t, cy - inner_y),
    ):
        add(
            "keeper_wall",
            _box_mesh(
                x_min=x_min - 0.2,
                x_max=x_max + 0.2,
                y_min=y_min,
                y_max=y_max,
                z_min=base_h,
                z_max=wall_height,
            ),
        )
    tie_h = upright.fixture_tie_height_mm
    tie_w = upright.fixture_tie_width_mm
    for tie_index, x_value in enumerate(tie_centers):
        if tie_index % 2 == 0:
            y_min, y_max = cy + face, cy + inner_y
        else:
            y_min, y_max = cy - inner_y, cy - face
        add(
            "breakaway_tie",
            _box_mesh(
                x_min=x_value - 0.5 * tie_w,
                x_max=x_value + 0.5 * tie_w,
                y_min=y_min,
                y_max=y_max,
                z_min=0.0,
                z_max=tie_h,
            ),
        )
    return components


def _wedge_fixture(
    part: PhysicalPart,
    globe: GlobeConfig,
    upright: UprightConfig,
    transformed_mesh: trimesh.Trimesh,
) -> List[FixtureComponent]:
    x_min = float(transformed_mesh.bounds[0, 0])
    x_max = float(transformed_mesh.bounds[1, 0])
    tie_offsets = (-54.0, -36.0, -12.0, 12.0, 36.0, 54.0)
    tie_centers = [
        upright.plate_center_x_mm + offset
        for offset in tie_offsets
        if x_min + 2.0 < upright.plate_center_x_mm + offset < x_max - 2.0
    ]
    return _straight_edge_fixture(
        part_id=part.part_id,
        x_min=x_min,
        x_max=x_max,
        tie_centers=tie_centers,
        globe=globe,
        upright=upright,
        wall_height=8.0,
    )


def build_upright_parts(
    globe: GlobeConfig,
    upright: UprightConfig,
) -> List[UprightPart]:
    globe.validate()
    upright.validate()
    result: List[UprightPart] = []
    for part in build_parts(globe):
        transform = _upright_transform(part, globe, upright)
        color_specs = _color_specs(part, transform, upright)
        mechanical = transformed_copy(part.mesh, transform)
        fixture = (
            _disc_fixture(part, globe, upright)
            if part.family == "disc"
            else _wedge_fixture(part, globe, upright, mechanical)
        )
        result.append(
            UprightPart(
                physical_part=part,
                transform=transform,
                color_specs=color_specs,
                fixture_components=fixture,
            )
        )
    return result


def _fixture_specs(
    upright_part: UprightPart,
    upright: UprightConfig,
    *,
    physical_part_id: Optional[str] = None,
) -> List[LeafMeshSpec]:
    resolved_part_id = physical_part_id or upright_part.part_id
    specs: List[LeafMeshSpec] = []
    for component in upright_part.fixture_components:
        label = f"{component.label}__PAT_{upright.fixture_token}__"
        specs.append(
            LeafMeshSpec(
                label,
                component.mesh,
                {
                    "physical_part_id": resolved_part_id,
                    "stack_token": upright.fixture_token,
                    "source_hex": upright.fixture_hex,
                    "support_role": component.role,
                    "removable_fixture": "true",
                    "print_orientation": "upright",
                    "recommended_layer_height_mm": f"{upright.layer_height_mm:.3f}",
                },
            )
        )
    return specs


def _mesh_bounds(meshes: Iterable[trimesh.Trimesh]) -> np.ndarray:
    bounds = [np.asarray(mesh.bounds, dtype=np.float64) for mesh in meshes]
    if not bounds:
        raise ValueError("cannot bound an empty mesh collection")
    return np.asarray(
        [
            np.min([value[0] for value in bounds], axis=0),
            np.max([value[1] for value in bounds], axis=0),
        ]
    )


def _union_solids(
    meshes: Sequence[trimesh.Trimesh],
    *,
    label: str,
) -> trimesh.Trimesh:
    if not meshes:
        raise ValueError(f"cannot union empty mesh list for {label}")
    if len(meshes) == 1:
        result = meshes[0].copy()
    else:
        result = trimesh.boolean.union(
            list(meshes),
            engine="manifold",
            check_volume=True,
        )
    if (
        result is None
        or result.is_empty
        or not result.is_watertight
        or not result.is_volume
    ):
        raise ValueError(f"fixture union failed for {label}")
    return result


def validate_upright_part(
    upright_part: UprightPart,
    upright: UprightConfig,
) -> UprightValidation:
    part_mesh = upright_part.mechanical_mesh
    color_meshes = [spec.mesh for spec in upright_part.color_specs]
    fixture_meshes = [component.mesh for component in upright_part.fixture_components]
    setup_bounds = _mesh_bounds([part_mesh, *fixture_meshes])
    normal = np.asarray(upright_part.transform[:3, 2], dtype=np.float64)
    normal = normal / np.linalg.norm(normal)
    xy_ok = (
        setup_bounds[0, 0] >= -1e-6
        and setup_bounds[0, 1] >= -1e-6
        and setup_bounds[1, 0] <= upright.plate_width_mm + 1e-6
        and setup_bounds[1, 1] <= upright.plate_depth_mm + 1e-6
    )
    fixture_intrusion = 0.0
    for fixture_mesh in fixture_meshes:
        overlap_min = np.maximum(part_mesh.bounds[0], fixture_mesh.bounds[0])
        overlap_max = np.minimum(part_mesh.bounds[1], fixture_mesh.bounds[1])
        if np.any(overlap_max <= overlap_min + 1e-8):
            continue
        intersection = trimesh.boolean.intersection(
            [part_mesh, fixture_mesh],
            engine="manifold",
            check_volume=False,
        )
        if intersection is not None and not intersection.is_empty:
            fixture_intrusion += abs(float(intersection.volume))
    return UprightValidation(
        part_id=upright_part.part_id,
        color_leaf_count=len(color_meshes),
        fixture_component_count=len(fixture_meshes),
        part_bounds_mm=tuple(
            tuple(float(value) for value in row)
            for row in np.asarray(part_mesh.bounds)
        ),
        setup_bounds_mm=tuple(
            tuple(float(value) for value in row)
            for row in setup_bounds
        ),
        broad_face_normal=tuple(float(value) for value in normal),
        broad_face_dot_build_z=abs(float(np.dot(normal, np.asarray([0.0, 0.0, 1.0])))),
        part_bed_contact=abs(float(part_mesh.bounds[0, 2])) <= 1e-5,
        fixture_bed_contact=abs(float(min(mesh.bounds[0, 2] for mesh in fixture_meshes))) <= 1e-5,
        within_build_plate_xy=bool(xy_ok),
        all_color_leaves_are_volumes=all(mesh.is_watertight and mesh.is_volume for mesh in color_meshes),
        all_fixture_components_are_volumes=all(
            mesh.is_watertight and mesh.is_volume for mesh in fixture_meshes
        ),
        fixture_intrusion_mm3=fixture_intrusion,
    )


def _write_part_outputs(
    upright_part: UprightPart,
    upright: UprightConfig,
    output_dir: Path,
) -> UprightValidation:
    source_dir = output_dir / "upright_sources"
    part_only_dir = output_dir / "part_only_sources"
    fixture_dir = output_dir / "fixtures"
    mechanical_dir = output_dir / "upright_mechanical_stl"
    for directory in (source_dir, part_only_dir, fixture_dir, mechanical_dir):
        directory.mkdir(parents=True, exist_ok=True)

    fixture_specs = _fixture_specs(upright_part, upright)
    write_metadata_assembly_3mf(
        upright_part.color_specs,
        str(part_only_dir / f"{upright_part.part_id}_upright_part_only_source.3mf"),
        title=f"{upright_part.part_id} upright color part only",
        validate_solids=True,
        include_display_colors=True,
    )
    write_metadata_assembly_3mf(
        [*upright_part.color_specs, *fixture_specs],
        str(source_dir / f"{upright_part.part_id}_upright_woven_source.3mf"),
        title=f"{upright_part.part_id} upright woven source with removable fixture",
        validate_solids=True,
        include_display_colors=True,
    )
    write_metadata_assembly_3mf(
        fixture_specs,
        str(fixture_dir / f"{upright_part.part_id}_removable_fixture.3mf"),
        title=f"{upright_part.part_id} removable upright fixture",
        validate_solids=True,
        include_display_colors=True,
    )

    upright_part.mechanical_mesh.export(
        mechanical_dir / f"{upright_part.part_id}_upright_mechanical.stl"
    )
    fixture_mesh = _union_solids(
        [component.mesh for component in upright_part.fixture_components],
        label=f"{upright_part.part_id} fixture",
    )
    fixture_mesh.export(
        fixture_dir / f"{upright_part.part_id}_removable_fixture.stl"
    )
    return validate_upright_part(upright_part, upright)


def _hex_sort_key(hex_value: str) -> Tuple[float, float, float]:
    raw = hex_value.strip().lstrip("#")
    rgb = tuple(int(raw[index : index + 2], 16) / 255.0 for index in (0, 2, 4))
    hue, saturation, value = colorsys.rgb_to_hsv(*rgb)
    if saturation < 0.06:
        hue = 2.0
    return (hue, saturation, value)


def _used_palette(parts: Sequence[UprightPart]) -> List[Tuple[str, str]]:
    by_token: Dict[str, str] = {}
    for upright_part in parts:
        for cell in upright_part.physical_part.cells:
            by_token[cell.token] = cell.source_hex
    return sorted(
        by_token.items(),
        key=lambda item: (*_hex_sort_key(item[1]), item[0]),
    )


def _calibration_transform(
    thickness_mm: float,
    *,
    center_x_mm: float,
    center_y_mm: float,
    panel_width_mm: float,
) -> np.ndarray:
    return np.asarray(
        [
            [1.0, 0.0, 0.0, center_x_mm - 0.5 * panel_width_mm],
            [0.0, 0.0, -1.0, center_y_mm + 0.5 * thickness_mm],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )


def _write_calibration_card(
    parts: Sequence[UprightPart],
    globe: GlobeConfig,
    upright: UprightConfig,
    output_dir: Path,
) -> Dict[str, object]:
    entries = _used_palette(parts)
    columns = upright.calibration_columns
    rows = int(math.ceil(len(entries) / columns))
    cell_size = upright.calibration_cell_mm
    panel_width = columns * cell_size
    panel_height = rows * cell_size
    if len(entries) != rows * columns:
        raise ValueError(
            "the default 13-column calibration grid expects a completely filled card"
        )
    transform = _calibration_transform(
        globe.plate_thickness_mm,
        center_x_mm=upright.plate_center_x_mm,
        center_y_mm=upright.plate_center_y_mm,
        panel_width_mm=panel_width,
    )
    specs: List[LeafMeshSpec] = []
    rows_out: List[Dict[str, object]] = []
    for index, (token, source_hex) in enumerate(entries):
        row = index // columns
        column = index % columns
        geometry = box(
            column * cell_size,
            row * cell_size,
            (column + 1) * cell_size,
            (row + 1) * cell_size,
        )
        label = f"calibration_r{row:02d}_c{column:02d}__PAT_{token}__"
        mesh = extrude_polygonal(
            geometry,
            height=globe.plate_thickness_mm,
            label=label,
        )
        specs.append(
            LeafMeshSpec(
                label,
                transformed_copy(mesh, transform),
                {
                    "physical_part_id": "upright_palette_calibration",
                    "stack_token": token,
                    "source_hex": source_hex,
                    "calibration_row": str(row),
                    "calibration_column": str(column),
                    "broad_faces_vertical": "true",
                    "recommended_layer_height_mm": f"{upright.layer_height_mm:.3f}",
                },
            )
        )
        rows_out.append(
            {
                "row": row,
                "column": column,
                "token": token,
                "source_hex": source_hex,
            }
        )

    x_min = upright.plate_center_x_mm - 0.5 * panel_width
    x_max = upright.plate_center_x_mm + 0.5 * panel_width
    tie_centers = [
        upright.plate_center_x_mm + offset
        for offset in (-30.0, -10.0, 10.0, 30.0)
        if x_min + 2.0 < upright.plate_center_x_mm + offset < x_max - 2.0
    ]
    fixture_components = _straight_edge_fixture(
        part_id="upright_palette_calibration",
        x_min=x_min,
        x_max=x_max,
        tie_centers=tie_centers,
        globe=globe,
        upright=upright,
        wall_height=8.0,
    )
    pseudo_part = UprightPart(
        physical_part=parts[0].physical_part,
        transform=np.eye(4),
        color_specs=specs,
        fixture_components=fixture_components,
    )
    fixture_specs = _fixture_specs(
        pseudo_part,
        upright,
        physical_part_id="upright_palette_calibration",
    )
    calibration_dir = output_dir / "calibration"
    calibration_dir.mkdir(parents=True, exist_ok=True)
    write_metadata_assembly_3mf(
        [*specs, *fixture_specs],
        str(calibration_dir / "upright_palette_calibration_source.3mf"),
        title="Upright LayerLoom palette calibration",
        validate_solids=True,
        include_display_colors=True,
    )
    write_metadata_assembly_3mf(
        specs,
        str(calibration_dir / "upright_palette_calibration_part_only.3mf"),
        title="Upright LayerLoom palette calibration part only",
        validate_solids=True,
        include_display_colors=True,
    )
    write_metadata_assembly_3mf(
        fixture_specs,
        str(calibration_dir / "upright_palette_calibration_fixture.3mf"),
        title="Upright calibration removable fixture",
        validate_solids=True,
        include_display_colors=True,
    )
    _union_solids(
        [component.mesh for component in fixture_components],
        label="upright palette calibration fixture",
    ).export(
        calibration_dir / "upright_palette_calibration_fixture.stl"
    )
    with open(
        calibration_dir / "upright_palette_calibration_legend.csv",
        "w",
        newline="",
        encoding="utf-8",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["row", "column", "token", "source_hex"],
        )
        writer.writeheader()
        writer.writerows(rows_out)
    return {
        "entry_count": len(entries),
        "rows": rows,
        "columns": columns,
        "cell_size_mm": cell_size,
        "panel_width_mm": panel_width,
        "panel_height_mm": panel_height,
        "fixture_component_count": len(fixture_components),
        "rows_data": rows_out,
        "specs": specs,
        "fixture_specs": fixture_specs,
    }


def _render_previews(
    parts: Sequence[UprightPart],
    calibration: Mapping[str, object],
    globe: GlobeConfig,
    upright: UprightConfig,
    output_dir: Path,
) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.collections import PatchCollection
        from matplotlib.patches import Polygon as MplPolygon
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    except Exception as exc:
        print(f"[upright] skipping previews: {exc}")
        return

    representatives = [
        next(part for part in parts if part.physical_part.family == "disc" and part.physical_part.index == index)
        for index in (0, 1, 2)
    ]
    representatives.append(
        next(part for part in parts if part.physical_part.family == "wedge")
    )
    fig, axes = plt.subplots(2, 2, figsize=(12.5, 10.0))
    for axis, upright_part in zip(axes.flat, representatives):
        part = upright_part.physical_part
        patches = []
        colors = []
        for cell in part.cells:
            for polygon in getattr(cell.geometry_2d, "geoms", (cell.geometry_2d,)):
                if not isinstance(polygon, Polygon):
                    continue
                coords = np.asarray(polygon.exterior.coords)
                if part.family == "disc":
                    radius = max(
                        abs(float(part.profile.bounds[1])),
                        abs(float(part.profile.bounds[3])),
                    )
                    xy = np.column_stack(
                        [
                            coords[:, 0],
                            coords[:, 1] + radius,
                        ]
                    )
                else:
                    xy = np.column_stack(
                        [
                            coords[:, 1],
                            coords[:, 0] - globe.axis_relief_mm,
                        ]
                    )
                patches.append(MplPolygon(xy, closed=True))
                colors.append(cell.source_hex)
        collection = PatchCollection(
            patches,
            facecolors=colors,
            edgecolors="#ffffff",
            linewidths=0.10,
        )
        axis.add_collection(collection)
        fixture_color = "#b7c3ce"
        center_x = upright.plate_center_x_mm
        for component in upright_part.fixture_components:
            bounds = np.asarray(component.mesh.bounds)
            rect = MplPolygon(
                np.asarray(
                    [
                        [bounds[0, 0] - center_x, bounds[0, 2]],
                        [bounds[1, 0] - center_x, bounds[0, 2]],
                        [bounds[1, 0] - center_x, bounds[1, 2]],
                        [bounds[0, 0] - center_x, bounds[1, 2]],
                    ]
                ),
                closed=True,
            )
            axis.add_patch(rect)
            rect.set_facecolor(fixture_color)
            rect.set_edgecolor("#617180")
            rect.set_alpha(0.22)
        setup = _mesh_bounds(
            [
                upright_part.mechanical_mesh,
                *(component.mesh for component in upright_part.fixture_components),
            ]
        )
        axis.set_xlim(setup[0, 0] - center_x - 4.0, setup[1, 0] - center_x + 4.0)
        axis.set_ylim(-1.0, setup[1, 2] + 5.0)
        axis.set_aspect("equal", "box")
        axis.set_title(
            f"{part.part_id}: broad faces vertical\n"
            f"print height {upright_part.mechanical_mesh.bounds[1, 2]:.1f} mm"
        )
        axis.set_xlabel("bed X (mm)")
        axis.set_ylabel("printer Z (mm)")
        axis.axhline(0.0, color="#202020", linewidth=0.8)
    fig.suptitle(
        "Upright color-weave orientations with removable fixtures",
        fontsize=15,
    )
    fig.tight_layout()
    fig.savefig(
        output_dir / "upright_orientation_preview.png",
        dpi=220,
        bbox_inches="tight",
    )
    plt.close(fig)

    rows = int(calibration["rows"])
    columns = int(calibration["columns"])
    fig, axis = plt.subplots(figsize=(13, 10))
    axis.set_xlim(0, columns)
    axis.set_ylim(0, rows)
    for item in calibration["rows_data"]:
        row = int(item["row"])
        column = int(item["column"])
        color = str(item["source_hex"])
        axis.add_patch(
            plt.Rectangle((column, row), 1.0, 1.0, facecolor=color, edgecolor="white")
        )
        rgb = tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))
        luminance = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
        axis.text(
            column + 0.5,
            row + 0.5,
            str(item["token"]),
            ha="center",
            va="center",
            fontsize=5.2,
            color="black" if luminance > 145 else "white",
        )
    axis.set_aspect("equal", "box")
    axis.set_xticks(range(columns))
    axis.set_yticks(range(rows))
    axis.set_xlabel("calibration column")
    axis.set_ylabel("calibration row")
    axis.set_title("143 LayerLoom recipes used by the upright globe")
    fig.tight_layout()
    fig.savefig(
        output_dir / "upright_palette_calibration_legend.png",
        dpi=220,
        bbox_inches="tight",
    )
    plt.close(fig)

    # Isometric fixture check: this makes the face-clearance and bed footprint
    # visible in a way that the front elevations cannot.
    isometric_parts = [
        next(
            part
            for part in parts
            if part.physical_part.family == "disc"
            and part.physical_part.index == 2
        ),
        next(part for part in parts if part.physical_part.family == "wedge"),
    ]
    fig = plt.figure(figsize=(13.0, 6.0))
    for plot_index, upright_part in enumerate(isometric_parts, start=1):
        axis = fig.add_subplot(1, 2, plot_index, projection="3d")
        part_mesh = upright_part.mechanical_mesh
        triangles = np.asarray(part_mesh.triangles)
        if len(triangles) > 1800:
            triangles = triangles[:: max(1, len(triangles) // 1800)]
        axis.add_collection3d(
            Poly3DCollection(
                triangles,
                facecolor="#4e9cc8",
                edgecolor="none",
                alpha=0.60,
            )
        )
        for component in upright_part.fixture_components:
            fixture_triangles = np.asarray(component.mesh.triangles)
            axis.add_collection3d(
                Poly3DCollection(
                    fixture_triangles,
                    facecolor="#ccd5dc",
                    edgecolor="#66737d",
                    linewidth=0.08,
                    alpha=0.72,
                )
            )
        setup = _mesh_bounds(
            [
                part_mesh,
                *(component.mesh for component in upright_part.fixture_components),
            ]
        )
        pad = np.asarray([4.0, 4.0, 3.0])
        lower = setup[0] - pad
        upper = setup[1] + pad
        axis.set_xlim(lower[0], upper[0])
        axis.set_ylim(lower[1], upper[1])
        axis.set_zlim(0.0, upper[2])
        axis.set_box_aspect(
            (
                max(1.0, upper[0] - lower[0]),
                max(1.0, 2.2 * (upper[1] - lower[1])),
                max(1.0, upper[2]),
            )
        )
        axis.view_init(elev=17, azim=-55)
        axis.set_title(
            f"{upright_part.part_id}\n"
            "blue = color part; gray = removable fixture"
        )
        axis.set_xlabel("bed X")
        axis.set_ylabel("bed Y")
        axis.set_zlabel("printer Z")
    fig.suptitle("Upright fixture isometric verification")
    fig.tight_layout()
    fig.savefig(
        output_dir / "upright_fixture_isometric_preview.png",
        dpi=220,
        bbox_inches="tight",
    )
    plt.close(fig)


def _write_manifest(
    *,
    globe: GlobeConfig,
    upright: UprightConfig,
    validations: Sequence[UprightValidation],
    calibration: Mapping[str, object],
    output_dir: Path,
) -> None:
    data = {
        "design": "LayerLoom upright woven sliceform color globe",
        "status": "generated production sources; physical fixture validation required",
        "globe_config": asdict(globe),
        "upright_config": asdict(upright),
        "orientation_contract": {
            "all_color_broad_faces_vertical": True,
            "broad_face_dot_build_z_max": max(
                result.broad_face_dot_build_z for result in validations
            ),
            "disc_orientation": "unslotted -90 degree rim tangent on Z=0",
            "wedge_orientation": "straight axial edge on Z=0",
            "one_physical_part_per_print": True,
        },
        "calibration": {
            key: value
            for key, value in calibration.items()
            if key not in {"rows_data", "specs", "fixture_specs"}
        },
        "validation": [asdict(result) for result in validations],
        "print_order": [
            "upright_palette_calibration",
            "wedge_01_a000",
            "disc_01_m048.0",
            "remaining wedges",
            "remaining discs, equator last",
        ],
    }
    (output_dir / "upright_manifest.json").write_text(
        json.dumps(data, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_print_instructions(
    *,
    globe: GlobeConfig,
    upright: UprightConfig,
    output_dir: Path,
) -> None:
    proof_names = (
        "upright_palette_calibration_woven.3mf",
        "wedge_01_a000_upright_woven_proof.3mf",
        "disc_01_m048.0_upright_woven_proof.3mf",
    )
    proofs_ready = all((output_dir / "proofs" / name).exists() for name in proof_names)
    proof_note = (
        """
## Prewoven proof files

The `proofs/` directory contains already-woven 0.12 mm CMYKW files for the
calibration card, wedge 1, and the small lower disc. These passed the complete
LayerLoom normalization, slicing, grouping, and final solid-bake workflow. Use
these three in that order for the first physical prints.
"""
        if proofs_ready
        else ""
    )
    text = f"""# Upright woven color-globe print package

Every color-bearing broad face in this package is already vertical. **Do not
use a slicer's "lay on face" or automatic orientation command.**

## Start here

1. Process `calibration/upright_palette_calibration_source.3mf` through
   LayerLoom at `{upright.layer_height_mm:.2f}` mm and print the result first.
2. Process and print
   `upright_sources/wedge_01_a000_upright_woven_source.3mf` as the first
   full-color structural proof.
3. Print `disc_01_m048.0` next. Confirm that the cradle snaps away cleanly and
   that its 2.15 mm slots still accept the calibrated 2.00 mm tongue.
4. Print one physical globe part per build plate. Do not batch the tall parts.

The files in `upright_sources/` include both the colored part and a removable
white fixture. Files in `part_only_sources/` omit the fixture for users who
prefer slicer-generated support. Matching fixture-only 3MF and STL files are in
`fixtures/`.

## Orientation and fixtures

- Discs stand on the unslotted -90° rim tangent. Their broad faces are vertical
  and the print heights are 72.0, 110.0, or 120.0 mm.
- Each disc cradle follows the lower rim with a
  {upright.fixture_interface_gap_mm:.2f} mm breakaway gap. Keeper walls remain
  {upright.fixture_face_clearance_mm:.2f} mm away from both color faces.
- Wedges stand on their long straight axial edge and are about 58.05 mm tall.
- Thin first-layer ties connect the part to the fixture rails. Cut or flex these
  ties first, then lift the fixture away from the color part.

## Slicer starting settings

- Layer height: `{upright.layer_height_mm:.2f}` mm, matching the LayerLoom step.
- First layer: no faster than 20 mm/s.
- Outer walls: 30–35 mm/s maximum.
- Acceleration: 800 mm/s² or lower.
- Z-hop: approximately 0.4 mm.
- Elephant-foot compensation: 0.15–0.20 mm.
- No automatic supports on either broad color face.

The disc slots that become horizontal span only
{globe.slot_width_mm:.2f} mm. Inspect them in slicer preview; they should bridge
without a support interface touching the color face.

## Removal and acceptance

Remove the first-layer ties before flexing the keeper walls. A finished part is
acceptable when:

- the broad plate is straight to within approximately 0.5 mm;
- cleaned thickness is 2.00 ± 0.10 mm;
- the 0.15 mm-clearance slots slide without bowing;
- the fixture leaves no broad-face scar;
- no slot ceiling is missing in slicer preview.

`upright_manifest.json` records the exact transforms, bounds, fixture counts,
and geometry validation results.
{proof_note}
"""
    (output_dir / "PRINT_ME_FIRST.md").write_text(text, encoding="utf-8")


def export_upright_package(
    *,
    globe: GlobeConfig,
    upright: UprightConfig,
    output_dir: Path,
    render_previews: bool = True,
) -> Tuple[List[UprightPart], List[UprightValidation]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    parts = build_upright_parts(globe, upright)
    validations = [
        _write_part_outputs(part, upright, output_dir)
        for part in parts
    ]
    failures = [
        result.part_id
        for result in validations
        if (
            result.broad_face_dot_build_z > 1e-8
            or not result.part_bed_contact
            or not result.fixture_bed_contact
            or not result.within_build_plate_xy
            or not result.all_color_leaves_are_volumes
            or not result.all_fixture_components_are_volumes
            or result.fixture_intrusion_mm3 > 1e-6
        )
    ]
    if failures:
        raise ValueError(f"upright validation failed for: {', '.join(failures)}")
    calibration = _write_calibration_card(parts, globe, upright, output_dir)
    if render_previews:
        _render_previews(parts, calibration, globe, upright, output_dir)
    _write_manifest(
        globe=globe,
        upright=upright,
        validations=validations,
        calibration=calibration,
        output_dir=output_dir,
    )
    _write_print_instructions(
        globe=globe,
        upright=upright,
        output_dir=output_dir,
    )
    return parts, validations


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate upright fixture-supported LayerLoom color-globe sources."
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/kolormondo_woven_upright_c015",
    )
    parser.add_argument("--diameter-mm", type=float, default=120.0)
    parser.add_argument("--thickness-mm", type=float, default=2.0)
    parser.add_argument("--slot-clearance-mm", type=float, default=0.15)
    parser.add_argument("--axis-clearance-mm", type=float, default=0.25)
    parser.add_argument("--layer-height-mm", type=float, default=0.12)
    parser.add_argument("--palette", default="Normal")
    parser.add_argument("--skip-previews", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    globe = GlobeConfig(
        diameter_mm=args.diameter_mm,
        plate_thickness_mm=args.thickness_mm,
        slot_clearance_mm=args.slot_clearance_mm,
        axis_clearance_mm=args.axis_clearance_mm,
        palette=args.palette,
    )
    upright = UprightConfig(layer_height_mm=args.layer_height_mm)
    parts, validations = export_upright_package(
        globe=globe,
        upright=upright,
        output_dir=Path(args.output_dir),
        render_previews=not args.skip_previews,
    )
    print(
        f"[upright] generated {len(parts)} upright sources; "
        f"{sum(result.color_leaf_count for result in validations)} color leaves; "
        "all broad faces vertical"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
