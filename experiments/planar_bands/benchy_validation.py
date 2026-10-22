"""Validate reconstructed Benchy geometry with the installed PrusaSlicer CLI.

Run from the repository root with PYTHONPATH=src. By default this consumes
results/benchy_repaired.3mf and its .report.json; --input rebuilds them first.
This is a single-extruder geometry smoke check, not a multicolor print test.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import time

import numpy as np
import shapely
import trimesh

from layerloom.contour_repair import reconstruct_file
from layerloom.ingest import read_parts
from slicer_probe import inspect_gcode


RESULTS = Path(__file__).resolve().parent / "results"


def file_record(path):
    path = Path(path).resolve()
    result = {"path": str(path), "exists": path.is_file()}
    if path.is_file():
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        result.update(sha256=digest.hexdigest(), bytes=path.stat().st_size)
    return result


def maximum(items, key):
    return max((item.get(key, 0.0) for item in items), default=0.0)


def geometry_metrics(report):
    profiles = [layer for part in report.get("parts", []) for layer in part.get("layers", [])]
    layers = report.get("layers", [])
    materials = [material for layer in layers for material in layer.get("materials", [])]
    extrusions = [material.get("extrusion", {}) for material in materials]
    partitions = [layer.get("partition", {}) for layer in layers]
    overlaps = [overlap for partition in partitions for overlap in partition.get("overlaps", [])]
    grid_changes = [change for partition in partitions for change in partition.get("input_grid_adjustments", [])]
    seam_changes = [change for partition in partitions for change in partition.get("material_changes", [])]
    normalized = [e for e in extrusions if e.get("quantization_normalization_passes", 0)]
    return {
        "input_parts": len(report.get("parts", [])),
        "source_profile_count": len(profiles),
        "source_profile_statuses": dict(sorted(Counter(p.get("status", "missing") for p in profiles).items())),
        "failed_source_profiles": sum(not p.get("ok", False) for p in profiles),
        "max_contour_deviation_measured_mm": maximum(profiles, "max_contour_deviation_mm"),
        "max_contour_deviation_bound_mm": maximum(profiles, "contour_deviation_bound_mm"),
        "max_contour_symmetric_difference_mm2": maximum(profiles, "contour_symmetric_difference_mm2"),
        "exported_bands": report.get("exported_bands", 0),
        "material_band_records": len(materials),
        "nonvolume_bands": sum(not m.get("is_volume", False) for m in materials),
        "zero_area_triangles": sum(m.get("zero_area_triangles", 0) for m in materials),
        "duplicate_faces": sum(m.get("duplicate_faces", 0) for m in materials),
        "normalization": {
            "bands_normalized": len(normalized),
            "passes": sum(e.get("quantization_normalization_passes", 0) for e in extrusions),
            "max_xy_displacement_mm": maximum(extrusions, "max_xy_displacement_mm"),
            "max_area_error_mm2": maximum(extrusions, "quantization_area_error_mm2"),
            "sum_area_error_mm2": sum(e.get("quantization_area_error_mm2", 0) for e in extrusions),
            "bands_with_component_count_change": sum(e.get("quantization_components_before") != e.get("quantization_components_after") for e in extrusions),
            "bands_with_hole_count_change": sum(e.get("quantization_holes_before") != e.get("quantization_holes_after") for e in extrusions),
            "bands_with_geometric_point_contacts": sum(bool(e.get("geometric_point_contacts")) for e in extrusions),
            "geometric_component_contacts": sum(e.get("geometric_component_contacts", 0) for e in extrusions),
            "split_vertex_fans": sum(e.get("split_vertex_fans", 0) for e in extrusions),
        },
        "roundtrip_max_band_footprint_error_mm2": report.get("roundtrip_max_footprint_error_mm2"),
        "roundtrip_max_complete_layer_error_mm2": report.get("roundtrip_max_complete_layer_error_mm2"),
        "roundtrip_footprint_method": report.get("roundtrip_footprint_method"),
        "roundtrip_max_missing_area_mm2": report.get("roundtrip_max_missing_area_mm2"),
        "roundtrip_max_extra_area_mm2": report.get("roundtrip_max_extra_area_mm2"),
        "roundtrip_residual_overlaps": {
            "pairs": len(report.get("roundtrip_residual_material_overlaps", [])),
            "max_area_mm2": maximum(report.get("roundtrip_residual_material_overlaps", []), "area_mm2"),
            "sum_area_mm2": sum(o["area_mm2"] for o in report.get("roundtrip_residual_material_overlaps", [])),
        },
        "seams": {
            "overlap_pairs": len(overlaps),
            "resolved_overlap_pairs": sum(bool(o.get("resolved")) for o in overlaps),
            "unresolved_overlap_pairs": sum(not o.get("resolved", False) for o in overlaps),
            "max_overlap_area_mm2": maximum(overlaps, "area_mm2"),
            "sum_overlap_area_mm2": sum(o.get("area_mm2", 0) for o in overlaps),
            "max_material_reassigned_area_mm2": maximum(seam_changes, "reassigned_area_mm2"),
            "sum_material_reassigned_area_mm2": sum(c.get("reassigned_area_mm2", 0) for c in seam_changes),
            "max_partition_union_error_mm2": maximum(partitions, "union_error_mm2"),
            "max_boolean_residual_overlap_mm2": maximum(partitions, "max_boolean_residual_overlap_mm2"),
            "max_input_grid_area_change_mm2": maximum(grid_changes, "changed_area_mm2"),
            "max_outside_grid_budget_mm2": maximum(grid_changes, "outside_grid_budget_mm2"),
        },
        "reconstruction_errors": report.get("errors", []),
        "reconstruction_warning_count": len(report.get("warnings", [])),
    }


def inspect_layer_grid(gcode, report, tolerance):
    """Require a one-to-one ordered Z match and extrusion on every report layer."""
    indices = sorted({int(layer["index"]) for layer in report["layers"]
                      if any(material.get("area_mm2", 0) > 0 for material in layer.get("materials", []))})
    expected = [report["z_origin_mm"] + (index + 1) * report["layer_height_mm"] for index in indices]
    actual = gcode.get("layers", [])
    comparisons = []
    for position, (index, z) in enumerate(zip(indices, expected)):
        layer = actual[position] if position < len(actual) else {}
        actual_z = layer.get("z")
        error = abs(actual_z - z) if actual_z is not None else None
        comparisons.append({"source_layer_index": index, "expected_z_mm": z,
                            "actual_z_mm": actual_z, "z_error_mm": error,
                            "xy_extrusion_moves": layer.get("xy_extrusion_moves", 0),
                            "ok": error is not None and error <= tolerance
                            and layer.get("xy_extrusion_moves", 0) > 0})
    return {"expected_layer_count": len(expected), "expected_z_mm": expected,
            "z_tolerance_mm": tolerance, "layer_checks": comparisons,
            "extra_actual_layers": actual[len(expected):],
            "exact_expected_grid_with_extrusion": bool(expected) and len(actual) == len(expected)
            and all(check["ok"] for check in comparisons)}


def run_slicer(binary, model, directory, layer_height, timeout):
    directory.mkdir(parents=True, exist_ok=True)
    gcode, settings, log_path = directory / "output.gcode", directory / "settings.ini", directory / "slicer.log"
    if Path(model).resolve() in {gcode.resolve(), settings.resolve(), log_path.resolve()}:
        raise ValueError("Slicer output paths must not overwrite its input")
    gcode.unlink(missing_ok=True)
    settings.unlink(missing_ok=True)
    # These are the same generic geometry settings as slicer_probe.py. The
    # temporary datadir prevents installed user or previous-run profile state.
    with tempfile.TemporaryDirectory(prefix="isolated-config-", dir=directory) as datadir:
        command = [str(binary), "--export-gcode", "--dont-arrange", "--no-ensure-on-bed",
                   "--no-variable-layer-height", "--layer-height", str(layer_height),
                   "--first-layer-height", str(layer_height), "--no-binary-gcode",
                   "--nozzle-diameter", "0.4", "--filament-diameter", "1.75", "--skirts", "0",
                   "--datadir", datadir, "--save", str(settings), "--output", str(gcode), str(model)]
        started = time.perf_counter()
        try:
            process = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
            returncode, log = process.returncode, process.stdout + process.stderr
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            def decoded(value):
                return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
            returncode, log = None, decoded(exc.stdout) + decoded(exc.stderr)
            timed_out = True
        elapsed = time.perf_counter() - started
    log_path.write_text(log)
    return {"command": command, "returncode": returncode, "timed_out": timed_out,
            "elapsed_s": elapsed, "log": str(log_path), "settings": file_record(settings),
            "gcode": file_record(gcode), **inspect_gcode(gcode)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repaired", type=Path, default=RESULTS / "benchy_repaired.3mf")
    parser.add_argument("--report", type=Path, help="Reconstruction report; defaults to REPAIRED.report.json")
    parser.add_argument("--output", type=Path, default=RESULTS / "benchy_validation.json")
    parser.add_argument("--input", type=Path, help="Optionally reconstruct this source before validation")
    parser.add_argument("--layer-height", type=float, default=0.2, help="Reconstruction height when --input is used")
    parser.add_argument("--source-control", action="store_true", help="Also slice the original as an independent height comparison")
    parser.add_argument("--slicer", type=Path, default=Path(shutil.which("prusa-slicer") or "/Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer"))
    parser.add_argument("--timeout", type=float, default=180, help="Timeout in seconds for each slicer run")
    parser.add_argument("--z-tolerance", type=float, default=1e-5, help="Tolerance for G-code layer Z comparisons in mm")
    args = parser.parse_args(argv)
    for name, value in (("timeout", args.timeout), ("z-tolerance", args.z_tolerance), ("layer-height", args.layer_height)):
        if not math.isfinite(value) or value <= 0:
            parser.error(f"--{name} must be finite and positive")
    repaired, output = args.repaired.resolve(), args.output.resolve()
    report_path = args.report.resolve() if args.report else repaired.with_suffix(".report.json")
    if len({repaired, output, report_path}) != 3 or (args.input and args.input.resolve() in {repaired, output, report_path}):
        parser.error("Input, repaired model, reconstruction report, and validation report must use separate paths")
    output.parent.mkdir(parents=True, exist_ok=True)
    run_directory = output.parent / (output.stem + "_run")
    result = {
        "status": "failed", "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Direct native-3MF geometry consumption with a generic single-extruder PrusaSlicer profile",
        "versions": {"python": platform.python_version(), "numpy": np.__version__,
                     "shapely": shapely.__version__, "geos": shapely.geos_version_string,
                     "trimesh": trimesh.__version__},
        "caveats": [
            "No physical print, printer hardware profile, or multicolor extruder assignment is validated.",
            "Exact layer Z and nonempty XY extrusion do not prove G-code footprint fidelity or retained fine features.",
            "Midpoint resampling can lose sub-layer features or change a partial top; original-source layer counts are an independent control, not a pass criterion.",
            "Reported geometry metrics come from the supplied reconstruction report; file hashes identify this validation's exact inputs.",
            "The recorded command uses a temporary isolated datadir that is removed after each run; the resolved settings file is retained.",
        ],
        "errors": [],
    }
    try:
        if args.input:
            capture = io.StringIO()
            with redirect_stdout(capture):
                report = reconstruct_file(args.input, repaired, layer_height=args.layer_height, report_path=report_path)
            run_directory.mkdir(parents=True, exist_ok=True)
            (run_directory / "reconstruction.log").write_text(capture.getvalue())
        else:
            report = json.loads(report_path.read_text())
        result["reconstruction_report"] = file_record(report_path)
        result["repaired_model"] = file_record(repaired)
        source = Path(report["input"]).resolve()
        result["source"] = file_record(source)
        result["geometry"] = geometry_metrics(report)
        result["reconstruction_parameters"] = {key: report.get(key) for key in (
            "layer_height_mm", "z_origin_mm", "max_gap_mm", "precision_mm", "z_gap_mm",
            "contour_tolerance_mm", "seam_tolerance_mm", "fallback_recipe")}
        if report.get("status") != "ok" or report.get("errors"):
            raise ValueError("Reconstruction report is not successful; no slicer validation was attempted")
        if not repaired.is_file():
            raise FileNotFoundError(repaired)
        if report.get("output_sha256") != result["repaired_model"]["sha256"]:
            raise ValueError("Repaired model does not match the reconstruction report's output hash; reconstruct again")
        version = subprocess.run([str(args.slicer), "--help"], capture_output=True, text=True, timeout=30)
        version_text = version.stdout + version.stderr
        result["versions"]["prusa_slicer"] = next((line for line in version_text.splitlines() if "PrusaSlicer" in line), version_text.splitlines()[0] if version_text else "unknown")
        result["slicer_binary"] = str(args.slicer.resolve())
        sliced = run_slicer(args.slicer, repaired, run_directory / "repaired", report["layer_height_mm"], args.timeout)
        sliced.update(inspect_layer_grid(sliced, report, args.z_tolerance))
        result["repaired_slicer"] = sliced
        if sliced["returncode"] != 0 or not sliced["exact_expected_grid_with_extrusion"]:
            result["errors"].append("Repaired model did not produce exactly the expected layer Zs with XY extrusion on every layer")
        if args.source_control:
            try:
                control = run_slicer(args.slicer, source, run_directory / "source_control", report["layer_height_mm"], args.timeout)
                control["required_for_success"] = False
                control["status"] = "ok" if control["returncode"] == 0 and control.get("layers_with_xy_extrusion", 0) > 0 else "failed"
                control["repaired_minus_source_layer_count"] = sliced.get("layer_count", 0) - control.get("layer_count", 0)
                result["source_control"] = control
                # Some repository 3MF inputs use packaging this Prusa build
                # rejects before slicing. Also compare the original transformed
                # triangles as one STL, without contour reconstruction or repair.
                source_mesh = trimesh.util.concatenate([mesh for _, mesh in read_parts([str(source)])])
                geometry_path = run_directory / "source_geometry_only.stl"
                source_mesh.export(geometry_path)
                geometry_control = run_slicer(args.slicer, geometry_path,
                    run_directory / "source_geometry_control", report["layer_height_mm"], args.timeout)
                geometry_control["required_for_success"] = False
                geometry_control["conversion"] = "Source build transforms baked into one binary STL; no contour reconstruction or repair, no material labels"
                geometry_control["source_geometry"] = file_record(geometry_path)
                geometry_control["status"] = "ok" if geometry_control["returncode"] == 0 and geometry_control.get("layers_with_xy_extrusion", 0) > 0 else "failed"
                geometry_control["repaired_minus_source_layer_count"] = sliced.get("layer_count", 0) - geometry_control.get("layer_count", 0)
                result["source_geometry_control"] = geometry_control
            except Exception as exc:
                result["source_control"] = {"required_for_success": False, "error": f"{type(exc).__name__}: {exc}"}
        if not result["errors"]:
            result["status"] = "ok"
    except Exception as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"status": result["status"], "report": str(output), "errors": result["errors"]}, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
