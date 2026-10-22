"""Bulk linework must preserve topology, ordering, and repair diagnostics."""
from __future__ import annotations

import numpy as np
import pytest
import shapely
from shapely.geometry import GeometryCollection, LineString, LinearRing, MultiLineString, Point, Polygon

import layerloom.planar_bands as bands


def previous_segments(geometry):
    result = []
    for line in shapely.get_parts(geometry):
        if line.geom_type != "LineString":
            continue
        coordinates = np.asarray(line.coords, dtype=float)[:, :2]
        result.extend(np.stack((coordinates[:-1], coordinates[1:]), axis=1))
    return np.asarray(result, dtype=float).reshape(-1, 2, 2)


def previous_node(segments, precision):
    return shapely.union_all([LineString(segment) for segment in segments], grid_size=precision)


@pytest.mark.parametrize("geometry", [
    GeometryCollection(), LineString(), Point(1, 2),
    LineString([(0, 0), (1, 0), (1, 1), (2, 2)]),
    MultiLineString([[(8, 1), (5, 1)], [(0, 0), (1, 0), (2, 0)]]),
    LineString([(0, 0, 2), (1, 0, 3), (1, 1, 4)]),
    GeometryCollection([
        Point(0, 0), LineString([(0, 0), (1, 0)]),
        Polygon([(0, 0), (1, 0), (1, 1)]), LineString(),
        LinearRing([(0, 0), (1, 0), (1, 1)]),
        MultiLineString([[(3, 3), (4, 4)]]),
        LineString([(1, 1), (2, 2), (3, 3)]),
    ]),
])
def test_bulk_segments_preserve_exact_coordinates_order_and_line_boundaries(geometry):
    actual = bands._segments(geometry)
    expected = previous_segments(geometry)
    assert actual.shape == expected.shape
    assert actual.dtype == expected.dtype
    assert np.array_equal(actual, expected)


def ring(points):
    points = np.asarray(points, dtype=float)
    return np.stack((points, np.roll(points, -1, axis=0)), axis=1)


SQUARE = ring([(0, 0), (1, 0), (1, 1), (0, 1)])
GAPPED = np.array([[[0, 0], [1, 0]], [[1, 0], [1, 1]],
                   [[1, 1], [0.51, 1]], [[0.49, 1], [0, 1]], [[0, 1], [0, 0]]])
CASES = [
    np.empty((0, 2, 2)),
    np.zeros((2, 2, 2)),
    SQUARE,
    SQUARE[[3, 1, 0, 2], ::-1],
    np.concatenate((SQUARE, SQUARE[:, ::-1], SQUARE)),
    np.concatenate((SQUARE * 10, SQUARE * 6 + 2, SQUARE * 2 + 4)),
    np.concatenate((SQUARE, SQUARE + [1.02, 0])),
    GAPPED,
    ring([(0, 0), (1, 1), (0, 1), (1, 0)]),
    np.concatenate((SQUARE, np.array([[[0.5, 0], [0.5, -0.5]]]))),
    np.array([[[0, 0], [1, 0]], [[1.01, 0], [1, 1]], [[1.02, 0], [2, 0]]]),
]


@pytest.mark.parametrize("segments", CASES)
def test_bulk_noding_preserves_exact_geos_linework(segments):
    before = previous_node(segments, 1e-5)
    after = bands._node(segments, 1e-5)
    assert before.wkb == after.wkb
    assert np.array_equal(previous_segments(before), bands._segments(after))


@pytest.mark.parametrize("segments", CASES)
@pytest.mark.parametrize("max_gap", [0, 0.03])
def test_bulk_recovery_keeps_exact_regions_and_all_acceptance_diagnostics(monkeypatch, segments, max_gap):
    config = bands.RepairConfig(max_gap=max_gap)
    after = bands.recover_profile(segments, config)
    with monkeypatch.context() as previous:
        previous.setattr(bands, "_segments", previous_segments)
        previous.setattr(bands, "_node", previous_node)
        before = bands.recover_profile(segments, config)
    assert before.region.wkb == after.region.wkb
    assert before.ok == after.ok
    assert before.diagnostics == after.diagnostics
