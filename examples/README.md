# LayerLoom Example Inputs

This folder contains small 3MF files for the beginner tutorial. They are
intended for learning the workflow, not for judging final print quality.

## `tutorial_cmy_cubes.3mf`

A first LayerLoom example using only cyan, magenta, and yellow tokens.

It contains four simple cube parts:

- `solid_cyan__PAT_c__`
- `solid_yellow__PAT_y__`
- `woven_green_1to1__PAT_cy__`
- `woven_purple_1to1__PAT_cm__`

The `__PAT_...__` text in each object name tells LayerLoom which weave pattern
to use. For example, `__PAT_cy__` means alternating cyan and yellow layers.

## `tutorial_cmy_benchy_cutup.3mf`

A cut-up 3DBenchy example using only cyan, magenta, and yellow tokens.

This is the better second example after the cubes because it behaves like a
real multi-part model but still only requires CMY filaments. The parts are
already labeled with `__PAT_...__` tokens, so you can open it, inspect the
assignments, and click **Weave** without manual color setup. The starter
patterns intentionally use short tokens so the first print prioritizes surface
quality over maximum palette complexity.

## Recommended First Run

Open LayerLoom using the [installation guide](../docs/install.md), then click
**Try the example**. You do not need to download either model separately:
LayerLoom opens a private copy of the four cubes and sets 0.16 mm layers.
Click **Weave**, select an output folder, and use **Show in Finder / Show in
folder** to find `tutorial_cmy_cubes_woven.3mf` when it finishes.

After the cubes work, use **Load Example** or **Help → Load the Benchy example**
for the larger sample. The bundled input files remain unchanged.

See [../docs/tutorial.md](../docs/tutorial.md) for the full walkthrough.
