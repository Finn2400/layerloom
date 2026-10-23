from pathlib import Path

import numpy as np
from PIL import Image

from layerloom.marbling_pattern import SUMINAGASHI_COLORS, generate_suminagashi_tile


def test_suminagashi_tile_uses_exact_palette_and_is_nearly_seamless(tmp_path: Path):
    output = generate_suminagashi_tile(tmp_path / "pattern.png", size=256)
    image = np.asarray(Image.open(output).convert("RGB"))

    colors = np.unique(image.reshape(-1, 3), axis=0)
    assert len(colors) == len(SUMINAGASHI_COLORS)

    horizontal_seam_changes = np.mean(np.any(image[:, 0] != image[:, -1], axis=1))
    vertical_seam_changes = np.mean(np.any(image[0] != image[-1], axis=1))
    assert horizontal_seam_changes < 0.15
    assert vertical_seam_changes < 0.15
