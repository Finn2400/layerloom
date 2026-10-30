# LayerLoom Paint and Weave Tutorial

Start with the small CMY cubes bundled with LayerLoom. You do not need a separate
model download. A slicer is needed after LayerLoom creates the printable 3MF.

## Open and inspect a model

1. Open LayerLoom using the [installation guide](install.md). LayerLoom
   starts in **Arrange**, with a pixelated Benchy design on the empty build plate.
2. Click **Try the example**, or **Help → Try the CMY cubes example**, to load
   the cubes. Loading an example keeps your current mode.
3. Move, rotate, and scale the model in Arrange. Turn on **Paint** in the main
   toolbar when you want surface-selection and painting controls.
4. Turn Paint off to return to Arrange. Both views keep the same model, colors,
   and placement; exporting is available in either mode.

The packaged source examples remain unchanged. Use **Open…** for your own 3MF,
GLB, or supported mesh file, or to reopen a saved `.llpaint` project.

## Paint a surface or area

1. Turn on **Paint** in the main toolbar. Choose **Surface select** and click a
   face to select a connected surface.
   Use **Settings…** to adjust which neighboring faces count as that surface.
2. Choose a paint color, then click **Apply paint**. Selection alone does not
   change the paint.
3. Use **Area select** for a smaller local region, **Color region select** to
   follow a connected color region, or the brush for direct strokes.
4. Use Undo and Redo to revise edits. You can also select and paint a whole part
   from Arrange.
5. Expand **Color mapping**, choose a palette, and assign colors to preview how
   those colors map to printable filament recipes.

Woven recipes alternate physical filaments through the model's height. Their
screen color is an approximation of the apparent printed color. Actual results
depend on the loaded filaments, layer height, geometry, and viewing direction.

## Save your editable work

Use **Save paint…** to save a `.llpaint` project. It retains the editable geometry,
colors, and project settings. This is the file to reopen when you want to edit
paint later.

A printable 3MF is a separate output: it contains material geometry and part
assignments, with no per-face paint. Export does not replace the editable source
model in the workspace.

## Export printable geometry

1. Click **Export geometry 3MF…** in the main toolbar.
2. Choose **Woven apparent colors** for LayerLoom's layered color recipes.
   **Direct physical filament colors** uses the available single filaments;
   **Exact colors — one material each** creates a material for each paint color.
3. The packaged tutorials select **0.16 mm** automatically, including after
   saving and reopening their `.llpaint` projects. Check this value and leave
   **XY sampling size** at its initial value for the first attempt. Ordinary
   imported models retain the general **0.2 mm** default; choose the height
   intended for your print.
4. Review the estimated additional memory and sampling work. Smaller sampling
   sizes retain more detail and increase time and memory use. The estimate is
   not a hard cap on total application memory.
5. Click **Choose save location…** and choose a `.3mf` filename and folder.
   The Mac example suggests **LayerLoom** in your home folder. The **Export complete**
   panel shows the filename, folder, and a **Show in Finder** button.
6. Use **Cancel export** in the footer if needed. Cancellation waits for the
   current geometry operation to finish and leaves any previous output intact.

The main export summary shows the active estimated memory budget: **2,048 MiB**
for cubes and ordinary imports, or **4,096 MiB** for the bundled Benchy. Advanced
export settings let you change that budget, the sampling budget and geometry
precision. The first export also compiles the geometry search code; compilation
may take time before regular progress updates begin. Increase a budget only
when the computer has enough capacity; a higher budget does not make conversion
faster. Open models must be closed solids before printable conversion.

For a larger model, expand **Advanced export settings**. **Sampling budget**
is the allowed sampling work, in millions of cells; **Export memory budget**
is an estimate of additional memory, in MiB. They are separate limits. If the
estimate exceeds either limit, first consider a coarser **XY sampling size**,
which reduces work but can lose detail. Raise only the relevant budget when the
computer has capacity and you can allow more processing time; leave memory for
the model, LayerLoom and other apps. A higher limit does not guarantee success
or speed. Keep the editable project and inspect the exported details in a slicer.

Paint extends through the full volume from its nearest surface. There is no
shell-depth control. The export samples the exterior at layer midpoints, so it
produces layer steps and can lose thin details within a larger connected mesh.
Inspect small features in the slicer before printing.

## Open the 3MF in a slicer

Load the exported 3MF as one model with multiple aligned parts when prompted.
Assign the `all_cyan`, `all_magenta`, `all_yellow`, and any other generated
filament groups to their matching physical spools. Keep the parts together;
do not arrange individual filament pieces separately.

Use **0.16 mm for both the first layer and subsequent layers** for this tutorial,
and disable adaptive layer heights. For another export, use the layer height
chosen in its export dialog. The exported assembly is centered and grounded.

For the tested Bambu Classic profile, enable **Detect thin walls** to retain
the Benchy's narrow top cap. Inspect narrow tips in preview; other models and
profiles need their own checks.

A valid geometry export is not a physical print test. Review the slicer's layer
preview, walls, thin features, and filament assignments before printing.

## Try the Benchy or your own model

Use **Help → Load the Benchy example** for the larger bundled model. It keeps
the current mode; turn on **Paint** for surface editing and Color mapping.
Its eight parts appear in several colors, with four woven recipes using cyan,
magenta and yellow. Follow the same save and export workflow. Its example
settings include the larger memory budget described above. Larger models or fine
sampling may need more memory and time. The [Paint workflow and known limits](../src/layerloom/PAINT_PROTOTYPE.md)
provide more detail about supported imports and conversion.

If LayerLoom does not open, follow the [installation troubleshooting table](install.md#troubleshooting).
For a failed operation, use **Help → Copy diagnostics** or **Help → Open logs**
and include the exact error. Keep the source model and editable project so the
problem can be reproduced.

## Command-line weaving

The existing part-recipe CLI remains available in the
[developer installation](install.md#developer-installation). It uses recipes
encoded in the source parts and is separate from editing a `.llpaint` project:

```bash
uv run --no-sync layerloom-weave \
  --input examples/tutorial_cmy_cubes.3mf \
  --step 0.16 \
  --output tutorial_cmy_cubes_woven.3mf
```

### Bambu Studio: check the parts before slicing

A LayerLoom export is geometry, not a saved Bambu printer preset. Bambu Studio
2.8.2 may say **“invalid config, load geometry data only”** when opening a valid
geometry-only 3MF. Continuing with geometry does not load printer settings:
select the profile for your own printer.

Expand the model in **Objects**. Assign `all_cyan`, `all_magenta` and `all_yellow`
to the corresponding physical filaments. The numbers depend on your existing
filament list: for example, if yellow is slot 1 and cyan is slot 3, assign
`all_cyan` to **3**, not automatically to 1. Keep the three parts together.

Use **0.16 mm for both initial and normal layer height** for the example, with
adaptive layers off. After changing any assignment, **slice again**. Clear the
selection, inspect the filament-colored preview at several layers, and check
that the slice actually uses all three intended filaments. An earlier preview
can be stale after edits.

Why is cyan present outside the area you painted? Each part collects **all of
that filament**, including its contribution to mixed colors: cyan helps make
purple and green too. Selecting `all_cyan` highlights those contributions across
the model. Surface paint also extends inward; it is not a thin surface sticker.
Use the combined, unselected preview to judge the apparent colors.

If names grow into repeated “Plate 1” labels, or assignments change unexpectedly,
stop and reimport the untouched export into a new project. Keep the original
export separate from any slicer project you save. Report the slicer version and
the last action; do not print a slice with unexplained assignments. This was
observed during automated Bambu control and is still being isolated.
