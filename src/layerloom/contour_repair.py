"""Experimental fixed-layer reconstruction CLI; independent of the production weave path.

Input parts must describe volumetric material regions, even when their mesh
boundaries are defective. Open surface-color patches are not an interior field.
PAT recipes in part labels take precedence over the fallback --run recipe.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
import os
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
from shapely import union_all
import trimesh

from layerloom.export import write_basic_3mf
from layerloom.ingest import read_parts
from layerloom.planar_bands import RepairConfig, extrude_profile, reconstruct_layers, section_profile
from layerloom.tokens import TOKEN_ALPHABET, COLOR_OBJECT_LABELS


def _package_native_volumes(path: Path) -> None:
    """Pack bands into one mesh with PrusaSlicer's inclusive triangle ranges."""
    namespace = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    tag = lambda name: f"{{{namespace}}}{name}"
    with zipfile.ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    member = "3D/3dmodel.model"
    root = ET.fromstring(entries[member])
    resources, build = root.find(tag("resources")), root.find(tag("build"))
    original = list(resources.findall(tag("object")))
    resources.clear()
    parent = ET.SubElement(resources, tag("object"), {"id": "1", "type": "model", "name": "Contour reconstruction"})
    mesh = ET.SubElement(parent, tag("mesh"))
    vertices, triangles = ET.SubElement(mesh, tag("vertices")), ET.SubElement(mesh, tag("triangles"))
    config = ET.Element("config")
    cfg_object = ET.SubElement(config, "object", {"id": "1", "instances_count": "1"})
    ET.SubElement(cfg_object, "metadata", {"type": "object", "key": "name", "value": "Contour reconstruction"})
    for obj in original:
        vertex_offset, first_face = len(vertices), len(triangles)
        source = obj.find(tag("mesh"))
        vertices.extend(list(source.find(tag("vertices"))))
        for triangle in source.find(tag("triangles")):
            ET.SubElement(triangles, tag("triangle"), {key: str(int(triangle.get(key)) + vertex_offset) for key in ("v1", "v2", "v3")})
        volume = ET.SubElement(cfg_object, "volume", {"firstid": str(first_face), "lastid": str(len(triangles) - 1)})
        for key, value in (("name", obj.get("name", "band")), ("volume_type", "ModelPart")):
            ET.SubElement(volume, "metadata", {"type": "volume", "key": key, "value": value})
    build.clear()
    ET.SubElement(build, tag("item"), {"objectid": "1"})
    version = ET.Element(tag("metadata"), {"name": "slic3rpe:Version3mf"})
    version.text = "1"
    root.insert(0, version)
    entries[member] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    entries["Metadata/Slic3r_PE_model.config"] = ET.tostring(config, encoding="utf-8", xml_declaration=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


def _read_native_bands(path: Path):
    """Unpack this prototype's native ranges; ordinary ingest sees one mesh."""
    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("3D/3dmodel.model"))
        config = ET.fromstring(archive.read("Metadata/Slic3r_PE_model.config"))
    vertices = np.array([[float(v.get(a)) for a in ("x", "y", "z")] for v in root.findall(".//m:vertex", ns)])
    faces = np.array([[int(f.get(a)) for a in ("v1", "v2", "v3")] for f in root.findall(".//m:triangle", ns)])
    result, cursor = [], 0
    for volume in config.findall("./object/volume"):
        first, last = int(volume.get("firstid")), int(volume.get("lastid"))
        if first != cursor or last < first or last >= len(faces):
            raise ValueError("Exported native volume ranges are incomplete or overlapping")
        cursor = last + 1
        name = next(md.get("value") for md in volume.findall("metadata") if md.get("key") == "name")
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces[first:last + 1], process=False)
        mesh.remove_unreferenced_vertices()
        result.append((name, mesh))
    if cursor != len(faces):
        raise ValueError("Exported native volume ranges do not cover every triangle")
    return result


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, suffix=".json", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _float32_mesh_checks(mesh: trimesh.Trimesh) -> dict:
    """Validate coordinates as a slicer may see them, after decimal XML export."""
    vertices = np.round(np.asarray(mesh.vertices), 9).astype(np.float32).astype(np.float64)
    faces = np.asarray(mesh.faces)
    triangles = vertices[faces]
    area_vectors = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    _, coordinate_ids = np.unique(vertices, axis=0, return_inverse=True)
    canonical_faces = np.sort(coordinate_ids[faces], axis=1)
    _, counts = np.unique(canonical_faces, axis=0, return_counts=True)
    rounded = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    return {
        "is_volume": bool(rounded.is_volume),
        "zero_area_triangles": int(np.count_nonzero(np.all(area_vectors == 0, axis=1))),
        "duplicate_faces": int(np.sum(counts - 1)),
        "vertices": int(len(vertices)),
        "triangles": int(len(faces)),
    }


def reconstruct_file(input_path: Path, output_path: Path, *, layer_height: float,
                     max_gap: float = 0.03, precision: float = 1e-5,
                     run: str = "cmy", z_gap: float = 0.001,
                     report_path: Path | None = None) -> dict:
    """Write complete valid output atomically, or a failure report with no new 3MF."""
    input_path, output_path = Path(input_path).resolve(), Path(output_path).resolve()
    report_path = Path(report_path).resolve() if report_path else output_path.with_suffix(".report.json")
    if len({input_path, output_path, report_path}) != 3:
        raise ValueError("Input, output, and report paths must be different")
    if output_path.suffix.lower() != ".3mf":
        raise ValueError("Output must have the .3mf extension")
    if not math.isfinite(layer_height) or layer_height <= 0:
        raise ValueError("layer_height must be finite and positive")
    if not math.isfinite(z_gap) or not 0 <= z_gap < layer_height:
        raise ValueError("z_gap must be finite and satisfy 0 <= z_gap < layer_height")
    run = run.lower()
    if not run or any(token not in TOKEN_ALPHABET for token in run):
        raise ValueError(f"run must contain only {TOKEN_ALPHABET}")
    config = RepairConfig(precision=precision, max_gap=max_gap)
    report = {
        "experimental": True, "status": "failed", "input": str(input_path),
        "output": str(output_path), "layer_height_mm": layer_height,
        "max_gap_mm": max_gap, "precision_mm": precision, "z_gap_mm": z_gap,
        "fallback_recipe": run, "recipe_policy": "last PAT label, otherwise fallback recipe",
        "packaging": "one mesh object with named per-layer PrusaSlicer volume ranges",
        "sampling": "global layer midpoint; piecewise constant XY profile through each band",
        "limitations": ["Short features between sample planes can disappear.",
                        "Surface color patches do not define volumetric regions.",
                        "Downstream first and subsequent layer heights and Z origin must match; adaptive layers must be disabled.",
                        "Internal Z gaps avoid coincident band caps but are an explicit geometry change.",
                        "Native volume names preserve recipes, but physical extruders must be assigned in the slicer.",
                        "Ordinary LayerLoom ingest and other slicers may ignore the native volume ranges and see one uncolored mesh."],
        "parts": [], "layers": [], "errors": [], "warnings": [],
    }
    if z_gap == 0:
        report["warnings"].append("z_gap=0 permits coincident caps between bands; per-band validation does not establish a clean whole-mesh union")
    try:
        if input_path.suffix.lower() == ".3mf":
            with zipfile.ZipFile(input_path) as archive:
                for member in archive.namelist():
                    if member.lower().endswith(".model"):
                        unit = ET.fromstring(archive.read(member)).get("unit", "millimeter")
                        if unit != "millimeter":
                            raise ValueError(f"Prototype accepts only millimeter 3MF models; {member} declares {unit!r}")
        parts = read_parts([str(input_path)])
        if not parts:
            raise ValueError("Input contains no mesh parts")
        z0 = float(min(mesh.bounds[0, 2] for _, mesh in parts))
        zmax = float(max(mesh.bounds[1, 2] for _, mesh in parts))
        report["z_origin_mm"] = z0
        last_layer = math.ceil((zmax - z0) / layer_height) - 1
        by_layer = defaultdict(lambda: defaultdict(list))
        for label, mesh in parts:
            tokens = re.findall(r"__PAT_(.*?)__", label, flags=re.IGNORECASE)
            recipe = tokens[-1].lower() if tokens else run
            if ("__PAT_" in label.upper() and not tokens) or not recipe or any(token not in TOKEN_ALPHABET for token in recipe):
                raise ValueError(f"Part {label!r} has an empty or unsupported PAT recipe")
            part_report = {"label": label, "recipe": recipe, "input_is_volume": bool(mesh.is_volume), "layers": []}
            report["parts"].append(part_report)
            for band in reconstruct_layers(mesh, layer_height, z0=z0, config=config):
                part_report["layers"].append({"index": band.index, "sample_z_mm": band.sample_z,
                                               "ok": band.profile.ok, **band.profile.diagnostics})
                if not band.profile.ok:
                    report["errors"].append(f"Part {label!r}, layer {band.index}: unresolved contour")
                elif not band.profile.region.is_empty:
                    by_layer[band.index][recipe[band.index % len(recipe)]].append(band.profile.region)
                else:
                    report["warnings"].append(f"Part {label!r}, layer {band.index}: empty midpoint sample; sub-layer features may be missed")

        export_parts = []
        intended_profiles = {}
        for index, token_regions in sorted(by_layer.items()):
            regions = {token: union_all(regions) for token, regions in token_regions.items()}
            layer_report = {"index": index, "sample_z_mm": z0 + (index + 0.5) * layer_height,
                            "materials": [], "overlaps": []}
            report["layers"].append(layer_report)
            tokens = sorted(regions)
            for i, token in enumerate(tokens):
                for other in tokens[i + 1:]:
                    overlap = float(regions[token].intersection(regions[other]).area)
                    if overlap > 0:
                        layer_report["overlaps"].append({"tokens": [token, other], "area_mm2": overlap})
                        report["errors"].append(f"Layer {index}: material overlap {token}/{other} ({overlap:g} mm²)")
            low = z0 + index * layer_height + (z_gap / 2 if index > 0 else 0)
            high = z0 + (index + 1) * layer_height - (z_gap / 2 if index < last_layer else 0)
            for token, region in sorted(regions.items()):
                mesh = extrude_profile(region, low, high)
                if mesh is None:
                    report["errors"].append(f"Layer {index}/{token}: extrusion produced no mesh")
                    continue
                checks = _float32_mesh_checks(mesh)
                layer_report["materials"].append({"token": token, "area_mm2": float(region.area),
                                                    "low_mm": low, "high_mm": high, **checks})
                if not checks["is_volume"] or checks["zero_area_triangles"] or checks["duplicate_faces"]:
                    report["errors"].append(f"Layer {index}/{token}: exported float32 geometry failed validation")
                name = f"{COLOR_OBJECT_LABELS[token]}__layer{index:05d}__PAT_{token}__"
                export_parts.append((name, mesh))
                intended_profiles[name] = (region, z0 + (index + 0.5) * layer_height)
        if not export_parts:
            report["errors"].append("No printable layer regions were reconstructed")
        if not report["errors"]:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=output_path.parent, suffix=".3mf", delete=False) as stream:
                temporary = Path(stream.name)
            try:
                write_basic_3mf(export_parts, str(temporary), title="LayerLoom experimental contour reconstruction")
                _package_native_volumes(temporary)
                reimported = _read_native_bands(temporary)
                if len(reimported) != len(export_parts):
                    raise ValueError("Export round trip changed the number of bands")
                largest_error = 0.0
                combined_expected, combined_actual = defaultdict(list), defaultdict(list)
                for name, mesh in reimported:
                    expected, sample_z = intended_profiles[name]
                    checks = _float32_mesh_checks(mesh)
                    if not checks["is_volume"] or checks["zero_area_triangles"] or checks["duplicate_faces"]:
                        raise ValueError(f"Reimported band {name!r} failed float32 validation")
                    mesh.vertices = np.asarray(mesh.vertices).astype(np.float32).astype(np.float64)
                    profile = section_profile(mesh, sample_z, config=config)
                    error = float(expected.symmetric_difference(profile.region).area)
                    largest_error = max(largest_error, error)
                    if not profile.ok or error > 2 * precision * expected.length + precision ** 2:
                        raise ValueError(f"Reimported band {name!r} changed its midpoint footprint ({error:g} mm²)")
                    combined_expected[sample_z].append(expected)
                    combined_actual[sample_z].append(profile.region)
                whole_errors = []
                for sample_z, regions in combined_expected.items():
                    expected = union_all(regions)
                    actual = union_all(combined_actual[sample_z])
                    error = float(expected.symmetric_difference(actual).area)
                    if error > 2 * precision * expected.length + precision ** 2:
                        raise ValueError(f"Exported complete-layer footprint changed at Z={sample_z:g}")
                    whole_errors.append(error)
                report["roundtrip_max_footprint_error_mm2"] = largest_error
                report["roundtrip_max_complete_layer_error_mm2"] = max(whole_errors, default=0.0)
                os.replace(temporary, output_path)
            finally:
                temporary.unlink(missing_ok=True)
            report["status"] = "ok"
            report["exported_bands"] = len(export_parts)
    except Exception as exc:
        report["errors"].append(f"{type(exc).__name__}: {exc}")
    _atomic_json(report_path, report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--layer-height", type=float, required=True)
    parser.add_argument("--max-gap", type=float, default=0.03, help="Maximum XY bridge length in mm")
    parser.add_argument("--precision", type=float, default=1e-5, help="XY snapping grid in mm")
    parser.add_argument("--run", default="cmy", help="Fallback weave recipe for parts without PAT labels")
    parser.add_argument("--z-gap", type=float, default=0.001, help="Gap between adjacent band intervals in mm")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        report = reconstruct_file(args.input, args.output, layer_height=args.layer_height,
                                  max_gap=args.max_gap, precision=args.precision, run=args.run,
                                  z_gap=args.z_gap, report_path=args.report)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps({"status": report["status"], "output": report["output"], "errors": report["errors"]}, indent=2))
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
