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
  4. Slices each baked part into Z-bands according to its CMY pattern.
  5. Groups and merges all slices by color (cyan, magenta, yellow).
  6. Writes a grouped temporary 3MF, bakes it, and exports a final woven 3MF.

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

from layerloom.ingest import read_parts, _3mf_needs_bake
from layerloom.strata import slice_repeating
from layerloom.group_by_color import group_by_color, write_color_stls
from layerloom.export import write_basic_3mf

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
# Baking helper
# ---------------------------------------------------------------------

def _auto_bake_if_needed(in_path: str, verbose: bool = True) -> str:
    """Detect whether a 3MF file is unbaked and, if needed, bake it automatically."""
    if not _3mf_needs_bake(in_path):
        if verbose:
            print(f"[check] file already baked → {os.path.basename(in_path)}")
        return in_path

    tmp_dir = tempfile.gettempdir()
    baked_path = os.path.join(tmp_dir, f"baked_{os.path.basename(in_path)}")

    cmd = [
        sys.executable,
        "-m", "layerloom.bake_in_memory_stlroundtrip",
        "-i", in_path,
        "-o", baked_path,
        "--safe-volumes",
        "--keep-stls",
    ]

    if verbose:
        print(f"[bake] input appears unbaked → baking now...")
        print("       ", " ".join(cmd))

    try:
        subprocess.run(cmd, check=True)
        if verbose:
            print(f"[bake] done → {baked_path}")
        return baked_path
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Baking failed for {in_path}: {e}")


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

    # 2. Bake if necessary
    baked_path = _auto_bake_if_needed(input_path, verbose=verbose)

    # 3. Load baked parts
    _section("Loading geometry")
    t0 = time.time()
    parts = read_parts([baked_path])
    print(f"[load] read {len(parts)} part(s) from {os.path.basename(baked_path)}")
    print(f"[timing] geometry loaded in {time.time() - t0:.2f}s")

    # 4. Compute Z baseline
    z0 = float(min(m.bounds[0, 2] for _, m in parts))
    print(f"[info] using z0 = {z0:.3f} mm")

    # 5. Slice by CMY token sequence
    _section("Slicing and layering")
    print(f"[slice] Cutting parts into color-patterned Z-bands (step={step} mm)...")
    PAT_RE = re.compile(r"__PAT_([cmy]+)__", re.IGNORECASE)
    sliced = []
    t1 = time.time()
    total_parts = len(parts)

    for i, (lbl, mesh) in enumerate(parts, 1):
        run = (PAT_RE.search(lbl).group(1).lower() if PAT_RE.search(lbl) else "c")
        bands = slice_repeating(mesh, z0=z0, step=step, groups=len(run))
        for g, group in enumerate(bands):
            color = run[g]
            for bi, b in enumerate(group):
                sliced.append((f"{lbl}__slice{bi:03d}__PAT_{color}__", b))
        if verbose and (i % 5 == 0 or i == total_parts):
            print(f"[slice] processed {i}/{total_parts} parts ({len(sliced)} bands so far)")

    print(f"[slice] total sliced parts: {len(sliced)} (elapsed {time.time() - t1:.1f}s)")

    # 6. Group and merge by color
    _section("Grouping by color")
    print("[group] Gathering color threads into unified solids...")
    grouped = group_by_color(sliced, merge=True, verbose=verbose)

    # 7. Write grouped 3MF (temporary)
    _section("Exporting grouped 3MF")
    tmp_dir = tempfile.gettempdir()
    grouped_tmp = os.path.join(tmp_dir, f"grouped_{os.path.basename(input_path)}")
    write_basic_3mf(grouped, grouped_tmp, title="LayerLoom Grouped Output")
    print(f"[export] grouped file written → {grouped_tmp}")

    # 8. Final bake → woven output
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

    # 9. Write per-color STL files
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

