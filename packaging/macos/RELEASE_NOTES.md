# LayerLoom 0.6.4a4 for Mac

**Unpublished test candidate.** Draft downloads require repository write access.
The app includes Python and its graphics libraries; no Python, Git, Homebrew,
Node or Terminal setup is needed.

## Download the app

Check **Apple menu → About This Mac**, then choose one DMG under Assets:

| Your Mac | Download | Minimum macOS |
| --- | --- | --- |
| Apple M-series chip | **LayerLoom-0.6.4a4-macos-arm64.dmg** | 14 Sonoma |
| Intel processor | **LayerLoom-0.6.4a4-macos-x86_64.dmg** | 15 Sequoia |

You only need the matching DMG to run the app. Checksums, notices and source
packages are supplementary files.

## Open and try the example

1. Open the DMG and drag **LayerLoom.app** to **Applications**. Open the copied app in Finder.
2. If macOS blocks it, use **System Settings → Privacy & Security → Open Anyway** and confirm. The attached **Read-Me-First.html** includes illustrations. See [Apple's procedure](https://support.apple.com/en-us/102445). These free builds are ad-hoc signed without notarization; managed Macs may prohibit approval.
3. Allow roughly half a minute for first startup. LayerLoom opens in **Arrange**, showing the build plate with a pixelated Benchy design. Click **Try the example** to load the CMY cubes.
4. Click **Export geometry 3MF…**. Keep **Woven apparent colors** and **0.16 mm**, then choose a `.3mf` filename in Documents. Wait for completion and use **Show in Finder**. The first export may take longer while its geometry search compiles.
5. Open the output in your slicer as one model with aligned parts. Match the C/M/Y parts to filaments. Use **0.16 mm for first and normal layers**, with adaptive layers off.

## Try Paint

Turn on **Paint** in the toolbar. Choose **Surface select**, click a cube surface,
choose a color and click **Apply paint**. Try Undo/Redo. Use **Save paint…** to
save an editable `.llpaint` project and **Open…** to reopen it. Export a separate
printable 3MF. Turn Paint off to return to Arrange; your model and edits remain.

For the larger example, choose **Help → Load the Benchy example**. Loading an
example keeps the current mode. In the tested Bambu Classic slicer profile,
enable **Detect thin walls** to retain its narrow top cap; inspect narrow tips
in preview. Other models and profiles need their own checks.

Use **Help → Copy diagnostics** or **Help → Open logs** if something fails.
These instructions accompany **0.6.4a4**. After first-open approval, bundled
examples and processing work offline.

## Testing and other downloads

**VALIDATION.txt** records the exact source commit, installer checksums, test
results and remaining acceptance checks for this candidate. Keep this release
unpublished until the required first-open and novice tests are complete. No
physical-print or universal compatibility claim is made.

Windows/Linux and source users should extract **LayerLoom-v0.6.4a4-source.zip**
and follow its `docs/install.md`. First source setup needs internet. GitHub's
generic Source code ZIP is not a Mac application.

For maintainers: attach the exact-commit source guide, both architecture notices
and build-info files, the checksum manifest, first-run instructions and release
checklist. Preserve earlier candidates and their evidence. A new artifact needs
its own validation; macOS 14 requires exact-artifact local testing after hosted
runner retirement. Do not publish automatically or include private models.
