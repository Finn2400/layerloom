#!/usr/bin/env python3
"""Collapse repeated or symmetric Figure 5 gamut measurements into recipe summaries.

The raw mosaic workflow intentionally measures every printed patch.  That is
useful QC, but it can overstate gamut density when the same local recipe appears
multiple times across mirrored or repeated sub-gamuts.  This script keeps the raw
measurements intact and writes collapsed tables/figures for publication claims.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Iterable

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "layerloom_matplotlib_cache"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from analyze_printed_gamut import (  # noqa: E402
    _convex_hull_points,
    _delta_e00,
    _distinguishable_counts,
    _pairwise_delta_e00,
    _polygon_area,
    _rgb01_to_hex,
)


def _round_fraction(value: object, ndigits: int = 6) -> str:
    try:
        return f"{float(value):.{ndigits}f}"
    except Exception:
        return "nan"


def _fraction_key(row: pd.Series) -> str:
    return "c{}_m{}_y{}".format(
        _round_fraction(row.get("fC")),
        _round_fraction(row.get("fM")),
        _round_fraction(row.get("fY")),
    )


def _unordered_fraction_key(row: pd.Series) -> str:
    vals = sorted(float(row.get(col, 0.0)) for col in ("fC", "fM", "fY"))
    return "mix_" + "_".join(f"{v:.6f}" for v in vals)


def _identity_key(df: pd.DataFrame) -> pd.Series:
    return df["global_label"].astype(str)


def _center_replicate_key(df: pd.DataFrame) -> pd.Series:
    """Collapse only the repeated center CMY triangles across panels.

    Outer sub-gamuts remain panel-specific because they may intentionally use
    different non-CMY anchors.  This is the most conservative correction.
    """
    exact = df.apply(_fraction_key, axis=1)
    return np.where(
        df["sub_gamut_name"].astype(str) == "center",
        "center|" + exact,
        df["panel_name"].astype(str) + "|" + df["sub_gamut_name"].astype(str) + "|" + exact,
    )


def _panel_local_symmetry_key(df: pd.DataFrame) -> pd.Series:
    """Collapse mirror/permutation-equivalent recipes within each sub-gamut.

    This treats C/M/Y label permutations as the same local weave-complexity
    recipe while preserving panel and sub-gamut identity.
    """
    unordered = df.apply(_unordered_fraction_key, axis=1)
    return df["panel_name"].astype(str) + "|" + df["sub_gamut_name"].astype(str) + "|" + unordered


def _panel_position_replicate_key(df: pd.DataFrame) -> pd.Series:
    """Collapse each sub-gamut position across panels by exact local recipe.

    This assumes the same named sub-gamut position is a repeated observation
    across the mosaic.  It is useful as a sensitivity analysis, but less
    conservative than center-only collapse.
    """
    exact = df.apply(_fraction_key, axis=1)
    return df["sub_gamut_name"].astype(str) + "|" + exact


def _global_local_symmetry_key(df: pd.DataFrame) -> pd.Series:
    """Strongest collapse: ignore panel/sub-gamut and keep only recipe fractions."""
    return df.apply(_unordered_fraction_key, axis=1)


STRATEGIES = {
    "raw": _identity_key,
    "center_replicates": _center_replicate_key,
    "panel_local_symmetry": _panel_local_symmetry_key,
    "panel_position_replicates": _panel_position_replicate_key,
    "global_local_symmetry": _global_local_symmetry_key,
}


CLASS_MANIFEST_COLUMNS = [
    "panel_name",
    "sub_gamut_name",
    "symmetry_class",
    "axis_c",
    "axis_m",
    "axis_y",
    "include_in_collapse",
    "notes",
]


def _unique_join(values: Iterable[object], limit: int = 12) -> str:
    seen: list[str] = []
    for value in values:
        text = str(value)
        if text not in seen:
            seen.append(text)
        if len(seen) >= limit:
            break
    suffix = "" if len(seen) < limit else ";..."
    return ";".join(seen) + suffix


def _collapse(df: pd.DataFrame, strategy: str) -> pd.DataFrame:
    keyed = df.copy()
    keyed["symmetry_key"] = STRATEGIES[strategy](keyed)
    return _collapse_keyed(keyed, strategy)


def _collapse_keyed(keyed: pd.DataFrame, strategy: str) -> pd.DataFrame:
    rows = []
    for key, sub in keyed.groupby("symmetry_key", sort=True):
        lab = sub[["measured_L", "measured_a", "measured_b"]].to_numpy(dtype=float)
        rgb = sub[["measured_R", "measured_G", "measured_B"]].to_numpy(dtype=float)
        median_lab = np.nanmedian(lab, axis=0)
        median_rgb = np.nanmedian(rgb, axis=0)
        de_to_median = _delta_e00(lab, median_lab.reshape(1, 3)) if len(sub) else np.array([])
        first = sub.iloc[0]
        rows.append(
            {
                "symmetry_strategy": strategy,
                "symmetry_key": key,
                "replicate_count": int(len(sub)),
                "symmetry_classes": _unique_join(sub["symmetry_class"]) if "symmetry_class" in sub.columns else "",
                "panel_names": _unique_join(sub["panel_name"]),
                "sub_gamut_names": _unique_join(sub["sub_gamut_name"]),
                "region_indices": _unique_join(sub["region_index"]),
                "labels": _unique_join(sub["label"]),
                "stack_tokens": _unique_join(sub["stack_token"]),
                "fC_median": float(np.nanmedian(sub["fC"])),
                "fM_median": float(np.nanmedian(sub["fM"])),
                "fY_median": float(np.nanmedian(sub["fY"])),
                "measured_L": float(median_lab[0]),
                "measured_a": float(median_lab[1]),
                "measured_b": float(median_lab[2]),
                "measured_R": float(np.clip(median_rgb[0], 0.0, 1.0)),
                "measured_G": float(np.clip(median_rgb[1], 0.0, 1.0)),
                "measured_B": float(np.clip(median_rgb[2], 0.0, 1.0)),
                "measured_hex": _rgb01_to_hex(median_rgb),
                "replicate_deltaE00_median": float(np.nanmedian(de_to_median)) if len(de_to_median) else math.nan,
                "replicate_deltaE00_p90": float(np.nanpercentile(de_to_median, 90)) if len(de_to_median) else math.nan,
                "replicate_deltaE00_max": float(np.nanmax(de_to_median)) if len(de_to_median) else math.nan,
                "example_global_label": str(first.get("global_label", "")),
            }
        )
    return pd.DataFrame(rows)


def _default_class_manifest(raw: pd.DataFrame) -> pd.DataFrame:
    """Create a conservative, editable symmetry-class manifest.

    The only default cross-panel collapse is the center CMY sub-gamut.  Outer
    triangles intentionally stay separate until the user marks them as equivalent
    by assigning the same symmetry_class and, if needed, permuting axis_c/m/y.
    """
    rows = []
    unique = raw[["panel_name", "sub_gamut_name"]].drop_duplicates().sort_values(["panel_name", "sub_gamut_name"])
    for row in unique.itertuples(index=False):
        panel_name = str(row.panel_name)
        sub_gamut_name = str(row.sub_gamut_name)
        is_center = sub_gamut_name == "center"
        rows.append(
            {
                "panel_name": panel_name,
                "sub_gamut_name": sub_gamut_name,
                "symmetry_class": "center_cmy" if is_center else f"{panel_name}__{sub_gamut_name}",
                "axis_c": "C",
                "axis_m": "M",
                "axis_y": "Y",
                "include_in_collapse": "yes",
                "notes": (
                    "Repeated center CMY; safe to collapse across panels."
                    if is_center
                    else "Outer sub-gamut kept unique by default. To average a true mirror/replicate, give matching rows the same symmetry_class and adjust axis_c/axis_m/axis_y."
                ),
            }
        )
    return pd.DataFrame(rows, columns=CLASS_MANIFEST_COLUMNS)


def _write_default_class_manifest(raw: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _default_class_manifest(raw).to_csv(path, index=False)


def _load_class_manifest(path: Path, raw: pd.DataFrame) -> pd.DataFrame:
    if not path.exists():
        _write_default_class_manifest(raw, path)
    manifest = pd.read_csv(path).fillna("")
    missing = [col for col in CLASS_MANIFEST_COLUMNS if col not in manifest.columns]
    if missing:
        raise ValueError(f"{path} is missing required columns: {missing}")
    manifest = manifest[CLASS_MANIFEST_COLUMNS].copy()
    manifest["panel_name"] = manifest["panel_name"].astype(str)
    manifest["sub_gamut_name"] = manifest["sub_gamut_name"].astype(str)
    return manifest


def _manifest_symmetry_keys(raw: pd.DataFrame, manifest: pd.DataFrame) -> pd.DataFrame:
    keyed = raw.merge(manifest, on=["panel_name", "sub_gamut_name"], how="left", validate="many_to_one")
    keyed["symmetry_class"] = keyed["symmetry_class"].fillna("")
    for col, default in (("axis_c", "C"), ("axis_m", "M"), ("axis_y", "Y")):
        keyed[col] = keyed[col].fillna("").replace("", default)
    keyed["include_in_collapse"] = keyed["include_in_collapse"].fillna("yes")

    keys = []
    for row in keyed.itertuples(index=False):
        include = str(row.include_in_collapse).strip().lower() not in {"0", "false", "no", "n", "skip"}
        symmetry_class = str(row.symmetry_class).strip()
        if not include or not symmetry_class:
            keys.append(str(row.global_label))
            continue

        mapped: dict[str, float] = {}
        for value, axis in ((row.fC, row.axis_c), (row.fM, row.axis_m), (row.fY, row.axis_y)):
            axis_name = str(axis).strip().upper() or "UNMAPPED"
            mapped[axis_name] = mapped.get(axis_name, 0.0) + float(value)
        fractions = "|".join(f"{axis}={mapped[axis]:.6f}" for axis in sorted(mapped))
        keys.append(f"{symmetry_class}|{fractions}")
    keyed["symmetry_key"] = keys
    return keyed


def _collapse_with_manifest(raw: pd.DataFrame, manifest: pd.DataFrame) -> pd.DataFrame:
    return _collapse_keyed(_manifest_symmetry_keys(raw, manifest), "manifest_classes")


def _safe_hull_area(df: pd.DataFrame) -> float:
    points = df[["measured_a", "measured_b"]].to_numpy(dtype=float)
    if len(points) < 3:
        return math.nan
    return float(_polygon_area(_convex_hull_points(points)))


def _strategy_summary(collapsed: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for strategy, df in collapsed.items():
        labels = df["symmetry_key"].astype(str).tolist()
        pairwise = _pairwise_delta_e00(df[["measured_L", "measured_a", "measured_b"]].to_numpy(dtype=float), labels)
        counts = _distinguishable_counts(pairwise, labels).set_index("threshold_deltaE00")
        rows.append(
            {
                "symmetry_strategy": strategy,
                "region_count_for_hull": int(len(df)),
                "raw_replicate_count": int(df["replicate_count"].sum()),
                "median_replicates_per_point": float(df["replicate_count"].median()),
                "max_replicates_per_point": int(df["replicate_count"].max()),
                "measured_ab_hull_area": _safe_hull_area(df),
                "median_replicate_deltaE00": float(df["replicate_deltaE00_median"].median()),
                "p90_replicate_deltaE00": float(df["replicate_deltaE00_p90"].median()),
                "distinguishable_colors_deltaE00_lt_2": int(counts.loc[2.0, "distinguishable_color_count"]),
                "distinguishable_colors_deltaE00_lt_5": int(counts.loc[5.0, "distinguishable_color_count"]),
                "distinguishable_colors_deltaE00_lt_10": int(counts.loc[10.0, "distinguishable_color_count"]),
            }
        )
    return pd.DataFrame(rows)


def _plot_strategy_comparison(raw: pd.DataFrame, collapsed: dict[str, pd.DataFrame], out_path: Path) -> None:
    order = [name for name in ("raw", "center_replicates", "panel_local_symmetry", "panel_position_replicates") if name in collapsed]
    fig, axes = plt.subplots(1, len(order), figsize=(5.0 * len(order), 5.2), squeeze=False)

    all_points = raw[["measured_a", "measured_b"]].to_numpy(dtype=float)
    x0, x1 = np.percentile(all_points[:, 0], [0.5, 99.5])
    y0, y1 = np.percentile(all_points[:, 1], [0.5, 99.5])
    pad = max(x1 - x0, y1 - y0) * 0.10

    raw_rgb = raw[["measured_R", "measured_G", "measured_B"]].to_numpy(dtype=float)
    for ax, strategy in zip(axes[0], order):
        df = collapsed[strategy]
        pts = df[["measured_a", "measured_b"]].to_numpy(dtype=float)
        rgb = df[["measured_R", "measured_G", "measured_B"]].to_numpy(dtype=float)
        ax.scatter(
            all_points[:, 0],
            all_points[:, 1],
            c=np.clip(raw_rgb, 0, 1),
            s=10,
            alpha=0.16,
            linewidth=0,
        )
        ax.scatter(
            pts[:, 0],
            pts[:, 1],
            c=np.clip(rgb, 0, 1),
            s=38,
            edgecolor="0.12",
            linewidth=0.35,
        )
        hull = _convex_hull_points(pts)
        if len(hull) >= 3:
            closed = np.vstack([hull, hull[0]])
            ax.plot(closed[:, 0], closed[:, 1], color="black", lw=2.0)
            ax.fill(closed[:, 0], closed[:, 1], color="0.1", alpha=0.05)
        ax.axhline(0, color="0.86", lw=0.8)
        ax.axvline(0, color="0.86", lw=0.8)
        ax.set_title(f"{strategy.replace('_', ' ')}\n{len(df)} points")
        ax.set_xlabel("a*")
        ax.set_ylabel("b*")
        ax.set_xlim(x0 - pad, x1 + pad)
        ax.set_ylim(y0 - pad, y1 + pad)
        ax.set_aspect("equal", adjustable="box")

    fig.suptitle("Figure 5 measured gamut: raw vs symmetry-collapsed summaries", fontsize=15)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=260)
    plt.close(fig)


def _plot_replicate_noise(collapsed: dict[str, pd.DataFrame], out_path: Path) -> None:
    rows = []
    for strategy, df in collapsed.items():
        if strategy == "raw":
            continue
        for value in df["replicate_deltaE00_median"].dropna():
            rows.append({"symmetry_strategy": strategy, "median_deltaE00_to_group": float(value)})
    if not rows:
        return
    data = pd.DataFrame(rows)
    strategies = list(data["symmetry_strategy"].drop_duplicates())
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    vals = [data.loc[data["symmetry_strategy"] == s, "median_deltaE00_to_group"].to_numpy() for s in strategies]
    ax.boxplot(vals, labels=[s.replace("_", "\n") for s in strategies], patch_artist=True)
    ax.set_ylabel("Within-equivalence median ΔE00")
    ax.set_title("Variation among collapsed replicate/symmetric observations")
    ax.grid(axis="y", color="0.88", lw=0.8)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=260)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measured-csv", required=True, help="Figure 5 all_measured_regions.csv")
    parser.add_argument("--out-dir", required=True, help="Output directory for collapsed tables and figures")
    parser.add_argument(
        "--class-manifest",
        help=(
            "Optional editable CSV defining true symmetry classes. If the file does not exist, "
            "a conservative template is written there and used."
        ),
    )
    parser.add_argument(
        "--write-class-template",
        help="Write a conservative symmetry-class template CSV and continue with normal analysis.",
    )
    parser.add_argument(
        "--strategy",
        action="append",
        choices=sorted(STRATEGIES),
        help="Collapse strategy to compute. Repeatable. Defaults to all useful strategies.",
    )
    args = parser.parse_args()

    measured_path = Path(args.measured_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    tables_dir = out_dir / "tables"
    figures_dir = out_dir / "figures"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    raw = pd.read_csv(measured_path)
    required = {"panel_name", "sub_gamut_name", "fC", "fM", "fY", "measured_L", "measured_a", "measured_b"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"{measured_path} is missing required columns: {sorted(missing)}")

    manifest: pd.DataFrame | None = None
    if args.write_class_template:
        template_path = Path(args.write_class_template).expanduser().resolve()
        _write_default_class_manifest(raw, template_path)
        print(f"[done] wrote {template_path}")
    if args.class_manifest:
        manifest = _load_class_manifest(Path(args.class_manifest).expanduser().resolve(), raw)

    strategies = args.strategy or [
        "raw",
        "center_replicates",
        "panel_local_symmetry",
        "panel_position_replicates",
        "global_local_symmetry",
    ]
    collapsed = {strategy: _collapse(raw, strategy) for strategy in strategies}
    if manifest is not None:
        collapsed["manifest_classes"] = _collapse_with_manifest(raw, manifest)
    for strategy, df in collapsed.items():
        df.to_csv(tables_dir / f"{strategy}_collapsed_regions.csv", index=False)

    summary = _strategy_summary(collapsed)
    summary.to_csv(tables_dir / "symmetry_collapse_summary.csv", index=False)
    _plot_strategy_comparison(raw, collapsed, figures_dir / "01_raw_vs_symmetry_collapsed_ab.png")
    _plot_replicate_noise(collapsed, figures_dir / "02_symmetry_replicate_deltaE00.png")

    print(f"[done] wrote {tables_dir / 'symmetry_collapse_summary.csv'}")
    print(f"[done] wrote {figures_dir / '01_raw_vs_symmetry_collapsed_ab.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
