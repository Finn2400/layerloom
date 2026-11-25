"""File-based experimental weaving UI; never loads a model into the viewer."""
from __future__ import annotations

import os
from pathlib import Path
import re
import sys

from layerloom.contour_options import ContourOptions
from layerloom.tokens import ALL_TOKENS


def contour_command(input_path: str, output_path: str, *, step: float,
                    first_layer_color: str, options: ContourOptions) -> list[str]:
    """Build a subprocess argv without shell interpolation or GUI dependencies."""
    options.validate(step)
    if Path(input_path).resolve() == Path(output_path).resolve():
        raise ValueError("Choose a different output file so the source is preserved")
    if Path(input_path).suffix.lower() != ".3mf" or Path(output_path).suffix.lower() != ".3mf":
        raise ValueError("Prepared input and output must both use .3mf")
    if first_layer_color not in {"auto", "off", *ALL_TOKENS}:
        raise ValueError("Unknown first-layer filament")
    args = ["-m", "layerloom.weave", "-i", str(input_path), "-o", str(output_path),
            "--weave-mode", "layer-contour", "--step", str(step),
            "--xy-tolerance-mm", str(options.xy_tolerance_mm),
            "--memory-budget-mib", str(options.memory_budget_mib),
            "--first-layer-color", first_layer_color, "--no-export-stls", "--verbose"]
    if options.first_layer_height is not None:
        args += ["--first-layer-height", str(options.first_layer_height)]
    if options.scratch_dir:
        args += ["--scratch-dir", options.scratch_dir]
    return args


def create_contour_dialog(parent=None):
    # Keep CLI imports independent of optional Qt packages.
    from qtpy import QtCore, QtWidgets

    class ContourWeaveDialog(QtWidgets.QDialog):
        def __init__(self):
            super().__init__(parent)
            self.setWindowTitle("Layer-contour weave — experimental")
            self.resize(700, 670)
            self.process = None
            QtWidgets.QApplication.instance().aboutToQuit.connect(self.shutdown)
            self.cancelled = False
            self.output_path = None
            self._pending = ""
            layout = QtWidgets.QVBoxLayout(self)
            description = QtWidgets.QLabel(
                "Weave a prepared, labeled 3MF without opening its full mesh. "
                "Legacy weaving remains the default.\n"
                "XY tolerance controls contour error; wall placement still needs slicer validation. "
                "Keep the exported orientation and layer heights when slicing."
            )
            description.setWordWrap(True)
            layout.addWidget(description)
            self.settings = QtWidgets.QWidget()
            form = QtWidgets.QFormLayout(self.settings)
            self.input = QtWidgets.QLineEdit()
            self.output = QtWidgets.QLineEdit()
            self.scratch = QtWidgets.QLineEdit()
            self.scratch.setPlaceholderText("Output folder (default; 4 GiB free-space reserve)")
            for label, edit, callback in [
                ("Prepared 3MF", self.input, self.choose_input),
                ("Output 3MF", self.output, self.choose_output),
                ("Scratch folder", self.scratch, self.choose_scratch),
            ]:
                row = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(row)
                h.setContentsMargins(0, 0, 0, 0); h.addWidget(edit)
                button = QtWidgets.QPushButton("Browse…"); button.clicked.connect(callback)
                h.addWidget(button); form.addRow(label, row)
            self.layer = QtWidgets.QDoubleSpinBox(); self.layer.setDecimals(4)
            self.layer.setRange(0.001, 10); self.layer.setValue(0.08); self.layer.setSuffix(" mm")
            self.first = QtWidgets.QDoubleSpinBox(); self.first.setDecimals(4)
            self.first.setRange(0.001, 10); self.first.setValue(0.08); self.first.setSuffix(" mm")
            self.same_first = QtWidgets.QCheckBox("Same as ordinary layer height")
            self.same_first.setChecked(True); self.first.setEnabled(False)
            self.same_first.toggled.connect(lambda same: self.first.setEnabled(not same))
            self.same_first.toggled.connect(lambda same: self.first.setValue(self.layer.value()) if same else None)
            self.layer.valueChanged.connect(lambda value: self.first.setValue(value) if self.same_first.isChecked() else None)
            first_row = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(first_row)
            h.setContentsMargins(0, 0, 0, 0); h.addWidget(self.first); h.addWidget(self.same_first)
            self.tolerance = QtWidgets.QDoubleSpinBox(); self.tolerance.setDecimals(4)
            self.tolerance.setRange(0, 1); self.tolerance.setValue(0.02)
            self.tolerance.setSingleStep(0.01); self.tolerance.setSuffix(" mm")
            self.memory = QtWidgets.QSpinBox(); self.memory.setRange(64, 65536)
            self.memory.setValue(1024); self.memory.setSuffix(" MiB")
            self.base_color = QtWidgets.QComboBox(); self.base_color.addItems(["auto", "off", *ALL_TOKENS])
            form.addRow("Layer height", self.layer)
            form.addRow("First-layer height", first_row)
            form.addRow("XY contour tolerance", self.tolerance)
            form.addRow("Working-data budget", self.memory)
            form.addRow("First-layer filament", self.base_color)
            layout.addWidget(self.settings)
            self.progress = QtWidgets.QProgressBar(); self.progress.setRange(0, 100)
            layout.addWidget(self.progress)
            self.status = QtWidgets.QLabel("Ready. The complete woven mesh will not be loaded into the viewer.")
            self.status.setWordWrap(True); layout.addWidget(self.status)
            self.log = QtWidgets.QPlainTextEdit(); self.log.setReadOnly(True)
            self.log.document().setMaximumBlockCount(500); layout.addWidget(self.log, 1)
            buttons = QtWidgets.QHBoxLayout(); layout.addLayout(buttons)
            self.run_button = QtWidgets.QPushButton("Weave 3MF")
            self.run_button.clicked.connect(self.start)
            self.cancel_button = QtWidgets.QPushButton("Cancel run")
            self.cancel_button.setEnabled(False); self.cancel_button.clicked.connect(self.cancel)
            self.close_button = QtWidgets.QPushButton("Close"); self.close_button.clicked.connect(self.close)
            buttons.addWidget(self.run_button); buttons.addWidget(self.cancel_button)
            buttons.addStretch(1); buttons.addWidget(self.close_button)

        def choose_input(self):
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Prepared labeled 3MF", "", "3MF (*.3mf)")
            if path:
                self.input.setText(path)
                if not self.output.text():
                    self.output.setText(str(Path(path).with_name(Path(path).stem + "_contour_woven.3mf")))

        def choose_output(self):
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Output 3MF", self.output.text(), "3MF (*.3mf)", options=QtWidgets.QFileDialog.DontConfirmOverwrite)
            if path:self.output.setText(path if path.lower().endswith(".3mf") else path + ".3mf")

        def choose_scratch(self):
            path = QtWidgets.QFileDialog.getExistingDirectory(self, "Scratch folder", self.scratch.text())
            if path:self.scratch.setText(path)

        def start(self):
            try:
                source = str(Path(self.input.text().strip()).expanduser().resolve())
                output = str(Path(self.output.text().strip()).expanduser().resolve())
                if not self.input.text().strip() or not Path(source).is_file():
                    raise ValueError("Choose an existing prepared 3MF")
                if not self.output.text().strip():raise ValueError("Choose an output 3MF")
                options = ContourOptions(
                    xy_tolerance_mm=self.tolerance.value(),
                    first_layer_height=None if self.same_first.isChecked() else self.first.value(),
                    memory_budget_mib=self.memory.value(),
                    scratch_dir=self.scratch.text().strip() or None,
                )
                args = contour_command(source, output, step=self.layer.value(),
                                       first_layer_color=self.base_color.currentText(), options=options)
                if Path(output).exists():
                    choice = QtWidgets.QMessageBox.question(self, "Replace output?", f"Replace {output}?",
                        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
                    if choice != QtWidgets.QMessageBox.Yes:return
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, "Cannot start weave", str(exc)); return
            self.output_path = output; self.cancelled = False; self._pending = ""
            self.log.clear(); self.settings.setEnabled(False); self.run_button.setEnabled(False)
            self.cancel_button.setEnabled(True); self.progress.setRange(0, 0)
            self.status.setText("Starting prepared-3MF weave…")
            self.process = QtCore.QProcess(self)
            env = QtCore.QProcessEnvironment.systemEnvironment()
            src_root = str(Path(__file__).resolve().parents[1])
            env.insert("PYTHONPATH", src_root + (os.pathsep + env.value("PYTHONPATH") if env.contains("PYTHONPATH") else ""))
            env.insert("PYTHONUNBUFFERED", "1")
            self.process.setProcessEnvironment(env)
            self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
            self.process.readyReadStandardOutput.connect(self.drain_output)
            self.process.finished.connect(self.finished)
            self.process.errorOccurred.connect(self.process_error)
            self.process.start(sys.executable, args)

        def drain_output(self):
            if self.process is None:return
            while self.process.bytesAvailable():
                self._pending += bytes(self.process.read(16384)).decode("utf-8", errors="replace")
                lines = self._pending.split("\n"); self._pending = lines.pop()
                if len(self._pending)>16384:
                    lines.append(self._pending[:16384] + " [long line truncated]"); self._pending=""
                for line in lines:
                    self.log.appendPlainText(line[:16384])
                    match = re.search(r"\blayer\s+(\d+)\s*/\s*(\d+)", line, re.I)
                    if match:
                        current,total=map(int,match.groups())
                        self.progress.setRange(0,max(1,total)); self.progress.setValue(current)
                        self.status.setText(f"Processing layer {current} of {total}")

        def cancel(self):
            process=self.process
            if process is None or process.state()==QtCore.QProcess.NotRunning:return
            self.cancelled=True; self.cancel_button.setEnabled(False)
            self.status.setText("Cancelling; waiting for worker cleanup…")
            process.terminate()
            # Graceful termination gives the backend time to remove its private spool.
            # Parent the kill timer to the worker, so closing/deleting the
            # dialog cannot leave a callback referencing a deleted QProcess.
            timer = QtCore.QTimer(process)
            timer.setSingleShot(True)
            timer.timeout.connect(process.kill)
            process.finished.connect(timer.stop)
            timer.start(5000)

        def process_error(self, error):
            if error==QtCore.QProcess.FailedToStart:
                self.status.setText("Could not start weaving: " + self.process.errorString())
                self.settings.setEnabled(True); self.run_button.setEnabled(True)
                self.cancel_button.setEnabled(False); self.progress.setRange(0,100); self.progress.setValue(0)

        def finished(self, code, status):
            self.drain_output()
            if self._pending:self.log.appendPlainText(self._pending);self._pending=""
            self.settings.setEnabled(True);self.run_button.setEnabled(True);self.cancel_button.setEnabled(False)
            self.progress.setRange(0,100)
            succeeded=code==0 and status==QtCore.QProcess.NormalExit and not self.cancelled and bool(self.output_path and Path(self.output_path).is_file())
            self.progress.setValue(100 if succeeded else 0)
            if succeeded:
                self.status.setText(f"Saved {self.output_path}\nInspect the validation report and slice with the same layer heights. The mesh was not loaded into the viewer.")
            elif self.cancelled:self.status.setText("Run cancelled. See the log for cleanup status.")
            else:self.status.setText(f"Weaving failed (exit {code}); see the log. No success is being reported.")

        def shutdown(self):
            process=self.process
            if process is not None and process.state()!=QtCore.QProcess.NotRunning:
                process.terminate()
                if not process.waitForFinished(5000):
                    process.kill();process.waitForFinished(1000)

        def closeEvent(self, event):
            if self.process is not None and self.process.state()!=QtCore.QProcess.NotRunning:
                self.cancel();event.ignore();return
            event.accept()

    return ContourWeaveDialog()
