# GLB colored-region regression and release hold

The main Arrange Open action had been redirected to the surface-paint parser.
A controlled GLB containing two colored boxes in one mesh therefore opened as
one editable mesh with two face colors. The legacy converter instead produces
two named color-region meshes, preserving all 24 source triangles.

The follow-up implementation restores that original region conversion,
placement, palette assignment and geometry-weaving path for GLB/GLTF files
opened in Arrange. Explicit Paint imports retain their surface-editing path.
Entering Paint from an imported region model materializes the current placement
and assignments rather than reopening the raw GLB and losing those settings.

Local validation: 85 native GUI/desktop checks passed, including actual region
weaving and output reopening, explicit Paint GLB import, and preservation of an
existing document when conversion fails. The packaged GUI acceptance script
now also imports a two-region GLB, assigns it, invokes the geometry weave and
reopens its material output. These checks do not establish geometric validity
for arbitrary models.

## Larger real-world check

Local input: `MAGEA3_print_same_scale.glb`, retained in the user's Downloads.
SHA-256: `c0cb5170e6c557c83294a7d0093c86b2a2acd0575cdc6e2c7a39f4d14fe914`.
The original file was unchanged. No model is embedded in this report.

- 351,610 source triangles; all preserved by region import.
- Two assigned regions; bounds preserved with the existing import scale of 0.1.
- GUI import approximately 21 seconds; geometry weave approximately 50 seconds.
- Woven 3MF reopened with 793,616 triangles in cyan/yellow material objects.

**Release blocker:** reopening is not sufficient validation. Exact-coordinate
and float32 checks of the woven file found:

| Material | Zero-area triangles (float64) | Zero-area triangles (float32) | Duplicate faces | Closed after exact coordinate welding |
| --- | ---: | ---: | ---: | --- |
| Cyan | 115 | 134 | 4 | No |
| Yellow | 14 | 15 | 74 | No |

The assigned input had no float64 zero-area triangles or duplicate faces and
was closed/oriented. Its cyan region already had eight float32 collapses.
The old geometry weave adds further defects. This is separate from the loss of
color-region import caused by the Paint routing change.

A scratch experiment using double-precision solid clipping and true material
union improved the intermediate geometry, but the final serialized/baked file
still failed float32 and closure checks. It is not a validated fix and is not
included in production code. The bake spool currently stores float32 vertices;
serialization and placement must be included in any proposed correction.

## Publication gate

Keep the follow-up PR in draft and a7 unpublished. Do not switch public download
links to nonexistent or unvalidated assets. The UI-navigation PR #5 is already
merged. Public a6 remains available; its release help links are pinned to a6.

Before publication, fix or explicitly scope the geometry limitation, rerun this
larger model through the complete serialized output path, verify topology,
float32 coordinates, region/component retention, bounds and volume, then rerun
packaged acceptance and slicer inspection. Do not use generic post-export repair
as evidence that geometry or color regions were preserved.

Local detailed logs, screenshots, controlled fixture and diagnostic scripts:
`/private/tmp/layerloom-glb-region-regression/`. Durable evidence is copied to
`~/LayerLoom Fresh User Test/Evidence/glb-regions-2026-10-30/`.
