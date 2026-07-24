#!/usr/bin/env python3
"""Recompute LayerLoom's aggregate gamut tables from archived region data."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ARCHIVE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ARCHIVE_ROOT / "software" / "src" / "layerloom"))
sys.path.insert(0, str(ARCHIVE_ROOT / "image_analysis"))

from analyze_expanded_gamut_photos import (  # noqa: E402
    _collapse_replicates,
    _repeatability,
    _summary_tables,
)


def _assert_matches(rebuilt: pd.DataFrame, reference_path: Path) -> None:
    reference = pd.read_csv(reference_path)
    pd.testing.assert_frame_equal(
        rebuilt.reset_index(drop=True),
        reference.reset_index(drop=True),
        check_exact=False,
        rtol=2e-7,
        atol=2e-7,
        check_dtype=False,
    )


def _one_row(
    frame: pd.DataFrame,
    *,
    condition: str,
    palette_family: str,
    threshold: float | None = None,
) -> pd.Series:
    mask = (frame["condition"] == condition) & (frame["palette_family"] == palette_family)
    if threshold is not None:
        mask &= np.isclose(frame["threshold_deltaE00"], threshold)
    selected = frame.loc[mask]
    if len(selected) != 1:
        raise AssertionError(
            f"Expected one row for {condition}/{palette_family}/{threshold}, found {len(selected)}"
        )
    return selected.iloc[0]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recompute aggregate gamut tables from the archived per-region measurements."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=ARCHIVE_ROOT
        / "image_analysis"
        / "expanded_gamut"
        / "tables"
        / "all_measured_regions.csv",
    )
    parser.add_argument(
        "--reference-dir",
        type=Path,
        default=ARCHIVE_ROOT / "image_analysis" / "expanded_gamut" / "tables",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=ARCHIVE_ROOT / "image_analysis" / "rebuilt" / "expanded_gamut",
    )
    args = parser.parse_args()

    all_measured = pd.read_csv(args.input)
    if len(all_measured) != 1955:
        raise AssertionError(f"Expected 1,955 measured regions, found {len(all_measured):,}")

    collapsed = _collapse_replicates(all_measured)
    repeatability = _repeatability(all_measured)
    summary, counts = _summary_tables(collapsed)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "collapsed_region_medians.csv": collapsed,
        "duplicate_repeatability.csv": repeatability,
        "condition_family_summary.csv": summary,
        "distinguishable_counts.csv": counts,
    }
    for name, frame in outputs.items():
        frame.to_csv(args.out_dir / name, index=False)
        _assert_matches(frame, args.reference_dir / name)

    full_cmy = _one_row(summary, condition="full", palette_family="CMY")
    full_all = _one_row(summary, condition="full", palette_family="CMY+neutral+OVG")
    full_cmy_de2 = _one_row(
        counts,
        condition="full",
        palette_family="CMY",
        threshold=2.0,
    )
    full_all_de2 = _one_row(
        counts,
        condition="full",
        palette_family="CMY+neutral+OVG",
        threshold=2.0,
    )

    expected = {
        "full CMY hull area": (float(full_cmy["measured_ab_hull_area"]), 6646.109139082127),
        "full expanded hull area": (float(full_all["measured_ab_hull_area"]), 8751.977942420035),
        "full CMY distinguishable at DeltaE00 < 2": (
            int(full_cmy_de2["distinguishable_count"]),
            51,
        ),
        "full expanded distinguishable at DeltaE00 < 2": (
            int(full_all_de2["distinguishable_count"]),
            393,
        ),
    }
    for label, (actual, target) in expected.items():
        if not np.isclose(actual, target, rtol=2e-7, atol=2e-7):
            raise AssertionError(f"{label}: expected {target}, got {actual}")

    print("[verified] 1,955 archived region measurements")
    print("[verified] full CMY: 51/55 distinguishable at DeltaE00 < 2")
    print("[verified] full expanded set: 393/1,210 distinguishable at DeltaE00 < 2")
    print("[verified] hull areas: CMY 6,646.11; expanded 8,751.98")
    print(f"[verified] rebuilt tables match {args.reference_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
