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
        recorder.start(duration=10)
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
        assert 'Recording started:' in text and 'layerloom-performance-v1' in text
        assert 'ui_timer_delay' in text and 'OpenGL renderer' in text
        assert not recorder._observers and not recorder._widgets and not recorder._thread.is_alive()
        count=report['render_calls']['build_plate']['total_frames']
        w.viewer.plotter.ren_win.Render()
        assert recorder.snapshot()['render_calls']['build_plate']['total_frames']==count
        recorder.start(duration=.2);QtTest.QTest.qWait(450)
        assert not recorder.running
        assert recorder.snapshot()['elapsed_seconds']<2
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
        assert 'Longest UI delay:' in d.status.text()
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
        assert json.loads(app.clipboard().text())['format']=='layerloom-performance-v1'
        d.installation_report = {'ok': False, 'error': 'test missing worker'}
        c.copy_diagnostics()
        assert json.loads(app.clipboard().text())['worker_check']['error'] == 'test missing worker'
    finally:
        d.close();w.paint_panel.editor.dirty=False;w.close()
        if c.example_directory:c.example_directory.cleanup()
        w.deleteLater();app.processEvents()
