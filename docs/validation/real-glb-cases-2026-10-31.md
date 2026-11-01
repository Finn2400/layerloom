# Real-world GLB checks — 2026-10-31

Tested source revision: `9eed44cc6de7eadc72de8adc9464bbe3e85abe65`.
These are native Qt integration checks using the locked source environment,
not a downloaded-DMG trial, slicer test, or physical print.

The sources were found locally; no original model was modified, repaired,
rescaled in place, or replaced. Each report records its SHA-256 before and
after testing. Models and generated outputs are not committed to the repository.

| Source | Input triangles | Imported regions | Import preserves all faces | Closed imported regions | Weave result |
| --- | ---: | ---: | --- | ---: | --- |
| `1ubq_rainbow_layerloom.glb` | 74,372 | 9 | Yes | 0/9 | Exports and reopens; geometry fails |
| `pulling_ubq.glb` | 1,177,536 | 12 | Yes | 0/12 | Import/geometry stress test only; not woven in this run |
| `fred_angel_fish_direct_cmy_solid.glb` | 324,102 | 3 | Yes | 2/3 | Exports and reopens; geometry fails |

The main Arrange action creates named, separately assigned regions rather than
one surface-paint document. The existing importer scale and placement behavior
were retained. Import times were approximately 3.5, 51.2 and 13.6 seconds,
respectively. The rainbow and fish weaves used 0.16 mm and completed in about
59.0 and 18.2 seconds. No optional mesh repair was available in this environment.

## Woven output audit

| Source | Output triangles | Zero-area float64 triangles | Zero-area float32 triangles | Duplicate faces |
| --- | ---: | ---: | ---: | ---: |
| Rainbow ubiquitin | 1,313,442 | 26 | 38 | 14 |
| Angelfish | 331,910 | 99 | 311 | 2 |

All three rainbow material objects are non-closed after exact coordinate
welding, with inconsistent winding. The fish magenta object is non-closed;
its cyan object also contains collapsed triangles despite being topologically
closed. These failures must not be hidden by a successful export message.

The assigned inputs have no collapsed triangles in either precision. Color
separation does not in itself make closed material volumes: all nine rainbow
regions and all twelve pulled-ubiquitin regions are open. That is a separate
issue from the new degeneracy introduced during weaving and serialization.
Do not infer useful physical volume measurements from open meshes.

The rainbow demo folder also contains an older candidate source ZIP with an
experimental solid-band backend. Its previous export results are not evidence
that the current application passes. That snapshot has not been adopted.

## Required follow-up

1. Preserve selectable color regions while producing valid material volumes
   for sources whose color boundaries cut across a continuous surface. Do not
   silently treat open color fragments as closed printable solids.
2. Correct and validate slicing, material union, coordinate precision and final
   serialization/baking. Check local and placed float32 coordinates, not merely
   successful archive creation or GUI reopening.
3. Make an invalid result fail clearly, rather than showing export success.
4. Repeat these real-world cases after the fix, then packaged-app and slicer
   checks. The pulled-ubiquitin case still needs a complete weave test.

PR #6 remains draft and a7 remains unpublished. The existing release hold in
[the initial GLB report](glb-regions-2026-10-30.md) still applies. Separately,
Windows native GUI jobs in the previous PR run ended cancelled, so that gate
also needs investigation/rerun before a release can be called fully validated.

## Reproduction and evidence

`~/LayerLoom Fresh User Test/Evidence/real-glb-2026-10-31/` contains:

- `summary.json`: source identities, counts, timings and unchanged-source checks.
- `run_gui_case.py`: reusable test driver (source path and output folder arguments;
  optional `--import-only`). It opens Arrange, materializes assigned regions,
  invokes the actual Weave action, reopens output, and audits exact/float32 geometry.
- Per-case `report.json`, import screenshots, assigned 3MFs, woven outputs where
  tested, and full GUI/worker logs.

Run the driver from a LayerLoom source checkout with its locked GUI environment.
Use a fresh output folder so overwrite dialogs cannot affect the run. The driver
controls its own test window; it does not send files to a printer.
