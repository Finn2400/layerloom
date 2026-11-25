# Layer-contour benchmark evidence

The final upright Benchy passes independent serialized-contour checks on all 423 slicing planes and completes native slicing. It **does not pass the 0.02 mm wall-preservation gate**: 309/423 layers fail the strict feature/width gate and 308 fail the combined-feature centerline test; the largest certified displacement upper bound is 0.081207 mm. Keep the mode experimental and opt-in.

Compared with legacy output, final .02 mode uses 39.4% fewer triangles, 44.8% fewer packed 3MF bytes, and 95.9% less observed weaving RSS. The 50% triangle-reduction goal was missed; the 50% RSS-reduction goal passed. Median runtime was essentially unchanged under concurrent validation.

Local file generation and slicing only; no physical print, upload, or printer action.

| Case / mode | Runs | Triangles | 3MF MiB | Median weave s | Peak observed RSS MiB | Builder contour checks |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| legacy_008 / legacy | 3/3 | 5,477,678 | 78.29 | 158.33 | 5263.3 | not certified |
| final_reference_008 / contour-0 | 1/1 | 3,551,088 | 46.21 | 114.68 | 207.8 | passed |
| final_008 / contour-0.02 | 3/3 | 3,318,900 | 43.20 | 160.59 | 215.3 | passed |

RSS is a 50 ms sampled process-tree peak. Geometry counts describe stored mesh resources. Full slicing and wall comparison are separate gates.
Fresh subprocesses were used. Validation, audits, rendering and briefly duplicate cancelled workers ran concurrently with some benchmarks; wall-clock values are contextual rather than isolated throughput claims. Process-tree RSS remains individually sampled. Legacy uses .08 mm geometry bands; its .16/.08 native slice failed at the first band boundary, while .08/.08 native slicing succeeded. The final zero/.02 controlled comparison uses .16/.08 in both models and the same unmodified first-width/thin-wall/elephant-foot profile settings.

## Recorded gates

- serialized contours: **passed**
- final zero/.02 full slicing: **passed**
- legacy .16/.08 full slicing: **failed** (retained compatibility case)
- wall preservation: **failed**
- visual inspection: **recorded**
- physical print: **untested**

## Independent serialized-contour audit

- contour-0-r1.audit.json: passed; 0/423 layers failed. Builder checks alone do not certify serialized output.
- contour-0.02-r1.audit.json: passed; 0/423 layers failed. Builder checks alone do not certify serialized output.

## Full slicing

Times below are native slicing time; staging time is recorded separately in the JSON.

- contour-0-r1.slice.json: completed, 11.19 s; profile/input hashes, placement, overrides and command recorded in report.json.
- contour-0.02-r1.slice.json: completed, 9.20 s; profile/input hashes, placement, overrides and command recorded in report.json.
- legacy-r1.slice.json: FAILED, 15.37 s; profile/input hashes, placement, overrides and command recorded in report.json.
- legacy-native.slice.json: completed, 18.39 s; profile/input hashes, placement, overrides and command recorded in report.json.
- contour-0.01-r1.slice.json: completed, 8.22 s; profile/input hashes, placement, overrides and command recorded in report.json.
- zero-repeat.slice.json: completed, 10.19 s; profile/input hashes, placement, overrides and command recorded in report.json.

## Actual wall comparison

- zero-vs-020-walls.jsonl.summary.json: FAILED; 309/423 layers failed; maximum strict displacement upper bound 0.081207 mm; combined-feature per-filament upper bound 0.081207 mm.
- zero-vs-010-walls.jsonl.summary.json: FAILED; 285/423 layers failed; maximum strict displacement upper bound 0.136361 mm; combined-feature per-filament upper bound 0.136361 mm.
- zero-repeat-vs-original.summary.json: passed; 0/423 layers failed; maximum strict displacement upper bound 0.000000 mm; combined-feature per-filament upper bound 0.000000 mm.

## Visual review

Inspected the final revision 2 color-contour overlays at ten actual planes (including first/last and worst reported contour errors), front/side/top/isometric silhouettes, wall contact sheet, and worst-point close-ups. At displayed raster resolution the hull, lettering, cabin/window sections, roof/chimney holes and recipe boundaries are retained. Dominant wall outlines closely overlay, but close-ups show real inner-loop tip and small-island deposition changes. Geometry visual review is recorded; wall preservation is NOT signed off and physical printing is untested.

This bundle includes failures and untested gates deliberately. Contour validation and resource savings do not certify downstream wall placement. Source evidence, profile hashes and output hashes are retained in [report.json](report.json).

## What the wall differences mean

The same-file native control passes all 423 layers with zero wall displacement. The .01 mm diagnostic tier still fails 285/423 strict layers, with a 0.136361 mm worst upper bound; a smaller mesh tolerance does not monotonically constrain slicer wall decisions.

At the .02 mm worst point (Z 25.6 mm), an inner-wall hairpin terminates about 0.0807 mm earlier while the nearby outer wall is identical. The nominal local swept-wall footprint loses 0.0537 mm². At Z 11.84 mm a small triangular outer-wall island shrinks. These are actual local deposition changes, not only feature labels or relocated seam gaps. The swept-footprint diagnostic uses width/2 round buffers, excludes infill, and is not a physical bead measurement.

A future acceptance guard should compare profile-specific offset curves/inner-wall topology before accepting each simplified layer and fall back around sensitive narrow features. Actual G-code comparison remains necessary. This guard is a proposed follow-up, not an implemented or certified feature.

The all-recorded full-slicing gate above includes the intentionally retained failed legacy .16/.08 case. Both final zero and .02 artifacts pass full slicing; their separate primary gates are recorded in report.json. The prior five-layer serialized precision failure is also retained there and is fixed in the final revision.

## Inspected images

- [Actual-plane color-contour overlays](contours.png)
- [Front/side/top/isometric silhouettes](silhouettes.png)
- [Wall centerline contact sheet](walls.png)
- [Worst-point nominal swept-wall footprint](worst-wall.png)

The synthetic 16× source-density stress case increased observed RSS 1.60×; 16× layer count increased it 1.07×, with three repetitions per scale. These support bounded working memory on those fixtures, not a universal hard RSS ceiling.

## Software validation

At source commit `311620f11f0374a9ca245797e63d1cbda4d9454e`, the final local suite reports **361 passed, 4 skipped**, and package build/content verification passed. This validates software packaging separately from the failed wall-preservation gate and untested physical print.

## Retained files

The final candidate, zero-approximation reference, upright source, legacy
baseline, final G-code and final stress fixtures remain locally available.
Superseded experimental binaries were removed, recovering 341.5 MB in addition
to the benchmark runner's automatic removal of repeat outputs. Their hashes,
measurements, audit records, logs and figures remain; [retention.json](retention.json)
lists the cleanup. The user's original models and printer files were not changed.
