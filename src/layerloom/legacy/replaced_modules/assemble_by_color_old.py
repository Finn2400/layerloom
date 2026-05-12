#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
layerloom.assemble_by_color
---------------------------

Default pipeline:
  1. Load a baked .3mf via `read_parts()`
  2. Slice each part into repeating CMY Z-bands (`slice_repeating`)
  3. Tag each band with __PAT_c__, __PAT_m__, or __PAT_y__
  4. Merge all bands of the same color across all parts
  5. Export combined per-color objects to 3MF and/or STL

Usage (CLI)
-----------
    python -m layerloom.assemble_by_color -i input.3mf -o output.3mf --step 0.5
    python -m layerloom.assemble_by_color -i input.3mf -o output.3mf --step 0.5 --stl-out stl_dir

Example
-------
    # Slice into 0.5 mm bands, merge per-color, and export STL & 3MF
    python -m layerloom.assemble_by_color \
        -i exploded_with_tokens5.3mf \
        -o grouped_output.3mf \
        --step 0.5 -v

Dependencies
------------
trimesh, numpy, re, argparse
"""

from __future__ import annotations
import argparse
import sys
import os
import re
import numpy as np

from layerloom.ingest import read_parts, _3mf_needs_bake
from layerloom.strata import slice_repeating
from layerloom.group_by_color import group_by_color
from layerloom.export import write_basic_3mf, write_color_stls


# ----------------------------------------------------------------------
# Core pipeline
# ----------------------------------------------------------------------
def assemble_by_color(
    input_path: str,
    output_path: str,
    step: float,
    stl_out: str | None = None,
    merge: bool = True,
    verbose: bool = False
) -> None:
    """
    Read a baked 3MF, slice into CMY bands, merge per color, and export.
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if _3mf_needs_bake(input_path):
        raise ValueError(f"{input_path} appears unbaked (has transforms/components).")

    # --------------------------------------------------------------
    # Step 1: load baked parts
    # --------------------------------------------------------------
    parts = read_parts([input_path])
    if verbose:
        print(f"[load] read {len(parts)} part(s) from {os.path.basename(input_path)}")

    if not parts:
        raise ValueError("No parts loaded from input file")

    # --------------------------------------------------------------
    # Step 2: determine global z0 baseline
    # --------------------------------------------------------------
    zmins = [mesh.bounds[0, 2] for _, mesh in parts if hasattr(mesh, "bounds")]
    z0_global = float(np.min(zmins)) if zmins else 0.0
    if verbose:
        print(f"[info] using z0 = {z0_global:.3f} mm")

    # --------------------------------------------------------------
    # Step 3: slice each part according to its CMY pattern
    # --------------------------------------------------------------
    PAT_RE = re.compile(r"__PAT_([cmy]+)__", re.IGNORECASE)
    sliced_parts = []
    for label, mesh in parts:
        run = (PAT_RE.search(label).group(1).lower() if PAT_RE.search(label) else "c")
        bands = slice_repeating(mesh, z0=z0_global, step=step, groups=len(run))
        for g, group in enumerate(bands):
            color = run[g]
            for bi, b in enumerate(group):
                new_label = f"{label}__slice{bi:03d}__PAT_{color}__"
                sliced_parts.append((new_label, b))
        if verbose:
            print(f"[slice] {label}: {sum(len(g) for g in bands)} bands ({run.upper()})")

    if verbose:
        print(f"[slice] total sliced parts: {len(sliced_parts)}")

    # --------------------------------------------------------------
    # Step 4: merge sliced bands by their color token
    # --------------------------------------------------------------
    grouped = group_by_color(sliced_parts, merge=merge, verbose=verbose)
    if not grouped:
        raise RuntimeError("No merged groups produced")

    if verbose:
        print(f"[group] merged {len(grouped)} color solids")

    # --------------------------------------------------------------
    # Step 5: write outputs (3MF + optional STLs)
    # --------------------------------------------------------------
    write_basic_3mf(
        items=grouped,
        out_path=output_path,
        title="LayerLoom grouped output"
    )
    if verbose:
        print(f"[export] wrote grouped 3MF → {output_path}")

    if stl_out:
        write_color_stls(grouped, stl_out, verbose=verbose)

    print(f"[done] wrote grouped file → {output_path}")


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="layerloom.assemble_by_color",
        description="Slice and group baked 3MF parts by CMY color bands."
    )
    parser.add_argument("-i", "--input", required=True, help="Input baked .3mf file")
    parser.add_argument("-o", "--output", required=True, help="Output grouped .3mf file")
    parser.add_argument("--step", type=float, required=True,
                        help="Z-step thickness in mm (e.g., 0.5)")
    parser.add_argument("--stl-out", type=str, default=None,
                        help="Optional directory to export per-color STLs")
    parser.add_argument("--no-merge", action="store_true",
                        help="Do not merge color bands (keep per-layer output)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose output")

    args = parser.parse_args(argv)

    try:
        assemble_by_color(
            input_path=os.path.abspath(args.input),
            output_path=os.path.abspath(args.output),
            step=args.step,
            stl_out=os.path.abspath(args.stl_out) if args.stl_out else None,
            merge=not args.no_merge,
            verbose=args.verbose,
        )
        return 0
    except Exception as e:
        print(f"[error] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

