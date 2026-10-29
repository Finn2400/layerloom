#!/usr/bin/env python3
"""Choose hosted Mac acceptance jobs without implying unrun macOS 14 coverage."""
from __future__ import annotations

import json
import os
from pathlib import Path


def validation_matrix(*, include_macos14: bool = False) -> dict:
    targets = [
        {"runner": "macos-15", "arch": "arm64"},
        {"runner": "macos-26", "arch": "arm64"},
        {"runner": "macos-15-intel", "arch": "x86_64"},
        {"runner": "macos-26-intel", "arch": "x86_64"},
    ]
    if include_macos14:
        targets.insert(0, {"runner": "macos-14", "arch": "arm64"})
    return {"include": targets}


def main() -> None:
    enabled = os.environ.get("LAYERLOOM_VALIDATE_MACOS14", "false").lower() == "true"
    matrix = validation_matrix(include_macos14=enabled)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
        stream.write("matrix=" + json.dumps(matrix, separators=(",", ":")) + "\n")
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
        stream.write("### macOS 14 acceptance coverage\n\n")
        if enabled:
            stream.write("The exact ARM artifact will also run on macos-14. This optional hosted runner retires November 2, 2026.\n")
        else:
            stream.write("**macOS 14 runtime acceptance is NOT performed by this workflow.** Before public release, validate this exact ARM artifact on a local macOS 14 Mac and record its SHA-256, OS, and GUI results. Static deployment checks and newer-OS tests do not replace that gate.\n")


if __name__ == "__main__":
    main()
