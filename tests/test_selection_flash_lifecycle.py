"""Use real Qt timer ownership with a recording renderer, without a native GPU."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest


def test_selection_callbacks_stop_when_the_window_or_scene_goes_away(tmp_path):
    pytest.importorskip("PyQt5")
    pytest.importorskip("pyvistaqt")
    script = r'''
import sys
import traceback
from types import SimpleNamespace
from PyQt5 import QtCore, QtWidgets
from layerloom.gui_app import _load_current_gui_module

module = _load_current_gui_module()
app = QtWidgets.QApplication([])
app.setQuitOnLastWindowClosed(False)
errors = []
def record_error(typ, value, tb):
    errors.append(str(value))
    traceback.print_exception(typ, value, tb)
sys.excepthook = record_error

class Viewer:
    def __init__(self):
        self.closed = False
        self.calls = []
        self.actor = SimpleNamespace(prop=SimpleNamespace(show_edges=False, edge_color="original"))
        self.actors_by_name = {"part": self.actor}
        self.plotter = self
    def set_color_by_name(self, name, color):
        self.calls.append(("color", name, color, self.closed))
    def render(self):
        self.calls.append(("render", self.closed))
    def close(self):
        self.closed = True

def settle():
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(350, loop.quit)
    loop.exec_()
    assert not errors, errors

for paint, base in [(False, module._V58.QtAssignColorsApp), (True, module.QtAssignColorsApp)]:
    class Window(base):
        def __init__(self):
            # Retain production callbacks, scene reload, closeEvent, and Qt
            # ownership. Only the expensive native renderer is substituted.
            QtWidgets.QMainWindow.__init__(self)
            self._flash_timers = {}
            self._paint_flash_states = {}
            self.paint_panel = None
            self.viewer = Viewer()
            self.assignments = {"1": {"hex": "#00ffff"}}
            self.oid_to_name = {"1": "part"}
            self.base_file = "model.3mf"
            self.temp_ply_dir = ""
        def _shared_paint_active(self): return paint
        def _current_selected_oid(self): return "1"
        def _set_transform_values(self, *args): pass
        def _reload_current(self): pass

    closed = Window()
    viewer = closed.viewer
    closed._flash_actor_by_name("part", "1")
    closed.close()
    settle()
    assert len(viewer.calls) == 1, viewer.calls
    assert not closed._flash_timers

    deleted = Window()
    viewer = deleted.viewer
    deleted._flash_actor_by_name("part", "1")
    deleted.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    settle()
    assert len(viewer.calls) == 1, viewer.calls

    # Reloading the SAME filename still replaces the scene; path comparison
    # alone must not let a pending callback modify replacement actors.
    reloaded = Window()
    reloaded._flash_actor_by_name("part", "1")
    reloaded._load_canonical_model("model.3mf")
    settle()
    assert len(reloaded.viewer.calls) == 1, reloaded.viewer.calls
    reloaded.close()

    active = Window()
    active._flash_actor_by_name("part", "1")
    stale_timer = active._flash_timers["part"]
    active._flash_actor_by_name("part", "1")
    current_timer = active._flash_timers["part"]
    stale_timer.timeout.emit()
    assert active._flash_timers["part"] is current_timer
    assert len(active.viewer.calls) == 2
    active.assignments["1"]["hex"] = "#ff00ff"
    settle()
    assert len(active.viewer.calls) == 3, active.viewer.calls
    assert not active._flash_timers
    if paint:
        assert not active.viewer.actor.prop.show_edges
        assert active.viewer.actor.prop.edge_color == "original"
        assert not active._paint_flash_states
    else:
        assert active.viewer.calls[-1] == ("color", "part", "#ff00ff", False)
    active.close()
print("Legacy and shared Paint callbacks passed close, deletion, scene reload and repeated selection.")
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "MPLCONFIGDIR": str(tmp_path / "mpl")},
        capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("platform", ["offscreen", "minimal", "offscreen:option=1", "offscreen;cocoa"])
def test_unsupported_macos_viewer_platform_reports_before_loading_qt(monkeypatch, platform):
    from layerloom import app_logging, gui_app
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "argv", ["layerloom-gui"])
    monkeypatch.setenv("QT_QPA_PLATFORM", platform)
    errors = []
    monkeypatch.setattr(app_logging, "initialize_logs", lambda: None)
    monkeypatch.setattr(app_logging, "report_startup_failure", errors.append)
    monkeypatch.setattr(app_logging, "install_qt_message_logging", lambda: pytest.fail("Qt was loaded"))
    monkeypatch.setattr(gui_app, "_load_current_gui_module", lambda: pytest.fail("VTK was loaded"))
    assert gui_app.main() == 1
    assert len(errors) == 1 and "QT_QPA_PLATFORM=cocoa" in str(errors[0])


@pytest.mark.parametrize("host,platform,args", [
    ("darwin", "", []), ("darwin", "cocoa", []),
    ("darwin", "offscreen", ["-platform", "cocoa"]),
    ("linux", "offscreen", []), ("win32", "windows", []),
])
def test_native_or_non_macos_platform_is_accepted(monkeypatch, host, platform, args):
    from layerloom import gui_app
    monkeypatch.setattr(sys, "platform", host)
    monkeypatch.setattr(sys, "argv", ["layerloom-gui", *args])
    monkeypatch.setenv("QT_QPA_PLATFORM", platform)
    gui_app._validate_gui_environment()


@pytest.mark.parametrize("args", [["-platform", "offscreen"], ["--platform=minimal"]])
def test_macos_platform_argument_is_checked(monkeypatch, args):
    from layerloom import gui_app
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "argv", ["layerloom-gui", *args])
    monkeypatch.setenv("QT_QPA_PLATFORM", "cocoa")
    with pytest.raises(RuntimeError, match="3D viewer cannot use"):
        gui_app._validate_gui_environment()
