#!/usr/bin/env python3
"""Build a locked Windows x64 app, portable ZIP and per-user Inno Setup installer."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import sys
import tomllib
from pathlib import Path

from build_macos import ROOT, run, source_identity, write_notices
from windows_native import audit_native_tree, concurrency_runtime


def find_iscc() -> Path:
    found = shutil.which("ISCC.exe")
    candidates = [Path(found)] if found else []
    for key in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(key):
            candidates.append(Path(os.environ[key]) / "Inno Setup 6/ISCC.exe")
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError("Inno Setup 6 is required on the build machine (not on users' PCs).")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/windows")
    args = parser.parse_args(argv)
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        parser.error("Build on native 64-bit Intel/AMD Windows.")
    expected = (ROOT / ".python-version").read_text().strip()
    if platform.python_version() != expected:
        parser.error(f"Use the locked environment with Python {expected}.")
    iscc = find_iscc()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    build = ROOT / "build/windows"
    stage = build / "dist"
    stage.mkdir(parents=True, exist_ok=True)
    from macos_icon import write_icon
    from PIL import Image
    source_icon = build / "LayerLoom.icns"
    write_icon(source_icon)
    icon = build / "LayerLoom.ico"
    with Image.open(source_icon) as image:
        image.save(icon, format="ICO", sizes=[(n, n) for n in (16, 32, 48, 64, 128, 256)])
    env = dict(os.environ, LAYERLOOM_BUILD_ICON=str(icon), PYINSTALLER_CONFIG_DIR=str(build / "cache"))
    for spec in ("worker.spec", "LayerLoom.spec"):
        run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", stage,
             "--workpath", build / "work", ROOT / "packaging/windows" / spec], env=env)
    app = stage / "LayerLoom"
    shutil.copytree(stage / "worker", app / "worker")
    write_notices(app / "THIRD_PARTY_NOTICES")
    (app / 'native-dependencies.json').write_text(json.dumps(audit_native_tree(app), indent=2), encoding='utf-8')
    (app / 'THIRD_PARTY_NOTICES/Microsoft-Visual-C++-Runtime.txt').write_text(
        'Microsoft Visual C++ runtime libraries\nCopyright Microsoft Corporation.\n\n'
        'concrt140.dll is copied unmodified from the Visual Studio x64 redistributable directory.\n'
        'Other Microsoft runtime DLLs are supplied with the Python and dependency wheels.\n'
        'Redistribution terms: https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution\n'
        'Local deployment: https://learn.microsoft.com/en-us/cpp/windows/deployment-in-visual-cpp\n'
        f'Builder source for concrt140.dll: {concurrency_runtime()}\n', encoding='utf-8')
    shutil.copy2(ROOT / "packaging/windows/Read Me First.html", app / "Read Me First.html")
    metadata = {
        "version": version, "architecture": platform.machine(), "platform": "windows",
        "minimum_windows_build": 19041, "build_windows": platform.version(),
        "python": expected, "pyinstaller": importlib.metadata.version("pyinstaller"),
        "runtime_versions": {n: importlib.metadata.version(n) for n in
                             ("PyQt5", "pyvista", "pyvistaqt", "vtk", "numpy", "trimesh", "lib3mf", "manifold3d")},
        "lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        "signing": "unsigned", **source_identity(ROOT),
        "native_dependency_audit": "passed; includes PE delay imports",
    }
    (app / "build-info.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    shutil.copy2(app / "build-info.json", output / "LayerLoom-windows-build-info.json")
    archive = Path(shutil.make_archive(str(output / f"LayerLoom-{version}-windows-x64-portable"),
                                       "zip", root_dir=stage, base_dir="LayerLoom"))
    run([iscc, f"/DAppVersion={version}", f"/DAppDir={app}", f"/DOutputDir={output}",
         f"/DIconFile={icon}", ROOT / "packaging/windows/LayerLoom.iss"])
    installer = output / f"LayerLoom-{version}-windows-x64-setup.exe"
    assert installer.is_file()
    for path in (archive, installer):
        path.with_suffix(path.suffix + ".sha256").write_text(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n", encoding="utf-8")
    print(f"Created {installer} and {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
