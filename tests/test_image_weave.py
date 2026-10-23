from __future__ import annotations

import zipfile
from xml.etree import ElementTree as ET

import numpy as np
import pytest
import trimesh
from shapely.geometry import Point

from layerloom.export import LeafMeshSpec, write_orca_multipart_3mf
from layerloom.image_weave import (
    assign_clusters_to_curated_recipes,
    compact_touching_meshes,
    partition_profile,
    remove_small_label_components,
    smooth_circular_runs,
    smooth_label_map_vertical_runs,
)


def test_orca_export_groups_filaments_as_one_multipart_object(tmp_path):
    cyan = trimesh.creation.box(extents=(2.0, 2.0, 1.0))
    magenta = cyan.copy()
    magenta.apply_translation((0.0, 0.0, 1.0))
    output = tmp_path / "multipart.3mf"

    write_orca_multipart_3mf(
        [
            LeafMeshSpec("all_cyan", cyan, {"stack_token": "c"}),
            LeafMeshSpec("all_magenta", magenta, {"stack_token": "m"}),
        ],
        str(output),
        title="Multipart test",
    )

    core = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
    with zipfile.ZipFile(output) as archive:
        root = ET.fromstring(archive.read("3D/3dmodel.model"))
        objects = root.find(core + "resources").findall(core + "object")
        build_items = root.find(core + "build").findall(core + "item")
        config = ET.fromstring(archive.read("Metadata/model_settings.config"))

    assert len(objects) == 1
    assert len(objects[0].find(core + "components").findall(core + "component")) == 2
    assert len(build_items) == 1
    assert len(config.find("object").findall("part")) == 2


def test_multipart_stream_preserves_geometry_metadata_and_input(tmp_path, monkeypatch):
    from layerloom import export
    mesh = trimesh.creation.icosphere(subdivisions=4)
    mesh.apply_translation([12.345678901, -4.876543219, 7.123456789])
    vertices, faces = mesh.vertices.copy(), mesh.faces.copy()
    label = 'café <dragon> & "paint"'
    def no_copy(*args, **kwargs):
        pytest.fail("The multipart writer must not duplicate whole meshes")
    monkeypatch.setattr(trimesh.Trimesh, "copy", no_copy)
    original_sub_element = export.ET.SubElement
    def bounded_tree(parent, tag, *args, **kwargs):
        assert tag not in {export.M("vertex"), export.M("triangle")}
        return original_sub_element(parent, tag, *args, **kwargs)
    monkeypatch.setattr(export.ET, "SubElement", bounded_tree)
    path = tmp_path / "café geometry.3mf"
    checks = []
    write_orca_multipart_3mf([LeafMeshSpec(label, mesh, {"stack_token": "c", "note": "a < b & c"})],
                             str(path), checkpoint=lambda: checks.append(True))
    with zipfile.ZipFile(path) as archive:
        child = ET.fromstring(archive.read("3D/Objects/object_1.model"))
        root = ET.fromstring(archive.read("3D/3dmodel.model"))
    obj = child.find(export.M("resources")).find(export.M("object"))
    assert obj.get("name") == label
    assert obj.find(export.M("metadatagroup")).findall(export.M("metadata"))[1].text == "a < b & c"
    loaded = np.array([[float(v.get(axis)) for axis in "xyz"] for v in obj.iter(export.M("vertex"))])
    center = np.array(root.find('.//' + export.M("component")).get("transform").split()[-3:], dtype=float)
    np.testing.assert_allclose(loaded + center, vertices, atol=1e-9, rtol=0)
    triangles = np.array([[int(t.get(axis)) for axis in ("v1", "v2", "v3")] for t in obj.iter(export.M("triangle"))])
    np.testing.assert_array_equal(triangles, faces)
    np.testing.assert_array_equal(mesh.vertices, vertices)
    np.testing.assert_array_equal(mesh.faces, faces)
    assert len(checks) > 5


def test_multipart_stream_can_cancel_within_one_large_part(tmp_path):
    mesh = trimesh.creation.icosphere(subdivisions=5)
    calls = 0
    class Cancelled(Exception):
        pass
    def checkpoint():
        nonlocal calls
        calls += 1
        if calls == 5:
            raise Cancelled()
    with pytest.raises(Cancelled):
        write_orca_multipart_3mf([LeafMeshSpec("sphere", mesh, {})], str(tmp_path / "cancelled.3mf"),
                                 checkpoint=checkpoint)
    assert calls == 5


def test_compaction_unions_touching_layer_slabs():
    lower = trimesh.creation.box(extents=(2.0, 2.0, 1.0))
    upper = lower.copy()
    upper.apply_translation((0.0, 0.0, 1.0))

    compacted = compact_touching_meshes([lower, upper])

    assert compacted.is_volume
    assert np.isclose(compacted.volume, 8.0)
    assert len(compacted.split()) == 1


def test_curated_recipe_assignment_uses_distinct_tokens():
    colors = np.asarray(
        [
            [0.9, 0.2, 0.2],
            [0.95, 0.8, 0.1],
            [0.1, 0.8, 0.8],
            [0.3, 0.2, 0.8],
        ]
    )
    tokens, _entries = assign_clusters_to_curated_recipes(colors, "CMY")
    assert len(tokens) == len(colors)
    assert len(set(tokens)) == len(colors)


def test_circular_run_smoothing_removes_short_run():
    values = np.asarray(list("ccccmmycccc"), dtype="<U1")
    smoothed = smooth_circular_runs(values, minimum_bins=3)
    assert "m" not in smoothed


def test_vertical_cleanup_removes_short_periodic_label_runs():
    labels = np.zeros((12, 2), dtype=np.int64)
    labels[4, 0] = 1
    labels[4:8, 1] = 1

    result = smooth_label_map_vertical_runs(labels, minimum_rows=2)

    assert np.all(result[:, 0] == 0)
    assert np.array_equal(result[:, 1], labels[:, 1])


def test_component_cleanup_removes_island_but_preserves_ribbon():
    labels = np.zeros((10, 10), dtype=np.int64)
    labels[1, 1] = 1
    labels[4:6, 2:8] = 2

    result = remove_small_label_components(labels, minimum_pixels=4)

    assert result[1, 1] == 0
    assert np.all(result[4:6, 2:8] == 2)


def test_partition_profile_preserves_area():
    profile = Point(0.0, 0.0).buffer(10.0, quad_segs=64)
    chars = np.asarray(["c"] * 60 + ["m"] * 60 + ["y"] * 60, dtype="<U1")
    partitioned = partition_profile(
        profile,
        chars,
        angular_bins=180,
        seam_angle_deg=0.0,
        radius=20.0,
        preserve_component_area_mm2=1.0,
        minimum_piece_area_mm2=0.1,
    )
    assert set(partitioned) == {"c", "m", "y"}
    assert np.isclose(sum(geometry.area for geometry in partitioned.values()), profile.area, rtol=1e-5)
