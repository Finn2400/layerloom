"""In-memory bridge between the main Arrange view and its paint document."""

import numpy as np
import pyvista as pv
from qtpy import QtCore, QtWidgets

from .paint_workspace_model import PaintWorkspaceModel


class SharedPaintWorkspace:
    """Adapt existing Arrange controls without serializing or reparsing a model."""

    def __init__(self, host, editor):
        self.host = host
        self.editor = editor
        self.model = None
        self.loading = False
        self._syncing_palette = False
        self.polys = {}
        self.mesh_index_by_oid = {}
        for index in range(editor.palette_combo.count()):
            name = editor.palette_combo.itemText(index)
            if host.palette_combo.findText(name) < 0:
                host.palette_combo.addItem(name)
        editor.documentChanged.connect(self.set_document)
        editor.paintChanged.connect(self.refresh_colors)
        editor.appearanceChanged.connect(self.refresh_colors)
        editor.geometryChanged.connect(self.refresh_geometry)
        editor.palette_combo.currentTextChanged.connect(self._paint_palette_changed)
        host.palette_combo.currentTextChanged.connect(self._arrange_palette_changed)

    @property
    def active(self):
        return self.model is not None and self.model.document is self.editor.document

    def set_document(self, document):
        host = self.host
        self.loading = True
        try:
            self.model = PaintWorkspaceModel(document)
            host._paint_pending_source = None
            host.source_file_path = document.source_path
            # Legacy controls use these values as loaded-state/display labels.
            # Shared-model operations never read these paths as geometry.
            host.base_file = document.source_path or "Paint project"
            host.current_file = host.base_file
            host.transformed_preview_file = None
            host._last_transform_plan = self.model.identity_plan
            host.pending_rotation_matrix = np.eye(3)
            host.applied_rotation_matrix = np.eye(3)
            host.pending_target_center_xy = None
            host.applied_target_center_xy = None
            host.applied_scale = 1.0
            host.large_model_mode = False
            host.large_model_reason = ""
            host.large_model_info = {}
            host.viewer_tool_mode = "normal"
            host._last_picked_oid = None
            host.assignments = {}
            for name in ("gray", "orange", "violet", "green"):
                toggle = getattr(host, f"add_{name}_checkbox", None)
                if toggle is not None:
                    blocker = QtCore.QSignalBlocker(toggle)
                    toggle.setChecked(False)
                    toggle.setEnabled(False)
                    toggle.hide()
                    del blocker
            for token in ("n", "o", "v", "g"):
                toggle = host.req_buttons.get(token)
                if toggle is not None:
                    blocker = QtCore.QSignalBlocker(toggle)
                    toggle.setChecked(False)
                    toggle.setEnabled(False)
                    toggle.hide()
                    del blocker
            host.oid_to_name = {}
            host.oid_to_import_name = {}
            host.oid_to_display_name = {}
            self.mesh_index_by_oid = {}
            blocker = QtCore.QSignalBlocker(host.parts_table)
            host.parts_table.clear()
            host.parts_table.setHeaderLabels(["Part", "Paint", "Pattern"])
            for index, mesh in enumerate(document.meshes):
                oid = str(index + 1)
                name = f"{mesh.name}__ID_{oid}__"
                self.mesh_index_by_oid[oid] = index
                host.oid_to_name[oid] = name
                host.oid_to_import_name[oid] = mesh.name
                host.oid_to_display_name[oid] = mesh.name
                item = QtWidgets.QTreeWidgetItem([mesh.name, "", ""])
                item.setData(0, QtCore.Qt.UserRole, oid)
                host.parts_table.addTopLevelItem(item)
            del blocker
            host.name_to_oid = {name: oid for oid, name in host.oid_to_name.items()}
            host.model_objects = list(host.oid_to_name.items())
            host._names_in_build_order = list(host.oid_to_name.values())
            host._set_transform_values(0.0, 0.0, 0.0, 1.0)
            host._update_transform_flags()
            self.rebuild_scene(reset_camera=True)
            self.refresh_colors()
            self._paint_palette_changed(self.editor.palette_combo.currentText())
            host._update_selected_part_summary(None)
            host._sync_viewer_tool_mode()
            host._set_viewer_hint(None)
        finally:
            self.loading = False
        host._workspace_changed(host.workspace_tabs.currentIndex())

    def _display_colors(self, mesh):
        colors = mesh.face_rgba.copy()
        if self.editor.preview_mapping.isChecked() and self.editor._mapping:
            rgb, inverse = np.unique(colors[:, :3], axis=0, return_inverse=True)
            display = []
            for value in rgb:
                key = "#" + bytes(value).hex().upper()
                entry = self.editor._mapping.get(key)
                display.append(list(bytes.fromhex(entry["hex"].lstrip("#")))[:3] if entry else value)
            colors[:, :3] = np.asarray(display, dtype=np.uint8)[inverse]
        return colors

    def rebuild_scene(self, *, reset_camera=False):
        if not self.active:
            return
        host, viewer = self.host, self.host.viewer
        plotter = viewer._ensure_plotter()
        viewer._teardown_scene()
        viewer._clear_layer_preview()
        viewer._active_move = None
        viewer._active_rotation = None
        viewer.on_model_pick = host._handle_viewer_pick_event
        self.polys = {}
        for oid, index in self.mesh_index_by_oid.items():
            mesh = self.model.document.meshes[index]
            cells = np.column_stack([np.full(len(mesh.faces), 3), mesh.faces]).ravel()
            poly = pv.PolyData(mesh.vertices.copy(), cells)
            poly.cell_data["paint_rgba"] = self._display_colors(mesh)
            name = host.oid_to_name[oid]
            actor = plotter.add_mesh(
                poly, name=name, scalars="paint_rgba", rgba=True, preference="cell",
                show_scalar_bar=False, smooth_shading=False, ambient=0.4, diffuse=0.6,
                specular=0.0, pickable=True, reset_camera=False,
            )
            actor.SetUserMatrix(None)
            viewer.actors_by_name[name] = actor
            self.polys[index] = poly
        if reset_camera:
            viewer._set_default_loaded_camera()
        viewer._clear_hover_model_pick_cache()
        plotter.reset_camera_clipping_range()
        plotter.render()

    def refresh_colors(self):
        if not self.active:
            return
        host = self.host
        preferences = self.editor._preferred_recipe_overrides()
        for oid, index in self.mesh_index_by_oid.items():
            mesh = self.model.document.meshes[index]
            poly = self.polys.get(index)
            if poly is not None:
                poly.cell_data["paint_rgba"] = self._display_colors(mesh)
                poly.Modified()
            uniform = bool(len(mesh.face_rgba) and np.all(mesh.face_rgba == mesh.face_rgba[0]))
            color = "#" + bytes(mesh.face_rgba[0, :3]).hex().upper() if uniform else "Mixed paint"
            host.assignments.pop(oid, None)
            preferred = (preferences.get(color) or self.editor._mapping.get(color, {}).get("token")) if uniform else None
            if preferred:
                host.assignments[oid] = {"hex": color, "token": preferred}
            item = host._item_for_oid(oid)
            if item is not None:
                item.setText(1, color)
                item.setText(2, host.assignments.get(oid, {}).get("token", ""))
        selected = host._current_selected_oid() or host._last_picked_oid
        host._update_selected_part_summary(selected)
        host.viewer.plotter.render()

    def refresh_geometry(self):
        if not self.active:
            return
        viewer = self.host.viewer
        # Drag previews live on actors. Commit coordinates once, then clear them.
        viewer._active_move = None
        viewer._active_rotation = None
        viewer._surface_place_cache.clear()
        for oid, index in self.mesh_index_by_oid.items():
            poly = self.polys.get(index)
            if poly is not None:
                poly.points = self.model.document.meshes[index].vertices.copy()
                poly.Modified()
            actor = viewer.actors_by_name.get(self.host.oid_to_name[oid])
            if actor is not None:
                actor.SetUserMatrix(None)
        viewer._clear_hover_model_pick_cache()
        viewer._rebuild_selection_outline()
        viewer.plotter.reset_camera_clipping_range()
        viewer.plotter.render()

    def apply_plan(self, plan):
        changed = self.model.apply(plan)
        self.host._last_transform_plan = self.model.current_plan
        if changed:
            self.editor.refresh_geometry(mark_dirty=True)
        else:
            self.refresh_geometry()
        return changed

    def assign_part(self, oid, entry):
        index = self.mesh_index_by_oid.get(str(oid))
        if index is None:
            return
        color = bytes.fromhex(entry["hex"].lstrip("#"))
        self.editor.fill_mesh(index, list(color[:3]) + [255])
        palette = self.host.current_palette_name
        if self.editor.palette_combo.findText(palette) >= 0:
            self.editor.palette_combo.setCurrentText(palette)
        self.editor.mapping_mode_combo.setCurrentIndex(self.editor.mapping_mode_combo.findData("woven"))
        preferences = self.model.document.metadata.get("paint_recipe_preferences")
        if not isinstance(preferences, dict):
            preferences = self.model.document.metadata["paint_recipe_preferences"] = {}
        palette = self.editor.palette_combo.currentText()
        if not isinstance(preferences.get(palette), dict):
            preferences[palette] = {}
        preferences[palette][entry["hex"].upper()] = entry["token"]
        self.editor.assign_colors()
        self.host.assignments[str(oid)] = dict(entry)
        self.refresh_colors()

    def _paint_palette_changed(self, name):
        if self._syncing_palette or not self.active:
            return
        if self.host.palette_combo.findText(name) < 0:
            return
        self._syncing_palette = True
        try:
            if self.host.palette_combo.currentText() != name:
                self.host.palette_combo.setCurrentText(name)
            else:
                self.host._load_palette(name)
        finally:
            self._syncing_palette = False

    def _arrange_palette_changed(self, name):
        if self._syncing_palette or not self.active:
            return
        if self.editor.palette_combo.findText(name) < 0:
            return
        self._syncing_palette = True
        try:
            self.editor.palette_combo.setCurrentText(name)
        finally:
            self._syncing_palette = False
