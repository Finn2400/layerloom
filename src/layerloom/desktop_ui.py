"""Small first-run and support affordances layered onto the existing Qt GUI."""
from __future__ import annotations

import logging
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from PyQt5 import QtCore, QtGui, QtWidgets

from .app_logging import diagnostic_text, log_dir
from .example_assets import find_example_file


class _WelcomePanel(QtWidgets.QFrame):
    def __init__(self, controller, parent):
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("layerloomWelcome")
        self.setStyleSheet("#layerloomWelcome { background: #252a31; border: 1px solid #566473; border-radius: 12px; } QLabel { color: #e9e9e9; }")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        self.message = QtWidgets.QLabel("Start with a small example, or open your own model.")
        self.message.setAlignment(QtCore.Qt.AlignCenter)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.actions = QtWidgets.QWidget()
        buttons = QtWidgets.QHBoxLayout(self.actions)
        buttons.setContentsMargins(0, 8, 0, 0)
        example = QtWidgets.QPushButton("Try the example")
        example.setObjectName("tryExampleButton")
        example.clicked.connect(controller.try_example)
        example.setStyleSheet("background: #39526a; padding: 10px 16px; color: white;")
        open_model = QtWidgets.QPushButton("Open a model")
        open_model.clicked.connect(controller.open_model)
        buttons.addWidget(example)
        buttons.addWidget(open_model)
        layout.addWidget(self.actions)

    def setText(self, text):
        self.message.setText(text)
        self.actions.setVisible(not self.controller.has_model())


class DesktopController(QtCore.QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.example_directory = None
        self.output_path = None
        self.paint_editor = getattr(getattr(window, "paint_panel", None), "editor", None)
        self.paint_welcome = None
        old_hint = window.viewer_empty_hint
        old_hint.hide()
        old_hint.deleteLater()
        window.viewer_empty_hint = _WelcomePanel(self, window.viewer_host)
        window.viewer_empty_hint.setVisible(not bool(window.base_file))
        window._position_viewer_overlays()
        window.viewer_empty_hint.raise_()
        self._install_result_panel()
        window._on_weave_complete = self.weave_complete
        if self.paint_editor is not None:
            # Keep the Arrange hint in its existing host. The Paint welcome is
            # a sibling of the central widget, centered over the VTK viewport.
            self.paint_welcome = _WelcomePanel(self, self.paint_editor)
            self.paint_editor.plotter.installEventFilter(self)
            self.paint_editor.documentChanged.connect(self._sync_paint_welcome)
            self.paint_editor.exportCompleted.connect(self.paint_export_complete)
            self._sync_paint_welcome()
        example_action = getattr(window, "_load_example_action", None)
        if example_action is not None:
            example_action.triggered.disconnect(window._on_load_example)
            example_action.triggered.connect(self.try_benchy_example)
        window._on_load_example = self.try_benchy_example
        menu = window.menuBar().addMenu("Help")
        menu.addAction("Try the CMY cubes example", self.try_example)
        menu.addAction("Load the Benchy example", window._on_load_example)
        menu.addSeparator()
        menu.addAction("Copy diagnostics", self.copy_diagnostics)
        menu.addAction("Open logs", self.open_logs)

    def has_model(self):
        if self.paint_editor is not None:
            return self.paint_editor.document is not None
        return bool(self.window.base_file)

    def _sync_paint_welcome(self, *_args):
        if self.paint_welcome is None:
            return
        panel, viewport = self.paint_welcome, self.paint_editor.plotter
        panel.setVisible(not self.has_model())
        if self.has_model():
            return
        width = max(1, min(520, viewport.width() - 40))
        panel.setMaximumWidth(width)
        panel.adjustSize()
        panel.resize(min(width, panel.sizeHint().width()), panel.sizeHint().height())
        origin = viewport.mapTo(self.paint_editor, QtCore.QPoint(0, 0))
        panel.move(origin.x() + max(0, (viewport.width() - panel.width()) // 2),
                   origin.y() + max(0, (viewport.height() - panel.height()) // 2))
        panel.raise_()

    def eventFilter(self, watched, event):
        if (self.paint_editor is not None and watched is self.paint_editor.plotter
                and event.type() in (QtCore.QEvent.Resize, QtCore.QEvent.Move, QtCore.QEvent.Show)):
            self._sync_paint_welcome()
        return super().eventFilter(watched, event)

    def _install_result_panel(self):
        dock = QtWidgets.QDockWidget("Exported model", self.window)
        dock.setObjectName("weaveResultDock")
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetClosable)
        content = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(content)
        self.result_text = QtWidgets.QLabel()
        self.result_text.setTextFormat(QtCore.Qt.PlainText)
        self.result_text.setWordWrap(True)
        self.result_text.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.result_text, 1)
        reveal = QtWidgets.QPushButton("Show in Finder" if sys.platform == "darwin" else "Show in folder")
        reveal.clicked.connect(self.reveal_output)
        layout.addWidget(reveal)
        dock.setWidget(content)
        self.window.addDockWidget(QtCore.Qt.BottomDockWidgetArea, dock)
        dock.hide()
        self.result_dock = dock

    def try_example(self):
        return self._load_packaged_example("tutorial_cmy_cubes.3mf")

    def try_benchy_example(self):
        return self._load_packaged_example("tutorial_cmy_benchy_cutup.3mf")

    def _load_packaged_example(self, filename):
        path = find_example_file(filename)
        if path is None:
            self.window._error("Example unavailable", "The packaged example is missing. Download a fresh LayerLoom release. Your models are unaffected.")
            return False
        directory = tempfile.TemporaryDirectory(prefix="layerloom_example_")
        local = Path(directory.name) / path.name
        try:
            shutil.copyfile(path, local)
            if not self.window.open_paint_path(str(local)):
                directory.cleanup()
                return False
            self._initialize_example_paint()
        except Exception as exc:
            directory.cleanup()
            logging.getLogger("layerloom.example").exception("Example could not load")
            self.window._error("Example could not load", str(exc))
            return False
        previous = self.example_directory
        self.example_directory = directory
        if previous:
            previous.cleanup()
        self.result_dock.hide()
        self.window.status_bar.showMessage("Example ready. Paint a surface or choose Export geometry 3MF… to create printable material parts.")
        return True

    def _initialize_example_paint(self):
        """Translate trusted tutorial name recipes into editable source paint.

        The packaged examples predate surface paint and store their recipes in
        __PAT_*__ names. This initialization is restricted to those examples;
        ordinary model imports keep their own imported colors.
        """
        from .paint_export import assign_palette
        from .palette_assignment import load_palette_entries
        from .tokens import PAT_TAG_RE
        editor = self.paint_editor
        document = editor.document
        entries = {entry["token"]: entry for entry in load_palette_entries("Normal")}
        preferences = {}
        for mesh in document.meshes:
            match = PAT_TAG_RE.search(mesh.name)
            entry = entries.get(match.group(1).lower()) if match else None
            if entry is None:
                continue
            rgba = list(bytes.fromhex(entry["hex"].lstrip("#"))) + [255]
            mesh.face_rgba[:] = rgba
            mesh.source_rgba[:] = rgba
            preferences[entry["hex"].upper()] = entry["token"]
        document.metadata.update({
            "paint_palette": "Normal", "paint_mapping_mode": "woven",
            "paint_recipe_preferences": {"Normal": preferences},
            "paint_mapping": assign_palette(document, "Normal", recipe_overrides=preferences),
        })
        editor.set_document(document)
        editor.suggested_output_directory = Path(
            QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.DocumentsLocation) or Path.home()
        )

    def open_model(self):
        return self.window._on_open()

    def paint_export_complete(self, report):
        self.output_path = Path(report["output_3mf"]).resolve()
        step = report["layer_height_mm"]
        self.result_text.setText(
            f"Saved: {self.output_path}\n"
            "Open all material parts together in your slicer and keep their alignment. "
            f"Use {step:g} mm for both first and subsequent layers; disable adaptive layer height."
        )
        self.result_dock.show()

    def weave_complete(self, path):
        self.output_path = Path(path).resolve()
        step = self.window.layer_height_spin.value()
        self.result_text.setText(
            f"Saved: {self.output_path}\n"
            f"Open this 3MF in your slicer. Match the all_… objects to your loaded filaments. "
            f"Use {step:g} mm layers; the first layer must be an integer multiple of this height."
        )
        self.result_dock.show()

    def reveal_output(self):
        if self.output_path is None:
            return
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["/usr/bin/open", "-R", str(self.output_path)])
            else:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.output_path.parent)))
        except OSError as exc:
            self.window._error("Could not open output folder", f"Your woven model is saved at:\n{self.output_path}\n\n{exc}")

    def copy_diagnostics(self):
        QtWidgets.QApplication.clipboard().setText(diagnostic_text())
        self.window.status_bar.showMessage("Diagnostics copied. You can paste them into a support request.", 5000)

    def open_logs(self):
        directory = log_dir()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            if not QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(directory))):
                self.window._error("Could not open logs", f"You can open this folder manually:\n{directory}")
        except OSError as exc:
            self.window._error("Logs unavailable", f"Could not open the log folder:\n{directory}\n\n{exc}")


def install_desktop_ui(window) -> DesktopController:
    existing = getattr(window, "_desktop_controller", None)
    if existing is not None:
        return existing
    controller = DesktopController(window)
    window._desktop_controller = controller
    return controller
