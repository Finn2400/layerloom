# Fresh-user rehearsal — 30 October 2026

**Result: the isolated Mac app passed the example and painted-Benchy workflow.
The complete download-to-print acceptance gate remains open.** This was an
agent-operated rehearsal on an existing Mac account, plus an independent agent
review of beginner instructions. It was not a first-time human trial or a clean
macOS account.

## Exact artifact and scope

- Host: Apple Silicon, macOS 14.6.1.
- Artifact: `LayerLoom-0.6.4a5-macos-arm64.dmg`, 316,312,533 bytes.
- SHA-256: `fef6eed441b9233df92b2319d395de65e02810f84c4d5d7993142a5139d67056`.
- Embedded build-info revision: `6935fcdc497e429d76b1d591689dbae9d7a41743`;
  source tree `640a475c690be1da507231e08e1b2b8e56c3ee2b`;
  workflow run `40982690862`. The draft release target is
  `a2ef44fc1cf14f892a52537e995ab40cf87ab082`; do not equate that target with
  the embedded build revision or with the latest source branch.
- Latest source head `cb30fbc0` separately passed Mac build/validation run
  `41003528879`: arm64 and x86_64 builds, macOS 15/26 on both architectures.
  Those later artifacts were not the app operated in this rehearsal.
- Finder copied the app to a new user Applications subfolder. The main
  installation and existing model files were not replaced.
- The local DMG had no quarantine attribute. The fresh Chrome download was
  blocked by a saved browser permission denying `github.com`. No alternative
  download route was used to bypass that decision. Browser download and
  Gatekeeper approval are **not passed** by this trial.
- Draft downloads are still private to repository maintainers. No release was
  published by this trial.

## Observed workflow

| Step | Result |
| --- | --- |
| Open local DMG, copy with Finder, open copied app | Passed; no Terminal or dependency installation used in the GUI workflow |
| Initial mode | Arrange; original pixelated Benchy build plate; Paint off |
| Try the example | Four preassigned colored cubes loaded |
| Default export | Woven apparent colors, 0.16 mm layers, 0.5 mm XY sampling |
| Save chooser | Local LayerLoom folder suggested; no Desktop/Downloads/cloud-access prompt observed |
| Completion | Persistent Export complete panel, filename, folder and slicer layer instructions; no warning-style success alert |
| Show in Finder | Correct 35,031-byte cube output selected |
| Reopen cube output | Three cyan/magenta/yellow material parts; visible woven geometry |
| Load larger example | Eight-part Benchy, 253,176 faces, 37.8 × 25.8 × 36.5 mm |
| Paint | Direct pointer toggle worked; Surface select selected 6,658 hull faces; cyan applied |
| Undo / Redo | Screenshots confirm original orange, then cyan restored |
| Save paint without extension | `.llpaint` added automatically; separate test file written |
| Reopen paint project | Cyan patch remained visible |
| Export reopened painted Benchy | Passed with unchanged 0.16/0.5 mm defaults, 0.001 mm precision and preserved 4,096 MiB estimated-memory budget |
| Reopen printable Benchy | Three parts, 1,431,342 faces; cyan hull patch visible |
| Independent slicer open | Bambu Studio 02.08.02.61 displayed an invalid-config / geometry-only import notice; see open issue below |

The app was responsive enough to show progress and a Cancel export control.
Cancellation itself was not exercised. No privacy settings, quarantine flags,
network settings, printer jobs or cloud permissions were changed.

## Output checks

Direct XML mesh parsing was used because a general-purpose 3MF loader can
misinterpret repeated component paths. Both archives pass ZIP CRC checks.
All six material meshes have positive volume, consistent winding and closed
surfaces. These checks do not replace slicer preview or a physical print.

| Output | Cyan faces | Magenta faces | Yellow faces | SHA-256 |
| --- | ---: | ---: | ---: | --- |
| Cubes | 1,834 | 700 | 1,132 | `ced6d22399fe6f9e65a2f38b57d1c675c6b0e87d42656e10ac2b57274d07c0f1` |
| Painted Benchy | 447,052 | 494,314 | 489,976 | `8adf04059cf1ef86024e2f7a4d15112d788f5de59fad4c43f166a02490f0a85a` |

## Issues exposed by the rehearsal

1. **Slicer handoff needs an explicit tested path.** Bambu Studio's File → Open
   Project displays: “The 3mf file has invalid config, load geometry data only”.
   The output contains filament color metadata but is not a complete Bambu
   printer/process project. This does not establish corrupt geometry, but a
   beginner will understandably interpret it as failure. Verify geometry-only
   continuation and compare File → Import before choosing an export or guide
   change. Do not claim this trial passed slicing.
2. **Source-specific Mac recovery is missing from the short HTML guide.**
   START_HERE links approval instructions/illustration for a DMG app, although
   source users have a `.command` launcher. Bring the source-specific fallback
   from `docs/install.md` into the short guide and label desktop-only advice.
3. **Installer presentation is unfinished.** Four equally prominent top-level
   items, no visible drag instruction/arrow, and a Python-style app icon.
   Add a clear copy cue and branded app icon; avoid presenting the support SVG
   as a competing entry point.
4. **App titles are misleading.** Startup says “Color Assigner v65”; after an
   example loads, Arrange still has a title saying “LayerLoom Paint” and “main
   UI prototype”. These undermine confidence and mode clarity.
5. **Export makes a previously saved paint project dirty.** Updating its color
   mapping marks it modified without explaining why. This rehearsal saved a
   separate mapped copy before reopening the printable output.
6. **Public/default instructions and the preview differ.** The independent
   reviewer observed a public default README using Code → Download ZIP and
   Weave, with no public releases. The preview has a direct ZIP link and newer
   Export geometry instructions. Supply testers one exact build-specific
   entry point; the draft is not a public novice distribution.
7. **Beginner explanations need one final step.** START_HERE ends at Export
   complete without Show in Finder/Show in folder, a slicer definition, or a
   clear explanation that no printer is needed to try the example. Its plain
   file-type glossary is useful. The DMG guide mixes first-run instructions
   with memory/sampling tuning that belongs in optional advanced help.
8. **Accessibility activation needs investigation.** AX activation changed
   the Paint check state without switching panels; direct pointer activation
   switched modes correctly. Multiple test copies and background-window
   rendering complicated observation. This is not evidence that ordinary
   mouse painting fails; test keyboard/VoiceOver and AX separately.

## Remaining gates

- Browser download with genuine quarantine and graphical first-open approval.
- Fresh macOS account or unfamiliar human using the exact downloadable build.
- Complete independent slicer import, filament assignment, 0.16 mm first and
  subsequent layers, adaptive layers off, and layer-by-layer preview.
- Physical offline reopening and example export without changing the user's
  current connection during remote work.
- Novice rehearsal on Intel and Windows; source/CI success is separate evidence.
- Resolve the above findings, rebuild if runtime/packaging changes, and repeat
  affected checks on the actual release assets.

Raw local evidence is in the task's `layerloom-fresh-user-2026-10-30` folder:
artifact metadata, cube/Benchy mesh-validation JSON, installer/startup/export
screenshots, paint/Undo/Redo/reopen comparison, and the exact slicer warning.
Screenshots with personal Finder sidebars or slicer account/history content
must not be published unredacted.

Portable evidence accompanies this report:
[trial metadata](fresh-user-2026-10-30/trial.json),
[cube meshes](fresh-user-2026-10-30/cubes-validation.json),
[Benchy meshes](fresh-user-2026-10-30/benchy-validation.json),
[paint → Undo → Redo → reopen screenshots](fresh-user-2026-10-30/paint-comparison.png),
and [reopened printable Benchy](fresh-user-2026-10-30/benchy-output-reopened.png).
