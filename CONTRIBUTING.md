# Contributing

LayerLoom is currently alpha-stage research software. Contributions are welcome,
but please keep changes focused and testable.

## Local Setup

For running LayerLoom, use the [installation guide](docs/install.md). For
development, install [uv](https://docs.astral.sh/uv/getting-started/installation/),
then run these commands from the extracted repository folder. uv selects the
pinned managed Python and installs the committed dependency lock.

```bash
uv sync --managed-python --locked --extra gui --extra bench --extra dev
uv run --no-sync pytest
```

## Development Notes

- Keep large generated files out of git.
- Prefer small fixtures over real slicer projects in tests.
- Avoid changing the versioned GUI entrypoints unless needed for compatibility.
- Add stable wrappers for public commands instead of asking users to import files
  such as `3mf_gui_v61.py` directly.

## Before Opening A Pull Request

```bash
uv run --no-sync pytest
uv run --no-sync python -m build
```

If GUI behavior changed, include manual test notes for the current Qt app.
