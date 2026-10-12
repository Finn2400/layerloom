from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import xml.etree.ElementTree as ET
import zipfile

import pytest

from layerloom import runtime, worker


@pytest.fixture(autouse=True)
def clear_capability_cache():
    runtime.repair_available.cache_clear()
    yield
    runtime.repair_available.cache_clear()


def _source_environment() -> dict[str, str]:
    env = os.environ.copy()
    source_root = str(Path(runtime.__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [source_root, env.get("PYTHONPATH")]))
    return env


def test_source_worker_keeps_paths_as_arguments(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    args = ["-i", "/a path/input.3mf", "-o", "/a path/output.3mf"]
    assert runtime.worker_command("bake", args) == [sys.executable, "-X", "utf8", "-u", "-m", "layerloom.worker", "bake", *args]
    with pytest.raises(ValueError, match="Unknown"):
        runtime.worker_command("os", [])


def test_worker_diagnostics_decode_unicode_and_tolerate_native_bytes():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write('café 染'.encode('utf-8') + b'\\xff')"],
        capture_output=True, **runtime.worker_process_options(),
    )
    assert result.returncode == 0
    assert result.stdout == "café 染\ufffd"


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_frozen_worker_survives_relocation_and_nested_launch(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    if platform == "darwin":
        root = tmp_path / "Moved App Folder" / "LayerLoom.app" / "Contents"
        executable = root / "MacOS" / "LayerLoom"
        helper = root / "Helpers" / "LayerLoomWorker.app" / "Contents" / "MacOS" / "layerloom-worker"
    else:
        root = tmp_path / "Moved App Folder"
        suffix = ".exe" if platform == "win32" else ""
        executable = root / ("LayerLoom" + suffix)
        helper = root / "worker" / ("layerloom-worker" + suffix)
    helper.parent.mkdir(parents=True)
    helper.touch()
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.chdir(tmp_path)
    assert runtime.worker_command("weave", ["--help"]) == [str(helper), "weave", "--help"]
    monkeypatch.setattr(sys, "executable", str(helper))
    assert runtime.worker_command("bake", ["--help"]) == [str(helper), "bake", "--help"]


def test_missing_frozen_worker_reports_reinstall(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "LayerLoom"))
    with pytest.raises(FileNotFoundError, match="Reinstall the complete"):
        runtime.worker_command("weave")


def test_worker_rejects_unknown_task_before_import(monkeypatch):
    def unexpected_import(_name):
        pytest.fail("An invalid task must not import a module")
    monkeypatch.setattr(worker.importlib, "import_module", unexpected_import)
    with pytest.raises(SystemExit) as exc:
        worker.main(["os", "--help"])
    assert exc.value.code == 2


def test_capabilities_does_not_import_native_modules(monkeypatch, capsys):
    monkeypatch.setattr(worker, "local_repair_available", lambda: False)
    monkeypatch.setattr(worker.importlib, "import_module", lambda _: pytest.fail("Capability lookup imported a module"))
    assert worker.main(["--capabilities"]) == 0
    assert json.loads(capsys.readouterr().out) == {"repair": False}


def test_worker_passes_task_arguments_and_propagates_failure(monkeypatch, capsys):
    received = []
    monkeypatch.setattr(worker.importlib, "import_module", lambda _: SimpleNamespace(main=lambda args: received.extend(args) or 7))
    assert worker.main(["bake", "-i", "a path.3mf", "-o", "b path.3mf"]) == 7
    assert received == ["-i", "a path.3mf", "-o", "b path.3mf"]

    def fail(_):
        raise RuntimeError("native operation failed")
    monkeypatch.setattr(worker.importlib, "import_module", lambda _: SimpleNamespace(main=fail))
    assert worker.main(["repair", "input.3mf"]) == 1
    assert "native operation failed" in capsys.readouterr().err


def test_frozen_repair_probe_uses_worker_and_caches_result(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(runtime, "_worker_prefix", lambda: ["bundled worker"])
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, '{"repair": false}\n', "")
    monkeypatch.setattr(runtime.subprocess, "run", run)
    assert runtime.repair_available() is False
    assert runtime.repair_available() is False
    assert calls == [["bundled worker", "--capabilities"]]


def test_unavailable_optional_repair_is_never_started(tmp_path, monkeypatch):
    from layerloom import headless
    calls = []
    monkeypatch.setattr(headless, "_run_external_tool", lambda label, *args, **kwargs: calls.append(label))
    monkeypatch.setattr(headless, "repair_available", lambda: False)
    kwargs = dict(
        split_3mf=str(tmp_path / "split.3mf"), manifest_out=str(tmp_path / "colors.json"),
        repaired_3mf=str(tmp_path / "repaired.3mf"), target_colors=3, source_scale=0.1,
        missing_color_mode="group", repair=True, strict_repair=False, verbose=False,
    )
    path, warning = headless.import_glb_like_gui("input.glb", **kwargs)
    assert path == kwargs["split_3mf"]
    assert "without repair" in warning
    assert calls == ["glb-split"]
    kwargs["strict_repair"] = True
    with pytest.raises(RuntimeError, match="unavailable"):
        headless.import_glb_like_gui("input.glb", **kwargs)
    assert calls == ["glb-split", "glb-split"]


def test_worker_weaves_and_runs_nested_bake_from_unrelated_directory(tmp_path):
    import trimesh
    from layerloom.export import write_basic_3mf

    source = tmp_path / "small café 染 input.3mf"
    output = tmp_path / "woven café 染 output.3mf"
    mesh = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
    mesh.apply_translation([0, 0, 0.5])
    write_basic_3mf([("cube__PAT_cm__", mesh)], str(source))
    env = _source_environment()
    # Simulate a non-UTF-8 default independently of the host operating system.
    # The explicit interpreter flags must govern weave and its nested bake.
    env.update(PYTHONUTF8="0", PYTHONCOERCECLOCALE="0", LC_ALL="C")
    env.pop("PYTHONIOENCODING", None)
    result = subprocess.run(
        runtime.worker_command("weave", ["-i", str(source), "-o", str(output), "--step", "0.5", "--no-export-stls"]),
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
        **runtime.worker_process_options(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "baked+repacked(stream)" in result.stdout
    assert "café 染" in result.stdout
    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    with zipfile.ZipFile(output) as archive:
        root = ET.fromstring(archive.read("3D/3dmodel.model"))
    assert root.findall(".//m:triangle", ns)
    assert len(root.findall("m:resources/m:object", ns)) == 2
    assert not (tmp_path / "stl_out").exists()


def test_current_qt_gui_imports_without_tk(tmp_path):
    pytest.importorskip("pyvistaqt")
    code = """
import importlib.abc
import sys
class NoTk(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'tkinter' or fullname.startswith('tkinter.'):
            raise ModuleNotFoundError('Tk deliberately unavailable', name=fullname)
sys.meta_path.insert(0, NoTk())
from layerloom.gui_app import _load_current_gui_module
module = _load_current_gui_module()
assert module._V58.tk is None
assert module.QtAssignColorsApp
print('Qt GUI loaded without Tk')
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=_source_environment(), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Qt GUI loaded without Tk" in result.stdout
