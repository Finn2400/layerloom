#!/usr/bin/env python3
"""One isolated repair/layer-creation measurement; no export or slicer work.

Run fresh workers serially. Loading, import, warmup, fingerprints and the common
posthoc audit are outside stage timers. Timed calls retain their own internal
checks. Source-part bands share one original global grid and a 0.001 mm Z gap.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict
import hashlib
import importlib.util
from importlib.metadata import PackageNotFoundError, version
import json
import math
from pathlib import Path
import platform
import re
import sys
import time

import numpy as np
import shapely
from shapely.geometry import Polygon
import trimesh

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from controlled_fixtures import fixtures, reference_profile
from layerloom.contour_repair import _float32_mesh_checks
from layerloom.group_by_color import group_by_color
from layerloom.ingest import read_parts
from layerloom.planar_bands import RepairConfig, extrude_profile, reconstruct_layer_profiles, section_profile
from layerloom.planar_materials import partition_materials
from layerloom.repair import MeshGeometryCounts, repair_rejection_reason
from layerloom.strata import _slice_between
from layerloom.tokens import TOKEN_ALPHABET

CASES = ("cubes", "benchy", "hollow_tube", "hollow_tube_gap020")
METHODS = ("cut", "repair_cut", "contour", "contour_optimized")
Z_GAP = 0.001
CONFIG = RepairConfig()
SEAM_TOLERANCE = 0.001
FOOTPRINT_DISTANCE_BUDGET = 0.001
EXAMPLES = {"cubes": "tutorial_cmy_cubes.3mf", "benchy": "tutorial_cmy_benchy_cutup.3mf"}


def _legacy_module():
    name = "_controlled_legacy_meshlab_repair"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "src/supporting_scripts/3mf_meshlab_repair_3mf.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            del sys.modules[name]
            raise
    return sys.modules[name]


def _baseline_planar(path):
    """Load an archived standalone planar kernel before any measured work."""
    path = Path(path).resolve()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    name = f"_controlled_baseline_planar_{digest}"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        # Dataclasses resolve their defining module during import.
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            del sys.modules[name]
            raise
    return sys.modules[name]


def _mesh_hash(mesh):
    if mesh is None:
        return None
    digest = hashlib.sha256()
    for array, dtype in ((mesh.vertices, "<f8"), (mesh.faces, "<i8")):
        data = np.asarray(array, dtype=dtype, order="C")
        digest.update(np.asarray(data.shape, dtype="<i8").tobytes())
        digest.update(data.tobytes())
    return digest.hexdigest()


def _json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _load_case(case, input_path=None):
    if case not in CASES:
        raise ValueError(f"Unknown case: {case}")
    if case in EXAMPLES:
        candidates = [Path(input_path)] if input_path else [
            ROOT / "examples" / EXAMPLES[case],
        ]
        path = next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)
        if path is None:
            raise FileNotFoundError(f"No source found: {candidates}; use --input to select a source explicitly")
        return read_parts([str(path)]), {
            "synthetic": False, "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    if input_path:
        raise ValueError("--input applies only to the repository example cases")
    fixture = fixtures()[case]
    parts = fixture["parts"]
    return parts, {**fixture["metadata"], "sha256": _json_hash([
        [label, _mesh_hash(mesh)] for label, mesh in parts]),
        "generator_sha256": hashlib.sha256((Path(__file__).parent / "controlled_fixtures.py").read_bytes()).hexdigest()}


def _counts(parts):
    return MeshGeometryCounts(objects=len(parts), vertices=sum(len(m.vertices) for _, m in parts),
                              triangles=sum(len(m.faces) for _, m in parts))


def _recipe(label):
    matches = re.findall(r"__PAT_(.*?)__", label, flags=re.IGNORECASE)
    value = matches[-1].lower() if matches else "cmy"
    if not value or any(token not in TOKEN_ALPHABET for token in value):
        raise ValueError(f"Unsupported PAT recipe in {label!r}")
    return value


def _warmup(legacy, *, planar=None):
    make_profile = section_profile if planar is None else planar.section_profile
    make_mesh = extrude_profile if planar is None else planar.extrude_profile
    config = CONFIG if planar is None else planar.RepairConfig(**asdict(CONFIG))
    mesh = trimesh.creation.box(extents=(1, 1, 1))
    mesh.apply_translation((0, 0, 0.5))
    legacy.repair_geometry(mesh.vertices, mesh.faces, legacy.RepairConfig(quiet=True))
    _slice_between(mesh.copy(), 0.2, 0.4, z_gap=Z_GAP)
    profile = make_profile(mesh.copy(), 0.3, config)
    band = make_mesh(profile.region, 0.2005, 0.3995)
    regions, _, errors = partition_materials({"c": [profile.region]}, precision=CONFIG.precision,
                                              seam_tolerance=SEAM_TOLERANCE)
    if errors or band is None:
        raise RuntimeError("Common warmup failed")
    native = make_mesh(regions["c"], 0.2005, 0.3995)
    _float32_mesh_checks(native)
    group_by_color([("warmup__PAT_c__", band)], verbose=False)


def _repair(parts, legacy):
    """Actual script kernel, followed by the existing headless global count guard."""
    details = {"before": asdict(_counts(parts)), "parts": [], "fallback_to_raw": False,
               "rejection_reason": None, "skipped_filters": []}
    repaired = []
    original_apply = legacy.apply_safe

    def tracked_apply(ms, name, quiet, **kwargs):
        succeeded = original_apply(ms, name, quiet, **kwargs)
        if not succeeded:
            details["skipped_filters"].append(name)
        return succeeded

    legacy.apply_safe = tracked_apply
    try:
        for index, (label, mesh) in enumerate(parts):
            vertices, faces, before, after = legacy.repair_geometry(
                mesh.vertices, mesh.faces, legacy.RepairConfig(quiet=True))
            repaired.append((label, trimesh.Trimesh(vertices=vertices, faces=faces, process=False)))
            details["parts"].append({"part_index": index, "topology_before": before,
                                     "topology_after": after})
        details["after"] = asdict(_counts(repaired))
        details["rejection_reason"] = repair_rejection_reason(_counts(parts), _counts(repaired))
    except Exception as exc:
        details["error"] = f"{type(exc).__name__}: {exc}"
        details["after"] = asdict(_counts(repaired))
        details["rejection_reason"] = "Legacy repair raised; headless non-strict behavior uses raw input"
    finally:
        legacy.apply_safe = original_apply
    if details["rejection_reason"]:
        details["fallback_to_raw"] = True
        return parts, details
    return repaired, details


def _rounded(mesh):
    return trimesh.Trimesh(vertices=np.round(np.asarray(mesh.vertices), 9).astype(np.float32).astype(float),
                           faces=mesh.faces, process=False)


def _profile_audit(mesh, z):
    if mesh is None:
        return Polygon(), {"ok": True, "status": "missing_output", "repair_applied": False}
    result = section_profile(mesh, z, CONFIG)
    diagnostics = result.diagnostics
    repaired = diagnostics.get("status") in {"repaired", "contour_repaired"}
    return result.region, {"ok": bool(result.ok), "status": diagnostics.get("status"),
                           "repair_applied": repaired, "diagnostics": diagnostics}


def _compare_profiles(expected, actual, reference_ok, actual_ok):
    def topology(region):
        polygons = [p for p in shapely.get_parts(region) if p.geom_type == "Polygon" and not p.is_empty]
        return len(polygons), sum(len(p.interiors) for p in polygons)
    expected_components, expected_holes = topology(expected)
    actual_components, actual_holes = topology(actual)
    record = {"reference_ok": reference_ok, "actual_ok": actual_ok,
              "reference_components": expected_components, "reference_holes": expected_holes,
              "actual_components": actual_components, "actual_holes": actual_holes,
              "reference_area_mm2": float(expected.area), "actual_area_mm2": float(actual.area),
              "expected_nonempty": bool(reference_ok and not expected.is_empty),
              "missing_expected_midpoint": bool(reference_ok and not expected.is_empty and actual.is_empty),
              "unexpected_midpoint": bool(reference_ok and expected.is_empty and not actual.is_empty)}
    if not reference_ok or not actual_ok:
        record.update(comparable=False, within_area_budget=False,
                      symmetric_difference_mm2=None, boundary_hausdorff_mm=None, area_budget_mm2=None)
        return record
    # Fixed declared tolerance, independent of which method produced the band.
    budget = FOOTPRINT_DISTANCE_BUDGET * float(expected.length)
    difference = float(expected.symmetric_difference(actual).area)
    boundary_distance = None if expected.is_empty or actual.is_empty else float(
        shapely.hausdorff_distance(expected.boundary, actual.boundary, densify=0.25))
    record.update(comparable=True, symmetric_difference_mm2=difference,
                  boundary_hausdorff_mm=boundary_distance, area_budget_mm2=budget,
                  within_area_budget=bool(difference <= budget + 1e-12))
    return record


def _audit(case, source_parts, records, meshes, native_records, native_meshes, height, z0):
    """One common audit; repaired audit contours are explicitly identified."""
    started = time.perf_counter()
    refs = defaultdict(lambda: defaultdict(list))
    reference_ok = defaultdict(lambda: True)
    summary = defaultdict(int)
    differences, distances = [], []
    for record, mesh in zip(records, meshes):
        part_index, index, token = record["part_index"], record["layer_index"], record["token"]
        z = z0 + (index + 0.5) * height
        if case.startswith("hollow_tube"):
            expected = reference_profile(z)
            reference = {"ok": True, "status": "analytical_polygonal_fixture", "repair_applied": False}
        else:
            expected, reference = _profile_audit(source_parts[part_index][1], z)
        reference_ok[index] &= reference["ok"]
        if reference["ok"]:
            refs[index][token].append(expected)
        actual, observation = _profile_audit(None if mesh is None else _rounded(mesh), z)
        comparison = _compare_profiles(expected, actual, reference["ok"], observation["ok"])
        entry = {"reference_profile": reference, "output_profile": observation, **comparison}
        if mesh is not None:
            checks = _float32_mesh_checks(mesh)
            entry["indexed_is_volume"] = bool(mesh.is_volume)
            entry["float32"] = checks
            summary["indexed_nonvolume_bands"] += not entry["indexed_is_volume"]
            summary["float32_nonvolume_bands"] += not checks["is_volume"]
            summary["zero_area_triangles"] += checks["zero_area_triangles"]
            summary["duplicate_faces"] += checks["duplicate_faces"]
        summary["reference_profile_failures"] += not reference["ok"]
        summary["output_profile_failures"] += not observation["ok"]
        summary["reference_audit_repairs"] += reference["repair_applied"]
        summary["output_audit_repairs"] += observation["repair_applied"]
        summary["expected_nonempty_midpoints"] += comparison["expected_nonempty"]
        summary["missing_expected_midpoints"] += comparison["missing_expected_midpoint"]
        summary["unexpected_midpoints"] += comparison["unexpected_midpoint"]
        summary["legitimate_empty_midpoints"] += bool(reference["ok"] and expected.is_empty)
        summary["footprint_area_failures"] += bool(comparison["comparable"] and not comparison["within_area_budget"])
        if comparison["symmetric_difference_mm2"] is not None:
            differences.append(comparison["symmetric_difference_mm2"])
        if comparison["boundary_hausdorff_mm"] is not None:
            distances.append(comparison["boundary_hausdorff_mm"])
        record["audit"] = entry

    native_by_layer = defaultdict(lambda: defaultdict(list))
    for record, mesh in zip(native_records, native_meshes):
        checks = _float32_mesh_checks(mesh)
        record["audit"] = {"indexed_is_volume": bool(mesh.is_volume), "float32": checks}
        summary["native_indexed_nonvolume_parts"] += not mesh.is_volume
        summary["native_float32_nonvolume_parts"] += not checks["is_volume"]
        summary["native_zero_area_triangles"] += checks["zero_area_triangles"]
        summary["native_duplicate_faces"] += checks["duplicate_faces"]
        rounded = _rounded(mesh)
        indexes = [record["layer_index"]] if record.get("layer_index") is not None else sorted(refs)
        for index in indexes:
            z = z0 + (index + 0.5) * height
            # Grouped legacy output contains many bands; skip nonintersecting groups.
            if not mesh.bounds[0, 2] < z < mesh.bounds[1, 2]:
                continue
            actual, observation = _profile_audit(rounded, z)
            native_by_layer[index][record["token"]].append((actual, observation))
    native_profiles = []
    for index in sorted(set(refs) | set(native_by_layer)):
        tokens = set(refs[index]) | set(native_by_layer[index])
        for token in sorted(tokens):
            expected = shapely.union_all(refs[index].get(token, []), grid_size=0)
            observations = native_by_layer[index].get(token, [])
            actual = shapely.union_all([item[0] for item in observations], grid_size=0)
            actual_ok = all(item[1]["ok"] for item in observations)
            comparison = _compare_profiles(expected, actual, reference_ok[index], actual_ok)
            native_profiles.append({"layer_index": index, "token": token, **comparison,
                                    "output_audit_repairs": sum(item[1]["repair_applied"] for item in observations)})
            summary["native_missing_expected_midpoints"] += comparison["missing_expected_midpoint"]
            summary["native_output_profile_failures"] += not actual_ok
            summary["native_output_audit_repairs"] += sum(item[1]["repair_applied"] for item in observations)
            summary["native_footprint_area_failures"] += bool(comparison["comparable"] and not comparison["within_area_budget"])
    summary["max_symmetric_difference_mm2"] = max(differences, default=0.0)
    summary["sum_symmetric_difference_mm2"] = sum(differences)
    summary["max_boundary_hausdorff_mm"] = max(distances, default=0.0)
    failures = ("indexed_nonvolume_bands", "float32_nonvolume_bands", "zero_area_triangles", "duplicate_faces",
                "reference_profile_failures", "output_profile_failures", "missing_expected_midpoints",
                "unexpected_midpoints", "footprint_area_failures", "native_indexed_nonvolume_parts",
                "native_float32_nonvolume_parts", "native_zero_area_triangles", "native_duplicate_faces",
                "native_missing_expected_midpoints", "native_output_profile_failures", "native_footprint_area_failures")
    summary["passes_declared_checks"] = not any(summary[key] for key in failures)
    return {"enabled": True, "summary": dict(summary), "native_midpoint_profiles": native_profiles,
            "area_budget": "0.001 mm * original/reference boundary length (plus 1e-12 mm2 arithmetic slack)",
            "scope": "Sampled midpoint footprints and indexed/float32 meshes; continuous 3D fidelity is not certified",
            "profile_audit": "Same bounded section_profile for all methods; every bridge/contour repair recorded",
            "audit_s": time.perf_counter() - started}


def run_case(case, height, method, audit=True, *, input_path=None, baseline_planar_path=None):
    if method not in METHODS:
        raise ValueError(f"Unknown method: {method}")
    optimized = method == "contour_optimized"
    contour_method = method in {"contour", "contour_optimized"}
    if optimized and audit:
        raise ValueError("contour_optimized defers source meshes; use audit=False and validate final native geometry identity instead of the source-mesh audit")
    if not math.isfinite(height) or height <= Z_GAP:
        raise ValueError(f"height must be finite and greater than {Z_GAP}")
    baseline = None
    baseline_provenance = {"path": None, "sha256": None, "loaded": False,
                           "used_for_timed_geometry": False}
    if baseline_planar_path is not None:
        path = Path(baseline_planar_path).resolve()
        baseline_provenance.update(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        if method == "contour":
            baseline = _baseline_planar(path)
            baseline_provenance.update(loaded=True, used_for_timed_geometry=True)
    make_profile = section_profile if baseline is None else baseline.section_profile
    make_mesh = extrude_profile if baseline is None else baseline.extrude_profile
    planar_config = CONFIG if baseline is None else baseline.RepairConfig(**asdict(CONFIG))
    parts, source = _load_case(case, input_path)
    if not parts:
        raise ValueError("No input parts")
    z0 = float(min(mesh.bounds[0, 2] for _, mesh in parts))
    schedules = []
    for part_index, (label, mesh) in enumerate(parts):
        recipe = _recipe(label)
        lo, hi = (float(value) for value in mesh.bounds[:, 2])
        for index in range(math.floor((lo - z0) / height), math.ceil((hi - z0) / height)):
            schedules.append({"part_index": part_index, "layer_index": index,
                              "token": recipe[index % len(recipe)], "sample_z_mm": z0 + (index + 0.5) * height})
    # Import and warm the same primitives for every arm, outside timed work.
    legacy = _legacy_module()
    warm_started = time.perf_counter()
    if baseline is None:
        _warmup(legacy)
    else:
        _warmup(legacy, planar=baseline)
    timings = {"warmup_s": time.perf_counter() - warm_started, "repair_s": 0.0, "repair_cpu_s": 0.0}
    repair = {"before": asdict(_counts(parts)), "after": asdict(_counts(parts)),
              "fallback_to_raw": False, "rejection_reason": None, "parts": []}
    effective = parts
    if method == "repair_cut":
        started, cpu = time.perf_counter(), time.process_time()
        effective, repair = _repair(parts, legacy)
        timings.update(repair_s=time.perf_counter() - started, repair_cpu_s=time.process_time() - cpu)
    records, meshes, profiles = [], [], []
    by_layer = defaultdict(lambda: defaultdict(list))
    errors, warnings = [], []
    profile_batches, profile_batch_errors = {}, {}
    started, cpu = time.perf_counter(), time.process_time()
    for scheduled in schedules:
        record = dict(scheduled)
        low = z0 + record["layer_index"] * height
        high = z0 + (record["layer_index"] + 1) * height
        mesh = None
        profile = None
        try:
            if contour_method:
                if optimized:
                    part_index = record["part_index"]
                    if part_index not in profile_batches and part_index not in profile_batch_errors:
                        try:
                            batch = reconstruct_layer_profiles(effective[part_index][1], height, z0=z0, config=CONFIG)
                            profile_batches[part_index] = {layer.index: layer for layer in batch}
                        except Exception as exc:
                            profile_batch_errors[part_index] = exc
                    if part_index in profile_batch_errors:
                        raise profile_batch_errors[part_index]
                    layer = profile_batches[part_index][record["layer_index"]]
                    if layer.sample_z != record["sample_z_mm"] or layer.low != low or layer.high != high:
                        raise ValueError("Production profile grid differs from the original benchmark grid")
                    profile = layer.profile
                else:
                    profile = make_profile(effective[record["part_index"]][1], record["sample_z_mm"], planar_config)
                record["profile"] = {"ok": bool(profile.ok), **profile.diagnostics}
                if not profile.ok:
                    record["failure"] = "profile_rejected"
                elif profile.region.is_empty:
                    record["empty_midpoint"] = True
                elif optimized:
                    record["extrusion_deferred"] = True
                    by_layer[record["layer_index"]][record["token"]].append(profile.region)
                else:
                    mesh = make_mesh(profile.region, low + Z_GAP / 2, high - Z_GAP / 2)
                    if mesh is not None:
                        by_layer[record["layer_index"]][record["token"]].append(profile.region)
            else:
                mesh = _slice_between(effective[record["part_index"]][1], low, high, z_gap=Z_GAP)
        except Exception as exc:
            record["exception"] = f"{type(exc).__name__}: {exc}"
            record["failure"] = "exception"
        if mesh is None and not optimized:
            record["missing_output"] = True
            if method == "contour" and not record.get("empty_midpoint"):
                record.setdefault("failure", "no_band")
        records.append(record)
        meshes.append(mesh)
        profiles.append(profile)
    timings.update(layers_s=time.perf_counter() - started, layers_cpu_s=time.process_time() - cpu)

    native_records, native_meshes = [], []
    started, cpu = time.perf_counter(), time.process_time()
    if contour_method:
        for index, token_regions in sorted(by_layer.items()):
            try:
                regions, diagnostics, partition_errors = partition_materials(token_regions, precision=CONFIG.precision,
                                                                              seam_tolerance=SEAM_TOLERANCE)
                errors.extend(f"Layer {index}: {error}" for error in partition_errors)
                for token, region in sorted(regions.items()):
                    mesh = make_mesh(region, z0 + index * height + Z_GAP / 2,
                                     z0 + (index + 1) * height - Z_GAP / 2)
                    if mesh is None:
                        errors.append(f"Layer {index}/{token}: no native band")
                        continue
                    # This check is mandatory in the current contour CLI.
                    checks = _float32_mesh_checks(mesh)
                    if not checks["is_volume"] or checks["zero_area_triangles"] or checks["duplicate_faces"]:
                        errors.append(f"Layer {index}/{token}: native intrinsic checks failed")
                    native_records.append({"name": f"layer{index:05d}__PAT_{token}__", "layer_index": index,
                                           "token": token, "partition": diagnostics, "intrinsic_float32": checks})
                    native_meshes.append(mesh)
            except Exception as exc:
                errors.append(f"Layer {index} assembly: {type(exc).__name__}: {exc}")
    else:
        labeled = [(f"part{record['part_index']}__layer{record['layer_index']}__PAT_{record['token']}__", mesh)
                   for record, mesh in zip(records, meshes) if mesh is not None]
        grouped = group_by_color(labeled, merge=True, verbose=False)
        # Returned names are labels such as all_cyan; obtain tokens by matching
        # the same actual grouping call on each token, without redoing geometry.
        from layerloom.tokens import COLOR_OBJECT_LABELS
        labels_to_tokens = {label: token for token, label in COLOR_OBJECT_LABELS.items()}
        for name, mesh in grouped:
            native_records.append({"name": name, "layer_index": None, "token": labels_to_tokens[name]})
            native_meshes.append(mesh)
    timings.update(assembly_s=time.perf_counter() - started, assembly_cpu_s=time.process_time() - cpu)
    timings["matched_part_bands_s"] = None if optimized else timings["repair_s"] + timings["layers_s"]
    timings["core_s"] = timings["repair_s"] + timings["layers_s"] + timings["assembly_s"]
    timings["core_cpu_s"] = sum(timings[name] for name in ("repair_cpu_s", "layers_cpu_s", "assembly_cpu_s"))

    # Stable exact geometry fingerprints are deliberately outside timed work.
    for record, mesh, profile in zip(records, meshes, profiles):
        record.update(geometry_sha256=_mesh_hash(mesh), triangles=0 if mesh is None else len(mesh.faces),
                      profile_sha256=None if profile is None else hashlib.sha256(profile.region.wkb).hexdigest())
        if record.get("failure"):
            errors.append(f"Part {record['part_index']}, layer {record['layer_index']}: {record.get('exception', record['failure'])}")
    for record, mesh in zip(native_records, native_meshes):
        record.update(geometry_sha256=_mesh_hash(mesh), triangles=len(mesh.faces))
    band_hash = None if optimized else _json_hash([[r["part_index"], r["layer_index"], r["token"], r["geometry_sha256"]] for r in records])
    profile_hash = None if not contour_method else _json_hash([
        [r["part_index"], r["layer_index"], r["token"], r.get("profile", {}).get("ok"), r["profile_sha256"]]
        for r in records])
    native_hash = _json_hash([[r["name"], r["layer_index"], r["token"], r["geometry_sha256"]] for r in native_records])
    if repair.get("fallback_to_raw"):
        warnings.append(repair["rejection_reason"])
    if repair.get("skipped_filters"):
        warnings.append("Legacy helper skipped unavailable/failed filters; see repair.skipped_filters")
    dependency_versions = {}
    for name in ("pymeshlab", "manifold3d", "scipy"):
        try:
            dependency_versions[name] = version(name)
        except PackageNotFoundError:
            dependency_versions[name] = None
    report = {"case": case, "height_mm": height, "method": method, "source": source,
              "timings": timings, "repair": repair, "bands": records, "native_parts": native_records,
              "baseline_planar": baseline_provenance,
              "execution_plan": {
                  "source_stage": "production profile extraction; source extrusion deferred" if optimized else "source-part mesh bands",
                  "native_stage": ("material partition and one final extrusion with mandatory intrinsic checks" if optimized else
                                   "material partition and second extrusion with mandatory intrinsic checks" if contour_method else
                                   "group_by_color mesh concatenation"),
                  "source_meshes_expected": not optimized,
                  "final_geometry_identity": "native_parts_sha256 includes canonical name, layer, token, vertices, and faces",
              },
              "timing_description": {
                  "layers_s": "profile extraction only; no source meshes created" if optimized else "source-part band creation",
                  "matched_part_bands_s": "not applicable: source meshes intentionally deferred" if optimized else "repair_s plus layers_s",
                  "core_s": "repair_s plus layers_s plus assembly_s; complete final geometry work",
              },
              "settings": {"z0_mm": z0, "z_gap_mm": Z_GAP, **asdict(CONFIG), "seam_tolerance": SEAM_TOLERANCE,
                           "grid": "original input part bounds, original global z0; same nominal intervals for every method",
                           "gap_policy": "both ends of every nominal band trimmed by 0.0005 mm, including outer boundaries",
                           "assembly": ("contour_optimized: profile-only source stage, partition_materials, one final extrusion with native intrinsic checks" if optimized else
                                        "old: actual group_by_color concatenation; contour: partition_materials and second extrusion with native intrinsic checks")},
              "versions": {"python": platform.python_version(), "numpy": np.__version__, "trimesh": trimesh.__version__,
                           "shapely": shapely.__version__, "geos": shapely.geos_version_string, **dependency_versions},
              "counts": {"source_parts": len(parts), "source_triangles": sum(len(mesh.faces) for _, mesh in parts),
                         "source_vertices": sum(len(mesh.vertices) for _, mesh in parts), "nominal_bands": len(records),
                         "produced_bands": sum(mesh is not None for mesh in meshes),
                         "produced_source_meshes": sum(mesh is not None for mesh in meshes),
                         "missing_bands": 0 if optimized else sum(mesh is None for mesh in meshes),
                         "source_profiles": sum(profile is not None for profile in profiles),
                         "nonempty_source_profiles": sum(profile is not None and profile.ok and not profile.region.is_empty for profile in profiles),
                         "empty_source_profiles": sum(profile is not None and profile.ok and profile.region.is_empty for profile in profiles),
                         "failed_source_profiles": sum("failure" in record for record in records) if contour_method else 0,
                         "deferred_source_extrusions": sum(record.get("extrusion_deferred", False) for record in records),
                         "exceptions": sum("exception" in record for record in records),
                         "failed_bands": sum("failure" in record for record in records),
                         "output_triangles": sum(record["triangles"] for record in records),
                         "native_parts": len(native_records), "native_triangles": sum(record["triangles"] for record in native_records)},
              "fingerprints": {"part_bands_sha256": band_hash, "native_parts_sha256": native_hash,
                               "source_profiles_sha256": profile_hash,
                               "combined_sha256": _json_hash([profile_hash if optimized else band_hash, native_hash])},
              "errors": errors, "warnings": warnings}
    report["audit"] = _audit(case, parts, records, meshes, native_records, native_meshes, height, z0) if audit else {"enabled": False}
    timings["audit_s"] = report["audit"].get("audit_s", 0.0)
    report["quality_status"] = "not_audited" if not audit else ("passed" if report["audit"]["summary"]["passes_declared_checks"] else "failed")
    report["status"] = "ok" if not errors and (not audit or report["audit"]["summary"]["passes_declared_checks"]) else "geometry_failure"
    return report


def _json_default(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(type(value).__name__)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, required=True)
    parser.add_argument("--height", type=float, required=True)
    parser.add_argument("--method", choices=METHODS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit", choices=("full", "none"), default="full")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--baseline-planar", type=Path,
                        help="Archived standalone planar_bands.py used by the unoptimized contour arm")
    args = parser.parse_args(argv)
    result = run_case(args.case, args.height, args.method, args.audit == "full", input_path=args.input,
                      baseline_planar_path=args.baseline_planar)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, default=_json_default, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"case": args.case, "method": args.method, "status": result["status"],
                      "timings": result["timings"], "output": str(args.output)}))
    # Geometry failures are data; worker crashes alone are process failures.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
