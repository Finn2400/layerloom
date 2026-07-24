from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from layerloom.calibrated_palette import (
    build_calibrated_palette_document,
    lab_for_hex,
    nearest_calibrated_palette_entry,
)
from layerloom.palette_utils import build_layer_fraction_palette, get_preset_spec


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "layerloom"


def test_calibrated_palette_uses_measured_hex_and_nominal_fallback(tmp_path):
    spec = get_preset_spec("normal")
    base = {
        "entries": list(
            build_layer_fraction_palette(
                ("c", "m", "y", "k", "w"),
                max_height=spec["max_height"],
                max_run=spec["max_run"],
                distinct_lte=spec["distinct_lte"],
            )
        )
    }
    measured_csv = tmp_path / "measured_regions.csv"
    measured_csv.write_text(
        "\n".join(
            [
                "stack_token,measured_hex,measured_R,measured_G,measured_B,measured_L,measured_a,measured_b,region_index,label,deltaE00_to_expected,within_region_deltaE00_median",
                "c,#102030,16,32,48,42.0,-3.0,-24.0,1,cyan,1.1,0.2",
                "myy,#af6d3b,175,109,59,55.0,24.0,37.0,2,warm-yellow,2.2,0.3",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    doc = build_calibrated_palette_document(base, measured_csv)
    entries = {entry["token"]: entry for entry in doc["entries"]}

    assert doc["calibration"]["measured_token_count"] == 2
    assert entries["c"]["calibrated"] is True
    assert entries["c"]["hex"] == "#102030"
    assert entries["c"]["nominal_hex"] == "#3ac8dc"
    assert entries["c"]["measured_L"] == 42.0

    assert entries["kw"]["calibrated"] is False
    assert entries["kw"]["hex"] == entries["kw"]["nominal_hex"] == "#848484"

    assert entries["ymy"]["calibrated"] is True
    assert entries["ymy"]["measured_token"] == "myy"
    assert entries["ymy"]["hex"] == "#af6d3b"
    assert entries["ymy"]["calibration_match"] == "fraction_signature"


def test_generated_calibrated_palette_artifact_is_present():
    path = PACKAGE / "palettes" / "calibrated_cmy_normal_core065_palette.json"
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["name"] == "Calibrated CMY Normal (core065)"
    assert len(data["entries"]) == 305
    assert sum(1 for entry in data["entries"] if entry.get("calibrated")) == 55


def test_nearest_match_prefers_measured_lab_over_nominal_hex_distance():
    source_hex = "#4472c9"
    source_lab = lab_for_hex(source_hex)
    entries = [
        {
            "token": "nominal_near",
            "hex": "#4573ca",
            "calibrated": False,
        },
        {
            "token": "printed_match",
            "hex": "#ffffff",
            "calibrated": True,
            "measured_L": float(source_lab[0]),
            "measured_a": float(source_lab[1]),
            "measured_b": float(source_lab[2]),
        },
    ]

    match = nearest_calibrated_palette_entry(entries, source_hex)

    assert match is not None
    assert match["token"] == "printed_match"
    assert np.isclose(match["measured_L"], source_lab[0])
