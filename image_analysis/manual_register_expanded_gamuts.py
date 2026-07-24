#!/usr/bin/env python3
"""Manually register expanded-gamut triangle photos.

This helper is meant for cases where the physical triangle edge is ambiguous
because bright yellow or white plastic blends into the light-box background, or
because the side wall is easier to detect than the front gamut face. It opens
each selected image with three draggable points initialized from the current
automatic estimate:

    bottom-left corner, top corner, bottom-right corner

The resulting CSV can be passed to ``analyze_expanded_gamut_photos.py`` with
``--corner-overrides`` or placed at
``expanded_gamut_analysis/manual_registration_overrides.csv``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageTk

HERE = Path(__file__).resolve().parent
LAYERLOOM_DIR = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(LAYERLOOM_DIR) not in sys.path:
    sys.path.insert(0, str(LAYERLOOM_DIR))

import analyze_expanded_gamut_photos as analysis  # noqa: E402


ROLES = ("bottom_left", "top", "bottom_right")


def _load_existing(path: Path) -> pd.DataFrame:
    if path.exists():
        return pd.read_csv(path)
    return pd.DataFrame(
        columns=[
            "analysis_id",
            "filename",
            "corner_label",
            "condition",
            "bottom_left_x",
            "bottom_left_y",
            "top_x",
            "top_y",
            "bottom_right_x",
            "bottom_right_y",
            "image_width",
            "image_height",
            "max_analysis_dim",
        ]
    )


def _upsert_override(path: Path, row: pd.Series, points: dict[str, np.ndarray], width: int, height: int, max_dim: int) -> None:
    df = _load_existing(path)
    new_row = {
        "analysis_id": row.analysis_id,
        "filename": row.filename,
        "corner_label": row.corner_label,
        "condition": row.condition,
        "bottom_left_x": float(points["bottom_left"][0]),
        "bottom_left_y": float(points["bottom_left"][1]),
        "top_x": float(points["top"][0]),
        "top_y": float(points["top"][1]),
        "bottom_right_x": float(points["bottom_right"][0]),
        "bottom_right_y": float(points["bottom_right"][1]),
        "image_width": int(width),
        "image_height": int(height),
        "max_analysis_dim": int(max_dim),
    }
    if "analysis_id" in df.columns and (df["analysis_id"] == row.analysis_id).any():
        for key, value in new_row.items():
            df.loc[df["analysis_id"] == row.analysis_id, key] = value
    else:
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def _existing_points(df: pd.DataFrame, analysis_id: str) -> dict[str, np.ndarray] | None:
    if df.empty or "analysis_id" not in df.columns:
        return None
    hit = df[df["analysis_id"] == analysis_id]
    if hit.empty:
        return None
    r = hit.iloc[-1]
    return {
        "bottom_left": np.array([float(r.bottom_left_x), float(r.bottom_left_y)], dtype=float),
        "top": np.array([float(r.top_x), float(r.top_y)], dtype=float),
        "bottom_right": np.array([float(r.bottom_right_x), float(r.bottom_right_y)], dtype=float),
    }


def _draw_points(canvas, points: dict[str, np.ndarray], scale: float, *, color: str, prefix: str, width: int = 3) -> None:
    if not points:
        return
    ordered = [points[role] * scale for role in ROLES]
    closed = ordered + [ordered[0]]
    for a, b in zip(closed[:-1], closed[1:]):
        canvas.create_line(a[0], a[1], b[0], b[1], fill=color, width=width)
    for role, point in zip(ROLES, ordered):
        x, y = float(point[0]), float(point[1])
        canvas.create_oval(x - 6, y - 6, x + 6, y + 6, fill=color, outline="black", width=1)
        canvas.create_text(x + 8, y, text=f"{prefix}{role}", anchor="w", fill=color, font=("Helvetica", 12, "bold"))


def _fallback_points(width: int, height: int) -> dict[str, np.ndarray]:
    return {
        "bottom_left": np.array([0.25 * width, 0.84 * height], dtype=float),
        "top": np.array([0.50 * width, 0.12 * height], dtype=float),
        "bottom_right": np.array([0.76 * width, 0.84 * height], dtype=float),
    }


def _copy_points(points: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {role: np.asarray(points[role], dtype=float).copy() for role in ROLES}


def _manual_register_one(row: pd.Series, args: argparse.Namespace, existing_df: pd.DataFrame) -> str:
    try:
        import tkinter as tk
    except Exception as exc:  # pragma: no cover - depends on local GUI runtime
        raise RuntimeError("Tkinter is required for manual registration.") from exc

    rgb, _scale = analysis._load_image(Path(row.source_path), args.max_analysis_dim)
    height, width = rgb.shape[:2]
    rgb8 = np.clip(rgb * 255, 0, 255).astype(np.uint8)
    pil = Image.fromarray(rgb8, mode="RGB")
    display_scale = min(1.0, args.display_max_width / width, args.display_max_height / height)
    display_size = (int(round(width * display_scale)), int(round(height * display_scale)))
    display = pil.resize(display_size, Image.Resampling.LANCZOS)

    auto_points = None
    try:
        auto_corners, _reg, _mask, _verts = analysis.detect_registration(rgb, str(row.corner_label))
        auto_points = {
            "bottom_left": np.asarray(auto_corners["C"], dtype=float),
            "top": np.asarray(auto_corners["Y"], dtype=float),
            "bottom_right": np.asarray(auto_corners["M"], dtype=float),
        }
    except Exception:
        auto_points = None
    existing = _existing_points(existing_df, str(row.analysis_id))
    editable_points = _copy_points(existing or auto_points or _fallback_points(width, height))

    root = tk.Tk()
    root.title(f"Manual registration: {row.condition} {row.corner_label} {row.filename}")
    photo = ImageTk.PhotoImage(display)
    status = tk.StringVar()
    done = tk.StringVar(value="")
    drag_state = {"role": ""}

    instructions = (
        "Drag the green points to the true front-face corners. "
        "Enter = save, S = skip, R = reset to automatic, E = reset to previous manual, Q/Esc = quit. "
        "Gray = automatic estimate; red = previous manual override."
    )
    tk.Label(root, text=f"{row.condition} {row.corner_label} {row.filename}", font=("Helvetica", 15, "bold")).pack(anchor="w", padx=10, pady=(8, 2))
    tk.Label(root, text=instructions, justify="left").pack(anchor="w", padx=10, pady=(0, 6))
    canvas = tk.Canvas(root, width=display_size[0], height=display_size[1], bg="white")
    canvas.pack(padx=10, pady=4)
    canvas.create_image(0, 0, image=photo, anchor="nw")
    if auto_points is not None:
        _draw_points(canvas, auto_points, display_scale, color="#777777", prefix="auto:", width=2)
    if existing is not None:
        _draw_points(canvas, existing, display_scale, color="#d62728", prefix="manual:", width=3)
    tk.Label(root, textvariable=status, font=("Helvetica", 12)).pack(anchor="w", padx=10, pady=(4, 10))

    def refresh_status() -> None:
        coords = ", ".join(
            f"{role}=({editable_points[role][0]:.1f},{editable_points[role][1]:.1f})"
            for role in ROLES
        )
        status.set(f"Drag vertices, then press Enter to save. {coords}")

    def redraw_selected() -> None:
        canvas.delete("selected")
        ordered = [editable_points[role] * display_scale for role in ROLES]
        closed = ordered + [ordered[0]]
        for a, b in zip(closed[:-1], closed[1:]):
            canvas.create_line(a[0], a[1], b[0], b[1], fill="#00aa55", width=3, tags="selected")
        role_colors = {
            "bottom_left": "#00c4ff",
            "top": "#f1d400",
            "bottom_right": "#ff4fb5",
        }
        for role, point in zip(ROLES, ordered):
            x, y = float(point[0]), float(point[1])
            color = role_colors.get(role, "#00aa55")
            canvas.create_oval(x - 10, y - 10, x + 10, y + 10, fill=color, outline="black", width=2, tags=("selected", role))
            canvas.create_text(x + 12, y, text=role, anchor="w", fill=color, font=("Helvetica", 13, "bold"), tags=("selected", role))

    def nearest_role(event, max_distance: float = 32.0) -> str:
        click = np.array([float(event.x), float(event.y)], dtype=float)
        distances = {
            role: float(np.linalg.norm(editable_points[role] * display_scale - click))
            for role in ROLES
        }
        role, distance = min(distances.items(), key=lambda item: item[1])
        return role if distance <= max_distance else ""

    def on_press(event) -> None:
        drag_state["role"] = nearest_role(event)

    def on_drag(event) -> None:
        role = drag_state["role"]
        if not role:
            return
        x = min(max(event.x / display_scale, 0.0), width - 1.0)
        y = min(max(event.y / display_scale, 0.0), height - 1.0)
        editable_points[role] = np.array([x, y], dtype=float)
        redraw_selected()
        refresh_status()

    def on_release(_event) -> None:
        drag_state["role"] = ""

    def on_double_click(event) -> None:
        role = nearest_role(event, max_distance=10_000.0)
        if not role:
            return
        editable_points[role] = np.array([event.x / display_scale, event.y / display_scale], dtype=float)
        redraw_selected()
        refresh_status()

    def on_key(event) -> None:
        key = event.keysym.lower()
        if key in {"return", "enter"}:
            done.set("save")
        elif key == "r":
            if auto_points is not None:
                editable_points.update(_copy_points(auto_points))
                redraw_selected()
                refresh_status()
        elif key == "e":
            if existing is not None:
                editable_points.update(_copy_points(existing))
                redraw_selected()
                refresh_status()
        elif key == "s":
            done.set("skip")
        elif key in {"q", "escape"}:
            done.set("quit")

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    canvas.bind("<Double-Button-1>", on_double_click)
    root.bind("<Key>", on_key)
    root.protocol("WM_DELETE_WINDOW", lambda: done.set("quit"))
    redraw_selected()
    refresh_status()
    root.wait_variable(done)
    result = done.get()
    root.destroy()

    if result == "save":
        _upsert_override(args.out_csv, row, editable_points, width, height, args.max_analysis_dim)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Click-register ambiguous expanded-gamut triangle photos.")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=analysis.LAYERLOOM_DIR / "LayerLoomPhotos100NCZ_5" / "centered_4x3_tif",
    )
    parser.add_argument(
        "--out-csv",
        type=Path,
        default=analysis.LAYERLOOM_DIR / "LayerLoomPhotos100NCZ_5" / "expanded_gamut_analysis" / "manual_registration_overrides.csv",
    )
    parser.add_argument("--max-analysis-dim", type=int, default=1800)
    parser.add_argument("--display-max-width", type=int, default=1400)
    parser.add_argument("--display-max-height", type=int, default=950)
    parser.add_argument(
        "--anchors",
        type=str,
        default="w",
        help=(
            "Comma-separated anchor tokens to review by default. "
            "Use 'w,y' for white/yellow cases or empty with --all to review everything."
        ),
    )
    parser.add_argument("--labels", type=str, default="", help="Optional comma-separated corner labels.")
    parser.add_argument("--conditions", type=str, default="", help="Optional comma-separated conditions.")
    parser.add_argument("--all", action="store_true", help="Review all selected gamuts, not just labels containing white.")
    parser.add_argument("--skip-existing", action="store_true", help="Skip rows already present in the override CSV.")
    args = parser.parse_args(argv)

    manifest = analysis.build_manifest(args.input_dir)
    if not args.all:
        anchors = {x.strip().lower() for x in args.anchors.split(",") if x.strip()}
        if anchors:
            manifest = manifest[
                manifest["corner_label"].astype(str).map(lambda label: bool(set(label) & anchors))
            ].copy()
    if args.labels.strip():
        labels = {x.strip().lower() for x in args.labels.split(",") if x.strip()}
        manifest = manifest[manifest["corner_label"].isin(labels)].copy()
    if args.conditions.strip():
        conditions = {x.strip().lower() for x in args.conditions.split(",") if x.strip()}
        manifest = manifest[manifest["condition"].isin(conditions)].copy()
    existing_df = _load_existing(args.out_csv)
    if args.skip_existing and not existing_df.empty and "analysis_id" in existing_df.columns:
        done_ids = set(existing_df["analysis_id"].astype(str))
        manifest = manifest[~manifest["analysis_id"].astype(str).isin(done_ids)].copy()
    if manifest.empty:
        print("[manual-register] no rows selected.")
        return 0

    print(f"[manual-register] selected {len(manifest)} image(s)")
    print(f"[manual-register] writing overrides to {args.out_csv}")
    for row in manifest.itertuples(index=False):
        result = _manual_register_one(pd.Series(row._asdict()), args, existing_df)
        existing_df = _load_existing(args.out_csv)
        if result == "quit":
            print("[manual-register] stopped by user.")
            break
        if result == "save":
            print(f"[manual-register] saved {row.analysis_id}")
        elif result == "skip":
            print(f"[manual-register] skipped {row.analysis_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
