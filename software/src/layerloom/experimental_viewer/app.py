"""Qt + PyVista build-plate viewer prototype."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import numpy as np

from .loader import LoadedMesh, LoadedScene, load_model
from .transform import BuildPlateSpec, TransformController


try:
    from qtpy import QtCore, QtGui, QtWidgets
    import pyvista as pv
    from pyvistaqt import QtInteractor

    _GUI_OK = True
except Exception as exc:  # pragma: no cover - exercised only without GUI deps
    _GUI_IMPORT_ERROR = exc
    _GUI_OK = False

    class _MissingSignal:
        def connect(self, *_args, **_kwargs):
            return None

        def emit(self, *_args, **_kwargs):
            return None

    class _MissingQtCore:
        class QObject:
            pass

        class QThread:
            pass

        class Qt:
            ShiftModifier = 0
            Horizontal = 1

        @staticmethod
        def Signal(*_args, **_kwargs):
            return _MissingSignal()

        @staticmethod
        def Slot(*_args, **_kwargs):
            def decorator(func):
                return func

            return decorator

    class _MissingQtGui:
        class QColor:
            def __init__(self, *_args, **_kwargs):
                pass

            def getRgbF(self):
                return (0.0, 0.0, 0.0, 1.0)

    class _MissingQtWidgets:
        class QMainWindow:
            pass

        class QApplication:
            @staticmethod
            def keyboardModifiers():
                return 0

    QtCore = _MissingQtCore()
    QtGui = _MissingQtGui()
    QtWidgets = _MissingQtWidgets()
    pv = None
    QtInteractor = None


def _require_gui() -> None:
    if not _GUI_OK:
        raise RuntimeError(
            "The experimental viewer requires LayerLoom GUI dependencies. "
            'Install with: python -m pip install -e ".[gui]"'
        ) from _GUI_IMPORT_ERROR


def _vtk_module():
    try:
        import vtk

        return vtk
    except Exception as exc:  # pragma: no cover - dependency failure path
        raise RuntimeError("vtk is required for interactive picking.") from exc


class ModelLoadWorker(QtCore.QObject):
    finished = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, path: str):
        super().__init__()
        self.path = path

    @QtCore.Slot()
    def run(self) -> None:
        try:
            self.finished.emit(load_model(self.path))
        except Exception as exc:
            self.failed.emit(str(exc))


class ViewerMouseEventFilter(QtCore.QObject):
    """Qt-side fallback for mouse releases that VTK observers can miss."""

    def __init__(self, owner: "ExperimentalViewerWindow"):
        super().__init__(owner)
        self.owner = owner

    def eventFilter(self, obj, event):  # noqa: N802 - Qt API name
        try:
            event_type = event.type()
            if event_type == QtCore.QEvent.KeyPress and event.key() == QtCore.Qt.Key_Escape:
                self.owner._handle_escape()
                return True
            if event_type == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.RightButton:
                if self.owner._handle_tool_cancel():
                    return True
            if event_type == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.LeftButton:
                self.owner._finish_pointer_interaction()
            elif event_type == QtCore.QEvent.Leave and not self.owner._left_button_is_down():
                self.owner._finish_pointer_interaction()
        except Exception:
            pass
        return False


class ExperimentalViewerWindow(QtWidgets.QMainWindow):
    def __init__(self, initial_path: Optional[str] = None):
        _require_gui()
        super().__init__()
        self.setWindowTitle("LayerLoom Experimental Viewer")
        self.resize(1360, 860)
        self.setMinimumSize(960, 640)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)

        self.plate = BuildPlateSpec()
        self.scene: Optional[LoadedScene] = None
        self.controller: Optional[TransformController] = None
        self.model_actors = []
        self.gimbal_actors = {}
        self.selection_outline_actor = None
        self._meshes: list[LoadedMesh] = []
        self._loading_thread = None
        self._loading_worker = None
        self._picker = None
        self._observer_ids = []
        self._event_filter = None
        self._syncing_fields = False
        self._mode = "normal"
        self._active_move = None
        self._active_rotation = None
        self._camera_drag_forwarded = False
        self._cursor_kind = "default"
        self._normal_click_press = None
        self.selected_mesh_index: Optional[int] = None

        self._build_ui()
        self._configure_plotter()
        self._set_controls_enabled(False)
        if initial_path:
            QtCore.QTimer.singleShot(0, lambda: self.load_path(initial_path))

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow { background: #202124; color: #eef0f2; }
            QWidget { color: #eef0f2; font-size: 12px; }
            QFrame#sidePanel { background: #25272b; border-left: 1px solid #373b41; }
            QLabel#muted { color: #aeb4bd; }
            QLabel#title { font-size: 15px; font-weight: 700; }
            QToolButton, QPushButton { padding: 5px 9px; border-radius: 5px; background: #343840; border: 1px solid #464b55; }
            QToolButton:hover, QPushButton:hover { background: #3c414a; }
            QToolButton:checked { background: #2d5f87; border-color: #5da3d5; }
            QToolButton:disabled, QPushButton:disabled { color: #777d86; background: #2b2d31; border-color: #363a40; }
            QDoubleSpinBox, QLineEdit { background: #181a1d; border: 1px solid #3d424a; border-radius: 4px; padding: 3px; }
            QGroupBox { border: 1px solid #3b4048; border-radius: 6px; margin-top: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #d7dce2; }
            QStatusBar { background: #1c1d20; color: #cbd1d8; }
            """
        )

        central = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        central.setChildrenCollapsible(False)
        central.setHandleWidth(6)
        self.setCentralWidget(central)

        self.plotter = QtInteractor(self)
        central.addWidget(self.plotter)

        side = QtWidgets.QFrame()
        side.setObjectName("sidePanel")
        side.setMinimumWidth(330)
        side.setMaximumWidth(430)
        central.addWidget(side)
        central.setStretchFactor(0, 1)
        central.setStretchFactor(1, 0)

        layout = QtWidgets.QVBoxLayout(side)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QtWidgets.QLabel("Build-Plate Viewer")
        title.setObjectName("title")
        layout.addWidget(title)
        self.file_label = QtWidgets.QLabel("No model loaded")
        self.file_label.setObjectName("muted")
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)

        self.open_btn = QtWidgets.QPushButton("Open Model")
        self.open_btn.clicked.connect(self._open_dialog)
        layout.addWidget(self.open_btn)

        selected_box = QtWidgets.QGroupBox("Selected Object")
        selected_layout = QtWidgets.QVBoxLayout(selected_box)
        selected_layout.setContentsMargins(10, 12, 10, 10)
        self.selected_name_label = QtWidgets.QLabel("None")
        self.selected_name_label.setObjectName("title")
        self.selected_stats_label = QtWidgets.QLabel("Click any object to select the moving model.")
        self.selected_stats_label.setObjectName("muted")
        self.selected_stats_label.setWordWrap(True)
        selected_layout.addWidget(self.selected_name_label)
        selected_layout.addWidget(self.selected_stats_label)
        layout.addWidget(selected_box)

        action_box = QtWidgets.QGroupBox("Placement")
        action_layout = QtWidgets.QGridLayout(action_box)
        action_layout.setContentsMargins(10, 12, 10, 10)
        self.auto_btn = QtWidgets.QToolButton(text="Auto Place")
        self.fit_btn = QtWidgets.QToolButton(text="Fit To Plate")
        self.center_btn = QtWidgets.QToolButton(text="Center")
        self.drop_btn = QtWidgets.QToolButton(text="Drop")
        self.home_btn = QtWidgets.QToolButton(text="Home View")
        self.reset_btn = QtWidgets.QToolButton(text="Reset Transform")
        self.auto_btn.clicked.connect(self._auto_place)
        self.fit_btn.clicked.connect(self._fit_to_plate)
        self.center_btn.clicked.connect(self._center_on_plate)
        self.drop_btn.clicked.connect(self._drop_to_plate)
        self.home_btn.clicked.connect(self._home_camera)
        self.reset_btn.clicked.connect(self._reset_transform)
        for idx, btn in enumerate(
            (self.auto_btn, self.fit_btn, self.center_btn, self.drop_btn, self.home_btn, self.reset_btn)
        ):
            action_layout.addWidget(btn, idx // 2, idx % 2)
        layout.addWidget(action_box)

        mode_box = QtWidgets.QGroupBox("Mode")
        mode_layout = QtWidgets.QGridLayout(mode_box)
        mode_layout.setContentsMargins(10, 12, 10, 10)
        self.mode_group = QtWidgets.QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.normal_btn = QtWidgets.QToolButton(text="Select / Orbit", checkable=True, checked=True)
        self.move_btn = QtWidgets.QToolButton(text="Move", checkable=True)
        self.rotate_btn = QtWidgets.QToolButton(text="Rotate", checkable=True)
        for idx, (name, btn) in enumerate(
            (
                ("normal", self.normal_btn),
                ("move", self.move_btn),
                ("rotate", self.rotate_btn),
            )
        ):
            self.mode_group.addButton(btn)
            btn.clicked.connect(lambda _checked=False, mode=name: self._set_mode(mode))
            mode_layout.addWidget(btn, 0, idx)
        layout.addWidget(mode_box)

        transform_box = QtWidgets.QGroupBox("Transform")
        form = QtWidgets.QGridLayout(transform_box)
        form.setContentsMargins(10, 12, 10, 10)
        self.rot_x_spin = self._spin(-9999.0, 9999.0, 0.0, 5.0)
        self.rot_y_spin = self._spin(-9999.0, 9999.0, 0.0, 5.0)
        self.rot_z_spin = self._spin(-9999.0, 9999.0, 0.0, 5.0)
        self.scale_spin = self._spin(0.0001, 1000.0, 1.0, 0.05, decimals=4)
        self.pos_x_spin = self._spin(-100000.0, 100000.0, 0.0, 1.0)
        self.pos_y_spin = self._spin(-100000.0, 100000.0, 0.0, 1.0)
        self.rotation_field_widgets = []
        for row, (label, spin) in enumerate(
            (
                ("Rot X", self.rot_x_spin),
                ("Rot Y", self.rot_y_spin),
                ("Rot Z", self.rot_z_spin),
                ("Scale", self.scale_spin),
                ("Pos X", self.pos_x_spin),
                ("Pos Y", self.pos_y_spin),
            )
        ):
            lbl = QtWidgets.QLabel(label)
            lbl.setObjectName("muted")
            form.addWidget(lbl, row, 0)
            form.addWidget(spin, row, 1)
            if spin in (self.rot_x_spin, self.rot_y_spin, self.rot_z_spin):
                self.rotation_field_widgets.extend((lbl, spin))
        layout.addWidget(transform_box)

        camera_box = QtWidgets.QGroupBox("Camera")
        camera_layout = QtWidgets.QGridLayout(camera_box)
        camera_layout.setContentsMargins(10, 12, 10, 10)
        for idx, (label, view_name) in enumerate(
            (
                ("Top", "top"),
                ("Front", "front"),
                ("Right", "right"),
                ("Left", "left"),
                ("Iso", "iso"),
            )
        ):
            btn = QtWidgets.QToolButton(text=label)
            btn.clicked.connect(lambda _checked=False, view=view_name: self._set_camera_view(view))
            camera_layout.addWidget(btn, idx // 2, idx % 2)
        layout.addWidget(camera_box)

        self.info_label = QtWidgets.QLabel("Open a model to begin.")
        self.info_label.setObjectName("muted")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)
        layout.addStretch(1)

        self.status_bar = QtWidgets.QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

        for spin in (self.rot_x_spin, self.rot_y_spin, self.rot_z_spin):
            spin.valueChanged.connect(self._rotation_fields_changed)
        self.scale_spin.valueChanged.connect(self._scale_field_changed)
        self.pos_x_spin.valueChanged.connect(self._position_fields_changed)
        self.pos_y_spin.valueChanged.connect(self._position_fields_changed)
        self._update_mode_visibility()

    def _spin(self, low: float, high: float, value: float, step: float, *, decimals: int = 3):
        spin = QtWidgets.QDoubleSpinBox()
        spin.setDecimals(decimals)
        spin.setRange(low, high)
        spin.setValue(value)
        spin.setSingleStep(step)
        spin.setKeyboardTracking(False)
        return spin

    def _configure_plotter(self) -> None:
        self.plotter.set_background("#f4f5f7")
        try:
            self.setFocusPolicy(QtCore.Qt.StrongFocus)
            self.plotter.setFocusPolicy(QtCore.Qt.StrongFocus)
        except Exception:
            pass
        try:
            self.plotter.enable_anti_aliasing()
        except Exception:
            pass
        try:
            self.plotter.show_axes()
        except Exception:
            pass
        self._build_plate()
        self._picker = _vtk_module().vtkCellPicker()
        self._picker.SetTolerance(0.0015)
        self._install_interaction_observers()
        self._install_qt_event_filter()
        self._set_camera_view("iso")

    def _build_plate(self) -> None:
        plate = pv.Plane(
            center=(self.plate.width_mm * 0.5, self.plate.depth_mm * 0.5, 0.0),
            direction=(0.0, 0.0, 1.0),
            i_size=self.plate.width_mm,
            j_size=self.plate.depth_mm,
            i_resolution=8,
            j_resolution=8,
        )
        self.plate_actor = self.plotter.add_mesh(
            plate,
            name="__build_plate__",
            color="#d5dae0",
            opacity=0.78,
            show_edges=True,
            edge_color="#9da6b1",
            lighting=False,
            pickable=False,
            reset_camera=False,
        )
        self.grid_actor = self.plotter.add_mesh(
            self._grid_mesh(),
            name="__plate_grid__",
            color="#b5bdc8",
            line_width=1,
            lighting=False,
            pickable=False,
            reset_camera=False,
        )
        self.boundary_actor = self.plotter.add_mesh(
            self._boundary_mesh(self.plate.margin_mm),
            name="__plate_boundary__",
            color="#4d5966",
            line_width=3,
            lighting=False,
            pickable=False,
            reset_camera=False,
        )

    def _grid_mesh(self):
        segments = []
        step = 16.0
        z = 0.03
        x = 0.0
        while x <= self.plate.width_mm + 1e-9:
            segments.append(((x, 0.0, z), (x, self.plate.depth_mm, z)))
            x += step
        y = 0.0
        while y <= self.plate.depth_mm + 1e-9:
            segments.append(((0.0, y, z), (self.plate.width_mm, y, z)))
            y += step
        return self._line_mesh(segments)

    def _boundary_mesh(self, margin: float):
        z = 0.07
        outer = [
            ((0.0, 0.0, z), (self.plate.width_mm, 0.0, z)),
            ((self.plate.width_mm, 0.0, z), (self.plate.width_mm, self.plate.depth_mm, z)),
            ((self.plate.width_mm, self.plate.depth_mm, z), (0.0, self.plate.depth_mm, z)),
            ((0.0, self.plate.depth_mm, z), (0.0, 0.0, z)),
        ]
        m = margin
        inner = [
            ((m, m, z), (self.plate.width_mm - m, m, z)),
            ((self.plate.width_mm - m, m, z), (self.plate.width_mm - m, self.plate.depth_mm - m, z)),
            ((self.plate.width_mm - m, self.plate.depth_mm - m, z), (m, self.plate.depth_mm - m, z)),
            ((m, self.plate.depth_mm - m, z), (m, m, z)),
        ]
        return self._line_mesh(outer + inner)

    def _line_mesh(self, segments):
        points = []
        lines = []
        for a, b in segments:
            start = len(points)
            points.extend([a, b])
            lines.extend([2, start, start + 1])
        mesh = pv.PolyData(np.asarray(points, dtype=np.float64))
        mesh.lines = np.asarray(lines, dtype=np.int64)
        return mesh

    def _install_interaction_observers(self) -> None:
        if self._observer_ids:
            return
        self._observer_ids = [
            self.plotter.iren.add_observer("LeftButtonPressEvent", self._on_left_button_press),
            self.plotter.iren.add_observer("MouseMoveEvent", self._on_mouse_move),
            self.plotter.iren.add_observer("LeftButtonReleaseEvent", self._on_left_button_release),
            self.plotter.iren.add_observer("RightButtonPressEvent", self._on_right_button_press),
            self.plotter.iren.add_observer("KeyPressEvent", self._on_key_press),
        ]

    def _install_qt_event_filter(self) -> None:
        if self._event_filter is not None:
            return
        self._event_filter = ViewerMouseEventFilter(self)
        try:
            self.plotter.installEventFilter(self._event_filter)
        except Exception:
            self._event_filter = None

    def _open_dialog(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open Model",
            "",
            "3D Models (*.3mf *.stl *.obj *.glb *.gltf);;All Files (*)",
        )
        if path:
            self.load_path(path)

    def load_path(self, path: str) -> None:
        self._clear_scene()
        self.file_label.setText(f"Loading {Path(path).name}...")
        self.status_bar.showMessage("Loading model...")
        self._set_controls_enabled(False)

        thread = QtCore.QThread(self)
        worker = ModelLoadWorker(path)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_load_finished)
        worker.failed.connect(self._on_load_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._loading_thread = thread
        self._loading_worker = worker

    def _on_load_finished(self, scene: LoadedScene) -> None:
        self.scene = scene
        self._meshes = list(scene.meshes)
        self.controller = TransformController(scene.points, plate=self.plate)
        self.controller.auto_place()
        self._add_model_actors(scene)
        self._apply_transform_to_actors()
        self._set_controls_enabled(True)
        self._sync_fields_from_controller()
        self.file_label.setText(str(scene.path.name))
        self.status_bar.showMessage(f"Loaded {scene.path.name}", 5000)
        self._home_camera()
        self._refresh_info()

    def _on_load_failed(self, message: str) -> None:
        self.file_label.setText("No model loaded")
        self.status_bar.showMessage("Load failed", 5000)
        self.info_label.setText(message)
        self._set_controls_enabled(False)
        QtWidgets.QMessageBox.critical(self, "Open Model", message)

    def _add_model_actors(self, scene: LoadedScene) -> None:
        for idx, mesh in enumerate(scene.meshes):
            actor = self.plotter.add_mesh(
                self._polydata_from_mesh(mesh),
                name=f"model_{idx}",
                color=mesh.color or "#b8bec7",
                smooth_shading=False,
                show_edges=False,
                edge_color="#343a42",
                line_width=0.6,
                lighting=True,
                pickable=True,
                reset_camera=False,
            )
            if actor.prop:
                actor.prop.diffuse = 1.0
                actor.prop.specular = 0.0
                actor.prop.interpolation = "gouraud"
            self.model_actors.append(actor)

    def _polydata_from_mesh(self, mesh: LoadedMesh):
        faces = np.asarray(mesh.faces, dtype=np.int64)
        if faces.ndim != 2 or faces.shape[1] != 3:
            raise ValueError("Only triangular meshes are supported by the prototype viewer.")
        pv_faces = np.hstack([np.full((faces.shape[0], 1), 3, dtype=np.int64), faces]).ravel()
        return pv.PolyData(np.asarray(mesh.vertices, dtype=np.float64), pv_faces)

    def _clear_scene(self) -> None:
        self._clear_gimbal()
        self._clear_selection(render=False)
        for actor in list(self.model_actors):
            try:
                self.plotter.remove_actor(actor, reset_camera=False)
            except Exception:
                pass
        self.model_actors = []
        self._meshes = []
        self.scene = None
        self.controller = None
        self._set_mode("normal")
        try:
            self.plotter.render()
        except Exception:
            pass

    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self.auto_btn,
            self.fit_btn,
            self.center_btn,
            self.drop_btn,
            self.home_btn,
            self.reset_btn,
            self.move_btn,
            self.rotate_btn,
            self.rot_x_spin,
            self.rot_y_spin,
            self.rot_z_spin,
            self.scale_spin,
            self.pos_x_spin,
            self.pos_y_spin,
        ):
            widget.setEnabled(enabled)
        self.normal_btn.setEnabled(True)

    def _vtk_matrix(self, matrix: np.ndarray):
        vtk = _vtk_module()
        vtk_mat = vtk.vtkMatrix4x4()
        for i in range(4):
            for j in range(4):
                vtk_mat.SetElement(i, j, float(matrix[i, j]))
        return vtk_mat

    def _apply_transform_to_actors(self) -> None:
        if not self.controller:
            return
        vtk_mat = self._vtk_matrix(self.controller.matrix)
        for actor in self.model_actors:
            try:
                actor.SetUserMatrix(vtk_mat)
            except Exception:
                pass
        self._update_plate_warning()
        self._rebuild_selection_outline()
        if self._mode == "rotate":
            self._rebuild_gimbal()
        try:
            self.plotter.render()
        except Exception:
            pass

    def _sync_fields_from_controller(self) -> None:
        if not self.controller:
            return
        self._syncing_fields = True
        try:
            rx, ry, rz = self.controller.euler_xyz_degrees()
            bounds = self.controller.transformed_bounds()
            center = bounds.center
            self.rot_x_spin.setValue(float(rx))
            self.rot_y_spin.setValue(float(ry))
            self.rot_z_spin.setValue(float(rz))
            self.scale_spin.setValue(float(self.controller.scale))
            self.pos_x_spin.setValue(float(center[0]))
            self.pos_y_spin.setValue(float(center[1]))
        finally:
            self._syncing_fields = False

    def _rotation_fields_changed(self) -> None:
        if self._syncing_fields or not self.controller:
            return
        self.controller.set_euler(
            self.rot_x_spin.value(),
            self.rot_y_spin.value(),
            self.rot_z_spin.value(),
        )
        self._apply_transform_to_actors()
        self._sync_fields_from_controller()
        self._refresh_info()

    def _scale_field_changed(self) -> None:
        if self._syncing_fields or not self.controller:
            return
        self.controller.set_scale(self.scale_spin.value())
        self._apply_transform_to_actors()
        self._sync_fields_from_controller()
        self._refresh_info()

    def _position_fields_changed(self) -> None:
        if self._syncing_fields or not self.controller:
            return
        self.controller.set_xy_position(self.pos_x_spin.value(), self.pos_y_spin.value(), clamp=True)
        self._apply_transform_to_actors()
        self._sync_fields_from_controller()
        self._refresh_info()

    def _auto_place(self) -> None:
        if not self.controller:
            return
        factor = self.controller.auto_place()
        self._apply_transform_to_actors()
        self._sync_fields_from_controller()
        self._refresh_info()
        if factor < 1.0:
            self.status_bar.showMessage(f"Auto placed and scaled to {self.controller.scale:.4g}", 5000)
        else:
            self.status_bar.showMessage("Auto placed on build plate", 5000)

    def _fit_to_plate(self) -> None:
        if not self.controller:
            return
        factor = self.controller.fit_to_plate(enlarge=False)
        self._apply_transform_to_actors()
        self._sync_fields_from_controller()
        self._refresh_info()
        self.status_bar.showMessage(
            "Already fits plate" if abs(factor - 1.0) <= 1e-9 else f"Scaled by {factor:.4g} to fit",
            5000,
        )

    def _center_on_plate(self) -> None:
        if not self.controller:
            return
        self.controller.center_xy()
        self.controller.clamp_xy_to_plate()
        self._apply_transform_to_actors()
        self._sync_fields_from_controller()
        self._refresh_info()
        self.status_bar.showMessage("Centered on build plate", 3000)

    def _drop_to_plate(self) -> None:
        if not self.controller:
            return
        self.controller.drop_to_plate()
        self._apply_transform_to_actors()
        self._sync_fields_from_controller()
        self._refresh_info()
        self.status_bar.showMessage("Dropped to build plate", 3000)

    def _reset_transform(self) -> None:
        if not self.controller:
            return
        self.controller.reset()
        self.controller.auto_place()
        self._apply_transform_to_actors()
        self._sync_fields_from_controller()
        self._refresh_info()
        self.status_bar.showMessage("Transform reset", 5000)

    def _set_mode(self, mode: str) -> None:
        if mode not in {"normal", "move", "rotate"}:
            mode = "normal"
        self._mode = mode
        self._active_move = None
        self._active_rotation = None
        self._camera_drag_forwarded = False
        self._normal_click_press = None
        if mode == "rotate":
            self._rebuild_gimbal()
        else:
            self._clear_gimbal()
        self._set_camera_drag_enabled(True)
        self._set_viewer_cursor("default")
        button_by_mode = {
            "normal": self.normal_btn,
            "move": self.move_btn,
            "rotate": self.rotate_btn,
        }
        btn = button_by_mode.get(mode)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)
        self._update_mode_visibility()
        self._refresh_info()

    def _update_mode_visibility(self) -> None:
        show_rotation = self._mode == "rotate"
        for widget in getattr(self, "rotation_field_widgets", ()):
            try:
                widget.setVisible(show_rotation)
            except Exception:
                pass

    def _set_camera_drag_enabled(self, enabled: bool) -> None:
        try:
            if enabled:
                self.plotter.iren.enable_trackball_style()
            else:
                vtk = _vtk_module()
                self.plotter.iren.interactor.SetInteractorStyle(vtk.vtkInteractorStyleUser())
        except Exception:
            pass

    def _set_viewer_cursor(self, kind: str) -> None:
        if self._cursor_kind == kind:
            return
        self._cursor_kind = kind
        try:
            cursors = {
                "move": QtCore.Qt.SizeAllCursor,
                "rotate": QtCore.Qt.OpenHandCursor,
                "drag": QtCore.Qt.ClosedHandCursor,
            }
            cursor = cursors.get(kind)
            if cursor is None:
                self.plotter.unsetCursor()
            else:
                self.plotter.setCursor(cursor)
        except Exception:
            pass

    def _update_hover_cursor(self, display_pos) -> None:
        if self._active_move is not None:
            self._set_viewer_cursor("drag")
            return
        if self._active_rotation is not None:
            self._set_viewer_cursor("rotate")
            return
        if not self.controller:
            self._set_viewer_cursor("default")
            return
        actor, _cell_id, _pick_point = self._pick_at_display(display_pos)
        if self._mode == "move" and self._model_index_for_actor(actor) is not None:
            self._set_viewer_cursor("move")
        elif self._mode == "rotate" and self._gimbal_axis_for_actor(actor) is not None:
            self._set_viewer_cursor("rotate")
        else:
            self._set_viewer_cursor("default")

    def _camera_interactor_style(self):
        try:
            return self.plotter.iren.interactor.GetInteractorStyle()
        except Exception:
            return None

    def _forward_camera_left_press(self) -> None:
        style = self._camera_interactor_style()
        if style is None:
            return
        try:
            style.OnLeftButtonDown()
        except Exception:
            pass

    def _forward_camera_left_release(self) -> None:
        style = self._camera_interactor_style()
        if style is None:
            return
        try:
            style.OnLeftButtonUp()
        except Exception:
            pass

    def _rebuild_gimbal(self) -> None:
        self._clear_gimbal()
        if not self.controller:
            return
        bounds = self.controller.transformed_bounds()
        center = bounds.center
        radius = max(float(np.max(bounds.size)) * 0.68, 12.0)
        colors = {"x": "#e64b4b", "y": "#36a852", "z": "#3677d8"}
        for axis, color in colors.items():
            actor = self.plotter.add_mesh(
                self._gimbal_ring_mesh(axis, center, radius),
                name=f"__gimbal_{axis}__",
                color=color,
                opacity=0.95,
                lighting=False,
                pickable=True,
                reset_camera=False,
            )
            self.gimbal_actors[axis] = actor

    def _clear_gimbal(self) -> None:
        for actor in list(self.gimbal_actors.values()):
            try:
                self.plotter.remove_actor(actor, reset_camera=False)
            except Exception:
                pass
        self.gimbal_actors = {}

    def _gimbal_ring_mesh(self, axis: str, center: np.ndarray, radius: float):
        t = np.linspace(0.0, 2.0 * np.pi, 241)
        if axis == "x":
            pts = np.column_stack([np.zeros_like(t), np.cos(t) * radius, np.sin(t) * radius])
        elif axis == "y":
            pts = np.column_stack([np.cos(t) * radius, np.zeros_like(t), np.sin(t) * radius])
        else:
            pts = np.column_stack([np.cos(t) * radius, np.sin(t) * radius, np.zeros_like(t)])
        ring = pv.lines_from_points(pts + center, close=True)
        return ring.tube(radius=max(radius * 0.006, 0.16), n_sides=10)

    def _gimbal_axis_for_actor(self, actor) -> Optional[str]:
        for axis, candidate in self.gimbal_actors.items():
            if self._same_actor(candidate, actor):
                return axis
        return None

    def _display_pos(self, point: np.ndarray) -> np.ndarray:
        renderer = self.plotter.renderer
        renderer.SetWorldPoint(float(point[0]), float(point[1]), float(point[2]), 1.0)
        renderer.WorldToDisplay()
        return np.asarray(renderer.GetDisplayPoint(), dtype=np.float64)

    def _current_event_xy(self) -> tuple[int, int]:
        try:
            return self.plotter.iren.get_event_position()
        except Exception:
            return self.plotter.iren.interactor.GetEventPosition()

    def _pick_at_display(self, display_pos):
        if self._picker is None:
            return None, -1, None
        x, y = int(display_pos[0]), int(display_pos[1])
        candidates = [(x, y)]
        try:
            height = int(self.plotter.window_size[1])
            flipped_y = max(0, height - y)
            if flipped_y != y:
                candidates.append((x, flipped_y))
        except Exception:
            pass
        for px, py in candidates:
            self._picker.Pick(px, py, 0.0, self.plotter.renderer)
            actor = self._picker.GetActor()
            if actor is not None:
                return actor, int(self._picker.GetCellId()), np.asarray(self._picker.GetPickPosition(), dtype=np.float64)
        return None, -1, None

    def _same_actor(self, left, right) -> bool:
        if left is right:
            return True
        try:
            return left.GetAddressAsString("") == right.GetAddressAsString("")
        except Exception:
            return False

    def _model_index_for_actor(self, actor) -> Optional[int]:
        for idx, candidate in enumerate(self.model_actors):
            if self._same_actor(candidate, actor):
                return idx
        return None

    def _left_button_is_down(self) -> bool:
        try:
            return bool(QtWidgets.QApplication.mouseButtons() & QtCore.Qt.LeftButton)
        except Exception:
            return True

    def _finish_pointer_interaction(self) -> None:
        had_move = self._active_move is not None
        had_rotation = self._active_rotation is not None
        self._active_move = None
        self._active_rotation = None
        if had_rotation and self._mode == "rotate":
            self._rebuild_gimbal()
            self._set_camera_drag_enabled(True)
        if had_move and self._mode == "move":
            self._set_camera_drag_enabled(True)
        self._set_viewer_cursor("default")
        if had_move or had_rotation:
            try:
                self.plotter.render()
            except Exception:
                pass
        if had_rotation:
            self.status_bar.showMessage("Rotated model", 3000)
        elif had_move:
            self.status_bar.showMessage("Moved model", 3000)

    def _handle_escape(self) -> None:
        if self._handle_tool_cancel():
            return
        if self.selected_mesh_index is not None:
            self._clear_selection()
            self.status_bar.showMessage("Selection cleared", 3000)

    def _handle_tool_cancel(self) -> bool:
        had_interaction = self._active_move is not None or self._active_rotation is not None
        if had_interaction:
            self._finish_pointer_interaction()
        if self._mode != "normal":
            self._set_mode("normal")
            self.status_bar.showMessage("Returned to Select / Orbit", 3000)
            return True
        return had_interaction

    def _select_mesh_index(self, mesh_idx: Optional[int]) -> None:
        if mesh_idx is not None and (mesh_idx < 0 or mesh_idx >= len(self._meshes)):
            mesh_idx = None
        self.selected_mesh_index = mesh_idx
        self._apply_selection_actor_style()
        self._update_selection_labels()
        self._rebuild_selection_outline()
        self._refresh_info()
        try:
            self.plotter.render()
        except Exception:
            pass

    def _clear_selection(self, *, render: bool = True) -> None:
        self.selected_mesh_index = None
        if self.selection_outline_actor is not None:
            try:
                self.plotter.remove_actor(self.selection_outline_actor, reset_camera=False)
            except Exception:
                pass
        self.selection_outline_actor = None
        self._apply_selection_actor_style()
        self._update_selection_labels()
        if render:
            try:
                self.plotter.render()
            except Exception:
                pass

    def _apply_selection_actor_style(self) -> None:
        selected = self.selected_mesh_index is not None
        for actor in self.model_actors:
            try:
                actor.prop.show_edges = selected
                actor.prop.edge_color = "#f2c94c" if selected else "#343a42"
                actor.prop.line_width = 1.8 if selected else 0.6
            except Exception:
                pass

    def _update_selection_labels(self) -> None:
        if not hasattr(self, "selected_name_label"):
            return
        idx = self.selected_mesh_index
        if idx is None or idx < 0 or idx >= len(self._meshes):
            self.selected_name_label.setText("None")
            self.selected_stats_label.setText("Click any object to select the moving model.")
            return
        mesh = self._meshes[idx]
        total_vertices = sum(len(part.vertices) for part in self._meshes)
        total_faces = sum(len(part.faces) for part in self._meshes)
        self.selected_name_label.setText("Whole Model")
        self.selected_stats_label.setText(
            f"Clicked {mesh.name}; moving all {len(self._meshes)} objects, "
            f"{total_vertices:,} vertices, {total_faces:,} triangles."
        )

    def _rebuild_selection_outline(self) -> None:
        if self.selection_outline_actor is not None:
            try:
                self.plotter.remove_actor(self.selection_outline_actor, reset_camera=False)
            except Exception:
                pass
        self.selection_outline_actor = None
        if not self.controller or self.selected_mesh_index is None:
            return
        model_bounds = self.controller.transformed_bounds()
        mins = model_bounds.min_corner
        maxs = model_bounds.max_corner
        if np.any((maxs - mins) <= 1e-9):
            return
        bounds = (mins[0], maxs[0], mins[1], maxs[1], mins[2], maxs[2])
        try:
            outline = pv.Cube(bounds=bounds).extract_all_edges()
            self.selection_outline_actor = self.plotter.add_mesh(
                outline,
                name="__selection_outline__",
                color="#f2c94c",
                line_width=4,
                lighting=False,
                pickable=False,
                reset_camera=False,
            )
        except Exception:
            self.selection_outline_actor = None

    def _on_left_button_press(self, *_args) -> None:
        if not self.controller:
            return
        pos = self._current_event_xy()
        if self._mode == "normal":
            actor, _cell_id, _pick_point = self._pick_at_display(pos)
            mesh_idx = self._model_index_for_actor(actor)
            if self.selected_mesh_index is not None and mesh_idx is not None:
                self._select_mesh_index(mesh_idx)
                self._set_mode("move")
                self._start_move_drag(pos)
                self.status_bar.showMessage("Move: drag selected model", 3000)
                return
            self._normal_click_press = np.asarray(pos, dtype=np.float64)
            return
        if self._mode == "move":
            actor, _cell_id, _pick_point = self._pick_at_display(pos)
            mesh_idx = self._model_index_for_actor(actor)
            if mesh_idx is not None:
                self._select_mesh_index(mesh_idx)
            else:
                self._active_move = None
                self._set_camera_drag_enabled(True)
                self._camera_drag_forwarded = True
                self._forward_camera_left_press()
                self.status_bar.showMessage("Camera drag; start on model to move", 3000)
                return
            self._start_move_drag(pos)
            return
        if self._mode == "rotate":
            actor, _cell_id, pick_point = self._pick_at_display(pos)
            axis = self._gimbal_axis_for_actor(actor)
            if axis is not None and pick_point is not None:
                self._start_rotation_drag(axis, pick_point, pos)
                return
            mesh_idx = self._model_index_for_actor(actor)
            if mesh_idx is not None:
                self._select_mesh_index(mesh_idx)
            self._active_rotation = None
            self._set_camera_drag_enabled(True)
            self._camera_drag_forwarded = True
            self._forward_camera_left_press()
            self.status_bar.showMessage("Camera drag; start on ring to rotate", 3000)

    def _start_move_drag(self, display_pos) -> None:
        if not self.controller:
            return
        plane = self._display_to_plate_point(display_pos)
        if plane is None:
            return
        self._set_camera_drag_enabled(False)
        self._set_viewer_cursor("drag")
        self._active_move = {
            "start": plane,
            "base_translation": self.controller.translation_xyz,
        }

    def _on_mouse_move(self, *_args) -> None:
        if not self.controller or self._mode == "normal":
            if self._mode == "normal":
                self._set_viewer_cursor("default")
            return
        if (self._active_move is not None or self._active_rotation is not None) and not self._left_button_is_down():
            self._finish_pointer_interaction()
            return
        pos = self._current_event_xy()
        if self._mode == "move" and self._active_move is not None:
            plane = self._display_to_plate_point(pos)
            if plane is None:
                return
            delta = plane - self._active_move["start"]
            base_translation = np.asarray(self._active_move["base_translation"], dtype=np.float64)
            new_translation = base_translation.copy()
            new_translation[0] += delta[0]
            new_translation[1] += delta[1]
            self.controller.transform = self.controller.transform.with_changes(translation_xyz=new_translation)
            self.controller.clamp_xy_to_plate()
            self._apply_transform_to_actors()
            self._sync_fields_from_controller()
            self._refresh_info()
        elif self._mode == "rotate" and self._active_rotation is not None:
            self._update_rotation_drag(pos)
        else:
            self._update_hover_cursor(pos)

    def _on_left_button_release(self, *_args) -> None:
        if self._camera_drag_forwarded:
            self._camera_drag_forwarded = False
            self._forward_camera_left_release()
            return
        if self._mode == "normal":
            release = np.asarray(self._current_event_xy(), dtype=np.float64)
            press = self._normal_click_press
            self._normal_click_press = None
            if press is not None and np.linalg.norm(release - press) <= 4.5:
                actor, _cell_id, _pick = self._pick_at_display(release)
                mesh_idx = self._model_index_for_actor(actor)
                if mesh_idx is None:
                    self._clear_selection()
                    self.status_bar.showMessage("Selection cleared", 3000)
                else:
                    self._select_mesh_index(mesh_idx)
                    mesh = self._meshes[mesh_idx]
                    self.status_bar.showMessage(f"Selected {mesh.name}", 5000)
            return
        self._finish_pointer_interaction()

    def _on_right_button_press(self, *_args) -> None:
        self._handle_tool_cancel()

    def _on_key_press(self, *_args) -> None:
        try:
            key = str(self.plotter.iren.interactor.GetKeySym()).lower()
        except Exception:
            key = ""
        if key in {"escape", "esc"}:
            self._handle_escape()

    def _start_rotation_drag(self, axis: str, pick_point: np.ndarray, display_pos) -> None:
        if not self.controller:
            return
        self._set_camera_drag_enabled(False)
        self._set_viewer_cursor("drag")
        if self.selected_mesh_index is None and self._meshes:
            self._select_mesh_index(0)
        bounds = self.controller.transformed_bounds()
        center = bounds.center
        axis_vec = TransformController._axis_vector(axis)
        radial = np.asarray(pick_point, dtype=np.float64) - center
        radial = radial - axis_vec * float(np.dot(radial, axis_vec))
        radial_norm = float(np.linalg.norm(radial))
        if radial_norm <= 1e-9:
            radial = self._rotation_fallback_radial(axis_vec)
            radial_norm = max(float(np.linalg.norm(radial)), 1.0)
        radial = radial / radial_norm
        tangent = np.cross(axis_vec, radial)
        tangent_norm = float(np.linalg.norm(tangent))
        if tangent_norm <= 1e-9:
            return
        tangent = tangent / tangent_norm
        reference_point = center + radial * radial_norm
        reference_display = self._display_pos(reference_point)
        tangent_display = self._display_pos(reference_point + tangent * max(radial_norm * 0.2, 1.0)) - reference_display
        tangent_2d = tangent_display[:2]
        tangent_2d_norm = float(np.linalg.norm(tangent_2d))
        if tangent_2d_norm <= 1e-9:
            return
        tangent_2d = tangent_2d / tangent_2d_norm
        center_display = self._display_pos(center)
        ring_radius_px = max(float(np.linalg.norm(reference_display[:2] - center_display[:2])), 24.0)
        self._active_rotation = {
            "axis": axis,
            "base_transform": self.controller.transform,
            "prev_display": np.asarray(display_pos, dtype=np.float64),
            "tangent_display": tangent_2d,
            "ring_radius_px": ring_radius_px,
            "angle": 0.0,
        }
        self.status_bar.showMessage(f"Rotate {axis.upper()}: drag ring; hold Shift to snap")

    def _rotation_fallback_radial(self, axis_vec: np.ndarray) -> np.ndarray:
        candidates = (
            np.array([1.0, 0.0, 0.0], dtype=np.float64),
            np.array([0.0, 1.0, 0.0], dtype=np.float64),
            np.array([0.0, 0.0, 1.0], dtype=np.float64),
        )
        best = candidates[0]
        best_norm = -1.0
        for candidate in candidates:
            radial = candidate - axis_vec * float(np.dot(candidate, axis_vec))
            norm = float(np.linalg.norm(radial))
            if norm > best_norm:
                best = radial / max(norm, 1e-12)
                best_norm = norm
        return best

    def _update_rotation_drag(self, display_pos) -> None:
        if not self.controller or self._active_rotation is None:
            return
        drag = self._active_rotation
        pos = np.asarray(display_pos, dtype=np.float64)
        delta = pos - drag["prev_display"]
        drag["prev_display"] = pos
        pixels_along = float(np.dot(delta[:2], drag["tangent_display"]))
        drag["angle"] += pixels_along * (180.0 / np.pi) / drag["ring_radius_px"]
        angle = float(drag["angle"])
        if QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.ShiftModifier:
            angle = round(angle / 15.0) * 15.0
        base_transform = drag["base_transform"]
        axis_vec = TransformController._axis_vector(drag["axis"])
        from layerloom.transform_3mf import axis_angle_rotation

        rotation = axis_angle_rotation(axis_vec, angle) @ base_transform.rotation
        self.controller.transform = base_transform.with_changes(rotation=rotation)
        self.controller.drop_to_plate()
        self.controller.clamp_xy_to_plate()
        self._apply_transform_to_actors()
        self._sync_fields_from_controller()
        self._refresh_info()
        self.status_bar.showMessage(f"Rotate {drag['axis'].upper()} {angle:.1f} deg")

    def _display_to_plate_point(self, display_pos) -> Optional[np.ndarray]:
        renderer = self.plotter.renderer
        x, y = float(display_pos[0]), float(display_pos[1])
        points = []
        for z in (0.0, 1.0):
            renderer.SetDisplayPoint(x, y, z)
            renderer.DisplayToWorld()
            world = np.asarray(renderer.GetWorldPoint(), dtype=np.float64)
            if abs(world[3]) <= 1e-12:
                return None
            points.append(world[:3] / world[3])
        p0, p1 = points
        ray = p1 - p0
        if abs(ray[2]) <= 1e-12:
            return None
        t = -p0[2] / ray[2]
        return p0 + ray * t

    def _update_plate_warning(self) -> None:
        if not self.controller:
            color = "#4d5966"
        else:
            color = "#4d5966" if self.controller.footprint_inside_plate() else "#d65a31"
        try:
            self.boundary_actor.prop.color = QtGui.QColor(color).getRgbF()[:3]
        except Exception:
            try:
                self.boundary_actor.prop.color = color
            except Exception:
                pass

    def _refresh_info(self) -> None:
        if not self.controller:
            self.info_label.setText("Open a model to begin.")
            return
        bounds = self.controller.transformed_bounds()
        fits = self.controller.footprint_inside_plate(bounds)
        mode_tip = {
            "normal": "Select / Orbit: click objects to select; drag the view to inspect.",
            "move": "Move: drag the model in XY; drag empty space to navigate.",
            "rotate": "Rotate: drag an axis ring; drag empty space to navigate.",
        }.get(self._mode, "")
        fit_text = "inside plate" if fits else "outside plate"
        self.info_label.setText(
            f"Size {bounds.size[0]:.1f} x {bounds.size[1]:.1f} x {bounds.size[2]:.1f} mm; "
            f"{fit_text}. {mode_tip}"
        )

    def _home_camera(self) -> None:
        self._set_camera_view("iso")

    def _set_camera_view(self, view: str) -> None:
        if self.controller:
            bounds = self.controller.transformed_bounds()
            center = bounds.center
            span = max(float(np.max(bounds.size)), self.plate.width_mm, self.plate.depth_mm, 20.0)
        else:
            center = np.array([self.plate.width_mm * 0.5, self.plate.depth_mm * 0.5, 8.0], dtype=np.float64)
            span = max(self.plate.width_mm, self.plate.depth_mm)
        distance = span * 1.65
        cx, cy, cz = [float(v) for v in center]
        if view == "top":
            camera = (cx, cy, cz + distance)
            up = (0.0, 1.0, 0.0)
        elif view == "front":
            camera = (cx, cy - distance, cz + distance * 0.12)
            up = (0.0, 0.0, 1.0)
        elif view == "right":
            camera = (cx + distance, cy, cz + distance * 0.12)
            up = (0.0, 0.0, 1.0)
        elif view == "left":
            camera = (cx - distance, cy, cz + distance * 0.12)
            up = (0.0, 0.0, 1.0)
        else:
            camera = (cx + distance * 0.78, cy - distance * 0.86, cz + distance * 0.58)
            up = (0.0, 0.0, 1.0)
        self.plotter.camera_position = [camera, (cx, cy, cz), up]
        try:
            self.plotter.camera.SetViewAngle(32.0)
            self.plotter.reset_camera_clipping_range()
            self.plotter.render()
        except Exception:
            pass

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API name
        try:
            if event.key() == QtCore.Qt.Key_Escape:
                self._handle_escape()
                event.accept()
                return
        except Exception:
            pass
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        try:
            self._clear_scene()
            self.plotter.close()
        except Exception:
            pass
        super().closeEvent(event)


def main(argv: Optional[list[str]] = None) -> int:
    _require_gui()
    args = list(sys.argv[1:] if argv is None else argv)
    initial_path = args[0] if args else None
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("LayerLoom Experimental Viewer")
    app.setStyle("Fusion")
    window = ExperimentalViewerWindow(initial_path)
    window.show()
    return int(app.exec_())
