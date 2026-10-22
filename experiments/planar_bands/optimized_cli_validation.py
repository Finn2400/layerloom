"""Untimed production CLI regression against the previously repaired Benchy.

Run from the repository with PYTHONPATH=src. The historical output/report and
controlled benchmark are read-only controls; fresh output/report paths are
required. This is separate from the controlled repair/layer timing experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from layerloom.contour_repair import _read_native_bands, reconstruct_file


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULTS = HERE / "results"
PARAMETERS = {
    "layer_height_mm": 0.2, "max_gap_mm": 0.03, "precision_mm": 1e-5,
    "z_gap_mm": 0.001, "contour_tolerance_mm": 0.001,
    "seam_tolerance_mm": 0.001, "fallback_recipe": "cmy",
}


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def mesh_hash(mesh):
    digest = hashlib.sha256()
    for array, dtype in ((mesh.vertices, "<f8"), (mesh.faces, "<i8")):
        data = np.asarray(array, dtype=dtype, order="C")
        digest.update(np.asarray(data.shape, dtype="<i8").tobytes())
        digest.update(data.tobytes())
    return digest.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(args):
    source, baseline, baseline_report, controlled, output, report_path, summary = (
        getattr(args, key).resolve() for key in
        ("source", "baseline", "baseline_report", "controlled", "output", "report", "summary"))
    require(len({source, baseline, baseline_report, controlled, output, report_path, summary}) == 7,
            "Input, control, and output paths must be distinct")
    require(not output.exists() and not report_path.exists() and not summary.exists(),
            "Use fresh output, report, and summary paths")
    old_report = json.loads(baseline_report.read_text())
    controlled_data = json.loads(controlled.read_text())
    controls = {str(path): file_hash(path) for path in (baseline, baseline_report, controlled)}
    source_hash = file_hash(source)
    control_source_hashes = sorted({run["result"]["source"]["sha256"]
                                   for run in controlled_data["runs"]
                                   if run["case"] == "benchy" and run.get("status") == "completed"})
    require(control_source_hashes == [source_hash], "Benchy input differs from controlled benchmark")
    require(old_report["status"] == "ok" and not old_report["errors"], "Baseline report failed")
    require(all(old_report.get(k) == v for k, v in PARAMETERS.items()), "Baseline parameters differ")
    require(old_report["output_sha256"] == controls[str(baseline)], "Baseline 3MF checksum differs")
    historical_input = Path(old_report["input"])
    historical_input_hash = file_hash(historical_input) if historical_input.exists() else None
    require(historical_input_hash in (None, source_hash), "Baseline report input path now has different bytes")

    print("Historical output and source provenance verified; running production reconstruction.", flush=True)
    current = reconstruct_file(source, output, layer_height=PARAMETERS["layer_height_mm"],
                               report_path=report_path)
    require(current["status"] == "ok" and not current["errors"],
            f"Production CLI failed: {current['errors']}")
    require(all(current.get(k) == v for k, v in PARAMETERS.items()), "Current parameters differ")
    require(current["output_sha256"] == file_hash(output), "Current output checksum differs")
    print("Production checks passed; comparing native band arrays and diagnostic metadata.", flush=True)
    old_bands, new_bands = _read_native_bands(baseline), _read_native_bands(output)
    require(len(old_bands) == len(new_bands) == old_report["exported_bands"] == 349,
            "Native band count differs")
    bands = []
    for (old_name, old_mesh), (name, mesh) in zip(old_bands, new_bands):
        require(name == old_name, "Native volume names or order differ")
        require(np.array_equal(old_mesh.vertices, mesh.vertices), f"Vertices differ: {name}")
        require(np.array_equal(old_mesh.faces, mesh.faces), f"Faces differ: {name}")
        old_hash, new_hash = mesh_hash(old_mesh), mesh_hash(mesh)
        require(old_hash == new_hash, f"Mesh bytes differ: {name}")
        bands.append({"name": name, "vertices": len(mesh.vertices), "triangles": len(mesh.faces),
                      "geometry_sha256": new_hash, "matches_control": True})
    # Paths, container hash, and the newly documented pipeline are the only
    # permitted report differences. This includes all contour, hole, seam,
    # final extrusion and exported-footprint metadata, not just mesh counts.
    excluded = {"input", "output", "output_sha256", "geometry_pipeline"}
    old_metadata = {k: v for k, v in old_report.items() if k not in excluded}
    new_metadata = {k: v for k, v in current.items() if k not in excluded}
    require(old_metadata == new_metadata, "Production diagnostic metadata differs")
    require(all(file_hash(Path(path)) == digest for path, digest in controls.items()),
            "A historical control file changed")
    require(file_hash(source) == source_hash, "Source changed during validation")
    materials = [material for layer in current["layers"] for material in layer["materials"]]
    normalizations = [material["extrusion"] for material in materials]
    holes_before = sum(item["quantization_holes_before"] for item in normalizations)
    holes_after = sum(item["quantization_holes_after"] for item in normalizations)
    geometry_hash = json_hash([[item["name"], item["geometry_sha256"]] for item in bands])
    result = {
        "schema_version": 1, "status": "passed", "timing": "Untimed geometry validation; excluded from benchmark.",
        "parameters": PARAMETERS,
        "source": {"path": str(source), "sha256": source_hash,
                   "matches_all_prior_controlled_benchy_runs": True,
                   "prior_report_input_path": str(historical_input),
                   "prior_report_input_path_current_sha256": historical_input_hash,
                   "prior_report_recorded_source_checksum": None},
        "control": {"output": str(baseline), "output_sha256": controls[str(baseline)],
                    "matches_prior_report_output_checksum": True,
                    "report": str(baseline_report), "report_sha256": controls[str(baseline_report)],
                    "controlled_benchmark": str(controlled), "controlled_benchmark_sha256": controls[str(controlled)],
                    "whole_geometry_sha256": geometry_hash},
        "output": {"path": str(output), "sha256": file_hash(output),
                   "report": str(report_path), "report_sha256": file_hash(report_path),
                   "whole_geometry_sha256": geometry_hash},
        "counts": {"native_bands": len(bands), "layers": len(current["layers"]),
                   "source_parts": len(current["parts"]),
                   "vertices": sum(item["vertices"] for item in bands),
                   "triangles": sum(item["triangles"] for item in bands),
                   "warnings": len(current["warnings"]),
                   "quantization_holes_before": holes_before,
                   "quantization_holes_after": holes_after,
                   "holes_removed_by_existing_float32_normalization": holes_before - holes_after,
                   "residual_material_overlap_records": len(current["roundtrip_residual_material_overlaps"])},
        "checks": {"native_names_and_order_identical": True,
                   "all_native_vertices_and_faces_exactly_equal": True,
                   "all_metadata_except_paths_container_hash_and_pipeline_identical": True,
                   "source_contour_diagnostics_identical": current["parts"] == old_report["parts"],
                   "partition_final_extrusion_and_tiny_hole_metadata_identical": current["layers"] == old_report["layers"],
                   "seam_metadata_identical": current["roundtrip_residual_material_overlaps"] == old_report["roundtrip_residual_material_overlaps"],
                   "final_intrinsic_and_float32_mesh_checks_passed": True,
                   "export_reimport_and_native_range_checks_passed": True,
                   "equal_top_bottom_cap_unions_and_vertical_sides_passed": True,
                   "per_band_and_complete_layer_footprint_checks_passed": True,
                   "exported_material_seam_budget_checks_passed": True,
                   "source_and_controls_unchanged": True},
        "roundtrip": {key: value for key, value in current.items()
                      if key.startswith("roundtrip_") and key != "roundtrip_residual_material_overlaps"},
        "metadata_sha256": json_hash(new_metadata),
        "seam_metadata_sha256": json_hash(current["roundtrip_residual_material_overlaps"]),
        "bands": bands,
    }
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"status": result["status"], "counts": result["counts"],
                      "whole_geometry_sha256": geometry_hash, "summary": str(summary)}, indent=2), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "examples/tutorial_cmy_benchy_cutup.3mf")
    parser.add_argument("--baseline", type=Path, default=RESULTS / "benchy_repaired.3mf")
    parser.add_argument("--baseline-report", type=Path, default=RESULTS / "benchy_repaired.report.json")
    parser.add_argument("--controlled", type=Path, default=RESULTS / "controlled_benchmark.json")
    parser.add_argument("--output", type=Path, default=RESULTS / "benchy_optimized.3mf")
    parser.add_argument("--report", type=Path, default=RESULTS / "benchy_optimized.report.json")
    parser.add_argument("--summary", type=Path, default=RESULTS / "optimization_validation.json")
    validate(parser.parse_args())


if __name__ == "__main__":
    main()
