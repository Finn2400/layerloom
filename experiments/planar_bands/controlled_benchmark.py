"""Serial, fresh-process benchmark of repair and layer generation only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys
import time


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
METHODS = ("cut", "repair_cut", "contour")
CASES = ("cubes", "benchy", "hollow_tube", "hollow_tube_gap020")
THREAD_ENV = {name: "1" for name in (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS",
)}


def schedule(cases, heights, repetitions, seed):
    """Rotate methods so each occupies every position once per three repeats."""
    rng = random.Random(seed)
    cells = [(case, height) for height in heights for case in cases]
    orders = {}
    for cell in cells:
        methods = list(METHODS)
        rng.shuffle(methods)
        orders[cell] = methods
    jobs = []
    for repeat in range(repetitions):
        rng.shuffle(cells)
        for case, height in cells:
            methods = orders[(case, height)]
            offset = repeat % len(methods)
            for method in methods[offset:] + methods[:offset]:
                jobs.append({"case": case, "height_mm": height,
                             "method": method, "repeat": repeat + 1})
    return jobs


def command_output(args):
    try:
        return subprocess.check_output(args, cwd=ROOT, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def metadata():
    files = [HERE / "controlled_worker.py", HERE / "controlled_fixtures.py",
             Path(__file__), ROOT / "src/layerloom/planar_bands.py",
             ROOT / "src/layerloom/planar_materials.py",
             ROOT / "src/layerloom/strata.py",
             ROOT / "src/supporting_scripts/3mf_meshlab_repair_3mf.py"]
    return {
        "utc_started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": platform.platform(), "machine": platform.machine(),
        "processor": command_output(["sysctl", "-n", "machdep.cpu.brand_string"]),
        "physical_cores": command_output(["sysctl", "-n", "hw.physicalcpu"]),
        "logical_cores": os.cpu_count(),
        "memory_bytes": command_output(["sysctl", "-n", "hw.memsize"]),
        "python": sys.version, "python_executable": sys.executable,
        "git_revision": command_output(["git", "rev-parse", "HEAD"]),
        "git_branch": command_output(["git", "branch", "--show-current"]),
        "git_status": command_output(["git", "status", "--short"]),
        "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in files if p.exists()},
        "thread_environment": THREAD_ENV,
        "thread_control_limit": "Environment limits common BLAS/OpenMP runtimes; not a guarantee for every native library.",
        "load_average_at_start": list(os.getloadavg()),
    }


def summarize(runs):
    groups = {}
    for run in runs:
        key = (run["case"], run["height_mm"], run["method"])
        groups.setdefault(key, []).append(run)
    summaries = []
    for (case, height, method), group in groups.items():
        successful = [r for r in group if r.get("status") == "completed"]
        full = [r["result"] for r in successful if r.get("audit_requested") == "full"
                and r["result"].get("audit", {}).get("enabled") is True]
        fingerprints = [r["result"].get("fingerprints", {}).get("combined_sha256")
                        for r in successful]
        stable = bool(fingerprints) and None not in fingerprints and len(set(fingerprints)) == 1
        sources = [r["result"].get("source", {}).get("sha256") for r in successful]
        same_source = bool(sources) and all(sources) and len(set(sources)) == 1
        names = sorted({k for r in successful for k, v in r["result"].get("timings", {}).items()
                        if isinstance(v, (int, float))})
        timings = {}
        for name in names:
            values = [r["result"]["timings"][name] for r in successful
                      if isinstance(r["result"].get("timings", {}).get(name), (int, float))]
            timings[name] = {"median": statistics.median(values), "min": min(values),
                             "max": max(values), "values": values}
        summaries.append({"case": case, "height_mm": height, "method": method,
                          "completed_repeats": len(successful), "requested_repeats": len(group),
                          "identical_geometry_across_repeats": stable,
                          "identical_source_across_repeats": same_source,
                          "timings": timings,
                          "counts": full[0].get("counts") if full else None,
                          "audit": full[0].get("audit") if full else None,
                          "geometry_status": full[0].get("status") if full else None,
                          "errors": full[0].get("errors") if full else None,
                          "warnings": full[0].get("warnings") if full else None,
                          "audit_applies_to_all_completed_repeats": stable and same_source and bool(full),
                          "repair": full[0].get("repair") if full else None})
    return summaries


def compact_result(result):
    """Keep aggregate evidence; individual meshes' diagnostics stay in run JSON."""
    compact = {key: value for key, value in result.items() if key not in {"bands", "native_parts"}}
    compact["audit"] = {key: value for key, value in result.get("audit", {}).items()
                        if key != "native_midpoint_profiles"}
    return compact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES))
    parser.add_argument("--heights", nargs="+", type=float, default=[0.2])
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261022)
    parser.add_argument("--timeout", type=float, default=1200)
    parser.add_argument("--output", type=Path, default=HERE / "results/controlled_benchmark.json")
    args = parser.parse_args()
    if args.repetitions < 1 or any(h <= 0 for h in args.heights):
        parser.error("Repetitions and heights must be positive")
    args.output = args.output.resolve()
    run_dir = args.output.parent / (args.output.stem + "_runs")
    run_dir.mkdir(parents=True, exist_ok=True)
    jobs = schedule(args.cases, args.heights, args.repetitions, args.seed)
    env = {**os.environ, **THREAD_ENV, "PYTHONHASHSEED": "0",
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT / "src")}
    report = {"schema_version": 1, "metadata": metadata(),
              "protocol": {
                  "cases": args.cases, "heights_mm": args.heights,
                  "repetitions": args.repetitions, "seed": args.seed,
                  "execution": "Serial fresh worker processes; seeded order with method-position rotation.",
                  "timed": "In-memory geometry repair, per-source-part layer bands, then native material assembly; intrinsic method checks included.",
                  "excluded": "Module imports, source reading, warm-up, common external audit, fingerprints, export, slicer processing, and printing.",
                  "audit_policy": "Full common audit on repeat 1; exact geometry fingerprints must match before applying it to later repeats.",
                  "normalization": "Original per-part layer indices, original global Z origin, identical 0.001 mm Z gap and midpoint assignment.",
                  "notes": "Local machine; no CPU pinning or isolation from OS/background apps. Common footprint audit is not included in primary timing.",
              }, "runs": []}

    def save():
        report["summary"] = summarize(report["runs"])
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")

    save()
    for index, job in enumerate(jobs, 1):
        stem = f"{index:02d}_{job['case']}_{job['height_mm']:g}_{job['method']}_r{job['repeat']}"
        result_path = run_dir / f"{stem}.json"
        # Never let a zero-exit worker that failed to write output reuse an
        # earlier invocation's geometry or timings.
        result_path.unlink(missing_ok=True)
        audit = "full" if job["repeat"] == 1 else "none"
        command = [sys.executable, str(HERE / "controlled_worker.py"),
                   "--case", job["case"], "--height", str(job["height_mm"]),
                   "--method", job["method"], "--audit", audit,
                   "--output", str(result_path)]
        run = {**job, "order": index, "audit_requested": audit,
               "load_average_before": list(os.getloadavg())}
        print(f"[{index}/{len(jobs)}] {stem} audit={audit}", flush=True)
        start = time.perf_counter()
        try:
            proc = subprocess.run(command, cwd=ROOT, env=env, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  timeout=args.timeout)
            run["process_returncode"] = proc.returncode
            (run_dir / f"{stem}.log").write_text(proc.stdout)
            if proc.returncode == 0 and result_path.exists():
                run["result"] = compact_result(json.loads(result_path.read_text()))
                run["detail_file"] = str(result_path.relative_to(args.output.parent))
                run["status"] = "completed"
            else:
                run["status"] = "failed"
                run["diagnostic_tail"] = proc.stdout[-4000:]
        except subprocess.TimeoutExpired as exc:
            run["status"] = "timeout"
            output = exc.stdout or b""
            if isinstance(output, bytes):
                output = output.decode(errors="replace")
            (run_dir / f"{stem}.log").write_text(output)
            run["diagnostic_tail"] = output[-4000:]
        run["process_elapsed_s_including_excluded_work"] = time.perf_counter() - start
        report["runs"].append(run)
        save()
        timings = run.get("result", {}).get("timings", {})
        print(f"  {run['status']}: matched={timings.get('matched_part_bands_s')}s core={timings.get('core_s')}s", flush=True)
    report["metadata"]["utc_finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report["metadata"]["load_average_at_finish"] = list(os.getloadavg())
    save()
    print(f"Saved {args.output}", flush=True)
    return 0 if all(r["status"] == "completed" for r in report["runs"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
