# Paint integration alpha: 0.6.4a3

**Source update after the existing a3 build:** startup now opens Arrange with
the pixelated Benchy design on the empty build plate. Examples keep the current
mode; turn on Paint in the toolbar for surface editing. The source security
illustration now ends at “Try the example”. Existing a3 DMGs are unchanged and
still open in Paint. These source changes need a new build and its own validation.

This unreleased candidate combines the standalone installer with the shared Paint/Arrange workspace. Editable paint is saved in `.llpaint`; printable 3MF output contains filament geometry and part assignments. Conversion supports cancellation and an estimated additional-memory budget.

This candidate repairs two problems found while demonstrating the bundled
Benchy: slicer display names could hide its original recipe tags, and an
equivalent CMY recipe spelling could fail to match the palette. The eight-part
example now retains its intended CMY colors and four woven recipes, including
through editable-project save/reopen. The frozen smoke test now checks those
actual Benchy assignments after the existing cubes/Paint export and reopen proof.

The candidate also corrects a precision mismatch in the overlap measurement.
The audit now measures final partition coordinates directly instead of snapping
their union a second time; material construction and accepted tolerances remain
unchanged. Frozen-test diagnostics record stage timing, UI responsiveness,
progress and renderer information, with bounded export and process deadlines.

It also retains 0.16 mm for both bundled tutorials, keeps that choice in
saved projects, and gives the larger Benchy a 4,096 MiB estimated export budget.
The active budget is visible in the main export summary. It also fixes selection
timers during scene/window changes and bundles decorative plate artwork so that
startup does not search Downloads for that artwork.

**Use the validation record for this exact candidate.** The draft's attached
`VALIDATION.txt` identifies the source commit, workflow runs, artifact checksums
and completed or outstanding manual checks. Every candidate needs newly built
source distributions and both architecture DMGs; do not relabel earlier builds.

For historical context, [0.6.4a2 source CI](https://github.com/Finn2400/layerloom/actions/runs/40920695537)
passed all five jobs. Its [Mac baseline workflow](https://github.com/Finn2400/layerloom/actions/runs/40920767769)
passed ARM macOS 14/15/26 and Intel 15; Intel 26 passed one unchanged retry after
an initial 180-second tutorial-export timeout. Those results cover the tested
cubes/Paint path, not the newly repaired Benchy import or new 0.6.4a3 bytes.
Complex-model results, timings and any unresolved defects belong in that
candidate's validation record. Passing a small example does not establish every
model's export or physical printing behavior.

This is an **unpublished draft** for maintainer testing. Draft assets are visible only to maintainers with repository write access. Browser-quarantine, Finder first-open, slicer, and first-time-user checks remain publication requirements; automated build results do not establish that they have passed.

## Choose the Mac app

| Your Mac, shown in Apple menu → About This Mac | Download | Minimum macOS |
| --- | --- | --- |
| Apple M-series chip | `LayerLoom-0.6.4a3-macos-arm64.dmg` | 14 Sonoma |
| Intel processor | `LayerLoom-0.6.4a3-macos-x86_64.dmg` | 15 Sequoia |

Use the **0.6.4a3** DMG matching your Mac and confirm that version in
**Help → Copy diagnostics**. Earlier candidates do not contain the Benchy fixes. The app contains Python and the required graphics libraries. GitHub's **Source code (zip)** asset contains the source installation, not a Mac app.

1. Open the DMG and drag **LayerLoom.app** to Applications.
2. Open the copied app. If macOS blocks it, follow the attached **Read-Me-First.html**: **System Settings → Privacy & Security**, find LayerLoom under Security, and use **Open Anyway**. Confirm any further prompt or password request. See [Apple's instructions](https://support.apple.com/en-us/102445).
3. Choose **Try the example → Export geometry 3MF…**. The tutorial selects **Woven apparent colors** and `0.16 mm`; check those values and confirm. Choose a `.3mf` filename in Documents and wait for completion, then use **Show in Finder**. First export includes geometry-search compilation and may take longer.
4. Open the exported `.3mf` in your slicer, keep its parts aligned, and use 0.16 mm for normal and first layers with adaptive layers disabled. Save `.llpaint` separately when you want to retain editable paint.

For a Paint demonstration, turn on **Paint** in the main toolbar, select a cube surface, choose a color, click **Apply paint**, then try Undo and Redo. Use **Save paint…** to save `.llpaint` and **Open…** to reopen it. Export a separate printable 3MF; editable paint stays in the project. The older 0.6.3 installer does not include this workspace.

These free builds are ad-hoc signed and are not notarized by Apple. A managed Mac may prohibit approval. After first-open approval, the app works offline. Use **Help → Copy diagnostics** and **Help → Open logs** if something fails.

## Source launchers

Windows x64 and Linux x64 use the source launchers. The source route is also available on Mac. Extract the complete ZIP and open the matching **Launch LayerLoom** file; it installs a private pinned Python and locked packages. First setup needs internet. Use the matching 0.6.4a3 source archive and its `docs/install.md` for extraction,
Mac script fallback, repair, and graphics requirements. The draft's source guide
links to its recorded commit; the default branch may contain an older build.

## Maintainer publication gate

Keep this release a draft until the attached checklist is complete. When
assembling the private preview after committing, include the exact source commit
and a commit-pinned link to `docs/install.md`; do not link testers to `main`. Record the exact commit and workflow runs, inspect both architecture validation reports and screenshots, and complete downloaded-DMG quarantine, Finder launch, offline reopening, app replacement, slicer, and novice tests. Review the architecture-specific dependency notices and corresponding-source obligations.

GitHub's macOS 14 runner retires November 2, 2026. The ARM build retains its
macOS 14 target; testing the exact new artifact on a local macOS 14 Mac remains
a publication gate after hosted coverage ends. A passing macOS 15/26 run does
not substitute for this evidence.

Attach both DMGs and checksums, source and wheel distributions, notices for both architectures, build-info files, the first-run HTML and SVG, the checklist, and `VALIDATION.txt`. Keep private complex-model inputs and screenshots out of public release assets; report only appropriate aggregate results. Before publication, replace this draft-status notice and the README/install-guide draft notices with the validated download link and actual test evidence. Do not claim untested operating systems or publish automatically.
