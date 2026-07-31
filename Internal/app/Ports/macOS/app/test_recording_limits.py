"""Regression coverage for the user-facing recording limits."""

import threading

import numpy as np
import pytest

import mumble_mac as mumble
import processing_route
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
    app._dictation_session = object()
    app.island = None
    app._last_level_queued = 0.0
    return app


class _DurableAppender:
    def __init__(self, accepted_samples=0, limit=None):
        self.accepted_samples = accepted_samples
        self.limit = limit
        self.blocks = []

    def append(self, block):
        remaining = (len(block) if self.limit is None else
                     max(0, self.limit - self.accepted_samples))
        accepted = min(len(block), remaining)
        if accepted:
            self.blocks.append(block[:accepted].copy())
            self.accepted_samples += accepted
        return accepted


def test_product_limits_and_labels_are_stable():
    assert recording_limits.DICTATION_MAX_SECONDS == 600
    assert recording_limits.MEETING_MAX_SECONDS == 14_400
    assert recording_limits.LONG_FORM_CHUNK_SECONDS == 600
    assert recording_limits.DICTATION_MAX_DISPLAY == "10:00"
    assert recording_limits.MEETING_MAX_DISPLAY == "4:00:00"


def test_audio_callback_honours_durable_session_acceptance_boundary():
    app = _callback_controller()
    app.frames = [np.ones((3, 1), dtype=np.float32)]
    app._recorded_samples = 3
    durable = _DurableAppender(accepted_samples=3, limit=5)
    app._append_durable_audio = durable.append

    app._audio_cb(np.ones((4, 1), dtype=np.float32), 4, None, None)
    app._audio_cb(np.ones((4, 1), dtype=np.float32), 4, None, None)

    assert sum(len(block) for block in durable.blocks) == 2
    assert durable.accepted_samples == 5
    assert sum(len(block) for block in app.frames) == 3
    assert app._recorded_samples == 5
    assert app._dictation_limit_triggered is False


def test_sleep_gap_resets_duration_counter_before_cap(monkeypatch):
    app = _callback_controller()
    app.frames = [np.ones((4, 1), dtype=np.float32)]
    app._recorded_samples = 4
    app._last_audio_cb_time = 1.0
    durable = _DurableAppender(accepted_samples=4)
    app._append_durable_audio = durable.append

    monkeypatch.setattr(mumble.time, "time", lambda: 10.0)

    app._audio_cb(np.ones((3, 1), dtype=np.float32), 3, None, None)

    assert app.frames == []
    assert sum(len(block) for block in durable.blocks) == 3
    assert durable.accepted_samples == 7
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
    settings = {
        "pro_mode": True,
        "local_only_mode": False,
        "transcription_mode": "cloud",
        "cloud_transcription_provider": "groq",
        "groq_api_key": "test-only-key",
        "groq_transcription_model": "whisper-large-v3-turbo",
    }
    invocation = processing_route.snapshot_inputs(
        settings, feature="dictation", lane="speech_to_text"
    )

    with pytest.raises(ValueError, match="at most 10 minutes"):
        transcription.transcribe(np.zeros(5, dtype=np.float32), invocation)
