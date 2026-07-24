#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
layerloom – group Z-bands from a 3MF into K repeating groups (default K=2: EVEN/ODD),
preserving world placement. Writes a new 3MF where each grouped mesh is a distinct object.

Key patches in this version:
 - Proper <model> root with namespaces and xml:lang.
 - Each <object> now includes name="..." so slicers display the part names.
 - Still writes vendor-friendly metadata as a fallback.
 - Supports --k-map N=K,... to override group count for specific part indices.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import tempfile
import zipfile
import subprocess
from typing import List, Tuple, Optional, Dict

import numpy as np
import trimesh

# Try lxml first for better namespace handling; fall back to stdlib
try:
    from lxml import etree as ET
    HAVE_LXML = True
except Exception:
    import xml.etree.ElementTree as ET
    HAVE_LXML = False

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
BAMBU_NS = "http://schemas.bambulab.com/package/2021"
XML_NS = "http://www.w3.org/XML/1998/namespace"

if not HAVE_LXML:
    ET.register_namespace("", CORE_NS)
    ET.register_namespace("BambuStudio", BAMBU_NS)
    ET.register_namespace("slic3r_pe", "http://slic3r.org/ns/pe")

def q(tag: str) -> str:
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

# ------------------------- 3MF helpers -------------------------

def new_model_root(title: str) -> ET.Element:
    if HAVE_LXML:
        nsmap = {
            None: CORE_NS,
            "BambuStudio": BAMBU_NS,
            "slic3r_pe": "http://slic3r.org/ns/pe",
        }
        root = ET.Element(q("model"), nsmap=nsmap)
        root.set("unit", "millimeter")
        root.set(ET.QName(XML_NS, "lang"), "en-US")
    else:
        root = ET.Element(q("model"), {"unit": "millimeter", "xml:lang": "en-US"})
    ET.SubElement(root, q("metadata"), {"name": "Title"}).text = title
    return root

def stamp_object_labels(obj_el: ET.Element, label: str) -> None:
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
    for v in mesh.vertices.view(np.ndarray):
        e = ET.SubElement(verts_el, q("vertex"))
        e.set("x", f"{v[0]:.9f}")
        e.set("y", f"{v[1]:.9f}")
        e.set("z", f"{v[2]:.9f}")
    tris_el = ET.SubElement(mesh_el, q("triangles"))
    for f in mesh.faces.view(np.ndarray):
        t = ET.SubElement(tris_el, q("triangle"))
        t.set("v1", str(int(f[0])))
        t.set("v2", str(int(f[1])))
        t.set("v3", str(int(f[2])))
    return obj

def pack_3mf(objects: List[Tuple[str,trimesh.Trimesh]],
             scene_objects: Optional[List[Tuple[str,trimesh.Trimesh]]],
             out_path: str,
             title: str) -> None:
    root = new_model_root(title)
    resources = ET.SubElement(root, q("resources"))
    build = ET.SubElement(root, q("build"))
    next_id = 1
    for label, mesh in objects:
        obj_el = mesh_to_object(next_id, mesh, label)
        resources.append(obj_el)
        ET.SubElement(build, q("item"), {"objectid": str(next_id), "printable":"1", "partnumber": label})
        next_id += 1
    if scene_objects:
        for label, mesh in scene_objects:
            obj_el = mesh_to_object(next_id, mesh, label)
            resources.append(obj_el)
            ET.SubElement(build, q("item"), {"objectid": str(next_id), "printable":"1", "partnumber": label})
            next_id += 1
    xml_bytes = ET.tostring(root, pretty_print=False, xml_declaration=True, encoding="utf-8") if HAVE_LXML else ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _content_types_xml())
        z.writestr("_rels/.rels", _rels_root_xml())
        z.writestr("3D/3dmodel.model", xml_bytes)
        z.writestr("3D/_rels/3dmodel.model.rels", _rels_empty_xml())

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
    return ("""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>""").encode("utf-8")

# ------------------------- Baking -------------------------

def should_bake_with_module() -> bool:
    try:
        import layerloom.needs_bake as nb  # noqa
        return True
    except Exception:
        return False

def needs_bake(path: str) -> bool:
    try:
        from layerloom.needs_bake import detect
        ok, _ = detect(path)
        return ok
    except Exception:
        return True

def orchestrate_bake(input_3mf: str, verbose: bool) -> str:
    tmpdir = tempfile.mkdtemp(prefix="layerloom_")
    baked_path = os.path.join(tmpdir, "baked.3mf")
    if verbose:
        print("[orchestrate] baking via layerloom.bake_in_memory")
    cmd = [sys.executable, "-m", "layerloom.bake_in_memory", "-i", input_3mf, "-o", baked_path]
    subprocess.run(cmd, check=True)
    return baked_path

# ------------------------- Load baked parts -------------------------

def load_baked_parts(path: str, verbose: bool=False) -> List[Tuple[str, trimesh.Trimesh]]:
    parts: List[Tuple[str, trimesh.Trimesh]] = []
    with zipfile.ZipFile(path, "r") as z:
        with z.open("3D/3dmodel.model") as f:
            data = f.read()
    root = ET.fromstring(data)
    obj_names: Dict[str,str] = {}
    for obj in root.findall(f".//{q('object')}"):
        oid = obj.get("id")
        name = obj.get("name") or ""
        if not name:
            for md in obj.findall(f"{q('metadata')}"):
                if md.get("name") in ("Title","Name","BambuStudio:ModelName","slic3r_pe:Name") and md.text:
                    name = md.text; break
        if not name:
            name = f"part_{int(oid):03d}"
        obj_names[oid] = name
    scene = trimesh.load(path, force='scene')
    if not isinstance(scene, trimesh.Scene):
        mesh = scene if isinstance(scene,trimesh.Trimesh) else None
        if mesh is None:
            raise RuntimeError("Failed to load baked 3MF geometry.")
        parts.append((obj_names.get("1","part_001"), mesh))
        return parts
    idx = 1
    for g in scene.geometry.values():
        if not isinstance(g, trimesh.Trimesh) or g.faces.size==0:
            continue
        name = obj_names.get(str(idx), f"part_{idx:03d}")
        parts.append((name, g.copy()))
        idx += 1
    if verbose:
        print(f"[info] parts={len(parts)} (baked)")
        for i,(n,m) in enumerate(parts, start=1):
            (xmin,ymin,zmin),(xmax,ymax,zmax) = m.bounds
            print(f"[part] {i:02d} {n:20s} tris={m.faces.size:7d} Z[{zmin:.3f}..{zmax:.3f}]")
    return parts

# ------------------------- Banding -------------------------

def choose_z0(parts, mode, z0_fixed):
    zmins = [float(m.bounds[0,2]) for _, m in parts]
    if mode=="auto-global":
        z0g=float(np.min(zmins)); return z0g,[z0g]*len(parts)
    if mode=="auto-local":
        z0g=float(np.min(zmins)); return z0g,[float(z) for z in zmins]
    if mode=="fixed":
        if z0_fixed is None: raise ValueError("z0-mode=fixed requires --z0-mm")
        return float(z0_fixed),[float(z0_fixed)]*len(parts)
    raise ValueError(f"Unknown z0-mode: {mode}")

def route_bands(parts,H,z0_mode,z0_fixed,groups_default,k_map,z_gap,xy_pad,verbose=False):
    z0_global, per_part_z0 = choose_z0(parts,z0_mode,z0_fixed)
    if verbose:
        print(f"[info] parts={len(parts)} z0-mode={z0_mode} z0-global={z0_global:.6f}")
    per_part_groups, scene_groups=[],{}
    for pidx,(name,mesh) in enumerate(parts,start=1):
        groups=k_map.get(pidx,groups_default)
        if verbose and pidx in k_map:
            print(f"[override] part {pidx} → K={groups}")
        (xmin,ymin,zmin),(xmax,ymax,zmax)=mesh.bounds
        z0_eff=per_part_z0[pidx-1]
        k_min=math.floor((zmin-z0_eff)/H)
        k_max=math.ceil((zmax-z0_eff)/H)-1
        if verbose:
            print(f"[bands] {name:20s} K={groups} k[{k_min}..{k_max}]")
        gm_lists=[[] for _ in range(groups)]
        for k in range(k_min,k_max+1):
            slab=slab_bounds_for_band(mesh,k,z0_eff,H,z_gap,xy_pad)
            slab_box=build_slab_box(slab)
            try: inter=trimesh.boolean.intersection([mesh,slab_box],engine=None)
            except Exception: inter=None
            if inter is None: continue
            if isinstance(inter,list): band=concat_meshes(inter)
            else: band=inter
            band=clean_and_filter(band)
            if band is None: continue
            g=(k%groups+groups)%groups
            gm_lists[g].append(band)
        per_part_groups.append((name,gm_lists))
        if groups not in scene_groups: scene_groups[groups]={g:[] for g in range(groups)}
        for g in range(groups):
            gm=concat_meshes(gm_lists[g])
            if gm is not None: scene_groups[groups][g].append(gm)
    return per_part_groups, scene_groups

# ------------------------- Writer -------------------------

def write_grouped(parts,output_3mf,H,z0_mode,z0_fixed,groups_default,k_map,include_scene,title,verbose=False):
    per_part_groups,scene_groups=route_bands(parts,H,z0_mode,z0_fixed,groups_default,k_map,0.01,1.0,verbose)
    out_objs,scene_objs=[],[]
    for idx,(name,gm_lists) in enumerate(per_part_groups,start=1):
        groups=k_map.get(idx,groups_default)
        if groups==2:
            labels=["EVEN","ODD"]
            for g in (0,1):
                m=concat_meshes(gm_lists[g])
                if m is not None: out_objs.append((f"{name}_{labels[g]}",m))
        else:
            for g in range(groups):
                m=concat_meshes(gm_lists[g])
                if m is not None: out_objs.append((f"{name}_{g+1}",m))
    if include_scene:
        for groups,gdict in scene_groups.items():
            if groups==2:
                labels=["SCENE_K2_EVEN","SCENE_K2_ODD"]
                for g in (0,1):
                    m=concat_meshes(gdict[g]); 
                    if m is not None: scene_objs.append((labels[g],m))
            else:
                for g in range(groups):
                    m=concat_meshes(gdict[g])
                    if m is not None: scene_objs.append((f"SCENE_K{groups}_{g+1}",m))
    pack_3mf(out_objs,scene_objs if include_scene else None,output_3mf,title)
    if verbose:
        print(f"[done] wrote {os.path.basename(output_3mf)} with {len(out_objs)+(len(scene_objs) if include_scene else 0)} objects")

# ------------------------- Main -------------------------

def run_layerloom(input_3mf,output_3mf,layer_height_mm,z0_mode,z0_mm,groups_default,k_map,include_scene,verbose=False):
    if layer_height_mm<=0: raise ValueError("Layer height must be >0")
    baked_path=input_3mf
    if should_bake_with_module() and needs_bake(input_3mf):
        if verbose: print("[detect] needs_bake=YES")
        baked_path=orchestrate_bake(input_3mf,verbose)
        if verbose: print(f"[done] baked → {baked_path}")
    else:
        if verbose: print("[detect] needs_bake=NO")
    parts=load_baked_parts(baked_path,verbose)
    title=os.path.splitext(os.path.basename(output_3mf))[0]
    write_grouped(parts,output_3mf,layer_height_mm,z0_mode,z0_mm,groups_default,k_map,include_scene,title,verbose)
    return 0

def parse_kmap(arg: str) -> Dict[int,int]:
    out={}
    if not arg: return out
    for tok in arg.split(","):
        if "=" not in tok: continue
        try:
            p,k=tok.split("=")
            out[int(p)]=int(k)
        except ValueError:
            continue
    return out

def build_argparser():
    ap=argparse.ArgumentParser(description="layerloom: split 3MF parts into Z-band groups")
    ap.add_argument("-i","--input",required=True)
    ap.add_argument("-o","--output",required=True)
    ap.add_argument("-H","--layer-height-mm",required=True,type=float)
    ap.add_argument("--z0-mode",choices=["auto-global","auto-local","fixed"],default="auto-global")
    ap.add_argument("--z0-mm",type=float,default=None)
    ap.add_argument("--groups",type=int,default=2)
    ap.add_argument("--k-map",type=str,default="",help="per-part overrides, e.g. 7=3,8=3")
    ap.add_argument("--include-scene",action="store_true")
    ap.add_argument("-v","--verbose",action="store_true")
    return ap

def main(argv: Optional[List[str]]=None) -> int:
    ap=build_argparser()
    args=ap.parse_args(argv)
    k_map=parse_kmap(args.k_map)
    return run_layerloom(args.input,args.output,args.layer_height_mm,
                         args.z0_mode,args.z0_mm,args.groups,
                         k_map,args.include_scene,args.verbose)

if __name__=="__main__":
    sys.exit(main())

