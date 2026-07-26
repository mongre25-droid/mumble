#!/usr/bin/env python3
"""Behavioral regression tests for production dictation calls."""

import os
import threading
from types import SimpleNamespace

import numpy as np

import mumble
import branding
import meeting
import meeting_store
from insertion import InsertionOutcome, InsertionResult


class _Settings:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def _binding_controller(**values):
    defaults = {
        "hotkey": "ctrl+windows",
        "quick_paste_hotkey": "ctrl+alt+v",
        "history_hotkey": "ctrl+alt+d",
        "search_hotkey": "ctrl+alt+f",
    }
    defaults.update(values)
    app = mumble.Mumble.__new__(mumble.Mumble)
    app.settings = _Settings(**defaults)
    app._binding_lock = threading.RLock()
    app.hotkey = defaults["hotkey"]
    app.quick_hotkey = defaults["quick_paste_hotkey"]
    app.history_hotkey = defaults["history_hotkey"]
    app.search_hotkey = defaults["search_hotkey"]
    app._hk_main = object()
    app._hk_quick = object()
    app._hk_history = object()
    app._hk_search = object()
    app.on_hotkey = lambda: None
    app.on_quick_paste = lambda: None
    app.on_open_history = lambda: None
    app.on_search_hotkey = lambda: None
    return app


def test_search_rebind_swaps_only_after_registration_and_persistence():
    app = _binding_controller()
    old_search = app._hk_search
    other_handles = (app._hk_main, app._hk_quick, app._hk_history)
    new_search = object()
    removed = []
    original_register = mumble.bindings.register_hotkey
    original_unregister = mumble.bindings.unregister
    mumble.bindings.register_hotkey = lambda spec, _cb: (
        new_search if spec == "ctrl+shift+f9" else None)
    mumble.bindings.unregister = lambda handle: removed.append(handle) or True
    try:
        ok, _message = app.apply_search_hotkey("ctrl+shift+f9")
    finally:
        mumble.bindings.register_hotkey = original_register
        mumble.bindings.unregister = original_unregister
    assert ok is True
    assert app.search_hotkey == "ctrl+shift+f9"
    assert app.settings.get("search_hotkey") == "ctrl+shift+f9"
    assert app._hk_search is new_search
    assert removed == [old_search]
    assert (app._hk_main, app._hk_quick, app._hk_history) == other_handles


def test_search_rebind_collision_keeps_previous_hook_and_setting():
    app = _binding_controller()
    old_search = app._hk_search
    original_register = mumble.bindings.register_hotkey
    mumble.bindings.register_hotkey = lambda *_a, **_k: (
        (_ for _ in ()).throw(AssertionError("collision reached registration")))
    try:
        ok, message = app.apply_search_hotkey("alt+ctrl+d")
    finally:
        mumble.bindings.register_hotkey = original_register
    assert ok is False
    assert "Open Deck" in message
    assert app._hk_search is old_search
    assert app.settings.get("search_hotkey") == "ctrl+alt+f"


def test_search_rebind_registration_failure_keeps_previous_state():
    app = _binding_controller()
    old_search = app._hk_search
    original_register = mumble.bindings.register_hotkey
    mumble.bindings.register_hotkey = lambda *_a, **_k: (
        (_ for _ in ()).throw(RuntimeError("reserved by another app")))
    try:
        ok, message = app.apply_search_hotkey("ctrl+shift+f9")
    finally:
        mumble.bindings.register_hotkey = original_register
    assert ok is False
    assert "reserved by another app" in message
    assert app._hk_search is old_search
    assert app.settings.get("search_hotkey") == "ctrl+alt+f"


def test_search_rebind_unhook_failure_rolls_back_new_hook_and_setting():
    app = _binding_controller()
    old_search = app._hk_search
    new_search = object()
    removed = []
    original_register = mumble.bindings.register_hotkey
    original_unregister = mumble.bindings.unregister
    mumble.bindings.register_hotkey = lambda *_a, **_k: new_search

    def unregister(handle):
        removed.append(handle)
        return handle is not old_search

    mumble.bindings.unregister = unregister
    try:
        ok, message = app.apply_search_hotkey("ctrl+shift+f9")
    finally:
        mumble.bindings.register_hotkey = original_register
        mumble.bindings.unregister = original_unregister
    assert ok is False
    assert "release" in message.lower()
    assert removed == [old_search, new_search]
    assert app._hk_search is old_search
    assert app.search_hotkey == "ctrl+alt+f"
    assert app.settings.get("search_hotkey") == "ctrl+alt+f"


def test_boot_heals_a_preexisting_search_collision_only():
    app = _binding_controller(search_hotkey="ctrl+alt+d")
    app._hk_search = None
    other_handles = (app._hk_main, app._hk_quick, app._hk_history)
    registered = []
    new_search = object()
    original_register = mumble.bindings.register_hotkey
    original_unregister = mumble.bindings.unregister
    mumble.bindings.register_hotkey = lambda spec, _cb: (
        registered.append(spec) or new_search)
    mumble.bindings.unregister = lambda _handle: True
    try:
        app._register_search()
    finally:
        mumble.bindings.register_hotkey = original_register
        mumble.bindings.unregister = original_unregister
    assert registered == ["ctrl+alt+f"]
    assert app.settings.get("search_hotkey") == "ctrl+alt+f"
    assert app.search_hotkey == "ctrl+alt+f"
    assert app._hk_search is new_search
    assert (app._hk_main, app._hk_quick, app._hk_history) == other_handles


def test_find_hotkey_toggles_during_every_dictation_state_without_rebinding_target():
    for recording, busy, processing in (
        (False, False, False),  # idle
        (True, False, False),   # listening
        (False, True, False),   # finalising transition
        (False, False, True),   # processing
    ):
        app = mumble.Mumble.__new__(mumble.Mumble)
        app.recording = recording
        app.busy = busy
        app._processing = processing
        lease = object()
        operation_id = "a" * 32
        app._dictation_insertion_lease = lease
        app._dictation_insertion_operation_id = operation_id
        hints = []
        app._quick_status = hints.append
        feature_events = []
        app._bump_feature = feature_events.append
        app._toggle_system_search_page = lambda: True
        original_thread = mumble.threading.Thread

        class InlineThread:
            def __init__(self, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        mumble.threading.Thread = InlineThread
        try:
            assert mumble.Mumble.on_search_hotkey(app) is True
        finally:
            mumble.threading.Thread = original_thread

        assert hints == []
        assert feature_events == ["search"]
        assert (app.recording, app.busy, app._processing) == (
            recording, busy, processing)
        assert app._dictation_insertion_lease is lease
        assert app._dictation_insertion_operation_id == operation_id


def test_search_open_failure_surfaces_an_island_hint():
    app = mumble.Mumble.__new__(mumble.Mumble)
    app.recording = False
    app.busy = False
    app._processing = False
    app._bump_feature = lambda *_a: None
    app._toggle_system_search_page = lambda: False
    hints = []
    app._quick_status = hints.append
    original_thread = mumble.threading.Thread

    class InlineThread:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    mumble.threading.Thread = InlineThread
    try:
        assert mumble.Mumble.on_search_hotkey(app) is True
    finally:
        mumble.threading.Thread = original_thread
    assert hints == ["Mumble Find couldn't toggle — try again"]


def test_find_toggle_retry_does_not_issue_a_second_toggle_via_process_launch():
    app = mumble.Mumble.__new__(mumble.Mumble)

    class LiveProcess:
        def poll(self):
            return None

    app._webui_proc = LiveProcess()
    sent = []

    def send(message, timeout):
        sent.append((message, timeout))
        return len(sent) == 2

    app._send_webui = send
    app._open_web_ui = lambda *_a: (_ for _ in ()).throw(
        AssertionError("a live resident process must not receive a second toggle")
    )
    original_sleep = mumble.time.sleep
    mumble.time.sleep = lambda _seconds: None
    try:
        assert mumble.Mumble._toggle_system_search_page(app) is True
    finally:
        mumble.time.sleep = original_sleep

    assert [entry[0] for entry in sent] == [
        {"cmd": "system_search_toggle"},
        {"cmd": "system_search_toggle"},
    ]


def test_twenty_cold_start_find_requests_launch_once_and_deliver_twenty_toggles():
    app = mumble.Mumble.__new__(mumble.Mumble)
    app._find_toggle_lock = mumble.threading.Lock()
    app._webui_proc = None
    launch_count = 0
    toggle_count = 0
    ready = False

    class LiveProcess:
        def poll(self):
            return None

    def open_web_ui(start):
        nonlocal launch_count, ready
        assert start == "search"
        launch_count += 1
        app._webui_proc = LiveProcess()
        ready = True
        return True

    def send(message, timeout):
        nonlocal toggle_count
        assert message == {"cmd": "system_search_toggle"}
        assert timeout == 0.8
        if not ready:
            return False
        toggle_count += 1
        return True

    app._open_web_ui = open_web_ui
    app._send_webui = send
    original_sleep = mumble.time.sleep
    mumble.time.sleep = lambda _seconds: None
    workers = [mumble.threading.Thread(
        target=lambda: mumble.Mumble._toggle_system_search_page(app)
    ) for _ in range(20)]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(1.0)
    finally:
        mumble.time.sleep = original_sleep

    assert all(not worker.is_alive() for worker in workers)
    assert launch_count == 1
    assert toggle_count == 20
    assert toggle_count % 2 == 0


def test_windows_heals_synced_macos_record_hotkey():
    app = mumble.Mumble.__new__(mumble.Mumble)
    app.settings = _Settings(hotkey="ctrl+option+d")
    original_platform = mumble.sys.platform
    mumble.sys.platform = "win32"
    try:
        assert app._sane_press_hotkey("hotkey", "ctrl+windows") == "ctrl+windows"
        assert app.settings.get("hotkey") == "ctrl+windows"
    finally:
        mumble.sys.platform = original_platform


def test_windows_keeps_valid_custom_record_hotkey():
    app = mumble.Mumble.__new__(mumble.Mumble)
    app.settings = _Settings(hotkey="ctrl+shift+space")
    original_platform = mumble.sys.platform
    mumble.sys.platform = "win32"
    try:
        assert app._sane_press_hotkey("hotkey", "ctrl+windows") == "ctrl+shift+space"
        assert app.settings.get("hotkey") == "ctrl+shift+space"
    finally:
        mumble.sys.platform = original_platform


class _Model:
    def __init__(self):
        self.kwargs = None

    def transcribe(self, audio, **kwargs):
        self.kwargs = kwargs
        return [SimpleNamespace(text=" hello", avg_logprob=-0.1,
                                start=0.0, end=0.5, words=[])], None


def _controller():
    app = mumble.Mumble.__new__(mumble.Mumble)
    app.settings = _Settings(language="en", confidence_gating=False,
                             vocabulary_terms=["  O'Brien  ", "bad<script>", "O'BRIEN"])
    app.lock = threading.Lock()
    app._tx_lock = threading.Lock()
    app.model = _Model()
    app._boost_priority = lambda: None
    app._restore_priority = lambda _old: None
    app._effective_model = lambda: "test"
    app._stt_device = "cpu"
    app._stt_compute = "int8"
    app._stt_model = "test"
    app._q_acc = []
    return app


def test_local_transcribe_calls_real_sanitizer_and_model():
    app = _controller()
    result = app._local_transcribe(np.zeros(1600, dtype=np.float32))
    assert result == "hello"
    assert app.model.kwargs["hotwords"] == "O'Brien, badscript"
    assert app.model.kwargs["temperature"] == 0.0
    assert app.model.kwargs["best_of"] == 1
    assert app.model.kwargs["without_timestamps"] is True
    assert app.model.kwargs["word_timestamps"] is False


def test_word_aligned_transcription_keeps_timestamps_enabled():
    app = _controller()
    result, _words = app._local_transcribe(
        np.zeros(1600, dtype=np.float32), want_words=True)
    assert result == "hello"
    assert app.model.kwargs["without_timestamps"] is False
    assert app.model.kwargs["word_timestamps"] is True


def test_cloud_empty_result_falls_back_to_local():
    app = _controller()
    app.settings.values.update(
        pro_mode=True,
        local_only_mode=False,
        transcription_mode="cloud",
        cloud_transcription_provider="groq",
        groq_api_key="FROZEN_TEST_KEY",
    )
    app._cloud_transcription_on = lambda _route: True
    app._cloud_transcribe = lambda _audio, _snapshot: "  "
    app._local_transcribe = lambda _audio, want_words=False: "local result"
    assert app._transcribe(np.zeros(10, dtype=np.float32)) == "local result"


def test_local_only_blocks_cloud_transcription_at_the_egress_boundary():
    app = mumble.Mumble.__new__(mumble.Mumble)
    app.settings = _Settings(
        local_only_mode=True,
        transcription_mode="cloud",
        cloud_transcription_provider="groq",
        groq_api_key="gsk-test",
    )

    assert app._cloud_transcription_on() is False


def test_local_only_blocks_cloud_generation_even_with_a_key():
    app = mumble.Mumble.__new__(mumble.Mumble)
    app.settings = _Settings(
        user_name="",
        prompt_prefs={},
        pro_mode=True,
        format_enabled=True,
    )
    app._ai_cfg = lambda: {
        "key": "csk-test", "provider": "cerebras", "model": "test",
        "url": "https://example.test", "models_url": "https://example.test/models",
        "key_setting": "cerebras_api_key",
    }
    app._gather_context = lambda *_args: ("", False)
    app._local_llm_generate = lambda *_args: None
    app._builder = lambda raw, *_args: ("text", raw)

    def cloud_should_not_run(*_args, **_kwargs):
        raise AssertionError("local-only attempted a cloud generation call")

    app._cloud_generate = cloud_should_not_run
    mode, output, used_offline = app._generate(
        "hello",
        det_mode="text",
        config_snap={
            "pro_mode": True,
            "format_enabled": True,
            "local_only_mode": True,
        },
        route_decision=SimpleNamespace(cloud_augmented=False),
    )

    assert mode == "text"
    assert output
    assert used_offline is True


def test_local_only_skips_background_provider_key_check(monkeypatch):
    app = mumble.Mumble.__new__(mumble.Mumble)
    app.settings = _Settings(pro_mode=True, local_only_mode=True)
    monkeypatch.setattr(
        mumble.ai,
        "key_ok",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("local-only attempted a key-check request")
        ),
    )

    app._check_pro_key()


def test_failed_stream_start_closes_partial_microphone_handle():
    class PartialStream:
        def __init__(self):
            self.stopped = False
            self.closed = False

        def stop(self):
            self.stopped = True

        def close(self):
            self.closed = True

    app = mumble.Mumble.__new__(mumble.Mumble)
    stream = PartialStream()
    app.stream = None
    app.frames = [np.ones(8, dtype=np.float32)]
    app.recording = False
    app.busy = True
    app._search_requested = True
    app.start_recording = lambda: (
        setattr(app, "stream", stream),
        (_ for _ in ()).throw(RuntimeError("simulated start failure")),
    )
    app._notify = lambda *_args: None
    app._set_state = lambda *_args: None
    app._idle = lambda: None

    original_sleep = mumble.time.sleep
    mumble.time.sleep = lambda _seconds: None
    try:
        app._safe_start()
    finally:
        mumble.time.sleep = original_sleep

    assert stream.stopped is True
    assert stream.closed is True
    assert app.stream is None
    assert app.frames == []
    assert app._search_requested is False
    assert app.busy is False


def test_stop_recording_closes_stream_after_device_stop_error():
    class UnpluggedStream:
        def __init__(self):
            self.closed = False

        def stop(self):
            raise RuntimeError("device disappeared")

        def close(self):
            self.closed = True

    app = mumble.Mumble.__new__(mumble.Mumble)
    stream = UnpluggedStream()
    app.lock = threading.Lock()
    app.recording = True
    app.stream = stream
    app.island = None
    app.frames = []
    app._stream_done = threading.Event()
    app._search_requested = True
    app._idle = lambda: None

    app.stop_recording()

    assert stream.closed is True
    assert app.stream is None
    assert app.recording is False
    assert app._stream_done.is_set()
    assert app._search_requested is False


def test_stop_reuses_idle_worker_without_three_second_join():
    class FinishedWorker:
        def __init__(self):
            self.joins = []

        def is_alive(self):
            return True

        def join(self, timeout=None):
            self.joins.append(timeout)

    worker = FinishedWorker()
    app = mumble.Mumble.__new__(mumble.Mumble)
    app.lock = threading.Lock()
    app.recording = True
    app.stream = None
    app.island = None
    app.frames = [np.ones(8000, dtype=np.float32)]
    app._stream_done = threading.Event()
    app._stream_idle = threading.Event()
    app._stream_idle.set()
    app._stream_worker_thread = worker
    app._search_requested = False
    app._mode_active = False
    app._processing = False
    app.settings = _Settings(min_seconds=0.3)
    processed = []
    app._process = lambda audio, duration, mode, windows: processed.append(
        (len(audio), duration, mode))
    app._set_state = lambda *_args: None

    app.stop_recording()

    assert processed and processed[0][0] == 8000
    assert worker.joins == [0.05]
    assert app._stream_done.is_set()


def test_microphone_validation_closes_handle_when_start_fails():
    wake_events = []

    class Wake:
        def pause(self, reason, wait=False):
            wake_events.append(("pause", reason, wait))
            return True

        def resume(self, reason):
            wake_events.append(("resume", reason))

    class BrokenValidationStream:
        last = None

        def __init__(self, **_kwargs):
            self.closed = False
            BrokenValidationStream.last = self

        def start(self):
            raise RuntimeError("driver start failed")

        def stop(self):
            raise RuntimeError("driver stop also failed")

        def close(self):
            self.closed = True

    class TrackingSettings:
        def __init__(self):
            self.saved = []

        def set(self, key, value):
            self.saved.append((key, value))

    app = mumble.Mumble.__new__(mumble.Mumble)
    app.settings = TrackingSettings()
    app._wake_word = Wake()
    original_stream = mumble.sd.InputStream
    mumble.sd.InputStream = BrokenValidationStream
    try:
        ok, _message = app.set_mic(7)
    finally:
        mumble.sd.InputStream = original_stream

    assert ok is False
    assert BrokenValidationStream.last.closed is True
    assert app.settings.saved == []
    assert [event[0] for event in wake_events] == ["pause", "resume"]
    assert wake_events[0][1].startswith("microphone-validation:")
    assert wake_events[1][1] == wake_events[0][1]
    assert wake_events[0][2] is True


def test_microphone_level_test_excludes_wake_listener():
    events = []
    completed = threading.Event()

    class Wake:
        def pause(self, reason, wait=False):
            events.append(("pause", reason, wait))
            return True

        def resume(self, reason):
            events.append(("resume", reason))

    class TestStream:
        def __init__(self, **_kwargs):
            events.append(("construct",))

        def __enter__(self):
            events.append(("enter",))
            return self

        def __exit__(self, *_args):
            events.append(("exit",))

    app = mumble.Mumble.__new__(mumble.Mumble)
    app._wake_word = Wake()
    app.settings = _Settings(mic_device=4)
    app._notify = lambda *_args: None

    original_stream = mumble.sd.InputStream
    original_sleep = mumble.time.sleep
    mumble.sd.InputStream = TestStream
    mumble.time.sleep = lambda _seconds: None
    try:
        app.test_microphone(lambda _level: None, completed.set)
        assert completed.wait(1.0)
    finally:
        mumble.sd.InputStream = original_stream
        mumble.time.sleep = original_sleep

    assert [event[0] for event in events] == [
        "pause", "construct", "enter", "exit", "resume"]
    assert events[0][1].startswith("microphone-test:")
    assert events[-1][1] == events[0][1]
    assert events[0][2] is True


def test_dictation_refuses_a_stuck_wake_microphone():
    app = mumble.Mumble.__new__(mumble.Mumble)
    app.paused = False
    app.busy = True
    app.meeting_recording = False
    app._transcription_ready = lambda: True
    app._pause_wake_word = lambda _reason: False
    resumed = []
    app._resume_wake_word = resumed.append
    app._open_input_stream = lambda: (_ for _ in ()).throw(
        AssertionError("dictation opened a competing stream"))

    try:
        app.start_recording()
    except RuntimeError as exc:
        assert "did not release" in str(exc)
    else:
        raise AssertionError("stuck wake listener did not veto dictation")
    assert resumed == ["dictation"]


def test_meeting_refuses_a_stuck_wake_microphone():
    started = []
    recorder = SimpleNamespace(start=lambda: started.append(True))
    app = mumble.Mumble.__new__(mumble.Mumble)
    app.meeting_recorder = recorder
    app.recording = False
    app._processing = False
    app.meeting_recording = False
    app._pause_wake_word = lambda _reason: False
    resumed = []
    app._resume_wake_word = resumed.append

    result = app._meeting_start()
    assert result["ok"] is False
    assert "did not release" in result["message"]
    assert started == []
    assert resumed == ["meeting"]


def test_inflight_stream_chunk_is_reused_after_stop():
    app = mumble.Mumble.__new__(mumble.Mumble)
    app._stream_done = threading.Event()
    app._stream_results = []
    app._stream_processed_samples = 0
    app._stream_session_id = 1
    app._stream_idle = threading.Event()
    app._stream_idle.set()
    app._stream_inflight_samples = 0
    app._stream_worker_thread = threading.current_thread()
    app.recording = True
    app.frames = [np.ones(mumble.SAMPLE_RATE * 4, dtype=np.float32)]
    app.lock = threading.Lock()

    def finish_after_stop(_audio, beam=1):
        app._stream_done.set()
        return "finished words"

    app._local_transcribe = finish_after_stop
    original_sleep = mumble.time.sleep
    mumble.time.sleep = lambda _seconds: None
    try:
        app._stream_worker()
    finally:
        mumble.time.sleep = original_sleep

    assert app._stream_results == ["finished words"]
    assert app._stream_processed_samples == mumble.SAMPLE_RATE * 4


def test_old_stream_session_cannot_publish_stale_chunk():
    app = mumble.Mumble.__new__(mumble.Mumble)
    app._stream_done = threading.Event()
    app._stream_results = []
    app._stream_processed_samples = 0
    app._stream_session_id = 1
    app._stream_idle = threading.Event()
    app._stream_idle.set()
    app._stream_inflight_samples = 0
    app._stream_worker_thread = threading.current_thread()
    app.recording = True
    app.frames = [np.ones(mumble.SAMPLE_RATE * 4, dtype=np.float32)]
    app.lock = threading.Lock()

    def finish_in_new_session(_audio, beam=1):
        app._stream_session_id = 2
        app._stream_done.set()
        return "stale words"

    app._local_transcribe = finish_in_new_session
    app._stream_worker()

    assert app._stream_results == []
    assert app._stream_processed_samples == 0
    assert not app._stream_idle.is_set()


def test_stop_between_snapshot_and_claim_prevents_decode():
    app = mumble.Mumble.__new__(mumble.Mumble)

    class StopAfterSnapshot:
        def __init__(self):
            self.entries = 0

        def __enter__(self):
            self.entries += 1
            return self

        def __exit__(self, *_args):
            if self.entries == 1:
                app.recording = False
                app._stream_done.set()

    calls = []
    app.lock = StopAfterSnapshot()
    app._stream_done = threading.Event()
    app._stream_results = []
    app._stream_processed_samples = 0
    app._stream_session_id = 1
    app._stream_idle = threading.Event()
    app._stream_idle.set()
    app._stream_inflight_samples = 0
    app._stream_worker_thread = threading.current_thread()
    app.recording = True
    app.frames = [np.ones(mumble.SAMPLE_RATE * 4, dtype=np.float32)]
    app._local_transcribe = lambda audio, beam=1: calls.append(audio) or "words"

    app._stream_worker()

    assert calls == []
    assert app._stream_idle.is_set()
    assert app._stream_processed_samples == 0


def test_autostart_setting_tracks_real_shortcut_result():
    class TrackingSettings:
        def __init__(self):
            self.saved = []

        def set(self, key, value):
            self.saved.append((key, value))

    app = mumble.Mumble.__new__(mumble.Mumble)
    app.settings = TrackingSettings()
    original = mumble.autostart.set_enabled
    try:
        mumble.autostart.set_enabled = lambda _value: False
        assert app.set_autostart(True) is False
        assert app.settings.saved == []

        mumble.autostart.set_enabled = lambda _value: True
        assert app.set_autostart(True) is True
        assert app.settings.saved == [("autostart", True)]
    finally:
        mumble.autostart.set_enabled = original


def test_failed_clipboard_write_never_sends_paste_hotkey():
    app = mumble.Mumble.__new__(mumble.Mumble)
    app.clipboard = None
    sent = []

    class FailedClipboardTransaction:
        def insert(self, request):
            return InsertionResult(
                operation_id=request.operation_id,
                source=request.source,
                outcome=InsertionOutcome.SAVED_ONLY,
                reason="clipboard write failed: fixture busy",
                message="saved only",
                send_count=0,
            )

    app._insertion_transaction = FailedClipboardTransaction()
    app._capture_insertion_target = lambda: None
    app._trace_mark = lambda *_args, **_kwargs: None
    app._paste_lock = threading.Lock()

    original_send = mumble.keyboard.send
    original_release = mumble.keyboard.release
    original_paste = mumble.pyperclip.paste
    original_sleep = mumble.time.sleep
    mumble.keyboard.send = lambda keys: sent.append(keys)
    mumble.keyboard.release = lambda _key: None
    mumble.pyperclip.paste = lambda: "previous clipboard"
    mumble.time.sleep = lambda _seconds: None
    try:
        result = app._paste("new dictated text")
    finally:
        mumble.keyboard.send = original_send
        mumble.keyboard.release = original_release
        mumble.pyperclip.paste = original_paste
        mumble.time.sleep = original_sleep

    assert result.outcome is InsertionOutcome.SAVED_ONLY
    assert result.send_count == 0
    assert sent == []


def test_copy_text_reports_clipboard_failure_honestly():
    app = mumble.Mumble.__new__(mumble.Mumble)
    app._set_clipboard = lambda _value: False
    notices = []
    app._notify = lambda title, message: notices.append((title, message))

    assert app.copy_text("cannot copy this") is False
    assert notices and notices[-1][0] == "Copy failed"


def test_capture_ignores_an_unchanged_clipboard_without_a_new_sequence():
    app = mumble.Mumble.__new__(mumble.Mumble)
    app.clipboard = None
    app._paste_lock = threading.Lock()
    app._set_clipboard = lambda *_args, **_kwargs: True
    sent = []

    original_send = mumble.keyboard.send
    original_release = mumble.keyboard.release
    original_paste = mumble.pyperclip.paste
    mumble.keyboard.send = lambda keys: sent.append(keys)
    mumble.keyboard.release = lambda _key: None
    mumble.pyperclip.paste = lambda: "existing clipboard text"
    try:
        assert app._grab_selection_quiet() == ""
        assert app._capture_conversation_quiet() == ""
    finally:
        mumble.keyboard.send = original_send
        mumble.keyboard.release = original_release
        mumble.pyperclip.paste = original_paste

    assert sent == ["ctrl+c", "ctrl+a", "ctrl+c"]


def test_meeting_delete_removes_private_audio():
    audio_dir = os.path.join(branding.DATA_DIR, "meetings_audio")
    os.makedirs(audio_dir, exist_ok=True)
    filename = "meeting_delete_regression.wav"
    path = os.path.join(audio_dir, filename)
    with open(path, "wb") as handle:
        handle.write(b"RIFF")
    mid = meeting_store.save_meeting(
        "Delete regression", filename, 1.0, [], [], status="ready")
    assert os.path.exists(path)
    assert meeting_store.delete_meeting(mid) is True
    assert not os.path.exists(path)
    assert meeting_store.get_meeting(mid) is None


def test_meeting_audio_names_do_not_collide():
    first_name, _first_path = meeting._new_meeting_audio_path()
    second_name, _second_path = meeting._new_meeting_audio_path()
    assert first_name != second_name


if __name__ == "__main__":
    failures = 0
    for fn in (test_windows_heals_synced_macos_record_hotkey,
               test_windows_keeps_valid_custom_record_hotkey,
               test_local_transcribe_calls_real_sanitizer_and_model,
               test_search_rebind_swaps_only_after_registration_and_persistence,
               test_search_rebind_collision_keeps_previous_hook_and_setting,
               test_search_rebind_registration_failure_keeps_previous_state,
               test_search_rebind_unhook_failure_rolls_back_new_hook_and_setting,
               test_boot_heals_a_preexisting_search_collision_only,
               test_search_hotkey_cannot_steal_focus_during_dictation,
               test_search_open_failure_surfaces_an_island_hint,
               test_word_aligned_transcription_keeps_timestamps_enabled,
               test_cloud_empty_result_falls_back_to_local,
               test_failed_stream_start_closes_partial_microphone_handle,
               test_stop_recording_closes_stream_after_device_stop_error,
               test_stop_reuses_idle_worker_without_three_second_join,
               test_microphone_validation_closes_handle_when_start_fails,
               test_microphone_level_test_excludes_wake_listener,
               test_dictation_refuses_a_stuck_wake_microphone,
               test_meeting_refuses_a_stuck_wake_microphone,
               test_inflight_stream_chunk_is_reused_after_stop,
               test_old_stream_session_cannot_publish_stale_chunk,
               test_stop_between_snapshot_and_claim_prevents_decode,
               test_autostart_setting_tracks_real_shortcut_result,
               test_failed_clipboard_write_never_sends_paste_hotkey,
               test_copy_text_reports_clipboard_failure_honestly,
               test_capture_ignores_an_unchanged_clipboard_without_a_new_sequence,
               test_meeting_delete_removes_private_audio,
               test_meeting_audio_names_do_not_collide):
        try:
            fn()
            print("  ok  " + fn.__name__)
        except Exception as exc:
            failures += 1
            print("  FAIL " + fn.__name__ + ": " + repr(exc))
    raise SystemExit(1 if failures else 0)
