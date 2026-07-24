from __future__ import annotations

import importlib.metadata
import importlib.util
from pathlib import Path


def test_package_metadata_available():
    assert importlib.metadata.version("layerloom")


def test_core_modules_import():
    import layerloom.cli
    import layerloom.doctor
    import layerloom.normalize_3mf_import
    import layerloom.weave

    assert layerloom.cli.normalize_main
    assert layerloom.doctor.main
    assert layerloom.normalize_3mf_import.normalize_3mf_import
    assert layerloom.weave.main


def test_current_gui_entrypoint_imports_v65():
    import layerloom.gui_app

    gui_path = Path(layerloom.gui_app.__file__).resolve().with_name("3mf_gui_v65.py")
    spec = importlib.util.spec_from_file_location("layerloom_gui_v65_smoke", gui_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert layerloom.gui_app._load_current_gui_module().__name__ == "layerloom_gui_v65_entrypoint"
    assert module.QtAssignColorsApp


def test_tutorial_cubes_slice_into_bands():
    from pathlib import Path

    from layerloom.ingest import read_parts
    from layerloom.normalize_3mf_import import normalize_3mf_import
    from layerloom.strata import slice_repeating

    example = Path(__file__).resolve().parents[1] / "examples" / "tutorial_cmy_cubes.3mf"
    normalized = normalize_3mf_import(str(example)).normalized_path
    parts = read_parts([normalized])

    assert parts
    z0 = min(mesh.bounds[0, 2] for _, mesh in parts)
    total_bands = sum(
        len(group)
        for _, mesh in parts
        for group in slice_repeating(mesh, z0=z0, step=0.20, groups=3)
    )
    assert total_bands > 0
