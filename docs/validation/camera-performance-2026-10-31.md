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

Help → Performance diagnostics… opens a modeless panel. Load a model first,
start a 60-second recording, then reproduce normal rotation and zooming.
Stop or wait for automatic completion, then Copy report. The screenshot-friendly
status shows longest render call and longest UI delay. Existing Copy diagnostics
includes the last completed performance report when one exists.

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
sampling, auto-stop, restart, observer cleanup, clipboard reporting, restoration
of smoothing, and preservation of model vertices. A new signed/ad-hoc packaged
artifact still needs testing; the public download does not yet have this panel.

Validation: 24 targeted native diagnostics, desktop UI, and logging tests passed;
the two recorder tests passed again after bounding watchdog lifetime and adding
slow-stack log persistence. A live Benchy camera-rotation recording also completed.
