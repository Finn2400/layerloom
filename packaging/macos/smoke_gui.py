"""Exercise the shared Paint UI and its real export worker in a frozen app."""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


def _wait_for_export(editor, qtcore, *, timeout_ms=180000):
    """Process Qt signals until the worker and its thread have both finished."""
    loop = qtcore.QEventLoop()
    poll = qtcore.QTimer()
    poll.setInterval(25)
    poll.timeout.connect(lambda: loop.quit() if editor._export_thread is None else None)
    deadline = qtcore.QTimer()
    deadline.setSingleShot(True)
    timed_out = []
    def timeout():
        timed_out.append(True)
        editor.cancel_export()
        loop.quit()
    deadline.timeout.connect(timeout)
    poll.start()
    deadline.start(timeout_ms)
    if editor._export_thread is not None:
        loop.exec_()
    poll.stop()
    deadline.stop()
    if timed_out:
        # Do not terminate a thread while native geometry owns memory/files.
        if editor._export_thread is not None:
            cleanup = qtcore.QEventLoop()
            editor._export_thread.finished.connect(cleanup.quit)
            qtcore.QTimer.singleShot(60000, cleanup.quit)
            cleanup.exec_()
        raise TimeoutError("Paint geometry export did not finish within the smoke-test deadline")


def main(directory: str) -> int:
    output = Path(directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    model_output = Path(tempfile.mkdtemp(prefix="paint-output-", dir=output))
    report = {"frozen": bool(getattr(sys, "frozen", False)), "executable": sys.executable}
    window = None
    old_information = old_critical = old_save_dialog = None
    try:
        from layerloom.app_logging import initialize_logs
        initialize_logs()
        from layerloom.gui_app import _load_current_gui_module
        from layerloom.paint_examples import example_document
        from layerloom.paint_io import save_paint_document, load_paint_document
        import numpy as np
        gui = _load_current_gui_module()
        qt, qtcore = gui._V58.QtWidgets, gui._V58.QtCore
        app, window = gui.create_main_window()
        controller = window._desktop_controller
        errors, notices = [], []
        window._error = lambda title, message: errors.append(f"{title}: {message}")
        window._warn = lambda title, message: errors.append(f"{title}: {message}")
        old_information, old_critical = qt.QMessageBox.information, qt.QMessageBox.critical
        qt.QMessageBox.information = lambda owner, title, message, *a, **k: notices.append(f"{title}: {message}")
        qt.QMessageBox.critical = lambda owner, title, message, *a, **k: errors.append(f"{title}: {message}")
        window.show()
        app.processEvents()
        welcome_button = next((button for button in window.findChildren(qt.QPushButton, "tryExampleButton")
                               if button.isVisible()), None)
        assert welcome_button is not None, "The active workspace has no visible example action"
        welcome_button.click()
        app.processEvents()
        editor = window.paint_panel.editor
        assert editor.document is not None and window._shared_paint_active()
        assert window.model_objects and not errors, errors
        # Sample the rendered sidebar background, since a native light palette
        # can otherwise make the inherited white labels effectively invisible.
        sidebar = editor.controls_panel.grab().toImage()
        background = sidebar.pixelColor(sidebar.width() // 2, sidebar.height() - 3)
        foreground = editor.model_label.palette().color(editor.model_label.foregroundRole())
        def luminance(color):
            rgb = [color.redF(), color.greenF(), color.blueF()]
            linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
            return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
        light, dark = sorted((luminance(background), luminance(foreground)), reverse=True)
        contrast = (light + 0.05) / (dark + 0.05)
        report["paint_ui"] = {
            "application_style": app.style().objectName(),
            "sidebar_background": background.name(),
            "label_foreground": foreground.name(),
            "contrast_ratio": contrast,
        }
        assert app.style().objectName().lower() == "fusion", report["paint_ui"]
        assert contrast >= 4.5, f"Paint sidebar labels are unreadable: {report['paint_ui']}"
        image = editor.plotter.screenshot(str(output / "example-render.png"))
        assert image is not None and image.std() > 5, "Paint example render is blank"
        assert window.grab().save(str(output / "example-window.png"))

        assert editor.document.metadata["paint_export_layer_height_mm"] == 0.16
        tutorial_output = model_output / "tutorial_cubes_geometry.3mf"
        assert editor.start_export(tutorial_output, mode="woven", layer_height=.16,
                                   pitch=.5, palette_name="Normal")
        _wait_for_export(editor, qtcore)
        assert not errors and tutorial_output.is_file(), errors
        assert editor.last_export_report["layer_height_mm"] == .16
        report["tutorial_export"] = editor.last_export_report

        # Reopen a small editable project through the main window so CI checks
        # bundled paint IO as well as the main-window export worker.
        source = model_output / "painted cube.llpaint"
        save_paint_document(example_document("cube", 4), source)
        assert window.open_paint_path(source)
        # Exercise real pointer event handling in the packaged editor. QtTest is
        # deliberately excluded from desktop builds; Qt's native event API is
        # sufficient to drive the same surface selection path.
        editor.view("top")
        app.processEvents()
        original = editor.document.meshes[0].face_rgba.copy()
        original_source = editor.document.meshes[0].source_rgba.copy()
        editor.tool_combo.setCurrentText("Surface select")
        editor.set_paint_color("#FFFF00")
        renderer = editor.plotter.renderer
        renderer.SetWorldPoint(.75, .1, 4., 1.)
        renderer.WorldToDisplay()
        x, y, _ = renderer.GetDisplayPoint()
        ratio = float(editor.plotter.devicePixelRatioF())
        point = qtcore.QPoint(round(x / ratio), round(editor.plotter.height() - y / ratio - 1))
        assert editor.pick_at(point) is not None, "Pointer proof missed visible geometry"
        for kind, buttons in ((qtcore.QEvent.MouseButtonPress, qtcore.Qt.LeftButton),
                              (qtcore.QEvent.MouseButtonRelease, qtcore.Qt.NoButton)):
            event = gui._V58.QtGui.QMouseEvent(kind, qtcore.QPointF(point),
                                            qtcore.Qt.LeftButton, buttons, qtcore.Qt.NoModifier)
            qt.QApplication.sendEvent(editor.plotter, event)
        app.processEvents()
        assert editor.apply_selection_button.isEnabled(), "Surface selection failed"
        editor.apply_selection_button.click()
        changed = editor.document.meshes[0].face_rgba.copy()
        count = int(np.count_nonzero(np.any(changed != original, axis=1)))
        assert 0 < count < len(original), "Surface paint must change only the selected faces"
        editor.undo_action.trigger()
        np.testing.assert_array_equal(editor.document.meshes[0].face_rgba, original)
        editor.redo_action.trigger()
        np.testing.assert_array_equal(editor.document.meshes[0].face_rgba, changed)
        editor.view("iso")
        app.processEvents()
        assert window.grab().save(str(output / "edited-paint-window.png"))

        saved_project = model_output / "edited paint café.llpaint"
        old_save_dialog = qt.QFileDialog.getSaveFileName
        qt.QFileDialog.getSaveFileName = lambda *a, **k: (str(saved_project), "")
        try:
            window._on_save()
        finally:
            qt.QFileDialog.getSaveFileName = old_save_dialog
            old_save_dialog = None
        assert saved_project.is_file() and not editor.dirty
        saved = load_paint_document(saved_project)
        np.testing.assert_array_equal(saved.meshes[0].face_rgba, changed)
        np.testing.assert_array_equal(saved.meshes[0].source_rgba, original_source)
        assert window.open_paint_path(saved_project)
        np.testing.assert_array_equal(editor.document.meshes[0].face_rgba, changed)
        report["paint_editing"] = {"selected_faces_changed": count, "undo_redo": True,
                                  "saved_project_reopened": True,
                                  "imported_colors_preserved_for_erase": True}
        document = editor.document
        window._set_paint_mode(False)
        app.processEvents()
        assert window._shared_paint_active() and editor.document is document
        window.viewer.plotter.render()
        window.viewer.plotter.screenshot(str(output / "arrange-render.png"))
        assert window.grab().save(str(output / "arrange-window.png"))
        target = model_output / "painted_cube_geometry.3mf"
        assert editor.start_export(target, mode="woven", layer_height=.16, pitch=.5, palette_name="Normal")
        _wait_for_export(editor, qtcore)
        assert not errors, errors
        assert editor.last_export_report is not None, "Paint export returned no success report"
        assert target.is_file() and target.stat().st_size > 0, "No printable geometry was written"
        assert editor.last_export_report["export_format"] == "volumes"
        with zipfile.ZipFile(target) as archive:
            for name in archive.namelist():
                if name.endswith(".model"):
                    model = ET.fromstring(archive.read(name))
                    assert not model.findall(".//{*}colorgroup")
                    assert not any("paint_color" in element.attrib for element in model.iter())
        assert not editor.export_cancel_button.isVisible()
        assert window.grab().save(str(output / "export-window.png"))
        exported_report = editor.last_export_report
        assert window.open_paint_path(target), "The exported 3MF could not be reopened"
        app.processEvents()
        assert editor.document.meshes and not errors, errors
        assert window.grab().save(str(output / "reopened-export-window.png"))
        report.update(status="passed", output=str(target), objects=len(window.model_objects),
                      notices=notices, paint_export=exported_report, exported_3mf_reopened=True)
        return 0
    except Exception:
        report.update(status="failed", traceback=traceback.format_exc())
        return 1
    finally:
        (output / "gui-smoke.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if old_save_dialog is not None:
            qt.QFileDialog.getSaveFileName = old_save_dialog
        if old_information is not None:
            qt.QMessageBox.information = old_information
            qt.QMessageBox.critical = old_critical
        if window is not None and window.paint_panel.editor._export_thread is None:
            window.paint_panel.editor.dirty = False
            window.close()
