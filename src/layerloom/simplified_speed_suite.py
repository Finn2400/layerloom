#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate a simplified LayerLoom speed-test suite.

This suite builds five 3x3 cube-grid arrangements and pre-weaves the
arrangements that contain mixed-pattern cubes.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import io
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import trimesh

from benchmark_common import TOKEN_HEX, ensure_dir, format_float, write_csv
from benchmark_suite import run_weave, wrap_build_items_as_single_assembly


PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_OUT_DIR = PACKAGE_DIR / "benchmark_outputs" / "simplified_speed_suite_v1"

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
XML_NS = "http://www.w3.org/XML/1998/namespace"
M = lambda tag: f"{{{CORE_NS}}}{tag}"

ET.register_namespace("", CORE_NS)

CUBE_SIZE_MM = 15.0
GRID_GAP_MM = 3.0
GRID_DIM = 3
LAYER_HEIGHT_MM = 0.12

COLOR_NAME_BY_TOKEN = {
    "c": "all_cyan",
    "m": "all_magenta",
    "y": "all_yellow",
}

MANIFEST_COLUMNS = [
    "arrangement_id",
    "arrangement_name",
    "layer_height",
    "source_path",
    "source_object_count",
    "woven_required",
    "woven_tokens",
    "woven_path",
    "recommended_slice_input_path",
    "notes",
]

WEAVE_STATUS_COLUMNS = [
    "arrangement_id",
    "layer_height",
    "source_path",
    "woven_path",
    "status",
    "notes",
]


@dataclass(frozen=True)
class MeshObjectSpec:
    name: str
    partnumber: str
    mesh: trimesh.Trimesh
    metadata: Dict[str, str]


def cell_origin(row: int, col: int, *, cube_size: float = CUBE_SIZE_MM, gap: float = GRID_GAP_MM) -> Tuple[float, float, float]:
    x = float(col - 1) * (cube_size + gap)
    y = float(GRID_DIM - row) * (cube_size + gap)
    return (x, y, 0.0)


def cube_mesh(row: int, col: int, *, cube_size: float = CUBE_SIZE_MM, gap: float = GRID_GAP_MM) -> trimesh.Trimesh:
    x, y, z = cell_origin(row, col, cube_size=cube_size, gap=gap)
    extents = np.array([cube_size, cube_size, cube_size], dtype=np.float64)
    transform = np.eye(4, dtype=np.float64)
    transform[:3, 3] = np.array([x + cube_size / 2.0, y + cube_size / 2.0, z + cube_size / 2.0], dtype=np.float64)
    return trimesh.creation.box(extents=extents, transform=transform)


def merge_meshes(meshes: Sequence[trimesh.Trimesh]) -> trimesh.Trimesh:
    if not meshes:
        raise ValueError("No meshes to merge.")
    if len(meshes) == 1:
        return trimesh.Trimesh(
            vertices=np.asarray(meshes[0].vertices, dtype=np.float64),
            faces=np.asarray(meshes[0].faces, dtype=np.int64),
            process=False,
        )
    merged = trimesh.util.concatenate(meshes)
    return trimesh.Trimesh(
        vertices=np.asarray(merged.vertices, dtype=np.float64),
        faces=np.asarray(merged.faces, dtype=np.int64),
        process=False,
    )


def blended_hex_for_token(token: str) -> str:
    chars = [ch for ch in token.lower() if ch in TOKEN_HEX]
    if not chars:
        return ""
    rgb = np.array(
        [[int(TOKEN_HEX[ch][i : i + 2], 16) for i in (1, 3, 5)] for ch in chars],
        dtype=np.float64,
    )
    avg = np.clip(np.round(rgb.mean(axis=0)), 0, 255).astype(int)
    return "#" + "".join(f"{value:02x}" for value in avg)


def object_metadata(*, token: str, arrangement_id: str, role: str, cell_label: str) -> Dict[str, str]:
    return {
        "stack_token": token,
        "source_hex": blended_hex_for_token(token),
        "benchmark_arrangement": arrangement_id,
        "benchmark_role": role,
        "benchmark_cell": cell_label,
    }


def write_mesh_3mf(path: Path, objects: Sequence[MeshObjectSpec], *, title: str) -> None:
    root = ET.Element(M("model"), {"unit": "millimeter", f"{{{XML_NS}}}lang": "en-US"})
    ET.SubElement(root, M("metadata"), {"name": "Title"}).text = title
    resources = ET.SubElement(root, M("resources"))
    build = ET.SubElement(root, M("build"))

    for idx, spec in enumerate(objects, start=1):
        obj_el = ET.SubElement(
            resources,
            M("object"),
            {"id": str(idx), "type": "model", "name": spec.name},
        )
        ET.SubElement(obj_el, M("metadata"), {"name": "Name"}).text = spec.name
        mg = ET.SubElement(obj_el, M("metadatagroup"))
        for key, value in spec.metadata.items():
            ET.SubElement(mg, M("metadata"), {"name": str(key)}).text = str(value)

        mesh_el = ET.SubElement(obj_el, M("mesh"))
        verts_el = ET.SubElement(mesh_el, M("vertices"))
        for x, y, z in np.asarray(spec.mesh.vertices, dtype=np.float64):
            v = ET.SubElement(verts_el, M("vertex"))
            v.set("x", f"{float(x):.9f}")
            v.set("y", f"{float(y):.9f}")
            v.set("z", f"{float(z):.9f}")

        tris_el = ET.SubElement(mesh_el, M("triangles"))
        for a, b, c in np.asarray(spec.mesh.faces, dtype=np.int64):
            tri = ET.SubElement(tris_el, M("triangle"))
            tri.set("v1", str(int(a)))
            tri.set("v2", str(int(b)))
            tri.set("v3", str(int(c)))

        ET.SubElement(
            build,
            M("item"),
            {
                "objectid": str(idx),
                "printable": "1",
                "partnumber": spec.partnumber,
            },
        )

    buffer = io.BytesIO()
    ET.ElementTree(root).write(buffer, encoding="utf-8", xml_declaration=True)
    xml_bytes = buffer.getvalue()
    content_types_xml = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        b'<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
        b"</Types>"
    )
    rels_xml = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" '
        b'Target="/3D/3dmodel.model"/>'
        b"</Relationships>"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("3D/3dmodel.model", xml_bytes)


def count_mesh_objects(path: Path) -> int:
    with zipfile.ZipFile(path, "r") as zf:
        model_name = next(name for name in zf.namelist() if name.endswith(".model"))
        root = ET.fromstring(zf.read(model_name))
    resources = root.find(M("resources"))
    if resources is None:
        return 0
    return sum(1 for obj in resources.findall(M("object")) if obj.find(M("mesh")) is not None)


def make_source_objects(arrangement_id: str, cells: Dict[Tuple[int, int], str]) -> List[MeshObjectSpec]:
    solids: Dict[str, List[trimesh.Trimesh]] = defaultdict(list)
    woven: List[MeshObjectSpec] = []

    for (row, col), token in sorted(cells.items()):
        mesh = cube_mesh(row, col)
        cell_label = f"r{row}c{col}"
        if len(token) == 1:
            solids[token].append(mesh)
        else:
            name = f"{arrangement_id}__{cell_label}__PAT_{token}__"
            woven.append(
                MeshObjectSpec(
                    name=name,
                    partnumber=name,
                    mesh=mesh,
                    metadata=object_metadata(
                        token=token,
                        arrangement_id=arrangement_id,
                        role="woven_cube",
                        cell_label=cell_label,
                    ),
                )
            )

    solid_specs: List[MeshObjectSpec] = []
    for token, meshes in sorted(solids.items()):
        color_name = COLOR_NAME_BY_TOKEN[token]
        solid_specs.append(
            MeshObjectSpec(
                name=color_name,
                partnumber=f"{color_name}__PAT_{token}__",
                mesh=merge_meshes(meshes),
                metadata=object_metadata(
                    token=token,
                    arrangement_id=arrangement_id,
                    role="solid_color_group",
                    cell_label=color_name,
                ),
            )
        )

    return solid_specs + woven


def arrangement_definitions() -> List[Tuple[str, str, Dict[Tuple[int, int], str]]]:
    base = {(row, col): "c" for row in range(1, 4) for col in range(1, 4)}

    arr1 = dict(base)
    arr1[(1, 1)] = "y"

    arr2 = dict(arr1)
    arr2[(2, 2)] = "cy"

    arr3 = dict(arr2)
    arr3[(1, 2)] = "yccc"
    arr3[(2, 1)] = "yyyc"

    arr4 = dict(arr3)
    arr4[(3, 3)] = "m"

    arr5 = dict(arr4)
    arr5[(2, 2)] = "cym"
    arr5[(2, 3)] = "mccc"
    arr5[(3, 2)] = "mmmc"

    return [
        ("arrangement_01", "solid_yellow_corner", arr1),
        ("arrangement_02", "center_cy_1to1", arr2),
        ("arrangement_03", "cy_progression", arr3),
        ("arrangement_04", "cy_progression_plus_magenta_corner", arr4),
        ("arrangement_05", "cym_center_and_magenta_gradients", arr5),
    ]


def unique_woven_tokens(cells: Dict[Tuple[int, int], str]) -> List[str]:
    return sorted({token for token in cells.values() if len(token) > 1})


def generate_suite(
    out_dir: Path,
    *,
    layer_height: float = LAYER_HEIGHT_MM,
    generate_woven: bool = True,
    verbose: bool = False,
    strict_weave: bool = True,
) -> Dict[str, Path]:
    suite_dir = ensure_dir(out_dir)
    sources_dir = ensure_dir(suite_dir / "sources")
    woven_dir = ensure_dir(suite_dir / "woven")
    manifests_dir = ensure_dir(suite_dir / "manifests")

    manifest_rows: List[Dict[str, object]] = []
    weave_status_rows: List[Dict[str, object]] = []

    for arrangement_id, arrangement_name, cells in arrangement_definitions():
        source_path = sources_dir / f"{arrangement_id}.3mf"
        objects = make_source_objects(arrangement_id, cells)
        write_mesh_3mf(source_path, objects, title=arrangement_name)

        tokens = unique_woven_tokens(cells)
        woven_required = bool(tokens)
        woven_path = woven_dir / f"{arrangement_id}__lh_{format_float(layer_height).replace('.', 'p')}_woven.3mf"
        recommended_slice = source_path

        if woven_required and generate_woven:
            try:
                run_weave(source_path, woven_path, layer_height, verbose=verbose)
                wrapped = wrap_build_items_as_single_assembly(
                    woven_path,
                    assembly_name=f"{arrangement_id}__assembly",
                )
                recommended_slice = woven_path
                weave_status_rows.append(
                    {
                        "arrangement_id": arrangement_id,
                        "layer_height": layer_height,
                        "source_path": str(source_path),
                        "woven_path": str(woven_path),
                        "status": "woven",
                        "notes": "assembly_wrapped" if wrapped else "",
                    }
                )
            except Exception as exc:
                weave_status_rows.append(
                    {
                        "arrangement_id": arrangement_id,
                        "layer_height": layer_height,
                        "source_path": str(source_path),
                        "woven_path": str(woven_path),
                        "status": "failed",
                        "notes": str(exc),
                    }
                )
                if strict_weave:
                    raise
        elif woven_required:
            weave_status_rows.append(
                {
                    "arrangement_id": arrangement_id,
                    "layer_height": layer_height,
                    "source_path": str(source_path),
                    "woven_path": str(woven_path),
                    "status": "pending",
                    "notes": "weave skipped by request",
                }
            )

        manifest_rows.append(
            {
                "arrangement_id": arrangement_id,
                "arrangement_name": arrangement_name,
                "layer_height": layer_height,
                "source_path": str(source_path),
                "source_object_count": count_mesh_objects(source_path),
                "woven_required": "yes" if woven_required else "no",
                "woven_tokens": ";".join(tokens),
                "woven_path": str(woven_path) if woven_required else "",
                "recommended_slice_input_path": str(recommended_slice),
                "notes": "",
            }
        )

    manifest_csv = manifests_dir / "simplified_speed_manifest.csv"
    weave_status_csv = manifests_dir / "simplified_speed_weave_status.csv"
    write_csv(manifest_csv, manifest_rows, MANIFEST_COLUMNS)
    write_csv(weave_status_csv, weave_status_rows, WEAVE_STATUS_COLUMNS)

    return {
        "suite_dir": suite_dir,
        "sources_dir": sources_dir,
        "woven_dir": woven_dir,
        "manifest_csv": manifest_csv,
        "weave_status_csv": weave_status_csv,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate simplified LayerLoom speed-test arrangements.")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Output suite directory")
    p.add_argument("--layer-height", type=float, default=LAYER_HEIGHT_MM, help="Weave layer height in mm")
    p.add_argument("--skip-weave", action="store_true", help="Generate source arrangements only")
    p.add_argument("--no-strict-weave", action="store_true", help="Do not fail if a weave job errors")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = generate_suite(
        Path(args.out_dir),
        layer_height=float(args.layer_height),
        generate_woven=not bool(args.skip_weave),
        verbose=bool(args.verbose),
        strict_weave=not bool(args.no_strict_weave),
    )
    print(f"[simplified-suite] suite dir: {out['suite_dir']}")
    print(f"[simplified-suite] manifest:  {out['manifest_csv']}")
    print(f"[simplified-suite] weave csv: {out['weave_status_csv']}")
    if not args.skip_weave:
        print(f"[simplified-suite] woven dir: {out['woven_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
