from __future__ import annotations

import math

import trimesh

from layerloom.weave import choose_first_layer_token, colorize_part


def _base_box(width: float) -> trimesh.Trimesh:
    mesh = trimesh.creation.box(extents=(width, 2.0, 1.0))
    mesh.apply_translation((0.0, 0.0, 0.5))
    return mesh


def test_auto_first_layer_uses_largest_base_footprint():
    narrow_cyan = _base_box(1.0)
    wide_magenta = _base_box(3.0)
    prepared = [
        ("cyan", narrow_cyan, "cmy", "test"),
        ("magenta", wide_magenta, "mcy", "test"),
    ]

    token, volumes = choose_first_layer_token("auto", prepared, z0=0.0, step=0.08)

    assert token == "m"
    assert volumes["m"] > volumes["c"]


def test_explicit_first_layer_color_overrides_auto_choice():
    token, volumes = choose_first_layer_token(
        "y",
        [("cyan", _base_box(2.0), "cmy", "test")],
        z0=0.0,
        step=0.08,
    )

    assert token == "y"
    assert volumes == {}


def test_first_layer_override_splits_single_token_volume():
    mesh = _base_box(2.0)
    parts = colorize_part(
        "cyan",
        mesh,
        "c",
        z0=0.0,
        step=0.08,
        first_layer_token="m",
    )

    labels = [label for label, _mesh in parts]
    base = next(part for label, part in parts if "__first_layer__PAT_m__" in label)
    remainder = next(part for label, part in parts if "__direct__PAT_c__" in label)
    assert len(parts) == 2
    assert math.isclose(float(base.bounds[1, 2]), 0.0795, abs_tol=1e-6)
    assert float(remainder.bounds[0, 2]) > 0.08
