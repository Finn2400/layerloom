#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

"""
palette_utils.py
================

Shared palette/gamut utilities for LayerLoom.

This module now acts as the canonical home for:
  - registry loading
  - shared palette preset definitions
  - token-to-hex conversion used by both palette generation and gamut export
"""

import json
from pathlib import Path

try:
    import numpy as np
except ImportError:
    np = None  # NumPy optional


DEFAULT_MAX_HEIGHT = 6

PALETTE_PRESETS = {
    "simple": {
        "name": "Simple",
        "max_height": DEFAULT_MAX_HEIGHT,
        "max_run": 2,
        "distinct_lte": 2,
        "description": "Up to 6 layers, <=2 repeats per color",
    },
    "normal": {
        "name": "Normal",
        "max_height": DEFAULT_MAX_HEIGHT,
        "max_run": 3,
        "distinct_lte": 3,
        "description": "Up to 6 layers, <=3 repeats per color (recommended <=0.2 mm)",
    },
    "full": {
        "name": "Full",
        "max_height": DEFAULT_MAX_HEIGHT,
        "max_run": 5,
        "distinct_lte": 5,
        "description": "Up to 6 layers, <=5 repeats per color (recommended <=0.12 mm)",
    },
}

# Custom filament base colors used across palette generation and gamut export.
CUSTOM_BASE_RGB = {
    "c": "#3ac8dc",
    "m": "#c31996",
    "y": "#ffdf00",
    "k": "#141414",
    "w": "#f5f5f5",
}

# ──────────────────────────────
# Load and registry access
# ──────────────────────────────
def load_registry(path="layer_perm_registry.json"):
    """Load the full palette registry JSON file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Registry file not found: {path}")
    with open(path, "r") as f:
        data = json.load(f)
    return data["registry"]


def get_preset_spec(name: str):
    key = (name or "").strip().lower()
    if key not in PALETTE_PRESETS:
        choices = ", ".join(sorted(PALETTE_PRESETS))
        raise KeyError(f"Unknown preset '{name}'. Expected one of: {choices}")
    return dict(PALETTE_PRESETS[key])


def resolve_preset_parameters(
    preset: str | None = None,
    *,
    max_height: int | None = None,
    max_run: int | None = None,
    distinct_lte: int | None = None,
):
    spec = get_preset_spec(preset or "normal")
    return {
        "preset": (preset or "normal").lower(),
        "max_height": int(max_height if max_height is not None else spec["max_height"]),
        "max_run": int(max_run if max_run is not None else spec["max_run"]),
        "distinct_lte": int(distinct_lte if distinct_lte is not None else spec["distinct_lte"]),
        "description": spec["description"],
        "name": spec["name"],
    }

def get_palette(registry, height, max_run):
    """Return list of color entries for a given (height, max_run)."""
    return registry[str(height)][str(max_run)]["colors"]

# ──────────────────────────────
# Lookup helpers
# ──────────────────────────────
def index_palette(palette):
    """Return fast lookup dicts (by id, hex, token)."""
    id_map = {c["id"]: c for c in palette}
    hex_map = {c["hex"].lower(): c for c in palette}
    token_map = {c["token"]: c for c in palette}
    return id_map, hex_map, token_map

def get_by_id(palette, idx):
    """Return color entry by numeric ID."""
    id_map, _, _ = index_palette(palette)
    return id_map.get(idx)

def get_by_hex(palette, hex_code):
    """Return color entry by hex code (case-insensitive)."""
    _, hex_map, _ = index_palette(palette)
    return hex_map.get(hex_code.lower())

def get_by_token(palette, token):
    """Return color entry by CMY token string (e.g. 'CMYMY')."""
    _, _, token_map = index_palette(palette)
    return token_map.get(token)


def token_is_supported(token: str, alphabet: str = "cmykw") -> bool:
    letters = set((token or "").lower())
    return bool(letters) and letters.issubset(set(alphabet.lower()))


def corrected_hex(token: str) -> str:
    token = (token or "").lower()
    if not token:
        raise ValueError("token must not be empty")
    rgb = [0, 0, 0]
    for ch in token:
        h = CUSTOM_BASE_RGB.get(ch, "#888888")
        r, g, b = tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))
        rgb[0] += r
        rgb[1] += g
        rgb[2] += b
    n = len(token)
    return "#{:02x}{:02x}{:02x}".format(*(int(c / n) for c in rgb))

# ──────────────────────────────
# Conversion utilities
# ──────────────────────────────
def to_cmy_array(palette):
    """
    Convert list of color entries into an Nx3 NumPy array
    of CMY fractions (if NumPy is available).
    """
    if np is None:
        raise ImportError("NumPy not installed; required for array conversion.")
    return np.array([
        [c["fractions"]["C"], c["fractions"]["M"], c["fractions"]["Y"]]
        for c in palette
    ])

def to_rgb_array(palette):
    """Convert list of color entries into Nx3 NumPy array of RGB values."""
    if np is None:
        raise ImportError("NumPy not installed; required for array conversion.")
    return np.array([c["rgb"] for c in palette])

# ──────────────────────────────
# Example helper: print summary
# ──────────────────────────────
def summarize_palette(palette, limit=10):
    """Print first few colors from a palette."""
    print(f"[Palette summary] Total colors: {len(palette)}")
    for c in palette[:limit]:
        print(f"  {c['id']:>3}: {c['hex']}  {c['token']}  distinct={c['distinct_colors']}")
