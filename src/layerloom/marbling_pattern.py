"""Generate print-oriented procedural marbling patterns."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image


SUMINAGASHI_COLORS = (
    "#c31996",  # magenta
    "#988694",  # muted violet
    "#ffdf00",  # yellow
    "#8da293",  # cool neutral
    "#3ac8dc",  # cyan
    "#a9957b",  # warm neutral
    "#db685a",  # red
    "#88d184",  # green
    "#678dc4",  # blue
)


def _rgb(hex_color: str) -> np.ndarray:
    value = hex_color.lstrip("#")
    return np.asarray(
        [int(value[index : index + 2], 16) for index in (0, 2, 4)],
        dtype=np.uint8,
    )


def _periodic_delta(values: np.ndarray, center: float) -> np.ndarray:
    return (values - center + 0.5) % 1.0 - 0.5


def generate_suminagashi_tile(
    output: Path,
    *,
    size: int = 1200,
    seed: int = 731,
) -> Path:
    """Generate broad, seamless nested contours suitable for a small print."""
    if size < 128:
        raise ValueError("size must be at least 128")

    rng = np.random.default_rng(seed)
    axis = np.arange(size, dtype=np.float64) / size
    x, y = np.meshgrid(axis, axis)

    # Periodic flow bends otherwise concentric contours without introducing a
    # seam when the image is wrapped around the model.
    flow_x = (
        0.045 * np.sin(2.0 * np.pi * y)
        + 0.018 * np.sin(4.0 * np.pi * (x + y))
        + 0.010 * np.sin(8.0 * np.pi * y + 0.7)
    )
    flow_y = (
        0.036 * np.sin(2.0 * np.pi * x + 0.9)
        + 0.014 * np.sin(6.0 * np.pi * (x - y))
    )
    warped_x = (x + flow_x) % 1.0
    warped_y = (y + flow_y) % 1.0

    centers = (
        (0.50, 0.50, 0.78, 1.18, 0.000),
        (0.14, 0.76, 1.05, 0.82, 0.035),
        (0.84, 0.15, 0.92, 1.08, 0.055),
    )
    distance_fields = []
    for index, (cx, cy, x_scale, y_scale, offset) in enumerate(centers):
        dx = _periodic_delta(warped_x, cx) * x_scale
        dy = _periodic_delta(warped_y, cy) * y_scale
        angle = np.arctan2(dy, dx)
        radius = np.hypot(dx, dy)
        ripple = (
            0.012 * np.sin((4 + index) * angle + 2.0 * np.pi * y)
            + 0.006 * np.sin(9.0 * angle - 2.0 * np.pi * x)
        )
        distance_fields.append(radius + ripple + offset)

    stack = np.stack(distance_fields)
    smoothness = 28.0
    scalar = -np.log(np.sum(np.exp(-smoothness * stack), axis=0)) / smoothness
    scalar -= float(np.min(scalar))

    # A slow phase drift keeps bands organic while retaining continuous,
    # unmistakable nested lanes.
    phase = scalar + 0.008 * np.sin(2.0 * np.pi * (x + 2.0 * y))
    band_width = 0.050
    bands = np.floor(np.maximum(phase, 0.0) / band_width).astype(np.int64)
    bands = (bands + rng.integers(0, len(SUMINAGASHI_COLORS))) % len(SUMINAGASHI_COLORS)

    palette = np.asarray([_rgb(color) for color in SUMINAGASHI_COLORS], dtype=np.uint8)
    image = Image.fromarray(palette[bands], mode="RGB")
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--size", type=int, default=1200)
    parser.add_argument("--seed", type=int, default=731)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    output = generate_suminagashi_tile(
        Path(args.output).expanduser().resolve(),
        size=args.size,
        seed=args.seed,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
