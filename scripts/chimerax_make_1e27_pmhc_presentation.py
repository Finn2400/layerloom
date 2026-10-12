#!/usr/bin/env python3
"""Create the focused 1E27 pMHC-I presentation scene for LayerLoom.

The source is HLA-B*5101 (chain A), beta-2-microglobulin (B), and the HIV
peptide LPPVVAKEI (C).  This preset retains A:1-180, the alpha1/alpha2
peptide-binding platform, and C:1-9.  It intentionally removes the alpha3
immunoglobulin domain and beta-2-microglobulin to match a cleft-focused view.

Run it in ChimeraX:

    /Applications/ChimeraX-1.10.1.app/Contents/MacOS/ChimeraX --nogui --exit \
      --script "/path/to/chimerax_make_1e27_pmhc_presentation.py"

Use ``--inspect`` without ``--nogui --exit`` to reopen the exported GLB for a
visual check in the same ChimeraX session.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


DEFAULT_OUTPUT_DIR = Path.home() / "Downloads"
DEFAULT_FILENAME = "1e27_mhci_binding_cleft_presentation.glb"

# Samples were taken from unshaded, midtone regions of the supplied slide.  The
# translucent white overlay on its left is presentation lighting, not a second
# printable material, so it is deliberately not exported.
# Explicit CMY-palette source colors keep the final woven recipes stable:
# HLA uses the simple green CY recipe; the peptide backbone remains pure cyan
# while nitrogen has a restrained, blue-leaning CCCM recipe.
MHC_GREEN = "#9CD36E"
PEPTIDE_CYAN = "#3AC8DC"
NITROGEN_BLUE = "#5C9CCA"
OXYGEN_RED = "#E83020"


def default_output_path(output_dir: Path) -> Path:
    return output_dir.expanduser() / DEFAULT_FILENAME


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export the 1E27 HLA-B*5101 peptide-binding cleft as a colored GLB."
    )
    parser.add_argument("-o", "--output", type=Path, help="Output GLB path.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for the default output. Defaults to ~/Downloads.",
    )
    parser.add_argument(
        "--ribbon-width",
        type=float,
        default=3.6,
        help="MHC ribbon width in Angstroms. Defaults to the LayerLoom protein preset: 3.6.",
    )
    parser.add_argument(
        "--ribbon-thickness",
        type=float,
        default=2.0,
        help="MHC ribbon thickness in Angstroms. Defaults to the LayerLoom protein preset: 2.0.",
    )
    parser.add_argument(
        "--strut-length",
        type=float,
        default=7.0,
        help="Maximum CA-to-CA strut length in Angstroms. Defaults to 7.0.",
    )
    parser.add_argument(
        "--strut-loop",
        type=float,
        default=30.0,
        help="Minimum through-bond loop length for a strut. Defaults to 30.0.",
    )
    parser.add_argument(
        "--strut-radius",
        type=float,
        default=0.8,
        help="Strut cylinder radius in Angstroms. Defaults to the protein preset: 0.8.",
    )
    parser.add_argument(
        "--peptide-atom-radius",
        type=float,
        default=2.0,
        help="Peptide atom radius in Angstroms. Defaults to the reinforced print preset: 2.0.",
    )
    parser.add_argument(
        "--peptide-stick-radius",
        type=float,
        default=1.1,
        help="Peptide bond radius in Angstroms. Defaults to the reinforced print preset: 1.1.",
    )
    parser.add_argument("--mhc-color", default=MHC_GREEN, help="HLA source color as a hex value.")
    parser.add_argument("--peptide-color", default=PEPTIDE_CYAN, help="Peptide backbone source color as a hex value.")
    parser.add_argument("--nitrogen-color", default=NITROGEN_BLUE, help="Peptide nitrogen source color as a hex value.")
    parser.add_argument("--oxygen-color", default=OXYGEN_RED, help="Peptide oxygen source color as a hex value.")
    parser.add_argument("--no-struts", action="store_true", help="Do not add structural CA struts.")
    parser.add_argument(
        "--no-halfbond",
        action="store_true",
        help="Use a single strut color instead of endpoint half-colors.",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="Close the source and reopen the exported GLB for visual inspection.",
    )
    parser.add_argument(
        "--flip-upside-down",
        action="store_true",
        help="Rotate the completed presentation 180 degrees about X before export.",
    )
    parser.add_argument(
        "--x-rotation-degrees",
        type=float,
        default=0.0,
        help="Additional rotation about X applied before export, in degrees.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Overwrite an existing output.")
    return parser


def _run(session, command: str):
    from chimerax.core.commands import run

    session.logger.info(f"1E27 presentation: {command}")
    return run(session, command)


def _quoted_path(path: Path) -> str:
    from chimerax.core.commands import quote_if_necessary

    return quote_if_necessary(str(path.expanduser()))


def _solidify_struts(strut_group, radius: float, halfbond: bool) -> int:
    """Make ChimeraX pseudo-bonds printable and color each half by its endpoint."""
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


def run_presentation_preset(session, args: argparse.Namespace) -> Path:
    output = args.output.expanduser() if args.output else default_output_path(args.output_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not args.overwrite:
        raise SystemExit(f"Output already exists, use --overwrite: {output}")
    if min(
        args.ribbon_width,
        args.ribbon_thickness,
        args.strut_length,
        args.strut_loop,
        args.strut_radius,
        args.peptide_atom_radius,
        args.peptide_stick_radius,
    ) <= 0:
        raise SystemExit("Ribbon, strut, and peptide dimensions must all be greater than zero.")

    _run(session, "open 1e27")
    # 1E27 includes 394 crystallographic water molecules.  They are useful for
    # the crystal structure but would become disconnected printable debris.
    _run(session, "delete solvent")
    _run(session, "delete #1 & ~protein")
    # Chain A residues 1-180 are the alpha1/alpha2 peptide-binding platform.
    # A:181-276 is alpha3, while B is beta-2-microglobulin.
    _run(session, "delete #1/B")
    _run(session, "delete #1/A:181-276")

    _run(session, "cartoon #1/A")
    _run(
        session,
        "cartoon style protein "
        f"width {args.ribbon_width:g} thickness {args.ribbon_thickness:g} "
        "xsection oval sides 24 divisions 20 arrows false",
    )
    # Color the hidden atoms as well: ChimeraX reads these endpoint colors when
    # drawing half-colored pseudo-bond struts.
    _run(session, f"color #1/A {args.mhc_color} target ac")

    # Keep the bound HIV peptide as a conventional molecular stick model.
    _run(session, "style #1/C stick")
    _run(
        session,
        "size #1/C "
        f"atomRadius {args.peptide_atom_radius:g} stickRadius {args.peptide_stick_radius:g}",
    )
    _run(session, f"color #1/C {args.peptide_color} target ab")
    _run(session, f"color #1/C@N* {args.nitrogen_color} target a")
    _run(session, f"color #1/C@O* {args.oxygen_color} target a")
    _run(session, "show #1/C atoms")
    _run(session, "show #1/C bonds")

    strut_count = 0
    if not args.no_struts:
        # After deleting solvent and chain B, #1@ca is exactly the retained MHC
        # platform plus its bound peptide.  This allows direct MHC-peptide
        # reinforcement while keeping the endpoint colors meaningful.
        strut_group = _run(
            session,
            "struts #1@ca "
            f"length {args.strut_length:g} loop {args.strut_loop:g} "
            f"radius {args.strut_radius:g} fattenRibbon true replace true "
            "name layerloom_pmhc_struts",
        )
        _run(
            session,
            "cartoon style protein "
            f"width {args.ribbon_width:g} thickness {args.ribbon_thickness:g} "
            "xsection oval sides 24 divisions 20 arrows false",
        )
        strut_count = _solidify_struts(strut_group, args.strut_radius, not args.no_halfbond)

    _run(session, "hide #1/A atoms")
    _run(session, "hide #1/A bonds")
    _run(session, "set bgColor white")
    if args.flip_upside_down:
        _run(session, "turn x 180 models #1")
    if args.x_rotation_degrees:
        _run(session, f"turn x {args.x_rotation_degrees:g} models #1")

    _run(
        session,
        f"save {_quoted_path(output)} format gltf center true centerEachNode false "
        "pruneVertexColors false backfaceCulling false",
    )
    session.logger.info(f"Saved 1E27 pMHC-I presentation GLB: {output} ({strut_count} half-colored struts)")

    if args.inspect:
        _run(session, "close all")
        _run(session, f"open {_quoted_path(output)}")
    return output


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    chimerax_session = globals().get("session")
    if chimerax_session is None:
        raise SystemExit("This script must be run inside ChimeraX with --script.")
    run_presentation_preset(chimerax_session, args)
    return 0


if __name__ == "__main__" or "session" in globals():
    raise SystemExit(main(sys.argv[1:]))
