"""Balanced serial comparison with a frozen pre-optimization 2D control.

Reuses previous external audits only after exact input/settings/final-geometry
identity. New output quality is never inferred from a timing or silhouette.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

import controlled_benchmark as base

HERE, ROOT = base.HERE, base.ROOT
METHODS = ("cut", "repair_cut", "contour", "contour_optimized")
SETTINGS = ("z0_mm", "z_gap_mm", "precision", "max_gap", "contour_tolerance", "seam_tolerance")
NATIVE_DEFECTS = (
    "native_indexed_nonvolume_parts", "native_float32_nonvolume_parts",
    "native_zero_area_triangles", "native_duplicate_faces", "native_footprint_area_failures",
)
AUDIT_COUNTS = (*NATIVE_DEFECTS, "native_output_profile_failures",
                "native_missing_expected_midpoints", "reference_profile_failures")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def schedule(cases, heights, repetitions, seed):
    return base.schedule(cases, heights, repetitions, seed, methods=METHODS)


def references(report):
    result = {}
    for row in report["summary"]:
        if not row["audit_applies_to_all_completed_repeats"]:
            raise ValueError("Original measurements do not have stable audited input/output hashes")
    for run in report["runs"]:
        row = run.get("result", {})
        if run["status"] == "completed" and row.get("audit", {}).get("enabled"):
            result[(run["case"], run["height_mm"], run["method"])] = row
    return result


def verify(result, reference, supplement=None):
    """Conservative inheritance: final coordinates, faces and color labels match."""
    old_summary = reference.get("audit", {}).get("summary", {})
    checks = {
        "source_sha256_matches": bool(result.get("source", {}).get("sha256"))
        and result["source"]["sha256"] == reference.get("source", {}).get("sha256"),
        "case_and_height_match": result.get("case") == reference.get("case")
        and result.get("height_mm") == reference.get("height_mm"),
        "method_matches_reference": result.get("method") == reference.get("method")
        or (result.get("method") == "contour_optimized" and reference.get("method") == "contour"),
        "reference_audit_present": reference.get("audit", {}).get("enabled") is True
        and bool(reference.get("audit", {}).get("summary")),
        "reference_audit_counts_complete": all(type(old_summary.get(key)) is int
                                                and old_summary[key] >= 0 for key in AUDIT_COUNTS),
        "settings_match": all(type(result.get("settings", {}).get(key)) in (float, int)
                              and math.isfinite(result["settings"][key])
                              and result["settings"][key] == reference.get("settings", {}).get(key)
                              for key in SETTINGS),
        "library_versions_match": bool(result.get("versions")) and result.get("versions") == reference.get("versions"),
        "native_geometry_sha256_matches": bool(result.get("fingerprints", {}).get("native_parts_sha256"))
        and result["fingerprints"]["native_parts_sha256"] == reference.get("fingerprints", {}).get("native_parts_sha256"),
        "native_counts_match": all(type(result.get("counts", {}).get(key)) is int
                                   and result["counts"][key] >= 0
                                   and result["counts"][key] == reference.get("counts", {}).get(key)
                                   for key in ("native_parts", "native_triangles")),
        "no_algorithm_errors": result.get("errors") == [] and reference.get("errors") == [],
    }
    inherited = all(checks.values())
    native_defects = {key: old_summary.get(key) for key in NATIVE_DEFECTS}
    profile_failures = old_summary.get("native_output_profile_failures")
    cap_proof = bool(inherited and profile_failures > 0 and supplement and supplement.get("passed") is True
                     and supplement.get("case") == result.get("case")
                     and supplement.get("method") == reference.get("method")
                     and supplement.get("source_sha256") == result["source"]["sha256"]
                     and supplement.get("measured_native_parts_sha256") == result["fingerprints"]["native_parts_sha256"]
                     and all(type(supplement.get(key)) is int and supplement[key] == profile_failures
                             for key in ("checked_profiles", "matching_geometry_hashes")))
    quality = (inherited and not any(native_defects.values())
               and old_summary.get("reference_profile_failures", 0) == 0
               and (profile_failures == 0 or cap_proof)
               and (old_summary.get("native_missing_expected_midpoints", 0) == 0
                    or (cap_proof and old_summary["native_missing_expected_midpoints"] <= profile_failures)))
    return {**checks, "audit_inherited_by_exact_identity": inherited,
            "reference_method": reference["method"],
            "reference_native_geometry_sha256": reference["fingerprints"]["native_parts_sha256"],
            "final_geometry_checks_pass": quality,
            "supplementary_prism_proof_applies": cap_proof,
            "inherited_native_defects": native_defects,
            "inherited_generic_profile_failures": profile_failures,
            "inherited_common_audit_summary": old_summary,
            "scope": "Original final-output audits inherited by exact arrays/name/layer/token identity; no new common audit executed; deferred source prisms are not outputs."}


def summarize(runs):
    groups = {}
    for run in runs:
        groups.setdefault((run["case"], run["height_mm"], run["method"]), []).append(run)
    rows = []
    for (case, height, method), group in groups.items():
        completed = [r for r in group if r["status"] == "completed"]
        names = sorted({key for r in completed for key, value in r["result"]["timings"].items()
                        if isinstance(value, (float, int))})
        timings = {}
        for key in names:
            values = [r["result"]["timings"][key] for r in completed
                      if isinstance(r["result"]["timings"].get(key), (float, int))]
            timings[key] = {"median": statistics.median(values), "min": min(values),
                            "max": max(values), "values": values}
        matched = bool(completed) and all(r["validation"]["audit_inherited_by_exact_identity"] for r in completed)
        rows.append({"case": case, "height_mm": height, "method": method,
                     "completed_repeats": len(completed), "requested_repeats": len(group),
                     "all_outputs_equal_original": matched,
                     "all_frozen_controls_verified": bool(completed) and all(r.get("frozen_control_verified") for r in completed),
                     "all_final_geometry_checks_pass": matched and all(r["validation"]["final_geometry_checks_pass"] for r in completed),
                     "timings": timings, "counts": completed[0]["result"]["counts"] if completed else None,
                     "validation": completed[0]["validation"] if completed else None})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", nargs="+", choices=base.CASES, default=list(base.CASES))
    parser.add_argument("--heights", nargs="+", type=base.positive_finite_float, default=[0.2])
    parser.add_argument("--repetitions", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20261022)
    parser.add_argument("--timeout", type=base.positive_finite_float, default=1200)
    parser.add_argument("--baseline-revision", default="a27dc7de")
    parser.add_argument("--reference", type=Path, default=HERE / "results/controlled_benchmark.json")
    parser.add_argument("--prism-audit", type=Path, default=HERE / "results/controlled_prism_audit.json")
    parser.add_argument("--output", type=Path, default=HERE / "results/optimized_benchmark.json")
    args = parser.parse_args()
    if args.repetitions < 1 or any(h <= 0 for h in args.heights):
        parser.error("Positive repetitions and layer heights required")
    output = args.output.resolve()
    evidence = {args.reference.resolve(), args.prism_audit.resolve()}
    run_dir = (output.parent / (output.stem + "_runs")).resolve()
    protected_run_dirs = {(path.parent / (path.stem + "_runs")).resolve() for path in evidence}
    if output in evidence or run_dir in protected_run_dirs:
        parser.error("Output must not overwrite previous evidence")
    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    supplement = json.loads(args.prism_audit.read_text(encoding="utf-8"))
    ref_rows = references(reference)
    jobs = schedule(args.cases, args.heights, args.repetitions, args.seed)
    for job in jobs:
        ref_method = "contour" if job["method"] == "contour_optimized" else job["method"]
        if (job["case"], job["height_mm"], ref_method) not in ref_rows:
            parser.error("Requested case/height has no archived audited reference")
    run_dir.mkdir(parents=True, exist_ok=True)
    frozen = subprocess.check_output(["git", "show", f"{args.baseline_revision}:src/layerloom/planar_bands.py"], cwd=ROOT)
    frozen_hash = hashlib.sha256(frozen).hexdigest()
    expected = reference["metadata"]["source_sha256"]["src/layerloom/planar_bands.py"]
    if frozen_hash != expected:
        raise ValueError("Frozen control source does not match the archived measurement's implementation")
    frozen_path = run_dir / "baseline_planar_bands.py"
    frozen_path.write_bytes(frozen)
    report = {"schema_version": 1, "metadata": base.metadata(), "protocol": {
        "cases": args.cases, "heights_mm": args.heights, "repetitions": args.repetitions,
        "methods": METHODS, "seed": args.seed,
        "execution": "Serial fresh workers; randomized case order and cyclic method order; every method occupies every position once per four repeats.",
        "timed": "Repair, source bands or profile-only extraction, and native color assembly with intrinsic checks. core_s is the directly comparable complete geometry processing time.",
        "excluded": "Imports, input loading, common warmup, fingerprints, external audits, export, slicer work and printing.",
        "optimized_first_stage": "Profiles only: no discarded source prisms. matched_part_bands_s is intentionally null.",
        "audit_policy": "Reuse archived final-output audits only on exact original input, numeric settings, library versions, output counts and native geometry/name/color/layer hashes; preserve generic audit failures and supplementary cap proof.",
        "normalization": reference["protocol"]["normalization"],
        "reference": {"path": str(args.reference.resolve()), "sha256": digest(args.reference)},
        "supplement": {"path": str(args.prism_audit.resolve()), "sha256": digest(args.prism_audit)},
        "frozen_control": {"revision": args.baseline_revision, "path": str(frozen_path), "sha256": frozen_hash},
        "limitations": "Local desktop; no CPU pinning or isolation from background applications. Environment thread limits do not cover every possible native runtime.",
    }, "runs": []}
    for path in (Path(__file__), ROOT / "src/layerloom/contour_repair.py"):
        report["metadata"]["source_sha256"][path.relative_to(ROOT).as_posix()] = digest(path)
    env = {**os.environ, **base.THREAD_ENV, "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(ROOT / "src")}

    def save():
        report["summary"] = summarize(report["runs"])
        output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    save()
    for index, job in enumerate(jobs, 1):
        stem = f"{index:02d}_{job['case']}_{job['height_mm']:g}_{job['method']}_r{job['repeat']}"
        path = run_dir / f"{stem}.json"
        path.unlink(missing_ok=True)
        command = [sys.executable, str(HERE / "controlled_worker.py"), "--case", job["case"],
                   "--height", str(job["height_mm"]), "--method", job["method"], "--audit", "none",
                   "--baseline-planar", str(frozen_path), "--output", str(path)]
        run = {**job, "order": index, "load_average_before": base.load_average()}
        print(f"[{index}/{len(jobs)}] {stem}", flush=True)
        started = time.perf_counter()
        try:
            proc = subprocess.run(command, cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE,
                                  encoding="utf-8", errors="replace",
                                  stderr=subprocess.STDOUT, timeout=args.timeout)
            (run_dir / f"{stem}.log").write_text(proc.stdout, encoding="utf-8")
            run["process_returncode"] = proc.returncode
            if proc.returncode == 0 and path.is_file():
                result = base.read_worker_result(path)
                ref_method = "contour" if job["method"] == "contour_optimized" else job["method"]
                run["result"] = base.compact_result(result)
                run["validation"] = verify(result, ref_rows[(job["case"], job["height_mm"], ref_method)], supplement)
                provenance = result.get("baseline_planar", {})
                uses_control = job["method"] == "contour"
                run["frozen_control_verified"] = (
                    provenance.get("path") == str(frozen_path)
                    and provenance.get("sha256") == frozen_hash
                    and provenance.get("loaded") is uses_control
                    and provenance.get("used_for_timed_geometry") is uses_control)
                run["status"] = "completed"
                run["detail_file"] = str(path.relative_to(output.parent))
            else:
                run.update(status="failed", diagnostic_tail=proc.stdout[-4000:])
        except subprocess.TimeoutExpired as exc:
            diagnostic = exc.stdout or b""
            if isinstance(diagnostic, bytes):
                diagnostic = diagnostic.decode(errors="replace")
            (run_dir / f"{stem}.log").write_text(diagnostic, encoding="utf-8")
            run.update(status="timeout", diagnostic_tail=diagnostic[-4000:])
        except (OSError, ValueError, TypeError, KeyError) as exc:
            run.pop("result", None)
            run.update(status="failed", diagnostic_tail=f"{type(exc).__name__}: {exc}")
        run["process_elapsed_s_including_excluded_work"] = time.perf_counter() - started
        report["runs"].append(run)
        save()
        seconds = run.get("result", {}).get("timings", {}).get("core_s")
        equal = run.get("validation", {}).get("audit_inherited_by_exact_identity")
        print(f"  {run['status']}: core={seconds}s final geometry identical={equal}", flush=True)
    report["metadata"]["utc_finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report["metadata"]["load_average_at_finish"] = base.load_average()
    save()
    return 0 if all(r["status"] == "completed" and r["validation"]["audit_inherited_by_exact_identity"]
                   and r["frozen_control_verified"] for r in report["runs"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
