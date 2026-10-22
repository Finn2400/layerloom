"""Run a GUI-owned command while Qt continues repainting the window."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence


def run_gui_process(
    parent, command: Sequence[str], *, label: str, temp_dir: str | None = None,
) -> subprocess.CompletedProcess:
    """Wait in Qt's event loop, keeping model edits disabled until the job ends.

    Output is explicitly UTF-8, including nested Python commands on Windows.
    No cancellation is offered: killing only the parent process could leave a
    nested exporter writing files after the GUI reports that it has stopped.
    """
    from qtpy import QtCore, QtWidgets

    class BusyDialog(QtWidgets.QDialog):
        def reject(self):
            # Escape must not release the modal lock while a writer is running.
            pass

        def closeEvent(self, event):
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
    dialog.setWindowFlag(QtCore.Qt.WindowCloseButtonHint, False)
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.addWidget(QtWidgets.QLabel("Creating woven layers. This may take a few minutes."))
    progress = QtWidgets.QProgressBar(dialog)
    progress.setRange(0, 0)
    layout.addWidget(progress)

    process = QtCore.QProcess()
    environment = QtCore.QProcessEnvironment.systemEnvironment()
    environment.insert("PYTHONIOENCODING", "utf-8")
    environment.insert("PYTHONUTF8", "1")
    if temp_dir is not None:
        for name in ("TMPDIR", "TEMP", "TMP"):
            environment.insert(name, temp_dir)
    process.setProcessEnvironment(environment)
    loop = QtCore.QEventLoop()
    output = bytearray()
    errors = bytearray()
    # Retain diagnostic tails without allowing verbose meshes to fill memory.
    max_log_bytes = 2 * 1024 * 1024

    def drain():
        output.extend(bytes(process.readAllStandardOutput()))
        errors.extend(bytes(process.readAllStandardError()))
        del output[:-max_log_bytes]
        del errors[:-max_log_bytes]

    def failed(error):
        if error == QtCore.QProcess.FailedToStart:
            loop.quit()

    process.readyReadStandardOutput.connect(drain)
    process.readyReadStandardError.connect(drain)
    process.finished.connect(loop.quit)
    process.errorOccurred.connect(failed)
    try:
        if owner is not None:
            owner.installEventFilter(close_guard)
        dialog.show()
        process.start(str(command[0]), [str(arg) for arg in command[1:]])
        if process.state() != QtCore.QProcess.NotRunning:
            loop.exec_()
        # Application shutdown (e.g. Cmd+Q) can unwind every nested event loop.
        # Finish the active writer before destroying its QProcess or workspace;
        # it may itself be waiting for a nested exporter.
        if process.state() != QtCore.QProcess.NotRunning:
            process.waitForFinished(-1)
        drain()
        if process.error() == QtCore.QProcess.FailedToStart:
            raise OSError(f"Could not start {command[0]}: {process.errorString()}")
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
