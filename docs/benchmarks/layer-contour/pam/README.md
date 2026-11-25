# Pam full-model evidence

Both the zero-simplification reference and the 0.02 mm candidate pass all
2,162 serialized-contour checks and complete native Snapmaker Orca 2.3.5
slicing. **The candidate fails the 0.02 mm wall-preservation gate at inspected
locations.** Use zero intentional XY simplification for this model/profile;
keep the 0.02 mm candidate experimental.

**Status: all 2,161 emitted wall layers have been compared and the three
largest sampled local cases have been inspected.** No physical print, printer
upload or GUI-transfer benchmark was performed.

## Input and controlled settings

The input is the previously approved, already simplified, labeled TCR–pMHC
ribbon/stick model: **321,328 triangles, 160,568 vertices, 5,298,890 bytes**.
Its SHA-256 is
`dbcb7e3d5129afd0634329a3ed093d2511c888b1808254275435fcc892193cb0`.
The existing scale and tilted orientation were reused, giving approximately
220.55 × 180.46 × 173.02 mm bounds. No molecular preparation, rotation or
rescaling was repeated in this experiment.

Both modes use a yellow 0.16 mm first layer, then fixed 0.08 mm layers.
Filament slots are cyan 1, yellow 3 and magenta 4. Recovered ribbon recipes are
unchanged; peptide carbon/sulfur uses `ccy`, nitrogen `cccm`, and oxygen `mmymy`.
All recipes and artifact hashes are in [metrics.json](metrics.json).

Both native projects preserve the saved tree supports, contact gaps, XY
placement, printer custom G-code, 29 mm tower, 18 mm tower brim, 16 mm extra
ribs and 12 mm rib width. Normal Lift replaces Auto Lift on all four heads;
the 0.4 mm hop height and every other profile setting are preserved. Native
files must be opened as projects with their settings. Changing the orientation,
scale or layer schedule invalidates the specialized woven geometry.

## Geometry and resource measurements

| Measurement | Zero intentional XY simplification | 0.02 mm candidate |
| --- | ---: | ---: |
| Native Normal Lift 3MF bytes | 170,599,009 | 151,922,943 |
| Triangles | 14,238,188 | 12,685,892 |
| Weaving time | 377.48 s | 433.57 s |
| Observed peak weave process-tree RSS | 222.17 MB | 224.30 MB |
| Passed serialized contour planes | 2,162 / 2,162 | 2,162 / 2,162 |
| Observed maximum contour displacement | 0 mm | 0.01500 mm |

The preceding legacy-woven artifact has 14,190,142 triangles and 204,456,707
bytes. The candidate is about 26% smaller on disk with 11% fewer triangles;
the 50% triangle-reduction goal was missed. The zero case is about 17% smaller
on disk, with a slightly higher triangle count. These are comparisons with the
existing artifact, not a newly timed Pam legacy run. Weaves ran concurrently,
so elapsed times are contextual rather than isolated throughput measurements.
MB/GB here are decimal; reports retain exact bytes.

The candidate falls back to exact contours on **1,335 of 2,162 planes**:
1,172 at initial simplification and 163 at the final export-grid guard.
Independent sampling of 80 fallback planes plus 12 accepted controls reproduced
every classification. Most sampled rejections involve thin material-contact
gaps, holes or overlap slivers. These are **rejected proposals**, not defects
accepted into the delivered candidate. Some narrow features are larger than
numerical roundoff; relaxing these guards to improve compression would change
the preservation policy. See [geometry evidence](evidence/geometry.json).

## What is measured

1. The prepared 3MF is streamed into disk-backed vertex/triangle spools and a
   spatial index. Each active cross-section is processed independently; mesh
   arrays for the whole model are not retained in memory. The working-data
   budget is 1,024 MiB, not an operating-system RSS ceiling.
2. Accepted material contours become closed layer prisms with a 0.0001 mm
   between-plane guard. Source intersections use a 1e-7 mm grid and a common
   binary export grid of 2^-16 mm. Export uses 17-digit float64 round trips.
   Zero tolerance disables discretionary XY simplification, not this numerical
   regularization. The observations are relative to regularized source planes.
3. An independent readback reconstructs every serialized candidate plane and
   checks material boundaries, union/overlap geometry, components and holes.
   It shares the slicing kernel with the builder; it is not a separate-kernel
   proof or a universal full-mesh topology certificate.
4. Both native projects are fully sliced with the same saved printer/process
   settings. Wall analysis reads one printed layer at a time, preserving
   machine and extrusion state. Named model-object regions scope the analysis;
   support, tower, infill, travel and custom-purge moves are excluded from the
   model-wall gate. Outer, inner and overhang walls remain separate in the
   strict gate; a combined per-filament diagnostic distinguishes relabeling
   from physical centerline changes.
5. Continuous wall Hausdorff enclosures use a 0.001 mm residual and a 0.02 mm
   acceptance threshold. Spatial width compatibility uses 0.02 mm. A separate
   diagnostic samples bidirectional nearest-wall distance at no more than
   0.01 mm spacing, weighted by path length. Its quantiles and affected-length
   estimates do not certify extrema or entire paths.

The full wall run uses six worker processes, at most eight submitted layer
pairs, and one streaming parser. It calls the unchanged certificate/sampling
functions, preserves layer order, and reduces each sampling chunk in serial
order. Eleven real-plane controls, including a denser plane, match the serial
rows and aggregates **exactly**. Job-specific, portable analysis snapshots are
under [methods/](methods/); no complete G-code or mesh is buffered by these
runners. They record the Pam investigation rather than a stable public API.

## Auditor correction discovered by Pam

Weaving used commit `88a73e4ec1a32a3ff512124e7fca346d5cbd9dff`.
Pam exposed a readback bug: a redundant union could erase an extremely narrow
valid hole in an already certified, single-part, lattice-exact section. The
fix at `3af5fb10cbdd30b5b5615df6956fa6a63048bcec` preserves that section
directly; generic and multiple-input paths retain their prior behavior.

All 2,162 source planes were shown unchanged by the fix, and the existing
exported 3MFs required **no regeneration**. Both final
`serialized-contours-3` audits pass; earlier revision-2 failures are superseded
diagnostics. The regression fixture is included in the repository's tests.

## Native slicing and the memory workaround

| Full native slice | Zero reference | 0.02 mm candidate |
| --- | ---: | ---: |
| Time | 166.65 s | 169.66 s |
| Observed process-tree RSS | 8.56 GB | 7.81 GB |
| Largest process lifetime physical footprint | 10.78 GB | 10.63 GB |
| Observed system swap growth | 0 | 0 |
| G-code bytes | 717,448,319 | 712,612,309 |
| Estimated print time | 4 d 1 h 24 m 45 s | 4 d 1 h 16 m 28 s |

Measurements are from native CLI slicing on a 16 GB arm64 Mac, separate from
weaving. Physical footprint accounts for memory that RSS can miss after
compression or swapping. Neither value measures a GUI transfer to the printer.

The matching release's [official G-code processor source](https://github.com/Snapmaker/OrcaSlicer/blob/761718a5eaf6f52b818af236653f3807d091a613/src/libslic3r/GCode/GCodeProcessor.cpp#L4807)
copies the current arc-interpolation vector into subsequent move records;
ordinary linear processing does not clear it. Auto Lift emits spiral G3 moves.
A source-based calculation from the partial Pam file estimates 3.75 GB of
duplicated raw point payload already around halfway through the print. This is
an estimate, not an allocator measurement. The Auto Lift run hit a 10 GiB
physical-footprint stop during export. Normal Lift emitted no arcs and both
full exports completed, with the substantial memory use shown above.
The [memory evidence](evidence/memory.json) records the official v2.3.5 source
commit and the estimate's assumptions. No native binary was patched.

A bounded Auto/Normal Lift control compares **all 1,090 complete shared model
wall layers**, Z 0.16–87.28 mm. All **3,042,892 wall segments** are identical
in order, endpoints, widths, tools, features and extrusion. The incomplete
Z=87.36 layer and unavailable upper layers are excluded. All 14,683 first-layer
deposition segments, including supports and tower, also match exactly.

An identical-reference full reslice reproduces every executable command in
order. Only the generated timestamp and 11,406 runtime-ID comments differ;
canonicalizing only those nonprinting IDs also makes the feature/object-marker
sequence identical. The strict byte-difference result is retained rather than
described as a byte-identical file. This is a determinism control for this exact
project/profile/binary pair, not a universal slicer guarantee.

Both files emit 2,161 print layers, Z 0.16–172.96 mm, while the planned footer
count is 2,162 with final planned Z 173.04 mm. The tiny final tip is a plausible
reason for an empty-extruder layer being omitted; this is an inference. The two
emitted schedules match exactly. See [slicing](evidence/slicing.json) and
[controls](evidence/controls.json).

## Wall result and printing choice

The full comparison covers every one of the **2,161 emitted model-wall layers**
in both completed files. **2,159 layers fail the strict gate and the combined
per-filament centerline upper-bound gate**; 1,862 have a combined lower bound
strictly above 0.02 mm. Failure of an upper-bound gate alone is not proof that
the true error exceeds the threshold. Most wall length nevertheless remains
very close:

| Length-weighted sampled wall metric, both directions | Result |
| --- | ---: |
| Total analyzed wall length | 8,955,453.48 mm |
| Midpoint samples, spacing ≤0.01 mm | 900,938,367 |
| Mean nearest-wall distance | 0.0002078 mm |
| 99th percentile, histogram upper bin | 0.0070 mm |
| 99.9th percentile, histogram upper bin | 0.0133 mm |
| Length more than 0.02 mm away | 1,015.81 mm (0.0113429%) |
| Length more than 0.10 mm away | 125.87 mm (0.0014056%) |
| Largest sampled distance | 1.22808 mm at Z=145.76 mm |
| Largest continuous combined-wall upper bound | 1.23234 mm |

The length denominator sums both files' wall paths and counts repeated passes;
it is not unique surface area. The much larger 20.42 mm feature-separated
maximum can include changes in wall classification and must not be presented
as physical displacement. Combining wall features retains filament identity
and reveals the remaining rare but substantial local differences.

Mapping printed planes back to the candidate's source-slab decisions separates
the substantial excursions from much smaller threshold-level differences:

| Source-slab status | Emitted layers | Sampled length >0.02 mm | Fraction of that subset's bidirectional length | Largest sample |
| --- | ---: | ---: | ---: | ---: |
| Simplification accepted | 826 | 1,006.035 mm | 0.0485948% | 1.22808 mm |
| Exact fallback | 1,335 | 9.772 mm | 0.000141934% | 0.0205582 mm |
| Exact fallback, with both adjacent slabs also fallback | 1,034 | 7.883 mm | 0.000140063% | 0.0205582 mm |

The last row is a subset of the fallback row, not additional layers. Every
large sampled excursion lies in an accepted-simplification slab. Exact source
contours do not imply byte-identical generated toolpaths: fallback slabs still
show small near-threshold changes, even with neighboring fallback slabs.
The full strict gate is unchanged. Spatial width compatibility also fails on
2,159 layers, but that gate tests nearby paths with compatible widths; the count
does not mean that 2,159 layers changed their annotated extrusion width.

The three largest sampled cases occur on **inner walls**, not the model's outer
silhouette. The [worst-case figure](figures/worst-wall.png) shows an inward wall
spur at Z=145.76 mm that the candidate omits. Including infill and other
filaments does not recover its sampled point. The nominal local deposited-area
distance is 1.120085–1.120711 mm, with 1.22116 mm² removed in the shown crop.
The whole layer's outer-plus-overhang centerline upper bound is only 0.268234 mm;
the 1.22808 mm inner-wall discrepancy is not an external surface shift of that
size. The figure's tool 3 is the G-code's zero-based identifier for filament
slot 4.

| Inspected print Z | Largest sampled centerline distance | Local all-model nominal footprint distance enclosure | Interpretation at sampled point |
| --- | ---: | ---: | --- |
| 145.76 mm | 1.22808 mm | 1.120085–1.120711 mm | Inner-wall spur omitted; infill does not recover it |
| 142.00 mm | 1.00229 mm | 0.184841–0.185375 mm | Mostly wall/infill substitution; both versions cover the point |
| 118.64 mm | 0.87178 mm | 0.759090–0.759824 mm | Partial infill coverage does not recover the inner-wall point |

These are nominal-width swept-area diagnostics, not measured physical bead
shapes. They demonstrate why a centerline discrepancy should be investigated
before interpreting it as material loss, and why the wall-preservation gate
still fails here. An earlier Z=20.48 mm acute-tip example also had a real local
deposition difference (about 0.227 mm), despite only 0.014658 mm source-contour
change. No molecular branch loss or whole-silhouette shift is inferred.

These measurements justify keeping simplification experimental and selecting
the zero case for this print. The zero case is a controlled reference, not a
claim that every slicer decision or physical print is ideal. Future acceptance
guards need profile-specific offset/inner-wall checks; they are not implemented
by this result. [Wall evidence](evidence/walls.json) retains the strict gate,
sampled distribution, source-slab stratification and all three local reviews.

## Visual and first-layer evidence

[Actual-plane contours](figures/contours.png) and
[projection silhouettes](figures/silhouettes.png) show no gross missing
branches, ribbons or exposed struts. Their raster resolution is coarser than
the numerical tolerance and cannot establish fine geometry fidelity.

The [zero-reference first layer](figures/zero-first-layer.png) is independently
parsed from its own completed G-code, including all 78 yellow brim segments.
It fits the nominal 270 × 270 mm bed with a minimum 6.325 mm rear margin.
The tower plus 18 mm brim spans approximately 86.47 × 86.69 mm; its nearest
support gap is 2.225 mm, with no tower/support overlap. The candidate's support
layout differs and has a 4.779 mm gap; its plot is not substituted for this one.

The zero model brim has 0.760 mm² of nominal bead-edge overlap with support
edges. Their centerlines do not cross and remain at least 0.4498 mm apart for
0.5 mm annotated widths. The actual model-body footprint does not overlap
supports. Width/2 swept areas are diagnostics, not physical bead measurements;
first-layer clearance does not certify upper-layer carriage clearance,
adhesion, support removal or mechanical strength.

## Artifacts and reproduction scope

[metrics.json](metrics.json) provides source/output hashes, versions and exact
byte counts. [manifest.json](manifest.json) records source-report and figure
hashes. The numerical reports are selected, path-sanitized evidence; full
layer JSONL, logs and model/G-code binaries remain local. The public bundle
does not contain the approved prepared model or saved user printer profile,
so it is not a stand-alone source-model reproduction package.

With the matching prepared model, the weaving options are:

```sh
python3 -m layerloom.weave -i prepared.3mf -o pam-zero.3mf \
  --weave-mode layer-contour --step 0.08 --first-layer-height 0.16 \
  --xy-tolerance-mm 0 --first-layer-color y --memory-budget-mib 1024 \
  --scratch-dir scratch --no-export-stls
```

The candidate changes only `--xy-tolerance-mm` to `0.02`. Native packaging
then retains the saved profile/placement and applies the documented Normal Lift
override. Full slicing is necessary before the model-wall comparison:

```sh
python3 methods/parallel_wall_audit.py zero.gcode candidate.gcode \
  --output-prefix results/pam_walls --workers 6 --inflight 8 --visuals
```

This runner requires LayerLoom and the documented NumPy/Shapely environment.
It refuses to overwrite existing output files. Storage cleanup preserved both
Normal Lift projects, generic forensic masters and the recommended zero G-code
ready to open. The experimental candidate G-code is retained as a verified
lossless gzip archive (204,291,097 bytes); its decoded hash matches the audited
712,612,309-byte file. Removing redundant Auto Lift projects and a duplicate
archive, compressing logs, and compressing the experimental G-code recovered
1,167,919,873 bytes (1.168 GB). The temporary repeat G-code was separately
removed after verification. The [retention ledger](evidence/retention.json)
records hashes.
