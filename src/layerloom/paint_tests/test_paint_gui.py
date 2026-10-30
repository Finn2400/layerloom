"""Pointer-driven checks: LAYERLOOM_GUI_TESTS=1 with native macOS Qt access.

Use an appropriate virtual display on Linux. macOS VTK requires Cocoa rather
than Qt's offscreen plugin, which cannot host its native render window.
"""
import os

import numpy as np
import pytest
import trimesh

if os.environ.get("LAYERLOOM_GUI_TESTS") != "1":
    pytest.skip("Native GUI checks are opt-in: LAYERLOOM_GUI_TESTS=1", allow_module_level=True)

pytest.importorskip("pyvistaqt")
from qtpy import QtCore, QtGui, QtWidgets, QtTest
from layerloom import file_dialogs
from layerloom.paint_gui import PaintWindow, PaintPanel
from layerloom.paint_model import PaintDocument, PaintMesh
from layerloom.paint_io import load_paint_document, save_paint_document


@pytest.fixture(scope="session")
def app():
    # Keep one QApplication alive across native modules and their Qt objects.
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def window(app):
    cube = trimesh.creation.box(extents=(10, 10, 10))
    colors = np.tile([100, 120, 150, 255], (len(cube.faces), 1)).astype(np.uint8)
    mesh = PaintMesh("cube", "cube", cube.vertices, cube.faces, colors)
    win = PaintWindow(document=PaintDocument([mesh]))
    win.show()
    app.processEvents()
    win.plotter.view_xy()
    win.plotter.reset_camera()
    win.plotter.render()
    app.processEvents()
    yield win
    win.dirty = False
    win.close()
    app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    app.processEvents()


def viewport_point(window, xyz):
    renderer = window.plotter.renderer
    renderer.SetWorldPoint(*xyz, 1.0)
    renderer.WorldToDisplay()
    x, y, _ = renderer.GetDisplayPoint()
    ratio = float(window.plotter.devicePixelRatioF())
    return QtCore.QPoint(round(x / ratio), round(window.plotter.height() - y / ratio - 1))


def click_surface(window, xyz=(1.0, 0.0, 5.0)):
    pos = viewport_point(window, xyz)
    assert window.pick_at(pos) is not None, "test must click real visible geometry"
    QtTest.QTest.mouseClick(window.plotter, QtCore.Qt.LeftButton, pos=pos)
    QtWidgets.QApplication.processEvents()


def test_real_pointer_object_fill_undo_redo(window):
    assert window.plotter.renderer.lights, "loading a document must restore scene lighting"
    original = window.document.meshes[0].face_rgba.copy()
    window.tool_combo.setCurrentText("Object fill")
    window.set_paint_color("#FF0000")
    click_surface(window)
    assert np.all(window.document.meshes[0].face_rgba == [255, 0, 0, 255])
    assert window.undo_action.isEnabled()
    window.undo()
    np.testing.assert_array_equal(window.document.meshes[0].face_rgba, original)
    window.redo()
    assert np.all(window.document.meshes[0].face_rgba == [255, 0, 0, 255])


def test_pointer_brush_drag_is_one_undoable_stroke_and_erase_restores_source(window):
    original = window.document.meshes[0].source_rgba.copy()
    window.tool_combo.setCurrentText("Brush")
    window.radius_spin.setValue(1.0)
    window.set_paint_color("#00FF00")
    first = viewport_point(window, (2.0, -2.0, 5.0))
    last = viewport_point(window, (-2.0, 2.0, 5.0))
    QtTest.QTest.mousePress(window.plotter, QtCore.Qt.LeftButton, pos=first)
    QtTest.QTest.mouseMove(window.plotter, last, delay=5)
    QtTest.QTest.mouseRelease(window.plotter, QtCore.Qt.LeftButton, pos=last)
    changed = np.any(window.document.meshes[0].face_rgba != original, axis=1)
    assert 0 < changed.sum() < len(changed)
    assert len(window._undo) == 1
    window.undo()
    np.testing.assert_array_equal(window.document.meshes[0].face_rgba, original)
    window.redo()
    window.tool_combo.setCurrentText("Erase")
    click_surface(window, (2.0, -2.0, 5.0))
    hit = window.pick_at(first)
    np.testing.assert_array_equal(window.document.meshes[0].face_rgba[hit[1]], original[hit[1]])


def test_connected_fill_stays_in_clicked_color_region(window):
    mesh = window.document.meshes[0]
    # Top triangles share an edge; the other ten faces have another color.
    top = np.flatnonzero(np.all(mesh.vertices[mesh.faces, 2] == 5.0, axis=1))
    assert len(top) == 2
    mesh.face_rgba[top] = [255, 0, 0, 255]
    window._update_mesh_colors(0)
    window.tool_combo.setCurrentText("Connected fill")
    window.set_paint_color("#FFFF00")
    click_surface(window)
    yellow = np.all(mesh.face_rgba == [255, 255, 0, 255], axis=1)
    np.testing.assert_array_equal(np.flatnonzero(yellow), top)


def test_palette_preview_is_nondestructive_and_save_reopen_keeps_erase_source(window, tmp_path):
    window.mapping_mode_combo.setCurrentIndex(window.mapping_mode_combo.findData("woven"))
    window.tool_combo.setCurrentText("Object fill")
    window.set_paint_color("#FD2400")
    click_surface(window)
    painted = window.document.meshes[0].face_rgba.copy()
    assert window.assign_colors()
    assert window.mapping_table.rowCount() == 1
    window.preview_mapping.setChecked(True)
    np.testing.assert_array_equal(window.document.meshes[0].face_rgba, painted)
    output = tmp_path / "edited.llpaint"
    save_paint_document(window.document, output)
    reopened = load_paint_document(output)
    np.testing.assert_array_equal(reopened.meshes[0].face_rgba, painted)
    np.testing.assert_array_equal(reopened.meshes[0].source_rgba, window.document.meshes[0].source_rgba)
    assert reopened.metadata["paint_palette"] == "Normal"
    window.set_document(reopened)
    assert window.mapping_table.rowCount() == 1
    assert window._mapping == reopened.metadata["paint_mapping"]
    assert not window.dirty
    assert window.assign_colors()
    assert not window.dirty, "Recomputing unchanged color choices must not dirty a saved project"
    window.tool_combo.setCurrentText("Object fill")
    window.set_paint_color("#12E4AB")
    click_surface(window)
    assert "paint_mapping" not in window.document.metadata


def test_direct_mapping_and_alt_drag_navigation(window):
    window.mapping_mode_combo.setCurrentIndex(window.mapping_mode_combo.findData("direct"))
    assert window.assign_colors()
    assert all(len(entry["token"]) == 1 for entry in window._mapping.values())
    assert window.document.metadata["paint_mapping_mode"] == "direct"
    original = window.document.meshes[0].face_rgba.copy()
    window.tool_combo.setCurrentText("Brush")
    first = viewport_point(window, (0.0, 0.0, 5.0))
    last = first + QtCore.QPoint(70, 35)
    camera_before = np.array(window.plotter.camera.position)
    # QTest.mouseClick does not move the native macOS cursor. Move it to the
    # start first so the drag's mouseMove always generates a native move event.
    QtTest.QTest.mouseMove(window.plotter, first, delay=20)
    QtTest.QTest.mousePress(window.plotter, QtCore.Qt.LeftButton, QtCore.Qt.AltModifier, pos=first)
    QtTest.QTest.mouseMove(window.plotter, last, delay=5)
    QtTest.QTest.mouseRelease(window.plotter, QtCore.Qt.LeftButton, QtCore.Qt.AltModifier, pos=last)
    np.testing.assert_array_equal(window.document.meshes[0].face_rgba, original)
    assert not np.allclose(window.plotter.camera.position, camera_before)


def test_alt_drag_release_applies_final_position_when_moves_are_coalesced(window):
    window.tool_combo.setCurrentText("Surface select")
    first = viewport_point(window, (0, 0, 5))
    last = first + QtCore.QPoint(80, 40)
    before_camera = np.array(window.plotter.camera.position)
    before_paint = window.document.meshes[0].face_rgba.copy()
    # Deliberately omit MouseMove: native systems may coalesce that event.
    QtTest.QTest.mousePress(window.plotter, QtCore.Qt.LeftButton, QtCore.Qt.AltModifier, pos=first)
    QtTest.QTest.mouseRelease(window.plotter, QtCore.Qt.LeftButton, QtCore.Qt.AltModifier, pos=last)
    assert not np.allclose(window.plotter.camera.position, before_camera)
    np.testing.assert_array_equal(window.document.meshes[0].face_rgba, before_paint)
    assert window.plotter._orbit_last is None and not window._selection


def test_background_export_uses_immutable_snapshot_and_cleans_up(window, monkeypatch, tmp_path):
    from layerloom import paint_export
    captured = {}
    def fake_export(document, output, progress=None, **settings):
        captured["document"] = document
        captured["settings"] = settings
        progress(1, 1)
        return {"output_3mf": str(output), "ok": True}
    monkeypatch.setattr(paint_export, "export_painted_3mf", fake_export)
    monkeypatch.setattr(window, "_export_success", lambda report: setattr(window, "last_export_report", report))
    assert window.start_export(tmp_path / "print.3mf", mode="woven", layer_height=.2, pitch=.5)
    window.document.meshes[0].face_rgba[:] = [255, 0, 0, 255]
    for _ in range(100):
        QtTest.QTest.qWait(20)
        if window._export_thread is None:
            break
    assert window._export_thread is None
    assert window.last_export_report["ok"]
    assert captured["document"] is not window.document
    assert not np.all(captured["document"].meshes[0].face_rgba == [255, 0, 0, 255])
    assert window.export_action.isEnabled()


def click_toolbar(window, action):
    for toolbar in window.findChildren(QtWidgets.QToolBar):
        if action in toolbar.actions():
            QtTest.QTest.mouseClick(toolbar.widgetForAction(action), QtCore.Qt.LeftButton)
            QtWidgets.QApplication.processEvents()
            return
    raise AssertionError("Toolbar action is missing")


def test_surface_select_preview_apply_undo_save_preserves_source(window, monkeypatch, tmp_path):
    mesh = window.document.meshes[0]
    source = mesh.face_rgba.copy()
    window.tool_combo.setCurrentText("Surface select")
    window.edge_angle_spin.setValue(30)
    window.seed_angle_spin.setValue(60)
    window.set_paint_color("#EE6922")
    click_surface(window)
    top = np.flatnonzero(np.all(mesh.vertices[mesh.faces, 2] == 5, axis=1))
    np.testing.assert_array_equal(np.flatnonzero(window._selection[0]), top)
    np.testing.assert_array_equal(mesh.face_rgba, source)
    assert not window.dirty and window.apply_selection_button.isEnabled()
    assert np.any(window._polys[0].cell_data["paint_rgba"][top] != source[top])
    QtTest.QTest.mouseClick(window.apply_selection_button, QtCore.Qt.LeftButton)
    assert not window._selection
    changed = np.flatnonzero(np.any(mesh.face_rgba != source, axis=1))
    np.testing.assert_array_equal(changed, top)
    assert len(window._undo) == 1
    click_toolbar(window, window.undo_action)
    np.testing.assert_array_equal(mesh.face_rgba, source)
    click_toolbar(window, window.redo_action)
    output = tmp_path / "selected_surface.llpaint"
    monkeypatch.setattr(file_dialogs, "get_save_file_name", lambda *a, **k: (str(output), ""))
    click_toolbar(window, window.save_action)
    assert output.exists() and not window.dirty
    assert window.open_path(output)
    np.testing.assert_array_equal(window.document.meshes[0].source_rgba, source)
    np.testing.assert_array_equal(window.document.meshes[0].face_rgba[changed], np.tile([238, 105, 34, 255], (2, 1)))


def test_rounded_surface_pointer_selection_is_bounded_and_no_back_face_leak(window):
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=5)
    colors = np.tile([180, 120, 200, 255], (len(sphere.faces), 1)).astype(np.uint8)
    window.set_document(PaintDocument([PaintMesh("sphere", "sphere", sphere.vertices, sphere.faces, colors.copy())]))
    window.view("top")
    QtWidgets.QApplication.processEvents()
    window.tool_combo.setCurrentText("Surface select")
    window.seed_angle_spin.setValue(35)
    pos = viewport_point(window, (.2, .1, 5))
    hit = window.pick_at(pos)
    assert hit is not None
    QtTest.QTest.mouseClick(window.plotter, QtCore.Qt.LeftButton, pos=pos)
    selected = window._selection[0].copy()
    normals = window._mesh_topology(0).normals
    assert 10 < selected.sum() < len(colors) // 2
    assert np.all(normals[selected] @ normals[hit[1]] >= np.cos(np.deg2rad(35)) - 1e-10)
    window.set_paint_color("#22AA55")
    QtTest.QTest.mouseClick(window.apply_selection_button, QtCore.Qt.LeftButton)
    changed = np.any(window.document.meshes[0].face_rgba != colors, axis=1)
    np.testing.assert_array_equal(changed, selected)
    assert not np.any(changed & ((normals @ normals[hit[1]]) < 0))


def test_area_selection_shift_add_ctrl_remove_and_clear_do_not_paint(window):
    from layerloom.paint_examples import example_document
    window.set_document(example_document("cube"))
    original = window.document.meshes[0].face_rgba.copy()
    window.view("top")
    QtWidgets.QApplication.processEvents()
    window.tool_combo.setCurrentText("Area select")
    window.radius_spin.setValue(1.5)
    left = viewport_point(window, (-5, 0, 20))
    right = viewport_point(window, (5, 0, 20))
    QtTest.QTest.mouseClick(window.plotter, QtCore.Qt.LeftButton, pos=left)
    first = window._selection[0].copy()
    QtTest.QTest.mouseClick(window.plotter, QtCore.Qt.LeftButton, QtCore.Qt.ShiftModifier, pos=right)
    assert window._selection[0].sum() > first.sum()
    QtTest.QTest.mouseClick(window.plotter, QtCore.Qt.LeftButton, QtCore.Qt.ControlModifier, pos=left)
    assert not np.any(window._selection[0] & first)
    np.testing.assert_array_equal(window.document.meshes[0].face_rgba, original)
    QtTest.QTest.mouseClick(window.clear_selection_button, QtCore.Qt.LeftButton)
    assert not window._selection and not window.dirty
    np.testing.assert_array_equal(window._polys[0].cell_data["paint_rgba"], original)


def test_embedded_panel_defaults_surface_selection_and_woven_color_mode(app):
    from layerloom.paint_examples import example_document
    host = QtWidgets.QMainWindow()
    tabs = QtWidgets.QTabWidget()
    host.setCentralWidget(tabs)
    panel = PaintPanel(document=example_document("cube"))
    tabs.addTab(panel, "Paint")
    tabs.addTab(QtWidgets.QWidget(), "Arrange")
    host.resize(1200, 850)
    host.show()
    app.processEvents()
    assert not panel.editor.isWindow()
    assert panel.editor.tool == "Surface select"
    assert panel.editor.mapping_mode_combo.currentData() == "woven"
    QtTest.QTest.mouseClick(panel.editor.mapping_toggle, QtCore.Qt.LeftButton)
    app.processEvents()
    QtTest.QTest.mouseClick(panel.editor.map_button, QtCore.Qt.LeftButton)
    assert panel.editor._mapping
    assert all(not entry["token"].startswith("#") for entry in panel.editor._mapping.values())
    before = panel.document
    tabs.setCurrentIndex(1)
    tabs.setCurrentIndex(0)
    assert panel.document is before
    panel.editor.dirty = False
    assert panel.can_close()
    panel.shutdown()
    host.close()
    host.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    app.processEvents()


@pytest.mark.parametrize("color_mode", ["woven", "direct", "source"])
def test_export_action_offers_only_material_geometry_for_every_color_mode(window, monkeypatch, tmp_path, color_mode):
    assert window.mapping_mode_combo.currentData() == "woven"
    assert window.export_action.text() == "Weave"
    assert not hasattr(window, "surface_export_action")
    assert not hasattr(window, "export_surface_dialog")
    captured = {}
    def choose_mode(dialog):
        assert dialog.windowTitle() == "Weave — create printable 3MF"
        mode = dialog.findChild(QtWidgets.QComboBox, "geometryExportMode")
        assert [mode.itemData(i) for i in range(mode.count())] == ["woven", "direct", "source"]
        assert all(spin.isEnabled() for spin in dialog.findChildren(QtWidgets.QDoubleSpinBox))
        assert any("no surface paint" in label.text() for label in dialog.findChildren(QtWidgets.QLabel))
        mode.setCurrentIndex(mode.findData(color_mode))
        return QtWidgets.QDialog.Accepted
    def capture_export(output, **settings):
        captured.update(output=output, settings=settings)
        return True
    monkeypatch.setattr(QtWidgets.QDialog, "exec_", choose_mode)
    output = tmp_path / f"{color_mode}_geometry.3mf"
    monkeypatch.setattr(file_dialogs, "get_save_file_name", lambda *a, **k: (str(output), ""))
    monkeypatch.setattr(window, "start_export", capture_export)
    click_toolbar(window, window.export_action)
    assert captured["output"] == str(output)
    assert captured["settings"] == {"mode": color_mode, "layer_height": .2, "pitch": .5, "palette_name": "Normal",
                                    "memory_budget_bytes": 2 * 1024 ** 3,
                                    "max_cells": 2_000_000, "precision": .001}
    assert window.document.metadata["paint_mapping_mode"] == color_mode
    if color_mode == "source":
        assert all(key == entry["hex"] == entry["token"] for key, entry in window._mapping.items())


def open_tool_settings(window):
    assert window.tool_settings_button.isVisible() and window.tool_settings_button.isEnabled()
    QtTest.QTest.mouseClick(window.tool_settings_button, QtCore.Qt.LeftButton)
    QtWidgets.QApplication.processEvents()
    assert window.tool_settings_menu.isVisible()


def close_tool_settings(window):
    QtTest.QTest.keyClick(window.tool_settings_menu, QtCore.Qt.Key_Escape)
    QtWidgets.QApplication.processEvents()
    assert not window.tool_settings_menu.isVisible()


def test_tool_settings_show_context_and_keep_values_between_tools(window):
    assert window.tool == "Navigate"
    assert not window.tool_settings_button.isEnabled()
    assert not window.settings_summary.isVisible()

    window.tool_combo.setCurrentText("Surface select")
    open_tool_settings(window)
    assert window._surface_settings.isVisible() and not window._radius_settings.isVisible()
    window.edge_angle_spin.setValue(37)
    window.seed_angle_spin.setValue(55)
    assert "37" in window.settings_summary.text() and "55" in window.settings_summary.text()
    close_tool_settings(window)

    window.tool_combo.setCurrentText("Brush")
    open_tool_settings(window)
    assert window._radius_settings.isVisible() and not window._surface_settings.isVisible()
    window.radius_spin.setValue(2.75)
    assert "2.75" in window.settings_summary.text() and "mm" in window.settings_summary.text()
    close_tool_settings(window)

    window.tool_combo.setCurrentText("Area select")
    open_tool_settings(window)
    assert window._radius_settings.isVisible() and not window._surface_settings.isVisible()
    assert window.radius_spin.value() == pytest.approx(2.75)
    close_tool_settings(window)

    window.tool_combo.setCurrentText("Surface select")
    open_tool_settings(window)
    assert window.edge_angle_spin.value() == 37 and window.seed_angle_spin.value() == 55
    close_tool_settings(window)
    window.tool_combo.setCurrentText("Object fill")
    assert not window.tool_settings_button.isEnabled()
    assert not window.tool_settings_menu.isVisible()


def test_settings_escape_preserves_selection_and_returns_keyboard_focus(window):
    window.tool_combo.setCurrentText("Surface select")
    click_surface(window)
    before_selection = {index: mask.copy() for index, mask in window._selection.items()}
    before_paint = window.document.meshes[0].face_rgba.copy()
    open_tool_settings(window)
    window.edge_angle_spin.setFocus()
    window.edge_angle_spin.selectAll()
    QtTest.QTest.keyClicks(window.edge_angle_spin, "42")
    QtTest.QTest.keyClick(window.edge_angle_spin, QtCore.Qt.Key_Tab)
    assert window.edge_angle_spin.value() == 42
    close_tool_settings(window)
    focus = QtWidgets.QApplication.focusWidget()
    assert focus is not None and focus.window() is window
    assert not window.tool_settings_menu.isAncestorOf(focus)
    assert window.edge_angle_spin.value() == 42
    assert not window.dirty and not window._undo
    np.testing.assert_array_equal(window.document.meshes[0].face_rgba, before_paint)
    for index, mask in before_selection.items():
        np.testing.assert_array_equal(window._selection[index], mask)
    window.tool_combo.setCurrentText("Navigate")
    assert window.apply_selection_button.isEnabled() and window.clear_selection_button.isEnabled()
    QtTest.QTest.mouseClick(window.clear_selection_button, QtCore.Qt.LeftButton)
    assert not window._selection


def test_dismissing_settings_on_model_does_not_paint_through(window):
    window.tool_combo.setCurrentText("Brush")
    window.radius_spin.setValue(3)
    window.set_paint_color("#EE6922")
    before = window.document.meshes[0].face_rgba.copy()
    pos = viewport_point(window, (1, 0, 5))
    assert window.pick_at(pos) is not None
    open_tool_settings(window)
    global_pos = window.plotter.mapToGlobal(pos)
    assert not window.tool_settings_menu.geometry().contains(global_pos)
    # Send through the host QWindow, so Qt's active-popup mouse routing and
    # possible outside-click replay are exercised before the model sees it.
    QtTest.QTest.mouseClick(window.windowHandle(), QtCore.Qt.LeftButton,
                           pos=window.mapFromGlobal(global_pos))
    QtWidgets.QApplication.processEvents()
    assert not window.tool_settings_menu.isVisible()
    np.testing.assert_array_equal(window.document.meshes[0].face_rgba, before)
    assert not window._undo and not window._stroke
    assert not window.plotter._painting and not window.plotter._selecting
    click_surface(window)
    assert np.any(window.document.meshes[0].face_rgba != before)
    assert len(window._undo) == 1


def test_compact_panel_keeps_paint_controls_and_mapping_summary_visible(app):
    from layerloom.paint_examples import example_document
    host = QtWidgets.QMainWindow()
    panel = PaintPanel(document=example_document("cube", 8), compact=True)
    host.setCentralWidget(panel)
    host.resize(1000, 650)
    host.show()
    app.processEvents()
    editor = panel.editor
    try:
        assert not editor.isWindow()
        assert not editor.project_toolbar.isVisible()
        assert not editor.menuBar().isVisible() and not editor.statusBar().isVisible()
        assert editor.controls_scroll.isVisible()
        assert editor.controls_scroll.width() < host.width() * .42
        assert editor.plotter.width() > host.width() * .5
        viewport = editor.controls_scroll.viewport()
        for control in [editor.tool_combo, editor.color_button, editor.apply_selection_button,
                        editor.clear_selection_button, editor.mapping_toggle, editor.mapping_label]:
            assert control.isVisible()
            assert viewport.rect().contains(control.mapTo(viewport, control.rect().center())), control.objectName()
        assert not editor.mapping_toggle.isChecked()
        assert not editor.mapping_section.isVisible()
        QtTest.QTest.mouseClick(editor.mapping_toggle, QtCore.Qt.LeftButton)
        app.processEvents()
        assert editor.mapping_section.isVisible() and editor.mapping_table.isVisible()
        QtTest.QTest.mouseClick(editor.map_button, QtCore.Qt.LeftButton)
        assert editor._mapping
        summary = editor.mapping_label.text()
        assert "filament" in summary.lower()
        QtTest.QTest.mouseClick(editor.mapping_toggle, QtCore.Qt.LeftButton)
        app.processEvents()
        assert not editor.mapping_section.isVisible()
        assert editor.mapping_label.isVisible() and editor.mapping_label.text() == summary
        assert viewport.rect().contains(editor.mapping_label.mapTo(viewport, editor.mapping_label.rect().center()))
    finally:
        editor.dirty = False
        panel.shutdown()
        host.close()
        host.deleteLater()
        app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        app.processEvents()


def test_shared_placement_preserves_paint_history_selection_mapping_and_project(window, tmp_path):
    mesh = window.document.meshes[0]
    original_paint = mesh.face_rgba.copy()
    original_source = mesh.source_rgba.copy()
    original_faces = mesh.faces.copy()
    window.tool_combo.setCurrentText("Surface select")
    window.set_paint_color("#DD6633")
    click_surface(window)
    QtTest.QTest.mouseClick(window.apply_selection_button, QtCore.Qt.LeftButton)
    click_surface(window)
    selection = window._selection[0].copy()
    painted = mesh.face_rgba.copy()
    assert window.assign_colors()
    mapping = window._mapping
    history = window._undo
    cached_topology = window._mesh_topology(0)
    window.project_path = str(tmp_path / "shared.llpaint")
    window.dirty = False
    changes = QtTest.QSignalSpy(window.geometryChanged)

    moved = mesh.vertices * 2 + [8, 3, 6]
    mesh.vertices = moved.copy()
    assert window.refresh_geometry()
    assert len(changes) == 1 and window.dirty
    assert window._undo is history and len(history) == 1
    assert window._mapping is mapping and window.mapping_table.rowCount() == len(mapping)
    assert window.project_path == str(tmp_path / "shared.llpaint")
    np.testing.assert_array_equal(window._selection[0], selection)
    np.testing.assert_array_equal(mesh.face_rgba, painted)
    np.testing.assert_array_equal(mesh.source_rgba, original_source)
    np.testing.assert_array_equal(mesh.faces, original_faces)
    np.testing.assert_array_equal(window._polys[0].points, moved)
    assert window._mesh_topology(0) is not cached_topology
    np.testing.assert_allclose(window._mesh_topology(0).centers, moved[mesh.faces].mean(axis=1))
    assert "20.0 × 20.0 × 20.0 mm" in window.model_label.text()
    window.dirty = False
    assert not window.refresh_geometry() and not window.dirty and len(changes) == 1

    window.plotter.reset_camera()
    hit = window.pick_at(viewport_point(window, (10, 3, 16)))
    assert hit is not None and np.all(mesh.vertices[mesh.faces[hit[1]], 2] == 16)
    window.undo()
    np.testing.assert_array_equal(mesh.face_rgba, original_paint)
    np.testing.assert_array_equal(mesh.vertices, moved)
    window.redo()
    np.testing.assert_array_equal(mesh.face_rgba, painted)
    output = tmp_path / "shared.llpaint"
    save_paint_document(window.document, output)
    reopened = load_paint_document(output)
    for name in ("vertices", "faces", "face_rgba", "source_rgba"):
        np.testing.assert_array_equal(getattr(reopened.meshes[0], name), getattr(mesh, name))


def test_shared_whole_part_fill_is_one_undoable_edit_without_changing_brush(window):
    first = window.document.meshes[0].copy()
    second = first.copy()
    second.key = "second"
    second.vertices += [20, 0, 0]
    loaded = QtTest.QSignalSpy(window.documentChanged)
    window.set_document(PaintDocument([first, second]))
    assert len(loaded) == 1 and loaded[0][0] is window.document
    source = second.source_rgba.copy()
    brush = window.paint_rgba.copy()
    changes = QtTest.QSignalSpy(window.paintChanged)
    assert window.fill_mesh(1, [22, 166, 77, 255])
    assert len(window._undo) == 1 and len(changes) == 1
    np.testing.assert_array_equal(first.face_rgba, first.source_rgba)
    np.testing.assert_array_equal(second.source_rgba, source)
    np.testing.assert_array_equal(window.paint_rgba, brush)
    assert np.all(second.face_rgba == [22, 166, 77, 255])
    assert not window.fill_mesh(1, [22, 166, 77, 255])
    assert len(window._undo) == 1 and len(changes) == 1
    window.undo()
    np.testing.assert_array_equal(second.face_rgba, source)
    window.redo()
    assert np.all(second.face_rgba == [22, 166, 77, 255]) and len(changes) == 3


def test_shared_geometry_refresh_rejects_topology_changes(window):
    old_points = window._polys[0].points.copy()
    window.document.meshes[0].vertices += [2, 3, 4]
    window.document.meshes[0].faces = window.document.meshes[0].faces[::-1].copy()
    with pytest.raises(ValueError, match="preserve mesh topology"):
        window.refresh_geometry()
    np.testing.assert_array_equal(window._polys[0].points, old_points)


def test_arrange_recipe_preferences_survive_edits_and_project_roundtrip(window, tmp_path):
    from layerloom.palette_assignment import load_palette_entries
    entries = load_palette_entries("Normal")
    chosen = next(entry for entry in entries if len(entry["token"]) > 1)
    color = "#123456"
    preferences = {"Normal": {color: chosen["token"], "bad": "c", "#FFFFFF": "invalid"}}
    window.document.metadata["paint_recipe_preferences"] = preferences
    window.palette_combo.setCurrentText("Normal")
    assert window.fill_mesh(0, [18, 52, 86, 255])
    assert window.assign_colors()
    assert window._mapping[color]["token"] == chosen["token"]
    window.undo()
    assert window.assign_colors() and color not in window._mapping
    window.redo()
    assert window.assign_colors() and window._mapping[color]["token"] == chosen["token"]
    output = tmp_path / "preferred_recipe.llpaint"
    save_paint_document(window.document, output)
    window.dirty = False
    assert window.open_path(output)
    assert window.document.metadata["paint_recipe_preferences"] == preferences
    assert window._mapping[color]["token"] == chosen["token"]
    assert window._preferred_recipe_overrides(mode="source") == {}
    assert window._preferred_recipe_overrides(mode="direct") == {}
    assert window._preferred_recipe_overrides(palette_name="Simple") == {}
    window.document.metadata["paint_recipe_preferences"] = ["invalid"]
    assert window._preferred_recipe_overrides() == {}


def test_export_recovers_explicit_recipe_after_paint_undo_clears_mapping(window, monkeypatch, tmp_path):
    from layerloom import paint_export
    color = "#647896"
    window.document.metadata["paint_recipe_preferences"] = {"Normal": {color: "c"}}
    window.fill_mesh(0, [255, 0, 0, 255])
    window.undo()
    assert not window._mapping
    captured = {}

    def fake_export(document, output, **settings):
        captured.update(settings)
        return {"ok": True}

    monkeypatch.setattr(paint_export, "export_painted_3mf", fake_export)
    monkeypatch.setattr(window, "_export_success", lambda report: setattr(window, "last_export_report", report))
    assert window.start_export(tmp_path / "preferred.3mf", mode="woven", palette_name="Normal")
    for _ in range(200):
        QtTest.QTest.qWait(10)
        if window._export_thread is None:
            break
    assert window._export_thread is None
    assert window.last_export_report["ok"] is True
    assert captured["recipe_overrides"] == {color: "c"}


def test_unfinished_stroke_cannot_bypass_discard_prompt(window, monkeypatch):
    original = window.document.meshes[0].face_rgba.copy()
    window.begin_stroke()
    window.apply_faces(0, [0, 1])
    assert not window.dirty and window._stroke
    prompts = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *args: prompts.append(args) or QtWidgets.QMessageBox.Cancel)
    assert not window.can_close()
    assert len(prompts) == 1 and window.dirty and len(window._undo) == 1
    assert not window._stroke
    window.undo()
    np.testing.assert_array_equal(window.document.meshes[0].face_rgba, original)


@pytest.mark.parametrize("event_type", [QtCore.QEvent.FocusOut, QtCore.QEvent.Hide])
def test_interrupted_pointer_gesture_commits_once_and_stops_painting(window, event_type):
    window.begin_stroke()
    window.apply_faces(0, [0])
    window.plotter._painting = True
    window.plotter._selecting = True
    event = QtGui.QFocusEvent(event_type) if event_type == QtCore.QEvent.FocusOut else QtGui.QHideEvent()
    QtWidgets.QApplication.sendEvent(window.plotter, event)
    assert window.dirty and len(window._undo) == 1 and not window._stroke
    assert not window.plotter._painting and not window.plotter._selecting
    window.begin_stroke()
    window.apply_faces(0, [1])
    window.begin_stroke()  # A new press after a missed release preserves the first edit.
    assert len(window._undo) == 2


def test_vtk_preparation_failure_preserves_current_project_and_history(window, monkeypatch):
    from layerloom import paint_gui
    from layerloom.paint_examples import example_document
    window.fill_mesh(0, [40, 50, 60, 255])
    document, poly = window.document, window._polys[0]
    history = list(window._undo)
    window.project_path = "previous.llpaint"
    monkeypatch.setattr(paint_gui.pv, "PolyData", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad mesh")))
    with pytest.raises(ValueError, match="bad mesh"):
        window.set_document(example_document("cube", 8))
    assert window.document is document and window._polys[0] is poly
    assert len(window._undo) == len(history) and window._undo[0] is history[0]
    assert window.project_path == "previous.llpaint" and window.dirty


def test_invalid_optional_palette_metadata_uses_defaults(window, tmp_path):
    document = window.document.copy()
    document.metadata.update(paint_palette=[], paint_mapping_mode={"invalid": True}, warnings=42)
    path = tmp_path / "optional_metadata.llpaint"
    save_paint_document(document, path)
    assert window.open_path(path)
    assert window.palette_combo.currentText() == "Normal"
    assert window.mapping_mode_combo.currentData() == "woven"


def test_shared_color_projection_keeps_selection_local_and_handles_pending_rgb(window):
    assert window.assign_colors()
    window.preview_mapping.setChecked(True)
    mesh = window.document.meshes[0]
    original = mesh.face_rgba.copy()
    window._selection[0] = np.arange(len(mesh.faces)) == 0
    arrange = window.display_mesh_colors(0, include_selection=False)
    paint = window.display_mesh_colors(0)
    np.testing.assert_array_equal(paint[1:], arrange[1:])
    assert np.any(paint[0] != arrange[0])
    np.testing.assert_array_equal(mesh.face_rgba, original)
    mesh.face_rgba[0] = [1, 2, 3, 255]
    np.testing.assert_array_equal(window.display_mesh_colors(0, include_selection=False)[0], [1, 2, 3, 255])


def test_export_report_warning_is_visible_without_claiming_geometry_failure(window, monkeypatch):
    notices = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *args: notices.append(args))
    report = {"output_path": "model.3mf", "report_written": False,
              "warnings": ["Geometry saved; audit report is locked."]}
    window._export_success(report)
    assert window.last_export_report is report
    assert "Geometry export complete" in window.statusBar().currentMessage()
    assert "audit report warning" in window.statusBar().currentMessage()
    assert not notices
    assert window.export_result_dock.isVisible()
    assert "Geometry saved; audit report is locked." in window.export_result_dock.warnings.text()


def test_memory_preflight_rejects_before_snapshot_or_thread(window, monkeypatch, tmp_path):
    errors = []
    output = tmp_path / "existing.3mf"
    output.write_bytes(b"keep previous output")
    monkeypatch.setattr(window.document, "copy", lambda **kwargs: pytest.fail("Budget must be checked before snapshot"))
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", lambda *args: errors.append(args[-1]))
    assert not window.start_export(output, memory_budget_bytes=1)
    assert errors and "memory" in errors[0].lower()
    assert window._export_thread is None and window._export_worker is None
    assert window._export_cancel_event is None and window.export_action.isEnabled()
    assert window.export_cancel_button.isHidden() and window.progress.isHidden()
    assert output.read_bytes() == b"keep previous output"


def test_snapshot_allocation_failure_does_not_leave_running_job(window, monkeypatch, tmp_path):
    errors = []
    def out_of_memory(**kwargs):
        raise MemoryError()
    monkeypatch.setattr(window.document, "copy", out_of_memory)
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", lambda *args: errors.append(args[-1]))
    assert not window.start_export(tmp_path / "print.3mf")
    assert errors and "memory" in errors[0].lower()
    assert window._export_thread is None and window._export_worker is None
    assert window.export_action.isEnabled() and window.export_cancel_button.isHidden()


def test_cancel_worker_keeps_window_alive_until_finished_then_allows_restart(window, monkeypatch, tmp_path):
    import threading
    from layerloom import paint_export
    from layerloom.paint_export_control import ExportCancelled
    started, observed_cancel, release = threading.Event(), threading.Event(), threading.Event()
    errors, notices = [], []
    def cancellable_export(document, output, *, cancel_requested, progress, stage, **settings):
        stage("Sampling layers…")
        progress(1, 5)
        started.set()
        for _ in range(1000):
            if cancel_requested():
                observed_cancel.set()
                release.wait(5)
                raise ExportCancelled()
            release.wait(.01)
        raise RuntimeError("Test never received cancellation")
    monkeypatch.setattr(paint_export, "export_painted_3mf", cancellable_export)
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", lambda *args: errors.append(args[-1]))
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *args: notices.append(args[-1]))
    assert window.start_export(tmp_path / "cancelled.3mf")
    try:
        for _ in range(200):
            QtTest.QTest.qWait(10)
            if started.is_set():
                break
        assert started.is_set() and window.export_cancel_button.isVisible()
        assert not window.export_action.isEnabled()
        QtTest.QTest.mouseClick(window.export_cancel_button, QtCore.Qt.LeftButton)
        assert window._export_cancel_event.is_set()
        assert not window.export_cancel_button.isEnabled() and not window.cancel_export()
        assert not window.can_close() and notices
        assert not window.start_export(tmp_path / "too-soon.3mf")
        window._export_stage("Late queued progress")
        assert "Cancelling" in window.statusBar().currentMessage()
    finally:
        window.cancel_export()
        release.set()
        for _ in range(500):
            QtTest.QTest.qWait(10)
            if window._export_thread is None:
                break
    assert observed_cancel.is_set() and window._export_thread is None
    assert window.last_export_report is None and not errors
    assert "cancelled" in window.statusBar().currentMessage()
    assert window.export_cancel_button.isHidden() and window.progress.isHidden()
    assert window.export_action.isEnabled() and window.can_close()
    monkeypatch.setattr(paint_export, "export_painted_3mf", lambda *a, **k: {"ok": True})
    assert window.start_export(tmp_path / "retry.3mf")
    for _ in range(500):
        QtTest.QTest.qWait(10)
        if window._export_thread is None:
            break
    assert window._export_thread is None and window.last_export_report["ok"]
    assert window._export_cancel_event is None and not errors


def test_export_dialog_recomputes_estimate_and_blocks_excess_work(window, monkeypatch):
    from layerloom.paint_export_control import estimate_export_resources
    observed = []
    def inspect_dialog(dialog):
        memory = dialog.findChild(QtWidgets.QSpinBox, "geometryExportMemoryBudget")
        label = dialog.findChild(QtWidgets.QLabel, "geometryExportEstimate")
        buttons = dialog.findChild(QtWidgets.QDialogButtonBox)
        ok = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        assert memory.value() == 2048 and ok.isEnabled()
        assert "additional memory" in label.text() and "planning estimate" in label.text()
        assert label.parentWidget().isHidden(), "Technical estimates belong under Advanced"
        validation = dialog.findChild(QtWidgets.QLabel, "geometryExportValidation")
        assert validation.isHidden()
        layer = dialog.findChild(QtWidgets.QDoubleSpinBox, "geometryExportLayerHeight")
        pitch = dialog.findChild(QtWidgets.QDoubleSpinBox, "geometryExportPitch")
        pitch.setValue(.05)
        layer.setValue(.04)
        assert not ok.isEnabled() and "cells" in label.text()
        assert not validation.isHidden(), "Budget failures must remain visible with Advanced collapsed"
        pitch.setValue(2)
        layer.setValue(.5)
        assert ok.isEnabled() and validation.isHidden()
        expected = estimate_export_resources(window.document, pitch=2, layer_height=.5)
        assert f"{expected.estimated_cells:,}" in label.text()
        memory.setValue(4096)
        assert ok.isEnabled()
        observed.append(True)
        return QtWidgets.QDialog.Rejected
    monkeypatch.setattr(QtWidgets.QDialog, "exec_", inspect_dialog)
    window.export_dialog()
    assert observed and window._export_thread is None


def test_export_advanced_settings_pass_through_without_bypassing_memory_guard(window, monkeypatch, tmp_path):
    captured = {}
    def choose_settings(dialog):
        pitch = dialog.findChild(QtWidgets.QDoubleSpinBox, "geometryExportPitch")
        cells = dialog.findChild(QtWidgets.QDoubleSpinBox, "geometryExportCellBudget")
        memory = dialog.findChild(QtWidgets.QSpinBox, "geometryExportMemoryBudget")
        precision = dialog.findChild(QtWidgets.QComboBox, "geometryExportPrecision")
        ok = dialog.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Ok)
        pitch.setValue(.05)
        cells.setValue(20)
        memory.setValue(64)
        assert not ok.isEnabled(), "A larger sampling budget must still respect the memory budget"
        memory.setValue(8192)
        assert ok.isEnabled()
        precision.setCurrentIndex(precision.findData(.00001))
        return QtWidgets.QDialog.Accepted
    monkeypatch.setattr(QtWidgets.QDialog, "exec_", choose_settings)
    monkeypatch.setattr(file_dialogs, "get_save_file_name", lambda *a, **k: (str(tmp_path / "fine.3mf"), ""))
    monkeypatch.setattr(window, "start_export", lambda output, **settings: captured.update(settings))
    window.export_dialog()
    assert captured["max_cells"] == 20_000_000
    assert captured["precision"] == .00001
    assert captured["memory_budget_bytes"] == 8192 * 1024 ** 2


def test_invalid_project_open_preserves_current_edits_and_history(window, monkeypatch, tmp_path):
    import json
    import zipfile
    window.tool_combo.setCurrentText("Object fill")
    window.set_paint_color("#FF0000")
    click_surface(window)
    window.project_path = str(tmp_path / "current.llpaint")
    original = window.document
    history = list(window._undo)
    colors = original.meshes[0].face_rgba.copy()
    invalid = tmp_path / "invalid.llpaint"
    save_paint_document(original, invalid)
    with zipfile.ZipFile(invalid) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(files["manifest.json"])
    manifest["metadata"]["nested"] = {"value": "OVERFLOW"}
    files["manifest.json"] = json.dumps(manifest).replace('"OVERFLOW"', '1e999').encode()
    with zipfile.ZipFile(invalid, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    errors = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", lambda *args: errors.append(args[-1]))
    assert not window.open_path(invalid)
    assert errors and window.document is original and window.dirty
    assert window.project_path == str(tmp_path / "current.llpaint")
    assert len(window._undo) == len(history) and window._undo[0] is history[0]
    np.testing.assert_array_equal(original.meshes[0].face_rgba, colors)
    window.undo()
    assert not np.array_equal(original.meshes[0].face_rgba, colors)
