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
from collections import Counter
from functools import lru_cache
from itertools import product

try:
    import numpy as np
except ImportError:
    np = None  # NumPy optional

try:
    from layerloom.tokens import (
        ALL_TOKENS,
        TOKEN_HEX,
        token_is_valid,
    )
except Exception:
    from tokens import (
        ALL_TOKENS,
        TOKEN_HEX,
        token_is_valid,
    )


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
CUSTOM_BASE_RGB = dict(TOKEN_HEX)

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


def token_is_supported(token: str, alphabet: str | None = None) -> bool:
    return token_is_valid(token, alphabet or "".join(ALL_TOKENS))


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


def _reduced_fraction_signature(token: str) -> tuple[tuple[str, int], ...]:
    counts = Counter(token)
    total = sum(counts.values())
    if total <= 0:
        return tuple()
    # Collapse orderings and reducible repeats, e.g. cmy, ccmm yy, and ymccmy.
    import math
    divisor = total
    for value in counts.values():
        divisor = math.gcd(divisor, value)
    return tuple(sorted((letter, value // divisor) for letter, value in counts.items()))


def _run_preference_key(token: str) -> tuple[int, int, int, int, int, str]:
    if not token:
        return (999, 999, 999, 0, 0, "")
    runs = []
    cur = token[0]
    n = 1
    for ch in token[1:]:
        if ch == cur:
            n += 1
        else:
            runs.append(n)
            cur = ch
            n = 1
    runs.append(n)
    max_run = max(runs)
    over2 = sum(max(0, run - 2) for run in runs)
    pairs = sum(1 for run in runs if run == 2)
    return (max_run, len(token), over2, -pairs, -len(runs), token)


def _token_fractions(token: str, alphabet: tuple[str, ...]) -> dict[str, float]:
    counts = Counter(token)
    n = max(len(token), 1)
    return {letter.upper(): counts.get(letter, 0) / n for letter in alphabet}


@lru_cache(maxsize=128)
def build_layer_fraction_palette(
    alphabet: tuple[str, ...] | str = ("c", "m", "y"),
    max_height: int = DEFAULT_MAX_HEIGHT,
    max_run: int = 3,
    distinct_lte: int = 3,
) -> tuple[dict, ...]:
    """
    Enumerate unique layer-fraction recipes for an arbitrary token alphabet.

    Different orderings and reducible repeats with the same layer fractions are
    collapsed to one representative token, preferring balanced runs and then
    the shortest reduced token. This is the same recipe-counting convention
    used for the manuscript design-space table.
    """
    if isinstance(alphabet, str):
        letters = tuple(ch.lower() for ch in alphabet)
    else:
        letters = tuple(str(ch).lower() for ch in alphabet)
    letters = tuple(dict.fromkeys(ch for ch in letters if token_is_supported(ch)))
    if not letters:
        return tuple()
    max_height = int(max(1, max_height))
    max_run = int(max(1, max_run))
    distinct_lte = int(max(1, distinct_lte))

    best_by_fraction: dict[tuple[tuple[str, int], ...], str] = {}
    for height in range(1, max_height + 1):
        for seq in product(letters, repeat=height):
            token = "".join(seq)
            if len(set(token)) > distinct_lte:
                continue
            if _run_preference_key(token)[0] > max_run:
                continue
            sig = _reduced_fraction_signature(token)
            prev = best_by_fraction.get(sig)
            if prev is None or _run_preference_key(token) < _run_preference_key(prev):
                best_by_fraction[sig] = token

    entries = []
    for token in best_by_fraction.values():
        entries.append({
            "token": token,
            "hex": corrected_hex(token),
            "distinct_colors": len(set(token)),
            "fractions": _token_fractions(token, letters),
        })
    return tuple(sorted(entries, key=lambda entry: entry["hex"]))

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
