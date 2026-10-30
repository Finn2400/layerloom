# Windows GUI acceptance trial — 0.6.4a5

Test date: October 29, 2026. Candidate source commit:
`a2ef44fc1cf14f892a52537e995ab40cf87ab082`.

## Scope and method

Tested on a physical Windows 11 Pro x64 computer, version 25H2, build
26200.9711. The launcher acquired managed CPython 3.12.15. Installation began
from the exact commit's GitHub ZIP downloaded in Windows Chrome, extracted
into a separate `LayerLoom Test a5` folder. No Git or GitHub login was needed.
The owner assisted with extraction and the first launcher invocation.

Remote GUI control used Parsec. Its native Mac client did not reliably forward
automated clicks; its official Chrome client did. Subsequent actions used
visible Windows controls, coordinate clicks and individual key events. Bulk
text insertion and some modifier combinations were unreliable in the remote
connection. These are control limitations, not demonstrated LayerLoom defects.

This computer already had development software installed. It was not a clean
Windows installation without Python. No persistent PowerShell policy, operating
system security setting or cloud-folder permission was changed for this trial.

## Observed passes

- The launcher downloaded its private Python and installed the locked GUI
  environment. Resource checks, GUI imports and lib3mf native initialization
  reported success. LayerLoom opened without manually configuring Python.
- Initial startup showed Arrange, the empty build plate with pixelated Benchy
  artwork, and the example/model onboarding actions. Paint was secondary.
- The tutorial cubes rendered, exported at 0.16 mm layers and 0.5 mm XY
  sampling, and reopened as the three CMY material meshes with visible bands.
  The output was displayed as 35 KB in Explorer.
- Export settings initially hid advanced controls. Completion used a persistent
  panel with filename, destination and slicer instructions. Show in folder
  revealed the output in Explorer.
- The larger tutorial Benchy loaded with 8 parts and 253,176 faces, measuring
  37.8 × 25.8 × 36.5 mm.
- In Paint, surface selection picked 6,658 hull faces. Cyan paint applied
  visibly. Undo restored the previous orange surface; Redo restored cyan.
- The editable project saved and reopened with the painted area retained.
  Returning to Arrange preserved the mixed-paint state.
- The painted Benchy completed geometry export and reopened with three CMY
  material parts and 1,431,342 faces. The cyan-painted hull area remained
  visible in the woven geometry. Explorer displayed the output as 13,614 KB.
- Closing and reopening through `Launch LayerLoom.bat` returned to the original
  Arrange build plate. No dependency installation was visible during relaunch.
  Windows again requested Run approval for the downloaded batch file; its
  “Always ask” option remained enabled.

Benchy export settings: woven apparent colors; layer height 0.16 mm; XY
sampling 0.5 mm; standard geometry precision 0.001 mm; memory budget 4,096 MiB;
sampling budget 2 million cells. The planning estimate was 228 layers, 960,336
cells and 1,256 MiB additional memory. Export ran for approximately 2–3 minutes;
this was a visual observation, not a benchmark. Progress and application
responsiveness were observed during sampling and finalization.

## Retained outputs

All outputs remain in the user's isolated Windows test folder:

- `windows-cubes-a5.3mf`
- `windows-painted-benchy-a5.llpaint` — initial saved surface paint
- `windows-mapped-benchy-a5.llpaint` — saved after export updated mapping
- `windows-painted-benchy-a5_geometry.3mf`

Private screenshots and the detailed machine-local record are retained by the
tester outside the repository. Desktop screenshots containing personal folder
names are not included in release assets.

## Usability findings

- The full GitHub source ZIP was 675 MB because it includes manuscript
  materials. Prefer the approximately 8 MB curated source ZIP when publishing
  the release, and keep its extraction/launcher steps prominent.
- The observed Windows prompt was “Open File – Security Warning,” rather than
  only SmartScreen's “Windows protected your PC.” Both paths need instructions.
  The source guide now covers the observed prompt; the tested candidate itself
  predates this documentation amendment.
- Window titles still expose internal labels such as “Color Assigner v65” and
  “main UI prototype.” These should be replaced with consistent product/version
  labels in a future candidate.
- Export can update material mapping after an editable project was saved.
  Opening another model then prompts to save again. Saving succeeded, but this
  behavior deserves clearer wording for novice users.

## Limits and remaining gates

These passes establish the listed workflows on this Windows computer, not all
features or universal compatibility. This trial did not independently validate
the exported meshes in a slicer, perform a physical print, test an offline
relaunch, or exhaustively exercise painting tools. CPU/GPU/RAM details were not
recorded. The ordinary successful relaunch is not evidence of offline operation.

The Windows source trial does not replace the exact-artifact Mac download,
Gatekeeper, offline, relocation, replacement and novice first-use gates in the
[release checklist](../../packaging/macos/RELEASE_CHECKLIST.md). No release was
published as part of this trial.
