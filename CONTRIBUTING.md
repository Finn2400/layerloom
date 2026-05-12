# Contributing

LayerLoom is currently alpha-stage research software. Contributions are welcome,
but please keep changes focused and testable.

## Local Setup

```bash
python -m pip install -e ".[gui,bench,dev]"
python -m pytest
```

## Development Notes

- Keep large generated files out of git.
- Prefer small fixtures over real slicer projects in tests.
- Avoid changing the versioned GUI entrypoints unless needed for compatibility.
- Add stable wrappers for public commands instead of asking users to import files
  such as `3mf_gui_v61.py` directly.

## Before Opening A Pull Request

```bash
python -m pytest
python -m build
```

If GUI behavior changed, include manual test notes for the current Qt app.
