# Changelog

## 0.6.4a1 - unreleased

- Integrates editable surface painting with the main Paint and Arrange workspace.
- Exports printable filament geometry without per-face paint; editable colors stay in `.llpaint` projects.
- Adds cancellation and an estimated memory budget for geometry conversion.
- Combines the managed desktop/source installer with Paint and packages tutorial models, helper scripts, Paint tests, and workflow documentation.
- Adds installed-wheel Paint conversion checks and native GUI coverage on Linux and Windows.

This is an integration alpha. Release CI, standalone-app validation, and manual first-open checks must pass before distribution.

## 0.6.3 - 2026-06-19

LayerLoom 0.6.3 is a public alpha release focused on the current Qt GUI,
build-plate manipulation, and calibrated CMY palette support.

### Added

- Added the v65 Qt GUI entrypoint through `layerloom-gui`.
- Added whole-model build-plate movement and rotation controls with direct
  viewer interaction.
- Added the optional `Calibrated CMY Normal (core065)` palette.
- Added measured printed-color metadata for calibrated CMY entries, with
  nominal fallback colors for unmeasured recipes.
- Added calibrated Lab/CIEDE2000 matching for imported GLB/3MF source colors
  when the calibrated palette is active.

### Changed

- Kept the default palette workflow unchanged; calibrated CMY is selectable but
  not the default.
- Updated package metadata and documentation for the v65 public entrypoint.

### Notes

- LayerLoom remains alpha research software.
- Calibrated CMY colors are based on the core065 measurement workflow and are
  not a general full-color printer profile.
