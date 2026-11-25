"""Import progress stays optional and cached colors retain source appearance."""

import zipfile

import numpy as np
import pytest
import trimesh

from layerloom import paint_io
from layerloom.paint_examples import _write_source_color_fixture_3mf
from layerloom.paint_model import PaintDocument, PaintMesh


@pytest.mark.parametrize("extension", [".llpaint", ".3mf", ".stl", ".glb"])
def test_import_stages_report_reading_parts_and_validation(tmp_path, extension):
    mesh = trimesh.creation.box()
    document = PaintDocument([PaintMesh(
        "box", "Box", mesh.vertices, mesh.faces,
        np.tile([30, 80, 160, 255], (len(mesh.faces), 1)).astype(np.uint8),
    )])
    path = tmp_path / ("box" + extension)
    if extension == ".llpaint":
        paint_io.save_paint_document(document, path)
    elif extension == ".3mf":
        _write_source_color_fixture_3mf(document, path)
    else:
        mesh.export(path)
    stages = []
    loaded = paint_io.load_paint_document(path, stage=stages.append)
    plain = paint_io.load_paint_document(path)
    assert stages[0].startswith("Reading")
    assert any("part 1" in message.lower() for message in stages)
    assert stages[-1] == "Checking model geometry and colors…"
    for attribute in ("vertices", "faces", "face_rgba", "source_rgba"):
        np.testing.assert_array_equal(getattr(loaded.meshes[0], attribute),
                                      getattr(plain.meshes[0], attribute))


def test_repeated_color_properties_are_decoded_once_without_changing_gradients(tmp_path, monkeypatch):
    triangles = ['<triangle v1="0" v2="1" v3="2" p1="0"/>'] * 200
    triangles += ['<triangle v1="0" v2="1" v3="2" p1="0" p2="1" p3="1"/>'] * 200
    root = f'''<model xmlns="{paint_io.CORE}"><resources>
    <basematerials id="5"><base name="red" displaycolor="#FF0000FF"/>
    <base name="blue" displaycolor="#0000FFFF"/></basematerials>
    <object id="1" pid="5"><mesh><vertices><vertex x="0" y="0" z="0"/>
    <vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/></vertices>
    <triangles>{''.join(triangles)}</triangles></mesh></object>
    </resources><build><item objectid="1"/></build></model>'''
    path = tmp_path / "repeated_colors.3mf"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("3D/3dmodel.model", root)
    decoded = []
    original = paint_io._hex_rgba

    def counted(value):
        decoded.append(value)
        return original(value)

    monkeypatch.setattr(paint_io, "_hex_rgba", counted)
    document = paint_io.load_paint_document(path)
    colors = document.meshes[0].face_rgba
    np.testing.assert_array_equal(colors[:200], np.tile([255, 0, 0, 255], (200, 1)))
    np.testing.assert_array_equal(colors[200:], np.tile([85, 0, 170, 255], (200, 1)))
    assert len(decoded) == 6
    assert len(document.metadata["warnings"]) == 1


def test_stage_failure_stops_import(tmp_path):
    def cancelled(message):
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        paint_io.load_paint_document(tmp_path / "unread.3mf", stage=cancelled)
