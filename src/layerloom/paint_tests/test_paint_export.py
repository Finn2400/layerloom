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
    doc = document(main, tiny)
    for item in doc.meshes:
        item.face_rgba[:] = [0, 255, 255, 255]
    report = export_painted_3mf(doc, tmp_path / "retained.3mf", layer_height=1)
    assert_clean_report(report)
    assert len(report["retained_sub_layer_parts"]) == 2
    assert report["retained_sub_layer_volume_mm3"] == pytest.approx(0.001, rel=1e-5)
    assert report["output_volume_mm3"] == pytest.approx(8.001, rel=1e-5)
    assert all(row["topology_and_face_count_preserved"] for row in report["retained_sub_layer_parts"])


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


def test_negative_cavity_shell_between_midpoints_is_not_a_dropped_object(tmp_path):
    outer = trimesh.creation.box((4, 4, 4))
    cavity = trimesh.creation.box((0.2, 0.2, 0.1))
    cavity.invert()
    joined = trimesh.util.concatenate([outer, cavity])
    doc = document(joined)
    report = export_painted_3mf(doc, tmp_path / "cavity.3mf", pitch=1, layer_height=1)
    assert_clean_report(report)
    row = report["source_meshes"][0]
    assert row["positive_components"] == 1
    assert row["negative_cavity_shells"] == 1
    assert len(row["component_z_bounds_mm"]) == 1
    assert report["output_volume_mm3"] == pytest.approx(64)


def test_printable_export_contains_geometry_and_slots_without_triangle_paint(tmp_path):
    path = tmp_path / "geometry_only.3mf"
    export_painted_3mf(document(trimesh.creation.box((2, 2, 2))), path, pitch=1, layer_height=1)
    forbidden = {"paint_color", "mmu_segmentation", "pid", "p1", "p2", "p3"}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name.endswith(".model"):
                root = ET.fromstring(archive.read(name))
                for node in root.iter():
                    if node.tag.rsplit("}", 1)[-1] == "triangle":
                        assert not forbidden.intersection(key.rsplit("}", 1)[-1] for key in node.attrib)


def test_sub_layer_retention_uses_saved_recipe_global_phase(tmp_path):
    main = trimesh.creation.box((2, 2, 2))
    tiny = trimesh.creation.box((0.1, 0.1, 0.1))
    tiny.apply_translation((3, 0, 0.2))
    doc = document(main, tiny)
    for item in doc.meshes:
        item.face_rgba[:] = [0, 255, 255, 255]
    report = export_painted_3mf(doc, tmp_path / "tiny_phase.3mf", layer_height=1,
                               palette_name="CMY", recipe_overrides={"#00FFFF": "cm"})
    assert_clean_report(report)
    retained = report["retained_sub_layer_parts"]
    assert len(retained) == 1
    assert retained[0]["layer_index"] == 1
    assert retained[0]["filament"] == "m"
    assert retained[0]["sampled_recipe"] == "cm"
    assert retained[0]["source_recipes"] == ["cm"]


def test_fine_grid_cleanup_uses_placed_float32_spacing_without_losing_components(tmp_path):
    import manifold3d as mf
    # The tiny inward notch is representable near the source origin, but falls
    # between adjacent float32 values after placement near X=128 mm. Its nearly
    # collinear boundary triangles must be cleaned within the declared ULP bound.
    polygon = np.array([[0, 0], [2, 0], [2, 2], [0, 2], [0.000003, 1]], dtype=float)
    solid = mf.CrossSection([polygon]).extrude(2)
    data = solid.to_mesh64()
    mesh = trimesh.Trimesh(data.vert_properties, data.tri_verts, process=False)
    doc = document(mesh)
    doc.meshes[0].face_rgba[:] = [0, 255, 255, 255]
    report = export_painted_3mf(doc, tmp_path / "fine_grid.3mf", layer_height=1, pitch=0.5,
                               precision=1e-7, interface_offset=0)
    assert_clean_report(report)
    part = report["filaments"][0]
    assert part["numerical_simplification_tolerance_mm"] > report["geometry_precision_mm"] / 2
    assert part["numerical_simplification_tolerance_mm"] <= part["placed_float32_ulp_mm"] * 4
    assert part["components_before_numerical_cleanup"] == part["components_after_numerical_cleanup"] == 1


def test_all_positive_objects_can_be_retained_between_midpoints(tmp_path):
    first = trimesh.creation.box((1, 1, 0.01))
    first.apply_translation((0, 0, 0.005))
    second = first.copy()
    second.apply_translation((2, 0, 0.19))
    doc = document(first, second)
    for mesh in doc.meshes:
        mesh.face_rgba[:] = [0, 255, 255, 255]
    report = export_painted_3mf(doc, tmp_path / "all_retained.3mf", layer_height=0.2)
    assert_clean_report(report)
    assert report["nonempty_layers"] == 0
    assert len(report["retained_sub_layer_parts"]) == 2
    assert report["retained_sub_layer_volume_mm3"] == pytest.approx(0.02, rel=1e-5)
    assert report["output_volume_mm3"] == pytest.approx(0.02, rel=1e-5)


def test_component_count_does_not_materialize_disconnected_solids():
    import manifold3d as mf
    from layerloom.paint_export import _solid_component_count
    first = mf.Manifold.cube((1, 1, 1))
    second = first.translate((3, 0, 0))
    solid = mf.Manifold.batch_boolean([first, second], mf.OpType.Add)
    assert _solid_component_count(solid) == len(solid.decompose()) == 2
    hollow = mf.Manifold.cube((4, 4, 4), center=True) - mf.Manifold.cube((2, 2, 2), center=True)
    assert _solid_component_count(hollow) == len(hollow.decompose())
    assert _solid_component_count(mf.Manifold()) == 0


def test_placed_vertex_collision_is_corrected_without_dropping_a_material_component():
    import manifold3d as mf
    from layerloom.paint_export import _finalize_filament
    first = mf.Manifold.cube((2, 2, 2), center=True)
    second = first.translate((2.000003, 0, 0))
    solid = mf.Manifold.batch_boolean([first, second], mf.OpType.Add).translate((-1.0000015, 0, 1))
    mesh, _, row = _finalize_filament(solid, "c", 1e-7)
    assert row["components_before_numerical_cleanup"] == row["components_after_numerical_cleanup"] == 2
    assert not any(row["defects"].values())
    assert not any(row["placed_float32_defects"].values())
    correction = row["local_vertex_correction"]
    assert correction is not None
    assert correction["topology_and_face_count_preserved"]
    assert correction["maximum_vertex_displacement_mm"] <= correction["displacement_limit_mm"]
    assert mesh.is_volume
