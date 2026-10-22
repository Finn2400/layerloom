"""Export-coordinate and cap-topology regressions for planar reconstruction."""

import numpy as np
import pytest
import shapely
from shapely.geometry import MultiPolygon, Polygon, box
import trimesh

from layerloom.planar_bands import extrude_profile, prepare_export_region


def exported_checks(mesh):
    vertices = np.round(mesh.vertices, 9).astype(np.float32).astype(float)
    tri = vertices[mesh.faces]
    area = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    assert not np.any(np.all(area == 0, axis=1))
    assert trimesh.Trimesh(vertices, mesh.faces, process=False).is_volume


def test_triangulation_happens_after_float32_rounding():
    # The almost-collinear roof vertex becomes exactly collinear in float32.
    # Triangulating in double precision first can create a cap that then collapses.
    profile = Polygon([(100, 0), (101, 0), (101, 1),
                       (100.5, 1 + 1e-8), (100, 1)])
    rounded, diagnostics = prepare_export_region(profile)
    assert rounded.symmetric_difference(box(100, 0, 101, 1)).area == 0
    assert 0 < diagnostics["quantization_area_error_mm2"] < 1e-7
    mesh = extrude_profile(profile, 2.0005, 2.1995)
    exported_checks(mesh)
    assert mesh.volume == pytest.approx(rounded.area * 0.199)
    assert mesh.metadata["extrusion"]["quantization_area_error_mm2"] > 0


def test_point_touching_hole_preserves_geometry_and_reports_contact():
    profile = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)],
                      [[(0, 2), (1, 1), (1, 3), (0, 2)]])
    assert profile.is_valid
    mesh = extrude_profile(profile, 0, 0.2)
    exported_checks(mesh)
    assert mesh.volume == pytest.approx(profile.area * 0.2)
    assert mesh.metadata["extrusion"]["geometric_point_contacts"]
    assert mesh.metadata["extrusion"]["split_vertex_fans"] >= 1
    # Separating fan indices is explicit, not a claim that the geometric
    # contact disappeared. There remain duplicated coordinates at the contact.
    assert len(np.unique(mesh.vertices, axis=0)) < len(mesh.vertices)


def test_point_touching_components_report_geometric_contact():
    profile = MultiPolygon([box(0, 0, 1, 1), box(1, 1, 2, 2)])
    assert profile.is_valid
    mesh = extrude_profile(profile, 0, 0.2)
    exported_checks(mesh)
    assert mesh.volume == pytest.approx(0.4)
    assert mesh.metadata["extrusion"]["geometric_point_contacts"]
    assert mesh.metadata["extrusion"]["geometric_component_contacts"] == 1


def test_float32_collapse_is_rejected_instead_of_filling_or_dropping():
    profile = box(1e8, 0, 1e8 + 1, 1)
    with pytest.raises(ValueError, match="float32"):
        prepare_export_region(profile)
    with pytest.raises(ValueError, match="float32"):
        extrude_profile(profile, 0, 0.2)


def test_rounding_preserves_ordinary_hole_and_is_idempotent():
    profile = box(0.123456789, 0.987654321, 3.125, 4.25).difference(box(1, 2, 2, 3))
    rounded, diagnostics = prepare_export_region(profile)
    again, second = prepare_export_region(rounded)
    assert len(rounded.interiors) == 1
    assert rounded.equals_exact(again, 0)
    assert second["quantization_area_error_mm2"] == 0
    mesh = extrude_profile(profile, 0, 0.2)
    exported_checks(mesh)
    assert mesh.volume == pytest.approx(rounded.area * 0.2)
    assert not mesh.metadata["extrusion"]["geometric_point_contacts"]


def test_basic_extrusion_has_validated_fallback_without_geos_cdt(monkeypatch):
    monkeypatch.setattr(shapely, "constrained_delaunay_triangles", None)
    mesh = extrude_profile(box(0, 0, 1, 1), 0, 0.2)
    exported_checks(mesh)
    assert mesh.metadata["extrusion"]["triangulator"] == "earcut"


def test_quantization_normalizes_and_reports_collapsed_microscopic_hole():
    profile = box(199, 0, 201, 10).difference(box(200, 2, 200 + 1e-6, 8))
    rounded, diagnostics = prepare_export_region(profile)
    assert rounded.equals(box(199, 0, 201, 10))
    assert diagnostics["quantization_holes_before"] == 1
    assert diagnostics["quantization_holes_after"] == 0
    assert diagnostics["quantization_normalization_passes"] == 1
    assert diagnostics["quantization_components_before"] == 1
    assert diagnostics["quantization_components_after"] == 1
    changed = profile.symmetric_difference(rounded)
    band = profile.boundary.buffer(diagnostics["max_xy_displacement_mm"] + 1e-12)
    assert changed.difference(band).area == 0
    assert diagnostics["quantization_area_error_mm2"] == pytest.approx(6e-6)
    exported_checks(extrude_profile(profile, 0, 0.2))


def test_quantization_cannot_silently_drop_a_whole_component():
    profile = MultiPolygon([box(0, 0, 1, 1), box(1e8, 0, 1e8 + 1, 1)])
    with pytest.raises(ValueError, match="component collapses"):
        prepare_export_region(profile)


def test_normalization_must_stay_inside_actual_rounding_boundary_band(monkeypatch):
    profile = box(199, 0, 201, 10).difference(box(200, 2, 200 + 1e-6, 8))
    monkeypatch.setattr(shapely, "make_valid", lambda _: box(199, 0, 202, 10))
    with pytest.raises(ValueError, match="boundary band"):
        prepare_export_region(profile)


def test_geos_decimal_precision_model_cannot_resnap_float32_output():
    profile = shapely.set_precision(box(199.12345, 0, 201.54321, 10), 1e-5)
    rounded, diagnostics = prepare_export_region(profile)
    assert shapely.get_precision(rounded) == 0
    coordinates = shapely.get_coordinates(rounded)
    assert np.array_equal(coordinates, coordinates.astype(np.float32).astype(float))
    assert diagnostics["quantization_area_error_mm2"] > 0
    exported_checks(extrude_profile(profile, 0, 0.2))


def test_rounding_bound_accounts_for_inscribed_buffer_chords():
    profile = Polygon([(201.23456, 1), (203.12345, 1), (203.12345, 0),
                       (200.12345, 0), (200.67901, 0.5)])
    rounded, diagnostics = prepare_export_region(profile)
    assert rounded.is_valid
    assert 0 < diagnostics["quantization_buffer_chord_allowance_mm"] < 5e-9
    exported_checks(extrude_profile(profile, 0, 0.2))


def test_short_geos_fillet_has_larger_chord_angle_than_nominal_step():
    # Reduced from Benchy material18/m: GEOS rounds this short fillet's segment
    # count down, producing a chord longer than one nominal angular step.
    profile = Polygon([(109.73736, 128.3048), (109.70985, 128.29229),
                       (109.59921, 128.22547)])
    rounded, diagnostics = prepare_export_region(profile)
    assert rounded.is_valid
    assert diagnostics["max_xy_displacement_mm"] == pytest.approx(8.154253376482497e-6)
    exported_checks(extrude_profile(profile, 0, 0.2))
