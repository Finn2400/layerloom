# Installing LayerLoom

The source launcher installs its own Python. Standalone Mac builds are also
available to build and test; they become the default download only after the
[release checks](release_and_zenodo.md) pass. No paid account is required.

## Standalone Mac builds

When a validated desktop release is available, download its **DMG asset**:

| Your Mac | Download | Minimum system |
| --- | --- | --- |
| About This Mac says **Chip: Apple M…** | `LayerLoom-<version>-macos-arm64.dmg` | macOS 14 |
| About This Mac says **Processor: Intel…** | `LayerLoom-<version>-macos-x86_64.dmg` | macOS 15 |

GitHub's automatic **Source code (zip)** download is not the desktop app.
Open the DMG and drag **LayerLoom.app** to Applications, or another folder you
can write to. Open the copied app. Python and required libraries are included;
no package downloads are needed. Updates are manual: quit LayerLoom and replace
the app. Keep your saved models in Documents or another folder outside the app.

These builds use free ad-hoc signing and are **not notarized by Apple**. macOS
may require approval for that specific download. Follow the illustrated
[Mac first-open guide](mac_first_open.md). A managed Mac may prohibit exceptions.

Click **Try the example → Weave**, choose an output folder, then **Show in Finder**
when the result is ready. This entire workflow works offline.

## Download and run the source

1. On the [LayerLoom repository](https://github.com/Finn2400/layerloom), choose
   **Code → Download ZIP**.
2. Extract the ZIP completely; do not run files from an archive preview.
3. Run the launcher below. Keep the extracted folder for future launches.

The launcher downloads a pinned `uv` executable, verifies its checksum, and
obtains a pinned Python 3.12 with locked GUI dependencies. You do not need
Python, Git, Node, or Homebrew first. Existing system Python, Conda, and `.venv`
folders are ignored. First setup needs internet and can take several minutes.

### macOS

Double-click **Launch LayerLoom.command**. Terminal shows setup progress, then
LayerLoom opens. Normally you do not need to type commands.

If macOS blocks the downloaded launcher, try **System Settings → Privacy &
Security → Open Anyway** after attempting to open it. If its executable
permission was lost during extraction, use this source-only fallback:

1. Open **Terminal** from Applications → Utilities.
2. Type `bash` followed by a space.
3. Drag **Launch LayerLoom.command** from Finder into Terminal.
4. Press Return.

The standalone `.app` does not need this script-permission workaround.

### Windows x64

Extract the ZIP and double-click **Launch LayerLoom.bat**. A setup window stays
visible during installation. The launcher uses a process-scoped PowerShell
policy; it does not change your account's execution policy or require
virtual-environment activation. Windows ARM is not a tested bootstrap target.

### Linux x64

Use a desktop session with working graphics drivers. Open a terminal in the
extracted folder and run:

```bash
bash "Launch LayerLoom.sh"
```

Python is managed automatically. Linux still requires the distribution's
Qt/X11 and OpenGL/EGL runtime libraries; a launcher cannot provide a display
server or graphics driver. A headless SSH shell is not a desktop installation.
Report your distribution and setup log if a native library fails to load.
Linux ARM and standalone Linux packages are outside this release's tested scope.

## Repair a source installation

Close LayerLoom first. From the extracted source folder, use:

```bash
# macOS
bash "Launch LayerLoom.command" --repair
# Linux
bash "Launch LayerLoom.sh" --repair
```

On Windows, open Command Prompt in that folder and run:

```bat
"Launch LayerLoom.bat" --repair
```

Repair creates a fresh managed environment. It never deletes your source,
models, exports, previous environments, or repository `.venv`. Moving the source
folder creates another environment because Python environments are tied to
their installation location. Reopening the same ready installation works offline.

Setup logs are in the `logs` subfolder of:

- macOS: `~/Library/Application Support/LayerLoom`
- Windows: `%LOCALAPPDATA%\LayerLoom`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/layerloom`

Mac GUI logs are in `~/Library/Logs/LayerLoom`. Use **Help → Copy diagnostics**
and **Help → Open logs** for support. For a damaged standalone app, download it
again instead of installing Python packages into the app.

## Developer installation

For contributors who already have [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
git clone https://github.com/Finn2400/layerloom.git
cd layerloom
uv sync --managed-python --locked --extra gui --extra dev
uv run --no-sync layerloom-doctor --render
uv run --no-sync layerloom-gui
```

The checked-in `.python-version` selects the exact runtime. Use
`uv run --no-sync` for other installed LayerLoom commands; no activation is needed.
Dependency updates are explicit changes to the pins and lock followed by the
installation/desktop test matrix. Beginner launchers never update the lock.

## Slicing your result

Open the woven output as a multi-object model and match the `all_…` objects to
loaded filaments. Match slicer layer height to the LayerLoom weave height; the
first layer must be an integer multiple. See the [tutorial](tutorial.md) and
[slicer compatibility notes](slicer_compatibility.md).
