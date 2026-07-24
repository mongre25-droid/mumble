"""Regression coverage for the user-facing recording limits."""

import threading

import numpy as np
import pytest

import mumble_mac as mumble
import recording_limits
import transcription


def _callback_controller():
    app = mumble.Mumble.__new__(mumble.Mumble)
    app.lock = threading.Lock()
    app.frames = []
    app._recorded_samples = 0
    app._dictation_limit_triggered = False
    app._last_audio_cb_time = 0
    app._stream_results = []
    app._stream_processed_samples = 0
    app.island = None
    app._last_level_queued = 0.0
    return app


def test_product_limits_and_labels_are_stable():
    assert recording_limits.DICTATION_MAX_SECONDS == 600
    assert recording_limits.MEETING_MAX_SECONDS == 14_400
    assert recording_limits.LONG_FORM_CHUNK_SECONDS == 600
    assert recording_limits.DICTATION_MAX_DISPLAY == "10:00"
    assert recording_limits.MEETING_MAX_DISPLAY == "4:00:00"


def test_audio_callback_trims_exactly_at_dictation_limit(monkeypatch):
    app = _callback_controller()
    app.frames = [np.ones((3, 1), dtype=np.float32)]
    app._recorded_samples = 3
    stopped = threading.Event()
    app._stop_at_dictation_limit = stopped.set

    class ImmediateThread:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(recording_limits, "DICTATION_MAX_SAMPLES", 5)
    monkeypatch.setattr(mumble.threading, "Thread", ImmediateThread)

    app._audio_cb(np.ones((4, 1), dtype=np.float32), 4, None, None)
    app._audio_cb(np.ones((4, 1), dtype=np.float32), 4, None, None)

    assert sum(len(block) for block in app.frames) == 5
    assert app._recorded_samples == 5
    assert app._dictation_limit_triggered is True
    assert stopped.is_set()


def test_sleep_gap_resets_duration_counter_before_cap(monkeypatch):
    app = _callback_controller()
    app.frames = [np.ones((4, 1), dtype=np.float32)]
    app._recorded_samples = 4
    app._last_audio_cb_time = 1.0
    app._stop_at_dictation_limit = lambda: None

    monkeypatch.setattr(recording_limits, "DICTATION_MAX_SAMPLES", 5)
    monkeypatch.setattr(mumble.time, "time", lambda: 10.0)

    app._audio_cb(np.ones((3, 1), dtype=np.float32), 3, None, None)

    assert len(app.frames) == 1
    assert len(app.frames[0]) == 3
    assert app._recorded_samples == 3
    assert app._dictation_limit_triggered is False


def test_meeting_limit_finalizes_even_without_island(monkeypatch):
    app = mumble.Mumble.__new__(mumble.Mumble)
    app.island = None
    app.meeting_recording = True
    stopped = []
    notifications = []
    refreshes = []
    app._meeting_stop = lambda title="": (
        stopped.append(title) or {"ok": True, "processing": True}
    )
    app._notify = lambda title, message: notifications.append((title, message))
    app._send_webui_async = refreshes.append

    class ImmediateThread:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(mumble.threading, "Thread", ImmediateThread)

    app._meeting_island_cb("limit_reached", 14_400, 0)

    assert stopped == [""]
    assert notifications and notifications[0][0] == "Meeting saved"
    assert refreshes == [{"cmd": "refresh", "what": "meeting_limit"}]


def test_cloud_request_rejects_audio_over_single_request_limit(monkeypatch):
    monkeypatch.setattr(recording_limits, "DICTATION_MAX_SAMPLES", 4)
    settings = type("Settings", (), {"get": staticmethod(lambda _k, d=None: d)})()

    with pytest.raises(ValueError, match="at most 10 minutes"):
        transcription.transcribe(
            np.zeros(5, dtype=np.float32), settings, language="en"
        )
