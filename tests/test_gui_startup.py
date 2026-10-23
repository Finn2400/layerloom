"""Check invalid viewer launches without importing Qt or opening a window."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from layerloom import gui_app


@pytest.mark.parametrize("platform", ["offscreen", "offscreen:option=1", "minimal", "offscreen;cocoa"])
def test_macos_rejects_invalid_viewer_platform_before_loading_gui(monkeypatch, capsys, platform):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "argv", ["layerloom-gui"])
    monkeypatch.setenv("QT_QPA_PLATFORM", platform)

    def unexpected_load():
        pytest.fail("Invalid platform must be rejected before loading Qt/VTK")

    monkeypatch.setattr(gui_app, "_load_current_gui_module", unexpected_load)
    assert gui_app.main() == 2
    assert "QT_QPA_PLATFORM=cocoa" in capsys.readouterr().err


@pytest.mark.parametrize("args", [["-platform", "offscreen"], ["--platform=minimal"]])
def test_macos_checks_command_line_platform_override(monkeypatch, args):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "argv", ["layerloom-gui", *args])
    monkeypatch.setenv("QT_QPA_PLATFORM", "cocoa")
    with pytest.raises(RuntimeError, match="3D viewer cannot use"):
        gui_app._validate_gui_environment()


@pytest.mark.parametrize("host,platform,args", [
    ("darwin", "", []),
    ("darwin", "cocoa", []),
    ("darwin", "offscreen", ["-platform", "cocoa"]),
    ("linux", "xcb", []),
    ("win32", "windows", []),
])
def test_native_platforms_are_accepted(monkeypatch, host, platform, args):
    monkeypatch.setattr(sys, "platform", host)
    monkeypatch.setattr(sys, "argv", ["layerloom-gui", *args])
    monkeypatch.setenv("QT_QPA_PLATFORM", platform)
    gui_app._validate_gui_environment()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-specific native crash prevention")
def test_offscreen_cli_exits_cleanly_without_native_crash():
    result = subprocess.run(
        [sys.executable, "-m", "layerloom.gui_app"],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 2, result.stderr
    assert "3D viewer cannot use" in result.stderr
