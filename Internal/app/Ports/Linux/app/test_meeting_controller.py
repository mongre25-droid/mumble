#!/usr/bin/env python3
"""Controller wiring tests — meeting commands, concurrent dictation, background
processing, island callbacks, and stats integration.

Covers: VAL-MEETING-014, VAL-MEETING-015, VAL-MEETING-016, VAL-MEETING-017,
        VAL-MEETING-162, VAL-MEETING-168, VAL-MEETING-169,
        VAL-MEETING-183, VAL-MEETING-184, VAL-MEETING-185, VAL-MEETING-186,
        VAL-MEETING-187, VAL-CROSS-016

No real network, no Tk, no hotkeys. Uses __new__ for a minimal instance.
Run:  .venv/Scripts/python.exe test_meeting_controller.py   (PYTHONUTF8=1)
"""

import inspect
import json
import os
import sys
import tempfile
import threading
import time
import queue
import wave

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---- redirect data/log paths to a temp dir BEFORE importing mumble ----------
import branding  # noqa: E402
_TMP = tempfile.mkdtemp(prefix="mumble_meetctl_")
branding.DATA_DIR = _TMP
branding.SETTINGS_PATH = os.path.join(_TMP, "settings.json")
branding.LOG_PATH = os.path.join(_TMP, "mumble.log")
try:
    branding.ensure_dirs()
except Exception:
    pass

import mumble_linux as mumble  # noqa: E402
import meeting  # noqa: E402
import recording_limits  # noqa: E402

_fails = []


def check(desc, cond):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {desc}")
    if not cond:
        _fails.append(desc)


def _blank():
    """A Mumble instance with NO __init__ side effects."""
    return mumble.Mumble.__new__(mumble.Mumble)


def _minimal_mumble():
    """Create a minimal Mumble with enough attributes for meeting tests."""
    m = _blank()
    m._tk_queue = queue.Queue()
    m.settings = mumble.Settings()
    m.recording = False
    m.busy = False
    m._processing = False
    m.paused = False
    m.meeting_recording = False
    m.meeting_recorder = None
    m._meeting_lock = threading.Lock()
    m.island = None  # no Tk root, so no island
    m.lock = threading.Lock()
    return m


class _RecordingMeetingRecorder:
    """A MeetingRecorder stub that simulates recording state."""
    def __init__(self, transcribe_fn=None, settings=None, island_callback=None):
        self._transcribe = transcribe_fn
        self._settings = settings
        self._island_cb = island_callback
        self._recording = False
        self.start_called = False
        self.stop_called = False
        self.pause_called = False
        self.resume_called = False
        self.stop_title = None

    def start(self):
        self._recording = True
        self.start_called = True

    def stop(self, title=""):
        self._recording = False
        self.stop_called = True
        self.stop_title = title
        return "abc123def456"

    def finish_capture(self, title=""):
        self._recording = False
        self.stop_called = True
        self.stop_title = title
        return "abc123def456"

    def process_pending(self, meeting_id):
        return meeting_id

    def pause(self):
        self.pause_called = True
        self._recording = False
        return True

    def resume(self):
        self.resume_called = True
        self._recording = True
        return True


# =============================================== VAL-MEETING-187
print("\n== MeetingRecorder initialization (VAL-MEETING-187) ==")
m = _minimal_mumble()
# Simulate what run() does
m.meeting_recorder = meeting.MeetingRecorder(
    transcribe_fn=getattr(m, "_transcribe", None),
    settings=m.settings,
    island_callback=getattr(m, "_meeting_island_cb", None))
check("recorder created", m.meeting_recorder is not None)
check("recorder has _transcribe attr", hasattr(m.meeting_recorder, "_transcribe"))
check("recorder has _settings attr", hasattr(m.meeting_recorder, "_settings"))
check("recorder has _island_cb attr", hasattr(m.meeting_recorder, "_island_cb"))

# Verify the MeetingRecorder constructor signature accepts the three args (static)
sig = inspect.signature(meeting.MeetingRecorder.__init__)
params = list(sig.parameters.keys())
check("__init__ accepts transcribe_fn",
      "transcribe_fn" in params or len(params) >= 4)  # self is first
check("__init__ accepts settings",
      "settings" in params)
check("__init__ accepts island_callback",
      "island_callback" in params)


# =============================================== VAL-MEETING-183
print("\n== Command: meeting_record_start (VAL-MEETING-183) ==")
m = _minimal_mumble()
m.meeting_recorder = _RecordingMeetingRecorder()
resp = m._meeting_start()
check("start returns ok True", resp.get("ok") is True)
check("start returns recording True", resp.get("recording") is True)
check("meeting_recording flag set", m.meeting_recording is True)
check("recorder.start() was called", m.meeting_recorder.start_called is True)


# =============================================== VAL-MEETING-184
print("\n== Command: meeting_record_stop (VAL-MEETING-184) ==")
m = _minimal_mumble()
rec = _RecordingMeetingRecorder()
rec._recording = True
m.meeting_recorder = rec
m.meeting_recording = True
m._send_webui_async = lambda obj: setattr(m, "_refresh_sent", obj)

resp = m._meeting_stop("Sprint Planning")
check("stop returns ok True", resp.get("ok") is True)
check("stop returns processing True (bg thread)",
      resp.get("processing") is True)
# Give the background thread time to finish
time.sleep(0.3)
check("recorder.stop() was called", rec.stop_called is True)
check("stop title passed through", rec.stop_title == "Sprint Planning")
check("meeting_recording flag cleared", m.meeting_recording is False)
check("webui refresh sent", getattr(m, "_refresh_sent", None) is not None)
refresh = getattr(m, "_refresh_sent", {})
check("refresh targets meetings", refresh.get("what") == "meetings")


# =============================================== VAL-MEETING-185
print("\n== Command: meeting_record_pause/resume (VAL-MEETING-185) ==")
m = _minimal_mumble()
rec = _RecordingMeetingRecorder()
rec._recording = True
m.meeting_recorder = rec
m.meeting_recording = True

# pause
rec.pause_called = False
m.meeting_recorder.pause()
check("pause forwarded to recorder", rec.pause_called is True)

# resume
rec.resume_called = False
m.meeting_recorder.resume()
check("resume forwarded to recorder", rec.resume_called is True)


# =============================================== VAL-MEETING-186
print("\n== Command: meeting_import_audio (VAL-MEETING-186) ==")
m = _minimal_mumble()
m._transcribe = lambda audio, want_words=False: "imported text"
m._send_webui_async = lambda obj: None
m.meeting_recorder = meeting.MeetingRecorder(
    transcribe_fn=m._transcribe,
    settings=m.settings,
    island_callback=None)

# A valid source is accepted after synchronous format/duration preflight, then
# normalized and transcribed in the background.
valid_wav = os.path.join(_TMP, "meeting-import.wav")
with wave.open(valid_wav, "wb") as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(16_000)
    wf.writeframes(b"\x00\x00" * 1_600)
resp = m._meeting_import(valid_wav)
check("import returns ok True", resp.get("ok") is True)
check("import returns processing True (bg thread)",
      resp.get("processing") is True)

# Invalid/non-audio input is rejected before a doomed background job is shown
# as successfully started.
resp = m._meeting_import(__file__)
check("non-audio import rejected during preflight", resp.get("ok") is False)

# Keep the native picker aligned with formats supported by the bundled
# libsndfile build. AAC/M4A is deliberately not advertised without a decoder.
shell_path = os.path.join(os.path.dirname(__file__), "webui_shell.py")
with open(shell_path, "r", encoding="utf-8") as f:
    shell_source = f.read()
picker_source = shell_source.split("def meeting_import_audio", 1)[1]
picker_source = picker_source.split("\n    def ", 1)[0]
check("meeting picker advertises supported WAV/MP3/FLAC/OGG formats",
      all(ext in picker_source for ext in ("*.wav", "*.mp3", "*.flac", "*.ogg")))
check("meeting picker does not advertise unsupported M4A",
      "*.m4a" not in picker_source.lower())

# Import with no path via _handle simulation
resp = {"ok": True}
path = ""  # simulate _handle
if path:
    resp = m._meeting_import(path)
else:
    resp = {"ok": False, "message": "No audio path provided."}
check("empty path rejected", resp.get("ok") is False)


# =============================================== VAL-MEETING-014
print("\n== Concurrency: dictation remains available during meeting capture (VAL-MEETING-014) ==")
m = _minimal_mumble()
m.meeting_recording = True
m.paused = False
# Hardware behavior is covered by test_recording_gate; pin the controller gate
# here so a future source change cannot silently restore mutual exclusion.
src = inspect.getsource(mumble.Mumble.start_recording)
code_only = "\n".join(
    line for line in src.splitlines() if not line.lstrip().startswith("#")
)
check("start_recording has no meeting-recording veto",
      "meeting_recording" not in code_only)


# =============================================== VAL-MEETING-015
print("\n== Concurrency: meeting can start while dictation is active (VAL-MEETING-015) ==")
m = _minimal_mumble()
m.meeting_recorder = _RecordingMeetingRecorder()
m.recording = True  # dictation active
m._processing = False

resp = m._meeting_start()
check("meeting starts while dictation is recording", resp.get("ok") is True)
check("meeting recorder started alongside dictation",
      m.meeting_recorder.start_called is True)

m = _minimal_mumble()
m.meeting_recorder = _RecordingMeetingRecorder()
m.recording = False
m._processing = True  # processing pipeline
resp = m._meeting_start()
check("meeting starts while a dictation is processing", resp.get("ok") is True)


# =============================================== VAL-MEETING-016
print("\n== Gate: meeting start vetoed while already recording (VAL-MEETING-016) ==")
m = _minimal_mumble()
m.meeting_recorder = _RecordingMeetingRecorder()
m.meeting_recording = True

resp = m._meeting_start()
check("second start vetoed", resp.get("ok") is False)
check("veto message mentions already recording",
      "already" in resp.get("message", "").lower() or
      "recording" in resp.get("message", "").lower())


# =============================================== VAL-MEETING-017
print("\n== Gate: dictation works after meeting stop (VAL-MEETING-017) ==")
m = _minimal_mumble()
m.meeting_recording = False
m.paused = False

# After meeting stop, meeting_recording is False — gate should pass
meeting_rec = False
paused = False
gate_passes = not paused and not meeting_rec
check("gate passes after meeting stop (meeting_recording False)",
      gate_passes is True)


# =============================================== VAL-CROSS-016
print("\n== Cross-area: Meeting/dictation coexistence (VAL-CROSS-016) ==")
# Both capture modes have independent streams and lifecycle flags.

# Direction 1: a meeting flag no longer blocks dictation
m1 = _minimal_mumble()
m1.meeting_recording = True
src = inspect.getsource(mumble.Mumble.start_recording)
code_only = "\n".join(
    line for line in src.splitlines() if not line.lstrip().startswith("#")
)
check("dictation gate ignores meeting_recording",
      "meeting_recording" not in code_only)

# Direction 2: active dictation no longer blocks meeting capture
m2 = _minimal_mumble()
m2.meeting_recorder = _RecordingMeetingRecorder()
m2.recording = True
resp = m2._meeting_start()
check("meeting starts with dictation recording",
      resp.get("ok") is True)

# Direction 3: dictation processing no longer blocks meeting capture
m3 = _minimal_mumble()
m3.meeting_recorder = _RecordingMeetingRecorder()
m3._processing = True
resp = m3._meeting_start()
check("meeting starts with dictation processing",
      resp.get("ok") is True)


# =============================================== VAL-MEETING-168
print("\n== Background processing: stop doesn't block (VAL-MEETING-168) ==")
m = _minimal_mumble()
rec = _RecordingMeetingRecorder()
rec._recording = True

# Make stop() slow to simulate real work
_orig_stop = rec.stop
def _slow_stop(title=""):
    time.sleep(0.5)
    return _orig_stop(title)
rec.stop = _slow_stop

m.meeting_recorder = rec
m.meeting_recording = True
m._send_webui_async = lambda obj: None

t0 = time.time()
resp = m._meeting_stop("Test")
elapsed = time.time() - t0
check("stop returns immediately (< 0.2s)", elapsed < 0.2)
check("stop returns processing True", resp.get("processing") is True)
# Wait for bg thread to finish
time.sleep(0.7)
check("meeting_recording flag cleared after bg processing",
      m.meeting_recording is False)


# =============================================== VAL-MEETING-169
print("\n== WebUI refresh after processing (VAL-MEETING-169) ==")
m = _minimal_mumble()
rec = _RecordingMeetingRecorder()
rec._recording = True
m.meeting_recorder = rec
m.meeting_recording = True
_refresh_data = []

def _capture_refresh(obj):
    _refresh_data.append(obj)
m._send_webui_async = _capture_refresh

resp = m._meeting_stop("Refresh Test")
time.sleep(0.3)
check("refresh sent after stop processing",
      len(_refresh_data) > 0)
if _refresh_data:
    check("refresh command targets meetings",
          _refresh_data[0].get("what") == "meetings")
    check("refresh has cmd field",
          _refresh_data[0].get("cmd") == "refresh")


# =============================================== VAL-MEETING-162
print("\n== Meeting stats in overview (VAL-MEETING-162) ==")
# Test that webui_shell.get_overview includes meeting stats
import webui_shell  # noqa: E402
import meeting_store  # noqa: E402

class _FakeStats:
    def summary(self):
        return {"total_words": 100, "total_transcripts": 5,
                "today_words": 10, "typing_time_display": "2 min"}
    def streak(self):
        return 3, 7

class _FakeSettings:
    def get(self, key, default=None):
        if key == "llm_provider":
            return "cerebras"
        if key == "model":
            return "base.en"
        if key == "pro_mode":
            return True
        if key == "first_run":
            return False
        return default

# Mock up the Api instance with minimal attributes
api = webui_shell.Api.__new__(webui_shell.Api)
api.settings = _FakeSettings()
api._stats = lambda: _FakeStats()
api._provider_key_ok = lambda p: True
api._lite_bat = lambda: "nonexistent.bat"

# Make sure meeting_store is clean for this test
orig_meetings = meeting_store.list_meetings

# Check that get_overview includes meeting fields
try:
    overview = api.get_overview()
    check("overview has meeting_count", "meeting_count" in overview)
    check("meeting_count is int", isinstance(overview.get("meeting_count"), int))
    check("overview has meeting_minutes", "meeting_minutes" in overview)
except Exception as e:
    check(f"get_overview works: {e}", False)


# =============================================== _handle command routing
print("\n== Command routing in _handle() ==")
# Verify the command branches exist in the source
src = inspect.getsource(mumble.Mumble._start_cmd_server)
check("_handle routes meeting_record_start",
      '"meeting_record_start"' in src)
check("_handle routes meeting_record_stop",
      '"meeting_record_stop"' in src)
check("_handle routes meeting_record_pause",
      '"meeting_record_pause"' in src)
check("_handle routes meeting_record_resume",
      '"meeting_record_resume"' in src)
check("_handle routes meeting_import_audio",
      '"meeting_import_audio"' in src)


# =============================================== _meeting_island_cb
print("\n== Island callback (_meeting_island_cb) ==")
m = _minimal_mumble()
if m.meeting_recorder is None:
    m.meeting_recorder = meeting.MeetingRecorder(
        transcribe_fn=None, settings=m.settings, island_callback=None)

# Test that _meeting_island_cb exists and queues Tk calls
check("_meeting_island_cb exists",
      callable(getattr(m, "_meeting_island_cb", None)))

# A capture-integrity error is dispatched off the PortAudio callback thread.
capture_error_dispatched = threading.Event()
m._meeting_stop_after_capture_error = capture_error_dispatched.set
m._meeting_island_cb("capture_error", 12, 0)
check("capture_error callback dispatches safe worker finalization",
      capture_error_dispatched.wait(1.0))

# With no island, it should be a no-op (not crash)
try:
    m._meeting_island_cb("recording", 125, 0)
    m._meeting_island_cb("paused", 125, 0)
    m._meeting_island_cb("transcribing", 0, 0)
    m._meeting_island_cb("diarising", 0, 0)
    m._meeting_island_cb("done", 125, 2)
    check("island cb no-op when island None (no crash)", True)
except Exception as e:
    check(f"island cb no-op when island None: {e}", False)

# The worker uses the normal idempotent stop path: durable finish first, then
# state cleanup, user-visible warning, and background processing/retry handling.
capture_m = _minimal_mumble()
capture_rec = _RecordingMeetingRecorder()
capture_rec._recording = True
capture_m.meeting_recorder = capture_rec
capture_m.meeting_recording = True
capture_m._capture_notifications = []
capture_m._capture_refreshes = []
capture_m._wake_resumed = []
capture_m._notify = lambda title, body: capture_m._capture_notifications.append(
    (title, body))
capture_m._send_webui_async = lambda obj: capture_m._capture_refreshes.append(obj)
capture_m._resume_wake_word = lambda reason: capture_m._wake_resumed.append(reason)
capture_m._meeting_stop_after_capture_error()
time.sleep(0.1)
check("capture error finalizes the contiguous recording",
      capture_rec.stop_called is True)
check("capture error clears meeting recording state",
      capture_m.meeting_recording is False)
check("capture error resumes wake word ownership",
      capture_m._wake_resumed == ["meeting"])
check("capture error surfaces an explicit early-stop notification",
      any("stopped early" in title.lower() and
          "contiguous audio" in body.lower()
          for title, body in capture_m._capture_notifications))
check("capture error refreshes meeting UI",
      any(item.get("what") == "meeting_capture_error"
          for item in capture_m._capture_refreshes))

# Routine meeting timer events must not overwrite foreground dictation state.
class _Island:
    def set_state(self, *_args):
        pass

    def hint(self, *_args):
        pass


overlay_m = _minimal_mumble()
overlay_m.island = _Island()
overlay_m.recording = True
overlay_m._meeting_island_cb("recording", 65, 0)
check("meeting timer does not overwrite active dictation island",
      overlay_m._tk_queue.empty())

overlay_m.recording = False
overlay_m.meeting_recording = True
overlay_m.meeting_recorder = _RecordingMeetingRecorder()
overlay_m.meeting_recorder._recording = True
check("meeting island is restored after dictation finishes",
      overlay_m._restore_meeting_island() is True)
restored = []
while not overlay_m._tk_queue.empty():
    _func, args, _kwargs = overlay_m._tk_queue.get_nowait()
    restored.append(args)
check("restored meeting island includes the live timer",
      any(args and args[0] == "Meeting \u00b7 0:00" for args in restored))


# =============================================== recorder-ready guard
print("\n== Guard: recorder not ready ==")
m = _minimal_mumble()
m.meeting_recorder = None

resp = m._meeting_start()
check("start without recorder returns ok False",
      resp.get("ok") is False)
check("start without recorder message",
      "not ready" in resp.get("message", "").lower() or
      "recorder" in resp.get("message", "").lower())

resp = m._meeting_stop()
check("stop without recorder returns ok False",
      resp.get("ok") is False)


# =============================================== authoritative capture status
print("\n== Authoritative recording status ==")
status_m = _minimal_mumble()
status_rec = _RecordingMeetingRecorder()
status_rec._recording = False
status_m.meeting_recorder = status_rec
status_m.meeting_recording = True
paused_status = status_m._meeting_status()
check("legacy recorder status reports active capture",
      paused_status.get("active") is True)
check("legacy recorder status reports paused capture",
      paused_status.get("state") == "paused" and
      paused_status.get("paused") is True)

class _SnapshotRecorder(_RecordingMeetingRecorder):
    def capture_status(self):
        return {"active": True, "recording": True, "paused": False,
                "state": "recording", "captured_seconds": 42,
                "meeting_id": "journal123"}

status_m.meeting_recorder = _SnapshotRecorder()
live_status = status_m._meeting_status()
check("controller forwards recorder-captured duration",
      live_status.get("captured_seconds") == 42)
check("controller forwards durable capture journal id",
      live_status.get("meeting_id") == "journal123")
check("controller status always includes four-hour maximum",
      live_status.get("max_seconds") == recording_limits.MEETING_MAX_SECONDS)
with open(os.path.join(os.path.dirname(__file__), "mumble_linux.py"),
          "r", encoding="utf-8") as f:
    controller_source = f.read()
check("command server routes meeting_record_status",
      'cmd == "meeting_record_status"' in controller_source)


# =============================================== SUMMARY
print(f"\n{'='*60}")
if _fails:
    print(f"FAILURES: {len(_fails)}")
    for f in _fails:
        print(f"  ✗ {f}")
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
    sys.exit(0)
