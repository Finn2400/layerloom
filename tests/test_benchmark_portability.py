"""Exercise OS and malformed-input cases without launching geometry workers."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest


HERE = Path(__file__).resolve().parents[1] / "experiments/planar_bands"


def load_script(name):
    spec = importlib.util.spec_from_file_location("portability_" + name, HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HERE))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


DRIVERS = [load_script(name) for name in
           ("controlled_benchmark", "optimized_benchmark", "profiled_benchmark")]
base = DRIVERS[0]


@pytest.mark.parametrize("driver", DRIVERS, ids=lambda module: module.__name__)
@pytest.mark.parametrize("option", ["--heights", "--timeout"])
@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "-1"])
def test_invalid_numeric_arguments_fail_before_writing_or_launching_workers(driver, option, value,
                                                                           monkeypatch, tmp_path):
    output = tmp_path / "untouched" / "report.json"
    monkeypatch.setattr(sys, "argv", [str(driver.__file__), f"{option}={value}", "--output", str(output)])
    monkeypatch.setattr(driver.subprocess, "run", lambda *a, **k: pytest.fail("Worker must not start"))
    with pytest.raises(SystemExit) as exc:
        driver.main()
    assert exc.value.code == 2
    assert not output.parent.exists()


def test_load_average_is_optional_when_os_does_not_offer_it(monkeypatch):
    monkeypatch.delattr(base.os, "getloadavg", raising=False)
    assert base.load_average() is None


def test_load_average_is_optional_when_os_rejects_it(monkeypatch):
    def unavailable():
        raise OSError("load averages are unavailable")
    monkeypatch.setattr(base.os, "getloadavg", unavailable, raising=False)
    assert base.load_average() is None


def test_non_macos_metadata_avoids_macos_only_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(base.platform, "system", lambda: "Windows")
    monkeypatch.setattr(base.platform, "processor", lambda: "test processor")
    monkeypatch.delattr(base.os, "getloadavg", raising=False)
    monkeypatch.setattr(base, "command_output", lambda args: calls.append(args))
    metadata = base.metadata()
    assert all(command[0] != "sysctl" for command in calls)
    assert metadata["processor"] == "test processor"
    assert metadata["load_average_at_start"] is None
    assert metadata["physical_cores"] is None and metadata["memory_bytes"] is None
    assert "src/layerloom/planar_bands.py" in metadata["source_sha256"]


@pytest.mark.parametrize("payload", [
    "[]", "null", "{}", '{"timings": []}', '{"timings": {"core_s": NaN}}',
    '{"timings": {"core_s": Infinity}}', '{"timings": {"core_s": 1e999}}',
    '{"timings": {"core_s": -1}}', '{"timings": {"core_s": true}}',
])
def test_worker_reports_reject_invalid_shape_and_nonfinite_or_invalid_timings(tmp_path, payload):
    path = tmp_path / "worker.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError):
        base.read_worker_result(path)


def test_worker_report_uses_utf8_and_accepts_deferred_timing(tmp_path):
    path = tmp_path / "worker.json"
    result = {"timings": {"core_s": 1.5, "matched_part_bands_s": None}, "label": "青色"}
    path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    assert base.read_worker_result(path) == result


@pytest.mark.parametrize("field", ["audit", "source", "fingerprints", "settings", "counts",
                                    "versions", "baseline_planar", "implementation",
                                    "audit.summary", "implementation.bindings",
                                    "implementation.source_sha256"])
@pytest.mark.parametrize("value", [None, [], "invalid"])
def test_present_nested_report_maps_must_be_objects(tmp_path, field, value):
    result = {"timings": {"core_s": 1}}
    target = result
    parts = field.split(".")
    for name in parts[:-1]:
        target = target.setdefault(name, {})
    target[parts[-1]] = value
    path = tmp_path / "worker.json"
    path.write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(ValueError, match="must be an object"):
        base.read_worker_result(path)


@pytest.mark.parametrize("field", ["source.sha256", "fingerprints.combined_sha256",
                                    "fingerprints.native_parts_sha256", "baseline_planar.sha256"])
@pytest.mark.parametrize("value", [["bad"], {"bad": 1}, 123, False])
def test_consumed_fingerprints_are_strings_or_null(tmp_path, field, value):
    parent, name = field.split(".")
    path = tmp_path / "worker.json"
    path.write_text(json.dumps({"timings": {"core_s": 1}, parent: {name: value}}), encoding="utf-8")
    with pytest.raises(ValueError, match="must be a string or null"):
        base.read_worker_result(path)
