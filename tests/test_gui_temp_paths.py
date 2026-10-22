"""Window scratch ownership without requiring a graphics display."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import zipfile

import pytest


pytestmark = pytest.mark.gui


@pytest.fixture
def windows(tmp_path, monkeypatch):
    pytest.importorskip("PyQt5.QtWidgets")
    pytest.importorskip("pyvistaqt")
    from layerloom.gui_app import _load_current_gui_module

    base = _load_current_gui_module()._V58.QtAssignColorsApp
    # Exercise the production ownership methods without creating a renderer.
    class WindowScratch:
        _init_temp_workspace = base._init_temp_workspace
        _cleanup_temp_workspace = base._cleanup_temp_workspace
        _clear_ply_cache = base._clear_ply_cache
        _preview_temp_path = base._preview_temp_path
        _stamp_ids_only = base._stamp_ids_only
        base_file = None
        current_file = None

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    created = []

    def make_window():
        window = WindowScratch()
        window._init_temp_workspace()
        created.append(window)
        return window

    yield make_window
    for window in created:
        window._cleanup_temp_workspace()


def test_window_cache_clear_and_cleanup_leave_other_windows_untouched(windows, tmp_path):
    legacy_cache = tmp_path / "layerloom_ply_cache"
    legacy_cache.mkdir()
    legacy_part = legacy_cache / "existing-window.ply"
    legacy_part.write_bytes(b"legacy preview")
    first = windows()
    first_part = Path(first.temp_ply_dir) / "part.ply"
    first_part.write_bytes(b"first preview")
    second = windows()
    second_part = Path(second.temp_ply_dir) / "part.ply"
    second_part.write_bytes(b"second preview")
    assert first_part.read_bytes() == b"first preview"
    assert Path(first.temp_ply_dir) != Path(second.temp_ply_dir)

    second._clear_ply_cache()
    assert not second_part.exists()
    assert first_part.read_bytes() == b"first preview"
    second_part.write_bytes(b"second preview")
    first._cleanup_temp_workspace()
    assert not Path(first._temp_dir).exists()
    assert second_part.read_bytes() == b"second preview"
    assert legacy_part.read_bytes() == b"legacy preview"


def test_same_source_has_separate_preview_and_stamp_files_per_window(windows):
    from layerloom.normalize_3mf_import import normalize_3mf_import

    source = Path(__file__).resolve().parents[1] / "examples" / "tutorial_cmy_cubes.3mf"
    source_digest = hashlib.sha256(source.read_bytes()).digest()
    first, second = windows(), windows()
    normalized = [Path(normalize_3mf_import(str(source), cache_dir=window._temp_dir).normalized_path)
                  for window in (first, second)]
    assert normalized[0] != normalized[1]
    assert normalized[0].parent == Path(first._temp_dir)
    assert normalized[1].parent == Path(second._temp_dir)
    stamped = [Path(window._stamp_ids_only(str(source))) for window in (first, second)]
    assert stamped[0] != stamped[1]
    assert stamped[0].name == stamped[1].name == "preview_id_tutorial_cmy_cubes.3mf"
    with zipfile.ZipFile(stamped[0]) as left, zipfile.ZipFile(stamped[1]) as right:
        assert left.namelist() == right.namelist()
        assert {name: left.read(name) for name in left.namelist()} == {
            name: right.read(name) for name in right.namelist()
        }
    for window in (first, second):
        window.base_file = str(source)
    previews = [Path(window._preview_temp_path("transform_preview")) for window in (first, second)]
    assert previews[0] != previews[1]
    assert previews[0].name == previews[1].name
    for index, preview in enumerate(previews):
        preview.write_bytes(f"transform {index}".encode("ascii"))
    assert previews[0].read_bytes() == b"transform 0"
    first._cleanup_temp_workspace()
    assert not stamped[0].exists()
    assert not previews[0].exists()
    assert stamped[1].is_file()
    assert not normalized[0].exists()
    assert normalized[1].is_file()
    assert previews[1].read_bytes() == b"transform 1"
    assert hashlib.sha256(source.read_bytes()).digest() == source_digest
