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
    QtCore.QTimer.singleShot(50, owner.close)
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


@pytest.mark.gui
@pytest.mark.parametrize("cancel_method", ["button", "escape", "close", "close-all"])
def test_gui_process_cancellation_reaps_nested_exporter(tmp_path, cancel_method):
    pytest.importorskip("qtpy")
    script = r'''
import sys
import time
from pathlib import Path
from qtpy import QtCore, QtWidgets, QtTest
from layerloom.gui_process import GuiProcessCancelled, run_gui_process

app = QtWidgets.QApplication([])
app.setQuitOnLastWindowClosed(False)
class Owner(QtWidgets.QWidget):
    close_count = 0
    def closeEvent(self, event):
        self.close_count += 1
        super().closeEvent(event)
owner = Owner()
owner.show()
folder = Path(sys.argv[1])
ready = folder / "nested-ready.txt"
late = folder / "must-not-be-written.txt"
heartbeat = folder / "heartbeat.txt"
nested = "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); (p/'nested-ready.txt').write_text('ready'); deadline=time.monotonic()+15" + "\nwhile time.monotonic()<deadline and not (p/'release.txt').exists():\n (p/'heartbeat.txt').write_text(str(time.monotonic())); time.sleep(.05)\n(p/'must-not-be-written.txt').write_text('export escaped cancellation')"
writer = "import subprocess,sys; print('-- Slicing and layering --',flush=True); subprocess.run([sys.executable,'-c'," + repr(nested) + ",sys.argv[1]],check=True)"
actions = []
failures = []
timer = QtCore.QTimer()
def cancel_when_ready():
    if not ready.exists():
        return
    timer.stop()
    try:
        dialogs = [w for w in app.topLevelWidgets() if isinstance(w, QtWidgets.QDialog) and w.isVisible()]
        assert len(dialogs) == 1, dialogs
        dialog = dialogs[0]
        assert dialog.findChild(QtWidgets.QLabel, "weaveStage").text() == "Slicing and layering"
        assert dialog.findChild(QtWidgets.QLabel, "weaveElapsed").text().startswith("Elapsed: ")
        method = sys.argv[2]
        if method == "button":
            button = dialog.findChild(QtWidgets.QPushButton, "cancelWeave")
            QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
            assert not button.isEnabled()
        elif method == "escape":
            QtTest.QTest.keyClick(dialog, QtCore.Qt.Key_Escape)
        elif method == "close":
            dialog.close()
        else:
            app.closeAllWindows()
        actions.append(method)
        assert not dialog.findChild(QtWidgets.QPushButton, "cancelWeave").isEnabled(), method
        # Repeated closure must not unlock the owner while writers terminate.
        dialog.reject()
        owner.close()
        assert owner.close_count == 0
    except BaseException as exc:
        failures.append(repr(exc))
        for dialog in dialogs:
            dialog.reject()
timer.timeout.connect(cancel_when_ready)
timer.start(10)
start = time.monotonic()
try:
    run_gui_process(owner, [sys.executable, "-c", writer, str(folder)], label="Cancel test", temp_dir=str(folder))
except GuiProcessCancelled:
    pass
else:
    raise AssertionError("Canceled export reported success")
assert not failures, failures
assert actions == [sys.argv[2]], actions
assert time.monotonic() - start < 10
assert owner.close_count == 0 and owner.isVisible()
assert not any(isinstance(w, QtWidgets.QDialog) and w.isVisible() for w in app.topLevelWidgets())
before = heartbeat.read_bytes() if heartbeat.exists() else None
(folder / "release.txt").write_text("writer must already have stopped")
QtTest.QTest.qWait(350)
assert not late.exists(), "Nested exporter outlived cancellation"
assert (heartbeat.read_bytes() if heartbeat.exists() else None) == before, "Nested exporter continued writing"
owner.close()
assert owner.close_count == 1
'''
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), cancel_method],
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Destroyed while process" not in result.stderr


@pytest.mark.gui
def test_gui_process_can_cancel_during_startup(tmp_path):
    pytest.importorskip("qtpy")
    script = r'''
import sys
from pathlib import Path
from qtpy import QtCore, QtWidgets, QtTest
from layerloom.gui_process import GuiProcessCancelled, run_gui_process
app = QtWidgets.QApplication([])
app.setQuitOnLastWindowClosed(False)
def cancel():
    for widget in app.topLevelWidgets():
        if isinstance(widget, QtWidgets.QDialog) and widget.isVisible():
            widget.reject()
QtCore.QTimer.singleShot(0, cancel)
writer = "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); deadline=time.monotonic()+15" + "\nwhile time.monotonic()<deadline and not p.with_suffix('.release').exists():\n time.sleep(.05)\np.write_text('escaped')"
try:
    run_gui_process(None, [sys.executable, "-c", writer, sys.argv[1]], label="Cancel startup")
except GuiProcessCancelled:
    pass
else:
    raise AssertionError("Startup cancellation was ignored")
Path(sys.argv[1]).with_suffix(".release").write_text("writer must already have stopped")
QtTest.QTest.qWait(350)
assert not Path(sys.argv[1]).exists()
'''
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "must-not-be-written.txt")],
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Destroyed while process" not in result.stderr


@pytest.mark.parametrize("outcome", ["success", "cancel", "failure", "missing"])
def test_weave_publishes_only_completed_output(tmp_path, monkeypatch, outcome):
    """Exercise the actual GUI export method without importing a VTK window."""
    import ast
    from pathlib import Path
    import tempfile
    import time
    from types import SimpleNamespace

    from layerloom import gui_process

    source = Path(__file__).resolve().parents[1] / "src/layerloom/3mf_gui_v58.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "QtAssignColorsApp")
    method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "_run_weave_subprocess")
    namespace = {"os": os, "sys": sys, "tempfile": tempfile, "time": time,
                 "Optional": __import__("typing").Optional, "_log": lambda *args: None,
                 "_perf_log": lambda *args, **kwargs: None}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
    destination = tmp_path / "existing.3mf"
    destination.write_bytes(b"previous successful output")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    errors, messages = [], []
    window = SimpleNamespace(_temp_dir=str(workspace),
                             status_bar=SimpleNamespace(showMessage=lambda *args: messages.append(args)),
                             _error=lambda *args: errors.append(args))

    def exporter(parent, command, *, label, temp_dir):
        staged = Path(command[command.index("-o") + 1])
        assert staged != destination and staged.parent.parent == tmp_path
        assert "--no-stls" in command
        assert Path(temp_dir).parent == workspace
        assert destination.read_bytes() == b"previous successful output"
        if outcome != "missing":
            staged.write_bytes(b"new woven output")
        if outcome == "cancel":
            raise gui_process.GuiProcessCancelled()
        return subprocess.CompletedProcess(command, 7 if outcome == "failure" else 0, "", "failed" if outcome == "failure" else "")

    monkeypatch.setattr(gui_process, "run_gui_process", exporter)
    result = namespace["_run_weave_subprocess"](window, "input.3mf", step=.2, output_path=str(destination))
    if outcome == "success":
        assert result == str(destination)
        assert destination.read_bytes() == b"new woven output"
        assert not errors
    else:
        assert result is None
        assert destination.read_bytes() == b"previous successful output"
        assert bool(errors) == (outcome != "cancel")
        if outcome == "cancel":
            assert "canceled" in messages[0][0]
    assert not list(workspace.iterdir())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["existing.3mf", "workspace"]
