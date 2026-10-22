"""Native integration checks for painting inside LayerLoom's main window."""
import os

import numpy as np
import pytest

if os.environ.get("LAYERLOOM_GUI_TESTS") != "1":
    pytest.skip("Native GUI checks require LAYERLOOM_GUI_TESTS=1", allow_module_level=True)

from qtpy import QtCore, QtWidgets, QtTest
from layerloom.gui_app import _load_current_gui_module
from layerloom.paint_examples import example_document
from layerloom.paint_io import save_paint_document


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


def test_paint_is_default_embedded_workspace(main_window):
    assert main_window._paint_active()
    assert main_window.workspace_tabs.count() == 2
    assert not main_window.paint_panel.editor.isWindow()
    assert main_window.paint_panel.editor.window() is main_window
    assert all(not bar.isVisible() for bar in main_window._arrange_toolbars)
    assert main_window.paint_panel.editor.tool_combo.currentText() == "Surface select"


def test_main_actions_route_to_paint_and_switches_retain_edits(main_window, monkeypatch, tmp_path):
    source = tmp_path / "source.3mf"
    save_paint_document(example_document("cube", 8), source)
    assert main_window.open_paint_path(str(source))
    editor = main_window.paint_panel.editor
    editor.document.meshes[0].face_rgba[0] = [220, 40, 50, 255]
    editor.dirty = True
    painted = editor.document.meshes[0].face_rgba.copy()
    calls = []
    monkeypatch.setattr(editor, "save_dialog", lambda: calls.append("save"))
    monkeypatch.setattr(editor, "export_dialog", lambda: calls.append("export"))
    monkeypatch.setattr(editor, "open_dialog", lambda: calls.append("open"))
    main_window._on_save()
    main_window._on_weave()
    main_window._on_open()
    assert calls == ["save", "export", "open"]
    main_window.workspace_tabs.setCurrentIndex(1)
    QtWidgets.QApplication.processEvents()
    assert all(bar.isVisible() for bar in main_window._arrange_toolbars)
    main_window._open_paint_editor()
    np.testing.assert_array_equal(editor.document.meshes[0].face_rgba, painted)
    assert editor.dirty


def test_arrange_paint_button_opens_original_in_same_window(main_window, tmp_path):
    source = tmp_path / "original.3mf"
    save_paint_document(example_document("cube", 8), source)
    main_window.source_file_path = str(source)
    main_window.base_file = str(tmp_path / "normalized_wrong.3mf")
    main_window.workspace_tabs.setCurrentIndex(1)
    QtWidgets.QApplication.processEvents()
    toolbar = main_window._arrange_toolbars[0]
    button = toolbar.widgetForAction(main_window._paint_action)
    assert button is not None and button.isVisible()
    QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
    QtWidgets.QApplication.processEvents()
    assert main_window._paint_active()
    assert main_window.paint_panel.editor.document.source_path == str(source)
    assert not main_window.paint_panel.editor.isWindow()


def test_pending_arrange_import_and_main_close_respect_cancel(main_window, monkeypatch, tmp_path):
    editor = main_window.paint_panel.editor
    editor.set_document(example_document("cube", 8))
    original = editor.document
    main_window.workspace_tabs.setCurrentIndex(1)
    source = tmp_path / "sphere.3mf"
    save_paint_document(example_document("sphere", 8), source)
    main_window._paint_pending_source = str(source)
    with monkeypatch.context() as patch:
        patch.setattr(main_window.paint_panel, "can_close", lambda: False)
        main_window.workspace_tabs.setCurrentIndex(0)
        assert main_window.workspace_tabs.currentIndex() == 1
        assert editor.document is original
        assert not main_window.close()
        assert main_window.isVisible()
    main_window.workspace_tabs.setCurrentIndex(0)
    assert editor.document.source_path == str(source)
    assert main_window._paint_pending_source is None
