"""The slicer smoke probe must detect motion and extrusion, not E text alone."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


PROBE = Path(__file__).resolve().parents[1] / "experiments/planar_bands/slicer_probe.py"
SPEC = spec_from_file_location("planar_slicer_probe", PROBE)
probe = module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def inspect(tmp_path, commands):
    path = tmp_path / "probe.gcode"
    path.write_text(commands)
    return probe.inspect_gcode(path)


@pytest.mark.parametrize("move", [
    "G1 X10 E0",             # Zero relative extrusion.
    "G1 X10 E-0.2",          # Retraction while moving.
    "G1 X0 Y0 E0.2",         # Extrusion without XY movement.
    "G1 Z0.4 E0.2",          # Z-only movement.
    "G1 X10 ; E0.2",         # E is only a comment.
    "G1 X10 (E0.2)",         # E is only a parenthetical comment.
])
def test_does_not_count_nonextruding_or_stationary_moves(tmp_path, move):
    result = inspect(tmp_path, f"G90\nM83\nG0 X0 Y0\n;Z:0.2\n{move}\n")
    assert result["layer_count"] == 1
    assert result["layers_with_xy_extrusion"] == 0


def test_absolute_e_requires_positive_delta_and_honors_g92(tmp_path):
    result = inspect(tmp_path, """G90
M82
G0 X0 Y0
G92 E10
;Z:0.2
G1 X10 E9
G1 X20 E9
G1 X30 E9.5
G92 E0
G1 X40 E0.2
""")
    assert result["layers"][0]["xy_extrusion_moves"] == 2


def test_absolute_xy_unknown_start_is_not_assumed_to_be_zero(tmp_path):
    result = inspect(tmp_path, """G90
M83
;Z:0.2
G1 X10 Y10 E0.2
G1 X10 Y10 E0.2
G1 X11 E0.2
""")
    assert result["layers"][0]["xy_extrusion_moves"] == 1


def test_absolute_e_unknown_start_is_not_assumed_to_be_zero(tmp_path):
    result = inspect(tmp_path, """G90
M82
G0 X0 Y0
;Z:0.2
G1 X1 E10
G1 X2 E10
G1 X3 E10.1
""")
    assert result["layers"][0]["xy_extrusion_moves"] == 1


def test_relative_xyz_and_e_have_known_deltas_with_unknown_origin(tmp_path):
    result = inspect(tmp_path, """G91
M83
;Z:0.2
G1 X-1 E0.2
G1 X0 Y0 E0.2
G1 Y2 E0.2
""")
    assert result["layers"][0]["xy_extrusion_moves"] == 2


def test_m82_overrides_relative_xyz_and_g90_resets_m83(tmp_path):
    result = inspect(tmp_path, """G92 X0 Y0 E10
G91
M82
;Z:0.2
G1 X1 E9
G1 X1 E9.1
M83
G1 X1 E0.1
G90
G1 X4 E0.1
G1 X5 E0.2
""")
    assert result["layers"][0]["xy_extrusion_moves"] == 3


def test_g92_resets_xyz_without_moving_and_state_survives_layer_comments(tmp_path):
    result = inspect(tmp_path, """G90
M83
G0 X10 Y10
;Z:0.2
G92 X100 Y100
G1 X100 Y100 E0.2
G1 X101 E0.2
;Z:0.4
G1 X101 E0.2
G1 Y101 E0.2
""")
    assert result["layer_z_mm"] == [0.2, 0.4]
    assert [layer["xy_extrusion_moves"] for layer in result["layers"]] == [1, 1]


def test_compact_numbered_moves_and_signed_decimal_values(tmp_path):
    result = inspect(tmp_path, """G90
M83
G0X0Y0
;Z:+.2
N12G1X-.5E+.2*0
""")
    assert result["layer_z_mm"] == [0.2]
    assert result["layers_with_xy_extrusion"] == 1


def test_homing_does_not_assume_a_known_xy_origin(tmp_path):
    result = inspect(tmp_path, """G90
M83
G0 X10 Y10
G28
;Z:0.2
G1 X0 Y0 E0.2
G1 X1 E0.2
""")
    assert result["layers"][0]["xy_extrusion_moves"] == 1
