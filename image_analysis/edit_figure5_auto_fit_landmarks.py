#!/usr/bin/env python3
"""
Open the latest Figure 5 auto-fit landmark guesses in the interactive editor.

Drag points, press `s` to save, `t` to toggle derived sub-gamut seams, and
`q` to quit.
"""

from __future__ import annotations

from pathlib import Path

from edit_mosaic_landmarks import LandmarkEditor
import matplotlib.pyplot as plt


HERE = Path(__file__).resolve().parent
IMAGE = HERE / "LayerLoom_figure5.png"
MANIFEST = HERE / "LayerLoom_figure5__mosaic_analysis" / "layerloom_figure5_panel_landmarks_auto_fit.csv"


def main() -> int:
    editor = LandmarkEditor(IMAGE, MANIFEST, 2400, None)
    editor.draw()
    plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
