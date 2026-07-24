from __future__ import annotations

import json
import os
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest


CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
NS = {"m": CORE_NS}


def _pad4(data: bytes) -> bytes:
    return data + (b"\x00" * ((4 - len(data) % 4) % 4))


def _write_triangle_glb(path: Path, *, vertex_rgb=None, material_rgb=None) -> None:
    pygltflib = pytest.importorskip("pygltflib")
    positions = np.asarray([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    indices = np.asarray([0, 1, 2], dtype=np.uint16)
    chunks = []
    offset = 0

    pos_bytes = positions.tobytes()
    chunks.append(pos_bytes)
    pos_offset = offset
    offset += len(pos_bytes)

    color_offset = None
    color_bytes = b""
    if vertex_rgb is not None:
        rgba = np.asarray([list(vertex_rgb) + [1.0]] * 3, dtype=np.float32)
        color_offset = offset
        color_bytes = rgba.tobytes()
        chunks.append(color_bytes)
        offset += len(color_bytes)

    index_offset = offset
    index_bytes = indices.tobytes()
    chunks.append(index_bytes)
    blob = _pad4(b"".join(chunks))

    buffer_views = [
        pygltflib.BufferView(buffer=0, byteOffset=pos_offset, byteLength=len(pos_bytes)),
    ]
    accessors = [
        pygltflib.Accessor(
            bufferView=0,
            byteOffset=0,
            componentType=5126,
            count=3,
            type="VEC3",
            min=[0.0, 0.0, 0.0],
            max=[1.0, 1.0, 0.0],
        )
    ]
    attrs = pygltflib.Attributes(POSITION=0)

    if vertex_rgb is not None:
        buffer_views.append(pygltflib.BufferView(buffer=0, byteOffset=color_offset, byteLength=len(color_bytes)))
        accessors.append(pygltflib.Accessor(bufferView=1, byteOffset=0, componentType=5126, count=3, type="VEC4"))
        attrs.COLOR_0 = 1

    index_view = len(buffer_views)
    index_accessor = len(accessors)
    buffer_views.append(pygltflib.BufferView(buffer=0, byteOffset=index_offset, byteLength=len(index_bytes)))
    accessors.append(pygltflib.Accessor(bufferView=index_view, byteOffset=0, componentType=5123, count=3, type="SCALAR"))

    primitive_kwargs = {"attributes": attrs, "indices": index_accessor, "mode": 4}
    materials = []
    if material_rgb is not None:
        primitive_kwargs["material"] = 0
        materials.append(
            pygltflib.Material(
                pbrMetallicRoughness=pygltflib.PbrMetallicRoughness(baseColorFactor=list(material_rgb) + [1.0])
            )
        )

    gltf = pygltflib.GLTF2(
        asset=pygltflib.Asset(version="2.0"),
        scenes=[pygltflib.Scene(nodes=[0])],
        scene=0,
        nodes=[pygltflib.Node(mesh=0)],
        meshes=[pygltflib.Mesh(primitives=[pygltflib.Primitive(**primitive_kwargs)])],
        buffers=[pygltflib.Buffer(byteLength=len(blob))],
        bufferViews=buffer_views,
        accessors=accessors,
        materials=materials,
    )
    gltf.set_binary_blob(blob)
    gltf.save_binary(str(path))


def _write_colored_box_glb(path: Path, *, vertex_rgb=(0.0, 1.0, 1.0)) -> None:
    pygltflib = pytest.importorskip("pygltflib")
    positions = np.asarray(
        [
            [-0.5, -0.5, -0.5],
            [0.5, -0.5, -0.5],
            [0.5, 0.5, -0.5],
            [-0.5, 0.5, -0.5],
            [-0.5, -0.5, 0.5],
            [0.5, -0.5, 0.5],
            [0.5, 0.5, 0.5],
            [-0.5, 0.5, 0.5],
        ],
        dtype=np.float32,
    )
    indices = np.asarray(
        [
            0, 2, 1, 0, 3, 2,
            4, 5, 6, 4, 6, 7,
            0, 1, 5, 0, 5, 4,
            1, 2, 6, 1, 6, 5,
            2, 3, 7, 2, 7, 6,
            3, 0, 4, 3, 4, 7,
        ],
        dtype=np.uint16,
    )
    colors = np.asarray([list(vertex_rgb) + [1.0]] * len(positions), dtype=np.float32)
    pos_bytes = positions.tobytes()
    color_offset = len(pos_bytes)
    color_bytes = colors.tobytes()
    index_offset = color_offset + len(color_bytes)
    index_bytes = indices.tobytes()
    blob = _pad4(pos_bytes + color_bytes + index_bytes)
    gltf = pygltflib.GLTF2(
        asset=pygltflib.Asset(version="2.0"),
        scenes=[pygltflib.Scene(nodes=[0])],
        scene=0,
        nodes=[pygltflib.Node(mesh=0)],
        meshes=[
            pygltflib.Mesh(
                primitives=[
                    pygltflib.Primitive(
                        attributes=pygltflib.Attributes(POSITION=0, COLOR_0=1),
                        indices=2,
                        mode=4,
                    )
                ]
            )
        ],
        buffers=[pygltflib.Buffer(byteLength=len(blob))],
        bufferViews=[
            pygltflib.BufferView(buffer=0, byteOffset=0, byteLength=len(pos_bytes)),
            pygltflib.BufferView(buffer=0, byteOffset=color_offset, byteLength=len(color_bytes)),
            pygltflib.BufferView(buffer=0, byteOffset=index_offset, byteLength=len(index_bytes)),
        ],
        accessors=[
            pygltflib.Accessor(
                bufferView=0,
                byteOffset=0,
                componentType=5126,
                count=len(positions),
                type="VEC3",
                min=[-0.5, -0.5, -0.5],
                max=[0.5, 0.5, 0.5],
            ),
            pygltflib.Accessor(bufferView=1, byteOffset=0, componentType=5126, count=len(positions), type="VEC4"),
            pygltflib.Accessor(bufferView=2, byteOffset=0, componentType=5123, count=len(indices), type="SCALAR"),
        ],
    )
    gltf.set_binary_blob(blob)
    gltf.save_binary(str(path))


def test_glb_import_reads_vertex_color_and_material_fallback(tmp_path):
    pytest.importorskip("lib3mf")
    from layerloom.glb_import import import_glb_to_3mf

    vertex_glb = tmp_path / "vertex.glb"
    vertex_3mf = tmp_path / "vertex.3mf"
    vertex_manifest = tmp_path / "vertex.colors.json"
    _write_triangle_glb(vertex_glb, vertex_rgb=(1.0, 0.0, 0.0))
    import_glb_to_3mf(str(vertex_glb), str(vertex_3mf), manifest_out=str(vertex_manifest), target_colors=8, scale=1.0)
    data = json.loads(vertex_manifest.read_text())
    assert data["objects"][0]["source_hex"] == "#ff0000"

    material_glb = tmp_path / "material.glb"
    material_3mf = tmp_path / "material.3mf"
    material_manifest = tmp_path / "material.colors.json"
    _write_triangle_glb(material_glb, material_rgb=(0.2, 0.4, 0.6))
    import_glb_to_3mf(str(material_glb), str(material_3mf), manifest_out=str(material_manifest), target_colors=8, scale=1.0)
    data = json.loads(material_manifest.read_text())
    assert data["objects"][0]["source_hex"] == "#336699"


def test_glb_vertex_colors_are_converted_from_linear_to_srgb():
    from layerloom.glb_import import linear_rgb_to_srgb

    # ChimeraX exports #48A840 as this glTF linear-light value.
    linear = np.asarray([[0.06479, 0.391562, 0.051255]], dtype=np.float32)
    display = linear_rgb_to_srgb(linear)

    np.testing.assert_allclose(display, [[0x48 / 255, 0xA8 / 255, 0x40 / 255]], atol=2e-4)


def test_palette_assignment_writes_pat_and_stack_metadata(tmp_path):
    trimesh = pytest.importorskip("trimesh")
    from layerloom.export import write_basic_3mf
    from layerloom.palette_assignment import assign_palette_to_3mf
    from layerloom.transform_3mf import write_transformed_3mf

    source = tmp_path / "source.3mf"
    with_meta = tmp_path / "with_meta.3mf"
    assigned = tmp_path / "assigned.3mf"
    mesh = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
    write_basic_3mf([("cyan_source", mesh)], str(source))
    write_transformed_3mf(
        str(source),
        str(with_meta),
        np.eye(4),
        metadata_updates={"1": {"source_hex": "#3ac8dc"}},
    )

    result = assign_palette_to_3mf(str(with_meta), str(assigned), palette="Normal")
    assert result.assignments["1"]["token"] == "c"

    with zipfile.ZipFile(assigned, "r") as zf:
        root = ET.fromstring(zf.read("3D/3dmodel.model"))
    obj = root.find(".//m:object", NS)
    assert obj is not None
    assert "__PAT_c__" in obj.get("name", "")
    stack = obj.find("m:metadata[@name='stack_token']", NS)
    assert stack is not None and stack.text == "c"


def test_cmy_palette_excludes_black_and_white():
    from layerloom.palette_assignment import load_palette_entries

    entries = load_palette_entries("CMY")
    tokens = {entry["token"] for entry in entries}

    assert tokens
    assert all(set(token) <= {"c", "m", "y"} for token in tokens)
    assert "k" not in tokens
    assert "w" not in tokens


def test_headless_fit_box_scales_centers_and_grounds(tmp_path):
    trimesh = pytest.importorskip("trimesh")
    from layerloom.export import write_basic_3mf
    from layerloom.headless import compute_headless_placement_plan

    source = tmp_path / "box.3mf"
    mesh = trimesh.creation.box(extents=(10.0, 20.0, 5.0))
    mesh.apply_translation((0.0, 0.0, 8.0))
    write_basic_3mf([("box__PAT_c__", mesh)], str(source))

    plan, scale = compute_headless_placement_plan(
        str(source),
        orientation_matrix=np.eye(3),
        fit_box=(180.0, 180.0),
    )
    assert np.isclose(scale, 200.0 / np.linalg.norm([10.0, 20.0, 5.0]))
    assert np.allclose(plan.transformed_bounds.center[:2], [90.0, 90.0])
    assert np.isclose(plan.transformed_bounds.min_corner[2], 0.0)
    assert np.all(plan.transformed_bounds.size[:2] <= [180.0 + 1e-6, 180.0 + 1e-6])
    assert np.linalg.norm(plan.transformed_bounds.size) <= 200.0 + 1e-6


def test_orientation_score_prefers_flipped_downward_face():
    trimesh = pytest.importorskip("trimesh")
    from layerloom.orientation import score_orientation
    from layerloom.transform_3mf import axis_angle_rotation

    vertices = np.asarray(
        [
            [0.0, 0.0, 10.0],
            [1.0, 0.0, 10.0],
            [1.0, 1.0, 10.0],
            [0.0, 1.0, 10.0],
            [0.0, 0.0, 0.0],
        ]
    )
    faces = np.asarray([[0, 2, 1], [0, 3, 2]])
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)

    identity_score = score_orientation(mesh, np.eye(3))
    flipped_score = score_orientation(mesh, axis_angle_rotation(np.array([1.0, 0.0, 0.0]), 180.0))
    assert flipped_score < identity_score


def test_repair_rejection_reason_catches_geometry_loss():
    from layerloom.repair import MeshGeometryCounts, repair_rejection_reason

    reason = repair_rejection_reason(
        MeshGeometryCounts(objects=4, vertices=10_000, triangles=20_000),
        MeshGeometryCounts(objects=4, vertices=1_000, triangles=2_000),
    )
    assert reason is not None
    assert "too much geometry" in reason or "too many vertices" in reason


def test_headless_cli_defaults_and_validation(tmp_path, monkeypatch):
    import layerloom.headless as headless

    monkeypatch.setattr(headless, "default_downloads_dir", lambda: str(tmp_path / "Downloads"))
    assert headless.resolve_output_path("protein.glb").endswith("Downloads/protein_woven.3mf")

    parser = headless.build_parser()
    with pytest.raises(SystemExit) as help_exit:
        parser.parse_args(["--help"])
    assert help_exit.value.code == 0

    with pytest.raises(SystemExit):
        parser.parse_args(["protein.glb", "--fit-box", "100,100", "--max-dim", "200"])

    cfg = headless.config_from_args(parser.parse_args(["protein.glb", "--max-dim", "200"]))
    assert cfg.fit_box is None
    assert cfg.max_dim == 200.0
    assert cfg.palette == "CMY"


@pytest.mark.skipif(
    os.environ.get("LAYERLOOM_RUN_HEADLESS_INTEGRATION") != "1",
    reason="Headless weave integration is opt-in because 0.08 mm weaving can be slow.",
)
def test_headless_pipeline_tiny_glb_opt_in(tmp_path):
    pytest.importorskip("lib3mf")
    pytest.importorskip("pymeshlab")
    from layerloom.headless import HeadlessConfig, run_headless

    glb = tmp_path / "tiny.glb"
    out = tmp_path / "tiny_woven.3mf"
    _write_colored_box_glb(glb, vertex_rgb=(0.0, 1.0, 1.0))
    result = run_headless(
        HeadlessConfig(
            input_path=str(glb),
            output=str(out),
            overwrite=True,
            orientation_quality="none",
            repair=False,
            layer_height=0.5,
        )
    )
    assert Path(result.output_path).exists()
