# LayerLoom paint prototype

The `codex/paint-release-integration` source starts in **Arrange**, with a
pixelated Benchy design on the empty build plate. **Paint** is an optional mode
in the main toolbar: turn it on for surface editing and off for placement.
Loading the cubes or Benchy example preserves the current mode. Both modes
use the same loaded model, paint and placement in memory. Switching modes
does not reread the source or replace paint edits. Arrange shows the same
surface colors as Paint, including the optional palette-mapping preview.

This startup default is newer than the already-built 0.6.4a3 DMGs, which open
with Paint enabled; it requires a new desktop build.

The compact side panel keeps the tool, paint color, selection and **Apply
paint** visible. **Settings…** opens a small contextual popover for brush
radius or surface-selection angles; Escape or an outside click dismisses it
without painting. **Color mapping** expands the palette and recipe controls.
There is no separate editor window or visible workspace tab bar. Open, Save,
Export, Undo and Redo use the main toolbar; export progress and **Cancel export**
stay in the main footer even when switching modes.

Use the normal `layerloom-gui` launcher. File/Open, File/Save and Tools/Export
operate on this shared model in either mode: Open loads a model or paint
project, Save writes an editable `.llpaint` project, and Export writes material
geometry. Undo and Redo remain available in both modes. Closing the main window
protects unsaved paint or placement changes and an active export.

The export dialog shows estimated additional memory, layer count and sampling
work before conversion. **Advanced export settings** exposes the memory budget
(default **2,048 MiB**), sampling budget (default **2 million cells**) and standard
or fine geometry precision. Work or memory estimates beyond the selected limits
disable export. Increase sampling size/layer height, reduce the model, or explicitly
raise the appropriate budget when resources permit. Leave memory for
the loaded model, application and other programs. This is an allocation estimate,
not a hard operating-system limit on application RAM. Runtime checks also track
generated geometry, nearest-surface candidates and serialization. Export snapshots
omit unused project metadata and are checked before copying the model.

**Cancel export** stops at the next safe checkpoint and leaves existing output
files intact. Native geometry operations must finish before cancellation is
observed. The first export also compiles the triangle search code; compilation
and index construction finish before their next cancellation checkpoint. The
status bar identifies this preparation stage. Closing waits for the worker to finish; cancel in the footer, then close
again. The completed 3MF is published with an atomic replacement; a cancellation
arriving after that replacement is reported as a successful export. The optional
audit report is handled separately. The paint project remains editable during
conversion, and export uses the snapshot from when it started.

The Qt-free `paint_export_control.py` owns resource estimates and cooperative
cancellation. `export_painted_3mf` accepts `memory_budget_bytes`,
`cancel_requested` (a callback such as `threading.Event.is_set`), and a `stage`
callback. Reports include the preflight estimate and peak runtime estimate;
neither is a measured RAM value. `paint_main_ui.py` now holds the Paint workspace
adapter and action routing independently of the versioned Arrange GUI modules.
The older Arrange inheritance chain remains; this extraction is a contained
first step toward simplifying it.

**Printable exports contain geometry only.** Imported and edited paint guides
the conversion into separate meshes for physical filaments. Exported 3MFs use
part-level filament assignments and contain no per-face paint annotations.
Editable `.llpaint` projects retain paint for future editing.

The editor can also be launched directly:

```sh
python -m layerloom.paint_gui /path/to/model.3mf
```

## Working with paint

1. Open a painted 3MF, GLB/GLTF, STL, OBJ, PLY, or `.llpaint` project. Turn on
   **Paint** in the main toolbar to show its surface-editing controls.
2. **Surface select** picks a whole connected patch. Open **Settings…** to
   adjust its edge angle and surface span. The crease-angle limit
   stops at sharp edges; the surface-span limit prevents selection from wrapping
   around a curved object. **Area select** brushes a selection within a radius
   in mm. **Color region select** follows a connected region of the current
   color. Area and brush radius are also under **Settings…**. Shift adds and
   Ctrl removes regions. The highlight is only a preview;
   choose a color and click **Apply paint** to change the selected faces.
3. Brush, Connected fill, Object fill, and Erase also work directly. Edges follow
   the source triangles. Erase restores imported colors. Undo/redo operates on a
   whole stroke or applied selection. Alt+left-drag rotates the camera;
   middle-drag pans and right-drag zooms. Navigate mode allows ordinary camera
   control without painting.
4. Expand **Color mapping** to choose the print palette and conversion mode.
   **Woven apparent colors** is the default for new documents and maps paint
   to LayerLoom recipes. **Direct physical filament colors** maps paint to
   individual palette filaments. **Exact colors — one material each** instead
   assigns one physical filament slot to each exact current paint color.
   Mapping preview never overwrites source paint. The table and status show the
   recipes and required physical filaments. Explicit recipe preferences apply
   to a paint color throughout the document within the selected palette. They
   are not independent assignments for separate regions of the same color.
5. **Save paint…** writes an editable `.llpaint` project. It includes geometry,
   the current model pose, current colors, original colors for erase, and
   palette metadata. Use Open to reopen it; the saved pose becomes the loaded
   placement for that session.
6. **Export geometry 3MF…** converts the painted solid into separate material
   meshes. Woven mode assigns each layer according to the recipe; the other
   modes make geometry for their physical colors. All modes export geometry,
   with no surface-paint annotations. Closed valid source solids are required;
   ordinary export does not silently repair source geometry.

Import all exported parts together, retaining their common alignment. There is
no native painted-3MF or standard surface-color export option. Colored 3MF
writers used to generate import-test fixtures are internal development helpers.

## Positioning the shared model

Turn Paint off to use Arrange's move, rotate, scale and plate-placement
controls. These change the whole assembly, keeping its parts aligned and paint
attached to the same faces. Each placement is calculated from the geometry as
it was loaded, using an absolute scale and rotation, so repeated edits do not
accumulate coordinate drift. Painting before or after placement changes the
same document, and paint undo/redo remains available after moving the model.

Loading a model preserves its original placement, including any height above
the plate. Explicit placement follows Arrange's centering and grounding rules.
**Reset** restores the loaded pose without undoing paint edits. Save retains
the current posed geometry; reopening that `.llpaint` starts from the saved
pose rather than reconstructing the earlier placement history.

Selecting a part in Arrange and assigning a palette color paints that entire
part as one undoable paint edit. Paint's Undo/Redo controls also undo and redo
these whole-part color edits. Erase continues to restore the imported source
colors. The chosen weave recipe is remembered for that color and palette
throughout the document, including other regions with the same color.

Printable geometry export uses the current rotated and scaled geometry. As in
the existing export contract, it then centers and grounds the assembly on the
output plate with one shared translation. Its XY plate position can therefore
differ from the Arrange view; relative part alignment, orientation and size
are preserved. Export does not change the editable model's placement.

## Material-volume conversion

For material-volume exports, paint extends through the source solid from the nearest surface triangle. There
is no adjustable colored-shell depth in this prototype. The converter samples
horizontal cross-sections at layer midpoints, partitions them by paint, applies
the palette recipe at the global layer index, and unions each physical material.
Disconnected positive shells missed by all midpoints are retained as separate
geometry parts, with one verified interior paint sample per layer piece. Thin
features within a larger connected solid still follow midpoint sampling.

The exterior is layer-stepped. XY material detail is limited by source triangles
and the selected sampling pitch. The export audit records coordinate snapping
(0.001 mm by default) and interface priority, bounded numerical cleanup, volume
change, layer coverage, and float32 geometry checks. It rejects open or
overlapping source solids, transparent colors, and invalid material geometry.

Use the exported layer height for **both first and subsequent slicer layers**,
disable adaptive layer height, and keep all parts aligned. Color matching is an
approximation to the selected palette. Physical printed color has not been
validated by these software checks.

## Import coverage

- Standard 3MF object/base colors and triangle colors, component/build
  transforms and units.
- Bambu/Orca `paint_color` and Prusa `mmu_segmentation`, including recursive
  triangle subdivisions and filament-slot colors from project metadata.
- GLB vertex/material colors and basic textures; GLTF meters become mm.
- Texture and vertex gradients are sampled to one color per triangle, with an
  import warning. Advanced glTF material extensions and texture transforms are
  not implemented. Unsupported 3MF properties raise an explicit error.

Paint and geometry are stored independently of PyVista actors. Both import and
interactive editing feed `PaintDocument`, so preview, save/reopen and printable
conversion use the same data. Original files are not overwritten automatically.

## Reproducing the examples

```sh
python -m layerloom.paint_examples --output-dir /path/to/paint_examples
python -m pytest src/layerloom/paint_tests -q
```

The example command creates painted cube and sphere source 3MFs, verifies exact
source/project round trips, and writes direct and woven printable exports with
JSON geometry audits. Example mixed recipes intentionally exercise CMY global
layer phase. Native GUI tests are opt-in because VTK requires a working display:

```sh
LAYERLOOM_GUI_TESTS=1 QT_QPA_PLATFORM=cocoa \
python -m pytest src/layerloom/paint_tests/test_paint_gui.py \
  src/layerloom/paint_tests/test_main_paint.py -q
```

On macOS the test process needs native WindowServer access; Qt's offscreen
backend does not support this QtInteractor configuration. On other platforms,
select a working native Qt platform rather than `cocoa`.

The paint exporter requires Manifold 3.2 or newer and checks its required API
before conversion. CI runs the paint backend tests in the existing macOS,
Windows and Linux/Python 3.10–3.12 matrix. A separate Linux Xvfb job exercises
the native Paint and Arrange controls with software rendering. Adding these
jobs does not establish a passing result until CI has run on the branch.

Project loading rejects empty meshes, fractional face indices, invalid color
arrays and malformed manifests before replacing the current edit session.
Interrupted brush strokes remain undoable and participate in save prompts.
Project saves use staged files beside the destination; failed replacements
preserve an existing project. A successful geometry export whose optional
audit report cannot be published reports that warning separately.

The local proof also uses actual Qt pointer strokes on the cube and sphere,
undo/redo, saving, reopening, and the main v65 Paint toolbar button. See
`outputs/paint_prototype_2026_10_22/gui/gui-proof.json` for source hashes and
assertions. Its screenshots combine Qt window chrome with the same native VTK
viewport framebuffer; the separate raw viewport captures are retained.

The shared-model proof exercises surface painting, placement, Save/Open from
Arrange, and a real geometry export, then compares both views of the repaired
baby dragon. It supplies file-dialog choices and captures export notifications
in the test harness; geometry conversion and model rendering use the real app.
Give it a new output directory:

```sh
QT_QPA_PLATFORM=cocoa python -m layerloom.paint_tests.shared_workspace_proof \
  --baby-project /path/to/baby_repaired_woven.llpaint \
  --output-dir /path/to/new-proof --stay-open
```

Its `shared-workspace-proof.json` records source hashes, checks and screenshots.
Paint and Arrange captures use the same camera; their viewport sizes differ.

Slicer checks use Bambu Studio 02.08.02.61 through its CLI, with a local X1
Carbon 0.4 mm profile, 100% infill, fixed 0.2 mm first/subsequent layers, and
automatic arrangement/orientation disabled. The geometry 3MFs remain unchanged
during slicing. All six exports passed: direct and woven cube/sphere examples,
plus the two brush-edited examples. Each contains 100 layers. The G-code audit
matches physical material use to each exported cross-section on all 600 layers;
part alignment is preserved and the slicer reports zero mesh repairs. See
`slicer/slicer-validation.json` for the detailed records. A separate audit in
`slicer/independent-validation.json` also checks the spatial placement of model
extrusions against their assigned material regions, accounting for the printer
profile's nozzle offset and extrusion width. All six files reimport with their
intended physical filament colors.

The `slicer/` folder retains commands, profiles, logs and sliced projects.
Its G-code is validation output for that Bambu profile; generate new
G-code with your own printer and material profiles before printing. Snapmaker
Orca CLI profile loading crashed, so this run does not establish U1 slicing
compatibility.

## Baby dragon validation

The requested input is `~/Downloads/babydragonmulticolorfactoryfiles.3mf`.
Its SHA256 is
`84ccf1fc260401d4e470c6b16fda9ca74a41a06611f93986270841ad33993587`.
Both instances and their black, purple, white and yellow paint are retained.
An independent annotation-area decoder agrees with the imported color regions;
geometry bounds and total surface area are unchanged, and `.llpaint` round trips
are exact.

The earlier saved `dragon/baby_assigned.llpaint` project uses the Normal woven palette.
It maps purple to `cmwmwm`, black to `k`, white to `w`, and yellow to `y`.
Those four apparent colors require **five physical filaments**: C, M, Y, K and
W. The mapping is restored when the project reopens; the original paint remains
available independently of the mapped preview.

The original source has 25 boundary edges and 160 nonmanifold edges per
instance. Import retains those defects. The requested repair is stored
separately under `outputs/paint_main_ui_2026_10_22/repair/`:

- `baby_repaired.llpaint`: editable repaired geometry and paint.
- `repair-audit.json`: component, hole, precision, geometry and color audits.

Repair retains all 39 positive-volume source components per instance and unites
their overlapping solids, removing buried internal surfaces. Only nine
opposite zero-thickness triangle pairs per instance are discarded; tiny local
holes are capped. Surface paint and erase-source colors follow source-face
provenance through the union. Local numerical corrections move 289 and 304
vertices respectively by at most 0.0000611 mm. Both instances pass zero-area,
duplicate-face, nonmanifold-edge and oriented-volume checks in their original
and centered export positions. The downloaded file is unchanged. No automatic
repair is applied during ordinary paint import or export.

The repair API requires Manifold's Mesh64 and face-provenance APIs; it was
validated with `manifold3d 3.5.2` and reports an upgrade error if those APIs are
missing. Repaired project geometry and face colors read back exactly. The maximum
measured final vertex deviation from the exact union exterior is 0.00006321 mm.

The repaired woven project is
`outputs/paint_main_ui_2026_10_22/woven/baby_repaired_woven.llpaint`.
It retains both repaired instances and the saved Normal weave recipes:
purple `cmwmwm`, black `k`, yellow `y`, and white `w`. These require five physical
filaments (C, M, Y, K, W). The printable file is
`outputs/paint_main_ui_2026_10_22/woven/baby_repaired_woven_geometry.3mf`.
It contains 41 aligned geometry parts assigned to those five filament slots,
with 303 layers at 0.2 mm. The earlier native-painted 3MF was a superseded
development artifact; it is not the woven output and is no longer an
application export path.

The woven volume differs from the repaired source by +0.00264%, primarily from
layer-midpoint sampling. Its measured material overlap is 0.12636 mm³ (about
1.13 ppm of model volume), within the declared numerical tolerance. The largest
placed coordinate correction is 0.0000944 mm. All connected material regions
remain. The 36 microscopic positive shells missed by layer-midpoint sampling
are retained as separate geometry parts; their retention does not imply that a
0.2 mm layer/nozzle can physically deposit them. The export report and slicer
audits record these limits.

Independent readback verifies all 41 original parts at local and placed float32
coordinates, with no surface-paint properties. Bambu Studio slices the original
file successfully: 303 fixed 0.2 mm layers, zero reported mesh repairs, matching
physical filament use on every layer, and unchanged part alignment. This checks
actual extrusion and layer phase, not every XY material boundary or physical
printed color.

Use the original `baby_repaired_woven_geometry.3mf` as the deliverable. Bambu's
separately re-saved sliced project rounds transforms by up to 0.000000049 mm;
its local float32 vertices and ordered triangles remain identical, but those
transform changes introduce placed-float32 degeneracies in three parts. The
saved-project checks are recorded as failures, separately from the valid
original geometry and successful G-code audit. See
`slicer/baby_woven_geometry/independent-woven-validation.json` and
`independent-transform-reserialization-audit.json` under the same slicer folder.

The full-size dragon proof uses 0.2 mm layers, a 0.4 mm paint-sampling pitch, and
0.00001 mm coordinate precision. The earlier development driver used parallel
layer checkpoints. The normal converter now includes an exact triangle-search
accelerator with deterministic lowest-face-ID tie resolution, portable Numba
wheels and no external compiler or writable JIT cache. It streams large 3MF XML
in bounded chunks and releases source search data before final unions. The
dragon settings are available through the normal dialog: 20 million sampling
cells, fine precision and a 12,288 MiB export budget. These settings have a
preflight estimate of roughly 11,176 MiB; actual memory also depends on the model.
`paint_tests/release_ui_proof.py` exercises the actual main-window options dialog
and worker with no cached geometry, recording input/output hashes and progress.

Main-window pointer tests select and paint an entire cube face, exercise
undo/redo and mode switching, and verify exact save/reopen. GUI evidence is under
`outputs/paint_main_ui_2026_10_22/gui/`; screenshot records identify the native Qt
chrome and same-instant VTK framebuffer capture method.

The compact toolbar-and-panel revision is recorded under
`outputs/paint_compact_ui_2026_10_22/`. Native checks cover contextual settings,
Escape and outside-click dismissal, selection preservation, compact layout,
mode switching, toolbar routing and export progress.

Earlier validation artifacts are in `outputs/paint_prototype_2026_10_22/`;
the main-UI, selection, repair and woven-geometry evidence is in
`outputs/paint_main_ui_2026_10_22/`.
They are local development evidence, not redistributed model fixtures.
