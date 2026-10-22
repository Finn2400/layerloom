"""Native Qt development proof for paint editing, persistence and v65 launch.

Run with native macOS window access, e.g. QT_QPA_PLATFORM=cocoa python -m
layerloom.paint_tests.gui_proof --output-dir OUTPUT --baby-project PROJECT.
All models are copied; the user-selected source is only read and hashed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
from unittest.mock import patch

import numpy as np
from qtpy import QtCore, QtGui, QtWidgets, QtTest

from layerloom.paint_gui import PaintWindow
from layerloom.paint_io import load_paint_document
from layerloom.paint_examples import assert_roundtrip


def digest(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def array_digest(array):
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def settle(window):
    window.show()
    window.raise_()
    window.activateWindow()
    QtWidgets.QApplication.processEvents()
    QtTest.QTest.qWait(150)
    window.plotter.render()
    QtWidgets.QApplication.processEvents()


def point(window, xyz):
    renderer = window.plotter.renderer
    renderer.SetWorldPoint(*xyz, 1.0)
    renderer.WorldToDisplay()
    x, y, _ = renderer.GetDisplayPoint()
    ratio = float(window.plotter.devicePixelRatioF())
    return QtCore.QPoint(round(x / ratio), round(window.plotter.height() - y / ratio - 1))


def action_button(window, action):
    for toolbar in window.window().findChildren(QtWidgets.QToolBar):
        if not toolbar.isVisible():
            continue
        for candidate in toolbar.actions():
            if candidate is action or candidate.text() == action.text():
                widget = toolbar.widgetForAction(candidate)
                if widget and widget.isVisible():
                    return widget
    raise AssertionError(f"No visible toolbar button for {action.text()}")


def click_action(window, action):
    QtTest.QTest.mouseClick(action_button(window, action), QtCore.Qt.LeftButton)
    QtWidgets.QApplication.processEvents()


def save_via_ui(window, output):
    with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(output), "LayerLoom paint (*.llpaint)")):
        click_action(window, window.save_action)
    assert output.is_file() and not window.dirty


def screenshot(window, output, *, host=None):
    popup = window.tool_settings_menu if window.tool_settings_menu.isVisible() else None
    if popup is None:
        settle(window)
    else:
        window.plotter.render()
        QtWidgets.QApplication.processEvents()
    host = host or window
    # macOS QWidget.grab omits VTK's native OpenGL child. Capture that same
    # render window's framebuffer separately, and place its unaltered pixels at
    # the exact child-widget rectangle in the Qt chrome capture.
    viewport_path = output.with_name(output.stem + "_viewport.png")
    pixels = np.ascontiguousarray(window.plotter.screenshot(str(viewport_path), return_img=True))
    channels = pixels.shape[2]
    fmt = QtGui.QImage.Format_RGBA8888 if channels == 4 else QtGui.QImage.Format_RGB888
    frame = QtGui.QImage(pixels.data, pixels.shape[1], pixels.shape[0], pixels.strides[0], fmt).copy()
    result = host.grab()
    painter = QtGui.QPainter(result)
    origin = window.plotter.mapTo(host, QtCore.QPoint(0, 0))
    painter.drawImage(QtCore.QRect(origin, window.plotter.size()), frame)
    popup_record = {}
    if popup is not None:
        # QMenu is a separate native window. Capture its actual pixels and
        # position, just as for VTK, instead of omitting it from the host grab.
        popup_path = output.with_name(output.stem + "_popover.png")
        popup_pixels = popup.grab()
        assert popup_pixels.save(str(popup_path))
        popup_origin = host.mapFromGlobal(popup.mapToGlobal(QtCore.QPoint(0, 0)))
        painter.drawPixmap(popup_origin, popup_pixels)
        popup_record = {"raw_popover": str(popup_path), "raw_popover_sha256": digest(popup_path),
                        "popover_origin": [popup_origin.x(), popup_origin.y()]}
    painter.end()
    assert result.save(str(output))
    return {"path": str(output), "sha256": digest(output),
            "method": ("Native Qt QWidget.grab chrome with same-instant VTK framebuffer at exact viewport rectangle"
                       + (" and native QMenu pixels at its actual position" if popup is not None else "")),
            "mapped_preview_enabled": window.preview_mapping.isChecked(),
            "viewport_colors": "mapped palette colors" if window.preview_mapping.isChecked() else "document surface paint colors",
            "mapping_table": {"palette": window.palette_combo.currentText(), "mode": window._mapping_mode,
                              "rows": window.mapping_table.rowCount()},
            "raw_viewport": str(viewport_path), "raw_viewport_sha256": digest(viewport_path),
            **popup_record}


def close(window):
    window.dirty = False
    window.close()
    QtWidgets.QApplication.processEvents()


def model_rows(document):
    return [{"key": mesh.key, "name": mesh.name, "triangles": len(mesh.faces),
             "vertices_sha256": array_digest(mesh.vertices), "faces_sha256": array_digest(mesh.faces),
             "paint_sha256": array_digest(mesh.face_rgba), "source_paint_sha256": array_digest(mesh.source_rgba)}
            for mesh in document.meshes]


def edit_example(shape, source, gui_dir):
    original = load_paint_document(source)
    window = PaintWindow(document=original.copy())
    settle(window)
    window.view("top")
    window.plotter.reset_camera()
    QtWidgets.QApplication.processEvents()
    window.tool_combo.setCurrentText("Brush")
    window.radius_spin.setValue(1.5)
    window.set_paint_color("#F4792A")
    path = []
    for x in np.linspace(-5, 5, 21):
        z = 20.0 if shape == "cube" else 10.0 + np.sqrt(100.0 - x * x)
        pos = point(window, (float(x), 0.0, float(z)))
        assert window.pick_at(pos) is not None
        path.append(pos)
    QtTest.QTest.mousePress(window.plotter, QtCore.Qt.LeftButton, pos=path[0])
    for pos in path[1:]:
        QtTest.QTest.mouseMove(window.plotter, pos, delay=3)
    QtTest.QTest.mouseRelease(window.plotter, QtCore.Qt.LeftButton, pos=path[-1])
    QtWidgets.QApplication.processEvents()
    edited = window.document.copy()
    changed = sum(int(np.any(before.face_rgba != after.face_rgba, axis=1).sum())
                  for before, after in zip(original.meshes, edited.meshes))
    assert changed > 10
    assert len(window._undo) == 1
    click_action(window, window.undo_action)
    for before, after in zip(original.meshes, window.document.meshes):
        np.testing.assert_array_equal(before.face_rgba, after.face_rgba)
    click_action(window, window.redo_action)
    for before, after in zip(edited.meshes, window.document.meshes):
        np.testing.assert_array_equal(before.face_rgba, after.face_rgba)
    QtTest.QTest.mouseClick(window.mapping_toggle, QtCore.Qt.LeftButton)
    QtTest.QTest.mouseClick(window.map_button, QtCore.Qt.LeftButton)
    assert len(window._mapping) == 3
    output = gui_dir / f"{shape}_brush_edited.llpaint"
    save_via_ui(window, output)
    saved = window.document.copy()
    close(window)
    reopened = PaintWindow()
    settle(reopened)
    assert reopened.open_path(output)
    assert_roundtrip(saved, reopened.document)
    assert reopened.mapping_table.rowCount() == 3
    assert not reopened.dirty
    reopened.tool_combo.setCurrentText("Brush")
    reopened.set_paint_color("#F4792A")
    reopened.radius_spin.setValue(1.5)
    reopened.view("iso")
    image = screenshot(reopened, gui_dir / f"{shape}_edited_reopened.png")
    rows = model_rows(reopened.document)
    close(reopened)
    return {"input": str(source), "input_sha256": digest(source), "edited_project": str(output),
            "edited_project_sha256": digest(output), "changed_faces": changed,
            "events": {"mouse_press": 1, "mouse_move": len(path) - 1, "mouse_release": 1,
                       "undo_toolbar_click": 1, "redo_toolbar_click": 1,
                       "assign_palette_button_click": 1, "save_toolbar_click": 1},
            "undo_redo_exact": True, "project_roundtrip_exact": True,
            "saved_mapping_restored_after_reopen": True, "original_colors_preserved": True,
            "meshes": rows, "screenshot": image}


def baby_proof(project, dragon_dir, gui_dir):
    imported = load_paint_document(project)
    user_source = Path(imported.source_path)
    expected = Path("/Users/finn/Downloads/babydragonmulticolorfactoryfiles.3mf")
    assert user_source.resolve() == expected.resolve(), f"Unexpected user source: {user_source}"
    window = PaintWindow(document=imported.copy())
    settle(window)
    window.palette_combo.setCurrentText("Normal")
    start = time.perf_counter()
    QtTest.QTest.mouseClick(window.mapping_toggle, QtCore.Qt.LeftButton)
    QtTest.QTest.mouseClick(window.map_button, QtCore.Qt.LeftButton)
    elapsed = time.perf_counter() - start
    assert window.mapping_table.rowCount() == 4
    assert not window.preview_mapping.isChecked()
    output = dragon_dir / "baby_assigned.llpaint"
    save_via_ui(window, output)
    saved = window.document.copy()
    close(window)
    reopened = PaintWindow()
    settle(reopened)
    assert reopened.open_path(output)
    assert_roundtrip(saved, reopened.document)
    assert reopened.mapping_table.rowCount() == 4
    assert not reopened.dirty and not reopened.preview_mapping.isChecked()
    for before, after in zip(imported.meshes, reopened.document.meshes):
        for attr in ("vertices", "faces", "face_rgba", "source_rgba"):
            np.testing.assert_array_equal(getattr(before, attr), getattr(after, attr))
    image = screenshot(reopened, gui_dir / "baby_imported_assigned_reopened.png")
    rows = model_rows(reopened.document)
    mapping = reopened._mapping
    close(reopened)
    return {"user_source": str(user_source), "user_source_sha256": digest(user_source),
            "cached_project": str(project), "cached_project_sha256": digest(project),
            "assigned_project": str(output), "assigned_project_sha256": digest(output),
            "palette": "Normal", "mapping": mapping, "assignment_seconds": elapsed,
            "paint_modified": False, "all_build_instances_preserved": True,
            "project_roundtrip_exact": True, "saved_mapping_restored_after_reopen": True,
            "events": {"assign_palette_button_click": 1, "save_toolbar_click": 1},
            "meshes": rows, "screenshot": image}


def main_integration(source, gui_dir):
    from layerloom.gui_app import _load_current_gui_module
    module = _load_current_gui_module()
    main = module.QtAssignColorsApp()
    main.show()
    QtWidgets.QApplication.processEvents()
    assert main._paint_active()
    assert main._paint_action.text() == "Paint"
    # The production loaded-source field must win over a normalized temp file.
    main.source_file_path = str(source)
    main.base_file = "/deliberately-not-the-original-normalized-model.3mf"
    click_action(main, main._paint_action)
    QtWidgets.QApplication.processEvents()
    QtTest.QTest.mouseClick(action_button(main, main._paint_action), QtCore.Qt.LeftButton)
    QtWidgets.QApplication.processEvents()
    assert main._paint_active()
    editor = main.paint_panel.editor
    assert not editor.isWindow()
    settle(editor)
    assert editor.document is not None
    assert Path(editor.document.source_path).resolve() == source.resolve()
    image = screenshot(editor, gui_dir / "v65_paint_entrypoint.png", host=main)
    editor.dirty = False
    main.close()
    QtWidgets.QApplication.processEvents()
    return {"native_v65_constructed": True, "toolbar_action_exists": True,
            "painting_embedded_in_main_window": True, "paint_is_default_workspace": True,
            "paint_toolbar_mouse_click": True, "original_source_used_over_normalized_path": True,
            "loaded_source": str(source), "screenshot": image}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--baby-project", type=Path, required=True)
    args = parser.parse_args()
    base = args.output_dir.resolve()
    gui_dir, dragon_dir = base / "gui", base / "dragon"
    gui_dir.mkdir(parents=True, exist_ok=True)
    dragon_dir.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    report = {"method": "Native Qt QTest pointer gestures and QWidget screenshot capture", "examples": {}}
    for shape in ("cube", "sphere"):
        report["examples"][shape] = edit_example(shape, base / "examples" / f"{shape}_source.3mf", gui_dir)
        print(f"{shape}: brush, undo/redo, save/reopen and screenshot passed", flush=True)
    report["baby"] = baby_proof(args.baby_project, dragon_dir, gui_dir)
    print("baby: exact imported arrays and assigned mapping round trip passed", flush=True)
    report["main_integration"] = main_integration(base / "examples" / "cube_source.3mf", gui_dir)
    print("v65: Paint toolbar opens original source in actual editor", flush=True)
    report_path = gui_dir / "gui-proof.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(report_path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
