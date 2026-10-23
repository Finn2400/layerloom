# LayerLoom Paint and Weave Tutorial

Start with the small CMY cubes bundled with LayerLoom. You do not need a separate
model download. A slicer is needed after LayerLoom creates the printable 3MF.

## Open and inspect a model

1. Open LayerLoom using the [installation guide](install.md).
2. Click **Try the example**, or **Help → Try the CMY cubes example**.
3. Paint is enabled in the main toolbar. The compact panel beside the model
   contains selection tools, the current paint color, and **Apply paint**.
4. Toggle **Paint** off to arrange the same model. Move, rotate, and scale it in
   Arrange, then toggle Paint on to continue painting. Both views keep the same
   model, colors, and placement.

The packaged source examples remain unchanged. Use **Open…** for your own 3MF,
GLB, or supported mesh file, or to reopen a saved `.llpaint` project.

## Paint a surface or area

1. Choose **Surface select** and click a face to select a connected surface.
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
3. For the cubes tutorial, set **Layer height** to **0.16 mm**. Leave **XY
   sampling size** at its initial value for the first attempt.
4. Review the estimated additional memory and sampling work. Smaller sampling
   sizes retain more detail and increase time and memory use. The estimate is
   not a hard cap on total application memory.
5. Confirm the settings and choose a `.3mf` filename in Documents or another
   folder outside the app. Wait for the completion message, which gives its path.
6. Use **Cancel export** in the footer if needed. Cancellation waits for the
   current geometry operation to finish and leaves any previous output intact.

Advanced export settings expose the estimated memory budget, sampling budget,
and geometry precision. Increase a budget only when the computer has enough
capacity; a higher budget does not make conversion faster. Open models must be
closed solids before printable conversion.

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

A valid geometry export is not a physical print test. Review the slicer's layer
preview, walls, thin features, and filament assignments before printing.

## Try the Benchy or your own model

Use **Help → Load the Benchy example** for the larger bundled model. Follow the
same Paint, save, and export workflow. Larger models or fine sampling may need
more memory and time. The [Paint workflow and known limits](../src/layerloom/PAINT_PROTOTYPE.md)
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
