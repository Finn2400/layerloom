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
5. Extrude the recovered footprint through its full nominal layer interval.
   Retain failures and diagnostics for the caller instead of skipping them.

`src/layerloom/contour_repair.py` is an independent command-line entry point.
It retains part PAT recipes, uses a supplied fallback recipe for unlabeled
parts, unions same-filament regions on each layer, and rejects positive-area
overlap between different filaments. It validates exported float32 geometry
and checks midpoint footprints after export. Any failed layer prevents a new
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

Defaults: 0.00001 mm XY precision, 0.03 mm maximum gap, and a 0.001 mm internal
Z gap. These are experiment parameters, not calibrated fabrication settings.
XY precision and gap-closing distance are separate controls. Rounding moves an
initial endpoint by at most `precision / sqrt(2)`; bridging adds a new segment
and is an explicit geometric assumption even when the distance is small.

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
difference is 0.000005689 mm². Box and repaired-gap differences are zero.

The independent geometry tests additionally cover unwelded triangle soup,
reversed winding, duplicate triangles, nested cavities with islands, nearby
separate islands, ambiguous bridges, invalid numeric parameters, nominal partial
top layers, and off-grid sampling differences. Timings in the fixture report
are single runs with initialization effects; they are not a speed benchmark.

See [comparison.png](results/comparison.png) and [report.json](results/report.json).

The combined geometry and CLI suite passes **46 tests**. An actual PrusaSlicer
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

## Existing repository input checks

The four-object `tutorial_cmy_cubes.3mf` passes at 0.20 mm: 160 source part-layer
profiles become 100 material bands after same-filament grouping, with zero
measured midpoint footprint change on export/reimport.

The denser `tutorial_cmy_benchy_cutup.3mf` (253,176 triangles, eight parts) does
**not** pass this prototype. On a 0.00001 mm grid, 657 sampled part-layers contain
627 accepted profiles, 22 branching profiles, and eight empty samples. Subsequent
material assembly also finds small overlaps, degenerate float32 triangles, and
an extrusion failure. No final Benchy file is published.

A coarser 0.001 mm grid changes this to 630 accepted profiles, 19 branching
profiles, and eight empty samples, but still fails export and produces more
float32 validation failures in the evaluated material bands. Coarsening the grid
can introduce collapsed or touching features; it is not a general robustness fix.
Both runs stop material extrusion on its first exception, so their export-error
counts are partial. See [real_input_summary.json](results/real_input_summary.json).

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
- A production material implementation should construct a shared planar graph
  for all colors, then assign disjoint cells. Independently snapping each color
  can create microscopic overlaps and gaps. This prototype rejects detected
  overlaps but does not establish coverage against an independent common envelope.
- Even-odd nesting treats an enclosed shell as a cavity. Nested solid components
  intended as additive volumes require an explicit different interpretation;
  the original intent cannot be inferred from geometry alone.
- Per-band validity does not establish a clean union of the entire assembly.
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
  --report repaired.report.json

PYTHONPATH=src python -m pytest \
  tests/test_planar_bands.py tests/test_contour_repair_cli.py

PYTHONPATH=src python experiments/planar_bands/benchmark.py

# Optional: requires the installed macOS PrusaSlicer application.
PYTHONPATH=src python experiments/planar_bands/slicer_probe.py
```

The source can be STL (assumed millimeters) or a millimeter 3MF supported by the
existing reader; other 3MF units are explicitly rejected. The existing reader's
extension limitations still apply. This is a whole-input opt-in path, not an
automatic per-layer fallback mixed with the smooth 3D cutter.

The next implementation step is robust shared material partitioning and
triangulation on dense real inputs, followed by more slicer and print validation.
The evidence supports an optional alternative backend; it does not yet support
replacing the default workflow or claiming general non-manifold repair.
