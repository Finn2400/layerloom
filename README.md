# LayerLoom Computers & Graphics Revision Archive

This archive accompanies the revised manuscript, "Existing Multi-Material FFF
Printers Are Sufficient For (Near) Arbitrary Coloration."

Release page:
https://github.com/Finn2400/layerloom/releases/tag/cag-revision-2026-07-23

## Contents

- `software/`: the LayerLoom 0.6.3 source snapshot, package metadata, palette
  JSON files, tests, and packaged tutorial inputs.
- `manuscript/`: the exact revised anonymous manuscript, bibliography, main
  figures, supplementary source, supplementary figures, and CSV tables.
- `analysis/`: the manuscript figure helper and the complete tensile-screen
  analysis script.
- `data/tensile_raw/`: all 20 raw Mark-10 Excel exports.
- `data/benchmark/`: the manuscript benchmark table and the contemporaneous
  measurement notes from which the measured values were transcribed.
- `image_analysis/`: the exact processed PNG inputs used for the reported
  expanded-gamut analysis, all per-region measurements, registration inputs,
  analysis scripts, configurations, result tables, and three auxiliary
  demonstration-mosaic analyses.
- `examples/`: representative input and woven-output 3MF files.
- `SHA256SUMS.txt`: checksums for every other file in the archive.

The manuscript's aggregate gamut values come from the 46 registered images in
`image_analysis/expanded_gamut/per_photo/`. Each `*_analysis_input.png` is the
exact post-crop/downsample image used for the corresponding measurement table.
The archive also includes the 1,955 measured-region records, replicate collapse,
registration coordinates, manual overrides, aggregate tables, and analysis
figures. The original 16-bit camera TIFF collection is approximately 15 GB and
is not duplicated in this release; source filenames are retained in the
manifest and measurement tables for provenance.

The three PNGs in `image_analysis/inputs/` support auxiliary single-mosaic
analyses and are not the source of the manuscript's final aggregate values.
All reported color quantities are image-derived measurements, not
spectrophotometer measurements.

## Software Environment

LayerLoom requires Python 3.10 or newer. From the archive root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e "./software[analysis,headless,dev]"
python -m pip install openpyxl
```

The package requirements and bounds are recorded in
`software/pyproject.toml`. Versions from the local verification environment
are recorded in `VERIFICATION_ENVIRONMENT.txt`.

## Rebuild The Tensile Supplement

```bash
python analysis/generate_tensile_screen.py \
  --data-dir data/tensile_raw \
  --revision-dir manuscript
```

This command reads all 20 workbooks and regenerates Supplementary Figs. S9-S11
and the specimen- and group-level CSV files used by Tables S6-S7. The analysis
uses 5 mm/min crosshead travel, initial grip separations of 40 mm for flat
coupons and 90 mm for upright coupons, and the gauge dimensions stated in the
supplement.

The woven and single-color coupons used PLA from the same product family under
matched print and test conditions. LayerLoom changed the deposited color
sequence rather than the underlying polymer, and no meaningful difference in
tensile strength was observed in either tested orientation.

## Verify The Reported Expanded-Gamut Values

The command below starts from the archived per-region measurements, collapses
replicate photographs, recomputes hull and CIEDE2000 distinguishability
statistics, and checks the rebuilt tables against the archived references:

```bash
python analysis/recompute_expanded_gamut_summary.py
```

The check verifies the principal reported values: `51/55` distinguishable full
CMY regions at CIEDE2000 below 2, `393/1210` distinguishable expanded-palette
regions at that threshold, and measured a*b* hull areas of `6646.11` and
`8751.98`, respectively.

To rerun registration and pixel sampling from the original 16-bit TIFFs, use
`image_analysis/analyze_expanded_gamut_photos.py` with the parameters in
`image_analysis/expanded_gamut/analysis_config.json` and the supplied manual
registration overrides. That first-stage rerun requires the original TIFF
collection, which is not included here.

## Rebuild The Auxiliary CMY Image Analyses

Simple CMY gamut:

```bash
PYTHONPATH=software/src/layerloom python image_analysis/analyze_printed_gamut.py \
  --image image_analysis/inputs/simple_example.png \
  --target-name simple_example_manual \
  --preset simple \
  --crop 126,150,2768,2444 \
  --corners "C:33.9,2053.2;M:2380.6,2041.1;Y:1109.2,52.3" \
  --sample-core-fraction 0.65 \
  --max-analysis-dim 2400 \
  --out-dir image_analysis/rebuilt/simple_example_manual_core065
```

Full CMY gamut:

```bash
PYTHONPATH=software/src/layerloom python image_analysis/analyze_printed_gamut.py \
  --image image_analysis/inputs/example_gamut.png \
  --target-name full_example_manual \
  --preset normal \
  --crop 89,95,6210,5373 \
  --corners "C:20.1,2024.8;M:2375.4,2060.9;Y:1239.9,30.8" \
  --sample-core-fraction 0.65 \
  --max-analysis-dim 2400 \
  --out-dir image_analysis/rebuilt/example_gamut_manual_core065
```

Expanded-endpoint mosaic:

```bash
PYTHONPATH=software/src/layerloom python image_analysis/analyze_gamut_mosaic.py \
  --image image_analysis/inputs/LayerLoom_figure5.png \
  --manifest image_analysis/registration/layerloom_figure5_panel_landmarks_auto_fit.csv \
  --sample-core-fraction 0.65 \
  --max-analysis-dim 2400 \
  --out-dir image_analysis/rebuilt/LayerLoom_figure5_auto_fit2400_core065
```

The `within_region_deltaE00_median` field in each measured-region table is the
median pixel-to-region-median CIEDE2000 difference inside the registered,
eroded sampling mask. It is retained only as an image-uniformity proxy, not as
a predictive striation model. The stored crop bounds prevent changes in the
automatic crop detector from shifting the auxiliary registrations. Small
last-digit floating-point differences can occur across NumPy, pandas, and
Pillow versions.

## Build The Manuscript And Supplement

```bash
cd manuscript
latexmk -pdf -interaction=nonstopmode -halt-on-error \
  layerloom__computers_graphics_anon.tex
cd supplement
latexmk -pdf -interaction=nonstopmode -halt-on-error supp.tex
```

Figures 1--4 and 6--8 preserve the submitted bitmap assets. Figure 5 was updated
to label print duration in hours and total filament use in grams directly on
the panel axes. The revised manuscript also adds a short numbered workflow guide
beneath Figure 1.
