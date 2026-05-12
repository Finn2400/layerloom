# LayerLoom legacy and local archive

This directory holds code and generated artifacts that were useful during
development but are not part of the current public-facing LayerLoom workflow.

Current GUI/runtime entrypoints remain in the package root:

- `3mf_gui_v61.py`
- `3mf_gui_v60.py` (base interaction layer used by v61)
- `3mf_gui_v58.py` (base UI layer used by v60)
- `gui_app.py`

Archived areas:

- `gui_versions/`: older GUI entrypoints no longer used by the package scripts.
- `replaced_modules/`: superseded backups and experimental module variants.
- `cube_tools/`: earlier cube-generation utilities retained for reference.
- `scratch_geometry/`: local generated geometry, old cube tests, and large STL/3MF assets.
- `generated_outputs/`: local benchmark and plotting outputs.
- `generated_cache/`: Python bytecode caches moved out of the source tree.

The scratch/generated directories are intended as local reference only and are
ignored by git. Move anything still needed for a reproducible public example into
a small fixture or documented release asset before publishing.
