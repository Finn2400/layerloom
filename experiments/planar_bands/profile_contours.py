#!/usr/bin/env python3
"""Profile the timed contour kernels, excluding imports, input, warmup and hashes.

Use a fresh process with one numerical-library thread. cProfile adds overhead;
its totals identify hotspots and must not be used as benchmark speed claims.
"""
from __future__ import annotations

import argparse
import cProfile
import functools
import hashlib
import json
import os
from pathlib import Path
import platform
import pstats
import subprocess
import time

import controlled_worker as worker

THREAD_VARIABLES = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                    "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS")
KERNELS = {
    "source_profiles": "reconstruct_layer_profiles",
    "material_partition": "partition_materials",
    "final_extrusion": "extrude_profile",
    "native_float32_checks": "_float32_mesh_checks",
}


def rows(stats, sort_key, limit=35):
    result = []
    for (filename, line, name), (primitive, calls, own, cumulative, _) in stats.stats.items():
        result.append({"file": filename, "line": line, "function": name,
                       "primitive_calls": primitive, "calls": calls,
                       "own_s": own, "cumulative_s": cumulative})
    return sorted(result, key=lambda row: row[sort_key], reverse=True)[:limit]


def run(case, height, output):
    if any(os.environ.get(name) != "1" for name in THREAD_VARIABLES):
        raise ValueError("Set all thread variables to 1 before starting Python: " + ", ".join(THREAD_VARIABLES))
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    original_warmup = worker._warmup
    originals = {name: getattr(worker, name) for name in KERNELS.values()}
    profiles = {name: cProfile.Profile() for name in KERNELS}
    counts = {name: 0 for name in KERNELS}
    durations = {name: 0.0 for name in KERNELS}
    warmed = False

    def warmup(*args, **kwargs):
        nonlocal warmed
        result = original_warmup(*args, **kwargs)
        warmed = True
        return result

    def wrapper(group, original):
        @functools.wraps(original)
        def wrapped(*args, **kwargs):
            if not warmed:
                return original(*args, **kwargs)
            counts[group] += 1
            started = time.perf_counter()
            profiles[group].enable()
            try:
                return original(*args, **kwargs)
            finally:
                profiles[group].disable()
                durations[group] += time.perf_counter() - started
        return wrapped

    worker._warmup = warmup
    for group, name in KERNELS.items():
        setattr(worker, name, wrapper(group, originals[name]))
    try:
        result = worker.run_case(case, height, "contour_optimized", audit=False)
    finally:
        worker._warmup = original_warmup
        for name, original in originals.items():
            setattr(worker, name, original)

    groups = {}
    active_profiles = []
    for group, profile in profiles.items():
        if counts[group] == 0:
            groups[group] = {"calls": 0, "wall_s": durations[group], "top_cumulative": [], "top_own": []}
            continue
        active_profiles.append(profile)
        stats = pstats.Stats(profile)
        groups[group] = {"calls": counts[group], "wall_s": durations[group],
                         "profile_total_s": stats.total_tt,
                         "top_cumulative": rows(stats, "cumulative_s"),
                         "top_own": rows(stats, "own_s")}
    total = pstats.Stats(*active_profiles)
    raw_path = output.with_suffix(".pstats")
    total.dump_stats(str(raw_path))
    source_paths = [Path(__file__), Path(worker.__file__)] + [
        worker.ROOT / "src/layerloom" / name for name in
        ("planar_bands.py", "planar_materials.py", "contour_repair.py")]
    report = {
        "description": "One cProfile-instrumented run; cumulative rows overlap. Wall totals include profiler overhead, not comparable benchmark timings.",
        "excluded": ["imports", "input loading", "warmup", "fingerprints", "posthoc audit", "export"],
        "scope": "Profile wrappers cover source profiles, material partition, final extrusion, and mandatory native float32 checks; worker scheduling overhead remains only in worker timings.",
        "case": case, "height_mm": height, "method": "contour_optimized",
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=worker.ROOT, text=True).strip(),
        "source_sha256": {str(path.relative_to(worker.ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths},
        "thread_environment": {name: os.environ[name] for name in THREAD_VARIABLES},
        "machine": platform.platform(), "versions": result["versions"],
        "source": result["source"], "settings": result["settings"],
        "counts": result["counts"], "fingerprints": result["fingerprints"],
        "status": result["status"], "errors": result["errors"],
        "worker_timings_instrumented": result["timings"], "groups": groups,
        "total_profile_s": total.total_tt,
        "top_cumulative": rows(total, "cumulative_s", 60),
        "top_own": rows(total, "own_s", 60), "raw_pstats": str(raw_path),
    }
    output.write_text(json.dumps(report, indent=2, default=worker._json_default, allow_nan=False) + "\n")
    print(json.dumps({"output": str(output), "status": result["status"],
                      "groups_s": durations, "fingerprints": result["fingerprints"]}))
    return 0 if result["status"] == "ok" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=worker.CASES, default="benchy")
    parser.add_argument("--height", type=float, default=0.2)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).parent / "results/contour_profile_before.json")
    args = parser.parse_args()
    return run(args.case, args.height, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
