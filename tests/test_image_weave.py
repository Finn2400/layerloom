from __future__ import annotations

import zipfile
from xml.etree import ElementTree as ET

import numpy as np
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
