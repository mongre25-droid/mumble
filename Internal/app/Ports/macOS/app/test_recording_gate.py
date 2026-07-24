#!/usr/bin/env python3
"""REGRESSION GUARD — recording must start whenever Mumble can transcribe.

The v0.9 "control window broken / can't start recording" bug: the cloud-STT
feature unloads the local faster-whisper model (`self.model = None`) when Cloud
transcription is active, but `start_recording` still vetoed on a bare
`self.model is None`. So turning on Cloud transcription silently broke ALL
recording — both the Home record button and the Ctrl+Win hotkey funnel through
`on_hotkey -> _safe_start -> start_recording`.

The fix routes BOTH boot-readiness and the record gate through one invariant,
`_transcription_ready()` (local model resident OR cloud active). This test pins
that invariant and proves the gate proceeds in cloud mode.

Runs against a throwaway data dir; never touches %APPDATA%\\Mumble.
Run:  python test_recording_gate.py
"""
import inspect
import os
import sys
import tempfile
import threading

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---- redirect data/log paths to a temp dir BEFORE importing mumble ----------
import branding  # noqa: E402
_TMP = tempfile.mkdtemp(prefix="mumble_recgate_")
branding.DATA_DIR = _TMP
branding.SETTINGS_PATH = os.path.join(_TMP, "settings.json")
branding.LOG_PATH = os.path.join(_TMP, "mumble.log")
try:
    branding.ensure_dirs()
except Exception:
    pass

import mumble_mac as mumble  # noqa: E402  (exercise the shipped macOS controller)

_fails = []


def check(desc, cond):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {desc}")
    if not cond:
        _fails.append(desc)


def _blank():
    """A Mumble instance with NO __init__ side effects — we set only the few
    attributes the gate + start path read."""
    return mumble.Mumble.__new__(mumble.Mumble)


# ===================================================== _transcription_ready
print("\n== _transcription_ready — the single can-we-transcribe invariant ==")
m = _blank()
m.model = None
m._cloud_transcription_on = lambda: False
check("no local model + cloud OFF  -> NOT ready", m._transcription_ready() is False)

m._cloud_transcription_on = lambda: True
check("no local model + cloud ON   -> ready (cloud transcribes)",
      m._transcription_ready() is True)

m.model = object()
m._cloud_transcription_on = lambda: False
check("local model resident + cloud OFF -> ready", m._transcription_ready() is True)


# ===================================================== the record GATE
# start_recording must EARLY-RETURN only when we truly can't transcribe, and must
# PROCEED (open the stream, flip self.recording) in cloud mode with no model.
class _FakeStream:
    def __init__(self):
        self.started = False

    def start(self):
        self.started = True


def _arm(m, *, paused, model, cloud):
    """Wire the minimum start_recording touches, with hardware stubbed out."""
    m.paused = paused
    m.model = model
    m._cloud_transcription_on = lambda: cloud
    m.busy = True            # on_hotkey set this; start_recording clears it on veto
    m.recording = False
    m.island = None
    m.lock = threading.Lock()
    m.prompt_mode_enabled = False   # Big Shift: the sticky Prompt toggle (off here)
    m._stream_done = threading.Event()
    m._set_state = lambda *a, **k: None
    m._tk_schedule = lambda *a, **k: None
    m._maybe_warm_ai = lambda *a, **k: None
    m._open_input_stream = lambda: _FakeStream()
    # resource_saver=True => start_recording skips spawning the live worker thread
    # (keeps the test free of background threads), exercising the same gate+stream.
    m.settings = type("S", (), {"get": staticmethod(lambda k, d=None:
                                                     True if k == "resource_saver" else d)})()


print("\n== record gate — CLOUD mode, no local model (the regression case) ==")
m = _blank()
_arm(m, paused=False, model=None, cloud=True)
m.start_recording()
check("recording STARTED in cloud mode with self.model=None", m.recording is True)
check("input stream was opened + started", getattr(m, "stream", None)
      and m.stream.started)

print("\n== record gate — LOCAL model resident ==")
m = _blank()
_arm(m, paused=False, model=object(), cloud=False)
m.start_recording()
check("recording STARTED with a local model", m.recording is True)

print("\n== record gate — vetoes when there is NO way to transcribe ==")
m = _blank()
_arm(m, paused=False, model=None, cloud=False)
m.start_recording()
check("recording did NOT start (no model, no cloud)", m.recording is False)
check("busy was released on veto", m.busy is False)

print("\n== record gate — vetoes while paused ==")
m = _blank()
_arm(m, paused=True, model=object(), cloud=False)
m.start_recording()
check("recording did NOT start while paused", m.recording is False)


# ============================ source guard: gate must use the shared invariant
print("\n== source guard — start_recording routes through _transcription_ready ==")
src = inspect.getsource(mumble.Mumble.start_recording)
# Inspect CODE only — comments legitimately describe the old bug.
code_only = "\n".join(
    ln for ln in src.splitlines() if not ln.lstrip().startswith("#")
)
check("start_recording calls _transcription_ready()", "_transcription_ready(" in code_only)
check("start_recording has no bare `self.model is None` gate (the old bug)",
      "self.model is None" not in code_only)


# ===================================================================== summary
print("\n" + ("ALL GREEN" if not _fails else f"{len(_fails)} FAILED: {_fails}"))
sys.exit(1 if _fails else 0)
