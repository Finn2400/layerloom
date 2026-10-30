# LayerLoom 0.6.4a6 — Mac early access

Make more apparent print colors from a few filaments. This app includes Python
and its graphics libraries; no developer tools or Terminal setup are needed.

## Download

- **Apple Silicon (M-series), macOS 14+:** [Download Mac app](https://github.com/Finn2400/layerloom/releases/download/v0.6.4a6/LayerLoom-0.6.4a6-macos-arm64.dmg)
- **Intel, macOS 15+:** [Download Mac app](https://github.com/Finn2400/layerloom/releases/download/v0.6.4a6/LayerLoom-0.6.4a6-macos-x86_64.dmg)

Check **Apple menu → About This Mac** if unsure. You only need the matching DMG.

## Your first model

1. Open the DMG. Drag **LayerLoom** to **Applications**, then open the copied app.
2. If macOS blocks it, dismiss the alert and choose **System Settings → Privacy & Security → Open Anyway**. Confirm when prompted. [Illustrated help](https://github.com/Finn2400/layerloom/blob/main/docs/mac_first_open.md).
3. Click **Try the example**, then **Export geometry 3MF…**. Keep **Woven apparent colors** and **0.16 mm**, choose a save location, and wait for **Export complete**.
4. Click **Show in Finder**. [Follow the slicer walkthrough](https://github.com/Finn2400/layerloom/blob/main/docs/tutorial.md#open-the-3mf-in-a-slicer) before printing.

No printer is needed to try the example. Turn on **Paint** to edit surfaces;
**Save paint…** keeps an editable project. The original build plate and Arrange
mode remain the default. **Help → Load the Benchy example** offers a larger model.

## Need help?

Use **Help → Copy diagnostics** and **Help → Open logs**.
These free builds are not Apple-notarized and need graphical approval; managed
Macs may prohibit it. Do not bypass a damaged/malware warning. This is alpha
software: inspect geometry, part-to-filament assignments and layers before printing.

Windows/Linux: download **LayerLoom-0.6.4a6-source.zip**, extract it and open
**START_HERE.html**. First source setup needs internet. GitHub's generic Source
code downloads are not Mac apps.

Checksums, dependency notices, build identity and **VALIDATION.txt** are supplied
for verification. Validation lists exact tested artifacts and remaining limits;
it is not a guarantee for every printer or model.
