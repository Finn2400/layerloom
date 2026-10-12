"""Exercise the same GUI/example/weaving path from a frozen executable in CI."""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path


def main(directory: str) -> int:
    output = Path(directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    model_output = Path(tempfile.mkdtemp(prefix="example-output-", dir=output))
    report = {"frozen": bool(getattr(sys, "frozen", False)), "executable": sys.executable}
    window = None
    try:
        from layerloom.app_logging import initialize_logs
        initialize_logs()
        from layerloom.gui_app import _load_current_gui_module
        from layerloom.desktop_ui import install_desktop_ui
        gui = _load_current_gui_module()
        qt = gui._V58.QtWidgets
        app = qt.QApplication.instance() or qt.QApplication(["LayerLoom smoke test"])
        window = gui.QtAssignColorsApp()
        controller = install_desktop_ui(window)
        errors = []
        window._error = lambda title, message: errors.append(f"{title}: {message}")
        window._warn = lambda title, message: errors.append(f"{title}: {message}")
        window.show()
        app.processEvents()
        controller.try_example()
        app.processEvents()
        assert window.model_objects, "Example contains no model objects"
        assert window.viewer and window.viewer.plotter, "Viewer did not initialize"
        assert not errors, errors
        assert abs(window.layer_height_spin.value() - 0.16) < 1e-8
        window.viewer.plotter.render()
        image = window.viewer.plotter.screenshot(str(output / "example-render.png"))
        assert image is not None and image.std() > 5, "Example render is blank"
        assert window.grab().save(str(output / "example-window.png")), "Could not save window proof"
        # Exercise the real Weave action, replacing only the user's folder choice.
        old_dialog = qt.QFileDialog.getExistingDirectory
        qt.QFileDialog.getExistingDirectory = lambda *_a, **_kw: str(model_output)
        try:
            window._on_weave()
        finally:
            qt.QFileDialog.getExistingDirectory = old_dialog
        app.processEvents()
        assert not errors, errors
        woven = sorted(model_output.glob("*_woven.3mf"))
        assert len(woven) == 1 and woven[0].stat().st_size > 0, "No woven example was written"
        # The real action reopens the output automatically; verify its model and rendering.
        assert window.model_objects and window.current_file
        window.viewer.plotter.render()
        window.viewer.plotter.screenshot(str(output / "woven-render.png"))
        assert window.grab().save(str(output / "woven-window.png"))
        report.update(status="passed", output=str(woven[0]), objects=len(window.model_objects))
        return 0
    except Exception:
        report.update(status="failed", traceback=traceback.format_exc())
        return 1
    finally:
        (output / "gui-smoke.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if window is not None:
            window.close()
