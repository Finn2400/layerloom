"""Small per-file import dialog; no settings are persisted implicitly."""
from qtpy import QtCore, QtWidgets
from .desktop_glb_import import ImportSettings


class GlbImportDialog(QtWidgets.QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Import model')
        self.setMinimumWidth(430)
        layout = QtWidgets.QVBoxLayout(self)
        tabs = QtWidgets.QTabWidget(self)
        normal = QtWidgets.QWidget(); basic = QtWidgets.QVBoxLayout(normal)
        from pathlib import Path
        name = QtWidgets.QLabel(Path(path).name); name.setWordWrap(True)
        name.setTextFormat(QtCore.Qt.PlainText)
        basic.addWidget(name)
        text = QtWidgets.QLabel('Keep the model’s colors and import it onto the build plate.\n\n'
                               'Default: automatic colors, standard detail, up to 64 source colors.\n'
                               'These are color regions, not the number of filaments.')
        text.setWordWrap(True); basic.addWidget(text); basic.addStretch()
        tabs.addTab(normal, 'Import')
        advanced = QtWidgets.QWidget(); form = QtWidgets.QFormLayout(advanced)
        def combo(label, choices):
            widget = QtWidgets.QComboBox()
            for title, value in choices: widget.addItem(title, value)
            form.addRow(label, widget); return widget
        self.source = combo('Color source', [('Automatic', 'auto'), ('Texture only', 'texture'),
                                             ('Vertex colors only', 'vertex'), ('Material color only', 'material')])
        self.detail = combo('Color detail', [('Standard', 'standard'), ('Fine (more memory)', 'fine'),
                                            ('Original triangles (less detail)', 'original')])
        self.colors = QtWidgets.QSpinBox(); self.colors.setRange(2, 256); self.colors.setValue(64)
        self.colors.setToolTip('Maximum source-color groups, before matching your filament recipes.')
        form.addRow('Maximum source colors', self.colors)
        self.units = combo('Source units', [('glTF standard (metres)', 'meters'), ('Millimetres', 'millimeters'),
                                           ('Legacy LayerLoom scale (0.1×)', 'legacy')])
        self.up = combo('Up direction', [('glTF standard (Y up)', 'y'), ('Z up', 'z')])
        note = QtWidgets.QLabel('Use units and orientation overrides only when the file was exported differently. '
                                'Open surfaces are flagged; automatic mesh repair is not applied.')
        note.setWordWrap(True); form.addRow(note)
        reset = QtWidgets.QPushButton('Reset to defaults'); reset.clicked.connect(self.reset_defaults); form.addRow(reset)
        tabs.addTab(advanced, 'Advanced'); layout.addWidget(tabs)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText('Import')
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def reset_defaults(self):
        for box in (self.source, self.detail, self.units, self.up): box.setCurrentIndex(0)
        self.colors.setValue(64)

    def settings(self):
        return ImportSettings(colors=self.colors.value(), color_source=self.source.currentData(),
                              detail=self.detail.currentData(), units=self.units.currentData(), up_axis=self.up.currentData())


def choose_settings(path, parent=None):
    dialog = GlbImportDialog(path, parent)
    return dialog.settings() if dialog.exec_() == QtWidgets.QDialog.Accepted else None
