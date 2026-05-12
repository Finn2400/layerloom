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


CORE_IMPORTS = ("numpy", "scipy", "trimesh")
GUI_IMPORTS = ("PyQt5", "pyvista", "pyvistaqt", "vtk")
EXAMPLE_FILES = (
    "tutorial_cmy_cubes.3mf",
    "tutorial_expanded_tiles_v62.3mf",
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


def _module_root() -> Path:
    return Path(__file__).resolve().parent


def _repo_root_guess() -> Path:
    # src/layerloom/doctor.py -> repo root in editable/source checkouts.
    return _module_root().parents[1]


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
    examples_dir = _repo_root_guess() / "examples"
    if not examples_dir.is_dir():
        _fail(
            f"examples directory not found at {examples_dir}. "
            "For the tutorial, run from a full GitHub checkout."
        )
        return 1

    for filename in EXAMPLE_FILES:
        path = examples_dir / filename
        if not path.is_file():
            failures += 1
            _fail(f"missing {path}")
            continue
        if path.stat().st_size <= 0:
            failures += 1
            _fail(f"{path} is empty")
            continue
        _ok(f"{path.relative_to(_repo_root_guess())} present ({path.stat().st_size} bytes)")
    return failures


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
    args = parser.parse_args(argv)

    print("LayerLoom install diagnostic")
    print("============================")
    print(f"Python executable: {sys.executable}")
    print(f"Python version:    {platform.python_version()}")
    print(f"Platform:          {platform.platform()}")
    print(f"LayerLoom version: {_package_version()}")
    print(f"LayerLoom module:  {_module_root()}")

    failures = 0
    failures += _check_imports(CORE_IMPORTS, label="Core imports")
    if args.skip_gui:
        _warn("GUI imports skipped by --skip-gui")
    else:
        failures += _check_imports(GUI_IMPORTS, label="GUI imports")

    if args.skip_examples:
        _warn("Tutorial example checks skipped by --skip-examples")
    else:
        failures += _check_examples()

    print("\nResult")
    if failures:
        _fail(
            f"{failures} check(s) failed. Re-run the install command inside an "
            "activated virtual environment, for example: python -m pip install -e \".[gui]\""
        )
        return 1

    _ok("LayerLoom looks ready. Try: layerloom-gui")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
