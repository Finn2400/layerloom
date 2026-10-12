# First open on a Mac

This guide applies to **LayerLoom.app from a desktop DMG**, not the source ZIP.
Desktop builds use free ad-hoc signing, without Apple notarization. Security
approval may be necessary for each downloaded release.

![Three first-open steps: copy and open the app, find Privacy and Security in System Settings, then approve LayerLoom with Open Anyway.](images/mac-first-open.svg)

1. Download the **Apple Silicon** or **Intel** DMG matching **Apple menu → About
   This Mac**. Apple Silicon builds require macOS 14+; Intel builds require 15+.
2. Open the DMG. Copy **LayerLoom.app** to Applications or another writable
   folder, then open the copied app.
3. If macOS says Apple cannot verify/check the app, dismiss that message and open
   **System Settings → Privacy & Security**.
4. Scroll to the security section, find **LayerLoom**, and click **Open Anyway**.
   Confirm **Open** when prompted.
5. Choose **Try the example**, click **Weave**, and select where to save your
   model. **Show in Finder** reveals the finished woven 3MF.

Apple provides screenshots and the current procedure in
[Safely open apps on your Mac](https://support.apple.com/en-us/102445).
Labels may differ between versions. This approves the individual app; it does
not disable Gatekeeper globally.

If **Open Anyway** is unavailable on a managed computer, contact its
administrator. If macOS reports actual damage or malware, do not treat that as
the ordinary unidentified-developer prompt: download a fresh copy from the
project's release page and report the exact message if it persists.

Once the app opens, Python and all required libraries are already inside it.
You can disconnect from the internet and complete the example. Outputs go to
the folder you choose, not inside the app.
