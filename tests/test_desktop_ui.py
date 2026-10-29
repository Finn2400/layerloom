from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pyvistaqt")
from PyQt5 import QtCore, QtTest, QtWidgets

from layerloom.desktop_ui import install_desktop_ui
from layerloom.example_assets import find_example_file
from layerloom.gui_app import _load_current_gui_module
from layerloom.paint_io import load_paint_document, save_paint_document

pytestmark = pytest.mark.skipif(
    os.environ.get("LAYERLOOM_RUN_DESKTOP_INTEGRATION") != "1",
    reason="Live Qt/VTK tests require a desktop graphics session; enable LAYERLOOM_RUN_DESKTOP_INTEGRATION=1.",
)


@pytest.fixture(scope="session")
def application():
    # Keep one QApplication alive across native modules and their Qt objects.
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.fixture
def desktop(application, monkeypatch):
    module = _load_current_gui_module()
    window = module.QtAssignColorsApp()
    controller = install_desktop_ui(window)
    errors = []
    monkeypatch.setattr(window, "_error", lambda title, text: errors.append((title, text)))
    window.resize(1200, 800)
    window.show()
    application.processEvents()
    yield window, controller, errors, module
    window.paint_panel.editor.dirty = False
    window.close()
    if controller.example_directory:
        controller.example_directory.cleanup()
    window.deleteLater()
    application.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    application.processEvents()


def test_welcome_actions_follow_visible_workspace_and_open_model(desktop, application, monkeypatch):
    window, controller, errors, _ = desktop
    assert install_desktop_ui(window) is controller
    assert window._paint_active()
    panel = controller.paint_welcome
    assert panel.isVisible() and not window.viewer_empty_hint.isVisible()
    buttons = panel.findChildren(QtWidgets.QPushButton)
    assert len(buttons) == 2
    for button in buttons:
        assert button.isVisible() and button.isEnabled()
        assert panel.rect().contains(button.mapTo(panel, button.rect().center()))
    viewport = window.paint_panel.editor.plotter
    center = panel.mapTo(window.paint_panel.editor, panel.rect().center())
    origin = viewport.mapTo(window.paint_panel.editor, viewport.rect().topLeft())
    assert viewport.rect().translated(origin).contains(center)
    opened = []
    monkeypatch.setattr(window, "_on_open", lambda: opened.append(True))
    open_button = next(button for button in buttons if button.text() == "Open a model")
    QtTest.QTest.mouseClick(open_button, QtCore.Qt.LeftButton)
    assert opened == [True]
    window._set_paint_mode(False)
    application.processEvents()
    assert window.viewer_empty_hint.isVisible() and not panel.isVisible()
    window._set_paint_mode(True)
    application.processEvents()
    assert panel.isVisible() and not window.viewer_empty_hint.isVisible()
    assert not errors


def test_example_preserves_source_and_initializes_shared_paint_recipes(desktop, application, tmp_path):
    window, controller, errors, _ = desktop
    source = find_example_file("tutorial_cmy_cubes.3mf")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    assert controller.try_example()
    application.processEvents()
    editor = window.paint_panel.editor
    document = editor.document
    assert not errors and window._shared_paint_active()
    assert window._shared_paint_workspace.model.document is document
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert Path(window.base_file).parent.resolve() == Path(controller.example_directory.name).resolve()
    assert Path(window.source_file_path).parent.resolve() == Path(controller.example_directory.name).resolve()
    assert window.base_file != str(source)
    assert len(document.meshes) == len(window.model_objects) == 4
    assert all(f"__ID_{oid}__" in name for oid, name in window.model_objects)
    assert {entry["token"] for entry in window.assignments.values()} == {"c", "y", "cy", "cm"}
    assert {entry["token"] for entry in editor._mapping.values()} == {"c", "y", "cy", "cm"}
    assert not editor.dirty and not editor._undo
    for mesh in document.meshes:
        np.testing.assert_array_equal(mesh.face_rgba, mesh.source_rgba)
        assert not np.all(mesh.face_rgba[:, :3] == [180, 180, 180])
    project = tmp_path / "edited-example.llpaint"
    save_paint_document(document, project)
    saved = load_paint_document(project)
    assert saved.metadata["paint_recipe_preferences"] == document.metadata["paint_recipe_preferences"]
    for original, reopened in zip(document.meshes, saved.meshes):
        np.testing.assert_array_equal(original.source_rgba, reopened.source_rgba)
    pixels = editor.plotter.screenshot(return_img=True)
    assert pixels is not None and pixels.std() > 10
    assert not controller.paint_welcome.isVisible()
    assert not window.viewer_empty_hint.isVisible()
    window._set_paint_mode(False)
    window._set_viewer_hint("Preview temporarily unavailable")
    application.processEvents()
    assert window.viewer_empty_hint.isVisible()
    assert not window.viewer_empty_hint.actions.isVisible()
    window._set_viewer_hint(None)


def test_example_export_uses_tutorial_height_after_project_reopen_without_affecting_other_models(
        desktop, monkeypatch, tmp_path):
    window, controller, errors, _ = desktop
    editor = window.paint_panel.editor
    observed = []
    exports = []

    def accept_export(dialog):
        height = dialog.findChild(QtWidgets.QDoubleSpinBox, "geometryExportLayerHeight")
        observed.append(height.value())
        return QtWidgets.QDialog.Accepted

    monkeypatch.setattr(QtWidgets.QDialog, "exec_", accept_export)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                        lambda *args: (str(tmp_path / "tutorial.3mf"), ""))
    monkeypatch.setattr(editor, "start_export", lambda output, **settings: exports.append(settings))
    assert controller.try_example()
    window._on_weave()
    project = tmp_path / "saved tutorial.llpaint"
    save_paint_document(editor.document, project)
    assert editor.open_path(project)
    window._on_weave()
    assert observed == [0.16, 0.16]
    assert [settings["layer_height"] for settings in exports] == [0.16, 0.16]

    # Opening an ordinary model must not inherit the previous tutorial's default.
    assert editor.open_path(find_example_file("tutorial_cmy_cubes.3mf"))
    window._on_weave()
    assert observed[-1] == 0.2 and exports[-1]["layer_height"] == 0.2
    assert not errors


def test_benchy_export_budget_is_visible_and_does_not_leak_to_other_models(desktop, monkeypatch, tmp_path):
    window, controller, errors, _ = desktop
    editor = window.paint_panel.editor
    source = find_example_file("tutorial_cmy_benchy_cutup.3mf")
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    observed = []

    def inspect_export(dialog):
        memory = dialog.findChild(QtWidgets.QSpinBox, "geometryExportMemoryBudget")
        summary = dialog.findChild(QtWidgets.QLabel, "geometryExportEstimate")
        assert f"Export memory budget: {memory.value():,} MiB" in summary.text()
        observed.append(memory.value())
        return QtWidgets.QDialog.Rejected

    monkeypatch.setattr(QtWidgets.QDialog, "exec_", inspect_export)
    assert controller.try_benchy_example()
    assert "larger example" in window.status_bar.currentMessage()
    assert "4,096 MiB" in window.status_bar.currentMessage()
    window._on_weave()
    project = tmp_path / "saved benchy.llpaint"
    save_paint_document(editor.document, project)
    assert editor.open_path(project)
    window._on_weave()
    assert controller.try_example()
    window._on_weave()
    assert editor.open_path(find_example_file("tutorial_cmy_cubes.3mf"))
    window._on_weave()
    assert observed == [4096, 4096, 2048, 2048]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original
    assert not errors


def test_repeating_example_replaces_private_copy_without_modifying_source(desktop, application):
    window, controller, errors, _ = desktop
    assert controller.try_example()
    first = Path(controller.example_directory.name)
    assert controller.try_example()
    application.processEvents()
    second = Path(controller.example_directory.name)
    assert first != second
    assert not first.exists()
    assert Path(window.base_file).parent.resolve() == second.resolve()
    assert second.exists() and not errors


def test_cancelled_example_keeps_edits_previous_copy_and_result(desktop, application, monkeypatch, tmp_path):
    from layerloom import desktop_ui
    window, controller, errors, _ = desktop
    assert controller.try_example()
    first = Path(controller.example_directory.name)
    editor = window.paint_panel.editor
    editor.fill_mesh(0, [255, 0, 0, 255])
    document, undo = editor.document, list(editor._undo)
    colors = document.meshes[0].face_rgba.copy()
    controller.weave_complete(tmp_path / "previous-output.3mf")
    created = []
    temporary_directory = desktop_ui.tempfile.TemporaryDirectory
    def record_temporary(*args, **kwargs):
        directory = temporary_directory(*args, **kwargs)
        created.append(Path(directory.name))
        return directory
    monkeypatch.setattr(desktop_ui.tempfile, "TemporaryDirectory", record_temporary)
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *args: QtWidgets.QMessageBox.Cancel)
    assert not controller.try_example()
    application.processEvents()
    assert editor.document is document and editor.dirty
    assert len(editor._undo) == len(undo) and all(a is b for a, b in zip(editor._undo, undo))
    np.testing.assert_array_equal(editor.document.meshes[0].face_rgba, colors)
    assert Path(controller.example_directory.name) == first and first.exists()
    assert created and all(not path.exists() for path in created)
    assert controller.result_dock.isVisible() and not errors


def test_failed_example_load_keeps_previous_private_copy(desktop, monkeypatch):
    window, controller, errors, _ = desktop
    assert controller.try_example()
    first = Path(controller.example_directory.name)
    document = window.paint_panel.editor.document
    attempted = []
    def decline(path):
        attempted.append(Path(path))
        assert Path(path).exists()
        return False
    monkeypatch.setattr(window, "open_paint_path", decline)
    assert not controller.try_example()
    assert attempted and not attempted[0].parent.exists()
    assert controller.example_directory.name == str(first) and first.exists()
    assert window.paint_panel.editor.document is document and not errors


def test_benchy_toolbar_routes_private_copy_through_paint_loader(desktop, application, monkeypatch):
    window, controller, errors, _ = desktop
    source = find_example_file("tutorial_cmy_benchy_cutup.3mf")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    loaded = []
    def decline_after_inspection(path):
        local = Path(path)
        loaded.append(local)
        assert local != source and local.name == source.name
        assert hashlib.sha256(local.read_bytes()).hexdigest() == before
        return False
    monkeypatch.setattr(window, "open_paint_path", decline_after_inspection)
    monkeypatch.setattr(window, "_load_canonical_model", lambda *args: pytest.fail("Examples must use shared Paint loading"))
    window._load_example_action.trigger()
    application.processEvents()
    assert len(loaded) == 1 and not loaded[0].parent.exists()
    assert controller.example_directory is None
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert not errors


def test_paint_completion_uses_report_layer_height_in_result_panel(desktop, application, monkeypatch, tmp_path):
    window, controller, errors, _ = desktop
    window.layer_height_spin.setValue(.16)
    output = tmp_path / "painted geometry.3mf"
    report = {"output_3mf": str(output), "layer_height_mm": .24}
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *args: None)
    window.paint_panel.editor._export_success(report)
    application.processEvents()
    assert controller.result_dock.isVisible()
    assert controller.output_path == output.resolve()
    text = controller.result_text.text()
    assert str(output) in text
    assert "0.24 mm for both first and subsequent layers" in text
    assert "disable adaptive layer height" in text
    assert "0.16" not in text and not errors


def test_legacy_weave_completion_hook_remains_available(desktop, application, tmp_path):
    window, controller, errors, _ = desktop
    window.layer_height_spin.setValue(.16)
    output = tmp_path / "legacy-woven.3mf"
    window._on_weave_complete(output)
    application.processEvents()
    assert controller.result_dock.isVisible()
    assert controller.output_path == output.resolve()
    assert "0.16 mm" in controller.result_text.text() and not errors


def test_example_save_and_export_defaults_outlive_private_copy(desktop, monkeypatch, tmp_path):
    window, controller, errors, _ = desktop
    documents = tmp_path / "Documents"
    monkeypatch.setattr(QtCore.QStandardPaths, "writableLocation", lambda _location: str(documents))
    assert controller.try_example()
    private = Path(controller.example_directory.name).resolve()
    editor = window.paint_panel.editor
    project = Path(editor.default_save_path(".llpaint"))
    geometry = Path(editor.default_save_path("_geometry.3mf"))
    assert project.parent == geometry.parent == documents
    assert project.name == "tutorial_cmy_cubes.llpaint"
    assert geometry.name == "tutorial_cmy_cubes_geometry.3mf"
    assert private not in project.parents and private not in geometry.parents
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda _parent, _title, default, _filter: (default, ""))
    assert editor.save_dialog() and project.is_file()
    saved = project.read_bytes()
    assert controller.try_example()
    assert not private.exists()
    assert project.read_bytes() == saved and not errors
