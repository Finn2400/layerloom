#!/usr/bin/env python3
"""Supplement failed generic contour audits with exact, hash-verified prism checks.

This never rewrites the controlled benchmark or changes its recorded failures.
Only native token/layers whose generic midpoint reconstruction failed are rebuilt.
Run after serial timing measurements have finished, not concurrently with them.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import controlled_worker as worker

import shapely
from shapely.geometry import Polygon
from layerloom.contour_repair import _exported_band_footprint, _float32_mesh_checks
from layerloom.planar_bands import RepairConfig, extrude_profile, section_profile
from layerloom.planar_materials import partition_materials

DEFAULT_REPORT = HERE / "results/controlled_benchmark_runs/12_benchy_0.2_contour_r1.json"
DEFAULT_OUTPUT = HERE / "results/controlled_prism_audit.json"


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _profile_summary(profile):
    return {"ok": bool(profile.ok), "status": profile.diagnostics.get("status"),
            "area_mm2": float(profile.region.area),
            "repair_applied": profile.diagnostics.get("status") in {"repaired", "contour_repaired"}}


def run(report_path=DEFAULT_REPORT, output_path=DEFAULT_OUTPUT):
    started = time.perf_counter()
    report_path, output_path = Path(report_path).resolve(), Path(output_path).resolve()
    if report_path == output_path:
        raise ValueError("Supplementary output must not overwrite the benchmark report")
    original_bytes = report_path.read_bytes()
    original = json.loads(original_bytes)
    result = {
        "status": "failed", "passed": False, "case": original.get("case"), "method": original.get("method"),
        "source_sha256": original.get("source", {}).get("sha256"),
        "measured_native_parts_sha256": original.get("fingerprints", {}).get("native_parts_sha256"),
        "checked_profiles": 0, "matching_geometry_hashes": 0, "records": [], "input_checks": [],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Supplement generic midpoint-audit failures with an exact cap/vertical-side prism check; preserve all original benchmark outcomes",
        "benchmark_report": {"path": str(report_path), "sha256": hashlib.sha256(original_bytes).hexdigest(),
                             "status": original.get("status"), "quality_status": original.get("quality_status"),
                             "common_audit_summary": original.get("audit", {}).get("summary")},
        "source": {}, "source_parts": [], "layers": [], "errors": [],
        "checks": {
            "hash_identity": "Exact original native geometry SHA256 required before interpreting supplemental checks",
            "prism_proof": "Float32/9-decimal coordinates; top and bottom cap unions exactly equal; every side triangle vertical",
            "reference": "Original source-part midpoint profiles, unioned by assigned token before material partition",
            "footprint_budget": "0.001 mm * original/reference boundary length + 1e-12 mm2 arithmetic slack",
            "scope": "Only the failed generic native profiles; no assertion of continuous source-surface equivalence or print validation",
        },
    }
    try:
        if original.get("method") != "contour" or not original.get("audit", {}).get("enabled"):
            raise ValueError("Requires a fully audited contour-worker report")
        settings = original["settings"]
        height, z0 = float(original["height_mm"]), float(settings["z0_mm"])
        z_gap = float(settings["z_gap_mm"])
        config = RepairConfig(precision=settings["precision"], max_gap=settings["max_gap"],
                              contour_tolerance=settings["contour_tolerance"])
        if config != worker.CONFIG or z_gap != worker.Z_GAP or settings["seam_tolerance"] != worker.SEAM_TOLERANCE:
            raise ValueError("Worker defaults differ from recorded settings; refusing ambiguous reproduction")
        source_path = Path(original["source"]["path"])
        before_source_hash = file_hash(source_path)
        result["source"] = {"path": str(source_path), "recorded_sha256": original["source"]["sha256"],
                            "current_sha256": before_source_hash,
                            "matches_benchmark": before_source_hash == original["source"]["sha256"]}
        if not result["source"]["matches_benchmark"]:
            raise ValueError("Source file no longer matches benchmark SHA256")
        parts, loaded_source = worker._load_case(original["case"], source_path)
        if loaded_source["sha256"] != before_source_hash:
            raise ValueError("Source changed while being loaded")
        actual_z0 = float(min(mesh.bounds[0, 2] for _, mesh in parts))
        if actual_z0 != z0:
            raise ValueError("Regenerated original global Z origin differs")
        if len(parts) != original["counts"]["source_parts"]:
            raise ValueError("Loaded source part count differs")
        result["settings"] = {"height_mm": height, "z0_mm": z0, "z_gap_mm": z_gap,
                              "precision": config.precision, "max_gap": config.max_gap,
                              "contour_tolerance": config.contour_tolerance,
                              "seam_tolerance": settings["seam_tolerance"]}
        for index, (label, mesh) in enumerate(parts):
            result["source_parts"].append({"part_index": index, "label": label,
                                           "geometry_sha256": worker._mesh_hash(mesh),
                                           "indexed_is_volume": bool(mesh.is_volume),
                                           "float32": _float32_mesh_checks(mesh)})

        failed = [record for record in original["audit"]["native_midpoint_profiles"] if not record["actual_ok"]]
        targets = defaultdict(list)
        for record in failed:
            targets[int(record["layer_index"])].append(record["token"])
        native_records = {(record["layer_index"], record["token"]): record for record in original["native_parts"]}
        source_records = {(record["part_index"], record["layer_index"]): record for record in original["bands"]}
        result["target_count"] = len(failed)
        result["affected_layer_count"] = len(targets)
        for index, tokens in sorted(targets.items()):
            low, high = z0 + index * height + z_gap / 2, z0 + (index + 1) * height - z_gap / 2
            z = z0 + (index + 0.5) * height
            profiles = defaultdict(list)
            original_profiles = defaultdict(list)
            source_details = []
            layer_errors = []
            for part_index, (label, mesh) in enumerate(parts):
                min_z, max_z = (float(value) for value in mesh.bounds[:, 2])
                first, last = math.floor((min_z - z0) / height), math.ceil((max_z - z0) / height)
                if not first <= index < last:
                    continue
                recipe = worker._recipe(label)
                token = recipe[index % len(recipe)]
                expected_source = source_records[(part_index, index)]
                if token != expected_source["token"]:
                    raise ValueError(f"Part {part_index}, layer {index}: recipe mismatch")
                profile = section_profile(mesh, z, config)
                source_detail = {"part_index": part_index, "token": token, **_profile_summary(profile)}
                source_mesh = None
                if not profile.ok:
                    layer_errors.append(f"Part {part_index}: source profile failed during reproduction")
                else:
                    original_profiles[token].append(profile.region)
                    if not profile.region.is_empty:
                        try:
                            source_mesh = extrude_profile(profile.region, low, high)
                        except Exception as exc:
                            layer_errors.append(f"Part {part_index}: source extrusion {type(exc).__name__}: {exc}")
                        if source_mesh is not None:
                            profiles[token].append(profile.region)
                regenerated_hash = worker._mesh_hash(source_mesh)
                source_detail.update(recorded_geometry_sha256=expected_source["geometry_sha256"],
                                     regenerated_geometry_sha256=regenerated_hash,
                                     geometry_hash_matches=regenerated_hash == expected_source["geometry_sha256"])
                if not source_detail["geometry_hash_matches"]:
                    layer_errors.append(f"Part {part_index}: source band geometry hash differs")
                source_details.append(source_detail)
            regions, partition, partition_errors = partition_materials(
                profiles, precision=config.precision, seam_tolerance=settings["seam_tolerance"])
            layer_errors.extend(partition_errors)
            layer_record = {"layer_index": index, "sample_z_mm": z, "source_profiles": source_details,
                            "partition_errors": partition_errors, "partition": partition,
                            "targets": [], "errors": layer_errors}
            result["layers"].append(layer_record)
            for token in sorted(tokens):
                target = {"layer_index": index, "token": token, "status": "failed", "errors": []}
                layer_record["targets"].append(target)
                expected_native = native_records[(index, token)]
                target["recorded_geometry_sha256"] = expected_native["geometry_sha256"]
                try:
                    if token not in regions:
                        raise ValueError("Target token absent after reproduced partition")
                    native = extrude_profile(regions[token], low, high)
                    if native is None:
                        raise ValueError("Target native extrusion returned no mesh")
                    actual_hash = worker._mesh_hash(native)
                    target.update(regenerated_geometry_sha256=actual_hash,
                                  geometry_hash_matches=actual_hash == expected_native["geometry_sha256"],
                                  extrusion=native.metadata.get("extrusion", {}))
                    if not target["geometry_hash_matches"]:
                        raise ValueError("Regenerated native mesh hash differs; supplementary geometry cannot validate the benchmark output")
                    rounded = worker._rounded(native)
                    target["indexed_is_volume"] = bool(native.is_volume)
                    target["float32"] = _float32_mesh_checks(native)
                    generic_region, generic = worker._profile_audit(rounded, z)
                    target["generic_profile_recheck"] = {**generic, "area_mm2": float(generic_region.area)}
                    target["original_generic_failure_reproduced"] = not generic["ok"]
                    # Unlike another mesh-plane reconstruction, this proves the
                    # actual constant cross-section directly from existing caps.
                    footprint = _exported_band_footprint(rounded)
                    reference = shapely.union_all(original_profiles[token], grid_size=0)
                    target["exact_prism_proof_passed"] = True
                    target["cap_footprint"] = worker._compare_profiles(reference, footprint, True, True)
                    changed = reference.symmetric_difference(footprint)
                    target["filled_area_change"] = {
                        "symmetric_difference_mm2": float(changed.area),
                        "boundary_band_mm": worker.FOOTPRINT_DISTANCE_BUDGET,
                        "area_outside_reference_boundary_band_mm2": float(changed.difference(
                            reference.boundary.buffer(worker.FOOTPRINT_DISTANCE_BUDGET)).area),
                    }
                    filled_holes = []
                    for polygon in shapely.get_parts(reference):
                        for ring in polygon.interiors:
                            hole = Polygon(ring)
                            filled_area = float(hole.intersection(footprint).area)
                            if hole.area > 0 and filled_area >= hole.area / 2:
                                filled_holes.append({"original_hole_area_mm2": float(hole.area),
                                                     "filled_area_mm2": filled_area,
                                                     "original_boundary_length_mm": float(hole.length),
                                                     "area_perimeter_width_proxy_mm": float(2 * hole.area / hole.length)})
                    target["substantially_filled_reference_holes"] = filled_holes
                    target["hole_width_note"] = "2*area/perimeter is a width proxy for long narrow slits, not a maximum-width bound"
                    target["against_partitioned_intent"] = worker._compare_profiles(regions[token], footprint, True, True)
                    checks = target["float32"]
                    valid = (not layer_errors and target["indexed_is_volume"] and checks["is_volume"]
                             and not checks["zero_area_triangles"] and not checks["duplicate_faces"]
                             and target["cap_footprint"]["within_area_budget"]
                             and target["against_partitioned_intent"]["within_area_budget"])
                    if valid:
                        target["status"] = "passed"
                    else:
                        target["errors"].append("One or more source identity, partition, volume, or footprint checks failed")
                except Exception as exc:
                    target["errors"].append(f"{type(exc).__name__}: {exc}")
            result["errors"].extend(f"Layer {index}: {error}" for error in layer_errors)
        target_results = [target for layer in result["layers"] for target in layer["targets"]]
        if not target_results:
            raise ValueError("No failed generic native profile exists to supplement")
        source_checks = result["source_parts"]
        result["summary"] = {
            "targets": len(target_results),
            "native_hashes_matching": sum(target.get("geometry_hash_matches", False) for target in target_results),
            "exact_prism_checks_passing": sum(target.get("exact_prism_proof_passed", False) for target in target_results),
            "supplementary_checks_passing": sum(target["status"] == "passed" for target in target_results),
            "generic_failures_reproduced": sum(target.get("original_generic_failure_reproduced", False) for target in target_results),
            "max_cap_symmetric_difference_mm2": max((target.get("cap_footprint", {}).get("symmetric_difference_mm2", 0.0)
                                                     for target in target_results), default=0.0),
            "max_cap_boundary_hausdorff_mm": max((target.get("cap_footprint", {}).get("boundary_hausdorff_mm") or 0.0
                                                   for target in target_results), default=0.0),
            "source_parts": len(source_checks),
            "source_indexed_nonvolume_parts": sum(not part["indexed_is_volume"] for part in source_checks),
            "source_float32_nonvolume_parts": sum(not part["float32"]["is_volume"] for part in source_checks),
            "source_float32_zero_area_triangles": sum(part["float32"]["zero_area_triangles"] for part in source_checks),
            "source_float32_duplicate_faces": sum(part["float32"]["duplicate_faces"] for part in source_checks),
        }
        if not result["errors"] and all(target["status"] == "passed" for target in target_results):
            result["status"] = "passed"
    except Exception as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        # Confirm the source result stayed intact; this audit never updates it.
        result["benchmark_report_unchanged"] = report_path.read_bytes() == original_bytes
        if not result["benchmark_report_unchanged"]:
            result["status"] = "failed"
            result["errors"].append("Original benchmark report changed during supplementary audit")
        result["records"] = [target for layer in result["layers"] for target in layer["targets"]]
        result["input_checks"] = result["source_parts"]
        result["checked_profiles"] = len(result["records"])
        result["matching_geometry_hashes"] = sum(target.get("geometry_hash_matches", False) for target in result["records"])
        result["passed"] = result["status"] == "passed"
        result["elapsed_s_not_a_benchmark"] = time.perf_counter() - started
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, default=worker._json_default, allow_nan=False) + "\n")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    result = run(args.report, args.output)
    print(json.dumps({"status": result["status"], "summary": result.get("summary"),
                      "errors": result["errors"], "output": str(args.output)}))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
