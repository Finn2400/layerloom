import pytest
from shapely.geometry import box
from shapely import union_all

from layerloom.planar_materials import partition_materials


def test_microscopic_seam_is_disjoint_without_changing_union():
    inputs = {"m": [box(0.9995, 0, 2, 1)], "c": [box(0, 0, 1, 1)]}
    regions, diagnostics, errors = partition_materials(inputs)
    assert not errors
    assert regions["c"].intersection(regions["m"]).area == 0
    assert union_all(list(regions.values())).symmetric_difference(box(0, 0, 2, 1)).area < 1e-12
    assert diagnostics["overlaps"][0]["resolved"]
    assert diagnostics["material_changes"][1]["reassigned_area_mm2"] == pytest.approx(0.0005)


def test_substantial_or_disabled_seam_repair_remains_error():
    for overlap, tolerance in [(0.01, 0.001), (0.0005, 0)]:
        _, d, errors = partition_materials(
            {"c": [box(0, 0, 1, 1)], "m": [box(1-overlap, 0, 2, 1)]}, seam_tolerance=tolerance)
        assert errors
        assert not d["overlaps"][0]["resolved"]


def test_seam_rule_cannot_erase_a_whole_material():
    _, _, errors = partition_materials(
        {"c": [box(0, 0, 1, 1)], "m": [box(0.9995, 0, 1, 1)]})
    assert errors


def test_same_color_union_preserves_real_hole():
    donut = box(0, 0, 5, 5).difference(box(1, 1, 4, 4))
    result, _, errors = partition_materials({"c": [donut, donut]})
    assert not errors
    assert result["c"].area == pytest.approx(16)
    assert len(result["c"].interiors) == 1


def test_precision_grid_cannot_drop_one_disconnected_material_island():
    from shapely.geometry import MultiPolygon, box

    source = MultiPolygon([box(0, 0, 1, 1), box(2, 0, 2.000004, 1)])
    _, diagnostics, errors = partition_materials({"c": [source]}, precision=1e-5)
    assert any("erase a component" in error for error in errors)
    adjustment = diagnostics["input_grid_adjustments"][0]
    assert adjustment["components_before"] == 2
    assert adjustment["components_after"] == 1


def test_equal_color_internal_seam_is_removed_before_grid_rounding():
    from shapely.geometry import Polygon

    left = Polygon([(0, 0), (.12, 0), (.88, 1), (0, 1)])
    right = Polygon([(.12, 0), (1, 0), (1, 1), (.88, 1), (.348, .3)])
    regions, _, errors = partition_materials({"c": [left, right]}, precision=.01)
    assert not errors
    assert regions["c"].symmetric_difference(box(0, 0, 1, 1)).area == 0


def test_unchanged_material_distance_skips_quadratic_comparison(monkeypatch):
    import layerloom.planar_materials as materials

    def unexpected(*args, **kwargs):
        raise AssertionError("identical boundaries do not need Hausdorff evaluation")

    monkeypatch.setattr(materials.shapely, "hausdorff_distance", unexpected)
    donut = box(0, 0, 5, 5).difference(box(1, 1, 4, 4))
    _, diagnostics, errors = materials.partition_materials({"c": [donut]})
    assert not errors
    assert diagnostics["material_changes"] == [
        {"token": "c", "reassigned_area_mm2": 0.0, "set_hausdorff_mm": 0.0}]


def test_changed_material_keeps_original_distance_diagnostic(monkeypatch):
    import layerloom.planar_materials as materials

    inputs = {"m": [box(0.9995, 0, 2, 1)], "c": [box(0, 0, 1, 1)]}
    actual, diagnostics, errors = materials.partition_materials(inputs)
    original = materials.shapely.hausdorff_distance
    monkeypatch.setattr(materials, "_set_hausdorff", lambda a, b: float(original(a, b)))
    reference, reference_diagnostics, reference_errors = materials.partition_materials(inputs)
    assert errors == reference_errors == []
    assert diagnostics == reference_diagnostics
    assert diagnostics["material_changes"][1]["set_hausdorff_mm"] > 0
    assert {k: p.wkb for k, p in actual.items()} == {k: p.wkb for k, p in reference.items()}


def test_equal_area_does_not_shortcut_changed_holes_or_boundary_samples():
    import shapely
    from shapely.geometry import Polygon
    from layerloom.planar_materials import _set_hausdorff

    a = box(0, 0, 5, 5).difference(box(1, 1, 2, 2))
    b = box(0, 0, 5, 5).difference(box(3, 3, 4, 4))
    assert a.area == b.area
    assert _set_hausdorff(a, b) == shapely.hausdorff_distance(a, b) > 0
    plain = box(0, 0, 1, 1)
    sampled = Polygon([(0, 0), (.5, 0), (1, 0), (1, 1), (0, 1)])
    assert plain.equals(sampled)
    assert _set_hausdorff(plain, sampled) == shapely.hausdorff_distance(plain, sampled)


def test_reordered_exact_rings_and_components_keep_zero_distance(monkeypatch):
    import shapely
    from shapely.geometry import MultiPolygon, Polygon
    import layerloom.planar_materials as materials

    shell = [(0, 0), (10, 0), (10, 10), (0, 10)]
    holes = [list(box(1, 1, 2, 2).exterior.coords), list(box(3, 3, 4, 4).exterior.coords)]
    a = MultiPolygon([Polygon(shell, holes), box(11, 0, 12, 1)])
    b = MultiPolygon([shapely.reverse(box(11, 0, 12, 1)),
                      Polygon(shell[2:] + shell[:2], [ring[::-1] for ring in holes[::-1]])])
    assert shapely.hausdorff_distance(a, b) == 0

    def unexpected(*args, **kwargs):
        raise AssertionError("ring/component order does not require Hausdorff evaluation")

    monkeypatch.setattr(materials.shapely, "hausdorff_distance", unexpected)
    assert materials._set_hausdorff(a, b) == 0
