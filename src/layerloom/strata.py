#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
layerloom.strata
----------------
Robust Z-band slicer for LayerLoom.

Slicing logic:
  - Repeats every `groups` bands (e.g. 3 → C/M/Y cycle; 4 → C/M/Y/K; 5 → C/M/Y/K/W)
  - Uses two-plane clipping with caps
  - Falls back to boolean intersection with a slab box
  - Pads in XY slightly to prevent empty or degenerate slices

Output:
  bands[g] = list of trimesh.Trimesh objects belonging to color group g

Notes
-----
- This slicer is token-agnostic. To “support K and W” you typically just set
  `groups=4` (adds K) or `groups=5` (adds K and W) in the caller.
- As a convenience, this module now exposes small helpers to map group indices
  to a token cycle over the alphabet "cmykw" without changing the slicer API.
"""

from __future__ import annotations
from typing import List, Optional, Sequence, Tuple
import math
import numpy as np
import trimesh

# ---------------------------------------------------------------------
# Optional convenience helpers (do not change core behavior)
# ---------------------------------------------------------------------

_DEFAULT_ALPHABET = "cmykw"

def group_token_for_band(k: int, *, alphabet: str = _DEFAULT_ALPHABET) -> str:
    """
    Return the token letter for band index k cycling over `alphabet`.
    Example: alphabet='cmykw' → k=0:'c', 1:'m', 2:'y', 3:'k', 4:'w', 5:'c', ...
    """
    if not alphabet:
        raise ValueError("alphabet must be non-empty")
    return alphabet[k % len(alphabet)]

def group_index_for_band(k: int, *, groups: int) -> int:
    """
    Return the group index in [0, groups-1] for band index k.
    (This is what the slicer itself uses internally.)
    """
    if groups <= 0:
        raise ValueError("groups must be > 0")
    return (k % groups + groups) % groups

# ---------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------

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


def _unique_mesh_edges(mesh: trimesh.Trimesh) -> np.ndarray:
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if faces.size == 0:
        return np.zeros((0, 2), dtype=np.int64)
    edges = np.vstack((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]))
    edges = np.sort(edges, axis=1)
    return np.unique(edges, axis=0)


def _slice_convex_between(mesh: trimesh.Trimesh,
                          zL_eff: float,
                          zU_eff: float,
                          *,
                          eps: float = 1e-9) -> Optional[trimesh.Trimesh]:
    """
    Deterministic fallback for convex meshes when trimesh's capped plane
    clipping fails. For a convex source mesh, the intersection with a horizontal
    slab is the convex hull of source vertices inside the slab plus mesh-edge
    intersections with the two slab planes.
    """
    if not bool(getattr(mesh, "is_convex", False)):
        return None

    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    if vertices.size == 0:
        return None

    points: List[np.ndarray] = []
    z = vertices[:, 2]
    inside = (z >= zL_eff - eps) & (z <= zU_eff + eps)
    if np.any(inside):
        points.extend(vertices[inside])

    for a_idx, b_idx in _unique_mesh_edges(mesh):
        a = vertices[int(a_idx)]
        b = vertices[int(b_idx)]
        za = float(a[2])
        zb = float(b[2])
        dz = zb - za
        if abs(dz) < eps:
            continue
        for plane_z in (zL_eff, zU_eff):
            t = (plane_z - za) / dz
            if -eps <= t <= 1.0 + eps:
                points.append(a + np.clip(t, 0.0, 1.0) * (b - a))

    if len(points) < 4:
        return None

    pts = np.unique(np.round(np.asarray(points, dtype=np.float64), decimals=12), axis=0)
    if len(pts) < 4:
        return None

    try:
        band = trimesh.Trimesh(vertices=pts, faces=[], process=False).convex_hull
    except Exception:
        return None
    return _clean(band)


def _repair_band_volume(source_mesh: trimesh.Trimesh,
                        band: Optional[trimesh.Trimesh]) -> Optional[trimesh.Trimesh]:
    """
    Try a narrow set of repairs for sliced slabs. For convex source meshes, a
    convex-hull rebuild is geometrically safe and fixes occasional non-manifold
    cap artifacts at fine layer heights.
    """
    band = _clean(band)
    if band is None:
        return None
    if band.is_volume:
        return band

    probe = band.copy()
    try:
        probe.process(validate=True)
        probe.remove_unreferenced_vertices()
    except Exception:
        pass
    if probe.is_volume:
        return probe

    try:
        trimesh.repair.fix_normals(probe)
    except Exception:
        pass
    if probe.is_volume:
        return probe

    if bool(getattr(source_mesh, "is_convex", False)):
        try:
            hull = band.convex_hull
            hull = _clean(hull)
        except Exception:
            hull = None
        if hull is not None and hull.is_volume:
            return hull

    return band


def _slice_between(mesh: trimesh.Trimesh,
                   zL: float,
                   zU: float,
                   *,
                   z_gap: float) -> Optional[trimesh.Trimesh]:
    """
    Slice a mesh between two absolute Z planes, with a small gap deducted from
    both ends to avoid coincident caps.
    """
    zL_eff = zL + 0.5 * z_gap
    zU_eff = zU - 0.5 * z_gap
    if zU_eff <= zL_eff:
        zU_eff = zL_eff + 1e-6

    band = None
    try:
        part = mesh.slice_plane([0, 0, zL_eff], [0, 0, 1], cap=True)
        if isinstance(part, trimesh.Trimesh) and part.faces.size > 0:
            part = part.slice_plane([0, 0, zU_eff], [0, 0, -1], cap=True)
            if isinstance(part, trimesh.Trimesh) and part.faces.size > 0:
                band = part
    except Exception:
        band = None

    if band is None:
        band = _slice_convex_between(mesh, zL_eff, zU_eff)

    if band is None:
        try:
            xmin, ymin, _ = mesh.bounds[0]
            xmax, ymax, _ = mesh.bounds[1]
            slab = (xmin - 1.0, xmax + 1.0, ymin - 1.0, ymax + 1.0, zL_eff, zU_eff)
            box = _build_slab_box(slab)
            inter = trimesh.boolean.intersection([mesh, box], engine=None)
            band = inter if isinstance(inter, trimesh.Trimesh) else (
                trimesh.util.concatenate(inter) if inter else None)
        except Exception:
            band = None

    return _repair_band_volume(mesh, band)


def token_run_segments(run: str) -> List[Tuple[str, int, int]]:
    """
    Return contiguous token segments as (token, start_index, length).
    Example: 'mmyyy' -> [('m', 0, 2), ('y', 2, 3)]
    """
    clean = "".join(ch for ch in (run or "").lower() if ch in _DEFAULT_ALPHABET)
    if not clean:
        return []
    out: List[Tuple[str, int, int]] = []
    start = 0
    current = clean[0]
    for idx, ch in enumerate(clean[1:], start=1):
        if ch != current:
            out.append((current, start, idx - start))
            current = ch
            start = idx
    out.append((current, start, len(clean) - start))
    return out


def estimate_run_aware_slice_count(minz: float,
                                   maxz: float,
                                   z0: float,
                                   step: float,
                                   run: str) -> int:
    """
    Estimate the number of run-aware slabs that intersect a mesh spanning
    [minz, maxz].
    """
    if step <= 0:
        raise ValueError("step must be > 0")
    segments = token_run_segments(run)
    if not segments or maxz <= minz:
        return 0

    cycle_len = len("".join(tok * length for tok, _, length in segments))
    if cycle_len <= 0:
        return 0

    k_min = math.floor((minz - z0) / step)
    k_max = math.ceil((maxz - z0) / step) - 1
    cycle_min = math.floor(k_min / cycle_len) - 1
    cycle_max = math.floor(k_max / cycle_len) + 1

    total = 0
    for cycle in range(cycle_min, cycle_max + 1):
        base = cycle * cycle_len
        for _token, start_idx, seg_len in segments:
            seg_start = base + start_idx
            seg_end = seg_start + seg_len
            if seg_end <= k_min or seg_start > k_max:
                continue
            total += 1
    return total


def slice_repeating_runs(mesh: trimesh.Trimesh,
                         z0: float,
                         step: float,
                         run: str,
                         *,
                         z_gap: float = 1e-3) -> List[Tuple[str, trimesh.Trimesh, int, int]]:
    """
    Slice a mesh using contiguous repeated-color runs rather than one slab per
    single layer in the token cycle.

    Returns:
      [(token, mesh, start_band_index, band_count), ...]
    """
    if step <= 0:
        raise ValueError("step must be > 0")

    clean_run = "".join(ch for ch in (run or "").lower() if ch in _DEFAULT_ALPHABET)
    segments = token_run_segments(clean_run)
    if not segments:
        return []

    minz, maxz = mesh.bounds[:, 2]
    if maxz <= minz:
        return []

    cycle_len = len(clean_run)
    k_min = math.floor((minz - z0) / step)
    k_max = math.ceil((maxz - z0) / step) - 1
    cycle_min = math.floor(k_min / cycle_len) - 1
    cycle_max = math.floor(k_max / cycle_len) + 1

    out: List[Tuple[str, trimesh.Trimesh, int, int]] = []
    for cycle in range(cycle_min, cycle_max + 1):
        base = cycle * cycle_len
        for token, start_idx, seg_len in segments:
            seg_start = base + start_idx
            seg_end = seg_start + seg_len
            if seg_end <= k_min or seg_start > k_max:
                continue
            band = _slice_between(
                mesh,
                z0 + seg_start * step,
                z0 + seg_end * step,
                z_gap=z_gap,
            )
            if band is None:
                continue
            out.append((token, band, seg_start, seg_len))

    return out

# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------

def slice_repeating(mesh: trimesh.Trimesh,
                    z0: float,
                    step: float,
                    groups: int,
                    *,
                    z_gap: float = 1e-3) -> List[List[trimesh.Trimesh]]:
    """
    Slice a mesh into repeating Z-bands using robust two-plane cutting with
    capping, and a boolean slab fallback.

    Args
    ----
    mesh   : trimesh.Trimesh input
    z0     : starting Z reference for the first band
    step   : band height
    groups : number of repeating groups (3→CMY, 4→CMYK, 5→CMYKW, etc.)
    z_gap  : small vertical gap deducted from each band's top/bottom to avoid
             coincident caps (default 1e-3)

    Returns
    -------
    bands[g] = list of meshes for group g
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
        band = _slice_between(mesh, z0 + k * step, z0 + (k + 1) * step, z_gap=z_gap)
        if band is None:
            continue

        g = (k % groups + groups) % groups
        bands[g].append(band)

    return bands
