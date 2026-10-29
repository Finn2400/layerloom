#!/usr/bin/env python3
"""Build a self-contained, ad-hoc signed Mac app and DMG using free tools.

Run in the locked desktop environment for the native architecture, selecting
wheels for its declared minimum macOS version. A newer build host still requires
native-binary checks and acceptance tests on the minimum supported OS. Cross
compilation and universal binaries are deliberately refused.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MINIMUM = {"arm64": "14.0", "x86_64": "15.0"}
MACHO_MAGICS = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}


def run(command, *, env=None):
    print("+", " ".join(map(str, command)), flush=True)
    return subprocess.run(list(map(str, command)), check=True, env=env)


def macho_files(app: Path):
    for path in sorted(app.rglob("*")):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                if stream.read(4) in MACHO_MAGICS:
                    yield path


def is_license_file(filename: Path) -> bool:
    text_suffixes = {"", ".txt", ".md", ".rst", ".html", ".license", ".bsd", ".mit", ".gpl", ".lgpl", ".lesser", ".rtf"}
    return filename.suffix.lower() in text_suffixes and any(
        word in filename.name.lower() for word in ("license", "copying", "copyright", "notice")
    )


def write_notices(destination: Path):
    """Preserve package-provided licenses, including native dependency notices."""
    destination.mkdir(parents=True)
    shutil.copy2(ROOT / "LICENSE", destination / "LayerLoom-LICENSE.txt")
    records = []
    for distribution in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = distribution.metadata["Name"]
        if name.lower() in {"pip", "pytest", "build", "twine", "pyinstaller-hooks-contrib"}:
            continue
        metadata = distribution.metadata
        record = {
            "name": name, "version": distribution.version,
            "license": metadata.get("License-Expression") or metadata.get("License", ""),
            "project_urls": metadata.get_all("Project-URL", []),
            "homepage": metadata.get("Home-page", ""), "license_files": [],
        }
        for file in distribution.files or ():
            filename = Path(file)
            if is_license_file(filename):
                original = Path(distribution.locate_file(file))
                if original.is_file():
                    # Keep the full distribution-relative path to prevent collisions.
                    relative = Path(name) / Path(*[part for part in file.parts if part not in {"..", "."}])
                    target = destination / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, target)
                    record["license_files"].append(str(relative))
        (destination / f"{name}-METADATA.txt").write_text(distribution.read_text("METADATA") or json.dumps(dict(metadata)), encoding="utf-8")
        records.append(record)
    python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError(f"Python runtime license missing: {python_license}")
    shutil.copy2(python_license, destination / "Python-LICENSE.txt")
    # PyQt5's wheel metadata declares GPLv3 but does not contain its license text.
    shutil.copy2(ROOT / "packaging/macos/GPL-3.0.txt", destination / "PyQt5-GPL-3.0.txt")
    (destination / "packages.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    (destination / "README.txt").write_text(
        "LayerLoom desktop third-party notices\n\n"
        "LayerLoom source is MIT licensed. This combined desktop distribution includes\n"
        "PyQt5 under GPLv3; its distribution and corresponding source obligations apply.\n"
        "Qt libraries include their LGPL/GPL terms; consult the included license files.\n"
        "The package metadata, license texts and versions are included in this directory.\n"
        "LayerLoom source and build instructions: https://github.com/Finn2400/layerloom\n"
        "PyQt5 source: https://pypi.org/project/PyQt5/#files\n"
        "Qt source: https://download.qt.io/archive/qt/\n"
        "Python source: https://www.python.org/downloads/source/\n"
        "A source archive for this LayerLoom release accompanies the DMG.\n"
        "Redistributors must preserve notices and provide all required corresponding source.\n",
        encoding="utf-8",
    )


def sign_app(app: Path):
    # Sign inside-out after the independent helper and all resources are assembled.
    for binary in macho_files(app):
        if binary.parent.name == "MacOS" and binary.parent.parent.name == "Contents":
            continue  # Sign each enclosing application after its frameworks.
        run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", binary])
    nested = [p for p in app.rglob("*") if p.is_dir() and not p.is_symlink() and p.suffix in {".framework", ".app"}]
    for bundle in sorted(nested, key=lambda p: len(p.parts), reverse=True) + [app]:
        run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", bundle])
    run(["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", app])


def source_identity(root: Path) -> dict:
    """Identify the exact checkout used for release artifacts without requiring Git."""
    identity = {"source_revision": None, "source_tree": None, "source_dirty": None}
    if (root / ".git").exists():
        try:
            def git(*arguments):
                return subprocess.check_output(
                    ["git", *arguments], cwd=root, text=True, stderr=subprocess.DEVNULL
                ).strip()
            identity.update(
                source_revision=git("rev-parse", "HEAD"),
                source_tree=git("rev-parse", "HEAD^{tree}"),
                source_dirty=bool(git("status", "--porcelain", "--untracked-files=normal")),
            )
        except (OSError, subprocess.CalledProcessError):
            pass  # A source archive or machine without Git can still build.
    if os.environ.get("GITHUB_ACTIONS") == "true":
        identity["workflow_revision"] = os.environ.get("GITHUB_SHA")
        identity["workflow_run_id"] = os.environ.get("GITHUB_RUN_ID")
    return identity


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/macos")
    parser.add_argument("--skip-dmg", action="store_true", help="Build the signed application only")
    parser.add_argument("--incremental", action="store_true", help="Reuse PyInstaller caches for local iteration; releases default to clean builds")
    args = parser.parse_args(argv)
    if sys.platform != "darwin" or platform.machine() not in MINIMUM:
        parser.error("Build on a native arm64 or x86_64 Mac; cross-compilation is unsupported.")
    arch = platform.machine()
    expected_python = (ROOT / ".python-version").read_text().strip()
    if platform.python_version() != expected_python:
        parser.error(f"Use the locked desktop environment with Python {expected_python}.")
    import tomllib
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    build = ROOT / "build/macos" / arch
    stage = build / "dist"
    # PyInstaller owns this exact build directory; it never shares source/user outputs.
    stage.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        LAYERLOOM_BUILD_ARCH=arch, LAYERLOOM_BUILD_VERSION=version,
        MACOSX_DEPLOYMENT_TARGET=MINIMUM[arch],
        PYINSTALLER_CONFIG_DIR=str(build / "pyinstaller-cache"),
        PYINSTALLER_STRICT_BUNDLE_CODESIGN_ERROR="1",
    )
    for spec in ("worker.spec", "LayerLoom.spec"):
        run([sys.executable, "-m", "PyInstaller", "--noconfirm", *([] if args.incremental else ["--clean"]), "--distpath", stage,
             "--workpath", build / "work", ROOT / "packaging/macos" / spec], env=env)
    app = stage / "LayerLoom.app"
    helpers = app / "Contents/Helpers"
    helpers.mkdir()
    shutil.copytree(stage / "LayerLoomWorker.app", helpers / "LayerLoomWorker.app", symlinks=True)
    write_notices(app / "Contents/Resources/THIRD_PARTY_NOTICES")
    # This summary is an auditable identity, not a claim that manual QA passed.
    build_info = {
        "version": version, "architecture": arch, "minimum_macos": MINIMUM[arch],
        "build_macos": platform.mac_ver()[0],
        "python": expected_python, "pyinstaller": importlib.metadata.version("pyinstaller"),
        "lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        "signing": "ad-hoc", "notarized": False,
        **source_identity(ROOT),
    }
    (app / "Contents/Resources/build-info.json").write_text(json.dumps(build_info, indent=2) + "\n")
    sign_app(app)
    final_app = output / "LayerLoom.app"
    if final_app.exists():
        # Never remove an arbitrary user folder supplied as output.
        marker = final_app / "Contents/Resources/build-info.json"
        if not marker.is_file():
            raise RuntimeError(f"Refusing to replace an unrecognized application: {final_app}")
        shutil.rmtree(final_app)
    shutil.copytree(app, final_app, symlinks=True)
    if not args.skip_dmg:
        dmg_stage = build / "dmg"
        if dmg_stage.exists():
            shutil.rmtree(dmg_stage)
        dmg_stage.mkdir()
        shutil.copytree(final_app, dmg_stage / "LayerLoom.app", symlinks=True)
        (dmg_stage / "Applications").symlink_to("/Applications")
        shutil.copy2(ROOT / "packaging/macos/Read Me First.html", dmg_stage / "Read Me First.html")
        shutil.copy2(ROOT / "docs/images/mac-first-open.svg", dmg_stage / "mac-first-open.svg")
        dmg = output / f"LayerLoom-{version}-macos-{arch}.dmg"
        run(["/usr/bin/hdiutil", "create", "-volname", f"LayerLoom {version}", "-srcfolder", dmg_stage,
             "-ov", "-format", "UDZO", dmg])
        digest = hashlib.sha256(dmg.read_bytes()).hexdigest()
        dmg.with_suffix(".dmg.sha256").write_text(f"{digest}  {dmg.name}\n")
        print(f"Created {dmg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
