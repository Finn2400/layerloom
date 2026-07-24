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
