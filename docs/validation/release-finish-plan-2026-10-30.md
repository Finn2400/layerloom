# Finish the Mac release: findings and bounded patch plan

30 October 2026. Reviewed the fresh-session report, observation log, screenshots, newly exported 3MF metadata, Bambu recovery metadata and retained G-code. This review changes no application code, installed app, slicer state or release visibility. Compact machine-readable evidence is in [slicer-evidence.json](post-trial-2026-10-30/slicer-evidence.json).

## What is established

The downloaded a5 app completed Finder installation with user-assisted graphical approval, default Arrange/pixelated plate, cubes, Benchy painting, Undo/Redo, editable-project persistence, printable export/reopen, ejected-installer use and relocation. The fresh files passed the agent's individual-mesh checks. These do not require repeating the full development test suite before narrowing the remaining work.

The remaining critical weakness is the slicer handoff, followed by the beginner-facing entry path. Offline and pristine-account use remain untested. The observed screen-capture permission failure belonged to the automation helper, not LayerLoom; do not put Screen Recording authorization into end-user installation instructions.

### Repeated Plate 1 names

The original `painted-benchy.3mf` names its assembly `painted-benchy` and parts `all_cyan`, `all_magenta`, `all_yellow`; no model/config/XML member contains `Plate 1`. Bambu's recovery `.3mf` contains the nested text in its plate name, object name and first part name. This is a saved mutation after export, not only a rendering artifact. The original output remains unchanged.

The fresh agent observed growth during Bambu automation. That makes a Bambu/automation interaction a plausible cause, but does not identify the triggering action. Bambu's current source formats a custom plate name as `Plate N (custom name)`; feeding the formatted label back into the custom-name field would produce this pattern. The inspected upstream source is not a controlled reproduction on installed 02.08.02.61. Do not “fix” LayerLoom by stripping strings without evidence that its export triggers the mutation.

Source: [Bambu ObjectDataViewModel.cpp](https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/GUI/ObjectDataViewModel.cpp), `SetCurSelectedPlateFullName`.

### Cyan covering more of the model

The paint report explicitly maps cyan paint to C, purple to CM, green to CY, brown to CMY, and orange to MY. Therefore `all_cyan` includes cyan layers throughout four of the five visible color regions, not just the freshly painted bow. Paint also extends inward from the nearest surface; it is not a thin surface decal. The pre-export and reopened-export screenshots retain the recognizable bow patch, with mixed regions converted to alternating layers.

The large cyan silhouette while the cyan part is selected is not by itself evidence of paint leaking. It must be assessed with selection cleared, all three materials visible and assigned correctly, followed by an actual filament-colored layer preview. If a changed boundary remains, compare the same camera and region against the editable project; only then investigate geometry sampling/nearest-surface behavior.

### A completed slice is not yet a correct three-material slice

Original export: part extruders C=1, M=2, Y=3. Existing Bambu filament slots: 1=yellow, 2=magenta, 3=cyan (plus two others). Correct assignment on that existing profile is C→3, M→2, Y→1. Slot numbers are not universal; do not hardcode those values into the exporter.

The recovery snapshot has all three parts set to extruder 1. The later screenshot shows distinct 3/2/1 assignments, so the snapshot and screenshot do not represent one consistent final state. More importantly, retained G-code says `filament: 1,2`, has physical tool selections T0/T1 and no T2, and includes two filament usage totals. Thus that saved slice cannot validate the final cyan-slot-3 assignment. It may be stale relative to the UI; the precise ordering is not established. Re-slice after verified assignments and retain the matching saved project/G-code summary.

Bambu's “invalid config, load geometry data only” warning also occurs for valid generic 3MF files upstream. It is not sufficient evidence of corrupt LayerLoom geometry. Do not fabricate a printer profile just to suppress it. [Upstream issue 11927](https://github.com/bambulab/BambuStudio/issues/11927) documents the message and an upstream change; availability in the user's installed version must not be assumed.

## Patch and validation order

### 1. Resolve the slicer state/assignment gate first

- Preserve the existing test project/recovery evidence. Start a separate clean Bambu document from the unchanged export, without restoring the corrupted session.
- Compare a manual pointer-only import with automation-assisted inspection. Capture names immediately after import and after opening the object tree. During the manual baseline avoid accessibility tree reads/writes. In a separate trial introduce read-only inspection, selection and assignment one at a time. Stop at the first name or assignment change. Do not repeatedly use generic setValue on custom object-tree controls.
- Compare the supported geometry-import route against Open Project. Record exact menu choices and whether the warning/part metadata persists. Use the route that actually retains aligned parts and requires the least explanation.
- Verify each part's physical filament assignment; deselect all parts; retain a saved slicer project so names and assignments can be inspected independently. Set first and normal layers to 0.16, verify adaptive layers off, then re-slice. Verify all three intended filament slots in generated output and inspect several layers, including the painted bow and mixed-color cabin.
- If only automation reproduces the mutation, change the QA procedure and record an upstream/tool issue. If a manual clean import reproduces it, reduce the exported metadata to a tiny reproducible fixture and patch `export.py`/`paint_export.py` only with an observed compatibility benefit. Retest both Bambu and the existing Orca path.
- Add a stable-name/three-part metadata regression check and a small CMY slicer fixture if export code changes. Treat a stale/mismatched slice as a failed acceptance check regardless of whether slicing completed.

Acceptance: stable names, three correctly assigned parts, all three intended filaments in the matching newly generated slice, preserved bow patch, aligned geometry and no unexplained broad recoloring.

### 2. Make the public entry path short and singular

Files: `README.md`, `docs/mac_first_open.md`, `docs/tutorial.md`, `START_HERE.html`, `packaging/macos/Read Me First.html`, `packaging/macos/RELEASE_NOTES.md`.

- Lead with one plain sentence about producing more print colors, followed immediately by **Download for Mac**. Offer Apple Silicon as the primary choice and Intel as the clearly labeled alternative, with minimum macOS versions beside them. Put source/developer installation behind a separate link; keep Windows/Linux instructions available lower down.
- Default path: download the DMG → drag LayerLoom to Applications → open it → Try the example → Export → Show in Finder. Use the exact shipped button labels. No GitHub account, Git, Python or Terminal should be introduced on this path.
- Keep graphical first-open approval visible in a short expandable “Mac blocked the app?” section outside the app. Show the observed warning and the Settings route; move signing implementation details and troubleshooting history out of the first-run steps.
- Keep a one-line definition of 3MF (printable model), editable paint project, and slicer near the step that needs it. Put sampling, memory, recipes and precision under advanced help. Explain in slicer help that selecting a filament part includes its contribution to mixed colors.
- End the first-run guide at a successful saved example; offer painting and slicing as the next two optional steps. The slicer guide must use the route verified in step 1, including correct physical filament assignment and both layer heights.
- Check public/default-branch docs, not just this working branch. Direct download links must resolve for an anonymous user to the exact tested asset. Keep patch history in changelog/validation material, not the download instructions.

The DMG is currently a draft. Do not replace the public source option with a dead/private link. Stage the new README and release text now; switch the public default together with an approved public prerelease after the remaining artifact gates pass. Publication still needs the user's explicit instruction. A new account using a manually supplied draft DMG tests installation, but does not test public discovery/download.

### 3. Small app and installer polish, without redesigning the workflow

- `3mf_gui_v65.py`, `paint_gui.py`, `paint_main_ui.py`: use a consistent LayerLoom title and correct Arrange/Paint mode identity. Remove Color Assigner/v65/main UI prototype from user-facing titles. Keep the old build plate and Arrange default; keep Paint secondary. Standardize the primary export action so the label does not unexpectedly change after loading an example.
- `paint_gui.py`: explain an export-generated mapping change if it marks the editable project dirty, or avoid dirtying the document when no persisted mapping changed. Preserve actual paint edits and save protection.
- `scripts/build_macos.py`, bundle specs/assets: provide a recognizable app icon and obvious copy-to-Applications cue. Move the loose approval illustration into a support subfolder or embed it in the HTML so the root has app, Applications and one short Read Me. Keep useful approval help available online if a default-browser launch fails.
- Check loading feedback with normal pointer use before modifying rendering code. The blank/stale frames may be capture timing; add a simple loading indication only if ordinary users can encounter an unexplained wait. Do not rewrite the rendering stack for an unconfirmed symptom.

Acceptance: a novice can identify the app, destination, current mode and next action without understanding development terms. Existing cube and Benchy workflows still pass.

### 4. One final candidate and the fresh-account test

Build a new numbered candidate after the small changes; do not overwrite a5 or reuse its checksums as proof. Run affected metadata/UI tests and existing Mac package/worker CI, build both architectures, then verify the actual arm64 download on this Mac. Retain Intel CI as separate evidence; do not claim an Intel human trial.

For the planned new macOS account, use the final short instructions and one exact artifact. Record discovery/download access, graphical approval, initial Arrange plate, cubes export/reopen and painted Benchy save/export. Test the slicer handoff with the clean per-part assignment procedure. A second run with networking unavailable should confirm the installed app can load the example and export; agree on the disconnection timing first.

Keep new work bounded: reuse the existing evidence folder, keep one candidate app and one DMG, and preserve the current model outputs. Do not start another broad audit, compatibility expansion, automatic-update project, or native Windows packaging effort before this release.

## Definition of done for this round

One accessible tested Mac download; a short matching default README; graphical first-open instructions; Arrange default; working example and painted Benchy; stable slicer names and verified three-material slice; fresh-account and offline results recorded. Publish only after the user approves. Remaining unsupported versions/platforms stay explicit limitations.
