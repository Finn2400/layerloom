"""Shared paint/placement state without a Qt window or geometry serialization."""

from copy import deepcopy
from dataclasses import replace
import zipfile
from xml.etree import ElementTree as ET

import numpy as np
import pytest
import trimesh

from layerloom.paint_examples import _write_source_color_fixture_3mf
from layerloom.paint_model import PaintDocument, PaintMesh
from layerloom.paint_workspace_model import PaintWorkspaceModel
from layerloom.transform_3mf import Bounds3D, TransformPlan, compute_transform_plan, rotation_matrix_xyz


def multipart_document():
    # An asymmetric tetrahedron catches rotating only the axis-aligned box;
    # the second mesh verifies that parts use one shared placement transform.
    tetra = trimesh.Trimesh(
        vertices=[[20.0, 31.0, 7.0], [26.0, 31.0, 7.0],
                  [22.0, 35.0, 7.0], [21.0, 32.0, 16.0]],
        faces=[[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]], process=False)
    box = trimesh.creation.box((4.0, 8.0, 3.0))
    box.apply_translation((43.0, 29.0, 10.5))
    meshes = []
    for index, mesh in enumerate([tetra, box]):
        colors = np.tile([30, 120, 210, 255], (len(mesh.faces), 1)).astype(np.uint8)
        colors[0] = [200, 40, 70, 255]
        original = colors.copy()
        original[0] = [20, 30, 40, 255]
        meshes.append(PaintMesh(f"part-{index}", f"Part {index}",
                                mesh.vertices, mesh.faces, colors, original))
    return PaintDocument(meshes, "/original/download.3mf", {"paint_mapping": {"recipe": "cmy"}})


def assert_bounds_equal(actual, expected):
    np.testing.assert_allclose(actual.min_corner, expected.min_corner, rtol=0, atol=1e-12)
    np.testing.assert_allclose(actual.max_corner, expected.max_corner, rtol=0, atol=1e-12)


def test_initial_workspace_preserves_loaded_placement_and_document_identity():
    document = multipart_document()
    arrays = [(mesh.vertices, mesh.faces, mesh.face_rgba, mesh.source_rgba) for mesh in document.meshes]
    workspace = PaintWorkspaceModel(document)
    assert workspace.document is document
    assert workspace.current_plan is workspace.identity_plan
    assert workspace.original_bounds.min_corner[2] == 7.0
    np.testing.assert_array_equal(workspace.current_matrix, np.eye(4))
    for mesh, original in zip(document.meshes, arrays):
        assert all(value is old for value, old in zip(
            (mesh.vertices, mesh.faces, mesh.face_rgba, mesh.source_rgba), original))
    assert not workspace.apply(workspace.identity_plan)
    assert not workspace.current_matrix.flags.writeable
    assert not workspace.original_bounds.min_corner.flags.writeable


@pytest.mark.parametrize("settings", [
    {"scale": 1.0},
    {"scale": 2.3},
    {"scale": 0.65, "orientation_matrix": rotation_matrix_xyz(31.0, -27.0, 81.0)},
    {"scale": 1.2, "orientation_matrix": rotation_matrix_xyz(0.0, 90.0, 0.0),
     "target_center_xy": np.array([71.5, 112.25])},
    {"scale": 0.9, "orientation_matrix": rotation_matrix_xyz(-18.0, 43.0, 11.0),
     "center_xy": True, "plate_width": 300.0, "plate_depth": 250.0},
    {"scale": 1.6, "center_xy": True, "target_center_xy": np.array([9.0, 37.0])},
])
def test_in_memory_plan_matches_existing_arrange_archive_math(tmp_path, settings):
    document = multipart_document()
    before = [mesh.vertices.copy() for mesh in document.meshes]
    source = tmp_path / "original.3mf"
    _write_source_color_fixture_3mf(document, source)
    workspace = PaintWorkspaceModel(document)
    plan = workspace.plan(**settings)
    legacy_settings = {"center_xy": False, **settings}
    legacy = compute_transform_plan(str(source), **legacy_settings)
    np.testing.assert_array_equal(plan.global_matrix, legacy.global_matrix)
    assert_bounds_equal(plan.original_bounds, legacy.original_bounds)
    assert_bounds_equal(plan.transformed_bounds, legacy.transformed_bounds)
    assert workspace.current_plan is workspace.identity_plan
    for mesh, original in zip(document.meshes, before):
        np.testing.assert_array_equal(mesh.vertices, original)
    assert workspace.apply(plan)
    vertices = np.concatenate([mesh.vertices for mesh in document.meshes])
    np.testing.assert_allclose(vertices.min(axis=0), plan.transformed_bounds.min_corner, atol=1e-12)
    np.testing.assert_allclose(vertices.max(axis=0), plan.transformed_bounds.max_corner, atol=1e-12)
    assert plan.transformed_bounds.min_corner[2] == 0.0


def test_repeated_absolute_placement_has_no_drift_and_keeps_paint_face_identity():
    document = multipart_document()
    originals = [mesh.vertices.copy() for mesh in document.meshes]
    meshes = list(document.meshes)
    fixed_arrays = [(mesh.faces, mesh.face_rgba, mesh.source_rgba) for mesh in meshes]
    source_colors = [mesh.source_rgba.copy() for mesh in meshes]
    metadata = document.metadata
    saved_metadata = deepcopy(metadata)
    workspace = PaintWorkspaceModel(document)
    first = workspace.plan(scale=1.8, orientation_matrix=rotation_matrix_xyz(17, 23, -54),
                           target_center_xy=np.array([80.5, 120.25]))
    second = workspace.plan(scale=0.7, orientation_matrix=rotation_matrix_xyz(-38, 5, 22))
    assert workspace.apply(first)
    first_vertices = [mesh.vertices.copy() for mesh in meshes]
    first_arrays = [mesh.vertices for mesh in meshes]
    assert not workspace.apply(first)
    assert all(mesh.vertices is array for mesh, array in zip(meshes, first_arrays))
    # Painting between placement changes belongs to the shared document.
    meshes[0].face_rgba[2] = [250, 10, 80, 255]
    painted = [mesh.face_rgba.copy() for mesh in meshes]
    for _ in range(40):
        assert workspace.apply(second)
        assert workspace.apply(first)
    for mesh, expected in zip(meshes, first_vertices):
        np.testing.assert_array_equal(mesh.vertices, expected)
    assert workspace.apply(workspace.identity_plan)
    for mesh, original, arrays, paint, source in zip(meshes, originals, fixed_arrays, painted, source_colors):
        np.testing.assert_array_equal(mesh.vertices, original)
        assert all(value is old for value, old in zip((mesh.faces, mesh.face_rgba, mesh.source_rgba), arrays))
        np.testing.assert_array_equal(mesh.face_rgba, paint)
        np.testing.assert_array_equal(mesh.source_rgba, source)
    assert all(current is original for current, original in zip(document.meshes, meshes))
    assert document.source_path == "/original/download.3mf"
    assert document.metadata is metadata and metadata == saved_metadata


def test_transform_changes_only_after_apply_and_explicit_drop_grounds_source():
    document = multipart_document()
    workspace = PaintWorkspaceModel(document)
    before = [mesh.vertices.copy() for mesh in document.meshes]
    plan = workspace.plan()
    assert workspace.current_plan.transformed_bounds.min_corner[2] == 7.0
    for mesh, expected in zip(document.meshes, before):
        np.testing.assert_array_equal(mesh.vertices, expected)
    assert workspace.apply(plan)
    assert workspace.current_plan.transformed_bounds.min_corner[2] == 0.0
    np.testing.assert_allclose(workspace.current_plan.transformed_bounds.center[:2],
                               workspace.original_bounds.center[:2], atol=1e-12)


@pytest.mark.parametrize("settings", [
    {"scale": 0.0}, {"scale": -1.0}, {"scale": np.nan}, {"scale": np.inf},
    {"orientation_matrix": np.zeros((2, 2))}, {"orientation_matrix": np.full((3, 3), np.nan)},
    {"target_center_xy": [1.0, 2.0, 3.0]}, {"target_center_xy": [np.inf, 0.0]},
    {"plate_width": 0.0}, {"plate_depth": np.nan},
])
def test_invalid_plan_settings_leave_shared_document_unchanged(settings):
    document = multipart_document()
    workspace = PaintWorkspaceModel(document)
    before = [mesh.vertices for mesh in document.meshes]
    with pytest.raises(ValueError):
        workspace.plan(**settings)
    assert workspace.current_plan is workspace.identity_plan
    assert all(mesh.vertices is original for mesh, original in zip(document.meshes, before))


@pytest.mark.parametrize("fault", ["nonfinite", "nonaffine", "bounds", "source", "topology"])
def test_invalid_commit_is_atomic_across_all_parts(fault):
    document = multipart_document()
    workspace = PaintWorkspaceModel(document)
    plan = workspace.plan(scale=1.2, target_center_xy=[80.0, 90.0])
    matrix = plan.global_matrix.copy()
    original_bounds, transformed_bounds = plan.original_bounds, plan.transformed_bounds
    if fault == "nonfinite":
        matrix[0, 0] = np.inf
    elif fault == "nonaffine":
        matrix[3, 0] = 1.0
    elif fault == "bounds":
        transformed_bounds = Bounds3D(np.zeros(3), np.ones(3))
    elif fault == "source":
        original_bounds = Bounds3D(np.zeros(3), np.ones(3))
    else:
        document.meshes[1].faces[0] = document.meshes[1].faces[1]
    invalid = replace(plan, global_matrix=matrix, original_bounds=original_bounds,
                      transformed_bounds=transformed_bounds)
    before = [mesh.vertices for mesh in document.meshes]
    with pytest.raises(ValueError):
        workspace.apply(invalid)
    assert workspace.current_plan is workspace.identity_plan
    assert all(mesh.vertices is original for mesh, original in zip(document.meshes, before))


def test_plans_from_different_workspace_with_equal_bounds_are_rejected():
    workspace = PaintWorkspaceModel(multipart_document())
    other = PaintWorkspaceModel(multipart_document())
    assert_bounds_equal(workspace.original_bounds, other.original_bounds)
    with pytest.raises(ValueError, match="different workspace"):
        workspace.apply(other.plan(scale=1.5))
    assert workspace.current_plan is workspace.identity_plan


@pytest.mark.parametrize("bad_bounds", [
    Bounds3D(np.full(3, np.nan), np.ones(3)),
    Bounds3D(np.zeros(3), np.ones(3)),
])
def test_repeated_matrix_still_validates_bounds(bad_bounds):
    workspace = PaintWorkspaceModel(multipart_document())
    invalid = replace(workspace.identity_plan, transformed_bounds=bad_bounds)
    with pytest.raises(ValueError, match="bounds"):
        workspace.apply(invalid)
    assert workspace.current_plan is workspace.identity_plan


def test_transformed_shared_paint_exports_rotated_material_regions(tmp_path):
    from layerloom.paint_export import export_painted_3mf
    from layerloom.paint_io import load_paint_document

    box = trimesh.creation.box((4.0, 2.0, 2.0))
    # Split each original surface triangle at its edge midpoints. This puts
    # vertices on X=0 and makes the two source paint halves exact triangles.
    vertices, faces = trimesh.remesh.subdivide(box.vertices, box.faces)
    vertices += [11.0, 21.0, 6.0]
    centers = vertices[faces].mean(axis=1)
    colors = np.tile([0, 255, 255, 255], (len(faces), 1)).astype(np.uint8)
    colors[centers[:, 0] > 11.0] = [255, 0, 255, 255]
    original_colors = np.tile([255, 255, 255, 255], (len(faces), 1)).astype(np.uint8)
    mesh = PaintMesh("painted-box", "Painted box", vertices, faces, colors, original_colors)
    document = PaintDocument([mesh], "/original/painted-box.3mf")
    workspace = PaintWorkspaceModel(document)
    arrays = (mesh.faces, mesh.face_rgba, mesh.source_rgba)
    values = [array.copy() for array in arrays]
    plan = workspace.plan(scale=0.5, orientation_matrix=rotation_matrix_xyz(0, 0, 90),
                          target_center_xy=[80.0, 90.0])
    assert workspace.apply(plan)
    transformed_vertices = mesh.vertices.copy()
    output = tmp_path / "placed-painted-geometry.3mf"
    # This mirrors the worker's detached snapshot of the shared document.
    report = export_painted_3mf(workspace.document.copy(), output, mode="source",
                               layer_height=0.25, pitch=0.25, interface_offset=0)
    np.testing.assert_allclose(report["source_bounds_mm"], [[79.5, 89.0, 0.0], [80.5, 91.0, 1.0]], atol=1e-12)
    assert report["source_volume_mm3"] == pytest.approx(16.0 * 0.5 ** 3)
    assert report["output_volume_mm3"] == pytest.approx(2.0)
    np.testing.assert_allclose(report["shared_translation_mm"], [-80.0, -90.0, 0.0], atol=1e-12)
    assert all(not any(row["defects"].values()) and not any(row["placed_float32_defects"].values())
               for row in report["filaments"])

    with zipfile.ZipFile(output) as archive:
        for name in archive.namelist():
            if name.endswith(".model"):
                for node in ET.fromstring(archive.read(name)).iter():
                    local = node.tag.rsplit("}", 1)[-1]
                    assert local not in {"colorgroup", "texture2d", "texture2dgroup", "multiproperties"}
                    if local == "triangle":
                        assert set(node.attrib) == {"v1", "v2", "v3"}

    restored = load_paint_document(output)
    assert len(restored.meshes) == 2
    by_color = {tuple(item.face_rgba[0]): trimesh.Trimesh(item.vertices, item.faces, process=False)
                for item in restored.meshes}
    assert set(by_color) == {(0, 255, 255, 255), (255, 0, 255, 255)}
    # The exporter centers the assembly on its slicer plate. The original
    # negative-X cyan half must now occupy negative Y, and magenta positive Y.
    for color, expected_y in [((0, 255, 255, 255), [127.0, 128.0]),
                              ((255, 0, 255, 255), [128.0, 129.0])]:
        material = by_color[color]
        assert material.is_volume
        assert material.volume == pytest.approx(1.0)
        np.testing.assert_allclose(material.bounds[:, 1], expected_y, atol=1e-7)
        np.testing.assert_allclose(material.bounds[:, 0], [127.5, 128.5], atol=1e-7)
        np.testing.assert_allclose(material.bounds[:, 2], [0.0, 1.0], atol=1e-7)
    np.testing.assert_array_equal(mesh.vertices, transformed_vertices)
    for actual, original, saved in zip((mesh.faces, mesh.face_rgba, mesh.source_rgba), arrays, values):
        assert actual is original
        np.testing.assert_array_equal(actual, saved)
