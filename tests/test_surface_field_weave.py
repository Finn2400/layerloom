"""Regression checks for closed, color-partitioned surface weave profiles."""
import numpy as np
import pytest

mf = pytest.importorskip("manifold3d")
shapely = pytest.importorskip("shapely")
if not hasattr(shapely, "constrained_delaunay_triangles"):
    pytest.skip("GEOS constrained triangulation is required", allow_module_level=True)
from shapely.geometry import Polygon, box
from layerloom.surface_field_weave import (
    extrude_profile, field_regions, mesh_defects, planar_region,
)


def test_closed_caps_preserve_holes_at_export_precision():
    profile = Polygon([(0, 0), (8, 0), (8, 8), (0, 8)],
                      [[(2, 2), (2, 6), (6, 6), (6, 2)]])
    mesh = extrude_profile(profile, 120.0005, 120.1195)
    assert mesh.is_volume
    assert mesh.volume == pytest.approx(profile.area * .119)
    assert not any(mesh_defects(mesh, (128, 128, 0)).values())


def test_nested_cross_section_and_disjoint_islands():
    section = mf.CrossSection.square((8, 8)) - mf.CrossSection.square((4, 4)).translate((2, 2))
    section += mf.CrossSection.square((1, 1)).translate((10, 0))
    profile = planar_region(section)
    assert profile.area == pytest.approx(49)
    assert not any(mesh_defects(extrude_profile(profile, 0, .12)).values())


@pytest.mark.parametrize("layer", range(12))
def test_partition_and_global_recipe_phase(layer):
    field = np.broadcast_to(np.linspace(-1, 1, 21)[:, None, None], (21, 21, 4)).copy()
    cover = np.ones_like(field, bool)
    cover[[0, -1], :, :] = False
    cover[:, [0, -1], :] = False
    footprint = box(2, 2, 18, 18)
    groups = field_regions(field, cover, np.zeros(3), 1, [-1, 0, 1],
                           ["mwwwww", "w", "cwwwww"], 1.5, layer, footprint)
    assert shapely.union_all(list(groups.values())).symmetric_difference(footprint).area < 1e-8
    assert sum(g.area for g in groups.values()) == pytest.approx(footprint.area)
    if layer % 6:
        assert set(groups) == {"w"}
    else:
        assert groups["m"].area > 0
        assert groups["c"].area > 0
    for geometry in groups.values():
        mesh = extrude_profile(geometry, layer * .12 + .0005, (layer + 1) * .12 - .0005)
        if mesh is not None:
            assert not any(mesh_defects(mesh, (128, 128, 0)).values())


def test_reject_inverted_band():
    with pytest.raises(ValueError):
        extrude_profile(box(0, 0, 1, 1), 1, 0)


def test_detect_geometric_degenerate_not_just_topology():
    import trimesh
    mesh = trimesh.Trimesh([[0, 0, 0], [1, 1, 0], [2, 2, 0]], [[0, 1, 2]], process=False)
    assert mesh_defects(mesh)["zero_area_triangles"] == 1


def test_point_contact_becomes_invalid_vertical_edge():
    profile = shapely.union_all([box(0, 0, 1, 1), box(1, 1, 2, 2)])
    mesh = extrude_profile(profile, 0, .119)
    assert mesh_defects(mesh)["nonmanifold_edges"] > 0
    separated = extrude_profile(profile.buffer(-.001, join_style="mitre"), 0, .119)
    assert not any(mesh_defects(separated, (128, 128, 0)).values())
