# Windows standalone app

**[Download Windows setup](https://github.com/Finn2400/layerloom/releases/download/windows-0.6.4a7-preview1/LayerLoom-0.6.4a7-windows-x64-setup.exe)**
for a 64-bit Intel/AMD PC. This is an early-access Windows preview.
[Release details and checksums](https://github.com/Finn2400/layerloom/releases/tag/windows-0.6.4a7-preview1).

To try it:

1. Download the **Windows setup** file linked above. You only need that `.exe` file.
2. Open it, choose **Install**, then **Finish**. It installs for your account;
   no administrator password, Python, Git, or Terminal setup is needed.
3. Click **Load example**, then **Weave**. Keep **Woven apparent colors** and
   **0.16 mm**, and choose where to save.
4. Wait for **Export complete**; the Benchy can take several minutes to weave.
   Click **Show in folder**. [Open the 3MF in your slicer](tutorial.md#open-the-3mf-in-a-slicer).

Later, find **LayerLoom** in the Start menu. The installed app works offline.
Paint is optional; **Back to build plate** returns to Arrange and lets you keep
or discard your changes.

## If Windows asks whether you trust it

This free installer is unsigned. For **Windows protected your PC**, verify you
downloaded from `github.com/Finn2400/layerloom`, then choose **More info → Run
anyway**, if offered. If Windows reports malware or organizational policy blocks
the app, stop and report the exact warning. Do not disable antivirus or Windows
security. Some managed computers will not permit this installation.

## Portable option

If you choose the portable ZIP, right-click it and choose **Extract All**. Open
the extracted **LayerLoom** folder and double-click **LayerLoom.exe**. Keep the
entire folder together, including `_internal` and `worker`. Do not run from inside
the ZIP. Setup is the recommended beginner path.

## Support and compatibility

This build targets 64-bit Intel/AMD PCs, Windows 10 build 19041 or newer, with a
working OpenGL 3.2 graphics driver. Windows 11 is the intended current desktop
target. Windows on ARM and 32-bit Windows are not validated.

Hosted Windows Server 2022/2025 runners exercise installation, repair, uninstall,
frozen imports/weaving and GUI workflows with a software graphics driver. These
checks do not replace a physical Windows 10/11 PC, browser-download/SmartScreen
trial, or a novice's first-use test. Their results must be recorded separately.

Choose **Help → Diagnostic logging → Start diagnostic logging**, reproduce a
problem, then stop and copy the summary. Nothing is uploaded automatically.
Logs are stored in `%LOCALAPPDATA%\LayerLoom\Logs`. Uninstall using Windows
**Settings → Apps**; saved model files are kept.

## Build and validation

The **Windows desktop** GitHub Actions workflow builds from `uv.lock` with the
pinned Python, using PyInstaller directory bundles and Inno Setup 6. GUI and worker
each contain their own Python and native libraries. Optional PyMeshLab is omitted.
The artifact includes an installer, portable ZIP, SHA-256 checksums and build
identity; both packages include the examples and third-party notices. Native
dependency checks include delayed DLL imports, so a preinstalled developer runtime
cannot hide a missing Visual C++ DLL. The unused optional Numba TBB backend is
excluded; the locked runtime's normal CPU backend remains available.

For a local build on Windows with Inno Setup 6 installed:

```powershell
uv sync --managed-python --locked --extra gui --extra desktop --no-editable
.venv\Scripts\python.exe scripts\build_windows.py
.venv\Scripts\python.exe scripts\validate_windows.py dist\windows --gui --report validation-windows
```

The validator installs into a temporary folder, repairs a deliberately missing
worker, tests both packaged forms with no Python on PATH, and uninstalls. Run it
only in a disposable test account: it intentionally creates/removes the LayerLoom
Start-menu shortcut and per-user uninstall registration.
