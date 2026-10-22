"""Qt event-loop and child-encoding checks; no 3D display is required."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


@pytest.mark.gui
def test_gui_process_repaints_and_handles_unicode_errors_and_failed_start(tmp_path):
    pytest.importorskip("qtpy")
    # Isolate QApplication from other GUI imports and run on Qt's offscreen
    # platform. Unlike the full workflow, this test does not create a VTK view.
    script = r'''
import os
import sys
import unicodedata
from qtpy import QtCore, QtWidgets
from layerloom.gui_process import run_gui_process

app = QtWidgets.QApplication([])
ticks = []
timer = QtCore.QTimer()
timer.timeout.connect(lambda: ticks.append(1))
timer.start(10)
os.environ["PYTHONIOENCODING"] = "cp1252"
os.environ["PYTHONUTF8"] = "0"
child = "import subprocess,sys,time; time.sleep(.15); subprocess.run([sys.executable,'-c',\"print('layers → café')\"],check=True); print('failure →',file=sys.stderr); sys.exit(7)"
result = run_gui_process(None, [sys.executable, "-c", child], label="Test")
assert len(ticks) >= 3, ticks
assert result.returncode == 7, result
assert "layers → café" in unicodedata.normalize("NFC", result.stdout), result
assert "failure →" in result.stderr, result
assert not any(w.isVisible() for w in app.topLevelWidgets())
try:
    run_gui_process(None, [sys.argv[1]], label="Missing executable")
except OSError as exc:
    assert "Could not start" in str(exc)
else:
    raise AssertionError("Missing executable was reported as successful")
assert not any(w.isVisible() for w in app.topLevelWidgets())

# A global application quit unwinds nested loops even while a dialog is modal.
# The helper must still reap its writer and report its real final status.
from pathlib import Path
marker = Path(sys.argv[2])
quit_results = []
def quit_during_job():
    QtCore.QTimer.singleShot(50, app.quit)
    writer = "import pathlib,sys,time; time.sleep(.20); pathlib.Path(sys.argv[1]).write_text('finished',encoding='utf-8'); print('finished before exit'); sys.exit(7)"
    quit_results.append(run_gui_process(
        None, [sys.executable, "-c", writer, str(marker)], label="Quit during export"
    ))
QtCore.QTimer.singleShot(0, quit_during_job)
app.exec_()
assert len(quit_results) == 1, quit_results
assert quit_results[0].returncode == 7, quit_results
assert "finished before exit" in quit_results[0].stdout, quit_results
assert marker.read_text(encoding="utf-8") == "finished"
assert not any(w.isVisible() for w in app.topLevelWidgets())
'''
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "missing-executable"), str(tmp_path / "finished.txt")],
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Destroyed while process" not in result.stderr


@pytest.mark.gui
def test_gui_process_protects_owner_workspace_until_writer_finishes(tmp_path):
    pytest.importorskip("qtpy")
    script = r'''
import shutil
import sys
from pathlib import Path
from qtpy import QtCore, QtWidgets
from layerloom.gui_process import run_gui_process

app = QtWidgets.QApplication([])
app.setQuitOnLastWindowClosed(False)
class Owner(QtWidgets.QWidget):
    def __init__(self, workspace):
        super().__init__()
        self.workspace = workspace
        self.close_count = 0
        workspace.mkdir()
        (workspace / "input.txt").write_text("model", encoding="utf-8")

    def closeEvent(self, event):
        self.close_count += 1
        shutil.rmtree(self.workspace)
        super().closeEvent(event)

for index in range(2):
    owner = Owner(Path(sys.argv[1]) / str(index))
    owner.show()
    close_request = owner.close if index == 0 else app.closeAllWindows
    QtCore.QTimer.singleShot(50, close_request)
    writer = "import pathlib,sys,time; time.sleep(.20); p=pathlib.Path(sys.argv[1]); assert (p/'input.txt').is_file(); (p/'output.txt').write_text('finished',encoding='utf-8')"
    result = run_gui_process(
        owner, [sys.executable, "-c", writer, str(owner.workspace)],
        label="Close during export", temp_dir=str(owner.workspace),
    )
    assert result.returncode == 0, result
    assert owner.close_count == 0, owner.close_count
    assert (owner.workspace / "output.txt").read_text(encoding="utf-8") == "finished"
    assert owner.isVisible()
    owner.close()
    assert owner.close_count == 1
    assert not owner.workspace.exists()

# Failed starts must also release the close guard.
owner = Owner(Path(sys.argv[1]) / "failed-start")
owner.show()
try:
    run_gui_process(owner, [str(owner.workspace / "missing-executable")], label="Missing")
except OSError:
    pass
else:
    raise AssertionError("Missing executable was reported as successful")
owner.close()
assert owner.close_count == 1
assert not owner.workspace.exists()
'''
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Destroyed while process" not in result.stderr
