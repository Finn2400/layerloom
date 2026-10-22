"""Validate the benchmark controls without importing or running geometry workers."""
from collections import Counter, defaultdict
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


DRIVER = Path(__file__).resolve().parents[1] / "experiments/planar_bands/controlled_benchmark.py"
SPEC = spec_from_file_location("controlled_benchmark_driver", DRIVER)
benchmark = module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def completed_run(repeat, seconds, *, fingerprint="geometry-a", source="source-a",
                  audit_enabled=None, case="hollow_tube", height=0.2, method="contour"):
    full = repeat == 1
    audit = {"enabled": full if audit_enabled is None else audit_enabled}
    if audit["enabled"]:
        audit["summary"] = {"passes_declared_checks": True}
    return {
        "case": case, "height_mm": height, "method": method, "repeat": repeat,
        "status": "completed", "audit_requested": "full" if full else "none",
        "result": {
            "case": case, "height_mm": height, "method": method, "status": "ok",
            "source": {"sha256": source},
            "fingerprints": {"combined_sha256": fingerprint},
            "timings": {"matched_part_bands_s": seconds, "core_s": seconds + 1},
            "counts": {"produced_bands": 100}, "repair": {"fallback_to_raw": False},
            "audit": audit,
        },
    }


def test_schedule_is_complete_reproducible_and_balanced_in_every_three_repeat_block():
    cases, heights = ["benchy", "hollow_tube"], [0.1, 0.2]
    jobs = benchmark.schedule(cases, heights, 6, seed=57)
    assert jobs == benchmark.schedule(cases, heights, 6, seed=57)
    assert jobs != benchmark.schedule(cases, heights, 6, seed=58)
    assert cases == ["benchy", "hollow_tube"]
    assert heights == [0.1, 0.2]
    assert len(jobs) == len(cases) * len(heights) * 6 * len(benchmark.METHODS)
    counts = Counter((j["case"], j["height_mm"], j["repeat"], j["method"]) for j in jobs)
    assert set(counts.values()) == {1}
    orders = defaultdict(dict)
    for start in range(0, len(jobs), len(benchmark.METHODS)):
        chunk = jobs[start:start + len(benchmark.METHODS)]
        assert len({(j["case"], j["height_mm"], j["repeat"]) for j in chunk}) == 1
        first = chunk[0]
        methods = [j["method"] for j in chunk]
        assert set(methods) == set(benchmark.METHODS)
        orders[(first["case"], first["height_mm"])][first["repeat"]] = methods
    assert len(orders) == len(cases) * len(heights)
    for repeats in orders.values():
        assert set(repeats) == set(range(1, 7))
        for block_start in (1, 4):
            for position in range(len(benchmark.METHODS)):
                assert {repeats[r][position] for r in range(block_start, block_start + 3)} == set(benchmark.METHODS)


def test_summary_uses_completed_timings_and_inherits_audit_for_identical_geometry_and_source():
    runs = [completed_run(1, 8), completed_run(2, 2), completed_run(3, 5)]
    failed = {**completed_run(4, 1000), "status": "failed"}
    timed_out = {**completed_run(5, 2000), "status": "timeout"}
    summary, = benchmark.summarize(runs + [failed, timed_out])
    assert summary["completed_repeats"] == 3
    assert summary["requested_repeats"] == 5
    assert summary["timings"]["matched_part_bands_s"] == {
        "median": 5, "min": 2, "max": 8, "values": [8, 2, 5],
    }
    assert summary["identical_geometry_across_repeats"] is True
    assert summary["identical_source_across_repeats"] is True
    assert summary["audit_applies_to_all_completed_repeats"] is True
    assert summary["audit"] == runs[0]["result"]["audit"]
    assert summary["counts"] == runs[0]["result"]["counts"]


@pytest.mark.parametrize("fingerprint", ["geometry-b", None])
def test_changed_or_missing_geometry_fingerprint_blocks_audit_inheritance(fingerprint):
    summary, = benchmark.summarize([completed_run(1, 1), completed_run(2, 1, fingerprint=fingerprint)])
    assert summary["identical_geometry_across_repeats"] is False
    assert summary["audit_applies_to_all_completed_repeats"] is False


@pytest.mark.parametrize("source", ["source-b", None])
def test_changed_or_missing_source_hash_blocks_audit_inheritance(source):
    summary, = benchmark.summarize([completed_run(1, 1), completed_run(2, 1, source=source)])
    assert summary["identical_geometry_across_repeats"] is True
    assert summary["identical_source_across_repeats"] is False
    assert summary["audit_applies_to_all_completed_repeats"] is False


@pytest.mark.parametrize("mode", ["not_requested", "disabled", "absent"])
def test_full_audit_must_be_present_and_enabled_before_inheritance(mode):
    first = completed_run(1, 1)
    if mode == "not_requested":
        first["audit_requested"] = "none"
    elif mode == "disabled":
        first["result"]["audit"] = {"enabled": False}
    else:
        first["result"].pop("audit")
    summary, = benchmark.summarize([first, completed_run(2, 2)])
    assert summary["audit_applies_to_all_completed_repeats"] is False
    assert summary["audit"] is None


def test_geometry_failure_remains_measured_and_failed_quality_audit_is_preserved():
    first, second = completed_run(1, 3), completed_run(2, 1)
    first["result"]["status"] = "geometry_failure"
    first["result"]["audit"]["summary"]["passes_declared_checks"] = False
    second["result"]["status"] = "geometry_failure"
    summary, = benchmark.summarize([first, second])
    assert summary["completed_repeats"] == 2
    assert summary["timings"]["matched_part_bands_s"]["median"] == 2
    assert summary["audit_applies_to_all_completed_repeats"] is True
    assert summary["audit"]["summary"]["passes_declared_checks"] is False


def test_summary_keeps_methods_heights_and_cases_separate_and_handles_no_completions():
    runs = [completed_run(1, 1), completed_run(1, 2, method="cut"),
            completed_run(1, 3, height=0.1), completed_run(1, 4, case="benchy")]
    failed = completed_run(1, 100, method="repair_cut")
    failed["status"] = "failed"
    failed.pop("result")
    summaries = benchmark.summarize([*runs, failed])
    assert len(summaries) == 5
    no_completions = next(s for s in summaries if s["method"] == "repair_cut")
    assert no_completions["completed_repeats"] == 0
    assert no_completions["requested_repeats"] == 1
    assert no_completions["timings"] == {}
    assert no_completions["audit"] is None
    assert no_completions["identical_geometry_across_repeats"] is False
    assert no_completions["audit_applies_to_all_completed_repeats"] is False


def configure_driver(monkeypatch, tmp_path, *extra_args):
    output = tmp_path / "controlled.json"
    monkeypatch.setattr(benchmark, "metadata", lambda: {"test_metadata": True})
    monkeypatch.setattr(benchmark.os, "getloadavg", lambda: (0.1, 0.2, 0.3), raising=False)
    monkeypatch.setattr(benchmark.sys, "argv", [str(DRIVER), "--cases", "cubes",
                                               "--repetitions", "1", "--output", str(output), *extra_args])
    return output


def worker_arguments(command):
    assert command[:2] == [benchmark.sys.executable, str(benchmark.HERE / "controlled_worker.py")]
    return dict(zip(command[2::2], command[3::2]))


def test_driver_runs_fresh_workers_serially_with_controls_and_checkpoints(monkeypatch, tmp_path):
    output = configure_driver(monkeypatch, tmp_path, "--cases", "cubes", "hollow_tube",
                              "--heights", "0.2", "0.4", "--repetitions", "3", "--seed", "17")
    expected = benchmark.schedule(["cubes", "hollow_tube"], [0.2, 0.4], 3, 17)
    observed = []
    monkeypatch.setenv("CONTROLLED_TEST_ENV", "preserved")

    def fake_worker(command, **kwargs):
        # Each previous call must be checkpointed before the next worker starts.
        checkpoint = json.loads(output.read_text())
        assert len(checkpoint["runs"]) == len(observed)
        job = expected[len(observed)]
        arguments = worker_arguments(command)
        assert kwargs["cwd"] == DRIVER.parents[2]
        assert (kwargs["cwd"] / "src/layerloom").is_dir()
        assert kwargs["timeout"] == 1200
        assert kwargs["text"] is True
        assert kwargs["stdout"] is benchmark.subprocess.PIPE
        assert kwargs["stderr"] is benchmark.subprocess.STDOUT
        env = kwargs["env"]
        assert all(env[key] == "1" for key in benchmark.THREAD_ENV)
        assert env["PYTHONHASHSEED"] == "0"
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"
        assert env["PYTHONIOENCODING"] == "utf-8"
        assert env["PYTHONPATH"] == str(DRIVER.parents[2] / "src")
        assert env["CONTROLLED_TEST_ENV"] == "preserved"
        assert arguments["--case"] == job["case"]
        assert float(arguments["--height"]) == job["height_mm"]
        assert arguments["--method"] == job["method"]
        assert arguments["--audit"] == ("full" if job["repeat"] == 1 else "none")
        result = completed_run(job["repeat"], len(observed) + 1, case=job["case"],
                               height=job["height_mm"], method=job["method"])["result"]
        Path(arguments["--output"]).write_text(json.dumps(result))
        observed.append(job)
        return SimpleNamespace(returncode=0, stdout="worker completed\n")

    monkeypatch.setattr(benchmark.subprocess, "run", fake_worker)
    assert benchmark.main() == 0
    report = json.loads(output.read_text())
    assert observed == expected
    assert len(report["runs"]) == 36
    assert [r["order"] for r in report["runs"]] == list(range(1, 37))
    assert all(r["status"] == "completed" for r in report["runs"])
    assert len(report["summary"]) == 12
    assert all(s["completed_repeats"] == 3 for s in report["summary"])
    assert all(s["audit_applies_to_all_completed_repeats"] for s in report["summary"])
    assert "utc_finished" in report["metadata"]
    assert len(list((tmp_path / "controlled_runs").glob("*.log"))) == 36


def test_driver_continues_after_crash_and_timeout_and_preserves_diagnostics(monkeypatch, tmp_path):
    output = configure_driver(monkeypatch, tmp_path)
    calls = []

    def fake_worker(command, **kwargs):
        arguments = worker_arguments(command)
        calls.append(arguments)
        if len(calls) == 1:
            return SimpleNamespace(returncode=7, stdout="worker failed")
        if len(calls) == 2:
            raise benchmark.subprocess.TimeoutExpired(command, kwargs["timeout"], output=b"timed out\xff")
        result = completed_run(1, 1, case=arguments["--case"], method=arguments["--method"])["result"]
        Path(arguments["--output"]).write_text(json.dumps(result))
        return SimpleNamespace(returncode=0, stdout="worker completed")

    monkeypatch.setattr(benchmark.subprocess, "run", fake_worker)
    assert benchmark.main() == 1
    report = json.loads(output.read_text())
    assert [r["status"] for r in report["runs"]] == ["failed", "timeout", "completed"]
    assert report["runs"][0]["process_returncode"] == 7
    assert report["runs"][0]["diagnostic_tail"] == "worker failed"
    assert report["runs"][1]["diagnostic_tail"] == "timed out\ufffd"
    assert all(r["process_elapsed_s_including_excluded_work"] >= 0 for r in report["runs"])
    assert "utc_finished" in report["metadata"]
    assert len(list((tmp_path / "controlled_runs").glob("*.log"))) == 3


def test_driver_never_accepts_a_previous_invocations_result_file(monkeypatch, tmp_path):
    output = configure_driver(monkeypatch, tmp_path)
    run_dir = tmp_path / "controlled_runs"
    run_dir.mkdir()
    for index, job in enumerate(benchmark.schedule(["cubes"], [0.2], 1, 20261022), 1):
        stem = f"{index:02d}_{job['case']}_{job['height_mm']:g}_{job['method']}_r{job['repeat']}"
        old_result = completed_run(1, 123, case=job["case"], method=job["method"])["result"]
        (run_dir / f"{stem}.json").write_text(json.dumps(old_result))

    def no_output_worker(command, **kwargs):
        # A nominally successful process must supply its own fresh result.
        return SimpleNamespace(returncode=0, stdout="no output written")

    monkeypatch.setattr(benchmark.subprocess, "run", no_output_worker)
    assert benchmark.main() == 1
    report = json.loads(output.read_text())
    assert all(r["status"] == "failed" and "result" not in r for r in report["runs"])
    assert all(s["completed_repeats"] == 0 for s in report["summary"])


@pytest.mark.parametrize("payload", ["{truncated", "[]", '{"timings": {"core_s": NaN}}',
                                    '{"timings": {"core_s": 1}, "audit": null}',
                                    '{"timings": {"core_s": 1}, "source": []}',
                                    '{"timings": {"core_s": 1}, "source": {"sha256": ["bad"]}}',
                                    '{"timings": {"core_s": 1}, "fingerprints": {"combined_sha256": {"bad": 1}}}'])
def test_invalid_worker_report_is_recorded_without_aborting_remaining_jobs(monkeypatch, tmp_path, payload):
    output = configure_driver(monkeypatch, tmp_path)

    def fake_worker(command, **kwargs):
        arguments = worker_arguments(command)
        Path(arguments["--output"]).write_text(payload, encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="worker returned malformed output")

    monkeypatch.setattr(benchmark.subprocess, "run", fake_worker)
    assert benchmark.main() == 1
    report = json.loads(output.read_text())
    assert len(report["runs"]) == 3
    assert all(row["status"] == "failed" and "result" not in row for row in report["runs"])
    assert all(row["diagnostic_tail"] for row in report["runs"])
