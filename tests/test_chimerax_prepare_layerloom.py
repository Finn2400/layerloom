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
    assert args.representation == "ribbon"
    assert args.ribbon_width == 3.6
    assert args.ribbon_thickness == 2.0
    assert args.strut_atoms == "@ca"
    assert args.strut_radius == 0.8
    assert not args.no_save


def test_parser_accepts_unsaved_preview_mode():
    module = load_script_module()

    args = module.build_parser().parse_args(["2erj", "--no-save"])

    assert args.no_save


def test_parser_accepts_hidden_cartoon_chain_list():
    module = load_script_module()

    args = module.build_parser().parse_args(["2erj", "--hide-cartoons", "E,F,G,H"])

    assert args.hide_cartoons == "E,F,G,H"


def test_parser_accepts_repeated_chain_color_overrides():
    module = load_script_module()
    args = module.build_parser().parse_args(
        ["2erj", "--chain-color", "A:#ffd700", "--chain-color", "D:#00008b"]
    )
    assert args.chain_color == ["A:#ffd700", "D:#00008b"]


def test_parser_accepts_protein_only_mode():
    module = load_script_module()
    args = module.build_parser().parse_args(["3kmd", "--protein-only"])
    assert args.protein_only


def test_surface_output_name_and_parser_mode(tmp_path):
    module = load_script_module()
    args = module.build_parser().parse_args(["3kmd", "--representation", "surface"])

    assert args.representation == "surface"
    assert module.default_output_path(
        "3kmd", tmp_path, "by-chain", args.representation
    ) == tmp_path / "3kmd_layerloom_by_chain_surface.glb"


def test_surface_color_helpers_target_atoms_and_surfaces(monkeypatch):
    module = load_script_module()
    commands = []
    monkeypatch.setattr(module, "_run", lambda session, command: commands.append(command))

    module._apply_color(None, "by-chain", target="as")
    module._apply_chain_color_overrides(None, ["A:#00bcd4"], target="as")

    assert commands == [
        "color bychain target as",
        "color /A #00bcd4 target as",
    ]
