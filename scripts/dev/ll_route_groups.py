#!/usr/bin/env python3
# grouping_router.py
#
# Step 5: Route non-empty Z bands into K repeating groups per part (no exports).
# - Assumes input .3mf is baked (identity build transforms, no <components>).
# - Supports default K plus per-part overrides via --k-map "2=3,5=3,8-10=4".
#
# Usage:
#   python3 grouping_router.py -i baked.3mf -H 0.20 --groups 2 --z0-mode auto-global
#   python3 grouping_router.py -i baked.3mf -H 0.20 --groups 2 --k-map "2=3,5=3,8-10=4"
#
import argparse, sys, zipfile, math, re, time, json
import xml.etree.ElementTree as ET
from typing import List, Tuple, Optional, Dict
import numpy as np
import trimesh

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
        return None
    vs = mesh.find("m:vertices", NS)
    ts = mesh.find("m:triangles", NS)
    if vs is None or ts is None:
        return None
    verts = []
    for v in vs.findall("m:vertex", NS):
        x = float(v.get("x", "0")); y = float(v.get("y", "0")); z = float(v.get("z", "0"))
        verts.append([x,y,z])
    faces = []
    for t in ts.findall("m:triangle", NS):
        v1 = int(t.get("v1")); v2 = int(t.get("v2")); v3 = int(t.get("v3"))
        faces.append([v1,v2,v3])
    return np.array(verts, dtype=np.float64), np.array(faces, dtype=np.int64)

def _parts_from_baked(root):
    build = root.find("m:build", NS)
    res   = root.find("m:resources", NS)
    if build is None or res is None:
        raise RuntimeError("Invalid 3MF (missing <build> or <resources>)")
    objmap = {o.get("id"): o for o in res.findall("m:object", NS)}
    parts = []
    for idx, it in enumerate(build.findall("m:item", NS), start=1):
        oid = it.get("objectid")
        o = objmap.get(oid)
        if o is None: continue
        data = _read_mesh(o)
        if data is None: continue
        V, F = data
        if len(V) == 0 or len(F) == 0: continue
        tm = trimesh.Trimesh(vertices=V, faces=F, process=False)
        name = it.get("name") or it.get("partnumber") or f"part_{idx:03d}"
        parts.append((idx, oid, name, tm))
    return parts

def _choose_z0(parts, mode: str, fixed: Optional[float]):
    zmins = [float(p[3].bounds[0,2]) for p in parts]
    if mode == "auto-global":
        z0 = float(np.min(zmins)); return z0, [z0]*len(parts)
    if mode == "auto-local":
        return float(np.min(zmins)), [float(z) for z in zmins]
    if mode == "fixed":
        if fixed is None:
            raise ValueError("--z0-mode fixed requires --z0-mm")
        return float(fixed), [float(fixed)]*len(parts)
    raise ValueError("bad --z0-mode")

def _compute_band_range(mesh: trimesh.Trimesh, z0: float, H: float):
    Zmin = float(mesh.bounds[0,2]); Zmax = float(mesh.bounds[1,2])
    kmin = math.floor((Zmin - z0) / H)
    kmax = math.ceil((Zmax - z0) / H) - 1
    return kmin, kmax

def _slab_xy_bounds(mesh: trimesh.Trimesh, xy_pad: float):
    (xmin,ymin,_),(xmax,ymax,_) = mesh.bounds
    return xmin-xy_pad, xmax+xy_pad, ymin-xy_pad, ymax+xy_pad

def _slab_box(mesh: trimesh.Trimesh, L: float, U: float, xy_pad: float):
    xmin,xmax,ymin,ymax = _slab_xy_bounds(mesh, xy_pad)
    cx,cy,cz = 0.5*(xmin+xmax), 0.5*(ymin+ymax), 0.5*(L+U)
    ex,ey,ez = (xmax-xmin), (ymax-ymin), (U-L)
    if ez <= 0: ez = 1e-6
    box = trimesh.creation.box(extents=(ex,ey,ez))
    box.apply_translation([cx,cy,cz])
    return box

def _parse_k_map(s: Optional[str]) -> Dict[int,int]:
    """
    Syntax: "2=3,5=3,8-10=4"  → part indices 2 and 5 use K=3; parts 8..10 use K=4.
    Indices are 1-based, as shown in reports. Later entries override earlier ones.
    """
    out: Dict[int,int] = {}
    if not s: return out
    for token in s.split(","):
        token = token.strip()
        if not token: continue
        m = re.match(r"^(\d+)(?:-(\d+))?=(\d+)$", token)
        if not m:
            raise ValueError(f"bad k-map entry: {token}")
        a, b, kval = m.group(1), m.group(2), m.group(3)
        a = int(a); kval = int(kval)
        if b:
            b = int(b)
            lo, hi = min(a,b), max(a,b)
            for i in range(lo, hi+1):
                out[i] = kval
        else:
            out[a] = kval
    return out

def main():
    ap = argparse.ArgumentParser(description="Route non-empty bands into K repeating groups per part (no exports).")
    ap.add_argument("-i","--input", required=True)
    ap.add_argument("-H","--layer-height-mm", required=True, type=float)
    ap.add_argument("--z0-mode", choices=["auto-global","auto-local","fixed"], default="auto-global")
    ap.add_argument("--z0-mm", type=float, default=None)
    ap.add_argument("--z-gap-mm", type=float, default=0.01)
    ap.add_argument("--xy-pad-mm", type=float, default=1.0)
    ap.add_argument("--log-every", type=int, default=25)
    ap.add_argument("--groups", type=int, default=2, help="Default K (2=EVEN/ODD)")
    ap.add_argument("--k-map", type=str, default=None, help='Overrides like "2=3,5=3,8-10=4"')
    ap.add_argument("--engine", default="auto", help="trimesh boolean engine hint")
    ap.add_argument("--manifest", type=str, default=None, help="Optional JSON manifest to write")
    args = ap.parse_args()

    if args.layer_height_mm <= 0:
        print("ERROR: -H must be > 0", file=sys.stderr); return 2
    if args.groups <= 0:
        print("ERROR: --groups must be >= 1", file=sys.stderr); return 2

    try:
        root = _read_root(args.input)
    except Exception as e:
        print(f"ERROR: can't open model: {e}", file=sys.stderr); return 2

    parts = _parts_from_baked(root)
    if not parts:
        print("ERROR: no mesh parts found (is the file baked?)", file=sys.stderr); return 2

    z0g, per_z0 = _choose_z0(parts, args.z0_mode, args.z0_mm)
    k_over = _parse_k_map(args.k_map)

    print(f"[info] parts={len(parts)}  z0-mode={args.z0_mode}  z0-global={z0g:.6f}  default-K={args.groups}")
    if k_over:
        print(f"[info] K overrides: {k_over}")

    print("idx  name                       K   groups(non-empty tris)             total_nonempty  k[first..last]")

    manifest = {
        "input": args.input,
        "H": args.layer_height_mm,
        "z0_mode": args.z0_mode,
        "z0_mm": args.z0_mm,
        "z0_global": z0g,
        "default_K": args.groups,
        "k_overrides": k_over,
        "parts": []
    }

    t0 = time.time()
    for (idx, oid, name, mesh), z0 in zip(parts, per_z0):
        K = int(k_over.get(idx, args.groups))
        kmin, kmax = _compute_band_range(mesh, z0, args.layer_height_mm)
        nbands = max(0, (kmax - kmin + 1))

        # accumulate per-group triangle counts for non-empty bands
        group_tris = [0]*K
        nonempty = 0
        first_k = None; last_k = None

        for i, k in enumerate(range(kmin, kmax+1), start=1):
            L = z0 + k*args.layer_height_mm
            U = z0 + (k+1)*args.layer_height_mm
            L_eff = L + args.z_gap_mm*0.5
            U_eff = U - args.z_gap_mm*0.5
            if U_eff <= L_eff: U_eff = L_eff + 1e-4

            slab = _slab_box(mesh, L_eff, U_eff, args.xy_pad_mm)
            try:
                inter = trimesh.boolean.intersection([mesh, slab], engine=None if args.engine=="auto" else args.engine)
                if isinstance(inter, list):
                    inter = trimesh.util.concatenate(inter) if inter else None
            except Exception:
                inter = None

            if isinstance(inter, trimesh.Trimesh) and inter.faces.size > 0:
                nonempty += 1
                first_k = k if first_k is None else first_k
                last_k = k
                g = (k % K)
                group_tris[g] += int(inter.faces.size)

            if (i % max(1,args.log_every) == 0) or k in (kmin, kmax):
                tri_count = (int(inter.faces.size) if isinstance(inter,trimesh.Trimesh) else 0)
                print(f"  part{idx:02d} k={k:6d} -> g={k%K}  [{L_eff:.3f},{U_eff:.3f}]  {'ok' if tri_count>0 else 'empty':6s}  tris={tri_count:6d}")

        # pretty group display
        if K == 2:
            labels = ["EVEN","ODD "]
        else:
            labels = [f"{i+1}" for i in range(K)]
        grp_disp = " ".join([f"{labels[i]}:{group_tris[i]}" for i in range(K)])

        print(f"{idx:>3}  {name:>25} {K:3d}  {grp_disp:35s}  {nonempty:14d}  {str(first_k) if first_k is not None else '-'}..{str(last_k) if last_k is not None else '-'}")

        manifest["parts"].append({
            "idx": idx,
            "object_id": oid,
            "name": name,
            "K": K,
            "nonempty": nonempty,
            "k_first": first_k,
            "k_last": last_k,
            "group_tris": group_tris
        })

    dt = time.time() - t0
    print(f"[summary] routed bands for {len(parts)} parts in {dt:.2f}s")

    if args.manifest:
        try:
            with open(args.manifest, "w") as f:
                json.dump(manifest, f, indent=2)
            print(f"[info] wrote manifest: {args.manifest}")
        except Exception as e:
            print(f"WARNING: failed to write manifest: {e}", file=sys.stderr)

    return 0

if __name__ == "__main__":
    sys.exit(main())

