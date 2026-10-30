# LayerLoom: fresh-session Mac acceptance test

Prepared 30 October 2026. This is a controlled GUI usability rehearsal, not proof that every feature or every Mac works. Do not treat earlier passes as passes in this run.

## Goal and boundaries

Act as a careful observer of a beginner trying to install LayerLoom, run its example, paint a larger model, and inspect the result in a slicer. Assume the beginner has never used GitHub, Python, Terminal, 3MF, GLB, or a slicer. Record what they would need to guess or look up. You are simulating that perspective; do not claim to be an unfamiliar human participant.

Use the already downloaded, quarantined installer. Carry out the installation and app workflow through Finder and the GUI. Read-only shell checks for artifact identity, process paths, storage, or output integrity are allowed as observer instrumentation, but must not repair or replace a failed user step. Do not rebuild, install developer tools, resolve dependencies, clone repositories, edit the app, publish releases, or send anything to a printer. Do not start more agents or chats.

The release is a private draft. A friend without repository permissions cannot use its draft download page. Record this distribution limitation; do not publish it to make the test work.

## Bounded workspace

Use only these destinations for new test work:

- Workspace: `/Users/finn/LayerLoom Fresh User Test`
- Install: `/Users/finn/LayerLoom Fresh User Test/Applications/LayerLoom.app`
- User-created models: `/Users/finn/LayerLoom Fresh User Test/Outputs`
- Notes, screenshots, logs: `/Users/finn/LayerLoom Fresh User Test/Evidence`
- Run identity: `/Users/finn/LayerLoom Fresh User Test/Evidence/run.json`
- Final report: `/Users/finn/LayerLoom Fresh User Test/Evidence/REPORT.md`
- Chronological observations: `/Users/finn/LayerLoom Fresh User Test/Evidence/observations.md`

The `.layerloom-test-workspace` marker identifies this disposable test area. Applications and Outputs are intentionally empty. Keep one installed test app. Do not overwrite `/Applications/LayerLoom.app`, the original repository, another worktree, existing user exports, or existing application settings. If a destination is already occupied, inspect the notes and ask before replacing it. Do not use a different copy just because this one cannot launch.

Keep the workspace below **2 GiB** and require **5 GiB free disk space** before installation or a large export. Check usage between major stages. Do not duplicate the DMG, download CI archives, retain repeated full app copies, or make unbounded screen recordings. App startup may write normal logs/settings outside this folder; record those locations and do not erase shared settings to simulate a fresh account.

## Artifact identity

Installer (already downloaded by the user through Chrome):

`/Users/finn/Downloads/LayerLoom-0.6.4a5-macos-arm64.dmg`

- Bytes: `316312533`
- SHA-256: `fef6eed441b9233df92b2319d395de65e02810f84c4d5d7993142a5139d67056`
- Recorded quarantine: `0081;6ae4a1f9;Chrome;11DA5FBC-3C0D-47D0-8559-E5B1B91B7D98`
- Expected version/architecture: `0.6.4a5`, `arm64`, minimum macOS 14.
- Expected embedded source revision: `6935fcdc497e429d76b1d591689dbae9d7a41743`.
- Expected build workflow: `40982690862`.
- Draft page: https://github.com/Finn2400/layerloom/releases/tag/untagged-fc771da6f1a2600af009
- Public entry: https://github.com/Finn2400/layerloom

Verify SHA-256, size, quarantine, OS, architecture, and free space before opening. Record actual results, not only expected values. Verify bundle version, executable path, and embedded build metadata after copying. The draft tag target, embedded build revision, and current source branch differ; the version string alone is insufficient evidence. Stop on a hash mismatch.

The browser download is a **user-completed step**, not a fresh agent-observed download. Review the public instructions if browser permission allows; do not redownload unnecessarily. If access is denied, record the exact blocked step and continue with the authorized local artifact. Do not bypass a website denial using Claude, another browser, curl, an API, raw browser debugging, or a second account.

## How to operate and report

1. Initialize the supported computer-use tool in this fresh session and read its returned documentation. Use observed UI state, not coordinates remembered from another session. There are multiple screens.
2. Use the supported GUI tool for app/browser interaction. Do not inject AppleScript or low-level mouse events as a workaround. Refresh the screenshot/state after a mode change, dialog, or window move.
3. If two sensible attempts fail on one control, stop repeating. Capture evidence, distinguish automation failure from application failure, and ask for one narrowly described manual action if needed. Record the exact human help and its result. Do not count an unobserved action as passed.
4. Do not repeatedly enumerate file-dialog sidebars or cloud locations. Use the dedicated local Outputs folder. If unexpected Desktop, Downloads, Google Drive, or OneDrive permissions appear, capture the app name, exact text, triggering action, and whether automation had inspected the dialog. Decline or leave pending; do not grant broad access. Test only the specific local file the user selected.
5. Never remove quarantine, disable Gatekeeper, reset macOS privacy permissions, grant Full Disk Access, or use Terminal to bypass first-open security. Use documented graphical approval; involve the user when macOS requires them. Stop on a damaged/malware warning and report exact wording.
6. Keep progress updates concise. Log observations as they happen. Prefer cropped app screenshots without personal file lists, account history, or unrelated windows. Evidence remains local unless the user asks to share it.
7. Do not fix discovered UI/docs defects during this run. Record reproducible issues and continue independent checks. A workaround is an assisted pass, not a clean novice success.
8. Separate observations from hypotheses. Do not claim 100% reliability, Intel/Windows coverage, physical print success, offline success, or first-time-user approval from this single Mac rehearsal.

## Test sequence

### 1. Instructions and installation

Before reading the historical findings below, review what a new user sees on the public GitHub page and the downloaded DMG. Record where the first installation instruction is, how architecture selection is explained, whether the download is actually accessible, and every unfamiliar term or ambiguous button. Time the steps; do not silently fill in missing instructions from developer knowledge.

Check that no old LayerLoom process or old mounted LayerLoom image will confuse the trial. Do not close unrelated work. Open the retained DMG through Finder. Read its included first-run document. Copy LayerLoom.app using Finder to the dedicated Applications directory, then launch **that copied app**, not the app inside the disk image. Explain in the report that this isolation directory is a deliberate deviation from normal `/Applications` installation.

Capture the initial DMG window, copy instruction, first-open prompt and final running app. If there is no security prompt, record “not observed on this existing account”; prior approvals may persist. This account is not a pristine macOS account. Do not reset consent databases to force a prompt.

Confirm there is no Python/Tkinter/Homebrew/Git/Node/Terminal setup. Record any dependency prompt or crash. Verify the running process belongs to the test directory. Inspect application logs only after recording the visible symptom. Do not start the app from its executable as a substitute for Finder first-open.

### 2. First successful export

Confirm the default is **Arrange**, with the original pixelated Benchy build plate and Paint available as a secondary toggle. Capture the first window before loading anything.

Use “Try the example.” Confirm the colored cubes visibly render. Follow the actual UI/documentation to export; record a mismatch if instructions say “Weave” but the build says “Export geometry 3MF…”. Use the documented 0.16 mm layer height. Record all export settings and timing. Save as `Outputs/cubes.3mf`, not inside the application or mounted disk image.

Check progress, completion wording, filename, persistent success panel, and Show in Finder. Success should not look like a warning. Reopen the exported file and visually confirm the colored material parts. Record filename, size and SHA-256. Cancel one Save dialog and check that no output was accidentally created. Do not overwrite the successful export during that check.

### 3. Larger model and painting

Use the bundled Benchy example through the GUI. This is mandatory; cubes alone are insufficient. Record model dimensions, part/face counts if shown, render responsiveness, and memory estimate. Avoid introducing unrelated downloaded models.

Toggle Paint with a direct observed pointer click. Select a recognizable hull patch, apply a visibly different color, and capture before/after. Selection highlight alone is **not evidence that paint was applied**. Check Undo, Redo, and switching back to Arrange without losing paint. Save `Outputs/painted-benchy.llpaint` (also test extension auto-addition by omitting the extension in the chooser), close/reopen the project, and confirm the actual painted color persists.

Export `Outputs/painted-benchy.3mf` using 0.16 mm layers and the displayed default XY sampling (previously 0.5 mm). Record precision and budget exactly; do not raise resource limits just to force a pass. Reopen the export. Inspect shape, hull patch, aligned material parts, and obvious missing geometry. Keep the editable project and exported geometry as different files. If export marks the editable project modified, record why the UI says so and save a separate mapped copy if needed.

For output validation, record ZIP integrity and model/material metadata where available. Do not install extra Python packages just for mesh checks. Prior mesh-validation results are background evidence, not validation of the newly generated file. If no suitable existing validator is available, mark mesh closure/winding validation not run.

### 4. Independent slicer handoff

Use the installed Bambu Studio only for local import and preview. Preserve unrelated unsaved user work. Import the new Benchy using documented UI. Capture any warning verbatim, including whether opening as a project differs from importing geometry. A successful import must visibly contain the model; a dismissed warning alone is not sufficient.

Keep all material parts aligned as one assembly. Verify cyan/magenta/yellow assignments rather than assuming the slicer retained them. Check both initial and subsequent layer heights are 0.16 mm and adaptive layers are off. Do not automatically rotate or independently arrange the parts. Record the selected printer/process profile and any locally required adjustments. Slice locally if possible, inspect multiple layers and material transitions, and save a screenshot. **Never Print, Send, upload a job, or start a printer.** If a suitable profile is missing, record the blocker rather than inventing one.

### 5. Reopening, relocation, offline scope

Quit only the isolated test app, eject the installer, and reopen the copied app through Finder. Confirm the saved paint project and example still work without the mounted image.

If the workspace remains within budget, move (do not duplicate) the app via Finder into `Applications/Relocated café/LayerLoom.app`, then reopen and test the example. Record the exact final app path. Restore to the original test location if that simplifies handoff.

Physical network disconnection needs separate user agreement because it can interrupt other work or remote access. If not agreed, leave offline testing explicitly pending. Absence of observed network traffic is not an offline test. Do not replace this with a claim that bundled Python proves offline operation.

### 6. Final usability review and evidence

Comment on installation clarity, DMG layout/icon, visual hierarchy, startup titles, default mode, discoverability, technical vocabulary, text density, feedback, save destination, confidence in success, and slicer handoff. Rate each as clear / needs explanation / blocks a novice, with one screenshot or concrete example. Identify what a beginner would search for (e.g. “What is a 3MF?”) and whether the supplied help answers it. Keep recommendations separate from observed defects.

For each check use PASS, ASSISTED PASS, FAIL, BLOCKED, or NOT RUN. Every claim needs an evidence filename or a timestamped observation. Include actual output hashes/settings, exact warnings, manual help, failed automation attempts, and limitations. Capture only a small set of relevant screenshots; do not collect unrelated personal content.

Complete `Evidence/REPORT.md` and `Evidence/run.json`. List issues by severity with reproduction steps, expected/actual behavior, evidence and whether the cause is confirmed. State separately: application defect, documentation/usability issue, automation limitation, or environmental limitation. Give a clear recommendation for the friend's demo, including outstanding blockers. Leave the app, outputs and evidence in the bounded workspace for review; do not create another installer copy or publish anything.

## Historical context — consult after recording first impressions

An earlier unquarantined-copy rehearsal on this Mac passed cube export and painted-Benchy export/reopening. It did not satisfy browser quarantine, first-open, offline, or full slicing gates. Benchy had 253,176 input faces; the earlier export had about 1.43 million faces and was about 14 MB. These are reference magnitudes, not exact requirements.

Bambu File → Open Project previously said “The 3mf file has invalid config, load geometry data only.” User-assisted continuation imported the geometry, but filament assignment and slicing were not completed. Initial layer height remained 0.20 mm despite normal layers being 0.16 mm. Do not call the geometry corrupt based solely on that notice.

Earlier automation could toggle the Paint action's checkmark through accessibility without switching the view; direct pointer activation worked. Finder automation later timed out repeatedly in the old session. Do not attribute either to the new app without a controlled observation.

Shared app logs may be under `~/Library/Logs/LayerLoom`. Copy only the relevant time interval or relevant log files, within the evidence budget. Historical reports remain in the repository's `docs/validation/fresh-user-2026-10-30.md`; read them after your independent impressions. Older artifact paths mentioned in those reports may have been removed during documented cleanup; compact evidence was preserved.
