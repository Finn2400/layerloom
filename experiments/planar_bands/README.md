# Fixed-layer contour reconstruction experiment

This experiment supports adding an optional layer-resolution reconstruction
backend to LayerLoom. It demonstrates a repair that the existing 3D cutter does
not complete, while exposing limits that prevent enabling it as the default.
It reconstructs printable layer geometry; it does not restore the original
continuous surface or infer missing material assignments.

Branch: `codex/layer-contour-repair`. The isolated worktree starts at
`6b000eef` on `headless`. Uncommitted changes in the primary checkout were not
copied or modified. The existing weave, import, and repair paths are unchanged.

## What the prototype does

`src/layerloom/planar_bands.py` implements the geometry backend:

1. Intersect raw triangles with the globally aligned plane
   `z0 + (layer_index + 0.5) * layer_height`. Input winding and mesh connectivity
   need not define a globally watertight volume.
2. Round XY endpoints to an explicit precision grid and deduplicate segments.
3. Bridge a gap only when each endpoint has exactly one candidate within the
   configured distance and the bridge does not cross existing linework. Record
   every bridge and its length. Nearby complete islands are not bridged.
4. Node the lines and reject branching vertices or unresolved open chains.
   Polygonize the graph, selecting faces with an even-odd rule so nested holes
   and islands survive. Crossings and overlapping shells in one input part are
   deliberately rejected rather than assigned an implicit fill interpretation.
5. If this strict graph fails, try the source mesh's ordered closed section
   loops, separated by face connectivity. Repair tiny invalid loops with bounded
   linear simplification. Require every loop to remain a valid single polygon,
   preserve nesting and existing point contacts, and check both area change and
   mutual boundary coverage within the contour tolerance. The final boundary
   must also cover all raw section segments within that bound, including small
   dangling spurs omitted by the closed-path extractor. This fallback does not
   assign a fill to arbitrary branching linework.
6. Normalize XY coordinates to the actual XML/float32 export representation
   before triangulation, then extrude through the full nominal layer interval.
   Use constrained Delaunay caps when available, with checked earcut fallback.
   Separate disconnected triangle fans at point contacts instead of welding
   them into a non-manifold edge. Check positive volume, cap area, and triangle
   survival at export precision.
   Retain failures and diagnostics for the caller instead of skipping them.

`src/layerloom/contour_repair.py` is an independent command-line entry point.
It retains part PAT recipes, uses a supplied fallback recipe for unlabeled
parts, and unions same-filament regions before snapping each material once to
the common XY precision grid. Clearing GEOS's inherited precision model then
prevents intermediate intersections from snapping again. A cross-material overlap is assignable only
when it lies within the seam tolerance of **both** materials' boundaries;
alphabetical material-token priority deterministically owns those cells.
Substantial overlap, erasing an entire material, movement outside the permitted
boundary bands, residual overlap, or a changed union footprint is an error
above a numerical area threshold of `0.01 * precision**2`; all residuals are
reported. Grid snapping also rejects loss of a whole disconnected component.
This is a bounded seam policy, not an inferred interior color assignment.
It validates exported float32 geometry and reopens the written native volumes.
Equal top and bottom cap unions plus vertical sides establish each band's
constant XY footprint without a second snapping operation. It checks individual
and combined footprints, separately reports missing and added area, and checks
residual material overlaps against both boundaries' rounding budgets. Float32
coordinates can leave microscopic overlaps or gaps despite disjoint intended
regions; the report does not hide these with a new precision grid.
Any failed layer prevents a new
output file; an existing output is preserved and a JSON failure report is
written.

Output is one mesh object with named per-band volume ranges in PrusaSlicer's
`Metadata/Slic3r_PE_model.config`. This retains all bands' placement in Prusa;
independent build items and a generic component assembly both failed the actual
slicer check by importing floating bands as separate objects. The prototype
reopens its native ranges for per-band and combined-layer footprint checks.
Physical extruders are not assigned automatically: material/recipe names are
retained for assignment in the slicer. Ordinary LayerLoom ingest and other
slicers may see this output as a single uncolored mesh.

Defaults: 0.00001 mm XY precision, 0.03 mm maximum gap, 0.001 mm contour-repair
tolerance, 0.001 mm material-seam tolerance, and a 0.001 mm internal Z gap.
These are separate experiment parameters, not calibrated fabrication settings.
Set either contour or seam tolerance to zero to disable that repair policy.
Rounding moves an initial endpoint by at most `precision / sqrt(2)`; bridging
adds a new segment and is an explicit geometric assumption even when the
distance is small. Contour repair additionally checks the actual resulting
boundary; a simplifier's requested tolerance alone is insufficient for an
initially invalid polygon.

The approach has precedent in Cura's
[per-layer segment stitching](https://ultimaker.github.io/CuraEngine/docs/slicing.html).
[Shapely polygonize_full](https://shapely.readthedocs.io/en/maint-2.0/reference/shapely.polygonize_full.html)
provides the polygons, cut edges, dangles, and invalid rings used for diagnostics.
The primary checkout also contains related `surface_field_weave.py` and
`image_weave.py` experiments; these were inspected but are not dependencies of
this isolated implementation.

## Measured geometry results

The seven-fixture comparison uses 0.20 mm bands, sampled at z=0.10 mm, and a
0.03 mm repair limit. The baseline is the committed `strata._slice_between`
implementation, not an external slicer.

| Fixture | 2D reconstruction | Existing 3D cutter |
| --- | --- | --- |
| Closed box | Valid solid; exact footprint | Valid solid |
| Missing top and bottom | Valid solid; exact footprint | Valid solid |
| 0.02 mm side opening | Repaired; valid solid; exact footprint | Returns a non-volume mesh |
| 0.06 mm side opening | Explicit rejection | Returns a non-volume mesh |
| Hollow tube | Valid solid; cavity retained | Valid solid |
| Curved surface | Valid midpoint approximation | Valid solid |
| Thin feature at z=0.02–0.04 mm | Misses the z=0.10 mm sample | Retains the thin solid |

Missing caps are supported but were **not a demonstrated improvement** over the
current cutter in this experiment. The small side opening is the demonstrated
repair improvement. All five nonempty reconstructed bands survive binary STL
round-trip as positive volumes. The maximum round-trip footprint symmetric
difference is 0.000005728 mm². Box and repaired-gap differences are zero.

The independent geometry tests additionally cover unwelded triangle soup,
reversed winding, duplicate triangles, nested cavities with islands, nearby
separate islands, ambiguous bridges, invalid numeric parameters, nominal partial
top layers, and off-grid sampling differences. Timings in the fixture report
are single runs with initialization effects; they are not a speed benchmark.

See [comparison.png](results/comparison.png) and [report.json](results/report.json).

The initial prototype's combined geometry and CLI suite passed **46 tests**.
Its actual PrusaSlicer
2.8.0 command-line check with isolated settings also passes: the 10×10×2 mm
open-top cube produces ten extruding layers at Z=0.2 through 2.0 mm, and the
tutorial cubes produce forty at Z=0.2 through 8.0 mm. Both direct native-3MF
imports succeed without merging. The twelve recorded cases include direct and
merged variants and single-object STL controls; these are not twelve independent
model families. The profile is single-extruder and emits no tool-change commands,
so this validates geometry retention, not physical multicolor assignment or a
printed result. See [slicer_validation.json](results/slicer_validation.json).

## Why start with midpoint contours

| Representation | Benefit | Cost or ambiguity |
| --- | --- | --- |
| One midpoint contour per band (implemented) | Directly comparable to a fixed slicer sampling grid; preserves contour XY detail | Features between samples may disappear |
| Several contours per band, diagnostic only | Detects some shape or topology changes through the layer | Finite samples still cannot guarantee every thin feature is found |
| Union of several sampled contours | Retains more features and outward extent | Expands sloped surfaces and can close cavities; changes the print interpretation |
| XY raster plus morphological gap closure | Offers simple bounded image operations and material labeling | Adds XY quantization, feature-size thresholds, and potentially large memory cost |

The prototype uses vector contours, not a full 3D voxelization. A sensible next
extension is diagnostic supersampling before considering any union/intersection
policy. A thicker 2D band is useful only after its occupancy rule is made explicit.

## Existing repository input checks: historical baseline

The four-object `tutorial_cmy_cubes.3mf` passes at 0.20 mm: 160 source part-layer
profiles become 100 material bands after same-filament grouping, with zero
measured midpoint footprint change on export/reimport.

The initial prototype could **not** export the denser
`tutorial_cmy_benchy_cutup.3mf` (253,176 triangles, eight parts). On a 0.00001 mm
grid, 657 sampled part-layers contained
627 accepted profiles, 22 branching profiles, and eight empty samples. Subsequent
material assembly also found small overlaps, degenerate float32 triangles, and
an extrusion failure. That run published no final Benchy file.

A coarser 0.001 mm grid changes this to 630 accepted profiles, 19 branching
profiles, and eight empty samples, but still fails export and produces more
float32 validation failures in the evaluated material bands. Coarsening the grid
can introduce collapsed or touching features; it is not a general robustness fix.
Both runs stopped material extrusion on its first exception, so their export-error
counts are partial. See [real_input_summary.json](results/real_input_summary.json).

## Current Benchy validation

The updated backend successfully reconstructs the entire Benchy at 0.20 mm:
627 source profiles pass the strict path, 22 are recovered by bounded contour
cleanup, and eight midpoint samples are empty. There are no failed source
profiles. The 649 nonempty profiles become **349 material bands across 182
occupied layers**. All exported and reopened bands are positive indexed volumes
with no zero-area triangles or duplicate faces at float32 precision.

The largest measured contour correction is 0.000998919 mm, within the checked
0.001 mm bound. Material partitioning resolves 110 microscopic overlap pairs;
the largest reassigned area is 0.000102183 mm², with maximum union-footprint
residue 7.73e-14 mm². Equal-color regions are unioned before rounding, avoiding
new internal seams from unequal boundary subdivisions.

Float32 conversion moves XY vertices by at most 0.000010762 mm. Twelve material
bands require bounded normalization, nine change their microscopic hole count,
and 33 retain reported geometric point contacts. No whole component disappears.
The maximum reopened band footprint difference is 0.000371754 mm²; the maximum
complete-layer difference is 0.000389103 mm². Export retains 57 bounded residual
material-overlap pairs, the largest 0.000020112 mm². These are explicit numerical
approximations, not a claim of perfectly disjoint exported solids or universal
manifold repair.

PrusaSlicer 2.8.0 directly opens the native reconstructed 3MF and emits **182
layers with positive extrusion during XY movement**, at every expected height
from Z=0.2 through 36.4 mm. The check tracks G90/G91, M82/M83, and G92, so
travel, stationary priming, zero-E moves, and retraction do not count as
printing. The full repository suite passes **118 tests**, with one opt-in
headless integration test skipped. The seven-fixture benchmark still shows
the intended small-gap repair and large-gap rejection.

The original 3MF container is rejected by this Prusa version as lacking a valid
mesh. As a separate geometry control, its original build-transformed triangles
were concatenated into one binary STL without contour reconstruction or repair.
That control slices successfully to **181 extruding layers**, ending at Z=36.2
mm. The reconstructed bands therefore change the topmost toolpath outcome;
the count difference must not be presented as proof of identical reslicing.
The fixed midpoint model intentionally holds each sampled profile through a
whole layer. Equal Z counts or nonempty toolpaths would not by themselves prove
whole-footprint toolpath equivalence either.

The slicer uses a generic single-extruder profile and reports overhang/adhesion
warnings. This validates geometry consumption, not printer setup, physical
multicolor assignment, or a printed result. Reconstruction plus export checks
took about 153 seconds in one local run; slicing took about 6 seconds on the
final repeated check. These are observations, not controlled speed benchmarks.

The detailed local reconstruction report is
`results/benchy_repaired.report.json`; the generated asset is
`results/benchy_repaired.3mf`. Large generated models and full diagnostics remain
ignored by Git; compact measured evidence is retained in
[benchy_validation.json](results/benchy_validation.json).

## Spline interpolation probe

`spline_probe.py` compares four methods on five selected Benchy part-layer
profiles at 0.20 mm layer height: zero-based `(part, layer)` pairs `(0, 103)`,
`(6, 16)`, `(6, 35)`, `(6, 82)`, and `(7, 27)`. Three raw source rings already
contain tiny self-intersections before precision-grid snapping. To isolate
interpolation behavior, the probe first forms valid reference rings with
measured boundary cleanup below 0.0001 mm. Those references contain 1,608
vertices in total. This controlled comparison is separate from the production
fallback, whose default bound is 0.001 mm.

Each candidate must be valid, preserve the reference's component and hole
counts, and stay within a 0.01 mm measured boundary-distance budget. All twenty
candidates remain valid with the same component and hole counts; the sparse
cubics fail because of geometric overshoot, not self-intersection in these
five cases.

| Method | Accepted profiles | Largest measured boundary drift | Largest absolute signed area change | Total output vertices |
| --- | --- | --- | --- | --- |
| Topology-preserving linear simplification, 0.005 mm tolerance | 5/5 | 0.004977 mm | 0.028611 mm² | 379 |
| Periodic cubic through reference vertices | 0/5 | 3.831867 mm | 52.439478 mm² | 12,864 |
| Periodic cubic through simplified vertices | 0/5 | 3.887416 mm | 54.441260 mm² | 3,032 |
| Periodic cubic after uniform arclength resampling, at most 0.02 mm control spacing | 5/5 | 0.008958 mm | 0.0000534 mm² | 51,000 |

Cubics use chord-length parameterization and periodic boundary conditions.
The first two cubic variants sample eight points per control edge; the uniform
variant samples four. See [spline_comparison.png](results/spline_comparison.png)
and [spline_report.json](results/spline_report.json) for profiles, per-case
measurements, source hash, and library versions. Distances are measured on the
sampled polygonal curves; they are not certified bounds on every point of the
continuous spline. Signed area changes can cancel local errors, so the report
also records symmetric-difference areas.

The dense uniform spline is a viable approximation in this limited probe and
conserves signed area better than the linear control. Its larger local drift
and 51,000 output vertices make it a poor default repair for these contours.
The result rejects unconstrained sparse periodic interpolation as a shortcut;
it does not rule out corner-aware or adaptively bounded splines. The tested
implementation follows SciPy's
[CubicSpline](https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.CubicSpline.html)
and Shapely's
[simplify](https://shapely.readthedocs.io/en/2.1.2/reference/shapely.simplify.html)
APIs.

Spline fitting does not decide which side of a branch is solid or establish
consistent material boundaries. A tangent-matched local spline could be tested
for an already unambiguous short gap, with explicit displacement, crossing,
and material-partition checks; a straight bridge currently has a simpler
bounded contract. Interpolating contours between layers would first require
loop correspondence through births, splits, merges, and changing holes. None
of those additional policies is established by this five-profile experiment.

## Approximation and unresolved decisions

- The Z sampling grid is authoritative. XY contours can retain fine detail;
  there is no requirement to voxelize XY at the layer height. Inside each band
  the shape is constant, so slopes become steps. Partial tops can extend up to
  half a layer beyond the source or disappear when the midpoint misses them.
- Features entirely between sample planes can disappear, including a small
  feature attached to a larger body whose sample is otherwise nonempty.
  Empty-sample diagnostics alone cannot detect all such losses. Sampling several
  heights per band would help flag variation; unioning those samples would be a
  separate, explicitly outward-biased geometry policy.
- Open surface-color patches do not specify their interiors. A closed external
  envelope plus a material field or an explicit interior assignment rule is
  still necessary. Independently capping each painted patch is not justified.
- The current material partition preserves the union of its grid-normalized
  inputs and resolves only bounded seam overlaps. It does not establish coverage
  against an independent common envelope or fill gaps between source materials.
  Alphabetical ownership of ambiguous seam cells is a declared tie-break, not
  evidence of the intended source color. A more general material field needs
  an explicit source of interior ownership and a shared planar subdivision.
- Float32 normalization can close a sufficiently narrow slit or alter hole or
  component counts. It permits such changes only within the measured rounding
  band around the original boundary, reports those counts, and rejects erasing
  an entire source component. This is a filled-area bound; it is not a promise
  to preserve every sub-resolution hole or slit. Quantizing before triangulation
  prevents avoidable collapsed cap triangles during later export.
- Even-odd nesting treats an enclosed shell as a cavity. Nested solid components
  intended as additive volumes require an explicit different interpretation;
  the original intent cannot be inferred from geometry alone.
- Per-band validity does not establish a clean union of the entire assembly.
  Separate triangle fans can retain a geometric point contact while producing
  closed indexed volumes. Such contacts are reported explicitly; coordinate
  welding downstream can recreate non-manifold edges.
  The default 0.001 mm Z gap avoids coincident horizontal caps and changes volume
  by approximately 0.5% for interior 0.20 mm bands. Setting it to zero permits
  coincident interfaces; cross-object interface validity is not certified.
- A slicer must use the same Z alignment, orientation, first-layer height and
  subsequent layer height, with adaptive layers disabled. The printability
  argument is about those sampled footprints, not arbitrary later reslicing.

## Running the experiment

From this worktree's repository root, with LayerLoom dependencies installed:

```bash
PYTHONPATH=src python -m layerloom.contour_repair \
  input.3mf repaired.3mf --layer-height 0.20 \
  --precision 0.00001 --max-gap 0.03 --run cmy \
  --contour-tolerance 0.001 --seam-tolerance 0.001 \
  --report repaired.report.json

PYTHONPATH=src python -m pytest \
  tests/test_planar_bands.py tests/test_planar_extrusion.py \
  tests/test_planar_materials.py tests/test_contour_repair_cli.py \
  tests/test_planar_gcode_probe.py

PYTHONPATH=src python experiments/planar_bands/benchmark.py

PYTHONPATH=src python experiments/planar_bands/spline_probe.py \
  /path/to/tutorial_cmy_benchy_cutup.3mf

# Optional: requires the installed macOS PrusaSlicer application.
PYTHONPATH=src python experiments/planar_bands/slicer_probe.py

PYTHONPATH=src python experiments/planar_bands/benchy_validation.py \
  --input /path/to/tutorial_cmy_benchy_cutup.3mf --source-control
```

The source can be STL (assumed millimeters) or a millimeter 3MF supported by the
existing reader; other 3MF units are explicitly rejected. The existing reader's
extension limitations still apply. This is a whole-input opt-in path, not an
automatic per-layer fallback mixed with the smooth 3D cutter.
The Benchy asset is not tracked in this worktree; pass its local source path.

Further work includes broader dense-input, slicer, and print validation, an
independent coverage reference for material partitions, and diagnostic sampling
between nominal layer midpoints.
The evidence supports an optional alternative backend; it does not yet support
replacing the default workflow or claiming general non-manifold repair.

## Controlled repair and layer-creation benchmark

`controlled_benchmark.py` compares in-memory geometry work on tutorial cubes,
the cut-up tutorial Benchy, and intact/damaged versions of a deterministic
hollow tube. The tube is 20 mm tall, with 20/16 mm outer/inner diameters and
256 circumferential facets. Its damaged twin removes four wall triangles,
leaving a 0.020 mm outer seam and a 0.016 mm inner seam. Its intended annular
footprint is known independently of the repair algorithm.

Three methods use the same original inputs, orientation, scale, global Z
origin, original per-part band indices, PAT assignments, 0.20 mm layer height,
and 0.001 mm gap:

- `cut`: existing `_slice_between` calls, including their normal fallbacks.
- `repair_cut`: the actual MeshLab helper's default `repair_geometry` kernel,
  including its topology checks, followed by the existing global destructive
  repair rejection guard and the same cuts. Ordinary 3MF weaving can skip this
  prepass, which is why both older paths are included.
- `contour`: bounded midpoint profiles and source-part extrusion, followed by
  the current prototype's material partition and final material extrusion.

`matched_part_bands_s` measures repair plus source-part layer creation. The
additional `assembly_s` measures each backend's native color assembly: mesh
concatenation for the old paths, partition and another extrusion for contours.
`core_s` is their sum. This separation exposes the prototype's extra work;
its second extrusion and mandatory float32 checks remain included. The
benchmark adapter trims both ends of every nominal contour band by 0.0005 mm
to match the cutter, including the model's outer boundaries. The standalone
contour CLI normally exempts its first/last outer boundaries from that trim.

Each measurement runs serially in a fresh process, after the same small-box
warm-up. A fixed seed randomizes case order and rotates method order so each
method occupies each position once across three repeats. Common BLAS/OpenMP
environment variables are limited to one thread; this is not a guarantee that
every native library is single-threaded. The desktop is not CPU-pinned or
isolated from background applications. Hardware, library versions, source
hashes, CPU time, and every measured wall time are recorded.

Input loading, imports, warm-up, geometry hashing, export, common posthoc
audits, slicer work, and printing are excluded. Checks performed intrinsically
by each algorithm remain timed. The first repetition gets a common external
audit; subsequent results inherit it only if exact source and output geometry
hashes match. Individual run JSON files retain the detailed diagnostics; the
tracked aggregate retains timings, checks, hashes, and repair outcomes.

The common audit checks positive indexed volumes, float32 volumes, collapsed
triangles, duplicate faces, and original-input midpoint footprints before and
after native assembly. The declared footprint area budget is 0.001 mm times
the reference perimeter. Hole/component counts and boundary distances are
also recorded, but do not independently determine pass/fail. The tube uses its
independent intended polygon; real inputs
use bounded section reconstruction of the original source, so their reference
is not independent of the contour algorithm. Any reference or audit-only
contour repairs are reported. This common audit does not repeat the contour
CLI's exact exported cap-union and intermaterial seam checks; section repair
can mask microscopic seams. Passing these checks does not certify continuous
3D fidelity, all non-manifold contacts, or downstream slicer acceptance.
Raw `missing_bands` includes legitimate empty midpoint samples; actual loss
must be assessed using the reference/audit diagnostics. A failed profile
reconstruction also cannot establish that material is physically absent.

Run from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python \
  experiments/planar_bands/controlled_benchmark.py

PYTHONPATH=src python experiments/planar_bands/controlled_plot.py \
  experiments/planar_bands/results/controlled_benchmark.json \
  --prism-audit experiments/planar_bands/results/controlled_prism_audit.json

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest \
  tests/test_controlled_benchmark.py tests/test_controlled_worker.py
```

The cube and Benchy files must be available in `examples/`; the worker also
supports `--input` for a different local path to either example. A separate
one-repeat cube pilot validates the harness and is excluded from measured
repetitions.

### Measured results, 22 October 2026

All 36 workers completed on an Apple M1 Pro with 10 CPU cores and 16 GiB RAM.
Each case/method produced identical exact source/output hashes across all three
repetitions. The aggregate is in
[`results/controlled_benchmark.json`](results/controlled_benchmark.json).
These are local measurements of this implementation, not a hardware-independent
performance claim. The intact-tube cut-only arm includes one slow wall-time
observation; it remains in the reported range.

Repair plus source-part layer creation, seconds: **median [minimum–maximum]**.
Bounded 2D contour repair is included in the contour column's layer time.

| Input | Existing cuts | MeshLab repair + cuts | Contour reconstruction |
| --- | ---: | ---: | ---: |
| Tutorial cubes | 0.217 [0.203–0.236] | 0.208 [0.206–0.210] | 0.252 [0.248–0.255] |
| Benchy | 11.430 [11.088–12.652] | 12.255 [11.817–14.294] | 34.545 [34.377–34.595] |
| Intact hollow tube | 1.139 [1.131–2.443] | 1.166 [1.154–1.179] | 6.277 [5.842–6.353] |
| Tube with 0.020 mm seam | 1.538 [1.534–1.550] | 1.139 [1.118–1.159] | 8.027 [7.996–8.144] |

Including each backend's native color assembly, total geometry-processing
medians are:

| Input | Existing cuts | MeshLab repair + cuts | Contour reconstruction |
| --- | ---: | ---: | ---: |
| Tutorial cubes | 0.220 s | 0.212 s | 0.465 s |
| Benchy | 11.613 s | 12.420 s | 60.971 s |
| Intact hollow tube | 1.169 s | 1.198 s | 13.181 s |
| Tube with 0.020 mm seam | 1.574 s | 1.170 s | 14.332 s |

The prototype's Benchy color assembly adds a median 26.426 s, versus about
0.18 s for old-path concatenation. It partitions colors, extrudes the final
regions again, and runs mandatory float32 checks. The MeshLab prepass itself
takes only 0.469 s on Benchy. Stage medians need not sum exactly to the total
median. The plot shows the stage breakdown from each median-total run, with
the observed total min–max range:

![Controlled repair and layer-creation timings](results/controlled_comparison.png)

The faster times do not represent equivalent geometry quality:

- **Cubes:** all methods pass the common checks.
- **Benchy:** all eight original parts are positive volumes, including after
  float32 conversion, with no collapsed triangles or duplicate faces. MeshLab
  reports them as manifold and produces exactly the same subsequent cut meshes
  as the cut-only arm. Both old paths nevertheless create 54 non-volume bands
  and 279,302 float32-collapsed triangles. This is a layer-creation problem that
  the input prepass does not fix.
- **Contour Benchy:** all 649 nonempty source-part bands and 349 final material
  bands pass indexed/float32 volume, collapsed-triangle and duplicate-face
  checks. The other eight source-part midpoints are legitimately empty. Final
  triangle count falls from 2,238,388 to 1,466,260, approximately 35% fewer.
- **Tubes:** the new method preserves the intended cavity and passes the common
  checks for both inputs. The cut-only damaged tube has 100 non-volume bands;
  MeshLab closes those openings successfully. Both older paths still produce
  six float32-collapsed triangles on the repaired/intact tube. These flags alone
  are not proof that a downstream slicer would reject the output.

The common generic section auditor could not reconstruct 11 native contour
Benchy profiles. Those failures remain unchanged in the raw benchmark report.
`controlled_prism_audit.py` regenerates only their affected layers and requires
exact source-band and native-band geometry hashes to match the timed outputs.
On those identical meshes, equal top/bottom cap unions plus vertical side
triangles establish the actual constant footprint directly. All 11 pass this
additional proof and the original-reference area budget; the maximum cap-area
symmetric difference is 0.000359 mm². Details and original-input checks are in
[`results/controlled_prism_audit.json`](results/controlled_prism_audit.json).
This supplementary run is excluded from every reported time:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python \
  experiments/planar_bands/controlled_prism_audit.py \
  --report experiments/planar_bands/results/controlled_benchmark_runs/12_benchy_0.2_contour_r1.json
```

The supplementary profiles retain their component counts, but nine of the
eleven change hole count: 18 microscopic holes disappear, totaling only
0.00000915 mm² of filled area (largest hole 0.00000271 mm²). All changed filled
area lies within 0.001 mm of an original reference boundary. A large 1.84 mm
boundary Hausdorff distance reflects the disappearance of long, extremely
narrow internal boundaries, not that much movement of filled geometry. Exact
hole topology is therefore not preserved; the successful check is explicitly
about bounded filled-area change. These are the rounding/seam topology changes
already permitted by the prototype's policy.

The current contour prototype therefore trades additional processing time for
more reliable band geometry. Avoiding its redundant source/final extrusion is
a concrete optimization candidate. A selective fallback is another candidate,
but would need checks for float32 degeneracies as well as indexed volume;
checking volume alone would miss some of the defects seen here. Neither
optimization is implemented or included in these measurements.

## Removing redundant extrusion, 22 October 2026

The production contour path now recovers source profiles, partitions the
material regions, and extrudes each final material region once. It no longer
creates source-part prisms that would be discarded before color assembly.
`reconstruct_layer_profiles` exposes that contour-only step; the existing
`reconstruct_layers` API still constructs and validates source-part meshes.
The final triangulator also reads GEOS triangle coordinates in bulk, retaining
the exact previous ordering and indexed cap arrays. Float32 normalization,
hole handling, boundary fan splitting, partition rules, and final validation
are unchanged.

One acceptance rule intentionally changes: a source profile no longer needs
to form a valid standalone prism if its final combined material region forms
a valid exported mesh. The contour itself must still pass reconstruction, and
the final material must pass extrusion, float32 volume, duplicate/collapsed
triangle, export/reimport, cap footprint, whole-layer and seam checks. Tests
explicitly check that a failed final extrusion is rejected.

The follow-up benchmark runs the same four inputs at 0.20 mm with the same
recipes, Z origin, 0.001 mm interlayer gap, settings, libraries, thread limits,
and fresh-process warmup. It runs four methods four times each, serially,
rotating order so every method occupies every position once for each input.
The original 2D control imports `planar_bands.py` frozen from `a27dc7de` for
both its source and final extrusion stages. The harness requires that source
file's SHA-256 to equal the one recorded in the previous benchmark; each
worker records whether it actually loaded and used this frozen implementation.
Neither the previous benchmark nor its detailed result directory is changed.

The directly comparable headline is **complete geometry processing**
(`core_s`): repair + source-band creation or contour-only extraction + final
color assembly. All intrinsic checks remain timed. Optimized contour-only
extraction does not create source meshes, so its old `matched_part_bands_s`
field is explicitly null; reporting it as a matched mesh-band time would be
misleading. Loading, imports, warmup, geometry hashing, export, external
audits, slicer processing and printing remain excluded.

Every new output must match the archived final mesh arrays, names, layer
indices and color tokens exactly, in addition to source bytes, numerical
settings, library versions and final counts. Only then does the report reuse
the original external audit evidence. Known old-path defects and the 11
generic-section Benchy failures remain recorded; the identical Benchy meshes
also retain the supplementary cap-union proof described above. The expensive
common external audit is not rerun in this timing experiment. A separate,
untimed production Benchy run repeats the full CLI's export/reimport and seam
checks and compares all 349 named bands against the previously inspected 3MF.

Reproduce from the repository root (using the same environment as above):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python \
  experiments/planar_bands/optimized_benchmark.py

PYTHONPATH=src python experiments/planar_bands/optimized_plot.py \
  experiments/planar_bands/results/optimized_benchmark.json

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python \
  experiments/planar_bands/optimized_cli_validation.py \
  --output /tmp/benchy_optimized_validation.3mf \
  --report /tmp/benchy_optimized_validation.report.json \
  --summary /tmp/benchy_optimization_validation.json
```

The CLI validation requires fresh output paths and the original local Benchy
3MF/report as controls. Benchmark raw details stay local; the aggregate report,
plot and compact CLI identity proof are retained in this directory.

The production validation passed for all **349 bands, 182 layers and
1,466,260 triangles**. Every native band name, vertex array and face array
equals the previously inspected output exactly. All report diagnostics also
match except the file paths, ZIP-container checksum and new pipeline
description: contour recovery, partitioning, tiny-hole normalization, exported
footprints, and seam metadata are unchanged. The original 3MF and its Orca
inspection copy were preserved; the new local output is
`results/benchy_optimized.3mf`. The compact proof is in
[`results/optimization_validation.json`](results/optimization_validation.json).

### Optimization measurements

All **64 workers** completed. Every final mesh matches its archived control
exactly, all frozen-control dispatch checks pass, and all recorded source-file
and previous-evidence hashes remained unchanged. All contour outputs retain
the successful final checks, including the supplementary Benchy prism proof.
The cut-only and mesh-repair paths retain their known defects described above.

Complete geometry processing, seconds: **median [minimum–maximum]**, four
fresh serial repetitions per cell. These are measurements from this new run,
including the frozen original 2D control, rather than a comparison against
historical timings from a different session.

| Input | Existing cuts | Mesh repair + cuts | Original 2D | Optimized 2D |
| --- | ---: | ---: | ---: | ---: |
| Tutorial cubes | 0.194 [0.192–0.199] | 0.203 [0.202–0.206] | 0.439 [0.434–0.450] | **0.299 [0.289–0.509]** |
| Benchy | 10.922 [10.877–11.973] | 11.453 [11.235–11.922] | 59.141 [57.475–64.200] | **39.998 [38.896–47.839]** |
| Intact hollow tube | 1.179 [1.122–1.193] | 1.172 [1.138–1.262] | 11.866 [11.816–23.683] | **7.451 [7.432–7.803]** |
| Tube with 0.020 mm seam | 1.506 [1.489–1.564] | 1.148 [1.133–1.165] | 15.694 [13.971–20.195] | **9.529 [9.478–10.288]** |

The optimized 2D method reduces median wall time by **32.0%, 32.4%, 37.2%
and 39.3%**, respectively: **1.47×, 1.48×, 1.59× and 1.65×** faster. The older
3D paths are still faster, with the previously measured geometry defects;
this optimization preserves the 2D geometry improvements rather than making
the two methods equivalent.

For Benchy, source processing falls from a median 33.193 s (contours plus
649 intermediate prisms) to 17.314 s (contours only). Final material assembly
falls from 26.046 s to 22.684 s with bulk triangle-coordinate extraction.
Stage medians do not necessarily sum to the median total. The optimized path
creates the same 349 final bands and 1,466,260 triangles. The benchmark reports
zero source meshes as intentional deferred work, not missing layers.

No observations were discarded. Several slow wall-time observations are
visible, especially the original intact tube (23.683 s wall versus 13.908 s
CPU) and one optimized cube. CPU time is recorded alongside every wall time;
Benchy's median CPU time also improves from 57.675 s to 39.365 s. These remain
local desktop measurements without CPU pinning or isolation from background
applications, with only four observations per cell; ranges are not confidence
intervals.

![Optimized repair and layer-creation timings](results/optimized_comparison.png)

The plot stacks the stage times from the two central total-time runs, averaging
them so the bar equals the median total; whiskers retain the full observed
range. Full timings, provenance and inherited quality evidence are in
[`results/optimized_benchmark.json`](results/optimized_benchmark.json).
The combined contour, CLI, benchmark and G-code-probe regression suite passed
**189 tests** after the measurements, without competing with the timed workers.
