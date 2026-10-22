"""Experimental fixed-layer reconstruction CLI; independent of the production weave path.

Input parts must describe volumetric material regions, even when their mesh
boundaries are defective. Open surface-color patches are not an interior field.
PAT recipes in part labels take precedence over the fallback --run recipe.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
from shapely import polygons, union_all
import trimesh

from layerloom.export import write_basic_3mf
from layerloom.ingest import read_parts
from layerloom.planar_bands import RepairConfig, extrude_profile, reconstruct_layer_profiles
from layerloom.planar_materials import partition_materials
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
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         suffix=".json", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        # Windows cannot unlink an open NamedTemporaryFile, including on errors.
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _same_file(left: Path, right: Path) -> bool:
    """Compare resolved names and existing file identity on any filesystem."""
    if left == right:
        return True
    try:
        return left.samefile(right)
    except FileNotFoundError:
        return False


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


def _exported_band_footprint(mesh: trimesh.Trimesh):
    """Measure an exported prism without adding another XY snapping grid.

    Equal top/bottom cap unions and vertical sides establish a constant XY
    footprint through this prototype's band. This also retains microscopic
    seams which an ordinary section reconstruction could snap away.
    """
    triangles = np.asarray(mesh.vertices)[mesh.faces]
    low, high = mesh.bounds[:, 2]
    top = np.all(triangles[:, :, 2] == high, axis=1)
    bottom = np.all(triangles[:, :, 2] == low, axis=1)
    if high <= low or not top.any() or not bottom.any():
        raise ValueError("Exported band has no complete top and bottom caps")
    sides = triangles[~(top | bottom), :, :2]
    a, b = sides[:, 1] - sides[:, 0], sides[:, 2] - sides[:, 0]
    if np.any(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0] != 0):
        raise ValueError("Exported band contains nonvertical sides")
    upper = union_all(polygons(triangles[top, :, :2]), grid_size=0)
    lower = union_all(polygons(triangles[bottom, :, :2]), grid_size=0)
    if upper.symmetric_difference(lower).area != 0:
        raise ValueError("Exported band's top and bottom footprints differ")
    return upper


def _exported_seams(actual, expected, displacements, *, precision):
    """Report residual rounding overlaps; reject changes beyond both seams."""
    overlaps = []
    names = sorted(actual)
    for i, name in enumerate(names):
        for other in names[i + 1:]:
            overlap = actual[name].intersection(actual[other])
            if overlap.area == 0:
                continue
            # Either neighbor may move while the other is exactly on-lattice.
            # A circumscribed distance buffer covers the sum of their moves.
            radius = (displacements[name] + displacements[other] + precision * 1e-6) / math.cos(3 * math.pi / 128)
            outside = max(overlap.difference(expected[n].boundary.buffer(radius)).area
                          for n in (name, other))
            if outside > precision ** 2 * 0.01:
                raise ValueError(f"Exported materials {name}/{other} overlap beyond rounding bounds")
            overlaps.append({"bands": [name, other], "area_mm2": float(overlap.area),
                             "boundary_budget_mm": radius})
    return overlaps


def reconstruct_file(input_path: Path, output_path: Path, *, layer_height: float,
                     max_gap: float = 0.03, precision: float = 1e-5,
                     contour_tolerance: float = 0.001, seam_tolerance: float = 0.001,
                     run: str = "cmy", z_gap: float = 0.001,
                     report_path: Path | None = None) -> dict:
    """Write complete valid output atomically, or a failure report with no new 3MF."""
    input_path, output_path = Path(input_path).resolve(), Path(output_path).resolve()
    report_path = Path(report_path).resolve() if report_path else output_path.with_suffix(".report.json")
    if any(_same_file(left, right) for left, right in
           ((input_path, output_path), (input_path, report_path), (output_path, report_path))):
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
    config = RepairConfig(precision=precision, max_gap=max_gap, contour_tolerance=contour_tolerance)
    if not math.isfinite(seam_tolerance) or seam_tolerance < 0:
        raise ValueError("seam_tolerance must be finite and nonnegative")
    report = {
        "experimental": True, "status": "failed", "input": str(input_path),
        "output": str(output_path), "layer_height_mm": layer_height,
        "max_gap_mm": max_gap, "precision_mm": precision, "z_gap_mm": z_gap,
        "contour_tolerance_mm": contour_tolerance, "seam_tolerance_mm": seam_tolerance,
        "fallback_recipe": run, "recipe_policy": "last PAT label, otherwise fallback recipe",
        "packaging": "one mesh object with named per-layer PrusaSlicer volume ranges",
        "sampling": "global layer midpoint; piecewise constant XY profile through each band",
        "geometry_pipeline": "recover source contours, partition materials, extrude final material regions once",
        "limitations": ["Short features between sample planes can disappear.",
                        "Surface color patches do not define volumetric regions.",
                        "Downstream first and subsequent layer heights and Z origin must match; adaptive layers must be disabled.",
                        "Internal Z gaps avoid coincident band caps but are an explicit geometry change.",
                        "Float32 export can leave measured, bounded residual overlaps or gaps between materials.",
                        "Separate mesh indices preserve some geometric point contacts; a clean geometric union is not certified.",
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
        for label, mesh in parts:
            bounds = np.asarray(mesh.bounds)
            if bounds.shape != (2, 3) or not np.isfinite(bounds).all():
                raise ValueError(f"Part {label!r} must have finite, nonempty mesh bounds")
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
            for band in reconstruct_layer_profiles(mesh, layer_height, z0=z0, config=config):
                part_report["layers"].append({"index": band.index, "sample_z_mm": band.sample_z,
                                               "ok": band.profile.ok, **band.profile.diagnostics})
                if not band.profile.ok:
                    reason = band.profile.diagnostics.get("error", "unresolved contour")
                    report["errors"].append(f"Part {label!r}, layer {band.index}: {reason}")
                elif not band.profile.region.is_empty:
                    by_layer[band.index][recipe[band.index % len(recipe)]].append(band.profile.region)
                else:
                    report["warnings"].append(f"Part {label!r}, layer {band.index}: empty midpoint sample; sub-layer features may be missed")

        export_parts = []
        intended_profiles = {}
        export_displacements = {}
        for index, token_regions in sorted(by_layer.items()):
            regions, partition, partition_errors = partition_materials(
                token_regions, precision=precision, seam_tolerance=seam_tolerance)
            layer_report = {"index": index, "sample_z_mm": z0 + (index + 0.5) * layer_height,
                            "materials": [], "overlaps": partition["overlaps"], "partition": partition}
            report["layers"].append(layer_report)
            report["errors"].extend(f"Layer {index}: {error}" for error in partition_errors)
            low = z0 + index * layer_height + (z_gap / 2 if index > 0 else 0)
            high = z0 + (index + 1) * layer_height - (z_gap / 2 if index < last_layer else 0)
            for token, region in sorted(regions.items()):
                mesh = extrude_profile(region, low, high)
                if mesh is None:
                    report["errors"].append(f"Layer {index}/{token}: extrusion produced no mesh")
                    continue
                checks = _float32_mesh_checks(mesh)
                layer_report["materials"].append({"token": token, "area_mm2": float(region.area),
                                                    "low_mm": low, "high_mm": high,
                                                    "extrusion": mesh.metadata.get("extrusion", {}), **checks})
                if not checks["is_volume"] or checks["zero_area_triangles"] or checks["duplicate_faces"]:
                    report["errors"].append(f"Layer {index}/{token}: exported float32 geometry failed validation")
                name = f"{COLOR_OBJECT_LABELS[token]}__layer{index:05d}__PAT_{token}__"
                export_parts.append((name, mesh))
                intended_profiles[name] = (region, z0 + (index + 0.5) * layer_height)
                export_displacements[name] = mesh.metadata["extrusion"]["max_xy_displacement_mm"]
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
                combined_expected, combined_actual = defaultdict(dict), defaultdict(dict)
                for name, mesh in reimported:
                    expected, sample_z = intended_profiles[name]
                    checks = _float32_mesh_checks(mesh)
                    if not checks["is_volume"] or checks["zero_area_triangles"] or checks["duplicate_faces"]:
                        raise ValueError(f"Reimported band {name!r} failed float32 validation")
                    mesh.vertices = np.asarray(mesh.vertices).astype(np.float32).astype(np.float64)
                    actual = _exported_band_footprint(mesh)
                    error = float(expected.symmetric_difference(actual).area)
                    largest_error = max(largest_error, error)
                    if error > 2 * precision * expected.length + precision ** 2:
                        raise ValueError(f"Reimported band {name!r} changed its midpoint footprint ({error:g} mm²)")
                    combined_expected[sample_z][name] = expected
                    combined_actual[sample_z][name] = actual
                whole_errors = []
                missing_areas, extra_areas, residual_overlaps = [], [], []
                for sample_z, regions in combined_expected.items():
                    expected = union_all(list(regions.values()))
                    actual = union_all(list(combined_actual[sample_z].values()))
                    error = float(expected.symmetric_difference(actual).area)
                    if error > 2 * precision * expected.length + precision ** 2:
                        raise ValueError(f"Exported complete-layer footprint changed at Z={sample_z:g}")
                    whole_errors.append(error)
                    missing_areas.append(float(expected.difference(actual).area))
                    extra_areas.append(float(actual.difference(expected).area))
                    residual_overlaps.extend({"sample_z_mm": sample_z, **overlap} for overlap in
                        _exported_seams(combined_actual[sample_z], regions,
                                        export_displacements, precision=precision))
                report["roundtrip_max_footprint_error_mm2"] = largest_error
                report["roundtrip_max_complete_layer_error_mm2"] = max(whole_errors, default=0.0)
                report["roundtrip_footprint_method"] = "float32 cap unions; equal top/bottom footprints and vertical sides"
                report["roundtrip_max_missing_area_mm2"] = max(missing_areas, default=0.0)
                report["roundtrip_max_extra_area_mm2"] = max(extra_areas, default=0.0)
                report["roundtrip_residual_material_overlaps"] = residual_overlaps
                if residual_overlaps:
                    report["warnings"].append("Float32 export leaves bounded residual material overlaps; see roundtrip_residual_material_overlaps")
                output_sha256 = hashlib.sha256(temporary.read_bytes()).hexdigest()
                os.replace(temporary, output_path)
                report["output_sha256"] = output_sha256
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
    parser.add_argument("--contour-tolerance", type=float, default=0.001,
                        help="Maximum bounded ordered-loop cleanup in mm; zero disables")
    parser.add_argument("--seam-tolerance", type=float, default=0.001,
                        help="Boundary band permitting microscopic material-overlap assignment in mm")
    parser.add_argument("--run", default="cmy", help="Fallback weave recipe for parts without PAT labels")
    parser.add_argument("--z-gap", type=float, default=0.001, help="Gap between adjacent band intervals in mm")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        report = reconstruct_file(args.input, args.output, layer_height=args.layer_height,
                                  max_gap=args.max_gap, precision=args.precision, run=args.run,
                                  contour_tolerance=args.contour_tolerance, seam_tolerance=args.seam_tolerance,
                                  z_gap=args.z_gap, report_path=args.report)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps({"status": report["status"], "output": report["output"], "errors": report["errors"]}, indent=2))
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
