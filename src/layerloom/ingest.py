#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# layerloom/ingest.py — baked 3MF loader with XML-based labeling
#
# Reads baked 3MF files into (label, trimesh.Trimesh) pairs.
# Parses the XML directly for robust labeling and order matching.
#

from __future__ import annotations
import os, glob, zipfile, re
import xml.etree.ElementTree as ET
from typing import List, Tuple, Optional, Iterable
import numpy as np
import trimesh
try:
    from layerloom.normalize_3mf_import import normalize_3mf_path
    from layerloom.transform_3mf import _parse_tf_3mf
    from layerloom.tokens import PAT_TAG_RE
except Exception:
    from normalize_3mf_import import normalize_3mf_path
    from transform_3mf import _parse_tf_3mf
    from tokens import PAT_TAG_RE

Part = Tuple[str, trimesh.Trimesh]
CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
NS = {"m": CORE_NS}
M = lambda tag: f"{{{CORE_NS}}}{tag}"
_MESH_EXTS = {".stl", ".obj", ".ply"}

# ---------------------------------------------------------------------
# LayerLoom PAT helpers (optional use)
# ---------------------------------------------------------------------
PAT_TOKEN_RE = PAT_TAG_RE

def strip_pat(name: str) -> str:
    """Remove __PAT_<token>__ from a label, preserving the rest."""
    return PAT_TOKEN_RE.sub("", name or "").strip()

def extract_pat_token(name: str) -> Optional[str]:
    """Return the LayerLoom token if present, else None."""
    m = PAT_TOKEN_RE.search(name or "")
    return m.group(1).lower() if m else None

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

def _find_model_xml(zf: zipfile.ZipFile) -> Optional[str]:
    for n in zf.namelist():
        if n.startswith("3D/") and n.lower().endswith(".model"):
            return n
    return None

def _object_label(obj: ET.Element, fallback: str) -> str:
    label = obj.get("name") or ""
    if not label:
        for md in obj.findall("m:metadata", NS):
            if md.get("name") in ("Title", "Name") and md.text:
                label = md.text
                break
    return label or fallback

def _mesh_from_object(obj: ET.Element) -> Optional[trimesh.Trimesh]:
    mesh_el = obj.find("m:mesh", NS)
    if mesh_el is None:
        return None
    verts_el = mesh_el.find("m:vertices", NS)
    tris_el = mesh_el.find("m:triangles", NS)
    if verts_el is None or tris_el is None:
        return None

    vertices = np.array(
        [
            (float(v.get("x", 0)), float(v.get("y", 0)), float(v.get("z", 0)))
            for v in verts_el.findall("m:vertex", NS)
        ],
        dtype=float,
    )
    faces = np.array(
        [
            (int(t.get("v1", 0)), int(t.get("v2", 0)), int(t.get("v3", 0)))
            for t in tris_el.findall("m:triangle", NS)
        ],
        dtype=int,
    )
    if vertices.size == 0 or faces.size == 0:
        return None
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)

def _mesh_with_transform(mesh: trimesh.Trimesh, transform: np.ndarray) -> trimesh.Trimesh:
    vertices = np.asarray(mesh.vertices, dtype=float)
    ones = np.ones((vertices.shape[0], 1), dtype=float)
    transformed = (np.asarray(transform, dtype=float) @ np.hstack([vertices, ones]).T).T[:, :3]
    return trimesh.Trimesh(vertices=transformed, faces=np.asarray(mesh.faces, dtype=int), process=False)

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
    """Extract build-reachable meshes from 3MF XML, honoring build transforms."""
    with zipfile.ZipFile(path, "r") as zf:
        model_xml = _find_model_xml(zf)
        if not model_xml:
            raise ValueError("3D/*.model not found in 3MF")
        root = ET.fromstring(zf.read(model_xml))

    parts: List[Part] = []
    resources = root.find("m:resources", NS)
    build = root.find("m:build", NS)

    if resources is not None and build is not None:
        objects = {obj.get("id", ""): obj for obj in resources.findall("m:object", NS)}
        mesh_cache: dict[str, trimesh.Trimesh] = {}
        for item_index, item in enumerate(build.findall("m:item", NS), start=1):
            oid = item.get("objectid") or ""
            obj = objects.get(oid)
            if obj is None:
                continue
            mesh = mesh_cache.get(oid)
            if mesh is None:
                mesh = _mesh_from_object(obj)
                if mesh is None:
                    continue
                mesh_cache[oid] = mesh
            label = item.get("partnumber") or _object_label(obj, f"part_{item_index:03d}")
            transform = _parse_tf_3mf(item.get("transform"))
            parts.append((label, _mesh_with_transform(mesh, transform)))

        if parts:
            print(f"[debug] extracted {len(parts)} build-transformed part(s) from XML")
            return parts

    for obj in root.findall(".//m:object", NS):
        label = _object_label(obj, f"part_{len(parts) + 1:03d}")
        mesh = _mesh_from_object(obj)
        if mesh is not None:
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
        normalized = normalize_3mf_path(p)
        return _extract_meshes_from_3mf(normalized)

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
