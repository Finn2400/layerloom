"""Stable public launcher for the current LayerLoom Qt GUI."""

from __future__ import annotations

import importlib.util
import os
import sys


_GUI_EXTRAS_HELP = """LayerLoom GUI dependencies are not available.

Install the GUI extras, then try again:

    python -m pip install "layerloom[gui]"

You can also run `layerloom-doctor` to inspect the current environment.
"""


def _validate_gui_environment() -> None:
    """Reject macOS test platforms that cannot host VTK's Cocoa window.

    Check before importing the viewer: native window attachment can segfault,
    so a Python exception handler around renderer construction is too late.
    Qt-only tests can still use these platforms without launching the viewer.
    """
    if sys.platform != "darwin":
        return
    platform = os.environ.get("QT_QPA_PLATFORM", "")
    for index, arg in enumerate(sys.argv[1:], 1):
        if arg in {"-platform", "--platform"} and index + 1 < len(sys.argv):
            platform = sys.argv[index + 1]
        elif arg.startswith(("-platform=", "--platform=")):
            platform = arg.split("=", 1)[1]
    platform = platform.split(";", 1)[0].split(":", 1)[0].strip().lower()
    if platform in {"offscreen", "minimal"}:
        raise RuntimeError(
            f"LayerLoom's macOS 3D viewer cannot use Qt's {platform!r} platform. "
            "Unset QT_QPA_PLATFORM (and remove any -platform override), or use "
            "QT_QPA_PLATFORM=cocoa from an unlocked desktop session."
        )


def _load_current_gui_module():
    module_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "3mf_gui_v65.py")
    spec = importlib.util.spec_from_file_location("layerloom_gui_v65_entrypoint", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load LayerLoom GUI module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    """Launch the current LayerLoom GUI."""
    try:
        _validate_gui_environment()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        return int(_load_current_gui_module().main())
    except ModuleNotFoundError as exc:
        if exc.name in {"pyvista", "pyvistaqt", "qtpy", "PyQt5", "vtk"}:
            print(_GUI_EXTRAS_HELP, file=sys.stderr)
            return 2
        raise
    except RuntimeError as exc:
        if "Qt dependencies are unavailable" in str(exc):
            print(_GUI_EXTRAS_HELP, file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main())
