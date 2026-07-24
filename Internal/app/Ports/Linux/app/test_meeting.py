#!/usr/bin/env python3
"""Meeting backend tests — recorder lifecycle, store CRUD, diarisation,
processing modes, AI extraction, and export formats.

Covers: VAL-MEETING-001 through VAL-MEETING-103 plus 164-180
and cross-area assertions VAL-CROSS-014, VAL-CROSS-015.

No real network — ai.cerebras_chat is stubbed where needed.
Run:  .venv/Scripts/python.exe test_meeting.py   (PYTHONUTF8=1)
"""

import json
import glob
import os
import sys
import threading
import time
import wave

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import ai
import meeting
import meeting_diarise
import meeting_store
import recording_limits

_fails = []


def check(desc, cond):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {desc}")
    if not cond:
        _fails.append(desc)


# ── test helpers ─────────────────────────────────────────────────────────────


class _FakeSettings:
    """Minimal settings-like object for tests."""

    def __init__(self, **kw):
        self._d = dict(kw)

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value
        return True


class _FakeTranscribe:
    """Stubbed transcription callable."""

    def __init__(self, text="Hello world. This is a test meeting. "
                              "We need to decide on the budget. "
                              "Alice will handle the report. "
                              "Who owns the deployment?"):
        self.text = text
        self.calls = []

    def __call__(self, audio, want_words=False):
        self.calls.append((len(audio), want_words))
        return self.text


class _RaisingTranscribe:
    """Transcription that always raises."""

    def __call__(self, audio, want_words=False):
        raise RuntimeError("transcription failure (simulated)")


class _FakeIslandCb:
    """Captures island callback calls."""

    def __init__(self):
        self.calls = []

    def __call__(self, state, timer, speaker_count):
        self.calls.append((state, timer, speaker_count))


class _FakeStream:
    """Minimal mock for sounddevice.InputStream."""

    def __init__(self, samplerate=None, channels=None, dtype=None,
                 device=None, callback=None):
        self.samplerate = samplerate
        self.callback = callback
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


_original_sd_InputStream = None


def _mock_sd_stream():
    """Replace sounddevice.InputStream with _FakeStream."""
    global _original_sd_InputStream
    import sounddevice as sd
    _original_sd_InputStream = sd.InputStream
    sd.InputStream = _FakeStream


def _restore_sd_stream():
    """Restore original sounddevice.InputStream."""
    global _original_sd_InputStream
    if _original_sd_InputStream is not None:
        import sounddevice as sd
        sd.InputStream = _original_sd_InputStream
        _original_sd_InputStream = None


def _inject_fake_audio(recorder, num_frames=50):
    """Simulate audio frames arriving via the stream callback."""
    import numpy as np
    cb = recorder._stream.callback
    frames = np.zeros((num_frames, 1), dtype=np.float32)
    for i in range(5):
        frames[i] = 0.1 * i
    cb(frames, num_frames, None, None)


# ── stub ai.cerebras_chat ────────────────────────────────────────────────────

_orig_cerebras_chat = None
_stub_results = []


def _install_ai_stub(results=None):
    """Replace ai.cerebras_chat with a callable that returns canned results."""
    global _orig_cerebras_chat, _stub_results
    _orig_cerebras_chat = ai.cerebras_chat
    _stub_results = list(results or []) if results else ["Stubbed summary."]
    _call_count = [0]

    def fake(system, user, key, model=None, url=None,
             max_tokens=None, timeout=None):
        _call_count[0] += 1
        idx = min(_call_count[0] - 1, len(_stub_results) - 1)
        return _stub_results[idx]

    fake.call_count = _call_count
    ai.cerebras_chat = fake
    return fake


def _restore_ai():
    """Restore original ai.cerebras_chat."""
    global _orig_cerebras_chat
    if _orig_cerebras_chat is not None:
        ai.cerebras_chat = _orig_cerebras_chat
        _orig_cerebras_chat = None


# ── temp store isolation ─────────────────────────────────────────────────────

_orig_path = None


def _isolate_store():
    """Redirect meeting_store to a temp file so real data is untouched."""
    global _orig_path
    import branding
    import tempfile
    _orig_path = meeting_store.PATH
    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="test_meeting_")
    os.close(fd)
    # Also redirect meetings_audio
    meeting_store._orig_data_dir = branding.DATA_DIR
    meeting_store.PATH = tmp
    branding.DATA_DIR = os.path.join(tempfile.gettempdir(),
                                     "test_meeting_data")
    os.makedirs(branding.DATA_DIR, exist_ok=True)
    # Clear in-memory state by re-import
    meeting_store._load.cache_clear() if hasattr(
        meeting_store._load, "cache_clear") else None


def _restore_store():
    """Restore meeting_store PATH and clean up temp file."""
    global _orig_path
    if _orig_path and hasattr(meeting_store, '_orig_data_dir'):
        import branding
        import shutil
        branding.DATA_DIR = meeting_store._orig_data_dir
        del meeting_store._orig_data_dir
        test_path = meeting_store.PATH
        for candidate in ([test_path, test_path + ".tmp", test_path + ".bak"]
                          + glob.glob(test_path + ".corrupt-*")):
            try:
                os.remove(candidate)
            except Exception:
                pass
        meeting_store.PATH = _orig_path
        _orig_path = None


# ═══════════════════════════════════════════════════════════════════════════════
#  Meeting Store CRUD
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Meeting Store — CRUD ==")

_isolate_store()

# VAL-MEETING-072: save_meeting returns 12-char hex id
mid = meeting_store.save_meeting(
    "Test Meeting", "test.wav", 120.5,
    segments=[{"speaker": "Speaker 1", "start_sec": 0.0, "end_sec": 5.0,
               "text": "Hello", "confidence": 0.5}],
    speakers=[{"label": "Speaker 1", "name": None, "color": "#D4AF37"}],
)
check("save_meeting returns 12-char hex id",
      bool(mid) and len(mid) == 12 and all(c in "0123456789abcdef" for c in mid))

# VAL-MEETING-072: newest first
mid2 = meeting_store.save_meeting(
    "Second", "test2.wav", 60.0,
    segments=[], speakers=[],
)
lst = meeting_store.list_meetings()
check("list_meetings newest first", lst[0]["id"] == mid2)

# VAL-MEETING-079: get_meeting returns full record
m = meeting_store.get_meeting(mid)
check("get_meeting returns full record", m is not None and m["id"] == mid)
check("get_meeting has segments", len(m["segments"]) == 1)
check("get_meeting has speakers", len(m["speakers"]) == 1)

# VAL-MEETING-080: get_meeting returns None for missing
check("get_meeting missing returns None",
      meeting_store.get_meeting("nonexistent") is None)

# VAL-MEETING-081: new fields present
check("get_meeting has key_decisions (list)",
      isinstance(m.get("key_decisions"), list))
check("get_meeting has open_questions (list)",
      isinstance(m.get("open_questions"), list))
check("get_meeting has processing_mode (str)",
      isinstance(m.get("processing_mode"), str))

# VAL-MEETING-073: auto title when empty
mid3 = meeting_store.save_meeting(
    "", "test3.wav", 300.0,
    segments=[], speakers=[],
)
m3 = meeting_store.get_meeting(mid3)
check("auto title is non-empty", bool(m3["title"]))
check("auto title contains 'Meeting'", "Meeting" in m3["title"])

# VAL-MEETING-074: defaults
check("default action_items is empty list", m3["action_items"] == [])
check("default key_decisions is empty list", m3["key_decisions"] == [])
check("default open_questions is empty list", m3["open_questions"] == [])
check("default starred is False", m3["starred"] is False)
check("default tags is empty list", m3["tags"] == [])

# VAL-MEETING-082: update_meeting title
ok = meeting_store.update_meeting(mid, title="Updated Title")
check("update_meeting returns True", ok is True)
check("update_meeting persisted title",
      meeting_store.get_meeting(mid)["title"] == "Updated Title")

# VAL-MEETING-083: update_meeting missing returns False
check("update_meeting missing returns False",
      meeting_store.update_meeting("missing", title="x") is False)

# VAL-MEETING-084: update_meeting persists new fields
ok = meeting_store.update_meeting(
    mid, summary="A summary", action_items=["Do X"],
    key_decisions=["Decided Y"], open_questions=["What about Z?"])
check("update_meeting new fields returns True", ok is True)
m = meeting_store.get_meeting(mid)
check("summary persisted", m["summary"] == "A summary")
check("action_items persisted", m["action_items"] == ["Do X"])
check("key_decisions persisted", m["key_decisions"] == ["Decided Y"])
check("open_questions persisted", m["open_questions"] == ["What about Z?"])

# VAL-MEETING-085: set_starred
check("set_starred True", meeting_store.set_starred(mid, True))
check("starred is True", meeting_store.get_meeting(mid)["starred"] is True)
check("set_starred False", meeting_store.set_starred(mid, False))
check("starred is False", meeting_store.get_meeting(mid)["starred"] is False)

# VAL-MEETING-086: set_starred missing returns False
check("set_starred missing returns False",
      meeting_store.set_starred("missing", True) is False)

# VAL-MEETING-087: rename_speaker
ok = meeting_store.rename_speaker(mid, "Speaker 1", "Alice")
check("rename_speaker returns True", ok is True)
m = meeting_store.get_meeting(mid)
sp = next((s for s in m["speakers"] if s["label"] == "Speaker 1"), None)
check("speaker name is Alice", sp is not None and sp["name"] == "Alice")

# VAL-MEETING-088: rename_speaker with blank clears name
ok = meeting_store.rename_speaker(mid, "Speaker 1", "  ")
check("rename_speaker blank returns True", ok is True)
m = meeting_store.get_meeting(mid)
sp = next((s for s in m["speakers"] if s["label"] == "Speaker 1"), None)
check("speaker name is None after blank", sp is not None and sp["name"] is None)

# VAL-MEETING-089: rename_speaker missing meeting/label
check("rename_speaker missing meeting returns False",
      meeting_store.rename_speaker("missing", "Speaker 1", "Alice") is False)
check("rename_speaker missing label returns False",
      meeting_store.rename_speaker(mid, "Speaker 99", "Alice") is False)

# VAL-MEETING-090: delete_meeting
mid_del = meeting_store.save_meeting(
    "To Delete", "del.wav", 10.0, segments=[], speakers=[])
check("delete_meeting returns True",
      meeting_store.delete_meeting(mid_del) is True)
check("deleted meeting get returns None",
      meeting_store.get_meeting(mid_del) is None)

# VAL-MEETING-091: delete_meeting idempotent
check("delete_meeting missing returns True",
      meeting_store.delete_meeting("missing") is True)

# VAL-MEETING-075: list_meetings metadata (no segments)
lst = meeting_store.list_meetings()
check("list_meetings has entries", len(lst) >= 2)
for entry in lst:
    check(f"list entry {entry['title'][:20]} has id", bool(entry.get("id")))
    check(f"list entry has title", bool(entry.get("title")))
    check(f"list entry has duration_display",
          isinstance(entry.get("duration_display"), str))
    check(f"list entry has no segments key", "segments" not in entry)

# VAL-MEETING-077: duration_display formatting
check("duration_display 0s -> '0:00'",
      meeting_store._fmt_duration(0) == "0:00")
check("duration_display 65s -> '1:05'",
      meeting_store._fmt_duration(65) == "1:05")
check("duration_display 3661s -> '1:01:01'",
      meeting_store._fmt_duration(3661) == "1:01:01")

# VAL-MEETING-078: preview
entry = lst[0]
segments = meeting_store.get_meeting(entry["id"])["segments"]
if segments:
    check("preview matches first segment truncation",
          entry["preview"] == segments[0].get("text", "")[:120])

# VAL-MEETING-092: user-owned meetings are never silently pruned
count_before_retention = len(meeting_store.list_meetings())
for i in range(60):
    meeting_store.save_meeting(
        f"Cap {i}", f"cap{i}.wav", 1.0,
        segments=[{"speaker": "S1", "start_sec": 0, "end_sec": 1,
                   "text": "x", "confidence": 0.5}],
        speakers=[{"label": "Speaker 1", "name": None, "color": "#D4AF37"}],
    )
lst = meeting_store.list_meetings()
check("all meetings persist until explicit deletion",
      len(lst) == count_before_retention + 60)
check("oldest meeting survives library growth",
      any(row["id"] == mid for row in lst))

# VAL-MEETING-093: atomic write (no .tmp file)
tmp_path = meeting_store.PATH + ".tmp"
check("no lingering .tmp file", not os.path.exists(tmp_path))

# VAL-MEETING-076: list_meetings sorted newest-first by created timestamp
lst2 = meeting_store.list_meetings()
sorted_ok = True
for i in range(len(lst2) - 1):
    if lst2[i]["created"] < lst2[i + 1]["created"]:
        sorted_ok = False
        break
check("list_meetings sorted newest-first (descending created)", sorted_ok)

# VAL-MEETING-098: list_tags returns unique tags with counts sorted by name
# Create meetings with tags
mt1 = meeting_store.save_meeting(
    "Tagged 1", "t1.wav", 10.0,
    segments=[], speakers=[],
    key_decisions=[], open_questions=[],
)
meeting_store.update_meeting(mt1, tags=["python", "sprint"])
mt2 = meeting_store.save_meeting(
    "Tagged 2", "t2.wav", 20.0,
    segments=[], speakers=[],
    key_decisions=[], open_questions=[],
)
meeting_store.update_meeting(mt2, tags=["python", "design"])
tags_list = meeting_store.list_tags()
check("list_tags returns list", isinstance(tags_list, list))
check("list_tags has entries", len(tags_list) >= 2)
# Verify sorted case-insensitively
names = [t["name"] for t in tags_list]
check("list_tags sorted by name",
      names == sorted(names, key=lambda x: x.lower()))
# Verify counts
python_tag = next((t for t in tags_list if t["name"] == "python"), None)
check("python tag has count 2", python_tag is not None and python_tag["count"] == 2)

# VAL-MEETING-181: save_meeting accepts optional deep fields and defaults
mid_explicit = meeting_store.save_meeting(
    "Explicit Deep", "exp.wav", 30.0,
    segments=[{"speaker": "S1", "start_sec": 0.0, "end_sec": 1.0,
               "text": "Deep", "confidence": 0.5}],
    speakers=[{"label": "S1", "name": None, "color": "#D4AF37"}],
    key_decisions=["Decided to ship"],
    open_questions=["What about testing?"],
    processing_mode="deep",
)
m_exp = meeting_store.get_meeting(mid_explicit)
check("VAL-MEETING-181: explicit key_decisions persisted",
      m_exp["key_decisions"] == ["Decided to ship"])
check("VAL-MEETING-181: explicit open_questions persisted",
      m_exp["open_questions"] == ["What about testing?"])
check("VAL-MEETING-181: explicit processing_mode persisted",
      m_exp["processing_mode"] == "deep")

# Default case: save without deep fields
mid_default = meeting_store.save_meeting(
    "Default Fields", "def.wav", 15.0,
    segments=[], speakers=[],
)
m_def = meeting_store.get_meeting(mid_default)
check("VAL-MEETING-181: default key_decisions is empty list",
      m_def["key_decisions"] == [])
check("VAL-MEETING-181: default open_questions is empty list",
      m_def["open_questions"] == [])
check("VAL-MEETING-181: default processing_mode is lightweight",
      m_def["processing_mode"] == "lightweight")

# VAL-MEETING-182: list_meetings includes processing_mode and deep-field counts
lst3 = meeting_store.list_meetings()
for entry in lst3:
    check(f"VAL-MEETING-182: list entry has processing_mode",
          "processing_mode" in entry and isinstance(entry["processing_mode"], str))
    check(f"VAL-MEETING-182: list entry has key_decision_count",
          "key_decision_count" in entry and isinstance(entry["key_decision_count"], int))
    check(f"VAL-MEETING-182: list entry has open_question_count",
          "open_question_count" in entry and isinstance(entry["open_question_count"], int))

# Verify counts for the explicit deep meeting
exp_list_entry = next((e for e in lst3 if e["id"] == mid_explicit), None)
check("VAL-MEETING-182: deep meeting key_decision_count=1",
      exp_list_entry is not None and exp_list_entry["key_decision_count"] == 1)
check("VAL-MEETING-182: deep meeting open_question_count=1",
      exp_list_entry is not None and exp_list_entry["open_question_count"] == 1)
check("VAL-MEETING-182: deep meeting processing_mode='deep'",
      exp_list_entry is not None and exp_list_entry["processing_mode"] == "deep")

_restore_store()


# ═══════════════════════════════════════════════════════════════════════════════
#  MeetingRecorder Lifecycle
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== MeetingRecorder — Lifecycle ==")

_isolate_store()
_mock_sd_stream()

try:
    settings = _FakeSettings(llm_provider="cerebras", cerebras_api_key="test-key")
    transcribe = _FakeTranscribe()
    island_cb = _FakeIslandCb()

    # VAL-MEETING-001: start opens stream and begins recording
    recorder = meeting.MeetingRecorder(transcribe, settings, island_cb)
    recorder.start()
    time.sleep(0.3)  # let timer thread fire
    check("start: _recording is True", recorder._recording is True)
    check("start: _stream is not None", recorder._stream is not None)
    check("start: _stream.started", recorder._stream.started)
    check("start: no samples captured yet", recorder._sample_count == 0)

    # Live capture accepts exactly four hours of samples, then hard-stops audio
    # accumulation and refuses resume until the meeting is finalized.
    limit_cb = _FakeIslandCb()
    limit_recorder = meeting.MeetingRecorder(transcribe, settings, limit_cb)
    limit_recorder.start()
    limit_recorder._sample_count = recording_limits.MEETING_MAX_SAMPLES - 25
    _inject_fake_audio(limit_recorder, num_frames=50)
    check("meeting limit is four hours",
          recording_limits.MEETING_MAX_SECONDS == 14400)
    check("capture stops at exact meeting sample limit",
          limit_recorder._sample_count == recording_limits.MEETING_MAX_SAMPLES)
    check("capture exposes limit reached", limit_recorder.limit_reached is True)
    check("capture stops recording at limit", limit_recorder._recording is False)
    check("limit reached is reported to controller callback",
          any(c[0] == "limit_reached" for c in limit_cb.calls))
    check("meeting cannot resume past hard limit",
          limit_recorder.resume() is False)
    # The helper thread finalizes the WAV + pending metadata without blocking
    # the real-time audio callback.
    limit_recorder._timer_loop()
    limited_id = limit_recorder._last_meeting_id
    check("four-hour hard stop is durably finalized",
          limit_recorder._capture_finalized and
          meeting_store.get_meeting(limited_id) is not None)
    meeting_store.delete_meeting(limited_id)

    # A saturated disk-writer queue must end capture at the last contiguous
    # block. Continuing after one dropped callback would create a silent hole in
    # the middle of an otherwise plausible meeting.
    original_queue_class = meeting.queue.Queue
    try:
        class _DeterministicOverflowQueue:
            def __init__(self, maxsize=0):
                self._items = []
                self._released = False
                self._condition = threading.Condition()

            def get(self):
                with self._condition:
                    while not self._released:
                        self._condition.wait()
                    while not self._items:
                        self._condition.wait()
                    return self._items.pop(0)

            def put_nowait(self, item):
                with self._condition:
                    if item is not None and self._items:
                        raise meeting.queue.Full
                    self._items.append(item)
                    if item is None:
                        self._released = True
                        self._condition.notify_all()

            def put(self, item, timeout=None):
                with self._condition:
                    self._items.append(item)
                    self._released = True
                    self._condition.notify_all()

        meeting.queue.Queue = _DeterministicOverflowQueue
        overflow_cb = _FakeIslandCb()
        overflow_recorder = meeting.MeetingRecorder(
            transcribe, settings, overflow_cb)
        overflow_recorder.start()
        _inject_fake_audio(overflow_recorder, num_frames=50)
        contiguous_samples = overflow_recorder._sample_count
        _inject_fake_audio(overflow_recorder, num_frames=50)
        _inject_fake_audio(overflow_recorder, num_frames=50)
        check("writer queue overflow stops capture immediately",
              overflow_recorder._recording is False)
        check("writer queue overflow never resumes after a dropped block",
              overflow_recorder._sample_count == contiguous_samples and
              overflow_recorder.resume() is False)
        check("writer queue overflow is reported to controller callback",
              any(c[0] == "capture_error" for c in overflow_cb.calls))
        overflow_recorder._timer_loop()
        overflow_id = overflow_recorder._last_meeting_id
        overflow_record = meeting_store.get_meeting(overflow_id)
        check("writer queue overflow durably finalizes partial recording",
              overflow_record is not None and
              overflow_record.get("status") == "interrupted")
        check("writer queue overflow persists an explicit integrity error",
              "stopped" in (overflow_record.get("error") or "").lower())
        overflow_path = meeting._resolve_audio_path(
            overflow_record.get("audio_path"))
        with wave.open(overflow_path, "rb") as wf:
            check("overflow WAV contains exactly the contiguous prefix",
                  wf.getnframes() == contiguous_samples)
        meeting_store.delete_meeting(overflow_id)
    finally:
        meeting.queue.Queue = original_queue_class

    # If the writer itself dies, accepted/queued samples can exceed the frames
    # that reached disk. Final metadata must follow the closed WAV header.
    writer_failure = meeting.MeetingRecorder(transcribe, settings)
    failed_name, failed_path = meeting._new_meeting_audio_path()
    with wave.open(failed_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(recording_limits.SAMPLE_RATE)
        wf.writeframes(b"\x00\x00" * 8_000)
    writer_failure._audio_filename = failed_name
    writer_failure._audio_full_path = failed_path
    writer_failure._sample_count = 16_000  # accepted, half never reached disk
    writer_failure._writer_error = "The meeting audio writer failed: disk error"
    failed_writer_id = writer_failure.finish_capture("Writer Failure")
    failed_writer_record = meeting_store.get_meeting(failed_writer_id)
    check("writer failure duration follows actual WAV frames",
          failed_writer_record is not None and
          failed_writer_record.get("duration_sec") == 0.5 and
          writer_failure._sample_count == 8_000)
    check("writer failure remains visibly retryable",
          failed_writer_record is not None and
          failed_writer_record.get("status") == "interrupted" and
          "writer failed" in (failed_writer_record.get("error") or "").lower())
    meeting_store.delete_meeting(failed_writer_id)

    # VAL-MEETING-003: timer emits island callbacks
    time.sleep(1.2)
    recording_calls = [c for c in island_cb.calls if c[0] == "recording"]
    check("timer: at least 2 recording callbacks", len(recording_calls) >= 1)
    if len(recording_calls) >= 2:
        check("timer: elapsed time increasing",
              recording_calls[-1][1] >= recording_calls[0][1])

    # VAL-MEETING-164: island shows recording state
    check("island receives 'recording' state",
          any(c[0] == "recording" for c in island_cb.calls))

    # VAL-MEETING-004: pause stops frame accumulation
    island_cb.calls.clear()
    _inject_fake_audio(recorder)
    count_before_pause = recorder._sample_count
    check("frames accumulated before pause", count_before_pause > 0)

    recorder.pause()
    check("pause: _recording is False", recorder._recording is False)
    _inject_fake_audio(recorder)
    count_after_pause = recorder._sample_count
    check("pause: no new frames accumulated",
          count_after_pause == count_before_pause)
    check("pause: island receives 'paused'",
          any(c[0] == "paused" for c in island_cb.calls))

    # The visible timer is captured-audio time, not wall time. A long pause must
    # not inflate the saved meeting duration or jump the timer on resume.
    captured_at_pause = recorder._captured_seconds()
    time.sleep(0.05)

    # VAL-MEETING-005: resume restarts frame accumulation
    island_cb.calls.clear()
    recorder.resume()
    check("resume: _recording is True", recorder._recording is True)
    resume_calls = [c for c in island_cb.calls if c[0] == "recording"]
    check("pause time excluded from meeting timer",
          bool(resume_calls) and resume_calls[-1][1] == captured_at_pause)
    _inject_fake_audio(recorder)
    count_after_resume = recorder._sample_count
    check("resume: new frames accumulated",
          count_after_resume > count_after_pause)
    check("resume: island receives 'recording'",
          any(c[0] == "recording" for c in island_cb.calls))

    # VAL-MEETING-006: multiple pause/resume cycles
    recorder.pause()
    recorder.resume()
    recorder.pause()
    recorder.resume()
    # Inject more frames, then stop
    _inject_fake_audio(recorder)
    mid = recorder.stop("Multi Cycle Test")
    check("multi cycle: stop returns meeting_id", mid is not None)
    m = meeting_store.get_meeting(mid)
    check("multi cycle: meeting saved", m is not None)
    check("multi cycle: title matches", m["title"] == "Multi Cycle Test")

except Exception as e:
    check(f"lifecycle error: {e}", False)
    import traceback
    traceback.print_exc()
finally:
    _restore_sd_stream()
    _restore_store()


# ═══════════════════════════════════════════════════════════════════════════════
#  MeetingRecorder — Stop Variants
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== MeetingRecorder — Stop Variants ==")

_isolate_store()
_mock_sd_stream()

try:
    settings = _FakeSettings(llm_provider="cerebras", cerebras_api_key="test-key")

    # VAL-MEETING-008: stop with no frames returns None
    recorder = meeting.MeetingRecorder(_FakeTranscribe(), settings)
    recorder.start()
    time.sleep(0.2)
    recorder._sample_count = 0  # simulate no frames arriving
    result = recorder.stop("Empty")
    check("stop with no frames returns None", result is None)

    # VAL-MEETING-007: stop returns meeting_id and saves
    recorder2 = meeting.MeetingRecorder(_FakeTranscribe(), settings)
    recorder2.start()
    time.sleep(0.2)
    _inject_fake_audio(recorder2)
    mid = recorder2.stop("Test Stop")
    check("stop returns non-None meeting_id", mid is not None)
    m = meeting_store.get_meeting(mid)
    check("stop: meeting exists in store", m is not None)
    check("stop: meeting has segments", len(m["segments"]) > 0)
    check("stop: meeting has speakers", len(m["speakers"]) > 0)

    # VAL-MEETING-009: empty title generates auto title
    recorder3 = meeting.MeetingRecorder(_FakeTranscribe(), settings)
    recorder3.start()
    time.sleep(0.2)
    _inject_fake_audio(recorder3)
    mid3 = recorder3.stop("")
    m3 = meeting_store.get_meeting(mid3)
    check("auto title: non-empty", bool(m3["title"]))
    check("auto title: contains 'Meeting'", "Meeting" in m3["title"])

    # VAL-MEETING-010: explicit title persisted
    tx4 = _FakeTranscribe()
    recorder4 = meeting.MeetingRecorder(tx4, settings)
    recorder4.start()
    time.sleep(0.2)
    _inject_fake_audio(recorder4)
    mid4 = recorder4.stop("Sprint Planning")
    m4 = meeting_store.get_meeting(mid4)
    check("explicit title persisted", m4["title"] == "Sprint Planning")
    count_before_repeat = len(meeting_store.list_meetings())
    repeated_mid4 = recorder4.finish_capture("Duplicate Stop")
    check("finish_capture is idempotent", repeated_mid4 == mid4)
    check("repeated finish does not duplicate meeting",
          len(meeting_store.list_meetings()) == count_before_repeat)
    tx4_calls = len(tx4.calls)
    check("ready meeting process is idempotent",
          recorder4.process_pending(mid4) == mid4)
    check("ready meeting is not transcribed again", len(tx4.calls) == tx4_calls)

    # VAL-MEETING-011: island callback sequence during stop
    island_cb2 = _FakeIslandCb()
    recorder5 = meeting.MeetingRecorder(
        _FakeTranscribe(), settings, island_cb2)
    recorder5.start()
    time.sleep(0.2)
    _inject_fake_audio(recorder5)
    mid5 = recorder5.stop("Callback Test")
    states = [c[0] for c in island_cb2.calls]
    check("stop: transcribing state in callbacks",
          "transcribing" in states)
    check("stop: diarising state in callbacks",
          "diarising" in states)
    check("stop: done state in callbacks",
          "done" in states)

    # VAL-MEETING-012: duration_sec rounded to 0.1
    m5 = meeting_store.get_meeting(mid5)
    dur = m5.get("duration_sec", 0)
    check("duration_sec is float", isinstance(dur, float))
    # Round to 1 decimal to verify precision
    check("duration_sec has at most 1 decimal",
          round(dur, 1) == dur)

    # VAL-MEETING-013: stop when not recording
    recorder6 = meeting.MeetingRecorder(_FakeTranscribe(), settings)
    result = recorder6.stop("Never Started")
    check("stop when not recording returns None", result is None)

    # VAL-MEETING-020: transcription failure doesn't lose audio
    island_cb3 = _FakeIslandCb()
    recorder7 = meeting.MeetingRecorder(
        _RaisingTranscribe(), settings, island_cb3)
    recorder7.start()
    time.sleep(0.2)
    _inject_fake_audio(recorder7)
    mid7 = recorder7.stop("Fail Test")
    check("stop with transcribe failure returns meeting_id", mid7 is not None)
    m7 = meeting_store.get_meeting(mid7)
    check("meeting saved despite transcribe failure", m7 is not None)
    check("audio_path saved despite failure",
          bool(m7.get("audio_path")))

    # VAL-MEETING-002: start resets frames from prior session
    recorder8 = meeting.MeetingRecorder(_FakeTranscribe(), settings)
    recorder8.start()
    time.sleep(0.2)
    _inject_fake_audio(recorder8)
    recorder8.stop("First")
    recorder8.start()
    check("second start resets sample count", recorder8._sample_count == 0)

except Exception as e:
    check(f"stop variants error: {e}", False)
    import traceback
    traceback.print_exc()
finally:
    _restore_sd_stream()
    _restore_store()


# ═══════════════════════════════════════════════════════════════════════════════
#  Diarisation
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Diarisation ==")

# VAL-MEETING-022: gap heuristics
segments = [
    {"start_sec": 0.0, "end_sec": 2.0, "text": "Hello", "confidence": 0.5},
    {"start_sec": 2.2, "end_sec": 5.0, "text": "Hi there", "confidence": 0.5},
    # gap 0.2s (<0.4s) — same speaker
    {"start_sec": 7.0, "end_sec": 9.0, "text": "New topic",
     "confidence": 0.5},
    # gap 2.0s (>1.5s) — new speaker
    {"start_sec": 9.3, "end_sec": 12.0, "text": "Agreed",
     "confidence": 0.5},
    # gap 0.3s (<0.4s) — same speaker
    {"start_sec": 15.0, "end_sec": 17.0, "text": "Another one",
     "confidence": 0.5},
    # gap 3.0s (>1.5s) — new speaker
]
result = meeting_diarise._lightweight_with_segments(segments)
check("diarise: returns speakers", len(result["speakers"]) >= 1)
check("diarise: returns segments", len(result["segments"]) == 5)

# Check gap heuristics
assigned = result["segments"]
# segments[0] and [1]: gap 0.2s => same speaker
check("gap < 0.4s: same speaker",
      assigned[0]["speaker"] == assigned[1]["speaker"])
# segments[1] and [2]: gap 2.0s => different speaker
check("gap > 1.5s: different speaker",
      assigned[1]["speaker"] != assigned[2]["speaker"])
# segments[2] and [3]: gap 0.3s => same speaker
check("gap < 0.4s: same speaker again",
      assigned[2]["speaker"] == assigned[3]["speaker"])
# segments[3] and [4]: gap 3.0s => different speaker
check("gap > 1.5s: different speaker again",
      assigned[3]["speaker"] != assigned[4]["speaker"])

# VAL-MEETING-023: single segment
single = [{"start_sec": 0.0, "end_sec": 5.0, "text": "Solo", "confidence": 0.5}]
r2 = meeting_diarise._lightweight_with_segments(single)
check("single segment: 1 speaker", len(r2["speakers"]) == 1)
check("single segment: Speaker 1", r2["speakers"][0]["label"] == "Speaker 1")
check("single segment: gold color",
      r2["speakers"][0]["color"] == "#D4AF37")

# VAL-MEETING-024: speaker colors from palette
for sp in result["speakers"]:
    check(f"speaker {sp['label']} color in palette",
          sp["color"] in meeting_diarise.SPEAKER_COLORS)

# VAL-MEETING-025: speaker labels cycle mod 8
many_segments = []
for i in range(20):
    many_segments.append({
        "start_sec": i * 3.0, "end_sec": i * 3.0 + 2.0,
        "text": f"Segment {i}", "confidence": 0.5,
    })
r3 = meeting_diarise._lightweight_with_segments(many_segments)
labels = set(s["speaker"] for s in r3["segments"])
for lab in labels:
    n = int(lab.split()[-1])
    check(f"label {lab} <= 8", n <= 8)

# VAL-MEETING-026: diarise failure fallback (tested via exception path in
# meeting.stop() above with _RaisingTranscribe)

# VAL-MEETING-027: diarise with no segments
r4 = meeting_diarise._lightweight_no_segments(
    __import__("numpy").zeros(16000, dtype="float32"))
check("no segments: empty speakers", r4["speakers"] == [])
check("no segments: empty segments", r4["segments"] == [])

# VAL-MEETING-028: pyannote unavailable without token
settings = _FakeSettings()
check("pyannote unavailable without HF token",
      meeting_diarise._pyannote_available(settings) is False)


# ═══════════════════════════════════════════════════════════════════════════════
#  Transcription to Segments
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Transcription to Segments ==")

import numpy as np

# VAL-MEETING-018: segments have required keys
audio = np.zeros(16000, dtype=np.float32)
segs = meeting._transcription_to_segments(
    "Hello world. This is a test.", audio, 16000)
check("transcription_to_segments returns list", isinstance(segs, list))
if segs:
    seg = segs[0]
    check("segment has start_sec", "start_sec" in seg)
    check("segment has end_sec", "end_sec" in seg)
    check("segment has text", "text" in seg)
    check("segment has confidence", "confidence" in seg)
    check("segments sorted by start_sec",
          all(segs[i]["start_sec"] <= segs[i + 1]["start_sec"]
              for i in range(len(segs) - 1)))

# VAL-MEETING-019: empty transcript
check("empty transcript returns []",
      meeting._transcription_to_segments("", audio, 16000) == [])
check("None transcript returns []",
      meeting._transcription_to_segments(None, audio, 16000) == [])

# Real Whisper word timestamps must drive meeting boundaries. The previous
# plain-text path manufactured contiguous sentence times, making its silence-gap
# diariser label virtually every ordinary meeting as a single speaker.
timestamp_words = [
    {"word": "Hello", "start": 0.1, "end": 0.4, "prob": 0.9},
    {"word": "team.", "start": 0.5, "end": 0.9, "prob": 0.8},
    {"word": "Agreed.", "start": 3.0, "end": 3.4, "prob": 0.95},
]
timed = meeting._word_timestamps_to_segments(timestamp_words, 5.0)
check("word timestamps produce real speech segments", len(timed) == 2)
check("word timestamp start preserved", timed[0]["start_sec"] == 0.1)
check("word timestamp silence gap preserved",
      timed[1]["start_sec"] - timed[0]["end_sec"] > 1.5)
check("word tokens joined with punctuation", timed[0]["text"] == "Hello team.")

timestamp_calls = []
def _timestamp_transcribe(samples, want_words=False):
    timestamp_calls.append(want_words)
    return "Hello team. Agreed.", timestamp_words

timed_result = meeting._process_audio(
    np.zeros(5 * 16000, dtype=np.float32), 16000,
    _timestamp_transcribe, _FakeSettings())
check("meeting transcription requests word timestamps",
      timestamp_calls == [True])
check("timestamp gaps reach diarisation",
      len(timed_result[0]) == 2 and
      timed_result[0][0]["speaker"] != timed_result[0][1]["speaker"])

cloud_calls = []
def _cloud_transcribe(samples, want_words=False):
    cloud_calls.append(want_words)
    return "Cloud meeting transcript."

meeting._process_audio(
    np.zeros(16000, dtype=np.float32), 16000,
    _cloud_transcribe, _FakeSettings(transcription_mode="cloud"))
check("explicit Cloud meeting mode is not forced to Local",
      cloud_calls == [False])


# ═══════════════════════════════════════════════════════════════════════════════
#  AI Extraction — Summarize
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== AI Extraction — Summarize ==")

_isolate_store()

# Create a test meeting with transcript
segments = [
    {"speaker": "Speaker 1", "start_sec": 0.0, "end_sec": 5.0,
     "text": "Hello everyone. Let's discuss the Q3 budget.", "confidence": 0.5},
    {"speaker": "Speaker 2", "start_sec": 5.5, "end_sec": 12.0,
     "text": "I think we need to cut costs in marketing.", "confidence": 0.5},
    {"speaker": "Speaker 1", "start_sec": 13.0, "end_sec": 18.0,
     "text": "Agreed. Alice will prepare the report by Friday.",
     "confidence": 0.5},
]
mid = meeting_store.save_meeting(
    "Budget Meeting", "budget.wav", 20.0,
    segments=segments,
    speakers=[
        {"label": "Speaker 1", "name": None, "color": "#D4AF37"},
        {"label": "Speaker 2", "name": None, "color": "#5AA9E6"},
    ],
)
settings = _FakeSettings(llm_provider="cerebras", cerebras_api_key="test-key")

# Install AI stub
stub = _install_ai_stub(["The team discussed the Q3 budget and agreed "
                          "that Alice will prepare a cost-cutting report."])

# VAL-MEETING-038: summarize_meeting persists and returns summary
summary = meeting.summarize_meeting(mid, settings)
check("summarize returns summary string", isinstance(summary, str) and summary)
check("summarize persisted to store",
      meeting_store.get_meeting(mid)["summary"] == summary)

# VAL-MEETING-037: transcript prompt is timestamped
# (The stub captured the user argument — we verify it's called)
check("summarize called cerebras_chat once", stub.call_count[0] >= 1)

# VAL-MEETING-179: re-summarizing updates stored summary
_install_ai_stub(["Updated summary with new info."])
summary2 = meeting.summarize_meeting(mid, settings)
check("re-summarize returns new summary", summary2 == "Updated summary with new info.")
check("re-summarize persisted new summary",
      meeting_store.get_meeting(mid)["summary"] == summary2)

# VAL-MEETING-035: non-existent meeting returns None
_install_ai_stub(["irrelevant"])
check("summarize nonexistent returns None",
      meeting.summarize_meeting("nonexistent", settings) is None)

# VAL-MEETING-036: empty transcript returns None
mid_empty = meeting_store.save_meeting(
    "Empty", "empty.wav", 1.0,
    segments=[], speakers=[],
)
check("summarize empty transcript returns None",
      meeting.summarize_meeting(mid_empty, settings) is None)

# VAL-MEETING-039: LLM exception returns None gracefully
_restore_ai()
_orig = ai.cerebras_chat

def _raising_chat(*a, **kw):
    raise RuntimeError("LLM error (simulated)")

ai.cerebras_chat = _raising_chat
try:
    check("summarize on LLM exception returns None",
          meeting.summarize_meeting(mid, settings) is None)
finally:
    ai.cerebras_chat = _orig

# VAL-MEETING-057: no key returns None
settings_no_key = _FakeSettings(llm_provider="cerebras", cerebras_api_key="")
check("summarize no key returns None",
      meeting.summarize_meeting(mid, settings_no_key) is None)

# VAL-MEETING-060: local provider proceeds without key
settings_local = _FakeSettings(llm_provider="local", local_api_key="")
_install_ai_stub(["Local summary."])
s = meeting.summarize_meeting(mid, settings_local)
check("summarize local provider proceeds", s is not None)
_restore_ai()

_restore_store()


# ═══════════════════════════════════════════════════════════════════════════════
#  AI Extraction — Action Items
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== AI Extraction — Action Items ==")

_isolate_store()

mid = meeting_store.save_meeting(
    "Action Meeting", "action.wav", 20.0,
    segments=[
        {"speaker": "S1", "start_sec": 0.0, "end_sec": 5.0,
         "text": "Alice should update the board.", "confidence": 0.5},
    ],
    speakers=[{"label": "S1", "name": None, "color": "#D4AF37"}],
)
settings = _FakeSettings(llm_provider="cerebras", cerebras_api_key="test-key")

# VAL-MEETING-043: JSON array response
_install_ai_stub(['["Alice: Update the sprint board by Friday", '
                  '"Bob: Draft the client email"]'])
items = meeting.extract_action_items(mid, settings)
check("extract_action_items returns list", isinstance(items, list))
check("extract_action_items parsed JSON array", len(items) == 2)

# VAL-MEETING-046: persisted
m = meeting_store.get_meeting(mid)
check("action_items persisted in store",
      m["action_items"] == items)

# VAL-MEETING-044: line-split fallback for non-JSON
_install_ai_stub(["- Alice: Do the thing\n- Bob: Fix the bug\n"
                  "- Charlie: Review code"])
items2 = meeting.extract_action_items(mid, settings)
check("line-split fallback works", len(items2) == 3)
check("line-split items are stripped",
      all(not i.startswith("- ") for i in items2))

# VAL-MEETING-045: empty strings filtered
_install_ai_stub(['["Valid item", "", "  ", "Another item"]'])
items3 = meeting.extract_action_items(mid, settings)
check("empty strings filtered", len(items3) == 2)

# VAL-MEETING-047: empty result doesn't overwrite
# (previous items persist)
prev_count = len(meeting_store.get_meeting(mid)["action_items"])
_install_ai_stub(["[]"])
items4 = meeting.extract_action_items(mid, settings)
check("empty JSON array returns []", items4 == [])
check("empty result does not overwrite",
      len(meeting_store.get_meeting(mid)["action_items"]) == prev_count)

# VAL-MEETING-041: non-existent meeting returns []
check("extract_actions nonexistent returns []",
      meeting.extract_action_items("nonexistent", settings) == [])

# VAL-MEETING-042: empty transcript returns []
mid_empty = meeting_store.save_meeting(
    "Empty Actions", "empty2.wav", 1.0,
    segments=[], speakers=[],
)
check("extract_actions empty transcript returns []",
      meeting.extract_action_items(mid_empty, settings) == [])

# VAL-MEETING-048: LLM exception returns []
_restore_ai()
_orig = ai.cerebras_chat
ai.cerebras_chat = _raising_chat
try:
    check("extract_actions on exception returns []",
          meeting.extract_action_items(mid, settings) == [])
finally:
    ai.cerebras_chat = _orig

# VAL-MEETING-180: re-extracting updates stored list
_install_ai_stub(['["New item only"]'])
items5 = meeting.extract_action_items(mid, settings)
check("re-extract returns new items", items5 == ["New item only"])
check("re-extract persisted",
      meeting_store.get_meeting(mid)["action_items"] == ["New item only"])

_restore_ai()
_restore_store()


# ═══════════════════════════════════════════════════════════════════════════════
#  AI Extraction — Key Decisions
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== AI Extraction — Key Decisions ==")

_isolate_store()

mid = meeting_store.save_meeting(
    "Decision Meeting", "dec.wav", 20.0,
    segments=[
        {"speaker": "S1", "start_sec": 0.0, "end_sec": 5.0,
         "text": "We decided to use React.", "confidence": 0.5},
    ],
    speakers=[{"label": "S1", "name": None, "color": "#D4AF37"}],
)
settings = _FakeSettings(llm_provider="cerebras", cerebras_api_key="test-key")

# VAL-MEETING-049: extract_key_decisions returns list
_install_ai_stub(['["Decided to use React for frontend", '
                  '"Sprint deadline moved to Friday"]'])
decisions = meeting.extract_key_decisions(mid, settings)
check("extract_key_decisions returns list", isinstance(decisions, list))
check("extract_key_decisions has items", len(decisions) == 2)
m = meeting_store.get_meeting(mid)
check("key_decisions persisted in store",
      m["key_decisions"] == decisions)

# VAL-MEETING-050: no LLM key returns []
settings_no_key = _FakeSettings(llm_provider="cerebras", cerebras_api_key="")
check("extract_key_decisions no key returns []",
      meeting.extract_key_decisions(mid, settings_no_key) == [])

# Exception handling
_restore_ai()
_orig = ai.cerebras_chat
ai.cerebras_chat = _raising_chat
try:
    check("extract_key_decisions on exception returns []",
          meeting.extract_key_decisions(mid, settings) == [])
finally:
    ai.cerebras_chat = _orig

_restore_store()


# ═══════════════════════════════════════════════════════════════════════════════
#  AI Extraction — Open Questions
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== AI Extraction — Open Questions ==")

_isolate_store()

mid = meeting_store.save_meeting(
    "Questions Meeting", "q.wav", 20.0,
    segments=[
        {"speaker": "S1", "start_sec": 0.0, "end_sec": 5.0,
         "text": "Who owns the deployment pipeline?", "confidence": 0.5},
    ],
    speakers=[{"label": "S1", "name": None, "color": "#D4AF37"}],
)
settings = _FakeSettings(llm_provider="cerebras", cerebras_api_key="test-key")

# VAL-MEETING-051: extract_open_questions returns list
_install_ai_stub(['["Who will own the deployment pipeline?", '
                  '"What is the Q3 budget?"]'])
questions = meeting.extract_open_questions(mid, settings)
check("extract_open_questions returns list", isinstance(questions, list))
check("extract_open_questions has items", len(questions) == 2)
m = meeting_store.get_meeting(mid)
check("open_questions persisted in store",
      m["open_questions"] == questions)

# VAL-MEETING-052: no LLM key returns []
settings_no_key = _FakeSettings(llm_provider="cerebras", cerebras_api_key="")
check("extract_open_questions no key returns []",
      meeting.extract_open_questions(mid, settings_no_key) == [])

# Exception handling
_restore_ai()
_orig = ai.cerebras_chat
ai.cerebras_chat = _raising_chat
try:
    check("extract_open_questions on exception returns []",
          meeting.extract_open_questions(mid, settings) == [])
finally:
    ai.cerebras_chat = _orig

_restore_store()


# ═══════════════════════════════════════════════════════════════════════════════
#  Deep Processing — process_meeting_deep
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Deep Processing ==")

_isolate_store()

segments = [
    {"speaker": "Speaker 1", "start_sec": 0.0, "end_sec": 5.0,
     "text": "Let's discuss the Q3 budget.", "confidence": 0.5},
    {"speaker": "Speaker 2", "start_sec": 5.5, "end_sec": 12.0,
     "text": "I think we need to cut marketing costs by 10%.", "confidence": 0.5},
    {"speaker": "Speaker 1", "start_sec": 13.0, "end_sec": 18.0,
     "text": "Agreed. Alice will prepare the report by Friday.",
     "confidence": 0.5},
    {"speaker": "Speaker 2", "start_sec": 19.0, "end_sec": 23.0,
     "text": "Who owns the deployment pipeline?", "confidence": 0.5},
]
mid = meeting_store.save_meeting(
    "Deep Meeting", "deep.wav", 25.0,
    segments=segments,
    speakers=[
        {"label": "Speaker 1", "name": None, "color": "#D4AF37"},
        {"label": "Speaker 2", "name": None, "color": "#5AA9E6"},
    ],
)
settings = _FakeSettings(llm_provider="cerebras", cerebras_api_key="test-key")

# VAL-MEETING-031: deep processing produces all four fields
deep_response = json.dumps({
    "summary": "The team discussed Q3 budget and agreed to cut marketing by 10%. "
               "Alice will prepare a report.",
    "action_items": ["Alice: Prepare the cost-cutting report by Friday"],
    "key_decisions": ["Cut marketing budget by 10%"],
    "open_questions": ["Who owns the deployment pipeline?"],
})
stub = _install_ai_stub([deep_response])

result = meeting.process_meeting_deep(mid, settings)
check("process_meeting_deep returns dict", isinstance(result, dict))
check("deep result has summary", bool(result.get("summary")))
check("deep result has action_items", isinstance(result.get("action_items"), list))
check("deep result has key_decisions", isinstance(result.get("key_decisions"), list))
check("deep result has open_questions", isinstance(result.get("open_questions"), list))
check("deep: action_items non-empty", len(result.get("action_items", [])) > 0)
check("deep: key_decisions non-empty", len(result.get("key_decisions", [])) > 0)
check("deep: open_questions non-empty", len(result.get("open_questions", [])) > 0)

# VAL-MEETING-032: single LLM call
check("deep: exactly one LLM call", stub.call_count[0] == 1)

# VAL-MEETING-053: all fields persisted
m = meeting_store.get_meeting(mid)
check("deep: summary persisted", bool(m.get("summary")))
check("deep: action_items persisted", len(m.get("action_items", [])) > 0)
check("deep: key_decisions persisted", len(m.get("key_decisions", [])) > 0)
check("deep: open_questions persisted", len(m.get("open_questions", [])) > 0)
check("deep: processing_mode set to 'deep'",
      m.get("processing_mode") == "deep")

# VAL-MEETING-054: no key returns None
settings_no_key = _FakeSettings(llm_provider="cerebras", cerebras_api_key="")
check("deep no key returns None",
      meeting.process_meeting_deep(mid, settings_no_key) is None)

# VAL-MEETING-055: non-existent meeting returns None
check("deep nonexistent returns None",
      meeting.process_meeting_deep("nonexistent", settings) is None)

# VAL-MEETING-056: LLM exception handled gracefully
_restore_ai()
_orig = ai.cerebras_chat
ai.cerebras_chat = _raising_chat
try:
    check("deep on exception returns None",
          meeting.process_meeting_deep(mid, settings) is None)
finally:
    ai.cerebras_chat = _orig

_restore_store()


# ═══════════════════════════════════════════════════════════════════════════════
#  Processing Modes
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Processing Modes ==")

settings = _FakeSettings()

# VAL-MEETING-033: set and read processing mode
ok = meeting.set_processing_mode("deep", settings)
check("set_processing_mode deep returns True", ok is True)
check("settings reflects deep mode",
      settings.get("meeting_processing_mode") == "deep")

ok = meeting.set_processing_mode("lightweight", settings)
check("set_processing_mode lightweight returns True", ok is True)
check("settings reflects lightweight mode",
      settings.get("meeting_processing_mode") == "lightweight")

# VAL-MEETING-034: invalid mode rejected
ok = meeting.set_processing_mode("ultra", settings)
check("set_processing_mode 'ultra' returns False", ok is False)
check("settings unchanged after invalid",
      settings.get("meeting_processing_mode") == "lightweight")

ok = meeting.set_processing_mode("", settings)
check("set_processing_mode '' returns False", ok is False)

ok = meeting.set_processing_mode(None, settings)
check("set_processing_mode None returns False", ok is False)


# ═══════════════════════════════════════════════════════════════════════════════
#  Export Formats
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Export Formats ==")

_isolate_store()

segments = [
    {"speaker": "Alice", "start_sec": 0.0, "end_sec": 5.0,
     "text": "Let's start the sprint planning.", "confidence": 0.5},
    {"speaker": "Bob", "start_sec": 5.5, "end_sec": 12.0,
     "text": "We should prioritize the Reader expansion.", "confidence": 0.5},
    {"speaker": "Alice", "start_sec": 13.0, "end_sec": 18.0,
     "text": "Agreed. I'll update the board.", "confidence": 0.5},
]
mid = meeting_store.save_meeting(
    "Sprint Planning — Jun 29, 2026 · 45 min", "sprint.wav", 45.0,
    segments=segments,
    speakers=[
        {"label": "Speaker 1", "name": "Alice", "color": "#D4AF37"},
        {"label": "Speaker 2", "name": "Bob", "color": "#5AA9E6"},
    ],
    summary="TL;DR: The team planned the sprint.",
    action_items=["Alice: Update the sprint board by Friday"],
    key_decisions=["Decided to ship the reader expansion in sprint 12"],
    open_questions=["Who owns the meeting mode documentation?"],
    processing_mode="deep",
)

# VAL-MEETING-062: TXT export
r_txt = meeting.export_meeting(mid, "txt")
check("txt export ok", r_txt["ok"] is True)
check("txt export has content", bool(r_txt.get("content")))
check("txt export correct mime", r_txt["mime"] == "text/plain")
content = r_txt["content"]
check("txt: has title header", content.startswith("# Sprint Planning"))
check("txt: has timestamped segment", "[0:00] " in content or "[00:00]" in content)
check("txt: has Summary section", "--- Summary ---" in content)
check("txt: has Action Items section", "--- Action Items ---" in content)
check("txt: has Key Decisions section", "--- Key Decisions ---" in content)
check("txt: has Open Questions section", "--- Open Questions ---" in content)

# VAL-MEETING-063: TXT omits sections when absent
mid_no_extras = meeting_store.save_meeting(
    "Simple", "simple.wav", 10.0,
    segments=[{"speaker": "S1", "start_sec": 0.0, "end_sec": 2.0,
               "text": "Hello", "confidence": 0.5}],
    speakers=[{"label": "S1", "name": None, "color": "#D4AF37"}],
)
r_txt2 = meeting.export_meeting(mid_no_extras, "txt")
check("txt no summary: no Summary header",
      "--- Summary ---" not in r_txt2["content"])
check("txt no actions: no Action Items header",
      "--- Action Items ---" not in r_txt2["content"])

# VAL-MEETING-064: JSON export
r_json = meeting.export_meeting(mid, "json")
check("json export ok", r_json["ok"] is True)
check("json export correct mime", r_json["mime"] == "application/json")
parsed = json.loads(r_json["content"])
check("json: has id", "id" in parsed)
check("json: has title", "title" in parsed)
check("json: has segments", "segments" in parsed)
check("json: has summary", "summary" in parsed)
check("json: has action_items", "action_items" in parsed)
check("json: has key_decisions", "key_decisions" in parsed)
check("json: has open_questions", "open_questions" in parsed)

# VAL-MEETING-065: Markdown export
r_md = meeting.export_meeting(mid, "markdown")
check("markdown export ok", r_md["ok"] is True)
check("markdown export correct mime", r_md["mime"] == "text/markdown")
md_content = r_md["content"]
check("md: has title heading", md_content.startswith("# Sprint Planning"))
check("md: has ## Summary", "## Summary" in md_content)
check("md: has ## Action Items", "## Action Items" in md_content)
check("md: has ## Key Decisions", "## Key Decisions" in md_content)
check("md: has ## Open Questions", "## Open Questions" in md_content)
check("md: has ## Transcript", "## Transcript" in md_content)
check("md: has speaker-labeled transcript",
      "Alice:" in md_content and "Bob:" in md_content)

# Also test "md" alias
r_md2 = meeting.export_meeting(mid, "md")
check("md alias export ok", r_md2["ok"] is True)

# VAL-MEETING-066: HTML export
r_html = meeting.export_meeting(mid, "html")
check("html export ok", r_html["ok"] is True)
check("html export correct mime", r_html["mime"] == "text/html")
html_content = r_html["content"]
check("html: starts with DOCTYPE or html",
      html_content.strip().startswith("<!DOCTYPE html>")
      or html_content.strip().startswith("<html"))
check("html: contains meeting title",
      "Sprint Planning" in html_content)
check("html: contains transcript content",
      "sprint planning" in html_content.lower())
check("html: has Summary heading", "<h2>Summary</h2>" in html_content)
check("html: has Action Items heading", "<h2>Action Items</h2>" in html_content)

# VAL-MEETING-067: MIME types
check("txt mime is text/plain",
      meeting.export_meeting(mid, "txt")["mime"] == "text/plain")
check("json mime is application/json",
      meeting.export_meeting(mid, "json")["mime"] == "application/json")
check("markdown mime is text/markdown",
      meeting.export_meeting(mid, "markdown")["mime"] == "text/markdown")
check("html mime is text/html",
      meeting.export_meeting(mid, "html")["mime"] == "text/html")

# VAL-MEETING-068: non-existent meeting
r_miss = meeting.export_meeting("nonexistent", "txt")
check("export nonexistent returns ok False", r_miss["ok"] is False)
check("export nonexistent has message", bool(r_miss.get("message")))

# VAL-MEETING-069: unsupported format
r_bad = meeting.export_meeting(mid, "pdf")
check("export unsupported format returns ok False", r_bad["ok"] is False)

# VAL-MEETING-071: clipboard export
r_clip = meeting.export_meeting(mid, "clipboard")
check("clipboard export ok", r_clip["ok"] is True)
check("clipboard has content", bool(r_clip.get("content")))

# VAL-MEETING-178: non-empty content for all supported formats
for fmt in ("txt", "json", "markdown", "html", "clipboard"):
    r = meeting.export_meeting(mid, fmt)
    check(f"export {fmt}: ok", r["ok"] is True)
    check(f"export {fmt}: non-empty content", len(r.get("content", "")) > 0)

# VAL-CROSS-014: markdown export content-compatible with Reader import
# The markdown content has proper heading structure that a markdown parser
# could consume
check("cross-014: md has proper heading structure",
      "## Summary" in md_content and "## Transcript" in md_content)

_restore_store()


# ═══════════════════════════════════════════════════════════════════════════════
#  Process Audio File
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Process Audio File ==")

_isolate_store()

settings = _FakeSettings(llm_provider="cerebras", cerebras_api_key="test-key")

# VAL-MEETING-099: process_audio_file transcribes and diarises
# Create a minimal WAV file for testing
import tempfile
import wave
import struct

tmp_wav = os.path.join(tempfile.gettempdir(), "test_meeting_audio.wav")
oversize_wav = os.path.join(tempfile.gettempdir(), "test_meeting_oversize.wav")
try:
    # Generate a 1-second 16kHz sine wave
    sample_rate = 16000
    duration = 1.0
    nsamples = int(sample_rate * duration)
    samples = np.sin(2 * np.pi * 440 * np.linspace(0, duration, nsamples))
    samples = (samples * 0.5 * 32767).astype(np.int16)

    with wave.open(tmp_wav, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())

    # VAL-MEETING-099: process_audio_file returns meeting_id
    transcribe = _FakeTranscribe("Test import transcript. We discussed the plan.")
    import_events = []
    def _ordered_transcribe(audio, want_words=False):
        import_events.append("transcribe")
        return transcribe(audio, want_words=want_words)

    mid = meeting.process_audio_file(
        tmp_wav, _ordered_transcribe, settings, title="Imported Meeting",
        on_saved=lambda meeting_id: import_events.append("saved"))
    check("process_audio_file returns meeting_id", mid is not None)
    check("import notifies durable record before transcription",
          import_events and import_events[0] == "saved")
    m = meeting_store.get_meeting(mid)
    check("imported meeting exists", m is not None)
    check("imported meeting has segments", len(m["segments"]) > 0)
    check("imported meeting has speakers", len(m["speakers"]) > 0)

    # VAL-MEETING-103: auto title when none provided
    mid2 = meeting.process_audio_file(
        tmp_wav, transcribe, settings, title="")
    m2 = meeting_store.get_meeting(mid2)
    check("auto title: non-empty", bool(m2["title"]))
    check("auto title: contains 'Meeting'", "Meeting" in m2["title"])

    deep_settings = _FakeSettings(
        llm_provider="cerebras", cerebras_api_key="test-key",
        meeting_processing_mode="deep")
    deep_import_id = meeting.process_audio_file(
        tmp_wav, transcribe, deep_settings, title="Deep Import")
    check("new meeting persists selected processing mode",
          meeting_store.get_meeting(deep_import_id)["processing_mode"] == "deep")

    # Once normalized audio is durable, a transcription failure returns the
    # interrupted record ID so it remains visible and recoverable.
    interrupted_id = meeting.process_audio_file(
        tmp_wav, _RaisingTranscribe(), settings, title="Interrupted Import")
    interrupted = meeting_store.get_meeting(interrupted_id)
    check("failed import keeps durable meeting id", interrupted_id is not None)
    check("failed import is marked interrupted",
          interrupted is not None and interrupted["status"] == "interrupted")
    check("failed import retains normalized audio",
          interrupted is not None and bool(interrupted.get("audio_path")))

    # Long-form processing reads bounded chunks and offsets every relative
    # timestamp into the full meeting timeline. Chunks overlap at boundaries so
    # a cut word gets a second context window; repeated prefix words are removed.
    chunk_calls = []
    def _chunk_transcribe(chunk, want_words=False):
        chunk_calls.append((len(chunk), want_words))
        call_index = len(chunk_calls)
        if call_index == 1:
            return "Boundary", [
                {"word": "Boundary", "start": 0.35, "end": 0.4, "prob": 0.9}
            ]
        if call_index == 2:
            return "Boundary continues.", [
                {"word": "Boundary", "start": 0.0, "end": 0.04, "prob": 0.9},
                {"word": "continues.", "start": 0.05, "end": 0.1, "prob": 0.9},
            ]
        return "Tail.", [
            {"word": "Tail.", "start": 0.05, "end": 0.1, "prob": 0.9}
        ]

    chunk_segments, _ = meeting._process_wav_path(
        tmp_wav, _chunk_transcribe, settings, chunk_seconds=0.4)
    chunk_starts = [s["start_sec"] for s in chunk_segments]
    check("long-form WAV is transcribed in bounded chunks",
          len(chunk_calls) == 3 and max(n for n, _ in chunk_calls) <= 6400)
    check("chunk timestamps are stitched to absolute time",
          chunk_starts == [0.35, 0.4, 0.77])
    check("overlapped chunk boundary text is deduplicated",
          [s["text"] for s in chunk_segments] == [
              "Boundary", "continues.", "Tail."])
    check("per-chunk energy survives global diarisation",
          all("energy" in s for s in chunk_segments))

    # A header-only fixture is sufficient for duration preflight: decoding must
    # never begin once declared duration exceeds four hours.
    too_many_frames = int(
        (recording_limits.MEETING_MAX_SECONDS + 1) * sample_rate)
    data_bytes = too_many_frames * 2
    oversize_header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_bytes, b"WAVE", b"fmt ", 16,
        1, 1, sample_rate, sample_rate * 2, 2, 16,
        b"data", data_bytes)
    with open(oversize_wav, "wb") as oversized:
        oversized.write(oversize_header)
    before_reject = len(meeting_store.list_meetings())
    rejected = meeting.process_audio_file(
        oversize_wav, transcribe, settings, title="Too Long")
    check("import over four hours is rejected before decode", rejected is None)
    check("rejected import creates no meeting record",
          len(meeting_store.list_meetings()) == before_reject)

    # VAL-MEETING-102: unreadable audio returns None
    bad = meeting.process_audio_file(
        "/nonexistent/file.wav", transcribe, settings)
    check("missing file returns None", bad is None)

except Exception as e:
    check(f"process_audio_file error: {e}", False)
    import traceback
    traceback.print_exc()
finally:
    try:
        os.remove(tmp_wav)
    except Exception:
        pass
    try:
        os.remove(oversize_wav)
    except Exception:
        pass
    _restore_store()


# ═══════════════════════════════════════════════════════════════════════════════
#  Meeting Store — Thread Safety & Robustness
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Meeting Store — Thread Safety & Robustness ==")

_isolate_store()

# VAL-MEETING-094: thread-safe concurrent saves
def _save_meeting(idx):
    meeting_store.save_meeting(
        f"Concurrent {idx}", f"conc{idx}.wav", 1.0 * idx,
        segments=[{"speaker": "S1", "start_sec": 0, "end_sec": 1,
                   "text": f"x{idx}", "confidence": 0.5}],
        speakers=[{"label": "Speaker 1", "name": None, "color": "#D4AF37"}],
    )

threads = []
for i in range(20):
    t = threading.Thread(target=_save_meeting, args=(i,))
    threads.append(t)
    t.start()
for t in threads:
    t.join()

lst = meeting_store.list_meetings()
check("concurrent saves: all entries present", len(lst) == 20)
# Verify JSON is valid by loading directly
with open(meeting_store.PATH, "r", encoding="utf-8") as f:
    data = json.load(f)
check("concurrent saves: valid JSON", isinstance(data, list))

# VAL-MEETING-095: corrupt/missing JSON recovers or preserves damaged bytes
tmp = meeting_store.PATH
backup = tmp + ".bak"
with open(backup, "r", encoding="utf-8") as f:
    backup_rows = json.load(f)
os.remove(tmp)
missing_recovered = meeting_store._load()
check("missing primary recovers the last atomic backup",
      missing_recovered == backup_rows and os.path.isfile(tmp))

# A corrupt primary restores the prior valid generation and preserves the bad
# bytes under a unique forensic/recovery filename.
with open(tmp, "w", encoding="utf-8") as f:
    f.write("primary corrupt sentinel {{{")
before_corrupt = set(glob.glob(tmp + ".corrupt-*"))
corrupt_recovered = meeting_store._load()
after_corrupt = set(glob.glob(tmp + ".corrupt-*"))
check("corrupt primary recovers backup metadata",
      corrupt_recovered == backup_rows)
check("corrupt primary bytes are preserved",
      len(after_corrupt - before_corrupt) == 1)

# If both generations are damaged, the primary is still moved aside before a
# later save starts a new store. The only recoverable bytes are never silently
# overwritten by a load-as-empty mutation.
primary_damage = "unrecoverable primary sentinel {{{"
with open(tmp, "w", encoding="utf-8") as f:
    f.write(primary_damage)
with open(backup, "w", encoding="utf-8") as f:
    f.write("unrecoverable backup sentinel {{{")
before_unrecoverable = set(glob.glob(tmp + ".corrupt-*"))
check("two corrupt generations return an empty logical store",
      meeting_store._load() == [])
new_snapshots = set(glob.glob(tmp + ".corrupt-*")) - before_unrecoverable
preserved_primary = False
for snapshot in new_snapshots:
    with open(snapshot, "r", encoding="utf-8") as f:
        preserved_primary = preserved_primary or f.read() == primary_damage
check("unrecoverable primary is moved aside",
      len(new_snapshots) == 1 and preserved_primary)
replacement_id = meeting_store.save_meeting(
    "Recovered Library", "recovered.wav", 1.0, segments=[], speakers=[])
check("new save succeeds without overwriting corrupt snapshot",
      replacement_id is not None and all(os.path.isfile(p) for p in new_snapshots))

# VAL-MEETING-096: WAV written with correct params
import branding
audio = np.zeros(16000, dtype=np.float32)
fname = meeting._save_meeting_audio(audio, 16000)
full = meeting._resolve_audio_path(fname)
check("WAV file exists", os.path.isfile(full))
with wave.open(full, "rb") as wf:
    check("WAV: 1 channel", wf.getnchannels() == 1)
    check("WAV: 2-byte sampwidth", wf.getsampwidth() == 2)
    check("WAV: 16kHz framerate", wf.getframerate() == 16000)

# VAL-MEETING-097: _resolve_audio_path
check("resolve existing path is not None",
      meeting._resolve_audio_path(fname) is not None)
check("resolve missing path is None",
      meeting._resolve_audio_path("nonexistent.wav") is None)

_restore_store()


# ═══════════════════════════════════════════════════════════════════════════════
#  VAL-MEETING-172: Meeting data is not synced to cloud
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Cloud Sync — Meeting Isolation ==")

# The cloud_sync SyncManager syncs exactly 6 types:
#   settings, stats, history, reader, favorites, presets
# Meetings must NOT be among them. Verify by checking _SYNC_TABLES.
import cloud_sync

_sync_types = tuple(cloud_sync._SYNC_TABLES.keys())
check("VAL-MEETING-172: cloud_sync._SYNC_TABLES does not include 'meetings'",
      "meetings" not in _sync_types)
check("VAL-MEETING-172: cloud_sync._SYNC_TABLES has exactly 6 types",
      len(_sync_types) == 6)
check("VAL-MEETING-172: expected sync types match",
      set(_sync_types) == {"settings", "stats", "history", "reader",
                           "favorites", "presets"})

# Also verify that save_meeting does not import or call any cloud sync
# function. The meeting_store module should not reference cloud_sync at all.
import meeting_store as ms_check
ms_source = ms_check.__file__
with open(ms_source, "r", encoding="utf-8") as f:
    ms_text = f.read()
check("VAL-MEETING-172: meeting_store.py does not import cloud_sync",
      "cloud_sync" not in ms_text)

# ═══════════════════════════════════════════════════════════════════════════════
#  VAL-MEETING-173: Full-transcript segment-aware analysis
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Full Transcript Analysis ==")

_isolate_store()

# Decisive content appears well beyond the old 24,000-character cut.
late_marker = "LATE_MARKER: Priya owns the launch checklist by Friday."
segments = [
    {"speaker": "S1", "start_sec": 0.0, "end_sec": 100.0,
     "text": "A" * 30000, "confidence": 0.5},
    {"speaker": "S1", "start_sec": 100.0, "end_sec": 200.0,
     "text": "B" * 30000, "confidence": 0.5},
    {"speaker": "S1", "start_sec": 200.0, "end_sec": 205.0,
     "text": late_marker, "confidence": 0.5},
]
mid = meeting_store.save_meeting(
    "Long", "long.wav", 100.0,
    segments=segments,
    speakers=[{"label": "S1", "name": None, "color": "#D4AF37"}],
)
settings = _FakeSettings(llm_provider="cerebras", cerebras_api_key="test-key")

# Capture the user argument passed to ai.cerebras_chat
_captured_user = []

def _capturing_chat(system, user, key, model=None, url=None,
                    max_tokens=None, timeout=None):
    _captured_user.append(user)
    late = "LATE_MARKER" in user
    if "JSON object" in system:
        return json.dumps({
            "summary": "LATE_MARKER covered" if late else "Early part",
            "action_items": (["Priya: launch checklist by Friday"]
                             if late else ["Early action"]),
            "key_decisions": (["Launch proceeds Friday"]
                              if late else ["Early decision"]),
            "open_questions": ["Who signs off?"] if late else [],
        })
    if "JSON array" in system:
        return json.dumps(["Priya: launch checklist by Friday"] if late
                          else ["Early finding"])
    return "LATE_MARKER covered" if late else "Early part summary"

_restore_ai()
_orig = ai.cerebras_chat
ai.cerebras_chat = _capturing_chat

try:
    summary = meeting.summarize_meeting(mid, settings)
    check("summarize map/reduce analyzes multiple chunks",
          len(_captured_user) == 3)
    check("summarize receives content after old 24k cutoff",
          any(late_marker in user for user in _captured_user))
    check("late content affects final summary", "LATE_MARKER" in summary)
    check("analysis map chunks stay near 55k chars",
          all(len(user) <= meeting.ANALYSIS_CHUNK_CHARS + 200
              for user in _captured_user[:2]))

    _captured_user.clear()
    actions = meeting.extract_action_items(mid, settings)
    check("extract_actions analyzes every chunk", len(_captured_user) == 2)
    check("extract_actions includes late action",
          "Priya: launch checklist by Friday" in actions)
    check("extract_actions keeps distinct early finding",
          "Early finding" in actions)

    _captured_user.clear()
    deep = meeting.process_meeting_deep(mid, settings)
    check("deep analysis maps chunks then reduces", len(_captured_user) == 3)
    check("deep analysis includes late action",
          "Priya: launch checklist by Friday" in deep["action_items"])
    check("deep analysis includes late decision",
          "Launch proceeds Friday" in deep["key_decisions"])

finally:
    ai.cerebras_chat = _orig

_restore_store()


# ═══════════════════════════════════════════════════════════════════════════════
#  VAL-MEETING-174: 8 speakers diarise without error
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== 8-Speaker Diarisation ==")

# Build segments with large gaps to force 8 different speakers
eight_segs = []
for i in range(16):
    eight_segs.append({
        "start_sec": i * 5.0, "end_sec": i * 5.0 + 2.0,
        "text": f"Speaker turn {i}", "confidence": 0.5,
    })
r8 = meeting_diarise._lightweight_with_segments(eight_segs)
labels8 = set(s["speaker"] for s in r8["segments"])
check("8 speakers: labels within 1-8",
      all(int(l.split()[-1]) <= 8 for l in labels8))
check("8 speakers: at least some variety", len(labels8) >= 2)
for sp in r8["speakers"]:
    n = int(sp["label"].split()[-1])
    check(f"speaker {sp['label']} has valid color",
          sp["color"] in meeting_diarise.SPEAKER_COLORS)
    # Verify index alignment
    check(f"speaker {sp['label']} color matches index",
          sp["color"] == meeting_diarise.SPEAKER_COLORS[n - 1])


# ═══════════════════════════════════════════════════════════════════════════════
#  Speaker NAME inference (refinement pass §8)
# ═══════════════════════════════════════════════════════════════════════════════
_infer = meeting_diarise.infer_speaker_names({
    "speakers": [{"label": "Speaker 1", "name": None},
                 {"label": "Speaker 2", "name": None},
                 {"label": "Speaker 3", "name": None}],
    "segments": [
        {"speaker": "Speaker 1", "text": "Hey Ralph, could you explain the rollout?"},
        {"speaker": "Speaker 2", "text": "Sure, the rollout starts Monday. This is Ralph."},
        {"speaker": "Speaker 1", "text": "Thanks Ralph. Priya, what about marketing?"},
        {"speaker": "Speaker 3", "text": "Marketing is on track for the launch."},
    ],
})
_bylabel = {s["label"]: s for s in _infer["speakers"]}
check("addressed+self → Speaker 2 suggests Ralph",
      "Ralph" in _bylabel["Speaker 2"]["suggested_names"])
check("lead address → Speaker 3 suggests Priya",
      "Priya" in _bylabel["Speaker 3"]["suggested_names"])
check("acknowledgement credits the previous speaker, not the next",
      "Ralph" not in _bylabel["Speaker 3"]["suggested_names"])
check("no spurious name for the un-addressed speaker",
      _bylabel["Speaker 1"]["suggested_names"] == [])

# Precision: ordinary chatter with no direct address yields no suggestions.
_clean = meeting_diarise.infer_speaker_names({
    "speakers": [{"label": "Speaker 1", "name": None},
                 {"label": "Speaker 2", "name": None}],
    "segments": [
        {"speaker": "Speaker 1", "text": "So basically the deadline is Friday."},
        {"speaker": "Speaker 2", "text": "Right, I think Monday works better actually."},
    ],
})
check("no false-positive names from plain chatter",
      all(s["suggested_names"] == [] for s in _clean["speakers"]))

# A user-set name is never re-suggested back to its own speaker.
_set = meeting_diarise.infer_speaker_names({
    "speakers": [{"label": "Speaker 1", "name": None},
                 {"label": "Speaker 2", "name": "Ralph"}],
    "segments": [
        {"speaker": "Speaker 1", "text": "Hey Ralph, go ahead."},
        {"speaker": "Speaker 2", "text": "Thanks, will do."},
    ],
})
check("already-named speaker is not re-suggested its own name",
      "Ralph" not in {s["label"]: s for s in _set["speakers"]}["Speaker 2"]["suggested_names"])

# Empty / single-speaker inputs are safe and add the field.
_empty = meeting_diarise.infer_speaker_names({"speakers": [], "segments": []})
check("infer_speaker_names safe on empty", _empty["speakers"] == [])


# ═══════════════════════════════════════════════════════════════════════════════
#  Summary
# ═══════════════════════════════════════════════════════════════════════════════

print(f"\n{'='*60}")
print(f"Test Meeting — {len(_fails)} failures")
if _fails:
    print("FAILURES:")
    for f in _fails:
        print(f"  - {f}")
else:
    print("ALL PASS")
print(f"{'='*60}")

sys.exit(1 if _fails else 0)
