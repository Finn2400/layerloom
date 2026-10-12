This is an **unpublished draft** for maintainer testing. Draft assets are visible only to maintainers with repository write access. Browser-quarantine, Finder first-open, slicer, and first-time-user checks remain publication requirements; automated build results do not establish that they have passed.

## Choose the Mac app

| Your Mac, shown in Apple menu → About This Mac | Download | Minimum macOS |
| --- | --- | --- |
| Apple M-series chip | `LayerLoom-<version>-macos-arm64.dmg` | 14 Sonoma |
| Intel processor | `LayerLoom-<version>-macos-x86_64.dmg` | 15 Sequoia |

Use the attached versioned DMG matching your Mac. The app contains Python and the required graphics libraries. GitHub's **Source code (zip)** asset contains the source installation, not a Mac app.

1. Open the DMG and drag **LayerLoom.app** to Applications.
2. Open the copied app. If macOS blocks it, follow the attached **Read-Me-First.html**: **System Settings → Privacy & Security**, find LayerLoom under Security, and use **Open Anyway**. Confirm any further prompt or password request. See [Apple's instructions](https://support.apple.com/en-us/102445).
3. Choose **Try the example → Weave**, select Documents or another output folder, and wait for **Your woven model**.
4. Click **Show in Finder**. Open `tutorial_cmy_cubes_woven.3mf` in your slicer, keep its parts aligned, and use 0.16 mm for normal and first layers with adaptive layers disabled.

These free builds are ad-hoc signed and are not notarized by Apple. A managed Mac may prohibit approval. After first-open approval, the app works offline. Use **Help → Copy diagnostics** and **Help → Open logs** if something fails.

## Source launchers

Windows x64 and Linux x64 use the source launchers. The source route is also available on Mac. Extract the complete ZIP and open the matching **Launch LayerLoom** file; it installs a private pinned Python and locked packages. First setup needs internet. See the [installation guide](https://github.com/Finn2400/layerloom/blob/main/docs/install.md) for extraction, Mac script fallback, repair, and graphics requirements.

## Maintainer publication gate

Keep this release a draft until the attached checklist is complete. Record the exact commit and workflow runs, inspect both architecture validation reports and screenshots, and complete downloaded-DMG quarantine, Finder launch, offline reopening, app replacement, slicer, and novice tests. Review the architecture-specific dependency notices and corresponding-source obligations.

Attach both DMGs and checksums, source and wheel distributions, notices for both architectures, build-info files, the first-run HTML and SVG, and the checklist. Before publication, replace this draft-status notice and the README/install-guide draft notices with the validated download link and actual test evidence. Do not claim untested operating systems or publish automatically.
