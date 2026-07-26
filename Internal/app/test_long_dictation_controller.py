"""Accelerated public-boundary proof for Issue #16 live dictation capture."""

import json
import threading
import types

import numpy as np

import mumble
import island_render
import dictation_trace
from overlay import Island
from dictation_session import DurableDictationSession
from history import History


class _Settings:
    def get(self, key, default=None):
        values = {
            "resource_saver": True,
            "min_seconds": 0.0,
            "model": "small.en",
            "transcription_mode": "local",
        }
        return values.get(key, default)


class _Stream:
    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


class _Island:
    def set_state(self, _state):
        pass

    def set_armed(self, _armed):
        pass

    def set_level(self, _level):
        pass


def _controller(session_root):
    controller = mumble.Mumble.__new__(mumble.Mumble)
    controller.settings = _Settings()
    controller.paused = False
    controller.model = object()
    controller.busy = False
    controller.recording = False
    controller.meeting_recording = False
    controller.stream = None
    controller.island = _Island()
    controller.lock = threading.Lock()
    controller.prompt_mode_enabled = False
    controller.active_mode = None
    controller._search_requested = False
    controller._stream_done = threading.Event()
    controller._stream_idle = threading.Event()
    controller._stream_idle.set()
    controller._stream_session_id = 0
    controller._processing = False
    controller._pause_wake_word = lambda _lease: True
    controller._resume_wake_word = lambda _lease: None
    controller._cloud_transcription_on = lambda _route=None: False
    controller._set_state = lambda _state: None
    controller._tk_schedule = lambda fn, *args, **kwargs: fn(*args, **kwargs)
    controller._maybe_warm_ai = lambda: None
    controller._open_input_stream = _Stream
    controller._restore_meeting_island = lambda: None
    controller._notify = lambda *_args, **_kwargs: None
    controller._trace_begin_dictation = lambda: None
    controller._trace_mark = lambda *_args, **_kwargs: None
    controller._trace_mark_once = lambda *_args, **_kwargs: None
    controller._trace_finish = lambda *_args, **_kwargs: None
    controller._transcribe = lambda _audio, want_words=False: ""
    controller._insertion_module = types.SimpleNamespace(
        begin=lambda *_args: types.SimpleNamespace(target_lease=object())
    )
    controller._dictation_session_root = session_root
    controller._dictation_segment_max_samples = 4
    controller._dictation_capture_queue_blocks = 2
    controller._dictation_finalizer_id = "f" * 32
    return controller


def test_public_record_stop_persists_every_sample_beyond_old_limit(tmp_path, monkeypatch):
    """The real controller seam must not cap or bypass the durable session."""
    controller = _controller(tmp_path)
    controller._dictation_capture_queue_blocks = 4
    processed = []
    controller._process = lambda audio, duration, mode_active, windows: processed.append({
        "audio": audio.copy(), "duration": duration,
    })

    # Twelve samples represent a dictation beyond the former eight-sample
    # "ten-minute" boundary, without waiting in real time.
    monkeypatch.setattr(mumble.recording_limits, "DICTATION_MAX_SAMPLES", 8)
    controller.start_recording()
    for start in (0, 4, 8):
        block = np.arange(start, start + 4, dtype=np.float32).reshape(-1, 1) / 20.0
        controller._audio_cb(block, len(block), None, None)
    controller.stop_recording()

    assert len(processed) == 1
    assert len(processed[0]["audio"]) == 0  # durable segments were consumed
    assert processed[0]["duration"] == 12 / mumble.SAMPLE_RATE
    manifests = list(tmp_path.glob("*/manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["next_sample"] == 12
    assert [segment["sample_count"] for segment in manifest["segments"]] == [4, 4, 4]
    assert manifest["finalization"]["state"] == "claimed"


def test_public_progress_stays_bounded_and_exposes_labelled_stop(tmp_path):
    controller = _controller(tmp_path)
    controller._process = lambda *_args, **_kwargs: None
    controller.start_recording()

    peak_buffered = 0
    for index in range(20):
        block = np.full((4, 1), index / 100.0, dtype=np.float32)
        controller._audio_cb(block, len(block), None, None)
        controller._dictation_capture_queue.join()
        progress = controller.dictation_progress()
        peak_buffered = max(peak_buffered, progress["buffered_samples"])

    controller.stop_recording()
    progress = controller.dictation_progress()
    assert progress["captured_samples"] == 80
    assert peak_buffered <= 12  # one 4-sample segment + two queued blocks
    assert progress["stop_action"] == {"label": "Stop", "enabled": False}


def test_public_stop_reconciles_ordered_segment_transcript(tmp_path):
    controller = _controller(tmp_path)
    controller._dictation_capture_queue_blocks = 4
    partials = iter(("we said very", "very very clearly", "clearly done"))
    inference_sizes = []

    def transcribe(audio, want_words=False):
        inference_sizes.append(len(audio))
        return next(partials)

    controller._transcribe = transcribe
    completed = []
    controller._process = lambda audio, *_args: completed.append({
        "audio_samples": len(audio),
        "transcript": " ".join(controller._stream_results),
    })

    controller.start_recording()
    for value in (0.1, 0.2, 0.3):
        block = np.full((4, 1), value, dtype=np.float32)
        controller._audio_cb(block, len(block), None, None)
    controller.stop_recording()

    assert completed == [{
        "audio_samples": 0,
        "transcript": "we said very very clearly done",
    }]
    assert inference_sizes == [4, 8, 8]


def test_public_stop_replaces_a_revised_overlap_word(tmp_path):
    controller = _controller(tmp_path)
    controller._dictation_capture_queue_blocks = 4
    controller._dictation_inference_overlap_samples = 2
    partials = iter((
        ("we heard cat", [
            {"word": "we", "start": 0.0, "end": 0.00004},
            {"word": "heard", "start": 0.00004, "end": 0.00010},
            {"word": "cat", "start": 0.00016, "end": 0.00024},
        ]),
        ("cap clearly", [
            {"word": "cap", "start": 0.00002, "end": 0.00010},
            {"word": "clearly", "start": 0.00016, "end": 0.00024},
        ]),
    ))
    controller._transcribe = lambda *_args, **_kwargs: next(partials)
    completed = []
    controller._process = lambda *_args: completed.append(
        controller._dictation_stable_transcript
    )

    controller.start_recording()
    for _index in range(2):
        block = np.full((4, 1), 0.1, dtype=np.float32)
        controller._audio_cb(block, len(block), None, None)
    controller.stop_recording()

    assert completed == ["we heard cap clearly"]


def test_history_public_add_is_idempotent_for_one_logical_session(tmp_path):
    history = History(
        str(tmp_path / "history.json"), str(tmp_path / "history.txt"), 100
    )
    first = history.add("ordered final text", record_id="a" * 32)
    replay = history.add("ordered final text", record_id="a" * 32)

    assert replay == first
    assert len(history.recent(10)) == 1
    assert history.stats()["total_transcripts"] == 1


def test_public_recovery_saves_once_and_never_replays_insertion(tmp_path):
    sessions = tmp_path / "sessions"
    session = DurableDictationSession.create(
        sessions,
        session_id="b" * 32,
        sample_rate=mumble.SAMPLE_RATE,
        channels=1,
        segment_max_samples=4,
    )
    session.append_pcm16(np.full(4, 1000, dtype="<i2").tobytes())

    controller = _controller(sessions)
    controller.history = History(
        str(tmp_path / "history.json"), str(tmp_path / "history.txt"), 100
    )
    controller._local_transcribe = lambda _audio, want_words=False: "recovered words"
    paste_calls = []
    controller._paste = lambda *_args, **_kwargs: paste_calls.append(1)

    first = controller.recover_durable_dictations()
    second = controller.recover_durable_dictations()

    assert first == {"recovered": 1, "errors": 0}
    assert second == {"recovered": 0, "errors": 0}
    assert [item["text"] for item in controller.history.recent(10)] == [
        "recovered words"
    ]
    assert paste_calls == []
    finalization = session.open(sessions, "b" * 32).read_manifest()["finalization"]
    assert finalization["state"] == "complete"
    assert finalization["insertion_outcome"] == "saved_only"


def test_island_exposes_one_labelled_stop_action_while_listening():
    layout = island_render.bar_layout({
        "stop_enabled": True,
        "modes": [("prompt", "Prompt"), ("email", "Email")],
    })
    calls = []
    island = Island.__new__(Island)
    island.on_stop = lambda: calls.append("Stop")
    island._widget_stop()

    assert layout["stop"] is not None
    assert layout["stop_label"] == "Stop"
    assert layout["chips"] == []
    assert calls == ["Stop"]


def test_island_stop_action_shows_truthful_live_progress():
    island = Island.__new__(Island)
    island.bar_state = {"stop_enabled": True}
    island._refresh_bar = lambda: None

    island.set_dictation_progress({
        "duration_seconds": 65.2,
        "segments_persisted": 2,
    })
    layout = island_render.bar_layout(island.bar_state)

    assert layout["stop_label"] == "Stop · 1:05 · 2 saved"


def test_content_free_trace_summary_reports_stage_percentiles():
    names = [
        ("activation", 0), ("island_render", 2),
        ("capture_finalization_started", 10),
        ("capture_finalization_finished", 13),
        ("conversion_started", 13), ("conversion_finished", 18),
        ("transfer_started", 18), ("transfer_finished", 25),
        ("vad_started", 25), ("vad_finished", 29),
        ("inference_started", 29), ("inference_finished", 40),
        ("decoding_started", 40), ("decoding_finished", 46),
        ("formatting_started", 46), ("formatting_ready", 50),
        ("paste_lock_acquired", 50), ("clipboard_ready", 53),
        ("insertion_started", 53), ("insertion_finished", 60),
        ("paste_finished", 60),
    ]
    warm = {
        "schema": dictation_trace.SCHEMA,
        "context": {"model_resident": True},
        "events": [
            {"name": name, "elapsed_ms": elapsed} for name, elapsed in names
        ],
    }
    cold = {
        "schema": dictation_trace.SCHEMA,
        "context": {"model_resident": False},
        "events": [
            {"name": name, "elapsed_ms": elapsed * 2} for name, elapsed in names
        ],
    }

    summary = dictation_trace.summarize_traces(
        [warm, cold], corpus_version="issue16-accelerated-v1"
    )

    assert summary["schema"] == "mumble.dictation-trace-summary.v1"
    assert summary["corpus_version"] == "issue16-accelerated-v1"
    assert set(summary["stages"]) == {
        "activation_to_paste", "capture_finalization", "conversion",
        "transfer", "vad", "inference", "decoding", "shaping", "ui",
        "clipboard", "insertion",
    }
    assert summary["by_lifecycle"]["warm"]["activation_to_paste"] == {
        "count": 1, "p50_ms": 60.0, "p90_ms": 60.0,
        "p95_ms": 60.0, "max_ms": 60.0,
    }
    assert summary["by_lifecycle"]["cold"]["activation_to_paste"]["p95_ms"] == 120.0
    assert summary["bottleneck_by_lifecycle"] == {
        "cold": "inference", "warm": "inference",
    }
    assert "text" not in json.dumps(summary).lower()


def test_public_finalization_commits_one_history_and_one_insertion(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    session = DurableDictationSession.create(
        sessions,
        session_id="c" * 32,
        sample_rate=mumble.SAMPLE_RATE,
        channels=1,
        segment_max_samples=4,
    )
    session.append_pcm16(np.full(4, 500, dtype="<i2").tobytes())
    owner_id = "d" * 32
    operation_id = "e" * 32
    session.claim_finalization(owner_id, operation_id)

    controller = _controller(sessions)
    controller._dictation_session = session
    controller._dictation_finalizer_id = owner_id
    controller.history = History(
        str(tmp_path / "history.json"), str(tmp_path / "history.txt"), 100
    )
    controller.stat_store = types.SimpleNamespace(record=lambda *_args, **_kwargs: True)
    controller._send_webui_async = lambda *_args, **_kwargs: None
    controller._remember_correction_candidate = lambda *_args, **_kwargs: None
    controller._maybe_island_tip = lambda *_args, **_kwargs: None
    controller._idle = lambda: None
    insertion_calls = []

    def paste(*_args, **_kwargs):
        insertion_calls.append(operation_id)
        return types.SimpleNamespace(
            confirmed=True,
            outcome=types.SimpleNamespace(value="confirmed"),
            target_lease=object(),
        )

    controller._paste = paste
    monkeypatch.setattr(mumble.time, "sleep", lambda _seconds: None)

    controller._finalize_text(
        "one ordered result",
        {"duration": 0.5},
        target_lease=object(),
        operation_id=operation_id,
    )
    controller._finalize_text(
        "one ordered result",
        {"duration": 0.5},
        target_lease=object(),
        operation_id=operation_id,
    )

    assert len(controller.history.recent(10)) == 1
    assert insertion_calls == [operation_id]
    assert session.read_manifest()["finalization"] == {
        "state": "complete",
        "owner_id": owner_id,
        "operation_id": operation_id,
        "history_state": "committed",
        "history_record_id": "c" * 32,
        "insertion_state": "requested",
        "insertion_outcome": "confirmed",
    }


def test_short_dictation_remains_discarded_at_public_stop_boundary(tmp_path):
    controller = _controller(tmp_path)
    base_settings = controller.settings

    class Settings:
        def get(self, key, default=None):
            if key == "min_seconds":
                return 0.3
            return base_settings.get(key, default)

    controller.settings = Settings()
    processed = []
    controller._process = lambda *_args, **_kwargs: processed.append(1)
    controller.start_recording()
    block = np.full((4, 1), 0.1, dtype=np.float32)
    controller._audio_cb(block, len(block), None, None)
    controller.stop_recording()

    assert processed == []
    assert list(tmp_path.iterdir()) == []


def test_recovery_reconciles_history_saved_before_manifest_marker(tmp_path):
    sessions = tmp_path / "sessions"
    session_id = "1" * 32
    owner_id = "2" * 32
    operation_id = "3" * 32
    session = DurableDictationSession.create(
        sessions,
        session_id=session_id,
        sample_rate=mumble.SAMPLE_RATE,
        channels=1,
        segment_max_samples=4,
    )
    session.append_pcm16(np.full(4, 700, dtype="<i2").tobytes())
    session.claim_finalization(owner_id, operation_id)

    controller = _controller(sessions)
    controller.history = History(
        str(tmp_path / "history.json"), str(tmp_path / "history.txt"), 100
    )
    controller.history.add(
        "already saved output", record_id=session_id
    )
    controller._local_transcribe = lambda *_args, **_kwargs: "different replay"

    assert controller.recover_durable_dictations() == {
        "recovered": 1, "errors": 0,
    }
    assert [entry["text"] for entry in controller.history.recent(10)] == [
        "already saved output"
    ]
    assert session.read_manifest()["finalization"]["state"] == "complete"


def test_slow_storage_never_blocks_callback_or_loses_accepted_block(
        tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    original_append = DurableDictationSession.append_pcm16

    def slow_append(session, payload):
        entered.set()
        release.wait(2.0)
        return original_append(session, payload)

    monkeypatch.setattr(DurableDictationSession, "append_pcm16", slow_append)
    controller = _controller(tmp_path)
    controller._dictation_capture_queue_blocks = 1
    controller._process = lambda *_args, **_kwargs: None
    controller.start_recording()
    block = np.full((4, 1), 0.1, dtype=np.float32)
    controller._audio_cb(block, len(block), None, None)
    assert entered.wait(1.0)
    controller._audio_cb(block, len(block), None, None)

    returned = threading.Event()
    callback = threading.Thread(target=lambda: (
        controller._audio_cb(block, len(block), None, None), returned.set()
    ))
    callback.start()
    prompt_returned = returned.wait(0.2)
    release.set()
    callback.join(2.0)
    stop_thread = getattr(controller, "_dictation_capture_stop_thread", None)
    if stop_thread is not None:
        stop_thread.join(2.0)
    if controller.recording:
        controller.stop_recording()

    assert prompt_returned is True
    assert controller._dictation_capture_stop_requested is True
    manifests = list(tmp_path.glob("*/manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["next_sample"] == 12


def test_failed_microphone_start_discards_empty_session_and_writer(tmp_path):
    controller = _controller(tmp_path)
    resumed = []
    controller._resume_wake_word = lambda lease: resumed.append(lease)
    controller._open_input_stream = lambda: (_ for _ in ()).throw(
        OSError("fixture microphone unavailable")
    )
    controller._restore_meeting_island = lambda: None
    controller._set_state = lambda _state: None
    controller._idle = lambda: None
    controller.busy = True

    controller._safe_start()

    writer = controller._dictation_capture_writer
    leaked = bool(writer and writer.is_alive()) or bool(list(tmp_path.iterdir()))
    if writer and writer.is_alive():
        controller._dictation_capture_queue.put(None)
        writer.join(2.0)
    session = controller._dictation_session
    if session is not None and session.path.exists():
        session.discard_unfinalized()

    assert leaked is False
    assert controller._dictation_session is None
    assert resumed == ["dictation"]


def test_writer_failure_retries_owned_audio_then_stops_cleanly(tmp_path):
    failed = threading.Event()
    stopped = threading.Event()
    injected = {"done": False}

    def fail_after_reservation(point):
        if point == "after:segment_reserved" and not injected["done"]:
            injected["done"] = True
            failed.set()
            raise OSError("fixture transient storage failure after reservation")

    controller = _controller(tmp_path)
    controller._process = lambda *_args, **_kwargs: stopped.set()
    controller.start_recording()
    controller._dictation_session.fault_injector = fail_after_reservation
    block = np.full((4, 1), 0.1, dtype=np.float32)
    controller._audio_cb(block, len(block), None, None)

    assert failed.wait(1.0)
    clean_stop = stopped.wait(1.0)
    if controller.recording:
        controller.recording = False

    assert clean_stop is True
    assert controller._dictation_capture_error is None
    manifest = controller._dictation_session.read_manifest()
    assert manifest["next_sample"] == 4


def test_stop_drain_reconciles_writer_failure_raised_by_final_flush(tmp_path):
    failed = threading.Event()
    injected = {"done": False}

    def fail_after_reservation(point):
        if point == "after:segment_reserved" and not injected["done"]:
            injected["done"] = True
            failed.set()
            raise OSError("fixture final-flush failure after reservation")

    controller = _controller(tmp_path)
    controller._dictation_segment_max_samples = 8
    controller._process = lambda *_args, **_kwargs: None
    controller.start_recording()
    controller._dictation_session.fault_injector = fail_after_reservation
    block = np.full((4, 1), 0.1, dtype=np.float32)
    controller._audio_cb(block, len(block), None, None)

    controller.stop_recording()

    assert failed.is_set()
    assert controller._dictation_capture_error is None
    manifest = controller._dictation_session.read_manifest()
    assert manifest["next_sample"] == 4
    assert manifest["segments"][0]["state"] == "sealed"


def test_stop_drain_does_not_duplicate_post_seal_failure(tmp_path):
    injected = {"done": False}

    def fail_after_seal(point):
        if point == "after:segment_sealed" and not injected["done"]:
            injected["done"] = True
            raise OSError("fixture final-flush failure after seal")

    controller = _controller(tmp_path)
    controller._dictation_segment_max_samples = 8
    controller._process = lambda *_args, **_kwargs: None
    controller.start_recording()
    controller._dictation_session.fault_injector = fail_after_seal
    block = np.full((4, 1), 0.1, dtype=np.float32)
    controller._audio_cb(block, len(block), None, None)

    controller.stop_recording()

    manifest = controller._dictation_session.read_manifest()
    assert manifest["next_sample"] == 4
    assert [segment["sample_count"] for segment in manifest["segments"]] == [4]


def test_versioned_corpus_measures_real_controller_cold_and_warm_traces(
        tmp_path):
    class StepClock:
        def __init__(self, step):
            self.value = 0.0
            self.step = step

        def __call__(self):
            current = self.value
            self.value += self.step
            return current

    records = []
    for label, resident, step in (
            ("warm", True, 0.001), ("cold", False, 0.002)):
        trace_path = tmp_path / f"{label}.jsonl"
        clock = StepClock(step)
        controller = _controller(tmp_path / label)
        controller.model = object() if resident else None
        controller._cloud_transcription_on = (
            lambda _route=None, cold=not resident: cold
        )
        controller._dictation_trace_sink = dictation_trace.DictationTraceSink(
            trace_path,
            enabled=True,
            clock=clock,
            wall_clock=lambda: 0.0,
            resource_sampler=lambda: {"cpu_ms": 0.0, "threads": 1},
        )
        controller._trace_begin_dictation = types.MethodType(
            mumble.Mumble._trace_begin_dictation, controller
        )
        controller._trace_mark = types.MethodType(
            mumble.Mumble._trace_mark, controller
        )
        controller._trace_mark_once = types.MethodType(
            mumble.Mumble._trace_mark_once, controller
        )
        controller._trace_finish = types.MethodType(
            mumble.Mumble._trace_finish, controller
        )

        def transcribe(_audio, want_words=False, target=controller):
            target._trace_mark("transfer_started", route="fixture")
            target._trace_mark("inference_started", route="fixture")
            target._trace_mark("inference_finished", route="fixture")
            target._trace_mark("transfer_finished", route="fixture")
            return "measured words"

        def process(*_args, target=controller):
            target._trace_mark("formatting_started", mode="text")
            target._trace_mark("formatting_ready", mode="text")
            target._trace_mark("paste_lock_acquired")
            target._trace_mark("clipboard_ready")
            target._trace_mark("insertion_started")
            target._trace_mark("insertion_finished")
            target._trace_mark("paste_finished", success=True)
            target._trace_finish("pasted", success=True)

        controller._transcribe = transcribe
        controller._process = process
        controller.start_recording()
        block = np.full((4, 1), 0.1, dtype=np.float32)
        controller._audio_cb(block, len(block), None, None)
        controller.stop_recording()
        records.extend(dictation_trace.read_traces(trace_path))

    summary = dictation_trace.summarize_traces(
        records, corpus_version="issue16-controller-v1"
    )

    assert summary["lifecycles"] == {"cold": 1, "warm": 1}
    warm = summary["by_lifecycle"]["warm"]["activation_to_paste"]
    cold = summary["by_lifecycle"]["cold"]["activation_to_paste"]
    assert warm["count"] == cold["count"] == 1
    assert 0 < warm["p95_ms"] < cold["p95_ms"]
