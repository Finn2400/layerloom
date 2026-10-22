# LayerLoom paint prototype

The `codex/paint-main-ui` branch opens **Paint surfaces** by default inside the
main LayerLoom window. **Arrange & weave** retains the existing whole-part
workflow. Switching tabs keeps each workspace's edits. The Paint surfaces
button in Arrange & weave opens the original source, preserving paint that the
legacy normalizer does not understand. Placement and whole-part assignments in
Arrange & weave are separate from the paint document.

Use the normal `layerloom-gui` launcher. File/Open, File/Save and Tools/Export
follow the selected workspace, and closing the main window protects unsaved
paint or an active export.

The editor can also be launched directly:

```sh
python -m layerloom.paint_gui /path/to/model.3mf
```

## Working with paint

1. Open a painted 3MF, GLB/GLTF, STL, OBJ, PLY, or `.llpaint` project.
2. **Surface select** picks a whole connected patch. The crease-angle limit
   stops at sharp edges; the surface-span limit prevents selection from wrapping
   around a curved object. **Area select** brushes a selection within a radius
   in mm. **Color region select** follows a connected region of the current
   color. Shift adds and Ctrl removes regions. The highlight is only a preview;
   choose a color and click **Apply paint** to change the selected faces.
3. Brush, Connected fill, Object fill, and Erase also work directly. Edges follow
   the source triangles. Erase restores imported colors. Undo/redo operates on a
   whole stroke or applied selection. Alt+left-drag rotates the camera;
   middle-drag pans and right-drag zooms. Navigate mode allows ordinary camera
   control without painting.
4. **Paint colors — one filament each** assigns one physical filament slot to
   each exact current paint color, including your edits. It is the default for
   new documents. **Woven apparent colors**
   and **Direct physical filament colors** instead map to a LayerLoom palette.
   Mapping preview never overwrites source paint. The table and status show the
   recipes and required physical filaments.
5. **Save paint…** writes an editable `.llpaint` project. It includes geometry,
   current colors, original colors for erase, and palette metadata. Use Open to
   reopen it.
6. **Export printable 3MF…** offers **Native painted 3MF** for original colors.
   This retains the exact exterior and face paint using Orca/Bambu-compatible
   filament assignments. **Material volumes** provides the sampled physical
   parts needed for palette weaving. Native export requires closed valid solids;
   it does not silently repair source geometry.

The standalone editor also exposes a standard surface-color 3MF export under
File. Slicers vary in their support for standard 3MF color properties; use
Native painted 3MF for the tested Orca/Bambu paint representation. Import all
parts of material-volume exports together, retaining their common alignment.

## Material-volume conversion

For material-volume exports, paint extends through the source solid from the nearest surface triangle. There
is no adjustable colored-shell depth in this prototype. The converter samples
horizontal cross-sections at layer midpoints, partitions them by paint, applies
the palette recipe at the global layer index, and unions each physical material.

The exterior is layer-stepped. XY material detail is limited by source triangles
and the selected sampling pitch. The export audit records 0.001 mm coordinate
snapping and interface priority, any bounded numerical simplification, volume
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

The local proof also uses actual Qt pointer strokes on the cube and sphere,
undo/redo, saving, reopening, and the main v65 Paint toolbar button. See
`outputs/paint_prototype_2026_10_22/gui/gui-proof.json` for source hashes and
assertions. Its screenshots combine Qt window chrome with the same native VTK
viewport framebuffer; the separate raw viewport captures are retained.

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
- `baby_repaired_native_painted.3mf`: native painted print export using the
  original purple, black, white and yellow as four physical filament slots.
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
missing. Exported geometry and face colors read back exactly. The maximum
measured final vertex deviation from the exact union exterior is 0.00006321 mm.

Bambu Studio sliced the repaired native-paint export into 302 fixed 0.2 mm
layers, with both objects, all four physical colors depositing model material,
and zero mesh repairs. The sliced project retains all 1,548,226 face colors and
triangle topology. A nearest-surface toolpath-color heuristic flags 8 of 8,686
near-exterior samples, all near the base at Z 0.5–1.5 mm. Native slicer paint
projection is not defined by that heuristic, so this is an unresolved boundary
check rather than proof of paint loss. Reducing bottom shells from five to one
did not change the result; the baseline profile remains the reference. Physical
printed-color accuracy has not been tested.

The branch's source was validated separately from unrelated working-tree
changes: 72 backend tests and 15 native GUI tests passed. Main-window pointer
tests select and paint an entire cube face, exercise undo/redo and tab switching,
and verify exact save/reopen. The repaired baby dragon is open in the main Paint
surfaces workspace with the exact four-color mapping. Its saved UI project and
screenshots are under `outputs/paint_main_ui_2026_10_22/gui/`; screenshot records
identify the native Qt chrome and same-instant VTK framebuffer capture method.

Earlier validation artifacts are in `outputs/paint_prototype_2026_10_22/`;
the main-UI, selection, repair and native-color evidence is in
`outputs/paint_main_ui_2026_10_22/`.
They are local development evidence, not redistributed model fixtures.
