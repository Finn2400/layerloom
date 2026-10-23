"""Qt callback ownership checks without constructing a native VTK renderer."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


@pytest.mark.gui
def test_selection_flash_does_not_outlive_its_window_or_scene():
    pytest.importorskip("qtpy")
    pytest.importorskip("pyvistaqt")
    script = r'''
import sys
import traceback
from qtpy import QtCore, QtWidgets
from layerloom.gui_app import _load_current_gui_module

base = _load_current_gui_module()._V58.QtAssignColorsApp
app = QtWidgets.QApplication([])
app.setQuitOnLastWindowClosed(False)
slot_errors = []
def record_exception(typ, value, tb):
    slot_errors.append(str(value))
    traceback.print_exception(typ, value, tb)
sys.excepthook = record_exception

class Viewer:
    def __init__(self):
        self.colors = []
        self.closed = False

    def set_color_by_name(self, name, color):
        self.colors.append((name, color, self.closed))

    def close(self):
        self.closed = True

class Window(base):
    def __init__(self):
        # Keep the real Qt ownership and production callback/close methods,
        # while replacing only the renderer and model-loading work.
        QtWidgets.QMainWindow.__init__(self)
        self._flash_timers = {}
        self.viewer = Viewer()
        self.base_file = "model.3mf"
        self.assignments = {"1": {"hex": "#00ffff"}}
        self.oid_to_name = {"1": "part"}

    def _cleanup_temp_workspace(self):
        pass

    def _set_transform_values(self, *args):
        pass

    def _reload_current(self):
        pass

def settle():
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(350, loop.quit)
    loop.exec_()
    assert not slot_errors, slot_errors

# A different window or a modal dialog can keep the application event loop
# alive after this window closes. Its pending flash must not touch the renderer.
closed = Window()
closed._flash_actor_by_name("part", "1")
closed.close()
settle()
assert closed.viewer.colors == [("part", "#FF0000", False)], closed.viewer.colors

# Qt deletion can also happen without a closeEvent (e.g. parent destruction).
deleted = Window()
viewer = deleted.viewer
deleted._flash_actor_by_name("part", "1")
deleted.deleteLater()
QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
settle()
assert viewer.colors == [("part", "#FF0000", False)], viewer.colors

# Reopening even the SAME source file creates a new scene; the old flash must
# not tint its replacement actor. Source-path comparison alone is insufficient.
reloaded = Window()
reloaded._flash_actor_by_name("part", "1")
reloaded._load_canonical_model("model.3mf")
settle()
assert reloaded.viewer.colors == [("part", "#FF0000", False)], reloaded.viewer.colors
reloaded.close()

# Repeated selection needs only the most recent restore, using the latest
# assignment even if a color was changed during the flash.
active = Window()
active._flash_actor_by_name("part", "1")
active._flash_actor_by_name("part", "1")
active.assignments["1"]["hex"] = "#ff00ff"
settle()
assert active.viewer.colors == [
    ("part", "#FF0000", False), ("part", "#FF0000", False),
    ("part", "#ff00ff", False),
], active.viewer.colors
assert not active._flash_timers, active._flash_timers
active.close()
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8"),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
