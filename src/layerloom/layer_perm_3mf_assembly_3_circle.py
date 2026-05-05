#!/bin/env python3
# -*- coding: utf-8 -*-
#
# layer_perm_3mf_volumes.py
#
# Export CMY regions as solid volumes and write:
#   1) Per-part STLs into a folder (DEFAULT).
#   2) Optional 3MF assembly with correctly named parts.
# Also emits a CSV mapping each region/part to its CMY token & fractions.
#
# NEW:
#   --shape {triangle,circle}  (default: triangle)
#     • triangle: original trigonal (CMY) ternary diagram inside an equilateral triangle
#     • circle  : radial/circular layout (Voronoi clipped to a disk)
#
# UPDATED (flexible per-stack color usage):
#   You can now control how many DISTINCT colors appear in each stack:
#     --colors-per-stack EX    (back-compat: EXACTly EX distinct colors; any int ≥1)
#     --colors-per-stack-exact EX
#     --min-colors-per-stack MIN
#     --max-colors-per-stack MAX
#     --allowed-combos  "c1+c2[,c1+c3,...]"
#     --disallowed-combos "..."
#
# Deps:
#   pip install numpy trimesh lxml matplotlib
#
# Quick start (triangle, back-compat defaults):
#   python layer_perm_3mf_volumes.py diag \
#     --shape triangle \
#     --palette "cyan,magenta,yellow" \
#     --height 5 \
#     --include-lower-heights \
#     --wrap-cyclic \
#     --unique-up-to-rotation \
#     --short-names "cyan=c,magenta=m,yellow=y" \
#     --grid-divisions 10 \
#     --grid-cell-size 0.065 \
#     --grid-pack 1 \
#     --tri-width-mm 120 \
#     --thickness 0.8 \
#     --export-stl-dir stl_out \
#     --export-3mf trigonal_regions.3mf \
#     --parts-csv parts_map.csv
#
# Circular example:
#   python layer_perm_3mf_volumes.py diag \
#     --shape circle \
#     --palette "cyan,magenta,yellow" \
#     --height 5 \
#     --include-lower-heights \
#     --wrap-cyclic \
#     --unique-up-to-rotation \
#     --colors-per-stack 2 \
#     --short-names "cyan=c,magenta=m,yellow=y" \
#     --grid-divisions 10 \
#     --grid-cell-size 0.065 \
#     --grid-pack 1 \
#     --tri-width-mm 120 \
#     --thickness 0.8 \
#     --export-stl-dir stl_out \
#     --export-3mf circular_regions_bicolor.3mf \
#     --parts-csv parts_map_bicolor.csv \
#     --debug-png debug_circle.png \
#     --debug-verbose
#
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import textwrap
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple, FrozenSet

import numpy as np
import trimesh as tm
import zipfile

# ──────────────────────────────────────────────────────────────────────────────
# Core types & rules (enumeration for stacks)
# ──────────────────────────────────────────────────────────────────────────────

Color = str
Predicate = Callable[[Sequence[Color]], bool]

@dataclass
class Rules:
    max_run_same: Optional[int] = 1
    forbid_adjacent_same: bool = True
    min_distance_same: int = 1
    banned_above: Dict[Color, set] = field(default_factory=dict)
    banned_below: Dict[Color, set] = field(default_factory=dict)
    required_counts: Optional[Dict[Color, int]] = None
    min_counts: Dict[Color, int] = field(default_factory=dict)
    max_counts: Dict[Color, int] = field(default_factory=dict)
    wrap_cyclic: bool = False
    custom_predicates: List[Predicate] = field(default_factory=list)

def _counts_ok(partial: Sequence[Color],
               palette: Sequence[Color],
               rules: Rules,
               target_height: int) -> bool:
    cnt: Dict[Color, int] = {c: 0 for c in palette}
    for c in partial:
        cnt[c] = cnt.get(c, 0) + 1

    if rules.required_counts is not None:
        for c in set(palette) | set(rules.required_counts):
            req = rules.required_counts.get(c, 0)
            if cnt.get(c, 0) > req:
                return False
            remaining = target_height - len(partial)
            if req - cnt.get(c, 0) > remaining:
                return False
        return True

    for c, mx in rules.max_counts.items():
        if cnt.get(c, 0) > mx:
            return False

    remaining = target_height - len(partial)
    need_sum = sum(max(0, m - cnt.get(c, 0)) for c, m in rules.min_counts.items())
    if need_sum > remaining:
        return False

    return True

def _local_ok(partial: Sequence[Color], rules: Rules) -> bool:
    n = len(partial)
    if n == 0:
        return True
    last = partial[-1]
    if n >= 2:
        prev = partial[-2]
        if rules.max_run_same is None or rules.max_run_same <= 1:
            if rules.forbid_adjacent_same and last == prev:
                return False
        if rules.min_distance_same > 1:
            k = rules.min_distance_same
            for i in range(1, min(k, n)):
                if partial[-1] == partial[-1 - i]:
                    return False
        if prev in rules.banned_above and last in rules.banned_above[prev]:
            return False
        if last in rules.banned_below and prev in rules.banned_below[last]:
            return False

    if rules.max_run_same is not None and n >= 1:
        run = 1
        i = n - 2
        while i >= 0 and partial[i] == last:
            run += 1
            if run > rules.max_run_same:
                return False
            i -= 1

    for p in rules.custom_predicates:
        if not p(partial):
            return False
    return True

def _wrap_ok(full: Sequence[Color], rules: Rules) -> bool:
    if not rules.wrap_cyclic or not full:
        return True
    if len(full) == 1:
        return True
    a, b = full[0], full[-1]
    if (rules.max_run_same is None or rules.max_run_same <= 1) and rules.forbid_adjacent_same and a == b:
        return False
    if rules.min_distance_same > 1 and a == b:
        return False
    if a in rules.banned_below and b in rules.banned_below[a]:
        return False
    if b in rules.banned_above and a in rules.banned_above[b]:
        return False
    if rules.max_run_same is not None and a == b:
        tail = 1
        i = len(full) - 2
        while i >= 0 and full[i] == b:
            tail += 1
            i -= 1
        head = 1
        j = 1
        while j < len(full) and full[j] == a:
            head += 1
            j += 1
        if head + tail > rules.max_run_same:
            return False
    return True

def enumerate_stacks(
    palette: Sequence[Color],
    height: int,
    rules: Optional[Rules] = None,
) -> Iterator[Tuple[Color, ...]]:
    r = rules or Rules()
    pal = list(palette)
    avail: Optional[Dict[Color, int]] = None
    if r.required_counts is not None:
        total = sum(r.required_counts.values())
        if total != height:
            raise ValueError("Sum of required_counts must equal height.")
        avail = dict(r.required_counts)

    def dfs(partial: List[Color]):
        n = len(partial)
        if n == height:
            if _wrap_ok(partial, r):
                yield tuple(partial)
            return
        if not _counts_ok(partial, pal, r, height):
            return
        cand = (c for c in (pal if avail is None else (c for c, k in avail.items() if k > 0)))
        for c in cand:
            partial.append(c)
            if _local_ok(partial, r):
                if avail is not None:
                    avail[c] -= 1
                    yield from dfs(partial)
                    avail[c] += 1
                else:
                    yield from dfs(partial)
            partial.pop()

    yield from dfs([])

def canonical_rotate(stack: Sequence[Color]) -> Tuple[Color, ...]:
    n = len(stack)
    rotations = [tuple(stack[i:] + stack[:i]) for i in range(n)]
    return min(rotations)

def dedupe_rotations(stacks: Iterable[Sequence[Color]]) -> List[Tuple[Color, ...]]:
    seen = set()
    out: List[Tuple[Color, ...]] = []
    for s in stacks:
        key = canonical_rotate(list(s))
        if key not in seen:
            seen.add(key)
            out.append(tuple(s))
    return out

# ──────────────────────────────────────────────────────────────────────────────
# Flexible per-stack color constraints
# ──────────────────────────────────────────────────────────────────────────────

def _parse_combos(arg: Optional[str]) -> List[FrozenSet[str]]:
    """Parse 'a+b,b+c' → [ {'a','b'}, {'b','c'} ]."""
    if not arg:
        return []
    combos: List[FrozenSet[str]] = []
    for chunk in arg.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split("+") if p.strip()]
        combos.append(frozenset(parts))
    return combos

def _filter_by_color_constraints(
    stacks: Iterable[Sequence[Color]],
    *,
    exact_k: Optional[int],
    min_k: Optional[int],
    max_k: Optional[int],
    allowed_sets: List[FrozenSet[str]],
    disallowed_sets: List[FrozenSet[str]],
    palette: Sequence[str],
) -> List[Tuple[Color, ...]]:
    pal_set = set(palette)
    # Validate combos against palette (warn unknowns)
    def _ok_combo(s: FrozenSet[str]) -> bool:
        unknown = [x for x in s if x not in pal_set]
        if unknown:
            print(f"[warn] combo {sorted(s)} contains colors not in palette: {unknown}", file=sys.stderr)
        return True

    for s in allowed_sets + disallowed_sets:
        _ok_combo(s)

    out: List[Tuple[Color, ...]] = []
    for stack in stacks:
        used = frozenset(stack)
        k = len(used)

        if exact_k is not None and k != exact_k:
            continue
        if min_k is not None and k < min_k:
            continue
        if max_k is not None and k > max_k:
            continue
        if allowed_sets and used not in allowed_sets:
            continue
        if disallowed_sets and used in disallowed_sets:
            continue

        out.append(tuple(stack))
    return out

# ──────────────────────────────────────────────────────────────────────────────
# CMY ternary mapping & token helpers
# ──────────────────────────────────────────────────────────────────────────────

def _barycentric_xy(fC: float, fM: float, fY: float) -> Tuple[float, float]:
    # Triangle vertices: C=(0,0), M=(1,0), Y=(1/2, sqrt(3)/2)
    return (fM + 0.5 * fY, (math.sqrt(3) / 2) * fY)

def _stack_fraction_CMY(stack: Sequence[Color], poles: Tuple[Color, Color, Color]) -> Tuple[float, float, float]:
    C, M, Y = poles
    n = len(stack) or 1
    cC = sum(1 for s in stack if s == C)
    cM = sum(1 for s in stack if s == M)
    cY = sum(1 for s in stack if s == Y)
    return (cC / n, cM / n, cY / n)

def _cmy_to_rgb01(fC: float, fM: float, fY: float) -> Tuple[float, float, float]:
    return (max(0.0, min(1.0, 1.0 - fC)),
            max(0.0, min(1.0, 1.0 - fM)),
            max(0.0, min(1.0, 1.0 - fY)))

def _adjacency_repeats(stack: Sequence[Color], wrap: bool) -> int:
    n = len(stack)
    if n <= 1:
        return 0
    rep = sum(1 for i in range(n - 1) if stack[i] == stack[i + 1])
    if wrap and stack[0] == stack[-1]:
        rep += 1
    return rep

def _parse_short_names(arg: Optional[str], palette: Sequence[str]) -> Dict[str, str]:
    import re
    if not arg:
        def short(c: str) -> str:
            m = re.search(r'[A-Za-z0-9]', c)
            return (m.group(0).lower() if m else 'x')
        return {c: short(c) for c in palette}
    out: Dict[str, str] = {}
    for pair in arg.split(","):
        if not pair:
            continue
        k, v = pair.split("=", 1)
        out[k.strip()] = v.strip()
    return out

def _stack_token(stack: Sequence[Color], short_names: Dict[str, str]) -> str:
    return "".join(short_names.get(c, c[:1].lower()) for c in stack)

def _sanitize_label(s: str) -> str:
    keep = []
    for ch in s:
        if ch.isalnum() or ch in "-_.":
            keep.append(ch)
        else:
            keep.append("_")
    out = "".join(keep).strip(".")
    return out or "part"

# ──────────────────────────────────────────────────────────────────────────────
# Geometry helpers: triangle & circle + Voronoi clipping
# ──────────────────────────────────────────────────────────────────────────────

# — Triangle layout —
def _ternary_grid_points(divisions: int) -> List[Tuple[float, float, float]]:
    pts: List[Tuple[float, float, float]] = []
    D = divisions
    for i in range(D + 1):
        for j in range(D + 1 - i):
            k = D - i - j
            fC = i / D; fM = j / D; fY = k / D
            pts.append((fC, fM, fY))
    return pts

def _offsets_for_pack(n: int) -> List[Tuple[float, float]]:
    if n <= 1: return [(0.0, 0.0)]
    if n == 2: return [(-0.25, 0.0), (0.25, 0.0)]
    if n == 3: return [(-0.25, 0.2), (0.25, 0.2), (0.0, -0.2)]
    return [(-0.22, 0.22), (0.22, 0.22), (-0.22, -0.22), (0.22, -0.22)]

def _clip_poly_halfplane(poly: List[Tuple[float,float]], nx: float, ny: float, c: float) -> List[Tuple[float,float]]:
    out: List[Tuple[float,float]] = []
    if not poly:
        return out
    eps = 1e-12
    def inside(p):
        return nx*p[0] + ny*p[1] <= c + eps
    def intersect(p1,p2):
        x1,y1 = p1; x2,y2 = p2
        dx,dy = (x2-x1, y2-y1)
        denom = nx*dx + ny*dy
        if abs(denom) < 1e-18:
            return p2
        t = (c - (nx*x1 + ny*y1)) / denom
        return (x1 + t*dx, y1 + t*dy)
    prev = poly[-1]
    prev_in = inside(prev)
    for cur in poly:
        cur_in = inside(cur)
        if cur_in:
            if not prev_in:
                out.append(intersect(prev,cur))
            out.append(cur)
        elif prev_in:
            out.append(intersect(prev,cur))
        prev, prev_in = cur, cur_in
    return out

def _bounded_voronoi_polygon(p: Tuple[float,float], others: List[Tuple[float,float]],
                             boundary_poly: List[Tuple[float,float]]) -> List[Tuple[float,float]]:
    poly = boundary_poly[:]
    px,py = p
    for qx,qy in others:
        nx = 2*(qx - px)
        ny = 2*(qy - py)
        c  = (qx*qx + qy*qy) - (px*px + py*py)
        poly = _clip_poly_halfplane(poly, nx, ny, c)
        if len(poly) < 3:
            break
    return poly

# — Circle layout —
def _circular_grid_points(divisions: int, radius: float = 1.0) -> List[Tuple[float, float]]:
    """
    Generate approximately uniform sampling within a disk:
    radii ~ sqrt(i/N), and per-ring angular samples proportional to circumference.
    """
    pts: List[Tuple[float, float]] = []
    rings = max(1, divisions)
    total = rings
    for i in range(rings):
        # radius in [0,1], area-uniform
        r = radius * math.sqrt((i + 0.5) / rings)
        # ensure at least 6 spokes; scale with circumference and divisions
        n_theta = max(6, int(2 * math.pi * r * (divisions if divisions > 0 else 1)))
        if n_theta == 0:
            continue
        for j in range(n_theta):
            theta = 2 * math.pi * j / n_theta
            pts.append((r * math.cos(theta), r * math.sin(theta)))
    # center point
    pts.append((0.0, 0.0))
    return pts

def _bounded_voronoi_polygon_circle(p: Tuple[float,float],
                                    others: List[Tuple[float,float]],
                                    radius: float) -> List[Tuple[float,float]]:
    """Voronoi cell of p clipped to a circle boundary (approximated by a 200-gon)."""
    n_circle = 200
    circle_poly = [(radius * math.cos(2 * math.pi * i / n_circle),
                    radius * math.sin(2 * math.pi * i / n_circle)) for i in range(n_circle)]
    return _bounded_voronoi_polygon(p, others, circle_poly)

# ──────────────────────────────────────────────────────────────────────────────
# Robust polygon → solid prism (watertight volumes)
# ──────────────────────────────────────────────────────────────────────────────

def _dedupe_consecutive(poly, tol=1e-12):
    if not poly:
        return []
    out = [poly[0]]
    for p in poly[1:]:
        if abs(p[0] - out[-1][0]) > tol or abs(p[1] - out[-1][1]) > tol:
            out.append(p)
    if len(out) >= 2 and abs(out[0][0] - out[-1][0]) <= tol and abs(out[0][1] - out[-1][1]) <= tol:
        out.pop()
    return out

def _remove_colinear(poly, tol=1e-12):
    n = len(poly)
    if n < 3:
        return poly[:]
    out = []
    for i in range(n):
        a = poly[(i - 1) % n]
        b = poly[i]
        c = poly[(i + 1) % n]
        ax, ay = a; bx, by = b; cx, cy = c
        abx, aby = (bx - ax, by - ay)
        bcx, bcy = (cx - bx, cy - by)
        cross = abx * bcy - aby * bcx
        if abs(cross) > tol:
            out.append(b)
    return out if len(out) >= 3 else poly[:]

def _area2(poly):
    s = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        s += x1 * y2 - x2 * y1
    return s

def _polygon_area(poly_xy: List[Tuple[float,float]]) -> float:
    return 0.5 * _area2(poly_xy)

def _ensure_ccw(poly):
    return poly if _area2(poly) > 0.0 else list(reversed(poly))

def _tri_fan_indices(n: int) -> List[Tuple[int,int,int]]:
    return [(0, i, i+1) for i in range(1, n-1)] if n >= 3 else []

def triangulate_prism_solid(poly2d: List[Tuple[float,float]], z0: float, z1: float,
                            clean_tol: float = 1e-9) -> Tuple[List[List[float]], List[List[int]]]:
    poly = _dedupe_consecutive(poly2d, tol=clean_tol)
    poly = _remove_colinear(poly, tol=clean_tol)
    if len(poly) < 3:
        return [], []
    poly = _ensure_ccw(poly)
    n = len(poly)

    V: List[List[float]] = []
    for (x,y) in poly:
        V.append([float(x), float(y), float(z0)])
    for (x,y) in poly:
        V.append([float(x), float(y), float(z1)])

    base0 = 0
    top0  = n
    F: List[List[int]] = []

    # base (clockwise when viewed from +Z) so outward normal points toward -Z
    for a,b,c in _tri_fan_indices(n):
        F.append([base0 + c, base0 + b, base0 + a])

    # top (CCW when viewed from +Z) so outward normal points toward +Z
    for a,b,c in _tri_fan_indices(n):
        F.append([top0 + a, top0 + b, top0 + c])

    # sides
    for i in range(n):
        j = (i + 1) % n
        bi, bj = base0 + i, base0 + j
        ti, tj = top0 + i,  top0 + j
        F.append([bi, bj, tj])
        F.append([bi, tj, ti])

    return V, F

# ──────────────────────────────────────────────────────────────────────────────
# 3MF writer (names preserved for slicers)
# ──────────────────────────────────────────────────────────────────────────────

try:
    from lxml import etree as ET
    HAVE_LXML = True
except Exception:
    import xml.etree.ElementTree as ET  # type: ignore
    HAVE_LXML = False

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
BAMBU_NS = "http://schemas.bambulab.com/package/2021"
XML_NS  = "http://www.w3.org/XML/1998/namespace"
SLIC3R_PE_NS = "http://slic3r.org/ns/pe"

NSMAP = {
    None: CORE_NS,
    "BambuStudio": BAMBU_NS,
    "slic3r_pe": SLIC3R_PE_NS,
    "xml": XML_NS
}

if not HAVE_LXML:
    ET.register_namespace("", CORE_NS)
    ET.register_namespace("BambuStudio", BAMBU_NS)
    ET.register_namespace("slic3r_pe", SLIC3R_PE_NS)

def q(tag: str) -> str:
    return f"{{{CORE_NS}}}{tag}"

def new_model_root(title: str) -> ET.Element:
    if HAVE_LXML:
        root = ET.Element(q("model"), nsmap=NSMAP)
        root.set("unit", "millimeter")
        root.set(ET.QName(XML_NS, "lang"), "en-US")
    else:
        root = ET.Element(q("model"), {"unit": "millimeter", "xml:lang": "en-US"})
    ET.SubElement(root, q("metadata"), {"name": "Title"}).text = title
    return root

def _append_object_metadata(parent: ET.Element, metadata: Dict[str, str]) -> None:
    if not metadata:
        return
    mg = ET.SubElement(parent, q("metadatagroup"))
    for key, value in metadata.items():
        if value is None:
            continue
        ET.SubElement(mg, q("metadata"), {"name": str(key)}).text = str(value)


def stamp_object_labels(obj_el: ET.Element, label: str) -> None:
    obj_el.set("name", label)
    obj_el.set("partnumber", label)
    ET.SubElement(obj_el, q("metadata"), {"name": "Title"}).text = label
    ET.SubElement(obj_el, q("metadata"), {"name": "Name"}).text = label
    if HAVE_LXML:
        ET.SubElement(obj_el, q("metadata"), {"name": f"{{{SLIC3R_PE_NS}}}Name"}).text = label
        ET.SubElement(obj_el, q("metadata"), {"name": f"{{{BAMBU_NS}}}ModelName"}).text = label
    else:
        ET.SubElement(obj_el, q("metadata"), {"name": "slic3r_pe:Name"}).text = label
        ET.SubElement(obj_el, q("metadata"), {"name": "BambuStudio:ModelName"}).text = label

def mesh_to_object(obj_id: int, mesh: tm.Trimesh, label: str, metadata: Optional[Dict[str, str]] = None) -> ET.Element:
    obj = ET.Element(q("object"), {"id": str(obj_id), "type":"model"})
    stamp_object_labels(obj, label)
    _append_object_metadata(obj, metadata or {})
    mesh_el = ET.SubElement(obj, q("mesh"))
    verts_el = ET.SubElement(mesh_el, q("vertices"))
    for v in mesh.vertices.view(np.ndarray):
        e = ET.SubElement(verts_el, q("vertex"))
        e.set("x", f"{float(v[0]):.9f}"); e.set("y", f"{float(v[1]):.9f}"); e.set("z", f"{float(v[2]):.9f}")
    tris_el = ET.SubElement(mesh_el, q("triangles"))
    for f in mesh.faces.view(np.ndarray):
        t = ET.SubElement(tris_el, q("triangle"))
        t.set("v1", str(int(f[0]))); t.set("v2", str(int(f[1]))); t.set("v3", str(int(f[2])))
    return obj

def _content_types_xml() -> bytes:
    return ("""
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>
""").strip().encode("utf-8")

def _rels_root_xml() -> bytes:
    return ("""
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" Target="/3D/3dmodel.model"/>
</Relationships>
""").strip().encode("utf-8")

def _rels_empty_xml() -> bytes:
    return b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'

def write_3mf_as_assembly(
    objects: List[Tuple[str, tm.Trimesh]],
    out_path: str,
    title: str,
    metadata_by_label: Optional[Dict[str, Dict[str, str]]] = None,
) -> None:
    root = new_model_root(title)
    resources = ET.SubElement(root, q("resources"))
    build = ET.SubElement(root, q("build"))

    part_ids = []
    next_id = 1
    for label, mesh in objects:
        part_id = next_id
        part_ids.append(part_id)
        obj_el = mesh_to_object(part_id, mesh, label, metadata=(metadata_by_label or {}).get(label))
        resources.append(obj_el)
        next_id += 1

    assembly_id = next_id
    assembly_obj = ET.Element(q("object"), {"id": str(assembly_id), "type": "model"})
    stamp_object_labels(assembly_obj, title)
    components = ET.SubElement(assembly_obj, q("components"))
    for part_id in part_ids:
        ET.SubElement(components, q("component"), {"objectid": str(part_id)})
    resources.append(assembly_obj)

    # Use a single printable assembly build item.
    # The leaf mesh objects remain addressable as separate parts for LayerLoom,
    # but avoiding per-leaf build items prevents downstream bake duplication.
    ET.SubElement(build, q("item"), {"objectid": str(assembly_id), "printable": "1"})

    xml_bytes = (ET.tostring(root, pretty_print=True, xml_declaration=True, encoding="utf-8")
                 if HAVE_LXML else ET.tostring(root, encoding="utf-8", xml_declaration=True))
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _content_types_xml())
        z.writestr("_rels/.rels", _rels_root_xml())
        z.writestr("3D/3dmodel.model", xml_bytes)
        z.writestr("3D/_rels/3dmodel.model.rels", _rels_empty_xml())

# ──────────────────────────────────────────────────────────────────────────────
# DIAG engine: choose stacks per cell, build regions, export volumes + CSV map
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_ternary_poles(args, palette: List[str]) -> Tuple[str, str, str]:
    if getattr(args, "ternary_poles", None):
        toks = [t.strip() for t in args.ternary_poles.split(",")]
        if len(toks) != 3:
            print("[error] --ternary-poles must specify exactly three colors.", file=sys.stderr)
            sys.exit(2)
        poles = tuple(toks)  # type: ignore
    else:
        if len(palette) < 3:
            print("[error] Need at least three colors in --palette for ternary.", file=sys.stderr)
            sys.exit(2)
        poles = (palette[0], palette[1], palette[2])
    for pcol in poles:
        if pcol not in palette:
            print(f"[error] ternary pole '{pcol}' not in palette.", file=sys.stderr)
            sys.exit(2)
    return poles  # type: ignore

def _enumerate_upto_height(palette: List[str],
                           max_height: int,
                           rules: Rules,
                           unique_up_to_rotation: bool,
                           limit: Optional[int]) -> List[Tuple[str, ...]]:
    all_stacks: List[Tuple[str, ...]] = []
    remaining: Optional[int] = None if limit is None else int(limit)
    for H in range(1, max_height + 1):
        if remaining is not None and remaining <= 0:
            break
        it = enumerate_stacks(palette, H, rules)
        if unique_up_to_rotation and rules.wrap_cyclic:
            pool: List[Tuple[str, ...]] = []
            if remaining is None:
                for s in it:
                    pool.append(s)
            else:
                for _, s in zip(range(remaining), it):
                    pool.append(s)
            deduped = dedupe_rotations(pool)
            take = deduped if remaining is None else deduped[:remaining]
            all_stacks.extend(take)
            if remaining is not None:
                remaining -= len(take)
        else:
            for s in it:
                all_stacks.append(s)
                if remaining is not None:
                    remaining -= 1
                    if remaining <= 0:
                        break
    return all_stacks

def _diag_compute_triangle(
    *,
    palette: List[str],
    stacks: List[Tuple[str, ...]],
    poles: Tuple[str,str,str],
    grid_divisions: int,
    cell_size: float,
    pack_per_cell: int,
    short_names_map: Dict[str,str],
    debug_png: Optional[str],
    debug_verbose: bool,
    region_centers_mode: str
) -> Tuple[List[Tuple[float,float,Tuple[float,float,float],str,int]], List[List[Tuple[float,float]]]]:
    # Triangle vertices and grid
    C,M,Y = poles
    vC=(0.0,0.0); vM=(1.0,0.0); vY=(0.5, math.sqrt(3)/2)
    tri_xy = [vC,vM,vY]

    bary_pts = _ternary_grid_points(grid_divisions)
    xy_pts = [_barycentric_xy(fC,fM,fY) for (fC,fM,fY) in bary_pts]

    from collections import defaultdict
    buckets = defaultdict(list)
    for idx, s in enumerate(stacks):
        fC,fM,fY = _stack_fraction_CMY(s, (C,M,Y))
        sx,sy = _barycentric_xy(fC,fM,fY)
        best_k=0; best_d2=1e30
        for k,(gx,gy) in enumerate(xy_pts):
            dx=sx-gx; dy=sy-gy; d2=dx*dx+dy*dy
            if d2<best_d2: best_d2=d2; best_k=k
        buckets[best_k].append((s,idx))

    chosen_centers_xy: List[Tuple[float,float]] = []
    chosen_colors_rgb: List[Tuple[float,float,float]] = []
    chosen_tokens: List[str] = []
    chosen_src_idx: List[int] = []

    for k, items in buckets.items():
        gx,gy = xy_pts[k]
        items_sorted = sorted(items, key=lambda t: _adjacency_repeats(t[0], wrap=True))

        if region_centers_mode == "grid":
            rep_stack, rep_idx = items_sorted[0]
            fC,fM,fY = _stack_fraction_CMY(rep_stack, (C,M,Y))
            rgb = _cmy_to_rgb01(fC,fM,fY)
            token = _stack_token(rep_stack, short_names_map)
            chosen_centers_xy.append((gx,gy))
            chosen_colors_rgb.append(rgb)
            chosen_tokens.append(token)
            chosen_src_idx.append(rep_idx)
        else:
            offsets = _offsets_for_pack(min(len(items_sorted), pack_per_cell))
            for (offset, (stack, sidx)) in zip(offsets, items_sorted[:pack_per_cell]):
                ox,oy = offset
                cx = gx + ox*cell_size
                cy = gy + oy*cell_size
                fC,fM,fY = _stack_fraction_CMY(stack, (C,M,Y))
                rgb = _cmy_to_rgb01(fC,fM,fY)
                token = _stack_token(stack, short_names_map)
                chosen_centers_xy.append((cx,cy))
                chosen_colors_rgb.append(rgb)
                chosen_tokens.append(token)
                chosen_src_idx.append(sidx)

    regions: List[List[Tuple[float,float]]] = []
    for i,p in enumerate(chosen_centers_xy):
        others = [q for j,q in enumerate(chosen_centers_xy) if j!=i]
        poly = _bounded_voronoi_polygon(p, others, tri_xy)
        regions.append(poly)
        if debug_verbose:
            ar2 = _area2(poly)
            print(f"[dbg] region {i}: verts={len(poly)} area={0.5*abs(ar2):.6f}", file=sys.stderr)

    if debug_png:
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(7.2,6.6))
        tri = [vC,vM,vY,vC]
        ax.plot([p[0] for p in tri],[p[1] for p in tri], color='black', lw=1.0)
        for poly, col in zip(regions, chosen_colors_rgb):
            if len(poly)>=3:
                xs=[p[0] for p in poly]; ys=[p[1] for p in poly]
                ax.fill(xs, ys, color=col, alpha=0.65, edgecolor='k', linewidth=0.4)
        if region_centers_mode == "grid" and pack_per_cell > 1:
            for k, items in buckets.items():
                gx, gy = xy_pts[k]
                items_sorted = sorted(items, key=lambda t: _adjacency_repeats(t[0], wrap=True))
                offsets = _offsets_for_pack(min(len(items_sorted), pack_per_cell))
                for (offset, (stack, _)) in zip(offsets, items_sorted[:pack_per_cell]):
                    ox, oy = offset
                    cx = gx + ox*cell_size
                    cy = gy + oy*cell_size
                    fC,fM,fY = _stack_fraction_CMY(stack, (C,M,Y))
                    col = _cmy_to_rgb01(fC,fM,fY)
                    ax.scatter([cx],[cy], s=22, facecolors='none', edgecolors=col, linewidths=1.0)
        ax.scatter([p[0] for p in chosen_centers_xy],[p[1] for p in chosen_centers_xy],
                   s=20, c=chosen_colors_rgb, edgecolors='k', linewidths=0.4)
        ax.set_xlim(-0.05,1.05); ax.set_ylim(-0.05, math.sqrt(3)/2 + 0.05)
        ax.set_aspect('equal','box'); ax.set_xticks([]); ax.set_yticks([])
        fig.tight_layout()
        fig.savefig(debug_png, dpi=220, bbox_inches="tight")
        plt.close(fig)

    centers = [(x,y,c,t,idx) for ( (x,y), c, t, idx) in zip(chosen_centers_xy, chosen_colors_rgb, chosen_tokens, chosen_src_idx)]
    return centers, regions

def _diag_compute_circle(
    *,
    palette: List[str],
    stacks: List[Tuple[str, ...]],
    poles: Tuple[str,str,str],
    grid_divisions: int,
    cell_size: float,
    pack_per_cell: int,
    short_names_map: Dict[str,str],
    debug_png: Optional[str],
    debug_verbose: bool,
    region_centers_mode: str
) -> Tuple[List[Tuple[float,float,Tuple[float,float,float],str,int]], List[List[Tuple[float,float]]]]:
    C, M, Y = poles
    radius = 1.0

    # Define symmetric pole vectors on unit circle (0°, 120°, 240°)
    pole_vecs = {
        C: (math.cos(0), math.sin(0)),
        M: (math.cos(2 * math.pi / 3), math.sin(2 * math.pi / 3)),
        Y: (math.cos(4 * math.pi / 3), math.sin(4 * math.pi / 3)),
    }

    # Generate grid points for possible site placement
    xy_pts = _circular_grid_points(grid_divisions, radius)

    from collections import defaultdict
    buckets = defaultdict(list)

    # Compute each stack’s CMY-weighted pole position
    for idx, s in enumerate(stacks):
        fC, fM, fY = _stack_fraction_CMY(s, (C, M, Y))
        vx = fC * pole_vecs[C][0] + fM * pole_vecs[M][0] + fY * pole_vecs[Y][0]
        vy = fC * pole_vecs[C][1] + fM * pole_vecs[M][1] + fY * pole_vecs[Y][1]
        gx, gy = vx, vy
        best_k, best_d2 = 0, 1e30
        for k, (x, y) in enumerate(xy_pts):
            d2 = (x - gx)**2 + (y - gy)**2
            if d2 < best_d2:
                best_d2, best_k = d2, k
        buckets[best_k].append((s, idx))

    chosen_centers_xy, chosen_colors_rgb, chosen_tokens, chosen_src_idx = [], [], [], []

    for k, items in buckets.items():
        gx, gy = xy_pts[k]
        items_sorted = sorted(items, key=lambda t: _adjacency_repeats(t[0], wrap=True))

        if region_centers_mode == "grid":
            rep_stack, rep_idx = items_sorted[0]
            fC, fM, fY = _stack_fraction_CMY(rep_stack, (C, M, Y))
            rgb = _cmy_to_rgb01(fC, fM, fY)
            token = _stack_token(rep_stack, short_names_map)
            chosen_centers_xy.append((gx, gy))
            chosen_colors_rgb.append(rgb)
            chosen_tokens.append(token)
            chosen_src_idx.append(rep_idx)
        else:
            offsets = _offsets_for_pack(min(len(items_sorted), pack_per_cell))
            for (offset, (stack, sidx)) in zip(offsets, items_sorted[:pack_per_cell]):
                ox, oy = offset
                cx = gx + ox * cell_size
                cy = gy + oy * cell_size
                fC, fM, fY = _stack_fraction_CMY(stack, (C, M, Y))
                rgb = _cmy_to_rgb01(fC, fM, fY)
                token = _stack_token(stack, short_names_map)
                chosen_centers_xy.append((cx, cy))
                chosen_colors_rgb.append(rgb)
                chosen_tokens.append(token)
                chosen_src_idx.append(sidx)

    regions = []
    for i, p in enumerate(chosen_centers_xy):
        others = [q for j, q in enumerate(chosen_centers_xy) if j != i]
        poly = _bounded_voronoi_polygon_circle(p, others, radius)
        regions.append(poly)
        if debug_verbose:
            print(f"[dbg] (circle) region {i}: verts={len(poly)} area={0.5*abs(_area2(poly)):.6f}", file=sys.stderr)

    if debug_png:
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(6.4, 6.4))
        for poly, col in zip(regions, chosen_colors_rgb):
            if len(poly) >= 3:
                xs, ys = zip(*poly)
                ax.fill(xs, ys, color=col, alpha=0.6, edgecolor='k', linewidth=0.35)
        ax.scatter([x for x, _ in chosen_centers_xy],
                   [y for _, y in chosen_centers_xy],
                   s=22, c=chosen_colors_rgb, edgecolors='k', linewidths=0.4)
        circ = plt.Circle((0, 0), radius, fill=False, linewidth=1.0)
        ax.add_patch(circ)
        ax.set_aspect('equal', 'box')
        ax.set_xlim(-1.05, 1.05)
        ax.set_ylim(-1.05, 1.05)
        ax.axis('off')
        fig.tight_layout()
        fig.savefig(debug_png, dpi=220, bbox_inches="tight")
        plt.close(fig)

    centers = [(x, y, c, t, idx) for ((x, y), c, t, idx) in zip(chosen_centers_xy, chosen_colors_rgb, chosen_tokens, chosen_src_idx)]
    return centers, regions


def _diag_compute(
    *,
    shape: str,
    palette: List[str],
    stacks: List[Tuple[str, ...]],
    poles: Tuple[str,str,str],
    grid_divisions: int,
    cell_size: float,
    pack_per_cell: int,
    short_names_map: Dict[str,str],
    debug_png: Optional[str],
    debug_verbose: bool,
    region_centers_mode: str
) -> Tuple[List[Tuple[float,float,Tuple[float,float,float],str,int]], List[List[Tuple[float,float]]]]:
    if shape == "triangle":
        return _diag_compute_triangle(
            palette=palette, stacks=stacks, poles=poles,
            grid_divisions=grid_divisions, cell_size=cell_size,
            pack_per_cell=pack_per_cell, short_names_map=short_names_map,
            debug_png=debug_png, debug_verbose=debug_verbose,
            region_centers_mode=region_centers_mode
        )
    elif shape == "circle":
        return _diag_compute_circle(
            palette=palette, stacks=stacks, poles=poles,
            grid_divisions=grid_divisions, cell_size=cell_size,
            pack_per_cell=pack_per_cell, short_names_map=short_names_map,
            debug_png=debug_png, debug_verbose=debug_verbose,
            region_centers_mode=region_centers_mode
        )
    else:
        print(f"[error] Unknown --shape '{shape}'.", file=sys.stderr)
        sys.exit(2)

# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def parse_counts_arg(arg: Optional[str]) -> Optional[Dict[str, int]]:
    if not arg:
        return None
    d: Dict[str, int] = {}
    for pair in arg.split(","):
        if not pair:
            continue
        k, v = pair.split("=")
        d[k.strip()] = int(v)
    return d

def parse_pairs(arg: Optional[str]) -> Dict[str, set]:
    out: Dict[str, set] = {}
    if not arg:
        return out
    for clause in arg.split(";"):
        clause = clause.strip()
        if not clause:
            continue
        if ">" in clause:
            a, b = clause.split(">", 1)
            out.setdefault(a.strip(), set()).add(b.strip())
        elif "<" in clause:
            b, a = clause.split("<", 1)
            out.setdefault(a.strip(), set()).add(b.strip())
        else:
            raise SystemExit(f"Invalid pair clause '{clause}'. Use A>B;C>D or A<B syntax.")
    return out

def _ensure_unique_filename(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while True:
        cand = f"{base}_{i}{ext}"
        if not os.path.exists(cand):
            return cand
        i += 1

def cmd_diag(args):
    palette = [c.strip() for c in args.palette.split(",") if c.strip()]

    max_run = 1 if args.max_run_same is None else max(1, int(args.max_run_same))
    forbid_adjacent = (max_run <= 1)

    rules = Rules(
        max_run_same=max_run,
        forbid_adjacent_same=forbid_adjacent and not args.allow_adjacent_same,
        min_distance_same=max(1, args.min_distance_same),
        wrap_cyclic=args.wrap_cyclic,
        required_counts=parse_counts_arg(args.required_counts),
        min_counts=parse_counts_arg(args.min_counts) or {},
        max_counts=parse_counts_arg(args.max_counts) or {},
        banned_above=parse_pairs(args.banned_above),
        banned_below=parse_pairs(args.banned_below),
    )

    # Enumerate stacks
    if args.include_lower_heights:
        stacks = _enumerate_upto_height(
            palette=palette,
            max_height=args.height,
            rules=rules,
            unique_up_to_rotation=args.unique_up_to_rotation,
            limit=args.limit
        )
    else:
        it = enumerate_stacks(palette, args.height, rules)
        if args.unique_up_to_rotation and rules.wrap_cyclic:
            cap = args.limit if args.limit is not None else 10_000
            pool = [s for _, s in zip(range(cap), it)]
            stacks = dedupe_rotations(pool)
            if args.limit is not None:
                stacks = stacks[:args.limit]
        else:
            stacks = []
            for s in it:
                stacks.append(s)
                if args.limit is not None and len(stacks) >= args.limit:
                    break

    # ── Apply flexible per-stack color constraints ─────────────
    exact_k = args.colors_per_stack_exact
    if exact_k is None and args.colors_per_stack is not None:
        exact_k = args.colors_per_stack
    min_k = args.min_colors_per_stack
    max_k = args.max_colors_per_stack

    # Back-compat default EXACT=3 if none specified
    if exact_k is None and min_k is None and max_k is None:
        exact_k = 3

    # Validate against palette size
    palette_size = len(set(palette))
    if exact_k is not None and exact_k > palette_size:
        print(f"[error] requested exact {exact_k} colors per stack, but palette has only {palette_size}.", file=sys.stderr)
        sys.exit(2)
    if max_k is not None and max_k > palette_size:
        print(f"[warn] clamping --max-colors-per-stack {max_k} → {palette_size} (palette size)", file=sys.stderr)
        max_k = palette_size
    if min_k is not None and min_k < 1:
        print(f"[warn] raising --min-colors-per-stack {min_k} → 1", file=sys.stderr)
        min_k = 1
    if exact_k is not None:
        if min_k is not None and exact_k < min_k:
            print(f"[error] exact {exact_k} < min {min_k}", file=sys.stderr); sys.exit(2)
        if max_k is not None and exact_k > max_k:
            print(f"[error] exact {exact_k} > max {max_k}", file=sys.stderr); sys.exit(2)

    allowed_sets = _parse_combos(args.allowed_combos)
    disallowed_sets = _parse_combos(args.disallowed_combos)

    before = len(stacks)
    stacks = _filter_by_color_constraints(
        stacks,
        exact_k=exact_k,
        min_k=min_k,
        max_k=max_k,
        allowed_sets=allowed_sets,
        disallowed_sets=disallowed_sets,
        palette=palette,
    )
    if args.debug_verbose:
        detail = []
        if exact_k is not None: detail.append(f"exact={exact_k}")
        if min_k is not None:   detail.append(f"min={min_k}")
        if max_k is not None:   detail.append(f"max={max_k}")
        if allowed_sets:        detail.append(f"allowed={['+'.join(sorted(s)) for s in allowed_sets]}")
        if disallowed_sets:     detail.append(f"disallowed={['+'.join(sorted(s)) for s in disallowed_sets]}")
        print(f"[dbg] color-constraints({' '.join(detail) if detail else 'default exact=3'}): kept {len(stacks)}/{before}", file=sys.stderr)

    if not stacks:
        print("[error] No stacks remain after applying color constraints; relax your settings.", file=sys.stderr)
        sys.exit(2)

    # ── compute regions (triangle or circle) ───────────────────
    poles = _resolve_ternary_poles(args, palette)
    short_names_map = _parse_short_names(args.short_names, palette)

    centers, regions = _diag_compute(
        shape=args.shape,
        palette=palette,
        stacks=stacks,
        poles=poles,
        grid_divisions=args.grid_divisions,
        cell_size=args.grid_cell_size,
        pack_per_cell=args.grid_pack,
        short_names_map=short_names_map,
        debug_png=args.debug_png,
        debug_verbose=args.debug_verbose,
        region_centers_mode=args.region_centers
    )

    # XY scaling: use tri_width_mm as a generic XY scale (kept for back-compat)
    tri_width = float(args.tri_width_mm)
    thickness = max(float(args.thickness), 0.2)
    sx = tri_width
    sy = tri_width

    rows_for_csv: List[Dict[str, object]] = []
    seen_counts: Dict[str, int] = {}

    for i, poly in enumerate(regions):
        token = centers[i][3] if i < len(centers) else f"region_{i}"
        cnt = seen_counts.get(token, 0)
        seen_counts[token] = cnt + 1
        label = token if cnt == 0 else f"{token}_{cnt+1}"

        sidx = centers[i][4] if i < len(centers) else -1
        stack = stacks[sidx] if 0 <= sidx < len(stacks) else tuple()

        fC,fM,fY = _stack_fraction_CMY(stack, poles)
        r,g,b = _cmy_to_rgb01(fC,fM,fY)

        area_units = abs(_polygon_area(poly))
        area_mm2 = area_units * (sx * sy)  # uniform XY scaling
        volume_mm3 = area_mm2 * thickness

        cx, cy = centers[i][0], centers[i][1]

        rows_for_csv.append({
            "region_index": i,
            "label": label,
            "token": token,
            "stack_index": sidx,
            "stack": "".join(stack),
            "fC": fC, "fM": fM, "fY": fY,
            "r": r, "g": g, "b": b,
            "center_x": cx, "center_y": cy,
            "area_mm2": area_mm2,
            "volume_mm3": volume_mm3
        })

    if args.parts_csv:
        with open(args.parts_csv, "w", newline="") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "region_index","label","token","stack_index","stack",
                    "fC","fM","fY","r","g","b",
                    "center_x","center_y","area_mm2","volume_mm3"
                ]
            )
            w.writeheader()
            w.writerows(rows_for_csv)
        print(f"[info] wrote parts CSV: {args.parts_csv}", file=sys.stderr)

    if args.regions_csv:
        with open(args.regions_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["region_index","polygon_xy"])
            w.writeheader()
            for i, poly in enumerate(regions):
                poly_s = ";".join(f"{x:.8f} {y:.8f}" for (x,y) in poly)
                w.writerow({"region_index":i, "polygon_xy": poly_s})
        print(f"[info] wrote regions CSV: {args.regions_csv}", file=sys.stderr)

    if args.centers_csv:
        with open(args.centers_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["x","y","r","g","b","token","stack_index"])
            w.writeheader()
            for (x,y,(r,g,b),token,sidx) in centers:
                w.writerow({"x":x,"y":y,"r":r,"g":g,"b":b,"token":token,"stack_index":sidx})
        print(f"[info] wrote centers CSV: {args.centers_csv}", file=sys.stderr)

    if args.export_3mf or args.export_stl_dir:
        z0 = float(args.z0_mm)
        z1 = z0 + thickness

        named_meshes: List[Tuple[str, tm.Trimesh]] = []
        skipped = 0

        if args.export_stl_dir:
            os.makedirs(args.export_stl_dir, exist_ok=True)

        seen_counts_export: Dict[str, int] = {}
        for i, poly in enumerate(regions):
            if len(poly) < 3:
                skipped += 1
                if args.debug_verbose:
                    print(f"[warn] region {i} degenerate (<3 verts); skipping", file=sys.stderr)
                continue

            token = centers[i][3] if i < len(centers) else f"region_{i}"
            cnt = seen_counts_export.get(token, 0)
            seen_counts_export[token] = cnt + 1
            label = token if cnt == 0 else f"{token}_{cnt+1}"
            safe_label = _sanitize_label(label)

            poly_mm = [(sx*x, sy*y) for (x,y) in poly]
            V, F = triangulate_prism_solid(poly_mm, z0, z1)
            if not V or not F:
                skipped += 1
                if args.debug_verbose:
                    print(f"[warn] region {i} triangulation failed; skipping", file=sys.stderr)
                continue

            mesh = tm.Trimesh(vertices=V, faces=F, process=False)
            try:
                if len(mesh.faces) > 0:
                    mesh.update_faces(mesh.unique_faces())
                mesh.remove_unreferenced_vertices()
                mesh.fix_normals()
            except Exception:
                pass

            if not getattr(mesh, "is_watertight", False):
                skipped += 1
                if args.debug_verbose:
                    print(f"[warn] region {i} not watertight; skipping", file=sys.stderr)
                continue
            if not getattr(mesh, "is_volume", False) or mesh.volume < 1e-9:
                skipped += 1
                if args.debug_verbose:
                    print(f"[warn] region {i} watertight but zero/invalid volume; skipping", file=sys.stderr)
                continue

            if args.stand_up:
                center = mesh.bounds.mean(axis=0)
                mesh.apply_translation(-center)
                rot_matrix = tm.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0])
                mesh.apply_transform(rot_matrix)
                z_min = mesh.bounds[0][2]
                mesh.apply_translation([0, 0, -z_min])

            if args.export_3mf:
                named_meshes.append((safe_label, mesh.copy()))

            if args.export_stl_dir:
                out_path = os.path.join(args.export_stl_dir, f"{safe_label}.{args.stl_format.lower()}")
                out_path = _ensure_unique_filename(out_path)
                try:
                    mesh.export(out_path)
                    if args.debug_verbose:
                        print(f"[info] wrote {args.stl_format.upper()}: {out_path}", file=sys.stderr)
                except Exception as e:
                    print(f"[warn] STL export failed for '{label}': {e}", file=sys.stderr)

        if args.export_3mf:
            if not named_meshes:
                print("[warn] no valid meshes to export; 3MF not written", file=sys.stderr)
            else:
                title = os.path.splitext(os.path.basename(args.export_3mf))[0]
                try:
                    write_3mf_as_assembly(named_meshes, args.export_3mf, title)
                    if args.debug_verbose:
                        print(f"[info] 3MF export: added {len(named_meshes)} meshes; skipped {skipped}", file=sys.stderr)
                    else:
                        print(f"[info] wrote 3MF: {args.export_3mf}", file=sys.stderr)
                except Exception as e:
                    print(f"[error] 3MF export failed: {e}", file=sys.stderr)
                    import traceback
                    traceback.print_exc()

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="layer_perm_3mf_volumes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent("""
        Export CMY regions as *volumetric* 3MF parts and/or a CSV mapping of parts → CMY tokens.
        Default behavior: export each valid region as an STL into ./stl_out (one file per part).

        New: --shape {triangle,circle} to choose the geometry of the partitioning.
        """)
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pd = sub.add_parser("diag", help="Diagnostics + CSV/3MF export (volumes).")
    pd.add_argument("--shape", choices=["triangle","circle"], default="triangle",
                    help="Region geometry: equilateral triangle (ternary) or circle. Default: %(default)s")

    pd.add_argument("--palette", required=True, help="Comma-separated colors (names or hex).")
    pd.add_argument("--height", type=int, required=True, help="Number of layers for stacks.")
    pd.add_argument("--include-lower-heights", action="store_true", help="Also include heights 1..height.")
    pd.add_argument("--wrap-cyclic", action="store_true", help="Treat first and last layers as adjacent.")
    pd.add_argument("--unique-up-to-rotation", action="store_true", help="When cyclic, dedupe stacks that are rotations of each other.")
    pd.add_argument("--limit", type=int, help="Limit number of stacks considered.")

    # Flexible per-stack color usage (defaults to EXACT 3 for back-compat)
    pd.add_argument("--colors-per-stack", type=int,
                    help="(Back-compat) EXACTly this many distinct colors per stack. Any int ≥1.")
    pd.add_argument("--colors-per-stack-exact", type=int,
                    help="EXACTly this many distinct colors per stack. Any int ≥1.")
    pd.add_argument("--min-colors-per-stack", type=int,
                    help="Minimum number of distinct colors allowed in a stack.")
    pd.add_argument("--max-colors-per-stack", type=int,
                    help="Maximum number of distinct colors allowed in a stack.")
    pd.add_argument("--allowed-combos",
                    help='Only allow these exact color sets per stack, e.g. "cyan+magenta,magenta+yellow".')
    pd.add_argument("--disallowed-combos",
                    help='Ban these exact color sets per stack, e.g. "cyan+yellow".')

    # constraints
    pd.add_argument("--max-run-same", type=int, default=1)
    pd.add_argument("--allow-adjacent-same", action="store_true")
    pd.add_argument("--min-distance-same", type=int, default=1)
    pd.add_argument("--required-counts", help='Exact counts: "A=2,B=2,C=1".')
    pd.add_argument("--min-counts", help='Minimum counts per color: "A=1,B=2"')
    pd.add_argument("--max-counts", help='Maximum counts per color: "A=3,B=5"')
    pd.add_argument("--banned-above", help='Immediate bans like "A>B;B>C".')
    pd.add_argument("--banned-below", help='Immediate bans like "A<B;C<D".')

    # layout config (shared)
    pd.add_argument("--grid-divisions", type=int, default=10, help="Grid subdivisions / ring count.")
    pd.add_argument("--grid-cell-size", type=float, default=0.065, help="Offset spacing inside each cell.")
    pd.add_argument("--grid-pack", type=int, default=4, help="Max representatives per cell.")
    pd.add_argument("--ternary-poles", help='Which palette entries map to CMY poles (still used for colors).')
    pd.add_argument("--region-centers", choices=["grid","offset"], default="grid",
                    help="Voronoi sites: 'grid' (one per cell at center) or 'offset' (packed sites). Default: %(default)s")

    # naming
    pd.add_argument("--short-names", help='Mapping like "cyan=c,magenta=m,yellow=y".')

    # outputs (CSV/PNG)
    pd.add_argument("--debug-png", help="Save a debug PNG of regions + centers.")
    pd.add_argument("--centers-csv", help="Write chosen centers/colors/tokens.")
    pd.add_argument("--regions-csv", help="Write region polygons.")
    pd.add_argument("--parts-csv", help="Write mapping of region → label/token/CMY fractions/area/volume.")
    pd.add_argument("--debug-verbose", action="store_true")

    # 3MF export (optional)
    pd.add_argument("--export-3mf", help="Path to output 3MF file (assembly).")
    pd.add_argument("--export-stl-dir", help="Directory to export per-region STL files.")
    pd.add_argument("--stl-format", choices=["stl","STL"], default="stl", help="STL file format (default: stl)")
    pd.add_argument("--z0-mm", type=float, default=0.0, help="Bottom Z in millimeters.")
    pd.add_argument("--thickness", type=float, default=0.8, help="Extrusion thickness (height) in millimeters.")
    pd.add_argument("--tri-width-mm", type=float, default=120.0,
                    help="Width of the full geometry (triangle side or circle diameter) in millimeters.")
    pd.add_argument("--stand-up", action="store_true", help="Reorient meshes upright before export.")

    pd.set_defaults(func=cmd_diag)
    return p


def main(argv: Optional[Sequence[str]] = None) -> None:
    p = build_parser()
    args = p.parse_args(argv)
    if not hasattr(args, "func"):
        p.print_help(sys.stderr)
        sys.exit(2)
    args.func(args)


if __name__ == "__main__":
    main()
