#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Deterministic benchmark generator for woven vs non-woven multi-material tests.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from typing import Dict, List, Tuple

from .benchmark_common import (
    CASE_COLUMNS,
    CELL_COUNT,
    CELL_SIZE_MM,
    COPLANAR_DIMS_MM,
    DISTINCT_COLOR_COUNTS,
    JOB_COLUMNS,
    LAYER_HEIGHTS,
    METHODS,
    PRINTERS,
    STACKED_DIMS_MM,
    TOKEN_FAMILIES,
    TOKEN_HEX,
    TOTAL_VOLUME_MM3,
    WOVEN_PRISM_DIMS_MM,
    ceil_layers,
    ensure_dir,
    format_float,
    layer_height_token,
    safe_slug,
    token_sequence,
    write_csv,
)

PACKAGE_DIR = Path(__file__).resolve().parent
PACKAGE_PARENT = PACKAGE_DIR.parent

TRIANGLES = [
    (0, 2, 1), (0, 3, 2),
    (4, 5, 6), (4, 6, 7),
    (0, 1, 5), (0, 5, 4),
    (3, 6, 2), (3, 7, 6),
    (0, 7, 3), (0, 4, 7),
    (1, 2, 6), (1, 6, 5),
]
WEAVE_STATUS_COLUMNS = [
    "case_id",
    "layer_height",
    "input_path",
    "woven_path",
    "status",
    "notes",
]
CORE_3MF_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"


def benchmark_python() -> str:
    override = os.environ.get("LAYERLOOM_BENCH_PYTHON", "").strip()
    if override:
        return override
    return sys.executable


def box_vertices(x: float, y: float, z: float, sx: float, sy: float, sz: float) -> List[Tuple[float, float, float]]:
    return [
        (x, y, z),
        (x + sx, y, z),
        (x + sx, y + sy, z),
        (x, y + sy, z),
        (x, y, z + sz),
        (x + sx, y, z + sz),
        (x + sx, y + sy, z + sz),
        (x, y + sy, z + sz),
    ]


def write_generic_3mf(path: Path, objects: List[Dict[str, object]], title: str = "") -> None:
    object_xml = []
    build_xml = []
    for idx, obj in enumerate(objects, start=1):
        name = str(obj["name"])
        vertices = obj["vertices"]
        metadata = dict(obj.get("metadata", {}))
        if title:
            metadata.setdefault("benchmark_title", title)
        meta_xml = "".join(
            f'<metadata name="{k}">{v}</metadata>'
            for k, v in metadata.items()
        )
        verts_xml = "\n".join(
            f'<vertex x="{v[0]}" y="{v[1]}" z="{v[2]}" />' for v in vertices
        )
        tri_xml = "\n".join(
            f'<triangle v1="{a}" v2="{b}" v3="{c}" />' for a, b, c in TRIANGLES
        )
        object_xml.append(
            f"""
            <object id="{idx}" name="{name}" type="model">
              {meta_xml}
              <mesh>
                <vertices>
                {verts_xml}
                </vertices>
                <triangles>
                {tri_xml}
                </triangles>
              </mesh>
            </object>
            """.strip()
        )
        build_xml.append(f'<item objectid="{idx}" partnumber="{name}" />')

    model_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <metadata name="Title">{title or path.stem}</metadata>
  <resources>
    {' '.join(object_xml)}
  </resources>
  <build>
    {' '.join(build_xml)}
  </build>
</model>"""
    rels_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>"""
    content_types_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>"""

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("_rels/.rels", rels_xml.strip())
        zf.writestr("[Content_Types].xml", content_types_xml.strip())
        zf.writestr("3D/3dmodel.model", model_xml.strip())


def case_id(method: str, distinct_colors: int) -> str:
    return safe_slug(f"bench_{method}_c{distinct_colors}_{TOKEN_FAMILIES[distinct_colors]}")


def build_monolith_case(distinct_colors: int) -> List[Dict[str, object]]:
    token_family = TOKEN_FAMILIES[distinct_colors]
    sx, sy, sz = WOVEN_PRISM_DIMS_MM
    return [{
        "name": f"monolith_baseline__for_c{distinct_colors}_{token_family}__PAT_c__",
        "vertices": box_vertices(0.0, 0.0, 0.0, sx, sy, sz),
        "metadata": {
            "stack_token": "c",
            "source_hex": TOKEN_HEX["c"],
            "benchmark_method": "monolith",
            "benchmark_distinct_colors": str(distinct_colors),
            "benchmark_token_family": token_family,
            "benchmark_reference_role": "single_color_baseline",
        },
    }]


def build_woven_case(distinct_colors: int) -> List[Dict[str, object]]:
    token_family = TOKEN_FAMILIES[distinct_colors]
    sx, sy, sz = WOVEN_PRISM_DIMS_MM
    return [{
        "name": f"woven_prism__PAT_{token_family}__",
        "vertices": box_vertices(0.0, 0.0, 0.0, sx, sy, sz),
        "metadata": {
            "stack_token": token_family,
            "source_hex": TOKEN_HEX[token_family[0]],
            "benchmark_method": "woven_prism",
            "benchmark_distinct_colors": str(distinct_colors),
            "benchmark_token_family": token_family,
        },
    }]


def build_stacked_cells_case(distinct_colors: int) -> List[Dict[str, object]]:
    token_family = TOKEN_FAMILIES[distinct_colors]
    tokens = token_sequence(token_family, CELL_COUNT)
    objects: List[Dict[str, object]] = []
    for idx, token in enumerate(tokens, start=1):
        z = (idx - 1) * CELL_SIZE_MM
        objects.append({
            "name": f"stack_cell_{idx:02d}_{token}",
            "vertices": box_vertices(0.0, 0.0, z, CELL_SIZE_MM, CELL_SIZE_MM, CELL_SIZE_MM),
            "metadata": {
                "stack_token": token,
                "source_hex": TOKEN_HEX[token],
                "benchmark_method": "stacked_cells",
                "benchmark_distinct_colors": str(distinct_colors),
                "benchmark_token_family": token_family,
            },
        })
    return objects


def build_coplanar_cells_case(distinct_colors: int) -> List[Dict[str, object]]:
    token_family = TOKEN_FAMILIES[distinct_colors]
    tokens = token_sequence(token_family, CELL_COUNT)
    objects: List[Dict[str, object]] = []
    idx = 0
    for row in range(3):
        for col in range(4):
            token = tokens[idx]
            idx += 1
            x = row * CELL_SIZE_MM
            y = col * CELL_SIZE_MM
            objects.append({
                "name": f"grid_cell_{idx:02d}_{token}",
                "vertices": box_vertices(x, y, 0.0, CELL_SIZE_MM, CELL_SIZE_MM, CELL_SIZE_MM),
                "metadata": {
                    "stack_token": token,
                    "source_hex": TOKEN_HEX[token],
                    "benchmark_method": "coplanar_grid",
                    "benchmark_distinct_colors": str(distinct_colors),
                    "benchmark_token_family": token_family,
                },
            })
    return objects


def dims_for_method(method: str) -> Tuple[float, float, float]:
    if method == "monolith":
        return WOVEN_PRISM_DIMS_MM
    if method == "woven_prism":
        return WOVEN_PRISM_DIMS_MM
    if method == "stacked_cells":
        return STACKED_DIMS_MM
    if method == "coplanar_grid":
        return COPLANAR_DIMS_MM
    raise KeyError(method)


def object_count_for_method(method: str) -> int:
    return 1 if method in {"monolith", "woven_prism"} else CELL_COUNT


def build_objects(method: str, distinct_colors: int) -> List[Dict[str, object]]:
    if method == "monolith":
        return build_monolith_case(distinct_colors)
    if method == "woven_prism":
        return build_woven_case(distinct_colors)
    if method == "stacked_cells":
        return build_stacked_cells_case(distinct_colors)
    if method == "coplanar_grid":
        return build_coplanar_cells_case(distinct_colors)
    raise KeyError(method)


def run_weave(input_path: Path, output_path: Path, layer_height: float, verbose: bool = False) -> None:
    stl_out = output_path.parent / "_woven_stl" / output_path.stem
    stl_out.mkdir(parents=True, exist_ok=True)
    cmd = [
        benchmark_python(),
        "-m",
        "layerloom.weave",
        "-i",
        str(input_path),
        "-o",
        str(output_path),
        "--stl-out",
        str(stl_out),
        "--step",
        format_float(layer_height),
    ]
    if verbose:
        cmd.append("-v")
    subprocess.run(cmd, cwd=str(PACKAGE_PARENT), check=True)


def wrap_build_items_as_single_assembly(path: Path, *, assembly_name: str = "layerloom_benchmark_assembly") -> bool:
    """
    Convert a multi-build-item 3MF into a single assembly build item that
    references the existing mesh objects as components. This matches the
    structure that Orca/Bambu typically expects when multiple overlapping
    material bodies belong to one combined object.
    """

    ET.register_namespace("", CORE_3MF_NS)
    with zipfile.ZipFile(path, "r") as zin:
        model_names = [name for name in zin.namelist() if name.endswith(".model")]
        if not model_names:
            raise FileNotFoundError(f"No .model payload found inside {path}")
        model_name = model_names[0]
        root = ET.fromstring(zin.read(model_name))
        resources = root.find(f"{{{CORE_3MF_NS}}}resources")
        build = root.find(f"{{{CORE_3MF_NS}}}build")
        if resources is None or build is None:
            raise ValueError(f"Missing resources/build section in {path}")
        items = build.findall(f"{{{CORE_3MF_NS}}}item")
        if len(items) <= 1:
            return False

        objects = resources.findall(f"{{{CORE_3MF_NS}}}object")
        max_id = max(int(obj.get("id")) for obj in objects)
        assembly_id = str(max_id + 1)
        assembly_obj = ET.Element(
            f"{{{CORE_3MF_NS}}}object",
            {"id": assembly_id, "type": "model", "name": assembly_name},
        )
        components = ET.SubElement(assembly_obj, f"{{{CORE_3MF_NS}}}components")
        for item in items:
            attrs = {"objectid": item.get("objectid")}
            transform = item.get("transform")
            if transform:
                attrs["transform"] = transform
            ET.SubElement(components, f"{{{CORE_3MF_NS}}}component", attrs)
        resources.append(assembly_obj)

        for item in list(build):
            build.remove(item)
        ET.SubElement(build, f"{{{CORE_3MF_NS}}}item", {"objectid": assembly_id, "printable": "1"})

        xml_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == model_name:
                    data = xml_bytes
                zout.writestr(info, data)
    tmp_path.replace(path)
    return True


def generate_suite(out_dir: Path, *, generate_woven: bool, verbose: bool, strict_weave: bool) -> Dict[str, Path]:
    suite_dir = ensure_dir(out_dir)
    sources_dir = ensure_dir(suite_dir / "sources")
    woven_dir = ensure_dir(suite_dir / "woven")
    manifests_dir = ensure_dir(suite_dir / "manifests")

    case_rows: List[Dict[str, object]] = []
    job_rows: List[Dict[str, object]] = []
    weave_status_rows: List[Dict[str, object]] = []
    woven_ready_paths: Dict[Tuple[str, float], Path] = {}

    for method in METHODS:
        for distinct_colors in DISTINCT_COLOR_COUNTS:
            token_family = TOKEN_FAMILIES[distinct_colors]
            cid = case_id(method, distinct_colors)
            objects = build_objects(method, distinct_colors)
            dims = dims_for_method(method)
            source_name = f"{cid}.3mf"
            source_path = sources_dir / source_name
            write_generic_3mf(source_path, objects, title=cid)
            case_rows.append({
                "case_id": cid,
                "method": method,
                "distinct_colors": distinct_colors,
                "token_family": token_family,
                "object_count": object_count_for_method(method),
                "total_volume_mm3": TOTAL_VOLUME_MM3,
                "dims_x_mm": dims[0],
                "dims_y_mm": dims[1],
                "dims_z_mm": dims[2],
                "source_kind": "woven_input" if method == "woven_prism" else "source_3mf",
                "source_path": str(source_path),
                "notes": "balanced cell family" if method != "monolith" else "single-color baseline",
            })

            if method == "woven_prism" and generate_woven:
                for layer_height in LAYER_HEIGHTS:
                    woven_name = f"{cid}__lh_{layer_height_token(layer_height)}_woven.3mf"
                    woven_path = woven_dir / woven_name
                    try:
                        if distinct_colors == 1:
                            shutil.copy2(source_path, woven_path)
                            weave_status_rows.append({
                                "case_id": cid,
                                "layer_height": layer_height,
                                "input_path": str(source_path),
                                "woven_path": str(woven_path),
                                "status": "identity_copy",
                                "notes": "single-color woven baseline uses source geometry directly",
                            })
                        else:
                            run_weave(source_path, woven_path, layer_height, verbose=verbose)
                            wrapped = wrap_build_items_as_single_assembly(
                                woven_path,
                                assembly_name=f"{cid}__assembly",
                            )
                            weave_status_rows.append({
                                "case_id": cid,
                                "layer_height": layer_height,
                                "input_path": str(source_path),
                                "woven_path": str(woven_path),
                                "status": "woven",
                                "notes": "assembly_wrapped" if wrapped else "",
                            })
                        woven_ready_paths[(cid, layer_height)] = woven_path
                    except subprocess.CalledProcessError as exc:
                        weave_status_rows.append({
                            "case_id": cid,
                            "layer_height": layer_height,
                            "input_path": str(source_path),
                            "woven_path": str(woven_path),
                            "status": "failed",
                            "notes": str(exc),
                        })
                        if strict_weave:
                            raise

            for printer in PRINTERS:
                for layer_height in LAYER_HEIGHTS:
                    slice_input = source_path
                    source_kind = "source_3mf"
                    job_note = ""
                    if method == "woven_prism":
                        slice_input = woven_dir / f"{cid}__lh_{layer_height_token(layer_height)}_woven.3mf"
                        if (cid, layer_height) in woven_ready_paths:
                            source_kind = "woven_ready_3mf"
                            if distinct_colors == 1:
                                job_note = "single-color woven baseline copied from source geometry"
                        elif generate_woven:
                            source_kind = "woven_missing"
                            job_note = "headless weave failed; see weave_status.csv"
                        else:
                            source_kind = "woven_pending"
                            job_note = "source generated; weave step skipped by request"
                    export_stem = f"{cid}__printer_{printer.lower()}__lh_{layer_height_token(layer_height)}"
                    job_rows.append({
                        "job_id": export_stem,
                        "case_id": cid,
                        "printer": printer,
                        "layer_height": layer_height,
                        "method": method,
                        "distinct_colors": distinct_colors,
                        "token_family": token_family,
                        "slice_input_path": str(slice_input),
                        "source_kind": source_kind,
                        "expected_layers": ceil_layers(dims[2], layer_height),
                        "export_stem": export_stem,
                        "notes": job_note,
                    })

    cases_csv = manifests_dir / "benchmark_cases.csv"
    jobs_csv = manifests_dir / "benchmark_jobs.csv"
    weave_status_csv = manifests_dir / "weave_status.csv"
    write_csv(cases_csv, case_rows, CASE_COLUMNS)
    write_csv(jobs_csv, job_rows, JOB_COLUMNS)
    write_csv(weave_status_csv, weave_status_rows, WEAVE_STATUS_COLUMNS)

    color_map_csv = manifests_dir / "color_map.csv"
    with open(color_map_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["token", "hex"])
        writer.writeheader()
        for token, hexv in TOKEN_HEX.items():
            if token == "w":
                continue
            writer.writerow({"token": token, "hex": hexv})

    return {
        "suite_dir": suite_dir,
        "sources_dir": sources_dir,
        "woven_dir": woven_dir,
        "cases_csv": cases_csv,
        "jobs_csv": jobs_csv,
        "weave_status_csv": weave_status_csv,
        "color_map_csv": color_map_csv,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate deterministic woven/non-woven benchmark cases.")
    p.add_argument("--out-dir", required=True, help="Output benchmark suite directory")
    p.add_argument("--skip-weave", action="store_true", help="Generate source cases only, skip woven outputs")
    p.add_argument("--strict-weave", action="store_true", help="Fail immediately if any headless weave job fails")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = generate_suite(
        Path(args.out_dir),
        generate_woven=not args.skip_weave,
        verbose=bool(args.verbose),
        strict_weave=bool(args.strict_weave),
    )
    print(f"[bench] suite dir: {out['suite_dir']}")
    print(f"[bench] weave python: {benchmark_python()}")
    print(f"[bench] cases csv: {out['cases_csv']}")
    print(f"[bench] jobs csv:  {out['jobs_csv']}")
    print(f"[bench] weave csv: {out['weave_status_csv']}")
    if not args.skip_weave:
        print(f"[bench] woven dir: {out['woven_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
