"""Capability fallback and representation-range checks for band extrusion."""

import numpy as np
import pytest
import shapely
from shapely.geometry import MultiPolygon, box
import trimesh

from layerloom.planar_bands import _graph, extrude_profile


def assert_export_volume(mesh, expected_volume):
    vertices = np.round(mesh.vertices, 9).astype(np.float32).astype(float)
    assert np.isfinite(vertices).all()
    assert trimesh.Trimesh(vertices=vertices, faces=mesh.faces, process=False).is_volume
    assert mesh.volume == pytest.approx(expected_volume)


def test_graph_accepts_numpy_2_0_0_inverse_shape(monkeypatch):
    segments = np.array([[[0, 0], [1, 0]], [[1, 0], [1, 1]]], dtype=float)
    original_unique = np.unique
    expected_vertices, expected_degrees = _graph(segments)

    def column_inverse(*args, **kwargs):
        vertices, inverse = original_unique(*args, **kwargs)
        return vertices, inverse.reshape(-1, 1)

    monkeypatch.setattr(np, "unique", column_inverse)
    vertices, degrees = _graph(segments)
    np.testing.assert_array_equal(vertices, expected_vertices)
    np.testing.assert_array_equal(degrees, expected_degrees)


def test_missing_cdt_falls_back_with_hole_preserved(monkeypatch):
    monkeypatch.delattr(shapely, "constrained_delaunay_triangles", raising=False)
    profile = box(0, 0, 4, 4).difference(box(1, 1, 3, 3))
    mesh = extrude_profile(profile, 0, 0.2)
    assert mesh.metadata["extrusion"]["triangulator"] == "earcut"
    assert_export_volume(mesh, 2.4)
    section = mesh.section(plane_origin=(0, 0, 0.1), plane_normal=(0, 0, 1))
    assert len(section.discrete) == 2


def test_cdt_symbol_with_unsupported_geos_uses_validated_fallback(monkeypatch):
    calls = []

    def unsupported(polygon):
        calls.append(polygon)
        raise shapely.errors.UnsupportedGEOSVersionError("CDT requires GEOS >= 3.10")

    monkeypatch.setattr(shapely, "constrained_delaunay_triangles", unsupported, raising=False)
    profile = MultiPolygon([box(0, 0, 1, 1), box(2, 0, 3, 1)])
    mesh = extrude_profile(profile, 0, 0.2)
    assert len(calls) == 1
    assert mesh.metadata["extrusion"]["triangulator"] == "earcut"
    assert_export_volume(mesh, 0.4)


def test_cdt_geometry_error_does_not_silently_switch_triangulators(monkeypatch):
    def broken(polygon):
        raise shapely.errors.GEOSException("invalid triangulation")

    monkeypatch.setattr(shapely, "constrained_delaunay_triangles", broken, raising=False)
    with pytest.raises(shapely.errors.GEOSException, match="invalid triangulation"):
        extrude_profile(box(0, 0, 1, 1), 0, 0.2)


@pytest.mark.parametrize("low,high", [(0, 3.5e38), (-3.5e38, 0), (3.5e38, 3.6e38)])
def test_finite_z_outside_export_range_is_rejected(low, high):
    # Infinite exports used to produce NaN area vectors, which do not compare
    # equal to zero and therefore bypassed the collapsed-triangle rejection.
    with pytest.raises(ValueError, match="float32 export range"):
        extrude_profile(box(0, 0, 1, 1), low, high)
