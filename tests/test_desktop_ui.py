from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

pytest.importorskip("pyvistaqt")
from PyQt5 import QtWidgets

from layerloom.desktop_ui import install_desktop_ui
from layerloom.example_assets import find_example_file
from layerloom.gui_app import _load_current_gui_module

pytestmark = pytest.mark.skipif(
    os.environ.get("LAYERLOOM_RUN_DESKTOP_INTEGRATION") != "1",
    reason="Live Qt/VTK tests require a desktop graphics session; enable LAYERLOOM_RUN_DESKTOP_INTEGRATION=1.",
)


@pytest.fixture(scope="module")
def application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.fixture
def desktop(application, monkeypatch):
    module = _load_current_gui_module()
    window = module.QtAssignColorsApp()
    controller = install_desktop_ui(window)
    errors = []
    monkeypatch.setattr(window, "_error", lambda title, text: errors.append((title, text)))
    window.resize(1200, 800)
    window.show()
    application.processEvents()
    yield window, controller, errors, module
    window.close()
    if controller.example_directory:
        controller.example_directory.cleanup()
    application.processEvents()


def test_welcome_actions_are_clickable_and_fit_the_panel(desktop, application):
    window, controller, errors, _ = desktop
    assert install_desktop_ui(window) is controller
    panel = window.viewer_empty_hint
    assert panel.isVisible()
    buttons = panel.findChildren(QtWidgets.QPushButton)
    assert len(buttons) == 2
    for button in buttons:
        assert button.isVisible()
        assert button.isEnabled()
        assert panel.rect().contains(button.mapTo(panel, button.rect().center()))
    assert not errors


def test_try_example_stamps_identities_preserves_source_and_assignments(desktop, application, tmp_path):
    window, controller, errors, module = desktop
    source = find_example_file("tutorial_cmy_cubes.3mf")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    assert controller.try_example()
    application.processEvents()
    assert not errors
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert Path(window.base_file).parent == Path(controller.example_directory.name)
    assert Path(window.source_file_path).parent == Path(controller.example_directory.name)
    assert window.base_file != str(source)
    assert len(window.model_objects) == 4
    assert all(f"__ID_{oid}__" in name for oid, name in window.model_objects)
    expected = {}
    for oid, name in window.model_objects:
        token = module._V58._extract_pat(name)
        expected[oid] = token
        assert window.assignments[oid]["token"] == token
    assert set(expected.values()) == {"c", "y", "cy", "cm"}
    pixels = window.viewer.plotter.screenshot(return_img=True)
    assert pixels is not None and pixels.std() > 10
    assert not window.viewer_empty_hint.isVisible()
    window._set_viewer_hint("Preview temporarily unavailable")
    application.processEvents()
    assert window.viewer_empty_hint.isVisible()
    assert not window.viewer_empty_hint.actions.isVisible()
    assert not window.viewer_empty_hint.findChild(QtWidgets.QPushButton, "tryExampleButton").isVisible()
    window._set_viewer_hint(None)

    output = tmp_path / "woven model.3mf"
    controller.weave_complete(output)
    application.processEvents()
    assert controller.result_dock.isVisible()
    assert str(output) in controller.result_text.text()
    assert "0.16 mm" in controller.result_text.text()
    window._load_canonical_model(window.base_file)
    application.processEvents()
    assert controller.result_dock.isVisible()
    assert controller.output_path == output
    assert not errors


def test_repeating_example_replaces_private_copy_without_modifying_source(desktop, application):
    window, controller, errors, _ = desktop
    assert controller.try_example()
    first = Path(controller.example_directory.name)
    assert controller.try_example()
    application.processEvents()
    second = Path(controller.example_directory.name)
    assert first != second
    assert not first.exists()
    assert Path(window.base_file).parent == second
    assert second.exists()
    assert not errors


def test_benchy_toolbar_uses_private_copy_without_mutating_asset(desktop, application, monkeypatch):
    window, controller, errors, _ = desktop
    source = find_example_file("tutorial_cmy_benchy_cutup.3mf")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    loaded = []
    # Verify the toolbar connection, copy, and real ID-stamping without spending
    # time rendering the much larger Benchy in this onboarding regression.
    monkeypatch.setattr(window, "_load_canonical_model", lambda path: loaded.append(path))
    window._load_example_action.trigger()
    application.processEvents()
    assert len(loaded) == 1
    assert Path(loaded[0]).parent == Path(controller.example_directory.name)
    assert Path(loaded[0]).name == "preview_id_tutorial_cmy_benchy_cutup.3mf"
    assert Path(window.source_file_path).parent == Path(controller.example_directory.name)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert not errors
