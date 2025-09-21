#!/usr/bin/env python3
# parts_report.py
#
# Step 3: Extract world-space parts from a baked 3MF and report:
#   idx, name, object_id, triangles, bounds (xmin xmax ymin ymax zmin zmax)
#
# Usage:
#   python3 parts_report.py -i benchy_cutup_baked_inmem.3mf
#   python3 parts_report.py -i benchy_cutup_baked_inmem.3mf --csv parts_report.csv
#
# Notes:
# - Expects a *baked* file (identity build transforms, no components).
# - Name resolution order: build item attributes (name/partnumber) → object Title → fallback.

import argparse, csv, sys, zipfile
import xml.etree.ElementTree as ET

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
NS = {"m": CORE_NS}
M = lambda tag: f"{{{CORE_NS}}}{tag}"

def _find_model_xml(zf):
    for n in zf.namelist():
        if n.startswith("3D/") and n.lower().endswith(".model"):
            return n
    return None

def _read_root(path):
    with zipfile.ZipFile(path, "r") as zf:
        name = _find_model_xml(zf)
        if not name:
            raise RuntimeError("3D/*.model not found in .3mf")
        xml = zf.read(name)
    return ET.fromstring(xml)

def _read_mesh(obj_elem):
    mesh = obj_elem.find("m:mesh", NS)
    if mesh is None:
        return None, None
    vs = mesh.find("m:vertices", NS)
    ts = mesh.find("m:triangles", NS)
    if vs is None or ts is None:
        return None, None
    verts = []
    for v in vs.findall("m:vertex", NS):
        x = float(v.get("x", "0")); y = float(v.get("y", "0")); z = float(v.get("z", "0"))
        verts.append((x,y,z))
    tris = []
    for t in ts.findall("m:triangle", NS):
        v1 = int(t.get("v1")); v2 = int(t.get("v2")); v3 = int(t.get("v3"))
        tris.append((v1,v2,v3))
    return verts, tris

def _bounds(verts):
    if not verts:
        return (0,0,0,0,0,0)
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))

def _get_object_title(obj_elem):
    # Typical 3MF uses <metadata name="Title"> under object, but not guaranteed.
    for md in obj_elem.findall("m:metadata", NS):
        if md.get("name") == "Title" and (md.text or "").strip():
            return md.text.strip()
    return ""

def _first_nonempty(*vals):
    for v in vals:
        if v and str(v).strip():
            return str(v).strip()
    return ""

def _sanitize_name(name, fallback):
    # Lightweight: keep alnum, _-., replace spaces with _
    if not name:
        name = fallback
    clean = []
    for ch in name:
        if ch.isalnum() or ch in ("_", "-", ".", "+"):
            clean.append(ch)
        elif ch.isspace():
            clean.append("_")
        else:
            # drop other characters
            pass
    out = "".join(clean)
    return out if out else fallback

def main():
    ap = argparse.ArgumentParser(description="Report parts (world-space) from a baked 3MF.")
    ap.add_argument("-i","--input", required=True, help="Input baked .3mf")
    ap.add_argument("--csv", help="Optional path to write CSV report")
    args = ap.parse_args()

    try:
        root = _read_root(args.input)
    except Exception as e:
        print(f"ERROR: cannot open {args.input}: {e}", file=sys.stderr)
        return 2

    build = root.find("m:build", NS)
    res   = root.find("m:resources", NS)
    if build is None or res is None:
        print("ERROR: invalid 3MF (missing <build> or <resources>)", file=sys.stderr)
        return 2

    rows = []
    items = build.findall("m:item", NS)
    objmap = {o.get("id"): o for o in res.findall("m:object", NS)}

    print(f"[info] build_items={len(items)}  objects={len(objmap)}")
    print("idx  object_id  name                         tris     xmin       xmax       ymin       ymax       zmin       zmax")
    print("---- ---------- ---------------------------- -------- ---------- ---------- ---------- ---------- ---------- ----------")

    for idx, it in enumerate(items, start=1):
        oid = it.get("objectid")
        obj = objmap.get(oid)
        if obj is None:
            print(f"{idx:>3}  {oid or 'NA':>10} (missing object)"); continue

        verts, tris = _read_mesh(obj)
        if verts is None or tris is None:
            print(f"{idx:>3}  {oid:>10} (no mesh)"); continue

        # name resolution: item[name|partnumber] → object Title → fallback
        nm = _first_nonempty(it.get("name"), it.get("partnumber"), _get_object_title(obj))
        nm = _sanitize_name(nm, f"part_{idx:03d}")

        xmin,xmax,ymin,ymax,zmin,zmax = _bounds(verts)
        tri_count = len(tris)

        print(f"{idx:>3}  {oid:>10} {nm:>28} {tri_count:>8} {xmin:>10.3f} {xmax:>10.3f} {ymin:>10.3f} {ymax:>10.3f} {zmin:>10.3f} {zmax:>10.3f}")
        rows.append({
            "idx": idx, "object_id": oid, "name": nm, "triangles": tri_count,
            "xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax, "zmin": zmin, "zmax": zmax
        })

    if args.csv:
        try:
            with open(args.csv, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["idx","object_id","name","triangles","xmin","xmax","ymin","ymax","zmin","zmax"])
                w.writeheader()
                for r in rows: w.writerow(r)
            print(f"[info] wrote CSV: {args.csv}")
        except Exception as e:
            print(f"WARNING: failed writing CSV: {e}", file=sys.stderr)

    return 0

if __name__ == "__main__":
    sys.exit(main())

