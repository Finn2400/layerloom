#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
layerloom.strata
----------------
Robust Z-band slicer for LayerLoom.

Slicing logic:
  - Repeats every `groups` bands (e.g. 3 → CMY cycle)
  - Uses two-plane clipping with caps
  - Falls back to boolean intersection with a slab box
  - Pads in XY slightly to prevent empty or degenerate slices

Output:
  bands[g] = list of trimesh.Trimesh objects belonging to color group g
"""

from __future__ import annotations
from typing import List, Optional
import math
import trimesh

def _slab_bounds_for_band(mesh: trimesh.Trimesh,
                          k: int,
                          z0: float,
                          step: float,
                          z_gap: float,
                          xy_pad: float = 1.0):
    (xmin, ymin, _), (xmax, ymax, _) = mesh.bounds
    L = z0 + k * step
    U = z0 + (k + 1) * step
    L_eff = L + 0.5 * z_gap
    U_eff = U - 0.5 * z_gap
    if U_eff <= L_eff:
        U_eff = L_eff + 1e-6
    return xmin - xy_pad, xmax + xy_pad, ymin - xy_pad, ymax + xy_pad, L_eff, U_eff

def _build_slab_box(slab):
    xmin, xmax, ymin, ymax, L_eff, U_eff = slab
    cx, cy, cz = 0.5 * (xmin + xmax), 0.5 * (ymin + ymax), 0.5 * (L_eff + U_eff)
    ex, ey, ez = (xmax - xmin), (ymax - ymin), (U_eff - L_eff)
    box = trimesh.creation.box(extents=(ex, ey, ez))
    box.apply_translation([cx, cy, cz])
    return box

def _clean(m: Optional[trimesh.Trimesh]) -> Optional[trimesh.Trimesh]:
    if m is None or not isinstance(m, trimesh.Trimesh) or m.faces.size == 0:
        return None
    try:
        m.remove_infinite_values()
        m.remove_unreferenced_vertices()
    except Exception:
        pass
    return m

def slice_repeating(mesh: trimesh.Trimesh,
                    z0: float,
                    step: float,
                    groups: int,
                    *,
                    z_gap: float = 1e-3) -> List[List[trimesh.Trimesh]]:
    """
    Slice a mesh into repeating Z-bands using robust two-plane cutting with
    capping, and a boolean slab fallback.

    Returns: bands[g] = list of meshes for group g
    """
    if groups <= 0:
        raise ValueError("groups must be > 0")
    if step <= 0:
        raise ValueError("step must be > 0")

    minz, maxz = mesh.bounds[:, 2]
    if maxz <= minz:
        return [[] for _ in range(groups)]

    k_min = math.floor((minz - z0) / step)
    k_max = math.ceil((maxz - z0) / step) - 1

    bands: List[List[trimesh.Trimesh]] = [[] for _ in range(groups)]

    for k in range(k_min, k_max + 1):
        zL = z0 + k * step + 0.5 * z_gap
        zU = z0 + (k + 1) * step - 0.5 * z_gap
        if zU <= zL:
            zU = zL + 1e-6

        band = None
        try:
            part = mesh.slice_plane([0, 0, zL], [0, 0, 1], cap=True)
            if isinstance(part, trimesh.Trimesh) and part.faces.size > 0:
                part = part.slice_plane([0, 0, zU], [0, 0, -1], cap=True)
                if isinstance(part, trimesh.Trimesh) and part.faces.size > 0:
                    band = part
        except Exception:
            band = None

        if band is None:
            try:
                slab = _slab_bounds_for_band(mesh, k, z0, step, z_gap, xy_pad=1.0)
                box = _build_slab_box(slab)
                inter = trimesh.boolean.intersection([mesh, box], engine=None)
                band = inter if isinstance(inter, trimesh.Trimesh) else (
                    trimesh.util.concatenate(inter) if inter else None)
            except Exception:
                band = None

        band = _clean(band)
        if band is None:
            continue

        g = (k % groups + groups) % groups
        bands[g].append(band)

    return bands

