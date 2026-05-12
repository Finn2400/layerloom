#!/usr/bin/env python3
"""
Interactive editor for Figure-5-style LayerLoom mosaic panel landmarks.

Usage:
  python gamut_photo_analysis/edit_mosaic_landmarks.py \
    --image gamut_photo_analysis/LayerLoom_figure5.png \
    --manifest gamut_photo_analysis/LayerLoom_figure5__mosaic_analysis/layerloom_figure5_panel_landmarks_first_pass.csv

Drag any yellow landmark point. Press:
  s  save the CSV
  q  quit
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

import matplotlib

try:
    matplotlib.use("MacOSX")
except Exception:
    pass

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PIL import Image


LANDMARKS = ("V1", "V2", "V3", "M12", "M23", "M31")


def _resize_to_max_dim(image: Image.Image, max_dim: int) -> Image.Image:
    if max_dim <= 0:
        return image
    w, h = image.size
    scale = min(float(max_dim) / max(w, h), 1.0)
    if scale >= 1.0:
        return image
    return image.resize((int(round(w * scale)), int(round(h * scale))), Image.Resampling.LANCZOS)


def _parse_landmarks(text: str) -> dict[str, np.ndarray]:
    points: dict[str, np.ndarray] = {}
    for chunk in str(text or "").split(";"):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        label, coord = chunk.split(":", 1)
        bits = [b.strip() for b in coord.split(",")]
        if len(bits) != 2:
            continue
        points[label.strip().upper()] = np.array([float(bits[0]), float(bits[1])], dtype=float)
    missing = [label for label in LANDMARKS if label not in points]
    if missing:
        raise ValueError(f"Missing landmarks in manifest row: {', '.join(missing)}")
    return {label: points[label] for label in LANDMARKS}


def _format_landmarks(points: dict[str, np.ndarray]) -> str:
    return ";".join(f"{label}:{points[label][0]:.1f},{points[label][1]:.1f}" for label in LANDMARKS)


def _row_scale(row: pd.Series, display_max_dim: int) -> float:
    raw = str(row.get("coordinate_max_dim", "")).strip()
    if not raw:
        return 1.0
    try:
        ref = float(raw)
    except ValueError:
        return 1.0
    return float(display_max_dim) / ref if ref > 0 else 1.0


class LandmarkEditor:
    def __init__(self, image_path: Path, manifest_path: Path, display_max_dim: int, out_path: Optional[Path]) -> None:
        self.image_path = image_path
        self.manifest_path = manifest_path
        self.out_path = out_path or manifest_path
        self.display_max_dim = display_max_dim
        self.df = pd.read_csv(manifest_path, dtype=str).fillna("")
        if "landmarks" not in self.df.columns:
            raise ValueError("Manifest needs a landmarks column.")
        self.rows = [i for i, row in self.df.iterrows() if str(row.get("landmarks", "")).strip()]
        if not self.rows:
            raise ValueError("No rows with landmarks found.")

        img = _resize_to_max_dim(Image.open(image_path).convert("RGB"), display_max_dim)
        self.rgb = np.asarray(img)
        self.points_by_row: dict[int, dict[str, np.ndarray]] = {}
        for idx in self.rows:
            scale = _row_scale(self.df.loc[idx], display_max_dim)
            self.points_by_row[idx] = {k: v * scale for k, v in _parse_landmarks(self.df.loc[idx, "landmarks"]).items()}

        self.active: Optional[tuple[int, str]] = None
        self.dirty = False
        self.show_subgamut_seams = True
        self.fig, self.ax = plt.subplots(figsize=(16, 6))
        self.cid_press = self.fig.canvas.mpl_connect("button_press_event", self.on_press)
        self.cid_move = self.fig.canvas.mpl_connect("motion_notify_event", self.on_move)
        self.cid_release = self.fig.canvas.mpl_connect("button_release_event", self.on_release)
        self.cid_key = self.fig.canvas.mpl_connect("key_press_event", self.on_key)

    def draw(self) -> None:
        self.ax.clear()
        self.ax.imshow(self.rgb)
        for idx in self.rows:
            row = self.df.loc[idx]
            pts = self.points_by_row[idx]
            outer = np.vstack([pts["V1"], pts["V2"], pts["V3"], pts["V1"]])
            mids = np.vstack([pts["M12"], pts["M23"], pts["M31"], pts["M12"]])
            self.ax.plot(outer[:, 0], outer[:, 1], color="white", lw=2.4)
            self.ax.plot(outer[:, 0], outer[:, 1], color="black", lw=0.7)
            self.ax.plot(mids[:, 0], mids[:, 1], color="#00d7ff", lw=1.4)
            if self.show_subgamut_seams:
                seam_segments = (
                    ("V1", "M12"),
                    ("V1", "M31"),
                    ("V2", "M12"),
                    ("V2", "M23"),
                    ("V3", "M23"),
                    ("V3", "M31"),
                    ("M12", "M23"),
                    ("M23", "M31"),
                    ("M31", "M12"),
                )
                for a, b in seam_segments:
                    pa = pts[a]
                    pb = pts[b]
                    self.ax.plot([pa[0], pb[0]], [pa[1], pb[1]], color="#ff4d4d", lw=0.9, alpha=0.72)
            for label, point in pts.items():
                is_active = self.active == (idx, label)
                self.ax.scatter(
                    [point[0]],
                    [point[1]],
                    s=90 if is_active else 48,
                    color="#ff4d4d" if is_active else "#ffdd00",
                    edgecolor="black",
                    linewidth=0.8,
                    zorder=5,
                )
                self.ax.text(
                    point[0] + 4,
                    point[1],
                    label,
                    fontsize=7,
                    va="center",
                    color="black",
                    bbox=dict(facecolor="white", alpha=0.72, edgecolor="none", pad=0.6),
                )
            centroid = np.vstack(list(pts.values())).mean(axis=0)
            self.ax.text(
                centroid[0],
                centroid[1],
                str(row.get("triangle_name", idx)),
                ha="center",
                va="center",
                fontsize=8,
                color="black",
                bbox=dict(facecolor="white", alpha=0.65, edgecolor="none", pad=1),
            )
        status = "modified, press s to save" if self.dirty else "drag points, press s to save, t toggles seams, q quits"
        self.ax.set_title(f"{self.manifest_path.name}  |  {status}")
        self.ax.axis("off")
        self.fig.canvas.draw_idle()

    def nearest_point(self, x: float, y: float) -> Optional[tuple[int, str]]:
        best: Optional[tuple[float, int, str]] = None
        for idx in self.rows:
            for label, point in self.points_by_row[idx].items():
                dist = float(np.linalg.norm(point - np.array([x, y], dtype=float)))
                if best is None or dist < best[0]:
                    best = (dist, idx, label)
        if best is None or best[0] > 18:
            return None
        return best[1], best[2]

    def on_press(self, event) -> None:
        if event.inaxes != self.ax or event.xdata is None or event.ydata is None:
            return
        self.active = self.nearest_point(float(event.xdata), float(event.ydata))
        self.draw()

    def on_move(self, event) -> None:
        if self.active is None or event.inaxes != self.ax or event.xdata is None or event.ydata is None:
            return
        idx, label = self.active
        self.points_by_row[idx][label] = np.array([float(event.xdata), float(event.ydata)], dtype=float)
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
        elif event.key == "t":
            self.show_subgamut_seams = not self.show_subgamut_seams
            self.draw()

    def save(self) -> None:
        out = self.df.copy()
        for idx in self.rows:
            scale = _row_scale(out.loc[idx], self.display_max_dim)
            if scale == 0:
                scale = 1.0
            manifest_points = {k: v / scale for k, v in self.points_by_row[idx].items()}
            out.loc[idx, "landmarks"] = _format_landmarks(manifest_points)
            if not str(out.loc[idx].get("coordinate_max_dim", "")).strip():
                out.loc[idx, "coordinate_max_dim"] = str(self.display_max_dim)
        out.to_csv(self.out_path, index=False)
        self.df = out
        self.dirty = False
        print(f"[saved] {self.out_path}", flush=True)
        self.draw()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", help="Optional output CSV. Defaults to overwriting --manifest.")
    parser.add_argument("--display-max-dim", type=int, default=2400)
    args = parser.parse_args()

    editor = LandmarkEditor(
        Path(args.image).expanduser().resolve(),
        Path(args.manifest).expanduser().resolve(),
        args.display_max_dim,
        Path(args.out).expanduser().resolve() if args.out else None,
    )
    editor.draw()
    plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
