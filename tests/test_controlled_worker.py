"""Behavioral checks for the isolated, matched-band benchmark harness."""
import importlib.util
import hashlib
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


def test_missing_repository_example_requires_explicit_input(monkeypatch, tmp_path):
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    monkeypatch.setattr(worker, "read_parts", lambda *a, **k: pytest.fail("Missing input cannot be read"))
    with pytest.raises(FileNotFoundError, match="use --input"):
        worker._load_case("benchy")


def test_explicit_example_input_is_used_and_fingerprinted(monkeypatch, tmp_path):
    path = tmp_path / "explicit.3mf"
    path.write_bytes(b"fixture data")
    monkeypatch.setattr(worker, "ROOT", tmp_path / "no repository examples")
    monkeypatch.setattr(worker, "read_parts", lambda paths: [("selected", paths)])
    parts, source = worker._load_case("benchy", input_path=path)
    assert parts == [("selected", [str(path.resolve())])]
    assert source["path"] == str(path.resolve())
    assert source["sha256"] == hashlib.sha256(b"fixture data").hexdigest()


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
    monkeypatch.setattr(worker, "_warmup", lambda legacy, **kwargs: None)


@pytest.mark.parametrize("method", ("cut", "repair_cut", "contour"))
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


def test_optimized_defers_source_extrusion_and_preserves_final_material_geometry(monkeypatch):
    controlled_inputs(monkeypatch, [("first__PAT_cm__", box()), ("second__PAT_y__", box(x=2))])
    previous = worker.run_case("cubes", 0.2, "contour", audit=False)
    reconstruct = worker.reconstruct_layer_profiles
    extrude = worker.extrude_profile
    calls = {"source_parts": 0, "extrusions": 0}

    def profiles(*args, **kwargs):
        calls["source_parts"] += 1
        return reconstruct(*args, **kwargs)

    def final_mesh(*args, **kwargs):
        calls["extrusions"] += 1
        return extrude(*args, **kwargs)

    monkeypatch.setattr(worker, "reconstruct_layer_profiles", profiles)
    monkeypatch.setattr(worker, "extrude_profile", final_mesh)
    result = worker.run_case("cubes", 0.2, "contour_optimized", audit=False)
    assert result["status"] == "ok", result["errors"]
    assert calls == {"source_parts": 2, "extrusions": result["counts"]["native_parts"]}
    assert result["fingerprints"]["native_parts_sha256"] == previous["fingerprints"]["native_parts_sha256"]
    assert result["fingerprints"]["source_profiles_sha256"] == previous["fingerprints"]["source_profiles_sha256"]
    assert result["fingerprints"]["part_bands_sha256"] is None
    assert result["counts"]["source_profiles"] == 4
    assert result["counts"]["deferred_source_extrusions"] == 4
    assert result["counts"]["produced_source_meshes"] == 0
    assert result["counts"]["missing_bands"] == 0
    assert result["execution_plan"]["source_meshes_expected"] is False
    for record in result["bands"]:
        assert record["extrusion_deferred"]
        assert "missing_output" not in record
        assert record["geometry_sha256"] is None
        assert len(record["profile_sha256"]) == 64
    for record in result["native_parts"]:
        assert record["intrinsic_float32"]["is_volume"]
        assert not record["intrinsic_float32"]["zero_area_triangles"]
        assert not record["intrinsic_float32"]["duplicate_faces"]
    times = result["timings"]
    assert times["matched_part_bands_s"] is None
    assert times["core_s"] == pytest.approx(times["repair_s"] + times["layers_s"] + times["assembly_s"])
    json.dumps(result, default=worker._json_default, allow_nan=False)


def test_optimized_rejects_inapplicable_source_mesh_audit():
    with pytest.raises(ValueError, match="defers source meshes; use audit=False"):
        worker.run_case("cubes", 0.2, "contour_optimized")


def test_optimized_empty_midpoints_are_profiles_without_missing_mesh_failures(monkeypatch):
    controlled_inputs(monkeypatch, [("anchor__PAT_c__", box()),
                                    ("thin__PAT_m__", box(height=0.02, bottom=0.05, x=2))])
    result = worker.run_case("cubes", 0.2, "contour_optimized", audit=False)
    assert result["status"] == "ok", result["errors"]
    assert result["counts"]["source_profiles"] == 3
    assert result["counts"]["nonempty_source_profiles"] == 2
    assert result["counts"]["empty_source_profiles"] == 1
    assert result["counts"]["deferred_source_extrusions"] == 2
    assert result["counts"]["missing_bands"] == 0
    assert result["counts"]["failed_source_profiles"] == 0
    thin = result["bands"][-1]
    assert thin["empty_midpoint"]
    assert "extrusion_deferred" not in thin
    assert "missing_output" not in thin


def test_optimized_final_extrusion_failure_is_rejected(monkeypatch):
    controlled_inputs(monkeypatch, [("box__PAT_c__", box())])

    def reject(*args, **kwargs):
        raise ValueError("final mesh rejected")

    monkeypatch.setattr(worker, "extrude_profile", reject)
    result = worker.run_case("cubes", 0.2, "contour_optimized", audit=False)
    assert result["status"] == "geometry_failure"
    assert result["counts"]["source_profiles"] == 2
    assert result["counts"]["missing_bands"] == 0
    assert result["counts"]["native_parts"] == 0
    assert len(result["errors"]) == 2
    assert all("final mesh rejected" in error for error in result["errors"])


def test_optimized_rejected_profile_cannot_reach_native_assembly(monkeypatch):
    controlled_inputs(monkeypatch, [("box__PAT_c__", box())])
    reconstruct = worker.reconstruct_layer_profiles

    def rejected_profile(*args, **kwargs):
        result = reconstruct(*args, **kwargs)
        result[0].profile.ok = False
        return result

    monkeypatch.setattr(worker, "reconstruct_layer_profiles", rejected_profile)
    result = worker.run_case("cubes", 0.2, "contour_optimized", audit=False)
    assert result["status"] == "geometry_failure"
    assert result["counts"]["failed_source_profiles"] == 1
    assert result["counts"]["deferred_source_extrusions"] == 1
    assert result["counts"]["missing_bands"] == 0
    assert [record["layer_index"] for record in result["native_parts"]] == [1]
    assert "profile_rejected" in result["errors"][0]


def test_frozen_planar_kernel_drives_both_timed_stages_and_warmup(monkeypatch, tmp_path):
    original_warmup = worker._warmup
    controlled_inputs(monkeypatch, [("box__PAT_c__", box())])
    previous = worker.run_case("cubes", 0.2, "contour", audit=False)
    archived = tmp_path / "frozen_planar.py"
    archived.write_text(
        "from layerloom.planar_bands import RepairConfig, section_profile as _section, extrude_profile as _extrude\n"
        "calls = []\n"
        "def section_profile(*args, **kwargs):\n"
        "    calls.append('profile')\n"
        "    return _section(*args, **kwargs)\n"
        "def extrude_profile(*args, **kwargs):\n"
        "    calls.append('extrude')\n"
        "    return _extrude(*args, **kwargs)\n")

    def forbidden(*args, **kwargs):
        raise AssertionError("current planar kernel used in frozen control")

    monkeypatch.setattr(worker, "section_profile", forbidden)
    monkeypatch.setattr(worker, "extrude_profile", forbidden)
    monkeypatch.setattr(worker, "_warmup", original_warmup)
    result = worker.run_case("cubes", 0.2, "contour", audit=False, baseline_planar_path=archived)
    frozen = worker._baseline_planar(archived)
    # One profile and two extrusions warm up the selected implementation;
    # each of two measured layers then has a source and final extrusion.
    assert frozen.calls.count("profile") == 3
    assert frozen.calls.count("extrude") == 6
    assert result["status"] == "ok", result["errors"]
    assert result["fingerprints"] == previous["fingerprints"]
    assert result["baseline_planar"] == {
        "path": str(archived.resolve()), "sha256": hashlib.sha256(archived.read_bytes()).hexdigest(),
        "loaded": True, "used_for_timed_geometry": True,
    }


@pytest.mark.parametrize("method", ("cut", "repair_cut", "contour_optimized"))
def test_other_methods_record_but_do_not_load_frozen_control(monkeypatch, tmp_path, method):
    controlled_inputs(monkeypatch, [("box__PAT_c__", box())])
    archived = tmp_path / "unused_planar.py"
    archived.write_text("raise RuntimeError('only the old contour arm may import this module')\n")
    result = worker.run_case("cubes", 0.2, method, audit=False, baseline_planar_path=archived)
    assert result["status"] == "ok", result["errors"]
    assert result["baseline_planar"] == {
        "path": str(archived.resolve()), "sha256": hashlib.sha256(archived.read_bytes()).hexdigest(),
        "loaded": False, "used_for_timed_geometry": False,
    }
