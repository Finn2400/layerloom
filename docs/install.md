# Installing LayerLoom

**You do not need to install Python, Git, Node, or Homebrew to use the source
launchers.** They install a private Python and the required packages for you.
LayerLoom and its installation tools are free.

**Mac users:** start with the [standalone app and four-step quick start](../README.md#download-for-mac).
It includes Python and its libraries. The source launchers below are intended
for Windows/Linux users and developers.

## Choose your installation

| Computer | Recommended installation |
| --- | --- |
| Mac with an Apple M-series chip, macOS 14+ | [Apple Silicon DMG](https://github.com/Finn2400/layerloom/releases/download/v0.6.4a6/LayerLoom-0.6.4a6-macos-arm64.dmg) |
| Mac with an Intel processor, macOS 15+ | [Intel DMG](https://github.com/Finn2400/layerloom/releases/download/v0.6.4a6/LayerLoom-0.6.4a6-macos-x86_64.dmg) |
| Windows x64 | Source ZIP, `.bat` launcher |
| Linux x86_64 desktop | Source ZIP, `.sh` launcher |

On a Mac, **Apple menu → About This Mac** shows the chip or processor and macOS
version. Windows ARM, Linux ARM, and older Mac systems are not validated targets
for this release. The source launcher does not guarantee that older systems can
run the required graphics libraries.

## Standalone Mac builds

For a desktop build supplied by the project, select its **DMG asset**:

| Your Mac | Filename |
| --- | --- |
| Apple Silicon | `LayerLoom-<version>-macos-arm64.dmg` |
| Intel | `LayerLoom-<version>-macos-x86_64.dmg` |

GitHub's automatic **Source code (zip)** asset is the source installation, not
the desktop app. A `.sha256` file is an optional download checksum; it is not an
installer. You do not need the `.whl`, `.tar.gz`, notices, or build-info files to
open the Mac app.

1. Open the downloaded DMG from Downloads.
2. Drag **LayerLoom.app** onto **Applications** in the disk-image window. If you
   cannot write there, copy it to a folder you own instead.
3. Open the copied app from Finder. Follow the [Mac first-open guide](mac_first_open.md)
   if macOS blocks it. You can eject the disk image after copying the app.
4. LayerLoom starts in Arrange, with a pixelated Benchy design on the empty
   build plate. Click **Try the example**, then **Weave**.
   The cubes select **Woven apparent colors** and **0.16 mm** automatically. Check those values,
   click **Choose save location…**, and choose a `.3mf` filename and folder.
   The Mac example suggests **LayerLoom** in your home folder. Wait for the **Export complete** panel, then use **Show in Finder** to locate it. The first export can take
   longer while its geometry search code compiles.

Loading an example keeps the current mode. To demonstrate surface painting,
turn on **Paint** in the main toolbar, select a surface, choose a color, click
**Apply paint**, and try Undo and Redo. Save an editable `.llpaint` project with **Save paint…**
and reopen it with **Open…**. Export a separate `.3mf` for your slicer; it contains
filament geometry rather than editable face-paint annotations. Follow the
[Paint tutorial](tutorial.md) for the full sequence.

These instructions accompany **0.6.4a6**. Use **Help → Copy diagnostics** to
confirm your build. Keep the instructions and download from the same release
together. No GitHub account is needed for the public release.

The app includes Python and its required libraries. After first-open approval,
the example works offline. To update, quit LayerLoom and replace the app with a
new copy; macOS may ask you to approve the new download. Keep saved models
in the suggested LayerLoom folder or another folder outside the app.

These builds use free ad-hoc signing and are **not notarized by Apple**. A
managed Mac may prohibit local approval. No paid Apple account is required to
use LayerLoom.

## Download and run the source

If a release supplies **LayerLoom-&lt;version&gt;-source.zip**, prefer that file:
it contains the application, examples, launchers and instructions without
manuscript materials. For a standalone Mac installation, choose the matching
DMG instead.

1. [Download the source ZIP](https://github.com/Finn2400/layerloom/releases/download/v0.6.4a6/LayerLoom-0.6.4a6-source.zip).
   This selects the release containing the installation fixes in this guide;
   no GitHub account is needed. Keep the launchers and all files from the same
   download together. GitHub's **Code → Download ZIP** uses whichever branch is
   selected on that page, which may contain an older version.
2. Open Downloads. On macOS, double-click the ZIP to extract it. On Windows,
   right-click the ZIP, choose **Extract All**, and finish extraction. On Linux,
   use your archive manager's **Extract** action.
3. Open the extracted folder containing `README.md`, `scripts`, and the
   **Launch LayerLoom** files. Do not run files from inside an archive preview.
   Double-click **START_HERE.html** for the short, offline-readable instructions.
4. Use the launcher for your computer below. Keep the extracted folder for
   future launches; moving it after setup requires a new environment download.

First setup needs internet and can take several minutes. A terminal window
shows download and installation progress, then opens LayerLoom. Keep that
window open while the app is running. Do not create a virtual environment or
install Python separately. The launcher obtains pinned Python 3.12 and locked
GUI dependencies; existing system Python, Conda, and repository `.venv` folders
are not used or changed. Reopening the same completed installation works
offline. Reopen the launcher if setup was interrupted.

### macOS

Double-click **Launch LayerLoom.command** in the extracted folder. If macOS
blocks it, try the per-app approval in [Mac first-open](mac_first_open.md#approve-a-blocked-download).

If it cannot run because of permissions or opens as text, this source-only
fallback works without making the script executable:

1. Open **Finder → Applications → Utilities → Terminal**.
2. Type `bash` followed by one space; do not press Return yet.
3. Drag **Launch LayerLoom.command** from Finder into Terminal. Terminal inserts
   its full path for you.
4. Press Return. Setup starts in that window.

The standalone `.app` uses the graphical installation above.

### Windows x64

Double-click **Launch LayerLoom.bat** in the extracted folder. File Explorer may
hide the `.bat` extension; its file type is **Windows Batch File**. A setup
window stays visible during installation.

If Windows shows **Open File – Security Warning** with **Unknown Publisher**,
check that the file is **Launch LayerLoom.bat** from the LayerLoom ZIP you
downloaded, then click **Run**. You can leave **Always ask before opening this
file** checked; Windows may show the same prompt on later launches.

If Windows shows **Windows protected your PC**, check that this is the launcher
from the LayerLoom download, then use **More info → Run anyway** if offered.
On a managed computer, an administrator may need to approve it. The launcher
uses a process-scoped PowerShell policy and needs no administrator terminal or
virtual-environment activation.

Microsoft describes this prompt in its [SmartScreen guidance for new apps](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/publish-first-app#step-6-handle-smartscreen-for-new-apps).

### Linux x64

Use a desktop session with working graphics drivers. Open a terminal in the
extracted folder and run:

```bash
bash "Launch LayerLoom.sh"
```

Python is managed automatically. Linux still needs the distribution's Qt/X11
and OpenGL/EGL runtime libraries, plus `curl` or `wget` for the first download.
A headless SSH shell cannot provide the GUI. On Ubuntu, install these runtime
packages if they are not already provided by your desktop:

```bash
sudo apt-get update
sudo apt-get install libgl1 libegl1 libopengl0 libgl1-mesa-dri \
  libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 libxcb-xinerama0 \
  libxcb-cursor0 libxkbcommon-x11-0
```

Qt's `xcb` platform plugin needs the XCB libraries even when the Python package
is installed correctly. Other distributions use different package names. Ask
your system administrator if a native library or graphics driver is unavailable.

## Repair a source installation

Close LayerLoom first. Repair needs internet and creates a fresh managed
environment; it preserves your source, saved models, exports, old environments,
and repository `.venv`.

On macOS, use the Terminal drag-and-drop steps above, then type ` --repair`
after the inserted path before pressing Return. From a terminal already open
in the extracted folder, use:

```bash
# macOS
bash "Launch LayerLoom.command" --repair
# Linux
bash "Launch LayerLoom.sh" --repair
```

On Windows, open the extracted folder in File Explorer, click its address bar,
type `cmd`, and press Enter. In the Command Prompt that opens, run:

```bat
"Launch LayerLoom.bat" --repair
```

You can replace `--repair` with `--doctor-only` to check the source installation
without opening the GUI. This checks packages and resources; it does not test
interactive graphics. For a damaged standalone app, copy a fresh app from the
project's DMG instead of installing packages inside it.

## Troubleshooting

| What you see | What to do |
| --- | --- |
| Unsure which download to choose | Use the direct Mac download links in the README; choose the DMG matching your chip. Windows/Linux users choose the source ZIP. |
| Mac says the developer cannot be verified or Apple cannot check the app | Follow [Mac first-open](mac_first_open.md). Attempt to open the copied app before looking for the approval button. |
| Mac says the app is damaged, or will damage your computer | Stop and obtain a fresh copy from the project. If it persists, report the exact message; this differs from the ordinary verification prompt. |
| Mac says this application is not supported | Check About This Mac against the architecture and minimum OS table. Use `arm64` for Apple Silicon and `x86_64` for Intel. |
| `python: command not found`, Python 3.9, or an activation error | Use the included launcher. You do not need a `python` command or a manually activated environment. |
| Download failed, checksum failed, connection error, or interrupted setup | Restore internet access and reopen the same launcher. It retries incomplete setup. Do not install extra Python versions to fix a download failure. |
| “The LayerLoom folder is incomplete” | Extract the entire ZIP, then open the launcher inside that extracted folder. Do not copy just the launcher to your Desktop. If files are still missing, download and extract a fresh ZIP. |
| Setup cannot write its logs or create its folders | Check free disk space and that your account can write to the reported folder. On a managed computer, ask your administrator; changing Python versions will not fix folder access. |
| `Another LayerLoom setup is running` | Let the first setup finish. If two launchers are open, keep the one doing the installation. A crashed process releases its lock automatically. |
| Setup needs a download while offline | Reconnect and reopen it. First setup, repair, a moved source folder, or changed runtime files needs a fresh environment. |
| A Qt platform plugin, OpenGL, or native library could not load | Save the error and setup log. On Linux, check the graphics prerequisites above. For source installs, try repair; for a Mac app, try a fresh matching DMG. |
| `Example unavailable` or `bundled worker is missing` | Source: extract a complete new ZIP. Mac app: replace the whole app from a fresh DMG. Keep your saved models. |
| Export exceeds the sampling limit | Open **Advanced export settings → Sampling budget**. This limits work in millions of cells, independently of memory. Consider coarser XY sampling first; raise the limit only for work you intend to run. |
| Export says its estimated memory budget is exceeded | Check **Advanced export settings → Export memory budget** and the displayed estimate. The supplied Benchy uses 4,096 MiB; cubes and ordinary imports use 2,048 MiB. Consider coarser XY sampling first, and leave memory for the model, app and other programs if increasing the budget. |
| Export completes but you cannot find the model | The completion message shows the full `.3mf` path you chose. `.llpaint` files are editable projects; open the exported `.3mf` in your slicer. |
| `layerloom-gui` or another command is not found | Beginner launchers do not add commands to your PATH. Reopen the launcher, or use the developer instructions below. |

### Collect useful information for support

Use **Help → Copy diagnostics**, then paste into a
[GitHub issue](https://github.com/Finn2400/layerloom/issues). Include the exact
error, your operating system, whether you used a DMG or source ZIP, and which
step failed. **Help → Open logs** opens the app's logs. If the app cannot open,
the source setup window or native error dialog shows the log location.

Default setup-log folders:

- macOS: `~/Library/Application Support/LayerLoom/logs`
- Windows: `%LOCALAPPDATA%\LayerLoom\logs`
- Linux: `~/.local/share/layerloom/logs` (or under `XDG_DATA_HOME` if set)

Default GUI-log folders:

- macOS: `~/Library/Logs/LayerLoom`
- Windows: `%LOCALAPPDATA%\LayerLoom\Logs`
- Linux: `~/.local/state/layerloom/logs` (or under `XDG_STATE_HOME` if set)

On a Mac, use **Finder → Go → Go to Folder** and paste the path. On Windows,
paste the path into File Explorer's address bar. Logs can contain local file
paths; review them before posting. If a default log folder is unavailable, the
app uses a temporary folder and reports that actual location in diagnostics.

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
`uv run --no-sync` for other installed LayerLoom commands; no activation is
needed. Dependency updates are explicit changes to the pins and lock followed
by the installation/desktop test matrix. Beginner launchers never update the lock.

## Slicing your result

Open the woven output as a multi-object model, keep the parts together, and
match the `all_…` objects to loaded filaments. Match slicer layer height to the
LayerLoom weave height; the first layer must be an integer multiple. See the
[tutorial](tutorial.md) and [slicer compatibility notes](slicer_compatibility.md).
