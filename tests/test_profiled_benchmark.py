"""Check complete frozen-kernel binding and protection of prior measurements."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


HERE = Path(__file__).resolve().parents[1] / "experiments/planar_bands"


def load_script(name):
    spec = importlib.util.spec_from_file_location("test_driver_" + name, HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HERE))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


benchmark = load_script("profiled_benchmark")
worker = load_script("profiled_worker")
BINDINGS = {
    "section_profile": "planar_bands", "reconstruct_layer_profiles": "planar_bands",
    "extrude_profile": "planar_bands", "partition_materials": "planar_materials",
    "_float32_mesh_checks": "contour_repair",
}


def configure_driver(monkeypatch, tmp_path):
    reference = tmp_path / "reference.json"
    reference.write_bytes((HERE / "results/controlled_benchmark.json").read_bytes())
    supplement = tmp_path / "supplement.json"
    supplement.write_text('{"passed": false}')
    previous = tmp_path / "previous.json"
    frozen = {name + ".py": ("frozen " + name).encode() for name in worker.MODULES}
    previous.write_text(json.dumps({"metadata": {"source_sha256": {
        "src/layerloom/" + name: hashlib.sha256(data).hexdigest() for name, data in frozen.items()}}}))
    output = tmp_path / "profiled.json"
    monkeypatch.setattr(benchmark.base, "metadata", lambda: {"source_sha256": {}})
    monkeypatch.setattr(benchmark.subprocess, "check_output",
                        lambda command, **kw: frozen[command[-1].rsplit("/", 1)[-1]])
    monkeypatch.setattr(sys, "argv", [str(HERE / "profiled_benchmark.py"), "--cases", "cubes",
        "--repetitions", "1", "--reference", str(reference), "--prism-audit", str(supplement),
        "--previous", str(previous), "--output", str(output)])
    return reference, supplement, previous, output, frozen


@pytest.mark.parametrize("name", worker.MODULES)
def test_each_frozen_module_must_match_previous_measured_source(monkeypatch, tmp_path, name):
    _, _, previous, output, _ = configure_driver(monkeypatch, tmp_path)
    data = json.loads(previous.read_text())
    data["metadata"]["source_sha256"][f"src/layerloom/{name}.py"] = "wrong"
    previous.write_text(json.dumps(data))
    saved = previous.read_bytes()
    monkeypatch.setattr(benchmark.subprocess, "run", lambda *a, **k: pytest.fail("No worker may start"))
    with pytest.raises(ValueError, match=f"Frozen {name} differs"):
        benchmark.main()
    assert previous.read_bytes() == saved
    assert not output.exists()


@pytest.mark.parametrize("target", ["reference", "supplement", "previous", "previous_symlink", "previous_detail"])
def test_previous_reports_and_detail_directories_are_protected(monkeypatch, tmp_path, target):
    reference, supplement, previous, _, _ = configure_driver(monkeypatch, tmp_path)
    originals = {p: p.read_bytes() for p in (reference, supplement, previous)}
    if target == "previous_symlink":
        destination = tmp_path / "alias.json"
        destination.symlink_to(previous)
    elif target == "previous_detail":
        destination = previous.with_suffix(".txt")
    else:
        destination = {"reference": reference, "supplement": supplement, "previous": previous}[target]
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--output", str(destination)])
    monkeypatch.setattr(benchmark.subprocess, "run", lambda *a, **k: pytest.fail("No worker may start"))
    with pytest.raises(SystemExit) as exc:
        benchmark.main()
    assert exc.value.code == 2
    assert all(p.read_bytes() == data for p, data in originals.items())


@pytest.mark.parametrize("tamper", [None, "frozen_flag", "bindings", "source_sha256"])
def test_driver_checks_complete_runtime_provenance_for_both_2d_arms(monkeypatch, tmp_path, tamper):
    reference, _, previous, output, frozen = configure_driver(monkeypatch, tmp_path)
    refs = benchmark.references(json.loads(reference.read_text()))
    observed = []

    def run(command, **kwargs):
        arguments = dict(zip(command[2::2], command[3::2]))
        assert all(kwargs["env"][key] == "1" for key in benchmark.base.THREAD_ENV)
        is_frozen = "--frozen-directory" in arguments
        directory = Path(arguments["--frozen-directory"]) if is_frozen else benchmark.ROOT / "src/layerloom"
        method = arguments["--method"]
        if is_frozen:
            assert method == "contour_optimized"
            assert all((directory / name).read_bytes() == data for name, data in frozen.items())
        ref_method = "contour" if method == "contour_optimized" else method
        result = deepcopy(refs[("cubes", 0.2, ref_method)])
        result.update(method=method, audit={"enabled": False})
        result["implementation"] = {
            "frozen": is_frozen,
            "bindings": {name: str(directory / (module + ".py")) for name, module in BINDINGS.items()},
            "source_sha256": {name + ".py": benchmark.digest(directory / (name + ".py")) for name in worker.MODULES},
        }
        if method == "contour_optimized" and tamper:
            if tamper == "frozen_flag":
                result["implementation"]["frozen"] = not is_frozen
            elif tamper == "bindings":
                result["implementation"]["bindings"]["partition_materials"] = "wrong.py"
            else:
                result["implementation"]["source_sha256"]["contour_repair.py"] = "wrong"
        Path(arguments["--output"]).write_text(json.dumps(result))
        observed.append((method, is_frozen))
        return SimpleNamespace(returncode=0, stdout="completed")

    saved = previous.read_bytes()
    monkeypatch.setattr(benchmark.subprocess, "run", run)
    assert benchmark.main() == (0 if tamper is None else 1)
    assert observed.count(("contour_optimized", True)) == 1
    assert observed.count(("contour_optimized", False)) == 1
    report = json.loads(output.read_text())
    assert len(report["runs"]) == 4
    for row in report["runs"]:
        assert row["validation"]["audit_inherited_by_exact_identity"]
        assert row["frozen_control_verified"] == (tamper is None or row["method"] not in {"contour", "contour_optimized"})
    assert previous.read_bytes() == saved


@pytest.mark.parametrize("wrong_binding", [False, True])
def test_worker_loads_all_frozen_modules_before_resolving_entrypoints(monkeypatch, tmp_path, wrong_binding):
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    sources = {
        "planar_bands": "def section_profile(): return 'old profiles'\ndef reconstruct_layer_profiles(): return section_profile()\ndef extrude_profile(): return 'old extrusion'\n",
        "planar_materials": "from layerloom.planar_bands import extrude_profile\ndef partition_materials(): return extrude_profile() + ' partition'\n",
        "contour_repair": "from layerloom.planar_materials import partition_materials\ndef _float32_mesh_checks(): return partition_materials() + ' checks'\n",
    }
    for name, source in sources.items():
        (frozen / (name + ".py")).write_text(source)
        # Register restoration before the production loader replaces each entry.
        monkeypatch.setitem(sys.modules, "layerloom." + name, None)
    package = ModuleType("layerloom")
    package.__path__ = []
    monkeypatch.setitem(sys.modules, "layerloom", package)
    stub = ModuleType("controlled_worker")
    calls = []

    def entrypoint(name):
        if name not in BINDINGS:
            raise AttributeError(name)
        if wrong_binding and name == "partition_materials":
            return lambda: "wrong implementation"
        return getattr(sys.modules["layerloom." + BINDINGS[name]], name)

    def run_case(case, height, method, audit):
        calls.append({name: entrypoint(name)() for name in BINDINGS})
        assert audit is False
        return {"status": "ok", "timings": {}}

    stub.__getattr__ = entrypoint
    stub.run_case = run_case
    stub._json_default = lambda value: value
    monkeypatch.setitem(sys.modules, "controlled_worker", stub)
    monkeypatch.setattr(sys, "path", list(sys.path))
    output = tmp_path / "result.json"
    monkeypatch.setattr(sys, "argv", ["profiled_worker.py", "--case", "cubes", "--height", ".2",
        "--method", "contour_optimized", "--frozen-directory", str(frozen), "--output", str(output)])
    if wrong_binding:
        with pytest.raises(ValueError, match="Wrong implementation bound for partition_materials"):
            worker.main()
        assert not calls and not output.exists()
    else:
        worker.main()
        assert calls == [{"section_profile": "old profiles", "reconstruct_layer_profiles": "old profiles",
            "extrude_profile": "old extrusion", "partition_materials": "old extrusion partition",
            "_float32_mesh_checks": "old extrusion partition checks"}]
        result = json.loads(output.read_text())["implementation"]
        assert result["frozen"] is True
        assert result["bindings"] == {name: str(frozen / (module + ".py")) for name, module in BINDINGS.items()}
        assert result["source_sha256"] == {name + ".py": hashlib.sha256(source.encode()).hexdigest()
                                            for name, source in sources.items()}
