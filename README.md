# LayerLoom

Make more apparent print colors from a few filaments. Open a model, choose its
colors, and export a printable file for your slicer.

## Download for Mac

**[Download for Apple Silicon](https://github.com/Finn2400/layerloom/releases/download/v0.6.4a6/LayerLoom-0.6.4a6-macos-arm64.dmg)** — M1/M2/M3/M4 or newer, macOS 14+

[Download for Intel](https://github.com/Finn2400/layerloom/releases/download/v0.6.4a6/LayerLoom-0.6.4a6-macos-x86_64.dmg) — macOS 15+

Not sure? **Apple menu → About This Mac** shows your chip and macOS version.
You only need the DMG for your Mac. No Python, Git, Homebrew, or Terminal setup.
This is an **early-access release**; inspect your result in a slicer before printing.

1. Open the downloaded DMG and drag **LayerLoom** onto **Applications**.
2. Open **Applications → LayerLoom**. Allow about half a minute on first launch.
3. Click **Try the example**, then **Export geometry 3MF…**.
4. Keep **Woven apparent colors** and **0.16 mm**, click **Choose save location…**,
   and choose where to save. Wait for **Export complete**, then **Show in Finder**.

You have made your first printable model. No printer is needed to try it.
A **3MF** is the model file; a **slicer** prepares that file for your printer.
[Open your result in a slicer](docs/tutorial.md#open-the-3mf-in-a-slicer).

<details>
<summary><strong>Mac blocked the app?</strong></summary>

Dismiss the warning, then open **Apple menu → System Settings → Privacy & Security**.
Scroll to **Security**, find LayerLoom and click **Open Anyway**. Confirm and enter
your Mac password if asked. Then open LayerLoom again if needed.

This free app is not Apple-notarized. Approve only the copy downloaded from this
repository. If the warning says the app is **damaged** or **will damage your computer**,
stop and [report the exact message](https://github.com/Finn2400/layerloom/issues).
Some managed Macs do not allow exceptions.

[Illustrated first-open guide](docs/mac_first_open.md)
</details>

## Try painting

LayerLoom starts with the original build plate in **Arrange** mode. Turn on **Paint**
when you want to edit a surface: select an area, choose a color, then **Apply paint**.
Turn Paint off to return to Arrange. **Save paint…** keeps an editable `.llpaint`
project; **Export geometry 3MF…** creates the separate printable model.

For a larger example, choose **Help → Load the Benchy example**.
[Full tutorial](docs/tutorial.md) · [Troubleshooting](docs/mac_first_open.md)

## Windows, Linux, and source installation

[Download the source ZIP](https://github.com/Finn2400/layerloom/releases/download/v0.6.4a6/LayerLoom-v0.6.4a6-source.zip),
extract it, and open **START_HERE.html**. On Windows, double-click
**Launch LayerLoom.bat**. The launcher installs its own Python automatically;
first setup needs internet. [Source installation guide](docs/install.md).

The Mac downloads above already include the required software and work offline
after installation. GitHub's **Source code** ZIP is not the Mac app.

## More information

[Release notes and checksums](https://github.com/Finn2400/layerloom/releases/tag/v0.6.4a6) ·
[Developer installation](docs/install.md#developer-installation) ·
[Report a problem](https://github.com/Finn2400/layerloom/issues)

Use **Help → Copy diagnostics** and **Help → Open logs** when reporting a problem.
LayerLoom is alpha software; printer-specific project settings are chosen in your
slicer. Physical print quality depends on your model, filaments and printer.

## Palette Options

The default `Normal` palette uses LayerLoom's nominal model-generated colors.
The optional `Calibrated CMY Normal (core065)` palette uses measured printed
CMY colors from the core065 gamut analysis where available, falling back to
nominal colors for unmeasured recipes. When this palette is active, GLB/3MF
source colors are matched against the measured printed colors, so imported
colors choose the recipe expected to print closest rather than the recipe whose
nominal screen color is closest.

## Command Line Tools

These commands are for the developer environment described in the
[installation guide](docs/install.md#developer-installation). Run them with
`uv run --no-sync` from the source folder; the beginner launchers manage a
separate private environment and do not add commands to your system PATH.

Check whether the install is ready for the tutorial/GUI path:

```bash
uv run --no-sync layerloom-doctor
```

Normalize a 3MF into LayerLoom's generic internal 3MF form:

```bash
uv run --no-sync layerloom-normalize input.3mf --output normalized.3mf
```

Generate a woven 3MF:

```bash
uv run --no-sync layerloom-weave --input input.3mf --step 0.08 --output output_woven.3mf
```

By default, LayerLoom makes the first weave layer a single filament color and
automatically chooses the color with the largest footprint on the model base.
Use `--first-layer-color c` (or another filament token) to choose it explicitly,
or `--first-layer-color off` to retain the repeating pattern on the first layer.

Run the headless GLB-to-woven workflow, intended for colored GLB/GLTF exports
from tools such as ChimeraX:

```bash
uv sync --managed-python --locked --extra gui --extra headless
uv run --no-sync layerloom-headless protein.glb
uv run --no-sync layerloom-headless protein.glb --output ~/Downloads/protein_woven.3mf --orientation-quality thorough
```

By default, `layerloom-headless` imports up to about 20 GLB color groups, matches
them to the `Normal` palette, safely attempts mesh repair, searches for a
support-saving orientation, fits the footprint into a 180 x 180 mm box, weaves at
0.08 mm, and writes the final woven 3MF to `~/Downloads`.

Generate benchmark geometry:

```bash
uv run --no-sync layerloom-simplified-speed-suite --help
uv run --no-sync layerloom-benchmark-suite --help
```

## How LayerLoom Works

LayerLoom shifts color planning into geometry:

1. Normalize the input model into a generic internal 3MF.
2. Preserve best-effort display names, source color metadata, and weave tokens.
3. Assign each editable part a weave signature such as `cy`, `yyyc`, or `cym`.
4. Slice selected geometry into horizontal Z-bands at the chosen weave height.
5. Regroup bands into per-filament output objects such as `all_cyan`,
   `all_magenta`, and `all_yellow`.
6. Open the resulting 3MF in a standard slicer and assign those objects to the
   physical filaments loaded in the printer.

Add `__NOWEAVE__` to a part name to exclude that part from slicing and color
grouping. LayerLoom carries the mesh and its full name through as an independent
output object, so it remains separately selectable and recolorable in the slicer.

LayerLoom normalizes incoming 3MFs before editing:

- resolves build items, components, transforms, and production-extension child models
- flattens reachable geometry into editable mesh parts
- preserves best-effort display names and color/pattern metadata
- writes LayerLoom-style generic 3MF outputs rather than preserving full slicer project semantics

## Slicer Compatibility

LayerLoom 3MF outputs have been opened and sliced successfully in:

- OrcaSlicer
- Bambu Studio
- PrusaSlicer
- Ultimaker Cura 5.12.0

For Cura, disable automatic object drop-to-plate behavior before opening woven
LayerLoom outputs. Otherwise Cura may reinterpret the intentional vertical
banding/placement in a way that breaks the woven geometry.

More setup notes are in
[docs/slicer_compatibility.md](docs/slicer_compatibility.md). Install and
platform troubleshooting is in [docs/install.md](docs/install.md).

## Layer Height Rules

Use the same slicer layer height as the LayerLoom weave height. The first layer
height should be an integer multiple of the weave height so slicer layers stay
aligned to woven Z-bands.

Examples:

| Weave height | Valid first layer examples | Avoid |
| --- | --- | --- |
| `0.08 mm` | `0.08 mm`, `0.16 mm`, `0.24 mm` | `0.20 mm` |
| `0.12 mm` | `0.12 mm`, `0.24 mm` | `0.20 mm` |
| `0.16 mm` | `0.16 mm`, `0.32 mm` | `0.20 mm`, `0.24 mm` |
| `0.20 mm` | `0.20 mm`, `0.40 mm` | `0.24 mm` |

For example, if the weave height is `0.08 mm`, a `0.16 mm` first layer is fine,
but `0.20 mm` is not because it cuts through the weave schedule at a half-step.

## Practical Caveats

- LayerLoom produces apparent woven colors, not full-color material mixing.
- The calibrated CMY palette improves display and matching for measured CMY
  recipes, but it is still specific to the measured printer/material/lighting
  workflow behind the core065 analysis.
- Broad flat top and bottom surfaces reveal layer striation more strongly than
  steep or vertical surfaces.
- High-contrast filament combinations show woven layers more readily than
  lower-contrast combinations.
- Changing the target weave pattern requires regenerating geometry.
- Vendor-specific 3MF features such as slicer modifiers, textures, and project
  settings are not guaranteed to survive normalization.
- Thin features near weave-band boundaries may need inspection after weaving.

## Repository Layout

```text
src/layerloom/
  3mf_gui_v65.py              # current GUI implementation
  gui_app.py                  # stable GUI command wrapper
  cli.py                      # stable CLI wrappers
  normalize_3mf_import.py     # canonical 3MF import normalizer
  weave.py                    # woven 3MF generation pipeline
  palettes/                   # packaged palette JSON files
  examples/                   # packaged tutorial 3MF files used by Load Example
  legacy/                     # older/experimental code
examples/
  tutorial_cmy_cubes.3mf      # beginner CMY sample input
  tutorial_cmy_benchy_cutup.3mf
tests/
  test_smoke.py               # lightweight import/package checks
```

## Large Files

Generated 3MF/STL/GLB files are intentionally ignored by git. The tiny tutorial
fixtures in [examples/](examples/) are the exception. Keep large test models,
slicer exports, and generated benchmark outputs outside the tracked repo or
publish them separately as release assets or Zenodo datasets.

Raw photographs, calibrated TIFF exports, manuscript figure workspaces, and
one-off analysis outputs are also intentionally kept out of the public software
repo. See [docs/release_and_zenodo.md](docs/release_and_zenodo.md) for the
release checklist and archival DOI workflow.

## Citation And DOI
(preprint)
Beruldsen, Finn, Existing Multi-Material FFF Printers Are Sufficient For (Near) Arbitrary Coloration. Available at SSRN: https://ssrn.com/abstract=6876564 or http://dx.doi.org/10.2139/ssrn.6876564

## License

MIT. See `LICENSE`.
