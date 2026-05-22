"""Packaged tutorial examples used by the GUI and install diagnostic."""

from __future__ import annotations

from pathlib import Path


EXAMPLE_FILES = (
    "tutorial_cmy_cubes.3mf",
    "tutorial_cmy_benchy_cutup.3mf",
)

DEFAULT_GUI_EXAMPLE = "tutorial_cmy_benchy_cutup.3mf"


def module_root() -> Path:
    return Path(__file__).resolve().parent


def repo_root_guess() -> Path:
    # src/layerloom/example_assets.py -> repo root in editable/source checkouts.
    return module_root().parents[1]


def example_search_dirs() -> tuple[Path, ...]:
    """Return likely locations for tutorial examples in source and wheel installs."""
    return (
        repo_root_guess() / "examples",
        module_root() / "examples",
    )


def find_example_file(filename: str = DEFAULT_GUI_EXAMPLE) -> Path | None:
    for directory in example_search_dirs():
        path = directory / filename
        if path.is_file():
            return path
    return None
