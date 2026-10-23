"""Native integration checks for painting inside LayerLoom's main window."""
import os

import numpy as np
import pytest

if os.environ.get("LAYERLOOM_GUI_TESTS") != "1":
    pytest.skip("Native GUI checks require LAYERLOOM_GUI_TESTS=1", allow_module_level=True)

from qtpy import QtCore, QtGui, QtWidgets, QtTest
from layerloom.gui_app import _load_current_gui_module
from layerloom.paint_examples import example_document, _write_source_color_fixture_3mf


@pytest.fixture(scope="module")
def main_module():
    return _load_current_gui_module()


@pytest.fixture(scope="session")
def app():
    # Keep one QApplication alive across native modules and their Qt objects.
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def main_window(main_module, app):
    window = main_module.QtAssignColorsApp()
    window.show()
    app.processEvents()
    yield window
    editor = window.paint_panel.editor
    late_errors = []
    # Test-scoped dialog patches may already have been restored after a failed
    # assertion. Drain a late worker before closing so its modal cannot hide
    # the original failure or leave a QThread alive when the window is deleted.
    with pytest.MonkeyPatch.context() as cleanup:
        cleanup.setattr(QtWidgets.QMessageBox, "information", lambda *args: None)
        cleanup.setattr(QtWidgets.QMessageBox, "critical",
                        lambda parent, title, message: late_errors.append(message))
        if editor._export_thread is not None:
            editor.cancel_export()
            timer = QtCore.QElapsedTimer()
            timer.start()
            while editor._export_thread is not None and timer.elapsed() < 120000:
                QtTest.QTest.qWait(20)
            assert editor._export_thread is None, "Test export worker did not finish after cancellation"
        editor.end_stroke()
        editor.dirty = False
        window.close()
        window.deleteLater()
        app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        app.processEvents()
    assert not late_errors, "Late export failure during cleanup: " + "\n".join(late_errors)


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


@pytest.mark.parametrize("edges", [False, True])
def test_overlapping_part_flashes_restore_original_edges(main_window, main_module, monkeypatch, edges):
    editor = main_window.paint_panel.editor
    editor.set_document(example_document("cube", 8))
    name = main_window.oid_to_name["1"]
    actor = main_window.viewer.actors_by_name[name]
    actor.prop.show_edges = edges
    actor.prop.edge_color = "#135790"
    original_color = actor.prop.edge_color
    callbacks = []
    monkeypatch.setattr(main_module._V58.QtCore.QTimer, "singleShot",
                        lambda delay, callback: callbacks.append(callback))
    # A new 3D pick selects its table row, then explicitly flashes the part.
    # A second pick also must not capture the temporary yellow appearance.
    main_window._handle_viewer_pick_event(name)
    main_window._handle_viewer_pick_event(name)
    assert len(callbacks) >= 2
    for callback in callbacks[:-1]:
        callback()
        assert actor.prop.show_edges
    callbacks[-1]()
    assert actor.prop.show_edges == edges
    assert actor.prop.edge_color == original_color
    assert not main_window._paint_flash_states


def test_stale_part_flash_timer_does_not_modify_replacement_model(main_window, main_module, monkeypatch):
    editor = main_window.paint_panel.editor
    editor.set_document(example_document("cube", 8))
    name = main_window.oid_to_name["1"]
    callbacks = []
    monkeypatch.setattr(main_module._V58.QtCore.QTimer, "singleShot",
                        lambda delay, callback: callbacks.append(callback))
    main_window._flash_actor_by_name(name)
    stale = callbacks[-1]
    editor.set_document(example_document("sphere", 8))
    replacement = main_window.viewer.actors_by_name[name]
    replacement.prop.show_edges = False
    replacement.prop.edge_color = "#135790"
    original_color = replacement.prop.edge_color
    main_window._flash_actor_by_name(name)
    current = callbacks[-1]
    stale()
    assert replacement.prop.show_edges
    current()
    assert not replacement.prop.show_edges
    assert replacement.prop.edge_color == original_color
    assert not main_window._paint_flash_states


def test_imported_part_id_markers_cannot_select_or_paint_another_part(main_window):
    document = example_document("cube", 8)
    first = document.meshes[0]
    first.name = "Body__ID_2____PAT_c__"
    second = first.copy()
    second.key = "second"
    second.name = "Second part"
    second.vertices += [12.0, 0.0, 0.0]
    document.meshes.append(second)
    untouched = second.face_rgba.copy()
    editor = main_window.paint_panel.editor
    editor.set_document(document)
    set_paint_mode(main_window, False)
    main_window._handle_viewer_pick_event(main_window.oid_to_name["1"])
    assert main_window._current_selected_oid() == "1"
    assert main_window._last_picked_oid == "1"
    main_window._assign_selected({"hex": "#00FFFF", "token": "c"})
    np.testing.assert_array_equal(first.face_rgba, np.tile([0, 255, 255, 255], (len(first.faces), 1)))
    np.testing.assert_array_equal(second.face_rgba, untouched)
    assert first.name == "Body__ID_2____PAT_c__"
    assert main_window._item_for_oid("1").text(0) == first.name


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
    assert toolbar_actions["Weave"].text() == "Export geometry 3MF…"
    assert all(not action.shortcut().isEmpty() for action in main_window._paint_history_actions)
    click_main_action(main_window, toolbar_actions["Save Labeled 3MF"])
    click_main_action(main_window, toolbar_actions["Weave"])
    assert calls == ["save", "export", "open", "save", "export"]
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
    assert main_window._shared_paint_active()
    assert main_window._shared_paint_workspace.model.document is main_window.paint_panel.document
    assert main_window._paint_pending_source is None
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


def test_same_path_arrange_reload_replaces_shared_model_and_respects_discard_cancel(main_window, monkeypatch, tmp_path):
    source = tmp_path / "reloaded.3mf"
    _write_source_color_fixture_3mf(example_document("cube", 8), source)
    monkeypatch.setattr(main_window, "_error", lambda title, message: pytest.fail(f"{title}: {message}"))
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), ""))
    set_paint_mode(main_window, False)
    main_window._on_open()
    set_paint_mode(main_window, True)
    editor = main_window.paint_panel.editor
    previous = editor.document
    editor.set_paint_color("#DC2832")
    editor.begin_stroke()
    editor.apply_faces(0, [0])
    editor.end_stroke()
    edited = previous.meshes[0].face_rgba.copy()
    assert editor.dirty and len(editor._undo) == 1

    replacement = example_document("cube", 12)
    replacement.meshes[0].face_rgba[:] = [0, 80, 240, 255]
    _write_source_color_fixture_3mf(replacement, source)
    source_bytes = source.read_bytes()
    set_paint_mode(main_window, False)
    with monkeypatch.context() as patch:
        patch.setattr(editor, "_confirm_discard", lambda: False)
        main_window._on_open()
        assert not main_window._paint_active()
        assert not main_window._paint_action.isChecked()
        assert editor.document is previous
        np.testing.assert_array_equal(previous.meshes[0].face_rgba, edited)
        assert editor.dirty and len(editor._undo) == 1
        assert main_window._paint_pending_source is None
    monkeypatch.setattr(editor, "_confirm_discard", lambda: True)
    main_window._on_open()
    assert main_window.source_file_path == str(source)
    set_paint_mode(main_window, True)
    assert editor.document is not previous
    np.testing.assert_allclose(editor.document.meshes[0].vertices, replacement.meshes[0].vertices)
    np.testing.assert_array_equal(editor.document.meshes[0].face_rgba, replacement.meshes[0].face_rgba)
    assert not editor.dirty and not editor._undo
    assert main_window._paint_pending_source is None
    assert source.read_bytes() == source_bytes


@pytest.mark.parametrize("failed", [False, True], ids=["cancelled", "invalid-source"])
def test_unsuccessful_arrange_open_does_not_replace_existing_paint(main_window, monkeypatch, tmp_path, failed):
    source = tmp_path / "unchanged_path.3mf"
    _write_source_color_fixture_3mf(example_document("cube", 8), source)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), ""))
    set_paint_mode(main_window, False)
    main_window._on_open()
    set_paint_mode(main_window, True)
    editor = main_window.paint_panel.editor
    previous = editor.document
    editor.set_paint_color("#DC2832")
    editor.begin_stroke()
    editor.apply_faces(0, [0])
    editor.end_stroke()
    edited = previous.meshes[0].face_rgba.copy()
    set_paint_mode(main_window, False)
    errors = []
    monkeypatch.setattr(editor, "_confirm_discard", lambda: True)
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", lambda parent, title, message: errors.append((title, message)))
    if failed:
        source.write_bytes(b"not a 3MF archive")
    else:
        monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
    main_window._on_open()
    assert bool(errors) == failed
    assert main_window._paint_pending_source is None
    set_paint_mode(main_window, True)
    assert editor.document is previous
    np.testing.assert_array_equal(previous.meshes[0].face_rgba, edited)
    assert editor.dirty and len(editor._undo) == 1


def test_arrange_open_and_placement_never_serialize_or_reparse_geometry(main_window, main_module, monkeypatch, tmp_path):
    source = tmp_path / "arrange.3mf"
    _write_source_color_fixture_3mf(example_document("cube", 8), source)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), ""))
    monkeypatch.setattr(main_window, "_normalize_incoming_3mf", lambda *a: pytest.fail("Shared Open must preserve original paint"))
    monkeypatch.setattr(main_window, "_export_ply_cache", lambda: pytest.fail("Shared viewer must use arrays"))
    monkeypatch.setattr(main_module._V58, "compute_transform_plan", lambda *a, **k: pytest.fail("Placement must use arrays"))
    monkeypatch.setattr(main_module._V58, "write_transformed_3mf", lambda *a, **k: pytest.fail("Placement must not serialize"))
    set_paint_mode(main_window, False)
    main_window._on_open()
    set_paint_mode(main_window, True)
    previous = main_window.paint_panel.document
    set_paint_mode(main_window, False)
    main_window.scale_spin.setValue(1.5)
    main_window._apply_move_delta([20.0, 30.0])
    assert main_window._paint_pending_source is None
    set_paint_mode(main_window, True)
    assert main_window.paint_panel.document is previous


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
    # Direct legacy loading is retained only as a compatibility path.
    main_window._load_canonical_model(str(source))
    main_window.source_file_path = str(source)
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
    export_controls = (editor.progress, editor.export_cancel_button)
    for control in export_controls:
        control.show()
        assert control.parentWidget() is main_window.status_bar
        assert control.isVisible()
    menu = editor.tool_settings_menu
    menu.popup(editor.plotter.mapToGlobal(QtCore.QPoint(20, 20)))
    QtWidgets.QApplication.processEvents()
    assert menu.isVisible()
    # A programmatic switch can occur while a popup owns mouse input.
    main_window._set_paint_mode(False)
    QtWidgets.QApplication.processEvents()
    assert not menu.isVisible()
    assert all(control.isVisible() for control in export_controls), (
        "Export progress and cancellation must remain visible across modes"
    )
    assert all(not action.isEnabled() for action in export_actions)
    editor.statusBar().showMessage("Hidden paint status")
    assert main_window.status_bar.currentMessage() == "Hidden paint status"
    set_paint_mode(main_window, True)
    assert all(not action.isEnabled() for action in export_actions)
    editor.export_action.setEnabled(True)
    assert all(action.isEnabled() for action in export_actions)
    for control in export_controls:
        control.hide()


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


def assert_shared_geometry(main_window):
    editor = main_window.paint_panel.editor
    workspace = main_window._shared_paint_workspace
    assert workspace.model.document is editor.document
    for oid, index in workspace.mesh_index_by_oid.items():
        mesh = editor.document.meshes[index]
        np.testing.assert_allclose(workspace.polys[index].points, mesh.vertices, atol=1e-12)
        np.testing.assert_allclose(editor._polys[index].points, mesh.vertices, atol=1e-12)
        np.testing.assert_array_equal(workspace.polys[index].faces.reshape(-1, 4)[:, 1:], mesh.faces)
        actor = main_window.viewer.actors_by_name[main_window.oid_to_name[oid]]
        assert actor.GetUserMatrix() is None, "Committed placement must not also remain on the actor"
        bounds = np.asarray(actor.GetBounds()).reshape(3, 2)
        np.testing.assert_allclose(bounds[:, 0], mesh.vertices.min(axis=0), atol=1e-7)
        np.testing.assert_allclose(bounds[:, 1], mesh.vertices.max(axis=0), atol=1e-7)


def test_paint_loaded_model_placement_save_and_reopen_share_exact_geometry(main_window, main_module, monkeypatch, tmp_path):
    from layerloom.paint_io import load_paint_document
    editor = main_window.paint_panel.editor
    document = example_document("cube", 8)
    document.source_path = str(tmp_path / "source_was_removed.3mf")
    editor.set_document(document)
    baseline = document.meshes[0].vertices.copy()
    original_colors = document.meshes[0].face_rgba.copy()
    monkeypatch.setattr(main_window, "_reload_current", lambda: pytest.fail("Shared placement must not reload source"))
    monkeypatch.setattr(main_module._V58, "compute_transform_plan", lambda *a, **k: pytest.fail("Shared placement uses arrays"))
    monkeypatch.setattr(main_module._V58, "write_transformed_3mf", lambda *a, **k: pytest.fail("Shared placement must not write a temp model"))
    set_paint_mode(main_window, False)
    main_window.scale_spin.setValue(1.75)
    main_window.rot_z_spin.setValue(30.0)
    QtTest.QTest.mouseClick(main_window.center_plate_btn, QtCore.Qt.LeftButton)
    main_window._apply_move_delta([12.0, -9.0])
    placed = document.meshes[0].vertices.copy()
    assert not np.allclose(placed, baseline)
    assert "*" in main_window.windowTitle(), "Arrange placement must retain the shared unsaved marker"
    assert_shared_geometry(main_window)
    for _ in range(3):
        set_paint_mode(main_window, True)
        set_paint_mode(main_window, False)
        assert_shared_geometry(main_window)
        np.testing.assert_array_equal(document.meshes[0].vertices, placed)
    np.testing.assert_array_equal(document.meshes[0].face_rgba, original_colors)
    saved = tmp_path / "placed.llpaint"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *a, **k: (str(saved), ""))
    main_window._on_save()
    assert saved.exists() and not editor.dirty
    reopened = load_paint_document(saved)
    np.testing.assert_array_equal(reopened.meshes[0].vertices, placed)
    np.testing.assert_array_equal(reopened.meshes[0].face_rgba, original_colors)
    assert editor.open_path(saved)
    assert_shared_geometry(main_window)
    main_window.scale_spin.setValue(1.25)
    main_window._reset_transform_to_default()
    np.testing.assert_array_equal(editor.document.meshes[0].vertices, placed)
    assert_shared_geometry(main_window)


@pytest.mark.parametrize("extension", [".glb", ".llpaint"])
def test_arrange_open_uses_same_paint_aware_parser_for_glb_and_project(main_window, monkeypatch, tmp_path, extension):
    from layerloom.paint_io import save_paint_document
    source = tmp_path / ("shared" + extension)
    if extension == ".llpaint":
        original = example_document("cube", 8)
        original.source_path = str(tmp_path / "original_no_longer_exists.3mf")
        save_paint_document(original, source)
    else:
        import trimesh
        mesh = trimesh.creation.box(extents=(.008, .006, .004))
        mesh.visual.face_colors = [30, 180, 70, 255]
        source.write_bytes(trimesh.Scene(mesh).export(file_type="glb"))
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), ""))
    monkeypatch.setattr(main_window, "_run_glb_import_pipeline", lambda *a: pytest.fail("Shared GLB import must retain its paint"))
    set_paint_mode(main_window, False)
    main_window._on_open_glb()
    assert main_window._shared_paint_active()
    assert_shared_geometry(main_window)
    current = main_window.paint_panel.document
    set_paint_mode(main_window, True)
    assert main_window.paint_panel.document is current
    if extension == ".glb":
        np.testing.assert_allclose(np.ptp(current.meshes[0].vertices, axis=0), [8, 6, 4], atol=1e-5)
    else:
        assert main_window.paint_panel.editor.project_path == str(source)
        np.testing.assert_array_equal(current.meshes[0].face_rgba, original.meshes[0].face_rgba)


def test_arrange_whole_part_paint_and_undo_update_both_views(main_window):
    editor = main_window.paint_panel.editor
    editor.set_document(example_document("cube", 8))
    original = editor.document.meshes[0].face_rgba.copy()
    source = editor.document.meshes[0].source_rgba.copy()
    set_paint_mode(main_window, False)
    main_window.parts_table.setCurrentItem(main_window.parts_table.topLevelItem(0))
    main_window._assign_selected({"hex": "#00FFFF", "token": "c"})
    np.testing.assert_array_equal(editor.document.meshes[0].face_rgba, np.tile([0, 255, 255, 255], (len(original), 1)))
    np.testing.assert_array_equal(main_window._shared_paint_workspace.polys[0].cell_data["paint_rgba"], editor.document.meshes[0].face_rgba)
    assert len(editor._undo) == 1
    click_main_action(main_window, editor.undo_action)
    np.testing.assert_array_equal(editor.document.meshes[0].face_rgba, original)
    np.testing.assert_array_equal(main_window._shared_paint_workspace.polys[0].cell_data["paint_rgba"], original)
    click_main_action(main_window, editor.redo_action)
    np.testing.assert_array_equal(main_window._shared_paint_workspace.polys[0].cell_data["paint_rgba"], editor.document.meshes[0].face_rgba)
    np.testing.assert_array_equal(editor.document.meshes[0].source_rgba, source)
    painted = editor.document.meshes[0].face_rgba.copy()
    main_window._load_palette("Simple")
    np.testing.assert_array_equal(editor.document.meshes[0].face_rgba, painted)
    assert len(editor._undo) == 1, "Browsing the Arrange palette must not edit paint"


def test_real_arrange_move_drag_commits_once_to_shared_document(main_window):
    editor = main_window.paint_panel.editor
    editor.set_document(example_document("cube", 20))
    set_paint_mode(main_window, False)
    main_window._center_model_on_plate()
    main_window.viewer.set_camera_view("top")
    QtTest.QTest.mouseClick(main_window.move_tool_btn, QtCore.Qt.LeftButton)
    QtWidgets.QApplication.processEvents()
    before = editor.document.meshes[0].vertices.copy()
    point = before.mean(axis=0)
    point[2] = before[:, 2].max()
    display = main_window.viewer._display_pos_qt(point)
    start = QtCore.QPoint(round(display[0]), round(display[1]))
    finish = start + QtCore.QPoint(30, -20)
    plotter = main_window.viewer.plotter
    host_start = plotter.mapTo(main_window, start)
    host_finish = plotter.mapTo(main_window, finish)
    native = main_window.windowHandle()
    main_window.activateWindow()
    QtTest.QTest.mousePress(native, QtCore.Qt.LeftButton, pos=host_start)
    assert main_window.viewer._active_move is not None
    QtTest.QTest.mouseMove(native, host_finish, delay=30)
    QtTest.QTest.mouseRelease(native, QtCore.Qt.LeftButton, pos=host_finish)
    QtWidgets.QApplication.processEvents()
    assert main_window.viewer._active_move is None
    assert not np.allclose(editor.document.meshes[0].vertices, before)
    assert_shared_geometry(main_window)


def test_real_arrange_rotation_drag_commits_once_to_shared_document(main_window):
    editor = main_window.paint_panel.editor
    editor.set_document(example_document("cube", 20))
    set_paint_mode(main_window, False)
    main_window._center_model_on_plate()
    main_window.viewer.set_camera_view("top")
    QtTest.QTest.mouseClick(main_window.rotate_tool_btn, QtCore.Qt.LeftButton)
    QtWidgets.QApplication.processEvents()
    viewer = main_window.viewer
    before = editor.document.meshes[0].vertices.copy()
    radius = viewer.gizmo_radius / np.sqrt(2.0)
    start_world = viewer.gizmo_center + [radius, radius, 0.0]
    finish_world = viewer.gizmo_center + [-radius, radius, 0.0]
    start = viewer._display_pos_qt(start_world)
    finish = viewer._display_pos_qt(finish_world)
    start = viewer.plotter.mapTo(main_window, QtCore.QPoint(round(start[0]), round(start[1])))
    finish = viewer.plotter.mapTo(main_window, QtCore.QPoint(round(finish[0]), round(finish[1])))
    native = main_window.windowHandle()
    main_window.activateWindow()
    QtTest.QTest.mousePress(native, QtCore.Qt.LeftButton, pos=start)
    assert viewer._active_rotation is not None
    QtTest.QTest.mouseMove(native, finish, delay=30)
    QtTest.QTest.mouseRelease(native, QtCore.Qt.LeftButton, pos=finish)
    QtWidgets.QApplication.processEvents()
    assert viewer._active_rotation is None
    assert not np.allclose(editor.document.meshes[0].vertices, before)
    assert_shared_geometry(main_window)


def test_visible_arrange_recipe_survives_undo_redo_and_geometry_export(main_window, main_module, monkeypatch, tmp_path):
    import trimesh
    from layerloom.paint_model import PaintDocument, PaintMesh
    from layerloom.palette_assignment import load_palette_entries
    from layerloom.paint_tests.test_geometry_save import _assert_geometry_only
    box = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
    box.apply_translation([0.0, 0.0, .5])
    colors = np.tile([0, 255, 255, 255], (len(box.faces), 1))
    editor = main_window.paint_panel.editor
    editor.set_document(PaintDocument([PaintMesh("cube", "Cube", box.vertices, box.faces, colors.copy())]))
    set_paint_mode(main_window, False)
    eligible = {entry["token"] for entry in load_palette_entries(editor.palette_combo.currentText())}
    assert {entry["token"] for entry in main_window.current_palette} <= eligible
    for name in ("gray", "orange", "violet", "green"):
        assert not getattr(main_window, f"add_{name}_checkbox").isEnabled()
    main_window.parts_table.setCurrentItem(main_window.parts_table.topLevelItem(0))
    swatches = main_window.palette_inner.findChildren(main_module._V58.PaletteSwatchButton)
    button = next(button for button in swatches if set(button.entry["token"]) == {"c", "m"}
                  and len(button.entry["token"]) == 2)
    entry = button.entry.copy()
    main_window.palette_scroll.ensureWidgetVisible(button)
    QtWidgets.QApplication.processEvents()
    assert button.isVisible()
    QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
    color = entry["hex"].upper()
    assert editor._mapping[color]["token"] == entry["token"]
    editor.undo()
    np.testing.assert_array_equal(editor.document.meshes[0].face_rgba, colors)
    editor.redo()
    assert editor.assign_colors()
    assert editor._mapping[color]["token"] == entry["token"]
    assert main_window.parts_table.topLevelItem(0).text(2) == entry["token"]
    errors = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *a: None)
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", lambda parent, title, message: errors.append(message))
    output = tmp_path / "selected_recipe.3mf"
    assert editor.start_export(output, mode="woven", palette_name=editor.palette_combo.currentText(),
                               layer_height=.5, pitch=.5)
    timer = QtCore.QElapsedTimer()
    timer.start()
    # This is the process's first real export, including uncached JIT setup.
    while editor._export_thread is not None and timer.elapsed() < 120000:
        QtTest.QTest.qWait(20)
    assert editor._export_thread is None, (
        f"Export still active after {timer.elapsed() / 1000:.1f}s: "
        f"{editor.statusBar().currentMessage()}; errors={errors!r}")
    assert not errors, "\n".join(errors)
    assert output.exists() and editor.last_export_report is not None
    assert editor.last_export_report["assignments"][color]["token"] == entry["token"]
    assert {row["token"] for row in editor.last_export_report["filaments"]} == {"c", "m"}
    _assert_geometry_only(output)
    editor.palette_combo.setCurrentText("Simple")
    assert main_window.palette_combo.currentText() == "Simple"
    main_window.palette_combo.setCurrentText("Normal")
    assert editor.palette_combo.currentText() == "Normal"


@pytest.mark.parametrize("initial_background, initial_text", [("#efefef", "#202020"), ("#202020", "#efefef")],
                         ids=["light-system-palette", "dark-system-palette"])
def test_real_startup_keeps_paint_controls_readable(main_module, app, tmp_path,
                                                   initial_background, initial_text):
    previous_palette = app.palette()
    previous_style = app.style().objectName()
    system_palette = QtGui.QPalette(previous_palette)
    for role in (QtGui.QPalette.Window, QtGui.QPalette.Base, QtGui.QPalette.Button):
        system_palette.setColor(role, QtGui.QColor(initial_background))
    for role in (QtGui.QPalette.WindowText, QtGui.QPalette.Text, QtGui.QPalette.ButtonText):
        system_palette.setColor(role, QtGui.QColor(initial_text))
    app.setPalette(system_palette)
    window = None

    def contrast(first, second):
        def luminance(color):
            values = [channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4
                      for channel in (color.redF(), color.greenF(), color.blueF())]
            return sum(channel * weight for channel, weight in zip(values, (.2126, .7152, .0722)))
        light, dark = sorted((luminance(first), luminance(second)), reverse=True)
        return (light + .05) / (dark + .05)

    try:
        startup_app, window = main_module.create_main_window()
        assert startup_app is app and app.style().objectName().lower() == "fusion"
        window.resize(1100, 720)
        window.show()
        assert window._desktop_controller.try_example()
        app.processEvents()
        editor = window.paint_panel.editor
        panel_image = editor.controls_panel.grab().toImage()
        # Sample the rendered surface, rather than trusting an inherited palette
        # when stylesheets or native OS colors could override the actual paint.
        background = panel_image.pixelColor(3, 3)
        assert contrast(editor.model_label.palette().color(QtGui.QPalette.WindowText), background) >= 4.5
        assert background.lightness() < 80
        settings = editor.tool_settings_button
        assert settings.isEnabled()
        for group in (QtGui.QPalette.Active, QtGui.QPalette.Inactive):
            palette = settings.palette()
            assert contrast(palette.color(group, QtGui.QPalette.ButtonText),
                            palette.color(group, QtGui.QPalette.Button)) >= 4.5
        disabled = editor.apply_selection_button.palette()
        assert contrast(disabled.color(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText),
                        disabled.color(QtGui.QPalette.Disabled, QtGui.QPalette.Button)) >= 3
        assert window.grab().save(str(tmp_path / "themed-example-window.png"))
    finally:
        if window is not None:
            window.paint_panel.editor.dirty = False
            window.close()
            if window._desktop_controller.example_directory is not None:
                window._desktop_controller.example_directory.cleanup()
            window.deleteLater()
            app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
            app.processEvents()
        app.setStyle(previous_style)
        app.setPalette(previous_palette)
