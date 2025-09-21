#!/usr/bin/env python3
# bake_in_memory.py
#
# Step 2: In-memory bake for 3MF files.
# - Parses 3D/3dmodel.model
# - Composes build-item and component transforms (3MF 12-number, column-major 3x4)
# - Applies world transforms to *leaf* mesh vertices
# - Flattens components (no <components> in output), build items get identity transforms
# - Writes a *baked* 3MF (optional), but the core baker works in-memory
#
# Usage:
#   python3 bake_in_memory.py -i input.3mf -o baked.3mf
#
# Validate:
#   python3 needs_bake.py -i baked.3mf  # should report needs_bake: NO

import argparse, sys, io, zipfile, math
import xml.etree.ElementTree as ET

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
NS = {"m": CORE_NS}
M = lambda tag: f"{{{CORE_NS}}}{tag}"

# 3MF transform: 12 floats, column-major 3x4:
# [ m11 m21 m31  m12 m22 m32  m13 m23 m33  m14 m24 m34 ]
IDENTITY_12 = [1.0,0.0,0.0,  0.0,1.0,0.0,  0.0,0.0,1.0,  0.0,0.0,0.0]

def _parse_tf_attr(s: str):
    if not s:
        return IDENTITY_12[:]
    parts = s.replace(",", " ").split()
    vals = [float(p) for p in parts]
    if len(vals) != 12:
        raise ValueError("transform must have 12 floats (3MF column-major 3x4)")
    return vals

def _mul_tf(a, b):
    # a,b are 12-float 3x4 (column-major). Return a @ b.
    # Convert to 4x4, multiply, return 12 again.
    A = _to4x4(a); B = _to4x4(b)
    C = [[0.0]*4 for _ in range(4)]
    for i in range(4):
        for j in range(4):
            C[i][j] = sum(A[i][k]*B[k][j] for k in range(4))
    return _from4x4(C)

def _to4x4(t):
    # column-major 3x4 → 4x4
    # t indices: [0..11]
    # columns: c0=(0,1,2,tx=9), c1=(3,4,5,ty=10), c2=(6,7,8,tz=11)
    return [
        [t[0], t[3], t[6],  t[9]],
        [t[1], t[4], t[7],  t[10]],
        [t[2], t[5], t[8],  t[11]],
        [0.0,  0.0,  0.0,   1.0],
    ]

def _from4x4(M4):
    # back to 12-float 3x4 (column-major)
    return [
        M4[0][0], M4[1][0], M4[2][0],
        M4[0][1], M4[1][1], M4[2][1],
        M4[0][2], M4[1][2], M4[2][2],
        M4[0][3], M4[1][3], M4[2][3],
    ]

def _apply_tf_to_vertices(tf12, verts):
    M4 = _to4x4(tf12)
    # Apply to column vectors [x,y,z,1]^T
    out = []
    for (x,y,z) in verts:
        X = M4[0][0]*x + M4[0][1]*y + M4[0][2]*z + M4[0][3]
        Y = M4[1][0]*x + M4[1][1]*y + M4[1][2]*z + M4[1][3]
        Z = M4[2][0]*x + M4[2][1]*y + M4[2][2]*z + M4[2][3]
        out.append((X,Y,Z))
    return out

def _find_model_xml(zf):
    for n in zf.namelist():
        if n.startswith("3D/") and n.lower().endswith(".model"):
            return n
    return None

def _read_model_root(path):
    with zipfile.ZipFile(path, "r") as zf:
        name = _find_model_xml(zf)
        if not name:
            raise RuntimeError("3D/*.model not found in .3mf")
        xml = zf.read(name)
    return ET.fromstring(xml)

def _write_3mf(root, out_path):
    # Minimal valid 3MF package
    content_types = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        b'<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
        b'</Types>'
    )
    rels = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" Target="/3D/3dmodel.model"/>'
        b'</Relationships>'
    )
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("3D/3dmodel.model", data)

def _is_identity(vals):
    if len(vals) != 12: return False
    eps = 1e-12
    for a,b in zip(vals, IDENTITY_12):
        if abs(a-b) > eps:
            return False
    return True

def detect_needs_bake(root):
    reasons = []
    build = root.find("m:build", NS)
    res = root.find("m:resources", NS)
    if build is None or res is None:
        raise RuntimeError("Invalid 3MF (missing <build> or <resources>)")
    with_tf = 0
    for it in build.findall("m:item", NS):
        tf = it.get("transform")
        if tf:
            vals = _parse_tf_attr(tf)
            if not _is_identity(vals):
                with_tf += 1
    with_components = 0
    for obj in res.findall("m:object", NS):
        comps = obj.find("m:components", NS)
        if comps is not None and list(comps):
            with_components += 1
    if with_tf: reasons.append("build_transform")
    if with_components: reasons.append("components")
    return (with_tf>0 or with_components>0), reasons

def _read_mesh(obj_elem):
    mesh = obj_elem.find("m:mesh", NS)
    if mesh is None: return None
    verts_elem = mesh.find("m:vertices", NS)
    tris_elem  = mesh.find("m:triangles", NS)
    if verts_elem is None or tris_elem is None: return None
    verts = []
    for v in verts_elem.findall("m:vertex", NS):
        x = float(v.get("x", "0")); y = float(v.get("y", "0")); z = float(v.get("z", "0"))
        verts.append((x,y,z))
    tris = []
    for t in tris_elem.findall("m:triangle", NS):
        v1 = int(t.get("v1")); v2 = int(t.get("v2")); v3 = int(t.get("v3"))
        tris.append((v1,v2,v3))
    return verts, tris

def _write_mesh(obj_elem, verts, tris):
    # Clear existing mesh & rewrite
    mesh = obj_elem.find("m:mesh", NS)
    if mesh is None:
        mesh = ET.SubElement(obj_elem, M("mesh"))
    verts_elem = mesh.find("m:vertices", NS)
    tris_elem  = mesh.find("m:triangles", NS)
    if verts_elem is not None: mesh.remove(verts_elem)
    if tris_elem  is not None: mesh.remove(tris_elem)
    verts_elem = ET.SubElement(mesh, M("vertices"))
    for (x,y,z) in verts:
        v = ET.SubElement(verts_elem, M("vertex"))
        # keep 6–7 significant digits is typical in 3MFs
        v.set("x", f"{x:.8f}".rstrip("0").rstrip("."))
        v.set("y", f"{y:.8f}".rstrip("0").rstrip("."))
        v.set("z", f"{z:.8f}".rstrip("0").rstrip("."))
    tris_elem = ET.SubElement(mesh, M("triangles"))
    for (v1,v2,v3) in tris:
        t = ET.SubElement(tris_elem, M("triangle"))
        t.set("v1", str(v1)); t.set("v2", str(v2)); t.set("v3", str(v3))

def _gather_components_chain(resources, obj_elem, tf_parent):
    """Yield (mesh_object_elem, composed_tf12) for all leaf meshes under obj_elem."""
    # If this object is a mesh, yield directly with current tf
    mesh = obj_elem.find("m:mesh", NS)
    if mesh is not None:
        yield (obj_elem, tf_parent)
        return

    comps = obj_elem.find("m:components", NS)
    if comps is None:
        # No mesh, no components → nothing to do
        return

    for c in comps.findall("m:component", NS):
        ref = c.get("objectid")
        if ref is None: continue
        ref_obj = resources.find(f"m:object[@id='{ref}']", NS)
        if ref_obj is None: continue
        local = _parse_tf_attr(c.get("transform")) if c.get("transform") else IDENTITY_12[:]
        tf = _mul_tf(tf_parent, local)
        # Recurse
        yield from _gather_components_chain(resources, ref_obj, tf)

def bake_root_in_memory(root):
    """Return a new baked root: all build items reference mesh objects with identity transforms,
    and all component transforms are applied to leaf meshes’ vertices."""
    unit = root.get("unit", "millimeter")
    lang = root.get("{http://www.w3.org/XML/1998/namespace}lang", "en-US")

    res_in  = root.find("m:resources", NS)
    build_in = root.find("m:build", NS)
    if res_in is None or build_in is None:
        raise RuntimeError("Invalid 3MF (missing <resources> or <build>)")

    # Build object map (id -> element)
    objmap = {}
    for obj in res_in.findall("m:object", NS):
        oid = obj.get("id")
        if oid:
            objmap[oid] = obj

    # New model skeleton
    root_out = ET.Element(M("model"), {"unit": unit, "xml:lang": lang})
    # Preserve a few common metadata entries
    for md in root.findall("m:metadata", NS):
        # copy “safe” metadata
        name = md.get("name", "")
        if name in ("Application","Title","CreationDate","ModificationDate","BambuStudio:3mfVersion","DesignerUserId"):
            md2 = ET.SubElement(root_out, M("metadata"))
            md2.set("name", name)
            if md.text: md2.text = md.text

    res_out  = ET.SubElement(root_out, M("resources"))
    build_out = ET.SubElement(root_out, M("build"))

    next_id = 1
    def new_id():
        nonlocal next_id
        nid = str(next_id)
        next_id += 1
        return nid

    # Walk build items in order; flatten to identity-placed meshes
    for it in build_in.findall("m:item", NS):
        ref = it.get("objectid")
        if not ref or ref not in objmap:
            # skip broken reference
            continue
        base = objmap[ref]

        bi_tf = _parse_tf_attr(it.get("transform")) if it.get("transform") else IDENTITY_12[:]

        # Expand to leaf meshes with composed transforms
        for leaf_obj, tf in _gather_components_chain(res_in, base, bi_tf):
            mesh = _read_mesh(leaf_obj)
            if mesh is None:
                continue
            verts, tris = mesh
            baked = _apply_tf_to_vertices(tf, verts)

            # Create new mesh object with identity placement
            oid = new_id()
            obj_out = ET.SubElement(res_out, M("object"), {"id": oid, "type": "model"})
            # Optional: copy a title/name
            # If source object had a metadata Title, move it over (nice-to-have)
            _write_mesh(obj_out, baked, tris)

            ET.SubElement(build_out, M("item"), {"objectid": oid, "printable": "1"})

    return root_out

def main():
    ap = argparse.ArgumentParser(description="Bake a 3MF in memory (compose transforms into vertices) and optionally write a baked 3MF.")
    ap.add_argument("-i", "--input", required=True, help="Input .3mf")
    ap.add_argument("-o", "--output", required=True, help="Output baked .3mf")
    args = ap.parse_args()

    try:
        root = _read_model_root(args.input)
    except Exception as e:
        print(f"ERROR: failed reading input: {e}", file=sys.stderr)
        return 2

    needs, reasons = detect_needs_bake(root)
    print(f"[detect] needs_bake={'YES' if needs else 'NO'}; reasons={','.join(reasons) if reasons else 'none'}")

    try:
        baked_root = bake_root_in_memory(root)
    except Exception as e:
        print(f"ERROR: baking failed: {e}", file=sys.stderr)
        return 2

    try:
        _write_3mf(baked_root, args.output)
    except Exception as e:
        print(f"ERROR: writing baked 3MF failed: {e}", file=sys.stderr)
        return 2

    # Quick self-check on the baked output
    try:
        root2 = _read_model_root(args.output)
        needs2, reasons2 = detect_needs_bake(root2)
        print(f"[verify] baked file needs_bake={'YES' if needs2 else 'NO'}; reasons={','.join(reasons2) if reasons2 else 'none'}")
    except Exception as e:
        print(f"WARNING: baked file re-open verification failed: {e}", file=sys.stderr)

    print(f"[done] baked → {args.output}")
    return 0

if __name__ == "__main__":
    sys.exit(main())

