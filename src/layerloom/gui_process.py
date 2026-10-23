"""Run a GUI-owned command while Qt continues repainting the window."""

from __future__ import annotations

import codecs
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Sequence


class GuiProcessCancelled(Exception):
    """The user stopped the export; its process tree has finished."""


def run_gui_process(
    parent, command: Sequence[str], *, label: str, temp_dir: str | None = None,
) -> subprocess.CompletedProcess:
    """Wait in Qt's event loop, keeping model edits disabled until the job ends.

    Output is explicitly UTF-8, including nested Python commands on Windows.
    Cancellation stops the process tree before releasing the modal lock. The
    caller must stage output files and publish them only after success.
    """
    from qtpy import QtCore, QtWidgets

    class BusyDialog(QtWidgets.QDialog):
        def reject(self):
            cancel()

        def closeEvent(self, event):
            cancel()
            event.ignore()

    class ParentCloseGuard(QtCore.QObject):
        def eventFilter(self, watched, event):
            # A programmatic close or Close All Windows can bypass modality.
            # The owner's closeEvent must not delete files used by the writer.
            if event.type() == QtCore.QEvent.Close:
                event.ignore()
                return True
            return False

    owner = parent.window() if parent is not None else None
    close_guard = ParentCloseGuard()
    dialog = BusyDialog(parent)
    dialog.setWindowTitle(label)
    dialog.setWindowModality(QtCore.Qt.ApplicationModal)
    dialog.setMinimumWidth(420)
    layout = QtWidgets.QVBoxLayout(dialog)
    stage = QtWidgets.QLabel("Preparing woven layers…", dialog)
    stage.setObjectName("weaveStage")
    stage.setWordWrap(True)
    layout.addWidget(stage)
    description = QtWidgets.QLabel(
        "This may take a few minutes. Cancel keeps your current model and any previous output.",
        dialog,
    )
    description.setWordWrap(True)
    layout.addWidget(description)
    progress = QtWidgets.QProgressBar(dialog)
    progress.setRange(0, 0)
    layout.addWidget(progress)
    elapsed = QtWidgets.QLabel("Elapsed: 0:00", dialog)
    elapsed.setObjectName("weaveElapsed")
    layout.addWidget(elapsed)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel, parent=dialog)
    cancel_button = buttons.button(QtWidgets.QDialogButtonBox.Cancel)
    cancel_button.setObjectName("cancelWeave")
    layout.addWidget(buttons)

    process = QtCore.QProcess()
    environment = QtCore.QProcessEnvironment.systemEnvironment()
    environment.insert("PYTHONIOENCODING", "utf-8")
    environment.insert("PYTHONUTF8", "1")
    environment.insert("PYTHONUNBUFFERED", "1")
    if temp_dir is not None:
        for name in ("TMPDIR", "TEMP", "TMP"):
            environment.insert(name, temp_dir)
    process.setProcessEnvironment(environment)
    loop = QtCore.QEventLoop()
    output = bytearray()
    errors = bytearray()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    pending_line = ""
    cancelled = False
    tree_stop = QtCore.QProcess()
    # Retain diagnostic tails without allowing verbose meshes to fill memory.
    max_log_bytes = 2 * 1024 * 1024

    def drain():
        nonlocal pending_line
        chunk = bytes(process.readAllStandardOutput())
        output.extend(chunk)
        errors.extend(bytes(process.readAllStandardError()))
        del output[:-max_log_bytes]
        del errors[:-max_log_bytes]
        pending_line += decoder.decode(chunk)
        lines = pending_line.split("\n")
        pending_line = lines.pop()[-8192:]
        if not cancelled:
            for line in lines:
                line = line.strip()
                if line.startswith("-- ") and line.endswith(" --"):
                    stage.setText(line[3:-3])

    def finished(*_args):
        if process.state() == QtCore.QProcess.NotRunning and tree_stop.state() == QtCore.QProcess.NotRunning:
            loop.quit()

    def stop_tree():
        pid = int(process.processId())
        if not pid:
            return
        if os.name == "nt":
            # QProcess.kill alone leaves the nested bake exporter running.
            # taskkill is supplied by Windows and waits for the selected tree.
            tree_stop.start("taskkill", ["/PID", str(pid), "/T", "/F"])
        else:
            try:
                if os.getpgid(pid) != pid:
                    # Wait for the bootstrap to isolate itself. Killing only
                    # its PID here could race exec/spawn and orphan an exporter.
                    QtCore.QTimer.singleShot(10, stop_tree)
                    return
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # Already exited; QProcess.finished will release the lock.
            except OSError:
                stage.setText("Unable to stop immediately. Waiting for the export to finish…")

    def cancel():
        nonlocal cancelled
        if cancelled or process.state() == QtCore.QProcess.NotRunning:
            return
        cancelled = True
        cancel_button.setEnabled(False)
        stage.setText("Stopping export…")
        stop_tree()

    def started():
        if cancelled:
            stop_tree()

    def tree_stop_finished(*_args):
        if tree_stop.exitCode() and process.state() != QtCore.QProcess.NotRunning:
            stage.setText("Unable to stop immediately. Waiting for the export to finish…")
        finished()

    def tree_stop_failed(error):
        if error == QtCore.QProcess.FailedToStart:
            stage.setText("Unable to stop immediately. Waiting for the export to finish…")
            finished()

    def failed(error):
        if error == QtCore.QProcess.FailedToStart:
            loop.quit()

    process.readyReadStandardOutput.connect(drain)
    process.readyReadStandardError.connect(drain)
    process.started.connect(started)
    process.finished.connect(finished)
    process.errorOccurred.connect(failed)
    tree_stop.finished.connect(tree_stop_finished)
    tree_stop.errorOccurred.connect(tree_stop_failed)
    buttons.rejected.connect(cancel)
    timer = QtCore.QTimer(dialog)
    start_time = time.monotonic()

    def update_elapsed():
        seconds = int(time.monotonic() - start_time)
        elapsed.setText(f"Elapsed: {seconds // 60}:{seconds % 60:02d}")

    timer.timeout.connect(update_elapsed)
    timer.start(1000)
    try:
        program, arguments = str(command[0]), [str(arg) for arg in command[1:]]
        if os.name != "nt":
            executable = shutil.which(program)
            if executable is None:
                raise OSError(f"Could not start {program}: executable not found or not executable")
            # Qt 5 has no Python child-process modifier. This small bootstrap
            # creates an isolated process group before it can start exporters.
            arguments = ["-c", "import os,sys; os.setsid(); os.execv(sys.argv[1],sys.argv[1:])", executable, *arguments]
            program = sys.executable
        if owner is not None:
            owner.installEventFilter(close_guard)
        dialog.show()
        process.start(program, arguments)
        if process.state() != QtCore.QProcess.NotRunning:
            loop.exec_()
        # Application shutdown (e.g. Cmd+Q) can unwind every nested event loop.
        # Finish the active writer before destroying its QProcess or workspace;
        # it may itself be waiting for a nested exporter.
        if process.state() != QtCore.QProcess.NotRunning:
            process.waitForFinished(-1)
        if tree_stop.state() != QtCore.QProcess.NotRunning:
            tree_stop.waitForFinished(-1)
        drain()
        if process.error() == QtCore.QProcess.FailedToStart:
            raise OSError(f"Could not start {command[0]}: {process.errorString()}")
        if cancelled:
            raise GuiProcessCancelled()
        returncode = process.exitCode()
        if process.exitStatus() == QtCore.QProcess.CrashExit:
            returncode = returncode or -1
        return subprocess.CompletedProcess(
            list(command), returncode,
            output.decode("utf-8", errors="replace"),
            errors.decode("utf-8", errors="replace"),
        )
    finally:
        if owner is not None:
            owner.removeEventFilter(close_guard)
        dialog.done(0)
        dialog.deleteLater()
