"""Locate LayerLoom's isolated worker in Python installs and desktop bundles."""

from __future__ import annotations

import importlib.util
import json
import logging
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Sequence


WORKER_MODULES = {
    "weave": "layerloom.weave",
    "bake": "layerloom.bake_in_memory_stlroundtrip",
    "glb-split": "supporting_scripts.glb_split_by_vertexcolor_v3_3mf",
    "repair": "supporting_scripts.3mf_meshlab_repair_3mf",
}
_LOG = logging.getLogger("layerloom.worker")


def _worker_prefix() -> list[str]:
    if not getattr(sys, "frozen", False):
        # Interpreter flags do not propagate from the GUI to subprocesses.
        # Windows pipes otherwise use the system code page and can fail on
        # perfectly valid model paths containing non-ASCII characters.
        return [sys.executable, "-X", "utf8", "-u", "-m", "layerloom.worker"]

    executable = Path(sys.executable).resolve()
    if executable.name.lower() in {"layerloom-worker", "layerloom-worker.exe"}:
        # A worker launching its final bake must use its own collected runtime.
        return [str(executable)]
    if sys.platform == "darwin":
        worker = executable.parent.parent / "Helpers" / "LayerLoomWorker.app" / "Contents" / "MacOS" / "layerloom-worker"
    else:
        name = "layerloom-worker.exe" if sys.platform == "win32" else "layerloom-worker"
        worker = executable.parent / "worker" / name
    if not worker.is_file():
        raise FileNotFoundError(
            f"LayerLoom's bundled worker is missing: {worker}. "
            "Reinstall the complete LayerLoom application."
        )
    return [str(worker)]


def worker_command(task: str, arguments: Sequence[str] = ()) -> list[str]:
    """Build an argument list for one known task; never invoke a shell."""
    if task not in WORKER_MODULES:
        raise ValueError(f"Unknown LayerLoom worker task: {task}")
    return [*_worker_prefix(), task, *arguments]


def worker_process_options() -> dict:
    """Decode worker diagnostics consistently and hide Windows consoles."""
    options = {"encoding": "utf-8", "errors": "replace"}
    if sys.platform == "win32":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return options


def log_worker_result(label: str, result: subprocess.CompletedProcess) -> None:
    """Preserve child diagnostics even when the GUI hides its debug output."""
    _LOG.info("%s exited with status %s", label, result.returncode)
    if result.stdout:
        _LOG.info("%s stdout:\n%s", label, result.stdout.rstrip())
    if result.stderr:
        _LOG.info("%s stderr:\n%s", label, result.stderr.rstrip())


def local_repair_available() -> bool:
    """Probe installed modules without importing the optional native library."""
    try:
        return importlib.util.find_spec("pymeshlab") is not None
    except (ImportError, ValueError):
        return False


@lru_cache(maxsize=1)
def repair_available() -> bool:
    """Ask the worker runtime, which can differ from the desktop GUI runtime."""
    if not getattr(sys, "frozen", False):
        return local_repair_available()
    try:
        result = subprocess.run(
            [*_worker_prefix(), "--capabilities"],
            capture_output=True,
            text=True,
            timeout=20,
            **worker_process_options(),
        )
        log_worker_result("worker capabilities", result)
        if result.returncode != 0:
            return False
        return json.loads(result.stdout).get("repair") is True
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        _LOG.exception("Could not inspect optional worker capabilities")
        return False
