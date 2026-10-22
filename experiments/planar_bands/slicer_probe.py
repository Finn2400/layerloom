"""Reproducible local PrusaSlicer geometry-consumption smoke probe."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
import zipfile

import trimesh

from layerloom.contour_repair import reconstruct_file, _package_native_volumes
from layerloom.export import write_basic_3mf
from layerloom.ingest import read_parts


def inspect_gcode(path):
    if not path.exists():
        return {}
    text = path.read_text()
    z_values = [float(z) for z in re.findall(r"^;Z:([\d.]+)$", text, re.MULTILINE)]
    layers = []
    current = None
    for line in text.splitlines():
        if line.startswith(";Z:"):
            current = {"z": float(line[3:]), "xy_extrusion_moves": 0}
            layers.append(current)
        elif current and line.startswith("G1 ") and re.search(r" [XY]-?[\d.]", line) and re.search(r" E[\d.]", line):
            current["xy_extrusion_moves"] += 1
    return {
        "bytes": path.stat().st_size,
        "layer_z_mm": z_values,
        "layer_count": len(z_values),
        "layers_with_xy_extrusion": sum(layer["xy_extrusion_moves"] > 0 for layer in layers),
        "layers": layers,
        "tool_commands": re.findall(r"^T\d+.*$", text, re.MULTILINE),
    }


def main():
    out = Path(__file__).parent / "results" / "slicer_native"
    out.mkdir(parents=True, exist_ok=True)
    source = out / "open_top_cube.3mf"
    cube = trimesh.creation.box(extents=(10, 10, 2))
    cube.apply_translation((0, 0, 1))
    cube.update_faces(cube.face_normals[:, 2] < 0.9)
    write_basic_3mf([("open_top_cube", cube)], str(source))
    binary = "/Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer"
    help_result = subprocess.run([binary, "--help"], capture_output=True, text=True)
    (out / "help.txt").write_text(help_result.stdout + help_result.stderr)
    results = {
        "slicer": help_result.stdout.splitlines()[0],
        "purpose": "Geometry consumption only; no printer hardware or multicolor profile was used.",
        "expected_cube_layers": 10,
        "expected_cube_z_mm": [round((i + 1) * 0.2, 3) for i in range(10)],
        "color_limit": "Native volume PAT names do not assign physical extruders in this generic single-extruder profile.",
        "cases": [],
    }
    paths = []
    for run in ("c", "cmy"):
        path = out / f"open_top_{run}.3mf"
        log = io.StringIO()
        with redirect_stdout(log):
            report = reconstruct_file(source, path, layer_height=0.2, run=run)
        (out / f"reconstruct_{run}.log").write_text(log.getvalue())
        if report["status"] != "ok":
            raise RuntimeError(report["errors"])
        paths.append((run, path, 10))
    tutorial = out.parent / "tutorial_cubes.3mf"
    if tutorial.exists():
        assembled_tutorial = out / "tutorial_cubes_assembly.3mf"
        shutil.copyfile(tutorial, assembled_tutorial)
        with zipfile.ZipFile(assembled_tutorial) as archive:
            already_native = "Metadata/Slic3r_PE_model.config" in archive.namelist()
        if not already_native:
            _package_native_volumes(assembled_tutorial)
        paths.append(("tutorial_cubes", assembled_tutorial, 40))
    for name, path, expected_layers in list(paths):
        geometry_path = out / f"{name}_geometry_only.stl"
        trimesh.util.concatenate([mesh for _, mesh in read_parts([str(path)])]).export(geometry_path)
        paths.append((f"{name}_geometry_only", geometry_path, expected_layers))
    for name, path, expected_layers in paths:
        for merge in (False, True):
            case_name = f"{name}_{'merged' if merge else 'direct'}"
            output = out / f"{case_name}.gcode"
            cmd = [binary, "--export-gcode", "--dont-arrange", "--no-ensure-on-bed",
                   "--no-variable-layer-height", "--layer-height", "0.2", "--first-layer-height", "0.2",
                   "--no-binary-gcode", "--nozzle-diameter", "0.4", "--filament-diameter", "1.75",
                   "--skirts", "0", "--datadir", str(out / "isolated_config"),
                   "--save", str(out / f"{case_name}.ini"), "--output", str(output)]
            if merge:
                cmd.append("--merge")
            cmd.append(str(path))
            output.unlink(missing_ok=True)
            started = time.perf_counter()
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            elapsed = time.perf_counter() - started
            (out / f"{case_name}.log").write_text(proc.stdout + proc.stderr)
            entry = {"name": case_name, "command": cmd, "returncode": proc.returncode,
                     "elapsed_s": elapsed, "expected_layers": expected_layers,
                     **inspect_gcode(output)}
            entry["retained_expected_layer_count"] = (
                proc.returncode == 0
                and entry.get("layer_count") == expected_layers
                and entry.get("layers_with_xy_extrusion") == expected_layers
            )
            results["cases"].append(entry)
    target = out.parent / "slicer_validation.json"
    old = out.parent / "slicer_validation_generic_assembly.json"
    if target.exists() and not old.exists():
        target.rename(old)
    target.write_text(json.dumps(results, indent=2) + "\n")
    failures = [case["name"] for case in results["cases"]
                if not case["retained_expected_layer_count"]]
    print(json.dumps({"report": str(target), "cases": len(results["cases"]),
                      "failed_cases": failures}, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
