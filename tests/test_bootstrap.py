"""Exercise setup recovery without network access or modifying a real install."""
from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SPEC = importlib.util.spec_from_file_location("layerloom_bootstrap", Path(__file__).parents[1] / "scripts/bootstrap.py")
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


@pytest.fixture
def setup_tree(tmp_path, monkeypatch):
    source = tmp_path / "LayerLoom café source"
    (source / "scripts").mkdir(parents=True)
    (source / "scripts/runtime-pins.txt").write_text("python 3.12.15\nuv 0.13.0\n")
    (source / ".python-version").write_text("3.12.15\n")
    (source / "pyproject.toml").write_text("[project]\nname = 'layerloom'\n")
    (source / "uv.lock").write_text("version = 1\n")
    (source / "src/layerloom").mkdir(parents=True)
    for name in ("__init__.py", "gui_app.py"):
        (source / "src/layerloom" / name).write_text("# fixture\n")
    (source / ".venv").mkdir()
    (source / ".venv/user-file.txt").write_text("preserve me")
    home = tmp_path / "Application Support" / "LayerLoom"
    home.mkdir(parents=True)
    monkeypatch.setattr(bootstrap.platform, "python_version", lambda: "3.12.15")
    monkeypatch.delenv("LAYERLOOM_OFFLINE", raising=False)
    return source, home, home / "uv"


def fake_installer(calls, *, doctor_status=0, sync_status=0):
    def run(command, *, source, env, log):
        calls.append(command)
        if "sync" in command:
            target = bootstrap.environment_python(Path(env["UV_PROJECT_ENVIRONMENT"]))
            target.parent.mkdir(parents=True)
            target.write_text("a simulated environment interpreter")
            return sync_status
        return doctor_status
    return run


def test_install_then_offline_reuse_never_touches_old_venv(setup_tree, monkeypatch):
    source, home, uv = setup_tree
    calls = []
    monkeypatch.setattr(bootstrap, "run", fake_installer(calls))
    installed, _ = bootstrap.prepare(source, home, uv, repair=False, log=io.StringIO())
    assert "sync" in calls[0]
    assert "--locked" in calls[0] and "--managed-python" in calls[0]
    assert "3.12.15" in calls[0]
    assert str(source) in calls[0]  # Spaces/non-ASCII stay one subprocess argument.
    monkeypatch.setenv("LAYERLOOM_OFFLINE", "1")
    calls.clear()
    reused, _ = bootstrap.prepare(source, home, uv, repair=False, log=io.StringIO())
    assert reused == installed
    assert len(calls) == 1 and "sync" not in calls[0]
    assert (source / ".venv/user-file.txt").read_text() == "preserve me"


def test_repair_creates_generation_and_preserves_previous_files(setup_tree, monkeypatch):
    source, home, uv = setup_tree
    monkeypatch.setattr(bootstrap, "run", fake_installer([]))
    old, _ = bootstrap.prepare(source, home, uv, repair=False, log=io.StringIO())
    (old / "user-file.txt").write_text("not ours to remove")
    new, _ = bootstrap.prepare(source, home, uv, repair=True, log=io.StringIO())
    assert new != old
    assert (old / "user-file.txt").read_text() == "not ours to remove"
    assert bootstrap.ready_environment(new.parent, bootstrap.installation_identity(source, bootstrap.read_pins(source))) == new


def test_interrupted_install_is_not_published(setup_tree, monkeypatch):
    source, home, uv = setup_tree
    monkeypatch.setattr(bootstrap, "run", fake_installer([], sync_status=1))
    with pytest.raises(bootstrap.SetupError, match="did not finish"):
        bootstrap.prepare(source, home, uv, repair=False, log=io.StringIO())
    assert not list(home.rglob("ready.json"))
    monkeypatch.setattr(bootstrap, "run", fake_installer([]))
    new, _ = bootstrap.prepare(source, home, uv, repair=False, log=io.StringIO())
    assert (new.parent / "ready.json").is_file()
    assert len(list(new.parent.glob("env-*"))) == 2


def test_failed_repair_keeps_previous_ready_pointer(setup_tree, monkeypatch):
    source, home, uv = setup_tree
    monkeypatch.setattr(bootstrap, "run", fake_installer([]))
    old, _ = bootstrap.prepare(source, home, uv, repair=False, log=io.StringIO())
    before = (old.parent / "ready.json").read_bytes()
    monkeypatch.setattr(bootstrap, "run", fake_installer([], doctor_status=1))
    with pytest.raises(bootstrap.SetupError, match="startup check"):
        bootstrap.prepare(source, home, uv, repair=True, log=io.StringIO())
    assert (old.parent / "ready.json").read_bytes() == before


def test_moved_source_and_updated_lock_select_new_environment(setup_tree):
    source, home, _ = setup_tree
    first = bootstrap.identity_key(bootstrap.installation_identity(source, bootstrap.read_pins(source)))
    (source / "uv.lock").write_text("version = 2\n")
    second = bootstrap.identity_key(bootstrap.installation_identity(source, bootstrap.read_pins(source)))
    moved = source.with_name("moved source")
    source.rename(moved)
    third = bootstrap.identity_key(bootstrap.installation_identity(moved, bootstrap.read_pins(moved)))
    assert len({first, second, third}) == 3


@pytest.mark.parametrize("problem", ["owner", "missing-python", "escape", "wrong-identity", "malformed"])
def test_invalid_ready_state_is_rejected(setup_tree, monkeypatch, problem):
    source, home, uv = setup_tree
    monkeypatch.setattr(bootstrap, "run", fake_installer([]))
    installed, _ = bootstrap.prepare(source, home, uv, repair=False, log=io.StringIO())
    identity = bootstrap.installation_identity(source, bootstrap.read_pins(source))
    state = installed.parent / "ready.json"
    if problem == "owner":
        (installed / "layerloom-owner.json").write_text('{}')
    elif problem == "missing-python":
        bootstrap.environment_python(installed).unlink()
    elif problem == "escape":
        state.write_text(json.dumps({"identity": identity, "environment": "env-../../outside"}))
    elif problem == "wrong-identity":
        identity = dict(identity, python="3.9.0")
    else:
        state.write_text("not JSON")
    assert bootstrap.ready_environment(installed.parent, identity) is None


def test_environment_ignores_active_interpreters_and_uv_overrides(tmp_path, monkeypatch):
    for name in ("VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONPATH", "PYTHONHOME", "UV_PYTHON", "UV_INDEX", "QT_API", "QT_PLUGIN_PATH"):
        monkeypatch.setenv(name, "unrelated-user-environment")
    clean = bootstrap.clean_environment(tmp_path)
    assert not {"VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONPATH", "PYTHONHOME", "UV_PYTHON", "UV_INDEX"} & clean.keys()
    assert clean["UV_PYTHON_INSTALL_DIR"] == str(tmp_path / "python")
    assert clean["QT_API"] == "pyqt5"
    assert "QT_PLUGIN_PATH" not in clean


def test_mismatched_pins_have_actionable_error(setup_tree):
    source, _, _ = setup_tree
    (source / ".python-version").write_text("3.10.0")
    with pytest.raises(bootstrap.SetupError, match="fresh LayerLoom release"):
        bootstrap.read_pins(source)


def test_lock_releases_after_failure(tmp_path):
    with pytest.raises(RuntimeError):
        with bootstrap.installation_lock(tmp_path):
            raise RuntimeError("simulated interrupted setup")
    with bootstrap.installation_lock(tmp_path, timeout=0):
        pass


def test_concurrent_setup_cannot_take_the_same_lock(tmp_path):
    script = """
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location('bootstrap', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with module.installation_lock(pathlib.Path(sys.argv[2])):
    print('locked', flush=True)
    sys.stdin.read(1)
"""
    process = subprocess.Popen([sys.executable, "-c", script, str(Path(bootstrap.__file__)), str(tmp_path)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(bootstrap.SetupError, match="Another setup"):
            with bootstrap.installation_lock(tmp_path, timeout=0):
                pytest.fail("Concurrent setup obtained a held lock")
    finally:
        process.communicate("x", timeout=10)
    with bootstrap.installation_lock(tmp_path, timeout=0):
        pass


def test_offline_first_install_never_starts_downloads(setup_tree, monkeypatch):
    source, home, uv = setup_tree
    monkeypatch.setenv("LAYERLOOM_OFFLINE", "1")
    calls = []
    monkeypatch.setattr(bootstrap, "run", fake_installer(calls))
    with pytest.raises(bootstrap.SetupError, match="Connect to the internet"):
        bootstrap.prepare(source, home, uv, repair=False, log=io.StringIO())
    assert calls == []


@pytest.mark.parametrize("offline", [False, True])
def test_unexecutable_ready_python_recovers_without_losing_old_environment(setup_tree, monkeypatch, offline):
    source, home, uv = setup_tree
    monkeypatch.setattr(bootstrap, "run", fake_installer([]))
    old, _ = bootstrap.prepare(source, home, uv, repair=False, log=io.StringIO())
    before = (old.parent / "ready.json").read_bytes()
    original_python = bootstrap.environment_python(old).read_bytes()
    calls = []
    install = fake_installer(calls)

    def broken_existing(command, **kwargs):
        if command[0] == str(bootstrap.environment_python(old)):
            raise OSError("damaged executable")
        return install(command, **kwargs)

    monkeypatch.setattr(bootstrap, "run", broken_existing)
    if offline:
        monkeypatch.setenv("LAYERLOOM_OFFLINE", "1")
        with pytest.raises(bootstrap.SetupError, match="Connect to the internet"):
            bootstrap.prepare(source, home, uv, repair=False, log=io.StringIO())
        assert calls == []
        assert (old.parent / "ready.json").read_bytes() == before
    else:
        new, _ = bootstrap.prepare(source, home, uv, repair=False, log=io.StringIO())
        assert new != old and "sync" in calls[0]
    assert bootstrap.environment_python(old).read_bytes() == original_python
    assert (source / ".venv/user-file.txt").read_text() == "preserve me"


@pytest.mark.parametrize("missing", ["uv.lock", "scripts/runtime-pins.txt", "src/layerloom/gui_app.py"])
def test_incomplete_source_explains_extraction_before_creating_environment(setup_tree, monkeypatch, missing):
    source, home, uv = setup_tree
    (source / missing).unlink()
    calls = []
    monkeypatch.setattr(bootstrap, "run", fake_installer(calls))
    with pytest.raises(bootstrap.SetupError, match="Extract the entire ZIP"):
        bootstrap.prepare(source, home, uv, repair=False, log=io.StringIO())
    assert calls == []
    assert not (home / "environments").exists()


@pytest.mark.parametrize("manifest", ["python 3.12.15\n", "python 3.12.15\nuv broken\n"])
def test_incomplete_manifest_has_recovery_instructions(setup_tree, manifest):
    source, _, _ = setup_tree
    (source / "scripts/runtime-pins.txt").write_text(manifest)
    with pytest.raises(bootstrap.SetupError, match="fresh LayerLoom ZIP"):
        bootstrap.read_pins(source)


def test_log_creation_failure_is_readable(tmp_path, capsys):
    home = tmp_path / "not-a-directory"
    home.write_text("preserve me")
    assert bootstrap.main(["--home", str(home), "--uv", str(tmp_path / "uv")]) == 1
    assert "cannot write its setup log" in capsys.readouterr().err
    assert home.read_text() == "preserve me"


@pytest.mark.parametrize("include_scripts", [False, True])
def test_real_launcher_rejects_partial_extraction_before_download(tmp_path, include_scripts):
    repo = Path(__file__).parents[1]
    source = tmp_path / "LayerLoom café partial"
    source.mkdir()
    home = tmp_path / "unused home"
    windows = os.name == "nt"
    launcher = "Launch LayerLoom.bat" if windows else "Launch LayerLoom.command"
    shutil.copyfile(repo / launcher, source / launcher)
    if include_scripts:
        (source / "scripts").mkdir()
        script = "bootstrap.ps1" if windows else "bootstrap.sh"
        shutil.copyfile(repo / "scripts" / script, source / "scripts" / script)
    command = ([os.environ["COMSPEC"], "/d", "/c", str(source / launcher)] if windows
               else ["bash", str(source / launcher)])
    result = subprocess.run(command, input="", capture_output=True, text=True, timeout=30,
                            env={**os.environ, "LAYERLOOM_HOME": str(home)})
    assert result.returncode != 0
    assert "Extract" in result.stdout + result.stderr
    assert not home.exists()


@pytest.mark.parametrize("blocked", ["home", "logs"])
def test_real_launcher_explains_unwritable_storage_before_download(tmp_path, blocked):
    repo = Path(__file__).parents[1]
    home = tmp_path / "LayerLoom storage"
    obstruction = home if blocked == "home" else home / "logs"
    obstruction.parent.mkdir(parents=True, exist_ok=True)
    obstruction.write_text("user file: keep me")
    command = ([os.environ["COMSPEC"], "/d", "/c", str(repo / "Launch LayerLoom.bat"), "--doctor-only"]
               if os.name == "nt" else ["bash", str(repo / "Launch LayerLoom.command"), "--doctor-only"])
    result = subprocess.run(command, input="", capture_output=True, text=True, timeout=30,
                            env={**os.environ, "LAYERLOOM_HOME": str(home)})
    assert result.returncode != 0
    assert "Check folder permissions and free disk space" in result.stdout + result.stderr
    assert obstruction.read_text() == "user file: keep me"
    assert "Downloading" not in result.stdout
