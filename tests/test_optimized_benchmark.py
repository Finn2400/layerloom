"""Exercise benchmark evidence gates with small JSON fixtures and stub workers."""
from collections import defaultdict
from copy import deepcopy
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


DRIVER = Path(__file__).resolve().parents[1] / "experiments/planar_bands/optimized_benchmark.py"
SPEC = spec_from_file_location("optimized_benchmark_driver", DRIVER)
benchmark = module_from_spec(SPEC)
sys.path.insert(0, str(DRIVER.parent))
try:
    SPEC.loader.exec_module(benchmark)
finally:
    sys.path.pop(0)


NATIVE_DEFECTS = (
    "native_indexed_nonvolume_parts", "native_float32_nonvolume_parts",
    "native_zero_area_triangles", "native_duplicate_faces", "native_footprint_area_failures",
)


def reference_result(method="contour"):
    return {
        "case": "benchy", "height_mm": 0.2, "method": method,
        "source": {"sha256": "source-a"},
        "settings": {"z0_mm": 0.0, "z_gap_mm": 0.001, "precision": 1e-5,
                     "max_gap": 0.03, "contour_tolerance": 0.001, "seam_tolerance": 0.001},
        "versions": {"python": "3.11", "numpy": "2.0", "trimesh": "4.0", "shapely": "2.1"},
        "fingerprints": {"native_parts_sha256": "native-" + method},
        "counts": {"native_parts": 100, "native_triangles": 400}, "errors": [],
        "timings": {"core_s": 2.0, "matched_part_bands_s": 1.5},
        "audit": {"enabled": True, "summary": {
            **dict.fromkeys(NATIVE_DEFECTS, 0), "native_output_profile_failures": 0,
            "native_missing_expected_midpoints": 0, "reference_profile_failures": 0,
            "passes_declared_checks": True,
        }},
    }


def new_result(reference, method="contour_optimized"):
    result = deepcopy(reference)
    result.update(method=method, audit={"enabled": False})
    if method == "contour_optimized":
        result["timings"]["matched_part_bands_s"] = None
    return result


def supplemental_proof(reference, count=2):
    reference["audit"]["summary"].update(native_output_profile_failures=count,
        native_missing_expected_midpoints=count, passes_declared_checks=False)
    return {"passed": True, "case": reference["case"], "method": reference["method"],
            "source_sha256": reference["source"]["sha256"],
            "measured_native_parts_sha256": reference["fingerprints"]["native_parts_sha256"],
            "checked_profiles": count, "matching_geometry_hashes": count}


def test_schedule_balances_four_method_positions_in_each_four_repeat_block():
    cases, heights = ["benchy", "cubes"], [0.1, 0.2]
    jobs = benchmark.schedule(cases, heights, 8, 92)
    assert len(jobs) == 2 * 2 * 8 * 4
    assert jobs == benchmark.schedule(cases, heights, 8, 92)
    assert jobs != benchmark.schedule(cases, heights, 8, 93)
    assert cases == ["benchy", "cubes"] and heights == [0.1, 0.2]
    orders = defaultdict(dict)
    for index in range(0, len(jobs), 4):
        chunk = jobs[index:index + 4]
        assert len({(j["case"], j["height_mm"], j["repeat"]) for j in chunk}) == 1
        key = chunk[0]["case"], chunk[0]["height_mm"]
        repeat = chunk[0]["repeat"]
        assert repeat not in orders[key]
        orders[key][repeat] = [j["method"] for j in chunk]
        assert set(orders[key][repeat]) == set(benchmark.METHODS)
    assert len(orders) == 4
    for repeats in orders.values():
        assert set(repeats) == set(range(1, 9))
        for start in (1, 5):
            for position in range(4):
                assert {repeats[r][position] for r in range(start, start + 4)} == set(benchmark.METHODS)


def test_exact_final_output_can_inherit_audit_without_discarded_source_prisms():
    reference = reference_result()
    result = new_result(reference)
    result["fingerprints"]["part_bands_sha256"] = None
    result["counts"]["produced_bands"] = 0
    validation = benchmark.verify(result, reference)
    assert validation["audit_inherited_by_exact_identity"] is True
    assert validation["final_geometry_checks_pass"] is True
    assert validation["supplementary_prism_proof_applies"] is False


@pytest.mark.parametrize("path,value", [
    (("source", "sha256"), "different-source"),
    (("source", "sha256"), None),
    (("settings", "precision"), 0.01),
    (("settings", "max_gap"), None),
    (("versions", "trimesh"), "different-version"),
    (("counts", "native_parts"), 99),
    (("counts", "native_triangles"), 401),
    (("counts", "native_parts"), True),
    (("fingerprints", "native_parts_sha256"), "different-native"),
    (("fingerprints", "native_parts_sha256"), None),
    (("case",), "cubes"), (("height_mm",), 0.1), (("method",), "cut"),
    (("errors",), ["partition failed"]),
])
def test_identity_requires_every_relevant_input_setting_and_output(path, value):
    reference = reference_result()
    result = new_result(reference)
    target = result if len(path) == 1 else result[path[0]]
    target[path[-1]] = value
    validation = benchmark.verify(result, reference)
    assert validation["audit_inherited_by_exact_identity"] is False
    assert validation["final_geometry_checks_pass"] is False


@pytest.mark.parametrize("field", ["versions", "counts", "errors"])
def test_missing_evidence_on_both_sides_is_not_an_identity_match(field):
    reference = reference_result()
    result = new_result(reference)
    reference.pop(field)
    result.pop(field)
    assert benchmark.verify(result, reference)["audit_inherited_by_exact_identity"] is False


@pytest.mark.parametrize("audit", [{}, {"enabled": False, "summary": {"passes_declared_checks": True}},
                                   {"enabled": True, "summary": {}}])
def test_reference_audit_must_actually_exist(audit):
    reference = reference_result()
    result = new_result(reference)
    reference["audit"] = audit
    assert benchmark.verify(result, reference)["audit_inherited_by_exact_identity"] is False


@pytest.mark.parametrize("defect", NATIVE_DEFECTS)
def test_original_native_defects_remain_failures_despite_a_valid_supplement(defect):
    reference = reference_result()
    proof = supplemental_proof(reference)
    reference["audit"]["summary"][defect] = 3
    validation = benchmark.verify(new_result(reference), reference, proof)
    assert validation["audit_inherited_by_exact_identity"] is True
    assert validation["supplementary_prism_proof_applies"] is True
    assert validation["inherited_native_defects"][defect] == 3
    assert validation["final_geometry_checks_pass"] is False


def test_supplement_preserves_original_failures_while_proving_the_same_native_output():
    reference = reference_result()
    proof = supplemental_proof(reference)
    result = new_result(reference)
    assert benchmark.verify(result, reference)["final_geometry_checks_pass"] is False
    validation = benchmark.verify(result, reference, proof)
    assert validation["final_geometry_checks_pass"] is True
    assert validation["inherited_generic_profile_failures"] == 2
    assert validation["inherited_common_audit_summary"]["passes_declared_checks"] is False


@pytest.mark.parametrize("key,value", [
    ("passed", False), ("case", "cubes"), ("method", "repair_cut"),
    ("source_sha256", "different-source"), ("measured_native_parts_sha256", "different-output"),
    ("checked_profiles", 1), ("matching_geometry_hashes", 3),
])
def test_supplement_must_match_case_method_source_native_geometry_and_coverage(key, value):
    reference = reference_result()
    proof = supplemental_proof(reference)
    proof[key] = value
    validation = benchmark.verify(new_result(reference), reference, proof)
    assert validation["audit_inherited_by_exact_identity"] is True
    assert validation["supplementary_prism_proof_applies"] is False
    assert validation["final_geometry_checks_pass"] is False


def test_supplement_cannot_waive_additional_missing_outputs_or_missing_references():
    reference = reference_result()
    proof = supplemental_proof(reference)
    reference["audit"]["summary"]["native_missing_expected_midpoints"] = 3
    assert benchmark.verify(new_result(reference), reference, proof)["final_geometry_checks_pass"] is False
    reference["audit"]["summary"].update(native_missing_expected_midpoints=2, reference_profile_failures=1)
    assert benchmark.verify(new_result(reference), reference, proof)["final_geometry_checks_pass"] is False


def test_original_algorithm_errors_are_not_erased_by_output_identity():
    reference = reference_result()
    result = new_result(reference)
    reference["errors"] = ["original partition failure"]
    assert benchmark.verify(result, reference)["audit_inherited_by_exact_identity"] is False


def test_summary_keeps_failed_processes_out_of_timings_without_hiding_bad_validation():
    reference = reference_result()
    runs = []
    for seconds in (1, 5, 3):
        result = new_result(reference)
        result["timings"]["core_s"] = seconds
        runs.append({"case": "benchy", "height_mm": 0.2, "method": "contour_optimized",
                     "status": "completed", "result": result,
                     "validation": benchmark.verify(result, reference), "frozen_control_verified": True})
    runs[1]["validation"]["audit_inherited_by_exact_identity"] = False
    runs[2]["frozen_control_verified"] = False
    runs.append({"case": "benchy", "height_mm": 0.2, "method": "contour_optimized", "status": "timeout"})
    summary, = benchmark.summarize(runs)
    assert summary["completed_repeats"] == 3 and summary["requested_repeats"] == 4
    assert summary["timings"]["core_s"] == {"median": 3, "min": 1, "max": 5, "values": [1, 5, 3]}
    assert "matched_part_bands_s" not in summary["timings"]
    assert summary["all_outputs_equal_original"] is False
    assert summary["all_frozen_controls_verified"] is False
    assert summary["all_final_geometry_checks_pass"] is False
    empty, = benchmark.summarize([runs[-1]])
    assert empty["timings"] == {} and empty["completed_repeats"] == 0
    assert empty["all_outputs_equal_original"] is False


def configure_driver(monkeypatch, tmp_path, *extra):
    frozen = b"frozen planar implementation\n"
    original = tmp_path / "original.json"
    supplement = tmp_path / "prism.json"
    output = tmp_path / "optimized.json"
    rows = {method: reference_result(method) for method in ("cut", "repair_cut", "contour")}
    report = {"metadata": {"source_sha256": {
        "src/layerloom/planar_bands.py": hashlib.sha256(frozen).hexdigest()}},
        "protocol": {"normalization": "original shared grid"},
        "summary": [{"case": "benchy", "height_mm": 0.2, "method": method,
                     "audit_applies_to_all_completed_repeats": True} for method in rows],
        "runs": [{"case": "benchy", "height_mm": 0.2, "method": method,
                  "status": "completed", "result": row} for method, row in rows.items()]}
    original.write_text(json.dumps(report))
    supplement.write_text(json.dumps({"passed": False}))
    monkeypatch.setattr(benchmark.base, "metadata", lambda: {"source_sha256": {}})
    monkeypatch.setattr(benchmark.os, "getloadavg", lambda: (0.1, 0.2, 0.3))
    monkeypatch.setattr(benchmark.subprocess, "check_output", lambda *a, **k: frozen)
    monkeypatch.setattr(benchmark.sys, "argv", [str(DRIVER), "--cases", "benchy", "--reference", str(original),
        "--prism-audit", str(supplement), "--output", str(output), "--repetitions", "4", *extra])
    return original, supplement, output, rows, frozen


def install_stub_workers(monkeypatch, output, rows, frozen, tamper=None):
    observed = []

    def worker(command, **kwargs):
        checkpoint = json.loads(output.read_text())
        assert len(checkpoint["runs"]) == len(observed)
        assert command[:2] == [sys.executable, str(DRIVER.parent / "controlled_worker.py")]
        arguments = dict(zip(command[2::2], command[3::2]))
        assert arguments["--audit"] == "none"
        assert kwargs["cwd"] == DRIVER.parents[2]
        assert all(kwargs["env"][key] == "1" for key in benchmark.base.THREAD_ENV)
        assert kwargs["env"]["PYTHONHASHSEED"] == "0"
        assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
        assert kwargs["env"]["PYTHONPATH"] == str(DRIVER.parents[2] / "src")
        baseline = Path(arguments["--baseline-planar"])
        assert baseline.read_bytes() == frozen
        method = arguments["--method"]
        reference_method = "contour" if method == "contour_optimized" else method
        result = new_result(rows[reference_method], method)
        result["baseline_planar"] = {"path": str(baseline), "sha256": hashlib.sha256(frozen).hexdigest(),
            "loaded": method == "contour", "used_for_timed_geometry": method == "contour"}
        if tamper:
            tamper(result)
        result["timings"]["core_s"] = float(len(observed) + 1)
        Path(arguments["--output"]).write_text(json.dumps(result))
        observed.append(method)
        return SimpleNamespace(returncode=0, stdout="stub worker completed\n")

    monkeypatch.setattr(benchmark.subprocess, "run", worker)
    return observed


def test_driver_preserves_archived_reports_runs_serially_and_checks_frozen_provenance(monkeypatch, tmp_path):
    original, supplement, output, rows, frozen = configure_driver(monkeypatch, tmp_path)
    original_bytes, supplement_bytes = original.read_bytes(), supplement.read_bytes()
    observed = install_stub_workers(monkeypatch, output, rows, frozen)
    assert benchmark.main() == 0
    report = json.loads(output.read_text())
    expected = benchmark.schedule(["benchy"], [0.2], 4, 20261022)
    assert observed == [job["method"] for job in expected]
    assert len(report["runs"]) == 16 and len(report["summary"]) == 4
    assert all(row["completed_repeats"] == 4 for row in report["summary"])
    assert all(row["all_outputs_equal_original"] for row in report["summary"])
    assert all(row["all_frozen_controls_verified"] for row in report["summary"])
    assert all(row["all_final_geometry_checks_pass"] for row in report["summary"])
    optimized = next(row for row in report["summary"] if row["method"] == "contour_optimized")
    assert "matched_part_bands_s" not in optimized["timings"]
    assert original.read_bytes() == original_bytes
    assert supplement.read_bytes() == supplement_bytes


@pytest.mark.parametrize("field,value", [("path", "wrong/path"), ("sha256", "wrong-sha"),
                                         ("loaded", None), ("used_for_timed_geometry", None)])
def test_driver_rejects_missing_or_wrong_frozen_worker_provenance(monkeypatch, tmp_path, field, value):
    _, _, output, rows, frozen = configure_driver(monkeypatch, tmp_path, "--repetitions", "1")
    install_stub_workers(monkeypatch, output, rows, frozen,
                         tamper=lambda result: result["baseline_planar"].update({field: value}))
    assert benchmark.main() == 1
    report = json.loads(output.read_text())
    assert all(not row["all_frozen_controls_verified"] for row in report["summary"])


def test_frozen_module_must_match_the_original_measured_implementation_before_workers_start(monkeypatch, tmp_path):
    original, _, output, rows, frozen = configure_driver(monkeypatch, tmp_path)
    observed = install_stub_workers(monkeypatch, output, rows, frozen)
    monkeypatch.setattr(benchmark.subprocess, "check_output", lambda *a, **k: b"wrong implementation")
    saved = original.read_bytes()
    with pytest.raises(ValueError, match="Frozen control source"):
        benchmark.main()
    assert observed == [] and not output.exists()
    assert original.read_bytes() == saved


@pytest.mark.parametrize("target", ["reference", "supplement", "symlink", "same_stem"])
def test_output_cannot_overwrite_archived_evidence_or_its_detail_directory(monkeypatch, tmp_path, target):
    original, supplement, _, _, _ = configure_driver(monkeypatch, tmp_path)
    saved = {path: path.read_bytes() for path in (original, supplement)}
    if target == "symlink":
        output = tmp_path / "alias.json"
        output.symlink_to(original)
    elif target == "same_stem":
        output = original.with_suffix(".txt")
    else:
        output = original if target == "reference" else supplement
    monkeypatch.setattr(benchmark.sys, "argv", [*benchmark.sys.argv, "--output", str(output)])
    monkeypatch.setattr(benchmark.subprocess, "run", lambda *a, **k: pytest.fail("No worker may start"))
    with pytest.raises(SystemExit) as exc:
        benchmark.main()
    assert exc.value.code == 2
    assert all(path.read_bytes() == data for path, data in saved.items())


def test_references_reject_unstable_archived_audit_evidence():
    report = {"summary": [{"audit_applies_to_all_completed_repeats": False}], "runs": []}
    with pytest.raises(ValueError, match="stable audited"):
        benchmark.references(report)
