import numpy as np
import pytest
import trimesh

from layerloom.paint_selection import build_topology, select_area, select_color_region, select_surface


def test_cube_whole_planar_surface_stops_at_crease():
    mesh = trimesh.creation.box()
    topology = build_topology(mesh.vertices, mesh.faces)
    top = np.flatnonzero(mesh.face_normals[:, 2] > .99)
    selected = select_surface(topology, top[0], edge_angle=35, seed_angle=60)
    np.testing.assert_array_equal(selected, top)


def test_sphere_seed_normal_limit_prevents_smooth_flood_reaching_back():
    mesh = trimesh.creation.icosphere(subdivisions=3)
    topology = build_topology(mesh.vertices, mesh.faces)
    seed = int(np.argmax(topology.normals[:, 2]))
    small = select_surface(topology, seed, edge_angle=30, seed_angle=25)
    large = select_surface(topology, seed, edge_angle=30, seed_angle=65)
    assert 1 < len(small) < len(large) < len(mesh.faces) // 2
    assert np.all(topology.normals[large] @ topology.normals[seed] >= np.cos(np.deg2rad(65)) - 1e-10)
    assert not np.any(topology.normals[large] @ topology.normals[seed] <= 0)


def test_small_edge_angle_bounds_a_rounded_surface():
    mesh = trimesh.creation.icosphere(subdivisions=1)
    topology = build_topology(mesh.vertices, mesh.faces)
    assert len(select_surface(topology, 0, edge_angle=0, seed_angle=80)) == 1
    assert len(select_surface(topology, 0, edge_angle=40, seed_angle=80)) > 1


def test_selection_does_not_jump_to_disconnected_coplanar_component():
    square = trimesh.Trimesh([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
                            [[0, 1, 2], [0, 2, 3]], process=False)
    other = square.copy()
    other.apply_translation([1.1, 0, 0])
    mesh = trimesh.util.concatenate([square, other])
    topology = build_topology(mesh.vertices, mesh.faces)
    np.testing.assert_array_equal(select_surface(topology, 0), [0, 1])


def test_area_selection_does_not_leak_through_thin_box():
    mesh = trimesh.creation.box(extents=[10, 10, .01])
    topology = build_topology(mesh.vertices, mesh.faces)
    top = np.flatnonzero(mesh.face_normals[:, 2] > .99)
    selected = select_area(topology, top[0], [0, 0, .005], 100)
    np.testing.assert_array_equal(selected, top)


def test_welded_selection_keeps_stl_source_arrays_unchanged():
    mesh = trimesh.creation.box()
    vertices = mesh.vertices[mesh.faces].reshape(-1, 3)
    faces = np.arange(len(vertices)).reshape(-1, 3)
    before_vertices, before_faces = vertices.copy(), faces.copy()
    topology = build_topology(vertices, faces)
    top = np.flatnonzero(mesh.face_normals[:, 2] > .99)
    np.testing.assert_array_equal(select_surface(topology, top[0]), top)
    np.testing.assert_array_equal(vertices, before_vertices)
    np.testing.assert_array_equal(faces, before_faces)


def test_color_region_selection_follows_paint_not_all_geometry():
    mesh = trimesh.creation.box()
    topology = build_topology(mesh.vertices, mesh.faces)
    top = np.flatnonzero(mesh.face_normals[:, 2] > .99)
    colors = np.tile([0, 255, 255, 255], (len(mesh.faces), 1))
    colors[top] = [255, 0, 0, 255]
    np.testing.assert_array_equal(select_color_region(topology, top[0], colors), top)


@pytest.mark.parametrize("kwargs", [{"seed_angle": 90}, {"seed_angle": -1}, {"edge_angle": 181}, {"edge_angle": float("nan")}])
def test_invalid_angles_rejected(kwargs):
    mesh = trimesh.creation.box()
    topology = build_topology(mesh.vertices, mesh.faces)
    with pytest.raises(ValueError):
        select_surface(topology, 0, **kwargs)
