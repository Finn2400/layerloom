"""Exercise the production window factory, including its GL reparenting path."""
import importlib.util
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('LAYERLOOM_RUN_DESKTOP_INTEGRATION') != '1',
                                reason='Requires a native Qt/VTK display')


def test_production_startup_and_mode_changes_preserve_live_presentation():
    from qtpy import QtCore, QtTest
    from layerloom.gui_app import _load_current_gui_module
    path = Path(__file__).resolve().parents[1] / 'packaging/macos/smoke_gui.py'
    spec = importlib.util.spec_from_file_location('presentation_smoke', path)
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    # Bare QtAssignColorsApp construction alone missed the issue: reproduce the
    # actual startup factory, styling, initialized GL context and reparenting.
    app, window = _load_current_gui_module().create_main_window()
    window.show(); window.raise_()
    assert QtTest.QTest.qWaitForWindowExposed(window, 5000)
    controller = window._desktop_controller
    try:
        assert controller.try_benchy_example()
        assert sum(len(mesh.faces) for mesh in window.paint_panel.editor.document.meshes) == 253176
        for paint in (False, True, False):
            window._set_paint_mode(paint)
            app.processEvents()
            plotter = window.paint_panel.editor.plotter if paint else window.viewer.plotter
            result = smoke.verify_camera_presentation(plotter, QtCore, timeout_ms=2000)
            assert result['qt_presented_steps'] == 3
        # Loading another model must not lose the bridge or require a resize.
        assert controller.try_example()
        assert smoke.verify_camera_presentation(window.viewer.plotter, QtCore)['qt_presented_steps'] == 3
    finally:
        window.paint_panel.editor.dirty = False
        window.close(); app.processEvents()
