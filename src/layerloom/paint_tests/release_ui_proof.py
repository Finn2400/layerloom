"""Export a supplied project through the normal main-window controls.

Requires a native display. Only file choices and notification sheets are supplied
by the harness; the real options dialog, shared document and export worker run.
The output directory must be new. No layer caches or geometry replacements.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from unittest.mock import patch

from qtpy import QtCore, QtWidgets, QtTest

from layerloom import file_dialogs
from layerloom.desktop_ui import install_desktop_ui
from layerloom.gui_app import _load_current_gui_module
from layerloom.paint_tests.gui_proof import click_action, digest, screenshot
from layerloom.paint_tests.shared_workspace_proof import CaptureView, assert_shared


def max_rss():
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except ImportError:
        return None


def run(args):
    args.output.mkdir(parents=True, exist_ok=False)
    project_before = digest(args.project)
    source_before = digest(args.original) if args.original else None
    app, host = _load_current_gui_module().create_main_window()
    controller = install_desktop_ui(host)
    host.resize(1440, 950)
    host.show()
    editor = host.paint_panel.editor
    notices, dialog_errors, progress = [], [], []
    destination = args.output / "baby_dragon_woven_geometry.3mf"
    started = time.monotonic()

    def notice(_parent, title, text, *unused):
        notices.append({"title": title, "text": text})
        print(title, text, flush=True)
        return QtWidgets.QMessageBox.Ok

    def configure_dialog():
        dialog = app.activeModalWidget()
        try:
            assert isinstance(dialog, QtWidgets.QDialog) and dialog.windowTitle() == "Weave — create printable 3MF"
            advanced = next(button for button in dialog.findChildren(QtWidgets.QToolButton)
                            if button.text() == "Advanced export settings")
            QtTest.QTest.mouseClick(advanced, QtCore.Qt.LeftButton)
            for name, value in (("geometryExportLayerHeight", args.layer),
                                ("geometryExportPitch", args.pitch),
                                ("geometryExportCellBudget", args.cells / 1_000_000)):
                dialog.findChild(QtWidgets.QDoubleSpinBox, name).setValue(value)
            dialog.findChild(QtWidgets.QSpinBox, "geometryExportMemoryBudget").setValue(args.memory_mib)
            precision = dialog.findChild(QtWidgets.QComboBox, "geometryExportPrecision")
            precision.setCurrentIndex(precision.findData(args.precision))
            mode = dialog.findChild(QtWidgets.QComboBox, "geometryExportMode")
            mode.setCurrentIndex(mode.findData("woven"))
            app.processEvents()
            assert dialog.grab().save(str(args.output / "export_settings.png"))
            ok = dialog.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Ok)
            assert ok.isEnabled(), dialog.findChild(QtWidgets.QLabel, "geometryExportEstimate").text()
            QtTest.QTest.mouseClick(ok, QtCore.Qt.LeftButton)
        except Exception as exc:
            dialog_errors.append(str(exc))
            if dialog is not None:
                dialog.reject()

    try:
        with patch.object(QtWidgets.QMessageBox, "information", side_effect=notice), \
                patch.object(QtWidgets.QMessageBox, "critical", side_effect=notice):
            with patch.object(file_dialogs, "get_open_file_name", return_value=(str(args.project), "")):
                click_action(editor, editor.open_action)
            assert editor.project_path == str(args.project), notices
            assert_shared(host)
            screenshot(CaptureView(host, editor, editor.plotter), args.output / "loaded_dragon.png", host=host)
            editor.progress.valueChanged.connect(lambda value: progress.append({"layer": value, "elapsed_s": time.monotonic() - started}))
            QtCore.QTimer.singleShot(100, configure_dialog)
            with patch.object(file_dialogs, "get_save_file_name", return_value=(str(destination), "")):
                click_action(editor, editor.export_action)
            assert not dialog_errors, dialog_errors
            assert editor._export_thread is not None, notices
            last_print = 0
            while editor._export_thread is not None:
                QtTest.QTest.qWait(100)
                elapsed = time.monotonic() - started
                if elapsed - last_print >= 30:
                    print(json.dumps({"elapsed_s": round(elapsed), "layer": editor.progress.value(),
                                      "total": editor.progress.maximum(), "status": editor.statusBar().currentMessage(),
                                      "maxrss": max_rss()}), flush=True)
                    last_print = elapsed
            assert editor.last_export_report is not None, notices
            assert controller.output_path == destination.resolve()
            assert controller.result_dock.isVisible()
            screenshot(CaptureView(host, editor, editor.plotter), args.output / "completed_dragon.png", host=host)
            assert_shared(host)
            record = {"project": str(args.project), "project_sha256": project_before,
                      "original": str(args.original) if args.original else None, "original_sha256": source_before,
                      "output": str(destination), "output_sha256": digest(destination),
                      "elapsed_s": time.monotonic() - started, "maxrss": max_rss(),
                      "actual_options_dialog": True, "actual_worker": True, "no_cached_geometry": True,
                      "report": editor.last_export_report, "notices": notices, "progress": progress,
                      "project_unchanged": digest(args.project) == project_before,
                      "original_unchanged": not args.original or digest(args.original) == source_before}
            (args.output / "normal-ui-export.json").write_text(json.dumps(record, indent=2) + "\n")
            assert record["project_unchanged"] and record["original_unchanged"]
    finally:
        editor.dirty = False
        if editor._export_thread is None:
            host.close()
            app.processEvents()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--original", type=Path)
    parser.add_argument("--layer", type=float, default=.2)
    parser.add_argument("--pitch", type=float, default=.4)
    parser.add_argument("--cells", type=int, default=20_000_000)
    parser.add_argument("--memory-mib", type=int, default=12288)
    parser.add_argument("--precision", type=float, default=.00001)
    run(parser.parse_args())
