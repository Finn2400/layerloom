#!/usr/bin/env python3
"""
Build a spherical CMYKW gamut object as a 3MF assembly.

The sphere is parameterized from the token layer counts directly:
  - relative C/M/Y balance determines longitude around the equator
  - W/K balance determines latitude toward the north/south poles
  - pure W and K land at the poles, while pure C/Y/M land on the equator

Each region is exported as a watertight hollow-shell patch between an outer
sphere and an inner offset sphere. The resulting 3MF is intended to load cleanly
into LayerLoom and auto-assign from per-part `source_hex` metadata.
"""

from __future__ import annotations

import argparse
import colorsys
import csv
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import trimesh as tm

from layer_perm_3mf_assembly_3_circle import write_3mf_as_assembly
from palette_utils import corrected_hex, load_registry, resolve_preset_parameters, token_is_supported


TOKEN_ORDER = ("C", "M", "Y", "K", "W")
CMY_CENTROID = (0.5, math.sqrt(3.0) / 6.0)


@dataclass(frozen=True)
class SphereEntry:
    token: str
    source_hex: str
    fractions: Dict[str, float]
    site_xyz: Tuple[float, float, float]
    site_lat_deg: float
    site_lon_deg: float


def _pure_fractions(token: str) -> Dict[str, float]:
    token = token.upper()
    return {key: 1.0 if key == token else 0.0 for key in TOKEN_ORDER}


def _finalize_region_mesh(mesh: tm.Trimesh, label: str) -> tm.Trimesh:
    if len(mesh.faces) > 0:
        mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()
    try:
        tm.repair.fix_winding(mesh)
    except Exception:
        pass
    try:
        tm.repair.fix_normals(mesh)
    except Exception:
        pass

    if mesh.is_watertight and not mesh.is_volume:
        mesh.invert()
        try:
            tm.repair.fix_normals(mesh)
        except Exception:
            pass

    if mesh.is_watertight and not mesh.is_winding_consistent:
        try:
            tm.repair.fix_winding(mesh)
        except Exception:
            pass
        try:
            tm.repair.fix_normals(mesh)
        except Exception:
            pass

    if not mesh.is_watertight or not mesh.is_volume or not mesh.is_winding_consistent:
        raise ValueError(
            f"Generated invalid spherical region mesh for {label}: "
            f"watertight={mesh.is_watertight} "
            f"is_volume={mesh.is_volume} "
            f"winding={mesh.is_winding_consistent}"
        )
    return mesh


def _registry_entries(
    *,
    max_height: int,
    max_run: int,
    distinct_lte: int,
    registry_path: Path,
) -> List[Dict[str, object]]:
    registry = load_registry(registry_path)
    seen: Dict[Tuple[float, float, float, float, float], Tuple[Tuple[int, str], Dict[str, object]]] = {}

    for h_s, levels in registry.items():
        try:
            height = int(h_s)
        except Exception:
            continue
        if height > max_height:
            continue

        for r_s, meta in levels.items():
            try:
                run = int(r_s)
            except Exception:
                continue
            if run > max_run:
                continue

            colors: Iterable[Dict[str, object]] = meta.get("colors", [])
            for entry in colors:
                token = str(entry.get("token") or "").lower()
                if not token or not token_is_supported(token):
                    continue
                if int(entry.get("distinct_colors", 99)) > distinct_lte:
                    continue

                raw_fr = entry.get("fractions", {})
                fr = {key: float(raw_fr.get(key, 0.0)) for key in TOKEN_ORDER}
                key = tuple(round(fr[k], 6) for k in TOKEN_ORDER)
                candidate = ((len(token), token), {"token": token, "fractions": fr})
                if key not in seen or candidate[0] < seen[key][0]:
                    seen[key] = candidate

    out = [item[1] for item in seen.values()]
    out.sort(key=lambda row: (row["token"], tuple(row["fractions"][k] for k in TOKEN_ORDER)))
    return out


def _fallback_cmy_hue(fractions: Dict[str, float]) -> float:
    c = float(fractions["C"])
    m = float(fractions["M"])
    y = float(fractions["Y"])
    total = c + m + y
    if total <= 1e-9:
        return 0.0
    cn, mn, yn = c / total, m / total, y / total
    x = (mn + 0.5 * yn) - CMY_CENTROID[0]
    yy = (math.sqrt(3.0) / 2.0 * yn) - CMY_CENTROID[1]
    if abs(x) < 1e-9 and abs(yy) < 1e-9:
        return 0.0
    return (math.atan2(yy, x) / (2.0 * math.pi)) % 1.0


def _fractions_to_count_site(fractions: Dict[str, float]) -> Tuple[Tuple[float, float, float], float, float]:
    c = float(fractions["C"])
    m = float(fractions["M"])
    y = float(fractions["Y"])
    k = float(fractions["K"])
    w = float(fractions["W"])

    cmy_total = c + m + y
    if cmy_total > 1e-9:
        cn, mn, yn = c / cmy_total, m / cmy_total, y / cmy_total
        dx = (mn + 0.5 * yn) - CMY_CENTROID[0]
        dy = (math.sqrt(3.0) / 2.0 * yn) - CMY_CENTROID[1]
        if abs(dx) < 1e-12 and abs(dy) < 1e-12:
            ang = 0.0
        else:
            ang = math.atan2(dy, dx)
    else:
        ang = 0.0

    z = w - k
    r_xy = cmy_total
    if abs(r_xy) < 1e-12 and abs(z) < 1e-12:
        r_xy = 1e-6

    x = r_xy * math.cos(ang)
    yv = r_xy * math.sin(ang)
    n = math.sqrt(x * x + yv * yv + z * z)
    xyz = (x / n, yv / n, z / n)
    lat = math.degrees(math.asin(xyz[2]))
    lon = (math.degrees(math.atan2(xyz[1], xyz[0])) + 360.0) % 360.0
    return xyz, lat, lon


def _entry_to_site(entry: Dict[str, object]) -> SphereEntry:
    token = str(entry["token"]).lower()
    fractions = {key: float(entry["fractions"][key]) for key in TOKEN_ORDER}
    source_hex = corrected_hex(token)
    xyz, lat_deg, lon_deg = _fractions_to_count_site(fractions)
    return SphereEntry(
        token=token,
        source_hex=source_hex,
        fractions=fractions,
        site_xyz=xyz,
        site_lat_deg=lat_deg,
        site_lon_deg=lon_deg,
    )


def build_pole_sites() -> List[SphereEntry]:
    sites = [
        _entry_to_site({"token": "w", "fractions": _pure_fractions("W")}),
        _entry_to_site({"token": "k", "fractions": _pure_fractions("K")}),
        _entry_to_site({"token": "c", "fractions": _pure_fractions("C")}),
        _entry_to_site({"token": "y", "fractions": _pure_fractions("Y")}),
        _entry_to_site({"token": "m", "fractions": _pure_fractions("M")}),
    ]
    sites.sort(key=lambda row: (-row.site_lat_deg, row.site_lon_deg, row.token))
    return sites


def _site_key(site: SphereEntry) -> Tuple[float, float, float]:
    return tuple(round(v, 8) for v in site.site_xyz)


def _dedupe_sites_by_position(
    sites: Sequence[SphereEntry],
    *,
    preserve_tokens: Optional[set] = None,
) -> List[SphereEntry]:
    preserve = {str(tok).lower() for tok in (preserve_tokens or set())}
    chosen: Dict[Tuple[float, float, float], SphereEntry] = {}
    for site in sites:
        key = _site_key(site)
        current = chosen.get(key)
        if current is None:
            chosen[key] = site
            continue
        current_is_anchor = current.token in preserve
        site_is_anchor = site.token in preserve
        if current_is_anchor and not site_is_anchor:
            continue
        if site_is_anchor and not current_is_anchor:
            chosen[key] = site
            continue
        if (len(site.token), site.token) < (len(current.token), current.token):
            chosen[key] = site
    out = list(chosen.values())
    out.sort(key=lambda row: (-row.site_lat_deg, row.site_lon_deg, row.token))
    return out


def build_anchor_inclusive_sites(
    *,
    max_height: int,
    max_run: int,
    distinct_lte: int,
    registry_path: Path,
) -> List[SphereEntry]:
    anchor_sites = build_pole_sites()
    anchors = {site.token: site for site in anchor_sites}
    entries = _registry_entries(
        max_height=max_height,
        max_run=max_run,
        distinct_lte=distinct_lte,
        registry_path=registry_path,
    )
    sites = list(anchor_sites)
    for entry in entries:
        site = _entry_to_site(entry)
        if site.token not in anchors:
            sites.append(site)
    return _dedupe_sites_by_position(sites, preserve_tokens={"w", "k", "c", "y", "m"})


def build_sites(
    *,
    site_mode: str,
    max_height: int,
    max_run: int,
    distinct_lte: int,
    registry_path: Path,
) -> List[SphereEntry]:
    if site_mode == "poles-only":
        return build_pole_sites()
    if site_mode == "palette-anchors":
        return build_anchor_inclusive_sites(
            max_height=max_height,
            max_run=max_run,
            distinct_lte=distinct_lte,
            registry_path=registry_path,
        )

    entries = _registry_entries(
        max_height=max_height,
        max_run=max_run,
        distinct_lte=distinct_lte,
        registry_path=registry_path,
    )
    return _dedupe_sites_by_position([_entry_to_site(entry) for entry in entries])


def _face_assignments(surface: tm.Trimesh, sites: Sequence[SphereEntry]) -> np.ndarray:
    centers = np.array(surface.triangles_center, dtype=np.float64, copy=True)
    norms = np.linalg.norm(centers, axis=1, keepdims=True)
    centers /= np.maximum(norms, 1e-12)

    site_xyz = np.asarray([site.site_xyz for site in sites], dtype=np.float64)
    assignment = np.empty(len(centers), dtype=np.int32)
    chunk = 4096
    for start in range(0, len(centers), chunk):
        stop = min(start + chunk, len(centers))
        dots = centers[start:stop] @ site_xyz.T
        assignment[start:stop] = np.argmax(dots, axis=1)
    return assignment


def _build_region_mesh(
    surface: tm.Trimesh,
    face_indices: np.ndarray,
    face_neighbors: Sequence[Sequence[int]],
    shell_scale: float,
    region_dir: np.ndarray,
    label: str,
) -> tm.Trimesh:
    remaining = set(int(idx) for idx in face_indices.tolist())
    components: List[List[int]] = []
    while remaining:
        start = remaining.pop()
        stack = [start]
        comp = [start]
        while stack:
            current = stack.pop()
            for nbr in face_neighbors[current]:
                if nbr in remaining:
                    remaining.remove(nbr)
                    stack.append(nbr)
                    comp.append(nbr)
        components.append(comp)

    component_meshes: List[tm.Trimesh] = []
    for comp in components:
        outer_faces = np.asarray(surface.faces[np.asarray(comp, dtype=np.int64)], dtype=np.int64)
        edge_counts: Dict[Tuple[int, int], int] = {}
        for face in outer_faces:
            a, b, c = int(face[0]), int(face[1]), int(face[2])
            for u, v in ((a, b), (b, c), (c, a)):
                key = (u, v) if u < v else (v, u)
                edge_counts[key] = edge_counts.get(key, 0) + 1

        boundary_edges = [edge for edge, count in edge_counts.items() if count == 1]
        used = sorted({int(v) for face in outer_faces for v in face})
        global_to_local = {vid: idx for idx, vid in enumerate(used)}

        outer_vertices = np.asarray(surface.vertices[used], dtype=np.float64)
        inner_vertices = outer_vertices * shell_scale
        outer_count = len(outer_vertices)
        local_vertices = np.vstack([outer_vertices, inner_vertices])

        local_faces: List[List[int]] = [[global_to_local[int(v)] for v in face] for face in outer_faces]
        local_faces.extend(
            [
                [
                    outer_count + global_to_local[int(face[0])],
                    outer_count + global_to_local[int(face[2])],
                    outer_count + global_to_local[int(face[1])],
                ]
                for face in outer_faces
            ]
        )

        for u, v in boundary_edges:
            lu = global_to_local[u]
            lv = global_to_local[v]
            iu = outer_count + lu
            iv = outer_count + lv
            p_u = outer_vertices[lu]
            p_v = outer_vertices[lv]
            normal = np.cross(p_u, p_v)
            if float(np.dot(normal, region_dir)) >= 0.0:
                local_faces.append([lu, lv, iv])
                local_faces.append([lu, iv, iu])
            else:
                local_faces.append([lu, iu, iv])
                local_faces.append([lu, iv, lv])

        component_meshes.append(
            tm.Trimesh(vertices=local_vertices, faces=np.asarray(local_faces, dtype=np.int64), process=False)
        )

    finalized_components = [
        _finalize_region_mesh(component, f"{label}__part{idx:02d}")
        for idx, component in enumerate(component_meshes, start=1)
    ]
    mesh = finalized_components[0] if len(finalized_components) == 1 else tm.util.concatenate(finalized_components)
    if not mesh.is_watertight or not mesh.is_volume:
        raise ValueError(
            f"Combined spherical region mesh for {label} is invalid after component assembly: "
            f"watertight={mesh.is_watertight} is_volume={mesh.is_volume}"
        )
    return _finalize_region_mesh(mesh, label)


def write_debug_png(out_path: str, sites: Sequence[SphereEntry]) -> None:
    fig = plt.figure(figsize=(7.2, 6.4))
    ax = fig.add_subplot(111, projection="3d")
    fig.patch.set_alpha(0.0)
    ax.set_facecolor((1.0, 1.0, 1.0, 0.0))

    phi = np.linspace(0.0, 2.0 * math.pi, 160)
    theta = np.linspace(0.0, math.pi, 80)
    xs = np.outer(np.cos(phi), np.sin(theta))
    ys = np.outer(np.sin(phi), np.sin(theta))
    zs = np.outer(np.ones_like(phi), np.cos(theta))
    ax.plot_wireframe(xs, ys, zs, rstride=14, cstride=14, color="#b6bcc8", linewidth=0.3, alpha=0.5)

    for site in sites:
        x, y, z = site.site_xyz
        ax.scatter([x], [y], [z], s=38, color=site.source_hex, depthshade=False, edgecolors="none")

    ax.set_axis_off()
    ax.view_init(elev=18.0, azim=-58.0)
    fig.tight_layout()
    fig.savefig(out_path, dpi=220, bbox_inches="tight", transparent=True)
    plt.close(fig)


def export_sphere(
    *,
    sites: Sequence[SphereEntry],
    diameter_mm: float,
    shell_thickness_mm: float,
    subdivisions: int,
    out_3mf: str,
    parts_csv: str | None,
    export_stl_dir: str | None,
    debug_png: str | None,
    plate_width_mm: float,
    plate_depth_mm: float,
) -> int:
    radius = diameter_mm * 0.5
    if shell_thickness_mm <= 0.0:
        raise ValueError("shell_thickness_mm must be > 0")
    inner_radius = radius - shell_thickness_mm
    if inner_radius <= 0.0:
        raise ValueError(
            f"shell_thickness_mm={shell_thickness_mm:g} is too large for diameter_mm={diameter_mm:g}"
        )
    shell_scale = inner_radius / radius
    surface = tm.creation.icosphere(subdivisions=subdivisions, radius=radius)
    assignments = _face_assignments(surface, sites)
    face_neighbors: List[List[int]] = [[] for _ in range(len(surface.faces))]
    for a, b in np.asarray(surface.face_adjacency, dtype=np.int64):
        face_neighbors[int(a)].append(int(b))
        face_neighbors[int(b)].append(int(a))

    face_counts = np.bincount(assignments, minlength=len(sites))
    empty = [sites[idx].token for idx, count in enumerate(face_counts) if int(count) == 0]
    if empty:
        raise ValueError(
            f"{len(empty)} spherical sites vanished at subdivisions={subdivisions}. "
            f"Increase subdivisions. Example missing sites: {', '.join(empty[:12])}"
        )

    if export_stl_dir:
        os.makedirs(export_stl_dir, exist_ok=True)

    named_meshes: List[Tuple[str, tm.Trimesh]] = []
    metadata_by_label: Dict[str, Dict[str, str]] = {}
    rows: List[Dict[str, object]] = []

    plate_shift = np.array([plate_width_mm * 0.5, plate_depth_mm * 0.5, radius], dtype=np.float64)

    for idx, site in enumerate(sites, start=1):
        face_idx = np.flatnonzero(assignments == (idx - 1))
        region_dir = np.asarray(site.site_xyz, dtype=np.float64)
        label = f"sphere_{idx:03d}_{site.token}"
        mesh = _build_region_mesh(surface, face_idx, face_neighbors, shell_scale, region_dir, label)
        mesh.apply_translation(plate_shift)

        named_meshes.append((label, mesh.copy()))
        metadata_by_label[label] = {
            "source_hex": site.source_hex,
            "stack_token": site.token,
            "region_index": str(idx),
            "fC": f"{site.fractions['C']:.6f}",
            "fM": f"{site.fractions['M']:.6f}",
            "fY": f"{site.fractions['Y']:.6f}",
            "fK": f"{site.fractions['K']:.6f}",
            "fW": f"{site.fractions['W']:.6f}",
            "site_lat_deg": f"{site.site_lat_deg:.6f}",
            "site_lon_deg": f"{site.site_lon_deg:.6f}",
        }

        if export_stl_dir:
            mesh.export(os.path.join(export_stl_dir, f"{label}.stl"))

        rows.append(
            {
                "index": idx,
                "label": label,
                "stack": site.token,
                "source_hex": site.source_hex,
                "fC": site.fractions["C"],
                "fM": site.fractions["M"],
                "fY": site.fractions["Y"],
                "fK": site.fractions["K"],
                "fW": site.fractions["W"],
                "site_lat_deg": site.site_lat_deg,
                "site_lon_deg": site.site_lon_deg,
                "shell_thickness_mm": shell_thickness_mm,
                "surface_faces": int(face_idx.size),
                "surface_area_mm2": float(surface.area_faces[face_idx].sum()),
                "volume_mm3": float(abs(mesh.volume)),
                "watertight": bool(mesh.is_watertight),
                "is_volume": bool(mesh.is_volume),
            }
        )

    title = os.path.splitext(os.path.basename(out_3mf))[0]
    write_3mf_as_assembly(named_meshes, out_3mf, title, metadata_by_label=metadata_by_label)

    if parts_csv:
        with open(parts_csv, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "index",
                    "label",
                    "stack",
                    "source_hex",
                    "fC",
                    "fM",
                    "fY",
                    "fK",
                    "fW",
                    "site_lat_deg",
                    "site_lon_deg",
                    "shell_thickness_mm",
                    "surface_faces",
                    "surface_area_mm2",
                    "volume_mm3",
                    "watertight",
                    "is_volume",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)

    if debug_png:
        write_debug_png(debug_png, sites)

    return len(named_meshes)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Export a spherical CMYKW Voronoi gamut as a 3MF assembly.")
    p.add_argument("--site-mode", choices=["palette", "palette-anchors", "poles-only"], default="palette-anchors")
    p.add_argument("--preset", choices=["simple", "normal", "full"], default="normal")
    p.add_argument("--max-height", type=int)
    p.add_argument("--max-run", type=int)
    p.add_argument("--distinct-lte", type=int)
    p.add_argument("--diameter-mm", type=float, default=150.0)
    p.add_argument("--shell-thickness-mm", type=float, default=4.0)
    p.add_argument("--subdivisions", type=int, default=7, help="Icosphere subdivisions. Default: %(default)s")
    p.add_argument("--plate-width-mm", type=float, default=256.0)
    p.add_argument("--plate-depth-mm", type=float, default=256.0)
    p.add_argument("--registry-path", default=str(Path(__file__).with_name("layer_perm_registry.json")))
    p.add_argument("--out-3mf", required=True)
    p.add_argument("--parts-csv")
    p.add_argument("--export-stl-dir")
    p.add_argument("--debug-png")
    return p


def main() -> None:
    args = build_parser().parse_args()
    resolved = resolve_preset_parameters(
        args.preset,
        max_height=args.max_height,
        max_run=args.max_run,
        distinct_lte=args.distinct_lte,
    )
    sites = build_sites(
        site_mode=args.site_mode,
        max_height=resolved["max_height"],
        max_run=resolved["max_run"],
        distinct_lte=resolved["distinct_lte"],
        registry_path=Path(args.registry_path),
    )
    count = export_sphere(
        sites=sites,
        diameter_mm=args.diameter_mm,
        shell_thickness_mm=args.shell_thickness_mm,
        subdivisions=args.subdivisions,
        out_3mf=args.out_3mf,
        parts_csv=args.parts_csv,
        export_stl_dir=args.export_stl_dir,
        debug_png=args.debug_png,
        plate_width_mm=args.plate_width_mm,
        plate_depth_mm=args.plate_depth_mm,
    )
    print(
        f"wrote {count} spherical regions to {args.out_3mf} "
        f"(site_mode={args.site_mode} preset={resolved['preset']} max_height={resolved['max_height']} "
        f"max_run={resolved['max_run']} distinct_lte={resolved['distinct_lte']} "
        f"diameter_mm={args.diameter_mm:g} shell_thickness_mm={args.shell_thickness_mm:g} "
        f"subdivisions={args.subdivisions})"
    )


if __name__ == "__main__":
    main()
