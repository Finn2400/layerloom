"""Regression checks for the desktop package boundary and binary inspection."""
from __future__ import annotations
import importlib.util
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "packaging/macos"))
from bundle_config import EXCLUDES, GUI_FILES, bundle_short_version, gui_hidden_imports, resource_files
from build_macos import is_license_file
from validate_macos import command, deployment_versions, write_test_glb


def test_explicit_desktop_resources_are_present():
    resources = resource_files(ROOT)
    assert len(resources) == 12
    assert (str(ROOT / "src/layerloom/assets/benchy_ascii_with_text.txt"), "layerloom/assets") in resources
    assert {Path(path).name for path, _ in resources if path.endswith(".py")} == set(GUI_FILES)
    assert not any("submission" in path or "manuscript" in path for path, _ in resources)
    imports = gui_hidden_imports(ROOT)
    assert "layerloom.transform_3mf" in imports
    assert "layerloom.paint_main_ui" in imports
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


def test_timed_out_native_command_preserves_output(tmp_path):
    log = tmp_path / "gui.log"
    with pytest.raises(subprocess.TimeoutExpired):
        command([sys.executable, "-u", "-c",
                 "import time; print('GUI stage reached'); time.sleep(30)"],
                cwd=tmp_path, env=os.environ.copy(), log=log, timeout=2)
    assert "GUI stage reached" in log.read_text()


@pytest.mark.parametrize(("version", "expected"), [
    ("0.6.4a1", "0.6.4"), ("0.6.4", "0.6.4"), ("1.2", "1.2.0"),
])
def test_mac_display_version_remains_numeric_for_alpha_releases(version, expected):
    assert bundle_short_version(version) == expected


def test_validation_matrix_requires_explicit_retired_runner_opt_in():
    from macos_validation_matrix import validation_matrix
    normal = validation_matrix()["include"]
    expanded = validation_matrix(include_macos14=True)["include"]
    assert len(normal) == 4 and len(expanded) == 5
    assert all(item["runner"] != "macos-14" for item in normal)
    assert [item for item in expanded if item not in normal] == [
        {"runner": "macos-14", "arch": "arm64"}
    ]
    for arch in ("arm64", "x86_64"):
        assert len([item for item in normal if item["arch"] == arch]) == 2


@pytest.mark.parametrize("enabled", [False, True])
def test_validation_plan_records_missing_minimum_os_evidence(tmp_path, enabled):
    output, summary = tmp_path / "output", tmp_path / "summary"
    env = dict(os.environ, GITHUB_OUTPUT=str(output), GITHUB_STEP_SUMMARY=str(summary),
               LAYERLOOM_VALIDATE_MACOS14=str(enabled).lower())
    subprocess.run([sys.executable, str(ROOT / "scripts/macos_validation_matrix.py")],
                   env=env, check=True)
    matrix = json.loads(output.read_text().split("=", 1)[1])
    assert (any(item["runner"] == "macos-14" for item in matrix["include"])) is enabled
    assert ("NOT performed" in summary.read_text()) is not enabled
    assert ("SHA-256" in summary.read_text()) is not enabled
