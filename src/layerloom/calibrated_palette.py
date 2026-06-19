"""Measured-color palette helpers for LayerLoom."""

from __future__ import annotations

import csv
import json
import math
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np


CALIBRATED_CMY_NORMAL_NAME = "Calibrated CMY Normal (core065)"
CALIBRATED_CMY_NORMAL_FILENAME = "calibrated_cmy_normal_core065_palette.json"
CALIBRATED_CMY_NORMAL_DATASET = "example_gamut_manual_core065"
CALIBRATED_CMY_NORMAL_SOURCE = (
    "gamut_photo_analysis/example_gamut_manual_core065/tables/measured_regions.csv"
)


def normalize_hex(hex_code: object) -> str | None:
    text = str(hex_code or "").strip()
    if not text:
        return None
    if not text.startswith("#"):
        text = "#" + text
    if len(text) != 7:
        return None
    try:
        int(text[1:], 16)
    except ValueError:
        return None
    return text.lower()


def hex_to_rgb01(hex_code: object) -> tuple[float, float, float]:
    hx = normalize_hex(hex_code)
    if hx is None:
        raise ValueError(f"Expected 6-digit hex color, got {hex_code!r}")
    return tuple(int(hx[i : i + 2], 16) / 255.0 for i in (1, 3, 5))


def rgb01_to_hex(rgb: Sequence[float]) -> str:
    vals = [int(np.clip(float(v), 0.0, 1.0) * 255 + 0.5) for v in rgb[:3]]
    return "#{:02x}{:02x}{:02x}".format(*vals)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    arr = np.asarray(rgb, dtype=float)
    linear = np.where(arr <= 0.04045, arr / 12.92, ((arr + 0.055) / 1.055) ** 2.4)
    matrix = np.array(
        [
            [0.4124564, 0.3575761, 0.1804375],
            [0.2126729, 0.7151522, 0.0721750],
            [0.0193339, 0.1191920, 0.9503041],
        ],
        dtype=float,
    )
    xyz = linear @ matrix.T
    white = np.array([0.95047, 1.00000, 1.08883], dtype=float)
    xyz_scaled = xyz / white
    delta = 6.0 / 29.0
    f = np.where(xyz_scaled > delta**3, np.cbrt(xyz_scaled), xyz_scaled / (3 * delta**2) + 4.0 / 29.0)
    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def lab_for_hex(hex_code: object) -> np.ndarray:
    return rgb_to_lab(np.asarray(hex_to_rgb01(hex_code), dtype=float).reshape(1, 3)).reshape(3)


def delta_e00(lab_a: np.ndarray, lab_b: np.ndarray) -> np.ndarray:
    a = np.asarray(lab_a, dtype=float).reshape(-1, 3)
    b = np.asarray(lab_b, dtype=float).reshape(-1, 3)
    if b.shape[0] == 1 and a.shape[0] > 1:
        b = np.repeat(b, a.shape[0], axis=0)
    if a.shape[0] == 1 and b.shape[0] > 1:
        a = np.repeat(a, b.shape[0], axis=0)
    if a.shape[0] != b.shape[0]:
        raise ValueError("Lab arrays must be same length or one must be length 1")

    L1, a1, b1 = a[:, 0], a[:, 1], a[:, 2]
    L2, a2, b2 = b[:, 0], b[:, 1], b[:, 2]

    C1 = np.sqrt(a1 * a1 + b1 * b1)
    C2 = np.sqrt(a2 * a2 + b2 * b2)
    Cbar = 0.5 * (C1 + C2)
    Cbar7 = Cbar**7
    G = 0.5 * (1.0 - np.sqrt(Cbar7 / (Cbar7 + 25.0**7 + 1e-30)))

    a1p = (1.0 + G) * a1
    a2p = (1.0 + G) * a2
    C1p = np.sqrt(a1p * a1p + b1 * b1)
    C2p = np.sqrt(a2p * a2p + b2 * b2)

    h1p = (np.degrees(np.arctan2(b1, a1p)) + 360.0) % 360.0
    h2p = (np.degrees(np.arctan2(b2, a2p)) + 360.0) % 360.0
    h1p = np.where(C1p <= 1e-12, 0.0, h1p)
    h2p = np.where(C2p <= 1e-12, 0.0, h2p)

    dLp = L2 - L1
    dCp = C2p - C1p
    dh = h2p - h1p
    dh = np.where(dh > 180.0, dh - 360.0, dh)
    dh = np.where(dh < -180.0, dh + 360.0, dh)
    dh = np.where((C1p * C2p) <= 1e-12, 0.0, dh)
    dHp = 2.0 * np.sqrt(C1p * C2p) * np.sin(np.radians(dh) / 2.0)

    Lbarp = 0.5 * (L1 + L2)
    Cbarp = 0.5 * (C1p + C2p)
    hsum = h1p + h2p
    hdiff = np.abs(h1p - h2p)
    hbarp = np.where(
        (C1p * C2p) <= 1e-12,
        hsum,
        np.where(hdiff <= 180.0, 0.5 * hsum, np.where(hsum < 360.0, 0.5 * (hsum + 360.0), 0.5 * (hsum - 360.0))),
    )

    T = (
        1.0
        - 0.17 * np.cos(np.radians(hbarp - 30.0))
        + 0.24 * np.cos(np.radians(2.0 * hbarp))
        + 0.32 * np.cos(np.radians(3.0 * hbarp + 6.0))
        - 0.20 * np.cos(np.radians(4.0 * hbarp - 63.0))
    )
    delta_theta = 30.0 * np.exp(-((hbarp - 275.0) / 25.0) ** 2)
    Rc = 2.0 * np.sqrt(Cbarp**7 / (Cbarp**7 + 25.0**7 + 1e-30))
    Sl = 1.0 + (0.015 * (Lbarp - 50.0) ** 2) / np.sqrt(20.0 + (Lbarp - 50.0) ** 2)
    Sc = 1.0 + 0.045 * Cbarp
    Sh = 1.0 + 0.015 * Cbarp * T
    Rt = -np.sin(np.radians(2.0 * delta_theta)) * Rc

    return np.sqrt(
        (dLp / Sl) ** 2
        + (dCp / Sc) ** 2
        + (dHp / Sh) ** 2
        + Rt * (dCp / Sc) * (dHp / Sh)
    )


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def token_fraction_signature(token: str) -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = {}
    for ch in str(token or "").strip().lower():
        counts[ch] = counts.get(ch, 0) + 1
    total = sum(counts.values())
    if total <= 0:
        return tuple()
    divisor = total
    for value in counts.values():
        divisor = math.gcd(divisor, value)
    return tuple(sorted((letter, value // divisor) for letter, value in counts.items()))


def load_measured_cmy_rows(csv_path: str | Path) -> dict[str, dict[str, Any]]:
    path = Path(csv_path)
    measured: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            token = str(row.get("stack_token") or "").strip().lower()
            if not token:
                continue
            if token in measured:
                raise ValueError(f"duplicate measured token {token!r} in {path}")
            rgb = (
                _float_or_none(row.get("measured_R")),
                _float_or_none(row.get("measured_G")),
                _float_or_none(row.get("measured_B")),
            )
            measured_hex = normalize_hex(row.get("measured_hex"))
            if measured_hex is None and all(v is not None for v in rgb):
                measured_hex = rgb01_to_hex([float(v) for v in rgb if v is not None])
            if measured_hex is None:
                continue
            measured[token] = {
                "measured_token": token,
                "hex": measured_hex,
                "measured_R": rgb[0],
                "measured_G": rgb[1],
                "measured_B": rgb[2],
                "measured_L": _float_or_none(row.get("measured_L")),
                "measured_a": _float_or_none(row.get("measured_a")),
                "measured_b": _float_or_none(row.get("measured_b")),
                "source_dataset": CALIBRATED_CMY_NORMAL_DATASET,
                "source_region_index": row.get("region_index"),
                "source_label": row.get("label"),
                "deltaE00_to_expected": _float_or_none(row.get("deltaE00_to_expected")),
                "within_region_deltaE00_median": _float_or_none(row.get("within_region_deltaE00_median")),
            }
    return measured


def _unique_entries_by_token(entries: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for entry in entries:
        token = str(entry.get("token") or "").strip().lower()
        if token and token not in out:
            item = dict(entry)
            item["token"] = token
            out[token] = item
    return list(out.values())


def build_calibrated_palette_document(
    base_palette: Mapping[str, Any],
    measured_csv: str | Path,
    *,
    source_csv_label: str = CALIBRATED_CMY_NORMAL_SOURCE,
) -> dict[str, Any]:
    measured = load_measured_cmy_rows(measured_csv)
    measured_by_signature: dict[tuple[tuple[str, int], ...], dict[str, Any]] = {}
    for token, calibration in measured.items():
        measured_by_signature.setdefault(token_fraction_signature(token), calibration)

    entries = []
    for entry in _unique_entries_by_token(base_palette.get("entries", [])):
        token = str(entry.get("token") or "").strip().lower()
        nominal_hex = normalize_hex(entry.get("hex"))
        if nominal_hex is None:
            continue
        item = dict(entry)
        item["token"] = token
        item["nominal_hex"] = nominal_hex
        calibration = measured.get(token)
        match_type = "exact_token"
        if calibration is None:
            calibration = measured_by_signature.get(token_fraction_signature(token))
            match_type = "fraction_signature"
        if calibration is not None:
            item["hex"] = calibration["hex"]
            item.update(calibration)
            item["calibrated"] = True
            item["calibration_match"] = match_type
        else:
            item["hex"] = nominal_hex
            item["calibrated"] = False
            item["source_dataset"] = "nominal_fallback"
        entries.append(item)

    return {
        "name": CALIBRATED_CMY_NORMAL_NAME,
        "description": (
            "Normal LayerLoom palette with measured CMY core065 colors from "
            f"{CALIBRATED_CMY_NORMAL_DATASET}; unmeasured recipes use nominal hex values."
        ),
        "calibration": {
            "source_dataset": CALIBRATED_CMY_NORMAL_DATASET,
            "source_csv": source_csv_label,
            "measured_token_count": len(measured),
            "fallback": "nominal_hex",
        },
        "entries": entries,
    }


def write_calibrated_cmy_normal_palette(
    base_palette_path: str | Path,
    measured_csv_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    base_path = Path(base_palette_path)
    with base_path.open("r", encoding="utf-8") as f:
        base_palette = json.load(f)
    document = build_calibrated_palette_document(base_palette, measured_csv_path)
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(document, f, indent=2)
        f.write("\n")
    return document


def entry_has_measured_lab(entry: Mapping[str, Any]) -> bool:
    return all(_float_or_none(entry.get(k)) is not None for k in ("measured_L", "measured_a", "measured_b"))


def palette_uses_calibrated_lab(entries: Iterable[Mapping[str, Any]]) -> bool:
    return any(bool(entry.get("calibrated")) and entry_has_measured_lab(entry) for entry in entries)


def entry_match_lab(entry: Mapping[str, Any]) -> np.ndarray:
    if entry_has_measured_lab(entry):
        return np.array(
            [
                float(entry["measured_L"]),
                float(entry["measured_a"]),
                float(entry["measured_b"]),
            ],
            dtype=float,
        )
    return lab_for_hex(entry.get("hex"))


def nearest_calibrated_palette_entry(
    entries: Iterable[Mapping[str, Any]],
    source_hex: object,
) -> dict[str, Any] | None:
    try:
        source_lab = lab_for_hex(source_hex)
    except Exception:
        return None

    best_entry: dict[str, Any] | None = None
    best_distance: float | None = None
    for entry in entries:
        try:
            distance = float(delta_e00(source_lab, entry_match_lab(entry))[0])
        except Exception:
            continue
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_entry = dict(entry)
    return best_entry
