# Surface-region volume construction — October 31, 2026

## Behavior

Arrange now distinguishes open color patches on a valid closed source from an
actually invalid source. Valid patches remain editable region assignments;
Weave reconstructs their intact surface, extends recipes inward by nearest
surface, partitions each layer, and writes closed physical-material volumes.
It reuses the Paint geometry engine without switching the user to Paint.
Separate closed color solids retain the existing CLI/weave path.

The new internal `region-weave` worker validates assigned source geometry and
actual serialized output before replacing a destination. No hole filling,
tolerance welding, normal repair, or removal of small solids is performed.
Disconnected sub-layer solids are retained. First-layer Auto/Off/filament choices
are honored. The preview normalizes a temporary copy of the output assembly;
the printable output retains its aligned multipart structure.

Defaults: 64 imported source colors, 0.5 mm XY sampling, current toolbar layer
height, 0.001 mm geometry precision. The exterior is intentionally layer-stepped.
This does not guarantee a physical color match or support arbitrarily thin details.

## Evidence

Evidence folder: `~/LayerLoom Fresh User Test/Evidence/region-volumes-2026-10-31/`.

- Closed two-color cube: serialized material union equals the placed source,
  with no measured missing or excess volume above 0.00001 mm³.
- Cavity plus disconnected 0.1 mm component: expected 56.1 mm³ retained.
- Textured curved sphere: 64 regions, 20,480 refined surface triangles;
  97,330 output triangles in five physical filaments. Saved parts are closed,
  consistently oriented, and have no collapsed triangles, duplicate faces,
  or nonmanifold edges. Final volume 505.901677 mm³; apparent overlap from
  float32/boolean comparison 0.0000254 mm³ (below validation tolerance).
- Bambu Studio 02.08.02.61 `--info`: manifold=yes, 505.901825 mm³.
  This is CLI import validation, not a GUI sliced preview or a physical print.
- Avocado still blocked: the original 682-triangle mesh has 38 boundary edges,
  with lengths about 0.99–3.34 mm. This is not merely disconnected color patches.
- Ubiquitin still blocked: the imported combined source contains duplicate
  faces/internal walls. No automatic source repair was attempted.

New imports and both bundled examples are centered together on the plate and
grounded by translation only. Saved `.llpaint` projects keep saved placement.
Source files and relative part positions are unchanged; Reset restores the
centered initial import.

## Local regression results

268 passed, 1 skipped across the targeted suites:
- 177 passed / 1 skipped: region weave, desktop GLB import, worker,
  paint export/control, file IO, and workspace geometry.
- 78 passed: native main-window and paint-window integration, including
  actual Arrange import → worker weave → preview reload, both centered
  examples, paint picking after placement, and saved-project reopening.
- 13 passed: native desktop controls, example initialization, tutorial settings,
  and private-copy/source preservation.

The native suite emitted 12 existing XML element-truthiness deprecation warnings.

## Remaining release gates

Run the updated frozen worker/app on both Mac architectures; then repeat browser
download and fresh-account validation. These local source tests do not establish
that the currently published DMG contains this implementation. Real-world source
repair and old closed-region cutting precision defects remain separate work.
