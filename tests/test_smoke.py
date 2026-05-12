from __future__ import annotations

import importlib.metadata


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
