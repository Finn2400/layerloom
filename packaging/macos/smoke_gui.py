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
    old_information = old_critical = None
    try:
        from layerloom.app_logging import initialize_logs
        initialize_logs()
        from layerloom.gui_app import _load_current_gui_module
        from layerloom.desktop_ui import install_desktop_ui
        from layerloom.paint_examples import example_document
        from layerloom.paint_io import save_paint_document
        gui = _load_current_gui_module()
        qt, qtcore = gui._V58.QtWidgets, gui._V58.QtCore
        app = qt.QApplication.instance() or qt.QApplication(["LayerLoom Paint smoke test"])
        window = gui.QtAssignColorsApp()
        controller = install_desktop_ui(window)
        errors, notices = [], []
        window._error = lambda title, message: errors.append(f"{title}: {message}")
        window._warn = lambda title, message: errors.append(f"{title}: {message}")
        old_information, old_critical = qt.QMessageBox.information, qt.QMessageBox.critical
        qt.QMessageBox.information = lambda owner, title, message, *a, **k: notices.append(f"{title}: {message}")
        qt.QMessageBox.critical = lambda owner, title, message, *a, **k: errors.append(f"{title}: {message}")
        window.show()
        app.processEvents()
        assert controller.try_example(), errors
        app.processEvents()
        editor = window.paint_panel.editor
        assert editor.document is not None and window._shared_paint_active()
        assert window.model_objects and not errors, errors
        image = editor.plotter.screenshot(str(output / "example-render.png"))
        assert image is not None and image.std() > 5, "Paint example render is blank"
        assert window.grab().save(str(output / "example-window.png"))

        # Reopen a small editable project through the main window so CI checks
        # bundled paint IO as well as the main-window export worker.
        source = model_output / "painted cube.llpaint"
        save_paint_document(example_document("cube", 4), source)
        assert window.open_paint_path(source)
        document = editor.document
        window._set_paint_mode(False)
        app.processEvents()
        assert window._shared_paint_active() and editor.document is document
        window.viewer.plotter.render()
        window.viewer.plotter.screenshot(str(output / "arrange-render.png"))
        target = model_output / "painted_cube_geometry.3mf"
        assert editor.start_export(target, mode="woven", layer_height=.5, pitch=.5, palette_name="Normal")
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
        report.update(status="passed", output=str(target), objects=len(window.model_objects),
                      notices=notices, paint_export=editor.last_export_report)
        return 0
    except Exception:
        report.update(status="failed", traceback=traceback.format_exc())
        return 1
    finally:
        (output / "gui-smoke.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if old_information is not None:
            qt.QMessageBox.information = old_information
            qt.QMessageBox.critical = old_critical
        if window is not None and window.paint_panel.editor._export_thread is None:
            window.paint_panel.editor.dirty = False
            window.close()
