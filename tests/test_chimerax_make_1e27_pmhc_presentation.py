from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "chimerax_make_1e27_pmhc_presentation.py"


def load_script_module():
    spec = importlib.util.spec_from_file_location("chimerax_make_1e27_pmhc_presentation", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_presentation_defaults_match_protein_print_preset(tmp_path):
    module = load_script_module()
    args = module.build_parser().parse_args([])

    assert args.ribbon_width == 3.6
    assert args.ribbon_thickness == 2.0
    assert args.strut_length == 7.0
    assert args.strut_loop == 30.0
    assert args.strut_radius == 0.8
    assert args.peptide_atom_radius == 2.0
    assert args.peptide_stick_radius == 1.1
    assert not args.no_struts
    assert not args.no_halfbond
    assert not args.flip_upside_down
    assert args.x_rotation_degrees == 0.0
    assert module.default_output_path(tmp_path) == tmp_path / "1e27_mhci_binding_cleft_presentation.glb"


def test_presentation_palette_is_explicit_and_cmy_compatible():
    module = load_script_module()

    assert module.MHC_GREEN == "#9CD36E"
    assert module.PEPTIDE_CYAN == "#3AC8DC"
    assert module.NITROGEN_BLUE == "#5C9CCA"
    assert module.OXYGEN_RED == "#E83020"
