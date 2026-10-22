"""Interactive surface-paint prototype: ``python -m layerloom.paint_gui [model]``.

The source mesh owns face colors; the VTK scene is only a view. Paint strokes do
not split or repair geometry. Printable conversion is performed by paint_export.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import traceback

import numpy as np
import pyvista as pv
from pyvistaqt import QtInteractor
from qtpy import QtCore, QtGui, QtWidgets
import vtk

from .paint_model import PaintDocument
from .paint_io import load_paint_document, save_paint_document
from .palette_assignment import PALETTE_FILES, load_palette_entries
from .paint_selection import build_topology, select_area, select_color_region, select_surface


MODEL_FILTER = "Models and paint projects (*.llpaint *.3mf *.glb *.gltf *.stl *.obj *.ply);;All files (*)"


def _hex(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(int(x) for x in rgb[:3]))


def _rgb(hx: str) -> np.ndarray:
    return np.array([int(hx[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.uint8)


class PaintInteractor(QtInteractor):
    """Consume painting gestures while retaining right/middle camera gestures."""

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner
        self._painting = False
        self._selecting = False
        self._orbit_last = None

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and event.modifiers() & QtCore.Qt.AltModifier:
            self._orbit_last = event.pos()
            event.accept()
            return
        if (event.button() == QtCore.Qt.LeftButton and self.owner.tool != "Navigate"
                and not (event.modifiers() & QtCore.Qt.AltModifier)):
            if self.owner.tool in {"Area select", "Surface select", "Color region select"}:
                self._selecting = True
                self.owner.begin_selection(event.modifiers())
                self.owner.select_at(event.pos())
                event.accept()
                return
            self._painting = True
            self.owner.begin_stroke()
            self.owner.paint_at(event.pos())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._orbit_last is not None:
            self._orbit_to(event.pos())
            event.accept()
            return
        if self._selecting:
            if self.owner.tool == "Area select":
                self.owner.select_at(event.pos())
            event.accept()
            return
        if self._painting:
            if self.owner.tool in {"Brush", "Erase"}:
                self.owner.paint_at(event.pos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def _orbit_to(self, position):
        """Apply each delivered pointer position, including the release point."""
        delta = position - self._orbit_last
        self._orbit_last = position
        if delta.isNull():
            return
        self.camera.Azimuth(-float(delta.x()) * 0.4)
        self.camera.Elevation(float(delta.y()) * 0.4)
        self.camera.OrthogonalizeViewUp()
        self.renderer.ResetCameraClippingRange()
        self.render()

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self._orbit_last is not None:
            self._orbit_to(event.pos())
            self._orbit_last = None
            event.accept()
            return
        if event.button() == QtCore.Qt.LeftButton and self._selecting:
            if self.owner.tool == "Area select":
                self.owner.select_at(event.pos())
            self._selecting = False
            event.accept()
            return
        if event.button() == QtCore.Qt.LeftButton and self._painting:
            if self.owner.tool in {"Brush", "Erase"}:
                self.owner.paint_at(event.pos())
            self._painting = False
            self.owner.end_stroke()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ExportWorker(QtCore.QObject):
    progress = QtCore.Signal(int, int)
    succeeded = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    finished = QtCore.Signal()

    def __init__(self, document, output, settings):
        super().__init__()
        self.document = document
        self.output = output
        self.settings = settings

    @QtCore.Slot()
    def run(self):
        try:
            settings = dict(self.settings)
            export_format = settings.pop("export_format", "volumes")
            if export_format == "native":
                if settings.get("mode", "source") != "source":
                    raise ValueError("Native painted export requires original paint colors")
                from .paint_export import export_native_painted_3mf
                report = export_native_painted_3mf(self.document, self.output, progress=self.progress.emit)
            else:
                from .paint_export import export_painted_3mf
                report = export_painted_3mf(
                    self.document, self.output, progress=self.progress.emit, **settings
                )
            report["export_format"] = export_format
            self.succeeded.emit(report)
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class PaintWindow(QtWidgets.QMainWindow):
    """Face-based painting, durable projects and background printable export."""

    def __init__(self, path=None, *, document: PaintDocument | None = None, parent=None):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.resize(1250, 850)
        self.document = None
        self.project_path = None
        self.dirty = False
        self.tool = "Navigate"
        self.paint_rgba = np.array([0, 190, 220, 255], dtype=np.uint8)
        self._polys = {}
        self._actors = {}
        self._topology = {}
        self._selection = {}
        self._selection_operation = "add"
        self._stroke = {}
        self._undo = []
        self._redo = []
        self._mapping = {}
        self._mapping_mode = "source"
        self._export_thread = None
        self._export_worker = None
        self.last_export_report = None
        self._build_ui()
        self.picker = vtk.vtkCellPicker()
        self.picker.SetTolerance(0.001)
        self.picker.PickFromListOn()
        self._update_title()
        if document is not None:
            self.set_document(document)
        elif path:
            QtCore.QTimer.singleShot(0, lambda: self.open_path(path))

    def _build_ui(self):
        toolbar = self.addToolBar("Paint project")
        toolbar.setMovable(False)
        self.open_action = toolbar.addAction("Open…", self.open_dialog)
        self.save_action = toolbar.addAction("Save paint…", self.save_dialog)
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(self.open_action)
        file_menu.addAction(self.save_action)
        self.surface_export_action = file_menu.addAction("Export surface colors as 3MF…", self.export_surface_dialog)
        toolbar.addSeparator()
        self.undo_action = toolbar.addAction("Undo", self.undo)
        self.undo_action.setShortcut(QtGui.QKeySequence.Undo)
        self.redo_action = toolbar.addAction("Redo", self.redo)
        self.redo_action.setShortcut(QtGui.QKeySequence.Redo)
        toolbar.addSeparator()
        self.export_action = toolbar.addAction("Export printable 3MF…", self.export_dialog)
        toolbar.addSeparator()
        toolbar.addAction("Fit view", self.fit_view)
        toolbar.addAction("Top", lambda: self.view("top"))
        toolbar.addAction("Front", lambda: self.view("front"))
        toolbar.addAction("Isometric", lambda: self.view("iso"))

        split = QtWidgets.QSplitter()
        self.setCentralWidget(split)
        side = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(side)
        title = QtWidgets.QLabel("Surface paint")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(title)
        self.model_label = QtWidgets.QLabel("Open a model to begin.")
        self.model_label.setWordWrap(True)
        layout.addWidget(self.model_label)
        self.tool_combo = QtWidgets.QComboBox()
        self.tool_combo.setObjectName("paintTool")
        self.tool_combo.addItems(["Navigate", "Surface select", "Area select", "Color region select",
                                  "Brush", "Connected fill", "Object fill", "Erase"])
        self.tool_combo.currentTextChanged.connect(self._set_tool)
        layout.addWidget(self.tool_combo)
        form = QtWidgets.QFormLayout()
        self.radius_spin = QtWidgets.QDoubleSpinBox()
        self.radius_spin.setObjectName("paintRadius")
        self.radius_spin.setRange(0.001, 100000)
        self.radius_spin.setDecimals(3)
        self.radius_spin.setValue(2.0)
        self.radius_spin.setSuffix(" mm")
        form.addRow("Brush radius", self.radius_spin)
        self.color_button = QtWidgets.QPushButton()
        self.color_button.setObjectName("paintColor")
        self.color_button.clicked.connect(self.choose_color)
        form.addRow("Paint color", self.color_button)
        self.edge_angle_spin = QtWidgets.QDoubleSpinBox()
        self.edge_angle_spin.setObjectName("surfaceEdgeAngle")
        self.edge_angle_spin.setRange(0, 180)
        self.edge_angle_spin.setValue(35)
        self.edge_angle_spin.setSuffix("°")
        self.edge_angle_spin.setToolTip("Stop surface selection at a crease greater than this angle. Click again after changing it.")
        form.addRow("Surface edge angle", self.edge_angle_spin)
        self.seed_angle_spin = QtWidgets.QDoubleSpinBox()
        self.seed_angle_spin.setObjectName("surfaceNormalSpan")
        self.seed_angle_spin.setRange(0, 89)
        self.seed_angle_spin.setValue(60)
        self.seed_angle_spin.setSuffix("°")
        self.seed_angle_spin.setToolTip("Maximum normal change from the picked face. Limits spread around curved objects; always below90°.")
        form.addRow("Surface span", self.seed_angle_spin)
        layout.addLayout(form)
        selection_row = QtWidgets.QHBoxLayout()
        self.apply_selection_button = QtWidgets.QPushButton("Apply paint")
        self.apply_selection_button.setObjectName("applySelectedPaint")
        self.apply_selection_button.clicked.connect(self.paint_selection)
        self.clear_selection_button = QtWidgets.QPushButton("Clear selection")
        self.clear_selection_button.setObjectName("clearPaintSelection")
        self.clear_selection_button.clicked.connect(self.clear_selection)
        selection_row.addWidget(self.apply_selection_button)
        selection_row.addWidget(self.clear_selection_button)
        layout.addLayout(selection_row)
        self.selection_label = QtWidgets.QLabel("No faces selected")
        layout.addWidget(self.selection_label)
        quick = QtWidgets.QHBoxLayout()
        for color in ["#00FFFF", "#FF00FF", "#FFFF00", "#FFFFFF", "#202020", "#FF4040"]:
            button = QtWidgets.QPushButton()
            button.setFixedSize(32, 26)
            button.setToolTip(color)
            button.setStyleSheet(f"background: {color}; border: 1px solid #777;")
            button.clicked.connect(lambda _checked=False, hx=color: self.set_paint_color(hx))
            quick.addWidget(button)
        quick.addStretch()
        layout.addLayout(quick)
        hint = QtWidgets.QLabel(
            "Click a surface or drag an area, then Apply paint. Shift adds; Ctrl removes. "
            "Alt+left rotates; middle pans; right zooms. Color region follows connected "
            "paint, including hidden faces. Erase restores imported colors."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(hint)
        layout.addSpacing(16)
        layout.addWidget(QtWidgets.QLabel("Printable color mapping"))
        self.palette_combo = QtWidgets.QComboBox()
        self.palette_combo.setObjectName("paintPalette")
        self.palette_combo.addItems(list(PALETTE_FILES))
        self.palette_combo.setCurrentText("Normal")
        self.palette_combo.currentTextChanged.connect(self._palette_changed)
        layout.addWidget(self.palette_combo)
        self.mapping_mode_combo = QtWidgets.QComboBox()
        self.mapping_mode_combo.setObjectName("paintMappingMode")
        self.mapping_mode_combo.addItem("Paint colors — one filament each", "source")
        self.mapping_mode_combo.addItem("Woven apparent colors", "woven")
        self.mapping_mode_combo.addItem("Direct physical filament colors", "direct")
        self.mapping_mode_combo.currentIndexChanged.connect(self._palette_changed)
        layout.addWidget(self.mapping_mode_combo)
        self.map_button = QtWidgets.QPushButton("Assign colors to palette")
        self.map_button.setObjectName("assignPaintPalette")
        self.map_button.clicked.connect(self.assign_colors)
        layout.addWidget(self.map_button)
        self.preview_mapping = QtWidgets.QCheckBox("Preview mapped colors")
        self.preview_mapping.toggled.connect(self._mapping_preview_changed)
        layout.addWidget(self.preview_mapping)
        self.mapping_table = QtWidgets.QTableWidget(0, 3)
        self.mapping_table.setHorizontalHeaderLabels(["Paint", "Mapped", "Recipe"])
        self.mapping_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.mapping_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.mapping_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.mapping_table.cellClicked.connect(self._mapping_color_clicked)
        layout.addWidget(self.mapping_table, 1)
        self.mapping_label = QtWidgets.QLabel("Assign colors before woven export.")
        self.mapping_label.setWordWrap(True)
        layout.addWidget(self.mapping_label)
        split.addWidget(side)
        self.plotter = PaintInteractor(self, split)
        self.plotter.setObjectName("paintViewport")
        split.addWidget(self.plotter)
        split.setSizes([325, 925])
        side.setMinimumWidth(300)
        side.setMaximumWidth(450)
        self.plotter.set_background("#222831")
        self.plotter.show_axes()
        self.progress = QtWidgets.QProgressBar()
        self.progress.setMaximumWidth(240)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().showMessage("Open a painted model or a mesh to begin.")
        self.set_paint_color(_hex(self.paint_rgba))
        self._sync_mapping_controls()
        self._update_actions()

    def _set_tool(self, tool):
        self.end_stroke()
        self.tool = tool
        self.plotter.setCursor(QtCore.Qt.ArrowCursor if tool == "Navigate" else QtCore.Qt.CrossCursor)
        self.edge_angle_spin.setEnabled(tool == "Surface select")
        self.seed_angle_spin.setEnabled(tool == "Surface select")
        self.radius_spin.setEnabled(tool in {"Brush", "Erase", "Area select"})
        self.statusBar().showMessage(f"{tool} · Alt+left rotates, middle pans, right zooms")

    def set_paint_color(self, hx):
        self.paint_rgba = np.append(_rgb(hx), np.uint8(255)).astype(np.uint8)
        self.color_button.setText(hx.upper())
        foreground = "#111" if self.paint_rgba[:3].mean() > 140 else "#fff"
        self.color_button.setStyleSheet(f"background: {hx}; color: {foreground}; padding: 5px;")

    def choose_color(self):
        color = QtWidgets.QColorDialog.getColor(QtGui.QColor(_hex(self.paint_rgba)), self, "Paint color")
        if color.isValid():
            self.set_paint_color(color.name())

    def _confirm_discard(self):
        if not self.dirty:
            return True
        answer = QtWidgets.QMessageBox.question(
            self, "Unsaved paint", "Save your paint changes before continuing?",
            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Save,
        )
        if answer == QtWidgets.QMessageBox.Cancel:
            return False
        return self.save_dialog() if answer == QtWidgets.QMessageBox.Save else True

    def open_dialog(self):
        if not self._confirm_discard():
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open model or paint project", "", MODEL_FILTER)
        if path:
            self.open_path(path)

    def open_path(self, path):
        self.statusBar().showMessage(f"Reading {Path(path).name}…")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        QtWidgets.QApplication.processEvents()
        try:
            document = load_paint_document(str(path))
            self.set_document(document)
            self.project_path = str(path) if Path(path).suffix.lower() == ".llpaint" else None
            self._update_title()
            self.statusBar().showMessage(f"Opened {Path(path).name}")
            warnings = document.metadata.get("warnings", [])
            if warnings:
                self.statusBar().showMessage(" · ".join(str(w) for w in warnings[:3]))
            return True
        except Exception as exc:
            traceback.print_exc()
            QtWidgets.QMessageBox.critical(self, "Open failed", str(exc))
            return False
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def set_document(self, document):
        document.validate()
        self.document = document
        self.project_path = None
        self.dirty = False
        self._undo.clear()
        self._redo.clear()
        self._stroke.clear()
        self._topology.clear()
        self._selection.clear()
        self._mapping.clear()
        self._polys.clear()
        self._actors.clear()
        self.mapping_table.setRowCount(0)
        self.preview_mapping.setChecked(False)
        self.plotter.clear()
        self.plotter.enable_lightkit()
        self.plotter.show_axes()
        self.picker.InitializePickList()
        for index, mesh in enumerate(document.meshes):
            cells = np.column_stack([np.full(len(mesh.faces), 3), mesh.faces]).ravel()
            poly = pv.PolyData(mesh.vertices.copy(), cells)
            poly.cell_data["paint_rgba"] = mesh.face_rgba.copy()
            poly.cell_data["source_face_id"] = np.arange(len(mesh.faces), dtype=np.int64)
            actor = self.plotter.add_mesh(
                poly, scalars="paint_rgba", rgba=True, preference="cell", name=f"paint_{index}",
                show_scalar_bar=False, smooth_shading=False, ambient=0.4, diffuse=0.6,
                specular=0.0, pickable=True, reset_camera=False,
            )
            self._polys[index] = poly
            self._actors[index] = actor
            self.picker.AddPickList(actor)
        size = (np.max([m.vertices.max(axis=0) for m in document.meshes], axis=0)
                - np.min([m.vertices.min(axis=0) for m in document.meshes], axis=0))
        face_count = sum(len(m.faces) for m in document.meshes)
        self.model_label.setText(
            f"{Path(document.source_path).name if document.source_path else 'Paint project'}\n"
            f"{len(document.meshes)} part(s) · {face_count:,} faces\n"
            f"{size[0]:.1f} × {size[1]:.1f} × {size[2]:.1f} mm"
        )
        self.radius_spin.setValue(max(float(size.max()) / 30.0, 0.01))
        palette = document.metadata.get("paint_palette", "Normal")
        if palette in PALETTE_FILES:
            blocker = QtCore.QSignalBlocker(self.palette_combo)
            self.palette_combo.setCurrentText(palette)
            del blocker
        mode = document.metadata.get("paint_mapping_mode", "source")
        blocker = QtCore.QSignalBlocker(self.mapping_mode_combo)
        self.mapping_mode_combo.setCurrentIndex(max(0, self.mapping_mode_combo.findData(mode)))
        del blocker
        self._mapping_mode = self.mapping_mode_combo.currentData()
        self._sync_mapping_controls()
        self._restore_saved_mapping()
        self.fit_view()
        self._update_actions()
        self._update_title()

    def fit_view(self):
        self.plotter.view_isometric()
        self.plotter.reset_camera()
        self.plotter.render()

    def view(self, direction):
        if direction == "top":
            self.plotter.view_xy()
        elif direction == "front":
            self.plotter.view_xz()
        else:
            self.plotter.view_isometric()
        self.plotter.render()

    def pick_at(self, pos):
        if self.document is None:
            return None
        ratio = float(self.plotter.devicePixelRatioF())
        x, y = pos.x() * ratio, (self.plotter.height() - pos.y() - 1) * ratio
        if not self.picker.Pick(x, y, 0.0, self.plotter.renderer):
            return None
        actor, cell = self.picker.GetActor(), int(self.picker.GetCellId())
        for index, candidate in self._actors.items():
            if actor == candidate and 0 <= cell < len(self.document.meshes[index].faces):
                return index, cell, np.asarray(self.picker.GetPickPosition())
        return None

    def _mesh_topology(self, index):
        if index not in self._topology:
            mesh = self.document.meshes[index]
            self._topology[index] = build_topology(mesh.vertices, mesh.faces)
        return self._topology[index]

    def affected_faces(self, index, seed, point):
        mesh = self.document.meshes[index]
        if self.tool == "Object fill":
            return np.arange(len(mesh.faces), dtype=np.int64)
        topology = self._mesh_topology(index)
        if self.tool == "Connected fill":
            return select_color_region(topology, seed, mesh.face_rgba)
        return select_area(topology, seed, point, self.radius_spin.value())

    def begin_selection(self, modifiers=QtCore.Qt.NoModifier):
        self.end_stroke()
        if modifiers & QtCore.Qt.ControlModifier:
            self._selection_operation = "subtract"
        else:
            self._selection_operation = "add"
            if not modifiers & QtCore.Qt.ShiftModifier:
                self.clear_selection()

    def select_at(self, pos):
        hit = self.pick_at(pos)
        if hit is None:
            return
        index, seed, point = hit
        topology = self._mesh_topology(index)
        if self.tool == "Surface select":
            faces = select_surface(topology, seed, edge_angle=self.edge_angle_spin.value(),
                                   seed_angle=self.seed_angle_spin.value())
        elif self.tool == "Color region select":
            faces = select_color_region(topology, seed, self.document.meshes[index].face_rgba)
        else:
            faces = select_area(topology, seed, point, self.radius_spin.value())
        selection = self._selection.setdefault(index, np.zeros(len(topology.normals), dtype=bool))
        selection[faces] = self._selection_operation != "subtract"
        self._update_mesh_colors(index)
        self._update_selection_controls()
        self.plotter.render()

    def _update_selection_controls(self):
        count = sum(int(mask.sum()) for mask in self._selection.values())
        self.selection_label.setText(f"{count:,} faces selected" if count else "No faces selected")
        self.apply_selection_button.setEnabled(bool(count))
        self.clear_selection_button.setEnabled(bool(count))

    def clear_selection(self):
        indices = list(self._selection)
        self._selection.clear()
        for index in indices:
            if index in self._polys:
                self._update_mesh_colors(index)
        self._update_selection_controls()
        self.plotter.render()

    def paint_selection(self):
        if self.document is None:
            return
        self.begin_stroke()
        count = 0
        for index, mask in self._selection.items():
            ids = np.flatnonzero(mask)
            count += len(ids)
            self.apply_faces(index, ids)
        self.end_stroke()
        self.clear_selection()
        self.statusBar().showMessage(f"Applied paint to {count:,} selected faces.")

    def begin_stroke(self):
        self._stroke = {}

    def paint_at(self, pos):
        hit = self.pick_at(pos)
        if hit is None:
            return
        index, seed, point = hit
        self.apply_faces(index, self.affected_faces(index, seed, point), erase=self.tool == "Erase")

    def apply_faces(self, index, face_ids, *, erase=False):
        """Apply one stroke sample; used by pointer handling and focused tests."""
        if self.document is None:
            return
        mesh = self.document.meshes[index]
        face_ids = np.asarray(face_ids, dtype=np.int64)
        replacement = mesh.source_rgba[face_ids] if erase else np.tile(self.paint_rgba, (len(face_ids), 1))
        changed = np.any(mesh.face_rgba[face_ids] != replacement, axis=1)
        face_ids, replacement = face_ids[changed], replacement[changed]
        before = self._stroke.setdefault(index, {})
        for face in face_ids:
            before.setdefault(int(face), mesh.face_rgba[face].copy())
        mesh.face_rgba[face_ids] = replacement
        if len(face_ids):
            self._update_mesh_colors(index, mapped=False)
            self.plotter.render()

    def end_stroke(self):
        if self.document is None or not self._stroke:
            return
        command = {}
        for index, before in self._stroke.items():
            if before:
                ids = np.asarray(list(before), dtype=np.int64)
                command[index] = (ids, np.asarray(list(before.values())), self.document.meshes[index].face_rgba[ids].copy())
        self._stroke = {}
        if command:
            self._undo.append(command)
            self._redo.clear()
            self._after_edit()

    def undo(self):
        self.end_stroke()
        if self._undo:
            command = self._undo.pop()
            for index, (ids, before, _after) in command.items():
                self.document.meshes[index].face_rgba[ids] = before
            self._redo.append(command)
            self._after_edit()

    def redo(self):
        if self._redo:
            command = self._redo.pop()
            for index, (ids, _before, after) in command.items():
                self.document.meshes[index].face_rgba[ids] = after
            self._undo.append(command)
            self._after_edit()

    def _after_edit(self):
        self.dirty = True
        self._mapping = {}
        self.document.metadata.pop("paint_mapping", None)
        self.mapping_table.setRowCount(0)
        self.mapping_label.setText("Paint changed. Assign colors to refresh the mapping.")
        self.preview_mapping.setChecked(False)
        for index in self._polys:
            self._update_mesh_colors(index, mapped=False)
        self.plotter.render()
        self._update_actions()
        self._update_title()

    def _update_mesh_colors(self, index, *, mapped=None):
        mesh = self.document.meshes[index]
        colors = mesh.face_rgba.copy()
        if mapped is None:
            mapped = self.preview_mapping.isChecked()
        if mapped and self._mapping:
            unique, inverse = np.unique(colors[:, :3], axis=0, return_inverse=True)
            targets = np.array([_rgb(self._mapping[_hex(rgb)]["hex"]) for rgb in unique])
            colors[:, :3] = targets[inverse]
        selected = self._selection.get(index)
        if selected is not None and selected.any():
            colors[selected, :3] = (colors[selected, :3].astype(float) * .4
                                    + np.array([255, 215, 50]) * .6).astype(np.uint8)
            colors[selected, 3] = 255
        self._polys[index].cell_data["paint_rgba"] = colors
        self._polys[index].Modified()

    def _palette_changed(self, _value):
        self._mapping = {}
        self.mapping_table.setRowCount(0)
        self.preview_mapping.setChecked(False)
        self._mapping_mode = self.mapping_mode_combo.currentData()
        self._sync_mapping_controls()
        if self.document is not None:
            self.document.metadata["paint_palette"] = self.palette_combo.currentText()
            self.document.metadata["paint_mapping_mode"] = self._mapping_mode
            self.document.metadata.pop("paint_mapping", None)
            self.dirty = True
            self._update_title()

    def _sync_mapping_controls(self):
        source = self.mapping_mode_combo.currentData() == "source"
        self.palette_combo.setEnabled(not source)
        self.map_button.setText("Use paint colors exactly" if source else "Assign colors to palette")
        self.preview_mapping.setEnabled(not source)

    def _restore_saved_mapping(self):
        """Restore only recipes valid for both current paint and selected palette."""
        stored = self.document.metadata.get("paint_mapping")
        self.mapping_label.setText("Assign colors to preview the selected print mode.")
        if not isinstance(stored, dict) or not stored:
            return
        try:
            source_keys = {_hex(rgb) for rgb in np.unique(np.concatenate(
                [m.face_rgba[:, :3] for m in self.document.meshes]), axis=0)}
            eligible = ({entry["token"]: entry for entry in load_palette_entries(self.palette_combo.currentText())
                         if self._mapping_mode == "woven" or len(entry["token"]) == 1}
                        if self._mapping_mode != "source" else {})
            mapping = {str(key).upper(): dict(value) for key, value in stored.items()}
            if set(mapping) != source_keys:
                raise ValueError("saved mapping does not match paint colors")
            for key, entry in mapping.items():
                if self._mapping_mode == "source":
                    if entry.get("token") != key or str(entry.get("hex", "")).upper() != key:
                        raise ValueError("saved source mapping changed paint colors")
                    continue
                expected = eligible.get(entry.get("token"))
                if expected is None or str(entry.get("hex", "")).upper() != expected["hex"].upper():
                    raise ValueError("saved recipe does not match the selected palette")
            self._mapping = mapping
            self._populate_mapping_table()
        except (ValueError, TypeError, KeyError):
            self.document.metadata.pop("paint_mapping", None)
            self.mapping_label.setText("Saved mapping no longer matches the paint. Assign colors to refresh it.")

    def _populate_mapping_table(self):
        items = sorted(self._mapping.items())
        source_mode = self._mapping_mode == "source"
        self.mapping_table.setHorizontalHeaderLabels(["Paint", "Output", "Slot" if source_mode else "Recipe"])
        self.mapping_table.setRowCount(min(len(items), 1000))
        for row, (source, entry) in enumerate(items[:1000]):
            for col, value in enumerate([source, entry["hex"], str(row + 1) if source_mode else entry["token"]]):
                item = QtWidgets.QTableWidgetItem(str(value))
                if col < 2:
                    item.setBackground(QtGui.QColor(str(value)))
                    rgb = _rgb(str(value))
                    item.setForeground(QtGui.QColor("#111" if rgb.mean() > 140 else "#fff"))
                self.mapping_table.setItem(row, col, item)
        recipes = len({entry["token"] for entry in self._mapping.values()})
        if source_mode:
            self.mapping_label.setText(f"{len(items):,} paint colors → {len(items):,} filament slots. "
                                      "Each paint color is preserved in the file. Click a row to reuse it.")
            return
        filaments = sorted({token for entry in self._mapping.values() for token in entry["token"]})
        mode = "woven recipes" if self._mapping_mode == "woven" else "physical filaments"
        self.mapping_label.setText(
            f"{len(items):,} paint colors → {recipes:,} {mode}. "
            + f"Requires {len(filaments)} physical filaments: {', '.join(t.upper() for t in filaments)}. "
            + ("Showing first 1,000 colors. " if len(items) > 1000 else "")
            + "Click a row to reuse its paint color."
        )

    def assign_colors(self):
        if self.document is None:
            return False
        try:
            from .paint_export import assign_palette
            self._mapping_mode = self.mapping_mode_combo.currentData()
            self._mapping = assign_palette(self.document, self.palette_combo.currentText(), mode=self._mapping_mode)
            self._populate_mapping_table()
            self.document.metadata["paint_palette"] = self.palette_combo.currentText()
            self.document.metadata["paint_mapping_mode"] = self._mapping_mode
            self.document.metadata["paint_mapping"] = self._mapping
            self.dirty = True
            self._update_title()
            self._mapping_preview_changed(self.preview_mapping.isChecked())
            return True
        except Exception as exc:
            traceback.print_exc()
            QtWidgets.QMessageBox.critical(self, "Palette mapping failed", str(exc))
            return False

    def _mapping_color_clicked(self, row, _column):
        item = self.mapping_table.item(row, 0)
        if item:
            self.set_paint_color(item.text())

    def _mapping_preview_changed(self, enabled):
        if enabled and not self._mapping and not self.assign_colors():
            self.preview_mapping.setChecked(False)
            return
        for index in self._polys:
            self._update_mesh_colors(index, mapped=enabled)
        self.plotter.render()

    def save_dialog(self):
        if self.document is None:
            return False
        self.end_stroke()
        default = self.project_path or str(Path(self.document.source_path or "painted_model").with_suffix(".llpaint"))
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save editable paint project", default, "LayerLoom paint (*.llpaint)")
        if not path:
            return False
        if not path.lower().endswith(".llpaint"):
            path += ".llpaint"
        try:
            save_paint_document(self.document, path)
            self.project_path = path
            self.dirty = False
            self._update_title()
            self.statusBar().showMessage(f"Saved {path}")
            return True
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save failed", str(exc))
            return False

    def export_surface_dialog(self):
        if self.document is None:
            return
        self.end_stroke()
        default = str(Path(self.project_path or self.document.source_path or "painted_model").with_suffix("")) + "_surface_colors.3mf"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export surface colors", default, "3MF (*.3mf)")
        if not path:
            return
        if not path.lower().endswith(".3mf"):
            path += ".3mf"
        try:
            save_paint_document(self.document, path)
            self.statusBar().showMessage(f"Surface-colored 3MF saved: {path}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Surface color export failed", str(exc))

    def export_dialog(self):
        if self.document is None or self._export_thread is not None:
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Export painted model for printing")
        layout = QtWidgets.QFormLayout(dialog)
        mode = QtWidgets.QComboBox()
        mode.addItem("Paint colors — one filament each", "source")
        mode.addItem("Woven apparent colors", "woven")
        mode.addItem("Direct physical filament colors", "direct")
        mode.setCurrentIndex(max(0, mode.findData(self.mapping_mode_combo.currentData())))
        export_format = QtWidgets.QComboBox()
        export_format.addItem("Native painted 3MF", "native")
        export_format.addItem("Material volumes", "volumes")
        export_format.setCurrentIndex(0 if mode.currentData() == "source" else 1)
        layer = QtWidgets.QDoubleSpinBox()
        layer.setDecimals(3)
        layer.setRange(0.04, 2.0)
        layer.setSingleStep(0.04)
        layer.setValue(0.2)
        layer.setSuffix(" mm")
        pitch = QtWidgets.QDoubleSpinBox()
        pitch.setDecimals(3)
        pitch.setRange(0.05, 10.0)
        pitch.setSingleStep(0.1)
        pitch.setValue(0.5)
        pitch.setSuffix(" mm")
        layout.addRow("Color mode", mode)
        layout.addRow("3MF representation", export_format)
        layout.addRow("Layer height", layer)
        layout.addRow("XY sampling size", pitch)
        note = QtWidgets.QLabel()
        note.setWordWrap(True)
        note.setMaximumWidth(440)
        layout.addRow(note)
        def update_export_options():
            native_available = mode.currentData() == "source"
            export_format.model().item(0).setEnabled(native_available)
            if not native_available:
                export_format.setCurrentIndex(1)
            native = export_format.currentData() == "native"
            layer.setEnabled(not native)
            pitch.setEnabled(not native)
            note.setText(
                "Preserves mesh detail and current face paint using slicer-native color assignments. "
                "Each source color receives its own filament slot. Closed solids are required; "
                "the assembly is centered and grounded."
                if native else
                "Paint extends through the full volume from the nearest surface; there is no "
                "shell-depth control. The exported exterior follows discrete layer steps. "
                "Smaller sampling sizes retain more detail and take longer. Use the chosen "
                "layer height in your slicer. Closed solids are required. The assembly is "
                "centered and grounded."
            )
        mode.currentIndexChanged.connect(update_export_options)
        export_format.currentIndexChanged.connect(update_export_options)
        update_export_options()
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        self.mapping_mode_combo.setCurrentIndex(self.mapping_mode_combo.findData(mode.currentData()))
        if not self._mapping and not self.assign_colors():
            return
        self.preview_mapping.setChecked(mode.currentData() != "source")
        default = str(Path(self.project_path or self.document.source_path or "painted_model").with_suffix("")) + "_painted_print.3mf"
        output, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save printable model", default, "3MF (*.3mf)")
        if not output:
            return
        if not output.lower().endswith(".3mf"):
            output += ".3mf"
        self.start_export(output, mode=mode.currentData(), layer_height=layer.value(), pitch=pitch.value(),
                          palette_name=self.palette_combo.currentText(), export_format=export_format.currentData())

    def start_export(self, output, **settings):
        """Export a snapshot; editing may continue while conversion runs."""
        if self.document is None or self._export_thread is not None:
            return False
        self.end_stroke()
        self.last_export_report = None
        if (self._mapping and self._mapping_mode != "source" and settings.get("mode", "woven") == self._mapping_mode
                and settings.get("palette_name", "Normal") == self.palette_combo.currentText()):
            settings.setdefault("recipe_overrides", {key: entry["token"] for key, entry in self._mapping.items()})
        thread = QtCore.QThread(self)
        worker = ExportWorker(self.document.copy(), str(output), settings)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._export_progress)
        worker.succeeded.connect(self._export_success)
        worker.failed.connect(self._export_failure)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._export_finished)
        thread.finished.connect(thread.deleteLater)
        self._export_thread, self._export_worker = thread, worker
        self.progress.setRange(0, 0)
        self.progress.show()
        self.export_action.setEnabled(False)
        self.statusBar().showMessage("Converting a snapshot of the painted model…")
        thread.start()
        return True

    @QtCore.Slot(int, int)
    def _export_progress(self, current, total):
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(current)

    @QtCore.Slot(object)
    def _export_success(self, report):
        self.last_export_report = report
        path = report.get("output_path", report.get("output_3mf", ""))
        self.statusBar().showMessage(f"Printable export complete. {path}")
        advice = ("Inspect the assigned colors in your slicer." if report.get("export_format") == "native"
                  else "Inspect it in your slicer using the selected layer height.")
        QtWidgets.QMessageBox.information(self, "Export complete", f"Printable 3MF written.\n{path}\n\n{advice}")

    @QtCore.Slot(str)
    def _export_failure(self, message):
        self.statusBar().showMessage("Printable export failed; paint project is unchanged.")
        QtWidgets.QMessageBox.critical(self, "Printable export failed", message)

    @QtCore.Slot()
    def _export_finished(self):
        self._export_thread = None
        self._export_worker = None
        self.progress.hide()
        self._update_actions()

    def _update_actions(self):
        loaded = self.document is not None
        self.save_action.setEnabled(loaded)
        self.surface_export_action.setEnabled(loaded)
        self.export_action.setEnabled(loaded and self._export_thread is None)
        self.undo_action.setEnabled(bool(self._undo))
        self.redo_action.setEnabled(bool(self._redo))
        self.map_button.setEnabled(loaded)
        self._update_selection_controls()

    def _update_title(self):
        path = self.project_path or (self.document.source_path if self.document else None)
        name = Path(path).name if path else "New project"
        self.setWindowTitle(f"LayerLoom Paint — {name}{' *' if self.dirty else ''}")

    def can_close(self):
        if self._export_thread is not None:
            QtWidgets.QMessageBox.information(self, "Export in progress", "Wait for the current printable export to finish before closing.")
            return False
        return self._confirm_discard()

    def shutdown(self):
        """Dispose the viewer after the owner has accepted can_close()."""
        self.plotter.close()

    def closeEvent(self, event):
        if not self.can_close():
            event.ignore()
            return
        self.shutdown()
        event.accept()


class PaintPanel(QtWidgets.QWidget):
    """Reusable main-UI paint surface; never creates a detached editor window.

    ``editor`` exposes tool controls and the complete painting API. Main-window
    owners should call ``can_close()`` before closing and ``shutdown()`` once
    the close is accepted. Tab switching does not dispose or reload its state.
    """

    def __init__(self, parent=None, path=None, *, document=None):
        super().__init__(parent)
        self._disposed = False
        self.editor = PaintWindow(path, document=document, parent=self)
        self.editor.setWindowFlags(QtCore.Qt.Widget)
        self.editor.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        self.editor.menuBar().setNativeMenuBar(False)
        self.editor.menuBar().hide()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.editor)
        self.editor.tool_combo.setCurrentText("Surface select")

    @property
    def document(self):
        return self.editor.document

    def open_path(self, path):
        return self.editor.open_path(path)

    def set_document(self, document):
        return self.editor.set_document(document)

    def can_close(self):
        return self.editor.can_close()

    def shutdown(self):
        if not self._disposed:
            self.editor.shutdown()
            self._disposed = True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="3MF, GLB, mesh, or .llpaint project")
    args = parser.parse_args(argv)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("LayerLoom Paint")
    window = PaintWindow(args.path)
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
