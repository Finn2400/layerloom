from __future__ import annotations

import io
import logging
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from layerloom import app_logging


@pytest.fixture(autouse=True)
def isolated_logger(tmp_path, monkeypatch):
    logger = logging.getLogger("layerloom")
    old_handlers, old_level, old_propagate = logger.handlers[:], logger.level, logger.propagate
    old_stdout, old_stderr = sys.stdout, sys.stderr
    logger.handlers = []
    monkeypatch.setenv("LAYERLOOM_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(app_logging, "_active_log_path", None)
    monkeypatch.setattr(app_logging, "_log_write_failed", False)
    yield
    sys.stdout, sys.stderr = old_stdout, old_stderr
    for handler in logger.handlers:
        handler.close()
    logger.handlers = old_handlers
    logger.setLevel(old_level)
    logger.propagate = old_propagate


def test_logs_keep_console_and_worker_diagnostics_without_duplicate_handlers(monkeypatch):
    stdout, stderr = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    path = app_logging.initialize_logs()
    assert app_logging.initialize_logs() == path
    print("legacy GUI output")
    sys.stderr.write("partial error")
    sys.stderr.flush()
    logging.getLogger("layerloom.worker").info("worker output")
    contents = path.read_text()
    assert contents.count("legacy GUI output") == 1
    assert contents.count("worker output") == 1
    assert "partial error" in contents
    assert stdout.getvalue() == "legacy GUI output\n"
    assert stderr.getvalue() == "partial error"


def test_unwritable_log_location_falls_back_to_private_temp(tmp_path, monkeypatch):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("unchanged")
    fallback = tmp_path / "fallback"
    monkeypatch.setenv("LAYERLOOM_LOG_DIR", str(blocked / "logs"))
    monkeypatch.setattr(app_logging.tempfile, "mkdtemp", lambda **_: str(fallback))
    path = app_logging.initialize_logs(redirect_streams=False)
    assert path == fallback / "layerloom.log"
    assert path.is_file()
    assert app_logging.log_dir() == fallback
    assert str(path) in app_logging.diagnostic_text()
    assert blocked.read_text() == "unchanged"


def test_no_writable_log_location_does_not_prevent_startup(tmp_path, monkeypatch):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("unchanged")
    monkeypatch.setenv("LAYERLOOM_LOG_DIR", str(blocked / "logs"))
    def denied(**_):
        raise PermissionError("temporary storage unavailable")
    monkeypatch.setattr(app_logging.tempfile, "mkdtemp", denied)
    assert app_logging.initialize_logs(redirect_streams=False) is None
    assert "not initialized or unavailable" in app_logging.diagnostic_text()


def test_closed_console_stream_does_not_hide_logs(monkeypatch):
    closed = io.StringIO()
    closed.close()
    monkeypatch.setattr(sys, "stdout", closed)
    path = app_logging.initialize_logs()
    print("survives a closed console")
    sys.stdout.flush()
    assert "survives a closed console" in path.read_text()


def test_log_write_failure_does_not_recurse_through_redirected_stderr(monkeypatch):
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    app_logging.initialize_logs()
    handler = next(h for h in logging.getLogger("layerloom").handlers if isinstance(h, app_logging._LogFileHandler))
    def disk_full(*_):
        raise OSError("disk full")
    monkeypatch.setattr(handler, "shouldRollover", disk_full)
    logging.getLogger("layerloom.worker").error("worker failed")
    assert app_logging._log_write_failed
    assert stderr.getvalue().count("could not write its log file") == 1


def test_native_mac_error_dialog_handles_broken_qt_and_missing_logs(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("LAYERLOOM_NO_DIALOGS", raising=False)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(app_logging.subprocess, "run", run)
    monkeypatch.setitem(sys.modules, "PyQt5", None)
    app_logging.report_startup_failure(RuntimeError("Qt platform plugin failed"))
    assert calls[0][0] == "/usr/bin/osascript"
    assert "Qt platform plugin failed" in calls[0][-1]
    assert "could not be created" in calls[0][-1]
    assert "Details were saved" not in calls[0][-1]


def test_failed_native_dialog_never_constructs_qapplication(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("LAYERLOOM_NO_DIALOGS", raising=False)
    monkeypatch.setattr(app_logging.subprocess, "run", lambda command, **_: subprocess.CompletedProcess(command, 1))
    class App:
        def __init__(self, *_):
            pytest.fail("Startup failure must not construct QApplication")
        @staticmethod
        def instance():
            return None
    monkeypatch.setitem(sys.modules, "PyQt5", SimpleNamespace(QtWidgets=SimpleNamespace(QApplication=App)))
    app_logging.report_startup_failure(RuntimeError("broken platform"))
    assert "broken platform" in capsys.readouterr().err


def test_qt_fatal_message_is_logged_and_uses_native_alert_once(monkeypatch):
    path = app_logging.initialize_logs(redirect_streams=False)
    installed, dialogs = [], []
    core = SimpleNamespace(
        QtDebugMsg=0, QtWarningMsg=1, QtCriticalMsg=2, QtFatalMsg=3,
        qInstallMessageHandler=installed.append,
    )
    monkeypatch.setitem(sys.modules, "PyQt5", SimpleNamespace(QtCore=core))
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("LAYERLOOM_NO_DIALOGS", raising=False)
    monkeypatch.setattr(app_logging, "_qt_message_handler", None)
    monkeypatch.setattr(app_logging, "_qt_fatal_reported", False)
    def run(command, **_):
        dialogs.append(command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(app_logging.subprocess, "run", run)
    app_logging.install_qt_message_logging()
    app_logging.install_qt_message_logging()
    assert len(installed) == 1
    installed[0](core.QtWarningMsg, None, "platform warning")
    installed[0](core.QtFatalMsg, None, "Cocoa platform initialization failed")
    installed[0](core.QtFatalMsg, None, "fatal callback reentry")
    assert len(dialogs) == 1
    assert dialogs[0][0] == "/usr/bin/osascript"
    assert "Cocoa platform initialization failed" in dialogs[0][-1]
    assert "platform warning" in path.read_text()
    assert "Cocoa platform initialization failed" in path.read_text()


def test_gui_installs_qt_handler_before_loading_application(monkeypatch):
    from layerloom import gui_app
    calls = []
    monkeypatch.setattr(app_logging, "initialize_logs", lambda: calls.append("logs"))
    monkeypatch.setattr(app_logging, "install_qt_message_logging", lambda: calls.append("qt-handler"))
    def load():
        calls.append("load-gui")
        return SimpleNamespace(main=lambda: calls.append("application") or 0)
    monkeypatch.setattr(gui_app, "_load_current_gui_module", load)
    assert gui_app.main() == 0
    assert calls == ["logs", "qt-handler", "load-gui", "application"]
