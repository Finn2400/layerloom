#!/usr/bin/env python3
"""
Interactive C/M/Y corner picker for one photographed LayerLoom gamut triangle.

The displayed image matches the coordinate system used by
analyze_printed_gamut.py: optional auto-crop, then optional max-dimension
resize. Drag C/M/Y points, press `s` to save, `q` to quit.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

import matplotlib

for _backend in ("MacOSX", "Qt5Agg", "TkAgg"):
    try:
        matplotlib.use(_backend, force=True)
        break
    except Exception:
        continue

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PIL import Image


LABELS = ("C", "M", "Y")


def _parse_manual_corners(text: str) -> dict[str, np.ndarray]:
    raw = [chunk.strip() for chunk in str(text or "").split(";") if chunk.strip()]
    if len(raw) != 3:
        raise ValueError("corners must contain exactly three points")
    parsed: dict[str, np.ndarray] = {}
    unlabeled: list[np.ndarray] = []
    for chunk in raw:
        label = None
        coord_text = chunk
        if ":" in chunk:
            label_text, coord_text = chunk.split(":", 1)
            label = label_text.strip().upper()
        bits = [b.strip() for b in coord_text.split(",")]
        if len(bits) != 2:
            raise ValueError(f"Bad corner coordinate: {chunk!r}")
        point = np.array([float(bits[0]), float(bits[1])], dtype=float)
        if label:
            parsed[label] = point
        else:
            unlabeled.append(point)
    if parsed and unlabeled:
        raise ValueError("Use either all labeled or all unlabeled corners")
    if parsed:
        missing = [label for label in LABELS if label not in parsed]
        if missing:
            raise ValueError(f"Labeled corners must include {', '.join(missing)}")
        return {label: parsed[label] for label in LABELS}
    return {label: point for label, point in zip(LABELS, unlabeled)}


def _parse_crop(text: Optional[str]) -> Optional[tuple[int, int, int, int]]:
    if not text:
        return None
    vals = [int(float(part.strip())) for part in text.split(",")]
    if len(vals) != 4:
        raise ValueError("--crop expects x0,y0,x1,y1")
    x0, y0, x1, y1 = vals
    if x1 <= x0 or y1 <= y0:
        raise ValueError("--crop must satisfy x1 > x0 and y1 > y0")
    return x0, y0, x1, y1


def _apply_crop(rgb: np.ndarray, crop: Optional[tuple[int, int, int, int]]) -> np.ndarray:
    if crop is None:
        return rgb.copy()
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = crop
    x0 = max(0, min(w, x0))
    x1 = max(0, min(w, x1))
    y0 = max(0, min(h, y0))
    y1 = max(0, min(h, y1))
    if x1 <= x0 or y1 <= y0:
        raise ValueError("Crop falls outside the image")
    return rgb[y0:y1, x0:x1].copy()


def _resize_for_analysis(rgb: np.ndarray, max_dim: int) -> tuple[np.ndarray, float]:
    if max_dim <= 0:
        return rgb, 1.0
    h, w = rgb.shape[:2]
    largest = max(h, w)
    if largest <= max_dim:
        return rgb, 1.0
    scale = float(max_dim) / float(largest)
    image = Image.fromarray(np.clip(rgb * 255.0, 0, 255).astype(np.uint8), mode="RGB")
    resized = image.resize((max(int(round(w * scale)), 1), max(int(round(h * scale)), 1)), Image.Resampling.BILINEAR)
    return np.asarray(resized, dtype=np.float32) / 255.0, scale


def _rgb_to_hsv(rgb: np.ndarray) -> np.ndarray:
    arr = np.asarray(rgb, dtype=float)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    mx = np.max(arr, axis=-1)
    mn = np.min(arr, axis=-1)
    diff = mx - mn
    h = np.zeros_like(mx)
    mask = diff > 1e-12
    rmask = mask & (mx == r)
    gmask = mask & (mx == g)
    bmask = mask & (mx == b)
    h[rmask] = ((g[rmask] - b[rmask]) / diff[rmask]) % 6.0
    h[gmask] = (b[gmask] - r[gmask]) / diff[gmask] + 2.0
    h[bmask] = (r[bmask] - g[bmask]) / diff[bmask] + 4.0
    h = h / 6.0
    s = np.zeros_like(mx)
    np.divide(diff, mx, out=s, where=mx > 1e-12)
    return np.stack([h, s, mx], axis=-1)


def _auto_crop_from_color(
    rgb: np.ndarray,
    *,
    min_saturation: float,
    min_value: float,
    max_value: float,
    pad_px: int,
) -> Optional[tuple[int, int, int, int]]:
    hsv = _rgb_to_hsv(rgb)
    mask = (hsv[:, :, 1] >= min_saturation) & (hsv[:, :, 2] >= min_value) & (hsv[:, :, 2] <= max_value)
    yy, xx = np.nonzero(mask)
    if len(xx) == 0:
        return None
    h, w = rgb.shape[:2]
    pad = max(int(pad_px), 0)
    return max(int(xx.min()) - pad, 0), max(int(yy.min()) - pad, 0), min(int(xx.max()) + pad + 1, w), min(int(yy.max()) + pad + 1, h)


def _cross_2d(o: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    return float((a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]))


def _convex_hull_points(points_xy: np.ndarray) -> np.ndarray:
    pts = np.unique(np.asarray(points_xy, dtype=float), axis=0)
    pts = pts[np.lexsort((pts[:, 1], pts[:, 0]))]
    if len(pts) <= 2:
        return pts
    lower = []
    for p in pts:
        while len(lower) >= 2 and _cross_2d(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and _cross_2d(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return np.asarray(lower[:-1] + upper[:-1], dtype=float)


def _auto_seed_corners(rgb: np.ndarray) -> dict[str, np.ndarray]:
    hsv = _rgb_to_hsv(rgb)
    mask = (hsv[:, :, 1] >= 0.10) & (hsv[:, :, 2] >= 0.08) & (hsv[:, :, 2] <= 0.98)
    yy, xx = np.nonzero(mask)
    if len(xx) < 3:
        h, w = rgb.shape[:2]
        return {
            "C": np.array([0.08 * w, 0.90 * h], dtype=float),
            "M": np.array([0.92 * w, 0.90 * h], dtype=float),
            "Y": np.array([0.50 * w, 0.08 * h], dtype=float),
        }
    hull = _convex_hull_points(np.column_stack([xx, yy]).astype(float))
    if len(hull) > 180:
        idx = np.linspace(0, len(hull) - 1, 180).round().astype(int)
        hull = hull[idx]
    best = None
    best_area = -1.0
    for i in range(len(hull) - 2):
        for j in range(i + 1, len(hull) - 1):
            for k in range(j + 1, len(hull)):
                area = abs(_cross_2d(hull[i], hull[j], hull[k]))
                if area > best_area:
                    best_area = area
                    best = np.array([hull[i], hull[j], hull[k]], dtype=float)
    assert best is not None
    # Geometric seed: top point is Y; bottom-left is C; bottom-right is M.
    top_idx = int(np.argmin(best[:, 1]))
    remaining = [idx for idx in range(3) if idx != top_idx]
    c_idx, m_idx = sorted(remaining, key=lambda idx: best[idx, 0])
    return {"C": best[c_idx], "M": best[m_idx], "Y": best[top_idx]}


def _format_corners(points: dict[str, np.ndarray]) -> str:
    return ";".join(f"{label}:{points[label][0]:.1f},{points[label][1]:.1f}" for label in LABELS)


def _load_seed(seed: Optional[str], rgb: np.ndarray) -> dict[str, np.ndarray]:
    if seed:
        path = Path(seed).expanduser()
        if path.exists():
            df = pd.read_csv(path, dtype=str).fillna("")
            if "corners" in df.columns and len(df):
                return _parse_manual_corners(str(df.iloc[0]["corners"]))
        return _parse_manual_corners(seed)

    return _auto_seed_corners(rgb)


class CornerEditor:
    def __init__(
        self,
        *,
        image_path: Path,
        out_csv: Path,
        target_name: str,
        preset: str,
        max_analysis_dim: int,
        crop_text: Optional[str],
        no_auto_crop: bool,
        auto_crop_pad: int,
        seed: Optional[str],
    ) -> None:
        self.image_path = image_path
        self.out_csv = out_csv
        self.target_name = target_name
        self.preset = preset
        self.max_analysis_dim = max_analysis_dim
        self.crop_text = crop_text
        self.no_auto_crop = no_auto_crop
        self.auto_crop_pad = auto_crop_pad

        rgb_full = np.asarray(Image.open(image_path).convert("RGB"), dtype=np.float32) / 255.0
        requested_crop = _parse_crop(crop_text)
        auto_crop = None
        if requested_crop is None and not no_auto_crop:
            auto_crop = _auto_crop_from_color(
                rgb_full,
                min_saturation=0.10,
                min_value=0.08,
                max_value=0.98,
                pad_px=auto_crop_pad,
            )
        self.crop = requested_crop if requested_crop is not None else auto_crop
        cropped = _apply_crop(rgb_full, self.crop)
        self.rgb, self.analysis_scale = _resize_for_analysis(cropped, max_analysis_dim)
        self.points = _load_seed(seed, self.rgb)
        self.active: Optional[str] = None
        self.dirty = False

        self.fig, self.ax = plt.subplots(figsize=(9, 8))
        self.fig.canvas.mpl_connect("button_press_event", self.on_press)
        self.fig.canvas.mpl_connect("motion_notify_event", self.on_move)
        self.fig.canvas.mpl_connect("button_release_event", self.on_release)
        self.fig.canvas.mpl_connect("key_press_event", self.on_key)

    def draw(self) -> None:
        self.ax.clear()
        self.ax.imshow(self.rgb)
        poly = np.vstack([self.points[label] for label in LABELS] + [self.points["C"]])
        self.ax.plot(poly[:, 0], poly[:, 1], color="white", lw=2.4)
        self.ax.plot(poly[:, 0], poly[:, 1], color="black", lw=0.8)
        colors = {"C": "#00b7ff", "M": "#ff00cc", "Y": "#ffe600"}
        for label, point in self.points.items():
            is_active = label == self.active
            self.ax.scatter(
                [point[0]],
                [point[1]],
                s=130 if is_active else 78,
                color="#ff4d4d" if is_active else colors[label],
                edgecolor="black",
                linewidth=0.9,
                zorder=5,
            )
            self.ax.text(
                point[0] + 5,
                point[1],
                label,
                va="center",
                fontsize=11,
                weight="bold",
                color="black",
                bbox=dict(facecolor="white", alpha=0.75, edgecolor="none", pad=1.2),
            )
        status = "modified, press s to save" if self.dirty else "drag C/M/Y corners, press s to save, q to quit"
        self.ax.set_title(f"{self.target_name} | {status}")
        self.ax.axis("off")
        self.fig.canvas.draw_idle()

    def nearest(self, x: float, y: float) -> Optional[str]:
        best_label = None
        best_dist = float("inf")
        target = np.array([x, y], dtype=float)
        for label, point in self.points.items():
            dist = float(np.linalg.norm(point - target))
            if dist < best_dist:
                best_label = label
                best_dist = dist
        return best_label if best_dist <= 30 else None

    def on_press(self, event) -> None:
        if event.inaxes != self.ax or event.xdata is None or event.ydata is None:
            return
        self.active = self.nearest(float(event.xdata), float(event.ydata))
        self.draw()

    def on_move(self, event) -> None:
        if self.active is None or event.inaxes != self.ax or event.xdata is None or event.ydata is None:
            return
        self.points[self.active] = np.array([float(event.xdata), float(event.ydata)], dtype=float)
        self.dirty = True
        self.draw()

    def on_release(self, _event) -> None:
        self.active = None
        self.draw()

    def on_key(self, event) -> None:
        if event.key == "s":
            self.save()
        elif event.key == "q":
            plt.close(self.fig)

    def save(self) -> None:
        self.out_csv.parent.mkdir(parents=True, exist_ok=True)
        corners = _format_corners(self.points)
        row = {
            "image": str(self.image_path),
            "target_name": self.target_name,
            "preset": self.preset,
            "corners": corners,
            "max_analysis_dim": self.max_analysis_dim,
            "crop": self.crop_text or "",
            "no_auto_crop": bool(self.no_auto_crop),
            "auto_crop_pad": self.auto_crop_pad,
            "applied_crop": json.dumps(self.crop),
            "analysis_scale": self.analysis_scale,
            "analysis_command": (
                f"python gamut_photo_analysis/analyze_printed_gamut.py --image {self.image_path} "
                f"--target-name {self.target_name} --preset {self.preset} --corners '{corners}' "
                f"--max-analysis-dim {self.max_analysis_dim}"
            ),
        }
        pd.DataFrame([row]).to_csv(self.out_csv, index=False)
        self.dirty = False
        print(f"[saved] {self.out_csv}")
        print(row["analysis_command"])
        self.draw()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--out-csv", required=True)
    parser.add_argument("--target-name", default="single_gamut")
    parser.add_argument("--preset", default="simple", choices=["simple", "normal", "full"])
    parser.add_argument("--max-analysis-dim", type=int, default=2400)
    parser.add_argument("--crop")
    parser.add_argument("--no-auto-crop", action="store_true")
    parser.add_argument("--auto-crop-pad", type=int, default=35)
    parser.add_argument("--seed", help="Optional corners string or CSV written by this script.")
    args = parser.parse_args()

    editor = CornerEditor(
        image_path=Path(args.image).expanduser().resolve(),
        out_csv=Path(args.out_csv).expanduser().resolve(),
        target_name=args.target_name,
        preset=args.preset,
        max_analysis_dim=args.max_analysis_dim,
        crop_text=args.crop,
        no_auto_crop=args.no_auto_crop,
        auto_crop_pad=args.auto_crop_pad,
        seed=args.seed,
    )
    editor.draw()
    plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
