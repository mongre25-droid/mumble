"""Focused Issue #23 contracts for Meetings and the Island Control Rail."""

import os
import sys
from types import SimpleNamespace

import pytest

import island_render
import meeting
import overlay
import webui_shell


class _Settings:
    def __init__(self, **overrides):
        self.values = {
            "mic_device": 7,
            "transcription_mode": "cloud",
            "cloud_transcription_provider": "groq",
            "groq_api_key": "test-only-stt-key",
            "pro_mode": True,
            "local_only_mode": False,
            "instant_text": False,
            "llm_provider": "cerebras",
            "cerebras_api_key": "test-only-analysis-key",
            "cerebras_model": "llama-3.3-70b",
        }
        self.values.update(overrides)

    def get(self, key, default=None):
        return self.values.get(key, default)


def _recorder_status(level):
    recorder = meeting.MeetingRecorder.__new__(meeting.MeetingRecorder)
    recorder._lock = __import__("threading").Lock()
    recorder._stream = object()
    recorder._recording = True
    recorder._starting = False
    recorder._audio_full_path = "meeting.wav"
    recorder._capture_finalized = False
    recorder._sample_count = 32_000
    recorder._active_meeting_id = "meeting-23"
    recorder._writer_error = None
    recorder._audio_level = level
    return recorder.capture_status()


def test_live_meeting_status_exposes_a_bounded_stable_input_level():
    quiet = _recorder_status(0.125)
    loud = _recorder_status(4.0)

    assert quiet["audio_level"] == pytest.approx(0.125)
    assert loud["audio_level"] == 1.0


def test_capture_status_freezes_the_effective_microphone_opened_for_the_meeting(
    monkeypatch, tmp_path
):
    class _Stream:
        device = 7

        def __init__(self, **_kwargs):
            pass

        def start(self):
            pass

    settings = _Settings(mic_device=7)
    audio_path = tmp_path / "meeting.wav"
    fake_sd = SimpleNamespace(
        InputStream=_Stream,
        query_devices=lambda index, *_args: {
            "name": "Conference microphone" if index == 7 else "Later microphone"
        },
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    monkeypatch.setattr(
        meeting, "_new_meeting_audio_path", lambda: (audio_path.name, str(audio_path))
    )
    monkeypatch.setattr(meeting.meeting_store, "save_meeting", lambda **_values: "meeting-23")
    recorder = meeting.MeetingRecorder(lambda *_args, **_kwargs: "", settings)

    try:
        recorder.start()
        settings.values["mic_device"] = 9

        status = recorder.capture_status()

        assert status["microphone"] == {
            "index": 7,
            "name": "Conference microphone",
        }
    finally:
        recorder._recording = False
        recorder._stream = None
        if recorder._writer_queue is not None:
            recorder._writer_queue.put(None)
        if recorder._writer_thread is not None:
            recorder._writer_thread.join(timeout=2)
        if audio_path.exists():
            os.remove(audio_path)


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        ({"local_only_mode": True}, "device_only"),
        ({"pro_mode": False}, "hosted_processing_off"),
        ({"cerebras_api_key": ""}, "missing_key"),
        ({"llm_provider": "unsupported"}, "unsupported_provider"),
    ],
)
def test_meeting_context_ledger_uses_effective_route_truth_without_secrets(
    overrides, expected_reason
):
    api = webui_shell.Api.__new__(webui_shell.Api)
    api.settings = _Settings(**overrides)
    api.list_microphones = lambda: [
        {"index": -1, "name": "System default"},
        {"index": 7, "name": "Conference microphone"},
    ]

    context = api.meeting_context()

    assert context["ok"] is True
    assert context["microphone"] == "Conference microphone"
    assert context["saved_location"] == "Private Mumble meeting library"
    assert context["transcription"]["effective"] in {"local", "cloud"}
    assert context["analysis"]["feature"] == "meetings"
    assert context["analysis"]["lane"] == "meeting_analysis"
    assert context["analysis"]["reason"] == expected_reason
    assert context["analysis"]["ready"] is False
    assert "api_key" not in context["analysis"]
    assert "test-only" not in repr(context)


def test_listening_stop_is_one_labelled_36_to_44_pixel_control():
    layout = island_render.bar_layout({
        "stop_enabled": True,
        "stop_label": "Stop · 1:05 · 2 saved",
    })

    assert layout["stop_label"].startswith("Stop")
    assert layout["stop"] is not None
    assert 36 <= layout["stop_target_height"] <= 44
    assert layout["stop_bounds"][3] - layout["stop_bounds"][1] == layout["stop_target_height"]
    assert layout["processing_cancel"] is None


def test_listening_stop_hit_test_uses_the_same_bounded_visible_geometry():
    calls = []
    bar = overlay._WidgetBar.__new__(overlay._WidgetBar)
    bar.ok = True
    bar.get_state = lambda: {"stop_enabled": True, "stop_label": "Stop"}
    bar.on_stop = lambda: calls.append("stop")
    bar.on_control_review = bar.on_control_cancel = None
    bar.on_mode = bar.on_deck = bar.on_correct = None
    bar.on_correct_dismiss = bar.on_foreign = None
    layout = island_render.bar_layout(bar.get_state())
    left, top, right, bottom = layout["stop_bounds"]

    bar._on_click(SimpleNamespace(x=(left + right) / 2, y=top - 1))
    bar._on_click(SimpleNamespace(x=(left + right) / 2, y=(top + bottom) / 2))

    assert calls == ["stop"]


def test_control_rail_is_nonactivating_before_its_first_visible_frame():
    events = []
    bar = overlay._WidgetBar.__new__(overlay._WidgetBar)
    bar.ok = True
    bar.visible = False
    bar.place_below = lambda *_args: events.append("place")
    bar._ensure_styled = lambda: events.append("style-noactivate")
    bar.win = SimpleNamespace(
        deiconify=lambda: events.append("show"),
        attributes=lambda *_args: events.append("topmost"),
        lift=lambda: events.append("lift"),
    )

    bar.show_below(0, 0, 460, 64)

    assert events.index("style-noactivate") < events.index("show")
    assert bar.visible is True


def test_reduced_motion_freezes_both_island_and_control_rail_frames():
    island_snap = {
        "state": "listening",
        "label": "Listening",
        "timer": "0:04",
        "level": 0.7,
        "reduced_motion": True,
    }
    rail_snap = {
        "stop_enabled": True,
        "stop_label": "Stop",
        "reduced_motion": True,
    }

    assert island_render.render({**island_snap, "frame": 1}).tobytes() == (
        island_render.render({**island_snap, "frame": 99}).tobytes()
    )
    assert island_render.render_bar({**rail_snap, "frame": 1}).tobytes() == (
        island_render.render_bar({**rail_snap, "frame": 99}).tobytes()
    )


def test_native_island_snapshot_carries_reduced_motion_without_entrance_fade():
    island = overlay.Island.__new__(overlay.Island)
    island.state = "listening"
    island.level = 0.4
    island.frame = 91
    island.armed = False
    island.is_suggest = False
    island.is_gathering = False
    island.reduced_motion = True
    island._was_active = False
    island._appear_left = 0

    snapshot = island._build_snapshot()

    assert snapshot["reduced_motion"] is True
    assert snapshot["fade"] == 1.0


def test_native_island_snapshot_defaults_motion_safely_for_lightweight_hosts():
    island = overlay.Island.__new__(overlay.Island)
    island.state = "listening"
    island.level = 0.4
    island.frame = 3
    island.armed = False
    island.is_suggest = False
    island.is_gathering = False
    island._was_active = False
    island._appear_left = 0

    snapshot = island._build_snapshot()

    assert snapshot["reduced_motion"] is False
    assert snapshot["frame"] == 3


def test_primary_island_is_click_through_but_control_rail_never_activates():
    base = 0x00040000
    primary = overlay._layered_exstyle(base, transparent=True)
    rail = overlay._layered_exstyle(base, transparent=False)

    assert primary & overlay.WS_EX_TRANSPARENT
    assert primary & overlay.WS_EX_NOACTIVATE
    assert rail & overlay.WS_EX_NOACTIVATE
    assert not rail & overlay.WS_EX_TRANSPARENT


def test_control_rail_stop_delegates_to_the_configured_global_transition_once():
    calls = []
    island = overlay.Island.__new__(overlay.Island)
    island.on_stop = lambda: calls.append("global-transition")

    island._widget_stop()

    assert calls == ["global-transition"]
