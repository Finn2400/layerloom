# LayerLoom

LayerLoom is an experimental Python toolkit for importing 3MF/GLB models,
assigning palette colors, and generating woven multi-material 3MF files for
slicer workflows.

The current public entrypoint is the Qt GUI from the v61 line, which includes
vendor 3MF normalization and streaming preview support for large models.

## Status

LayerLoom is alpha research software. It is useful, but the public API is not
stable yet. Expect file-format edge cases and slicer-specific quirks.

## Install From A Clone

```bash
git clone https://github.com/your-org/layerloom.git
cd layerloom
python -m pip install -e ".[gui]"
```

For development and benchmark plotting tools:

```bash
python -m pip install -e ".[gui,bench,dev]"
```

## Run The GUI

```bash
layerloom-gui
```

From a source checkout you can also run:

```bash
python src/layerloom/3mf_gui_v61.py
```

## Command Line Tools

Normalize a 3MF into LayerLoom's generic internal 3MF form:

```bash
layerloom-normalize input.3mf --output normalized.3mf
```

Generate a woven 3MF:

```bash
layerloom-weave --input input.3mf --step 0.12 --output output_woven.3mf
```

Generate benchmark geometry:

```bash
layerloom-simplified-speed-suite --help
layerloom-benchmark-suite --help
```

## What The Importer Does

LayerLoom normalizes incoming 3MFs before editing:

- resolves build items, components, transforms, and production-extension child models
- flattens reachable geometry into editable mesh parts
- preserves best-effort display names and color/pattern metadata
- writes LayerLoom-style generic 3MF outputs rather than preserving full slicer project semantics

## Repository Layout

```text
src/layerloom/
  3mf_gui_v61.py              # current GUI implementation
  gui_app.py                  # stable GUI command wrapper
  cli.py                      # stable CLI wrappers
  normalize_3mf_import.py     # canonical 3MF import normalizer
  weave.py                    # woven 3MF generation pipeline
  palettes/                   # packaged palette JSON files
  legacy/                     # older/experimental code
tests/
  test_smoke.py               # lightweight import/package checks
```

## Large Files

Generated 3MF/STL/GLB files are intentionally ignored by git. Keep large test
models, slicer exports, and generated benchmark outputs outside the tracked repo
or publish them separately as release assets.

## License

MIT. See `LICENSE`.
