# Advanced import defaults: experimental assessment

Status: experimental assessment followed by a desktop implementation on the draft branch; not released.

## What was tested

Khronos Avocado GLB, SHA-256 ccc9c3ce56423720b09399c2351537207cd5a65f859f9e6e2f30922762f3abd4. The existing Arrange workflow loses its texture and produces one cyan region. This experiment samples its base-color texture through UV coordinates, uniformly subdividing each source triangle in its own plane. It does not smooth, repair, close, or weave the mesh.

Five subdivision depths (682 through 174,592 triangles) and palette sizes 12, 20, and 32 were compared. Color clustering used deterministic area-weighted samples and RGB k-means. The reference was 698,368 finer triangle-center texture samples, not an exact continuous-texture integral. Error is area-weighted mean absolute RGB channel error on a 0–255 scale, not perceptual Delta E or physical print accuracy. This fixture has a texture; combined texture/vertex/material appearance and extensions remain untested.

| Triangles | Error with 20 colors |
| ---: | ---: |
| 682 | 11.96 |
| 2,728 | 11.03 |
| 10,912 | 7.64 |
| 43,648 | 5.64 |
| 174,592 | 5.11 |

The last 4x increase in triangles lowers error only about 9%. At 43,648 triangles, 12 colors scored 9.40, and 32 scored 5.01. At 174,592 triangles, 32 colors scored 3.91. Palette optimization uses one fixed seed and may have local minima. These values support a balanced starting point, not universal thresholds.

## Proposed defaults

- **Appearance: Auto.** Interpret the actual base-color appearance, including texture, material factor, and vertex color contributions. Explicit source overrides belong in Advanced. Never treat a present but unsupported texture as an uncolored success.
- **Detail: Standard.** Use color-aware refinement with a bounded resource budget. The avocado benchmark target is visual quality comparable to about 44k uniformly refined triangles. Do not hard-code that count or subdivision depth for every model. Fine is an optional higher-detail mode. A robust adaptive algorithm and hardware budgets still need implementation/testing.
- **Source palette: not finalized; evaluate 64 as the next candidate.** Distinguish this from physical filament count. The earlier 20-color recommendation was premature. This should be an actual limit, unlike the existing RGB-bin parameter, which can yield more groups than the UI target. Test rare accent-color preservation before adopting area-weighted clustering universally.
- **Scale: use file units; show dimensions.** Avocado is about 42.6 x 62.9 x 27.6 mm; ToyCar is about 73.4 x 28.8 x 74.0 mm. Duck is about 1655 x 1540 x 1153 mm, and BoxVertexColors is 1000 mm per side. Offer explicit Fit to plate for oversized models. Do not silently rescale every file or retain the legacy arbitrary 0.1 multiplier as the standard glTF unit conversion.
- **Orientation: honor format up axis; preview placement.** Retain an Advanced override for nonconforming exporters. No unrequested geometry rotation beyond documented coordinate conversion.
- **Geometry: preserve existing valid solids.** Continuous surface colors require validated volume construction. Open input surfaces should be diagnosed before claiming printable regions. Do not enable generic automatic repair by default.
- **Preferences: per import.** Reset to defaults available; persistence only if requested. Normal view shows model preview, dimensions, and Import; the remaining controls belong in Advanced.

## Release gates

This experiment does not solve open color regions, final-bake precision, texture transforms, transparency, wrapping modes, rare colors, adaptive refinement topology, or material-volume construction. The original avocado has open topology. Recovered colors alone do not establish printability. Keep a7 held until actual saved woven geometry and slicer previews pass.

Next implementation checks: texture-only avocado, vertex-only controlled cube, combined material/vertex/texture synthetic fixtures, UV seams and wrapping, unsupported extensions, actual palette cap, cancellation/resource caps, scale and orientation, and real ubiquitin solid-region regressions. Confirm any topology change preserves source shape and intended colors.

Evidence folder: `/Users/finn/LayerLoom Fresh User Test/Evidence/import-defaults-2026-10-31/` contains `sweep.py`, `results.json`, `input-audit.json`, and `sampling-comparison.png`. Original downloaded assets and hashes remain under `Evidence/online-glb-2026-10-31/`.

## Follow-up: higher color counts

Repeated all five subdivision levels with 20, 32, 64, 128, and 256 colors, preserving the fixed seed, reference, and sampling method (25 combinations). At 174,592 triangles, errors were respectively 5.109, 3.905, 3.180, 2.807, and 2.550. At 43,648 triangles they were 5.642, 5.010, 4.477, 4.125, and 3.919. Higher color counts consistently improve this fixture; 20 is not justified as a general default. At the finest tested mesh, 64 reduces error by 38% versus 20; 256 reduces it by 50%. Palette clustering and assignment took approximately 0.08, 0.10, 0.16, 0.36, and 1.08 seconds respectively at that mesh density. These are NOT end-to-end import/weave timings.

64 is a reasonable next experimental candidate, with 128/256 available for comparison, pending downstream region construction and actual printable-recipe mapping. These source-color previews do not simulate physical filament gamut or woven print appearance. More source colors do not mean more filaments. A source color cap should not be finalized solely from this fixture, nor should source colors be merged before evaluating which distinctions survive recipe mapping.

New evidence: `sweep_higher_colors.py`, `higher-colors-results.json`, `higher-colors.log`, and `higher-colors-comparison.png` in the same evidence directory. No runtime defaults or release artifacts were changed.

## Implementation follow-through

The desktop now defaults to a real maximum of 64 source-color groups, with a per-file Import / Advanced dialog. It uses a new isolated `glb-import` worker; existing public `glb-split` and headless CLI behavior remains unchanged. Auto combines basic base-color texture, material factor, and vertex colors in linear light before display-color conversion. Unsupported appearance extensions, non-repeat wrapping, additional UV sets, and transparency fail explicitly. Standard and Fine have bounded planar texture refinement; this is currently uniform per mesh rather than color-adaptive.

Default units are standard glTF metres converted to millimetres, and Y-up is converted to Z-up. Overrides allow millimetres, legacy 0.1 scale, and Z-up. The old toolbar color-count control is replaced by the Advanced tab. This iteration does not yet include a pre-import 3D preview or automatic fit-to-plate option.

Actual avocado GUI import: 64 source-color groups, 43,648 triangles, approximately 4 seconds, original GLB unchanged. The old test harness reports triangle-count preservation as false because texture refinement intentionally subdivides the original 682 faces in their own planes. It preserves the piecewise planar source surface, not its triangle count. Colors now appear in Arrange and map to the existing Normal recipe palette. This is distinct from the experimental full-source-color renders above.

Imported regions are checked for closed volume and for defects in the serialized coordinates, including float32 collapsed triangles and duplicate faces. A persistent main-window message and pre-weave guard stop legacy region weaving when these checks fail. The avocado is inspectable but still cannot produce validated material volumes. No generic automatic repair is applied. Full volume construction, remaining weave-stage defects, frozen app validation, and release publication remain outstanding.

Regression evidence: the initial new importer lost mixed material/COLOR_0 data when calling trimesh's TextureVisuals.copy(). A real rainbow-ubiquitin import exposed this; the implementation now explicitly preserves that attribute and a synthetic GLB regression covers it. Retesting ubiquitin with legacy scale and Z-up retains all 74,372 triangles, produces 64 colored regions, and flags the open regions. The source-color cap and topology blocking are therefore exercised on both the avocado and a real protein export.

Validation: the combined native GUI, desktop integration, worker, GLB compatibility, and initial importer suite passed 74 tests; three additional backend checks (combined appearance, scene instances, and material-plus-vertex-color copy preservation) also passed. Existing deprecation warnings remain in the legacy XML helper. Native GLB integration was rerun after the copy fix. No new DMG has been built or published from this change.
