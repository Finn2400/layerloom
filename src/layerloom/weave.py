#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
layerloom.weave
---------------

High-level pipeline for LayerLoom.

This script performs the following steps:
  1. Detects whether the input 3MF file is baked.
  2. If unbaked, runs the internal baking routine automatically.
  3. Displays a weaving quote and begins the slicing process.
  4. Slices each baked part into Z-bands according to its LayerLoom pattern.
  5. Groups and merges all slices by color.
  6. Writes a grouped temporary 3MF, bakes it, and exports a final woven 3MF.

Robust mapping:
  • Captures PAT tokens from the *original* input XML (before baking) and
    uses that as a fallback if the bake step drops labels/partnumbers.
  • Accepts LayerLoom tokens; sanitizes to the supported token alphabet.

Defaults
--------
• step = 0.2 mm
• output = <input_basename>_woven.3mf
• stl_out = ./stl_out/

Example
-------
    python -m layerloom.weave -i input.3mf -v
"""

from __future__ import annotations
import os
import re
import sys
import time
import argparse
import tempfile
import numpy as np
import subprocess
import random
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, Tuple, List
from collections import Counter

from layerloom.ingest import read_parts
from layerloom.normalize_3mf_import import normalize_3mf_import
from layerloom.strata import slice_repeating
from layerloom.group_by_color import group_by_color, write_color_stls
from layerloom.export import write_basic_3mf
from layerloom.tokens import PAT_TAG_RE, TOKEN_ALPHABET, sanitize_token_run

# ---------------------------------------------------------------------
# Literary intro
# ---------------------------------------------------------------------

WEAVE_QUOTES = [
    ("“Mightily wove they the web of fate, While Bralund's towns were trembling all;\n"
     "\tAnd there the golden threads they wove, \n\t\tAnd in the moon's hall fast they made them.”\n"
     "\n\t\t\t— Description of the Norns in Helgakviða Hundingsbana"),
    ("“A good man will welcome every experience the looms of fate may weave for him.”\n\t\t\t\t\t — Marcus Aurelius"),
    ("“We may say most aptly that the Analytical Engine weaves algebraical patterns\n "
     "just as the Jacquard loom weaves flowers and leaves.”\n\t\t\t\t\t — Ada Lovelace"),
    ("“An ancient metaphor: thought is a thread, and the raconteur is a\n spinner of yarns – "
     "but the true storyteller, the poet, is a weaver.” \n\t\t\t\t\t— Robert Bringhurst"),
    ("“We gave the Future to the winds, and slumbered tranquilly in the Present, \n"
     "\tweaving the dull world around us into dreams.” \n\t\t\t\t\t— Edgar Allan Poe"),
]


def print_intro() -> None:
    """Display a random weaving quote and section header."""
    quote = random.choice(WEAVE_QUOTES)
    width = 82
    print("\n" + "=" * width)
    for line in quote.splitlines():
        if line.strip():
            print("   " + line)
        else:
            print()
    print("-" * width)
    print("Weaving Layers...".center(width))
    print("=" * width + "\n")


def _section(title: str) -> None:
    """Visually separate major stages."""
    print(f"\n—— {title} ——\n")


# ---------------------------------------------------------------------
# 3MF XML helpers for robust token mapping (pre-bake)
# ---------------------------------------------------------------------

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
NS = {"m": CORE_NS}
M = lambda t: f"{{{CORE_NS}}}{t}"

PAT_RE = PAT_TAG_RE
ALPHABET = set(TOKEN_ALPHABET)

def _find_model_xml_name(zf: zipfile.ZipFile) -> str | None:
    for n in zf.namelist():
        if n.startswith("3D/") and n.lower().endswith(".model"):
            return n
    return None

def _read_model_root(path: str) -> ET.Element | None:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            model_name = _find_model_xml_name(zf)
            if not model_name:
                return None
            return ET.fromstring(zf.read(model_name))
    except Exception:
        return None

def _oid_map(root: ET.Element) -> dict[str, ET.Element]:
    res = root.find("m:resources", NS)
    return {} if res is None else {o.get("id", ""): o for o in res.findall("m:object", NS)}

def _effective_label_for_oid(root: ET.Element, obj_el: ET.Element) -> str:
    """Prefer build<item@partnumber>, else object@name, else metadata Title/Name."""
    oid = obj_el.get("id")
    build = root.find("m:build", NS)
    if build is not None:
        for it in build.findall("m:item", NS):
            if it.get("objectid") == oid:
                pn = it.get("partnumber")
                if pn:
                    return pn
    name = obj_el.get("name")
    if name:
        return name
    for key in ("Title", "Name"):
        md = obj_el.find(f"m:metadata[@name='{key}']", NS)
        if md is not None and md.text:
            return md.text
    return ""

def _is_component_container(obj_el: ET.Element) -> bool:
    return obj_el.find("m:components", NS) is not None

def _component_children_ids(obj_el: ET.Element) -> list[str]:
    comps = obj_el.find("m:components", NS)
    if comps is None:
        return []
    out = []
    for c in comps.findall("m:component", NS):
        rid = c.get("objectid")
        if rid:
            out.append(rid)
    return out

def _strip_pat(s: str) -> str:
    return PAT_RE.sub("", s or "")

def _strip_e_suffix(s: str) -> str:
    return re.sub(r"(__E\d+)$", "", s or "")

def _extract_token_from_label(label: str) -> str | None:
    if not label:
        return None
    m = PAT_RE.search(label)
    return m.group(1).lower() if m else None

def build_name_token_map(three_mf_path: str) -> Dict[str, Tuple[str, str]]:
    """
    Build {effective_label -> (token, source)} from 3MF model XML.
    Propagate container tokens to components.
    Source is 'build.partnumber', 'object.name', 'metadata', etc.
    """
    root = _read_model_root(three_mf_path)
    if root is None:
        return {}

    id2obj = _oid_map(root)
    tok_by_id: dict[str, Tuple[str, str]] = {}

    # direct tokens (from effective label)
    for oid, o in id2obj.items():
        label = _effective_label_for_oid(root, o)
        tok = _extract_token_from_label(label)
        if tok:
            src = "effective"
            # refine source for debugging
            if root.find("m:build", NS) is not None:
                for it in root.find("m:build", NS).findall("m:item", NS):
                    if it.get("objectid") == oid and it.get("partnumber") and PAT_RE.search(it.get("partnumber", "")):
                        src = "build.partnumber"
                        break
            if src == "effective" and o.get("name") and PAT_RE.search(o.get("name")):
                src = "object.name"
            if src == "effective":
                for key in ("Title", "Name"):
                    md = o.find(f"m:metadata[@name='{key}']", NS)
                    if md is not None and md.text and PAT_RE.search(md.text):
                        src = f"metadata.{key}"
                        break
            tok_by_id[oid] = (tok, src)

    # propagate from container to children
    changed = True
    while changed:
        changed = False
        for oid, o in id2obj.items():
            if not _is_component_container(o):
                continue
            if oid not in tok_by_id:
                t = _extract_token_from_label(_effective_label_for_oid(root, o))
                if t:
                    tok_by_id[oid] = (t, "container.effective")
            if oid not in tok_by_id:
                continue
            t_src = tok_by_id[oid]
            for child in _component_children_ids(o):
                if child not in tok_by_id:
                    tok_by_id[child] = t_src
                    changed = True

    # convert to name->(token,source) with normalized variants
    name_to_token: dict[str, Tuple[str, str]] = {}
    for oid, o in id2obj.items():
        label = _effective_label_for_oid(root, o)
        if not label:
            continue
        pair = tok_by_id.get(oid)
        if not pair:
            t = _extract_token_from_label(label)
            if t:
                pair = (t, "effective")
        if pair:
            tok, src = pair
            for key in {label, _strip_pat(label), _strip_e_suffix(label), _strip_e_suffix(_strip_pat(label))}:
                name_to_token[key] = (tok, src)
    return name_to_token

def resolve_run_from_map(mesh_label: str, name_token_map: Dict[str, Tuple[str, str]]) -> Tuple[str, str] or None:
    """Try to resolve a token for a given mesh label using the pre-bake map."""
    for key in (mesh_label, _strip_pat(mesh_label), _strip_e_suffix(mesh_label), _strip_e_suffix(_strip_pat(mesh_label))):
        if key in name_token_map:
            tok, src = name_token_map[key]
            return tok, f"map:{src}"
    return None

def sanitize(run: str) -> str:
    return sanitize_token_run(run, alphabet=ALPHABET, default="cmy")


# ---------------------------------------------------------------------
# Baking helper
# ---------------------------------------------------------------------

def _auto_bake_if_needed(in_path: str, verbose: bool = True) -> str:
    """
    Normalize any incoming 3MF into a generic LayerLoom-editable 3MF.
    This accepts vendor-saved packages and also acts as a no-op for already
    normalized inputs.
    """
    result = normalize_3mf_import(in_path)
    if verbose:
        if result.normalized_path == os.path.abspath(in_path):
            print(f"[import] using already-normalized 3MF → {os.path.basename(in_path)}")
        else:
            print(f"[import] normalized 3MF → {result.normalized_path}")
        for warning in result.warnings[:8]:
            print(f"[import warn] {warning}")
        if len(result.warnings) > 8:
            print(f"[import warn] … {len(result.warnings) - 8} additional warning(s)")
    return result.normalized_path


# ---------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------

def weave_pipeline(
    input_path: str,
    step: float = 0.2,
    stl_out: str | None = None,
    verbose: bool = True,
    output_path: str | None = None,
) -> None:
    """Execute the full LayerLoom color grouping workflow."""
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    cwd = os.getcwd()
    stl_out = stl_out or os.path.join(cwd, "stl_out")
    t_start = time.time()

    # 1. Display weaving intro (always shown)
    print_intro()

    # 2. Build a name→token map from the *original* file BEFORE baking.
    _section("Scanning input for PAT tokens")
    pre_map = build_name_token_map(input_path)
    print(f"[map] pre-bake XML-derived token entries: {len(pre_map)}")
    if verbose and pre_map:
        shown = 0
        for k, (tok, src) in pre_map.items():
            print(f"      {k}  →  {tok}   [{src}]")
            shown += 1
            if shown >= 6:
                break

    # 3. Bake if necessary
    baked_path = _auto_bake_if_needed(input_path, verbose=verbose)

    # 4. Load baked parts
    _section("Loading geometry")
    t0 = time.time()
    parts = read_parts([baked_path])
    print(f"[load] read {len(parts)} part(s) from {os.path.basename(baked_path)}")
    print(f"[timing] geometry loaded in {time.time() - t0:.2f}s")

    # 5. Compute Z baseline
    z0 = float(min(m.bounds[0, 2] for _, m in parts))
    print(f"[info] using z0 = {z0:.3f} mm")

    # 6. Slice by LayerLoom token sequence
    _section("Slicing and layering")
    print(f"[slice] Cutting parts into color-patterned Z-bands (step={step} mm)...")
    sliced = []
    t1 = time.time()
    total_parts = len(parts)

    # Diagnostics
    if verbose:
        print("[slice] token resolution per part:")

    for i, (lbl, mesh) in enumerate(parts, 1):
        # Priority: PAT in baked label → pre-bake map → default 'cmy'
        m = PAT_RE.search(lbl)
        if m:
            run = sanitize(m.group(1))
            src = "label.PAT"
        else:
            resolved = resolve_run_from_map(lbl, pre_map)
            if resolved:
                run, src = sanitize(resolved[0]), resolved[1]
            else:
                run, src = "cmy", "default.cmy"

        if verbose:
            print(f"   {lbl}  →  {run}   [{src}]")

        bands = slice_repeating(mesh, z0=z0, step=step, groups=len(run))
        for g, group in enumerate(bands):
            color = run[g]  # single character
            for bi, b in enumerate(group):
                sliced.append((f"{lbl}__slice{bi:03d}__PAT_{color}__", b))
        if verbose and (i % 5 == 0 or i == total_parts):
            print(f"[slice] processed {i}/{total_parts} parts ({len(sliced)} bands so far)")

    print(f"[slice] total sliced parts: {len(sliced)} (elapsed {time.time() - t1:.1f}s)")

    # 7. Group and merge by color
    _section("Grouping by color")
    print("[group] Gathering color threads into unified solids...")
    grouped = group_by_color(sliced, merge=True, verbose=verbose)

    # 8. Write grouped 3MF (temporary)
    _section("Exporting grouped 3MF")
    tmp_dir = tempfile.gettempdir()
    grouped_tmp = os.path.join(tmp_dir, f"grouped_{os.path.basename(input_path)}")
    write_basic_3mf(grouped, grouped_tmp, title="LayerLoom Grouped Output")
    print(f"[export] grouped file written → {grouped_tmp}")

    # 9. Final bake → woven output
    _section("Final baking")

    if output_path:
        woven_path = output_path
    else:
        woven_name = os.path.splitext(os.path.basename(input_path))[0] + "_woven.3mf"
        woven_path = os.path.join(os.path.dirname(input_path), woven_name)

    cmd = [
        sys.executable,
        "-m", "layerloom.bake_in_memory_stlroundtrip",
        "-i", grouped_tmp,
        "-o", woven_path,
        "--safe-volumes",
        "--keep-stls",
    ]
    print("[bake] running final bake for woven output...")
    subprocess.run(cmd, check=True)
    print(f"[bake] final woven file → {woven_path}")

    # 10. Write per-color STL files
    _section("Exporting color STLs")
    os.makedirs(stl_out, exist_ok=True)
    write_color_stls(grouped, stl_out)
    print(f"[export] wrote STL group → {stl_out}")

    print(f"\n[done] Weave complete — threads bound, model ready → {woven_path} "
          f"({time.time() - t_start:.1f}s)\n")


# ---------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="layerloom.weave",
        description="Run the full LayerLoom color-layer workflow (auto-bakes if needed)."
    )
    parser.add_argument("-i", "--input", required=True, help="Input .3mf file")
    parser.add_argument("--step", type=float, default=0.2,
                        help="Z-step (layer thickness in mm, default=0.2)")
    parser.add_argument("--stl-out", type=str,
                        help="Optional output folder for per-color STL export (default: ./stl_out/)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable verbose logging")
    parser.add_argument("-o", "--output", help="Output 3MF path "
                                               "(default: <input_basename>_woven.3mf)")

    args = parser.parse_args(argv)

    try:
        weave_pipeline(
            input_path=os.path.abspath(args.input),
            step=args.step,
            stl_out=os.path.abspath(args.stl_out) if args.stl_out else None,
            verbose=args.verbose,
            output_path=os.path.abspath(args.output) if args.output else None,
        )
        return 0
    except Exception as e:
        print(f"[error] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
