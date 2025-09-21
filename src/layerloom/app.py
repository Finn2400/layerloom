#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
layerloom – group Z-bands from a 3MF into K repeating groups (default K=2: EVEN/ODD),
preserving world placement. Writes a new 3MF where each grouped mesh is a distinct object.

Key patches in this version:
 - Proper <model> root with namespaces and xml:lang.
 - Each <object> now includes name="..." so slicers display the part names.
 - Still writes vendor-friendly metadata as a fallback.
 - Removed parent-side post-bake verification (avoid duplicate/noisy logs).

CLI (installed as `layerloom`):
  layerloom -i INPUT.3mf -o OUTPUT.3mf -H 0.20 --z0-mode auto-global --groups 2 --include-scene -v
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys
import tempfile
import zipfile
import subprocess
from typing import List, Tuple, Optional, Dict, Any

import numpy as np
import trimesh

# Try lxml first for better namespace handling; fall back to stdlib
try:
    from lxml import etree as ET
    HAVE_LXML = True
except Exception:
    import xml.etree.ElementTree as ET
    HAVE_LXML = False

# ------------------------- Namespaces & helpers -------------------------

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
BAMBU_NS = "http://schemas.bambulab.com/package/2021"
XML_NS = "http://www.w3.org/XML/1998/namespace"

if not HAVE_LXML:
    # stdlib ET needs explicit registration to write xmlns prefixes
    ET.register_namespace("", CORE_NS)
    ET.register_namespace("BambuStudio", BAMBU_NS)
    ET.register_namespace("slic3r_pe", "http://slic3r.org/ns/pe")

def q(tag: str) -> str:
    """Qualified tag in default 3MF core namespace."""
    return f"{{{CORE_NS}}}{tag}"

# ------------------------- Geometry utils -------------------------

def slab_bounds_for_band(mesh: trimesh.Trimesh, k: int, z0: float, H: float, z_gap: float, xy_pad: float):
    (xmin, ymin, _), (xmax, ymax, _) = mesh.bounds
    L = z0 + k*H
    U = z0 + (k+1)*H
    L_eff = L + z_gap/2.0
    U_eff = U - z_gap/2.0
    if U_eff <= L_eff:
        U_eff = L_eff + 0.05*H
    return xmin-xy_pad, xmax+xy_pad, ymin-xy_pad, ymax+xy_pad, L_eff, U_eff

def build_slab_box(slab):
    xmin,xmax,ymin,ymax,L_eff,U_eff = slab
    cx,cy,cz = 0.5*(xmin+xmax), 0.5*(ymin+ymax), 0.5*(L_eff+U_eff)
    ex,ey,ez = (xmax-xmin), (ymax-ymin), (U_eff-L_eff)
    box = trimesh.creation.box(extents=(ex,ey,ez))
    box.apply_translation([cx,cy,cz])
    return box

def clean_and_filter(m: Optional[trimesh.Trimesh], min_tris=20, min_area=1e-5):
    if m is None or m.faces.size==0:
        return None
    try: m.update_faces(m.unique_faces())
    except Exception: pass
    try: m.update_faces(m.nondegenerate_faces(height=1e-12))
    except Exception: pass
    m.remove_unreferenced_vertices()
    comps = m.split(only_watertight=False)
    kept = [c for c in comps if (len(c.faces)>=min_tris and c.area>=min_area)]
    if not kept: return None
    return kept[0] if len(kept)==1 else trimesh.util.concatenate(kept)

def concat_meshes(meshes: List[trimesh.Trimesh]) -> Optional[trimesh.Trimesh]:
    meshes = [m for m in meshes if isinstance(m,trimesh.Trimesh) and m.faces.size>0]
    if not meshes:
        return None
    if len(meshes) == 1:
        return trimesh.Trimesh(vertices=meshes[0].vertices.view(np.ndarray),
                               faces=meshes[0].faces.view(np.ndarray),
                               process=False)
    return trimesh.util.concatenate(meshes)

# ------------------------- 3MF pack/unpack helpers -------------------------

def new_model_root(title: str) -> ET.Element:
    """
    Create <model> with default namespace, vendor prefixes, and xml:lang.
    (Patched) Uses QName for xml:lang when lxml is available.
    """
    if HAVE_LXML:
        nsmap = {
            None: CORE_NS,
            "BambuStudio": BAMBU_NS,
            "slic3r_pe": "http://slic3r.org/ns/pe",
        }
        root = ET.Element(q("model"), nsmap=nsmap)
        root.set("unit", "millimeter")
        # xml:lang attribute
        root.set(ET.QName(XML_NS, "lang"), "en-US")
    else:
        # stdlib: namespaces registered above; xml:lang ok as a literal attribute
        root = ET.Element(q("model"), {"unit": "millimeter", "xml:lang": "en-US"})
    ET.SubElement(root, q("metadata"), {"name": "Title"}).text = title
    return root

def stamp_object_labels(obj_el: ET.Element, label: str) -> None:
    """
    (Patched) Primary display name via object attribute 'name'.
    Also add metadata fallbacks some slicers might read.
    """
    obj_el.set("name", label)
    ET.SubElement(obj_el, q("metadata"), {"name": "Title"}).text = label
    ET.SubElement(obj_el, q("metadata"), {"name": "Name"}).text = label
    ET.SubElement(obj_el, q("metadata"), {"name": "slic3r_pe:Name"}).text = label
    ET.SubElement(obj_el, q("metadata"), {"name": "BambuStudio:ModelName"}).text = label

def mesh_to_object(obj_id: int, mesh: trimesh.Trimesh, label: str) -> ET.Element:
    obj = ET.Element(q("object"), {"id": str(obj_id), "type":"model"})
    stamp_object_labels(obj, label)
    mesh_el = ET.SubElement(obj, q("mesh"))
    verts_el = ET.SubElement(mesh_el, q("vertices"))
    # write vertices
    V = mesh.vertices.view(np.ndarray)
    for v in V:
        e = ET.SubElement(verts_el, q("vertex"))
        e.set("x", f"{v[0]:.9f}")
        e.set("y", f"{v[1]:.9f}")
        e.set("z", f"{v[2]:.9f}")
    # write triangles
    tris_el = ET.SubElement(mesh_el, q("triangles"))
    F = mesh.faces.view(np.ndarray)
    for f in F:
        t = ET.SubElement(tris_el, q("triangle"))
        t.set("v1", str(int(f[0])))
        t.set("v2", str(int(f[1])))
        t.set("v3", str(int(f[2])))
    return obj

def pack_3mf(objects: List[Tuple[str,trimesh.Trimesh]],
             scene_objects: Optional[List[Tuple[str,trimesh.Trimesh]]],
             out_path: str,
             title: str) -> None:
    """
    Create a valid 3MF where each (label, mesh) becomes an object and build item.
    scene_objects, if provided, are appended after per-part objects.
    """
    root = new_model_root(title)
    resources = ET.SubElement(root, q("resources"))
    build = ET.SubElement(root, q("build"))

    next_id = 1
    # per-part objects
    for label, mesh in objects:
        obj_el = mesh_to_object(next_id, mesh, label)
        resources.append(obj_el)
        ET.SubElement(build, q("item"), {"objectid": str(next_id), "printable":"1", "partnumber": label})
        next_id += 1

    # optional scene-level objects
    if scene_objects:
        for label, mesh in scene_objects:
            obj_el = mesh_to_object(next_id, mesh, label)
            resources.append(obj_el)
            ET.SubElement(build, q("item"), {"objectid": str(next_id), "printable":"1", "partnumber": label})
            next_id += 1

    # Serialize XML
    if HAVE_LXML:
        xml_bytes = ET.tostring(root, pretty_print=False, xml_declaration=True, encoding="utf-8")
    else:
        xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    # Write 3MF (zip)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _content_types_xml())
        z.writestr("_rels/.rels", _rels_root_xml())
        z.writestr("3D/3dmodel.model", xml_bytes)
        z.writestr("3D/_rels/3dmodel.model.rels", _rels_empty_xml())

def _content_types_xml() -> bytes:
    # minimal content types for 3MF
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
    return ("""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>""").encode("utf-8")

# ------------------------- Baking orchestration -------------------------

def should_bake_with_module() -> bool:
    try:
        import layerloom.needs_bake as nb  # noqa: F401
        return True
    except Exception:
        return False

def needs_bake(path: str) -> bool:
    try:
        from layerloom.needs_bake import detect
        ok, _ = detect(path)
        return ok
    except Exception:
        # if detector missing, assume needs bake for safety
        return True

def orchestrate_bake(input_3mf: str, verbose: bool) -> str:
    """
    Bake via installed module: python -m layerloom.bake_in_memory
    Returns path to baked 3MF (temp file).
    """
    tmpdir = tempfile.mkdtemp(prefix="layerloom_")
    baked_path = os.path.join(tmpdir, "baked.3mf")
    if verbose:
        print("[orchestrate] baking via module layerloom.bake_in_memory")
    cmd = [sys.executable, "-m", "layerloom.bake_in_memory", "-i", input_3mf, "-o", baked_path]
    subprocess.run(cmd, check=True)
    return baked_path

# ------------------------- Load baked parts as meshes -------------------------

def load_baked_parts(path: str, verbose: bool=False) -> List[Tuple[str, trimesh.Trimesh]]:
    """
    Load a baked 3MF (no transforms/components) into per-object meshes and names.
    """
    parts: List[Tuple[str, trimesh.Trimesh]] = []

    # Parse the model XML to get object names and then load geometry via trimesh
    with zipfile.ZipFile(path, "r") as z:
        with z.open("3D/3dmodel.model") as f:
            data = f.read()
    # parse xml
    if HAVE_LXML:
        root = ET.fromstring(data)
    else:
        root = ET.fromstring(data)

    # map: object id -> name
    obj_names: Dict[str,str] = {}
    for obj in root.findall(f".//{q('object')}"):
        oid = obj.get("id")
        name = obj.get("name") or ""
        if not name:
            # fallback to Title metadata if needed
            for md in obj.findall(f"{q('metadata')}"):
                if md.get("name") in ("Title", "Name", "BambuStudio:ModelName", "slic3r_pe:Name"):
                    if md.text:
                        name = md.text
                        break
        if not name:
            name = f"part_{int(oid):03d}"
        obj_names[oid] = name

    # Load geometry via trimesh scene then extract per-object meshes
    scene = trimesh.load(path, force='scene')
    if not isinstance(scene, trimesh.Scene):
        mesh = scene if isinstance(scene, trimesh.Trimesh) else None
        if mesh is None:
            raise RuntimeError("Failed to load baked 3MF geometry.")
        parts.append((obj_names.get("1","part_001"), mesh))
        return parts

    idx = 1
    for g in scene.geometry.values():
        if not isinstance(g, trimesh.Trimesh) or g.faces.size == 0:
            continue
        name = obj_names.get(str(idx), f"part_{idx:03d}")
        parts.append((name, g.copy()))
        idx += 1

    if verbose:
        print(f"[info] parts={len(parts)}  (baked)")
        for i,(n,m) in enumerate(parts, start=1):
            (xmin,ymin,zmin),(xmax,ymax,zmax) = m.bounds
            print(f"[part] {i:02d} {n:24s} tris={m.faces.size:7d}  "
                  f"X[{xmin:.3f},{xmax:.3f}] Y[{ymin:.3f},{ymax:.3f}] Z[{zmin:.3f},{zmax:.3f}]")

    return parts

# ------------------------- Banding & grouping -------------------------

def choose_z0(parts: List[Tuple[str,trimesh.Trimesh]], mode: str, z0_fixed: Optional[float]) -> Tuple[float, List[float]]:
    zmins = [float(m.bounds[0,2]) for _, m in parts]
    if mode == "auto-global":
        z0g = float(np.min(zmins)); return z0g, [z0g]*len(parts)
    if mode == "auto-local":
        z0g = float(np.min(zmins)); return z0g, [float(z) for z in zmins]
    if mode == "fixed":
        if z0_fixed is None:
            raise ValueError("z0-mode=fixed requires --z0-mm.")
        return float(z0_fixed), [float(z0_fixed)]*len(parts)
    raise ValueError(f"Unknown z0-mode: {mode}")

def route_bands(parts: List[Tuple[str,trimesh.Trimesh]], H: float, z0_mode: str, z0_fixed: Optional[float],
                groups: int, z_gap: float, xy_pad: float, verbose: bool=False) -> Tuple[List[Tuple[str, List[List[trimesh.Trimesh]]]], Dict[int, List[trimesh.Trimesh]]]:
    """
    For each part, boolean-slice into Z bands and assign band k -> group (k mod K).
    Returns:
      per_part_groups: [ (name, [group0_meshes, group1_meshes, ...]) ]
      scene_groups: {g: [meshes...] }
    """
    z0_global, per_part_z0 = choose_z0(parts, z0_mode, z0_fixed)
    if verbose:
        print(f"[info] parts={len(parts)}  z0-mode={z0_mode}  z0-global={z0_global:.6f}  default-K={groups}")

    per_part_groups: List[Tuple[str, List[List[trimesh.Trimesh]]]] = []
    scene_groups: Dict[int, List[trimesh.Trimesh]] = {g: [] for g in range(groups)}

    for pidx, (name, mesh) in enumerate(parts, start=1):
        z0_eff = per_part_z0[pidx-1]
        (xmin, ymin, zmin), (xmax, ymax, zmax) = mesh.bounds
        k_min = math.floor((zmin - z0_eff) / H)
        k_max = math.ceil((zmax - z0_eff) / H) - 1

        if verbose:
            print(f"[bands] {name:20s} K={groups}  k[{k_min}..{k_max}]  Z[{zmin:.3f}..{zmax:.3f}]")

        group_mesh_lists: List[List[trimesh.Trimesh]] = [[] for _ in range(groups)]

        for k in range(k_min, k_max+1):
            slab = slab_bounds_for_band(mesh, k, z0_eff, H, z_gap, xy_pad)
            slab_box = build_slab_box(slab)
            try:
                inter = trimesh.boolean.intersection([mesh, slab_box], engine=None)
            except Exception:
                inter = None
            if inter is None or (isinstance(inter, trimesh.Trimesh) and inter.faces.size == 0) or (isinstance(inter, list) and not inter):
                continue
            if isinstance(inter, list):
                band = concat_meshes(inter)
            else:
                band = inter
            band = clean_and_filter(band)
            if band is None or band.faces.size == 0:
                continue
            g = (k % groups + groups) % groups
            group_mesh_lists[g].append(band)

        # Concatenate per-group for scene aggregation
        for g in range(groups):
            gm = concat_meshes(group_mesh_lists[g])
            if gm is not None:
                scene_groups[g].append(gm)

        per_part_groups.append((name, group_mesh_lists))

    return per_part_groups, scene_groups

# ------------------------- Writer (grouped 3MF) -------------------------

def write_grouped(parts: List[Tuple[str,trimesh.Trimesh]], output_3mf: str,
                  H: float, z0_mode: str, z0_fixed: Optional[float], groups: int,
                  include_scene: bool, title: str, verbose: bool=False) -> None:
    per_part_groups, scene_groups = route_bands(parts, H, z0_mode, z0_fixed, groups,
                                                z_gap=0.01, xy_pad=1.0, verbose=verbose)

    out_objects: List[Tuple[str, trimesh.Trimesh]] = []
    for name, gm_lists in per_part_groups:
        if groups == 2:
            # EVEN = group 0, ODD = group 1
            labels = ["EVEN", "ODD"]
            for g in (0,1):
                m = concat_meshes(gm_lists[g])
                if m is not None and m.faces.size > 0:
                    out_objects.append((f"{name}_"+labels[g], m))
        else:
            for g in range(groups):
                m = concat_meshes(gm_lists[g])
                if m is not None and m.faces.size > 0:
                    out_objects.append((f"{name}_{g+1}", m))

    scene_objects: Optional[List[Tuple[str,trimesh.Trimesh]]] = None
    if include_scene:
        scene_objects = []
        if groups == 2:
            labels = ["SCENE_K2_EVEN", "SCENE_K2_ODD"]
            for g in (0,1):
                m = concat_meshes(scene_groups[g])
                if m is not None and m.faces.size > 0:
                    scene_objects.append((labels[g], m))
        else:
            for g in range(groups):
                m = concat_meshes(scene_groups[g])
                if m is not None and m.faces.size > 0:
                    scene_objects.append((f"SCENE_K{groups}_{g+1}", m))

    pack_3mf(out_objects, scene_objects if include_scene else None, output_3mf, title)

    if verbose:
        total_tris = sum(m.faces.size for _,m in out_objects) + (sum(m.faces.size for _,m in (scene_objects or [])))
        print(f"[done] wrote {os.path.basename(output_3mf)} with {len(out_objects)+(len(scene_objects or []))} object(s), total tris={total_tris}")

# ------------------------- Main flow -------------------------

def run_layerloom(input_3mf: str, output_3mf: str, layer_height_mm: float,
                  z0_mode: str, z0_mm: Optional[float], groups: int,
                  include_scene: bool, verbose: bool=False) -> int:
    if layer_height_mm <= 0:
        raise ValueError("Layer height must be > 0.")

    # 1) Ensure baked placement (no transforms/components)
    baked_path = input_3mf
    if should_bake_with_module() and needs_bake(input_3mf):
        if verbose:
            print("[detect] needs_bake=YES; reasons=build_transform,components")
        baked_path = orchestrate_bake(input_3mf, verbose)
        # intentionally no parent post-bake verification to avoid duplicate logs
        if verbose:
            print(f"[done] baked → {baked_path}")
    else:
        if verbose:
            print("[detect] needs_bake=NO")

    # 2) Load baked parts
    parts = load_baked_parts(baked_path, verbose=verbose)

    # 3) Group & write 3MF
    title = os.path.splitext(os.path.basename(output_3mf))[0]
    write_grouped(parts, output_3mf, layer_height_mm, z0_mode, z0_mm, groups, include_scene, title, verbose=verbose)
    return 0

def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="layerloom: split 3MF parts into Z-band groups and write a grouped 3MF")
    ap.add_argument("-i","--input", required=True, help="input 3MF")
    ap.add_argument("-o","--output", required=True, help="output 3MF")
    ap.add_argument("-H","--layer-height-mm", required=True, type=float)
    ap.add_argument("--z0-mode", choices=["auto-global","auto-local","fixed"], default="auto-global")
    ap.add_argument("--z0-mm", type=float, default=None)
    ap.add_argument("--groups", type=int, default=2, help="number of repeating groups K (default 2=EVEN/ODD)")
    ap.add_argument("--include-scene", action="store_true", help="also include scene-level grouped objects")
    ap.add_argument("-v","--verbose", action="store_true")
    return ap

def main(argv: Optional[List[str]] = None) -> int:
    ap = build_argparser()
    args = ap.parse_args(argv)
    return run_layerloom(
        input_3mf=args.input,
        output_3mf=args.output,
        layer_height_mm=args.layer_height_mm,
        z0_mode=args.z0_mode,
        z0_mm=args.z0_mm,
        groups=args.groups,
        include_scene=args.include_scene,
        verbose=args.verbose
    )

if __name__ == "__main__":
    sys.exit(main())

