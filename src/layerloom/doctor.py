"""Install diagnostic for LayerLoom.

This module is intentionally boring and dependency-light. It should be the
first command a new user runs when the GUI does not launch.
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 10):  # Keep source-checkout failures readable.
    print(
        "LayerLoom requires Python 3.10 or newer. "
        f"This interpreter is Python {sys.version_info.major}.{sys.version_info.minor}: {sys.executable}"
    )
    raise SystemExit(1)

import argparse
import importlib
import importlib.metadata
import platform
from pathlib import Path

try:
    from layerloom.example_assets import EXAMPLE_FILES, example_search_dirs, repo_root_guess
except Exception:  # pragma: no cover - supports direct script execution.
    from example_assets import EXAMPLE_FILES, example_search_dirs, repo_root_guess


CORE_IMPORTS = (
    "lxml",
    "manifold3d",
    "mapbox_earcut",
    "networkx",
    "numpy",
    "PIL",
    "rtree",
    "shapely",
    "scipy",
    "trimesh",
)
GUI_IMPORTS = (
    "lib3mf",
    "pygltflib",
    "PyQt5",
    "pyvista",
    "pyvistaqt",
    "vtk",
)


def _ok(message: str) -> None:
    print(f"[OK]   {message}")


def _warn(message: str) -> None:
    print(f"[WARN] {message}")


def _fail(message: str) -> None:
    print(f"[FAIL] {message}")


def _package_version() -> str:
    try:
        return importlib.metadata.version("layerloom")
    except importlib.metadata.PackageNotFoundError:
        return "not installed as package"


def _source_version() -> str:
    try:
        from layerloom.version import __version__
    except Exception:  # pragma: no cover - supports direct script execution.
        from version import __version__
    return str(__version__)


def _check_package_version() -> int:
    installed = _package_version()
    source = _source_version()
    if getattr(sys, "frozen", False) and installed == "not installed as package":
        _warn(f"standalone app version {source}; distribution metadata unavailable")
        return 0
    if installed == source:
        _ok(f"installed package metadata matches source ({source})")
        return 0
    _fail(
        f"installed package metadata is {installed}, but this source tree is {source}; "
        "repair the source installation with its launcher, or replace the standalone app"
    )
    return 1


def _module_root() -> Path:
    return Path(__file__).resolve().parent


def _repo_root_guess() -> Path:
    # src/layerloom/doctor.py -> repo root in editable/source checkouts.
    return repo_root_guess()


def _check_imports(names: tuple[str, ...], *, label: str) -> int:
    failures = 0
    print(f"\n{label}")
    for name in names:
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # pragma: no cover - exact import failures vary by platform.
            failures += 1
            _fail(f"{name}: {type(exc).__name__}: {exc}")
            continue

        version = getattr(module, "__version__", None)
        if version is None:
            try:
                version = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                version = "version unknown"
        _ok(f"{name} importable ({version})")
    return failures


def _check_examples() -> int:
    failures = 0
    print("\nTutorial examples")
    for filename in EXAMPLE_FILES:
        path = None
        for examples_dir in example_search_dirs():
            candidate = examples_dir / filename
            if candidate.is_file():
                path = candidate
                break
        if path is None:
            failures += 1
            searched = ", ".join(str(p) for p in example_search_dirs())
            _fail(f"missing {filename}; searched {searched}")
            continue
        if not path.is_file():
            failures += 1
            _fail(f"missing {path}")
            continue
        if path.stat().st_size <= 0:
            failures += 1
            _fail(f"{path} is empty")
            continue
        try:
            display = path.relative_to(_repo_root_guess())
        except ValueError:
            display = path
        _ok(f"{display} present ({path.stat().st_size} bytes)")
    return failures


def _check_palettes() -> int:
    import json
    failures = 0
    print("\nPackaged palettes")
    for name in ("simple_palette.json", "normal_palette.json", "full_palette.json", "calibrated_cmy_normal_core065_palette.json"):
        path = _module_root() / "palettes" / name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not data:
                raise ValueError("palette is empty")
        except (OSError, ValueError) as exc:
            _fail(f"{name}: {exc}")
            failures += 1
        else:
            _ok(name)
    return failures


def _check_gui_assets() -> int:
    print("\nPackaged viewer artwork")
    path = _module_root() / "assets" / "benchy_ascii_with_text.txt"
    try:
        if not path.read_text(encoding="utf-8").strip():
            raise ValueError("artwork is empty")
    except (OSError, ValueError) as exc:
        _fail(f"{path.name}: {exc}")
        return 1
    _ok(path.name)
    return 0


def _check_native() -> int:
    print("\nNative 3MF library")
    try:
        import lib3mf
        model = lib3mf.get_wrapper().CreateModel()
        if model is None:
            raise RuntimeError("lib3mf returned no model")
    except Exception as exc:
        _fail(f"lib3mf initialization: {type(exc).__name__}: {exc}")
        return 1
    _ok("lib3mf native wrapper created a model")
    return 0


def _check_render() -> int:
    print("\nQt / VTK render")
    try:
        from PyQt5 import QtWidgets
        import pyvista as pv
        from pyvistaqt import QtInteractor
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        widget = QtWidgets.QWidget()
        plotter = QtInteractor(widget, off_screen=True)
        try:
            plotter.add_mesh(pv.Cube(), color="cyan")
            plotter.reset_camera()
            app.processEvents()
            pixels = plotter.screenshot(return_img=True)
            if pixels is None or pixels.size == 0 or pixels.max() == pixels.min():
                raise RuntimeError("renderer produced no usable pixels")
        finally:
            plotter.close()
            widget.close()
    except Exception as exc:
        _fail(f"Qt / VTK render: {type(exc).__name__}: {exc}")
        return 1
    _ok("Qt / VTK rendered geometry")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="layerloom-doctor",
        description="Check whether the LayerLoom install can run the GUI/tutorial path.",
    )
    parser.add_argument(
        "--skip-gui",
        action="store_true",
        help="Only check core CLI dependencies, not PyQt/PyVista/VTK GUI imports.",
    )
    parser.add_argument(
        "--skip-examples",
        action="store_true",
        help="Do not require tutorial example files to be present.",
    )
    parser.add_argument("--render", action="store_true", help="Also create a Qt/VTK renderer (requires a working desktop/graphics driver).")
    args = parser.parse_args(argv)

    print("LayerLoom install diagnostic")
    print("============================")
    print(f"Python executable: {sys.executable}")
    print(f"Python version:    {platform.python_version()}")
    print(f"Platform:          {platform.platform()}")
    print(f"LayerLoom version: {_package_version()}")
    print(f"LayerLoom module:  {_module_root()}")
    print(f"Installation:     {'standalone app' if getattr(sys, 'frozen', False) else 'Python/source'}")

    failures = _check_package_version()
    failures += _check_imports(CORE_IMPORTS, label="Core imports")
    if args.skip_gui:
        _warn("GUI imports skipped by --skip-gui")
    else:
        failures += _check_imports(GUI_IMPORTS, label="GUI imports")
        failures += _check_native()
        failures += _check_palettes()
        failures += _check_gui_assets()
        if args.render:
            failures += _check_render()

    if args.skip_examples:
        _warn("Tutorial example checks skipped by --skip-examples")
    else:
        failures += _check_examples()

    print("\nResult")
    if failures:
        _fail(
            f"{failures} check(s) failed. For a source install, rerun its launcher with --repair. "
            "For a standalone app, replace it with a fresh download. Your saved models are unaffected."
        )
        return 1

    _ok("Dependency and resource checks passed. Open LayerLoom and try the example.")
    if not args.render and not args.skip_gui:
        _warn("Interactive rendering was not tested; use --render to check the graphics stack.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
