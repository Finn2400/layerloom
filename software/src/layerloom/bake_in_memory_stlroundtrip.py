#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bake_in_memory_stlroundtrip.py (v9-streaming-3mf)
-------------------------------------------------
Universal 3MF baker/flattener that can handle *very large* 3D/3dmodel.model files
(e.g., multi-GB) by streaming XML parsing (ElementTree.iterparse) and avoiding
ET.fromstring(zf.read(...)).

Key changes vs v8:
- 3MF model XML is parsed with iterparse() directly from the zip member stream.
- Mesh vertex/triangle data is *spooled to temp binary files* per object (float32/uint32),
  rather than held in memory.
- Output 3MF is written as a streamed XML file (no giant ElementTree DOM).
- Unit handling: vertex coordinates are converted to mm; translations are converted to mm
  only when unit != millimeter (no double-scaling for mm Prusa/Bambu).

Notes / limitations:
- Your example 3MF contains only [Content_Types].xml, _rels/.rels, and a huge 3D/3dmodel.model.
  This script assumes a single model file: 3D/3dmodel.model.
- This script does not implement every 3MF extension (materials, colors, textures).
  It focuses on core geometry + build/components transforms similar to your original.
"""

import argparse, os, tempfile, shutil, zipfile, math, struct, mmap, sys, time
import xml.etree.ElementTree as ET
from array import array
from typing import List, Dict, Optional, Tuple, Iterable

try:
    import resource
except Exception:
    resource = None

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
XML_NS  = "http://www.w3.org/XML/1998/namespace"
PROD_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"

NS = {"m": CORE_NS}
M  = lambda tag: f"{{{CORE_NS}}}{tag}"

IDENTITY_12 = [1,0,0, 0,1,0, 0,0,1, 0,0,0]


def _rss_mb() -> Optional[float]:
    if resource is None:
        return None
    try:
        raw = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        return None
    if sys.platform == "darwin":
        return raw / (1024.0 * 1024.0)
    return raw / 1024.0


def _perf(label: str, started_at: float, *, extra: str = "") -> None:
    elapsed = time.perf_counter() - started_at
    msg = f"[perf] {label}: {elapsed:.3f}s"
    rss = _rss_mb()
    if rss is not None:
        msg += f" | rss≈{rss:.1f} MB"
    if extra:
        msg += f" | {extra}"
    print(msg)


# -----------------------------------------------------------------------------
# Unit handling
# -----------------------------------------------------------------------------
def _unit_to_mm(unit: Optional[str]) -> float:
    if not unit:
        return 1.0
    u = unit.strip().lower()
    if u in ("millimeter", "millimetre", "mm"):
        return 1.0
    if u in ("micron", "micrometer", "micrometre", "um"):
        return 0.001
    if u in ("meter", "metre", "m"):
        return 1000.0
    if u in ("inch", "in"):
        return 25.4
    if u in ("foot", "ft"):
        return 304.8
    return 1.0

def scale_tf_translation(tf12: List[float], s: float) -> List[float]:
    """Scale only the translation terms of a 3MF 12-float transform."""
    if s == 1.0:
        return tf12[:]
    out = tf12[:]
    out[9]  *= s
    out[10] *= s
    out[11] *= s
    return out


# -----------------------------------------------------------------------------
# Matrix math
# -----------------------------------------------------------------------------
def parse_tf_3mf(s: Optional[str]) -> List[float]:
    if not s:
        return IDENTITY_12[:]
    vals = [float(v) for v in s.replace(",", " ").split()]
    return vals if len(vals) == 12 else IDENTITY_12[:]

def to4x4(t12: List[float]) -> List[List[float]]:
    return [
        [t12[0], t12[3], t12[6],  t12[9]],
        [t12[1], t12[4], t12[7],  t12[10]],
        [t12[2], t12[5], t12[8],  t12[11]],
        [0,      0,      0,       1],
    ]

def from4x4(M4: List[List[float]]) -> List[float]:
    return [
        M4[0][0], M4[1][0], M4[2][0],
        M4[0][1], M4[1][1], M4[2][1],
        M4[0][2], M4[1][2], M4[2][2],
        M4[0][3], M4[1][3], M4[2][3],
    ]

def mul_tf(a12: List[float], b12: List[float]) -> List[float]:
    A, B = to4x4(a12), to4x4(b12)
    C = [[sum(A[i][k] * B[k][j] for k in range(4)) for j in range(4)] for i in range(4)]
    return from4x4(C)

def apply_tf_to_point(tf12: List[float], x: float, y: float, z: float) -> Tuple[float, float, float]:
    M4 = to4x4(tf12)
    return (
        M4[0][0]*x + M4[0][1]*y + M4[0][2]*z + M4[0][3],
        M4[1][0]*x + M4[1][1]*y + M4[1][2]*z + M4[1][3],
        M4[2][0]*x + M4[2][1]*y + M4[2][2]*z + M4[2][3],
    )

def parse_prusa_matrix_to_12(s: str) -> List[float]:
    vals = [float(v) for v in s.replace(",", " ").split()]
    if len(vals) == 16:
        m00,m01,m02,m03,m10,m11,m12,m13,m20,m21,m22,m23,*_ = vals
        return [m00,m10,m20, m01,m11,m21, m02,m12,m22, m03,m13,m23]
    return vals if len(vals) == 12 else IDENTITY_12[:]


# -----------------------------------------------------------------------------
# STL writing
# -----------------------------------------------------------------------------
def _normal(a, b, c):
    ux,uy,uz = b[0]-a[0], b[1]-a[1], b[2]-a[2]
    vx,vy,vz = c[0]-a[0], c[1]-a[1], c[2]-a[2]
    nx,ny,nz = (uy*vz-uz*vy, uz*vx-ux*vz, ux*vy-uy*vx)
    l = math.sqrt(nx*nx + ny*ny + nz*nz)
    return (nx/l, ny/l, nz/l) if l else (0.0, 0.0, 0.0)

def write_stl_from_spool(
    out_path: str,
    v_mm: mmap.mmap,
    t_mm: mmap.mmap,
    v_count: int,
    t_count: int,
    tf12: List[float],
):
    """
    Write binary STL using vertex+triangle spools (float32 verts, uint32 tris),
    applying tf12 (already in mm space).
    """
    def v_at(i: int) -> Tuple[float,float,float]:
        off = i * 12
        x,y,z = struct.unpack_from("<3f", v_mm, off)
        return apply_tf_to_point(tf12, x, y, z)

    with open(out_path, "wb") as f:
        f.write(b"Baked by bake_in_memory_stlroundtrip (stream)".ljust(80, b"\0"))
        f.write(struct.pack("<I", t_count))
        for ti in range(t_count):
            off = ti * 12
            a_i, b_i, c_i = struct.unpack_from("<3I", t_mm, off)
            a = v_at(a_i); b = v_at(b_i); c = v_at(c_i)
            nx,ny,nz = _normal(a,b,c)
            f.write(struct.pack("<3f", nx, ny, nz))
            f.write(struct.pack("<3f", *a))
            f.write(struct.pack("<3f", *b))
            f.write(struct.pack("<3f", *c))
            f.write(struct.pack("<H", 0))


# -----------------------------------------------------------------------------
# Streaming 3MF model parsing
# -----------------------------------------------------------------------------
def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag

class MeshSpool:
    """
    Per-object spool for a mesh:
      - verts: float32 triplets (x,y,z) in *model units* (converted to mm before writing!)
      - tris:  uint32 triplets (v1,v2,v3)
    We store verts already scaled to mm (float32), so later transforms can be mm-consistent.
    """
    def __init__(self, tmp_dir: str, obj_id: str):
        self.obj_id = obj_id
        self.v_path = os.path.join(tmp_dir, f"obj_{obj_id}__verts_f32.bin")
        self.t_path = os.path.join(tmp_dir, f"obj_{obj_id}__tris_u32.bin")
        self.vf = open(self.v_path, "wb")
        self.tf = open(self.t_path, "wb")
        self.v_count = 0
        self.t_count = 0

    def add_vertex_mm(self, x: float, y: float, z: float):
        self.vf.write(struct.pack("<3f", float(x), float(y), float(z)))
        self.v_count += 1

    def add_tri(self, a: int, b: int, c: int):
        self.tf.write(struct.pack("<3I", int(a), int(b), int(c)))
        self.t_count += 1

    def close(self):
        try:
            self.vf.close()
        finally:
            self.tf.close()

def parse_3dmodel_streaming(
    zf: zipfile.ZipFile,
    member: str,
    tmp_dir: str,
    needed_obj_ids: Optional[set] = None,
    pass_mode: str = "meta",
) -> Dict:
    """
    Streaming parse of 3D/3dmodel.model.

    pass_mode:
      - "meta": build object/component graph + build items + names + unit. No mesh spooling.
      - "mesh": spool mesh data for objects in needed_obj_ids (and only those).

    Returns dict with keys:
      unit_scale_mm: float
      objects: dict[obj_id] -> {"name": str|None, "components": [(child_id, tf12)] , "has_mesh": bool}
      build_items: list[(obj_id, tf12)]
      meshes: dict[obj_id] -> {"v_path","t_path","v_count","t_count"}   (only for pass_mode="mesh")
    """
    objects: Dict[str, Dict] = {}
    build_items: List[Tuple[str, List[float]]] = []
    meshes: Dict[str, Dict] = {}

    unit_scale_mm = 1.0

    # State while iterparsing
    cur_obj_id: Optional[str] = None
    cur_obj_name: Optional[str] = None
    in_components = False
    in_mesh = False
    in_vertices = False
    in_triangles = False
    cur_spool: Optional[MeshSpool] = None

    with zf.open(member) as f:
        # events=("start","end") so we can detect boundaries and clear elements.
        for event, elem in ET.iterparse(f, events=("start", "end")):
            tag = _strip_ns(elem.tag)

            if event == "start":
                if tag == "model":
                    # unit is on root <model>
                    unit_scale_mm = _unit_to_mm(elem.get("unit"))
                elif tag == "object":
                    cur_obj_id = elem.get("id")
                    cur_obj_name = elem.get("name")
                    if cur_obj_id:
                        objects.setdefault(cur_obj_id, {"name": cur_obj_name, "components": [], "has_mesh": False})
                        # If name attribute absent, keep whatever we saw first
                        if cur_obj_name and not objects[cur_obj_id].get("name"):
                            objects[cur_obj_id]["name"] = cur_obj_name
                elif tag == "components":
                    in_components = True
                elif tag == "component" and in_components and cur_obj_id:
                    child = elem.get("objectid")
                    if child:
                        local = parse_tf_3mf(elem.get("transform"))
                        objects[cur_obj_id]["components"].append((child, local))
                elif tag == "mesh" and cur_obj_id:
                    in_mesh = True
                    objects[cur_obj_id]["has_mesh"] = True
                    # If this is the mesh pass, spool only if needed
                    if pass_mode == "mesh":
                        if (needed_obj_ids is None) or (cur_obj_id in needed_obj_ids):
                            cur_spool = MeshSpool(tmp_dir, cur_obj_id)
                elif tag == "vertices" and in_mesh:
                    in_vertices = True
                elif tag == "triangles" and in_mesh:
                    in_triangles = True
                elif tag == "item":
                    # <build><item objectid="..."> ... transform="...">
                    oid = elem.get("objectid")
                    if oid:
                        tf = parse_tf_3mf(elem.get("transform"))
                        build_items.append((oid, tf))

            else:  # event == "end"
                if tag == "vertex" and in_vertices and cur_spool is not None:
                    # Convert vertex to mm immediately so later transform math is consistent
                    x = float(elem.get("x", "0")) * unit_scale_mm
                    y = float(elem.get("y", "0")) * unit_scale_mm
                    z = float(elem.get("z", "0")) * unit_scale_mm
                    cur_spool.add_vertex_mm(x, y, z)
                elif tag == "triangle" and in_triangles and cur_spool is not None:
                    a = int(elem.get("v1", "0"))
                    b = int(elem.get("v2", "0"))
                    c = int(elem.get("v3", "0"))
                    cur_spool.add_tri(a, b, c)

                elif tag == "vertices":
                    in_vertices = False
                elif tag == "triangles":
                    in_triangles = False
                elif tag == "mesh":
                    in_mesh = False
                    if cur_spool is not None:
                        cur_spool.close()
                        meshes[cur_spool.obj_id] = {
                            "v_path": cur_spool.v_path,
                            "t_path": cur_spool.t_path,
                            "v_count": cur_spool.v_count,
                            "t_count": cur_spool.t_count,
                        }
                        cur_spool = None
                elif tag == "components":
                    in_components = False
                elif tag == "object":
                    cur_obj_id = None
                    cur_obj_name = None

                # Critical for streaming memory use
                elem.clear()

    return {
        "unit_scale_mm": unit_scale_mm,
        "objects": objects,
        "build_items": build_items,
        "meshes": meshes,
    }


# -----------------------------------------------------------------------------
# Component traversal (now uses streamed meta graph)
# -----------------------------------------------------------------------------
def compute_needed_closure(objects: Dict[str, Dict], build_items: List[Tuple[str, List[float]]]) -> set:
    needed = set(oid for oid, _ in build_items)
    stack = list(needed)
    while stack:
        oid = stack.pop()
        info = objects.get(oid)
        if not info:
            continue
        for child_id, _tf in info.get("components", []):
            if child_id not in needed:
                needed.add(child_id)
                stack.append(child_id)
    return needed

def gather_leaves_mm_from_meta(
    objects: Dict[str, Dict],
    obj_id: str,
    tf_mm: List[float],
    unit_scale_mm: float,
) -> Iterable[Tuple[str, List[float]]]:
    """
    Yield (leaf_obj_id, tf_mm_total) for each mesh leaf under obj_id.
    Important: component local transforms are in *model units*. We must scale translation
    into mm when unit != mm. (Vertices are already spooled in mm.)
    """
    info = objects.get(obj_id)
    if not info:
        return
    if info.get("has_mesh", False):
        yield (obj_id, tf_mm)
        return
    for child_id, local_tf in info.get("components", []):
        # scale translation only if needed
        local_tf_mm = scale_tf_translation(local_tf, unit_scale_mm)
        tf_next = mul_tf(tf_mm, local_tf_mm)
        yield from gather_leaves_mm_from_meta(objects, child_id, tf_next, unit_scale_mm)


# -----------------------------------------------------------------------------
# Prusa sidecar (small; OK to parse normally)
# -----------------------------------------------------------------------------
def read_prusa_sidecar(zf: zipfile.ZipFile):
    names = [
        n for n in zf.namelist()
        if ("Slic3r_PE_model.config" in n or "PrusaSlicer_model.config" in n or "OrcaSlicer_model.config" in n)
    ]
    if not names:
        return []
    try:
        root = ET.fromstring(zf.read(names[0]))
    except Exception:
        return []
    if root.tag != "config":
        return []
    out = []
    for obj in root.findall("object"):
        oid = obj.get("id")
        for vol in obj.findall("volume"):
            first, last = int(vol.get("firstid", "-1")), int(vol.get("lastid", "-1"))
            if first < 0 or last < first:
                continue
            mat = None
            for md in vol.findall("metadata"):
                if md.get("key") == "matrix":
                    mat = md.get("value")
            out.append({"first": first, "last": last, "matrix12": parse_prusa_matrix_to_12(mat) if mat else None, "object_id": oid})
    return out


# -----------------------------------------------------------------------------
# Streamed 3MF packer (no giant DOM)
# -----------------------------------------------------------------------------
def _fmt_f(x: float) -> str:
    # compact float formatting similar to your original
    s = f"{x:.9f}".rstrip("0").rstrip(".")
    return s if s else "0"

def pack_3mf_stream(
    out_path: str,
    title: str,
    parts: List[Dict],
    meshes: Dict[str, Dict],
):
    """
    Write output 3MF with streamed XML:
    parts: list of dict with keys:
      - label
      - leaf_obj_id
      - tf_mm_total
    meshes: leaf_obj_id -> spool metadata
    """
    # Build XML to a temp file so we can zip it
    tmp_xml = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", newline="\n")
    tmp_xml_path = tmp_xml.name

    def w(line: str):
        tmp_xml.write(line)

    # XML header + root + metadata
    w('<?xml version="1.0" encoding="utf-8"?>\n')
    w(f'<model xmlns="{CORE_NS}" unit="millimeter" xml:lang="en-US">\n')
    w(f'  <metadata name="Title">{title}</metadata>\n')
    w('  <resources>\n')

    # Write each object as its own mesh, re-indexing verts/tris 1..N
    # (We keep triangles referencing original indices; since we re-emit all vertices in order, indices match.)
    for out_id, part in enumerate(parts, start=1):
        label = part["label"]
        leaf_id = part["leaf_obj_id"]
        tf12 = part["tf_mm_total"]

        m = meshes.get(leaf_id)
        if not m:
            # No geometry for this leaf (shouldn't happen)
            continue

        v_path = m["v_path"]; t_path = m["t_path"]
        v_count = m["v_count"]; t_count = m["t_count"]

        w(f'    <object id="{out_id}" type="model" name="{label}">\n')
        w(f'      <metadata name="Title">{label}</metadata>\n')
        w(f'      <metadata name="Name">{label}</metadata>\n')
        w('      <mesh>\n')
        w('        <vertices>\n')

        # Stream vertices: read float32 triplets, apply final tf12, write
        with open(v_path, "rb") as vf:
            vmm = mmap.mmap(vf.fileno(), 0, access=mmap.ACCESS_READ)
            for i in range(v_count):
                off = i * 12
                x, y, z = struct.unpack_from("<3f", vmm, off)
                X, Y, Z = apply_tf_to_point(tf12, x, y, z)
                w(f'          <vertex x="{_fmt_f(X)}" y="{_fmt_f(Y)}" z="{_fmt_f(Z)}"/>\n')
            vmm.close()

        w('        </vertices>\n')
        w('        <triangles>\n')

        # Stream triangles: uint32 triplets
        with open(t_path, "rb") as tf:
            tmm = mmap.mmap(tf.fileno(), 0, access=mmap.ACCESS_READ)
            for i in range(t_count):
                off = i * 12
                a, b, c = struct.unpack_from("<3I", tmm, off)
                w(f'          <triangle v1="{a}" v2="{b}" v3="{c}"/>\n')
            tmm.close()

        w('        </triangles>\n')
        w('      </mesh>\n')
        w('    </object>\n')

    w('  </resources>\n')
    w('  <build>\n')

    # build items: each output object gets a build item at identity
    for out_id, part in enumerate(parts, start=1):
        label = part["label"]
        w(f'    <item objectid="{out_id}" printable="1" partnumber="{label}"/>\n')

    w('  </build>\n')
    w('</model>\n')
    tmp_xml.close()

    # Zip it as a 3MF
    xml_bytes = None
    with open(tmp_xml_path, "rb") as f:
        xml_bytes = f.read()

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            b'<?xml version="1.0"?>'
            b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            b'<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
            b'</Types>'
        )
        z.writestr(
            "_rels/.rels",
            b'<?xml version="1.0"?>'
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" Target="3D/3dmodel.model"/>'
            b'</Relationships>'
        )
        z.writestr("3D/3dmodel.model", xml_bytes)
        z.writestr(
            "3D/_rels/3dmodel.model.rels",
            b'<?xml version="1.0"?>'
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
        )

    os.unlink(tmp_xml_path)


# -----------------------------------------------------------------------------
# Main bake
# -----------------------------------------------------------------------------
def bake_roundtrip(
    input_path: str,
    output_path: str,
    keep_stls: bool = False,
    debug: bool = False,
    safe_volumes: bool = False,
    growth_limit: float = 2.0,
):
    member = "3D/3dmodel.model"
    t_total = time.perf_counter()

    tmp_root = tempfile.mkdtemp(prefix="bake3mf_stream_")
    tmp_mesh_dir = os.path.join(tmp_root, "mesh_spools")
    os.makedirs(tmp_mesh_dir, exist_ok=True)

    try:
        with zipfile.ZipFile(input_path, "r") as zf:
            if member not in zf.namelist():
                raise RuntimeError(f"Expected {member} in 3MF; found: {zf.namelist()}")

            prusa_vols = read_prusa_sidecar(zf)  # retained (currently unused like your v8)

            # PASS 1: META (unit, objects, components, build)
            t_meta = time.perf_counter()
            meta = parse_3dmodel_streaming(zf, member, tmp_mesh_dir, pass_mode="meta")
            _perf("bake meta parse", t_meta, extra=f"objects={len(meta['objects'])} build_items={len(meta['build_items'])}")
            unit_scale_mm = meta["unit_scale_mm"]
            objects = meta["objects"]
            build_items = meta["build_items"]

            if debug:
                print(f"[meta] unit_scale_mm={unit_scale_mm}")
                print(f"[meta] objects={len(objects)} build_items={len(build_items)}")

            if not build_items:
                raise RuntimeError("No <build><item> entries found; cannot proceed.")

            # Determine which object IDs we actually need (closure over components)
            needed = compute_needed_closure(objects, build_items)

            if debug:
                print(f"[meta] needed objects (closure) = {len(needed)}")

            # PASS 2: MESH (spool only needed meshes to disk; vertices are pre-scaled to mm)
            t_mesh = time.perf_counter()
            mesh_pass = parse_3dmodel_streaming(
                zf,
                member,
                tmp_mesh_dir,
                needed_obj_ids=needed,
                pass_mode="mesh",
            )
            meshes = mesh_pass["meshes"]
            _perf("bake mesh spool", t_mesh, extra=f"needed={len(needed)} spooled={len(meshes)}")

            if debug:
                total_v = sum(m["v_count"] for m in meshes.values())
                total_t = sum(m["t_count"] for m in meshes.values())
                print(f"[mesh] spooled meshes={len(meshes)} verts={total_v} tris={total_t}")

            # Build parts list by expanding build items into leaf meshes
            parts: List[Dict] = []
            stl_dir = os.path.join(tmp_root, "stls") if keep_stls else None

            t_parts = time.perf_counter()
            for base_oid, base_tf in build_items:
                # Convert build-item translation into mm (only if needed)
                base_tf_mm = scale_tf_translation(base_tf, unit_scale_mm)

                # Expand into mesh leaves
                for leaf_oid, tf_leaf_mm in gather_leaves_mm_from_meta(objects, base_oid, base_tf_mm, unit_scale_mm):
                    if leaf_oid not in meshes:
                        if debug:
                            print(f"[warn] leaf {leaf_oid} has_mesh={objects.get(leaf_oid,{}).get('has_mesh')} but no spooled mesh found; skipping")
                        continue

                    label = objects.get(leaf_oid, {}).get("name") or f"id_{leaf_oid}"

                    m = meshes[leaf_oid]
                    if keep_stls:
                        os.makedirs(stl_dir, exist_ok=True)
                        # Write STL roundtrip artifacts only when explicitly requested.
                        with open(m["v_path"], "rb") as vf, open(m["t_path"], "rb") as tf:
                            vmm = mmap.mmap(vf.fileno(), 0, access=mmap.ACCESS_READ)
                            tmm = mmap.mmap(tf.fileno(), 0, access=mmap.ACCESS_READ)
                            stl_path = os.path.join(stl_dir, f"{label}.stl")
                            write_stl_from_spool(stl_path, vmm, tmm, m["v_count"], m["t_count"], tf_leaf_mm)
                            vmm.close(); tmm.close()

                    parts.append({"label": label, "leaf_obj_id": leaf_oid, "tf_mm_total": tf_leaf_mm})

                    if debug:
                        print(f"[part] {label} leaf={leaf_oid} v={m['v_count']} t={m['t_count']}")

            if not parts:
                raise RuntimeError("No parts produced (no leaf meshes found).")

            _perf("bake part expansion", t_parts, extra=f"parts={len(parts)}")

            # Pack to output 3MF (streamed XML)
            t_pack = time.perf_counter()
            pack_3mf_stream(output_path, title="baked_roundtrip_stream", parts=parts, meshes=meshes)
            _perf("bake pack 3mf", t_pack, extra=os.path.basename(output_path))

            if keep_stls:
                print(f"[info] STLs kept in {stl_dir}")
                print(f"[info] temp data kept in {tmp_root}")
            else:
                shutil.rmtree(tmp_root, ignore_errors=True)

            print(f"[done] baked+repacked(stream) → {output_path} ({len(parts)} parts)")
            _perf("bake total", t_total, extra=f"parts={len(parts)}")

    except Exception:
        # If something explodes and keep_stls is on, keep temp for debugging
        if keep_stls:
            print(f"[info] keeping temp dir for debugging: {tmp_root}")
        else:
            shutil.rmtree(tmp_root, ignore_errors=True)
        raise


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--input", required=True)
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--keep-stls", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--safe-volumes", action="store_true")
    ap.add_argument("--growth-limit", type=float, default=2.0)
    a = ap.parse_args()
    bake_roundtrip(a.input, a.output, a.keep_stls, a.debug, a.safe_volumes, a.growth_limit)
