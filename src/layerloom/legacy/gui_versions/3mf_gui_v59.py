#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LayerLoom v59
-------------

Qt single-window UI with universal 3MF import normalization.
"""

from __future__ import annotations

import importlib.util
import os
import sys

try:
    from layerloom.normalize_3mf_import import normalize_3mf_import
except Exception:
    from normalize_3mf_import import normalize_3mf_import


_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_V58_PATH = os.path.join(_THIS_DIR, "3mf_gui_v58.py")
_SPEC = importlib.util.spec_from_file_location("layerloom_gui_v58_runtime", _V58_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Could not load v58 base module from {_V58_PATH}")
_V58 = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _V58
_SPEC.loader.exec_module(_V58)


class QtAssignColorsApp(_V58.QtAssignColorsApp):
    def __init__(self):
        super().__init__()
        # Keep the grid uniform, just tighten the inter-swatch gaps a bit.
        self.palette_grid.setHorizontalSpacing(6)
        self.palette_grid.setVerticalSpacing(8)
        self.palette_grid.setContentsMargins(0, 0, 0, 0)

    def _normalize_incoming_3mf(self, path: str, title: str) -> str | None:
        try:
            result = normalize_3mf_import(path)
        except Exception as e:
            self._error(title, f"Failed to normalize 3MF import:\n{e}")
            return None
        for warning in result.warnings[:8]:
            _V58._log("WARN", f"[3mf-normalize] {warning}")
        if len(result.warnings) > 8:
            _V58._log("WARN", f"[3mf-normalize] … {len(result.warnings) - 8} additional warning(s)")
        return result.normalized_path

    def _on_open(self):
        path, _ = _V58.QtWidgets.QFileDialog.getOpenFileName(self, "Open 3MF", "", "3MF Files (*.3mf)")
        if not path:
            return
        normalized = self._normalize_incoming_3mf(path, "Open 3MF")
        if not normalized:
            return
        self._pending_source_hex_by_name = {}
        stamped = _V58._stamp_ids_only(normalized)
        self._load_canonical_model(stamped)
        _V58._log("INFO", f"Opened: {path}")
        if normalized != path:
            _V58._log("INFO", f"[3mf-normalize] canonical temp → {normalized}")
        if stamped != normalized:
            _V58._log("INFO", f"Previewing ID-stamped temp copy: {stamped}")

    def _on_open_glb(self):
        path, _ = _V58.QtWidgets.QFileDialog.getOpenFileName(self, "Open GLB/GLTF", "", "GLB/GLTF Files (*.glb *.gltf)")
        if not path:
            return
        try:
            import_3mf, manifest_map, repair_warning, color_levels = self._run_glb_import_pipeline(path)
        except Exception as e:
            self._error("Open GLB", f"Failed to import GLB:\n{e}")
            return
        normalized = self._normalize_incoming_3mf(import_3mf, "Open GLB")
        if not normalized:
            return
        self._pending_source_hex_by_name = manifest_map
        stamped = _V58._stamp_ids_only(normalized)
        self._load_canonical_model(stamped)
        _V58._log("INFO", f"Opened GLB: {path}")
        _V58._log(
            "INFO",
            f"GLB import produced 3MF: {import_3mf} (target_colors={self.glb_colors_spin.value()}, color_levels={color_levels})",
        )
        if normalized != import_3mf:
            _V58._log("INFO", f"[3mf-normalize] canonical temp → {normalized}")
        if repair_warning:
            _V58._log("WARN", f"[glb-import] {repair_warning}")
            if not self.large_model_mode:
                self.status_bar.showMessage("GLB imported; repair warning logged.", 5000)
        elif not self.large_model_mode:
            self.status_bar.showMessage(f"GLB imported and auto-matched using palette '{self.current_palette_name}'.", 5000)


def main():
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        script_dir = os.getcwd()

    _V58.APP_TITLE = "LayerLoom — Color Assigner v59"
    _V58.PALETTES_DIR = os.path.join(script_dir, "palettes")
    if not os.path.isdir(_V58.PALETTES_DIR):
        _V58._log("WARN", f"'palettes' directory not found at {_V58.PALETTES_DIR}. Using dummy palette data.")
        _V58.PALETTES_DIR = ""
    _V58.PALETTE_FILES = {
        "Simple": os.path.join(_V58.PALETTES_DIR, "simple_palette.json"),
        "Normal": os.path.join(_V58.PALETTES_DIR, "normal_palette.json"),
        "Full": os.path.join(_V58.PALETTES_DIR, "full_palette.json"),
    }

    try:
        if hasattr(sys, "_MEIPASS") and _V58.trimesh is not None:
            _V58.trimesh.constants.GLTF_VALIDATOR = os.path.join(sys._MEIPASS, "gltf_validator")
    except Exception:
        pass

    if not _V58._QT_OK:
        raise RuntimeError("Qt dependencies are unavailable; cannot launch v59.")

    app = _V58.QtWidgets.QApplication.instance() or _V58.QtWidgets.QApplication(sys.argv)
    app.setApplicationName("LayerLoom")
    app.setStyle("Fusion")

    palette = _V58.QtGui.QPalette()
    palette.setColor(_V58.QtGui.QPalette.Window, _V58.QtGui.QColor(31, 31, 31))
    palette.setColor(_V58.QtGui.QPalette.WindowText, _V58.QtGui.QColor(233, 233, 233))
    palette.setColor(_V58.QtGui.QPalette.Base, _V58.QtGui.QColor(23, 23, 23))
    palette.setColor(_V58.QtGui.QPalette.AlternateBase, _V58.QtGui.QColor(30, 30, 30))
    palette.setColor(_V58.QtGui.QPalette.ToolTipBase, _V58.QtGui.QColor(35, 35, 35))
    palette.setColor(_V58.QtGui.QPalette.ToolTipText, _V58.QtGui.QColor(240, 240, 240))
    palette.setColor(_V58.QtGui.QPalette.Text, _V58.QtGui.QColor(233, 233, 233))
    palette.setColor(_V58.QtGui.QPalette.Button, _V58.QtGui.QColor(43, 43, 43))
    palette.setColor(_V58.QtGui.QPalette.ButtonText, _V58.QtGui.QColor(233, 233, 233))
    palette.setColor(_V58.QtGui.QPalette.BrightText, _V58.QtGui.QColor(255, 255, 255))
    palette.setColor(_V58.QtGui.QPalette.Highlight, _V58.QtGui.QColor(57, 82, 106))
    palette.setColor(_V58.QtGui.QPalette.HighlightedText, _V58.QtGui.QColor(255, 255, 255))

    palette.setColor(_V58.QtGui.QPalette.Disabled, _V58.QtGui.QPalette.Text, _V58.QtGui.QColor(176, 176, 176))
    palette.setColor(_V58.QtGui.QPalette.Disabled, _V58.QtGui.QPalette.ButtonText, _V58.QtGui.QColor(176, 176, 176))
    palette.setColor(_V58.QtGui.QPalette.Disabled, _V58.QtGui.QPalette.WindowText, _V58.QtGui.QColor(176, 176, 176))
    palette.setColor(_V58.QtGui.QPalette.Disabled, _V58.QtGui.QPalette.HighlightedText, _V58.QtGui.QColor(220, 220, 220))
    app.setPalette(palette)

    window = QtAssignColorsApp()
    window.setStyleSheet(
        window.styleSheet()
        + """
        QLabel { color: #e9e9e9; }
        QPushButton:disabled, QToolButton:disabled, QCheckBox:disabled {
            color: #b0b0b0;
            background: #4a4a4a;
            border: 1px solid #666666;
        }
        QComboBox:disabled, QDoubleSpinBox:disabled, QSpinBox:disabled, QLineEdit:disabled {
            color: #b8b8b8;
            background: #2a2a2a;
            border: 1px solid #555555;
        }
        QTreeWidget, QAbstractItemView, QHeaderView::section {
            color: #f0f0f0;
        }
        """
    )
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
