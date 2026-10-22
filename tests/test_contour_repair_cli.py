"""Observable reconstruction/export contracts for the opt-in contour prototype."""
import json
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import numpy as np
import pytest
import trimesh

from layerloom.contour_repair import (main, reconstruct_file, _read_native_bands,
                                     _exported_band_footprint, _exported_seams)
from layerloom.planar_bands import extrude_profile
from layerloom.planar_materials import partition_materials
from layerloom.export import write_basic_3mf
from layerloom.ingest import read_parts


def cube(x=0.0):
    mesh = trimesh.creation.box(extents=(2, 2, 2))
    mesh.apply_translation((x, 0, 1))
    return mesh


def input_3mf(tmp_path, parts):
    path = tmp_path / "source.3mf"
    write_basic_3mf(parts, str(path))
    return path


def test_open_top_reconstructs_and_reimports_with_recipe(tmp_path):
    mesh = cube()
    mesh.update_faces(mesh.face_normals[:, 2] < 0.9)
    source = input_3mf(tmp_path, [("open_box__PAT_cm__", mesh)])
    output = tmp_path / "reconstructed.3mf"
    report = reconstruct_file(source, output, layer_height=0.2)
    assert report["status"] == "ok", report["errors"]
    assert report["exported_bands"] == 10
    parts = _read_native_bands(output)
    assert len(parts) == 10
    assert all(mesh.is_volume for _, mesh in parts)
    assert len(read_parts([str(output)])) == 1  # Generic readers see one mesh.
    assert report["roundtrip_max_footprint_error_mm2"] < 1e-6
    assert [layer["materials"][0]["token"] for layer in report["layers"]] == list("cm" * 5)
    for layer in report["layers"]:
        assert layer["materials"][0]["area_mm2"] == pytest.approx(4.0)
    saved = json.loads(output.with_suffix(".report.json").read_text())
    assert saved["status"] == "ok"


def test_unresolved_part_rejects_entire_export_and_preserves_existing_file(tmp_path):
    broken = cube(4)
    broken.update_faces(broken.face_normals[:, 0] < 0.9)
    source = input_3mf(tmp_path, [("good", cube()), ("broken", broken)])
    output = tmp_path / "reconstructed.3mf"
    output.write_bytes(b"previous output")
    report = reconstruct_file(source, output, layer_height=0.2)
    assert report["status"] == "failed"
    assert any("unresolved contour" in error for error in report["errors"])
    assert output.read_bytes() == b"previous output"
    assert json.loads(output.with_suffix(".report.json").read_text())["status"] == "failed"


def test_cross_material_overlap_is_rejected(tmp_path):
    source = input_3mf(tmp_path, [("first__PAT_c__", cube()), ("second__PAT_m__", cube(1))])
    output = tmp_path / "reconstructed.3mf"
    report = reconstruct_file(source, output, layer_height=1.0)
    assert report["status"] == "failed"
    assert any("material overlap" in error for error in report["errors"])
    assert not output.exists()
    assert report["layers"][0]["overlaps"][0]["area_mm2"] == pytest.approx(2.0)


def test_same_material_overlaps_are_unioned_before_extrusion(tmp_path):
    source = input_3mf(tmp_path, [("first__PAT_c__", cube()), ("second__PAT_c__", cube(1))])
    output = tmp_path / "reconstructed.3mf"
    report = reconstruct_file(source, output, layer_height=1.0)
    assert report["status"] == "ok", report["errors"]
    assert report["exported_bands"] == 2
    assert all(layer["materials"][0]["area_mm2"] == pytest.approx(6.0) for layer in report["layers"])
    assert all(layer["materials"][0]["duplicate_faces"] == 0 for layer in report["layers"])


def test_touching_distinct_materials_are_allowed(tmp_path):
    source = input_3mf(tmp_path, [("first__PAT_c__", cube()), ("second__PAT_m__", cube(2))])
    report = reconstruct_file(source, tmp_path / "reconstructed.3mf", layer_height=1.0)
    assert report["status"] == "ok", report["errors"]
    assert report["exported_bands"] == 4


def test_cli_and_stl_fallback_recipe(tmp_path):
    source = tmp_path / "cube.stl"
    cube().export(source)
    output = tmp_path / "reconstructed.3mf"
    assert main([str(source), str(output), "--layer-height", "1", "--run", "y"]) == 0
    report = json.loads(output.with_suffix(".report.json").read_text())
    assert report["parts"][0]["recipe"] == "y"


@pytest.mark.parametrize("kwargs", [{"layer_height": 0}, {"layer_height": float("nan")},
                                    {"layer_height": 0.2, "z_gap": 0.2},
                                    {"layer_height": 0.2, "run": "xyz"}])
def test_invalid_parameters_fail_before_output(tmp_path, kwargs):
    source = input_3mf(tmp_path, [("cube", cube())])
    output = tmp_path / "reconstructed.3mf"
    with pytest.raises(ValueError):
        reconstruct_file(source, output, **kwargs)
    assert not output.exists()


def test_paths_cannot_overwrite_source(tmp_path):
    source = input_3mf(tmp_path, [("cube", cube())])
    original = source.read_bytes()
    with pytest.raises(ValueError, match="paths must be different"):
        reconstruct_file(source, source, layer_height=0.2)
    assert source.read_bytes() == original


@pytest.mark.parametrize("label", ["part__PAT_x__", "part__PAT___", "part__PAT_cq__"])
def test_invalid_pat_is_rejected_instead_of_using_fallback(tmp_path, label):
    source = input_3mf(tmp_path, [(label, cube())])
    report = reconstruct_file(source, tmp_path / "reconstructed.3mf", layer_height=1.0)
    assert report["status"] == "failed"
    assert any("unsupported PAT" in error for error in report["errors"])


def test_non_mm_3mf_is_rejected(tmp_path):
    source = input_3mf(tmp_path, [("cube", cube())])
    with ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    root = ET.fromstring(entries["3D/3dmodel.model"])
    root.set("unit", "inch")
    entries["3D/3dmodel.model"] = ET.tostring(root)
    with ZipFile(source, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    report = reconstruct_file(source, tmp_path / "reconstructed.3mf", layer_height=1.0)
    assert report["status"] == "failed"
    assert any("only millimeter" in error for error in report["errors"])


def test_zero_z_gap_has_assembly_warning(tmp_path):
    source = input_3mf(tmp_path, [("cube", cube())])
    report = reconstruct_file(source, tmp_path / "reconstructed.3mf", layer_height=1.0, z_gap=0)
    assert report["status"] == "ok"
    assert any("coincident caps" in warning for warning in report["warnings"])


def test_export_check_detects_rounding_overlap_on_unequally_segmented_seam():
    from shapely.geometry import Polygon

    x, dx = 200.12345, 1.23456
    regions, _, errors = partition_materials({
        "c": [Polygon([(x-1, 0), (x, 0), (x+dx, 1), (x-1, 1)])],
        "m": [Polygon([(x, 0), (x+3, 0), (x+3, 1), (x+dx, 1), (x+dx/2, .5)])],
    })
    assert not errors
    assert regions["c"].intersection(regions["m"]).area == 0
    meshes = {name: extrude_profile(region, 0, .2) for name, region in regions.items()}
    actual = {name: _exported_band_footprint(mesh) for name, mesh in meshes.items()}
    displacements = {name: mesh.metadata["extrusion"]["max_xy_displacement_mm"]
                     for name, mesh in meshes.items()}
    overlaps = _exported_seams(actual, regions, displacements, precision=1e-5)
    assert overlaps[0]["area_mm2"] == pytest.approx(3.814697265625e-6)


def test_export_overlap_guard_rejects_change_beyond_rounding_band():
    from shapely.geometry import box

    expected = {"c": box(0, 0, 1, 1), "m": box(1, 0, 2, 1)}
    actual = {"c": box(0, 0, 1.01, 1), "m": expected["m"]}
    with pytest.raises(ValueError, match="overlap beyond rounding"):
        _exported_seams(actual, expected, {"c": 1e-5, "m": 0}, precision=1e-5)


def test_roundtrip_footprint_does_not_accept_a_sloping_band():
    mesh = cube()
    mesh.vertices[mesh.vertices[:, 2] > 1, 0] += .1
    with pytest.raises(ValueError, match="nonvertical"):
        _exported_band_footprint(mesh)
