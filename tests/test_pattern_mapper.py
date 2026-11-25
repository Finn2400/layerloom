from __future__ import annotations

import numpy as np

from layerloom.pattern_mapper import (
    choose_wide_palette,
    choose_direct_cmy_palette,
    linear_to_srgb,
    map_vertex_colors,
    oklab_to_srgb,
    srgb_to_linear,
    srgb_to_oklab,
    voxel_union_mesh,
    write_glb,
)


def test_srgb_and_oklab_round_trips():
    colors = np.asarray(
        [
            [0.0, 0.0, 0.0],
            [1.0, 1.0, 1.0],
            [0.15, 0.72, 0.91],
            [0.88, 0.18, 0.43],
        ]
    )
    assert np.allclose(linear_to_srgb(srgb_to_linear(colors)), colors, atol=1e-7)
    assert np.allclose(oklab_to_srgb(srgb_to_oklab(colors)), colors, atol=2e-6)


def test_mapping_uses_only_prepared_palette():
    vertices = np.asarray(
        [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 1.0],
            [-1.0, 0.0, 2.0],
            [0.0, -1.0, 3.0],
        ]
    )
    normals = np.asarray([[1.0, 0.0, 0.0]] * 4)
    tile = np.asarray(
        [
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            [[0.0, 0.0, 1.0], [1.0, 1.0, 0.0]],
        ]
    )
    mapped = map_vertex_colors(vertices, normals, tile, period_mm=4.0, seam_angle_deg=90.0)
    palette = {tuple(color) for color in tile.reshape(-1, 3)}
    assert all(tuple(color) in palette for color in mapped)


def test_wide_palette_preserves_lightness_order():
    source = np.asarray(
        [
            [0.05, 0.03, 0.08],
            [0.35, 0.40, 0.50],
            [0.92, 0.90, 0.84],
        ]
    )
    mapped, _entries = choose_wide_palette(source, "CMY")
    lightness = srgb_to_oklab(mapped)[:, 0]
    assert np.all(np.diff(lightness) >= -1e-6)


def test_direct_cmy_uses_each_primary_once():
    source = np.asarray(
        [
            [0.10, 0.70, 0.82],
            [0.75, 0.08, 0.55],
            [0.95, 0.82, 0.05],
        ]
    )
    mapped, entries = choose_direct_cmy_palette(source, "CMY")
    assert {entry["token"] for entry in entries} == {"c", "m", "y"}
    assert len(np.unique(mapped, axis=0)) == 3


def test_glb_contains_position_color_and_indices(tmp_path):
    pygltflib = __import__("pygltflib")
    output = tmp_path / "colored.glb"
    positions = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float)
    faces = np.asarray([[0, 1, 2]], dtype=np.uint32)
    colors = np.asarray([[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)

    write_glb(output, positions, faces, colors, name="test")

    gltf = pygltflib.GLTF2().load_binary(str(output))
    primitive = gltf.meshes[0].primitives[0]
    assert primitive.attributes.POSITION == 0
    assert primitive.attributes.COLOR_0 == 1
    assert primitive.indices == 2
    assert gltf.accessors[1].type == "VEC4"
    assert gltf.accessors[2].componentType == pygltflib.UNSIGNED_INT


def test_voxel_union_mesh_is_watertight_and_connected():
    trimesh = __import__("trimesh")
    cells = np.asarray([[0, 0, 0], [1, 0, 0], [1, 1, 0]], dtype=np.int64)
    vertices, faces = voxel_union_mesh(cells, 0.7)
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    assert mesh.is_watertight
    assert mesh.body_count == 1
    assert np.isclose(mesh.volume, 3 * 0.7**3)
