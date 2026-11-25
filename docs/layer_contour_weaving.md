# Experimental layer-contour weaving

The optional `layer-contour` backend reconstructs a compact, layer-aware 3MF
instead of retaining all sliced bands and performing a whole-model export.
Legacy weaving remains the default. This is a geometry/export optimization,
not a promise that every slicer or printer will use less memory.

## Use

Install the experimental geometry and benchmark extras in the feature checkout:

```sh
python -m pip install -e '.[contour,bench]'
```

The backend requires Shapely 2.1 with GEOS support for constrained Delaunay
triangulation. Existing legacy installations do not need this extra.

```sh
layerloom-weave -i examples/tutorial_cmy_benchy_cutup.3mf \
  -o benchy-contour.3mf --weave-mode layer-contour \
  --step 0.08 --first-layer-height 0.16 --xy-tolerance-mm 0.02 \
  --memory-budget-mib 1024 --scratch-dir /path/with/free/space \
  --no-export-stls
```

The input must be a prepared labeled 3MF with its final scale and orientation.
Use the same first/subsequent layer heights and fixed placement in the slicer;
do not rotate or rescale the woven model, or enable adaptive layer heights.
The first-layer color setting retains its existing purpose. A separate first
height is explicit: 0.16/0.08 is not treated as a uniform 0.08 mm lattice.

A tolerance of zero disables intentional contour approximation; it is useful as
a controlled reference for reconstruction tests. The default 0.02 mm is an XY
contour error allowance, not a guarantee that all final walls will move less
than 0.02 mm. Wall generation, overlapping material clipping, compensation,
seams and slicer settings still require the downstream comparisons below.
The simplification allowance is checked against the reference layer contours,
not added independently at every stage. Zero tolerance still includes explicit
numerical regularization: intersections use a 0.0000001 mm grid and exported XY
coordinates use a binary 2^-16 mm grid (about 0.0000153 mm). Numerical checks allow
up to twice that export grid even when intentional simplification is disabled.
Separately counted polygonization debris is removed only when its area is at
most 1e-9 mm² and its bounding-box diagonal is at most 0.001 mm. These facts are
recorded in the report; a zero-tolerance run is not a claim of bit-exact geometry.
Printable connections and holes must survive the remaining topology checks.

In the GUI, choose **Tools → Weave prepared 3MF — experimental…**. This file-based
dialog leaves ordinary Weave behavior unchanged, runs the backend in a separate
process, and shows bounded progress/log output without loading either full mesh.
It confirms replacement of an existing output once, preserves the input path,
and supports cancellation. Changing the ordinary layer height also updates the
first-layer display when “Same as ordinary layer height” is selected.

The Python API is opt-in as well:

```python
from layerloom.contour_options import ContourOptions
from layerloom.weave import weave_pipeline

report = weave_pipeline(
    "prepared.3mf", output_path="contour-woven.3mf", step=0.08,
    weave_mode="layer-contour",
    options=ContourOptions(xy_tolerance_mm=0.02, first_layer_height=0.16),
    export_stls=False,
)
```

The new backend returns its report dictionary and writes `.report.json` and
`.layers.jsonl` sidecars. Legacy API calls retain their existing behavior and
return value. `layerloom-headless` accepts the same new flags, but they cover
only the prepared-3MF weaving stage of its GLB workflow.

## Memory and compatibility

The bounded path starts with **prepared labeled 3MF** and streams source
geometry, disk indexing, active-layer work and output. The working set depends
on a layer's complexity; a pathological layer can exceed the budget. A budget
failure must be reported, not silently retried through the full-memory backend.
Scratch defaults to the output folder; `--scratch-dir` selects another folder.
The default free-space reserve is 4 GiB. The working-data budget is an allocation
and complexity guard, not an operating-system hard RSS limit; measure observed
process-tree RSS separately. Cancellation or disk exhaustion must leave the
previous final 3MF intact. Core validation reports do not claim full slicing or
physical-print validation.

GLB import, automatic orientation, ordinary viewer loading and third-party
slicing are separate stages. They are not covered by this memory claim. The GUI
file-based action avoids a mandatory full source/woven-model preview. Output is
3MF; legacy STL export behavior is separate.

`NOWEAVE` parts keep their geometry and any known uniform display color from
`source_hex` or a 3MF base material. Unknown colors remain unassigned instead of
being changed to cyan. Nonuniform or unresolved per-face material properties
are rejected for these parts. Source extruder numbers are retained as metadata
provenance only; map the output parts to the intended printer filaments in the
slicer. Source meshes copied unchanged are not repaired or independently
certified as float32-safe closed solids.

## Surface representation and unfinished optimization

The runtime exporter writes closed layer prisms with a 0.0001 mm between-band
numerical guard. The guard lies between the declared slicing planes. It does
not perform a whole-model Boolean or merge adjacent-layer caps. Touching contour
rings are handled by splitting disconnected incident triangle fans before
extrusion, preserving their coordinates while keeping valid mesh topology.

`contour_surface.py` is an **unintegrated research prototype**, not a selectable
runtime backend. Its adjacent-cap differences and rolling indexed frontier pass
analytic cases, but valid touching-hole geometry in a real Benchy hull still
fails its interface-link check. Correct integration needs fan-aware frontier
indices and delayed or patchable faces. Its rejection regression is intentional;
passing those tests does not establish that the cap-merging approach is ready.
The working exporter continues to use the validated closed-band path.

## Reproducible Benchy measurements

Install the `bench` extra, which includes `psutil` and plotting dependencies.
Run benchmarks in a new directory; do not overwrite a previous evidence set.
Start with the equal-height case, then prepare the upright fixture for the
practical unequal-first-height case. The original tutorial is tilted onto a
corner; that sliver makes first-layer slicer diagnostics unusually sensitive.
The preparation script pins the original file hash, records the transform, and
leaves the original unchanged. Orientation must be set before weaving.

```sh
python -m layerloom.contour_benchmark run examples/tutorial_cmy_benchy_cutup.3mf \
  --output-dir /path/benchy-020 --step .20 --first-layer-height .20 --repeats 3 --visuals
python scripts/prepare_contour_benchy.py --output /path/upright-source.3mf
python -m layerloom.contour_benchmark run /path/upright-source.3mf \
  --output-dir /path/benchy-008 --step .08 --first-layer-height .16 --repeats 3 --visuals
```

The default matrix is legacy, new mode with zero simplification, 0.01 mm, and
0.02 mm. Every weave runs in a fresh subprocess. Reports include source/output
stored mesh counts, compressed and uncompressed geometry bytes, hashes,
wall-clock time, observed peak process-tree RSS, core validation reports, and
explicit downstream validation status. RSS is sampled at 50 ms; it is an
observed peak, not an exact allocator high-water mark. Median runtime and the
largest observed RSS are aggregated across repetitions. Only the first repeat's
large deliverables are retained by default; hashes and metrics remain for the
others. Use `--keep-repeats` to retain all files.

Legacy historically uses the ordinary weave height for its first geometry
band. The runner records this rather than pretending it supports a distinct
first height. For unequal first layers, new mode with zero simplification is the
controlled reconstruction reference; legacy remains an explicitly separate
compatibility comparison.

### Independent serialized-3MF contour audit

Reopen the source and the delivered 3MF independently after weaving:

```sh
python -m layerloom.contour_validation prepared.3mf contour-woven.3mf \
  --report contour-woven.audit.json
```

The auditor reads the candidate's adjacent `.report.json` for its actual first
height, ordinary height, first-layer color and tolerance. It verifies both file
hashes, derives every nominal slicing plane from the source placement, then
compares material contours, their combined outline and positive-area material
overlaps. Component/hole counts must match, and two-way boundary-buffer
containment must certify the tolerance. Reported Hausdorff distances are useful
diagnostics; they are not the acceptance certificate. `--tolerance-mm` can
explicitly override the reported tolerance. Exit status is zero only on a pass.
Boundary copies retain their coordinates but clear fixed-precision metadata for
buffering, so lattice rounding cannot expand the certified distance allowance.

The two artifacts use separate disk-backed stores; layer records stream to an
adjacent `.layers.jsonl` file rather than accumulating all layers in memory.
Directly copied single-color parts remain included in the contour comparison.
NOWEAVE parts are checked separately by indexed float64 geometry, including
transforms and winding. Current export uses 17 significant digits to retain
float64 coordinates; the audit identifies earlier nine-decimal files explicitly. Preserved
source colors and source-extruder provenance must also match; that provenance
does not assign a physical printer slot. NOWEAVE parts are not silently excluded
from the aggregate pass.

This is independent artifact readback, not an independent slicing-kernel proof:
both paths deliberately share the declared intersection/export grids and debris
policy. Proven complementary vertical sidewall triangles are paired to recover
their original rectangle edge. When the entire section has such pairs, its
original boundary vertices bypass intermediate intersection rounding; this
preserves very narrow lattice holes. If those vertices already lie exactly on
the export lattice, the precision metadata is attached without another topology
normalization. Mixed or unmatched sections retain ordinary slicing. Every part
still undergoes the same contour comparisons.
The audit does not certify full-mesh topology away from the slicing planes,
physical printability, or slicer-generated wall centerlines, widths and seams.
Use the following full-slicing comparison for those toolpath checks.

Local regression evidence for the equal-height Benchy is retained in
`experiments/layer_contour/benchy/benchy_020_readback.audit.json` and its layer
JSONL. After fixing precision retention, combined-outline checks and exact
sidewall pairing, all 182 planes passed at 0.02 mm: no material, union or overlap
failures, with observed maximum boundary Hausdorff distance 0.0149897 mm.
The candidate contains 947,848 triangles and is 12,038,388 compressed bytes.
Earlier generated candidates failed this readback check even though their
in-process counters passed; their old counters are not validation evidence.
This case uses 0.20/0.20 mm layers and does not establish the unequal-first-height
or downstream wall-path results.

### Full slicing and wall-path comparison

A successful 3MF import is **not** a complete slicing test. Supply
`--slicer-command-json command.json` with an argv array for the installed
slicer's full-slice command. The command must use a fixed printer/process/
filament configuration and write a G-code file at `{output}`. Available template
substitutions are `{input}`, `{output}`, and `{output_dir}`; no shell is invoked.
Do not put an import-only `--info` command here. Record the exact slicer version,
profile hashes, physical filament mapping and placement with the results.

Without that command, `wall_paths_validated` stays false. With it, the runner
measures slicing separately and compares each result to the zero-simplification
run for the same repetition. Comparison errors and failed slices are not passes.

For independently exported G-code:

```sh
python -m layerloom.contour_gcode reference.gcode candidate.gcode \
  --tolerance-mm .02 --width-tolerance-mm .02 --report walls.jsonl
```

The parser streams one wall layer at a time, understands absolute/relative E,
G92, tool changes, positive extrusion and Orca/Prusa wall annotations. Travel,
retraction-only moves, and harmless segment subdivision are ignored. Layer Z,
filament, outer/inner/overhang classification and width are retained. Unknown
widths cannot receive a width-validation pass. Extruding arcs, nonplanar walls,
volumetric E, inch units and sequential-object layer resets currently fail
closed; disable these modes for the controlled comparison. The validator
compares nominal annotated widths, not measured physical bead widths or flow.

Wall displacement uses continuous line-set coverage by inscribed round buffers,
not sparse point samples. Its reported conservative upper enclosure has at most
0.001 mm search residual. Missing tool/feature layers fail. Widths are compared
spatially, so swapping widths between two walls cannot pass just because the
minimum and maximum widths match. Seam gaps are part of the compared line sets;
use a fixed seam for the primary test, then repeat with normal seam settings.
A separate combined-feature, per-filament displacement diagnostic distinguishes
actual deposition changes from spans reclassified as outer/overhang walls; it
does not relax the strict validation gate.

## Visual review and release gates

`--visuals` produces matched front, side, top and isometric **silhouettes**,
source/candidate difference images, and actual-layer color-contour overlays.
Silhouettes stream triangles into fixed-size raster buffers; they are not
shaded 3D surface renders. Visual sign-off is deliberately pending until someone
inspects the images. Review the bow/hull curvature, windows, chimney, roof
bridges, first/last layers, recipe transitions and objectively worst-error
layers. Additional layers can be passed to the Python `render_comparison(...,
zs=[...])` helper. Save close-ups of suspect areas and G-code wall overlays.

Required correctness gates:

- Every layer's contours and actual wall deposition checked; no missing walls,
  severed necks, lost holes or unintended color/height changes.
- Maximum wall displacement at most 0.02 mm for the default tier; include the
  0.01 mm tier and narrow-feature fixtures.
- Exact serialized coordinates, including translated float32 coordinates,
  retain valid closed/oriented topology without collapsed triangles or
  duplicate internal faces.
- Native import **and full slicing** succeed under the recorded profile.
- Numerical and visual findings both pass; neither substitutes for the other.

Stress tests should vary source tessellation density and layer count by 1x/4x/16x
while keeping active cross-section complexity comparable. Compare RSS and scratch
usage, and exercise cancellation, full disks, malformed indices and oversized
layers. The effectiveness goal is at least 50% fewer triangles and 50% lower
peak weaving RSS on the colored Benchy. A missed goal is reported honestly;
correctness never becomes negotiable to meet a reduction percentage. Keep this
mode opt-in until complex-model validation supports a separate default-change
review. Automated tests never upload or start a physical print.

The reproducible stress fixture generator streams 3MF directly and retains no
STL. It increases surface tessellation without changing the box's geometry, or
increases height/layer count while retaining its cross-section:

```sh
python scripts/benchmark_contour_stress.py --output-dir /path/stress
```

The local final-core run in `experiments/layer_contour/stress_final/stress_summary.json`
completed all 18 fresh subprocesses (three repetitions per case), with complete
RSS observation and no reported contour failures:

| Series | 1× | 4× | 16× | Peak RSS at 1× / 4× / 16× |
| --- | ---: | ---: | ---: | --- |
| Source triangles, fixed 10 layers | 49,152 | 196,608 | 786,432 | 132.3 / 165.0 / 211.7 MB |
| Layers, fixed 12 source triangles | 100 | 400 | 1,600 | 96.6 / 98.4 / 103.5 MB |

Here MB means 1,000,000 bytes. Peak RSS grew 1.60× for 16× source triangles and
1.071× for 16× layers; memory was bounded in these cases, not perfectly flat.
These simple cross-sections do not substitute for the Benchy fidelity and
toolpath gates. The measurements cover weaving, not downstream slicing or
printer transfer, and concurrent local jobs may affect recorded runtimes.

### Installed Snapmaker Orca validation adapter

The `slice-orca` subcommand stages a generated contour 3MF as a native Orca
project, using an explicitly supplied flat project-settings JSON:

```sh
python -m layerloom.contour_benchmark slice-orca benchy-contour.3mf \
  --output benchy-contour.gcode --profile-json project_settings.json
```

This adapter currently supports CMY output only and assigns cyan/magenta/yellow
to U1 slots 1/4/3. It uses a fixed XY placement derived from source bounds, copies
printer custom G-code unchanged, sets the manifest's layer heights, and uses
classic three-wall generation, 0.012 mm resolution, rear seam, with supports and
prime tower disabled for the controlled wall comparison. Original first-layer
width, thin-wall detection and elephant-foot compensation are preserved. The
explicit `--thin-wall-fixture` diagnostic option enables thin walls, uses a
0.42 mm first-layer width, and disables elephant-foot compensation; it is only
for examining tilted/sliver fixtures, not a recommended printing setup. Its `.slice.json`
records all overrides, hashes, the actual full-slice command, timing and RSS.
It runs `--slice 1`, checks the resulting G-code, and never uploads or starts it.

For the benchmark runner, use an argv-template file containing this subcommand
with `{input}` and `{output}` substitutions. The adapter reads the new mode's
manifest. For legacy input, also supply `--step`, `--first-layer-height` and
`--source-reference` so its geometry receives the same placement and schedule.
The source reference is a prepared labeled 3MF, not the already woven model.
If process enumeration is restricted by the host sandbox, measurements record
`rss_measurement_complete: false`; those results cannot certify a memory gate.

### Compact evidence bundle

Collect completed benchmark, slice and wall-summary JSON files without loading
models or whole G-code files:

```sh
python -m layerloom.contour_benchmark summarize \
  --benchmark-json /path/legacy/benchmark.json /path/contour/benchmark.json \
  --slice-json /path/contour/contour-0-r1.slice.json /path/contour/contour-0.02-r1.slice.json \
  --wall-json /path/walls.jsonl.summary.json \
  --output-dir docs/benchmarks/layer-contour
```

The resulting `report.json` preserves profile/input/output hashes, explicit
failures, runtime/library versions, timing and RSS. `report.md` is the compact
human-readable view. Add `--visual` image paths and a specific
`--visual-review-note` only after inspecting them. `--measurement-note` records
conditions such as concurrent validation work; these matter when interpreting
runtime. Current summary-environment metadata is separate from metadata captured
at each benchmark's start, so earlier reports without environment capture remain
identifiable. Failed slices and wall comparisons must be included in the bundle,
not filtered out to make the gates pass.

For a failed wall comparison, `contour_gcode --diagnostic-prefix /path/zoom`
adds close-ups at the largest tool-specific wall differences. It records nearby
segment classes/widths and nominal swept-wall footprints (each segment buffered
by half its annotated width), including missing/extra area and set-distance
bounds. These diagnostics distinguish a shifted inner-loop tip from a seam or
feature-label change. They do not weaken the centerline gate and do not model
measured bead shape, flow, solid infill, or an actual printed result. Repeat a
full slice of the identical reference model/profile as a control before
attributing differences to simplification.
