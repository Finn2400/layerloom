"""PyInstaller's windowed entrypoint; no source Python is consulted."""
import sys

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--desktop-smoke-test":
        from layerloom.app_logging import initialize_logs, install_qt_message_logging
        initialize_logs()
        install_qt_message_logging()
        from smoke_gui import main
        raise SystemExit(main(sys.argv[2]))
    from layerloom.gui_app import main
    raise SystemExit(main())
