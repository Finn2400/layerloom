"""Exercise real Qt file selection without the native provider sidebar."""
import os
from pathlib import Path

import pytest

pytest.importorskip("qtpy")
from qtpy import QtCore, QtWidgets
from layerloom import file_dialogs

pytestmark = pytest.mark.skipif(os.environ.get("LAYERLOOM_GUI_TESTS") != "1",
                                reason="Requires a desktop Qt session")


@pytest.fixture(scope="session")
def application():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_mac_chooser_limits_locations_and_keeps_cancel_safe(application, monkeypatch, tmp_path):
    local = tmp_path / "LayerLoom é"
    monkeypatch.setattr(file_dialogs, "default_output_directory", lambda: local)
    monkeypatch.setattr(file_dialogs.sys, "platform", "darwin")
    assert not local.exists()
    seen = []
    def cancel(dialog):
        assert dialog.testOption(QtWidgets.QFileDialog.DontUseNativeDialog)
        assert dialog.testOption(QtWidgets.QFileDialog.DontUseCustomDirectoryIcons)
        assert dialog.testOption(QtWidgets.QFileDialog.DontResolveSymlinks)
        assert dialog.directory().absolutePath() == str(local)
        assert [url.toLocalFile() for url in dialog.sidebarUrls()] == [str(local)]
        assert dialog.defaultSuffix() == "3mf"
        seen.append(True)
        return QtWidgets.QDialog.Rejected
    monkeypatch.setattr(QtWidgets.QFileDialog, "exec_", cancel)
    assert file_dialogs.get_save_file_name(None, "Save", str(local / "cube.3mf"), "3MF (*.3mf)") == ("", "")
    assert seen and local.is_dir() and not list(local.iterdir())


@pytest.mark.parametrize("typed_name", ["existing", "existing.stl"])
def test_mac_real_selection_suffix_and_overwrite_confirmation(application, monkeypatch, tmp_path, typed_name):
    dialog = file_dialogs._mac_dialog(None, "Save", str(tmp_path / "cube.3mf"), "3MF (*.3mf)", save=True)
    try:
        dialog.selectFile("new model é")
        assert dialog.selectedFiles() == [str(tmp_path / "new model é.3mf")]
        target = tmp_path / (typed_name + ".3mf")
        target.write_bytes(b"preserve")
        dialog.selectFile(typed_name)
        prompts = []
        def decline_overwrite():
            prompt = QtWidgets.QApplication.activeModalWidget()
            if isinstance(prompt, QtWidgets.QMessageBox):
                prompts.append(prompt.text())
                prompt.done(QtWidgets.QMessageBox.No)
        assert not dialog.testOption(QtWidgets.QFileDialog.DontConfirmOverwrite)
        timer = QtCore.QTimer(dialog)
        timer.timeout.connect(decline_overwrite)
        timer.start(10)
        QtCore.QTimer.singleShot(0, dialog.accept)
        QtCore.QTimer.singleShot(500, dialog.reject)
        assert dialog.exec_() == QtWidgets.QDialog.Rejected
        assert prompts, "The filename without an extension must still trigger overwrite confirmation"
        assert target.read_bytes() == b"preserve"
    finally:
        dialog.deleteLater()


def test_mac_open_and_directory_modes(application, tmp_path):
    for is_directory in (False, True):
        initial = tmp_path if is_directory else tmp_path / "model.3mf"
        dialog = file_dialogs._mac_dialog(None, "Choose", str(initial), "3MF (*.3mf)", directory=is_directory)
        assert dialog.fileMode() == (QtWidgets.QFileDialog.Directory if is_directory else QtWidgets.QFileDialog.ExistingFile)
        assert dialog.acceptMode() == QtWidgets.QFileDialog.AcceptOpen
        dialog.deleteLater()
