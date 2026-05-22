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

Start with `tutorial_cmy_cubes.3mf`, then move to
`tutorial_cmy_benchy_cutup.3mf` after the basic workflow makes sense.
The GUI **Load Example** button opens the Benchy example directly from the
packaged install.

See [../docs/tutorial.md](../docs/tutorial.md) for the full walkthrough.
