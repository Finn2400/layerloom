"""Regression checks for the desktop package boundary and binary inspection."""
from __future__ import annotations
import importlib.util
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "packaging/macos"))
from bundle_config import EXCLUDES, GUI_FILES, gui_hidden_imports, resource_files
from build_macos import is_license_file
from validate_macos import deployment_versions, write_test_glb


def test_explicit_desktop_resources_are_present():
    resources = resource_files(ROOT)
    assert len(resources) == 11
    assert {Path(path).name for path, _ in resources if path.endswith(".py")} == set(GUI_FILES)
    assert not any("submission" in path or "manuscript" in path for path, _ in resources)
    imports = gui_hidden_imports(ROOT)
    assert "layerloom.transform_3mf" in imports
    assert "PyQt5.QtWidgets" in imports
    assert "tkinter" not in imports and "pymeshlab" in EXCLUDES


def test_deployment_versions_ignores_dylib_current_version():
    output = '''Load command 1
      cmd LC_BUILD_VERSION
 platform 1
    minos 14.0
      sdk 15.2
Load command 2
      cmd LC_ID_DYLIB
  current version 99.0.0
Load command 3
      cmd LC_VERSION_MIN_MACOSX
  version 13.0
      sdk 14.0'''
    assert deployment_versions(output) == [(14, 0), (13, 0)]


def test_smoke_glb_has_matching_buffers(tmp_path):
    path = tmp_path / "fixture.glb"
    write_test_glb(path)
    data = path.read_bytes()
    magic, version, size = struct.unpack_from("<4sII", data)
    assert (magic, version, size) == (b"glTF", 2, len(data))
    json_size, kind = struct.unpack_from("<I4s", data, 12)
    assert kind == b"JSON"
    document = json.loads(data[20:20 + json_size])
    binary_size, kind = struct.unpack_from("<I4s", data, 20 + json_size)
    assert kind == b"BIN\x00"
    assert document["buffers"][0]["byteLength"] == binary_size


def test_license_inventory_does_not_ship_native_test_binaries():
    assert is_license_file(Path("LICENSE.txt"))
    assert is_license_file(Path("COPYING.LESSER"))
    assert not is_license_file(Path("copying.cpython-312-darwin.so"))
    assert not is_license_file(Path("test_copying.py"))
