from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "chimerax_prepare_layerloom.py"


def load_script_module():
    spec = importlib.util.spec_from_file_location("chimerax_prepare_layerloom", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_default_output_path_for_pdb_id(tmp_path):
    module = load_script_module()

    output = module.default_output_path("1ubq", tmp_path, "rainbow-residue")

    assert output == tmp_path / "1ubq_layerloom_rainbow_residue_ribbon_struts.glb"


def test_parser_defaults_are_printable_ribbon_settings():
    module = load_script_module()

    args = module.build_parser().parse_args(["1ubq"])

    assert args.color_mode == "rainbow-residue"
    assert args.ribbon_width == 3.6
    assert args.ribbon_thickness == 2.0
    assert args.strut_atoms == "@ca"
    assert args.strut_radius == 0.8
