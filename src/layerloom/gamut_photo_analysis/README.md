# Printed Gamut Photo Analysis

This folder contains tools for measuring photographed LayerLoom gamut prints.
The current production script is `analyze_printed_gamut.py`.

Superseded exploratory runs are tucked under `_archive/`. The current
publication-oriented outputs are:

- `simple_example_manual_core065/`
- `example_gamut_manual_core065/`
- `LayerLoom_figure5__auto_fit2400_core065/`
- `publication_gamut_figures_core065/`

The important workflow is deliberate registration: analyze one printed gamut at
a time. The canonical example image for this workflow is `example_gamut.png`,
which contains one CMY gamut triangle on a gray/white background.

Wide photos can contain several triangular panels, and some panels may use
non-CMY anchor colors such as black, white, gray, orange, green, or violet. Use
`analyze_gamut_mosaic.py` for these strip/mosaic photos: it analyzes each
registered triangular sub-gamut separately, then aggregates the measured
colors into combined CIE a*b* hull plots. Only use the CMY expected-color
comparison on sub-gamuts that match the generated LayerLoom CMY region map.

## 1. Make a Corner Guide

Start by generating inspectable guide images:

```bash
python gamut_photo_analysis/analyze_printed_gamut.py \
  --image gamut_photo_analysis/example_gamut.png \
  --target-name example_single_gamut \
  --guide-only \
  --out-dir gamut_photo_analysis/example_gamut__corner_guide
```

Useful outputs:

- `qc/00_crop_overview.png`: full image plus the selected crop.
- `qc/00_corner_coordinate_guide.png`: pixel grid used to choose C/M/Y corners.
- `qc/00_analysis_input.png`: the exact image that will be analyzed after
  auto-crop and optional downsampling.

By default, the script auto-crops to the colored printed target and downsamples
large photos to a maximum analysis dimension of 2400 px. This keeps iteration
fast while preserving enough detail for region-level measurement. Use
`--max-analysis-dim 0` for a full-resolution final rerun.

## 2. Run Registered Region Analysis

For `example_gamut.png`, automatic corner detection should usually be enough:

```bash
python gamut_photo_analysis/analyze_printed_gamut.py \
  --image gamut_photo_analysis/example_gamut.png \
  --target-name example_single_gamut \
  --preset normal \
  --out-dir gamut_photo_analysis/example_gamut__region_analysis
```

If automatic registration is off, make a corner guide and provide manual C/M/Y
corners:

```bash
python gamut_photo_analysis/analyze_printed_gamut.py \
  --image gamut_photo_analysis/example_gamut.png \
  --target-name example_single_gamut \
  --corners "C:95,1940;M:2300,1940;Y:1200,90" \
  --preset normal \
  --out-dir gamut_photo_analysis/example_gamut__region_analysis_manual
```

Manual corner coordinates use the coordinate system shown in
`qc/00_corner_coordinate_guide.png`, after auto-crop and downsampling. They are
not coordinates from the original uncropped photo.

Use `--preset simple`, `--preset normal`, or `--preset full` to match the
weave constraints used to print that target. You can also override the preset
with `--max-height`, `--max-run`, and `--distinct-lte`.

## 3. Analyze A Wide Multi-Gamut Mosaic

If a photo contains several big triangles, and each big triangle contains
multiple smaller triangular gamuts, use the mosaic driver. First create a
coordinate guide and manifest template:

```bash
python gamut_photo_analysis/analyze_gamut_mosaic.py \
  --image gamut_photo_analysis/my_wide_photo.png \
  --guide-only \
  --out-dir gamut_photo_analysis/my_wide_photo__mosaic_analysis
```

Open `qc/00_corner_coordinate_guide.png` and fill in
`mosaic_manifest_template.csv`.

For Figure-5-style mosaics, where each large triangle is actually four smaller
sub-gamuts, use the panel-landmark mode. Add one row per large triangle panel
with six observed landmarks:

- `V1`, `V2`, `V3`: the three outer panel vertices.
- `M12`, `M23`, `M31`: the observed side-midpoints where the four sub-gamuts
  meet.
- `coordinate_max_dim`: the max image dimension used when marking the
  coordinates, such as `2400`. Coordinates are rescaled automatically if you
  later run a faster pass with `--max-analysis-dim 900`.

Example:

```csv
group_name,triangle_name,preset,max_height,max_run,distinct_lte,corners,landmarks,coordinate_max_dim,notes
CMY,panel_01,simple,,,,,"V1:50,690;V2:685,690;V3:363,145;M12:368,690;M23:524,418;M31:207,418",2400,
```

The mosaic script expands that one panel row into four local affine
registrations: `corner_1`, `corner_2`, `corner_3`, and `center`. With
`preset=simple`, each sub-gamut has 30 regions, so six large panels produce
`6 x 4 x 30 = 720` sampled regions.

The older one-row-per-small-triangle mode is still supported. In that mode,
leave `landmarks` blank and provide `corners` directly:

```csv
group_name,triangle_name,preset,max_height,max_run,distinct_lte,corners,notes
big_triangle_01,big01_inner_cmy,simple,,,,"C:100,800;M:500,800;Y:300,300",
big_triangle_01,big01_outer_ovg,simple,,,,"C:120,820;M:80,260;Y:300,300",
```

Then run:

```bash
python gamut_photo_analysis/analyze_gamut_mosaic.py \
  --image gamut_photo_analysis/my_wide_photo.png \
  --manifest gamut_photo_analysis/my_wide_photo__mosaic_analysis/mosaic_manifest_template.csv \
  --out-dir gamut_photo_analysis/my_wide_photo__mosaic_analysis
```

To nudge panel landmarks visually instead of editing the CSV by hand:

```bash
python gamut_photo_analysis/edit_mosaic_landmarks.py \
  --image gamut_photo_analysis/LayerLoom_figure5.png \
  --manifest gamut_photo_analysis/LayerLoom_figure5__mosaic_analysis/layerloom_figure5_panel_landmarks_first_pass.csv
```

Drag the yellow landmark points directly on the photo. Press `s` to save and
`q` to quit. The editor preserves the manifest coordinate system, so the same
CSV can still be used for both `--max-analysis-dim 900` validation runs and
`--max-analysis-dim 2400` final runs.

Mosaic outputs include:

- `per_triangle/`: the full single-triangle QC bundle for each sub-gamut.
- `qc/01_panel_landmarks.png`: panel vertices, side-midpoints, and derived seams.
- `qc/01_mosaic_registered_regions.png`: region overlay after panel expansion.
- `qc/02_mosaic_sampling_overlay.png`: central sampled pixels/colors.
- `tables/all_measured_regions.csv`: all measured regions across the photo.
- `tables/mosaic_manifest_input.csv`: the panel or sub-gamut manifest as provided.
- `tables/mosaic_manifest_used.csv`: the expanded sub-gamut registrations actually analyzed.
- `tables/sub_gamut_sample_counts.csv`: sampled region counts per panel and sub-gamut.
- `tables/group_summary.csv`: measured hull area and separability by big triangle.
- `figures/01_mosaic_measured_ab_hulls.png`: combined CIE a*b* hulls.
- `figures/02_group_hull_area_bars.png`: measured hull area by group.

The `group_name` column should identify each large triangle or palette family.
For example, use groups like `CMY`, `OVG`, `CMY_plus_gray`, or
`white_gray_black` if that is the comparison you want to plot.

## 4. Optional: Crop A Wide Photo To One Target

For a wide multi-panel photo, crop to one triangle or subtriangle first:

```bash
python gamut_photo_analysis/analyze_printed_gamut.py \
  --image gamut_photo_analysis/my_wide_photo.png \
  --target-name cmy_inner_triangle \
  --crop 1400,0,2300,1050 \
  --guide-only \
  --out-dir gamut_photo_analysis/cmy_inner_triangle__corner_guide
```

After cropping, use coordinates from `qc/00_corner_coordinate_guide.png` for
manual registration if needed.

## Outputs

Each analysis run writes:

- `qc/`: input, mask, registration, expected-region overlay, and sampling masks.
- `tables/expected_regions.csv`: theoretical region map and expected colors.
- `tables/measured_regions.csv`: measured RGB/Lab, DeltaE, and uniformity per region.
- `tables/pairwise_deltaE00.csv`: separability between printed regions.
- `tables/summary.csv`: publication-friendly summary metrics.
- `figures/`: palette strips, CIE a*b* gamut plots, region error maps, and histograms.
- `masks/`: label images for inspecting which pixels were sampled.

## Interpretation Notes

Photo measurements are only as good as lighting and calibration. Without a
color reference chart or measured filament primaries, `deltaE00_to_expected`
should be interpreted as photo-space agreement with the generated palette, not
absolute colorimetry. Within-region variation, pairwise separability, measured
gamut area, and repeatability across photos are usually more robust first-pass
metrics.

The global color mask is used for auto-crop, corner detection, and QC only. Per
region measurements use the central core of each registered Voronoi polygon
(`--sample-core-fraction`, default `0.55`), then apply Lab-space outlier
trimming to reject glare, shadows, dust, and border bleed without incorrectly
removing legitimate low-chroma gray/brown regions. Use
`--sample-core-fraction 1.0` if you want full-region sampling.

Outer non-CMY panels should not be compared against the CMY expected-region map
until a matching expected-region model is added for their anchor colors.
