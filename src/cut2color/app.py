#!/usr/bin/env python3
"""
cut2color.py — one-shot "bake if needed" + slice into repeating Z groups, output grouped 3MF.

Pipeline
  1) Detect if input 3MF needs baking (placements → vertices). If so, call bake_in_memory.py.
  2) Load baked 3MF (identity build items, no components).
  3) Slice each part into true-thickness Z bands and route bands into K repeating groups.
  4) Emit a 3MF with one object per (part,group) and optional scene-level group objects.

Notes
  - For K=2 we name outputs EVEN/ODD. For K>2 we suffix _1.._K.
  - Use --quick-mask for a fast preview (keeps faces whose 3 verts lie fully within the band).
  - For robust booleans, install OpenSCAD or Blender and use --engine scad|blender.
"""

import argparse, io, os, sys, zipfile, tempfile, subprocess, math, json, time
from typing import List, Tuple, Optional, Dict
import numpy as np
import trimesh
from xml.sax.saxutils import escape

# ---------- Needs-bake detector (reuse your script) ----------
try:
    from needs_bake import analyze_3mf as _analyze_3mf
except Exception:
    _analyze_3mf = None

def analyze_3mf(path: str) -> Dict:
    if _analyze_3mf is None:
        # Fallback: assume it needs baking if lib3mf readers or XML transforms likely present
        return {"needs_bake": True, "reasons": ["no-needs_bake-module"]}
    return _analyze_3mf(path)

def ensure_baked(input_path: str, verbose=False) -> str:
    info = analyze_3mf(input_path)
    if verbose:
        print(f"[detect] needs_bake={'YES' if info['needs_bake'] else 'NO'}; reasons={','.join(info.get('reasons', []) or ['none'])}")
    if not info.get("needs_bake", False):
        return input_path
    # Bake via your trusted script.
    tmpd = tempfile.mkdtemp(prefix="cut2color_")
    out_path = os.path.join(tmpd, "baked.3mf")
    baker = os.path.join(os.path.dirname(__file__), "bake_in_memory.py")
    cmd = [sys.executable, baker, "-i", input_path, "-o", out_path]
    if verbose:
        print(f"[orchestrate] baking via {os.path.basename(baker)} → {out_path}")
    subprocess.check_call(cmd)
    return out_path

# ---------- Geometry loading (baked 3MF) ----------
def load_baked_parts(path: str, verbose=False) -> List[Tuple[str, trimesh.Trimesh]]:
    """
    Loads a baked 3MF (no transforms/components) and returns [(name, mesh)].
    We use trimesh scene; if names unavailable, generate part_XXX.
    """
    scene = trimesh.load(path, force='scene')
    parts: List[Tuple[str, trimesh.Trimesh]] = []
    if isinstance(scene, trimesh.Scene) and scene.geometry:
        for idx, (gkey, geom) in enumerate(scene.geometry.items(), start=1):
            if not isinstance(geom, trimesh.Trimesh): continue
            m = geom.copy()
            # No scene transforms expected in baked files, but apply if present just in case
            try:
                # Map any nodes that reference this geometry key
                tf = np.eye(4)
                for node_name, g in getattr(scene.graph, "nodes_geometry", []):
                    if g == gkey:
                        tf = scene.graph.get_transform(node_name) @ tf
                if not np.allclose(tf, np.eye(4)):
                    m.apply_transform(tf)
            except Exception:
                pass
            name = f"part_{idx:03d}"
            parts.append((name, m))
    elif isinstance(scene, trimesh.Trimesh):
        parts.append(("part_001", scene.copy()))
    else:
        raise RuntimeError("Failed to load geometry from baked 3MF.")
    if verbose:
        print(f"[info] parts={len(parts)}  (baked)")
        for i,(n,m) in enumerate(parts, start=1):
            b = m.bounds
            print(f"[part] {i:02d} {n:24s} tris={m.faces.shape[0]:7d}  "
                  f"X[{b[0,0]:.3f},{b[1,0]:.3f}] Y[{b[0,1]:.3f},{b[1,1]:.3f}] Z[{b[0,2]:.3f},{b[1,2]:.3f}]")
    return parts

# ---------- Band math & helpers ----------
def compute_band_range(mesh: trimesh.Trimesh, z0: float, H: float):
    Zmin = float(mesh.bounds[0,2]); Zmax = float(mesh.bounds[1,2])
    k_min = math.floor((Zmin - z0) / H)
    k_max = math.ceil((Zmax - z0) / H) - 1
    return k_min, k_max, Zmin, Zmax

def slab_bounds_for_band(mesh: trimesh.Trimesh, k: int, z0: float, H: float,
                         z_gap: float, xy_pad: float):
    (xmin, ymin, _), (xmax, ymax, _) = mesh.bounds
    L = z0 + k*H; U = z0 + (k+1)*H
    L_eff = L + z_gap/2.0; U_eff = U - z_gap/2.0
    if U_eff <= L_eff: U_eff = L_eff + 0.05*H
    return xmin-xy_pad, xmax+xy_pad, ymin-xy_pad, ymax+xy_pad, L_eff, U_eff

def build_slab_box(slab):
    xmin,xmax,ymin,ymax,L_eff,U_eff = slab
    cx,cy,cz = 0.5*(xmin+xmax), 0.5*(ymin+ymax), 0.5*(L_eff+U_eff)
    ex,ey,ez = (xmax-xmin), (ymax-ymin), (U_eff-L_eff)
    box = trimesh.creation.box(extents=(ex,ey,ez))
    box.apply_translation([cx,cy,cz])
    return box

def k_to_group(k: int, K: int) -> int:
    # 0..K-1 cycling on absolute k
    return (k % K)

def parse_k_map(s: Optional[str]) -> Dict[int,int]:
    """
    Parse like "2=3,5=3,8-10=4" → {2:3,5:3,8:4,9:4,10:4}
    Part indices are 1-based (as presented to users).
    """
    out: Dict[int,int] = {}
    if not s: return out
    items = [t.strip() for t in s.split(",") if t.strip()]
    for it in items:
        if "=" not in it: continue
        left, right = it.split("=", 1)
        K = int(right)
        left = left.strip()
        if "-" in left:
            a,b = left.split("-",1)
            a,b = int(a), int(b)
            for p in range(a,b+1):
                out[p] = K
        else:
            out[int(left)] = K
    return out

# ---------- Fast face-filter ("quick-mask") ----------
def quick_mask_band(mesh: trimesh.Trimesh, L_eff: float, U_eff: float) -> Optional[trimesh.Trimesh]:
    z = mesh.vertices[:,2]
    faces = mesh.faces.view(np.ndarray)
    keep = (z[faces] >= L_eff).all(axis=1) & (z[faces] <= U_eff).all(axis=1)
    if not np.any(keep):
        return None
    f = faces[keep]
    # Keep all original vertices; remove unreferenced to shrink
    m = trimesh.Trimesh(vertices=mesh.vertices.view(np.ndarray), faces=f, process=False)
    m.remove_unreferenced_vertices()
    if m.faces.shape[0] == 0: return None
    return m

# ---------- Boolean band (robust) ----------
def boolean_band(mesh: trimesh.Trimesh, slab_box: trimesh.Trimesh, engine: Optional[str]=None) -> Optional[trimesh.Trimesh]:
    try:
        res = trimesh.boolean.intersection([mesh, slab_box], engine=(None if engine in (None, 'auto') else engine))
        if isinstance(res, trimesh.Trimesh): 
            return res
        if isinstance(res, list) and res:
            return trimesh.util.concatenate(res)
        return None
    except Exception:
        return None

# ---------- 3MF writer (grouped objects) ----------
XML_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"

def write_grouped_3mf(out_path: str,
                      grouped_objects: List[Tuple[str, trimesh.Trimesh]],
                      title: str = "cut2color grouped",
                      app: str = "cut2color-0.1") -> None:
    """
    grouped_objects: [(object_title, mesh)]
    Writes a minimal, valid 3MF zip with one mesh object per item & identity build.
    """
    # Build XML
    buf = io.StringIO()
    buf.write(f'<?xml version="1.0" encoding="UTF-8"?>\n')
    buf.write(f'<model xmlns="{XML_NS}" unit="millimeter" xml:lang="en-US">')
    buf.write(f'<metadata name="Title">{escape(title)}</metadata>')
    buf.write(f'<metadata name="Application">{escape(app)}</metadata>')
    buf.write('<resources>')
    next_id = 1
    id_map = []  # (id, name, mesh)
    for name, mesh in grouped_objects:
        verts = mesh.vertices.view(np.ndarray)
        faces = mesh.faces.view(np.ndarray)
        buf.write(f'<object id="{next_id}" type="model">')
        buf.write(f'<metadata name="Title">{escape(name)}</metadata>')
        buf.write('<mesh>')
        buf.write('<vertices>')
        # Write vertices
        # Use 6 decimal places; keep consistent formatting
        for v in verts:
            buf.write(f'<vertex x="{v[0]:.6f}" y="{v[1]:.6f}" z="{v[2]:.6f}"/>')
        buf.write('</vertices>')
        buf.write('<triangles>')
        for f in faces:
            buf.write(f'<triangle v1="{int(f[0])}" v2="{int(f[1])}" v3="{int(f[2])}"/>')
        buf.write('</triangles>')
        buf.write('</mesh></object>')
        id_map.append((next_id, name, mesh))
        next_id += 1
    buf.write('</resources><build>')
    for oid, name, _ in id_map:
        buf.write(f'<item objectid="{oid}" printable="1"/>')
    buf.write('</build></model>')
    xml_bytes = buf.getvalue().encode("utf-8")

    # Write 3MF zip
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
                   '</Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
                   'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>' 
                   '</Relationships>')
        z.writestr("3D/3dmodel.model", xml_bytes)

# ---------- Main cut2color pipeline ----------
def run_cut2color(input_path: str,
                  output_path: str,
                  H: float,
                  z0_mode: str,
                  z0_mm: Optional[float],
                  default_K: int,
                  k_map_str: Optional[str],
                  include_scene: bool,
                  engine: str,
                  z_gap: float,
                  xy_pad: float,
                  quick_mask: bool,
                  verbose: bool) -> None:

    if H <= 0:
        raise ValueError("Layer height H must be > 0.")

    work_3mf = ensure_baked(input_path, verbose=verbose)
    parts = load_baked_parts(work_3mf, verbose=verbose)

    # z0 selection
    zmins = [float(m.bounds[0,2]) for _,m in parts]
    if z0_mode == "auto-global":
        z0_global = float(np.min(zmins))
        per_part_z0 = [z0_global]*len(parts)
    elif z0_mode == "auto-local":
        z0_global = float(np.min(zmins))
        per_part_z0 = [float(z) for z in zmins]
    elif z0_mode == "fixed":
        if z0_mm is None:
            raise ValueError("--z0-mode fixed requires --z0-mm")
        z0_global = float(z0_mm)
        per_part_z0 = [z0_global]*len(parts)
    else:
        raise ValueError("Unknown z0-mode")

    if verbose:
        print(f"[info] parts={len(parts)}  z0-mode={z0_mode}  z0-global={z0_global:.6f}  default-K={default_K}")

    # Parse K overrides
    k_over = parse_k_map(k_map_str)
    if k_over and verbose:
        print(f"[info] K overrides: {k_over}")

    grouped_meshes: List[Tuple[str, trimesh.Trimesh]] = []
    scene_buckets: Dict[int, List[trimesh.Trimesh]] = {}

    for pidx, (pname, mesh) in enumerate(parts, start=1):
        z0 = per_part_z0[pidx-1]
        kmin,kmax,Zmin,Zmax = compute_band_range(mesh, z0, H)

        # Guardrail: if band count looks insane, switch to local z0 for this part
        nbands = max(0, kmax - kmin + 1)
        if nbands > 20000 and z0_mode == "auto-global":
            # recompute local
            z0 = float(Zmin)
            kmin,kmax,_,_ = compute_band_range(mesh, z0, H)
            nbands = max(0, kmax - kmin + 1)
            if verbose:
                print(f"[warn] {pname}: excessive bands ({nbands}); switching to local z0 (Zmin={Zmin:.3f})")

        K = int(k_over.get(pidx, default_K))
        if K < 1:
            K = 1

        if verbose:
            print(f"[bands] {pname:20s}  K={K}  k[{kmin}..{kmax}]  Z[{Zmin:.3f}..{Zmax:.3f}]")

        # Prepare K group collectors
        part_groups: Dict[int, List[trimesh.Trimesh]] = {g: [] for g in range(K)}

        # Iterate bands sparsely: only test bands that plausibly intersect
        t0 = time.time()
        nonempty = 0
        for k in range(kmin, kmax+1):
            # Effective slab bounds for this band
            slab = slab_bounds_for_band(mesh, k, z0, H, z_gap, xy_pad)
            L_eff, U_eff = slab[4], slab[5]

            # Quick reject using Z bounds
            if U_eff < Zmin or L_eff > Zmax:
                continue

            # Build band mesh
            if quick_mask:
                band = quick_mask_band(mesh, L_eff, U_eff)
            else:
                box = build_slab_box(slab)
                band = boolean_band(mesh, box, engine=engine)

            if band is None or band.faces.shape[0] == 0:
                continue

            nonempty += 1
            g = k_to_group(k, K)
            part_groups[g].append(band)

        if verbose:
            print(f"[bands] {pname}  nonempty-bands={nonempty}  dt={time.time()-t0:.2f}s")

        # Concatenate per-group meshes and store for output + scene
        for g in range(K):
            if not part_groups[g]:
                continue
            if len(part_groups[g]) == 1:
                merged = part_groups[g][0]
            else:
                merged = trimesh.util.concatenate(part_groups[g])

            if K == 2:
                suffix = "EVEN" if g == 0 else "ODD"
            else:
                suffix = f"{g+1}"
            oname = f"{pname}_{suffix}"
            grouped_meshes.append((oname, merged))

            # scene bucket
            scene_buckets.setdefault(K, [])
            scene_buckets[K].append(merged)

    # Optional scene-level grouped objects
    if include_scene:
        for K, meshes in scene_buckets.items():
            if not meshes:
                continue
            merged = trimesh.util.concatenate(meshes) if len(meshes) > 1 else meshes[0]
            if K == 2:
                # Split EVEN/ODD again? No—scene already collected per group inside loop above.
                # We built only one list per K; better to build separate per-g groups:
                pass

        # Recreate proper per-K scene groups
        # Build per-K per-group lists
        k_scene_per_group: Dict[int, Dict[int, List[trimesh.Trimesh]]] = {}
        for (name, m) in grouped_meshes:
            # name pattern: part_XXX_SUFFIX ; infer K from suffix if present in scene_buckets keys
            # Safer: reconstruct by scanning original K logic. We'll just rebuild per K per group by suffix.
            if name.endswith("EVEN"):
                Kc = 2; g = 0
            elif name.endswith("ODD"):
                Kc = 2; g = 1
            else:
                # trailing _N
                if "_" in name:
                    tail = name.rsplit("_",1)[-1]
                    if tail.isdigit():
                        g = int(tail) - 1
                        # find K that contains a group index g
                        # This is ambiguous if multiple K present; assume max K >= g+1 that exists from k_over/defaults
                        # Simplify: collect under K=max(g+1,2) if unknown; but we can detect from other names
                        Kc = None
                        for candK in scene_buckets.keys():
                            if g < candK:
                                Kc = candK; break
                        if Kc is None:
                            Kc = g+1
                    else:
                        continue
                else:
                    continue
            k_scene_per_group.setdefault(Kc, {}).setdefault(g, []).append(m)

        # Emit one scene object per (K,g)
        for Kc, groups in k_scene_per_group.items():
            for g, lst in groups.items():
                if not lst: continue
                merged = trimesh.util.concatenate(lst) if len(lst) > 1 else lst[0]
                if Kc == 2:
                    suffix = "EVEN" if g == 0 else "ODD"
                    sname = f"SCENE_K2_{suffix}"
                else:
                    sname = f"SCENE_K{Kc}_{g+1}"
                grouped_meshes.append((sname, merged))

    # Write 3MF
    write_grouped_3mf(output_path, grouped_meshes, title=os.path.splitext(os.path.basename(output_path))[0])
    if verbose:
        tot_tris = sum(m.faces.shape[0] for _,m in grouped_meshes)
        print(f"[done] wrote {output_path} with {len(grouped_meshes)} object(s), total tris={tot_tris}")

# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser(description="cut2color — bake if needed, then slice 3MF parts into repeating Z groups and write grouped 3MF")
    ap.add_argument("-i","--input", required=True, help="input .3mf")
    ap.add_argument("-o","--output", required=True, help="output grouped .3mf")
    ap.add_argument("-H","--layer-height-mm", required=True, type=float)
    ap.add_argument("--z0-mode", choices=["auto-global","auto-local","fixed"], default="auto-global")
    ap.add_argument("--z0-mm", type=float, default=None, help="required if --z0-mode fixed")
    ap.add_argument("--groups", type=int, default=2, help="default K (repeating groups, 2=EVEN/ODD)")
    ap.add_argument("--k-map", type=str, default=None, help='per-part overrides, e.g. "2=3,5=3,8-10=4" (1-based part indices)')
    ap.add_argument("--include-scene", action="store_true", help="add scene-level grouped objects")
    ap.add_argument("--engine", type=str, default="auto",
                    help="boolean engine: auto|scad|blender|igl|manifold|cork|carve")
    ap.add_argument("--z-gap-mm", type=float, default=0.01, help="shrink band by this gap at top/bottom")
    ap.add_argument("--xy-pad-mm", type=float, default=1.0, help="expand slab XY to ensure full coverage")
    ap.add_argument("--quick-mask", action="store_true",
                    help="FAST preview: keep faces fully inside band (no triangle splitting/caps)")
    ap.add_argument("-v","--verbose", action="store_true")
    args = ap.parse_args()

    run_cut2color(args.input, args.output, args.layer_height_mm,
                  args.z0_mode, args.z0_mm, args.groups, args.k_map,
                  args.include_scene, args.engine, args.z_gap_mm,
                  args.xy_pad_mm, args.quick_mask, args.verbose)

if __name__ == "__main__":
    sys.exit(main())

