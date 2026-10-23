from __future__ import annotations

import trimesh

from layerloom.weave import colorize_part


def test_direct_token_preserves_original_closed_mesh():
    mesh = trimesh.creation.box(extents=(2.0, 3.0, 4.0))
    parts = colorize_part("cyan_part", mesh, "c", z0=0.0, step=0.08)

    assert len(parts) == 1
    assert parts[0][0].endswith("__direct__PAT_c__")
    assert parts[0][1] is mesh
    assert parts[0][1].is_watertight


def test_blended_token_still_uses_band_slicing():
    mesh = trimesh.creation.box(extents=(2.0, 3.0, 0.48))
    mesh.apply_translation((0.0, 0.0, 0.24))
    parts = colorize_part("blend", mesh, "cm", z0=0.0, step=0.08)

    assert len(parts) > 1
    assert {label.rsplit("__PAT_", 1)[1][0] for label, _mesh in parts} == {"c", "m"}
