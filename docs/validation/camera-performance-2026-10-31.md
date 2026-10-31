# Camera performance investigation — October 31, 2026

Reported: up to 12 seconds of delay while rotating or zooming, with both the
Benchy tutorial and a shared ubiquitin file, on an unidentified recent Mac
(possibly M1/M2). Exact LayerLoom version, macOS, memory, monitor configuration,
and whether the model was woven remain unconfirmed. Do not attribute the fault
to geometry size or the user's hardware without reproducing it.

## Code review

- Current and v0.6.4a6 Arrange call `enable_anti_aliasing()` without a type;
  the installed PyVista default is SSAA (supersampling). The actual pixel
  framebuffer grows with display scaling and window size.
- QtInteractor uses a 5 Hz automatic render timer. The installed 0.13.1
  implementation skips hidden widgets. On macOS its `render()` emits a queued
  signal from a Python thread. Queue buildup is a candidate, not an observed
  cause of the friend's stall.
- The normal camera path does not invoke weaving or model normalization.
  Move/Rotate/Place tools have additional picking/preview work, so capture
  the exact interaction mode when reproducing.
- Existing Copy diagnostics reports versions and paths, but not graphics,
  scene complexity, render timings, or UI responsiveness.
- No dependency versions or rendering defaults have been changed in this
  investigation. v0.6.4a6 and this checkout lock PyVista 0.49.1,
  PyVistaQt 0.13.1, and VTK 9.7.1.

## Local probe (not end-to-end interaction latency)

Apple M1 Pro; actual Qt/VTK window, bundled cubes and 253,176-face Benchy.
At 1200×800 logical window size, viewport framebuffers were 1434×1360 (Arrange)
and 1682×1360 (Paint). Fifteen synchronous VTK render calls per configuration,
with camera azimuth/dolly changes; first two excluded from summary.
Original Arrange median call durations were 1.59 ms (cubes), 1.77 ms (Benchy).
Disabling antialiasing gave 1.36 and 1.41 ms. MSAA4 was not faster locally.
These CPU-side call timings do NOT measure GPU completion or input queue delay.
The reported 12-second problem has not been reproduced.

Evidence and script:
`~/LayerLoom Fresh User Test/Evidence/performance-2026-10-31/benchmark.json`
and `benchmark.py`. Idle counts in that probe include previously queued render
requests; do not interpret them as a steady-state idle rendering rate.

## Opt-in diagnostics

Help → Diagnostic logging… opens a modeless panel. Start diagnostic logging,
then use any application workflow normally. There is no automatic time cutoff.
Closing the panel keeps logging on, with a permanent indicator in the main
window. Stop logging or quit the app to finish. Open log folder exposes the
diagnostic-session.log file and its rotated copies; Copy summary and existing
Copy diagnostics include the session summary and worker check when available.

Records package/app versions, architecture, active graphics vendor/renderer,
framebuffer/display scaling, actor/cell counts, bounded render timing samples,
200 ms GUI timer delays, process peak memory where available, and bounded
main-thread stack locations for >2-second stalls. Stack sampling may fail while
native code holds the GIL. No model contents, local variables, model filenames,
or uploads are collected. Slow samples and the finished report also go to the
existing rotating application log.

A temporary Reduce display smoothing checkbox supports an A/B recording. It
changes antialiasing only, is fixed during each recording, and restores the
original display settings on closing the panel. It does not simplify geometry.

## Wednesday comparison

The DMG app uses PyInstaller's bundled Python; its worker is addressed inside
`Contents/Helpers`, without looking up a system interpreter. Current source
launchers also explicitly acquire managed Python 3.12.15. Check the report's
Installation, Python, and Executable fields before ruling out an older source
installation on the friend's Mac. Bundling prevents system-Python selection
problems; it does not rule out defects in the bundled graphics dependencies.

1. Record exact build and Mac/macOS/display configuration first.
2. Restart the same app; load bundled Benchy, then record camera rotation/zoom.
3. Repeat with reduced smoothing. Keep model, window size, and mode identical.
4. Repeat with the exact ubiquitin file, before weaving. Record whether delays
   differ after weaving; keep that a separate recording.
5. Compare the old and diagnostic builds using the same sequence. If practical,
   compare a smaller window and the built-in display as separate trials.
6. Keep reports plus a short screen recording showing the delayed action.
   Persistent hangs may require macOS Activity Monitor → Sample Process.

Local tests inject a 2.7-second UI stall to verify delay detection, main-thread
sampling, restart, observer cleanup, clipboard reporting, restoration
of smoothing, and preservation of model vertices. A new signed/ad-hoc packaged
artifact still needs testing; the public download does not yet have this panel.

Validation: 24 targeted native diagnostics, desktop UI, and logging tests passed;
the two recorder tests passed again after bounding watchdog lifetime and adding
slow-stack log persistence. A live Benchy camera-rotation recording also completed.
The 11 recorder/logging checks passed after retaining completed timing summaries
and including PyVistaQt in the runtime version report.

## Follow-up: missing GLB dependency and viewport drops

The friend's screenshot reports `ModuleNotFoundError: No module named 'lib3mf'`
at the legacy GLB splitter's top-level import. This occurs before model parsing,
so it does not diagnose defective source geometry. The user saw version 6 being
installed and run. Its exact executable and installation type remain unknown;
traceback source paths alone cannot distinguish source from frozen applications.
The v0.6.4a6 GUI extras and worker spec already include lib3mf. Check the running
executable and worker environment rather than attributing this to stale installs
or system Python without evidence. Other readers/writers and rendering paths do
not require lib3mf, explaining why some operations can still work.

Current PyVistaQt 0.13.1 enables drops and handles them with
`add_mesh(pyvista.read(filename))`. LayerLoom inherited that handler in both
viewports, bypassing document state, assignments, and normal GLB conversion.
This explains a mechanism for a display-only "import", but the friend's exact
drop has not been reproduced. Extra actors may add rendering load; there is no
evidence that this caused his reported 12-second delay.

Desktop drops now consume the inherited handler and open one local supported
model through LayerLoom's normal import route. GLB drops in Arrange share the
Open action's settings dialog. Paint retains its intentional surface-import
route. Native Qt event tests cover both viewports, a real GLB worker import,
settings cancellation preserving the current model, and rejecting multiple,
remote, and unsupported files. Browser/Finder testing of the finished DMG is
still required; this change is not in public a6 downloads.
All 17 native desktop checks passed after the drop fix (including the four new
drop cases); two existing XML-element truthiness deprecation warnings remain.

## Additional robustness checks

The worker now has an explicit `--diagnostics` probe that records its version,
Python, architecture, frozen status and executable, then actually creates a
native lib3mf model. A missing/broken library produces structured failure data
and a nonzero exit. Normal Copy diagnostics identifies the resolved worker
command or a missing bundled helper without launching a background operation.
Missing-module errors from import workers give source-repair or app-replacement
instructions; the full traceback remains in the existing logs.

The Mac verifier previously exercised only legacy `glb-split`. It now also runs
the current desktop `glb-import` on a multicolor intact surface, applies explicit
filament recipes to its generated regions, runs `region-weave`, and checks the
saved output and material-volume validation report. In artifact runs these
operations use the relocated frozen helper with external Python paths removed.
The same verifier chain passed locally through source worker subprocesses in an
unrelated folder with spaces and Unicode; this is not frozen-artifact evidence.

Local validation after these changes: 39 worker/packaging/logging checks, 20
native desktop/performance checks, and 40 bootstrap/desktop-import checks passed
(99 total). The native cases include refusing replacement with unsaved edits
and a real malformed GLB failure: document identity, geometry, assignments and
dirty state survive. Bootstrap coverage includes stale environments, interrupted
setup, repair failure, offline reuse, source relocation and lock contention;
these automated cases do not replace the planned clean-account trial.

Both architecture DMGs and the hosted cross-OS validation matrix still need to
pass for this exact commit before release. Friend-specific camera latency and
his missing-lib3mf installation remain unconfirmed on his actual machine.

## Persistent support evidence and runtime identity

The performance window now offers a nonblocking Check installation action. It
runs the selected worker's diagnostic command, compares app/Python/architecture
and core-library versions, and creates a native lib3mf model. Failed launch,
malformed reports, mismatches and a 15-second timeout are reported without
blocking Qt. Results are logged and included in Copy report; checking is kept
separate from timing a camera recording. No tools are installed or changed.

New Mac builds include exact core dependency versions in build-info.json alongside
commit, tree, lock hash and Python. Diagnostics include that build identity and
flag available version mismatches. Metadata for those packages is explicitly
collected into the bundle, including the independent worker. This identifies
what is actually running, not merely which DMG a user remembers downloading.

A controlled 2.7-second GUI stall wrote a slow-thread sample to disk before Qt
recovered, then saved the completed timing/graphics report. Native tests also
exercise a real healthy worker, an intentionally mismatched worker, timeout and
missing executable. 43 diagnostics/worker/packaging/logging checks passed; the
14 native diagnostics/logging checks passed again after the final lifecycle and
identity checks. Small copied evidence logs are stored locally at
`~/LayerLoom Fresh User Test/Evidence/performance-2026-10-31/log-proof/`.
The stall is deliberately injected test evidence, not a reproduction of the
friend's unexplained delay. Detailed session logging is opt-in; basic
startup and worker-error logging is automatic. Python sampling cannot guarantee
a stack during native calls holding the GIL; Activity Monitor sampling remains
the fallback for a persistent freeze. These additions still require new DMG
validation and are not in the public a6 download.

## General session logging (replaces the timed-recorder interface)

At the user's request, the diagnostic UI now starts a general log session rather
than a timed camera recording. It attaches a rotating diagnostic-session.log
handler (2 MB threshold, three backups), captures application/worker logging and
model/action/status signals, and saves periodic responsiveness checkpoints.
Import attempts, paint edits, export settings, export stages/failures and mode
changes are included. Closing the panel does not stop logging; accepted app exit
does. Technical summaries and the smoothing comparison are secondary controls.
Full model contents and text-field contents are not captured; operation paths
can appear. Basic startup/error logging remains on independently.

Native tests exercise a session beyond the former 60-second boundary, hide the
panel, load a model, apply paint, provoke an export-budget failure, and verify
those events in the saved log. They check file rotation, stopping on accepted
app close, disconnecting activity listeners, restoring the previous log level,
and leaving no extra handler attached.
Validation: all 33 native session/desktop/logging checks passed; the four native
session tests passed again after moving shutdown ahead of viewer disposal.
