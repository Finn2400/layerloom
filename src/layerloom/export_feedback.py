"""Persistent, non-modal export completion shared by both workspaces."""
from pathlib import Path
import subprocess
import sys

from qtpy import QtCore, QtGui, QtWidgets


class ExportResultDock(QtWidgets.QDockWidget):
    def __init__(self, parent):
        super().__init__("Export complete", parent)
        self.setObjectName("weaveResultDock")
        self.setFeatures(QtWidgets.QDockWidget.DockWidgetClosable)
        self.output_path = None
        content = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(content)
        self.message = QtWidgets.QLabel()
        self.message.setTextFormat(QtCore.Qt.PlainText)
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.message)
        self.warnings = QtWidgets.QLabel()
        self.warnings.setObjectName("exportWarnings")
        self.warnings.setTextFormat(QtCore.Qt.PlainText)
        self.warnings.setWordWrap(True)
        layout.addWidget(self.warnings)
        self.reveal_button = QtWidgets.QPushButton("Show in Finder" if sys.platform == "darwin" else "Show in folder")
        self.reveal_button.clicked.connect(self.reveal)
        layout.addWidget(self.reveal_button, 0, QtCore.Qt.AlignLeft)
        self.setWidget(content)
        parent.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self)
        self.hide()

    def show_result(self, path, instruction, warnings=()):
        self.output_path = Path(path).absolute()
        folder = str(self.output_path.parent)
        self.message.setText(f"Export complete — {self.output_path.name}\nSaved in: {folder}\n{instruction}")
        self.warnings.setText("Please check:\n" + "\n".join(warnings) if warnings else "")
        self.warnings.setVisible(bool(warnings))
        self.show()

    def reveal(self):
        if self.output_path is None:
            return
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["/usr/bin/open", "-R", str(self.output_path)])
            elif not QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.output_path.parent))):
                raise OSError("The folder could not be opened.")
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Could not show file", f"Your export is saved at:\n{self.output_path}\n\n{exc}")


def slicer_instruction(report):
    step = report.get("layer_height_mm")
    layers = f"Use {step:g} mm for both first and subsequent layers" if step else "Use the export layer height in your slicer"
    return f"Open the 3MF in your slicer and keep its material parts aligned. {layers}; disable adaptive layer height."
