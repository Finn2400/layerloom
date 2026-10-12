# LayerLoom Beginner Tutorial

This tutorial walks through a complete first LayerLoom workflow using the CMY
sample files in [`examples/`](../examples/). You do not need Python or Terminal experience. A slicer is only needed
after you have created your first woven model.

## What LayerLoom Does

LayerLoom takes a colored 3MF or GLB-style model and creates a new multi-material
3MF where color is encoded as thin stacked geometry. Instead of needing one
physical filament for every visible color, LayerLoom can alternate layers of
loaded filaments. For example, a cyan-yellow pattern can make a green-looking
region.

The slicer does not need a LayerLoom plugin. It sees the result as ordinary
multi-object geometry, and you assign each output object to the matching loaded
filament.

## First-Run Checklist

1. Install and open LayerLoom using the [installation guide](install.md).
2. Click **Try the example** in the empty window. The small CMY cubes already
   have their weave patterns assigned, and the example sets 0.16 mm layers.
3. Click **Weave** and choose an output folder, such as Documents.
4. Wait for the **Your woven model** panel, then click **Show in Finder**
   (or **Show in folder** on Windows/Linux).
5. Open that woven 3MF in your slicer. Assign the cyan, magenta, and yellow
   output objects to matching physical filaments.
6. Use 0.16 mm slicer layers for this example; the first layer height must be
   an integer multiple of 0.16 mm. Disable adaptive layer heights.

You can repeat this workflow offline. The packaged examples remain unchanged.
The **Load Example** toolbar action provides the larger CMY Benchy example.

If the app does not behave as expected, use **Help → Copy diagnostics** or
**Help → Open logs** and include the information when reporting the problem.

## A Few Terms

- A **filament** is a physical spool loaded in the printer.
- A **token** is LayerLoom's one-letter name for a filament color: `C` cyan,
  `M` magenta, `Y` yellow, `K` black, `W` white, `N` gray, `O` orange, `V`
  violet, and `G` green.
- A **weave pattern** is a token string such as `cy`, `cm`, or `novg`. The
  printer repeats that sequence through the Z direction.
- The **weave height** is the height of one woven band. Your slicer layer height
  should match this value.

Object names can include a pattern tag such as `__PAT_cy__`. That tag tells
LayerLoom to use the `cy` pattern automatically.

## Tutorial 1: Weave The Basic CMY Cubes

1. Open LayerLoom.
2. Click **Try the example**, or use **Help → Try the CMY cubes example**.
3. The packaged CMY cubes open automatically.
4. Confirm that the parts appear in the parts list. These sample objects already
   include `__PAT_...__` tags, so you do not need to manually color them.
5. Set **Layer height** to `0.160` for this beginner example.
6. Click **Weave**.
7. Save the woven output as something like `tutorial_cmy_cubes_woven.3mf`.

The woven output should contain objects named by final filament, such as
`all_cyan`, `all_magenta`, and `all_yellow`. This is expected. LayerLoom has
regrouped the thin bands by the physical filament that should print them.

## Open The Woven File In A Slicer

Open `tutorial_cmy_cubes_woven.3mf` in your slicer.

Assign the generated objects to matching loaded filaments:

- `all_cyan` -> cyan filament
- `all_magenta` -> magenta filament
- `all_yellow` -> yellow filament

Set the slicer layer height to match the LayerLoom weave height:

```text
Layer height: 0.16 mm
First layer height: 0.16 mm or 0.32 mm
```

The important rule is:

```text
first_layer_height / weave_height = integer
```

For example, if you weave at `0.08 mm`, a `0.16 mm` first layer is fine, but
`0.20 mm` is not aligned to the weave schedule.

## Tutorial 2: Try The CMY Benchy Example

After the CMY cubes work, try the cut-up Benchy sample. It is still CMY-only,
but it is a more realistic multi-part model than the beginner cubes.

1. Click **Load Example** in the toolbar, or click **Open 3MF** and open
   [`examples/tutorial_cmy_benchy_cutup.3mf`](../examples/tutorial_cmy_benchy_cutup.3mf).
2. Confirm that the Benchy parts appear in the parts list. The parts are already
   labeled with CMY `__PAT_...__` tokens.
3. Set **Layer height** to the layer height you plan to use in the slicer, such
   as `0.120` for a finer tutorial print.
4. Click **Weave** and save the output.
5. In your slicer, assign each generated `all_...` object to the matching
   physical filament.

The example only uses `C`, `M`, and `Y`, so it is suitable for a three-filament
first print.

## Optional: Use The Calibrated CMY Palette

The default `Normal` palette is the nominal LayerLoom color model. To preview
and auto-match against measured printed CMY colors, choose
`Calibrated CMY Normal (core065)` from the palette menu before importing a GLB
or before assigning colors. Measured entries use the observed printed hex/Lab
values; unmeasured recipes fall back to the nominal colors.

## Optional: Weave From The Command Line

You can also weave the examples without opening the GUI:

```bash
layerloom-weave \
  --input examples/tutorial_cmy_cubes.3mf \
  --step 0.16 \
  --output tutorial_cmy_cubes_woven.3mf
```

```bash
layerloom-weave \
  --input examples/tutorial_cmy_benchy_cutup.3mf \
  --step 0.12 \
  --output tutorial_cmy_benchy_cutup_woven.3mf
```

The GUI is usually easier for first-time use because you can inspect assignments
before weaving.

## If Something Fails

- If `layerloom-gui` or `layerloom-weave` is not found, confirm that the virtual
  environment is activated.
- If `layerloom-doctor` reports a missing package, rerun
  the source launcher with `--repair`; see the [installation guide](install.md#repair-a-source-installation).
- If the GUI fails to import, include the full `layerloom-doctor` output when
  reporting the problem.
- If the slicer shows several separate objects, that is normal. Assign each
  final-color object to the corresponding filament.
- If a woven model looks vertically scrambled, check that the slicer layer
  height matches the LayerLoom weave height.
- If the first layer is not aligned, use an integer multiple of the weave height.
- If using Ultimaker Cura 5.12.0, disable automatic object drop-to-plate
  behavior before loading woven LayerLoom outputs.
- If woven colors look too stripey, try a lower layer height or rotate the model
  so broad flat surfaces are not facing straight up.
- If a model has very thin features, inspect the woven output carefully. Thin
  geometry near a band boundary can be fragile.

## Next Steps

Once the sample files work, try your own model:

1. Export or save a 3MF/GLB with separate named parts.
2. Open it in LayerLoom.
3. Select a part and assign a weave color from the palette.
4. Weave the file.
5. Slice the output with layer-height alignment.

LayerLoom is most reliable when the model has clear, separate regions and when
the desired colors are visual or illustrative rather than mechanically critical.
