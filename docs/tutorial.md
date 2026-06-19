# LayerLoom Beginner Tutorial

This tutorial walks through a complete first LayerLoom workflow using the CMY
sample files in [`examples/`](../examples/). It assumes you are comfortable
opening a terminal and a slicer, but it does not assume much 3D-printing
background.

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

1. Install LayerLoom in a virtual environment.
2. Run `layerloom-doctor` and confirm the checks pass.
3. Launch `layerloom-gui`.
4. Open one of the sample inputs, or click **Load Example** to open the packaged
   CMY Benchy example.
5. Weave the model and save the output.
6. Open the woven output in a slicer.
7. Assign generated `all_...` objects to matching physical filaments.
8. Set slicer layer height and first-layer height correctly.

## Install And Open The GUI

The simplest first attempt is to use the double-click launcher from the
repository folder:

- macOS: `Launch LayerLoom.command`
- Windows: `Launch LayerLoom.bat`

The launcher creates or reuses `.venv`, installs GUI dependencies if needed,
runs `layerloom-doctor`, and opens the GUI. If anything fails, use the terminal
install below so the error messages are easier to inspect.

From a fresh clone on macOS or Linux:

```bash
git clone https://github.com/Finn2400/layerloom.git
cd layerloom
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[gui]"
layerloom-doctor
layerloom-gui
```

From a fresh clone on Windows PowerShell:

```powershell
git clone https://github.com/Finn2400/layerloom.git
cd layerloom
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[gui]"
layerloom-doctor
layerloom-gui
```

From a source checkout, you can also run the current GUI directly:

```bash
python src/layerloom/3mf_gui_v65.py
```

If the GUI opens to an empty build plate, that is normal. Load a sample file to
begin.

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

1. Launch LayerLoom with `layerloom-gui`.
2. Click **Open 3MF**.
3. Open [`examples/tutorial_cmy_cubes.3mf`](../examples/tutorial_cmy_cubes.3mf).
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
  `python -m pip install -e ".[gui]"` inside the activated environment.
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
