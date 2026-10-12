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
   - `Launch LayerLoom.sh`
   - `scripts/bootstrap.py`, both bootstrap wrappers, and `scripts/runtime-pins.txt`
   - `.python-version` and `uv.lock`
3. Confirm a fresh install works:

   ```bash
   uv sync --managed-python --locked --extra gui --extra dev --no-editable
   uv run --no-sync layerloom-doctor
   uv run --no-sync pytest
   ```

4. Confirm no local generated files are staged:

   ```bash
   git status --short
   ```

5. Build the source and wheel distributions:

   ```bash
   uv run --no-sync python -m build
   ```

6. Validate package metadata:

   ```bash
   uv run --no-sync python -m twine check dist/*
   ```

7. Inspect the built distributions and confirm that packaged examples and
   palette files are present, especially:
   - `src/layerloom/examples/*.3mf`
   - `src/layerloom/palettes/*.json`
   - `src/layerloom/palettes/calibrated_cmy_normal_core065_palette.json`

8. Check that the intended version and tag are not already published. Update
   `CITATION.cff`, `.zenodo.json`, `pyproject.toml`, and `src/layerloom/version.py`
   together if the version changes; regenerate the lock and rerun checks.
   Do not replace an existing release or move an existing tag.

## Standalone Mac Release

Use the **Mac desktop** workflow for a manual or pull-request build first. Relevant
pull requests build and validate both architectures before merge. It uses only
free standard GitHub Actions runners in a public repository, with ad-hoc signing
and no paid Apple Developer account or notarization. The workflow intentionally
skips private repositories rather than incur runner charges.

The build produces `LayerLoom-<version>-macos-arm64.dmg` (macOS 14+) and
`LayerLoom-<version>-macos-x86_64.dmg` (macOS 15+), SHA-256 files, architecture-specific
notices and build-info files, and archived applications that preserve executable
permissions and symbolic links. The oldest supported OS builds each architecture
once; the same app is validated on macOS 14/15/26 for arm64 and 15/26 for Intel.
Keep the CI run URL, commit SHA, reports, and screenshots with the release evidence.

Reproduce locally on a native Mac with pinned uv:

```sh
UV_PROJECT_ENVIRONMENT=.venv-desktop uv sync --managed-python --locked --extra gui --extra desktop --no-default-groups --no-editable
.venv-desktop/bin/python scripts/build_macos.py
.venv-desktop/bin/python scripts/validate_macos.py dist/macos/LayerLoom.app --gui --report dist/macos/validation
```

`packaging/macos/bundle_config.py` enumerates the runtime resources. `build-info.json` inside the application records the runtime, architecture, target OS and lock hash. `THIRD_PARTY_NOTICES` inside the application includes dependency metadata and license texts. Review PyQt/Qt redistribution and corresponding-source obligations before publishing; the source repository's MIT license does not replace dependency terms.

Follow [the desktop release checklist](../packaging/macos/RELEASE_CHECKLIST.md), including inspection of GUI screenshots, browser quarantine, Finder opening, offline reopening, app replacement, and a person unfamiliar with Python completing the example. These manual checks cannot be inferred from automated tests. Once the initial gates pass, set the Actions repository variable `MAC_DESKTOP_RELEASES_ENABLED` to `true` to enable tagged draft builds. Tags must match the app version. All releases remain drafts until their manual checks pass; the workflow never publishes them.

### Preparing an unpublished draft

A maintainer may assemble an unpublished draft from successful manual or
pull-request build artifacts while manual checks remain pending. Keep the draft
tied to the exact tested commit and version; never combine assets from different
commits. Creating a draft does not satisfy a publication gate or require enabling
tag-triggered drafts. Do not enable the repository release variable merely to
work around an incomplete checklist.

Draft releases and assets are visible only to maintainers with repository write
access. They are not public download links for new users. Attach:

- Both architecture DMGs and their SHA-256 files.
- The matching source distribution and wheel.
- `THIRD_PARTY_NOTICES-arm64.tar.gz` and `THIRD_PARTY_NOTICES-x86_64.tar.gz`.
- `LayerLoom-arm64-build-info.json` and `LayerLoom-x86_64-build-info.json`.
- `Read-Me-First.html`, `mac-first-open.svg`, and `RELEASE_CHECKLIST.md`.

Keep the source-installation quick start as the public default until a validated
Mac download is published. Before publication, record completed manual checks,
resolve corresponding-source obligations, replace the draft notices in README,
install/first-open docs, release notes, and the DMG HTML, and rebuild if bundled
instructions changed. The default Mac link must point to an actual public DMG.

## Creating A Persistent DOI

1. Log into Zenodo with GitHub enabled.
2. In Zenodo, enable archiving for `Finn2400/layerloom`.
3. After the release checks pass, publish a GitHub release from the desired
   unused tag, for example `v0.6.3`. A draft is not a publication.
4. Verify that Zenodo archived the published release and minted a version-specific DOI.
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
