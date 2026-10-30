# First open on a Mac

Download the **[Mac app](https://github.com/Finn2400/layerloom/releases/tag/v0.6.4a6)**,
copy it to Applications, and open it. No Python or Terminal setup is needed.
This guide covers the standalone app; [source users have separate instructions](install.md#download-and-run-the-source).

![Three first-open steps: copy and open the app, find Privacy and Security in System Settings, then approve LayerLoom with Open Anyway.](images/mac-first-open.svg)

## Choose and copy the app

1. Choose **Apple menu → About This Mac**. Note the macOS version and the chip
   or processor.
2. Use the `arm64` DMG for an **Apple M-series chip** on macOS 14 or newer.
   Use the `x86_64` DMG for an **Intel processor** on macOS 15 or newer.
3. Open the DMG and drag **LayerLoom.app** to Applications, or another folder
   you can write to. Open that copied app from Finder.

## Approve a blocked download

If the alert says the developer cannot be verified or Apple cannot check the
app, confirm that this copy came from the LayerLoom project. Then:

1. Dismiss the alert without moving the app to Trash.
2. Open **Apple menu → System Settings → Privacy & Security** and scroll down
   to **Security**.
3. Find the message naming **LayerLoom**. Choose **Open Anyway**; some versions
   first show **Open** in this section. Confirm **Open** or **Open Anyway** in
   the resulting dialog and enter your Mac login password if asked.

The button is available for about an hour after an attempted launch. If no
LayerLoom message appears, try opening the copied app again, then return to
Privacy & Security. This approves that app; subsequent launches use an ordinary
double-click. On macOS 15 Sequoia and 26 Tahoe, use this Settings route instead
of relying on a Finder right-click shortcut. The Settings route also applies
to macOS 14 Sonoma.

Apple documents the procedure in [Safely open apps on your Mac](https://support.apple.com/en-us/102445)
and its version-specific guides for [macOS 15](https://support.apple.com/en-gb/guide/mac-help/mh40617/15.0/mac/15.0)
and [macOS 26](https://support.apple.com/en-gb/guide/mac-help/mh40617/26/mac/26).
Button wording can vary with the exact macOS update.

If approval remains unavailable, a managed-computer policy may prevent it;
contact the administrator. An **app is damaged** or **will damage your computer**
message is different: stop, obtain a fresh project download, and report the
exact message if it persists. Do not disable macOS security protections to open it.

On the first launch, allow roughly half a minute for bundled libraries and
font caches to initialize. After approval, wait for the window instead of
opening another copy.

## Make your first woven model

1. LayerLoom starts in Arrange, showing a pixelated Benchy design on
   the empty build plate. Click **Try the example** to load four CMY cubes.
   Examples keep the mode you are using.
2. Click **Export geometry 3MF…**. The tutorial selects **Woven apparent
   colors** and **0.16 mm** automatically. Check those values and click **Choose save location…**.
   The first export may take longer while geometry search code compiles.
3. Choose a `.3mf` filename and folder. The example suggests **LayerLoom** in your home folder. Wait for the **Export complete** panel.
   Use **Show in Finder** to locate the output.
4. Open it in your slicer, keep its parts aligned, and assign the `all_…`
   objects to the corresponding filaments. For this first example, use
   **0.16 mm for both normal and first layers**, with adaptive layers disabled.

## Try painting

With the cubes open, turn on **Paint** in the main toolbar, choose **Surface
select**, click a surface, pick a color, and click **Apply paint**. Try Undo
and Redo. Choose **Save paint…** to keep an editable `.llpaint` project, then
reopen it with **Open…**. Export a separate
`.3mf` for slicing: it contains the physical filament geometry generated from
those colors. It does not retain editable face paint. See the [tutorial](tutorial.md)
for selection tools, the larger Benchy example, and export limits.

These instructions accompany **0.6.4a6**. Use **Help → Copy diagnostics** to
confirm the build and follow the instructions supplied with that download.

Python and the required libraries are inside the app. Once first-open approval
is complete, you can run the example offline. Outputs go to the folder you
choose. For help, use **Help → Copy diagnostics** or **Help → Open logs**.

## Choosing files and folders

The Mac app uses a simple file chooser without Finder’s cloud-provider sidebar.
The example suggests the local **LayerLoom** folder in your home folder. You can
navigate to another folder or enter a path in the chooser. Technical export
settings are under **Advanced export settings**.

macOS may ask for access if you choose Documents, Desktop, Downloads, an external
drive, or cloud storage. Only allow a location you intended to use; the example
does not require Google Drive or OneDrive. Replacing an ad-hoc-signed test build
may require approval again. Report unexpected requests rather than granting
Full Disk Access.
