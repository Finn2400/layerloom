# LayerLoom Example Inputs

This folder contains tiny 3MF files for the beginner tutorial. They are small
enough to keep in git and are meant for learning the workflow, not for judging
final print quality.

## `tutorial_cmy_cubes.3mf`

A first LayerLoom example using only cyan, magenta, and yellow tokens.

It contains four simple cube parts:

- `solid_cyan__PAT_c__`
- `solid_yellow__PAT_y__`
- `woven_green_1to1__PAT_cy__`
- `woven_purple_1to1__PAT_cm__`

The `__PAT_...__` text in each object name tells LayerLoom which weave pattern
to use. For example, `__PAT_cy__` means alternating cyan and yellow layers.

## `tutorial_expanded_tiles_v62.3mf`

A small v62 expanded-palette example using the additional token buttons:

- `N` for gray
- `O` for orange
- `V` for violet
- `G` for green

It contains a few solid-color and mixed-token tiles so you can practice adding
expanded colors in the GUI and assigning the resulting objects in a slicer.

## Recommended First Run

Start with `tutorial_cmy_cubes.3mf`, then move to
`tutorial_expanded_tiles_v62.3mf` after the basic workflow makes sense.

See [../docs/tutorial.md](../docs/tutorial.md) for the full walkthrough.
