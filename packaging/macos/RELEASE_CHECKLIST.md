# Mac desktop release gates

Build with the **Mac desktop** manual GitHub Actions workflow. Only public-repository standard runners are used, to keep the workflow free. Apple Silicon is built on macOS 14; Intel is built on macOS 15. The very same applications are then tested on macOS 14/15/26 (arm64) and 15/26 (Intel).

The workflow builds draft-quality artifacts. **A passing workflow does not verify Gatekeeper or establish novice usability.** Before enabling tagged draft builds, complete the checks below and set the repository Actions variable `MAC_DESKTOP_RELEASES_ENABLED` to `true`. Every resulting release remains a draft until a maintainer finishes its manual checks. Do not publish automatically.

## Automated evidence

- [ ] All installation/bootstrap tests pass on Windows, Linux and macOS.
- [ ] A clean source archive and wheel include runtime modules, palettes and both examples.
- [ ] Both architecture builds pass native minimum-OS, signature and relocation checks.
- [ ] All five OS/architecture jobs complete the frozen worker → weave → bake path and GLB import with optional repair absent.
- [ ] All five jobs render the actual GUI, try the cubes, weave at 0.16 mm, save and reopen the output. Inspect the captured example and woven screenshots; a process exit alone is insufficient.

## Required manual checks on the downloaded DMGs

- [ ] Download each DMG with a browser so the normal quarantine attribute is applied. Keep quarantine intact; do not use `xattr` as an installation workaround.
- [ ] On a Mac without a user-installed Python, Git, Node or Homebrew, drag LayerLoom to Applications and launch it from Finder. Follow Read Me First's graphical Open Anyway instructions. Record OS, architecture and the observed prompts.
- [ ] Have a person unfamiliar with Python load the example, weave it and locate the resulting 3MF without Terminal or coaching. Record any confusing step.
- [ ] Open the result in a slicer; verify the preassigned parts, common alignment and the 0.16 mm layer instructions.
- [ ] Reopen while offline, move the app to another folder containing spaces/non-ASCII characters, and repeat the example.
- [ ] Replace an older app with this build, verify the new version starts, and record any renewed approval prompt. Do not delete user output or application logs.
- [ ] Review `THIRD_PARTY_NOTICES`, the GPL/LGPL terms of the bundled PyQt/Qt components and availability of required corresponding sources for the exact versions. Include or link corresponding source downloads alongside the draft; do not assume the LayerLoom MIT license replaces dependency obligations.
- [ ] Verify draft assets include both DMGs, their SHA-256 files, source archive, wheel, notices and first-run instructions. Confirm the tag equals the app version.

Only after these gates pass may a maintainer publish the draft and switch the README's default Mac installation path to the validated release. Managed Macs that prohibit local approval are unsupported by ad-hoc signing; do not claim universal compatibility.

## Local reproduction

With the pinned uv installed, from a clean checkout:

```sh
UV_PROJECT_ENVIRONMENT=.venv-desktop uv sync --managed-python --locked --extra gui --extra desktop --no-default-groups --no-editable
.venv-desktop/bin/python scripts/build_macos.py
.venv-desktop/bin/python scripts/validate_macos.py dist/macos/LayerLoom.app --gui --report dist/macos/validation
```

The app uses no external interpreter. The Python above is only the developer's build/verification tool. `build-info.json` inside the app records its exact Python, PyInstaller, architecture, minimum macOS version and dependency-lock hash. Local builds on newer macOS are development artifacts until validated on the minimum supported OS.
