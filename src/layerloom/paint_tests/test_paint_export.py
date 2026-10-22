"""Printable surface paint: volume, material boundaries, and shared alignment."""

from types import SimpleNamespace
import json
import zipfile
from xml.etree import ElementTree as ET

import numpy as np
import pytest
import trimesh

from layerloom.paint_export import assign_palette, export_painted_3mf


def document(*meshes):
    items = []
    for index, mesh in enumerate(meshes):
        rgba = np.asarray([[58, 200, 220, 255]] * len(mesh.faces), dtype=np.uint8)
        rgba[np.asarray(mesh.triangles_center)[:, 0] > mesh.centroid[0]] = [195, 25, 150, 255]
        items.append(SimpleNamespace(key=str(index), name=f"test {index}", vertices=mesh.vertices.copy(),
                                     faces=mesh.faces.copy(), face_rgba=rgba, source_rgba=rgba.copy()))
    return SimpleNamespace(meshes=items)


def assert_clean_report(report):
    assert report["maximum_internal_layer_gap_mm2"] < 1e-7
    assert report["maximum_layer_overlap_mm2"] < 1e-7
    assert report["output_overlap_mm3"] < 1e-5
    assert report["output_volume_mm3"] == pytest.approx(report["sampled_volume_mm3"], rel=1e-5)
    assert all(not any(part["defects"].values()) for part in report["filaments"])
    assert all(not any(part["placed_float32_defects"].values()) for part in report["filaments"])


def test_cube_paint_export_retains_volume_and_materials(tmp_path):
    doc = document(trimesh.creation.box((4, 4, 4)))
    before = [array.copy() for array in (doc.meshes[0].vertices, doc.meshes[0].faces, doc.meshes[0].face_rgba)]
    progress = []
    report = export_painted_3mf(doc, tmp_path / "cube.3mf", pitch=1, layer_height=1,
                               progress=lambda done, total: progress.append((done, total)))
    assert_clean_report(report)
    assert report["output_volume_mm3"] == pytest.approx(64)
    assert {row["token"] for row in report["filaments"]} == {"c", "m"}
    assert progress[-1] == (4, 4)
    assert json.loads((tmp_path / "cube.paint-report.json").read_text())["paint_policy"] == "nearest_surface_full_depth"
    for original, current in zip(before, (doc.meshes[0].vertices, doc.meshes[0].faces, doc.meshes[0].face_rgba)):
        assert np.array_equal(original, current)
    with zipfile.ZipFile(tmp_path / "cube.3mf") as zf:
        root = ET.fromstring(zf.read("3D/3dmodel.model"))
        metadata = {node.get("name"): node.text for node in root
                    if node.tag.rsplit("}", 1)[-1] == "metadata"}
        assert metadata["Application"] == "LayerLoom Paint Prototype"
        assert "BambuStudio:3mfVersion" not in metadata
        colors = json.loads(zf.read("Metadata/project_settings.config"))
        assert colors["filament_colour"] == [row["hex"] for row in report["filaments"]]
        settings = ET.fromstring(zf.read("Metadata/model_settings.config"))
        parts = settings.findall("object/part")
        assert len(parts) == 2
        assert {part.find("metadata[@key='extruder']").get("value") for part in parts} == {"1", "2"}
    from layerloom.paint_io import load_paint_document
    reimported = load_paint_document(tmp_path / "cube.3mf")
    assert len(reimported.meshes) == 2
    assert sum(len(mesh.faces) for mesh in reimported.meshes) == sum(row["triangles"] for row in report["filaments"])
    assert {tuple(mesh.face_rgba[0]) for mesh in reimported.meshes} == {(58, 200, 220, 255), (195, 25, 150, 255)}


def test_sphere_curved_paint_exports_closed_disjoint_parts(tmp_path):
    doc = document(trimesh.creation.icosphere(subdivisions=1, radius=2))
    report = export_painted_3mf(doc, tmp_path / "sphere.3mf", pitch=0.8, layer_height=0.5)
    assert_clean_report(report)
    assert abs(report["relative_volume_change"]) < 0.05
    assert {row["token"] for row in report["filaments"]} == {"c", "m"}


def test_disjoint_components_are_retained_and_translated_together(tmp_path):
    a = trimesh.creation.box((2, 2, 2))
    b = a.copy()
    b.apply_translation((5, 0, 0))
    doc = document(a, b)
    report = export_painted_3mf(doc, tmp_path / "two.3mf", pitch=1, layer_height=1)
    assert_clean_report(report)
    assert len(report["source_meshes"]) == 2
    assert report["output_volume_mm3"] == pytest.approx(16)
    assert report["shared_translation_mm"] == pytest.approx([-2.5, 0, 1])


def test_uv_seam_vertices_are_welded_without_discarding_paint(tmp_path):
    mesh = trimesh.creation.box((2, 2, 2))
    doc = document(mesh)
    item = doc.meshes[0]
    item.vertices = item.vertices[item.faces].reshape(-1, 3)
    item.faces = np.arange(len(item.vertices)).reshape(-1, 3)
    report = export_painted_3mf(doc, tmp_path / "seams.3mf", pitch=1, layer_height=1)
    assert_clean_report(report)
    assert report["source_meshes"][0]["welded_vertices"] == 28
    assert report["output_volume_mm3"] == pytest.approx(8)


def test_mixed_recipe_uses_global_layer_phase(tmp_path):
    doc = document(trimesh.creation.box((2, 2, 4)))
    doc.meshes[0].face_rgba[:] = [90, 90, 180, 255]
    entries = assign_palette(doc, "CMY")
    source_hex = next(iter(entries))
    report = export_painted_3mf(doc, tmp_path / "mixed.3mf", pitch=1, layer_height=1,
                               palette_name="CMY", recipe_overrides={source_hex: "cm"})
    assert_clean_report(report)
    volumes = {row["token"]: row["volume_mm3"] for row in report["filaments"]}
    assert volumes == pytest.approx({"c": 8, "m": 8})


def test_direct_assignment_only_uses_one_filament():
    doc = document(trimesh.creation.box())
    doc.meshes[0].face_rgba[:] = [110, 80, 160, 255]
    assert all(len(row["token"]) == 1 for row in assign_palette(doc, mode="direct").values())


def test_open_surface_is_rejected_before_writing(tmp_path):
    doc = document(trimesh.creation.box())
    doc.meshes[0].faces = doc.meshes[0].faces[:-1]
    doc.meshes[0].face_rgba = doc.meshes[0].face_rgba[:-1]
    path = tmp_path / "open.3mf"
    with pytest.raises(ValueError, match="closed"):
        export_painted_3mf(doc, path)
    assert not path.exists()


def test_overlapping_solids_and_unbounded_work_are_rejected(tmp_path):
    mesh = trimesh.creation.box((2, 2, 2))
    with pytest.raises(ValueError, match="Overlapping"):
        export_painted_3mf(document(mesh, mesh.copy()), tmp_path / "overlap.3mf")
    with pytest.raises(ValueError, match="grid cells"):
        export_painted_3mf(document(mesh), tmp_path / "large.3mf", max_cells=1)


def test_transparency_requires_an_explicit_opaque_choice(tmp_path):
    doc = document(trimesh.creation.box())
    doc.meshes[0].face_rgba[0, 3] = 0
    with pytest.raises(ValueError, match="transparent"):
        export_painted_3mf(doc, tmp_path / "alpha.3mf")


def test_tiny_disconnected_component_cannot_silently_disappear(tmp_path):
    main = trimesh.creation.box((2, 2, 2))
    tiny = trimesh.creation.box((0.1, 0.1, 0.1))
    tiny.apply_translation((3, 0, 0))
    with pytest.raises(ValueError, match="disconnected component falls between"):
        export_painted_3mf(document(main, tiny), tmp_path / "missing.3mf", layer_height=1)


def test_xy_precision_cannot_silently_remove_a_tall_disconnected_component(tmp_path):
    main = trimesh.creation.box((2, 2, 2))
    # This component intersects every layer midpoint, but its two X coordinates
    # (3.0, 3.0004) both snap to 3.0 at the default 0.001 mm XY precision.
    narrow = trimesh.creation.box((0.0004, 0.0018, 2))
    narrow.apply_translation((3.0002, 0, 0))
    doc = document(main, narrow)
    for mesh in doc.meshes:
        mesh.face_rgba[:] = [0, 255, 255, 255]
    output = tmp_path / "missing_xy.3mf"
    with pytest.raises(ValueError, match="XY precision.*disconnected source component"):
        export_painted_3mf(doc, output, pitch=0.5, layer_height=1)
    assert not output.exists()
    assert not output.with_suffix(".paint-report.json").exists()


def test_layer_holes_are_preserved(tmp_path):
    annulus = trimesh.creation.annulus(r_min=1, r_max=2, height=2, sections=16)
    report = export_painted_3mf(document(annulus), tmp_path / "hole.3mf", pitch=0.5, layer_height=0.5)
    assert_clean_report(report)
    assert report["output_volume_mm3"] == pytest.approx(annulus.volume, rel=0.001)


@pytest.mark.parametrize("shape", ["cube", "sphere"])
def test_source_colors_keep_four_exact_rgb_filaments_and_all_instances(tmp_path, shape):
    base = trimesh.creation.box((4, 4, 4)) if shape == "cube" else trimesh.creation.icosphere(subdivisions=1, radius=2)
    second = base.copy()
    second.apply_translation((6, 0, 0))
    doc = document(base, second)
    colors = np.array([[0, 0, 0, 255], [179, 102, 255, 255],
                       [255, 255, 0, 255], [255, 255, 255, 255]], dtype=np.uint8)
    for item in doc.meshes:
        centers = item.vertices[item.faces].mean(axis=1) - item.vertices.mean(axis=0)
        quadrants = (centers[:, 0] > 0).astype(int) + 2 * (centers[:, 1] > 0).astype(int)
        item.face_rgba[:] = colors[quadrants]
    mapping = assign_palette(doc, "not-a-palette", mode="source")
    expected = {"#000000", "#B366FF", "#FFFF00", "#FFFFFF"}
    assert set(mapping) == expected
    assert all(row["token"] == row["hex"] == key for key, row in mapping.items())
    report = export_painted_3mf(doc, tmp_path / f"{shape}.3mf", mode="source", palette_name="not-a-palette",
                               pitch=0.5, layer_height=0.5)
    assert_clean_report(report)
    assert len(report["source_meshes"]) == 2
    assert {row["hex"] for row in report["filaments"]} == expected
    assert [row["extruder"] for row in report["filaments"]] == [1, 2, 3, 4]
    from layerloom.paint_io import load_paint_document
    restored = load_paint_document(tmp_path / f"{shape}.3mf")
    assert len(restored.meshes) == 4
    assert {tuple(mesh.face_rgba[0]) for mesh in restored.meshes} == {tuple(color) for color in colors}
    vertices = np.concatenate([mesh.vertices for mesh in restored.meshes])
    left, right = vertices[vertices[:, 0] < 128], vertices[vertices[:, 0] > 128]
    left_center = (left[:, 0].min() + left[:, 0].max()) / 2
    right_center = (right[:, 0].min() + right[:, 0].max()) / 2
    assert right_center - left_center == pytest.approx(6, abs=0.002)


def test_source_mode_rejects_palette_recipe_overrides():
    with pytest.raises(ValueError, match="does not accept recipe overrides"):
        assign_palette(document(trimesh.creation.box()), mode="source", recipe_overrides={"#3AC8DC": "cm"})


def test_source_preflight_welds_only_exact_coordinate_duplicates():
    from layerloom.paint_export import _source_geometry
    doc = document(trimesh.creation.box((4e-9, 1, 1)))
    # Decimal8 welding would merge the two X sides, although their float32
    # coordinates are distinct. Preserve them until the explicit XY-grid guard.
    surface, solid, _, _ = _source_geometry(doc, assign_palette(doc, mode="source"))
    assert len(surface.vertices) == 8
    assert surface.is_volume
    assert solid.volume() == pytest.approx(4e-9, rel=1e-6)
