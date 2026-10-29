"""Paint workspace hosting for LayerLoom's main Qt window.

This adapter owns mode switching and routes main-window actions to the shared
paint document. Geometry and placement remain in paint_workspace and the host's
Arrange implementation. It deliberately does not import any versioned GUI module.
"""

from __future__ import annotations

import os

from qtpy import QtCore, QtGui, QtWidgets


_PAINT_ACTION_LABELS = {
    "Open 3MF…": "Open model or paint project…",
    "Open GLB…": "Open another model…",
    "Save Labeled 3MF…": "Save paint project…",
    "Open 3MF": "Open…",
    "Open GLB": "Open model…",
    "Save Labeled 3MF": "Save paint…",
    "Weave": "Export geometry 3MF…",
    "Preview 3D": "Fit view",
}


class PaintWorkspaceHostMixin:
    """Cooperative adapter placed before the legacy Arrange window in its MRO.

    State is initialized before the Arrange constructor builds its toolbars;
    the Paint panel is installed once the Arrange widgets are available.
    """

    def __init__(self):
        self.paint_panel = None
        self.workspace_tabs = None
        self._paint_pending_source = None
        self._shared_paint_workspace = None
        self._paint_flash_states = {}
        super().__init__()
        self._install_paint_workspace()

    def _install_paint_workspace(self):
        """Use the main toolbar to switch modes without discarding either view."""
        from .paint_gui import PaintPanel
        from .paint_workspace import SharedPaintWorkspace
        self._arrange_toolbars = list(self.findChildren(QtWidgets.QToolBar))
        self._arrange_window_title = self.windowTitle()
        self._workspace_action_labels = [
            (action, action.text()) for action in self.findChildren(QtWidgets.QAction)
            if action.text() in _PAINT_ACTION_LABELS
        ]
        self._arrange_action_enabled = {action: action.isEnabled() for action, _ in self._workspace_action_labels}
        self._arrange_toolbar_visibility = {
            action: action.isVisible() for toolbar in self._arrange_toolbars for action in toolbar.actions()
        }
        self._arrange_only_toolbar_actions = [
            action for action in self._arrange_toolbar_visibility
            if isinstance(action, QtWidgets.QWidgetAction) or action.isSeparator()
            or action.text() in {"Open GLB", "Load Example"}
        ]
        arrange = self.takeCentralWidget()
        tabs = QtWidgets.QTabWidget(self)
        tabs.setObjectName("layerloomWorkspaces")
        self.workspace_tabs = tabs
        self.paint_panel = PaintPanel(parent=tabs, compact=True)
        tabs.addTab(self.paint_panel, "Paint surfaces")
        tabs.addTab(arrange, "Arrange & weave")
        tabs.tabBar().hide()
        self.setCentralWidget(tabs)
        tabs.currentChanged.connect(self._workspace_changed)
        editor = self.paint_panel.editor
        editor.windowTitleChanged.connect(self._paint_title_changed)
        editor.statusBar().messageChanged.connect(self._paint_status_changed)
        # Both controls belong to the host footer so an export remains
        # cancellable while the user switches between Paint and Arrange.
        for widget in (editor.progress, editor.export_cancel_button):
            editor.statusBar().removeWidget(widget)
            self.status_bar.addPermanentWidget(widget)
            widget.hide()
        self._paint_history_actions = [editor.undo_action, editor.redo_action]
        self._paint_shortcuts = {action: action.shortcut() for action in self._paint_history_actions}
        for action in self._paint_history_actions:
            self._main_toolbar.addAction(action)
        editor.export_action.changed.connect(self._sync_paint_host_actions)
        editor.save_action.changed.connect(self._sync_paint_host_actions)
        self._shared_paint_workspace = SharedPaintWorkspace(self, editor)
        # The plate is the primary workspace, including before a model loads.
        # Initialize its scenery once; mode toggles must preserve its camera.
        self.viewer._ensure_plotter()
        self.viewer._set_default_loaded_camera()
        self.workspace_tabs.setCurrentIndex(1)
        # Fit after Qt has laid out the real viewport, including narrow screens.
        QtCore.QTimer.singleShot(0, self._fit_initial_build_plate)

    def _fit_initial_build_plate(self):
        if not self._shared_paint_active() and self.paint_panel.editor.document is None:
            self.viewer.plotter.reset_camera()

    def _shared_paint_active(self):
        return self._shared_paint_workspace is not None and self._shared_paint_workspace.active

    def _paint_active(self):
        return (self.workspace_tabs is not None and self.paint_panel is not None
                and self.workspace_tabs.currentWidget() is self.paint_panel)

    def _paint_title_changed(self, title):
        if self._paint_active() or self._shared_paint_active():
            self.setWindowTitle(title + " — main UI prototype")

    def _paint_status_changed(self, message):
        if self._paint_active() or self._shared_paint_active():
            self.status_bar.showMessage(message)

    def _sync_paint_host_actions(self):
        paint = self._paint_active() or self._shared_paint_active()
        editor = self.paint_panel.editor
        for action, original in self._workspace_action_labels:
            if original == "Weave":
                action.setEnabled(editor.export_action.isEnabled() if paint else self._arrange_action_enabled[action])
            elif original.startswith("Save Labeled 3MF"):
                action.setEnabled(editor.save_action.isEnabled() if paint else self._arrange_action_enabled[action])

    def _workspace_changed(self, index):
        paint = self._paint_active()
        shared = self._shared_paint_active()
        for toolbar in self._arrange_toolbars:
            toolbar.show()
        for action in self._arrange_only_toolbar_actions:
            legacy_setting = isinstance(action, QtWidgets.QWidgetAction)
            action.setVisible(False if paint or (shared and legacy_setting)
                              else self._arrange_toolbar_visibility[action])
        for action, original in self._workspace_action_labels:
            action.setText(_PAINT_ACTION_LABELS[original] if paint or shared else original)
        for action in self._paint_history_actions:
            action.setVisible(paint or shared)
            action.setShortcut(self._paint_shortcuts[action] if paint or shared else QtGui.QKeySequence())
        self._sync_paint_host_actions()
        if hasattr(self, "_paint_action"):
            self._paint_action.setChecked(paint)
        if paint:
            self._paint_title_changed(self.paint_panel.editor.windowTitle())
            message = self.paint_panel.editor.statusBar().currentMessage()
            self.status_bar.showMessage(message or "Paint · select an area, choose a color, then Apply paint")
            pending = self._paint_pending_source
            if pending:
                if not self.paint_panel.can_close():
                    self.workspace_tabs.setCurrentIndex(1)
                    return
                if self.paint_panel.open_path(pending):
                    self._paint_pending_source = None
            self.paint_panel.editor.plotter.setFocus(QtCore.Qt.OtherFocusReason)
        else:
            menu = getattr(self.paint_panel.editor, "tool_settings_menu", None)
            if menu is not None:
                menu.hide()
            self.setWindowTitle(self._arrange_window_title)
            self._refresh_status_summary()
            if shared:
                self._paint_title_changed(self.paint_panel.editor.windowTitle())
            plotter = getattr(self.viewer, "plotter", None)
            (plotter if plotter is not None else self.viewer_host).setFocus(QtCore.Qt.OtherFocusReason)

    def open_model_path(self, path):
        """Open a shared model without changing the user's workspace."""
        if not self.paint_panel.can_close():
            return False
        self._paint_pending_source = None
        return self.paint_panel.open_path(path)

    def open_paint_path(self, path):
        """Open a model explicitly in Paint, preserving the existing API."""
        opened = self.open_model_path(path)
        if opened:
            self.workspace_tabs.setCurrentWidget(self.paint_panel)
        return opened

    def _on_open(self):
        if self.paint_panel is not None:
            return self.paint_panel.editor.open_dialog()
        return super()._on_open()

    def _on_open_glb(self):
        if self.paint_panel is not None:
            return self.paint_panel.editor.open_dialog()
        return super()._on_open_glb()

    def _on_load_example(self):
        if self.paint_panel is not None:
            from .example_assets import DEFAULT_GUI_EXAMPLE, find_example_file
            path = find_example_file(DEFAULT_GUI_EXAMPLE)
            if path is None:
                self._error("Load Example", "The packaged example model could not be found.")
                return
            if self.paint_panel.can_close():
                return self.paint_panel.open_path(str(path))
            return
        return super()._on_load_example()

    def _on_save(self):
        if self._paint_active() or self._shared_paint_active():
            if self._shared_paint_active() and not self._ensure_transform_preview_current():
                return
            return self.paint_panel.editor.save_dialog()
        return super()._on_save()

    def _on_weave(self):
        if self._paint_active() or self._shared_paint_active():
            if self._shared_paint_active() and not self._ensure_transform_preview_current():
                return
            return self.paint_panel.editor.export_dialog()
        return super()._on_weave()

    def _on_preview(self):
        if self._paint_active():
            return self.paint_panel.editor.fit_view()
        if self._shared_paint_active():
            self.viewer._set_default_loaded_camera()
            return
        return super()._on_preview()

    def closeEvent(self, event):
        panel = getattr(self, "paint_panel", None)
        if panel is not None:
            if not panel.can_close():
                event.ignore()
                return
            panel.shutdown()
        return super().closeEvent(event)

    def _build_toolbar(self):
        super()._build_toolbar()
        toolbar = next(iter(self.findChildren(QtWidgets.QToolBar)), None)
        if toolbar is not None:
            self._main_toolbar = toolbar
            action = QtWidgets.QAction("Paint", self)
            action.setObjectName("paintMode")
            action.setCheckable(True)
            action.setToolTip("Toggle surface painting. Paint and Arrange share the same model, colors and placement.")
            action.triggered.connect(self._set_paint_mode)
            toolbar.insertAction(toolbar.actions()[0] if toolbar.actions() else None, action)
            self._paint_action = action

    def _set_paint_mode(self, enabled):
        if enabled:
            self._open_paint_editor()
        elif self.workspace_tabs is not None:
            self.workspace_tabs.setCurrentIndex(1)

    def _open_paint_editor(self):
        if self.paint_panel is not None:
            source = getattr(self, "source_file_path", None) or getattr(self, "base_file", None)
            if self.paint_panel.editor.document is None and source and os.path.exists(source):
                self._paint_pending_source = source
            already_active = self._paint_active()
            self.workspace_tabs.setCurrentWidget(self.paint_panel)
            if already_active and self._paint_pending_source:
                self._workspace_changed(0)
