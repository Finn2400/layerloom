"""Small stable command-line wrappers for LayerLoom."""

from __future__ import annotations

import argparse
import os
import shutil
import sys

from layerloom.normalize_3mf_import import normalize_3mf_import


def normalize_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="layerloom-normalize",
        description="Normalize a generic or vendor 3MF into a LayerLoom-ready generic 3MF.",
    )
    parser.add_argument("input", help="Input .3mf file")
    parser.add_argument("-o", "--output", help="Optional output .3mf path")
    parser.add_argument("--print-parts", action="store_true", help="Print normalized part labels")
    args = parser.parse_args(argv)

    try:
        result = normalize_3mf_import(os.path.abspath(args.input))
        output_path = result.normalized_path
        if args.output:
            output_path = os.path.abspath(args.output)
            shutil.copyfile(result.normalized_path, output_path)
        print(output_path)
        for warning in result.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        if args.print_parts:
            for part in result.parts:
                print(part.label)
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(normalize_main())
