"""Behavioral checks for the isolated, matched-band benchmark harness."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import trimesh

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("controlled_worker", ROOT / "experiments/planar_bands/controlled_worker.py")
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


def box(height=0.4, bottom=0.0, x=0.0):
    mesh = trimesh.creation.box(extents=(1, 1, height))
    mesh.apply_translation((x, 0, bottom + height / 2))
    return mesh


def fake_legacy(transform=None):
    def repair(vertices, faces, config):
        if transform:
            vertices, faces = transform(vertices, faces)
        return vertices, faces, {"test_before": True}, {"test_after": True}
    return SimpleNamespace(repair_geometry=repair, RepairConfig=lambda **kw: kw,
                           apply_safe=lambda *args, **kwargs: True)


def controlled_inputs(monkeypatch, parts):
    monkeypatch.setattr(worker, "_load_case", lambda case, path=None: (
        [(name, mesh.copy()) for name, mesh in parts], {"synthetic": True, "sha256": "test"}))
    monkeypatch.setattr(worker, "_legacy_module", lambda: fake_legacy())
    monkeypatch.setattr(worker, "_warmup", lambda legacy: None)


@pytest.mark.parametrize("method", worker.METHODS)
def test_same_original_grid_assignments_and_valid_footprints(monkeypatch, method):
    controlled_inputs(monkeypatch, [("first__PAT_cm__", box()), ("second__PAT_y__", box(x=2))])
    result = worker.run_case("cubes", 0.2, method)
    assert result["status"] == "ok", result["audit"]
    assert result["audit"]["enabled"] is True
    assert [(b["part_index"], b["layer_index"], b["token"]) for b in result["bands"]] == [
        (0, 0, "c"), (0, 1, "m"), (1, 0, "y"), (1, 1, "y")]
    assert result["counts"]["produced_bands"] == 4
    assert result["audit"]["summary"]["expected_nonempty_midpoints"] == 4
    assert result["audit"]["summary"]["passes_declared_checks"]
    times = result["timings"]
    assert times["core_s"] == pytest.approx(times["repair_s"] + times["layers_s"] + times["assembly_s"])
    assert times["matched_part_bands_s"] == pytest.approx(times["repair_s"] + times["layers_s"])
    assert all(record["audit"]["float32"]["is_volume"] for record in result["bands"])
    assert len(result["fingerprints"]["combined_sha256"]) == 64
    json.dumps(result, default=worker._json_default, allow_nan=False)


def test_audit_is_untimed_optional_and_does_not_change_geometry(monkeypatch):
    controlled_inputs(monkeypatch, [("box__PAT_c__", box())])
    full = worker.run_case("cubes", 0.2, "contour", audit=True)
    quick = worker.run_case("cubes", 0.2, "contour", audit=False)
    assert quick["audit"] == {"enabled": False}
    assert quick["timings"]["audit_s"] == 0
    assert quick["fingerprints"] == full["fingerprints"]
    assert "audit" not in quick["bands"][0]
    assert "audit" in full["bands"][0]


def test_thin_feature_empty_midpoint_is_distinct_from_failed_expected_band(monkeypatch):
    controlled_inputs(monkeypatch, [("anchor__PAT_c__", box()),
                                    ("thin__PAT_m__", box(height=0.02, bottom=0.05, x=2))])
    result = worker.run_case("cubes", 0.2, "contour")
    thin = result["bands"][-1]
    assert thin["empty_midpoint"]
    assert thin["geometry_sha256"] is None
    assert thin["audit"]["expected_nonempty"] is False
    assert result["audit"]["summary"]["legitimate_empty_midpoints"] == 1
    assert result["audit"]["summary"]["missing_expected_midpoints"] == 0
    assert result["status"] == "ok"


def test_legacy_guard_reverts_entire_input_after_destructive_repair():
    dense = trimesh.creation.icosphere(subdivisions=3)
    parts = [("dense", dense)]
    legacy = fake_legacy(lambda vertices, faces: (vertices, faces[:10]))
    effective, details = worker._repair(parts, legacy)
    assert effective is parts
    assert details["fallback_to_raw"]
    assert "removed too much geometry" in details["rejection_reason"]
    assert details["after"]["triangles"] == 10


def test_repair_changes_are_measured_against_original_not_repaired_source(monkeypatch):
    controlled_inputs(monkeypatch, [("box__PAT_c__", box())])
    def move(vertices, faces):
        changed = np.array(vertices, copy=True)
        changed[:, 0] += 0.1
        return changed, faces
    monkeypatch.setattr(worker, "_legacy_module", lambda: fake_legacy(move))
    result = worker.run_case("cubes", 0.2, "repair_cut")
    assert result["status"] == "geometry_failure"
    assert result["audit"]["summary"]["footprint_area_failures"] == 2
    assert result["audit"]["summary"]["max_boundary_hausdorff_mm"] == pytest.approx(0.1, abs=1e-6)


def test_same_grid_survives_repaired_bounds_change(monkeypatch):
    controlled_inputs(monkeypatch, [("box__PAT_c__", box())])
    def compress(vertices, faces):
        changed = np.array(vertices, copy=True)
        changed[:, 2] *= 0.25
        return changed, faces
    monkeypatch.setattr(worker, "_legacy_module", lambda: fake_legacy(compress))
    result = worker.run_case("cubes", 0.2, "repair_cut")
    assert [b["layer_index"] for b in result["bands"]] == [0, 1]
    assert result["audit"]["summary"]["missing_expected_midpoints"] >= 1
    assert result["status"] == "geometry_failure"


def test_actual_legacy_helper_and_common_warmup_are_usable():
    legacy = worker._legacy_module()
    worker._warmup(legacy)
    source = box()
    effective, details = worker._repair([("box", source)], legacy)
    assert details["parts"][0]["topology_before"]
    assert details["parts"][0]["topology_after"]
    assert not details["fallback_to_raw"]
    assert effective[0][1].is_volume
