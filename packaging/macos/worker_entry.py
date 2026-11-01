"""Independent console worker, including its own Python and native libraries."""
from layerloom.worker import main

if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    # Frozen Windows consoles otherwise use the local code page, even though
    # the GUI decodes captured output as UTF-8. Nested workers use this too.
    import sys
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())
