"""Geometry contracts for the experimental fixed-layer reconstruction path."""

from __future__ import annotations

import numpy as np
import pytest
from shapely.geometry import Polygon, box
import trimesh

from layerloom.planar_bands import (
    RepairConfig,
    extrude_profile,
    reconstruct_layers,
    recover_profile,
    section_profile,
)


def ring_segments(points):
    points = np.asarray(points, dtype=float)
    return np.stack((points, np.roll(points, -1, axis=0)), axis=1)


def square_with_gap(width):
    """An otherwise unit square with a centered opening in its upper edge."""
    left, right = 0.5 - width / 2, 0.5 + width / 2
    return np.array([
        [[0, 0], [1, 0]], [[1, 0], [1, 1]],
        [[1, 1], [right, 1]], [[left, 1], [0, 1]], [[0, 1], [0, 0]],
    ], dtype=float)


def box_mesh(size=(10, 10, 10)):
    mesh = trimesh.creation.box(extents=size)
    mesh.apply_translation(np.asarray(size) / 2)
    return mesh


def independent_convex_section(mesh, z):
    """Independent reference for single convex loops, using Trimesh's path API."""
    path = mesh.section(plane_origin=(0, 0, z), plane_normal=(0, 0, 1))
    if path is None:
        return Polygon()
    assert len(path.discrete) == 1
    return Polygon(np.asarray(path.discrete[0])[:, :2])


@pytest.mark.parametrize("damage", ["none", "missing_caps", "inverted", "duplicates", "unwelded"])
def test_box_footprint_survives_targeted_mesh_defects(damage):
    mesh = box_mesh()
    if damage == "missing_caps":
        mesh.update_faces(np.abs(mesh.face_normals[:, 2]) < 0.5)
        assert not mesh.is_watertight
    elif damage == "inverted":
        mesh.invert()
        assert mesh.volume < 0
    elif damage == "duplicates":
        mesh = trimesh.Trimesh(vertices=mesh.vertices,
                               faces=np.vstack((mesh.faces, mesh.faces)), process=False)
        assert not mesh.is_watertight
    elif damage == "unwelded":
        vertices = mesh.triangles.reshape(-1, 3)
        mesh = trimesh.Trimesh(vertices=vertices,
                               faces=np.arange(len(vertices)).reshape(-1, 3), process=False)
        assert not mesh.is_watertight
    result = section_profile(mesh, 5.1)
    assert result.ok, result.diagnostics
    assert result.region.symmetric_difference(box(0, 0, 10, 10)).area < 1e-7


def test_nested_rings_keep_hole_and_inner_island():
    segments = np.concatenate([
        ring_segments([(0, 0), (10, 0), (10, 10), (0, 10)]),
        ring_segments([(2, 2), (8, 2), (8, 8), (2, 8)]),
        ring_segments([(4, 4), (6, 4), (6, 6), (4, 6)]),
    ])
    result = recover_profile(segments)
    expected = box(0, 0, 10, 10).difference(box(2, 2, 8, 8)).union(box(4, 4, 6, 6))
    assert result.ok, result.diagnostics
    assert result.region.symmetric_difference(expected).area < 1e-7
    assert result.region.area == pytest.approx(68)


def test_hollow_tube_remains_hollow():
    mesh = trimesh.creation.annulus(r_min=2, r_max=4, height=10, sections=64)
    result = section_profile(mesh, 0.37)
    expected = (4**2 - 2**2) * 64 * np.sin(2 * np.pi / 64) / 2
    assert result.ok, result.diagnostics
    assert result.region.area == pytest.approx(expected, abs=2e-4)
    assert len(result.region.interiors) == 1


def test_gap_within_budget_is_closed():
    result = recover_profile(square_with_gap(0.02), RepairConfig(max_gap=0.03))
    assert result.ok, result.diagnostics
    assert result.region.symmetric_difference(box(0, 0, 1, 1)).area < 1e-7


def test_gap_exceeding_budget_is_explicit_failure():
    result = recover_profile(square_with_gap(0.04), RepairConfig(max_gap=0.03))
    assert not result.ok
    assert result.diagnostics


def test_closed_nearby_islands_are_not_bridged():
    first = ring_segments([(0, 0), (1, 0), (1, 1), (0, 1)])
    second = first + np.array([1.02, 0])
    result = recover_profile(np.concatenate((first, second)), RepairConfig(max_gap=0.03))
    expected = box(0, 0, 1, 1).union(box(1.02, 0, 2.02, 1))
    assert result.ok, result.diagnostics
    assert len(result.region.geoms) == 2
    assert result.region.symmetric_difference(expected).area < 1e-7


def test_multiple_possible_bridge_partners_are_rejected():
    segments = np.array([
        [[0, 0], [1, 0]], [[1.01, 0], [1, 1]], [[1.02, 0], [2, 0]],
    ], dtype=float)
    result = recover_profile(segments, RepairConfig(max_gap=0.03))
    assert not result.ok
    assert result.diagnostics["ambiguous_endpoints"] > 0


@pytest.mark.parametrize("segments", [
    ring_segments([(0, 0), (1, 1), (0, 1), (1, 0)]),
    np.concatenate((ring_segments([(0, 0), (1, 0), (1, 1), (0, 1)]),
                    np.array([[[0.5, 0], [0.5, -0.5]]]))),
])
def test_crossings_and_branches_are_not_guessed(segments):
    result = recover_profile(segments)
    assert not result.ok
    assert result.diagnostics


def test_duplicate_and_reversed_segments_do_not_cancel_footprint():
    segments = ring_segments([(0, 0), (1, 0), (1, 1), (0, 1)])
    result = recover_profile(np.concatenate((segments, segments[:, ::-1], segments)))
    assert result.ok, result.diagnostics
    assert result.region.area == pytest.approx(1)


def test_hollow_extrusion_survives_stl_roundtrip(tmp_path):
    expected = box(0, 0, 10, 10).difference(box(2, 2, 8, 8))
    mesh = extrude_profile(expected, 1.2, 1.4)
    assert mesh is not None and mesh.is_volume
    assert mesh.volume == pytest.approx(expected.area * 0.2)
    path = tmp_path / "hollow-band.stl"
    mesh.export(path)
    restored = trimesh.load(path, force="mesh", process=True)
    result = section_profile(restored, 1.3)
    assert restored.is_volume
    assert result.ok, result.diagnostics
    assert result.region.symmetric_difference(expected).area < 1e-7
    assert len(result.region.interiors) == 1


def test_sphere_retains_midpoint_profile_after_export(tmp_path):
    source = trimesh.creation.icosphere(subdivisions=2, radius=3)
    z = 0.7
    expected = independent_convex_section(source, z)
    result = section_profile(source, z, RepairConfig(precision=1e-6))
    assert result.ok, result.diagnostics
    assert result.region.symmetric_difference(expected).area < 1e-4
    mesh = extrude_profile(result.region, 0.6, 0.8)
    path = tmp_path / "sphere-band.stl"
    mesh.export(path)
    restored = trimesh.load(path, force="mesh", process=True)
    actual = independent_convex_section(restored, z)
    assert actual.symmetric_difference(expected).area < 1e-4
    # The midpoint is faithful; a fixed profile intentionally changes off-grid geometry.
    assert actual.symmetric_difference(independent_convex_section(source, 0.79)).area > 0.05


def test_thin_feature_between_sampling_planes_is_explicitly_empty():
    mesh = box_mesh((1, 1, 0.02))
    mesh.apply_translation([0, 0, 0.02])
    bands = reconstruct_layers(mesh, layer_height=0.2, z0=0)
    assert len(bands) == 1
    assert bands[0].sample_z == pytest.approx(0.1)
    assert bands[0].mesh is None
    assert bands[0].profile.region.is_empty
    assert bands[0].profile.diagnostics.get("sampling_warning")


def test_partial_top_uses_global_nominal_layer_midpoint():
    mesh = box_mesh((1, 1, 0.31))
    bands = reconstruct_layers(mesh, layer_height=0.2, z0=0)
    assert len(bands) == 2
    assert [b.index for b in bands] == [0, 1]
    assert [(b.low, b.high) for b in bands] == pytest.approx([(0, 0.2), (0.2, 0.4)])
    assert [b.sample_z for b in bands] == pytest.approx([0.1, 0.3])
    assert all(b.profile.ok and b.mesh is not None for b in bands)
    assert bands[-1].mesh.bounds[1, 2] == pytest.approx(0.4)


@pytest.mark.parametrize("kwargs", [
    {"precision": 0}, {"precision": -1}, {"precision": float("nan")},
    {"precision": float("inf")}, {"max_gap": -1},
    {"max_gap": float("nan")}, {"max_gap": float("inf")},
])
def test_invalid_repair_tolerances_fail_early(kwargs):
    with pytest.raises(ValueError):
        config = RepairConfig(**kwargs)
        recover_profile(ring_segments([(0, 0), (1, 0), (1, 1), (0, 1)]), config)


@pytest.mark.parametrize("height", [0, -1, float("nan"), float("inf")])
def test_invalid_layer_height_is_rejected(height):
    with pytest.raises(ValueError):
        reconstruct_layers(box_mesh(), layer_height=height)


def test_nonfinite_input_is_rejected():
    with pytest.raises(ValueError):
        recover_profile(np.array([[[0, 0], [np.nan, 1]]]))
    with pytest.raises(ValueError):
        section_profile(box_mesh(), float("nan"))
    with pytest.raises(ValueError):
        reconstruct_layers(box_mesh(), layer_height=0.2, z0=float("nan"))
    with pytest.raises(ValueError):
        extrude_profile(box(0, 0, 1, 1), 0, float("inf"))
