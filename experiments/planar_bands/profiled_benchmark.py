"""Balanced comparison against the previous optimized 2D implementation.

Reuses previous external audits only after exact input/settings/final-geometry
identity. New output quality is never inferred from a timing or silhouette.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import controlled_benchmark as base
from optimized_benchmark import digest, schedule, references, verify, summarize

HERE, ROOT = base.HERE, base.ROOT
METHODS = ("cut", "repair_cut", "contour", "contour_optimized")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", nargs="+", choices=base.CASES, default=list(base.CASES))
    parser.add_argument("--heights", nargs="+", type=base.positive_finite_float, default=[0.2])
    parser.add_argument("--repetitions", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20261022)
    parser.add_argument("--timeout", type=base.positive_finite_float, default=1200)
    parser.add_argument("--baseline-revision", default="514b61a6")
    parser.add_argument("--reference", type=Path, default=HERE / "results/controlled_benchmark.json")
    parser.add_argument("--prism-audit", type=Path, default=HERE / "results/controlled_prism_audit.json")
    parser.add_argument("--output", type=Path, default=HERE / "results/profiled_benchmark.json")
    parser.add_argument("--previous", type=Path, default=HERE / "results/optimized_benchmark.json")
    args = parser.parse_args()
    if args.repetitions < 1 or any(h <= 0 for h in args.heights):
        parser.error("Positive repetitions and layer heights required")
    output = args.output.resolve()
    evidence = {args.reference.resolve(), args.prism_audit.resolve(), args.previous.resolve()}
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
    previous = json.loads(args.previous.read_text(encoding="utf-8"))
    frozen_path = run_dir / "previous_kernel"
    frozen_path.mkdir(exist_ok=True)
    frozen_hashes = {}
    for name in ("planar_bands", "planar_materials", "contour_repair"):
        relative = f"src/layerloom/{name}.py"
        frozen = subprocess.check_output(["git", "show", f"{args.baseline_revision}:{relative}"], cwd=ROOT)
        frozen_hashes[name + ".py"] = hashlib.sha256(frozen).hexdigest()
        if frozen_hashes[name + ".py"] != previous["metadata"]["source_sha256"][relative]:
            raise ValueError(f"Frozen {name} differs from the previous measured implementation")
        (frozen_path / (name + ".py")).write_bytes(frozen)
    current_hashes = {name: digest(ROOT / "src/layerloom" / name) for name in frozen_hashes}
    report = {"schema_version": 1, "metadata": base.metadata(), "protocol": {
        "cases": args.cases, "heights_mm": args.heights, "repetitions": args.repetitions,
        "methods": METHODS, "seed": args.seed,
        "method_labels": {"contour": "Previous optimized 2D (514b61a6)", "contour_optimized": "Profile-guided 2D", "cut": "3D cuts", "repair_cut": "Mesh repair + 3D cuts"},
        "plot_title": "Profile-guided 2D repair: faster processing, identical geometry",
        "plot_labels": {"cut": "3D cuts", "repair_cut": "Mesh repair + cuts", "contour": "Previous 2D", "contour_optimized": "Faster 2D"},
        "previous": {"path": str(args.previous.resolve()), "sha256": digest(args.previous)},
        "execution": "Serial fresh workers; randomized case order and cyclic method order; every method occupies every position once per four repeats.",
        "timed": "Repair, source bands or profile-only extraction, and native color assembly with intrinsic checks. core_s is the directly comparable complete geometry processing time.",
        "excluded": "Imports, input loading, common warmup, fingerprints, external audits, export, slicer work and printing.",
        "optimized_first_stage": "Both 2D arms extract profiles only; no discarded source prisms. matched_part_bands_s is intentionally null for both.",
        "audit_policy": "Reuse archived final-output audits only on exact original input, numeric settings, library versions, output counts and native geometry/name/color/layer hashes; preserve generic audit failures and supplementary cap proof.",
        "normalization": reference["protocol"]["normalization"],
        "reference": {"path": str(args.reference.resolve()), "sha256": digest(args.reference)},
        "supplement": {"path": str(args.prism_audit.resolve()), "sha256": digest(args.prism_audit)},
        "frozen_control": {"revision": args.baseline_revision, "path": str(frozen_path), "source_sha256": frozen_hashes},
        "limitations": "Local desktop; no CPU pinning or isolation from background applications. Environment thread limits do not cover every possible native runtime.",
    }, "runs": []}
    for path in (Path(__file__), HERE / "profiled_worker.py", ROOT / "src/layerloom/contour_repair.py"):
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
        actual_method = "contour_optimized" if job["method"] == "contour" else job["method"]
        command = [sys.executable, str(HERE / "profiled_worker.py"), "--case", job["case"],
                   "--height", str(job["height_mm"]), "--method", actual_method, "--output", str(path)]
        if job["method"] == "contour":
            command += ["--frozen-directory", str(frozen_path)]
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
                provenance = result.get("implementation", {})
                uses_control = job["method"] == "contour"
                expected_directory = frozen_path if uses_control else ROOT / "src/layerloom"
                bindings = {name: str(expected_directory / filename) for name, filename in (
                    ("section_profile", "planar_bands.py"), ("reconstruct_layer_profiles", "planar_bands.py"),
                    ("extrude_profile", "planar_bands.py"), ("partition_materials", "planar_materials.py"),
                    ("_float32_mesh_checks", "contour_repair.py"))}
                run["frozen_control_verified"] = (
                    provenance.get("frozen") is uses_control
                    and provenance.get("bindings") == bindings
                    and provenance.get("source_sha256") == (frozen_hashes if uses_control else current_hashes))
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
