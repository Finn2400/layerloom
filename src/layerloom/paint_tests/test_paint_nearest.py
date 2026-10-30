"""Exact, bounded-memory paint queries on the portable compiled BVH."""
from threading import Event

import numpy as np
import pytest
import trimesh

from layerloom.paint_export import _nearest_faces
from layerloom.paint_export_control import ExportCancelled, ExportControl, estimate_export_resources
from layerloom.paint_model import PaintDocument, PaintMesh
from layerloom.paint_nearest import NearestTriangles


def test_sphere_queries_match_original_surface_algorithm():
    mesh = trimesh.creation.icosphere(subdivisions=2)
    points = np.random.default_rng(629).uniform(-2, 2, (1500, 3))
    tree = NearestTriangles(mesh)
    assert np.array_equal(tree.query(points), _nearest_faces(mesh, points))


def test_lowest_face_ties_on_cube_center_edges_and_vertices():
    mesh = trimesh.creation.box((2, 2, 2))
    points = np.array([[0, 0, 0], [1, 1, 1], [1, 1, 0], [0, 0, 1], [-1, -1, -1]], dtype=float)
    assert np.array_equal(NearestTriangles(mesh).query(points), _nearest_faces(mesh, points))


def test_tolerance_uses_true_minimum_without_chained_tie_drift():
    # Face0 lies within tolerance of face1, but not of the true minimum(face2).
    tolerance = 4e-14
    triangles = np.array([[[-1, -1, z], [1, -1, z], [0, 1, z]]
                          for z in (np.sqrt(1 + tolerance * 1.5),
                                    np.sqrt(1 + tolerance * .75), 1.)])
    mesh = trimesh.Trimesh(triangles.reshape(-1, 3), np.arange(9).reshape(-1, 3), process=False)
    tree = NearestTriangles(mesh)
    ids, squared = tree.query(np.zeros((1, 3)), return_distance=True)
    assert ids.tolist() == [1]
    assert squared.tolist() == [1.]
    assert ids.tolist() == _nearest_faces(mesh, np.zeros((1, 3))).tolist()


def test_search_owns_immutable_triangle_geometry():
    mesh = trimesh.creation.icosphere(subdivisions=1)
    points = np.random.default_rng(52).uniform(-2, 2, (16, 3))
    tree = NearestTriangles(mesh)
    before = tree.query(points, return_distance=True)
    mesh.vertices += 50
    after = tree.query(points, return_distance=True)
    assert all(np.array_equal(a, b) for a, b in zip(before, after))


def test_large_query_is_batched_and_cancelled_before_next_batch(monkeypatch):
    mesh = trimesh.creation.box()
    colors = np.full((len(mesh.faces), 4), 255, dtype=np.uint8)
    doc = PaintDocument([PaintMesh("box", "Box", mesh.vertices, mesh.faces, colors)])
    stop = Event()
    control = ExportControl(estimate_export_resources(doc), 1024**3, stop.is_set)
    tree = NearestTriangles(mesh, control=control)
    sizes = []
    check_memory = control.check_memory
    def request_cancel_after_batch(extra_bytes=0, **kwargs):
        check_memory(extra_bytes, **kwargs)
        if kwargs.get("stage") == "nearest-surface search":
            sizes.append(extra_bytes)
            stop.set()
    monkeypatch.setattr(control, "check_memory", request_cancel_after_batch)
    with pytest.raises(ExportCancelled):
        tree.query(np.zeros((10000, 3)))
    assert len(sizes) == 1
    assert sizes[0] == 4096 * (tree.stack_size * 8 + 64)


@pytest.mark.parametrize("points", [np.array([[np.inf, 0, 0]]), np.zeros((2, 2))])
def test_invalid_query_input_fails_cleanly(points):
    with pytest.raises(ValueError, match="queries"):
        NearestTriangles(trimesh.creation.box()).query(points)


def test_empty_query_and_single_triangle_are_supported():
    mesh = trimesh.Trimesh([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]], process=False)
    tree = NearestTriangles(mesh)
    assert tree.query(np.zeros((0, 3))).shape == (0,)
    ids, squared = tree.query(np.array([[.25, .25, 2.]]), return_distance=True)
    assert ids.tolist() == [0]
    assert squared.tolist() == [4.]
