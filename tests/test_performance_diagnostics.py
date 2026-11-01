"""Native diagnostics tests with controlled stalls; no network or user models."""
import json
import os
import time
import pytest

pytestmark = pytest.mark.skipif(os.environ.get('LAYERLOOM_RUN_DESKTOP_INTEGRATION') != '1',
                                reason='Requires a native Qt/VTK display')


@pytest.fixture
def performance_log(tmp_path, monkeypatch):
    import logging
    from layerloom import app_logging
    logger = logging.getLogger('layerloom')
    handlers, level, propagate = logger.handlers[:], logger.level, logger.propagate
    logger.handlers = []
    monkeypatch.setenv('LAYERLOOM_LOG_DIR', str(tmp_path / 'logs'))
    monkeypatch.setattr(app_logging, '_active_log_path', None)
    path = app_logging.initialize_logs(redirect_streams=False)
    yield path
    for handler in logger.handlers:
        handler.close()
    logger.handlers, logger.level, logger.propagate = handlers, level, propagate


def test_recording_captures_ui_stall_and_stack_then_detaches(tmp_path, performance_log):
    from qtpy import QtWidgets,QtTest
    from layerloom.gui_app import _load_current_gui_module
    from layerloom.performance_diagnostics import PerformanceRecorder
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w=_load_current_gui_module().QtAssignColorsApp();w.show();app.processEvents()
    recorder=PerformanceRecorder(w)
    try:
        recorder.start()
        w.viewer.plotter.ren_win.Render()
        # Releases the GIL so the watchdog can sample a blocked GUI thread.
        time.sleep(2.7)
        # A sample must already be on disk while the GUI thread is still blocked.
        assert 'Slow UI sample:' in performance_log.read_text()
        QtTest.QTest.qWait(250)
        recorder.stop()
        report=recorder.snapshot()
        assert report['ui_timer_delay']['max_ms']>2000
        assert report['slow_main_thread_samples']
        assert report['render_calls']['build_plate']['total_frames']>0
        assert report['viewports']['build_plate']['render_pixels'][0]>0
        assert any('renderer' in s for s in report['viewports']['build_plate']['graphics'])
        assert 'test_recording' in json.dumps(report['slow_main_thread_samples'])
        text = performance_log.read_text()
        assert 'Session environment:' in text and 'layerloom-diagnostic-session-v1' in text
        assert 'ui_timer_delay' in text and 'OpenGL renderer' in text
        assert not recorder._observers and not recorder._widgets and not recorder._thread.is_alive()
        count=report['render_calls']['build_plate']['total_frames']
        w.viewer.plotter.ren_win.Render()
        assert recorder.snapshot()['render_calls']['build_plate']['total_frames']==count
        recorder.start(); recorder.started -= 75; recorder._tick()
        assert recorder.running  # No 60-second cutoff.
        recorder.stop()
    finally:
        recorder.stop();w.close();w.deleteLater();app.processEvents()


def test_installation_button_checks_real_worker_and_handles_timeout(monkeypatch, performance_log):
    import sys
    from qtpy import QtWidgets, QtCore, QtTest
    from layerloom.gui_app import _load_current_gui_module
    from layerloom.performance_diagnostics import PerformanceDialog
    from layerloom import runtime
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = _load_current_gui_module().QtAssignColorsApp()
    window.show(); app.processEvents()
    dialog = PerformanceDialog(window)
    def wait_for_report():
        deadline = time.monotonic() + 10
        while dialog.probe.state() != QtCore.QProcess.NotRunning and time.monotonic() < deadline:
            QtTest.QTest.qWait(50)
        app.processEvents()
        assert dialog.probe.state() == QtCore.QProcess.NotRunning
    try:
        dialog.check_installation(); wait_for_report()
        assert dialog.installation_report['ok'], dialog.installation_report
        assert dialog.installation_report['lib3mf']['ok']
        dialog.copy_report()
        assert json.loads(app.clipboard().text())['worker_check']['ok']
        assert 'Worker installation check:' in performance_log.read_text()
        # A mismatched/wrong worker must not be reported as healthy.
        monkeypatch.setattr(runtime, '_worker_prefix', lambda: [sys.executable, '-c',
            'import json;print(json.dumps({"version":"old", "lib3mf":{"ok":True},"packages":{}}))'])
        dialog.check_installation(); wait_for_report()
        assert not dialog.installation_report['ok'] and dialog.installation_report['mismatches']
        monkeypatch.setattr(runtime, '_worker_prefix', lambda: [sys.executable, '-c', 'import time;time.sleep(30)'])
        dialog.check_installation()
        assert not dialog.start_button.isEnabled()
        dialog.probe_timer.start(20)
        wait_for_report()
        assert not dialog.installation_report['ok']
        assert 'timed out' in dialog.installation_report['error']
        assert dialog.start_button.isEnabled() and dialog.check_button.isEnabled()
        monkeypatch.setattr(runtime, '_worker_prefix', lambda: ['/nonexistent/layerloom-worker'])
        dialog.check_installation(); wait_for_report()
        assert not dialog.installation_report['ok']
        assert dialog.start_button.isEnabled() and dialog.check_button.isEnabled()
    finally:
        dialog.close();window.close();window.deleteLater();app.processEvents()


def test_general_session_survives_hidden_panel_logs_work_and_stops_on_exit(monkeypatch, performance_log, tmp_path):
    import logging
    import numpy as np
    from qtpy import QtWidgets
    from layerloom.gui_app import _load_current_gui_module
    from layerloom.desktop_ui import install_desktop_ui
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = _load_current_gui_module().QtAssignColorsApp()
    controller = install_desktop_ui(window)
    window.show(); app.processEvents()
    controller.performance_diagnostics(); dialog = controller._performance_dialog
    recorder = dialog.recorder
    logger = logging.getLogger('layerloom')
    initial_level = logger.level
    initial_handlers = len(logger.handlers)
    monkeypatch.setattr(QtWidgets.QMessageBox, 'critical', lambda *_: None)
    try:
        dialog.start(); dialog.close(); app.processEvents()
        assert recorder.running and dialog.indicator.isVisible()
        assert controller.try_example()
        window._set_paint_mode(True)
        editor = window.paint_panel.editor
        editor.paint_rgba = np.array([255, 0, 0, 255], dtype=np.uint8)
        editor.begin_stroke(); editor.apply_faces(0, [0]); editor.end_stroke()
        assert not editor.start_export(tmp_path / 'blocked.3mf', max_cells=1)
        recorder.started -= 75
        recorder._last_checkpoint -= 75
        recorder._tick()
        assert recorder.running
        log = recorder.session_path
        text = log.read_text()
        for entry in ('Opening model:', 'Document loaded:', 'Workspace: Paint', 'paintChanged',
                      'Export requested:', 'Export failed:', 'Session checkpoint:'):
            assert entry in text, entry
        # Bounded log rotation, even when activity is verbose.
        recorder._session_handler.maxBytes = 1000
        for _ in range(12): logger.info('rotation test %s', 'x' * 600)
        assert len(list(log.parent.glob('diagnostic-session.log*'))) == 4
        editor.dirty = False
        window.close(); app.processEvents()
        assert not recorder.running and not recorder._connections
        assert recorder._session_handler is None
        assert logger.level == initial_level and len(logger.handlers) == initial_handlers
        assert 'Diagnostic session stopped' in log.read_text()
    finally:
        recorder.stop(); window.paint_panel.editor.dirty = False; window.close()
        if controller.example_directory: controller.example_directory.cleanup()
        window.deleteLater(); app.processEvents()


def test_dialog_restores_quality_and_copies_report_without_model_contents():
    from qtpy import QtWidgets,QtTest
    from layerloom.gui_app import _load_current_gui_module
    from layerloom.desktop_ui import install_desktop_ui
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w=_load_current_gui_module().QtAssignColorsApp();c=install_desktop_ui(w)
    w.show();app.processEvents();assert c.try_example();app.processEvents()
    c.performance_diagnostics();d=c._performance_dialog
    p=w.viewer.plotter
    samples=p.ren_win.GetMultiSamples()
    ssaa=p.renderer._render_passes._ssaa_pass is not None
    vertices=[m.vertices.copy() for m in w.paint_panel.document.meshes]
    try:
        d.effects.setChecked(True)
        assert p.ren_win.GetMultiSamples()==0
        d.start();QtTest.QTest.qWait(450);d.recorder.stop()
        assert 'Logging stopped' in d.status.text()
        d.copy_report();report=json.loads(app.clipboard().text())
        assert report['reduced_smoothing'] is True
        assert 'source_path' not in report
        import numpy as np
        for original,mesh in zip(vertices,w.paint_panel.document.meshes):
            np.testing.assert_array_equal(original,mesh.vertices)
        d.close()
        assert p.ren_win.GetMultiSamples()==samples
        assert (p.renderer._render_passes._ssaa_pass is not None)==ssaa
        c.copy_diagnostics()
        assert json.loads(app.clipboard().text())['format']=='layerloom-diagnostic-session-v1'
        d.installation_report = {'ok': False, 'error': 'test missing worker'}
        c.copy_diagnostics()
        assert json.loads(app.clipboard().text())['worker_check']['error'] == 'test missing worker'
    finally:
        d.close();w.paint_panel.editor.dirty=False;w.close()
        if c.example_directory:c.example_directory.cleanup()
        w.deleteLater();app.processEvents()


def test_delivered_drag_changes_camera_and_reaches_qt_swap(performance_log):
    from qtpy import QtCore, QtGui, QtWidgets, QtTest
    from layerloom.gui_app import _load_current_gui_module
    from layerloom.performance_diagnostics import PerformanceRecorder
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = _load_current_gui_module().QtAssignColorsApp()
    window.resize(800, 600)
    window.show()
    window.raise_()
    assert QtTest.QTest.qWaitForWindowExposed(window, 5000)
    QtTest.QTest.qWait(500)
    plotter = window.viewer.plotter
    recorder = PerformanceRecorder(window)
    try:
        recorder.start()
        before = tuple(plotter.camera.position)
        def mouse(kind, x, y, button, buttons):
            event = QtGui.QMouseEvent(kind, QtCore.QPointF(x, y), button, buttons, QtCore.Qt.NoModifier)
            app.sendEvent(plotter, event)
        mouse(QtCore.QEvent.MouseButtonPress, 160, 150, QtCore.Qt.LeftButton, QtCore.Qt.LeftButton)
        for x in (180, 200, 220):
            mouse(QtCore.QEvent.MouseMove, x, 180, QtCore.Qt.NoButton, QtCore.Qt.LeftButton)
            QtTest.QTest.qWait(30)
        mouse(QtCore.QEvent.MouseButtonRelease, 220, 180, QtCore.Qt.LeftButton, QtCore.Qt.NoButton)
        QtTest.QTest.qWait(250)
        assert tuple(plotter.camera.position) != before
        recorder.stop()
        report = recorder.snapshot()
        activity = report['viewport_activity']['build_plate']
        assert activity['drag_moves'] == 3
        assert activity['camera_changes'] > 0
        assert activity['qt_frame_swap_signal_available']
        assert activity['qt_frame_swaps'] > 0
        assert activity['oldest_camera_change_to_next_qt_swap']['samples'] > 0
        assert activity['pending_camera_change_age_ms'] is None
        assert report['workspace'] == 'Arrange'
        # No observers/signals may leak into subsequent sessions.
        frozen_count = activity['camera_changes']
        plotter.camera.Azimuth(10)
        QtTest.QTest.qWait(250)
        assert recorder.viewport_activity['build_plate'].snapshot()['camera_changes'] == frozen_count
        recorder.start()
        assert recorder.snapshot()['viewport_activity']['build_plate']['drag_moves'] == 0
    finally:
        recorder.stop()
        window.close(); window.deleteLater(); app.processEvents()


def test_logging_shutdown_survives_deferred_window_destruction(performance_log):
    """Exercise real Qt deletion, not just hiding a closed Python-owned window."""
    import gc
    from qtpy import QtCore, QtWidgets
    from layerloom.gui_app import _load_current_gui_module
    from layerloom.performance_diagnostics import PerformanceDialog
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    for _ in range(3):
        window = _load_current_gui_module().QtAssignColorsApp()
        window.show(); app.processEvents()
        dialog = PerformanceDialog(window)
        dialog.start()
        recorder = dialog.recorder
        lifetime = recorder._destroyed_event
        log_path = recorder.session_path
        window.close()
        assert not recorder.running
        assert not recorder._thread.is_alive()
        assert 'Diagnostic session stopped' in log_path.read_text()
        window.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        app.processEvents()
        assert lifetime.is_set()
        del dialog, recorder, window
        gc.collect()
        app.processEvents()
