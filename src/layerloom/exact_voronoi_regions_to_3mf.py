#!/usr/bin/env python3
"""
Build a 3D object from the exact Voronoi regions used by layer_perm_cmy_visualizer.

This matches the fraction-based ternary Voronoi layout rather than the snapped-grid
"diag" workflow used by the older 3MF exporters.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
from typing import Dict, List, Sequence, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import trimesh as tm

import layer_perm_cmy_visualizer as vis
from layer_perm_3mf_assembly_3_circle import triangulate_prism_solid, write_3mf_as_assembly
from palette_utils import PALETTE_PRESETS, corrected_hex, resolve_preset_parameters


Stack = Tuple[str, ...]
Point = Tuple[float, float]


def _finalize_region_mesh(mesh: tm.Trimesh, label: str) -> tm.Trimesh:
    """
    Normalize a generated prism mesh and refuse to emit invalid solids.
    """
    if len(mesh.faces) > 0:
        mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()
    try:
        mesh.merge_vertices()
    except Exception:
        pass

    if mesh.is_watertight and not mesh.is_volume:
        # For our generated prisms, this usually means the shell winding is flipped.
        mesh.invert()

    if not mesh.is_watertight or not mesh.is_volume or not mesh.is_winding_consistent:
        raise ValueError(
            f"Generated invalid region mesh for {label}: "
            f"watertight={mesh.is_watertight} "
            f"is_volume={mesh.is_volume} "
            f"winding={mesh.is_winding_consistent}"
        )
    return mesh


def _angle_tag(angle_deg: float) -> str:
    text = f"{angle_deg:g}"
    return text.replace("-", "neg").replace(".", "p")


def _parse_copy_angles(arg: str | None) -> List[float]:
    if not arg:
        return [0.0]
    out: List[float] = []
    for chunk in arg.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        out.append(float(chunk))
    return out or [0.0]


def _collection_bounds(named_meshes: Sequence[Tuple[str, tm.Trimesh]]) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    mins = [float("inf"), float("inf"), float("inf")]
    maxs = [float("-inf"), float("-inf"), float("-inf")]
    for _, mesh in named_meshes:
        b = mesh.bounds
        for i in range(3):
            mins[i] = min(mins[i], float(b[0, i]))
            maxs[i] = max(maxs[i], float(b[1, i]))
    return (mins[0], mins[1], mins[2]), (maxs[0], maxs[1], maxs[2])


def _expand_rotated_copies(
    named_meshes: Sequence[Tuple[str, tm.Trimesh]],
    metadata_by_label: Dict[str, Dict[str, str]],
    rows: Sequence[Dict[str, object]],
    copy_angles_deg: Sequence[float],
    plate_width_mm: float,
    plate_depth_mm: float,
    copy_gap_mm: float,
) -> Tuple[List[Tuple[str, tm.Trimesh]], Dict[str, Dict[str, str]], List[Dict[str, object]]]:
    if not copy_angles_deg or (len(copy_angles_deg) == 1 and abs(copy_angles_deg[0]) < 1e-9):
        return list(named_meshes), dict(metadata_by_label), list(rows)

    base_meshes = list(named_meshes)
    base_meta = dict(metadata_by_label)
    row_lookup = {str(r["label"]): dict(r) for r in rows}

    copy_specs = []
    for angle in copy_angles_deg:
        rot = tm.transformations.rotation_matrix(math.radians(angle), [1.0, 0.0, 0.0], point=[0.0, 0.0, 0.0])
        rotated_members: List[Tuple[str, tm.Trimesh]] = []
        for label, mesh in base_meshes:
            m = mesh.copy()
            m.apply_transform(rot)
            rotated_members.append((label, m))
        mins, maxs = _collection_bounds(rotated_members)
        dims = (
            float(maxs[0] - mins[0]),
            float(maxs[1] - mins[1]),
            float(maxs[2] - mins[2]),
        )
        copy_specs.append(
            {
                "angle": float(angle),
                "tag": _angle_tag(angle),
                "rotated_members": rotated_members,
                "mins": mins,
                "dims": dims,
            }
        )

    # Pack larger-footprint copies first to keep the overall plate usage compact.
    copy_specs.sort(key=lambda spec: spec["dims"][1], reverse=True)

    rows_layout: List[Dict[str, object]] = []
    current_row = {"copies": [], "width": 0.0, "depth": 0.0}
    for spec in copy_specs:
        width = float(spec["dims"][0])
        depth = float(spec["dims"][1])
        needed = width if not current_row["copies"] else float(current_row["width"]) + copy_gap_mm + width
        if current_row["copies"] and needed > plate_width_mm:
            rows_layout.append(current_row)
            current_row = {"copies": [], "width": 0.0, "depth": 0.0}
        current_row["copies"].append(spec)
        current_row["width"] = width if not current_row["width"] else float(current_row["width"]) + copy_gap_mm + width
        current_row["depth"] = max(float(current_row["depth"]), depth)
    if current_row["copies"]:
        rows_layout.append(current_row)

    total_depth = sum(float(row["depth"]) for row in rows_layout)
    total_depth += copy_gap_mm * max(0, len(rows_layout) - 1)
    if total_depth > plate_depth_mm + 1e-6:
        raise ValueError(
            f"Rotated triangle copies do not fit on plate: need depth {total_depth:.2f} mm "
            f"for plate depth {plate_depth_mm:.2f} mm"
        )

    expanded_meshes: List[Tuple[str, tm.Trimesh]] = []
    expanded_meta: Dict[str, Dict[str, str]] = {}
    expanded_rows: List[Dict[str, object]] = []

    y_cursor = (plate_depth_mm - total_depth) * 0.5
    for row in rows_layout:
        row_width = float(row["width"])
        x_cursor = (plate_width_mm - row_width) * 0.5
        for spec in row["copies"]:
            mins = spec["mins"]
            tx = x_cursor - float(mins[0])
            ty = y_cursor - float(mins[1])
            tz = -float(mins[2])
            transform = tm.transformations.translation_matrix([tx, ty, tz])
            for base_label, rotated in spec["rotated_members"]:
                out_label = f"{base_label}__tilt_{spec['tag']}deg"
                m = rotated.copy()
                m.apply_transform(transform)
                expanded_meshes.append((out_label, m))
                md = dict(base_meta.get(base_label, {}))
                md["copy_angle_deg"] = f"{float(spec['angle']):g}"
                expanded_meta[out_label] = md

                row_data = dict(row_lookup.get(base_label, {"label": base_label}))
                row_data["label"] = out_label
                row_data["copy_angle_deg"] = float(spec["angle"])
                expanded_rows.append(row_data)

            x_cursor += float(spec["dims"][0]) + copy_gap_mm
        y_cursor += float(row["depth"]) + copy_gap_mm

    return expanded_meshes, expanded_meta, expanded_rows


def build_stacks(max_height: int, max_run: int, distinct_lte: int) -> List[Stack]:
    return vis.build_palette_stacks(max_height, max_run, distinct_lte)


def build_regions(stacks: Sequence[Stack]) -> Tuple[List[Point], List[Tuple[float, float, float]], List[List[Point]]]:
    v_c, v_m, v_y = (0.0, 0.0), (1.0, 0.0), (0.5, vis.SQRT3_2)
    tri = [v_c, v_m, v_y]
    sites = [vis.barycentric_xy(*vis.stack_fractions(s)) for s in stacks]
    rgbs = [vis.blend_rgb(s) for s in stacks]
    regions: List[List[Point]] = []
    for i, p in enumerate(sites):
        others = [q for j, q in enumerate(sites) if j != i]
        poly = vis.bounded_voronoi_polygon(p, others, tri)
        regions.append(poly)
    return sites, rgbs, regions


def write_debug_png(
    out_path: str,
    stacks: Sequence[Stack],
    rgbs: Sequence[Tuple[float, float, float]],
    regions: Sequence[Sequence[Point]],
    max_height: int,
    max_run: int,
) -> None:
    fig, ax = plt.subplots(figsize=(6.8, 6.3))
    ax.axis("off")
    ax.set_title(
        f"Ternary CMY — Up to Height={max_height}, Max-Run={max_run} | Solid Voronoi regions",
        fontsize=14,
        pad=10,
    )

    for poly, rgb in zip(regions, rgbs):
        if len(poly) < 3:
            continue
        xs = [x for x, _ in poly]
        ys = [y for _, y in poly]
        ax.fill(xs, ys, color=rgb, lw=0)

    for idx, (poly, rgb) in enumerate(zip(regions, rgbs), start=1):
        if len(poly) < 3:
            continue
        cx, cy = vis.polygon_centroid(poly)
        txt = "black" if sum(rgb) > vis.TEXT_LIGHT_THRESHOLD else "white"
        ax.text(cx, cy, str(idx), ha="center", va="center", fontsize=8, color=txt, fontweight="bold")

    ax.set_aspect("equal", "box")
    ax.set_xlim(-0.05, 1.05)
    ax.set_ylim(-0.05, vis.SQRT3_2 + 0.05)
    fig.tight_layout()
    fig.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close(fig)


def export_regions(
    stacks: Sequence[Stack],
    regions: Sequence[Sequence[Point]],
    edge_mm: float,
    thickness_mm: float,
    out_3mf: str,
    out_stl_dir: str | None,
    parts_csv: str | None,
    copy_angles_deg: Sequence[float],
    plate_width_mm: float,
    plate_depth_mm: float,
    copy_gap_mm: float,
) -> int:
    os.makedirs(out_stl_dir, exist_ok=True) if out_stl_dir else None

    named_meshes: List[Tuple[str, tm.Trimesh]] = []
    metadata_by_label = {}
    rows = []
    for idx, (stack, poly) in enumerate(zip(stacks, regions), start=1):
        if len(poly) < 3:
            continue
        token = "".join(stack).lower()
        label = f"gamut_{idx:03d}_{token}"
        safe_label = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in label)
        poly_mm = [(edge_mm * x, edge_mm * y) for x, y in poly]
        verts, faces = triangulate_prism_solid(poly_mm, 0.0, thickness_mm)
        if not verts or not faces:
            continue

        mesh = _finalize_region_mesh(
            tm.Trimesh(vertices=verts, faces=faces, process=False),
            safe_label,
        )
        named_meshes.append((safe_label, mesh.copy()))
        f_c = stack.count("C") / len(stack)
        f_m = stack.count("M") / len(stack)
        f_y = stack.count("Y") / len(stack)
        source_hex = corrected_hex(token)
        metadata_by_label[safe_label] = {
            "source_hex": source_hex,
            "stack_token": token,
            "region_index": str(idx),
            "fC": f"{f_c:.6f}",
            "fM": f"{f_m:.6f}",
            "fY": f"{f_y:.6f}",
        }

        if out_stl_dir:
            mesh.export(os.path.join(out_stl_dir, f"{safe_label}.stl"))

        area_mm2 = 0.0
        for j in range(len(poly_mm)):
            x1, y1 = poly_mm[j]
            x2, y2 = poly_mm[(j + 1) % len(poly_mm)]
            area_mm2 += x1 * y2 - x2 * y1
        area_mm2 = abs(area_mm2) * 0.5
        rows.append(
            {
                "index": idx,
                "label": safe_label,
                "stack": token,
                "source_hex": source_hex,
                "fC": f_c,
                "fM": f_m,
                "fY": f_y,
                "area_mm2": area_mm2,
                "volume_mm3": area_mm2 * thickness_mm,
                "watertight": bool(mesh.is_watertight),
                "is_volume": bool(mesh.is_volume),
            }
        )

    named_meshes, metadata_by_label, rows = _expand_rotated_copies(
        named_meshes=named_meshes,
        metadata_by_label=metadata_by_label,
        rows=rows,
        copy_angles_deg=copy_angles_deg,
        plate_width_mm=plate_width_mm,
        plate_depth_mm=plate_depth_mm,
        copy_gap_mm=copy_gap_mm,
    )

    write_3mf_as_assembly(
        named_meshes,
        out_3mf,
        os.path.splitext(os.path.basename(out_3mf))[0],
        metadata_by_label=metadata_by_label,
    )

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
                    "area_mm2",
                    "volume_mm3",
                    "watertight",
                    "is_volume",
                    "copy_angle_deg",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)

    return len(named_meshes)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Export exact ternary Voronoi regions as a 3MF assembly.")
    p.add_argument("--preset", choices=sorted(PALETTE_PRESETS), default="normal")
    p.add_argument("--max-height", type=int)
    p.add_argument("--max-run", type=int)
    p.add_argument("--distinct-lte", type=int)
    p.add_argument("--edge-mm", type=float, default=100.0)
    p.add_argument("--thickness-mm", type=float, default=3.0)
    p.add_argument("--copy-angles", default="0", help="Comma-separated plate tilt angles in degrees, e.g. 90,45,10")
    p.add_argument("--plate-width-mm", type=float, default=256.0)
    p.add_argument("--plate-depth-mm", type=float, default=256.0)
    p.add_argument("--copy-gap-mm", type=float, default=8.0)
    p.add_argument("--out-3mf", required=True)
    p.add_argument("--debug-png")
    p.add_argument("--export-stl-dir")
    p.add_argument("--parts-csv")
    return p


def main() -> None:
    args = build_parser().parse_args()
    resolved = resolve_preset_parameters(
        args.preset,
        max_height=args.max_height,
        max_run=args.max_run,
        distinct_lte=args.distinct_lte,
    )
    stacks = build_stacks(resolved["max_height"], resolved["max_run"], resolved["distinct_lte"])
    _, rgbs, regions = build_regions(stacks)
    if args.debug_png:
        write_debug_png(
            args.debug_png,
            stacks,
            rgbs,
            regions,
            resolved["max_height"],
            resolved["max_run"],
        )
    count = export_regions(
        stacks=stacks,
        regions=regions,
        edge_mm=args.edge_mm,
        thickness_mm=args.thickness_mm,
        out_3mf=args.out_3mf,
        out_stl_dir=args.export_stl_dir,
        parts_csv=args.parts_csv,
        copy_angles_deg=_parse_copy_angles(args.copy_angles),
        plate_width_mm=args.plate_width_mm,
        plate_depth_mm=args.plate_depth_mm,
        copy_gap_mm=args.copy_gap_mm,
    )
    print(
        f"wrote {count} meshes to {args.out_3mf} "
        f"(preset={resolved['preset']} max_height={resolved['max_height']} "
        f"max_run={resolved['max_run']} distinct_lte={resolved['distinct_lte']})"
    )


if __name__ == "__main__":
    main()
