# Release And Zenodo Checklist

This repository is intended to stay small and useful for people installing
LayerLoom. Large photographs, generated manuscript figures, slicer exports, and
temporary 3MF/STL outputs should not be committed to the public software repo.
Archive those files separately as release assets or as a Zenodo dataset when
they are needed for manuscript reproducibility.

## Before Tagging A Release

1. Confirm the public examples still exist:
   - `examples/tutorial_cmy_cubes.3mf`
   - `examples/tutorial_cmy_benchy_cutup.3mf`
2. Confirm the source-checkout launchers are present:
   - `Launch LayerLoom.command`
   - `Launch LayerLoom.bat`
3. Confirm a fresh install works:

   ```bash
   python -m pip install -e ".[gui,dev]"
   layerloom-doctor
   pytest
   ```

4. Confirm no local generated files are staged:

   ```bash
   git status --short
   ```

5. Build the source and wheel distributions:

   ```bash
   python -m build
   ```

6. Validate package metadata:

   ```bash
   python -m twine check dist/*
   ```

7. Inspect the built distributions and confirm that packaged examples and
   palette files are present, especially:
   - `src/layerloom/examples/*.3mf`
   - `src/layerloom/palettes/*.json`
   - `src/layerloom/palettes/calibrated_cmy_normal_core065_palette.json`

8. Update `CITATION.cff`, `.zenodo.json`, and `pyproject.toml` if the release
   version or author metadata changes.

## Creating A Persistent DOI

1. Log into Zenodo with GitHub enabled.
2. In Zenodo, enable archiving for `Finn2400/layerloom`.
3. Create a GitHub release from the desired tag, for example `v0.6.3`.
4. Zenodo will archive that GitHub release and mint a version-specific DOI.
5. Add the minted DOI to the README and manuscript once it exists.

## What Belongs In The Software Repo

- Source code needed to install and run LayerLoom.
- Tiny tutorial examples that make the first-run path easy.
- Tests, CI, package metadata, and user-facing docs.
- Small documentation images that help users understand the tool.

## What Should Be Archived Separately

- RAW, DNG, TIFF, and JPEG photo datasets.
- Manual landmark files and large color-analysis work directories.
- Generated manuscript figures and figure-part folders.
- Slicer project files, G-code, BG-code, STL exports, and large generated 3MFs.
- One-off plotting scripts that are manuscript-specific rather than user-facing.

For manuscript reproducibility, create a separate Zenodo record for the analysis
dataset or attach the large materials as release assets. Link that record from
the paper and from this repository after the DOI is available.
