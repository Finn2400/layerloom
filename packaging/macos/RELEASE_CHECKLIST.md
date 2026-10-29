# Mac desktop release gates

Build with the **Mac desktop** manual or pull-request GitHub Actions workflow.
Only public-repository standard runners are used, to keep the workflow free.
Both architectures build on macOS 15. Apple Silicon dependency selection and
native binaries target macOS 14; Intel targets macOS 15. The same applications
are tested on macOS 15/26 for both architectures. The manual workflow's
`validate_macos14` input adds a hosted macOS 14 ARM job while that runner remains
available; it defaults to false because GitHub retires it on November 2, 2026.
The workflow summary explicitly records when macOS 14 runtime testing is absent.

**macOS 14 support still requires a passing test of the exact ARM artifact on
macOS 14.** After runner retirement, perform this gate on a local macOS 14 Mac.
Record the artifact SHA-256, OS version, verifier report and GUI first-run result.
A newer build host, compatible wheel tags and static native-library checks alone
do not establish backward compatibility. Do not mark this gate passed using a
previous build's evidence. Runner retirement does not change the app's stated
minimum OS. See [GitHub's retirement notice](https://github.com/actions/runner-images/issues/13518).

The workflow builds draft-quality artifacts. **A passing workflow does not
verify Gatekeeper or establish novice usability.** Before enabling tagged draft
builds, complete the initial checks below and set the repository Actions variable
`MAC_DESKTOP_RELEASES_ENABLED` to `true`. A maintainer may assemble an unpublished
test draft from successful manual/PR artifacts while manual checks remain pending.
Every release remains a draft until its checks pass. Draft downloads are visible
only to maintainers with repository write access. Do not publish automatically.

## Record evidence for this exact build

- Version / tag:
- Commit SHA:
- Installation CI run:
- Mac build and validation run:
- Apple Silicon checksum:
- Intel checksum:
- Manual tester, date, Mac model and OS for each test:

Leave a box unchecked until its evidence exists. A local Apple Silicon run does
not establish Intel compatibility, and a copied app without browser quarantine
does not establish the first-download experience.

## Automated evidence

- [ ] All installation/bootstrap tests pass on Windows, Linux and macOS.
- [ ] Source and wheel content validation passes, including Paint modules, palettes, both examples, helper scripts, source tests, and Paint documentation. The installed wheel exports a painted cube from an unrelated directory.
- [ ] The real source launchers pass first setup, offline reopening and Unicode-path worker tests on native Windows, macOS and Linux CI.
- [ ] Both architecture builds pass native minimum-OS, signature and relocation checks.
- [ ] All four standard hosted OS/architecture jobs complete the frozen worker → weave → bake path and GLB import with optional repair absent.
- [ ] This exact ARM artifact also passes `validate_macos.py --gui` on macOS 14, using the optional hosted job before retirement or a local Mac afterward. Record its checksum and report; this remains required for publication.
- [ ] A fresh launch shows Arrange, the empty build plate and pixelated Benchy artwork with Paint off. Loading either example preserves the selected mode. Toggle Paint on to expose surface-editing controls, then off to confirm model, colors and placement are retained. Verify the bundled first-open illustration ends at “Try the example”.
- [ ] All hosted jobs and the required macOS 14 acceptance run render the actual GUI and export the packaged cubes from Arrange at 0.16 mm, then explicitly enable Paint. They must select and paint a surface, undo and redo the edit, save and reopen an editable project, and switch between Paint and Arrange. Their real asynchronous export worker must complete the painted-cube geometry proof at 0.16 mm, including the bundled Numba/LLVM search, then reopen its exported 3MF. Inspect example, edited-paint, Arrange, export, and reopened-output screenshots; a process exit alone is insufficient.
- [ ] Inspect frozen GUI stage timings, heartbeat/progress logs and any near-deadline traceback. A valid output file alone does not pass the test: worker completion, editing, saved-project reopening and exported-model reopening must all succeed.
- [ ] Native Paint, Arrange, and desktop integration tests pass on Linux and Windows, including mode switching, shared placement, cancellation, and export controls.

## Required manual checks on the downloaded DMGs

- [ ] Download each DMG with a browser so the normal quarantine attribute is applied. Keep quarantine intact; do not use `xattr` as an installation workaround.
- [ ] On a Mac without a user-installed Python, Git, Node or Homebrew, drag LayerLoom to Applications and launch it from Finder. Follow Read Me First's graphical Open Anyway instructions. Record OS, architecture and the observed prompts.
- [ ] Have a person unfamiliar with Python load the example, use Export geometry 3MF with 0.16 mm layers, and locate the resulting 3MF without Terminal or coaching. Record any confusing step.
- [ ] Open the result in a slicer; verify the preassigned parts, common alignment and the 0.16 mm layer instructions.
- [ ] Reopen while offline, move the app to another folder containing spaces/non-ASCII characters, and repeat the example.
- [ ] Replace an older app with this build, verify the new version starts, and record any renewed approval prompt. Do not delete user output or application logs.
- [ ] Review `THIRD_PARTY_NOTICES`, the GPL/LGPL terms of the bundled PyQt/Qt components and availability of required corresponding sources for the exact versions. Include or link corresponding source downloads alongside the draft; do not assume the LayerLoom MIT license replaces dependency obligations.
- [ ] Match the version shown in Help → Copy diagnostics to the DMG/source archive and instructions. In the private preview release body, link the source guide to the recorded commit, not the default branch. A new candidate needs newly built source distributions and both DMGs.
- [ ] Verify draft assets include both DMGs, their SHA-256 files, source archive, wheel, architecture-specific notices/build-info files, first-run HTML plus SVG, and `VALIDATION.txt` with results for that exact candidate. Confirm the tag equals the app version and all artifacts correspond to the recorded commit.
- [ ] Before publication, update draft-status notices in README, install/first-open docs, release notes and DMG HTML; add the actual validated public download link. Rebuild the DMGs if their bundled instructions change.

Private complex-model input files and screenshots stay outside public release assets; the validation record may summarize appropriate aggregate measurements.

Only after these gates pass may a maintainer publish the draft and switch the README's default Mac installation path to the validated release. Managed Macs that prohibit local approval are unsupported by ad-hoc signing; do not claim universal compatibility.

## Local reproduction

With the pinned uv installed, from a clean checkout:

```sh
# Apple Silicon: target aarch64-apple-darwin, minimum 14.0.
# Intel: use x86_64-apple-darwin and minimum 15.0 instead.
MACOSX_DEPLOYMENT_TARGET=14.0 UV_PROJECT_ENVIRONMENT=.venv-desktop uv sync --managed-python --locked --extra gui --extra desktop --no-default-groups --no-install-project --no-build --python-platform aarch64-apple-darwin
MACOSX_DEPLOYMENT_TARGET=14.0 UV_PROJECT_ENVIRONMENT=.venv-desktop uv sync --managed-python --locked --extra gui --extra desktop --no-default-groups --no-editable --python-platform aarch64-apple-darwin
.venv-desktop/bin/python scripts/build_macos.py
.venv-desktop/bin/python scripts/validate_macos.py dist/macos/LayerLoom.app --gui --report dist/macos/validation
```

The app uses no external interpreter. The Python above is only the developer's build/verification tool. `build-info.json` inside the app records its exact Python, PyInstaller, architecture, minimum macOS version and dependency-lock hash. Local builds on newer macOS are development artifacts until validated on the minimum supported OS.
