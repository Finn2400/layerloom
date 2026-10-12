"""Manage the source launcher's private runtime; never touch a user's .venv.

The shell/PowerShell wrappers provision the pinned uv and Python, then run this
stdlib-only module. Environments are created at their final path, never moved.
Incomplete or superseded environments are retained; user files are never erased.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import uuid

OWNER = "layerloom-source-launcher-v1"


class SetupError(RuntimeError):
    pass


def read_pins(source: Path) -> dict[str, str]:
    rows = [line.split() for line in (source / "scripts/runtime-pins.txt").read_text(encoding="utf-8").splitlines()]
    pins = {row[0]: row[1] for row in rows if len(row) == 2 and not row[0].startswith("#")}
    if pins.get("python") != (source / ".python-version").read_text(encoding="utf-8").strip():
        raise SetupError("The downloaded runtime files disagree. Download a fresh LayerLoom release.")
    return pins


def clean_environment(home: Path) -> dict[str, str]:
    # A user's active Conda/venv, uv configuration, or PYTHONPATH must not select
    # an interpreter or inject packages into this application-owned environment.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("UV_") and key not in
           {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV",
            "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH"}}
    env.update(UV_CACHE_DIR=str(home / "cache"), UV_PYTHON_INSTALL_DIR=str(home / "python"),
               UV_PYTHON_INSTALL_BIN="false", UV_PYTHON_INSTALL_REGISTRY="false",
               UV_PYTHON_NO_REGISTRY="true", PYTHONNOUSERSITE="1", PYTHONUNBUFFERED="1", QT_API="pyqt5")
    # An explicit offline test/request remains useful, but cannot change pins.
    if os.environ.get("LAYERLOOM_OFFLINE") == "1":
        env["UV_OFFLINE"] = "true"
    return env


def installation_identity(source: Path, pins: dict[str, str]) -> dict[str, str]:
    return {"owner": OWNER, "source": str(source.resolve()), "python": pins["python"],
            "uv": pins["uv"], "platform": sys.platform, "architecture": platform.machine(),
            "project": hashlib.sha256((source / "pyproject.toml").read_bytes()).hexdigest(),
            "lock": hashlib.sha256((source / "uv.lock").read_bytes()).hexdigest()}


def identity_key(identity: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]


def environment_python(directory: Path) -> Path:
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ready_environment(root: Path, identity: dict[str, str]) -> Path | None:
    try:
        state = json.loads((root / "ready.json").read_text(encoding="utf-8"))
        name = state["environment"]
        if state["identity"] != identity or not isinstance(name, str) or not name.startswith("env-"):
            return None
        candidate = root / name
        if candidate.resolve().parent != root.resolve():
            return None
        if json.loads((candidate / "layerloom-owner.json").read_text(encoding="utf-8")) != identity:
            return None
        return candidate if environment_python(candidate).is_file() else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def publish_ready(root: Path, environment: Path, identity: dict[str, str]) -> None:
    if environment.parent != root:
        raise SetupError("Refusing to publish an environment outside LayerLoom's installation directory.")
    pending = root / f"ready-{uuid.uuid4().hex}.tmp"
    pending.write_text(json.dumps({"identity": identity, "environment": environment.name}, indent=2), encoding="utf-8")
    pending.replace(root / "ready.json")


@contextmanager
def installation_lock(root: Path, timeout: float = 1200):
    """OS locks release automatically if setup is interrupted or crashes."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / "setup.lock").open("a+b") as handle:
        handle.write(b"\0")
        handle.flush()
        start = time.monotonic()
        announced = False
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if not announced:
                    print("Another LayerLoom setup is running. Waiting for it to finish...", flush=True)
                    announced = True
                if time.monotonic() - start > timeout:
                    raise SetupError("Another setup is still running. Close it, then reopen LayerLoom.")
                time.sleep(0.25)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run(command: list[str], *, source: Path, env: dict[str, str], log) -> int:
    log.write("\nCommand: " + repr(command) + "\n")
    log.flush()
    process = subprocess.Popen(command, cwd=source, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
        log.write(line)
    log.flush()
    return process.wait()


def prepare(source: Path, home: Path, uv: Path, *, repair: bool, log) -> tuple[Path, dict[str, str]]:
    pins = read_pins(source)
    if platform.python_version() != pins["python"]:
        raise SetupError(f"The launcher needs its managed Python {pins['python']}. Reopen the included launcher.")
    identity = installation_identity(source, pins)
    root = home / "environments" / identity_key(identity)
    env = clean_environment(home)
    with installation_lock(root):
        existing = None if repair else ready_environment(root, identity)
        if existing is not None:
            print("Checking your existing LayerLoom installation...", flush=True)
            check = [str(environment_python(existing)), "-I", "-X", "utf8", "-u", "-m", "layerloom.doctor"]
            if run(check, source=source, env=env, log=log) == 0:
                return existing, env
            print("The installation needs repair. Preparing a fresh environment...", flush=True)
        if env.get("UV_OFFLINE"):
            raise SetupError("This installation needs a download. Connect to the internet and reopen LayerLoom.")
        environment = root / f"env-{uuid.uuid4().hex}"
        # uv creates the directory itself. Its final name is never changed.
        env["UV_PROJECT_ENVIRONMENT"] = str(environment)
        print("Installing LayerLoom and its graphics packages. The first launch can take several minutes...", flush=True)
        command = [str(uv), "sync", "--project", str(source), "--locked", "--extra", "gui",
                   "--no-default-groups", "--managed-python", "--python", pins["python"],
                   "--no-config"]
        # Supported desktop platforms have binary wheels for these dependencies.
        # Fail usefully if a release loses a wheel; never ask beginners for a C compiler.
        for package in ("numpy", "scipy", "shapely", "lxml", "manifold3d", "mapbox-earcut",
                        "rtree", "PyQt5", "vtk", "lib3mf", "Pillow"):
            command.extend(["--no-build-package", package])
        if run(command, source=source, env=env, log=log) != 0:
            raise SetupError("Package installation did not finish. Check the error above and setup log, then reopen LayerLoom to retry.")
        (environment / "layerloom-owner.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")
        check = [str(environment_python(environment)), "-I", "-X", "utf8", "-u", "-m", "layerloom.doctor"]
        if run(check, source=source, env=env, log=log) != 0:
            raise SetupError("Installed packages did not pass the startup check. See the setup log for the failing check.")
        publish_ready(root, environment, identity)
        return environment, env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set up and launch LayerLoom using its private Python.")
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--uv", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--repair", action="store_true", help="Create a fresh environment; preserve all existing files.")
    parser.add_argument("--doctor-only", action="store_true", help="Verify setup without opening the GUI.")
    args = parser.parse_args(argv)
    home, source = args.home.expanduser().resolve(), args.source.resolve()
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f"setup-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.log"
    print(f"LayerLoom setup log: {log_path}", flush=True)
    with log_path.open("w", encoding="utf-8") as log:
        try:
            environment, env = prepare(source, home, args.uv.resolve(), repair=args.repair, log=log)
            if args.doctor_only or os.environ.get("LAYERLOOM_LAUNCHER_DOCTOR_ONLY") == "1":
                print("LayerLoom is installed and its startup checks passed.")
                return 0
            print("Opening LayerLoom...", flush=True)
            status = run([str(environment_python(environment)), "-I", "-X", "utf8", "-u", "-m", "layerloom.gui_app"],
                         source=source, env=env, log=log)
            if status:
                raise SetupError("The graphics window could not start. See the setup log for details.")
            return 0
        except (SetupError, OSError, ValueError) as exc:
            message = f"LayerLoom could not start: {exc}\nSetup log: {log_path}"
            print(message, file=sys.stderr)
            log.write(message + "\n")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
