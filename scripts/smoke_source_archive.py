"""Exercise the downloadable source ZIP through the real native launcher."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("distributions", type=Path)
    args = parser.parse_args()
    archives = list(args.distributions.glob("LayerLoom-*-source.zip"))
    if len(archives) != 1:
        raise RuntimeError("Expected one validated beginner source ZIP.")
    # Keep failures and diagnostics for CI artifacts/local inspection. Never
    # clean up a user's source checkout or normal application storage.
    root = Path(tempfile.mkdtemp(prefix="LayerLoom café archive "))
    home = Path(os.environ.get("LAYERLOOM_HOME", str(root / "private runtime")))
    with zipfile.ZipFile(archives[0]) as archive:
        archive.extractall(root / "extracted")
    sources = list((root / "extracted").iterdir())
    if len(sources) != 1 or not sources[0].is_dir():
        raise RuntimeError("The source ZIP must contain one application folder.")
    source = sources[0]
    unrelated = root / "unrelated working folder"
    unrelated.mkdir()
    # A stale repository environment must remain untouched by every run.
    (source / ".venv").mkdir()
    sentinel = source / ".venv" / "user-file.txt"
    sentinel.write_text("preserve this pre-existing environment", encoding="utf-8")
    if os.name == "nt":
        command = [os.environ["COMSPEC"], "/d", "/c", str(source / "Launch LayerLoom.bat"), "--doctor-only"]
    else:
        launcher = "Launch LayerLoom.command" if sys.platform == "darwin" else "Launch LayerLoom.sh"
        command = ["bash", str(source / launcher), "--doctor-only"]
    env = {**os.environ, "LAYERLOOM_HOME": str(home)}
    env.pop("LAYERLOOM_OFFLINE", None)
    print(f"Testing extracted download: {source}\nPrivate runtime: {home}", flush=True)
    subprocess.run(command, cwd=unrelated, env=env, stdin=subprocess.DEVNULL, check=True, timeout=900)
    ready = {path: path.read_bytes() for path in (home / "environments").rglob("ready.json")}
    if not ready:
        raise RuntimeError("First setup did not publish a ready environment.")
    env["LAYERLOOM_OFFLINE"] = "1"
    print("Reopening the downloaded installation with uv offline...", flush=True)
    subprocess.run(command, cwd=unrelated, env=env, stdin=subprocess.DEVNULL, check=True, timeout=180)
    assert ready == {path: path.read_bytes() for path in (home / "environments").rglob("ready.json")}
    assert sentinel.read_text(encoding="utf-8") == "preserve this pre-existing environment"
    print("PASS: downloaded ZIP setup, offline reuse, unrelated cwd and existing .venv preservation.", flush=True)


if __name__ == "__main__":
    main()
