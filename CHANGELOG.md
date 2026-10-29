# Changelog

## 0.6.4a3 - unreleased

- Restores the bundled Benchy's CMY colors when a slicer's display names hide the original part recipe tags.
- Resolves equivalent tutorial CMY recipe spellings against the selected palette, so every Benchy part receives its intended recipe.
- Preserves those recipes and imported colors when saving and reopening an editable paint project.
- Adds checks of the actual eight-part Benchy colors and recipe assignments to source tests and the frozen app smoke test.
- Corrects a false overlap report in the validation measurement of final color partitions; material construction and accepted tolerances remain unchanged.
- Records frozen GUI stage timings, responsiveness and progress, with bounded five-minute export waits and a fifteen-minute outer verifier deadline.

This candidate requires new source distributions, both Mac installers and their own validation. The passing 0.6.4a2 baseline does not validate the rebuilt 0.6.4a3 artifacts.

## 0.6.4a2 - unreleased

- Selects the tutorial layer height of 0.16 mm automatically, including after reopening a saved tutorial project.
- Gives the larger Benchy example a 4,096 MiB estimated export budget and shows the active budget in the main export summary; ordinary models retain the 2,048 MiB default.
- Cancels selection-flash timers when a model or window closes, and restores colors correctly after repeated selection.
- Reports unsupported macOS Qt platforms before initializing the native 3D viewer.
- Bundles decorative build-plate artwork so startup no longer searches the user's Downloads folder or requests access to it for that decoration.
- Builds on maintained macOS 15 runners while retaining the ARM macOS 14 target and a required test of each release artifact on macOS 14.
- Updates the Mac first-run and Paint demonstration instructions.

This candidate remains unpublished. Each rebuilt installer needs its own download, Finder, GUI, offline and slicer acceptance evidence.

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
