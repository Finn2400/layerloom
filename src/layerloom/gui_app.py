"""Stable public launcher for the current LayerLoom Qt GUI."""

from __future__ import annotations

import importlib.util
import os
import sys


_GUI_EXTRAS_HELP = """LayerLoom GUI dependencies are not available.

For a source download, run the included launcher again with --repair.
For a standalone app, download and replace it with a fresh LayerLoom app.
Your saved models are unaffected. See the installation guide for details.
"""


def _validate_gui_environment() -> None:
    """Reject Qt platforms that cannot host VTK's native macOS window."""
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
    from .app_logging import initialize_logs, install_qt_message_logging, report_startup_failure
    try:
        initialize_logs()
        _validate_gui_environment()
        install_qt_message_logging()
        return int(_load_current_gui_module().main())
    except Exception as exc:
        if isinstance(exc, ModuleNotFoundError):
            print(_GUI_EXTRAS_HELP, file=sys.stderr)
        report_startup_failure(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
