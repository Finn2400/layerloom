"""Legacy Arrange Save is geometry-only, including marked/cross-model input."""
import copy
import hashlib
from pathlib import Path
import zipfile
from xml.etree import ElementTree as ET

import numpy as np
import trimesh

from layerloom.normalize_3mf_import import NORMALIZED_FORMAT_VERSION, normalize_3mf_import
from layerloom.paint_examples import _write_source_color_fixture_3mf
from layerloom.paint_model import PaintDocument, PaintMesh
from layerloom.transform_3mf import write_transformed_3mf

CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
MAT = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"
PROD = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
M = lambda tag: f"{{{CORE}}}{tag}"


def _marked_painted_archive(tmp_path, *, child=True):
    source = Path(tmp_path) / "marked_painted.3mf"
    mesh = trimesh.creation.box((2, 2, 2))
    colors = np.tile([160, 80, 210, 255], (len(mesh.faces), 1)).astype(np.uint8)
    _write_source_color_fixture_3mf(PaintDocument([PaintMesh("cube", "cube", mesh.vertices, mesh.faces, colors)]), source)
    with zipfile.ZipFile(source) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    root = ET.fromstring(members["3D/3dmodel.model"])
    for name, text in [("LayerLoomNormalized", "1"), ("LayerLoomNormalizedVersion", NORMALIZED_FORMAT_VERSION)]:
        node = ET.Element(M("metadata"), {"name": name})
        node.text = text
        root.insert(0, node)
    tri = next(root.iter(M("triangle")))
    tri.set("paint_color", "1")
    tri.set("mmu_segmentation", "1")
    tri.set("paint_supports", "1")
    resources = root.find(M("resources"))
    base = ET.SubElement(resources, M("basematerials"), {"id": "10"})
    ET.SubElement(base, M("base"), {"name": "physical material", "displaycolor": "#A050D2FF"})
    obj = resources.find(M("object"))
    obj.set("pid", "10")
    obj.set("pindex", "0")
    if child:
        child_root = copy.deepcopy(root)
        child_resources = child_root.find(M("resources"))
        child_resources.find(M("object")).set("pid", "2")
        ET.SubElement(child_resources, f"{{{MAT}}}texture2d", {
            "id": "20", "path": "../Textures/paint.png", "contenttype": "image/png"})
        ET.SubElement(child_resources, f"{{{MAT}}}texture2dgroup", {"id": "21", "texid": "20"})
        ET.register_namespace("p", PROD)
        root.set("requiredextensions", "m p")
        parent = ET.SubElement(resources, M("object"), {"id": "3", "type": "model"})
        components = ET.SubElement(parent, M("components"))
        ET.SubElement(components, M("component"), {"objectid": "1", f"{{{PROD}}}path": "/3D/Objects/child.model"})
        ET.SubElement(root.find(M("build")), M("item"), {"objectid": "3"})
        members["3D/Objects/child.model"] = ET.tostring(child_root)
        members["3D/Textures/paint.png"] = b"fixture texture payload"
        members["3D/Objects/_rels/child.model.rels"] = (
            f'<Relationships xmlns="{REL}"><Relationship Id="texture" Target="../Textures/paint.png" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dtexture"/></Relationships>').encode()
        content_types = ET.fromstring(members["[Content_Types].xml"])
        ET.SubElement(content_types, "{http://schemas.openxmlformats.org/package/2006/content-types}Override", {
            "PartName": "/3D/Textures/paint.png", "ContentType": "image/png"})
        members["[Content_Types].xml"] = ET.tostring(content_types)
    members["3D/3dmodel.model"] = ET.tostring(root)
    members["Metadata/model_settings.config"] = b'<config><object id="1"><metadata key="extruder" value="2"/></object></config>'
    with zipfile.ZipFile(source, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return source


def _assert_geometry_only(path):
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.endswith(".model"):
                continue
            root = ET.fromstring(archive.read(name))
            for node in root.iter():
                local = node.tag.rsplit("}", 1)[-1]
                assert local not in {"colorgroup", "texture2d", "texture2dgroup", "multiproperties", "compositematerials"}
                if local == "triangle":
                    assert set(node.attrib) == {"v1", "v2", "v3"}
                assert not any(key.rsplit("}", 1)[-1].startswith("paint_") or key.rsplit("}", 1)[-1] == "mmu_segmentation"
                               for key in node.attrib)


def test_geometry_save_scrubs_marked_input_and_referenced_models_without_changing_source(tmp_path):
    source = _marked_painted_archive(tmp_path)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    # A previous LayerLoom marker survives repainting in external applications.
    normalized = normalize_3mf_import(str(source)).normalized_path
    assert normalized == str(source)
    output = tmp_path / "geometry.3mf"
    write_transformed_3mf(normalized, str(output), np.eye(4), geometry_only=True)
    _assert_geometry_only(output)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    with zipfile.ZipFile(source) as before, zipfile.ZipFile(output) as after:
        assert "3D/Textures/paint.png" not in after.namelist()
        assert before.read("Metadata/model_settings.config") == after.read("Metadata/model_settings.config")
        for name in ["3D/3dmodel.model", "3D/Objects/child.model"]:
            original = ET.fromstring(before.read(name))
            saved = ET.fromstring(after.read(name))
            assert [node.attrib for node in original.iter(M("vertex"))] == [node.attrib for node in saved.iter(M("vertex"))]
            assert [tuple(node.get(key) for key in ("v1", "v2", "v3")) for node in original.iter(M("triangle"))] == [
                tuple(node.get(key) for key in ("v1", "v2", "v3")) for node in saved.iter(M("triangle"))]
            assert saved.find(f"{M('resources')}/{M('basematerials')}") is not None
        root = ET.fromstring(after.read("3D/3dmodel.model"))
        assert root.find(f"{M('resources')}/{M('object')}").get("pid") == "10"
        assert root.get("requiredextensions") == "p"
        child = ET.fromstring(after.read("3D/Objects/child.model"))
        assert child.find(f"{M('resources')}/{M('object')}").get("pid") is None
        assert child.get("requiredextensions") is None
        assert not list(ET.fromstring(after.read("3D/Objects/_rels/child.model.rels")))
        assert not any(node.get("PartName") == "/3D/Textures/paint.png"
                       for node in ET.fromstring(after.read("[Content_Types].xml")))


def test_transform_default_preserves_import_paint_and_textures(tmp_path):
    source = _marked_painted_archive(tmp_path)
    output = tmp_path / "preview.3mf"
    write_transformed_3mf(str(source), str(output), np.eye(4))
    with zipfile.ZipFile(output) as archive:
        assert "3D/Textures/paint.png" in archive.namelist()
        for name in ["3D/3dmodel.model", "3D/Objects/child.model"]:
            root = ET.fromstring(archive.read(name))
            triangle = next(root.iter(M("triangle")))
            assert triangle.get("paint_color") == "1" and triangle.get("p1") == "0"


def test_geometry_save_restores_root_namespace_after_differently_prefixed_child(tmp_path):
    source = _marked_painted_archive(tmp_path)
    with zipfile.ZipFile(source) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    child = ET.fromstring(members["3D/Objects/child.model"])
    child.find(f"{M('resources')}/{M('object')}").set(f"{{{PROD}}}UUID", "child-object")
    child.set("requiredextensions", "m q")
    ET.register_namespace("q", PROD)
    members["3D/Objects/child.model"] = ET.tostring(child)
    with zipfile.ZipFile(source, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    output = tmp_path / "different_prefixes.3mf"
    write_transformed_3mf(str(source), str(output), np.eye(4), geometry_only=True)
    import io
    with zipfile.ZipFile(output) as archive:
        for name, required in [("3D/3dmodel.model", "p"), ("3D/Objects/child.model", "q")]:
            content = archive.read(name)
            namespaces = {prefix: uri for _, (prefix, uri) in ET.iterparse(io.BytesIO(content), events=("start-ns",))}
            root = ET.fromstring(content)
            assert root.get("requiredextensions") == required
            assert namespaces[required] == PROD


def test_geometry_save_rejects_unbound_required_extension_before_writing(tmp_path):
    import pytest
    source = _marked_painted_archive(tmp_path, child=False)
    with zipfile.ZipFile(source) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    root = ET.fromstring(members["3D/3dmodel.model"])
    root.set("requiredextensions", "unbound")
    members["3D/3dmodel.model"] = ET.tostring(root)
    with zipfile.ZipFile(source, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    output = tmp_path / "invalid.3mf"
    with pytest.raises(ValueError, match="unbound required extension"):
        write_transformed_3mf(str(source), str(output), np.eye(4), geometry_only=True)
    assert not output.exists()
