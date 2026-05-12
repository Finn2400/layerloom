#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LayerLoom v62
-------------

v61 large-model preview behavior plus first-class expanded weave tokens for
gray, orange, violet, and green. The extra palette dimensions are opt-in from
the GUI, like the existing black/white toggles.
"""

from __future__ import annotations

from collections import Counter
import importlib.util
import os
import sys
from typing import Dict, List, Tuple

try:
    from layerloom.palette_utils import build_layer_fraction_palette, corrected_hex, get_preset_spec
    from layerloom.tokens import ALL_TOKENS, BASE_TOKENS, token_is_valid
except Exception:
    from palette_utils import build_layer_fraction_palette, corrected_hex, get_preset_spec
    from tokens import ALL_TOKENS, BASE_TOKENS, token_is_valid


_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_V61_PATH = os.path.join(_THIS_DIR, "3mf_gui_v61.py")
_SPEC = importlib.util.spec_from_file_location("layerloom_gui_v61_runtime_for_v62", _V61_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Could not load v61 base module from {_V61_PATH}")
_V61 = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _V61
_SPEC.loader.exec_module(_V61)
_V58 = _V61._V58


def _checked(widget) -> bool:
    try:
        return bool(widget.isChecked())
    except Exception:
        return False


def _fractions_for_token(token: str) -> Dict[str, float]:
    counts = Counter(token)
    n = max(len(token), 1)
    return {letter.upper(): counts.get(letter, 0) / n for letter in ALL_TOKENS}


def _entry_for_token(token: str) -> Dict[str, object] | None:
    tok = str(token or "").strip().lower()
    if not tok or not token_is_valid(tok):
        return None
    return {
        "token": tok,
        "hex": corrected_hex(tok),
        "distinct_colors": len(set(tok)),
        "fractions": _fractions_for_token(tok),
    }


class QtAssignColorsApp(_V61.QtAssignColorsApp):
    def __init__(self):
        self.add_gray_checkbox = None
        self.add_orange_checkbox = None
        self.add_violet_checkbox = None
        self.add_green_checkbox = None
        super().__init__()
        self._install_v62_palette_controls()
        self._load_palette(self.current_palette_name)
        self.setWindowTitle("LayerLoom — Color Assigner v62")

    def _install_v62_palette_controls(self) -> None:
        """Replace mixed legacy filters with consistent v62 add/require rows."""
        if not hasattr(self, "palette_scroll"):
            return
        palette_box = self.palette_scroll.parentWidget()
        palette_layout = palette_box.layout() if palette_box is not None else None
        if palette_layout is None:
            return

        # Hide inherited K/W checkboxes and C/M/Y/K/W require buttons. v62
        # rebuilds these controls with one visual language and independent O/V/G.
        for widget_name in ("add_black_checkbox", "add_white_checkbox"):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.hide()
        for btn in list(getattr(self, "req_buttons", {}).values()):
            try:
                btn.hide()
            except Exception:
                pass

        def make_toggle(text: str, tip: str):
            btn = _V58.QtWidgets.QToolButton()
            btn.setText(text)
            btn.setToolTip(tip)
            btn.setCheckable(True)
            btn.setAutoRaise(False)
            btn.toggled.connect(self._on_expanded_palette_toggle)
            return btn

        add_row = _V58.QtWidgets.QHBoxLayout()
        add_row.setSpacing(6)
        add_label = _V58.QtWidgets.QLabel("Add")
        add_label.setObjectName("muted")
        add_row.addWidget(add_label)
        self.add_black_checkbox = make_toggle("K", "Enable black")
        self.add_white_checkbox = make_toggle("W", "Enable white")
        self.add_gray_checkbox = make_toggle("N", "Enable neutral gray")
        self.add_orange_checkbox = make_toggle("O", "Enable orange")
        self.add_violet_checkbox = make_toggle("V", "Enable violet")
        self.add_green_checkbox = make_toggle("G", "Enable green")
        for btn in (
            self.add_black_checkbox,
            self.add_white_checkbox,
            self.add_gray_checkbox,
            self.add_orange_checkbox,
            self.add_violet_checkbox,
            self.add_green_checkbox,
        ):
            add_row.addWidget(btn)
        add_row.addStretch(1)

        req_row = _V58.QtWidgets.QHBoxLayout()
        req_row.setSpacing(6)
        req_label = _V58.QtWidgets.QLabel("Require")
        req_label.setObjectName("muted")
        req_row.addWidget(req_label)
        self.req_buttons = {}
        for letter, tip in (
            ("C", "Require cyan"),
            ("M", "Require magenta"),
            ("Y", "Require yellow"),
            ("K", "Require black"),
            ("W", "Require white"),
            ("N", "Require gray"),
            ("O", "Require orange"),
            ("V", "Require violet"),
            ("G", "Require green"),
        ):
            btn = _V58.QtWidgets.QToolButton()
            btn.setText(letter)
            btn.setToolTip(tip)
            btn.setCheckable(True)
            btn.setAutoRaise(False)
            btn.toggled.connect(self._on_req_letters_toggle)
            req_row.addWidget(btn)
            self.req_buttons[letter.lower()] = btn
        req_row.addStretch(1)

        insert_at = palette_layout.indexOf(self.palette_scroll)
        if insert_at < 0:
            insert_at = palette_layout.count()
        palette_layout.insertLayout(insert_at, req_row)
        palette_layout.insertLayout(insert_at, add_row)

    def _on_expanded_palette_toggle(self, _checked: bool) -> None:
        self._load_palette(self.current_palette_name)

    def _palette_alphabet(self) -> Tuple[str, ...]:
        letters: List[str] = list(BASE_TOKENS)
        if _checked(getattr(self, "add_black_checkbox", None)):
            letters.append("k")
        if _checked(getattr(self, "add_white_checkbox", None)):
            letters.append("w")
        if _checked(getattr(self, "add_gray_checkbox", None)):
            letters.append("n")
        if _checked(getattr(self, "add_orange_checkbox", None)):
            letters.append("o")
        if _checked(getattr(self, "add_violet_checkbox", None)):
            letters.append("v")
        if _checked(getattr(self, "add_green_checkbox", None)):
            letters.append("g")
        return tuple(dict.fromkeys(letters))

    def _load_palette(self, name: str):
        try:
            spec = get_preset_spec(str(name or "Normal").lower())
        except Exception:
            spec = get_preset_spec("normal")
            name = "Normal"
        alphabet = self._palette_alphabet()
        entries = [
            dict(entry)
            for entry in build_layer_fraction_palette(
                alphabet,
                max_height=spec["max_height"],
                max_run=spec["max_run"],
                distinct_lte=spec["distinct_lte"],
            )
        ]
        if _checked(getattr(self, "limit_two_checkbox", None)):
            entries = [e for e in entries if len(set(e.get("token", ""))) <= 2]
        entries = _V58._sort_by_hue(entries)
        entries = self._filter_by_required_letters(entries, self._required_letters())
        self.current_palette_name = str(name or spec["name"])
        self.current_palette = entries
        self._render_palette_grid()
        self._refresh_status_summary()

    def _exact_palette_entry_for_token(self, token: str):
        tok = str(token or "").strip().lower()
        if not tok:
            return None
        for entry in getattr(self, "current_palette", []) or []:
            if str(entry.get("token", "")).strip().lower() == tok:
                return entry
        return _entry_for_token(tok)

    def _refresh_status_summary(self):
        super()._refresh_status_summary()
        try:
            suffix = " | OVG/gray enabled" if (
                _checked(getattr(self, "add_gray_checkbox", None))
                or _checked(getattr(self, "add_orange_checkbox", None))
                or _checked(getattr(self, "add_violet_checkbox", None))
                or _checked(getattr(self, "add_green_checkbox", None))
            ) else ""
            text = self.toolbar_status_label.text()
            if suffix and suffix not in text:
                self.toolbar_status_label.setText(text + suffix)
        except Exception:
            pass


def main():
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        script_dir = os.getcwd()

    _V58.APP_TITLE = "LayerLoom — Color Assigner v62"
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
        raise RuntimeError("Qt dependencies are unavailable; cannot launch v62.")

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
