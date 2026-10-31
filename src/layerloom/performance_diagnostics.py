"""Opt-in, bounded viewport diagnostics; no model contents or uploads."""
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
from qtpy import QtCore, QtWidgets

from .app_logging import diagnostic_text


def _summary(values):
    values = list(values)
    return {"samples": len(values), "median_ms": round(float(np.median(values)), 2),
            "p95_ms": round(float(np.percentile(values, 95)), 2),
            "max_ms": round(max(values), 2)} if values else {"samples": 0}


def _plotters(window):
    return {"build_plate": window.viewer.plotter,
            "paint": window.paint_panel.editor.plotter}


def _environment(window):
    result = {"installation": diagnostic_text().replace(str(Path.home()), "<home>"),
              "viewports": {}}
    for name, plotter in _plotters(window).items():
        rw = plotter.ren_win
        screen = plotter.screen()
        cells = 0
        for actor in plotter.renderer.actors.values():
            mapper = actor.GetMapper() if hasattr(actor, 'GetMapper') else None
            data = mapper.GetInput() if mapper is not None and hasattr(mapper, 'GetInput') else None
            if data is not None and hasattr(data, 'GetNumberOfCells'):
                cells += data.GetNumberOfCells()
        result['viewports'][name] = {
            "visible": plotter.isVisible(), "render_pixels": list(rw.GetSize()),
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

    def start(self, duration=60):
        if self.running:
            return
        self.environment = _environment(self.window)
        self.duration = duration
        self.started = self.heartbeat = time.monotonic()
        self._last_notice = self.started
        self.frames = {name: deque(maxlen=4096) for name in _plotters(self.window)}
        self.frame_counts = {name: 0 for name in self.frames}
        self.frame_max = {name: 0.0 for name in self.frames}
        self.lags = deque(maxlen=600)
        self.stacks = deque(maxlen=6)
        self.input_counts = {'mouse_moves': 0, 'wheel_events': 0}
        self.report_data = None
        self.running = True
        self._stop_event = threading.Event()
        stop_event = self._stop_event
        for name, plotter in _plotters(self.window).items():
            rw = plotter.ren_win
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
        self.changed.emit()

    def eventFilter(self, obj, event):
        if self.running:
            if event.type() == QtCore.QEvent.MouseMove:
                self.input_counts['mouse_moves'] += 1
            elif event.type() == QtCore.QEvent.Wheel:
                self.input_counts['wheel_events'] += 1
        return False

    def _watch(self, stop_event):
        # Never call Qt/VTK from this thread. Python may not schedule us during
        # native calls that hold the GIL; the GUI heartbeat still records delay.
        last_sample = 0.0
        while not stop_event.wait(.5):
            now = time.monotonic()
            if now-self.started > self.duration:
                return
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
        self.lags.append(max(0., (now-self.heartbeat)*1000-self.timer.interval()))
        self.heartbeat = now
        if now-self.started >= self.duration:
            self.stop()
        elif now-self._last_notice >= 1:
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
        return {"format": "layerloom-performance-v1", "status": "recording" if self.running else "stopped",
            "elapsed_seconds": round(time.monotonic()-self.started, 1), **self.environment,
            "input_events_delivered": dict(self.input_counts),
            "render_calls": {name: {**_summary(values), "total_frames": self.frame_counts[name],
                            "max_ms_entire_recording": round(self.frame_max[name], 2)}
                             for name, values in self.frames.items()},
            "ui_timer_delay": _summary(self.lags), "process_peak_memory_mib": round(peak, 1) if peak else None,
            "python_threads": threading.active_count(), "slow_main_thread_samples": list(self.stacks),
            "notes": ["Render timings are VTK call durations, not GPU frame completion or mouse-to-screen latency.",
                      "UI delay measures a 200 ms timer arriving late; includes deliberate busy operations.",
                      "Render percentiles retain the last 4096 frames per viewport; maximum covers the whole recording.",
                      "Stack samples may be unavailable during native calls holding Python's GIL.",
                      "No model contents, model filenames, local variables, or uploads are included."]}

    def stop(self):
        if not self.running:
            return
        self.running = False
        self.timer.stop()
        self._stop_event.set()
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
        self.report_data = self.snapshot()
        logging.getLogger('layerloom.performance').info('%s', json.dumps(self.report_data))
        self.changed.emit()
        self.finished.emit()

    def text(self):
        return json.dumps(self.snapshot(), indent=2)


class PerformanceDialog(QtWidgets.QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle('Performance diagnostics')
        self.setModal(False)
        self.resize(570, 360)
        self.recorder = PerformanceRecorder(window)
        self._original_quality = None
        self._window = window
        layout = QtWidgets.QVBoxLayout(self)
        label = QtWidgets.QLabel('Load a model first. Start recording, then rotate and zoom the model for up to 60 seconds. '
                                'Copy the report afterward. Nothing is uploaded.')
        label.setWordWrap(True); layout.addWidget(label)
        self.effects = QtWidgets.QCheckBox('Reduce display smoothing for comparison')
        self.effects.setToolTip('Changes viewport appearance only; model and exported geometry stay the same.')
        self.effects.toggled.connect(self._quality)
        layout.addWidget(self.effects)
        self.status = QtWidgets.QLabel('Ready to record'); self.status.setWordWrap(True); layout.addWidget(self.status)
        row = QtWidgets.QHBoxLayout()
        self.start_button = QtWidgets.QPushButton('Start 60-second recording')
        self.start_button.clicked.connect(self.start)
        self.stop_button = QtWidgets.QPushButton('Stop'); self.stop_button.clicked.connect(self.recorder.stop)
        self.copy_button = QtWidgets.QPushButton('Copy report'); self.copy_button.clicked.connect(self.copy_report)
        for button in (self.start_button, self.stop_button, self.copy_button):row.addWidget(button)
        layout.addLayout(row)
        self.details = QtWidgets.QPlainTextEdit(); self.details.setReadOnly(True); layout.addWidget(self.details)
        self.recorder.changed.connect(self._refresh)
        self.recorder.finished.connect(lambda: self.details.setPlainText(self.recorder.text()))
        self._refresh()

    def start(self):
        self.details.clear()
        self.recorder.start()
        self.recorder.environment['reduced_smoothing'] = self.effects.isChecked()

    def _refresh(self):
        running = self.recorder.running
        self.start_button.setEnabled(not running); self.stop_button.setEnabled(running)
        self.effects.setEnabled(not running)
        if running:
            data = self.recorder.snapshot()
            frames = [x.get('max_ms_entire_recording', 0) for x in data['render_calls'].values()]
            self.status.setText(f"Recording: {data['elapsed_seconds']:.0f} / 60 s · "
                               f"Longest render: {max(frames):.0f} ms · "
                               f"Longest UI delay: {data['ui_timer_delay'].get('max_ms', 0):.0f} ms")
        elif self.recorder.report_data is not None:
            self.status.setText('Recording complete. Copy the report, or change smoothing and record a comparison.')

    def copy_report(self):
        QtWidgets.QApplication.clipboard().setText(self.recorder.text())
        self.status.setText('Report copied. Paste it into your message.')

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
        self.recorder.stop()
        self.effects.setChecked(False)
        event.accept()
