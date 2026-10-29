"""Exercise the shared Paint UI and its real export worker in a frozen app."""
from __future__ import annotations

import faulthandler
import hashlib
import json
import sys
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


EXPORT_TIMEOUT_MS = 300_000
GUI_TIMEOUT_SECONDS = 900


class _SmokeDiagnostics:
    """Persist stage and Qt-loop evidence even if the outer verifier kills us."""
    def __init__(self, output, report):
        self.output, self.report = output, report
        self.started = time.monotonic()
        self.last_heartbeat = None
        self.editor = None
        self.heartbeat = None
        self.stage = "startup"
        self.trace = (output / "gui-deadline-traceback.log").open("w", encoding="utf-8")
        report.update(timings=[], ui_heartbeat={"count": 0, "maximum_gap_seconds": 0.0},
                      deadlines={"export_seconds": EXPORT_TIMEOUT_MS / 1000,
                                 "outer_gui_seconds": GUI_TIMEOUT_SECONDS})
        self.mark("startup")
        self.arm_dump()

    def mark(self, stage, **details):
        now = time.monotonic()
        self.stage = stage
        event = {"stage": stage, "monotonic_seconds": now,
                 "elapsed_seconds": round(now - self.started, 3), **details}
        self.report["timings"].append(event)
        with (self.output / "gui-stages.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event) + "\n")
        return event

    def snapshot(self):
        snapshot = {}
        if self.editor is not None:
            try:
                snapshot = {
                    "status": self.editor.statusBar().currentMessage(),
                    "worker_thread_present": self.editor._export_thread is not None,
                    "progress": {"value": self.editor.progress.value(),
                                 "minimum": self.editor.progress.minimum(),
                                 "maximum": self.editor.progress.maximum(),
                                 "visible": self.editor.progress.isVisible()},
                }
            except Exception as exc:
                snapshot["snapshot_error"] = f"{type(exc).__name__}: {exc}"
        return snapshot

    def tick(self):
        now = time.monotonic()
        heartbeat = self.report["ui_heartbeat"]
        heartbeat["count"] += 1
        if self.last_heartbeat is not None:
            heartbeat["maximum_gap_seconds"] = max(
                heartbeat["maximum_gap_seconds"], round(now - self.last_heartbeat, 3))
        self.last_heartbeat = now
        heartbeat.update(monotonic_seconds=now, elapsed_seconds=round(now - self.started, 3),
                         stage=self.stage, **self.snapshot())
        (self.output / "gui-progress.json").write_text(json.dumps(heartbeat, indent=2) + "\n", encoding="utf-8")
        with (self.output / "gui-heartbeats.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(heartbeat) + "\n")

    def start_heartbeat(self, qtcore, window, editor):
        self.editor = editor
        self.heartbeat = qtcore.QTimer(window)
        self.heartbeat.setInterval(1000)
        self.heartbeat.timeout.connect(self.tick)
        self.last_heartbeat = time.monotonic()
        self.heartbeat.start()
        self.tick()

    def arm_dump(self, export_deadline=None):
        # faulthandler's watchdog can dump stacks even when Qt is blocked. Keep
        # a real file descriptor: windowed app stderr may be a logging wrapper.
        deadline = self.started + GUI_TIMEOUT_SECONDS - 30
        if export_deadline is not None:
            deadline = min(deadline, export_deadline - 15)
        delay = max(1.0, deadline - time.monotonic())
        try:
            faulthandler.cancel_dump_traceback_later()
            self.trace.write(f"Stage {self.stage}: traceback watchdog in {delay:.3f}s\n")
            self.trace.flush()
            faulthandler.dump_traceback_later(delay, file=self.trace)
        except (OSError, RuntimeError, ValueError) as exc:
            self.report.setdefault("diagnostic_errors", []).append(str(exc))

    def finish(self):
        if self.heartbeat is not None:
            try:
                self.heartbeat.stop()
            except RuntimeError:
                pass  # Closing a delete-on-close window can destroy its timer.
        self.report["elapsed_seconds"] = round(time.monotonic() - self.started, 3)
        faulthandler.cancel_dump_traceback_later()
        self.trace.close()


def _wait_for_export(editor, qtcore, *, diagnostics, label,
                     timeout_ms=EXPORT_TIMEOUT_MS):
    """Wait for full worker/thread completion; output existence cannot pass it."""
    started = time.monotonic()
    diagnostics.mark(label + ":waiting", **diagnostics.snapshot())
    diagnostics.arm_dump(started + timeout_ms / 1000)
    loop = qtcore.QEventLoop()
    poll = qtcore.QTimer()
    poll.setInterval(25)
    poll.timeout.connect(lambda: loop.quit() if editor._export_thread is None else None)
    deadline = qtcore.QTimer()
    deadline.setSingleShot(True)
    timed_out = []

    def timeout():
        # The worker-finished callback and this timer can both be queued while
        # native rendering is busy. Recheck completion before declaring failure.
        if editor._export_thread is None:
            diagnostics.mark(label + ":completed_at_deadline", **diagnostics.snapshot())
            loop.quit()
            return
        timed_out.append(True)
        diagnostics.mark(label + ":deadline", **diagnostics.snapshot())
        editor.cancel_export()
        loop.quit()

    deadline.timeout.connect(timeout)
    poll.start()
    deadline.start(timeout_ms)
    try:
        if editor._export_thread is not None:
            loop.exec_()
    finally:
        poll.stop()
        deadline.stop()
    if timed_out:
        # Allow bounded cooperative cleanup; never terminate native geometry.
        if editor._export_thread is not None:
            diagnostics.mark(label + ":cancel_cleanup", **diagnostics.snapshot())
            cleanup = qtcore.QEventLoop()
            cleanup_poll = qtcore.QTimer()
            cleanup_poll.setInterval(25)
            cleanup_poll.timeout.connect(lambda: cleanup.quit() if editor._export_thread is None else None)
            cleanup_deadline = qtcore.QTimer()
            cleanup_deadline.setSingleShot(True)
            cleanup_deadline.timeout.connect(cleanup.quit)
            cleanup_poll.start()
            cleanup_deadline.start(60_000)
            try:
                cleanup.exec_()
            finally:
                cleanup_poll.stop()
                cleanup_deadline.stop()
        diagnostics.mark(label + ":timed_out", wait_seconds=round(time.monotonic() - started, 3),
                         **diagnostics.snapshot())
        diagnostics.arm_dump()
        raise TimeoutError(f"Paint geometry export did not finish within {timeout_ms / 1000:g}s ({label})")
    diagnostics.mark(label + ":finished", wait_seconds=round(time.monotonic() - started, 3),
                     **diagnostics.snapshot())
    diagnostics.arm_dump()


def main(directory: str) -> int:
    output = Path(directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    model_output = Path(tempfile.mkdtemp(prefix="paint-output-", dir=output))
    report = {"frozen": bool(getattr(sys, "frozen", False)), "executable": sys.executable}
    diagnostics = _SmokeDiagnostics(output, report)
    window = None
    old_information = old_critical = old_save_dialog = None
    try:
        from layerloom.app_logging import initialize_logs
        initialize_logs()
        from layerloom.gui_app import _load_current_gui_module
        from layerloom.paint_examples import example_document
        from layerloom.paint_io import save_paint_document, load_paint_document
        import numpy as np
        diagnostics.mark("load_gui_modules")
        gui = _load_current_gui_module()
        qt, qtcore = gui._V58.QtWidgets, gui._V58.QtCore
        diagnostics.mark("create_window")
        app, window = gui.create_main_window()
        diagnostics.mark("window_created")
        controller = window._desktop_controller
        errors, notices = [], []
        window._error = lambda title, message: errors.append(f"{title}: {message}")
        window._warn = lambda title, message: errors.append(f"{title}: {message}")
        old_information, old_critical = qt.QMessageBox.information, qt.QMessageBox.critical
        qt.QMessageBox.information = lambda owner, title, message, *a, **k: notices.append(f"{title}: {message}")
        qt.QMessageBox.critical = lambda owner, title, message, *a, **k: errors.append(f"{title}: {message}")
        window.show()
        app.processEvents()
        editor = window.paint_panel.editor
        diagnostics.start_heartbeat(qtcore, window, editor)
        assert not window._paint_active() and not window._paint_action.isChecked()
        assert window.workspace_tabs.currentIndex() == 1 and editor.document is None
        assert window.viewer_empty_hint.isVisible() and not controller.paint_welcome.isVisible()
        startup_actors = window.viewer.plotter.actors
        decoration = {name: actor for name, actor in startup_actors.items()
                      if name.startswith(("__plate_ascii_", "__layer_static_ruler"))}
        assert any(name.startswith("__layer_static_ruler") for name in decoration)
        plate_actor_names = ("__build_plate__", "__plate_ascii_0__", "__plate_ascii_1__")
        for name in plate_actor_names:
            assert name in startup_actors and startup_actors[name].GetVisibility(), name
            assert startup_actors[name].GetMapper().GetInput().GetNumberOfCells() > 0, name
        startup_image = window.viewer.plotter.screenshot(str(output / "startup-plate-render.png"))
        assert startup_image is not None and startup_image.std() > 5, "Startup build plate render is blank"
        assert window.grab().save(str(output / "startup-window.png"))
        report["startup_ui"] = {"mode": "arrange", "paint_toggle_checked": False,
                                "empty_document": True, "plate_art_actors": list(plate_actor_names),
                                "welcome_visible": True}
        diagnostics.mark("arrange_startup_verified")

        def toggle_paint(enabled):
            button = window._main_toolbar.widgetForAction(window._paint_action)
            assert button is not None and button.isVisible() and button.isEnabled()
            if window._paint_active() != enabled:
                button.click()
                app.processEvents()
            assert window._paint_active() == enabled
            assert window._paint_action.isChecked() == enabled

        welcome_button = next((button for button in window.findChildren(qt.QPushButton, "tryExampleButton")
                               if button.isVisible()), None)
        assert welcome_button is not None, "The active workspace has no visible example action"
        diagnostics.mark("load_cubes")
        welcome_button.click()
        app.processEvents()
        diagnostics.mark("cubes_loaded")
        assert editor.document is not None and window._shared_paint_active()
        assert window.model_objects and not errors, errors
        assert not window._paint_active() and not window._paint_action.isChecked()
        assert not window.viewer_empty_hint.isVisible()
        for name, actor in decoration.items():
            assert window.viewer.plotter.actors.get(name) is actor and actor.GetVisibility(), name
        assert window.grab().save(str(output / "example-arrange-window.png"))
        tutorial_document = editor.document
        tutorial_vertices = [mesh.vertices.copy() for mesh in tutorial_document.meshes]
        tutorial_colors = [mesh.face_rgba.copy() for mesh in tutorial_document.meshes]
        diagnostics.mark("toggle_paint_for_cubes")
        toggle_paint(True)
        assert editor.document is tutorial_document
        for mesh, vertices, colors in zip(editor.document.meshes, tutorial_vertices, tutorial_colors):
            np.testing.assert_array_equal(mesh.vertices, vertices)
            np.testing.assert_array_equal(mesh.face_rgba, colors)
        report["startup_ui"].update(example_preserves_arrange=True, explicit_paint_toggle=True)
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
        diagnostics.mark("cubes_rendered")
        report["renderer"] = {"qt_platform": app.platformName(),
                              "device_pixel_ratio": float(editor.plotter.devicePixelRatioF())}
        try:
            render_window = editor.plotter.GetRenderWindow()
            report["renderer"].update(class_name=render_window.GetClassName(),
                                      size=list(render_window.GetSize()),
                                      capabilities=render_window.ReportCapabilities())
        except Exception as exc:
            report["renderer"]["details_unavailable"] = f"{type(exc).__name__}: {exc}"

        assert editor.document.metadata["paint_export_layer_height_mm"] == 0.16
        tutorial_output = model_output / "tutorial_cubes_geometry.3mf"
        diagnostics.mark("tutorial_export:start")
        assert editor.start_export(tutorial_output, mode="woven", layer_height=.16,
                                   pitch=.5, palette_name="Normal")
        _wait_for_export(editor, qtcore, diagnostics=diagnostics, label="tutorial_export")
        assert not errors and tutorial_output.is_file(), errors
        assert editor.last_export_report["layer_height_mm"] == .16
        report["tutorial_export"] = editor.last_export_report

        # Reopen a small editable project through the main window so CI checks
        # bundled paint IO as well as the main-window export worker.
        source = model_output / "painted cube.llpaint"
        save_paint_document(example_document("cube", 4), source)
        diagnostics.mark("open_editable_cube")
        assert window.open_paint_path(source)
        diagnostics.mark("editable_cube_opened")
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
        diagnostics.mark("apply_surface_paint")
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

        diagnostics.mark("paint_undo_redo_verified")
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
        diagnostics.mark("reopen_saved_paint")
        assert window.open_paint_path(saved_project)
        diagnostics.mark("saved_paint_reopened")
        np.testing.assert_array_equal(editor.document.meshes[0].face_rgba, changed)
        report["paint_editing"] = {"selected_faces_changed": count, "undo_redo": True,
                                  "saved_project_reopened": True,
                                  "imported_colors_preserved_for_erase": True}
        document = editor.document
        vertices_before_toggle = [mesh.vertices.copy() for mesh in document.meshes]
        diagnostics.mark("switch_to_arrange")
        toggle_paint(False)
        assert window._shared_paint_active() and editor.document is document
        for mesh, vertices in zip(document.meshes, vertices_before_toggle):
            np.testing.assert_array_equal(mesh.vertices, vertices)
        np.testing.assert_array_equal(document.meshes[0].face_rgba, changed)
        toggle_paint(True)
        assert editor.document is document
        np.testing.assert_array_equal(document.meshes[0].face_rgba, changed)
        toggle_paint(False)
        for mesh, vertices in zip(document.meshes, vertices_before_toggle):
            np.testing.assert_array_equal(mesh.vertices, vertices)
        report["paint_editing"]["toolbar_mode_roundtrip_preserves_geometry_and_paint"] = True
        for name, actor in decoration.items():
            assert window.viewer.plotter.actors.get(name) is actor and actor.GetVisibility(), name
        report["startup_ui"]["plate_art_and_rulers_survive_import_and_toggles"] = True
        window.viewer.plotter.render()
        window.viewer.plotter.screenshot(str(output / "arrange-render.png"))
        assert window.grab().save(str(output / "arrange-window.png"))
        target = model_output / "painted_cube_geometry.3mf"
        diagnostics.mark("painted_export:start")
        assert editor.start_export(target, mode="woven", layer_height=.16, pitch=.5, palette_name="Normal")
        _wait_for_export(editor, qtcore, diagnostics=diagnostics, label="painted_export")
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
        diagnostics.mark("reopen_export")
        assert window.open_paint_path(target), "The exported 3MF could not be reopened"
        app.processEvents()
        assert editor.document.meshes and not errors, errors
        assert window.grab().save(str(output / "reopened-export-window.png"))
        diagnostics.mark("export_reopened")
        reopened_objects = len(window.model_objects)
        # The real multipart tutorial carries PAT tags in 3MF object names,
        # while slicer metadata supplies different friendly display names.
        # Check the exact packaged asset, not another synthetic cube.
        from layerloom.example_assets import find_example_file
        benchy_source = find_example_file("tutorial_cmy_benchy_cutup.3mf")
        benchy_hash = hashlib.sha256(benchy_source.read_bytes()).hexdigest()
        diagnostics.mark("load_benchy")
        assert window._paint_active(), "Explicit paint-path open should enter Paint"
        assert controller.try_benchy_example(), errors
        app.processEvents()
        assert window._paint_active() and window._paint_action.isChecked(), "Example must retain Paint mode"
        benchy = editor.document
        recipes = sorted({entry["token"] for entry in editor._mapping.values()})
        assert len(benchy.meshes) == 8 and sum(len(mesh.faces) for mesh in benchy.meshes) == 253176
        assert len(editor._mapping) == 4 and recipes == ["cm", "cmy", "cy", "my"], recipes
        assert set("".join(recipes)) == {"c", "m", "y"}
        assert hashlib.sha256(benchy_source.read_bytes()).hexdigest() == benchy_hash
        assert not errors, errors
        image = editor.plotter.screenshot(str(output / "benchy-example-render.png"))
        assert image is not None and image.std() > 5, "Benchy example render is blank"
        assert window.grab().save(str(output / "benchy-example-window.png"))
        diagnostics.mark("benchy_rendered_and_recipes_verified")
        report["benchy_example"] = {"parts": 8, "faces": 253176, "paint_colors": 4,
                                   "recipes": recipes, "filaments": ["c", "m", "y"],
                                   "source_sha256": benchy_hash, "source_unchanged": True}
        report.update(status="passed", output=str(target), objects=reopened_objects,
                      notices=notices, paint_export=exported_report, exported_3mf_reopened=True)
        diagnostics.mark("assertions_passed")
        return 0
    except Exception:
        report.update(status="failed", traceback=traceback.format_exc())
        diagnostics.mark("failed", **diagnostics.snapshot())
        return 1
    finally:
        (output / "gui-smoke.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if old_save_dialog is not None:
            qt.QFileDialog.getSaveFileName = old_save_dialog
        if old_information is not None:
            qt.QMessageBox.information = old_information
            qt.QMessageBox.critical = old_critical
        try:
            if window is not None and window.paint_panel.editor._export_thread is None:
                diagnostics.mark("close_window")
                window.paint_panel.editor.dirty = False
                window.close()
                diagnostics.mark("window_closed")
        finally:
            diagnostics.finish()
            (output / "gui-smoke.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
