#!/usr/bin/env python3
"""Validate a relocated frozen app with no source tree or Python on PATH.

This is automated acceptance evidence. Browser quarantine, Finder approval and a
novice's first-use trial remain manual release gates; this script does not waive them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import struct
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from build_macos import MINIMUM, macho_files


def validate_3mf(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None, f"Corrupt 3MF archive: {path}"
        models = [name for name in archive.namelist() if name.lower().endswith(".model")]
        assert models, "3MF contains no model"
        triangles = 0
        objects = 0
        for name in models:
            root = ET.fromstring(archive.read(name))
            objects += len(root.findall(".//{*}object"))
            triangles += len(root.findall(".//{*}triangle"))
        assert objects and triangles, f"Empty 3MF: {path}"
        return {"objects": objects, "triangles": triangles, "bytes": path.stat().st_size}


def write_test_glb(path: Path):
    positions = struct.pack("<12f", 0, 0, 0, 10, 0, 0, 0, 10, 0, 0, 0, 10)
    indices = struct.pack("<12H", 0, 2, 1, 0, 1, 3, 1, 2, 3, 2, 0, 3)
    colors = bytes([0, 255, 255, 255] * 4)
    blob = positions + indices + colors
    document = {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}], "buffers": [{"byteLength": len(blob)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
            {"buffer": 0, "byteOffset": len(positions), "byteLength": len(indices)},
            {"buffer": 0, "byteOffset": len(positions) + len(indices), "byteLength": len(colors)},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3", "min": [0, 0, 0], "max": [10, 10, 10]},
            {"bufferView": 1, "componentType": 5123, "count": 12, "type": "SCALAR"},
            {"bufferView": 2, "componentType": 5121, "count": 4, "type": "VEC4", "normalized": True},
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "COLOR_0": 2}, "indices": 1}]}],
    }
    header = json.dumps(document, separators=(",", ":")).encode()
    header += b" " * (-len(header) % 4)
    blob += b"\x00" * (-len(blob) % 4)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(header) + 8 + len(blob))
                     + struct.pack("<I4s", len(header), b"JSON") + header
                     + struct.pack("<I4s", len(blob), b"BIN\x00") + blob)


def command(command, *, cwd, env, log, timeout=300):
    # Stream to disk so diagnostics survive TimeoutExpired or a hung GUI.
    started = time.monotonic()
    proc, timed_out = None, False
    try:
        with log.open("w", encoding="utf-8") as stream:
            proc = subprocess.run([str(arg) for arg in command], cwd=cwd, env=env,
                                  stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        raise
    finally:
        log.with_suffix(".timing.json").write_text(json.dumps({
            "monotonic_started": started, "elapsed_seconds": round(time.monotonic() - started, 3),
            "timeout_seconds": timeout, "timed_out": timed_out,
            "returncode": proc.returncode if proc is not None else None,
        }, indent=2) + "\n", encoding="utf-8")
    output = log.read_text(encoding="utf-8", errors="replace")
    if proc.returncode:
        raise RuntimeError(f"Command exited {proc.returncode}: {command}\n{output[-12000:]}")
    return output


def deployment_versions(output: str) -> list[tuple[int, ...]]:
    versions = []
    for block in re.split(r"Load command \d+", output):
        if "cmd LC_BUILD_VERSION" in block:
            match = re.search(r"\bminos ([\d.]+)", block)
        elif "cmd LC_VERSION_MIN_MACOSX" in block:
            match = re.search(r"\bversion ([\d.]+)", block)
        else:
            continue
        if match:
            versions.append(tuple(int(part) for part in match[1].split(".")))
    return versions


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--gui", action="store_true", help="Also render and weave in a real Qt window")
    args = parser.parse_args(argv)
    report_dir = args.report.resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    timings = []
    def mark(stage):
        now = time.monotonic()
        timings.append({"stage": stage, "monotonic_seconds": now,
                        "elapsed_seconds": round(now - started, 3)})
        (report_dir / "validation-stages.json").write_text(json.dumps(timings, indent=2) + "\n")
    mark("native_checks_started")
    with (args.app / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    arch = info["LayerLoomArchitecture"]
    assert arch == platform.machine(), f"This app is for {arch}, this Mac is {platform.machine()}"
    assert info["LSMinimumSystemVersion"] == MINIMUM[arch]
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", args.app], check=True)
    target = tuple(int(part) for part in MINIMUM[arch].split("."))
    native_count = 0
    for path in macho_files(args.app):
        native_count += 1
        subprocess.run(["/usr/bin/lipo", path, "-verify_arch", arch], check=True)
        dependencies = subprocess.check_output(["/usr/bin/otool", "-L", path], text=True)
        for line in dependencies.splitlines()[1:]:
            library = line.strip().split(" (", 1)[0]
            if library.startswith("/"):
                assert library.startswith(("/usr/lib/", "/System/Library/")), f"External native dependency: {path} -> {library}"
        native = subprocess.check_output(["/usr/bin/otool", "-l", path], text=True)
        for minimum in deployment_versions(native):
            assert (minimum + (0, 0, 0))[:3] <= (target + (0, 0, 0))[:3], f"{path} needs macOS {minimum}, later than {target}"
    assert native_count > 10, "App is missing bundled native libraries"
    for path in args.app.rglob("*"):
        assert not any(part.startswith(("submission_", "manuscript_", "LayerLoomPhotos", "color_calibrated_TIF")) for part in path.parts)
        assert "pymeshlab" not in path.parts and "_tkinter" not in path.name
    mark("native_checks_finished")
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "QT_PLUGIN_PATH", "DYLD_LIBRARY_PATH"):
        env.pop(key, None)
    env.update(PATH="/usr/bin:/bin", PYTHONNOUSERSITE="1",
               LAYERLOOM_LOG_DIR=str(report_dir / "logs"), MPLCONFIGDIR=str(report_dir / "matplotlib"))
    report = {"architecture": arch, "os": platform.mac_ver()[0], "native_binaries": native_count,
              "manual_quarantine_test": "not performed by automation", "status": "running", "timings": timings}
    try:
        with tempfile.TemporaryDirectory(prefix="LayerLoom relocated é ") as directory:
            relocated = Path(directory) / "LayerLoom.app"
            shutil.copytree(args.app, relocated, symlinks=True)
            mark("application_relocated")
            work = Path(directory) / "unrelated working directory"
            work.mkdir()
            helper = relocated / "Contents/Helpers/LayerLoomWorker.app/Contents/MacOS/layerloom-worker"
            capabilities = json.loads(command([helper, "--capabilities"], cwd=work, env=env, log=report_dir / "capabilities.log"))
            mark("worker_capabilities_completed")
            assert capabilities["repair"] is False, "First desktop bundle must not include PyMeshLab"
            examples = relocated / "Contents/Resources/layerloom/examples"
            original = examples / "tutorial_cmy_cubes.3mf"
            original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
            output = work / "cubes_woven.3mf"
            command([helper, "weave", "-i", original, "-o", output, "--step", "0.16", "--no-export-stls"],
                    cwd=work, env=env, log=report_dir / "weave.log")
            report["woven"] = validate_3mf(output)
            mark("worker_weave_validated")
            assert hashlib.sha256(original.read_bytes()).hexdigest() == original_hash
            glb = work / "colored-tetrahedron.glb"
            write_test_glb(glb)
            imported = work / "glb-import.3mf"
            command([helper, "glb-split", glb, "--scale", "1", "--out", imported], cwd=work, env=env, log=report_dir / "glb.log")
            report["glb_import"] = validate_3mf(imported)
            mark("glb_import_validated")
            if args.gui:
                executable = relocated / "Contents/MacOS/LayerLoom"
                mark("gui_started")
                command([executable, "--desktop-smoke-test", report_dir], cwd=work, env=env,
                        log=report_dir / "gui.log", timeout=900)
                mark("gui_process_finished")
                gui = json.loads((report_dir / "gui-smoke.json").read_text())
                assert gui["status"] == "passed" and gui["frozen"], gui
                report["gui"] = gui
            report["status"] = "passed"
            mark("validation_passed")
    except Exception as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        mark("validation_failed")
        raise
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        (report_dir / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
