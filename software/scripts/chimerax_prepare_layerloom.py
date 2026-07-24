#!/usr/bin/env python3
"""Prepare a protein in ChimeraX for LayerLoom GLB import.

Run from ChimeraX, for example:

    /Applications/ChimeraX-1.10.1.app/Contents/MacOS/ChimeraX --nogui --exit \
      --script "/path/to/chimerax_prepare_layerloom.py 1ubq"

ChimeraX treats extra words after --script as files unless the script path and
arguments are passed as one quoted value.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


DEFAULT_OUTPUT_DIR = Path.home() / "Downloads"


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _slug(value: str) -> str:
    value = value.strip().replace(":", "_")
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("._")
    return value or "protein"


def inferred_label(input_spec: str) -> str:
    expanded = Path(input_spec).expanduser()
    if expanded.exists():
        return _slug(expanded.stem)
    return _slug(input_spec)


def default_output_path(input_spec: str, output_dir: Path, color_mode: str) -> Path:
    label = inferred_label(input_spec)
    mode = color_mode.replace("-", "_")
    return output_dir.expanduser() / f"{label}_layerloom_{mode}_ribbon_struts.glb"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Open a local protein file or PDB ID in ChimeraX, make a robust "
            "printable ribbon with half-colored struts, and save a GLB."
        )
    )
    parser.add_argument(
        "input",
        help="Local PDB/mmCIF path or an identifier ChimeraX can open, e.g. 1ubq.",
    )
    parser.add_argument("-o", "--output", type=Path, help="Output .glb path.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for default output path. Defaults to ~/Downloads.",
    )
    parser.add_argument(
        "--color-mode",
        choices=("rainbow-residue", "rainbow-chain", "by-chain"),
        default="rainbow-residue",
        help="Protein coloring mode. Defaults to rainbow-residue.",
    )
    parser.add_argument(
        "--ribbon-width",
        type=_positive_float,
        default=3.6,
        help="Protein cartoon ribbon width in Angstroms. Defaults to 3.6.",
    )
    parser.add_argument(
        "--ribbon-thickness",
        type=_positive_float,
        default=2.0,
        help="Protein cartoon ribbon thickness in Angstroms. Defaults to 2.0.",
    )
    parser.add_argument(
        "--ribbon-sides",
        type=_positive_int,
        default=24,
        help="Cartoon cross-section sides. Defaults to 24.",
    )
    parser.add_argument(
        "--strut-atoms",
        default="@ca",
        help="Atom spec used as strut endpoints. Defaults to @ca.",
    )
    parser.add_argument(
        "--strut-length",
        type=_positive_float,
        default=7.0,
        help="Maximum strut length in Angstroms. Defaults to 7.0.",
    )
    parser.add_argument(
        "--strut-loop",
        type=_positive_float,
        default=30.0,
        help="Minimum through-bond loop length for adding struts. Defaults to 30.0.",
    )
    parser.add_argument(
        "--strut-radius",
        type=_positive_float,
        default=0.8,
        help="Strut cylinder radius in Angstroms. Defaults to 0.8.",
    )
    parser.add_argument(
        "--no-struts",
        action="store_true",
        help="Skip ChimeraX strut generation.",
    )
    parser.add_argument(
        "--no-halfbond",
        action="store_true",
        help="Do not split strut colors by endpoint atom colors.",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="Reopen the exported GLB in the same ChimeraX session after saving.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite an existing output file.",
    )
    return parser


def _command_path(input_spec: str) -> str:
    from chimerax.core.commands import quote_if_necessary

    expanded = Path(input_spec).expanduser()
    if expanded.exists():
        return quote_if_necessary(str(expanded))
    return quote_if_necessary(input_spec)


def _save_path(path: Path) -> str:
    from chimerax.core.commands import quote_if_necessary

    return quote_if_necessary(str(path.expanduser()))


def _run(session, command: str):
    from chimerax.core.commands import run

    session.logger.info(f"LayerLoom prep: {command}")
    return run(session, command)


def _apply_color(session, color_mode: str) -> None:
    if color_mode == "rainbow-residue":
        _run(session, "rainbow residues target ac")
    elif color_mode == "rainbow-chain":
        _run(session, "rainbow chains target ac")
    elif color_mode == "by-chain":
        _run(session, "color bychain target ac")
    else:
        raise ValueError(f"Unsupported color mode: {color_mode}")


def _style_ribbon(session, args: argparse.Namespace) -> None:
    _run(
        session,
        "cartoon style protein "
        f"width {args.ribbon_width:g} "
        f"thickness {args.ribbon_thickness:g} "
        f"xsection oval sides {args.ribbon_sides:d} divisions 20 arrows false",
    )


def _solidify_struts(strut_group, radius: float, halfbond: bool) -> int:
    if strut_group is None:
        return 0
    if hasattr(strut_group, "halfbond"):
        strut_group.halfbond = bool(halfbond)
    if hasattr(strut_group, "dashes"):
        strut_group.dashes = 0
    count = 0
    for pseudobond in getattr(strut_group, "pseudobonds", []):
        pseudobond.radius = radius
        pseudobond.halfbond = bool(halfbond)
        count += 1
    return count


def run_layerloom_prep(session, args: argparse.Namespace) -> Path:
    output = args.output.expanduser() if args.output else default_output_path(args.input, args.output_dir, args.color_mode)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not args.overwrite:
        raise SystemExit(f"Output already exists, use --overwrite: {output}")

    _run(session, f"open {_command_path(args.input)}")
    _run(session, "cartoon")
    _apply_color(session, args.color_mode)
    _style_ribbon(session, args)

    strut_count = 0
    if not args.no_struts:
        strut_group = _run(
            session,
            "struts "
            f"{args.strut_atoms} "
            f"length {args.strut_length:g} "
            f"loop {args.strut_loop:g} "
            f"radius {args.strut_radius:g} "
            "fattenRibbon true "
            "replace true "
            "name layerloom_struts",
        )
        _style_ribbon(session, args)
        strut_count = _solidify_struts(strut_group, args.strut_radius, not args.no_halfbond)

    # Struts intentionally turn endpoint atoms back on. Hide atoms and native
    # bonds after strut generation so the GLB contains the printable ribbon and
    # struts, not extra CA spheres.
    _run(session, "hide atoms")
    _run(session, "hide bonds")

    _run(
        session,
        f"save {_save_path(output)} "
        "format gltf "
        "center true "
        "centerEachNode false "
        "pruneVertexColors false "
        "backfaceCulling false",
    )
    session.logger.info(f"LayerLoom prep saved {output} with {strut_count} struts")

    if args.inspect:
        _run(session, "close all")
        _run(session, f"open {_save_path(output)}")

    return output


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    chimerax_session = globals().get("session")
    if chimerax_session is None:
        parser.error("This script must be run inside ChimeraX with --script.")
    run_layerloom_prep(chimerax_session, args)
    return 0


if __name__ == "__main__" or "session" in globals():
    raise SystemExit(main(sys.argv[1:]))
