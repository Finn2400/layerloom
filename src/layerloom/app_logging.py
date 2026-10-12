"""Persistent diagnostics for desktop launches, including launches without a console."""
from __future__ import annotations

import importlib.metadata
import io
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile


_active_log_path: Path | None = None
_log_write_failed = False
_qt_message_handler = None
_qt_fatal_reported = False


def log_dir() -> Path:
    if _active_log_path is not None:
        return _active_log_path.parent
    return _preferred_log_dir()


def _preferred_log_dir() -> Path:
    override = os.environ.get("LAYERLOOM_LOG_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "LayerLoom"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "LayerLoom" / "Logs"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "layerloom" / "logs"


class _LoggedStream(io.TextIOBase):
    """Keep source-checkout console output while also logging the legacy GUI's prints."""
    def __init__(self, original, level: int):
        self.original = original
        self.level = level
        self.pending = ""

    @property
    def encoding(self):
        return getattr(self.original, "encoding", None) or "utf-8"

    def writable(self):
        return True

    def isatty(self):
        return bool(self.original and getattr(self.original, "isatty", lambda: False)())

    def write(self, value):
        if self.original is not None:
            try:
                self.original.write(value)
            except (OSError, ValueError):
                self.original = None
        self.pending += value
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            if line:
                logging.getLogger("layerloom.console").log(self.level, line)
        return len(value)

    def flush(self):
        if self.original is not None:
            try:
                self.original.flush()
            except (OSError, ValueError):
                self.original = None
        if self.pending:
            logging.getLogger("layerloom.console").log(self.level, self.pending)
            self.pending = ""


class _LogFileHandler(RotatingFileHandler):
    def handleError(self, record):
        global _log_write_failed
        if _log_write_failed:
            return
        _log_write_failed = True
        # logging's default error handler writes to sys.stderr. That stream is
        # redirected back to this logger, so a full disk would recurse forever.
        stream = sys.stderr
        if isinstance(stream, _LoggedStream):
            stream = stream.original
        try:
            if stream is not None:
                stream.write("LayerLoom could not write its log file.\n")
        except (OSError, ValueError):
            pass


def initialize_logs(*, redirect_streams: bool = True) -> Path | None:
    global _active_log_path, _log_write_failed
    _log_write_failed = False
    logger = logging.getLogger("layerloom")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    preferred = _preferred_log_dir()
    failures = []
    for attempt in range(2):
        try:
            directory = preferred if attempt == 0 else Path(tempfile.mkdtemp(prefix="layerloom_logs_"))
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / "layerloom.log"
            if not any(getattr(handler, "baseFilename", None) == str(path.resolve()) for handler in logger.handlers):
                handler = _LogFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
                # This module owns its handlers; switching destinations must not
                # keep writing to a stale or no-longer-accessible log directory.
                for old in list(logger.handlers):
                    if getattr(old, "_layerloom_log", False):
                        logger.removeHandler(old)
                        old.close()
                handler._layerloom_log = True
                logger.addHandler(handler)
            _active_log_path = path
            break
        except OSError as exc:
            failures.append(str(exc))
    else:
        _active_log_path = None
        # An unwritable home and temporary directory must not prevent launch.
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())
    if redirect_streams:
        if not isinstance(sys.stdout, _LoggedStream):
            sys.stdout = _LoggedStream(sys.stdout, logging.INFO)
        if not isinstance(sys.stderr, _LoggedStream):
            sys.stderr = _LoggedStream(sys.stderr, logging.ERROR)
    logger.info("Starting LayerLoom\n%s", diagnostic_text())
    if failures:
        logger.warning("Preferred logging location was unavailable: %s", "; ".join(failures))
    return _active_log_path


def diagnostic_text() -> str:
    from .version import __version__
    lines = [
        f"LayerLoom: {__version__}",
        f"Installation: {'standalone app' if getattr(sys, 'frozen', False) else 'Python/source'}",
        f"System: {platform.platform()} ({platform.machine()})",
        f"Python: {platform.python_version()}",
        f"Executable: {sys.executable}",
        f"Logs: {_active_log_path if _active_log_path is not None else 'not initialized or unavailable'}",
    ]
    for name in ("PyQt5", "pyvista", "vtk", "numpy", "trimesh", "lib3mf", "manifold3d"):
        try:
            version = importlib.metadata.version(name)
        except (importlib.metadata.PackageNotFoundError, OSError, ValueError):
            version = "metadata unavailable"
        lines.append(f"{name}: {version}")
    return "\n".join(lines)


def install_qt_message_logging() -> None:
    """Catch Qt platform initialization failures before QApplication can abort."""
    global _qt_message_handler
    if _qt_message_handler is not None:
        return
    from PyQt5 import QtCore

    def handle_message(kind, context, message):
        global _qt_fatal_reported
        level = {
            QtCore.QtDebugMsg: logging.DEBUG,
            QtCore.QtWarningMsg: logging.WARNING,
            QtCore.QtCriticalMsg: logging.ERROR,
            QtCore.QtFatalMsg: logging.CRITICAL,
        }.get(kind, logging.INFO)
        logging.getLogger("layerloom.qt").log(level, "%s", message)
        if kind == QtCore.QtFatalMsg and not _qt_fatal_reported:
            # Qt aborts after this callback returns. Never enter Qt again from
            # its fatal handler, and never display a second alert on reentry.
            _qt_fatal_reported = True
            report_startup_failure(RuntimeError(f"Qt reported a fatal error: {message}"), allow_qt=False)

    QtCore.qInstallMessageHandler(handle_message)
    _qt_message_handler = handle_message  # Retain the Python callback for Qt.


def report_startup_failure(exc: Exception, *, allow_qt: bool = True) -> None:
    """Use a native alert if the Qt stack itself could not load."""
    logging.getLogger("layerloom.startup").error("Startup failed", exc_info=(type(exc), exc, exc.__traceback__))
    location = (f"Details were saved in:\n{_active_log_path}" if _active_log_path is not None and not _log_write_failed
                else "A log file could not be created. Please copy these error details for support.")
    message = f"LayerLoom could not start.\n\n{type(exc).__name__}: {exc}\n\n{location}"
    if not getattr(sys, "frozen", False):
        message += "\n\nTry the source launcher again with --repair."
    if os.environ.get("LAYERLOOM_NO_DIALOGS") == "1":
        return
    if sys.platform == "darwin":
        script = 'on run argv\n display alert "LayerLoom could not start" message (item 1 of argv) as critical\nend run'
        try:
            result = subprocess.run(["/usr/bin/osascript", "-e", script, message], check=False, timeout=60)
            if result.returncode == 0:
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
    elif sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "LayerLoom could not start", 0x10)
            return
        except (AttributeError, OSError):
            pass
    if allow_qt:
        try:
            from PyQt5 import QtWidgets
            # Constructing QApplication can abort the process if the Qt platform
            # plugin is broken. Only use Qt if a live application already exists.
            if QtWidgets.QApplication.instance() is not None:
                QtWidgets.QMessageBox.critical(None, "LayerLoom could not start", message)
                return
        except Exception:
            pass
    try:
        if sys.stderr is not None:
            print(message, file=sys.stderr)
    except (OSError, ValueError):
        pass
