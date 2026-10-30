"""File choosers without macOS's provider-enumerating native sidebar.

Keep access scoped to a user-selected directory. In particular, do not restore
Finder favourites, resolve sidebar aliases, or ask the OS for directory icons.
Other platforms retain their standard dialogs.
"""
from pathlib import Path
import sys

from qtpy import QtCore, QtWidgets


def default_output_directory():
    # Unlike Documents/Desktop/Downloads, this is not a macOS protected folder.
    # Return a suggestion only; opening an example must not create directories.
    return Path.home() / "LayerLoom"


class _MacFileDialog(QtWidgets.QFileDialog):
    def accept(self):
        # QFileDialog's defaultSuffix only handles names with no suffix. The
        # exporters also append their suffix to e.g. model.stl, so normalize
        # that case BEFORE Qt checks whether the final output already exists.
        suffix = self.defaultSuffix()
        paths = self.selectedFiles()
        if self.acceptMode() == self.AcceptSave and suffix and paths:
            if not paths[0].lower().endswith("." + suffix.lower()):
                # Qt leaves focused filename edits untouched in selectFile().
                self.setFocus()
                self.selectFile(paths[0] + "." + suffix)
        super().accept()


def _mac_dialog(parent, title, initial, name_filter, *, save=False, directory=False):
    fallback = default_output_directory()
    initial = Path(initial).expanduser() if initial else fallback
    folder = initial if directory or initial == fallback else initial.parent
    if folder == fallback:
        folder.mkdir(parents=True, exist_ok=True)
    # Pass the directory into the constructor, so the file model does not start
    # by listing the process's working directory (often Documents for sources).
    dialog = _MacFileDialog(parent, title, str(folder), name_filter)
    dialog.setOption(QtWidgets.QFileDialog.DontUseNativeDialog, True)
    dialog.setOption(QtWidgets.QFileDialog.DontUseCustomDirectoryIcons, True)
    dialog.setOption(QtWidgets.QFileDialog.DontResolveSymlinks, True)
    dialog.setSidebarUrls([QtCore.QUrl.fromLocalFile(str(folder))])
    dialog.setHistory([str(folder)])
    dialog.setAcceptMode(QtWidgets.QFileDialog.AcceptSave if save else QtWidgets.QFileDialog.AcceptOpen)
    dialog.setFileMode(QtWidgets.QFileDialog.Directory if directory else
                       QtWidgets.QFileDialog.AnyFile if save else QtWidgets.QFileDialog.ExistingFile)
    if directory:
        dialog.setOption(QtWidgets.QFileDialog.ShowDirsOnly, True)
    elif initial != fallback:
        dialog.selectFile(initial.name)
    if save:
        # Include the extension before QFileDialog's overwrite confirmation.
        # Otherwise appending it afterwards could silently replace a file.
        suffix = {"3MF (*.3mf)": "3mf", "3MF files (*.3mf)": "3mf",
                  "LayerLoom paint (*.llpaint)": "llpaint"}.get(name_filter)
        if suffix:
            dialog.setDefaultSuffix(suffix)
    return dialog


def _choose(parent, title, initial, name_filter, **options):
    try:
        dialog = _mac_dialog(parent, title, initial, name_filter, **options)
    except OSError as exc:
        QtWidgets.QMessageBox.critical(parent, "Could not open folder", str(exc))
        return "", ""
    try:
        if dialog.exec_() == QtWidgets.QDialog.Accepted and dialog.selectedFiles():
            return dialog.selectedFiles()[0], dialog.selectedNameFilter()
        return "", ""
    finally:
        dialog.deleteLater()


def get_open_file_name(parent, title, initial="", name_filter=""):
    if sys.platform != "darwin":
        return QtWidgets.QFileDialog.getOpenFileName(parent, title, initial, name_filter)
    return _choose(parent, title, initial, name_filter)


def get_save_file_name(parent, title, initial="", name_filter=""):
    if sys.platform != "darwin":
        return QtWidgets.QFileDialog.getSaveFileName(parent, title, initial, name_filter)
    return _choose(parent, title, initial, name_filter, save=True)


def get_existing_directory(parent, title, initial=""):
    if sys.platform != "darwin":
        return QtWidgets.QFileDialog.getExistingDirectory(parent, title, initial)
    return _choose(parent, title, initial, "", directory=True)[0]
