#!/usr/bin/env python3
"""Behaviour contract for Mumble's privacy-safe dictation trace."""

import json
import os
import tempfile
import threading

from dictation_trace import DictationTraceSink, export_traces, read_traces
from insertion import InsertionOperationExpired, InsertionRequest, InsertionTransaction


class Clock:
    def __init__(self):
        self.value = 10.0

    def __call__(self):
        return self.value

    def advance_ms(self, milliseconds):
        self.value += milliseconds / 1000.0


def check(name, condition):
    print("  [{}] {}".format("ok  " if condition else "FAIL", name))
    if not condition:
        raise AssertionError(name)


def trace_contract():
    with tempfile.TemporaryDirectory(prefix="mumble_trace_") as temp_dir:
        trace_path = os.path.join(temp_dir, "dictation-traces.jsonl")
        clock = Clock()
        sink = DictationTraceSink(
            trace_path,
            enabled=True,
            clock=clock,
            wall_clock=lambda: 1_785_000_000.0,
            resource_sampler=lambda: {
                "rss_mb": 321.25,
                "cpu_ms": 88.0,
                "threads": 7,
            },
        )

        session = sink.start({
            "route": "local",
            "model": "small.en",
            "device": "cpu",
            "compute_type": "int8",
            "model_resident": True,
            "partial_contract": "stable_chunks_only",
            "transcript": "PRIVATE WORDS MUST NEVER BE STORED",
        })
        session.mark("activation", state="requested")
        clock.advance_ms(75)
        session.mark("first_audio", audio_state="recording")
        clock.advance_ms(425)
        session.mark(
            "stable_partial_ready",
            stream_chunks=1,
            text="PRIVATE PARTIAL MUST NEVER BE STORED",
        )
        clock.advance_ms(500)
        record = session.finish("pasted", success=True)

        rows = read_traces(trace_path)
        check("one finished session is readable", len(rows) == 1)
        check("finish returns the persisted record", rows[0] == record)
        check("schema is explicit and versioned", rows[0]["schema"] == "mumble.dictation-trace.v1")
        check("events retain lifecycle order",
              [event["name"] for event in rows[0]["events"]]
              == ["activation", "first_audio", "stable_partial_ready", "finished"])
        check("monotonic event time is measured from activation",
              [event["elapsed_ms"] for event in rows[0]["events"]]
              == [0.0, 75.0, 500.0, 1000.0])
        check("resource samples are numeric and local",
              rows[0]["events"][0]["resources"]["rss_mb"] == 321.25)
        serialized = json.dumps(rows[0])
        check("transcript content is rejected", "PRIVATE WORDS" not in serialized)
        check("partial content is rejected", "PRIVATE PARTIAL" not in serialized)
        check("unknown content-bearing fields are rejected",
              "transcript" not in rows[0]["context"]
              and "text" not in rows[0]["events"][2])

        export_path = os.path.join(temp_dir, "export", "trace-export.json")
        exported = export_traces(trace_path, export_path)
        check("trace can be exported without changing the source", exported == 1)
        check("export is a standalone JSON array",
              json.load(open(export_path, encoding="utf-8")) == rows)

        disabled_path = os.path.join(temp_dir, "disabled.jsonl")
        disabled = DictationTraceSink(disabled_path, enabled=False)
        check("disabled tracing creates no session", disabled.start({}) is None)
        check("disabled tracing creates no file", not os.path.exists(disabled_path))


def controller_capture_contract():
    import numpy as np
    import mumble

    class Settings:
        def get(self, key, default=None):
            values = {
                "resource_saver": True,
                "min_seconds": 0.3,
                "model": "small.en",
                "transcription_mode": "local",
            }
            return values.get(key, default)

    class Stream:
        def __init__(self):
            self.started = self.stopped = self.closed = False

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

        def close(self):
            self.closed = True

    class Island:
        def set_state(self, _state):
            pass

        def set_armed(self, _armed):
            pass

        def set_level(self, _level):
            pass

    with tempfile.TemporaryDirectory(prefix="mumble_controller_trace_") as temp_dir:
        trace_path = os.path.join(temp_dir, "dictation-traces.jsonl")
        sink = DictationTraceSink(trace_path, enabled=True)
        controller = mumble.Mumble.__new__(mumble.Mumble)
        controller.settings = Settings()
        controller.paused = False
        controller.model = object()
        controller.busy = True
        controller.recording = False
        controller.meeting_recording = False
        controller.island = Island()
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
        controller._open_input_stream = Stream
        controller._restore_meeting_island = lambda: None
        controller._dictation_trace_sink = sink
        controller._dictation_trace_session = None
        controller._dictation_inference_ordinal = 0

        controller.start_recording()
        controller._audio_cb(
            np.full((160, 1), 0.05, dtype=np.float32), 160, None, None
        )
        controller.stop_recording()

        rows = read_traces(trace_path)
        check("public record lifecycle produces one completed trace", len(rows) == 1)
        names = [event["name"] for event in rows[0]["events"]]
        check("capture trace covers activation through closed audio",
              names == [
                  "activation", "island_render", "audio_opening",
                  "audio_recording", "first_audio", "audio_stopped",
                  "audio_closed", "stream_drain_finished", "finished",
              ])
        check("short dictation outcome is explicit",
              rows[0]["events"][-1]["outcome"] == "too_short")
        check("trace identifies local stable-chunk architecture",
              rows[0]["context"]["route"] == "local"
              and rows[0]["context"]["partial_contract"] == "stable_chunks_only")


def completed_dictation_contract():
    import types

    import numpy as np
    import mumble

    class Settings:
        def get(self, key, default=None):
            values = {
                "resource_saver": True,
                "min_seconds": 0.3,
                "model": "small.en",
                "transcription_mode": "local",
                "pro_mode": False,
                "local_only_mode": True,
                "english_only": True,
                "foreign_mode": False,
                "foreign_languages": [],
                "format_enabled": True,
                "instant_text": True,
                "vocabulary": {},
                "vocabulary_terms": [],
                "confidence_gating": False,
                "language": "en",
            }
            return values.get(key, default)

    class Stream:
        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    class Island:
        def set_state(self, _state):
            pass

        def set_armed(self, _armed):
            pass

        def set_level(self, _level):
            pass

        def set_building(self, *_args, **_kwargs):
            pass

        def flash(self, *_args, **_kwargs):
            pass

    class History:
        def add(self, *_args, **_kwargs):
            return {"words": 2}

    class Stats:
        def record(self, *_args, **_kwargs):
            return True

    class Model:
        def transcribe(self, *_args, **_kwargs):
            return ([types.SimpleNamespace(
                text="hello world", avg_logprob=-0.1, start=0.0, end=0.5,
            )], None)

    class Clipboard:
        def pause(self):
            pass

        def mark_own(self, _text):
            pass

        def resume(self, skip_current=False):
            pass

    with tempfile.TemporaryDirectory(prefix="mumble_pipeline_trace_") as temp_dir:
        trace_path = os.path.join(temp_dir, "dictation-traces.jsonl")
        controller = mumble.Mumble.__new__(mumble.Mumble)
        controller.settings = Settings()
        controller.paused = False
        controller.model = Model()
        controller.busy = True
        controller.recording = False
        controller.meeting_recording = False
        controller.island = Island()
        controller.window = None
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
        controller._open_input_stream = Stream
        controller._restore_meeting_island = lambda: None
        controller._tx_lock = threading.Lock()
        controller._paste_lock = threading.Lock()
        controller._boost_priority = lambda: None
        controller._restore_priority = lambda _old: None
        controller._stt_model = "small.en"
        controller._stt_device = "cpu"
        controller._stt_compute = "int8"
        controller._q_acc = []
        controller.history = History()
        controller.stat_store = Stats()
        controller.clipboard = Clipboard()
        controller._send_webui_async = lambda *_args, **_kwargs: None
        controller._remember_correction_candidate = lambda *_args, **_kwargs: None
        controller._maybe_island_tip = lambda *_args, **_kwargs: None
        controller.refresh_tray_menu = lambda: None
        controller._notify = lambda *_args, **_kwargs: None
        controller._dictation_trace_sink = DictationTraceSink(trace_path, enabled=True)
        controller._dictation_trace_session = None
        controller._dictation_inference_ordinal = 0
        controller._focused_editable = lambda: True

        class Target:
            def current(self):
                return object()

        class Transaction:
            def insert(self, request):
                controller._insertion_trace(
                    "clipboard_ready", operation_id=request.operation_id,
                    content_kind=request.content_kind,
                )
                controller._insertion_trace(
                    "paste_sent", operation_id=request.operation_id,
                    requested=4, accepted=4,
                )
                return mumble.InsertionResult(
                    operation_id=request.operation_id,
                    source=request.source,
                    outcome=mumble.InsertionOutcome.CONFIRMED,
                    reason="fixture confirmed one insertion",
                    message="Pasted.",
                    send_count=1,
                    native_requested=4,
                    native_accepted=4,
                )

        controller._insertion_target = Target()
        controller._insertion_transaction = Transaction()

        clipboard_state = {"text": "original clipboard"}
        old_paste = mumble.pyperclip.paste
        old_copy = mumble.pyperclip.copy
        old_send = mumble.keyboard.send
        old_sleep = mumble.time.sleep
        mumble.pyperclip.paste = lambda: clipboard_state["text"]
        mumble.pyperclip.copy = lambda text: clipboard_state.__setitem__("text", text)
        mumble.keyboard.send = lambda _keys: None
        mumble.time.sleep = lambda _seconds: None
        try:
            controller.start_recording()
            controller._audio_cb(
                np.full((8000, 1), 0.05, dtype=np.float32), 8000, None, None
            )
            controller.stop_recording()
        finally:
            mumble.pyperclip.paste = old_paste
            mumble.pyperclip.copy = old_copy
            mumble.keyboard.send = old_send
            mumble.time.sleep = old_sleep

        rows = read_traces(trace_path)
        check("completed public lifecycle persists a trace", len(rows) == 1)
        names = [event["name"] for event in rows[0]["events"]]
        required = {
            "stream_drain_finished", "final_transcript_ready",
            "transcription_lock_acquired", "inference_started",
            "inference_finished",
            "formatting_started", "formatting_ready",
            "persistence_started", "persistence_finished",
            "paste_lock_acquired", "clipboard_ready", "paste_sent",
            "paste_finished", "finished",
        }
        check("completed trace covers every post-capture boundary",
              required.issubset(set(names)))
        local_start = next(
            event for event in rows[0]["events"]
            if event["name"] == "inference_started"
        )
        check("actual local inference route is explicit",
              local_start.get("route") == "local")
        check("finished event reports successful insertion",
              rows[0]["events"][-1]["outcome"] == "pasted"
              and rows[0]["events"][-1]["success"] is True)
        serialized = json.dumps(rows[0])
        check("final transcript never enters performance evidence",
              "hello world" not in serialized)


def cloud_inference_contract():
    import numpy as np
    import mumble

    class Settings:
        def get(self, key, default=None):
            values = {
                "pro_mode": True,
                "local_only_mode": False,
                "transcription_mode": "cloud",
                "cloud_transcription_provider": "groq",
                "groq_api_key": "FROZEN_TRACE_KEY",
                "language": "en",
            }
            return values.get(key, default)

    with tempfile.TemporaryDirectory(prefix="mumble_cloud_trace_") as temp_dir:
        trace_path = os.path.join(temp_dir, "dictation-traces.jsonl")
        controller = mumble.Mumble.__new__(mumble.Mumble)
        controller.settings = Settings()
        controller._dictation_trace_sink = DictationTraceSink(trace_path, enabled=True)
        controller._dictation_trace_session = controller._dictation_trace_sink.start({
            "route": "cloud",
        })
        controller._dictation_trace_seen = set()
        controller._dictation_inference_ordinal = 0
        controller._cloud_stt_failed = False
        controller._notify = lambda *_args, **_kwargs: None

        old_transcribe = mumble.transcription.transcribe
        mumble.transcription.transcribe = lambda *_args, **_kwargs: "private cloud words"
        try:
            result = controller._cloud_transcribe(
                np.zeros(1600, dtype=np.float32)
            )
            controller._trace_finish("test_complete", success=True)
        finally:
            mumble.transcription.transcribe = old_transcribe

        check("cloud transcription result still reaches its caller",
              result == "private cloud words")
        rows = read_traces(trace_path)
        events = rows[0]["events"]
        started = next(event for event in events if event["name"] == "inference_started")
        finished = next(event for event in events if event["name"] == "inference_finished")
        check("actual cloud request route and provider are measurable",
              started.get("route") == "cloud"
              and started.get("provider") == "groq")
        check("cloud request completion is measurable",
              finished.get("route") == "cloud"
              and finished.get("success") is True)
        check("cloud words never enter performance evidence",
              "private cloud words" not in json.dumps(rows[0]))


def insertion_only_trace_contract():
    with tempfile.TemporaryDirectory(prefix="mumble_insertion_trace_") as temp_dir:
        trace_path = os.path.join(temp_dir, "dictation-traces.jsonl")
        sink = DictationTraceSink(trace_path, enabled=True)
        valid_operation_id = "a" * 32
        unsafe_operation_id = "PRIVATE_TRANSCRIPT_CONTENT"
        session = sink.start_insertion({
            "trace_kind": "insertion",
            "operation_id": valid_operation_id,
            "source": "paste_latest",
            "content_kind": "text",
            "text": "PRIVATE TRANSCRIPT",
            "path": r"C:\Private\secret.png",
            "title": "Private window title",
        })
        session.mark(
            "insertion_started",
            operation_id=valid_operation_id,
            requested_count=4,
            target_captured=True,
            editable=True,
            integrity_relation="same",
            transcript="PRIVATE TRANSCRIPT",
            exception="PRIVATE RAW EXCEPTION",
        )
        session.finish(
            "sent_unconfirmed",
            source="paste_latest",
            accepted_count=4,
            send_count=1,
            confirmation="unavailable",
            fallback_reason="confirmation_unavailable",
            cleanup_warning=False,
        )

        rows = read_traces(trace_path)
        check("insertion-only operation is bounded in the strict sink", len(rows) == 1)
        serialized = json.dumps(rows[0])
        check("safe insertion identity and counts survive",
              valid_operation_id in serialized
              and '"accepted_count": 4' in serialized
              and '"send_count": 1' in serialized)
        check("private insertion data has no trace field",
              "PRIVATE" not in serialized and "secret.png" not in serialized)

        unsafe_path = os.path.join(temp_dir, "unsafe-operation.jsonl")
        unsafe_sink = DictationTraceSink(unsafe_path, enabled=True)
        unsafe_session = unsafe_sink.start_insertion({
            "operation_id": unsafe_operation_id,
            "source": "deck_history",
            "content_kind": "text",
        })
        check("unsafe operation cannot create a trace session",
              unsafe_session is None)
        unsafe_serialized = json.dumps(read_traces(unsafe_path))
        check("unsafe operation text cannot enter the strict trace sink",
              unsafe_operation_id not in unsafe_serialized)

        import mumble
        controller_path = os.path.join(temp_dir, "controller-insertions.jsonl")
        controller = mumble.Mumble.__new__(mumble.Mumble)
        controller._dictation_trace_session = None
        controller._dictation_trace_sink = DictationTraceSink(
            controller_path, enabled=True)
        controller._insertion_trace_sessions = {}
        controller._insertion_trace_lock = threading.Lock()
        controller_operation_id = "b" * 32
        controller._insertion_trace(
            "insertion_started", operation_id=controller_operation_id,
            source="deck_history", content_kind="text",
            target_captured=True)
        controller._insertion_trace(
            "insertion_finished", operation_id=controller_operation_id,
            source="deck_history", outcome="not_sent", send_count=0,
            fallback_reason="target_changed", cleanup_warning=False)
        controller._insertion_trace(
            "insertion_finished", operation_id=controller_operation_id,
            source="deck_history", outcome="not_sent", send_count=0,
            fallback_reason="target_changed", cleanup_warning=False)
        controller_rows = read_traces(controller_path)
        check("controller creates a bounded record without dictation",
              len(controller_rows) == 1
              and controller_rows[0]["context"]["trace_kind"] == "insertion")
        check("duplicate controller terminal trace is idempotent",
              sum(event.get("name") == "insertion_finished"
                  for event in controller_rows[0]["events"]) == 1)
        controller_serialized = json.dumps(controller_rows[0])
        check("valid opaque identity correlates controller trace events",
              controller_serialized.count(controller_operation_id) >= 2)

        unsafe_controller_path = os.path.join(
            temp_dir, "unsafe-controller-operation.jsonl")
        controller._dictation_trace_sink = DictationTraceSink(
            unsafe_controller_path, enabled=True)
        controller._insertion_trace(
            "insertion_started", operation_id=unsafe_operation_id,
            source="deck_history", content_kind="text")
        controller._insertion_trace(
            "insertion_finished", operation_id=unsafe_operation_id,
            source="deck_history", outcome="not_sent", send_count=0)
        check("unsafe operation text cannot enter controller trace material",
              unsafe_operation_id not in json.dumps(
                  read_traces(unsafe_controller_path)))
        check("unsafe operation cannot enter controller trace registry",
              unsafe_operation_id not in controller._insertion_trace_sessions)
        check("controller boundary rejects unsafe operation identifiers",
              controller._validated_external_operation_id(
                  unsafe_operation_id) is None)
        check("controller boundary accepts runtime opaque identifiers",
              controller._validated_external_operation_id(
                  valid_operation_id) == valid_operation_id)

        failure_path = os.path.join(temp_dir, "terminal-failures.jsonl")
        failure_controller = mumble.Mumble.__new__(mumble.Mumble)
        failure_controller._dictation_trace_session = None
        failure_controller._dictation_trace_sink = DictationTraceSink(
            failure_path, enabled=True)
        failure_controller._insertion_trace_sessions = {}
        failure_controller._insertion_trace_lock = threading.Lock()
        transaction = InsertionTransaction(
            None, None, None, trace=failure_controller._insertion_trace)
        sentinel = "PRIVATE_CALLER_TEXT_AND_EXCEPTION"

        class OriginalFailure(RuntimeError):
            pass

        def fail_terminal(_request):
            raise OriginalFailure(sentinel)

        transaction._execute = fail_terminal
        for index in range(300):
            request = InsertionRequest(
                operation_id="{:032x}".format(index + 50000),
                source="deck_history",
                content_kind="text",
                text=sentinel,
                activation_target=None,
            )
            try:
                transaction.insert(request)
            except OriginalFailure:
                pass
            else:
                raise AssertionError("terminal exception was not preserved")
            try:
                transaction.insert(request)
            except InsertionOperationExpired:
                pass
            else:
                raise AssertionError("failed operation became reusable")

        failure_rows = read_traces(failure_path)
        failure_serialized = json.dumps(failure_rows)
        check("300 terminal failures leave no controller trace sessions",
              failure_controller._insertion_trace_sessions == {})
        check("300 terminal failures each persist one terminal trace",
              len(failure_rows) == 300
              and all(sum(event.get("name") == "insertion_finished"
                          for event in row["events"]) == 1
                      for row in failure_rows))
        check("terminal failure traces remain content-free",
              sentinel not in failure_serialized)


if __name__ == "__main__":
    print("\n== privacy-safe exportable dictation trace ==")
    trace_contract()
    print("\n== controller capture lifecycle trace ==")
    controller_capture_contract()
    print("\n== completed dictation lifecycle trace ==")
    completed_dictation_contract()
    print("\n== cloud inference boundary trace ==")
    cloud_inference_contract()
    print("\n== insertion-only trace boundary ==")
    insertion_only_trace_contract()
    print("\nALL GREEN")
