#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
layerloom.group_by_color
------------------------

Group (and optionally merge) parts by their CMY color token.

Key behavior:
  • Uses the *last* '__PAT_...__' tag in each part label.
  • Single-letter tags ('c', 'm', 'y') identify actual sliced colors.
  • Multi-letter tags (e.g. '__PAT_cmy__') fall back to their first char.
  • Outputs merged meshes 'all_cyan', 'all_magenta', 'all_yellow'.

Also includes a write_color_stls() utility for exporting grouped STL files.

Dependencies: trimesh, numpy, re, os
"""

from __future__ import annotations
from typing import Dict, List, Tuple
import os
import re
import numpy as np
import trimesh

Part = Tuple[str, trimesh.Trimesh]
PAT_ANY = re.compile(r"__PAT_([cmy]+)__", re.IGNORECASE)

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _last_pat_token(label: str) -> str:
    """Return the effective CMY token by using the last '__PAT_...__' tag."""
    if not label:
        return "unknown"

    matches = list(PAT_ANY.finditer(label))
    if not matches:
        return "unknown"

    last = matches[-1].group(1).lower()
    if len(last) >= 1:
        return last[0]  # single-letter (preferred) or first char of multi-run
    return "unknown"


def _concat(meshes: List[trimesh.Trimesh]) -> trimesh.Trimesh:
    """Concatenate a list of meshes into a single trimesh.Trimesh."""
    if not meshes:
        return trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=int), process=False)
    if len(meshes) == 1:
        m = meshes[0]
        return trimesh.Trimesh(
            vertices=m.vertices.view(np.ndarray),
            faces=m.faces.view(np.ndarray),
            process=False,
        )
    return trimesh.util.concatenate(meshes)


# ---------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------

def group_by_color(parts: List[Part], merge: bool = True, verbose: bool = True) -> List[Part]:
    """
    Group parts by CMY token based on their final '__PAT_...__' tag.

    Parameters
    ----------
    parts : list[(label, mesh)]
        Band-sliced parts produced by assemble_by_color.
    merge : bool
        If True, concatenate all meshes of each color into one.
    verbose : bool
        Print diagnostic information.

    Returns
    -------
    list[(label, mesh)]
        [('all_cyan', M_c), ('all_magenta', M_m), ('all_yellow', M_y)]
    """
    if not parts:
        return []

    if not merge:
        out = [(lbl, m) for lbl, m in parts if isinstance(m, trimesh.Trimesh) and m.faces.size > 0]
        if verbose:
            print(f"[group] merge=False; passing through {len(out)} parts")
        return out

    buckets: Dict[str, List[trimesh.Trimesh]] = {"c": [], "m": [], "y": []}
    counts = {"c": 0, "m": 0, "y": 0, "unknown": 0}

    for label, mesh in parts:
        if not isinstance(mesh, trimesh.Trimesh) or mesh.faces.size == 0:
            continue
        token = _last_pat_token(label)
        if token in buckets:
            buckets[token].append(mesh)
            counts[token] += 1
        else:
            counts["unknown"] += 1

    merged: List[Part] = []
    if buckets["c"]:
        merged.append(("all_cyan", _concat(buckets["c"])))
        if verbose: print(f"[merge] merged {counts['c']:>4d} band(s) into 'all_cyan'")
    if buckets["m"]:
        merged.append(("all_magenta", _concat(buckets["m"])))
        if verbose: print(f"[merge] merged {counts['m']:>4d} band(s) into 'all_magenta'")
    if buckets["y"]:
        merged.append(("all_yellow", _concat(buckets["y"])))
        if verbose: print(f"[merge] merged {counts['y']:>4d} band(s) into 'all_yellow'")

    if verbose and counts["unknown"] > 0:
        print(f"[group] note: {counts['unknown']} band(s) had no recognizable PAT token")

    if verbose:
        print(f"[group] final part count: {len(merged)}")

    return merged


# ---------------------------------------------------------------------
# STL Export Utility
# ---------------------------------------------------------------------

def write_color_stls(parts: List[Part], out_dir: str, verbose: bool = True) -> None:
    """
    Write each (label, mesh) as an STL in `out_dir`.

    Skips empty meshes.
    """
    os.makedirs(out_dir, exist_ok=True)
    written = 0
    for label, mesh in parts:
        if not isinstance(mesh, trimesh.Trimesh) or mesh.faces.size == 0:
            continue
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in label)
        path = os.path.join(out_dir, f"{safe}.stl")
        mesh.export(path)
        written += 1
        if verbose:
            print(f"[stl] wrote {path}")
    if verbose:
        print(f"[stl] wrote {written} STL file(s) to {out_dir}")

