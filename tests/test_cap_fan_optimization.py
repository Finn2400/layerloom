"""Fan detection preserves the original cap topology, including point contacts."""
from collections import defaultdict

import numpy as np
import pytest
import shapely
from shapely.geometry import Polygon, box

from layerloom.planar_bands import (
    _cap_edge_topology, _cdt_cap_arrays, _extrude_indexed_cap,
    _split_cap_vertex_fans,
)


def original_split(vertices, faces):
    """Frozen per-vertex traversal from 514b61a6, for exact-index regression."""
    incident = defaultdict(list)
    for face_index, face in enumerate(faces):
        for vertex in face:
            incident[int(vertex)].append(face_index)
    points, faces = vertices.tolist(), faces.copy()
    splits = 0
    for vertex, face_indices in incident.items():
        edge_faces = defaultdict(list)
        for face_index in face_indices:
            for other in faces[face_index]:
                if other != vertex:
                    edge_faces[int(other)].append(face_index)
        neighbors = defaultdict(set)
        for shared in edge_faces.values():
            for face_index in shared:
                neighbors[face_index].update(shared)
        unseen, components = set(face_indices), []
        while unseen:
            stack, component = [unseen.pop()], []
            while stack:
                face_index = stack.pop()
                component.append(face_index)
                for other in neighbors[face_index] & unseen:
                    unseen.remove(other)
                    stack.append(other)
            components.append(component)
        for component in components[1:]:
            replacement = len(points)
            points.append(points[vertex])
            for face_index in component:
                faces[face_index][faces[face_index] == vertex] = replacement
            splits += 1
    return np.asarray(points, dtype=float), faces, splits


def assert_parity(vertices, faces):
    original_vertices, original_faces = vertices.copy(), faces.copy()
    expected = original_split(vertices, faces)
    actual = _split_cap_vertex_fans(vertices, faces)
    assert np.array_equal(actual[0], expected[0])
    assert np.array_equal(actual[1], expected[1])
    assert actual[2] == expected[2]
    assert np.array_equal(vertices, original_vertices)
    assert np.array_equal(faces, original_faces)
    return actual


@pytest.mark.parametrize("seed", range(12))
def test_random_cap_collections_preserve_original_indices(seed):
    rng = np.random.default_rng(seed)
    # Include closed fans, open fans and multiple contacts. Connectivity must
    # be checked, rather than inferred from the number of boundary edges.
    vertices = rng.uniform(-10, 10, size=(45, 2))
    faces = np.asarray([rng.choice(len(vertices), 3, replace=False)
                        for _ in range(80)])
    rng.shuffle(faces)
    assert_parity(vertices, faces)
    edges, _, _, incidence = _cap_edge_topology(faces)
    _, inverse, counts = np.unique(np.sort(edges, axis=1), axis=0,
                                   return_inverse=True, return_counts=True)
    assert np.array_equal(incidence, counts[inverse])


@pytest.mark.parametrize("seed", range(8))
def test_triangulated_holes_and_point_contacts_keep_exact_geometry(seed):
    rng = np.random.default_rng(seed)
    profile = Polygon([(0, 0), (8, 0), (8, 8), (0, 8)],
                      [[(0, 4), (1, 3), (1, 5), (0, 4)],
                       [(8, 4), (7, 5), (7, 3), (8, 4)]])
    vertices, faces = _cdt_cap_arrays(shapely.constrained_delaunay_triangles(profile))
    rng.shuffle(faces)
    vertices = vertices + rng.uniform(-100, 100, size=2)
    assert assert_parity(vertices, faces)[2] == 2
    mesh, splits = _extrude_indexed_cap(vertices, faces, 0.0005, 0.1995)
    assert mesh.is_volume
    assert splits == 2
    assert mesh.volume == pytest.approx(profile.area * 0.199)


def test_disconnected_closed_fans_are_detected_without_boundary_edges():
    vertices = np.arange(26, dtype=float).reshape(-1, 2)
    faces = np.asarray([(0, 1, 2), (0, 2, 3), (0, 3, 1),
                        (0, 4, 5), (0, 5, 6), (0, 6, 4),
                        (7, 8, 9), (7, 10, 11), (7, 11, 12)])
    assert assert_parity(vertices, faces)[2] == 2


def test_nonmanifold_cap_edge_is_still_rejected():
    vertices = np.asarray([(0, 0), (1, 0), (0, 1), (0, -1), (1, 1)], dtype=float)
    faces = np.asarray([(0, 1, 2), (1, 0, 3), (0, 1, 4)])
    assert_parity(vertices, faces)
    with pytest.raises(ValueError, match="nonmanifold edge"):
        _extrude_indexed_cap(vertices, faces, 0, 0.2)


def test_collapsed_triangle_is_still_rejected():
    with pytest.raises(ValueError, match="collapsed cap triangle"):
        _extrude_indexed_cap(np.asarray([(0, 0), (1, 1), (2, 2)]),
                             np.asarray([(0, 1, 2)]), 0, 0.2)


def test_ordinary_dense_cap_needs_no_splits():
    profile = box(0, 0, 20, 20).difference(shapely.Point(10, 10).buffer(4, quad_segs=64))
    vertices, faces = _cdt_cap_arrays(shapely.constrained_delaunay_triangles(profile))
    assert assert_parity(vertices, faces)[2] == 0


@pytest.mark.parametrize("size", [2, 4, 16, 17, 32, 64, 256])
def test_hybrid_paths_preserve_small_and_large_fans(size):
    angles = np.arange(size + 2) * 2 * np.pi / (size + 2)
    vertices = np.column_stack((np.cos(angles), np.sin(angles)))
    faces = np.asarray([(0, i, i + 1) for i in range(1, size + 1)])
    assert assert_parity(vertices, faces)[2] == 0
    # A disconnected triangle at vertex zero must be split by either path.
    vertices = np.vstack((vertices, [[3, 0], [3, 1]]))
    faces = np.vstack((faces[:-1], [[0, len(vertices) - 2, len(vertices) - 1]]))
    assert assert_parity(vertices, faces)[2] == 1
