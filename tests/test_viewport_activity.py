"""Timing correlation without opening a GUI or capturing user input."""
import pytest

pytest.importorskip('qtpy')
from layerloom import performance_diagnostics as diagnostics


def test_pending_stall_is_not_hidden_by_newer_input(monkeypatch):
    now = [1.0]
    monkeypatch.setattr(diagnostics.time, 'monotonic', lambda: now[0])
    activity = diagnostics._ViewportActivity(True)
    activity.input('hover_moves')
    assert activity.snapshot()['pending_input_age_ms'] is None
    activity.input('drag_moves')
    activity.camera_changed()
    now[0] = 13.0
    activity.input('drag_moves')
    activity.camera_changed()
    report = activity.snapshot()
    assert report['pending_input_age_ms'] == 12000
    assert report['pending_camera_change_age_ms'] == 12000
    activity.swapped()
    report = activity.snapshot()
    assert report['oldest_input_to_next_qt_swap']['max_ms'] == 12000
    assert report['oldest_camera_change_to_next_qt_swap']['max_ms'] == 12000
    assert report['pending_input_age_ms'] is None
    assert report['drag_moves'] == report['camera_changes'] == 2
    # Idle swaps must not manufacture measurements or lower the session maximum.
    now[0] = 60.0
    activity.swapped()
    assert activity.snapshot()['oldest_input_to_next_qt_swap']['samples'] == 1


def test_timings_are_bounded_and_keep_session_maximum(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(diagnostics.time, 'monotonic', lambda: now[0])
    activity = diagnostics._ViewportActivity(True)
    for index in range(4200):
        activity.input('wheel_events')
        activity.camera_changed()
        now[0] += 12.0 if index == 0 else .001
        activity.swapped()
    report = activity.snapshot()
    assert report['oldest_input_to_next_qt_swap']['samples'] == 4096
    assert report['oldest_input_to_next_qt_swap']['max_ms'] == 1
    assert report['oldest_input_to_next_qt_swap']['max_ms_entire_recording'] == 12000
    assert report['oldest_camera_change_to_next_qt_swap']['max_ms_entire_recording'] == 12000


def test_unavailable_swap_signal_is_explicit():
    report = diagnostics._ViewportActivity(False).snapshot()
    assert report['qt_frame_swap_signal_available'] is False
    assert report['oldest_input_to_next_qt_swap'] == {'samples': 0, 'max_ms_entire_recording': 0.0}
