"""Viewer decoration must be packaged, never read from a user's Downloads."""
from __future__ import annotations

import ast
import builtins
import os
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
ART = "benchy_ascii_with_text.txt"


def _plate_art_reader(gui_path):
    # Execute the production file reader without constructing a Qt/VTK window.
    tree = ast.parse(gui_path.read_text(encoding="utf-8"))
    viewer = next(node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == "EmbeddedPVViewer")
    method = next(node for node in viewer.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_read_plate_ascii_lines")
    namespace = {"os": os, "__file__": str(gui_path), "List": list, "_log": lambda *args: None}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(gui_path), "exec"), namespace)
    return namespace["_read_plate_ascii_lines"]


def test_plate_art_loads_from_relocated_bundle_without_downloads(tmp_path, monkeypatch):
    # Preserve the same package-relative paths as PyInstaller's Resources and
    # Frameworks layout. Current directory and user home must be irrelevant.
    package = tmp_path / "Relocated tèst 日本語" / "LayerLoom.app/Contents/Frameworks/layerloom"
    (package / "assets").mkdir(parents=True)
    for name in ("3mf_gui_v58.py", f"assets/{ART}"):
        shutil.copyfile(ROOT / "src/layerloom" / name, package / name)
    reader = _plate_art_reader(package / "3mf_gui_v58.py")
    expected = package / "assets" / ART
    monkeypatch.delenv("LAYERLOOM_PLATE_ASCII", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "Unrelated user"))
    monkeypatch.chdir(tmp_path)
    original_open = builtins.open
    opened = []

    def bundle_only_open(path, *args, **kwargs):
        opened.append(Path(path))
        assert Path(path) == expected, "Default artwork must not read user folders"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", bundle_only_open)
    lines = reader(None)
    assert opened == [expected]
    assert lines and {"0", "1"} <= set("".join(lines))
    # Failure must degrade to no decoration, with no developer-folder fallback.
    expected.unlink()
    assert reader(None) == []
    assert opened == [expected, expected]


def test_plate_art_explicit_override_is_preserved(tmp_path, monkeypatch):
    custom = tmp_path / "custom art.txt"
    custom.write_text("  \n  01  \n  10  \n  \n", encoding="utf-8")
    monkeypatch.setenv("LAYERLOOM_PLATE_ASCII", str(custom))
    reader = _plate_art_reader(ROOT / "src/layerloom/3mf_gui_v58.py")
    assert reader(None) == ["01", "10"]


def test_doctor_checks_packaged_artwork():
    from layerloom.doctor import _check_gui_assets
    assert _check_gui_assets() == 0


@pytest.mark.parametrize("content", [None, " \n\t"])
def test_doctor_reports_missing_or_empty_artwork(tmp_path, monkeypatch, capsys, content):
    from layerloom import doctor
    monkeypatch.setattr(doctor, "_module_root", lambda: tmp_path)
    if content is not None:
        (tmp_path / "assets").mkdir()
        (tmp_path / "assets" / ART).write_text(content, encoding="utf-8")
    assert doctor._check_gui_assets() == 1
    assert ART in capsys.readouterr().out
