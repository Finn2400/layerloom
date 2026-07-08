"""Headless GLB-to-woven-3MF orchestration."""

from __future__ import annotations

import argparse
import math
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from layerloom.transform_3mf import compute_transform_plan, write_transformed_3mf


DEFAULT_FIT_BOX = (180.0, 180.0)
DEFAULT_FIT_DIAGONAL_CAP = 200.0
DEFAULT_LAYER_HEIGHT = 0.08


@dataclass(frozen=True)
class HeadlessConfig:
    input_path: str
    output: Optional[str] = None
    output_dir: Optional[str] = None
    overwrite: bool = False
    palette: str = "Normal"
    glb_target_colors: int = 20
    source_scale: float = 0.1
    fit_box: Optional[tuple[float, float]] = DEFAULT_FIT_BOX
    max_dim: Optional[float] = None
    max_diagonal: Optional[float] = None
    no_upscale: bool = False
    orientation_quality: str = "balanced"
    repair: bool = True
    strict_repair: bool = False
    keep_intermediates: bool = False
    stl_out: Optional[str] = None
    layer_height: float = DEFAULT_LAYER_HEIGHT
    missing_color_mode: str = "group"
    verbose: bool = False


@dataclass(frozen=True)
class HeadlessResult:
    output_path: str
    intermediate_dir: Optional[str]
    orientation_score: float
    orientation_evaluated: int
    scale: float


def _safe_stem(path: str) -> str:
    stem = Path(path).stem
    return re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "layerloom_model"


def default_downloads_dir() -> str:
    return os.path.join(str(Path.home()), "Downloads")


def resolve_output_path(input_path: str, *, output: Optional[str] = None, output_dir: Optional[str] = None) -> str:
    if output:
        return os.path.abspath(os.path.expanduser(output))
    out_dir = os.path.abspath(os.path.expanduser(output_dir or default_downloads_dir()))
    return os.path.join(out_dir, f"{_safe_stem(input_path)}_woven.3mf")


def parse_fit_box(value: str) -> tuple[float, float]:
    text = str(value or "").lower().replace("x", ",")
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("fit box must be W,H, for example 180,180")
    try:
        width, depth = float(parts[0]), float(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("fit box dimensions must be numbers") from exc
    if width <= 0 or depth <= 0:
        raise argparse.ArgumentTypeError("fit box dimensions must be > 0")
    return width, depth


def _sizing_mode_count(config: HeadlessConfig) -> int:
    return sum(value is not None for value in (config.fit_box, config.max_dim, config.max_diagonal))


def _target_colors_to_levels(target_colors: int) -> int:
    target_colors = max(2, int(target_colors))
    return max(2, int(math.ceil(target_colors ** (1.0 / 3.0))))


def _support_script(name: str) -> str:
    root = Path(__file__).resolve().parents[1]
    return str(root / "supporting_scripts" / name)


def _run_external_tool(label: str, cmd: list[str], *, verbose: bool = False) -> None:
    if verbose:
        print(f"[{label}] running: {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if verbose and proc.stdout:
        print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
    if verbose and proc.stderr:
        print(proc.stderr, end="" if proc.stderr.endswith("\n") else "\n", file=sys.stderr)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"{label} failed with exit code {proc.returncode}" + (f":\n{detail}" if detail else ""))


def import_glb_like_gui(
    glb_path: str,
    *,
    split_3mf: str,
    manifest_out: str,
    repaired_3mf: str,
    target_colors: int,
    source_scale: float,
    missing_color_mode: str,
    repair: bool,
    strict_repair: bool,
    verbose: bool,
) -> tuple[str, Optional[str]]:
    from layerloom.repair import count_3mf_mesh_geometry, repair_rejection_reason

    split_script = _support_script("glb_split_by_vertexcolor_v3_3mf.py")
    repair_script = _support_script("3mf_meshlab_repair_3mf.py")
    if not os.path.isfile(split_script):
        raise RuntimeError(f"GLB split script not found: {split_script}")

    color_levels = _target_colors_to_levels(target_colors)
    split_cmd = [
        sys.executable,
        split_script,
        glb_path,
        "--scale",
        str(float(source_scale)),
        "--out",
        split_3mf,
        "--manifest-out",
        manifest_out,
        "--color-levels",
        str(color_levels),
    ]
    if missing_color_mode == "error":
        raise ValueError("--missing-color-mode=error is not supported by the GUI GLB splitter; use group or skip.")
    split_cmd += ["--no-color-mode", missing_color_mode]
    _run_external_tool("glb-split", split_cmd, verbose=verbose)

    if not repair:
        return split_3mf, None
    if not os.path.isfile(repair_script):
        msg = f"Repair script not found; continuing with unrepaired 3MF: {repair_script}"
        if strict_repair:
            raise RuntimeError(msg)
        return split_3mf, msg

    repair_cmd = [
        sys.executable,
        repair_script,
        split_3mf,
        "--out",
        repaired_3mf,
        "--quiet",
    ]
    try:
        _run_external_tool("3mf-repair", repair_cmd, verbose=verbose)
        before = count_3mf_mesh_geometry(split_3mf)
        after = count_3mf_mesh_geometry(repaired_3mf)
        reject = repair_rejection_reason(before, after)
        if reject:
            if strict_repair:
                raise RuntimeError(reject)
            return split_3mf, reject
        return repaired_3mf, None
    except Exception as exc:
        if strict_repair:
            raise
        return split_3mf, f"Repair failed; continuing with unrepaired 3MF: {exc}"


def compute_headless_placement_plan(
    input_3mf: str,
    *,
    orientation_matrix: np.ndarray,
    fit_box: Optional[tuple[float, float]] = DEFAULT_FIT_BOX,
    max_dim: Optional[float] = None,
    max_diagonal: Optional[float] = None,
    no_upscale: bool = False,
):
    probe = compute_transform_plan(
        input_3mf,
        scale=1.0,
        orientation_matrix=orientation_matrix,
        center_xy=False,
    )
    dims = np.asarray(probe.transformed_bounds.size, dtype=np.float64)
    scale = 1.0
    if fit_box is not None:
        scale = min(
            float(fit_box[0]) / max(float(dims[0]), 1e-9),
            float(fit_box[1]) / max(float(dims[1]), 1e-9),
            DEFAULT_FIT_DIAGONAL_CAP / max(float(np.linalg.norm(dims)), 1e-9),
        )
        target_xy = (float(fit_box[0]) * 0.5, float(fit_box[1]) * 0.5)
        plate_w, plate_d = float(fit_box[0]), float(fit_box[1])
    elif max_dim is not None:
        scale = float(max_dim) / max(float(np.max(dims)), 1e-9)
        target_xy = (0.0, 0.0)
        plate_w = plate_d = float(max_dim)
    elif max_diagonal is not None:
        scale = float(max_diagonal) / max(float(np.linalg.norm(dims)), 1e-9)
        target_xy = (0.0, 0.0)
        plate_w = plate_d = float(max_diagonal)
    else:
        target_xy = (0.0, 0.0)
        plate_w = plate_d = max(float(np.max(dims)), 1.0)

    if no_upscale:
        scale = min(1.0, scale)

    plan = compute_transform_plan(
        input_3mf,
        scale=scale,
        orientation_matrix=orientation_matrix,
        plate_width=plate_w,
        plate_depth=plate_d,
        center_xy=False,
        target_center_xy=target_xy,
    )
    return plan, float(scale)


def _ensure_output_ok(path: str, overwrite: bool) -> None:
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(path) and not overwrite:
        raise FileExistsError(f"Output already exists: {path}. Pass --overwrite to replace it.")


def _log(config: HeadlessConfig, message: str) -> None:
    if config.verbose:
        print(message)


def run_headless(config: HeadlessConfig) -> HeadlessResult:
    from layerloom.orientation import choose_orientation_for_3mf
    from layerloom.palette_assignment import assign_palette_to_3mf
    from layerloom.weave import weave_pipeline

    if _sizing_mode_count(config) != 1:
        raise ValueError("Choose exactly one sizing mode: --fit-box, --max-dim, or --max-diagonal.")
    input_path = os.path.abspath(os.path.expanduser(config.input_path))
    if not os.path.exists(input_path):
        raise FileNotFoundError(input_path)
    if Path(input_path).suffix.lower() not in {".glb", ".gltf"}:
        raise ValueError("Headless pipeline currently expects a .glb or .gltf input.")

    output_path = resolve_output_path(input_path, output=config.output, output_dir=config.output_dir)
    _ensure_output_ok(output_path, config.overwrite)

    temp_owner = None
    if config.keep_intermediates:
        work_dir = os.path.join(os.path.dirname(output_path), f"{_safe_stem(input_path)}_layerloom_headless_intermediates")
        os.makedirs(work_dir, exist_ok=True)
    else:
        temp_owner = tempfile.TemporaryDirectory(prefix="layerloom_headless_")
        work_dir = temp_owner.name

    try:
        stem = _safe_stem(input_path)
        split_3mf = os.path.join(work_dir, f"{stem}_split.3mf")
        split_manifest = os.path.join(work_dir, f"{stem}_split.colors.json")
        repaired_3mf = os.path.join(work_dir, f"{stem}_repaired.3mf")
        assigned_3mf = os.path.join(work_dir, f"{stem}_assigned.3mf")
        placed_3mf = os.path.join(work_dir, f"{stem}_placed.3mf")

        _log(config, "[headless] importing GLB colors with GUI support scripts")
        current_3mf, repair_warning = import_glb_like_gui(
            input_path,
            split_3mf=split_3mf,
            manifest_out=split_manifest,
            repaired_3mf=repaired_3mf,
            target_colors=config.glb_target_colors,
            source_scale=config.source_scale,
            missing_color_mode=config.missing_color_mode,
            repair=config.repair,
            strict_repair=config.strict_repair,
            verbose=config.verbose,
        )
        if repair_warning:
            print(f"[repair warning] {repair_warning}", file=sys.stderr)

        _log(config, "[headless] assigning LayerLoom palette tokens")
        assign_palette_to_3mf(current_3mf, assigned_3mf, palette=config.palette)

        _log(config, f"[headless] choosing orientation ({config.orientation_quality})")
        orientation = choose_orientation_for_3mf(assigned_3mf, quality=config.orientation_quality)

        _log(config, "[headless] fitting and placing model")
        plan, scale = compute_headless_placement_plan(
            assigned_3mf,
            orientation_matrix=orientation.matrix,
            fit_box=config.fit_box,
            max_dim=config.max_dim,
            max_diagonal=config.max_diagonal,
            no_upscale=config.no_upscale,
        )
        write_transformed_3mf(assigned_3mf, placed_3mf, plan.global_matrix)

        _log(config, "[headless] weaving final 3MF")
        weave_pipeline(
            placed_3mf,
            step=config.layer_height,
            stl_out=os.path.abspath(config.stl_out) if config.stl_out else None,
            verbose=config.verbose,
            output_path=output_path,
            export_stls=bool(config.stl_out),
        )

        if not os.path.exists(output_path):
            raise RuntimeError(f"Weave completed but output was not found: {output_path}")

        return HeadlessResult(
            output_path=os.path.abspath(output_path),
            intermediate_dir=work_dir if config.keep_intermediates else None,
            orientation_score=orientation.score,
            orientation_evaluated=orientation.evaluated,
            scale=scale,
        )
    finally:
        if temp_owner is not None:
            temp_owner.cleanup()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Headlessly convert a colored GLB/GLTF into a woven LayerLoom 3MF.")
    parser.add_argument("input", help="Input .glb or .gltf")
    parser.add_argument("-o", "--output", help="Output .3mf path")
    parser.add_argument("--output-dir", default=default_downloads_dir(), help="Output folder when --output is not supplied")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite an existing output path")
    parser.add_argument("--palette", default="Normal", help="Palette name or palette JSON path")
    parser.add_argument("--glb-target-colors", type=int, default=20, help="Approximate GLB color target before palette matching")
    parser.add_argument("--source-scale", type=float, default=0.1, help="Uniform scale passed to the GUI GLB splitter")
    sizing = parser.add_mutually_exclusive_group()
    sizing.add_argument("--fit-box", type=parse_fit_box, default=None, help="Fit XY footprint into W,H mm, capped to 200 mm 3D diagonal by default (default: 180,180)")
    sizing.add_argument("--max-dim", type=float, help="Scale so the longest bounding-box dimension is this many mm")
    sizing.add_argument("--max-diagonal", type=float, help="Scale so the 3D bounding-box diagonal is this many mm")
    parser.add_argument("--no-upscale", action="store_true", help="Never scale above the imported size")
    parser.add_argument("--orientation-quality", choices=["none", "fast", "balanced", "thorough"], default="balanced")
    parser.add_argument("--no-repair", action="store_true", help="Skip mesh repair")
    parser.add_argument("--strict-repair", action="store_true", help="Fail if repair fails or is rejected")
    parser.add_argument("--keep-intermediates", action="store_true", help="Keep intermediate 3MF files next to the output")
    parser.add_argument("--stl-out", help="Optional folder for per-color STL exports")
    parser.add_argument("--layer-height", type=float, default=DEFAULT_LAYER_HEIGHT, help="Layer weave height in mm")
    parser.add_argument("--missing-color-mode", choices=["error", "skip", "group"], default="group")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def config_from_args(args: argparse.Namespace) -> HeadlessConfig:
    fit_box = args.fit_box
    if fit_box is None and args.max_dim is None and args.max_diagonal is None:
        fit_box = DEFAULT_FIT_BOX
    return HeadlessConfig(
        input_path=args.input,
        output=args.output,
        output_dir=args.output_dir,
        overwrite=bool(args.overwrite),
        palette=args.palette,
        glb_target_colors=int(args.glb_target_colors),
        source_scale=float(args.source_scale),
        fit_box=fit_box,
        max_dim=args.max_dim,
        max_diagonal=args.max_diagonal,
        no_upscale=bool(args.no_upscale),
        orientation_quality=args.orientation_quality,
        repair=not bool(args.no_repair),
        strict_repair=bool(args.strict_repair),
        keep_intermediates=bool(args.keep_intermediates),
        stl_out=args.stl_out,
        layer_height=float(args.layer_height),
        missing_color_mode=args.missing_color_mode,
        verbose=bool(args.verbose),
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run_headless(config_from_args(args))
        print(result.output_path)
        if result.intermediate_dir:
            print(f"intermediates: {result.intermediate_dir}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
