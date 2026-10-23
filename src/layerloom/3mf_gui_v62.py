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
    from layerloom.example_assets import DEFAULT_GUI_EXAMPLE, find_example_file
    from layerloom.palette_utils import build_layer_fraction_palette, corrected_hex, get_preset_spec
    from layerloom.tokens import ALL_TOKENS, BASE_TOKENS, token_is_valid
except Exception:
    from example_assets import DEFAULT_GUI_EXAMPLE, find_example_file
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

WEAVE_BUTTON_STYLE = """
QToolButton#primaryWeaveButton {
    background: #e89b2f;
    color: #101010;
    border: 1px solid #f3c16f;
    border-radius: 5px;
    padding: 5px 14px;
    font-weight: 700;
}
QToolButton#primaryWeaveButton:hover {
    background: #f2ad44;
    border-color: #ffd28a;
}
QToolButton#primaryWeaveButton:pressed {
    background: #c9791e;
    border-color: #e7a14f;
}
QToolButton#primaryWeaveButton:disabled {
    background: #5b4630;
    color: #a9a9a9;
    border-color: #6b5843;
}
"""


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

    def _build_toolbar(self):
        super()._build_toolbar()
        self._install_load_example_button()
        self._promote_weave_button()

    def _install_load_example_button(self) -> None:
        """Add a one-click CMY Benchy example beside the regular file opener."""
        if getattr(self, "_load_example_action", None) is not None:
            return
        toolbars = self.findChildren(_V58.QtWidgets.QToolBar)
        toolbar = toolbars[0] if toolbars else None
        if toolbar is None:
            return

        action = _V58.QtWidgets.QAction("Load Example", self)
        action.setToolTip("Open the packaged CMY cut-up Benchy tutorial model.")
        action.triggered.connect(self._on_load_example)
        self._load_example_action = action

        actions = toolbar.actions()
        open_action = next((act for act in actions if act.text().strip() == "Open 3MF"), None)
        if open_action is None:
            toolbar.addAction(action)
            return
        open_idx = actions.index(open_action)
        before = actions[open_idx + 1] if open_idx + 1 < len(actions) else None
        toolbar.insertAction(before, action)

    def _on_load_example(self) -> None:
        path = find_example_file(DEFAULT_GUI_EXAMPLE)
        if path is None:
            self._error(
                "Load Example",
                f"Could not find packaged example {DEFAULT_GUI_EXAMPLE}.\n"
                "Try reinstalling LayerLoom or open examples/tutorial_cmy_benchy_cutup.3mf manually.",
            )
            return
        try:
            normalized = self._normalize_incoming_3mf(str(path), "Load Example")
            if not normalized:
                return
            self._pending_source_hex_by_name = {}
            stamped = self._stamp_ids_only(normalized)
            self.document_path = str(path)
            self._load_canonical_model(stamped)
            _V58._log("INFO", f"Loaded example: {path}")
            self.status_bar.showMessage(f"Loaded example: {path.name}", 5000)
        except Exception as exc:
            self._error("Load Example", f"Failed to load {path.name}:\n{exc}")

    def _promote_weave_button(self) -> None:
        """Make the final weaving action visually distinct from setup tools."""
        for btn in self.findChildren(_V58.QtWidgets.QToolButton):
            action = btn.defaultAction()
            if action is None or action.text().strip().lower() != "weave":
                continue
            btn.setObjectName("primaryWeaveButton")
            btn.setToolTip(
                "Generate the woven 3MF using the current placement, layer height, and assignments."
            )
            btn.setMinimumHeight(max(btn.minimumHeight(), 30))
            btn.setStyleSheet(WEAVE_BUTTON_STYLE)
            return

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

        add_row = _V58.QtWidgets.QGridLayout()
        add_row.setSpacing(6)
        add_label = _V58.QtWidgets.QLabel("Optional colors")
        add_label.setObjectName("muted")
        add_row.addWidget(add_label, 0, 0, 1, 3)
        self.add_black_checkbox = make_toggle("K · Black", "Add recipes that can use black filament.")
        self.add_white_checkbox = make_toggle("W · White", "Add recipes that can use white filament.")
        self.add_gray_checkbox = make_toggle("N · Gray", "Add recipes that can use gray filament.")
        self.add_orange_checkbox = make_toggle("O · Orange", "Add recipes that can use orange filament.")
        self.add_violet_checkbox = make_toggle("V · Violet", "Add recipes that can use violet filament.")
        self.add_green_checkbox = make_toggle("G · Green", "Add recipes that can use green filament.")
        for index, btn in enumerate((
            self.add_black_checkbox,
            self.add_white_checkbox,
            self.add_gray_checkbox,
            self.add_orange_checkbox,
            self.add_violet_checkbox,
            self.add_green_checkbox,
        )):
            add_row.addWidget(btn, 1 + index // 3, index % 3)

        req_row = _V58.QtWidgets.QGridLayout()
        req_row.setSpacing(6)
        req_label = _V58.QtWidgets.QLabel("Show recipes containing")
        req_label.setObjectName("muted")
        req_row.addWidget(req_label, 0, 0, 1, 3)
        self.req_buttons = {}
        for index, (letter, tip) in enumerate((
            ("C", "Require cyan"),
            ("M", "Require magenta"),
            ("Y", "Require yellow"),
            ("K", "Require black"),
            ("W", "Require white"),
            ("N", "Require gray"),
            ("O", "Require orange"),
            ("V", "Require violet"),
            ("G", "Require green"),
        )):
            btn = _V58.QtWidgets.QToolButton()
            color_name = tip.removeprefix("Require ")
            btn.setText(f"{letter} · {color_name.capitalize()}")
            btn.setToolTip(f"Show only recipes that include {color_name}.")
            btn.setCheckable(True)
            btn.setAutoRaise(False)
            btn.toggled.connect(self._on_req_letters_toggle)
            req_row.addWidget(btn, 1 + index // 3, index % 3)
            self.req_buttons[letter.lower()] = btn

        insert_at = palette_layout.indexOf(self.palette_scroll)
        if insert_at < 0:
            insert_at = palette_layout.count()
        palette_layout.insertLayout(insert_at, req_row)
        palette_layout.insertLayout(insert_at, add_row)
        help_label = _V58.QtWidgets.QLabel(
            "Select a part, then choose a color. Each recipe repeats its letters as printed layers: "
            "C = cyan, M = magenta, Y = yellow. For example, cy alternates cyan and yellow."
        )
        help_label.setWordWrap(True)
        help_label.setObjectName("muted")
        palette_layout.insertWidget(insert_at, help_label)

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
        requested_name = str(name or "Normal")
        palette_path = _V58.PALETTE_FILES.get(requested_name)
        if palette_path and requested_name not in {"Simple", "Normal", "Full"}:
            entries = _V58._load_palette_file(
                palette_path,
                limit_two=_checked(getattr(self, "limit_two_checkbox", None)),
            )
            entries = _V58._filter_by_bw_visibility(
                entries,
                add_k=_checked(getattr(self, "add_black_checkbox", None)),
                add_w=_checked(getattr(self, "add_white_checkbox", None)),
            )
            entries = self._filter_by_required_letters(entries, self._required_letters())
            self.current_palette_name = requested_name
            self.current_palette = entries
            self._render_palette_grid()
            self._refresh_assignment_hexes_from_current_palette()
            self._refresh_status_summary()
            return

        try:
            spec = get_preset_spec(requested_name.lower())
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
        self._refresh_assignment_hexes_from_current_palette()
        self._refresh_status_summary()

    def _exact_palette_entry_for_token(self, token: str):
        tok = str(token or "").strip().lower()
        if not tok:
            return None
        palette_path = _V58.PALETTE_FILES.get(str(getattr(self, "current_palette_name", "") or ""))
        if palette_path and str(getattr(self, "current_palette_name", "") or "") not in {"Simple", "Normal", "Full"}:
            try:
                for entry in _V58._load_palette_file(palette_path, limit_two=False):
                    if str(entry.get("token", "")).strip().lower() == tok:
                        return entry
            except Exception:
                pass
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
        _V58.CALIBRATED_CMY_NORMAL_NAME: os.path.join(
            _V58.PALETTES_DIR,
            _V58.CALIBRATED_CMY_NORMAL_FILENAME,
        ),
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
