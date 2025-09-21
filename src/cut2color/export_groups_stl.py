#!/usr/bin/env python3
# export_groups_stl.py
#
# Step 6: Export per-part, per-group STLs as a placement smoke test.
# - Input: baked .3mf (identity build items, no <components>)
# - Groups: K=2 -> names use EVEN/ODD; K>2 -> suffixes _1.._K
# - Optional scene-level unions per group (all parts merged)
#
# Usage:
#   python3 export_groups_stl.py -i baked.3mf -H 0.20 -o out_stl --groups 2 --z0-mode auto-global --scene
#
import argparse, os, sys, zipfile, math, time, re
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
            for i in range(lo, hi+1): out[i] = kval
        else:
            out[a] = kval
    return out

def _label_for_group(g: int, K: int):
    if K == 2:
        return "EVEN" if g == 0 else "ODD"
    return f"{g+1}"

def _safe_name(s: str):
    out = []
    for ch in s:
        if ch.isalnum() or ch in ("_", "-", ".", "+"):
            out.append(ch)
        elif ch.isspace():
            out.append("_")
    return "".join(out) or "part"

def export_groups(input_path: str, outdir: str, H: float, z0_mode: str, z0_mm: Optional[float],
                  z_gap: float, xy_pad: float, groups_default: int, k_map: Optional[str],
                  engine_hint: Optional[str], emit_scene: bool):
    os.makedirs(outdir, exist_ok=True)
    root = _read_root(input_path)
    parts = _parts_from_baked(root)
    if not parts:
        raise RuntimeError("No mesh parts found (is the file baked?)")

    z0g, per_z0 = _choose_z0(parts, z0_mode, z0_mm)
    k_over = _parse_k_map(k_map)
    print(f"[info] parts={len(parts)}  z0-mode={z0_mode}  z0-global={z0g:.6f}  default-K={groups_default}")
    if k_over: print(f"[info] K overrides: {k_over}")

    # Accumulate scene-level unions per K bucket value
    scene_accums: Dict[int, Dict[int, List[trimesh.Trimesh]]] = {}

    t0 = time.time()
    for (idx, oid, name, mesh), z0 in zip(parts, per_z0):
        K = int(k_over.get(idx, groups_default))
        group_bins: List[List[trimesh.Trimesh]] = [[] for _ in range(K)]

        kmin, kmax = _compute_band_range(mesh, z0, H)
        for k in range(kmin, kmax+1):
            L = z0 + k*H
            U = z0 + (k+1)*H
            L_eff = L + z_gap*0.5
            U_eff = U - z_gap*0.5
            if U_eff <= L_eff: U_eff = L_eff + 1e-4

            slab = _slab_box(mesh, L_eff, U_eff, xy_pad)
            try:
                inter = trimesh.boolean.intersection([mesh, slab], engine=None if (engine_hint in (None, "", "auto")) else engine_hint)
                if isinstance(inter, list):
                    inter = trimesh.util.concatenate(inter) if inter else None
            except Exception:
                inter = None

            if isinstance(inter, trimesh.Trimesh) and inter.faces.size > 0:
                g = (k % K)
                group_bins[g].append(inter)

        # Export each group for this part
        base = _safe_name(name)
        for g in range(K):
            if not group_bins[g]:
                continue
            merged = trimesh.util.concatenate(group_bins[g]) if len(group_bins[g]) > 1 else group_bins[g][0]
            label = _label_for_group(g, K)
            if K == 2:
                out_name = f"{base}_{label}.stl"
            else:
                out_name = f"{base}_{label}.stl"  # label is 1..K
            out_path = os.path.join(outdir, out_name)
            merged.export(out_path)
            print(f"[write] {out_name}  tris={merged.faces.size}")

            if emit_scene:
                scene_accums.setdefault(K, {})
                scene_accums[K].setdefault(g, [])
                scene_accums[K][g].append(merged)

    # Scene-level unions (optional)
    if emit_scene and scene_accums:
        for K, groups in scene_accums.items():
            for g, meshes in groups.items():
                if not meshes: continue
                merged = trimesh.util.concatenate(meshes) if len(meshes) > 1 else meshes[0]
                label = _label_for_group(g, K)
                scene_name = f"SCENE_K{K}_{label}.stl"
                merged.export(os.path.join(outdir, scene_name))
                print(f"[scene] {scene_name}  tris={merged.faces.size}")

    dt = time.time() - t0
    print(f"[done] Exported grouped STLs to {outdir} in {dt:.2f}s")

def main():
    ap = argparse.ArgumentParser(description="Export per-part, per-group STLs (placement smoke test).")
    ap.add_argument("-i","--input", required=True, help="Baked input .3mf")
    ap.add_argument("-o","--outdir", required=True, help="Output directory for STLs")
    ap.add_argument("-H","--layer-height-mm", required=True, type=float)
    ap.add_argument("--z0-mode", choices=["auto-global","auto-local","fixed"], default="auto-global")
    ap.add_argument("--z0-mm",  type=float, default=None)
    ap.add_argument("--z-gap-mm", type=float, default=0.01)
    ap.add_argument("--xy-pad-mm", type=float, default=1.0)
    ap.add_argument("--groups", type=int, default=2, help="Default K")
    ap.add_argument("--k-map", type=str, default=None, help='Overrides like "2=3,5=3,8-10=4"')
    ap.add_argument("--engine", default="auto", help="trimesh boolean engine hint (auto(default), scad, blender, cork, trimesh, …)")
    ap.add_argument("--scene", action="store_true", help="Also export scene-level unions per group")
    args = ap.parse_args()

    if args.layer_height_mm <= 0:
        print("ERROR: -H must be > 0", file=sys.stderr); return 2
    os.makedirs(args.outdir, exist_ok=True)
    export_groups(args.input, args.outdir, args.layer_height_mm, args.z0_mode, args.z0_mm,
                  args.z_gap_mm, args.xy_pad_mm, args.groups, args.k_map, args.engine, args.scene)
    return 0

if __name__ == "__main__":
    sys.exit(main())

