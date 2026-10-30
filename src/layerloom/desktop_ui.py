"""Small first-run and support affordances layered onto the existing Qt GUI."""
from __future__ import annotations

import logging
from pathlib import Path
import shutil
import tempfile

from PyQt5 import QtCore, QtGui, QtWidgets

from .app_logging import diagnostic_text, log_dir
from .example_assets import find_example_file
from .file_dialogs import default_output_directory
from .export_feedback import ExportResultDock, slicer_instruction


class _WelcomePanel(QtWidgets.QFrame):
    def __init__(self, controller, parent):
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("layerloomWelcome")
        self.setStyleSheet("#layerloomWelcome { background: #252a31; border: 1px solid #566473; border-radius: 12px; } QLabel { color: #e9e9e9; }")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        self.message = QtWidgets.QLabel("Open a model or try the example. Select or paint the colors, then press Weave.")
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
            self.paint_editor.external_export_feedback = True
            self.paint_editor.exportCompleted.connect(self.paint_export_complete)
            self.paint_editor.documentChanged.connect(self.result_dock.hide)
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
        self.result_dock = ExportResultDock(self.window)
        self.result_text = self.result_dock.message

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
            if not self.window.open_model_path(str(local)):
                directory.cleanup()
                return False
            self._initialize_example_paint(filename)
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
        if filename == "tutorial_cmy_benchy_cutup.3mf":
            message = ("Benchy ready — larger example. Export uses 0.16 mm layers and a "
                       "4,096 MiB memory budget. Allow extra time for conversion.")
        else:
            message = ("Example ready. Choose Weave to create printable material parts, "
                       "or turn on Paint to color a surface.")
        self.window.status_bar.showMessage(message)
        return True

    def _initialize_example_paint(self, filename):
        """Translate trusted tutorial name recipes into editable source paint.

        The packaged examples predate surface paint and store their recipes in
        __PAT_*__ names. This initialization is restricted to those examples;
        ordinary model imports keep their own imported colors.
        """
        from collections import Counter
        from .paint_export import assign_palette
        from .paint_export_control import DEFAULT_MEMORY_BUDGET_BYTES
        from .palette_assignment import load_palette_entries
        from .tokens import PAT_TAG_RE
        editor = self.paint_editor
        document = editor.document
        entries = {entry["token"]: entry for entry in load_palette_entries("Normal")}
        # The palette deduplicates equal filament counts (e.g. cym -> cmy).
        equivalent = {tuple(sorted(Counter(token).items())): entry for token, entry in entries.items()}
        source_names = document.metadata.get("3mf_source_object_names", {})
        preferences = {}
        for mesh in document.meshes:
            match = PAT_TAG_RE.search(source_names.get(mesh.key) or mesh.name)
            token = match.group(1).lower() if match else ""
            entry = entries.get(token) or equivalent.get(tuple(sorted(Counter(token).items())))
            if entry is None:
                continue
            rgba = list(bytes.fromhex(entry["hex"].lstrip("#"))) + [255]
            mesh.face_rgba[:] = rgba
            mesh.source_rgba[:] = rgba
            preferences[entry["hex"].upper()] = entry["token"]
        document.metadata.update({
            "paint_palette": "Normal", "paint_mapping_mode": "woven",
            "paint_export_layer_height_mm": 0.16,
            "paint_export_memory_budget_mib": (4096 if filename == "tutorial_cmy_benchy_cutup.3mf"
                                                else DEFAULT_MEMORY_BUDGET_BYTES // (1024 ** 2)),
            "paint_recipe_preferences": {"Normal": preferences},
            "paint_mapping": assign_palette(document, "Normal", recipe_overrides=preferences),
        })
        editor.set_document(document)
        editor.suggested_output_directory = default_output_directory()

    def open_model(self):
        return self.window._on_open()

    def paint_export_complete(self, report):
        self.output_path = Path(report.get("output_3mf") or report["output_path"]).absolute()
        self.result_dock.show_result(self.output_path, slicer_instruction(report), report.get("warnings", []))

    def weave_complete(self, path):
        self.output_path = Path(path).resolve()
        step = self.window.layer_height_spin.value()
        self.result_dock.show_result(self.output_path,
            f"Open this 3MF in your slicer. Match the all_… objects to your loaded filaments. "
            f"Use {step:g} mm layers; the first layer must be an integer multiple of this height.")

    def reveal_output(self):
        self.result_dock.reveal()

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
