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
