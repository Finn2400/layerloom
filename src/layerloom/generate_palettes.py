#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_palettes.py — Precompute CMY/K/W palette subsets for LayerLoom GUI

Creates JSON summaries and optional PDF previews for:
  • simple_palette.json
  • normal_palette.json
  • full_palette.json

Notes
-----
- This version accepts tokens that include K (black) and W (white) in addition
  to C/M/Y. Anything outside the {c,m,y,k,w} alphabet is ignored.
- If your visualizer supports an --alphabet flag, we pass "cmykw". If not,
  we fall back to the original arguments without failing the whole run.
"""

import json, subprocess, sys
from pathlib import Path
from typing import Dict, Any, Iterable

import layer_perm_cmy_visualizer as cmy_vis
from palette_utils import (
    PALETTE_PRESETS,
    corrected_hex,
    load_registry,
    token_is_supported,
)

def extract_palette_subset(registry: Dict[str, Any], max_run: int, filter_lte: int):
    out = []
    for h_s, levels in registry.items():
        try:
            h = int(h_s)
        except Exception:
            continue
        if h > 6:
            continue

        for r_s, meta in levels.items():
            try:
                r = int(r_s)
            except Exception:
                continue
            if r > max_run:
                continue

            colors: Iterable[Dict[str, Any]] = meta.get("colors", [])
            for entry in colors:
                tok = (entry.get("token") or "").lower()
                if not token_is_supported(tok):
                    continue
                if int(entry.get("distinct_colors", 0)) <= filter_lte:
                    out.append({
                        "token": tok,
                        "hex": corrected_hex(tok),
                        "distinct_colors": int(entry.get("distinct_colors", 0)),
                        "fractions": entry.get("fractions", []),
                    })
    return out


def build_exact_cmy_entries(max_height: int, max_run: int, distinct_lte: int):
    entries = []
    for stack in cmy_vis.build_palette_stacks(max_height, max_run, distinct_lte):
        token = "".join(stack).lower()
        entries.append({
            "token": token,
            "hex": corrected_hex(token),
            "distinct_colors": len(set(token)),
            "fractions": {
                "C": stack.count("C") / len(stack),
                "M": stack.count("M") / len(stack),
                "Y": stack.count("Y") / len(stack),
                "K": 0.0,
                "W": 0.0,
            },
        })
    return entries


def merge_exact_cmy_entries(entries, *, max_height: int, max_run: int, distinct_lte: int):
    merged = list(entries)
    seen_hex = {entry["hex"].lower() for entry in merged}
    for entry in build_exact_cmy_entries(max_height, max_run, distinct_lte):
        hx = entry["hex"].lower()
        if hx in seen_hex:
            continue
        merged.append(entry)
        seen_hex.add(hx)
    return merged

def _try_run_visualizer(cmd_base, pdf_out: Path):
    try:
        proc = subprocess.run(cmd_base, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"[preview] Skipped PDF (visualizer failed): {pdf_out.name}\n{proc.stderr}", file=sys.stderr)
    except FileNotFoundError:
        print(f"[preview] Visualizer not found; skipped PDF: {pdf_out.name}", file=sys.stderr)

def main():
    here = Path(__file__).resolve().parent
    reg = load_registry(here / "layer_perm_registry.json")
    out_dir = here / "palettes"
    out_dir.mkdir(exist_ok=True)

    visualizer = here / "layer_perm_cmy_visualizer.py"

    for name, spec in PALETTE_PRESETS.items():
        subset = extract_palette_subset(
            reg, max_run=spec["max_run"], filter_lte=spec["distinct_lte"]
        )
        subset = merge_exact_cmy_entries(
            subset,
            max_height=spec["max_height"],
            max_run=spec["max_run"],
            distinct_lte=spec["distinct_lte"],
        )
        out_json = out_dir / f"{name}_palette.json"
        data = {
            "name": spec["name"],
            "description": spec["description"],
            "entries": subset
        }
        with open(out_json, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[✓] Wrote {out_json} ({len(subset)} entries)")

        pdf_out = out_dir / f"{name}_palette.pdf"
        if visualizer.exists():
            cmd = [
                sys.executable, str(visualizer),
                "--filter-lte", str(spec["distinct_lte"]),
                "--max-height", str(spec["max_height"]),
                "--max-run", str(spec["max_run"]),
                "--pdf-out", str(pdf_out),
            ]
            _try_run_visualizer(cmd, pdf_out)
        else:
            print(f"[preview] No visualizer found; skipped PDF: {pdf_out.name}")

if __name__ == "__main__":
    main()
