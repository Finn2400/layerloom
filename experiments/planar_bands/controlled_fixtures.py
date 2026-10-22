"""Deterministic millimeter-scale curved controls for repair/layer timing.

The defective tube removes only four vertical wall triangles from its clean
twin. Both retain the same nominal annular footprint and all top/bottom cap
triangles. The two missing wall strips have known endpoint gaps at every
interior sample height, independent of the layer height under test.
"""
from __future__ import annotations

import math

import numpy as np
from shapely.geometry import Polygon
import trimesh


OUTER_RADIUS_MM = 10.0
INNER_RADIUS_MM = 8.0
HEIGHT_MM = 20.0
OUTER_GAP_MM = 0.020
SECTIONS = 256


def _rings():
    # The first edge spans the exact requested chord width. All remaining
    # edges evenly cover the rest of the circle, keeping a shared topology.
    half_angle = math.asin(OUTER_GAP_MM / (2 * OUTER_RADIUS_MM))
    angles = np.r_[-half_angle, np.linspace(half_angle, 2 * math.pi - half_angle,
                                          SECTIONS - 1, endpoint=False)]
    unit = np.column_stack((np.cos(angles), np.sin(angles)))
    return unit * OUTER_RADIUS_MM, unit * INNER_RADIUS_MM


def reference_profile(z: float = HEIGHT_MM / 2):
    """Exact polygonal intended cross-section; not an ideal-circle surrogate."""
    if not 0 < z < HEIGHT_MM:
        return Polygon()
    outer, inner = _rings()
    return Polygon(outer, [inner[::-1]])


def _tube(*, open_seam: bool):
    outer, inner = _rings()
    n = len(outer)
    xy = np.vstack((outer, inner))
    vertices = np.vstack((np.column_stack((xy, np.zeros(2 * n))),
                          np.column_stack((xy, np.full(2 * n, HEIGHT_MM)))))
    faces = []
    for i in range(n):
        j = (i + 1) % n
        ob, oj, ib, ij = i, j, n + i, n + j
        ot, jt, it, kt = 2 * n + i, 2 * n + j, 3 * n + i, 3 * n + j
        # Horizontal annular caps, outward normals down and up respectively.
        faces.extend(((ob, ib, oj), (oj, ib, ij),
                      (ot, jt, it), (jt, kt, it)))
        if not (open_seam and i == 0):
            # Outer wall faces outward; inner wall faces into the cavity.
            faces.extend(((ob, oj, jt), (ob, jt, ot),
                          (ib, it, kt), (ib, kt, ij)))
    return trimesh.Trimesh(vertices=vertices, faces=np.asarray(faces), process=False)


def fixtures():
    """Return names mapped to independent part lists and JSON-safe metadata."""
    reference = reference_profile()
    outer, inner = _rings()
    common = {
        "synthetic": True,
        "units": "millimeter",
        "shape": "hollow polygonal tube",
        "outer_diameter_mm": 2 * OUTER_RADIUS_MM,
        "inner_diameter_mm": 2 * INNER_RADIUS_MM,
        "height_mm": HEIGHT_MM,
        "circumferential_sections": len(outer),
        "expected_midpoint_holes": 1,
        "expected_midpoint_components": 1,
        "reference_midpoint_area_mm2": float(reference.area),
        "nominal_volume_mm3": float(reference.area * HEIGHT_MM),
        "reference": "controlled_fixtures.reference_profile(z)",
    }
    result = {}
    for name, open_seam in (("hollow_tube", False), ("hollow_tube_gap020", True)):
        mesh = _tube(open_seam=open_seam)
        metadata = {
            **common,
            "expected_source_is_volume": not open_seam,
            "input_triangles": int(len(mesh.faces)),
            "removed_wall_triangles": 4 if open_seam else 0,
            "outer_opening_mm": float(np.linalg.norm(outer[1] - outer[0])) if open_seam else 0.0,
            "inner_opening_mm": float(np.linalg.norm(inner[1] - inner[0])) if open_seam else 0.0,
            "defect": "two uncapped vertical wall strips; top/bottom annular caps retained" if open_seam else None,
        }
        result[name] = {"parts": [(f"{name}__PAT_cmy__", mesh)], "metadata": metadata}
    return result
