"""Native integration checks for painting inside LayerLoom's main window."""
import os

import numpy as np
import pytest

if os.environ.get("LAYERLOOM_GUI_TESTS") != "1":
    pytest.skip("Native GUI checks require LAYERLOOM_GUI_TESTS=1", allow_module_level=True)

from qtpy import QtCore, QtWidgets, QtTest
from layerloom.gui_app import _load_current_gui_module
from layerloom.paint_examples import example_document, _write_source_color_fixture_3mf


@pytest.fixture(scope="module")
def main_module():
    return _load_current_gui_module()


@pytest.fixture
def main_window(main_module):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = main_module.QtAssignColorsApp()
    window.show()
    app.processEvents()
    yield window
    window.paint_panel.editor.dirty = False
    window.close()
    app.processEvents()


def click_main_action(window, action):
    button = window._main_toolbar.widgetForAction(action)
    assert button is not None and button.isVisible()
    QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
    QtWidgets.QApplication.processEvents()


def set_paint_mode(window, enabled):
    if window._paint_active() != enabled:
        click_main_action(window, window._paint_action)
    assert window._paint_active() == enabled
    assert window._paint_action.isChecked() == enabled


def test_paint_is_default_compact_mode_with_visible_main_toolbar(main_window):
    assert main_window._paint_active()
    assert main_window.workspace_tabs.count() == 2
    assert main_window.workspace_tabs.tabBar().isHidden()
    assert not main_window.paint_panel.editor.isWindow()
    assert main_window.paint_panel.editor.window() is main_window
    assert all(bar.isVisible() for bar in main_window._arrange_toolbars)
    assert not main_window.paint_panel.editor.project_toolbar.isVisible()
    assert main_window._paint_action.text() == "Paint"
    assert main_window._paint_action.isChecked()
    assert main_window.paint_panel.editor.tool_combo.currentText() == "Surface select"


def test_main_actions_route_to_paint_and_switches_retain_edits(main_window, monkeypatch, tmp_path):
    source = tmp_path / "source.3mf"
    _write_source_color_fixture_3mf(example_document("cube", 8), source)
    assert main_window.open_paint_path(str(source))
    editor = main_window.paint_panel.editor
    original = editor.document.meshes[0].face_rgba.copy()
    editor.view("top")
    editor.set_paint_color("#DC2832")
    QtWidgets.QApplication.processEvents()
    renderer = editor.plotter.renderer
    renderer.SetWorldPoint(1.0, 0.0, 8.0, 1.0)
    renderer.WorldToDisplay()
    x, y, _ = renderer.GetDisplayPoint()
    ratio = float(editor.plotter.devicePixelRatioF())
    point = QtCore.QPoint(round(x / ratio), round(editor.plotter.height() - y / ratio - 1))
    assert editor.pick_at(point) is not None
    QtTest.QTest.mouseClick(editor.plotter, QtCore.Qt.LeftButton, pos=point)
    assert editor._selection and editor.apply_selection_button.isEnabled()
    QtTest.QTest.mouseClick(editor.apply_selection_button, QtCore.Qt.LeftButton)
    painted = editor.document.meshes[0].face_rgba.copy()
    assert np.any(painted != original) and editor.dirty
    calls = []
    monkeypatch.setattr(editor, "save_dialog", lambda: calls.append("save"))
    monkeypatch.setattr(editor, "export_dialog", lambda: calls.append("export"))
    monkeypatch.setattr(editor, "open_dialog", lambda: calls.append("open"))
    toolbar_actions = dict((original, action) for action, original in main_window._workspace_action_labels
                           if action in main_window._main_toolbar.actions())
    click_main_action(main_window, toolbar_actions["Save Labeled 3MF"])
    click_main_action(main_window, toolbar_actions["Weave"])
    click_main_action(main_window, toolbar_actions["Open 3MF"])
    assert calls == ["save", "export", "open"]
    set_paint_mode(main_window, False)
    assert all(bar.isVisible() for bar in main_window._arrange_toolbars)
    assert toolbar_actions["Weave"].text() == "Weave"
    assert all(action.shortcut().isEmpty() for action in main_window._paint_history_actions)
    set_paint_mode(main_window, True)
    np.testing.assert_array_equal(editor.document.meshes[0].face_rgba, painted)
    assert editor.dirty
    assert all(not action.shortcut().isEmpty() for action in main_window._paint_history_actions)


def test_arrange_paint_button_opens_original_in_same_window(main_window, tmp_path):
    source = tmp_path / "original.3mf"
    _write_source_color_fixture_3mf(example_document("cube", 8), source)
    main_window.source_file_path = str(source)
    main_window.base_file = str(tmp_path / "normalized_wrong.3mf")
    set_paint_mode(main_window, False)
    toolbar = main_window._arrange_toolbars[0]
    button = toolbar.widgetForAction(main_window._paint_action)
    assert button is not None and button.isVisible()
    QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
    QtWidgets.QApplication.processEvents()
    assert main_window._paint_active()
    assert main_window.paint_panel.editor.document.source_path == str(source)
    assert not main_window.paint_panel.editor.isWindow()


def test_arrange_import_then_paint_uses_original_source_colors(main_window, monkeypatch, tmp_path):
    source = tmp_path / "original_painted.3mf"
    original = example_document("cube", 8)
    _write_source_color_fixture_3mf(original, source)
    source_bytes = source.read_bytes()
    monkeypatch.setattr(main_window, "_error", lambda title, message: pytest.fail(f"{title}: {message}"))
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), ""))
    set_paint_mode(main_window, False)
    main_window._on_open()
    assert main_window.source_file_path == str(source)
    assert main_window.base_file != str(source), "Arrange must run its normal import pipeline"
    assert main_window._paint_pending_source == str(source)
    set_paint_mode(main_window, True)
    assert main_window._paint_active()
    painted = main_window.paint_panel.document
    assert painted.source_path == str(source)
    assert len(painted.meshes) == len(original.meshes)
    for actual, expected in zip(painted.meshes, original.meshes):
        np.testing.assert_array_equal(actual.face_rgba, expected.face_rgba)
        np.testing.assert_array_equal(actual.source_rgba, expected.source_rgba)
    assert main_window._paint_pending_source is None
    assert source.read_bytes() == source_bytes


def test_pending_arrange_import_and_main_close_respect_cancel(main_window, monkeypatch, tmp_path):
    editor = main_window.paint_panel.editor
    editor.set_document(example_document("cube", 8))
    original = editor.document
    set_paint_mode(main_window, False)
    source = tmp_path / "sphere.3mf"
    _write_source_color_fixture_3mf(example_document("sphere", 8), source)
    main_window._paint_pending_source = str(source)
    with monkeypatch.context() as patch:
        patch.setattr(main_window.paint_panel, "can_close", lambda: False)
        click_main_action(main_window, main_window._paint_action)
        assert main_window.workspace_tabs.currentIndex() == 1
        assert not main_window._paint_action.isChecked()
        assert editor.document is original
        assert not main_window.close()
        assert main_window.isVisible()
    set_paint_mode(main_window, True)
    assert editor.document.source_path == str(source)
    assert main_window._paint_pending_source is None


def test_arrange_save_scrubs_paint_even_when_input_has_normalized_marker(main_window, monkeypatch, tmp_path):
    from layerloom.paint_tests.test_geometry_save import _marked_painted_archive, _assert_geometry_only
    monkeypatch.setattr(main_window, "_error", lambda title, message: pytest.fail(f"{title}: {message}"))
    source = _marked_painted_archive(tmp_path, child=False)
    original = source.read_bytes()
    set_paint_mode(main_window, False)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), ""))
    main_window._on_open()
    assert main_window.source_file_path == str(source)
    output = tmp_path / "saved_geometry.3mf"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *a, **k: (str(output), ""))
    main_window._on_save()
    assert output.exists(), "The real Arrange Save action must write geometry"
    _assert_geometry_only(output)
    assert source.read_bytes() == original


def test_host_export_state_progress_status_and_tool_popup_follow_mode(main_window):
    editor = main_window.paint_panel.editor
    editor.set_document(example_document("cube", 8))
    export_actions = [action for action, original in main_window._workspace_action_labels if original == "Weave"]
    assert export_actions and all(action.isEnabled() for action in export_actions)
    editor.export_action.setEnabled(False)
    assert all(not action.isEnabled() for action in export_actions)
    editor.statusBar().showMessage("Geometry conversion in progress")
    assert main_window.status_bar.currentMessage() == "Geometry conversion in progress"
    editor.progress.show()
    assert editor.progress.parentWidget() is main_window.status_bar
    assert editor.progress.isVisible()
    menu = editor.tool_settings_menu
    menu.popup(editor.plotter.mapToGlobal(QtCore.QPoint(20, 20)))
    QtWidgets.QApplication.processEvents()
    assert menu.isVisible()
    # A programmatic switch can occur while a popup owns mouse input.
    main_window._set_paint_mode(False)
    QtWidgets.QApplication.processEvents()
    assert not menu.isVisible()
    assert editor.progress.isVisible(), "Export progress must remain visible across modes"
    assert all(action.isEnabled() for action in export_actions)
    editor.statusBar().showMessage("Hidden paint status")
    assert main_window.status_bar.currentMessage() != "Hidden paint status"
    set_paint_mode(main_window, True)
    assert all(not action.isEnabled() for action in export_actions)
    editor.export_action.setEnabled(True)
    assert all(action.isEnabled() for action in export_actions)
    editor.progress.hide()


def test_return_to_arrange_focuses_viewport_so_escape_cancels_tool(main_window, monkeypatch, tmp_path):
    source = tmp_path / "arrange_cube.3mf"
    _write_source_color_fixture_3mf(example_document("cube", 8), source)
    monkeypatch.setattr(main_window, "_error", lambda title, message: pytest.fail(f"{title}: {message}"))
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), ""))
    main_window.activateWindow()
    set_paint_mode(main_window, False)
    main_window._on_open()
    assert main_window.move_tool_btn.isEnabled()
    QtTest.QTest.mouseClick(main_window.move_tool_btn, QtCore.Qt.LeftButton)
    assert main_window.viewer.tool_mode == "move"
    set_paint_mode(main_window, True)
    set_paint_mode(main_window, False)
    assert main_window.viewer.tool_mode == "move", "Switching modes must preserve the Arrange tool"
    focused = QtWidgets.QApplication.focusWidget()
    assert focused is main_window.viewer.plotter
    QtTest.QTest.keyClick(focused, QtCore.Qt.Key_Escape)
    assert main_window.viewer.tool_mode == "normal"
    assert main_window.viewer_tool_mode == "normal"
