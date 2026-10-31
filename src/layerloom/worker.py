"""Console-only entry point for isolated desktop and command-line operations."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import platform
import sys
import traceback
from typing import Sequence

from layerloom.runtime import WORKER_MODULES, local_repair_available


def runtime_diagnostics():
    """Probe the worker itself, including actual native-library initialization."""
    from layerloom.version import __version__
    report = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False)),
              "executable": sys.executable, "python": platform.python_version(),
              "architecture": platform.machine(), "lib3mf": {"ok": False}, "packages": {}}
    for name in ("lib3mf", "numpy", "trimesh", "manifold3d"):
        try:
            report["packages"][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            report["packages"][name] = "metadata unavailable"
    try:
        lib = importlib.import_module("lib3mf")
        model = lib.get_wrapper().CreateModel()
        if model is None:
            raise RuntimeError("lib3mf returned no model")
        report["lib3mf"] = {"ok": True, "module": lib.__file__}
    except Exception as exc:
        report["lib3mf"]["error"] = f"{type(exc).__name__}: {exc}"
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="layerloom-worker")
    parser.add_argument("--capabilities", action="store_true", help="Print optional worker capabilities as JSON")
    parser.add_argument("--diagnostics", action="store_true", help="Report worker runtime and check native lib3mf")
    parser.add_argument("task", nargs="?", choices=tuple(WORKER_MODULES))
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.diagnostics:
        if args.task is not None or args.capabilities:
            parser.error("--diagnostics cannot be combined with other operations")
        report = runtime_diagnostics()
        print(json.dumps(report))
        return 0 if report["lib3mf"]["ok"] else 1
    if args.capabilities:
        if args.task is not None:
            parser.error("--capabilities cannot be combined with a task")
        print(json.dumps({"repair": local_repair_available()}))
        return 0
    if args.task is None:
        parser.error("a worker task is required")
    try:
        module = importlib.import_module(WORKER_MODULES[args.task])
        result = module.main(args.arguments)
        return 0 if result is None else int(result)
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
