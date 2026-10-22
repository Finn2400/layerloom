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
from qtpy import QtCore, QtWidgets, QtTest
from layerloom.paint_gui import PaintWindow, PaintPanel
from layerloom.paint_model import PaintDocument, PaintMesh
from layerloom.paint_io import load_paint_document, save_paint_document


@pytest.fixture(scope="module")
def app():
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
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *a, **k: (str(output), ""))
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


def test_embedded_panel_defaults_surface_selection_and_original_color_mode(app):
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
    assert panel.editor.mapping_mode_combo.currentData() == "source"
    QtTest.QTest.mouseClick(panel.editor.map_button, QtCore.Qt.LeftButton)
    assert all(key == entry["hex"] == entry["token"] for key, entry in panel.editor._mapping.items())
    before = panel.document
    tabs.setCurrentIndex(1)
    tabs.setCurrentIndex(0)
    assert panel.document is before
    panel.editor.dirty = False
    assert panel.can_close()
    panel.shutdown()
    host.close()
    app.processEvents()
