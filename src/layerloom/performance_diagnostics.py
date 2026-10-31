"""User-controlled session logging with bounded responsiveness diagnostics."""
from __future__ import annotations

from collections import deque
import json
import logging
from pathlib import Path
import sys
import threading
import time
import traceback

import numpy as np
from qtpy import QtCore, QtGui, QtWidgets

from .app_logging import diagnostic_text, packaged_build_info, log_dir, _LogFileHandler


def _summary(values):
    values = list(values)
    return {"samples": len(values), "median_ms": round(float(np.median(values)), 2),
            "p95_ms": round(float(np.percentile(values, 95)), 2),
            "max_ms": round(max(values), 2)} if values else {"samples": 0}


def _plotters(window):
    return {"build_plate": window.viewer.plotter,
            "paint": window.paint_panel.editor.plotter}


class _ViewportActivity:
    """Bounded correlation of delivered input, camera changes and Qt swaps.

    A swap is a top-level composition notification, not proof that a particular
    input reached the physical display. Keep those limits explicit in reports.
    """

    def __init__(self, swap_supported):
        self.swap_supported = swap_supported
        self.counts = dict.fromkeys(('hover_moves', 'drag_moves', 'button_presses',
                                    'wheel_events', 'gesture_events',
                                    'camera_changes', 'qt_frame_swaps'), 0)
        self.pending_input = None
        self.pending_camera = None
        self.input_waits = deque(maxlen=4096)
        self.camera_waits = deque(maxlen=4096)
        self.max_input_wait = self.max_camera_wait = 0.0

    def input(self, kind):
        self.counts[kind] += 1
        if kind != 'hover_moves' and self.pending_input is None:
            self.pending_input = time.monotonic()

    def camera_changed(self, *_):
        self.counts['camera_changes'] += 1
        if self.pending_camera is None:
            self.pending_camera = time.monotonic()

    def swapped(self):
        self.counts['qt_frame_swaps'] += 1
        now = time.monotonic()
        if self.pending_input is not None:
            elapsed = (now - self.pending_input) * 1000
            self.input_waits.append(elapsed)
            self.max_input_wait = max(self.max_input_wait, elapsed)
            self.pending_input = None
        if self.pending_camera is not None:
            elapsed = (now - self.pending_camera) * 1000
            self.camera_waits.append(elapsed)
            self.max_camera_wait = max(self.max_camera_wait, elapsed)
            self.pending_camera = None

    def snapshot(self):
        now = time.monotonic()
        def age(start):
            return round((now - start) * 1000, 2) if start is not None else None
        return {**self.counts, 'qt_frame_swap_signal_available': self.swap_supported,
                'oldest_input_to_next_qt_swap': {
                    **_summary(self.input_waits), 'max_ms_entire_recording': round(self.max_input_wait, 2)},
                'oldest_camera_change_to_next_qt_swap': {
                    **_summary(self.camera_waits), 'max_ms_entire_recording': round(self.max_camera_wait, 2)},
                'pending_input_age_ms': age(self.pending_input),
                'pending_camera_change_age_ms': age(self.pending_camera)}


def _environment(window):
    result = {"installation": diagnostic_text().replace(str(Path.home()), "<home>"),
              "viewports": {}}
    for name, plotter in _plotters(window).items():
        rw = plotter.ren_win
        screen = plotter.screen()
        handle = plotter.window().windowHandle()
        cells = 0
        for actor in plotter.renderer.actors.values():
            mapper = actor.GetMapper() if hasattr(actor, 'GetMapper') else None
            data = mapper.GetInput() if mapper is not None and hasattr(mapper, 'GetInput') else None
            if data is not None and hasattr(data, 'GetNumberOfCells'):
                cells += data.GetNumberOfCells()
        result['viewports'][name] = {
            "visible": plotter.isVisible(), "render_pixels": list(rw.GetSize()),
            "qt_window_exposed": handle.isExposed() if handle else False,
            "qt_widget_valid": plotter.isValid() if hasattr(plotter, 'isValid') else None,
            "device_pixel_ratio": plotter.devicePixelRatioF(),
            "screen_pixels": [screen.size().width(), screen.size().height()] if screen else None,
            "actors": len(plotter.renderer.actors), "display_cells_including_overlays": cells,
            "multisamples": rw.GetMultiSamples(),
            "supersampling": getattr(plotter.renderer._render_passes, '_ssaa_pass', None) is not None,
            "auto_render_interval_ms": plotter.render_timer.interval() if plotter.render_timer.isActive() else None,
            "graphics": [line.strip() for line in rw.ReportCapabilities().splitlines()
                         if line.startswith(('OpenGL vendor', 'OpenGL renderer', 'OpenGL version'))] if plotter.isVisible() else ["Not queried while hidden"],
        }
    return result


class PerformanceRecorder(QtCore.QObject):
    changed = QtCore.Signal()
    finished = QtCore.Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.running = False
        self.report_data = None
        self._stop_event = threading.Event()
        self.destroyed.connect(lambda: self._stop_event.set())
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self._tick)
        self._observers = []
        self._widgets = []
        self._connections = []
        self._session_handler = None
        self.session_path = None
        window._diagnostic_recorder = self
        QtWidgets.QApplication.instance().aboutToQuit.connect(self.stop)

    def start(self):
        if self.running:
            return
        directory = log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        self.session_path = directory / 'diagnostic-session.log'
        handler = _LogFileHandler(self.session_path, maxBytes=2_000_000, backupCount=3, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
        logger = logging.getLogger('layerloom')
        self._previous_log_level = logger.level
        self._session_handler = handler
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.info('Diagnostic session started\n%s', diagnostic_text())
        try:
            self._start()
        except Exception:
            logger.exception('Could not start diagnostic session')
            if self.running:
                self.stop()
            else:
                self._close_session_log()
            raise

    def _start(self):
        self.environment = _environment(self.window)
        self.started = self.heartbeat = time.monotonic()
        self._last_notice = self.started
        self._last_checkpoint = self.started
        self.frames = {name: deque(maxlen=4096) for name in _plotters(self.window)}
        self.frame_counts = {name: 0 for name in self.frames}
        self.frame_max = {name: 0.0 for name in self.frames}
        self.lags = deque(maxlen=600)
        self.max_lag = 0.0
        self.stacks = deque(maxlen=6)
        self.input_counts = {'mouse_moves': 0, 'wheel_events': 0}
        self.viewport_activity = {}
        self._widget_activity = {}
        self.report_data = None
        self.running = True
        self._connect_activity()
        self._stop_event = threading.Event()
        stop_event = self._stop_event
        for name, plotter in _plotters(self.window).items():
            rw = plotter.ren_win
            swap_signal = getattr(plotter, 'frameSwapped', None)
            activity = _ViewportActivity(swap_signal is not None)
            self.viewport_activity[name] = activity
            self._widget_activity[plotter] = activity
            camera = plotter.camera
            self._observers.append((camera, camera.AddObserver('ModifiedEvent', activity.camera_changed)))
            if swap_signal is not None:
                swap_signal.connect(activity.swapped)
                self._connections.append((swap_signal, activity.swapped))
            start_time = [None]
            def begin(*_, holder=start_time):
                holder[0] = time.monotonic()
            def end(*_, holder=start_time, label=name):
                if holder[0] is not None and self.running:
                    elapsed = (time.monotonic()-holder[0])*1000
                    self.frames[label].append(elapsed)
                    self.frame_counts[label] += 1
                    self.frame_max[label] = max(self.frame_max[label], elapsed)
                    holder[0] = None
            self._observers.extend([(rw, rw.AddObserver('StartEvent', begin)),
                                    (rw, rw.AddObserver('EndEvent', end))])
            plotter.installEventFilter(self)
            self._widgets.append(plotter)
        self._thread = threading.Thread(target=self._watch, args=(stop_event,), daemon=True,
                                        name='LayerLoom performance watchdog')
        self._thread.start()
        self.timer.start()
        logging.getLogger('layerloom.performance').info('Session environment: %s', json.dumps(self.environment))
        self.changed.emit()

    def _connect_activity(self):
        def connect(signal, slot):
            signal.connect(slot)
            self._connections.append((signal, slot))
        logger = logging.getLogger('layerloom.activity')
        for action in self.window.findChildren(QtWidgets.QAction):
            connect(action.triggered, lambda checked=False, action=action:
                    logger.debug('Action: %s; checked=%s', action.text(), checked))
        editor = self.window.paint_panel.editor
        for name in ('paintChanged', 'geometryChanged', 'appearanceChanged'):
            connect(getattr(editor, name), lambda name=name: logger.debug('Model change: %s', name))
        connect(editor.documentChanged, lambda doc: logger.info('Document loaded: parts=%s; triangles=%s',
                len(getattr(doc, 'meshes', [])), sum(len(mesh.faces) for mesh in getattr(doc, 'meshes', []))))
        for status in (self.window.statusBar(), editor.statusBar()):
            connect(status.messageChanged, lambda text: logger.debug('Status: %s', text) if text else None)
        connect(self.window.workspace_tabs.currentChanged, lambda index: logger.info('Workspace: %s',
                'Paint' if self.window._paint_active() else 'Arrange'))

    def eventFilter(self, obj, event):
        if self.running:
            activity = self._widget_activity.get(obj)
            if event.type() == QtCore.QEvent.MouseMove:
                self.input_counts['mouse_moves'] += 1
                if activity:
                    activity.input('drag_moves' if event.buttons() else 'hover_moves')
            elif event.type() == QtCore.QEvent.Wheel:
                self.input_counts['wheel_events'] += 1
                if activity:
                    activity.input('wheel_events')
            elif activity and event.type() == QtCore.QEvent.MouseButtonPress:
                activity.input('button_presses')
            elif activity and event.type() in (QtCore.QEvent.Gesture, QtCore.QEvent.NativeGesture):
                activity.input('gesture_events')
        return False

    def _watch(self, stop_event):
        # Never call Qt/VTK from this thread. Python may not schedule us during
        # native calls that hold the GIL; the GUI heartbeat still records delay.
        last_sample = 0.0
        while not stop_event.wait(.5):
            now = time.monotonic()
            if now-self.heartbeat < 2 or now-last_sample < 3:
                continue
            frame = sys._current_frames().get(threading.main_thread().ident)
            if frame is not None:
                stack = traceback.extract_stack(frame, limit=20)
                sample = {"at_seconds": round(now-self.started, 1),
                    "heartbeat_age_ms": round((now-self.heartbeat)*1000),
                    "stack": [f'{Path(f.filename).name}:{f.lineno} in {f.name}' for f in stack]}
                self.stacks.append(sample)
                logging.getLogger('layerloom.performance').warning('Slow UI sample: %s', json.dumps(sample))
                del frame
            last_sample = now

    def _tick(self):
        now = time.monotonic()
        lag = max(0., (now-self.heartbeat)*1000-self.timer.interval())
        self.lags.append(lag)
        self.max_lag = max(self.max_lag, lag)
        self.heartbeat = now
        if now-self._last_checkpoint >= 15:
            self._last_checkpoint = now
            logging.getLogger('layerloom.performance').info('Session checkpoint: %s', self.text())
        if now-self._last_notice >= 1:
            self._last_notice = now
            self.changed.emit()

    def snapshot(self):
        if not self.running and self.report_data is not None:
            return self.report_data
        if not hasattr(self, 'started'):
            return {"status": "No performance recording yet", **_environment(self.window)}
        peak = None
        try:
            import resource
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024**2 if sys.platform == 'darwin' else 1024)
        except (ImportError, AttributeError):
            pass
        return {"format": "layerloom-diagnostic-session-v1", "status": "logging" if self.running else "stopped",
            "session_log": str(self.session_path),
            "elapsed_seconds": round(time.monotonic()-self.started, 1), **self.environment,
            "input_events_delivered": dict(self.input_counts),
            "viewport_activity": {name: activity.snapshot() for name, activity in self.viewport_activity.items()},
            "workspace": "Paint" if self.window._paint_active() else "Arrange",
            "build_plate_tool": getattr(self.window.viewer, 'tool_mode', None),
            "render_calls": {name: {**_summary(values), "total_frames": self.frame_counts[name],
                            "max_ms_entire_recording": round(self.frame_max[name], 2)}
                             for name, values in self.frames.items()},
            "ui_timer_delay": {**_summary(self.lags), "max_ms_entire_session": round(self.max_lag, 2)},
            "process_peak_memory_mib": round(peak, 1) if peak else None,
            "python_threads": threading.active_count(), "slow_main_thread_samples": list(self.stacks),
            "notes": ["Render timings are VTK call durations, not GPU frame completion or mouse-to-screen latency.",
                      "Qt swaps are window composition notifications, not proof of physical display latency or which input a frame contains.",
                      "Input/camera waits start at the oldest event since the previous swap; idle hover is excluded. Pending ages also include hidden/occluded time.",
                      "UI delay measures a 200 ms timer arriving late; includes deliberate busy operations.",
                      "Render percentiles retain the last 4096 frames per viewport; maximum covers the whole recording.",
                      "Stack samples may be unavailable during native calls holding Python's GIL.",
                      "Session logs include actions, paths and errors, but not model contents or local variables. Nothing is uploaded."]}

    def stop(self):
        if not self.running:
            return
        self.running = False
        self.timer.stop()
        self._stop_event.set()
        if hasattr(self, '_thread') and self._thread.is_alive():
            self._thread.join(timeout=.2)
        for rw, oid in self._observers:
            try:
                rw.RemoveObserver(oid)
            except RuntimeError:
                pass
        self._observers.clear()
        for widget in self._widgets:
            try:
                widget.removeEventFilter(self)
            except RuntimeError:
                pass
        self._widgets.clear()
        self._widget_activity.clear()
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._connections.clear()
        self.report_data = self.snapshot()
        logging.getLogger('layerloom.performance').info('%s', json.dumps(self.report_data))
        logging.getLogger('layerloom').info('Diagnostic session stopped')
        self._close_session_log()
        self.changed.emit()
        self.finished.emit()

    def _close_session_log(self):
        if self._session_handler is not None:
            logger = logging.getLogger('layerloom')
            logger.removeHandler(self._session_handler)
            self._session_handler.close()
            self._session_handler = None
            logger.setLevel(self._previous_log_level)

    def text(self):
        return json.dumps(self.snapshot(), indent=2)


class PerformanceDialog(QtWidgets.QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle('Diagnostic logging')
        self.setModal(False)
        self.resize(570, 360)
        self.recorder = PerformanceRecorder(window)
        self.installation_report = None
        self._probe_timed_out = False
        self.probe = QtCore.QProcess(self)
        self.probe.finished.connect(self._installation_finished)
        self.probe.errorOccurred.connect(self._installation_error)
        self.probe_timer = QtCore.QTimer(self)
        self.probe_timer.setSingleShot(True)
        self.probe_timer.timeout.connect(self._installation_timeout)
        self._original_quality = None
        self._window = window
        layout = QtWidgets.QVBoxLayout(self)
        label = QtWidgets.QLabel('Start logging, then use LayerLoom normally. Imports, edits, exports, errors and '
                                'responsiveness are logged until you stop or quit the app. You can close this panel. '
                                'Nothing is uploaded.')
        label.setWordWrap(True); layout.addWidget(label)
        self.effects = QtWidgets.QCheckBox('Reduce display smoothing for comparison')
        self.effects.setToolTip('Changes viewport appearance only; model and exported geometry stay the same.')
        self.effects.toggled.connect(self._quality)
        layout.addWidget(self.effects)
        self.effects.hide()
        self.status = QtWidgets.QLabel('Diagnostic logging is off'); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.indicator = QtWidgets.QLabel('Diagnostic logging is on')
        window.statusBar().addPermanentWidget(self.indicator)
        self.indicator.hide()
        row = QtWidgets.QHBoxLayout()
        self.start_button = QtWidgets.QPushButton('Start diagnostic logging')
        self.start_button.clicked.connect(self.start)
        self.stop_button = QtWidgets.QPushButton('Stop logging'); self.stop_button.clicked.connect(self.recorder.stop)
        self.copy_button = QtWidgets.QPushButton('Copy summary'); self.copy_button.clicked.connect(self.copy_report)
        self.copy_timer = QtCore.QTimer(self)
        self.copy_timer.setSingleShot(True)
        self.copy_timer.timeout.connect(lambda: self.copy_button.setText('Copy summary'))
        for button in (self.start_button, self.stop_button, self.copy_button):row.addWidget(button)
        layout.addLayout(row)
        self.open_logs_button = QtWidgets.QPushButton('Open log folder')
        self.open_logs_button.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(log_dir()))))
        layout.addWidget(self.open_logs_button)
        self.check_button = QtWidgets.QPushButton('Check installation')
        self.check_button.clicked.connect(self.check_installation)
        layout.addWidget(self.check_button)
        self.installation_status = QtWidgets.QLabel('Installation has not been checked in this session.')
        self.installation_status.setWordWrap(True)
        layout.addWidget(self.installation_status)
        self.advanced_button = QtWidgets.QToolButton(text='Technical details')
        self.advanced_button.setCheckable(True)
        layout.addWidget(self.advanced_button)
        self.details = QtWidgets.QPlainTextEdit(); self.details.setReadOnly(True); layout.addWidget(self.details)
        self.details.hide()
        self.advanced_button.toggled.connect(self.details.setVisible)
        self.advanced_button.toggled.connect(self.effects.setVisible)
        self.recorder.changed.connect(self._refresh)
        self.recorder.finished.connect(lambda: self.details.setPlainText(self.recorder.text()))
        self.recorder.finished.connect(lambda: self.effects.setChecked(False))
        self._refresh()

    def start(self):
        if self.probe.state() != QtCore.QProcess.NotRunning:
            return
        self.details.clear()
        try:
            self.recorder.start()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, 'Could not start logging', str(exc))
            return
        self.recorder.environment['reduced_smoothing'] = self.effects.isChecked()
        if self.installation_report is not None:
            self.recorder.environment['worker_check'] = self.installation_report

    def _refresh(self):
        running = self.recorder.running
        self.indicator.setVisible(running)
        self.indicator.setToolTip(str(self.recorder.session_path or ''))
        self.check_button.setEnabled(not running and self.probe.state() == QtCore.QProcess.NotRunning)
        self.start_button.setEnabled(not running and self.probe.state() == QtCore.QProcess.NotRunning)
        self.stop_button.setEnabled(running)
        self.effects.setEnabled(not running)
        if running:
            self.status.setText(f"Logging is on. Continue using the app; close this panel if you like.\n"
                               f"Log file: {self.recorder.session_path}")
        elif self.recorder.report_data is not None:
            self.status.setText(f"Logging stopped. Files are saved in {log_dir()}")

    def report_text(self):
        report = self.recorder.snapshot()
        if self.installation_report is not None:
            report = {**report, 'worker_check': self.installation_report}
        return json.dumps(report, indent=2)

    def copy_report(self):
        QtWidgets.QApplication.clipboard().setText(self.report_text())
        self.copy_button.setText('Copied')
        self.copy_timer.start(2000)

    def check_installation(self):
        if self.recorder.running or self.probe.state() != QtCore.QProcess.NotRunning:
            return
        from .runtime import _worker_prefix
        self._probe_timed_out = False
        try:
            command = [*_worker_prefix(), '--diagnostics']
        except OSError as exc:
            self._save_installation({'ok': False, 'error': str(exc)})
            return
        self.check_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.installation_status.setText('Checking the processing runtime…')
        self.probe.start(command[0], command[1:])
        self.probe_timer.start(15000)

    def _installation_timeout(self):
        self._probe_timed_out = True
        self.probe.kill()

    def _installation_error(self, error):
        if error == QtCore.QProcess.FailedToStart:
            self._save_installation({'ok': False, 'error': self.probe.errorString()})

    def _installation_finished(self, exit_code, *_):
        stdout = bytes(self.probe.readAllStandardOutput()).decode('utf-8', errors='replace')
        stderr = bytes(self.probe.readAllStandardError()).decode('utf-8', errors='replace')
        try:
            if self._probe_timed_out:
                raise ValueError('Worker check timed out after 15 seconds.')
            report = json.loads(stdout)
            from .version import __version__
            import importlib.metadata
            import platform
            expected = packaged_build_info()
            mismatches = [line for line in diagnostic_text().splitlines() if line.startswith('Runtime mismatches:')]
            if expected.get('read_error'):
                mismatches.append('Packaged build identity could not be read: ' + expected['read_error'])
            for key, value in [('version', expected.get('version', __version__)), ('python', expected.get('python', platform.python_version())),
                               ('frozen', bool(getattr(sys, 'frozen', False))), ('architecture', platform.machine())]:
                if report.get(key) != value:
                    mismatches.append(f"{key}: expected {value}, found {report.get(key)}")
            for name in ('lib3mf', 'numpy', 'trimesh', 'manifold3d'):
                wanted = expected.get('runtime_versions', {}).get(name)
                if wanted is None:
                    try:
                        wanted = importlib.metadata.version(name)
                    except importlib.metadata.PackageNotFoundError:
                        wanted = 'metadata unavailable'
                actual = report.get('packages', {}).get(name)
                if actual != wanted or actual == 'metadata unavailable':
                    mismatches.append(f'{name}: expected {wanted}, found {actual}')
            report.update(mismatches=mismatches, ok=exit_code == 0 and report.get('lib3mf', {}).get('ok') is True and not mismatches)
            if stderr:
                report['stderr'] = stderr[-4000:]
        except (ValueError, TypeError, AttributeError) as exc:
            report = {'ok': False, 'error': str(exc), 'worker_output': (stderr or stdout)[-4000:]}
        self._save_installation(report)

    def _save_installation(self, report):
        self.probe_timer.stop()
        # Do not put personal home-directory paths into the copyable report.
        self.installation_report = json.loads(json.dumps(report).replace(json.dumps(str(Path.home()))[1:-1], '<home>'))
        logging.getLogger('layerloom.performance').info('Worker installation check: %s', json.dumps(self.installation_report))
        self.installation_status.setText('Worker versions match; native lib3mf initialization passed.' if report['ok'] else
                                        'Installation check failed. Copy the report and open logs for details.')
        self.details.setPlainText(json.dumps(self.installation_report, indent=2))
        if self.recorder.report_data is not None:
            self.recorder.report_data['worker_check'] = self.installation_report
        self._refresh()

    def _quality(self, reduced):
        if reduced:
            self._original_quality = []
            for plotter in _plotters(self._window).values():
                self._original_quality.append((plotter, plotter.ren_win.GetMultiSamples(),
                    bool(plotter.renderer.GetUseFXAA()),
                    getattr(plotter.renderer._render_passes, '_ssaa_pass', None) is not None))
                plotter.disable_anti_aliasing(); plotter.render()
        elif self._original_quality is not None:
            for plotter, samples, fxaa, ssaa in self._original_quality:
                plotter.disable_anti_aliasing()
                plotter.ren_win.SetMultiSamples(samples)
                if ssaa:plotter.enable_anti_aliasing('ssaa')
                elif fxaa:plotter.enable_anti_aliasing('fxaa')
                plotter.render()
            self._original_quality = None

    def closeEvent(self, event):
        if self.probe.state() != QtCore.QProcess.NotRunning:
            self.probe.kill()
            self.probe.waitForFinished(100)
        self.probe_timer.stop()
        if not self.recorder.running:
            self.effects.setChecked(False)
        event.accept()
