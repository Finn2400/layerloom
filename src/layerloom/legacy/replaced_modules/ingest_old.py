#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# layerloom/ingest.py — baked 3MF loader with XML-based labeling
#
# Reads baked 3MF files into (label, trimesh.Trimesh) pairs.
# Parses the XML directly for robust labeling and order matching.
#

from __future__ import annotations
import os, glob, zipfile
import xml.etree.ElementTree as ET
from typing import List, Tuple, Optional, Iterable
import numpy as np
import trimesh

Part = Tuple[str, trimesh.Trimesh]
CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
NS = {"m": CORE_NS}
M = lambda tag: f"{{{CORE_NS}}}{tag}"
_MESH_EXTS = {".stl", ".obj", ".ply"}

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _is_mesh_path(path: str) -> bool:
    return os.path.splitext(path.lower())[1] in _MESH_EXTS

def _list_inputs(inputs: List[str]) -> List[str]:
    out: List[str] = []
    for p in inputs:
        if os.path.isdir(p):
            for ext in _MESH_EXTS | {".3mf"}:
                out.extend(sorted(glob.glob(os.path.join(p, f"*{ext}"))))
        else:
            out.append(p)
    return out

def _concat_meshes(meshes: Iterable[trimesh.Trimesh]) -> Optional[trimesh.Trimesh]:
    ms = [m for m in meshes if isinstance(m, trimesh.Trimesh) and m.faces.size > 0]
    if not ms:
        return None
    if len(ms) == 1:
        m0 = ms[0]
        return trimesh.Trimesh(vertices=m0.vertices.view(np.ndarray),
                               faces=m0.faces.view(np.ndarray),
                               process=False)
    return trimesh.util.concatenate(ms)

def _read_mesh_file(path: str) -> Optional[trimesh.Trimesh]:
    m = trimesh.load(path, force="mesh")
    if isinstance(m, trimesh.Scene):
        return _concat_meshes([g for g in m.geometry.values() if isinstance(g, trimesh.Trimesh)])
    if isinstance(m, trimesh.Trimesh):
        return m
    return None

def _find_model_xml(zf: zipfile.ZipFile) -> str | None:
    for n in zf.namelist():
        if n.startswith("3D/") and n.lower().endswith(".model"):
            return n
    return None

# ---------------------------------------------------------------------
# 3MF baked check
# ---------------------------------------------------------------------

def _3mf_needs_bake(path: str) -> bool:
    """Return True if file has transforms or components (unbaked)."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            name = _find_model_xml(zf)
            if not name:
                return True
            root = ET.fromstring(zf.read(name))
    except Exception:
        return True

    build = root.find("m:build", NS)
    res = root.find("m:resources", NS)
    if build is None or res is None:
        return True

    # Allow explicit identity transforms
    for it in build.findall("m:item", NS):
        tf = (it.get("transform") or "").strip()
        if tf and tf != "1 0 0 0 1 0 0 0 1 0 0 0":
            return True

    # Look for component assemblies
    for obj in res.findall("m:object", NS):
        comps = obj.find("m:components", NS)
        if comps is not None and list(comps):
            return True

    return False

# ---------------------------------------------------------------------
# XML-based 3MF extraction
# ---------------------------------------------------------------------

def _extract_meshes_from_3mf(path: str) -> List[Part]:
    """Extract meshes directly from baked 3MF XML."""
    with zipfile.ZipFile(path, "r") as zf:
        model_xml = _find_model_xml(zf)
        if not model_xml:
            raise ValueError("3D/*.model not found in 3MF")
        root = ET.fromstring(zf.read(model_xml))

    ns = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
    parts: List[Part] = []

    for obj in root.findall(f".//{ns}object"):
        label = obj.get("name") or ""
        if not label:
            for md in obj.findall(f"{ns}metadata"):
                if md.get("name") in ("Title", "Name") and md.text:
                    label = md.text
                    break
        if not label:
            label = f"part_{len(parts) + 1:03d}"

        mesh_el = obj.find(f"{ns}mesh")
        if mesh_el is None:
            continue
        verts_el = mesh_el.find(f"{ns}vertices")
        tris_el = mesh_el.find(f"{ns}triangles")
        if verts_el is None or tris_el is None:
            continue

        V = np.array([[float(v.get("x", 0)), float(v.get("y", 0)), float(v.get("z", 0))]
                      for v in verts_el.findall(f"{ns}vertex")], dtype=float)
        F = np.array([[int(t.get("v1", 0)), int(t.get("v2", 0)), int(t.get("v3", 0))]
                      for t in tris_el.findall(f"{ns}triangle")], dtype=int)

        if V.size == 0 or F.size == 0:
            continue

        mesh = trimesh.Trimesh(vertices=V, faces=F, process=False)
        parts.append((label, mesh))

    print(f"[debug] extracted {len(parts)} parts from XML")
    return parts

# ---------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------

def read_parts(inputs: List[str]) -> List[Part]:
    """
    Entry point for ingest stage:
      - Single baked .3mf → direct XML loader
      - Multiple mesh files (.stl/.obj/.ply) → standard trimesh load
    """
    paths = _list_inputs(inputs)
    if not paths:
        raise ValueError("no inputs provided")

    only3mf = [p for p in paths if p.lower().endswith(".3mf")]
    onlymeshes = [p for p in paths if _is_mesh_path(p)]

    if len(paths) == 1 and only3mf:
        p = only3mf[0]
        if _3mf_needs_bake(p):
            raise ValueError(f"3MF appears unbaked: {p}")
        return _extract_meshes_from_3mf(p)

    if not onlymeshes:
        raise ValueError("no mesh files found (expected .stl/.obj/.ply)")

    out: List[Part] = []
    for mp in onlymeshes:
        m = _read_mesh_file(mp)
        if m is None or m.faces.size == 0:
            continue
        name = os.path.splitext(os.path.basename(mp))[0]
        out.append((name, m))
    return out

