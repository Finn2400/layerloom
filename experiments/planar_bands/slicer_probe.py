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
    """Count linear XY moves with positive extrusion, using Marlin modes.

    G90/G91 also reset the E mode; a subsequent M82/M83 overrides it. Unknown
    absolute starting coordinates cannot establish displacement until a move
    or G92 supplies them. This is a probe for the generated linear G-code,
    not a general interpreter for arcs, macros, or printer-specific commands.
    See https://marlinfw.org/docs/gcode/M082.html for mode precedence.
    """
    if not path.exists():
        return {}
    text = path.read_text()
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
    word_pattern = re.compile(rf"([A-Z])\s*({number})")
    layer_pattern = re.compile(rf"^\s*;Z:\s*({number})\s*$")
    layers = []
    current = None
    position = dict.fromkeys("XYZE")
    xyz_relative = e_relative = False
    for line in text.splitlines():
        layer_match = layer_pattern.fullmatch(line)
        if layer_match:
            current = {"z": float(layer_match[1]), "xy_extrusion_moves": 0}
            layers.append(current)
            continue
        code = re.sub(r"\([^)]*\)", "", line.split(";", 1)[0]).upper()
        words = word_pattern.findall(code)
        if words and words[0][0] == "N":
            words = words[1:]
        if not words:
            continue
        command = (words[0][0], float(words[0][1]))
        parameters = {axis: float(value) for axis, value in words[1:]}
        if command in {("G", 90), ("G", 91)}:
            xyz_relative = e_relative = command[1] == 91
            continue
        if command in {("M", 82), ("M", 83)}:
            e_relative = command[1] == 83
            continue
        if command == ("G", 92):
            for axis in position.keys() & parameters.keys():
                position[axis] = parameters[axis]
            continue
        if command == ("G", 28):
            # Homing positions depend on the printer configuration. Forget
            # them conservatively instead of assuming every axis homes at 0.
            position.update(dict.fromkeys("XYZ"))
            continue
        if command not in {("G", 0), ("G", 1)}:
            continue
        delta = {}
        for axis in position.keys() & parameters.keys():
            value, previous = parameters[axis], position[axis]
            relative = e_relative if axis == "E" else xyz_relative
            delta[axis] = value if relative else value - previous if previous is not None else None
            position[axis] = (previous + value if previous is not None else None) if relative else value
        xy_moves = any(delta.get(axis) is not None and delta[axis] != 0 for axis in "XY")
        extrudes = delta.get("E") is not None and delta["E"] > 0
        if current is not None and xy_moves and extrudes:
            current["xy_extrusion_moves"] += 1
    z_values = [layer["z"] for layer in layers]
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
