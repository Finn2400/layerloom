from __future__ import annotations

import importlib.metadata


def test_package_metadata_available():
    assert importlib.metadata.version("layerloom")


def test_core_modules_import():
    import layerloom.cli
    import layerloom.normalize_3mf_import
    import layerloom.weave

    assert layerloom.cli.normalize_main
    assert layerloom.normalize_3mf_import.normalize_3mf_import
    assert layerloom.weave.main
