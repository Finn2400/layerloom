# Changelog

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
