"""Layer-profile weaving for a scalar field on an already printable surface.

The molecular outline is sampled at each printer layer's midpoint. Color regions
are partitioned in 2D before triangulation, avoiding coincident internal faces
from independently clipping and concatenating 3D color masks. This intentionally
produces layer-stepped geometry, not a replacement smooth molecular surface.
"""
from __future__ import annotations

import numpy as np
import shapely
from shapely.geometry import Polygon
import trimesh
from trimesh.path.polygons import enclosure_tree


def polygons(geometry):
    if isinstance(geometry, Polygon):
        if not geometry.is_empty:
            yield geometry
    elif hasattr(geometry, "geoms"):
        for child in geometry.geoms:
            yield from polygons(child)


def planar_region(cross_section, precision=0.001):
    """Recover holes and islands from an oriented solid cross section."""
    rings = [Polygon(p) for p in cross_section.to_polygons() if len(p) >= 3]
    if not rings:
        return Polygon()
    roots, tree = enclosure_tree(rings)
    regions = [Polygon(rings[r].exterior.coords,
                       [rings[h].exterior.coords for h in tree.successors(r)])
               for r in roots]
    valid = shapely.make_valid(shapely.union_all(regions))
    return shapely.set_precision(shapely.union_all(list(polygons(valid))), precision)


def field_regions(field, cover, origin, pitch, levels, runs, z, layer_index,
                  domain, precision=0.001):
    """Disjoint physical-filament regions at one globally indexed layer."""
    import manifold3d as mf
    from skimage.measure import find_contours

    if len(levels) != len(runs) or np.any(np.diff(levels) <= 0):
        raise ValueError("Field levels must increase and have one recipe each")
    if any(not run for run in runs):
        raise ValueError("Empty weave recipe")
    iz = (z - origin[2]) / pitch
    j = int(np.floor(iz))
    if not 0 <= j < field.shape[2] - 1:
        raise ValueError("Layer outside the sampled scalar field")
    t = iz - j
    section = field[:, :, j] * (1 - t) + field[:, :, j + 1] * t
    section = np.where(cover[:, :, j] | cover[:, :, j + 1], section,
                       min(levels) - 1)
    colors = [run[layer_index % len(run)] for run in runs]
    remaining = shapely.set_precision(domain, precision)
    grouped = {c: [] for c in set(colors)}
    for i in range(len(runs) - 1, -1, -1):
        if i and colors[i] == colors[i - 1]:
            continue
        if i == 0:
            piece = remaining
        else:
            paths = find_contours(section, (levels[i] + levels[i - 1]) / 2,
                                  fully_connected="high")
            region = planar_region(mf.CrossSection(
                [np.asarray(p) * pitch + origin[:2] for p in paths],
                mf.FillRule.EvenOdd), precision)
            piece = shapely.intersection(region, remaining, grid_size=precision)
            piece = shapely.union_all(list(polygons(piece)), grid_size=precision)
        grouped[colors[i]].append(piece)
        remaining = shapely.difference(remaining, piece, grid_size=precision)
        remaining = shapely.union_all(list(polygons(remaining)), grid_size=precision)
    return {c: shapely.union_all(p, grid_size=precision)
            for c, p in grouped.items()}


def extrude_profile(geometry, low, high):
    """Use GEOS constrained triangulation, including holes, for closed bands."""
    if high <= low:
        raise ValueError("A band must have positive thickness")
    parts = []
    for polygon in polygons(geometry):
        triangles = shapely.constrained_delaunay_triangles(polygon)
        if triangles.is_empty:
            continue
        points = np.asarray([np.asarray(t.exterior.coords)[:3]
                             for t in triangles.geoms]).reshape(-1, 2)
        vertices, ids = np.unique(points, axis=0, return_inverse=True)
        faces = ids.reshape(-1, 3)
        mesh = trimesh.creation.extrude_triangulation(vertices, faces, high - low)
        mesh.apply_translation([0, 0, low])
        parts.append(mesh)
    return trimesh.util.concatenate(parts) if parts else None


def mesh_defects(mesh, translation=(0, 0, 0)):
    """Check exported single-precision geometry, not just indexed topology."""
    vertices = (np.asarray(mesh.vertices) + translation).astype(np.float32).astype(float)
    faces = np.asarray(mesh.faces)
    tri = vertices[faces]
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    _, ids = np.unique(vertices, axis=0, return_inverse=True)
    canonical = np.sort(ids[faces], axis=1)
    _, counts = np.unique(canonical, axis=0, return_counts=True)
    edges = np.sort(np.concatenate([ids[faces[:, [0, 1]]],
                                    ids[faces[:, [1, 2]]],
                                    ids[faces[:, [2, 0]]]]), axis=1)
    _, edge_counts = np.unique(edges, axis=0, return_counts=True)
    return {
        "zero_area_triangles": int(np.sum(np.all(cross == 0, axis=1))),
        "duplicate_faces": int(np.sum(counts - 1)),
        "nonmanifold_edges": int(np.sum(edge_counts != 2)),
        "not_closed_oriented_volume": int(not mesh.is_volume),
    }
