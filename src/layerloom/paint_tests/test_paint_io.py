"""Behavioral checks for color retention, slicer split geometry and projects."""
from pathlib import Path
import json
import zipfile
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import trimesh

from layerloom.paint_model import PaintDocument, PaintMesh
from layerloom.paint_io import (CORE, MAT, decode_paint_annotation,
                               load_paint_document, save_paint_document)


def _doc(mesh):
    colors = np.tile([20, 90, 180, 255], (len(mesh.faces), 1)).astype(np.uint8)
    colors[::2] = [250, 10, 40, 255]
    return PaintDocument([PaintMesh("shape", "Shape", mesh.vertices, mesh.faces, colors)],
                         "/original/source.3mf", {"palette": "Normal", "warnings": []})


@pytest.mark.parametrize("shape", ["cube", "sphere"])
def test_editable_project_preserves_paint_and_erase_source(tmp_path, shape):
    original = trimesh.creation.box() if shape == "cube" else trimesh.creation.icosphere(subdivisions=2)
    doc = _doc(original)
    doc.meshes[0].face_rgba[1:4] = [0, 255, 0, 255]
    path = tmp_path / f"{shape}.llpaint"
    save_paint_document(doc, path)
    loaded = load_paint_document(path)
    assert loaded.metadata == doc.metadata
    assert loaded.source_path == doc.source_path
    for key in ("vertices", "faces", "face_rgba", "source_rgba"):
        np.testing.assert_array_equal(getattr(loaded.meshes[0], key), getattr(doc.meshes[0], key))
    assert not np.array_equal(loaded.meshes[0].face_rgba, loaded.meshes[0].source_rgba)
    copied = loaded.copy()
    copied.meshes[0].face_rgba[0] = 0
    assert not np.array_equal(copied.meshes[0].face_rgba, loaded.meshes[0].face_rgba)


def test_standard_3mf_retains_per_face_colors_and_disconnected_objects(tmp_path):
    doc = _doc(trimesh.creation.box())
    sphere = _doc(trimesh.creation.icosphere(subdivisions=1)).meshes[0]
    sphere.key, sphere.name = "sphere", "Sphere"
    sphere.vertices += [10., 20., 30.]
    doc.meshes.append(sphere)
    path = tmp_path / "two_objects.3mf"
    save_paint_document(doc, path)
    loaded = load_paint_document(path)
    assert len(loaded.meshes) == 2
    for before, after in zip(doc.meshes, loaded.meshes):
        assert before.name == after.name
        for key in ("vertices", "faces", "face_rgba"):
            np.testing.assert_array_equal(getattr(before, key), getattr(after, key))


def _native_file(tmp_path, *, paint="481", attribute="paint_color", colors=None):
    doc = _doc(trimesh.creation.box())
    path = tmp_path / "native.3mf"
    save_paint_document(doc, path)
    with zipfile.ZipFile(path) as z:
        files = {n: z.read(n) for n in z.namelist()}
    root = ET.fromstring(files["3D/3dmodel.model"])
    # Strip standard colors to isolate native slot and split interpretation.
    for tr in root.findall(f".//{{{CORE}}}triangle"):
        for key in ("pid", "p1", "p2", "p3"):
            tr.attrib.pop(key, None)
    root.find(f".//{{{CORE}}}triangle").set(attribute, paint)
    files["3D/3dmodel.model"] = ET.tostring(root)
    files["Metadata/project_settings.config"] = json.dumps({"filament_colour": colors or ["#FF0000", "#0000FF"]}).encode()
    files["Metadata/model_settings.config"] = b'<config><object id="1"><metadata key="extruder" value="1"/></object></config>'
    with zipfile.ZipFile(path, "w") as z:
        for n, data in files.items():
            z.writestr(n, data)
    return path, doc


@pytest.mark.parametrize("attribute", ["paint_color", "mmu_segmentation"])
def test_native_split_keeps_shape_closed_and_maps_slots(tmp_path, attribute):
    path, original = _native_file(tmp_path, attribute=attribute)
    doc = load_paint_document(path)
    mesh = doc.meshes[0]
    solid = trimesh.Trimesh(mesh.vertices, mesh.faces, process=False)
    assert solid.is_watertight
    assert solid.is_winding_consistent
    assert solid.volume == pytest.approx(1.0)
    assert len(mesh.faces) > len(original.meshes[0].faces)
    np.testing.assert_allclose(solid.bounds, [-.5 * np.ones(3), .5 * np.ones(3)])
    blue = np.all(mesh.face_rgba == [0, 0, 255, 255], axis=1)
    # One half of one cube-side triangle is blue (cube triangle area=.5).
    assert solid.area_faces[blue].sum() == pytest.approx(.25)
    assert doc.metadata["paint_split_annotations"] == 1


@pytest.mark.parametrize("code,state", [("0", 0), ("4", 1), ("8", 2), ("0C", 3), ("EC", 17), ("0FC", 18), ("2FFC", 35)])
def test_native_extended_states(code, state):
    assert decode_paint_annotation(code, (0, 1, 2), None) == [((0, 1, 2), state)]


@pytest.mark.parametrize("code", ["x", "C", "1", "04", "F000"])
def test_invalid_paint_fails_instead_of_silently_dropping(code):
    with pytest.raises(ValueError):
        decode_paint_annotation(code, (0, 1, 2), lambda a, b: 3)


def test_missing_native_palette_slot_is_explicit(tmp_path):
    path, _ = _native_file(tmp_path, paint="0C")
    with pytest.raises(ValueError, match="filament slot 3"):
        load_paint_document(path)


def test_component_build_transforms_units_and_instancing(tmp_path):
    path = tmp_path / "components.3mf"
    root = f'''<model xmlns="{CORE}" unit="inch"><resources>
    <basematerials id="5"><base name="green" displaycolor="#00FF00FF"/></basematerials>
    <object id="1" name="Part" pid="5" pindex="0"><mesh><vertices>
    <vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/>
    </vertices><triangles><triangle v1="0" v2="1" v3="2"/></triangles></mesh></object>
    <object id="2"><components><component objectid="1" transform="1 0 0 0 1 0 0 0 1 1 0 0"/></components></object>
    </resources><build><item objectid="2" transform="1 0 0 0 1 0 0 0 1 0 2 0"/>
    <item objectid="2" transform="1 0 0 0 1 0 0 0 1 0 3 0"/>
    <item objectid="1" printable="0"/></build></model>'''
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("3D/3dmodel.model", root)
    doc = load_paint_document(path)
    assert len(doc.meshes) == 2
    np.testing.assert_allclose(doc.meshes[0].vertices[0], [25.4, 50.8, 0])
    np.testing.assert_allclose(doc.meshes[1].vertices[0], [25.4, 76.2, 0])
    np.testing.assert_array_equal(doc.meshes[0].face_rgba, [[0, 255, 0, 255]])


def test_stl_loads_without_changing_solid(tmp_path):
    source = trimesh.creation.icosphere(subdivisions=2, radius=5)
    path = tmp_path / "sphere.stl"
    source.export(path)
    doc = load_paint_document(path)
    assert len(doc.meshes) == 1
    m = doc.meshes[0]
    assert len(m.faces) == len(source.faces)
    np.testing.assert_allclose(np.sort(m.vertices, axis=0).min(axis=0), source.bounds[0], atol=1e-6)


def test_paint_model_rejects_bad_geometry():
    with pytest.raises(ValueError, match="invalid vertex"):
        PaintMesh("bad", "Bad", np.zeros((3, 3)), [[0, 1, 3]], [[0, 0, 0, 255]])


def _write_glb(path, vertex_color=None, material_color=None, texture=False):
    gl = pytest.importorskip("pygltflib")
    arrays = [np.asarray([[0, 0, 0], [.01, 0, 0], [0, .01, 0]], dtype=np.float32),
              np.asarray([0, 1, 2], dtype=np.uint16)]
    types, components = ["VEC3", "SCALAR"], [5126, 5123]
    attrs = gl.Attributes(POSITION=0)
    if vertex_color is not None:
        attrs.COLOR_0 = len(arrays)
        arrays.append(np.tile(list(vertex_color) + [1.], (3, 1)).astype(np.float32))
        types.append("VEC4")
        components.append(5126)
    if texture:
        attrs.TEXCOORD_0 = len(arrays)
        arrays.append(np.asarray([[0, 0], [1, 0], [0, 1]], dtype=np.float32))
        types.append("VEC2")
        components.append(5126)
    blob = bytearray()
    views, accessors = [], []
    for array, dtype, component in zip(arrays, types, components):
        blob.extend(b"\0" * (-len(blob) % 4))
        views.append(gl.BufferView(buffer=0, byteOffset=len(blob), byteLength=array.nbytes))
        accessors.append(gl.Accessor(bufferView=len(views)-1, componentType=component,
                                     count=len(array), type=dtype))
        blob.extend(array.tobytes())
    material = None
    materials, textures, images = [], [], []
    if material_color is not None or texture:
        pbr = gl.PbrMetallicRoughness(baseColorFactor=list(material_color or [1., 1., 1.]) + [1.])
        if texture:
            import io, base64
            from PIL import Image
            buf = io.BytesIO()
            Image.new("RGBA", (2, 2), (255, 0, 0, 255)).save(buf, format="PNG")
            images = [gl.Image(uri="data:image/png;base64," + base64.b64encode(buf.getvalue()).decode())]
            textures = [gl.Texture(source=0)]
            pbr.baseColorTexture = gl.TextureInfo(index=0)
        materials = [gl.Material(pbrMetallicRoughness=pbr)]
        material = 0
    doc = gl.GLTF2(asset=gl.Asset(version="2.0"), scene=0, scenes=[gl.Scene(nodes=[0])],
                   nodes=[gl.Node(mesh=0)], meshes=[gl.Mesh(primitives=[gl.Primitive(attributes=attrs, indices=1, material=material)])],
                   materials=materials, textures=textures, images=images,
                   buffers=[gl.Buffer(byteLength=len(blob))], bufferViews=views, accessors=accessors)
    doc.set_binary_blob(bytes(blob))
    doc.save_binary(str(path))


@pytest.mark.parametrize("kind,expected", [("vertex", [124,170,203]), ("material", [124,170,203]),
                                            ("combined", [89,124,149]), ("texture", [137,0,0])])
def test_glb_linear_colors_materials_texture_and_units(tmp_path, kind, expected):
    path = tmp_path / f"{kind}.glb"
    _write_glb(path, vertex_color=[.2,.4,.6] if kind in ("vertex", "combined") else None,
               material_color=[.2,.4,.6] if kind == "material" else [.5,.5,.5] if kind == "combined" else [.25,.25,.25] if kind == "texture" else None,
               texture=kind == "texture")
    doc = load_paint_document(path)
    np.testing.assert_allclose(doc.meshes[0].face_rgba[0,:3], expected, atol=1)
    np.testing.assert_allclose(doc.meshes[0].vertices.max(axis=0), [10,10,0], atol=1e-5)


def test_local_baby_dragon_keeps_paint_and_both_build_instances():
    import os
    value = os.environ.get("LAYERLOOM_PAINT_DRAGON_FIXTURE")
    if not value:
        pytest.skip("Set LAYERLOOM_PAINT_DRAGON_FIXTURE for the optional user-source regression")
    path = Path(value)
    if not path.exists():
        pytest.skip("Optional user-download regression fixture is not distributed")
    doc = load_paint_document(path)
    assert len(doc.meshes) == 2
    assert doc.metadata["paint_annotations"] == 1165368
    assert doc.metadata["paint_split_annotations"] == 1738
    assert all(len(m.faces) == 1384249 for m in doc.meshes)
    for mesh in doc.meshes:
        assert len(np.unique(mesh.face_rgba, axis=0)) == 4
        assert {tuple(c) for c in np.unique(mesh.face_rgba, axis=0)} == {
            (179, 102, 255, 255), (0, 0, 0, 255), (255, 255, 255, 255), (255, 255, 0, 255)}
    np.testing.assert_array_equal(doc.meshes[0].face_rgba, doc.meshes[1].face_rgba)
    np.testing.assert_array_equal(doc.meshes[0].faces, doc.meshes[1].faces)
