#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bake_in_memory_stlroundtrip.py (v8-prusaunitfix + name-preserve)
------------------------------------------------
Universal 3MF baker and flattener with STL roundtrip, Prusa metadata awareness,
and corrected unit handling (no double-scaling for Prusa/Bambu files).

Adds:
 • Preservation of original <object name="..."> tags from the input 3MF.
   These names are copied into the baked output so label continuity survives
   through weave → bake → slice → group.

Fix summary:
 • Translations from build-items, components, or Prusa matrices are **not rescaled**
   if the file’s unit is already "millimeter".
 • Vertex coordinates are still converted to mm if needed.
 • All earlier Prusa-volume filtering, growth limits, and STL roundtrips retained.
"""

import argparse, sys, os, tempfile, shutil, zipfile, math, struct
import xml.etree.ElementTree as ET
from typing import List, Tuple, Dict, Optional

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
XML_NS  = "http://www.w3.org/XML/1998/namespace"
PROD_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
NS = {"m": CORE_NS}
M  = lambda tag: f"{{{CORE_NS}}}{tag}"
IDENTITY_12 = [1,0,0, 0,1,0, 0,0,1, 0,0,0]

# -----------------------------------------------------------------------------
# Unit handling
# -----------------------------------------------------------------------------
def _unit_to_mm(unit: Optional[str]) -> float:
    if not unit: return 1.0
    u = unit.strip().lower()
    if u in ("millimeter","millimetre","mm"): return 1.0
    if u in ("micron","micrometer","micrometre","um"): return 0.001
    if u in ("meter","metre","m"): return 1000.0
    if u in ("inch","in"): return 25.4
    if u in ("foot","ft"): return 304.8
    return 1.0

def scale_tf_translation(tf12: List[float], s: float) -> List[float]:
    if s == 1.0: return tf12[:]
    out = tf12[:]
    out[9] *= s; out[10] *= s; out[11] *= s
    return out

# -----------------------------------------------------------------------------
# Matrix math
# -----------------------------------------------------------------------------
def parse_tf_3mf(s: Optional[str]) -> List[float]:
    if not s: return IDENTITY_12[:]
    vals = [float(v) for v in s.replace(",", " ").split()]
    return vals if len(vals)==12 else IDENTITY_12[:]

def to4x4(t12: List[float]) -> List[List[float]]:
    return [[t12[0],t12[3],t12[6],t12[9]],
            [t12[1],t12[4],t12[7],t12[10]],
            [t12[2],t12[5],t12[8],t12[11]],
            [0,0,0,1]]

def from4x4(M4: List[List[float]]) -> List[float]:
    return [M4[0][0],M4[1][0],M4[2][0],
            M4[0][1],M4[1][1],M4[2][1],
            M4[0][2],M4[1][2],M4[2][2],
            M4[0][3],M4[1][3],M4[2][3]]

def mul_tf(a12: List[float], b12: List[float]) -> List[float]:
    A,B=to4x4(a12),to4x4(b12)
    C=[[sum(A[i][k]*B[k][j] for k in range(4)) for j in range(4)] for i in range(4)]
    return from4x4(C)

def apply_tf(tf12, verts):
    M4=to4x4(tf12)
    return [(M4[0][0]*x+M4[0][1]*y+M4[0][2]*z+M4[0][3],
             M4[1][0]*x+M4[1][1]*y+M4[1][2]*z+M4[1][3],
             M4[2][0]*x+M4[2][1]*y+M4[2][2]*z+M4[2][3]) for x,y,z in verts]

def parse_prusa_matrix_to_12(s: str):
    vals=[float(v) for v in s.replace(","," ").split()]
    if len(vals)==16:
        m00,m01,m02,m03,m10,m11,m12,m13,m20,m21,m22,m23,*_=vals
        return [m00,m10,m20,m01,m11,m21,m02,m12,m22,m03,m13,m23]
    return vals if len(vals)==12 else IDENTITY_12[:]

# -----------------------------------------------------------------------------
# STL writing
# -----------------------------------------------------------------------------
def _normal(a,b,c):
    ux,uy,uz=b[0]-a[0],b[1]-a[1],b[2]-a[2]
    vx,vy,vz=c[0]-a[0],c[1]-a[1],c[2]-a[2]
    nx,ny,nz=(uy*vz-uz*vy, uz*vx-ux*vz, ux*vy-uy*vx)
    l=math.sqrt(nx*nx+ny*ny+nz*nz)
    return (nx/l,ny/l,nz/l) if l else (0,0,0)

def write_stl(path, verts, tris):
    with open(path,"wb") as f:
        f.write(b"Baked by bake_in_memory_stlroundtrip".ljust(80,b"\0"))
        f.write(struct.pack("<I",len(tris)))
        for i,j,k in tris:
            a,b,c=verts[i],verts[j],verts[k]
            nx,ny,nz=_normal(a,b,c)
            f.write(struct.pack("<3f",nx,ny,nz))
            f.write(struct.pack("<3f",*a))
            f.write(struct.pack("<3f",*b))
            f.write(struct.pack("<3f",*c))
            f.write(struct.pack("<H",0))

# -----------------------------------------------------------------------------
# 3MF parsing
# -----------------------------------------------------------------------------
def read_all_models(path):
    roots={}
    with zipfile.ZipFile(path,"r") as zf:
        for n in zf.namelist():
            if n.startswith("3D/") and n.lower().endswith(".model"):
                roots[n]=ET.fromstring(zf.read(n))
    return roots

def file_unit_scales(all_models):
    return {fname:_unit_to_mm(root.get("unit")) for fname,root in all_models.items()}

def build_object_map(all_models):
    obj_map={}
    for fname,root in all_models.items():
        res=root.find("m:resources",NS)
        if res is None: continue
        for obj in res.findall("m:object",NS):
            oid=obj.get("id")
            if oid: obj_map[f"{fname}:{oid}"]=obj
    return obj_map

def read_mesh(obj):
    mesh=obj.find("m:mesh",NS)
    if mesh is None: return None
    vs=mesh.find("m:vertices",NS); ts=mesh.find("m:triangles",NS)
    if vs is None or ts is None: return None
    verts=[(float(v.get("x","0")),float(v.get("y","0")),float(v.get("z","0"))) for v in vs.findall("m:vertex",NS)]
    tris=[(int(t.get("v1")),int(t.get("v2")),int(t.get("v3"))) for t in ts.findall("m:triangle",NS)]
    return verts,tris

# -----------------------------------------------------------------------------
# Leaf traversal (no rescaling for Prusa)
# -----------------------------------------------------------------------------
def gather_leaves_mm(obj_map, obj_key, tf_mm, parent_file, file_scale_mm):
    obj=obj_map[obj_key]
    if obj.find("m:mesh",NS) is not None:
        yield obj,tf_mm,parent_file
        return
    comps=obj.find("m:components",NS)
    if comps is None: return
    for c in comps.findall("m:component",NS):
        ref=c.get("objectid")
        path=(c.get(f"{{{PROD_NS}}}path") or c.get("p:path") or c.get("path"))
        if not ref: continue
        child_file=path.strip("/") if path and path.startswith("/") else parent_file
        child_key=f"{child_file}:{ref}"
        if child_key not in obj_map:
            print(f"[warn] missing ref {child_key}"); continue
        local=parse_tf_3mf(c.get("transform")) if c.get("transform") else IDENTITY_12[:]
        local_mm=local[:]
        tf_next_mm=mul_tf(tf_mm,local_mm)
        yield from gather_leaves_mm(obj_map,child_key,tf_next_mm,child_file,file_scale_mm)

# -----------------------------------------------------------------------------
# Prusa sidecar
# -----------------------------------------------------------------------------
def read_prusa_sidecar(zf):
    names=[n for n in zf.namelist() if "Sodel.config" in n or "PrusaSlicer_model.config" in n or "OrcaSlicer_model.config" in n]
    if not names: return []
    try: root=ET.fromstring(zf.read(names[0]))
    except Exception: return []
    if root.tag!="config": return []
    out=[]
    for obj in root.findall("object"):
        oid=obj.get("id")
        for vol in obj.findall("volume"):
            first,last=int(vol.get("firstid","-1")),int(vol.get("lastid","-1"))
            if first<0 or last<first: continue
            mat=None
            for md in vol.findall("metadata"):
                if md.get("key")=="matrix": mat=md.get("value")
            out.append({"first":first,"last":last,"matrix12":parse_prusa_matrix_to_12(mat) if mat else None,"object_id":oid})
    return out

# -----------------------------------------------------------------------------
# Packing and baking
# -----------------------------------------------------------------------------
def _bbox(verts):
    xs=[v[0] for v in verts]; ys=[v[1] for v in verts]; zs=[v[2] for v in verts]
    return (min(xs),max(xs)),(min(ys),max(ys)),(min(zs),max(zs))
def _diag(bx,by,bz): return math.sqrt((bx[1]-bx[0])**2+(by[1]-by[0])**2+(bz[1]-bz[0])**2)

def pack_3mf(parts,out,title="baked_roundtrip"):
    ET.register_namespace("",CORE_NS)
    root=ET.Element(M("model"),{"unit":"millimeter",f"{{{XML_NS}}}lang":"en-US"})
    ET.SubElement(root,M("metadata"),{"name":"Title"}).text=title
    res=ET.SubElement(root,M("resources")); build=ET.SubElement(root,M("build"))
    for i,(lbl,verts,tris) in enumerate(parts,1):
        o=ET.SubElement(res,M("object"),{"id":str(i),"type":"model","name":lbl})
        for n in("Title","Name"): ET.SubElement(o,M("metadata"),{"name":n}).text=lbl
        m=ET.SubElement(o,M("mesh"))
        vs=ET.SubElement(m,M("vertices"))
        for x,y,z in verts:
            v=ET.SubElement(vs,M("vertex"))
            v.set("x",f"{x:.9f}".rstrip("0").rstrip("."))
            v.set("y",f"{y:.9f}".rstrip("0").rstrip("."))
            v.set("z",f"{z:.9f}".rstrip("0").rstrip("."))
        ts=ET.SubElement(m,M("triangles"))
        for a,b,c in tris:
            t=ET.SubElement(ts,M("triangle"))
            t.set("v1",str(a)); t.set("v2",str(b)); t.set("v3",str(c))
        ET.SubElement(build,M("item"),{"objectid":str(i),"printable":"1","partnumber":lbl})
    xml=ET.tostring(root,encoding="utf-8",xml_declaration=True)
    with zipfile.ZipFile(out,"w",compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
        z.writestr("_rels/.rels",b'<?xml version="1.0"?><Relationships xmlns="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" Target="3D/3dmodel.model"/></Relationships>')
        z.writestr("3D/3dmodel.model",xml)
        z.writestr("3D/_rels/3dmodel.model.rels",b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')

# -----------------------------------------------------------------------------
# Main bake_roundtrip with name preservation
# -----------------------------------------------------------------------------
def bake_roundtrip(input_path,output_path,keep_stls=False,debug=False,safe_volumes=False,growth_limit=2.0):
    zf=zipfile.ZipFile(input_path,"r")

    # =====================================================
    # 🆕 PATCH: Preserve original object names from input
    # =====================================================
    original_names = {}
    try:
        with zipfile.ZipFile(input_path, "r") as zin:
            model_name = next(
                (n for n in zin.namelist() if n.startswith("3D/") and n.lower().endswith(".model")), None
            )
            if model_name:
                root = ET.fromstring(zin.read(model_name))
                ns = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
                for obj in root.findall(f".//{ns}object"):
                    oid, nm = obj.get("id"), obj.get("name")
                    if oid and nm:
                        original_names[oid] = nm
        if debug:
            print(f"[bake] preserved {len(original_names)} original names")
    except Exception as e:
        print("[warn] could not extract original object names:", e)
    # =====================================================

    models=read_all_models(input_path)
    obj_map=build_object_map(models)
    scales=file_unit_scales(models)
    prusa_vols=read_prusa_sidecar(zf)
    main="3D/3dmodel.model" if "3D/3dmodel.model" in models else next(iter(models))
    root=models[main]; main_scale=scales.get(main,1.0)
    build=root.find("m:build",NS)
    tmp=tempfile.mkdtemp(prefix="bake3mf_")
    parts=[]
    for item in build.findall("m:item",NS):
        oid=item.get("objectid")
        if not oid: continue
        tf=parse_tf_3mf(item.get("transform"))
        tf_mm=tf[:]  # do not scale build-item translations
        base_key=f"{main}:{oid}"
        if base_key not in obj_map:
            match=[k for k in obj_map if k.endswith(f":{oid}")]
            if not match: continue
            base_key=match[0]
        for leaf,tf_final,src in gather_leaves_mm(obj_map,base_key,tf_mm,main,scales):
            mesh=read_mesh(leaf)
            if not mesh: continue
            verts,tris=mesh
            s=scales.get(src,1.0)
            if s!=1.0: verts=[(x*s,y*s,z*s) for x,y,z in verts]
            verts_tf=apply_tf(tf_final,verts)
            bx,by,bz=_bbox(verts_tf); diag=_diag(bx,by,bz)
            # 🔹 Use preserved name if available
            label = leaf.get("name") or original_names.get(oid) or f"id_{oid}"
            if debug: print(f"[part] {label} diag={diag:.2f} src={src}")
            write_stl(os.path.join(tmp,f"{label}.stl"),verts_tf,tris)
            parts.append((label,verts_tf,tris))
    pack_3mf(parts,output_path)
    if keep_stls: print(f"[info] STLs kept in {tmp}")
    else: shutil.rmtree(tmp,ignore_errors=True)
    print(f"[done] baked+repacked → {output_path} ({len(parts)} parts)")

# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("-i","--input",required=True)
    ap.add_argument("-o","--output",required=True)
    ap.add_argument("--keep-stls",action="store_true")
    ap.add_argument("--debug",action="store_true")
    ap.add_argument("--safe-volumes",action="store_true")
    ap.add_argument("--growth-limit",type=float,default=2.0)
    a=ap.parse_args()
    bake_roundtrip(a.input,a.output,a.keep_stls,a.debug,a.safe_volumes,a.growth_limit)
