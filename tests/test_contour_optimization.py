"""The profile-first CLI must retain its final geometry and rejection checks."""
from __future__ import annotations

import numpy as np
import pytest
import shapely
from shapely.geometry import GeometryCollection, LineString, Polygon
import trimesh

import layerloom.contour_repair as cli
import layerloom.planar_bands as bands
from layerloom.export import write_basic_3mf


def legacy_cdt_arrays(triangulated):
    points = np.asarray([np.asarray(triangle.exterior.coords)[:3]
                         for triangle in triangulated.geoms]).reshape(-1, 2)
    vertices, inverse = np.unique(points, axis=0, return_inverse=True)
    return vertices, inverse.reshape(-1, 3)


def box(x=0.0, height=0.6):
    mesh = trimesh.creation.box(extents=(2, 2, height))
    mesh.apply_translation((x, 0, height / 2))
    return mesh


def wall_box(gap=0.02, height=0.6):
    segments = [((-1, -1), (1, -1)), ((1, -1), (1, 1)),
                ((1, 1), (gap / 2, 1)), ((-gap / 2, 1), (-1, 1)),
                ((-1, 1), (-1, -1))]
    vertices, faces = [], []
    for a, b in segments:
        n = len(vertices)
        vertices.extend([(*a, 0), (*b, 0), (*b, height), (*a, height)])
        faces.extend([(n, n + 1, n + 2), (n, n + 2, n + 3)])
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)


def source(tmp_path, *, damaged=False):
    first = wall_box() if damaged else box()
    tube = trimesh.creation.annulus(r_min=0.4, r_max=1, height=0.6, sections=24)
    tube.apply_translation((5, 0, 0.3))
    if damaged:
        tube.update_faces(np.abs(tube.face_normals[:, 2]) < 0.5)
    path = tmp_path / "source.3mf"
    write_basic_3mf([("first__PAT_c__", first), ("overlap__PAT_c__", box(1)),
                     ("tube__PAT_m__", tube)], str(path))
    return path


@pytest.mark.parametrize("damaged", [False, True])
def test_optimized_export_is_exactly_equal_to_discarded_extrusion_path(tmp_path, monkeypatch, damaged):
    input_path = source(tmp_path, damaged=damaged)
    before, after = tmp_path / "before.3mf", tmp_path / "after.3mf"
    optimized_extractor = cli.reconstruct_layer_profiles
    # Reproduce the pre-optimization CLI: extract each source mesh by building
    # its full bands, then discard those meshes and partition their profiles.
    monkeypatch.setattr(cli, "reconstruct_layer_profiles", bands.reconstruct_layers)
    before_report = cli.reconstruct_file(input_path, before, layer_height=0.2)
    monkeypatch.setattr(cli, "reconstruct_layer_profiles", optimized_extractor)
    after_report = cli.reconstruct_file(input_path, after, layer_height=0.2)
    assert before_report["status"] == after_report["status"] == "ok"
    before_parts, after_parts = cli._read_native_bands(before), cli._read_native_bands(after)
    assert len(before_parts) == len(after_parts) == 6
    for (before_name, before_mesh), (after_name, after_mesh) in zip(before_parts, after_parts):
        assert before_name == after_name
        assert np.array_equal(before_mesh.vertices, after_mesh.vertices)
        assert np.array_equal(before_mesh.faces, after_mesh.faces)
        assert after_mesh.is_volume
    assert before_report["parts"] == after_report["parts"]
    assert before_report["layers"] == after_report["layers"]
    assert before_report["roundtrip_max_complete_layer_error_mm2"] == after_report["roundtrip_max_complete_layer_error_mm2"]


def test_cli_extrudes_only_final_materials_and_compatibility_api_still_extrudes(tmp_path, monkeypatch):
    input_path = source(tmp_path, damaged=True)
    final_extrude = cli.extrude_profile
    calls = []

    def forbidden_intermediate(*args, **kwargs):
        raise RuntimeError("intermediate source extrusion is forbidden")

    def record_final(region, low, high):
        calls.append((region.area, low, high))
        return final_extrude(region, low, high)

    monkeypatch.setattr(bands, "extrude_profile", forbidden_intermediate)
    monkeypatch.setattr(cli, "extrude_profile", record_final)
    report = cli.reconstruct_file(input_path, tmp_path / "result.3mf", layer_height=0.2)
    assert report["status"] == "ok", report["errors"]
    assert len(calls) == report["exported_bands"] == 6
    # Source bounds are float32 after ingestion and may add an empty final
    # midpoint; none of these source slots should cause an intermediate mesh.
    assert sum(len(part["layers"]) for part in report["parts"]) > len(calls)
    compatibility = bands.reconstruct_layers(box(), 0.2)
    assert len(compatibility) == 3
    assert all(not band.profile.ok and band.mesh is None for band in compatibility)
    assert all(band.profile.diagnostics["status"] == "extrusion_failed" for band in compatibility)


def test_profiles_keep_global_indices_and_empty_sample_diagnostics():
    mesh = box(height=0.02)
    mesh.apply_translation((0, 0, 0.02))
    profiles = bands.reconstruct_layer_profiles(mesh, 0.2, z0=0)
    assert len(profiles) == 1
    assert profiles[0].index == 0
    assert (profiles[0].low, profiles[0].high, profiles[0].sample_z) == (0, 0.2, 0.1)
    assert profiles[0].profile.ok and profiles[0].profile.region.is_empty
    assert "sampling_warning" in profiles[0].profile.diagnostics
    shifted = bands.reconstruct_layer_profiles(box(), 0.2, z0=-1)
    assert shifted[0].index == 5
    assert shifted[0].low == 0


@pytest.mark.parametrize("failure", ["contour", "overlap", "extrusion", "float32"])
def test_optimized_path_preserves_final_rejection_and_previous_output(tmp_path, monkeypatch, failure):
    parts = [("first__PAT_c__", box())]
    expected = {"contour": "unresolved contour", "overlap": "material overlap",
                "extrusion": "forced final extrusion failure", "float32": "float32 geometry failed"}[failure]
    if failure == "contour":
        parts = [("broken__PAT_c__", wall_box(0.08))]
    elif failure == "overlap":
        parts.append(("overlap__PAT_m__", box(1)))
    elif failure == "extrusion":
        def fail_extrusion(*args, **kwargs):
            raise ValueError("forced final extrusion failure")
        monkeypatch.setattr(cli, "extrude_profile", fail_extrusion)
    elif failure == "float32":
        real_checks = cli._float32_mesh_checks
        def fail_checks(mesh):
            return {**real_checks(mesh), "zero_area_triangles": 1}
        monkeypatch.setattr(cli, "_float32_mesh_checks", fail_checks)
    input_path = tmp_path / "source.3mf"
    write_basic_3mf(parts, str(input_path))
    output = tmp_path / "result.3mf"
    output.write_bytes(b"previous result")
    report = cli.reconstruct_file(input_path, output, layer_height=0.2)
    assert report["status"] == "failed"
    assert any(expected in error for error in report["errors"]), report["errors"]
    assert output.read_bytes() == b"previous result"


@pytest.mark.parametrize("region", [
    Polygon([(0, 0), (3, 0), (3, 1), (1, 1), (1, 3), (0, 3)]),
    Polygon([(0, 0), (3, 0), (3, 3), (0, 3)],
            [[(1, 1), (1, 2), (2, 2), (2, 1)]]),
    Polygon([(0, 0), (3, 0), (3, 3), (0, 3)],
            [[(1, 0), (1.3, 0.5), (0.7, 0.5)]]),
])
def test_bulk_cdt_preserves_exact_cap_and_mesh_arrays(region, monkeypatch):
    cdt = getattr(shapely, "constrained_delaunay_triangles", None)
    if cdt is None:
        pytest.skip("CDT is unavailable with Shapely 2.0")
    prepared, _ = bands.prepare_export_region(region)
    for polygon in shapely.get_parts(prepared):
        triangulated = cdt(polygon)
        triangles = shapely.get_parts(triangulated)
        assert np.all(shapely.get_num_coordinates(triangles) == 4)
        assert not np.any(shapely.has_z(triangles))
        legacy_vertices, legacy_faces = legacy_cdt_arrays(triangulated)
        bulk_vertices, bulk_faces = bands._cdt_cap_arrays(triangulated)
        assert np.array_equal(legacy_vertices, bulk_vertices)
        assert np.array_equal(legacy_faces, bulk_faces)
    bulk_mesh = bands.extrude_profile(region, 0.0005, 0.1995)
    monkeypatch.setattr(bands, "_cdt_cap_arrays", legacy_cdt_arrays)
    legacy_mesh = bands.extrude_profile(region, 0.0005, 0.1995)
    assert np.array_equal(legacy_mesh.vertices, bulk_mesh.vertices)
    assert np.array_equal(legacy_mesh.faces, bulk_mesh.faces)
    assert legacy_mesh.metadata == bulk_mesh.metadata


@pytest.mark.parametrize("triangulated", [
    GeometryCollection(),
    GeometryCollection([Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])]),
    GeometryCollection([LineString([(0, 0), (1, 0), (0, 1), (0, 0)])]),
    GeometryCollection([Polygon([(0, 0, 1), (1, 0, 1), (0, 1, 1)])]),
])
def test_bulk_cdt_rejects_unexpected_coordinate_layout(triangulated):
    with pytest.raises(ValueError, match="closed 2D triangles"):
        bands._cdt_cap_arrays(triangulated)
