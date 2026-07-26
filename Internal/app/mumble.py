#!/usr/bin/env python3
"""
Mumble — private, on-device voice-to-text for Windows (Golden Black).

Tap the hotkey, speak, tap again. Audio is transcribed locally (faster-whisper,
beam search + VAD), then shaped by Cerebras cloud AI (gpt-oss-120b) when Pro Mode is
on, saved to history, and pasted at your cursor. Mumble also remembers what you copy
(clipboard history) and tracks word stats. A golden island shows what's happening.
Transcription is ALWAYS on-device; only the transcribed text is sent to Cerebras for
intelligent cleanup and formatting. With Pro Mode off (or no key), everything runs
locally on the CPU via the offline builder.
"""

import copy
import ctypes
from collections import OrderedDict
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import uuid

import branding


_PREPARED_INSERTION_LOCK_INIT = threading.Lock()

branding.ensure_dirs()

# Always tee stdout/stderr to the log file — not only when launched without a
# console (pythonw). Otherwise console runs leave the log stale and a crash is
# invisible after the fact. The Tee also writes to the real console when there is one.
try:
    _log = open(branding.LOG_PATH, "a", encoding="utf-8", buffering=1)

    class _Tee:
        def __init__(self, *streams):
            self._streams = [s for s in streams if s is not None]

        def write(self, data):
            for s in self._streams:
                try:
                    s.write(data)
                    s.flush()
                except Exception:
                    pass

        def flush(self):
            for s in self._streams:
                try:
                    s.flush()
                except Exception:
                    pass

    import datetime as _dt

    _log.write(f"\n===== launch {_dt.datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
    sys.stdout = _Tee(sys.__stdout__, _log)
    sys.stderr = _Tee(sys.__stderr__, _log)
except Exception:
    pass

import tkinter as tk

print("[startup] importing input/audio libs…", flush=True)
import keyboard
import numpy as np
import pyperclip
import pystray
import sounddevice as sd
from PIL import Image, ImageDraw
print("[startup] input/audio libs ok", flush=True)

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
import ai
import autostart
import bindings  # unified keyboard+mouse binding layer (record/re-paste/search/mode key)
import dictation_trace
import foreign_boost  # local (offline) Foreign-Mode phonetic term correction
import formatting
import islamic_terms  # Foreign mode: slash-candidate annotation for Arabic/Islamic terms
import local_engine  # cloud-dominance routing gate (cloud-primary-when-key, local degrade)
import processing_route  # immutable, privacy-enforcing text-shaping decisions
import recording_limits
import transcription  # optional cloud STT (advanced); local faster-whisper is default
import ui
import update
from insertion import (
    DeliveryIntent,
    ImagePayload,
    InsertionCoordinator,
    InsertionCoordinatorCapacityError,
    InsertionModule,
    InsertionOperationExpired,
    InsertionOutcome,
    InsertionRequest,
    InsertionRequestConflict,
    InsertionResult,
    InsertionTransaction,
    OperationIdReplayGuard,
    TargetLease,
    TextPayload,
)
from windows_insertion import (
    WindowsClipboardAdapter,
    WindowsNativeInputAdapter,
    WindowsTargetAdapter,
)
from branding import MODE_LABELS, STATE_COLORS, C
from clipboard import Clipboard, _clipboard_seq
from context_store import ConversationStore
from favorites import Favorites
# Startup breadcrumbs: faster-whisper pulls in the heavy native stack
# (av / numpy / ctranslate2). On a memory-starved machine that import can stall
# or fail with "paging file too small" — and previously the log showed only the
# launch banner, with no hint of WHERE it died. These markers make an import
# hang/failure obvious in mumble.log (and tell the user to free memory / restart).
print("[startup] base imports ok — loading transcription engine…", flush=True)
from faster_whisper import WhisperModel
print("[startup] transcription engine loaded", flush=True)
from history import History
from overlay import Island
from settings import Settings, SEARCH_HOTKEY_DEFAULT, TEXT_PROCESSING_PROVIDERS
import meeting
from prompt_history import PromptHistory
from stats import Stats
print("[startup] all imports complete — initialising Mumble…", flush=True)

# experimental: AI agent compensation tracking (safe to delete experimental/ folder)
_comp_tracker = None
try:
    from experimental.comp_tracker import CompTracker

    _comp_tracker = CompTracker()
except Exception:
    pass

SAMPLE_RATE = 16000
LEVEL_GAIN = 20.0
STREAM_CHUNK_SECONDS = 4.0
STREAM_DRAIN_TIMEOUT = 25.0


STATUS_LABELS = {
    "loading": "Loading…",
    "idle": "Ready",
    "listening": "Listening…",
    "transcribing": "Transcribing…",
    "processing": "Processing text…",
    "error": "Error",
}


class Mumble:
    def __init__(self):
        self.settings = Settings()
        self.history = History(
            branding.HISTORY_JSON,
            branding.HISTORY_TXT,
            self.settings.get("history_max", 100),
        )
        self.clipboard = Clipboard(
            branding.CLIPBOARD_JSON, self.settings.get("clipboard_max", 100)
        )
        # Stats live in their own file, independent of transcripts + clipboard. Seed
        # once from existing history so the numbers carry over for current installs.
        self.stat_store = Stats(branding.STATS_JSON)
        self.prompt_history = PromptHistory()
        self.favorites = Favorites()  # Deck ★ items (favorites.json)
        if self.stat_store.is_new:
            try:
                self.stat_store.seed(
                    list(self.history.items), getattr(self.history, "_cumulative", None)
                )
            except Exception as e:
                print("stats seed error:", e)
        self.conv_store = ConversationStore(branding.CONV_STORE_JSON)
        self.clipboard._conv_store = self.conv_store
        # New copy captured → live-refresh the web window's History/Deck.
        self.clipboard.on_change = lambda: self._send_webui_async(
            {"cmd": "refresh", "what": "clipboard"})
        # experimental: wire compensation tracker into conversation pipeline
        if _comp_tracker is not None:
            try:
                self.conv_store._on_reply_callback = _comp_tracker.log_reply
            except Exception:
                pass
        self.model = None
        self.model_name = self.settings.get("model", "small.en")
        # Wire the on-device LLM backend (cheap — nothing loads until first use).
        self._init_local_llm()
        self.hotkey = self.settings.get("hotkey", "ctrl+windows")
        self.quick_hotkey = self.settings.get("quick_paste_hotkey", "ctrl+alt+v")
        self.history_hotkey = self.settings.get("history_hotkey", "ctrl+alt+d")
        self.search_hotkey = self.settings.get(
            "search_hotkey", SEARCH_HOTKEY_DEFAULT)
        # Serialise cold-start and repeated Mumble Find requests so a shortcut
        # burst can launch at most one shell process and deliver each toggle once.
        self._find_toggle_lock = threading.Lock()
        self.mode_key = self.settings.get("mode_key", "right shift")

        # --- THE BIG SHIFT: Prompt is a sticky toggle, not a held key ---
        # The held Right-Shift "mode key" is retired. `prompt_mode_enabled` is the
        # one explicit mode: ON = every dictation is crafted into an AI prompt; OFF
        # = clean, punctuated text only. `_mode_active` now mirrors this toggle.
        self.prompt_mode_enabled = bool(self.settings.get("prompt_mode_enabled", False))
        # ITEM 4 (owner 2026-06-29): the island mode deck can force ANY processing
        # lane, not just Prompt. `active_mode` is the single source of truth for the
        # island-selected mode (None = plain dictation / inference). Prompt stays a
        # special case so the existing web "Prompt mode" switch keeps working:
        # prompt_mode_enabled mirrors (active_mode == "prompt").
        self.active_mode = self.settings.get("island_active_mode") or (
            "prompt" if self.prompt_mode_enabled else None)
        if self.active_mode not in (None, "prompt", "email"):
            self.active_mode = None
            self.settings.set("island_active_mode", "")
        if self.active_mode == "prompt":
            self.prompt_mode_enabled = True
        # Legacy mode-key state — retained as inert fields so any lingering
        # reference can't crash; nothing arms or polls them any more.
        self._mode_key_down = False
        self._mode_active = False  # = an AI mode is active at record start (see start_recording)
        self._active_mode_start = None  # which mode was active when recording began
        self._mode_windows = []
        self._rec_start = 0.0
        self._hk_mode = None
        # --- AI mode second-opinion / suggestion-chip state ---
        self._last_raw = ""  # previous pre-conversion transcript (for redo)
        self._suggested_mode = ""  # the mode the AI suspects we missed
        self._suggest_ts = 0.0
        # Shift-to-respeak (owner v6): while a clarification is on screen the user
        # can hold the mode key (Right Shift) to re-speak INSTEAD of pressing
        # Ctrl+Win. `_clarify_until` is the wall-clock deadline of that window;
        # `_shift_ptt` is True while a Shift-held push-to-talk recording is live.
        self._clarify_until = 0.0
        self._shift_ptt = False
        # Identity guard so EXACTLY ONE of {respeak, fade-fallback paste} resolves a
        # clarification — set when a clarify chip is shown, nulled (under self.lock)
        # by whichever path fires first. Prevents the double-paste the old picker
        # could cause when close_picker() fired on_dismiss AND a respeak ran.
        self._clarify_token = None
        self._update_manifest = None  # pending update manifest (set by auto-check)

        self.recording = False
        self.busy = False
        # True while a STOPPED dictation is still running its transcribe→AI→paste
        # pipeline (_process). `recording`/`busy` are both False during that window,
        # so without this guard a fresh hotkey/mode-key press would start a new
        # recording on top of the in-flight one — resetting shared stream state and
        # opening a second mic stream. Checked in the two start paths.
        self._processing = False
        # Meeting Mode state (owner 2026-06-29 — meeting-mode milestone).
        self.meeting_recorder = None
        self.meeting_recording = False
        self._meeting_transition_lock = threading.RLock()
        self.paused = False
        self._search_requested = False
        self._pro_notified = False  # one-time "key stopped working" notice
        self.pro_key_failed = False  # set if the Cerebras key is rejected/unreachable
        self._cloud_stt_failed = False  # one-time "cloud STT failed, using local" toast
        self.frames = []
        self._recorded_samples = 0
        self._dictation_limit_triggered = False
        self.stream = None
        self.lock = threading.Lock()
        self.state = "loading"

        # --- latency optimizations ---
        self._tx_lock = threading.Lock()  # serialize model.transcribe (defensive)
        self._paste_lock = threading.Lock()  # serialize _paste (non-reentrant clipboard)
        self._last_paste_sent_at = None  # visible Ctrl+V point for latency metrics
        self._insertion_target = WindowsTargetAdapter()
        self._insertion_clipboard = WindowsClipboardAdapter()
        self._insertion_native = WindowsNativeInputAdapter()
        self._insertion_module = InsertionModule(
            self._insertion_target,
            self._insertion_clipboard,
            self._insertion_native,
            trace=self._insertion_trace,
        )
        self._insertion_coordinator = self._insertion_module
        self._dictation_insertion_lease = None
        self._dictation_insertion_operation_id = None
        self._deck_insertion_lease = None
        self._deck_focus_request_pending = False
        self._deck_displacement_confirmed = False
        self._prepared_insertion_leases = OrderedDict()
        self._prepared_insertion_tombstones = OperationIdReplayGuard()
        self._prepared_insertion_created_at = {}
        self._prepared_insertion_executing = set()
        self._prepared_insertion_clock = time.monotonic
        self._prepared_insertion_ttl_s = 30.0
        self._prepared_insertion_lock = threading.Lock()
        self._deck_job_lock = threading.Lock()  # in-flight guard for Deck/History jobs
        self._deck_job_active = False           # True while a Deck job is running
        self._last_llm_ok = (
            0.0  # time of last successful AI call (for cold-start warm-up)
        )
        # --- streaming transcription ---
        self._stream_results = []  # accumulated partial transcriptions
        self._stream_done = threading.Event()  # signals worker to stop
        self._stream_worker_thread = None
        self._stream_processed_samples = 0  # sample count covered by stream chunks
        self._stream_session_id = 0  # rejects publication from an older recording
        self._stream_idle = threading.Event()  # set while no chunk is decoding
        self._stream_idle.set()
        self._stream_inflight_samples = 0
        self._active_keyword_template = ""  # per-utterance keyword template
        self._dictation_timing = None
        self._dictation_trace_sink = dictation_trace.DictationTraceSink(
            os.path.join(
                branding.DATA_DIR, "diagnostics", "dictation-traces.jsonl"
            ),
            enabled=(os.environ.get("MUMBLE_DICTATION_TRACE", "") == "1"),
        )
        self._dictation_trace_session = None
        self._dictation_trace_seen = set()
        self._dictation_trace_lock = threading.Lock()
        self._dictation_inference_ordinal = 0
        self._insertion_trace_sessions = {}
        self._finished_insertion_trace_ids = OperationIdReplayGuard()
        self._insertion_trace_lock = threading.Lock()

        # --- thread-safe Tkinter dispatch queue ---
        self._tk_queue = queue.Queue()
        self._last_level_queued = 0.0

        self.root = None
        self.island = None
        self.icon = None
        self.window = None
        self.cmd_q = queue.Queue()
        self._ensure_cmd_token()
        self._hk_main = None
        self._hk_quick = None
        self._hk_history = None
        self._hk_search = None
        # Controller-command connections are handled on separate threads. Keep
        # conflict check -> registration -> persistence -> swap as one critical
        # section so two rapid Settings captures cannot interleave.
        self._binding_lock = threading.RLock()
        # Compatibility seam for audio exclusivity. The retired wake/control
        # experiment no longer starts a competing microphone service.
        self._wake_word = None
        # Experimental correction learning is lazy and cleanly removable. The
        # manager stores only learned pairs + undo metadata; the full dictated
        # sentence remains in the normal History store and is never duplicated.
        self._correction_learning = None
        self._correction_monitor = None
        self._last_correction_capture = None
        # Manager/monitor construction can be requested by the settings reload
        # thread, controller commands, and a just-finished dictation at nearly
        # the same time.  A single init lock prevents duplicate managers from
        # writing the same correction history through independent locks.
        self._correction_init_lock = threading.RLock()
        self._correction_capture_lock = threading.RLock()

    # ============================================================ status/tray
    def status(self):
        return self.state, STATUS_LABELS.get(self.state, self.state.title())

    def _trace_begin_dictation(self):
        """Start one optional local trace without making dictation depend on it."""
        try:
            sink = getattr(self, "_dictation_trace_sink", None)
            if sink is None:
                sink = dictation_trace.DictationTraceSink(
                    os.path.join(
                        branding.DATA_DIR, "diagnostics", "dictation-traces.jsonl"
                    ),
                    enabled=(os.environ.get("MUMBLE_DICTATION_TRACE", "") == "1"),
                )
                self._dictation_trace_sink = sink
            route = "cloud" if self._cloud_transcription_on() else "local"
            self._dictation_trace_session = sink.start({
                "route": route,
                "model": (getattr(self, "_stt_model", None)
                          or self.settings.get("model", "unknown")),
                "device": getattr(self, "_stt_device", "unknown"),
                "compute_type": getattr(self, "_stt_compute", "unknown"),
                "model_resident": getattr(self, "model", None) is not None,
                # Current streaming emits finalized four-second chunks. It has
                # no tentative-token surface, so never pretend otherwise.
                "partial_contract": "stable_chunks_only",
                "resource_saver": bool(self.settings.get("resource_saver", False)),
            })
            self._dictation_trace_seen = set()
            if not hasattr(self, "_dictation_trace_lock"):
                self._dictation_trace_lock = threading.Lock()
            self._trace_mark("activation", state="requested")
        except Exception as exc:
            print("dictation trace start skipped:", type(exc).__name__)
            self._dictation_trace_session = None

    def _trace_mark(self, event, **fields):
        try:
            session = getattr(self, "_dictation_trace_session", None)
            if session is not None:
                return session.mark(event, **fields)
        except Exception as exc:
            print("dictation trace mark skipped:", type(exc).__name__)
        return None

    def _trace_mark_once(self, event, **fields):
        lock = getattr(self, "_dictation_trace_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._dictation_trace_lock = lock
        with lock:
            seen = getattr(self, "_dictation_trace_seen", set())
            if event in seen:
                return None
            seen.add(event)
            self._dictation_trace_seen = seen
        return self._trace_mark(event, **fields)

    def _trace_next_inference_ordinal(self):
        """Return a process-local inference number across local and cloud routes."""
        lock = getattr(self, "_dictation_trace_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._dictation_trace_lock = lock
        with lock:
            ordinal = getattr(self, "_dictation_inference_ordinal", 0) + 1
            self._dictation_inference_ordinal = ordinal
            return ordinal

    def _trace_finish(self, outcome, **fields):
        session = getattr(self, "_dictation_trace_session", None)
        self._dictation_trace_session = None
        if session is None:
            return None
        try:
            return session.finish(outcome, **fields)
        except Exception as exc:
            print("dictation trace finish skipped:", type(exc).__name__)
            return None

    def _set_island_state_traced(self, state):
        island = getattr(self, "island", None)
        if island is None:
            return
        island.set_state(state)
        self._trace_mark("island_render", state=state)

    def _set_state(self, s):
        self.state = s
        if self.icon is not None:
            try:
                self.icon.icon = self._tray_image(STATE_COLORS.get(s, C.gold))
                self.icon.title = f"Mumble — {STATUS_LABELS.get(s, s)}"
            except Exception:
                pass

    def _idle(self):
        if self.island:
            self._tk_schedule(self.island.set_state, "idle")
        self._set_state("idle")

    # ================================================= experimental correction learning
    def _ensure_correction_learning(self):
        """Lazy-load the isolated, standard-library-only correction manager."""
        # Real controllers create this lock in __init__.  The fallback keeps
        # lightweight __new__-based tests and old embedded callers compatible.
        lock = getattr(self, "_correction_init_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._correction_init_lock = lock
        with lock:
            if self._correction_learning is not None:
                return self._correction_learning
            try:
                from experimental.correction_learning import CorrectionLearningManager

                path = os.path.join(branding.DATA_DIR, "correction_learning.json")
                self._correction_learning = CorrectionLearningManager(self.settings, path)
                return self._correction_learning
            except Exception as e:
                print("[correction-learning] unavailable:", e)
                return None

    def _ensure_correction_monitor(self):
        lock = getattr(self, "_correction_init_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._correction_init_lock = lock
        with lock:
            if self._correction_monitor is not None:
                return self._correction_monitor
            if os.name != "nt":
                return None
            try:
                from experimental.correction_learning import UIAEditMonitor

                self._correction_monitor = UIAEditMonitor()
                return self._correction_monitor
            except Exception as e:
                # UI Automation is best-effort. The explicit island editor remains
                # available on every platform and in controls UIA cannot inspect.
                print("[correction-learning] target monitor unavailable:", e)
                return None

    def _prewarm_correction_monitor(self):
        if (os.name != "nt"
                or not self.settings.get("correction_learning_enabled", False)
                or not self.settings.get("correction_learning_auto_detect", True)):
            return
        monitor = self._ensure_correction_monitor()
        if monitor is None:
            return
        try:
            prewarm = getattr(monitor, "prewarm", None)
            if callable(prewarm):
                prewarm(wait=False)
        except Exception:
            pass

    def _cancel_correction_monitor(self):
        monitor = getattr(self, "_correction_monitor", None)
        if monitor is None:
            return
        try:
            cancel = getattr(monitor, "cancel", None) or getattr(monitor, "stop", None)
            if callable(cancel):
                cancel()
        except Exception:
            pass

    def _on_target_correction(self, capture_id, original, corrected):
        """Review-gate one localized edit found in the just-pasted target field."""
        if not self.settings.get("correction_learning_enabled", False):
            return
        manager = self._ensure_correction_learning()
        if manager is None:
            return
        try:
            preview = dict(manager.preview(original, corrected) or {})
        except Exception:
            return
        changes = preview.get("changes") or []
        if not preview.get("ok") or not changes:
            return
        with self._correction_capture_lock:
            current = self._last_correction_capture or {}
            if current.get("id") != capture_id or current.get("text") != original:
                return
            current["detected_text"] = str(corrected).strip()
            current["detected_changes"] = [
                {"from": c.get("from", ""), "to": c.get("to", "")}
                for c in changes
            ]
            # The external app already contains the user's correction. Review
            # should only learn it; there is nothing to undo/repaste.
            current["replace_verified"] = False
        first = changes[0]
        heard = str(first.get("from") or "")[:22]
        target = str(first.get("to") or "")[:22]
        hint = f"Save {heard} → {target}?" if heard and target else "Save correction?"
        if self.island:
            self._tk_schedule(self.island.offer_correction, "Learn", hint)
            self._tk_schedule(self.island.wake)

    @staticmethod
    def _foreground_hwnd():
        if os.name != "nt":
            return None
        try:
            return int(ctypes.windll.user32.GetForegroundWindow() or 0) or None
        except Exception:
            return None

    @staticmethod
    def _correction_auto_detect_safe(text):
        """Fail closed for text that resembles a credential or secret."""
        value = str(text or "")
        if re.search(
            r"(?i)\b(password|passcode|api[ _-]?key|access[ _-]?token|"
            r"secret|bearer)\b\s*[:=]",
            value,
        ):
            return False
        for token in re.findall(r"[^\s]+", value):
            compact = token.strip("'\"`.,;:()[]{}")
            if len(compact) < 24:
                continue
            classes = sum((
                any(c.islower() for c in compact),
                any(c.isupper() for c in compact),
                any(c.isdigit() for c in compact),
                any(not c.isalnum() for c in compact),
            ))
            if classes >= 3:
                return False
        return True

    def _remember_correction_candidate(self, text, mode, landed,
                                       target_lease=None):
        """Remember one eligible paste for short-lived correction detection.

        Only plain/foreign dictation is eligible. Prompt, Email, Reply and Deck
        output may contain intentional rewrites and must never train the speech
        vocabulary. The capture is in-memory and replaced by the next result.

        Merely finishing a dictation must not keep the island open. A review is
        surfaced later by ``_on_target_correction`` only if the target observer
        finds a concrete edit to this paste.
        """
        self._cancel_correction_monitor()
        enabled = bool(self.settings.get("correction_learning_enabled", False))
        eligible = (enabled and bool(landed) and mode in ("text", "foreign")
                    and bool(str(text or "").strip()))
        with self._correction_capture_lock:
            if eligible:
                self._last_correction_capture = {
                    "id": f"{time.time_ns():x}",
                    "operation_id": uuid.uuid4().hex,
                    "text": str(text).strip(),
                    "mode": mode,
                    "created_at": time.time(),
                    "target_hwnd": self._foreground_hwnd(),
                    "target_lease": (TargetLease(
                        target_lease.target,
                        "correction_replace",
                        "confirmed_insertion",
                        True,
                    ) if target_lease and target_lease.target else None),
                    # Learn & replace stays hidden until a target-field observer
                    # proves the pasted span is still unchanged. HWND + elapsed
                    # time alone cannot prove Ctrl+Z would undo the paste rather
                    # than a character typed afterwards.
                    "replace_verified": False,
                }
            else:
                self._last_correction_capture = None
        if not self.island:
            return
        # Clear any older review. Do not offer a generic action after every paste:
        # the candidate is passive until a real correction is detected below.
        self._tk_schedule(self.island.clear_correction)
        if (eligible and self.settings.get(
                "correction_learning_auto_detect", True)
                and self._correction_auto_detect_safe(text)):
            monitor = self._ensure_correction_monitor()
            if monitor is not None:
                with self._correction_capture_lock:
                    capture_id = (self._last_correction_capture or {}).get("id")
                try:
                    monitor.start(
                        str(text).strip(),
                        lambda original, corrected, cid=capture_id:
                            self._on_target_correction(cid, original, corrected),
                    )
                except Exception as e:
                    print("[correction-learning] monitor start skipped:", e)
        if enabled:
            self._send_webui_async(
                {"cmd": "refresh", "what": "correction_learning"})

    def _correction_can_replace(self, capture, foreground=None):
        """Conservative gate for the explicit undo+repaste convenience action."""
        if os.name != "nt" or not capture:
            return False
        target = capture.get("target_hwnd")
        if (not target or not capture.get("replace_verified")
                or time.time() - float(capture.get("created_at") or 0) > 30):
            return False
        try:
            u = ctypes.windll.user32
            if not u.IsWindow(int(target)):
                return False
            current = foreground if foreground is not None else self._foreground_hwnd()
            return bool(current and int(current) == int(target))
        except Exception:
            return False

    def correction_learning_status(self):
        manager = self._ensure_correction_learning()
        if manager is None:
            base = {"ok": False, "message": "Correction learning is unavailable."}
        else:
            try:
                base = dict(manager.status() or {})
                base.setdefault("ok", bool(base.get("ready", True)))
                if not base.get("ok") and not base.get("message"):
                    base["message"] = (base.get("storage_error") or
                                       "Correction history is unavailable.")
            except Exception as e:
                base = {"ok": False, "message": str(e)}
        with self._correction_capture_lock:
            cap = dict(self._last_correction_capture or {})
        base["enabled"] = bool(
            self.settings.get("correction_learning_enabled", False))
        base["capture_available"] = bool(cap.get("text"))
        base["replace_available"] = self._correction_can_replace(cap)
        base["auto_detect_enabled"] = bool(self.settings.get(
            "correction_learning_auto_detect", True))
        monitor = getattr(self, "_correction_monitor", None)
        if monitor is not None:
            try:
                base["monitor"] = monitor.status()
            except Exception:
                pass
        return base

    def open_correction_learning(self):
        """Open the local editor for the last eligible dictation."""
        if not self.settings.get("correction_learning_enabled", False):
            return {"ok": False, "message": "Enable correction learning first."}
        if self.island is None:
            return {"ok": False, "message": "The island is not ready yet."}
        manager = self._ensure_correction_learning()
        if manager is None:
            return {"ok": False, "message": "Correction learning is unavailable."}
        with self._correction_capture_lock:
            capture = dict(self._last_correction_capture or {})
        original = capture.get("text", "")
        if not original:
            return {"ok": False, "message": "Dictate some plain text first."}
        foreground = self._foreground_hwnd()
        detected = str(capture.get("detected_text") or "").strip()
        already_applied = bool(detected and detected != original)
        can_replace = (not already_applied
                       and self._correction_can_replace(capture, foreground))
        self._cancel_correction_monitor()

        def complete(result):
            threading.Thread(
                target=self._apply_correction_learning,
                args=(dict(capture), dict(result or {})),
                name="correction-learning",
                daemon=True,
            ).start()

        self._tk_schedule(
            self.island.show_correction_editor,
            original,
            complete,
            can_replace=can_replace,
            initial=(detected or original),
            already_applied=already_applied,
            preview_fn=manager.preview,
        )
        return {"ok": True, "opened": True, "can_replace": can_replace,
                "message": "Correction editor opened on the island."}

    def _copy_corrected_text(self, text):
        """Leave corrected text on the clipboard without polluting context history."""
        if self.clipboard:
            self.clipboard.pause()
            try:
                self.clipboard.mark_own(text)
            except Exception:
                pass
        ok = self._set_clipboard(text)
        if self.clipboard:
            try:
                self.clipboard._last_text = pyperclip.paste() or ""
            except Exception:
                pass
            self.clipboard.resume(skip_current=True)
        return ok

    def _replace_correction_capture(self, capture, corrected):
        """Undo the explicitly-selected last paste, then paste the corrected text."""
        if not self._correction_can_replace(capture, capture.get("target_hwnd")):
            return False
        try:
            lease = capture.get("target_lease")
            if not isinstance(lease, TargetLease) or lease.target is None:
                return False
            result = self._paste(
                corrected,
                source="correction_replace",
                target_lease=lease,
                operation_id=(dictation_trace.validated_operation_id(
                    capture.get("operation_id")) or uuid.uuid4().hex),
                undo_before_paste=True,
            )
            # An unconfirmed replacement can never be called successful: the
            # undo has already changed the field and only confirmed evidence can
            # prove the corrected value replaced it exactly once.
            return result.confirmed
        except Exception as e:
            print("[correction-learning] replace failed:", e)
            return False

    def _apply_correction_learning(self, capture, result):
        manager = self._ensure_correction_learning()
        if manager is None:
            return
        original = str(result.get("original") or capture.get("text") or "").strip()
        corrected = str(result.get("corrected") or "").strip()
        action = str(result.get("action") or "learn")
        try:
            learned = dict(manager.learn(original, corrected) or {})
        except Exception as e:
            learned = {"ok": False, "message": str(e)}
        if not learned.get("ok", False):
            msg = learned.get("message") or "No reusable word correction found."
            if self.island:
                self._tk_schedule(self.island.hint, str(msg)[:80])
                self._tk_schedule(self.island.wake)
            return

        replaced = False
        copied = False
        if action == "replace":
            replaced = self._replace_correction_capture(capture, corrected)
            if not replaced:
                copied = self._copy_corrected_text(corrected)
        elif action == "copy":
            copied = self._copy_corrected_text(corrected)

        with self._correction_capture_lock:
            current = self._last_correction_capture or {}
            if current.get("id") == capture.get("id"):
                self._last_correction_capture = None

        changes = learned.get("changes") or learned.get("applied") or []
        count = learned.get("applied_count")
        try:
            count = int(count if count is not None else len(changes))
        except Exception:
            count = len(changes) if isinstance(changes, list) else 1
        tail = (" · replaced last paste" if replaced else
                " · corrected text copied" if copied else "")
        message = (((learned.get("message") or "This correction is already learned.")
                    + tail)
                   if count == 0 else
                   f"Learned {count} correction{'s' if count != 1 else ''}{tail}")
        if self.island:
            self._tk_schedule(self.island.hint, message)
            self._tk_schedule(self.island.wake)
        if self.window and hasattr(self.window, "refresh_vocabulary"):
            self._tk_schedule(self.window.refresh_vocabulary)
        self._send_webui_async({"cmd": "refresh", "what": "settings"})

    def undo_correction_learning(self):
        manager = self._ensure_correction_learning()
        if manager is None:
            return {"ok": False, "message": "Correction learning is unavailable."}
        try:
            result = dict(manager.undo_last() or {})
        except Exception as e:
            result = {"ok": False, "message": str(e)}
        if result.get("ok"):
            self._send_webui_async({"cmd": "refresh", "what": "settings"})
            if self.window and hasattr(self.window, "refresh_vocabulary"):
                self._tk_schedule(self.window.refresh_vocabulary)
            if self.island:
                self._tk_schedule(self.island.hint, "Learned correction undone")
                self._tk_schedule(self.island.wake)
        return result

    # Compatibility hooks for existing microphone call sites. The retired
    # wake-word service is no longer created, so these are normally no-ops.
    def _pause_wake_word(self, reason="dictation"):
        wake = getattr(self, "_wake_word", None)
        if wake is None:
            return True
        try:
            return bool(wake.pause(reason, wait=True))
        except Exception:
            return False

    def _resume_wake_word(self, reason="dictation"):
        wake = getattr(self, "_wake_word", None)
        if wake is not None:
            try:
                wake.resume(reason)
            except Exception:
                pass

    def _tk_schedule(self, func, *args, **kwargs):
        """Schedule a callable on the Tkinter main thread via the dispatch queue.
        Thread-safe: can be called from audio callback / worker / streaming threads."""
        self._tk_queue.put((func, args, kwargs))

    def _meeting_island_cb(self, state, timer, speaker_count):
        """MeetingRecorder island callback — marshals to Tk thread via _tk_schedule.
        Called from the recording timer thread and processing thread."""
        if state == "limit_reached":
            if self.island is not None:
                self._tk_schedule(self.island.set_state, "transcribing")
                self._tk_schedule(
                    self.island.hint,
                    "Four-hour limit reached · saving meeting",
                )
            # This signal originates on PortAudio's callback thread. Finalize
            # on a worker so stream shutdown, WAV flush, and store writes can
            # block safely. finish_capture() is idempotent if the recorder's
            # watchdog reaches it at the same time.
            threading.Thread(
                target=self._meeting_stop_at_limit,
                name="meeting-limit-stop",
                daemon=True,
            ).start()
            return
        if state == "capture_error":
            if self.island is not None:
                self._tk_schedule(self.island.set_state, "transcribing")
                self._tk_schedule(
                    self.island.hint,
                    "Recording stopped early · saving captured audio",
                )
            # The recorder has stopped accepting frames at the first failed
            # block, so its queued audio is one contiguous prefix. Finalize on
            # a worker (never PortAudio's callback), then process that saved
            # prefix. The recorder watchdog may race us; finish_capture is
            # deliberately idempotent.
            threading.Thread(
                target=self._meeting_stop_after_capture_error,
                name="meeting-capture-error-stop",
                daemon=True,
            ).start()
            return
        if self.island is None:
            return
        # Dictation is the foreground interaction. A live meeting emits a timer
        # update every second, so routine meeting updates must not overwrite the
        # Listening/Transcribing display while both capture modes are active.
        if (getattr(self, "recording", False)
                or getattr(self, "_processing", False)
                or getattr(self, "busy", False)):
            return
        if state == "recording":
            self._tk_schedule(self.island.set_state, "listening")
            self._tk_schedule(self.island.hint, f"Meeting \u00b7 {timer // 60}:{timer % 60:02d}")
        elif state == "paused":
            self._tk_schedule(self.island.set_state, "idle")
            self._tk_schedule(self.island.hint, "Meeting Paused")
        elif state == "transcribing":
            self._tk_schedule(self.island.set_state, "transcribing")
            self._tk_schedule(self.island.hint, "Transcribing Meeting")
        elif state == "diarising":
            self._tk_schedule(self.island.set_state, "transcribing")
            self._tk_schedule(self.island.hint, "Diarising Meeting")
        elif state == "done":
            self._tk_schedule(self.island.set_state, "idle")
            mins = int(timer // 60)
            secs = int(timer % 60)
            self._tk_schedule(
                self.island.hint,
                f"Meeting Saved \u00b7 {speaker_count} speaker{'' if speaker_count == 1 else 's'} \u00b7 {mins}:{secs:02d}")

    def _restore_meeting_island(self):
        """Restore the live meeting display after foreground dictation ends."""
        island = getattr(self, "island", None)
        if (island is None
                or getattr(self, "recording", False)
                or getattr(self, "_processing", False)
                or not getattr(self, "meeting_recording", False)):
            return False
        snapshot = self._meeting_status()
        timer = int(snapshot.get("captured_seconds", 0) or 0)
        if snapshot.get("paused"):
            self._tk_schedule(island.set_state, "idle")
            self._tk_schedule(island.hint, "Meeting Paused")
        elif snapshot.get("active"):
            self._tk_schedule(island.set_state, "listening")
            self._tk_schedule(
                island.hint,
                f"Meeting \u00b7 {timer // 60}:{timer % 60:02d}",
            )
        else:
            return False
        return True

    def _meeting_stop_at_limit(self):
        """Finalize, surface, and process a meeting capped by the recorder."""
        if not getattr(self, "meeting_recording", False):
            return
        result = self._meeting_stop("")
        if result.get("ok"):
            self._notify(
                "Meeting saved",
                "Mumble reached the four-hour meeting limit. The audio is safe "
                "and transcription is running.",
            )
            self._send_webui_async({"cmd": "refresh", "what": "meeting_limit"})
        else:
            self._notify(
                "Meeting could not be finalized",
                result.get("message", "Unknown meeting recording error."),
            )

    def _meeting_stop_after_capture_error(self):
        """Finalize and surface a meeting stopped by an audio integrity error."""
        if not getattr(self, "meeting_recording", False):
            return
        result = self._meeting_stop("")
        if result.get("ok"):
            self._notify(
                "Meeting recording stopped early",
                "Mumble detected an audio capture problem. The contiguous audio "
                "recorded before it is safe, and transcription is running.",
            )
            self._send_webui_async(
                {"cmd": "refresh", "what": "meeting_capture_error"})
        else:
            self._notify(
                "Meeting could not be finalized",
                result.get("message", "Unknown meeting recording error."),
            )

    def _bump_feature(self, name):
        """Best-effort: nudge a coarse feature-adoption counter so the usage-aware
        island tips can RETIRE a tip once the user actually adopts that feature
        (see tips.py). Count only, never content; never blocks the action."""
        try:
            if getattr(self, "stat_store", None) is not None:
                self.stat_store.bump_feature(name)
        except Exception:
            pass

    def _tip_usage(self):
        """Assemble the usage snapshot that gates the island tips: which features the
        user has adopted (so we never nag about something they already use, and only
        surface tips for genuinely-unused features). Pure reads from existing stores —
        no new tracking on the hot path. Any failure degrades to an empty signal."""
        u = {"n": 0, "modes": {}, "reader_sessions": 0, "features": {},
             "has_key": False, "pro_on": False, "favorites": 0,
             "multilingual": False, "foreign_toggle": False}
        try:
            u["n"] = int(self.stat_store.summary().get("total_transcripts", 0))
            u["modes"] = {m: c for (m, c, _w) in self.stat_store.mode_stats()}
            u["reader_sessions"] = int(
                self.stat_store.reader_summary().get("total_sessions", 0))
            u["features"] = self.stat_store.feature_counts()
        except Exception:
            pass
        try:
            s = self.settings
            u["pro_on"] = bool(s.get("pro_mode", True))
            prov = s.get("llm_provider", "cerebras")
            u["has_key"] = bool((s.get(f"{prov}_api_key", "") or "").strip())
            u["multilingual"] = not bool(s.get("english_only", True))
            u["foreign_toggle"] = bool(s.get("island_foreign_toggle", False))
        except Exception:
            pass
        try:
            u["favorites"] = len(self.favorites.items) if getattr(
                self, "favorites", None) else 0
        except Exception:
            pass
        return u

    def _maybe_island_tip(self, landed):
        """ITEM 19: occasionally surface a spaced, self-retiring island TIP after a
        dictation actually lands. All the "show naturally / never spam / never repeat
        / retire after N" gating lives in tips.next_tip(); the per-tip counts persist
        in settings('island_tips'). Skipped when the paste didn't land (the user is
        mid-context) so a tip never interrupts. Renders through the island's own hint
        chip — same visual language as the island, never a separate notification."""
        if not (self.island and landed):
            return
        try:
            import tips
            state = self.settings.get("island_tips", {}) or {}
            usage = self._tip_usage()
            tip, new_state = tips.next_tip(state, time.time(), usage)
            if tip is None:
                return
            self.settings.set("island_tips", new_state)
            text = tip["text"]
            if tip.get("id") == "search":
                search_binding = self.settings.get(
                    "search_hotkey", SEARCH_HOTKEY_DEFAULT)
                text = (f"{bindings.pretty(search_binding)} opens Mumble "
                        "Search for apps and files")
            # Let the ~1.35s done-flash clear first, then show the tip, so they don't
            # stack. Marshalled to the Tk thread (root.after is not thread-safe).
            self._tk_schedule(self._show_island_tip, text)
        except Exception as e:
            print("island tip skipped:", e)

    def _show_island_tip(self, text):
        """Main-thread: show a tip in the island's hint chip after the flash clears."""
        try:
            self.root.after(1700, lambda: self._fire_island_tip(text))
        except Exception:
            pass

    def _fire_island_tip(self, text):
        if self.island:
            try:
                self.island.hint(text)
                self.island.wake()
            except Exception:
                pass

    def _tray_image(self, color):
        S = 256
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        bars = [0.42, 0.72, 1.0, 0.62, 0.46]
        n, bw, total = len(bars), S * 0.085, S * 0.52
        gap = (total - n * bw) / (n - 1)
        x = (S - total) / 2 + bw / 2
        cy = S / 2
        for h in bars:
            half = (S * 0.34) * h
            d.rounded_rectangle(
                [x - bw / 2, cy - half, x + bw / 2, cy + half],
                radius=bw / 2,
                fill=color,
            )
            x += bw + gap
        return img.resize((64, 64), Image.LANCZOS)

    def _notify(self, title, msg):
        try:
            if self.icon:
                self.icon.notify(str(msg)[:200], title)
        except Exception:
            pass

    def _boost_priority(self):
        """Boost this process to high priority during transcription.
        Returns the old priority class so it can be restored. On non-Windows
        or failure, returns None (no-op)."""
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            # GetCurrentProcess returns a pseudo-handle (-1) that doesn't need closing
            HIGH = 0x00000080
            handle = kernel32.GetCurrentProcess()
            old = kernel32.GetPriorityClass(handle)
            if old != HIGH:
                kernel32.SetPriorityClass(handle, HIGH)
            return old
        except Exception:
            return None

    def _restore_priority(self, old_priority):
        """Restore process priority to its previous class."""
        if old_priority is None:
            return
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetCurrentProcess()
            kernel32.SetPriorityClass(handle, old_priority)
        except Exception:
            pass

    # =================================================================== audio
    def _audio_cb(self, indata, frames, time_info, status):
        # Detect a sleep/resume gap before applying the cap. Audio discarded
        # here must not count toward the fresh post-resume recording.
        now = time.time()
        last_cb = getattr(self, "_last_audio_cb_time", 0)
        if last_cb > 0 and now - last_cb > 2.0:
            sleep_dur = now - last_cb
            print(f"[audio] {sleep_dur:.0f}s gap in audio — system may have "
                  "slept; discarding stale buffer and resetting")
            with self.lock:
                self.frames = []
                self._recorded_samples = 0
                # Reject any pre-sleep chunk that completes later. Replace the
                # idle event as well so that old work cannot signal the fresh
                # post-resume session.
                self._stream_session_id = getattr(self, "_stream_session_id", 0) + 1
                self._stream_results = []
                self._stream_processed_samples = 0
                self._stream_inflight_samples = 0
                self._stream_idle = threading.Event()
                self._stream_idle.set()
        self._last_audio_cb_time = now

        # Normal dictation is deliberately bounded to ten minutes.  Besides
        # preventing an accidental all-day hotkey press from exhausting RAM,
        # ten minutes of our 16 kHz mono PCM fits below every supported cloud
        # provider's 25 MB direct-upload limit.  Keep exactly the remaining
        # samples from the final callback, then stop outside PortAudio's callback
        # thread (closing a stream from inside its own callback can deadlock).
        recorded = max(0, int(getattr(self, "_recorded_samples", 0)))
        remaining = recording_limits.DICTATION_MAX_SAMPLES - recorded
        if remaining <= 0:
            block = None
        else:
            block = indata[:remaining].copy()
            self.frames.append(block)
            self._recorded_samples = recorded + len(block)

        if (getattr(self, "_recorded_samples", 0)
                >= recording_limits.DICTATION_MAX_SAMPLES
                and not getattr(self, "_dictation_limit_triggered", False)):
            self._dictation_limit_triggered = True
            threading.Thread(
                target=self._stop_at_dictation_limit,
                name="mumble-dictation-limit",
                daemon=True,
            ).start()

        # Once the cap is full there is no new audio block to meter.
        if block is None or len(block) == 0:
            return

        self._trace_mark_once("first_audio", audio_state="recording")

        if self.island is not None:
            rms = float(np.sqrt(np.mean(block**2)))
            lvl = min(1.0, rms * LEVEL_GAIN)
            # Throttle Tkinter dispatch: only queue when level changes
            # significantly (saves ~93 callbacks/sec from flooding the event queue).
            if abs(lvl - self._last_level_queued) >= 0.04:
                self._last_level_queued = lvl
                self._tk_schedule(self.island.set_level, lvl)

    def _stop_at_dictation_limit(self):
        """Finish a capped dictation from a safe worker thread."""
        try:
            self._notify(
                "Recording limit reached",
                "Mumble stopped at the 10-minute dictation limit and is "
                "transcribing what it captured.",
            )
        finally:
            self.stop_recording()

    def _stream_worker(self):
        """Background worker: transcribes accumulated audio in chunks DURING the
        recording so most of the decode is done before the user stops; on stop,
        only the remaining tail needs a final pass.

        Coverage is tracked in ONE unit — SAMPLES. `_stream_processed_samples`
        is the exact index into the flattened sample stream that the streamed
        text covers; `_process` slices the tail from precisely that index, so
        the stream/tail seam can never drop or repeat audio. (The old code
        compared a callback-BLOCK count against a SAMPLE threshold — ~32000
        blocks ≈ 5+ minutes — so the worker essentially never fired; and its
        skip/error paths advanced the block cursor without advancing the sample
        counter, desyncing the seam whenever it DID fire.)

        Failure semantics keep the seam exact:
        • silence (a chunk that transcribes to "") still advances the seam —
          the audio was covered, it just had nothing to say;
        • a transcription ERROR stops streaming entirely instead of skipping —
          the seam stays where it was, so the final tail still covers the
          failed chunk and everything after it, in order."""
        chunk_secs = STREAM_CHUNK_SECONDS
        session_id = getattr(self, "_stream_session_id", 0)
        consumed_blocks = 0  # cursor into self.frames (callback blocks)
        while not self._stream_done.is_set():
            if getattr(self, '_stream_worker_thread', None) is not threading.current_thread():
                break
            if self._stream_done.wait(0.1):
                break
            if not self.recording:
                break
            with self.lock:
                if (session_id != getattr(self, "_stream_session_id", 0)
                        or self._stream_done.is_set() or not self.recording):
                    break
                blocks = list(self.frames[consumed_blocks:])
            new_samples = sum(len(b) for b in blocks)
            if new_samples < int(SAMPLE_RATE * chunk_secs):
                continue  # not enough NEW AUDIO yet (samples vs samples)
            audio_chunk = np.concatenate(blocks, axis=0).flatten()
            consumed_blocks += len(blocks)
            idle = getattr(self, "_stream_idle", None)
            with self.lock:
                if (session_id != getattr(self, "_stream_session_id", 0)
                        or self._stream_done.is_set() or not self.recording):
                    break
                self._stream_inflight_samples = len(audio_chunk)
                if idle is not None:
                    idle.clear()
            partial_text = None
            try:
                partial_text = self._local_transcribe(audio_chunk, beam=1)
            except Exception as e:
                print("stream worker chunk failed (tail will cover it):", e)
                partial_text = None
            finally:
                # Finishing after key-release is not stale: it is useful work for
                # this same recording. Publish it once so stop never re-decodes
                # the claimed range. Only a session mismatch makes it stale.
                with self.lock:
                    same_session = (
                        session_id == getattr(self, "_stream_session_id", 0))
                    if same_session and partial_text is not None:
                        if partial_text.strip():
                            self._stream_results.append(partial_text.strip())
                            trace_once = getattr(self, "_trace_mark_once", None)
                            if trace_once is not None:
                                trace_once(
                                    "stable_partial_ready",
                                    stream_chunks=len(self._stream_results),
                                    processed_samples=(
                                        self._stream_processed_samples
                                        + len(audio_chunk)
                                    ),
                                )
                        self._stream_processed_samples += len(audio_chunk)
                    if same_session:
                        self._stream_inflight_samples = 0
                        if idle is not None:
                            idle.set()
            if partial_text is None or self._stream_done.is_set():
                break

    # ========================================================= meeting mode
    def _meeting_lock(self):
        """Return the transition lock, including for lightweight test hosts."""
        lock = getattr(self, "_meeting_transition_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._meeting_transition_lock = lock
        return lock

    def _meeting_start(self):
        """Start meeting capture independently of foreground dictation."""
        with self._meeting_lock():
            return self._meeting_start_locked()

    def _meeting_start_locked(self):
        """Serialised implementation behind :meth:`_meeting_start`."""
        recorder = getattr(self, "meeting_recorder", None)
        if recorder is None:
            return {"ok": False, "message": "Meeting recorder not ready."}
        if getattr(self, "meeting_recording", False):
            return {"ok": False, "message": "A meeting is already recording."}
        if not self._pause_wake_word("meeting"):
            self._resume_wake_word("meeting")
            return {"ok": False, "message": (
                "Voice activation did not release the microphone in time. "
                "Turn it off and try the meeting again.")}
        try:
            self.meeting_recording = True
            recorder.start()
            snapshot = self._meeting_status()
            if not snapshot.get("recording"):
                return {"ok": False, "message": (
                    snapshot.get("message")
                    or "Meeting capture stopped while the microphone was opening.")}
            self._bump_feature("meeting")
            return snapshot
        except Exception as e:
            self.meeting_recording = False
            self._resume_wake_word("meeting")
            print(f"[meeting] start failed: {e}")
            return {"ok": False, "message": str(e)}

    def _meeting_stop(self, title=""):
        """Stop meeting recording and run processing in a background thread.
        Returns immediately with a 'processing' response; the actual
        transcription + diarisation + save runs on a daemon thread."""
        with self._meeting_lock():
            return self._meeting_stop_locked(title)

    def _meeting_stop_locked(self, title=""):
        recorder = getattr(self, "meeting_recorder", None)
        if recorder is None:
            return {"ok": False, "message": "Meeting recorder not ready."}
        snapshot = self._meeting_status()
        if not getattr(self, "meeting_recording", False) and not snapshot.get("active"):
            return {"ok": False, "message": "No meeting is recording."}
        try:
            meeting_id = recorder.finish_capture(title)
            self.meeting_recording = False
            self._resume_wake_word("meeting")
        except Exception as e:
            # A failed metadata commit is deliberately retryable: the stream is
            # closed, but the recorder still owns the private WAV and journal.
            self.meeting_recording = bool(self._meeting_status().get("active"))
            self._resume_wake_word("meeting")
            print(f"[meeting] stop failed: {e}")
            return {"ok": False, "message": str(e)}
        if not meeting_id:
            return {"ok": False, "message": "No meeting audio was captured."}

        def _bg():
            try:
                if recorder.process_pending(meeting_id):
                    # Push refresh so the web UI updates its meeting list
                    self._send_webui_async({"cmd": "refresh", "what": "meetings"})
            except Exception as e:
                print(f"[meeting] stop processing error: {e}")

        threading.Thread(target=_bg, daemon=True).start()
        return {"ok": True, "recording": False, "processing": True,
                "meeting_id": meeting_id}

    def _meeting_status(self):
        """Return the controller-owned live capture state for Web UI recovery."""
        recorder = getattr(self, "meeting_recorder", None)
        if recorder is None:
            return {"ok": True, "active": False, "recording": False,
                    "paused": False, "state": "idle",
                    "captured_seconds": 0,
                    "max_seconds": recording_limits.MEETING_MAX_SECONDS,
                    "meeting_id": None}
        snapshot_fn = getattr(recorder, "capture_status", None)
        if callable(snapshot_fn):
            snapshot = dict(snapshot_fn() or {})
        else:
            active = bool(getattr(self, "meeting_recording", False))
            paused = active and not bool(getattr(recorder, "_recording", False))
            snapshot = {"active": active, "recording": active and not paused,
                        "paused": paused,
                        "state": "paused" if paused else (
                            "recording" if active else "idle"),
                        "captured_seconds": 0, "meeting_id": None}
        snapshot.update(ok=True)
        snapshot.setdefault("max_seconds", recording_limits.MEETING_MAX_SECONDS)
        return snapshot

    def _meeting_import(self, path):
        """Import an external audio file as a meeting. Runs in background."""
        recorder = getattr(self, "meeting_recorder", None)
        if recorder is None:
            return {"ok": False, "message": "Meeting recorder not ready."}
        try:
            path = os.path.abspath(os.fspath(path))
            if not os.path.isfile(path):
                raise ValueError("The selected audio file does not exist.")
            info = meeting._source_audio_info(path)
            duration = float(info.get("duration_sec", 0) or 0)
            if duration <= 0:
                raise ValueError("The selected audio file contains no audio.")
            if duration > recording_limits.MEETING_MAX_SECONDS:
                raise ValueError(
                    "Meeting audio exceeds the four-hour maximum. "
                    "Split the recording and import each part separately."
                )
        except Exception as e:
            return {"ok": False, "message": str(e)}

        def _bg():
            try:
                meeting_id = meeting.process_audio_file(
                    path,
                    self._transcribe,
                    self.settings,
                    title="",
                    on_saved=lambda _meeting_id: self._send_webui_async(
                        {"cmd": "refresh", "what": "meetings"}
                    ),
                )
                if meeting_id:
                    self._send_webui_async({"cmd": "refresh", "what": "meetings"})
                else:
                    self._notify(
                        "Meeting import failed",
                        "Mumble could not decode or save that audio file.",
                    )
            except Exception as e:
                print(f"[meeting] import error: {e}")
                self._notify("Meeting import failed", str(e))

        threading.Thread(target=_bg, daemon=True).start()
        return {"ok": True, "processing": True, "duration_sec": duration}

    def _meeting_retry(self, meeting_id):
        """Retry a durable interrupted/failed meeting on the live STT engine."""
        recorder = getattr(self, "meeting_recorder", None)
        if recorder is None:
            return {"ok": False, "message": "Meeting recorder not ready."}
        if self.recording or self._processing or self.meeting_recording:
            return {"ok": False, "message": "Stop the active recording first."}
        record = meeting.meeting_store.get_meeting(meeting_id)
        if not record:
            return {"ok": False, "message": "Meeting not found."}
        if record.get("status") == "ready":
            return {"ok": True, "processing": False, "meeting_id": meeting_id}
        if record.get("status") == "failed":
            return {"ok": False, "message": (
                "The original recording is unavailable, so this meeting "
                "cannot be transcribed again.")}
        if record.get("status") not in ("processing", "interrupted"):
            return {"ok": False, "message": "This meeting cannot be retried."}
        meeting.meeting_store.update_meeting(
            meeting_id, status="processing", error=None
        )
        self._send_webui_async({"cmd": "refresh", "what": "meetings"})

        def _bg():
            recorder.process_pending(meeting_id)
            self._send_webui_async({"cmd": "refresh", "what": "meetings"})

        threading.Thread(
            target=_bg,
            name=f"meeting-retry-{meeting_id}",
            daemon=True,
        ).start()
        return {"ok": True, "processing": True, "meeting_id": meeting_id}

    def start_recording(self):
        self._trace_begin_dictation()
        # Veto only when there is genuinely no way to transcribe. Cloud
        # transcription mode runs with NO local model resident (it's unloaded to
        # free RAM, or deferred at boot), yet recording must still work — the
        # audio is captured identically and transcribed via the cloud provider,
        # with an on-demand local load as the fallback (see _transcribe). This
        # mirrors the boot-readiness invariant (`self.model is not None or
        # self._cloud_transcription_on()`).
        #
        # REGRESSION FIX: the cloud-STT feature unloads self.model when Cloud
        # mode is active, but this guard checked only `self.model is None`, so
        # turning on Cloud transcription silently broke ALL recording — both the
        # Home record button and the Ctrl+Win hotkey funnel through here. Tested
        # by test_recording_gate.py.
        if self.paused or not self._transcription_ready():
            self.busy = False
            self._trace_finish("not_ready", success=False)
            return
        # The wake detector owns an idle microphone stream. Close it before
        # normal dictation opens PortAudio. Meeting capture has its own stream,
        # so an active meeting does not prevent this foreground recording.
        # Resume this lease as soon as the dictation stream closes.
        if not self._pause_wake_word("dictation"):
            self._resume_wake_word("dictation")
            self._trace_finish("microphone_lease_failed", success=False)
            raise RuntimeError(
                "Voice activation did not release the microphone in time. "
                "Turn it off and try dictation again.")
        self.frames = []
        self._recorded_samples = 0
        self._dictation_limit_triggered = False
        self._q_acc = []   # fresh audio-quality accumulator per dictation
        # Reset the level-throttle baseline so the FIRST audio level of this
        # recording always reaches the island (stale value from the previous
        # recording's final level could otherwise swallow the opening update).
        self._last_level_queued = 0.0
        # Reset the mode-key window for this utterance. If the key is already held when
        # recording starts, open a window at t=0 (keyword spoken right at the start).
        self._rec_start = time.time()
        stat_clock = time.localtime(self._rec_start)
        self._dictation_stat_context = {
            "day": time.strftime("%Y-%m-%d", stat_clock),
            "hour": stat_clock.tm_hour,
            "duration": 0.0,
        }
        # THE BIG SHIFT: the per-utterance "mode" is simply whether the sticky
        # Prompt toggle is on. No key-window bookkeeping any more.
        with self.lock:
            self._mode_windows = []
            # ITEM 4: an AI mode is active this utterance if the island mode deck
            # selected one (active_mode) — Prompt or any other lane. Capture WHICH,
            # so _process forces that lane (previously hardcoded to "prompt").
            self._active_mode_start = getattr(self, "active_mode", None) or (
                "prompt" if self.prompt_mode_enabled else None)
            self._mode_active = bool(self._active_mode_start)
        # Show "Listening" FIRST — the pill must appear the instant recording
        # starts (owner v6 responsiveness). Opening the audio stream takes a few
        # ms; making the user wait on that I/O before any feedback is what made
        # activation feel laggy. The waveform sits at level 0 until audio arrives.
        # Legacy compatibility only: current Mumble Find never sets this flag;
        # it opens the separate local launcher and does not enter dictation.
        is_search = getattr(self, "_search_requested", False)
        rec_state = "search" if is_search else "listening"
        if self.island:
            self._tk_schedule(self._set_island_state_traced, rec_state)
            try:
                # The island's "armed" accent now means "Prompt mode is on".
                self._tk_schedule(self.island.set_armed, self.prompt_mode_enabled)
            except Exception:
                pass
        self._set_state(rec_state)
        self._trace_mark("audio_opening", audio_state="opening")
        try:
            self.stream = self._open_input_stream()
            self.stream.start()
        except Exception as exc:
            self._trace_finish(
                "audio_open_failed", success=False,
                error_class=type(exc).__name__,
            )
            raise
        self.recording = True
        self._trace_mark("audio_recording", audio_state="recording")
        # Launch streaming transcription worker — transcribes chunks in the
        # background while the user speaks, so on stop the final paste is
        # near-instant (only the last chunk needs decoding).
        self._stream_results = []
        self._stream_processed_samples = 0
        self._stream_inflight_samples = 0
        self._stream_session_id = getattr(self, "_stream_session_id", 0) + 1
        if not hasattr(self, "_stream_idle"):
            self._stream_idle = threading.Event()
        self._stream_idle.set()
        self._stream_done.clear()
        # Skip the live (streaming) worker entirely when:
        #  - Resource Saver Mode is on (decoding chunks mid-recording is exactly the
        #    extra background compute the saver exists to avoid), OR
        #  - this is an armed (Prompt-mode) dictation: _process only consumes streamed
        #    chunks on the `not mode_active` path, so for a Prompt dictation every
        #    chunk would be decoded and then thrown away (wasted CPU/battery) AND its
        #    confidence samples would pollute the audio-quality chip. _mode_active was
        #    just set under the lock above, so it's known here.
        # Leaving `_stream_worker_thread` as None makes the stop-path join a no-op,
        # and an empty `_stream_results` makes `_process` take its authoritative
        # single full pass over the whole audio — transcription still works end to
        # end, it just runs once at stop.
        if self.settings.get("resource_saver") or self._mode_active:
            self._stream_worker_thread = None
        else:
            self._stream_worker_thread = threading.Thread(
                target=self._stream_worker, daemon=True
            )
            self._stream_worker_thread.start()
        print("> listening")
        # Hide cold-start latency: warm the AI model now, in parallel with speaking.
        self._maybe_warm_ai()

    def _maybe_warm_ai(self):
        """Optionally pre-warm the AI engine to hide serverless cold-starts.

        OFF by default (`ai_warmup`): it's pointless on Cerebras (dedicated hardware,
        no cold-start) and it BURNS one of the 5-requests-per-minute free-tier slots
        on every record-start, which can push a real dictation over the limit (429)
        and force a local-builder fallback. Only useful on a cold-starting serverless
        engine — re-enable via the setting if you switch to one."""
        # Resource Saver Mode: never fire the cloud warm-up, regardless of the
        # `ai_warmup` setting — a warm-up is a speculative network/compute call
        # the saver exists to avoid (and it burns a free-tier request slot).
        if self.settings.get("resource_saver"):
            return
        if not self.settings.get("ai_warmup", False):
            return
        if not self.settings.get("pro_mode", True):
            return
        decision = processing_route.snapshot(
            self.settings, feature="dictation", lane="prompt")
        if not decision.ready:
            return
        if (time.time() - self._last_llm_ok) < 90:
            return  # warm enough — don't waste a call

        def run():
            try:
                provider_info = ai.PROVIDERS.get(decision.provider) or {}
                ai.cerebras_warm(
                    decision.api_key, decision.model,
                    url=provider_info.get("url", ""),
                    route_decision=decision,
                )
                self._last_llm_ok = time.time()  # worker is now warm
                print("AI warmed up")
            except Exception as e:
                print("warm-up skipped:", e)

        threading.Thread(target=run, daemon=True).start()

    # ----------------------------------------------------------- mode key (button)
    def _register_mode_key(self):
        """THE BIG SHIFT: the held Right-Shift "mode key" is retired. Mode is no
        longer armed by a key — Prompt is a sticky toggle (prompt_mode_enabled) and
        everything else is auto-inferred. This is now a deliberate no-op: it unhooks
        any stale binding from an older build and registers nothing, so a tap of
        Right-Shift is just a normal Shift again. Kept (not deleted) so the boot
        registration table and any legacy caller stay valid."""
        try:
            bindings.unregister(self._hk_mode)
        except Exception:
            pass
        self._hk_mode = None
        self._mode_key_down = False

    def _clarify_window_active(self):
        """True while a clarification is on screen and the user may RE-SPEAK with
        the mode key instead of Ctrl+Win (owner v6): the 'Which mode?' picker is
        open, or a suggest/convert/redo chip is within its linger window."""
        if self.island is not None and getattr(self.island, "_picker_open", False):
            return True
        return time.time() < self._clarify_until

    def _on_mode_down(self, _e=None):
        if self._mode_key_down:
            return  # ignore auto-repeat while held
        self._mode_key_down = True
        if self.recording:
            with self.lock:
                self._mode_active = True
                self._mode_windows.append([max(0.0, time.time() - self._rec_start), None])
            if self.island:
                try:
                    self._tk_schedule(self.island.set_armed, True)
                except Exception:
                    pass
        elif self._clarify_window_active():
            # Shift-to-respeak: a clarification chip is up — hold the mode key to
            # start a fresh ARMED dictation (no Ctrl+Win). Speak the mode (+ content);
            # release to process those words.
            with self.lock:
                if self.busy or self.paused or self._processing:
                    return
                if self._clarify_token is None:
                    return  # already resolved by the fade-fallback paste
                self.busy = True
                self._clarify_token = None   # consume — stops the fade-fallback
            self._shift_ptt = True
            self._clarify_until = 0.0  # consumed
            if self.island is not None:
                try:
                    # Freeze the fading chip while the key is held, then close any
                    # legacy picker (a no-op when none is open).
                    self._tk_schedule(self.island.set_armed, True)
                    self._tk_schedule(self.island.close_picker)
                except Exception:
                    pass
            threading.Thread(target=self._safe_start, daemon=True).start()

    def _on_mode_up(self, _e=None):
        self._mode_key_down = False
        if self.recording:
            with self.lock:
                if self._mode_windows and self._mode_windows[-1][1] is None:
                    self._mode_windows[-1][1] = max(0.0, time.time() - self._rec_start)
        if self.island:
            try:
                self._tk_schedule(self.island.set_armed, False)
            except Exception:
                pass
        if self._shift_ptt:
            # release ends the push-to-talk respeak → stop + process it
            self._shift_ptt = False
            if self.recording:
                with self.lock:
                    self.busy = True
                threading.Thread(target=self._safe_stop, daemon=True).start()

    def _watch_mode_key(self):
        """THE BIG SHIFT: there is no held mode key to watch any more, so this
        safety-net poller is retired (a no-op). It used to re-sync the "armed"
        indicator to the real Right-Shift state every ~200ms; the island's Prompt
        accent now follows the sticky `prompt_mode_enabled` toggle, which has no
        stuck-key failure mode. Kept as a no-op so the boot call stays valid and
        the idle loop costs nothing."""
        return

    def _words_in_windows(self, words, windows):
        """The transcript words whose timing falls inside a button press window — the
        most precise read of 'what did they say while holding the key'. None if we have
        no timestamps/windows (caller then falls back to scanning the first few words)."""
        if not words or not windows:
            return None
        picked = []
        for w in words:
            ws, we = float(w.get("start", 0.0)), float(w.get("end", 0.0))
            for a, b in windows:
                hi = 1e9 if b is None else float(b)
                if we >= float(a) - 0.15 and ws <= hi + 0.15:
                    t = (w.get("word") or "").strip()
                    if t:
                        picked.append(t)
                    break
        return picked or None

    # NOTE: _maybe_apply_suggestion / _match_keyword_template (the pre-Big-Shift
    # "press the key, say the mode, resend" chip + prompt-keyword templating) were
    # removed here — dead since _process stopped detecting modes (STATUS T6/T8).
    # The port controllers still carry their own copies until they adopt the Big
    # Shift _process.

    def _clarify_chip_text(self):
        """Short, clear clarification-chip label that names the REAL mode key, so the
        user knows exactly how to recover — e.g. 'Hold Right shift to redo'. Used by
        every low-confidence clarify path so they read consistently."""
        try:
            keyname = bindings.pretty(self.mode_key) or "the mode key"
        except Exception:
            keyname = "the mode key"
        return f"Hold {keyname} to redo"

    def _clarify_convert(self):
        """'Convert' was armed but named no target and had nothing to convert. Show a
        short, clear chip ('Hold <key> to redo') that fades in ~2s. While it's up,
        holding the mode key respeaks the format + content directly — the
        _clarify_token is what ENABLES that respeak (see _on_mode_down)."""
        self._suggested_mode = "convert"   # respeak resolves the target
        self._suggest_ts = time.time()
        self._clarify_until = time.time() + 2.4
        self._clarify_token = object()
        if self.island:
            try:
                self._tk_schedule(self.island.suggest, self._clarify_chip_text())
            except Exception:
                pass

    def _suggest_redo(self):
        """Couldn't tell which mode and there's nothing to re-process (e.g. the mic
        caught nothing while the mode key was held). Shows a short, clear chip
        ('Hold <key> to redo') that fades in ~2s; holding the mode key respeaks
        (the _clarify_token enables it — see _on_mode_down)."""
        self._suggested_mode = "redo"
        self._suggest_ts = time.time()
        self._clarify_until = time.time() + 2.4
        self._clarify_token = object()
        if self.island:
            try:
                self._tk_schedule(self.island.suggest, self._clarify_chip_text())
            except Exception:
                pass

    # ---- mode-ambiguity recovery: ASK which mode, don't say "speak again" -----
    def _offer_mode_pick(self, raw, ai_guess, clean, *, target_lease=None,
                         operation_id=None):
        """Low-confidence mode recovery (0.9 rework). The old click-picker popup
        ("Which mode? — pick one") confused users and lingered ~8s. Instead show a
        brief, self-explaining island chip that FADES in ~2s: hold the mode key
        within that window and say what you want to redo those words; do nothing and
        the polished plain text pastes, so the dictation is never lost.

        Exactly one of {respeak, fade-paste} resolves it — guarded by
        self._clarify_token under self.lock, so neither double-pastes nor drops."""
        modes = ("text", "prompt", "email", "reply")
        guess = ai_guess if ai_guess in modes else ""
        clean = (clean or "").strip()
        # Arm the Shift-to-respeak window; preserve the words for any reuse path.
        self._last_raw = (raw or "").strip()
        self._suggested_mode = guess or "redo"
        self._suggest_ts = time.time()
        self._clarify_until = time.time() + 2.4   # ~matches the 2s chip fade
        token = object()
        self._clarify_token = token
        stat_context = dict(getattr(self, "_pending_stat_context", {}) or {})
        if self.island is None:
            # Headless: no chip surface — just paste the polished default.
            threading.Thread(target=self._finalize_text,
                             args=(clean, stat_context),
                             kwargs={"target_lease": target_lease,
                                     "operation_id": operation_id},
                             daemon=True).start()
            return
        # The whole hint is the chip label (state=="hint"); name the REAL mode key.
        self._tk_schedule(self.island.suggest, self._clarify_chip_text())

        def _fade_fallback():
            # No respeak within the window → paste the polished plain text. Resolve
            # atomically against the respeak path: fire only while THIS
            # clarification is still active and unconsumed.
            time.sleep(2.5)
            with self.lock:
                if self._clarify_token is not token or self.busy:
                    return
                self._clarify_token = None
                self._clarify_until = 0.0
            self._finalize_text(
                clean, stat_context, target_lease=target_lease,
                operation_id=operation_id)

        threading.Thread(target=_fade_fallback, daemon=True).start()

    def _reprocess(self, raw, mode, *, target_lease=None, operation_id=None):
        """Re-run a USER-CHOSEN mode on a PRESERVED transcript — the click-to-pick
        recovery from the mode picker (owner v6). The spoken words are never lost:
        they're processed in the mode you chose and pasted. Self-contained
        (history + stats + paste + flash), so it doesn't depend on the original
        dictation's _process context."""
        if not raw or not mode:
            self._idle()
            return
        self.busy = True
        try:
            if self.island:
                self._tk_schedule(self.island.set_building, mode)
            clip = 1 if mode == "reply" else 0
            # An explicit "text" pick means "just clean it" — pass mode_active=False
            # so the plain-polish lane runs WITHOUT the second-opinion picker (else a
            # low-confidence text polish could re-open the picker in a loop). Real
            # modes run their focused lane and never re-trigger the picker.
            m, out, used_offline = self._generate(
                raw, mode, raw, clip, mode != "text", None, None,
                target_lease=target_lease, operation_id=operation_id)
            if not out:
                self._idle()
                return
            stat_context = dict(getattr(self, "_pending_stat_context", {}) or {})
            duration = stat_context.get("duration", 0.0)
            entry = self.history.add(out, m, duration, raw=raw)
            if not entry:
                self._notify("Couldn't save the result",
                             "Nothing was pasted. Check Mumble's data-folder permissions and try again.")
                self._idle()
                return
            try:
                if not self.stat_store.record(
                        entry["words"], duration, m,
                        day=stat_context.get("day"),
                        hour=stat_context.get("hour")):
                    print("reprocess stats save failed")
            except Exception as e:
                print("reprocess stats error:", e)
            self._send_webui_async({"cmd": "refresh", "what": "history"})
            result = self._paste(
                out, source="mode_reprocess", target_lease=target_lease,
                operation_id=operation_id)
            print(f"[reprocess · {m}] {out!r}")
            if self.island:
                self._tk_schedule(self.island.flash, m,
                                  offline=used_offline,
                                  pasted=result.confirmed,
                                  outcome=result.outcome.value,
                                  reason=result.reason, message=result.message,
                                  cleanup_warning=result.cleanup_warning)
            self._remember_correction_candidate(
                out, m, result.confirmed, result.target_lease)
            self._set_state("idle")
        except Exception as e:
            print("reprocess error:", e)
            self._idle()
        finally:
            self.busy = False

    def _finalize_text(self, clean, stat_context=None, *, target_lease=None,
                       operation_id=None):
        """Paste the polished plain-text default (the mode picker was dismissed).
        Nothing is lost even when the user doesn't pick a mode."""
        clean = (clean or "").strip()
        if not clean:
            self._idle()
            return
        self.busy = True
        try:
            # The picker window just closed; give Windows a beat to hand focus
            # back to the user's field before we paste (the _reprocess path is
            # covered by its AI-call latency; this immediate path is not).
            time.sleep(0.18)
            stat_context = dict(stat_context or {})
            duration = stat_context.get("duration", 0.0)
            entry = self.history.add(clean, "text", duration)
            if not entry:
                self._notify("Couldn't save the dictation",
                             "Nothing was pasted. Check Mumble's data-folder permissions and try again.")
                self._idle()
                return
            try:
                if not self.stat_store.record(
                        entry["words"], duration, "text",
                        day=stat_context.get("day"),
                        hour=stat_context.get("hour")):
                    print("finalize-text stats save failed")
            except Exception as e:
                print("finalize-text stats error:", e)
            self._send_webui_async({"cmd": "refresh", "what": "history"})
            result = self._paste(
                clean, source="finalize_text", target_lease=target_lease,
                operation_id=operation_id)
            if self.island:
                self._tk_schedule(self.island.flash, "text",
                                  pasted=result.confirmed,
                                  outcome=result.outcome.value,
                                  reason=result.reason, message=result.message,
                                  cleanup_warning=result.cleanup_warning)
                self._maybe_island_tip(result.confirmed)  # ITEM 19
            self._remember_correction_candidate(
                clean, "text", result.confirmed, result.target_lease)
            self._set_state("idle")
        except Exception as e:
            print("finalize-text error:", e)
            self._idle()
        finally:
            self.busy = False

    def _mark_llm_ok(self):
        self.pro_key_failed = False
        self._pro_notified = False
        self._last_llm_ok = time.time()

    def _open_input_stream(self):
        """Open the chosen mic. If it's unavailable (unplugged, busy, or its device index
        drifted — common when you swap headsets), fall back to the system default and self-heal
        the saved choice, so a bad mic never throws an 'error' the moment you press the hotkey."""
        dev = self.settings.get("mic_device", None)
        try:
            return sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                device=dev,
                callback=self._audio_cb,
            )
        except Exception as e:
            if dev is None:
                raise
            print(
                f"mic device {dev!r} unavailable ({e}); falling back to system default"
            )
            self.settings.set("mic_device", None)
            self._notify(
                "Microphone",
                "Your saved mic wasn't available — using the system "
                "default. Pick it again in Settings if you want.",
            )
            return sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                device=None,
                callback=self._audio_cb,
            )

    def stop_recording(self):
        # Idempotent: a double hotkey press (or a hotkey racing the command
        # server) could enter stop twice before `recording` flipped, and the
        # second run would reach np.concatenate(self.frames) after the first
        # already cleared it → "need at least one array to concatenate". Flip the
        # flag under the lock and bail out of a duplicate stop.
        with self.lock:
            if not self.recording:
                return
            self.recording = False
            # The worker uses this same lock before claiming a chunk. It may
            # finish one existing claim, but can never start another after stop.
            self._stream_done.set()
        # Ordinary dictation belongs to the destination selected when Stop is
        # requested. Processing may take seconds, but it must never send the
        # result back to the field that happened to be active at Start.
        self._dictation_insertion_operation_id = uuid.uuid4().hex
        module = getattr(self, "_insertion_module", None)
        if module is not None:
            receipt = module.begin(
                self._dictation_insertion_operation_id,
                "dictation",
            )
            self._dictation_insertion_lease = receipt.target_lease
        else:
            self._dictation_insertion_lease = self._make_insertion_lease(
                "dictation", "stop", mumble_displaced_target=False)
        self._trace_mark("audio_stopped", audio_state="stopping")
        release_at = time.perf_counter()
        self._dictation_timing = {
            "release_at": release_at,
            "stream_drain_ms": 0.0,
            "raw_ready_at": None,
            "output_ready_at": None,
            "paste_done_at": None,
        }
        # Device removal can make ``stop()`` raise.  Closing is still required,
        # and the audio already captured is still valid, so isolate both calls
        # and continue through transcription instead of abandoning the utterance.
        stream = self.stream
        self.stream = None
        if stream is not None:
            try:
                stream.stop()
            except Exception as e:
                print("audio stream stop failed:", e)
            try:
                stream.close()
            except Exception as e:
                print("audio stream close failed:", e)
        self._trace_mark("audio_closed", audio_state="closed")
        self._resume_wake_word("dictation")
        if self.island:
            self._tk_schedule(self.island.set_level, 0.0)
            try:
                self._tk_schedule(self.island.set_armed, False)
            except Exception:
                pass
        if not self.frames:
            self._search_requested = False
            self._idle()
            self._dictation_timing = None
            self._trace_finish("no_audio", success=False)
            return
        # Flip the pill to "Transcribing" the MOMENT the user stops, before an
        # already-running chunk is drained into the committed stream result.
        if self.island:
            self._tk_schedule(self.island.set_state, "transcribing")
        self._set_state("transcribing")
        # Drain only a chunk already in flight. It publishes its exact covered
        # range for this session, so we reuse it instead of waiting three seconds
        # and then decoding the same audio again.
        drain_started = time.perf_counter()
        idle = getattr(self, "_stream_idle", None)
        if idle is not None and not idle.wait(timeout=STREAM_DRAIN_TIMEOUT):
            print("stream decode did not drain within 25s — cancelling dictation")
            self._notify(
                "Transcription stalled",
                "The local transcription engine stopped responding. Restart "
                "Mumble before trying again.",
            )
            self.frames = []
            self._search_requested = False
            self._dictation_timing = None
            self._idle()
            self._trace_finish("stream_stalled", success=False)
            return
        self._dictation_timing["stream_drain_ms"] = round(
            (time.perf_counter() - drain_started) * 1000.0, 1)
        self._trace_mark(
            "stream_drain_finished",
            wait_ms=self._dictation_timing["stream_drain_ms"],
        )
        worker = self._stream_worker_thread
        if worker and worker.is_alive():
            worker.join(timeout=0.05)  # cleanup only; inference is already idle
        audio = np.concatenate(self.frames, axis=0).flatten()
        self.frames = []
        duration = len(audio) / SAMPLE_RATE
        if duration < self.settings.get("min_seconds", 0.3):
            self._search_requested = False
            self._dictation_timing = None
            self._idle()
            self._trace_finish("too_short", success=False,
                               audio_duration_ms=duration * 1000.0)
            return
        # Prompt toggle ON = Prompt mode. OFF = plain text (clean, punctuated).
        with self.lock:
            mode_active = bool(self._mode_active)
        windows = None
        # Mark the pipeline in-flight so a new press can't start a recording on top
        # of it (recording/busy are both False here). finally guarantees release —
        # a stuck flag would block ALL future recordings, so it must always clear.
        self._processing = True
        try:
            self._process(audio, duration, mode_active, windows)
        finally:
            self._processing = False
            self._restore_meeting_island()

    def _transcribe(self, audio, want_words=False):
        """Transcribe audio and return the raw transcript untouched — all cleanup,
        mode detection, and terminology fixes happen later in the AI pass.

        Transcription is on-device (faster-whisper) by DEFAULT — audio never leaves
        the machine. An advanced, opt-in Cloud mode (Settings → Transcription)
        uploads the utterance to a low-latency hosted Whisper endpoint instead, for
        faster voice-to-text on modest hardware. Cloud is used only for plain
        dictation; it falls back to local on ANY error so a flaky network never
        loses a dictation.

        When `want_words` is True (mode key was held) we ALWAYS use local: the
        mode-key feature needs per-word timestamps to map the button window onto the
        spoken keyword, which the cloud path doesn't provide. Returns a plain string
        normally, or (text, words) when want_words=True."""
        invocation_snapshot = self._transcription_snapshot()
        if not want_words and self._cloud_transcription_on(
            invocation_snapshot.route
        ):
            text = self._cloud_transcribe(audio, invocation_snapshot)
            # Treat an empty/whitespace cloud result as a non-result, not success:
            # a provider that returns {"text": ""} on a real utterance would
            # otherwise short-circuit the local fallback and silently drop the
            # dictation. Mirror the empty-is-not-a-result convention used elsewhere
            # (the streaming worker and _process both gate on truthy text).
            if text and text.strip():
                return text
            # cloud failed/empty → fall through to local so the dictation still lands
        return self._local_transcribe(audio, want_words=want_words)

    def _transcription_snapshot(self):
        """Freeze permission and every cloud speech-to-text input once."""
        return processing_route.snapshot_inputs(
            self.settings,
            feature="dictation",
            lane="speech_to_text",
            local_model_ready=getattr(self, "model", None) is not None,
        )

    def _cloud_transcription_on(self, route_decision=None):
        """True only when the user has explicitly switched to Cloud mode AND a key
        is present for a supported chosen provider (otherwise stay on-device)."""
        decision = route_decision or self._transcription_snapshot().route
        return bool(decision.ready and decision.cloud_augmented)

    def _transcription_ready(self):
        """SINGLE SOURCE OF TRUTH for 'can Mumble turn speech into text right now?'
        True when a local model is resident OR Cloud transcription is active (which
        records and transcribes with no local model loaded).

        Boot-readiness AND the record gate (start_recording) BOTH go through here so
        they can never disagree. They previously drifted — boot treated cloud mode as
        ready while start_recording still demanded `self.model is not None` — which
        silently broke ALL recording the moment Cloud transcription was enabled
        (the v0.9 control-window regression). Keep both callers on this method."""
        return self.model is not None or self._cloud_transcription_on()

    def _cloud_transcribe(self, audio, invocation_snapshot=None):
        """Run one cloud transcription. Returns the text, or None on any failure
        (logged) so the caller falls back to local. On the first failure per session
        the user gets a one-time toast so they know cloud STT is degraded."""
        invocation_snapshot = (
            invocation_snapshot or self._transcription_snapshot()
        )
        provider = invocation_snapshot.route.provider
        ordinal = self._trace_next_inference_ordinal()
        self._trace_mark(
            "inference_started",
            inference_ordinal=ordinal,
            route="cloud",
            provider=provider,
            device="remote",
        )
        try:
            t0 = time.time()
            text = transcription.transcribe(audio, invocation_snapshot)
            print(f"[cloud-stt] {time.time() - t0:.2f}s "
                  f"({provider})")
            self._trace_mark(
                "inference_finished",
                inference_ordinal=ordinal,
                route="cloud",
                provider=provider,
                success=bool(text and text.strip()),
            )
            return text
        except Exception as e:
            self._trace_mark(
                "inference_finished",
                inference_ordinal=ordinal,
                route="cloud",
                provider=provider,
                success=False,
                error_class=type(e).__name__,
            )
            print(f"[cloud-stt] failed, falling back to local: {e}")
            if not self._cloud_stt_failed:
                self._cloud_stt_failed = True
                self._notify(
                    "Cloud transcription",
                    "Cloud transcription failed — using on-device transcription "
                    "instead. Check your key and internet connection in Settings.")
            return None

    def _local_transcribe(self, audio, beam=1, want_words=False):
        """Transcribe using local faster-whisper. Serialized via _tx_lock (with a
        bounded wait) as cheap insurance so two transcribes can never run the model
        concurrently (faster-whisper isn't safe for concurrent transcribe).

        beam=1 (greedy) is the default for near-real-time latency — on a fast model
        (tiny.en) the decode is the bottleneck, and the cloud AI repairs the small
        accuracy loss in the same pass. Raise beam for higher raw accuracy.

        With want_words=True, enables word-level timestamps and returns (text, words),
        where words = [{'word','start','end'}, ...] — used to align the mode-key window."""
        lang = self.settings.get("language", "en")
        with self.lock:
            model = self.model
        if model is None:
            # Cloud mode deferred the local load — bring it up now. This path is
            # hit on a cloud failure/fallback, or a mode-key dictation that needs
            # local word timestamps (cloud can't supply them).
            if self._ensure_local_model():
                with self.lock:
                    model = self.model
            if model is None:
                return ("", []) if want_words else ""

        # Boost process priority during transcription — gives Mumble more CPU time
        # slices, reducing transcription latency by 15-30% on busy systems.
        old_priority = self._boost_priority()
        # Bounded wait: if a previous transcribe is wedged holding the lock, don't
        # block the whole pipeline forever — bail so the island returns to idle.
        lock_wait_started = time.perf_counter()
        if not self._tx_lock.acquire(timeout=25):
            self._trace_mark(
                "transcription_lock_acquired",
                wait_ms=25000.0,
                route="local",
                success=False,
            )
            print("transcribe lock busy >25s — skipping to avoid a stuck pipeline")
            self._restore_priority(old_priority)
            return ("", []) if want_words else ""
        lock_wait_ms = (time.perf_counter() - lock_wait_started) * 1000.0
        ordinal = self._trace_next_inference_ordinal()
        self._trace_mark(
            "transcription_lock_acquired",
            wait_ms=lock_wait_ms,
            inference_ordinal=ordinal,
            route="local",
            success=True,
        )
        try:
            # Personal vocabulary terms bias the decoder toward the user's
            # names/jargon at the STT level (faster-whisper `hotwords`) — the
            # cheapest, most effective accuracy lever for proper nouns.
            terms = self.settings.get("vocabulary_terms", []) or []
            # Sanitize terms before passing to STT to prevent prompt injection
            # via special chars, newlines, or excessive length
            terms = formatting.sanitize_hotwords(terms)
            hotwords = ", ".join(terms[:50]) if terms else None
            kwargs = {}
            if want_words:
                # Suppress Whisper's classic silence hallucinations ("Thank
                # you." etc.) — only effective with word timestamps enabled.
                kwargs["hallucination_silence_threshold"] = 2.0
            _perf_t0 = time.time()  # ITEM 20: realtime-factor timer (see finally)
            self._trace_mark(
                "inference_started",
                inference_ordinal=ordinal,
                model=(getattr(self, "_stt_model", None)
                       or self.settings.get("model", "unknown")),
                device=getattr(self, "_stt_device", "unknown"),
                compute_type=getattr(self, "_stt_compute", "unknown"),
                vad_enabled=True,
                route="local",
            )
            segs, _ = model.transcribe(
                audio,
                language=lang,
                beam_size=beam,
                best_of=1,
                # A temperature list makes faster-whisper retry difficult
                # segments repeatedly. Those retries caused the observed
                # 15–268 second Windows outliers. Dictation uses one greedy pass.
                temperature=0.0,
                vad_filter=True,
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
                log_prob_threshold=-1.0,
                word_timestamps=want_words,
                # Ordinary Text mode does not consume timestamps. Avoid decoding
                # timestamp tokens; word-aligned modes explicitly opt back in.
                without_timestamps=not want_words,
                hotwords=hotwords,
                **kwargs,
            )
            if want_words:
                text_parts, words = [], []
                for s in segs:
                    self._note_quality(s)
                    # Segment confidence gating: drop segments that are likely
                    # hallucinations (silence, looping, very low confidence).
                    if self.settings.get("confidence_gating", True):
                        keep, reason = formatting.gate_segment(s)
                        if not keep:
                            print(f"[gate] dropped: {reason}")
                            continue
                    text_parts.append(s.text)
                    for w in getattr(s, "words", None) or []:
                        words.append(
                            {
                                "word": w.word,
                                "start": w.start,
                                "end": w.end,
                                "prob": getattr(w, "probability", 1.0),
                            }
                        )
                return "".join(text_parts).strip(), words
            parts = []
            for s in segs:
                self._note_quality(s)
                # Segment confidence gating
                if self.settings.get("confidence_gating", True):
                    keep, reason = formatting.gate_segment(s)
                    if not keep:
                        print(f"[gate] dropped: {reason}")
                        continue
                parts.append(s.text)
            return "".join(parts).strip()
        finally:
            self._trace_mark(
                "inference_finished",
                inference_ordinal=ordinal,
                route="local",
            )
            self._tx_lock.release()
            self._restore_priority(old_priority)
            # ITEM 20 (owner 2026-06-29): local-transcription performance check.
            # Realtime factor RTF = decode_time / audio_seconds. <1.0 means Mumble
            # transcribes FASTER than you speak (healthy). Report slow runs without
            # assuming the cause: decoder retries and duplicate work have caused
            # application-level outliers in older builds as well as hardware load.
            try:
                secs = max(0.05, len(audio) / float(SAMPLE_RATE))
                elapsed = time.time() - _perf_t0
                rtf = elapsed / secs
                dev = getattr(self, "_stt_device", "cpu")
                comp = getattr(self, "_stt_compute", "int8")
                mdl = getattr(self, "_stt_model", None) or self._effective_model()
                verdict = "healthy (faster than realtime)" if rtf <= 1.0 else (
                    "ok" if rtf <= 1.5 else "SLOW")
                note = ""
                if rtf > 1.5 and dev == "cpu":
                    note = (f" — this decode was slow on '{mdl}'. Mumble now uses "
                            "one-pass decoding; if this persists, enable Resource "
                            "Saver or choose a smaller local model.")
                elif rtf > 2.0:
                    note = (f" — unusually slow on {dev}; if this persists with a "
                            "small model, it's environmental (CPU load / thermal).")
                print(f"[perf] local STT {verdict}: {elapsed:.2f}s for {secs:.1f}s "
                      f"audio · RTF {rtf:.2f} · {dev}/{comp}/{mdl}{note}")
            except Exception:
                pass

    def _note_quality(self, seg):
        """Accumulate one segment's decoder confidence for the audio-quality
        score (duration-weighted, so a long clean stretch outweighs a short
        mumble). Drained once per dictation by _take_quality."""
        try:
            lp = float(getattr(seg, "avg_logprob", 0.0) or 0.0)
            dur = max(0.05, float(getattr(seg, "end", 0) or 0)
                      - float(getattr(seg, "start", 0) or 0))
            if not hasattr(self, "_q_acc"):
                self._q_acc = []
            self._q_acc.append((lp, dur))
        except Exception:
            pass

    def _take_quality(self):
        """Collapse the accumulated confidences into good / fair / bad — the
        per-transcript audio-quality chip. avg_logprob is whisper's own
        per-token confidence: above −0.35 is a clean signal; below −0.7 the
        decoder was struggling (noise, distance, clipped mic)."""
        acc = getattr(self, "_q_acc", None) or []
        self._q_acc = []
        if not acc:
            return None
        total = sum(d for _, d in acc)
        mean = sum(lp * d for lp, d in acc) / max(total, 1e-6)
        return "good" if mean > -0.35 else ("fair" if mean > -0.7 else "bad")

    def _process(self, audio, duration, mode_active=False, windows=None):
        # Freeze the Stop-time insertion context before any shaping or mode
        # clarification can yield control. Later focus is never a new target.
        insertion_lease = getattr(self, "_dictation_insertion_lease", None)
        insertion_operation_id = getattr(
            self, "_dictation_insertion_operation_id", None)
        timing = getattr(self, "_dictation_timing", None)
        stat_context = dict(getattr(self, "_dictation_stat_context", {}) or {})
        if not stat_context:
            now = time.localtime()
            stat_context = {
                "day": time.strftime("%Y-%m-%d", now),
                "hour": now.tm_hour,
            }
        stat_context["duration"] = duration
        self._pending_stat_context = stat_context
        # Snapshot the AI/processing configuration at the START of this dictation
        # run so that settings changes mid-pipeline do NOT affect the in-progress
        # run (VAL-CROSS-020). Nested preferences and vocabulary are copied too,
        # so later in-place edits cannot alter the invocation already underway.
        _snap = {
            "pro_mode": self.settings.get("pro_mode", True),
            "local_only_mode": self.settings.get("local_only_mode", False),
            "llm_provider": self.settings.get("llm_provider", "cerebras"),
            "english_only": self.settings.get("english_only", True),
            "foreign_mode": self.settings.get("foreign_mode", False),
            "foreign_languages": list(
                self.settings.get("foreign_languages", []) or []
            ),
            "format_enabled": self.settings.get("format_enabled", True),
            "instant_text": self.settings.get("instant_text", True),
            **processing_route.capture_text_provider_settings(
                self.settings, supported_providers=TEXT_PROCESSING_PROVIDERS
            ),
            "user_name": self.settings.get("user_name", ""),
            "prompt_prefs": copy.deepcopy(
                self.settings.get("prompt_prefs", {}) or {}
            ),
            "primary_language": self.settings.get("primary_language", ""),
            "vocabulary": copy.deepcopy(
                self.settings.get("vocabulary", {}) or {}
            ),
            "vocabulary_terms": list(
                self.settings.get("vocabulary_terms", []) or []
            ),
            "polish_aggressiveness": self.settings.get(
                "polish_aggressiveness", "Light"
            ),
            "rpunct_enabled": self.settings.get("rpunct_enabled", True),
            "modes": copy.deepcopy(self.settings.get("modes", {}) or {}),
            "_local_model_ready": local_engine.local_llm_ready(),
        }

        # Consume the search request up front so an error or early return can
        # never leave a stale flag that would hijack the NEXT dictation into a
        # browser search.
        search_requested = getattr(self, "_search_requested", False)
        self._search_requested = False
        try:
            # Use streaming results as the base transcription when available —
            # but ONLY for un-armed plain dictation. An armed (mode-key)
            # utterance needs word timestamps across the WHOLE recording to map
            # the keyword to the button window, and the streamed chunks carry
            # none — so armed dictations always take the authoritative full
            # pass below (accuracy over latency when a mode is intended).
            # Snapshot the streaming state as ONE consistent pair under the lock:
            # the worker may still be mid-chunk if its join timed out, and reading
            # the text list and the sample seam separately could drop a chunk.
            with self.lock:
                stream_results = list(self._stream_results)
                processed_samples = max(0, self._stream_processed_samples)
            if stream_results and not mode_active:
                streaming_base = " ".join(stream_results)
                # The seam: the worker counted EXACTLY how many samples its
                # text covers (one unit — samples — on both sides). The tail is
                # everything from that index on; slicing anywhere else drops or
                # repeats speech at the boundary.
                print(
                    f"[stream] {len(stream_results)} chunks / "
                    f"{processed_samples} samples → {streaming_base[:80]!r}..."
                )
                words = []
                tail_audio = (
                    audio[processed_samples:]
                    if processed_samples < len(audio)
                    else audio[:0]  # fully covered — never re-decode (dup text)
                )
                tail_text = ""
                # Skip micro-tails: whisper on a fraction of a word tends to
                # hallucinate; anything meaningful is >= 0.25s.
                if len(tail_audio) >= int(SAMPLE_RATE * 0.25):
                    try:
                        tail_text = self._local_transcribe(tail_audio, beam=1)
                    except Exception as e:
                        print("tail transcription failed:", e)
                if tail_text and tail_text.strip():
                    raw = streaming_base + " " + tail_text.strip()
                else:
                    raw = streaming_base
            else:
                # Short recording, worker didn't fire, or the mode key was held
                # (armed needs full word timestamps): authoritative full pass.
                try:
                    result = self._transcribe(audio, want_words=mode_active)
                    raw, words = result if isinstance(result, tuple) else (result, [])
                except Exception as e:
                    print("transcription error:", e)
                    self._trace_finish(
                        "transcription_failed", success=False,
                        error_class=type(e).__name__,
                    )
                    self._notify("Transcription failed", str(e))
                    self._set_state("error")
                    time.sleep(1.0)
                    self._idle()
                    return
            if not raw:
                # Total mic failure WHILE the mode button was held: don't lose the press —
                # tell the user to redo it on the island instead of going silent.
                if mode_active:
                    self._suggest_redo()
                self._trace_finish("no_transcript", success=False)
                self._idle()
                return

            if timing is not None:
                timing["raw_ready_at"] = time.perf_counter()
            self._trace_mark("final_transcript_ready")

            # Resolve the lane and gather mutable context exactly once before
            # any text shaping begins. The resulting immutable snapshot is the
            # only settings/context input passed through generation.
            if mode_active:
                det_mode = getattr(self, "_active_mode_start", None) or "prompt"
                clip_count = 0
            else:
                det_mode, clip_count = "text", 0
            context, context_strict = self._gather_context(
                det_mode, clip_count, mode_active
            )
            if not context:
                context_policy = "none"
            elif context.startswith("[Conversation]"):
                context_policy = "captured_conversation"
            elif "highlighted selection" in context or context_strict:
                context_policy = "highlighted_selection"
            elif "latest clipboard" in context:
                context_policy = "latest_clipboard"
            else:
                context_policy = "explicit_context"
            if det_mode == "prompt":
                try:
                    conversation = self._conv_context_for_prompt()
                    if conversation:
                        context = conversation + (
                            "\n\n" + context if context else ""
                        )
                        context_policy = (
                            "captured_conversation+" + context_policy
                            if context_policy != "none"
                            else "captured_conversation"
                        )
                except Exception:
                    pass
            _input_snapshot = processing_route.snapshot_inputs(
                _snap,
                feature=(
                    det_mode
                    if det_mode in ("prompt", "email", "reply")
                    else "dictation"
                ),
                lane=det_mode,
                context=context,
                context_policy=context_policy,
                context_strict=context_strict,
                local_model_ready=_snap["_local_model_ready"],
            )

            # Personal vocabulary, two passes before anything downstream sees
            # the transcript: (1) explicit wrong=right pairs (advanced), then
            # (2) term matching — high-confidence phonetic/fuzzy hits on the
            # user's term list are corrected automatically; medium-confidence
            # candidates are offered to the polish AI later (annotate_vocab_terms).
            try:
                raw = formatting.apply_vocabulary(
                    raw, _input_snapshot.vocabulary_dict()
                )
                raw = formatting.apply_vocabulary_terms(
                    raw, _input_snapshot.vocabulary_terms
                )
            except Exception as e:
                print("vocabulary error:", e)

            self._active_keyword_template = ""  # always initialized
            window_words = None
            via_convert = None

            # Mode resolution: an island mode is active → force THAT lane (ITEM 4 —
            # was hardcoded to "prompt"; now Prompt/Email/List/Reply per the deck).
            # No mode active → plain, clean, punctuated text only (no inference).
            det_request = raw

            # Cloud-dominance routing (local_engine.route): cloud is the primary
            # authority whenever a key is present and local-only is OFF; otherwise
            # the local engine runs best-effort and never hard-blocks. Same
            # cloud-when-key behaviour as before, now via the tested router plus the
            # local_only_mode privacy toggle. Uses the _snap config captured at
            # process start so settings changes mid-run don't affect the route
            # (VAL-CROSS-020).
            _route = _input_snapshot.route
            will_cloud = _route.cloud_augmented
            instant_text = _input_snapshot.instant_text and not mode_active
            if instant_text:
                will_cloud = False

            # Foreign Mode, fully on-device: when shaping stays LOCAL, run the local
            # phonetic booster so high-confidence foreign terms the English model
            # mis-decoded (e.g. "juz"→"just") are corrected on the edge; flags are
            # stripped for clean output. Cloud-augmented runs skip it (the cloud lane
            # resolves terms with full context). It runs for a MULTILINGUAL user
            # automatically, OR for ANYONE who flips the island's Foreign toggle on
            # (the toggle's whole purpose — an English-primary user flagging a
            # dictation that happens to carry foreign terms). English-primary users
            # who never touch the toggle are still never affected.
            # Uses _snap for isolation (VAL-CROSS-020).
            _foreign_forced = bool(_snap["foreign_mode"])
            if (not will_cloud
                    and (not bool(_snap["english_only"])
                         or _foreign_forced)
                    and _snap["foreign_languages"]):
                try:
                    _br = foreign_boost.boost(
                        det_request, _snap["foreign_languages"] or [])
                    if _br and getattr(_br, "text", None):
                        det_request = foreign_boost.strip_flags(_br.text)
                        raw = det_request
                except Exception as _e:
                    print("foreign boost error:", _e)

            # A highlighted selection + any mode word acts as the source
            # (see _gather_context).

            # Stash the pre-conversion transcript so the suggestion chip can resend it.
            self._last_raw = raw

            # NOTE: "context" is no longer a spoken trigger. Material-as-context
            # lives in History (Ctrl+Alt+D): pick items + a preset there.
            if self.island:
                self._tk_schedule(
                    self.island.set_building, det_mode, offline=not will_cloud
                )
            self._trace_mark("formatting_started", mode=det_mode)
            # Everyday Text is the latency-critical path. Whisper already emits
            # punctuation, and the deterministic formatter handles cleanup in
            # milliseconds. AI modes still use their full processing; users can
            # switch this off when they explicitly prefer network polishing.
            if instant_text:
                mode = "text"
                out = (formatting.format_transcript(raw, commands=False)
                       if _snap["format_enabled"] else raw.strip())
                used_offline = True
                print("[latency] instant local text — skipped network polishing")
            else:
                # Transcription is complete; expose the distinct text-processing
                # stage to Home/tray instead of leaving them on "Transcribing".
                self._set_state("processing")
                mode, out, used_offline = self._generate(
                    raw,
                    det_mode,
                    det_request,
                    clip_count,
                    mode_active,
                    words,
                    windows,
                    keyword_template=self._active_keyword_template,
                    window_words=window_words if mode_active else None,
                    config_snap=_snap,
                    route_decision=_route,
                    invocation_snapshot=_input_snapshot,
                    target_lease=insertion_lease,
                    operation_id=insertion_operation_id,
                )
            # Mode was ambiguous → the interactive "Which mode?" picker is now up
            # and OWNS the outcome (re-process the preserved words, or paste the
            # polished default on dismiss). Don't paste/record here (owner v6).
            if mode == "__pick__":
                self._trace_finish("awaiting_choice", success=False)
                self._set_state("idle")
                return
            if not out:
                self._trace_finish("no_output", success=False)
                self._idle()
                return
            if timing is not None:
                timing["output_ready_at"] = time.perf_counter()
            self._trace_mark("formatting_ready", mode=mode)
            if mode in ("prompt",) and _comp_tracker is not None:
                try:
                    _comp_tracker.log_prompt(out)
                except Exception:
                    pass
            # Keep the completed artefact in the Deck. It is never fed into a
            # later generation; Prompt Memory was deliberately removed.
            if mode == "prompt":
                try:
                    self.prompt_history.record(det_request or raw, out)
                except Exception as e:
                    print("prompt history record error:", e)
            self._trace_mark("persistence_started", mode=mode)
            entry = self.history.add(out, mode, duration, raw=raw,
                                     quality=self._take_quality(),
                                     via=via_convert)
            if not entry:
                self._trace_mark("persistence_finished", success=False)
                self._trace_finish("persistence_failed", success=False)
                self._notify("Couldn't save the dictation",
                             "Nothing was pasted. Check Mumble's data-folder permissions and try again.")
                self._idle()
                return
            self._trace_mark("persistence_finished", success=True)
            try:
                if not self.stat_store.record(
                        entry["words"], duration, mode,
                        day=stat_context.get("day"),
                        hour=stat_context.get("hour")):
                    print("stats record save failed")
            except Exception as e:
                print("stats record error:", e)
            # Live refresh: tell the web window new data exists — fire-and-forget
            # on a daemon thread so a slow/busy webui socket can NEVER delay the
            # PASTE below (owner v6 reliability: the paste is the user-facing
            # action and outranks every UI refresh).
            self._send_webui_async({"cmd": "refresh", "what": "history"})
            print(f"[{mode}{' · offline' if used_offline else ''}] {out!r}")
            # PASTE FIRST — tray/window bookkeeping happens AFTER, so it can never
            # sit in front of the paste on this thread.
            if search_requested:
                self._open_search(out)
                insertion_result = None
                confirmed = True
                print(f"[{mode}] Searched: {out}")
            else:
                insertion_result = self._paste(
                    out,
                    source="dictation",
                    target_lease=insertion_lease,
                    operation_id=insertion_operation_id,
                )
                confirmed = insertion_result.confirmed
            self._trace_mark(
                "paste_finished",
                success=confirmed,
                outcome=("searched" if search_requested else
                         insertion_result.outcome.value),
                send_count=(0 if search_requested else insertion_result.send_count),
            )
            if timing is not None:
                visible_at = None if search_requested else getattr(
                    self, "_last_paste_sent_at", None)
                timing["paste_done_at"] = visible_at or time.perf_counter()
                release = timing["release_at"]
                raw_at = timing.get("raw_ready_at") or release
                out_at = timing.get("output_ready_at") or raw_at
                paste_at = timing.get("paste_done_at") or out_at
                print(
                    "[latency] release-to-paste "
                    f"{(paste_at - release) * 1000.0:.0f}ms · "
                    f"drain {timing.get('stream_drain_ms', 0.0):.0f}ms · "
                    f"tail {max(0.0, (raw_at - release) * 1000.0 - timing.get('stream_drain_ms', 0.0)):.0f}ms · "
                    f"shape {(out_at - raw_at) * 1000.0:.0f}ms · "
                    f"paste {(paste_at - out_at) * 1000.0:.0f}ms"
                )
                self._dictation_timing = None
            self._trace_finish(
                "searched" if search_requested else
                ("pasted" if insertion_result.confirmed else
                 insertion_result.outcome.value),
                success=confirmed,
                audio_duration_ms=duration * 1000.0,
                mode=mode,
            )
            if self.island:
                if search_requested:
                    self._tk_schedule(self.island.flash, mode,
                                      offline=used_offline, pasted=True,
                                      outcome="confirmed")
                else:
                    self._tk_schedule(
                        self.island.flash, mode, offline=used_offline,
                        pasted=insertion_result.confirmed,
                        outcome=insertion_result.outcome.value,
                        reason=insertion_result.reason,
                        message=insertion_result.message,
                        cleanup_warning=insertion_result.cleanup_warning)
                self._maybe_island_tip(confirmed)  # ITEM 19
            self._remember_correction_candidate(
                out, mode, confirmed and not bool(search_requested),
                None if search_requested else insertion_result.target_lease)
            # Tray + main-window refresh AFTER the paste — isolated, never fatal.
            try:
                self.refresh_tray_menu()
                if self.window:
                    self._tk_schedule(self.window.refresh_history_tab)
            except Exception as e:
                print("UI refresh error (non-fatal):", e)
            self._set_state("idle")
        except Exception as e:
            # Top-level guard: if _generate or _gather_context throws an unexpected
            # exception, don't get stuck at "Transcribing…" forever.
            print(f"_process unhandled error: {e}")
            import traceback

            traceback.print_exc()
            self._trace_finish(
                "processing_failed", success=False,
                error_class=type(e).__name__,
            )
            self._notify("Processing error", str(e))
            self._set_state("error")
            time.sleep(1.0)
            self._idle()

    def _quick_paste_label(self):
        hk = self.settings.get("quick_paste_hotkey", "ctrl+alt+v")
        return " + ".join(p.strip().capitalize() for p in hk.split("+") if p.strip())

    def _focused_editable(self):
        """Best-effort: is the foreground window's focused control something a paste would
        land in? A blinking caret, or an edit/rich-text/browser class, says yes. When we
        can't tell, default to True so we don't nag on a paste that actually worked."""
        try:
            import ctypes
            from ctypes import wintypes

            u = ctypes.windll.user32
            hwnd = u.GetForegroundWindow()
            if not hwnd:
                return False
            tid = u.GetWindowThreadProcessId(hwnd, None)

            class GTI(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("hwndActive", wintypes.HWND),
                    ("hwndFocus", wintypes.HWND),
                    ("hwndCapture", wintypes.HWND),
                    ("hwndMenuOwner", wintypes.HWND),
                    ("hwndMoveSize", wintypes.HWND),
                    ("hwndCaret", wintypes.HWND),
                    ("rcCaret", wintypes.RECT),
                ]

            g = GTI()
            g.cbSize = ctypes.sizeof(GTI)
            if not u.GetGUIThreadInfo(tid, ctypes.byref(g)):
                return True
            if g.hwndCaret:
                return True  # a live caret = a text field
            focus = g.hwndFocus
            if not focus:
                return False  # nothing focused -> paste goes nowhere
            buf = ctypes.create_unicode_buffer(96)
            u.GetClassNameW(focus, buf, 96)
            cls = (buf.value or "").lower()
            # Native edits + the umbrella render-surface classes used by browsers / Electron.
            # Also covers modern chat apps (Discord, Slack, WhatsApp Web, Telegram, etc.)
            for kw in (
                "edit",
                "text",
                "richedit",
                "scintilla",
                "chrome_render",
                "chrome_widget",
                "mozilla",
                "intermediate d3d",
                "webview",
                "cef",
                "note",
                "console",
                "_wwb",
                "textarea",
                "input",
                "contenteditable",
                "msg",
                "chat",
                "message",
                "compose",
                "draft",
                "editor",
                "code",
            ):
                if kw in cls:
                    return True
            # Focused, but the class matched no caret and no known editable /
            # render-surface control. We CANNOT confirm a paste landed here, so
            # report False → the island says "Saved" rather than falsely claiming
            # "Pasted" (owner v4). The Ctrl+V is still sent regardless; this only
            # decides the honest label, never whether we paste. (Real text
            # fields, browsers, editors and chat apps are caught above by the
            # caret check or the class keywords — incl. chrome_render/cef/webview.)
            return False
        except Exception:
            # On a detection error, don't over-claim: a paste we can't verify
            # is reported as "Saved", which is always true (it's in History).
            return False

    def _set_clipboard(self, value, tries=6, delay=0.04):
        """Put `value` on the clipboard and CONFIRM it actually stuck.

        Windows clipboard writes fail silently when another app is holding the
        clipboard open — including the app we just sent Ctrl+V to, which keeps
        it open while it reads. A single pyperclip.copy() that loses that race
        leaves the WRONG text on the clipboard. We retry until a read-back
        confirms our value (or the small budget runs out). Returns True if the
        clipboard is confirmed to hold `value`."""
        value = "" if value is None else value
        for _ in range(max(1, tries)):
            try:
                pyperclip.copy(value)
            except Exception:
                pass
            try:
                if (pyperclip.paste() or "") == value:
                    self._trace_mark_once("clipboard_ready")
                    return True
            except Exception:
                pass
            time.sleep(delay)
        return False

    def _ensure_insertion_transaction(self):
        """Create adapters lazily for focused controller tests and recovery paths."""
        module = getattr(self, "_insertion_module", None)
        if module is not None:
            self._insertion_coordinator = module
            return module
        if getattr(self, "_insertion_transaction", None) is None:
            self._insertion_target = WindowsTargetAdapter()
            self._insertion_clipboard = WindowsClipboardAdapter()
            self._insertion_native = WindowsNativeInputAdapter()
            self._insertion_module = InsertionModule(
                self._insertion_target,
                self._insertion_clipboard,
                self._insertion_native,
                trace=self._insertion_trace,
            )
            self._insertion_coordinator = self._insertion_module
            return self._insertion_module
        if (getattr(self, "_insertion_coordinator", None) is None
                or getattr(self._insertion_coordinator, "_transaction", None)
                is not self._insertion_transaction):
            self._insertion_coordinator = InsertionCoordinator(
                self._insertion_transaction)
        return self._insertion_transaction

    def _capture_insertion_target(self):
        try:
            self._ensure_insertion_transaction()
            return self._insertion_target.current()
        except Exception as exc:
            print("insertion target capture error:", exc)
            return None

    def _make_insertion_lease(self, source, capture_phase, *,
                              mumble_displaced_target=False, target=None):
        return TargetLease(
            target=target if target is not None else self._capture_insertion_target(),
            source=str(source or "unknown"),
            capture_phase=str(capture_phase or "unknown"),
            mumble_displaced_target=bool(mumble_displaced_target),
        )

    def _get_prepared_insertion_lock(self):
        """Return the one lock protecting prepared Deck target bindings."""
        lock = getattr(self, "_prepared_insertion_lock", None)
        if lock is not None:
            return lock
        with _PREPARED_INSERTION_LOCK_INIT:
            lock = getattr(self, "_prepared_insertion_lock", None)
            if lock is None:
                lock = threading.Lock()
                self._prepared_insertion_lock = lock
        return lock

    @staticmethod
    def _require_operation_id(value):
        operation_id = dictation_trace.validated_operation_id(value)
        if operation_id is None:
            raise ValueError("invalid_operation_id")
        return operation_id

    def _get_prepared_insertion_tombstones(self):
        guard = getattr(self, "_prepared_insertion_tombstones", None)
        if guard is None:
            guard = OperationIdReplayGuard()
            self._prepared_insertion_tombstones = guard
        return guard

    def _prepared_insertion_now(self):
        return getattr(self, "_prepared_insertion_clock", time.monotonic)()

    def _cleanup_prepared_insertions_locked(self, now=None):
        """Expire only prepared-never-started leases using monotonic time."""
        now = self._prepared_insertion_now() if now is None else float(now)
        leases = getattr(self, "_prepared_insertion_leases", None)
        if leases is None:
            leases = OrderedDict()
            self._prepared_insertion_leases = leases
        created = getattr(self, "_prepared_insertion_created_at", None)
        if created is None:
            created = {}
            self._prepared_insertion_created_at = created
        executing = getattr(self, "_prepared_insertion_executing", None)
        if executing is None:
            executing = set()
            self._prepared_insertion_executing = executing
        ttl_s = max(0.0, float(getattr(
            self, "_prepared_insertion_ttl_s", 30.0)))
        expired = []
        for operation_id in list(leases):
            if operation_id in executing:
                continue
            started_at = created.get(operation_id)
            if started_at is None:
                created[operation_id] = now
                continue
            if max(0.0, now - started_at) < ttl_s:
                continue
            leases.pop(operation_id, None)
            created.pop(operation_id, None)
            self._get_prepared_insertion_tombstones().remember(operation_id)
            expired.append(operation_id)
        module = getattr(self, "_insertion_module", None)
        if module is not None:
            for operation_id in expired:
                try:
                    module.abandon(operation_id)
                except Exception:
                    pass
        return expired

    def _cleanup_prepared_insertions(self):
        """Run the monotonic prepared-lease backstop without registration pressure."""
        with self._get_prepared_insertion_lock():
            return self._cleanup_prepared_insertions_locked()

    def _prepare_deck_insertion_lease(self, operation_id, source,
                                      ui_process_id=None):
        """Freeze the external destination before the Deck is dismissed."""
        operation_id = self._require_operation_id(operation_id)
        if source not in {"deck_history", "deck_image", "deck_job"}:
            raise ValueError("invalid_insertion_source")
        try:
            ui_pid = int(ui_process_id or 0)
        except (TypeError, ValueError):
            ui_pid = 0
        # Windows/UI inspection may stall.  It must never hold the registry lock
        # needed by an already-prepared operation's final lookup.
        current = self._capture_insertion_target()
        with self._get_prepared_insertion_lock():
            self._cleanup_prepared_insertions_locked()
            tombstones = self._get_prepared_insertion_tombstones()
            if operation_id in tombstones:
                raise InsertionOperationExpired(
                    "operation identity is no longer reusable")
            retained = getattr(self, "_deck_insertion_lease", None)
            target = None
            displaced_by_mumble = False
            if current is not None and (not ui_pid or current.process_id != ui_pid):
                target = current
            elif retained is not None:
                target = retained.target
                # A retained external target is restoration-authorised only when
                # the observed foreground belongs to Mumble's Web UI at the exact
                # action boundary. Merely remembering an older target proves no
                # Mumble-caused displacement.
                displaced_by_mumble = bool(
                    current is not None and ui_pid
                    and current.process_id == ui_pid and target is not None
                    and getattr(self, "_deck_displacement_confirmed", False))
            # Displacement proof is single-use. A later Deck action must have its
            # own controller-requested focus transition or fail closed.
            self._deck_focus_request_pending = False
            self._deck_displacement_confirmed = False
            lease = TargetLease(
                target, source, "before_deck_dismiss", displaced_by_mumble)
            leases = getattr(self, "_prepared_insertion_leases", None)
            if leases is None:
                leases = OrderedDict()
                self._prepared_insertion_leases = leases
            original = leases.get(operation_id)
            if original is not None:
                if original != lease:
                    raise InsertionRequestConflict(
                        "prepared_operation_binding_conflict")
                leases.move_to_end(operation_id)
                return original
            if lease.target is None:
                return lease
            if len(leases) >= 256:
                raise InsertionCoordinatorCapacityError(
                    "prepared insertion capacity is occupied by active operations")
            leases[operation_id] = lease
            self._prepared_insertion_created_at[operation_id] = (
                self._prepared_insertion_now())
            leases.move_to_end(operation_id)
            return lease

    def _note_webui_focus(self, focused):
        """Record only a focus gain caused by this controller's Deck request."""
        focused = bool(focused)
        with self._get_prepared_insertion_lock():
            if not focused:
                self._deck_focus_request_pending = False
                self._deck_displacement_confirmed = False
                return
            if getattr(self, "_deck_focus_request_pending", False):
                self._deck_focus_request_pending = False
                self._deck_displacement_confirmed = True

    def _prepared_insertion_lease(self, operation_id, source):
        operation_id = self._require_operation_id(operation_id)
        if source not in {"deck_history", "deck_image", "deck_job"}:
            raise ValueError("invalid_insertion_source")
        with self._get_prepared_insertion_lock():
            self._cleanup_prepared_insertions_locked()
            if operation_id in self._get_prepared_insertion_tombstones():
                raise InsertionOperationExpired(
                    "operation identity is no longer reusable")
            lease = getattr(self, "_prepared_insertion_leases", {}).get(
                operation_id)
            if lease is not None and lease.source != source:
                raise InsertionRequestConflict(
                    "prepared_operation_binding_conflict")
            if lease is not None:
                self._prepared_insertion_executing.add(operation_id)
            return lease or TargetLease(
                None, source, "before_deck_dismiss", False)

    def _retire_prepared_insertion(self, operation_id):
        """Finish or abandon one prepared Deck binding without permitting reuse."""
        operation_id = self._require_operation_id(operation_id)
        with self._get_prepared_insertion_lock():
            leases = getattr(self, "_prepared_insertion_leases", {})
            leases.pop(operation_id, None)
            getattr(self, "_prepared_insertion_created_at", {}).pop(
                operation_id, None)
            getattr(self, "_prepared_insertion_executing", set()).discard(
                operation_id)
            self._get_prepared_insertion_tombstones().remember(operation_id)

    @staticmethod
    def _record_insertion_cleanup_diagnostic(*, stage, category, outcome):
        """Emit fixed, content-free lifecycle metadata only."""
        print(
            "insertion_cleanup",
            "stage={}".format(stage),
            "category={}".format(category),
            "outcome={}".format(outcome),
        )

    def _attempt_insertion_cleanup(self, stage, action):
        """Run one cleanup independently without changing insertion truth."""
        try:
            action()
        except Exception:
            try:
                self._record_insertion_cleanup_diagnostic(
                    stage=stage,
                    category="lifecycle_cleanup",
                    outcome="failed",
                )
            except Exception:
                pass

    def _finalize_insertion_lifecycle(
            self, operation_id, coordinator, *, deck_operation,
            clipboard_pause_attempted, content_kind):
        """Apply one terminal-outcome precedence contract to text and image.

        The caller's primary InsertionResult or exception is authoritative.
        These independent cleanup attempts are diagnostic-only and never return
        or raise insertion truth, so setup failures keep their existing meaning
        and completed sends can never be relabelled or retried by cleanup.
        """
        if deck_operation:
            if coordinator is not None:
                self._attempt_insertion_cleanup(
                    "coordinator_abandonment",
                    lambda: coordinator.abandon(operation_id),
                )
            self._attempt_insertion_cleanup(
                "prepared_binding_retirement",
                lambda: self._retire_prepared_insertion(operation_id),
            )
        clipboard = getattr(self, "clipboard", None)
        if clipboard is None or not clipboard_pause_attempted:
            return
        if content_kind == "text":
            try:
                clipboard._last_text = pyperclip.paste() or ""
            except Exception:
                pass
        self._attempt_insertion_cleanup(
            "clipboard_resume",
            lambda: clipboard.resume(skip_current=True),
        )

    def _abandon_prepared_insertion(self, operation_id):
        """Idempotently abandon a prepared action unless it already started."""
        operation_id = self._require_operation_id(operation_id)
        with self._get_prepared_insertion_lock():
            self._cleanup_prepared_insertions_locked()
            tombstones = self._get_prepared_insertion_tombstones()
            if operation_id in tombstones:
                return {"abandoned": True, "state": "retired"}
            executing = getattr(self, "_prepared_insertion_executing", set())
            if operation_id in executing:
                return {"abandoned": False, "state": "pending"}
            getattr(self, "_prepared_insertion_leases", {}).pop(
                operation_id, None)
            getattr(self, "_prepared_insertion_created_at", {}).pop(
                operation_id, None)
            tombstones.remember(operation_id)
        coordinator = getattr(self, "_insertion_coordinator", None)
        if coordinator is not None:
            try:
                coordinator.abandon(operation_id)
            except Exception:
                pass
        return {"abandoned": True, "state": "retired"}

    def _get_finished_insertion_trace_ids(self):
        guard = getattr(self, "_finished_insertion_trace_ids", None)
        if guard is None:
            guard = OperationIdReplayGuard()
            self._finished_insertion_trace_ids = guard
        return guard

    def _insertion_trace(self, name, **fields):
        operation_id = dictation_trace.validated_operation_id(
            fields.get("operation_id"))
        if operation_id is None:
            return None
        fields = dict(fields)
        fields["operation_id"] = operation_id
        if name == "paste_sent":
            self._last_paste_sent_at = time.perf_counter()
        if getattr(self, "_dictation_trace_session", None) is not None:
            return self._trace_mark(name, **fields)
        lock = getattr(self, "_insertion_trace_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._insertion_trace_lock = lock
        sessions = getattr(self, "_insertion_trace_sessions", None)
        if sessions is None:
            sessions = {}
            self._insertion_trace_sessions = sessions
        with lock:
            finished = self._get_finished_insertion_trace_ids()
            if operation_id in finished:
                return None
            session = sessions.get(operation_id)
            if session is None:
                sink = getattr(self, "_dictation_trace_sink", None)
                if sink is None:
                    return None
                session = sink.start_insertion({
                    "operation_id": operation_id,
                    "source": fields.get("source"),
                    "content_kind": fields.get("content_kind"),
                })
                if session is None:
                    return None
                sessions[operation_id] = session
            if name == "insertion_finished":
                sessions.pop(operation_id, None)
                finished.remember(operation_id)
        try:
            if name == "insertion_finished":
                session.mark(name, **fields)
                finish_fields = dict(fields)
                finish_outcome = finish_fields.pop("outcome", None) or "unknown"
                return session.finish(finish_outcome, **finish_fields)
            return session.mark(name, **fields)
        except Exception as exc:
            print("insertion trace skipped:", type(exc).__name__)
            return None

    @staticmethod
    def _insertion_confirmed(result):
        return bool(result and result.outcome is InsertionOutcome.CONFIRMED)

    def _paste(self, text, keep_on_clipboard=False, *, source="dictation",
               activation_target=None, target_lease=None, operation_id=None,
               undo_before_paste=False):
        """Run one idempotent, target-bound text insertion transaction."""
        text = text or ""
        self._last_paste_sent_at = None
        operation_id = self._require_operation_id(
            operation_id or uuid.uuid4().hex)
        deck_operation = source in {"deck_history", "deck_image", "deck_job"}
        clipboard_pause_attempted = False
        coordinator = None
        result = None
        try:
            self._ensure_insertion_transaction()
            lease = target_lease or self._make_insertion_lease(
                source, "invocation", target=activation_target)
            if self.clipboard:
                clipboard_pause_attempted = True
                self.clipboard.pause()
                try:
                    self.clipboard.mark_own(text)
                except Exception:
                    pass
            wait_started = time.perf_counter()
            module = getattr(self, "_insertion_module", None)
            if module is not None:
                coordinator = module
                module._bind_lease(operation_id, lease.source or source, lease)
                with self._paste_lock:
                    self._trace_mark(
                        "paste_lock_acquired",
                        wait_ms=(time.perf_counter() - wait_started) * 1000.0,
                        operation_id=operation_id,
                    )
                    result = module.deliver(
                        operation_id, TextPayload(text),
                        (DeliveryIntent.REPLACE if undo_before_paste else
                         DeliveryIntent.INSERT),
                    )
            else:
                request = InsertionRequest(
                    operation_id=operation_id,
                    source=source,
                    content_kind="text",
                    text=text,
                    activation_target=lease.target,
                    target_lease=lease,
                    restore_clipboard=not keep_on_clipboard,
                    settle_seconds=min(1.2, 0.18 + len(text) / 20000.0),
                    undo_before_paste=bool(undo_before_paste),
                )
                coordinator = self._insertion_coordinator
                coordinator.prepare(request)
                with self._paste_lock:
                    self._trace_mark(
                        "paste_lock_acquired",
                        wait_ms=(time.perf_counter() - wait_started) * 1000.0,
                        operation_id=request.operation_id,
                    )
                    result = coordinator.submit(request)
        finally:
            self._finalize_insertion_lifecycle(
                operation_id,
                coordinator,
                deck_operation=deck_operation,
                clipboard_pause_attempted=clipboard_pause_attempted,
                content_kind="text",
            )
        return result

    # ================================================================= hotkeys
    def on_hotkey(self):
        """Toggle recording. Returns the INTENDED new recording state (True when
        starting, False when stopping), or None if the press was vetoed (paused
        or busy). The actual start/stop runs on a daemon thread, so callers that
        need to report state must use this return value, not self.recording
        (which hasn't flipped yet when this returns)."""
        with self.lock:
            if self.paused:
                return None
            if self.busy:
                return None  # prevent rapid-fire double-recording or state corruption
            if not self.recording:
                if self._processing:
                    # Previous dictation is still transcribing/pasting — starting now
                    # would corrupt shared stream state + open a second mic stream.
                    return None
                self._cancel_correction_monitor()
                self.busy = True
                threading.Thread(target=self._safe_start, daemon=True).start()
                return True
            else:
                self.busy = True
                threading.Thread(target=self._safe_stop, daemon=True).start()
                return False

    def _safe_start(self):
        try:
            self.start_recording()
        except Exception as e:
            print("could not start recording:", e)
            # Clean up a partially-started stream so the mic isn't left open.
            if self.recording:
                try:
                    self.stop_recording()
                except Exception:
                    pass
            else:
                # ``start_recording`` assigns ``self.stream`` before calling
                # ``stream.start()``.  When that call fails ``recording`` is
                # still False, so the stop path above is deliberately a no-op;
                # close the acquired handle explicitly instead of leaking the
                # microphone until process exit.
                stream = self.stream
                self.stream = None
                if stream is not None:
                    try:
                        stream.stop()
                    except Exception:
                        pass
                    try:
                        stream.close()
                    except Exception:
                        pass
                self.frames = []
            # A failed voice-search start must not turn the next ordinary
            # dictation into a search once the microphone recovers.
            self._search_requested = False
            self._resume_wake_word("dictation")
            self._notify("Microphone error", e)
            self._set_state("error")
            time.sleep(1.0)
            self._idle()
        finally:
            self.busy = False
            self._restore_meeting_island()

    def _safe_stop(self):
        try:
            self.stop_recording()
        finally:
            self.busy = False
            self._restore_meeting_island()

    def _deck_items(self, max_per_source=60):
        """The Deck's unified data: recent transcripts + clipboard items + saved
        prompts merged into one newest-first list, then the FAVOURITES store
        merged in (live items matching a favourite are flagged fav=True; starred
        items that have since been evicted from the capped stores are appended
        from the favourites copies, so a ★ never disappears). Text items carry
        their full text; clipboard images carry image_path. Exact-duplicate
        texts are collapsed (keep the newest occurrence)."""
        from datetime import datetime as _dt

        items = []
        try:
            for e in self.history.recent(max_per_source):
                t = (e.get("text") or "").strip()
                if t:
                    items.append({
                        "source": "transcript", "mode": e.get("mode", "text"),
                        "time": e.get("stamp") or e.get("time", ""),
                        "text": t,
                    })
        except Exception as e:
            print("hub transcripts error:", e)
        if self.clipboard:
            try:
                for it in self.clipboard.recent(max_per_source):
                    stamp = it.get("stamp") or it.get("time", "")
                    if it.get("type") == "text":
                        t = (it.get("text") or "").strip()
                        if t:
                            items.append({
                                "source": "clipboard", "time": stamp,
                                "text": t, "own": self.clipboard.is_own(t),
                            })
                    elif it.get("type") == "image" and it.get("path"):
                        items.append({
                            "source": "clipboard", "time": stamp, "text": "",
                            "image_path": it["path"],
                            "size": it.get("size", ""),
                        })
            except Exception as e:
                print("hub clipboard error:", e)
        try:
            for p in self.prompt_history.all_prompts()[:max_per_source]:
                t = (p.get("prompt") or "").strip()
                if t:
                    items.append({
                        "source": "prompt",
                        "time": p.get("time", ""), "text": t,
                    })
        except Exception as e:
            print("hub prompts error:", e)

        def _ts(it):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    return _dt.strptime(it.get("time", ""), fmt).timestamp()
                except ValueError:
                    continue
            return 0.0

        items.sort(key=_ts, reverse=True)
        seen, out = set(), []
        for it in items:
            sig = it.get("text") or it.get("image_path")
            if sig in seen:
                continue
            seen.add(sig)
            out.append(it)

        # Favourites: flag live items, then append starred items whose origin
        # has been evicted (they live on as copies in favorites.json).
        try:
            for it in out:
                if it.get("text") and self.favorites.is_fav(it["text"]):
                    it["fav"] = True
            for fe in self.favorites.recent():
                if fe.get("text") not in seen:
                    seen.add(fe["text"])
                    out.append({
                        "source": fe.get("source", "clipboard"),
                        "time": fe.get("time") or fe.get("added", ""),
                        "text": fe["text"], "fav": True,
                    })
        except Exception as e:
            print("hub favorites error:", e)
        return out

    def _deck_fav_toggle(self, item, new_state):
        """Persist a ★ toggle from the Deck (called on the Tk thread; cheap)."""
        try:
            if new_state:
                self.favorites.add(item.get("text", ""),
                                   item.get("source", "clipboard"),
                                   item.get("time", ""))
            else:
                self.favorites.remove(item.get("text", ""))
        except Exception as e:
            print("favorite toggle error:", e)

    def _reserve_deck_job(self):
        """Atomically reserve the single Deck-job slot before acknowledging it."""
        with self._deck_job_lock:
            if self._deck_job_active:
                return False
            self._deck_job_active = True
            return True

    def _run_deck_job(self, items, intent, intent_title, mode, reserved=False,
                      target_lease=None, operation_id=None):
        """Guard wrapper: the web flyout's Go/Convert buttons have no disabled
        state, so a fast double-click fired two `deck_job` commands → two AI calls
        and two pastes. Serialise — a second job while one is in flight is
        dropped (the island/paste then run exactly once)."""
        if not reserved and not self._reserve_deck_job():
            print("[history] a Deck job is already running — rejecting duplicate Go")
            return False
        self._bump_feature("preset_run")
        try:
            self._run_deck_job_impl(
                items, intent, intent_title, mode,
                target_lease=target_lease, operation_id=operation_id)
        finally:
            if operation_id is not None:
                self._retire_prepared_insertion(operation_id)
            with self._deck_job_lock:
                self._deck_job_active = False
        return True

    def _run_deck_job_impl(self, items, intent, intent_title, mode, *,
                           target_lease=None, operation_id=None):
        """Run a Deck AI job: the chosen preset's instruction (and/or a MODE's
        output-form directive) over the checked items, via the intent lane —
        then paste the result. This is the Context Island's engine, now driven
        from the Deck. Same guarantees: retries once on a transient failure,
        NEVER dumps the raw material on error."""
        import presets as _presets

        ctx_block = ai.format_context_items(items or [])
        instruction = (intent or "").strip() or (
            "Execute the most useful rendering of the material for the user: "
            "produce a clean, faithful, well-organised result — never just "
            "echo it verbatim."
        )
        out_mode = mode or "context"
        if mode:
            directive = _presets.MODE_DIRECTIVES.get(mode)
            if directive:
                instruction += "\n\nOUTPUT FORM:\n" + directive
        deck_snapshot = processing_route.snapshot_inputs(
            self.settings,
            feature="deck",
            lane=mode or "deck_reason",
            context=ctx_block,
            context_policy="deck_selection",
            local_model_ready=local_engine.local_llm_ready(),
        )
        route_decision = deck_snapshot.route
        ctx_block = deck_snapshot.context
        key = route_decision.api_key
        if self.island:
            build = ["context", mode] if mode else "context"
            self._tk_schedule(self.island.set_building, build,
                              offline=not route_decision.cloud_augmented)
        out, used_offline = "", True
        provider_ready = route_decision.ready and (
            route_decision.cloud_augmented or route_decision.provider == "local"
        )
        if provider_ready:
            last_err = None
            for attempt in (1, 2):
                try:
                    info = ai.PROVIDERS[route_decision.provider]
                    gen = processing_route.call_provider(
                        route_decision,
                        ai.cerebras_intent,
                        instruction, ctx_block, "", key, route_decision.model,
                        url=info["url"],
                        expected_feature="deck",
                        expected_lane="deck_reason",
                    )
                    raw_out = self._collect_text(gen)
                    if raw_out and raw_out.strip():
                        self._mark_llm_ok()
                        out, used_offline = ai._clean(raw_out), False
                        print(f"[history] applied intent={intent_title!r} "
                              f"mode={mode!r}")
                        break
                    print("[history] AI returned empty output"
                          + (" — one retry in 3s" if attempt == 1 else ""))
                    if attempt == 1:
                        time.sleep(3)
                except Exception as e:
                    last_err = e
                    msg = str(e)
                    if "429" in msg and attempt == 1:
                        wait = 15.0
                        m = re.search(r"RETRY_AFTER=([\d.]+)", msg)
                        if m:
                            try:
                                wait = float(m.group(1)) + 1.0
                            except ValueError:
                                pass
                        wait = max(2.0, min(wait, 25.0))
                        print(f"[history] 429 — retrying in ~{wait:.0f}s")
                        time.sleep(wait)
                        continue
                    print("deck job AI failed:", e)
                    break
            if last_err is not None and not out:
                self._pro_fallback_notice(last_err)
        if not out:
            # NEVER dump the raw material (the old context bug). Say so — with the
            # SPECIFIC reason so the fix is obvious (the reported "presets don't
            # work" is almost always a missing key, not a broken preset).
            if not provider_ready:
                self._notify("Deck presets need AI",
                             "This action stayed on this device. Enable hosted "
                             "text processing with a supported provider and key "
                             "to run presets like Summarise, Merge or Answer it.")
            else:
                self._notify("The Deck couldn't reach the AI",
                             "Nothing was generated — check your connection and "
                             "try again.")
            self._idle()
            return
        try:
            entry = self.history.add(out, out_mode, 0.0)
            if not entry:
                self._notify("Couldn't save the result",
                             "Nothing was pasted. Check that Mumble can write "
                             "to its data folder, then try again.")
                self._idle()
                return
        except Exception as e:
            print("hub history/stats error:", e)
            self._notify("Couldn't save the result",
                         "Nothing was pasted. Check Mumble's data-folder permissions and try again.")
            self._idle()
            return
        # Tell the WEB window a new history item landed — fire-and-forget so it
        # can't delay the paste. The dictation path does this too (see _process);
        # Deck jobs (Reply, Answer it, Summarise …) need it so the newest result
        # surfaces without a manual refresh. (Owner: "most recent reply is not
        # displayed reliably.")
        self._send_webui_async({"cmd": "refresh", "what": "history"})
        # The paste tail is wrapped so the island ALWAYS returns to idle — unlike
        # _process/_reprocess/_finalize_text, this Deck path had no guard, so a
        # paste exception stranded the pill spinning on "building" forever (owner
        # v6 audit). _set_state("idle") in the finally matches the other paths.
        try:
            result = self._paste(
                out, source="deck_job", target_lease=target_lease,
                operation_id=operation_id)
            self._send_webui_async({
                "cmd": "insertion_result",
                **result.as_dict(),
            })
            if self.island:
                flash = ["context", mode] if mode else "context"
                self._tk_schedule(self.island.flash, flash,
                                  offline=used_offline,
                                  pasted=result.confirmed,
                                  outcome=result.outcome.value,
                                  reason=result.reason, message=result.message,
                                  cleanup_warning=result.cleanup_warning)
        finally:
            # Tray + main-window refresh AFTER the paste — never block it.
            try:
                self.refresh_tray_menu()
                if self.window:
                    self._tk_schedule(self.window.refresh_history_tab)
            except Exception:
                pass
            self._set_state("idle")

    def _paste_image(self, path, *, source="deck_image", activation_target=None,
                     target_lease=None, operation_id=None):
        """Run image insertion through the same target and clipboard contract."""
        self._last_paste_sent_at = None
        operation_id = self._require_operation_id(
            operation_id or uuid.uuid4().hex)
        deck_operation = source in {"deck_history", "deck_image", "deck_job"}
        clipboard_pause_attempted = False
        coordinator = None
        result = None
        try:
            self._ensure_insertion_transaction()
            lease = target_lease or self._make_insertion_lease(
                source, "invocation", target=activation_target)
            wait_started = time.perf_counter()
            if self.clipboard:
                clipboard_pause_attempted = True
                self.clipboard.pause()
            module = getattr(self, "_insertion_module", None)
            if module is not None:
                coordinator = module
                module._bind_lease(operation_id, lease.source or source, lease)
                with self._paste_lock:
                    self._trace_mark(
                        "paste_lock_acquired",
                        wait_ms=(time.perf_counter() - wait_started) * 1000.0,
                        operation_id=operation_id,
                    )
                    result = module.deliver(
                        operation_id, ImagePayload(path or ""))
            else:
                request = InsertionRequest(
                    operation_id=operation_id,
                    source=source,
                    content_kind="image",
                    image_path=path or "",
                    activation_target=lease.target,
                    target_lease=lease,
                    settle_seconds=0.30,
                )
                coordinator = self._insertion_coordinator
                coordinator.prepare(request)
                with self._paste_lock:
                    self._trace_mark(
                        "paste_lock_acquired",
                        wait_ms=(time.perf_counter() - wait_started) * 1000.0,
                        operation_id=request.operation_id,
                    )
                    result = coordinator.submit(request)
        finally:
            self._finalize_insertion_lifecycle(
                operation_id,
                coordinator,
                deck_operation=deck_operation,
                clipboard_pause_attempted=clipboard_pause_attempted,
                content_kind="image",
            )
        return result

    # ---- the Mumble command channel (controller ⇄ web window) --------------
    # The web window runs as its own process; these two tiny localhost sockets
    # make the pair feel like ONE app: the controller serves paste/job/record
    # commands on 49519 (webui → controller), and the web shell listens on
    # 49520 (controller → webui: open History, come to front).
    CMD_PORT = 49519
    WEBUI_PORT = 49520

    def _ensure_cmd_token(self):
        """Mint a per-session token for the localhost command channel and write
        it to a same-user-only file so the web window (and a second launch) can
        authenticate. The channel is otherwise unauthenticated — any local
        process could inject `paste` (types into the focused app) or
        `grab_selection` (reads the highlighted text), plus record/quit/deck_job.
        Regenerated every controller start and overwritten, so a crashed
        session's token never lingers as valid."""
        import secrets
        self._cmd_token = secrets.token_hex(32)
        try:
            branding.ensure_dirs()
            path = branding.cmd_token_path()
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._cmd_token)
            try:
                os.chmod(path, 0o600)  # best-effort owner-only (POSIX)
            except OSError:
                pass
        except OSError as e:
            # Fail closed: keep the in-memory token (so the channel stays
            # authenticated) even though clients can't read it — better a
            # temporarily mute web window than an open injection port.
            print("cmd token write failed:", e)

    def _cmd_token_ok(self, req):
        """Constant-time check that a command carries this session's token."""
        import hmac
        want = getattr(self, "_cmd_token", "") or ""
        got = req.get("token") or ""
        return bool(want) and hmac.compare_digest(str(got), str(want))

    @staticmethod
    def _validated_external_operation_id(value):
        """Accept only IDs generated by the Web UI, never caller-controlled text."""
        return dictation_trace.validated_operation_id(value)

    @staticmethod
    def _validated_external_insertion_source(value):
        if value in {"deck_history", "deck_image", "deck_job"}:
            return value
        return None

    def _start_cmd_server(self):
        """Serve JSON-line commands from the web window. Daemon accept loop;
        each handler replies one JSON line. Heavy work goes to worker threads
        through the SAME paths the tkinter surfaces use — one pipeline."""
        import json as _json
        import socket as _socket

        def _handle(conn):
            try:
                conn.settimeout(5)
                data = b""
                while not data.endswith(b"\n") and len(data) < 1_000_000:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                req = _json.loads(data.decode("utf-8", "replace") or "{}")
                # Authenticate BEFORE dispatch: without a valid per-session token
                # any local process could inject paste/grab_selection/deck_job on
                # this port. Reject unauthenticated callers outright.
                if not self._cmd_token_ok(req):
                    conn.sendall(
                        (_json.dumps({"ok": False, "message": "unauthorized"})
                         + "\n").encode("utf-8"))
                    return
                cmd = req.get("cmd", "")
                # Authenticate before dispatching ANY command. Without this, any
                # local process (or a malicious page hitting 127.0.0.1:49519) could
                # inject paste/grab_selection/record/hub_job/quit. The web window
                # sends the per-session token on every command; reject mismatches.
                if not branding.token_ok(req.get("token"), self._cmd_token):
                    try:
                        conn.sendall(
                            (_json.dumps({"ok": False, "message": "unauthorized"})
                             + "\n").encode("utf-8"))
                    except Exception:
                        pass
                    return
                insertion_commands = {
                    "prepare_insertion", "paste", "paste_image",
                    "insertion_status", "abandon_insertion", "deck_job",
                }
                operation_id = (self._validated_external_operation_id(
                    req.get("operation_id")) if cmd in insertion_commands else None)
                resp = {"ok": True}
                if cmd in insertion_commands and operation_id is None:
                    resp = {
                        "ok": False,
                        "operation_id": "",
                        "reason": "invalid_operation_id",
                        "message": "This paste request has an invalid operation identifier.",
                    }
                elif cmd == "status":
                    st, txt = self.status()
                    resp.update(state=st, text=txt, recording=self.recording,
                                active_mode=getattr(self, "active_mode", None))
                elif cmd == "record":
                    # EXACTLY the activation-hotkey pipeline (Rule: every
                    # recording entry point behaves identically).
                    acted = self.on_hotkey()
                    # on_hotkey starts/stops on a background thread, so
                    # self.recording hasn't flipped yet. Report the intended
                    # state when we actually acted; if the press was vetoed
                    # (paused/busy), report the unchanged real state so the UI
                    # doesn't claim "Recording…" when nothing started.
                    resp.update(
                        recording=self.recording if acted is None else acted)
                elif cmd == "quit":
                    # "Switch to Mumble Lite": the controller must release the
                    # single-instance lock (Lite binds the same port) — a real
                    # switch, not two Mumbles fighting over the mic.
                    self.cmd_q.put("quit")
                elif cmd == "prepare_insertion":
                    source = self._validated_external_insertion_source(
                        req.get("source"))
                    if source is None:
                        resp = {
                            "ok": False,
                            "operation_id": operation_id,
                            "reason": "invalid_insertion_source",
                            "message": "This paste request has an invalid source.",
                        }
                    else:
                        try:
                            lease = self._prepare_deck_insertion_lease(
                                operation_id, source, req.get("ui_process_id"))
                            module = getattr(self, "_insertion_module", None)
                            if module is not None and lease is not None:
                                module._bind_lease(operation_id, source, lease)
                            resp.update(
                                ok=bool(lease and lease.target),
                                operation_id=operation_id,
                                message=("" if lease and lease.target else
                                         "Select the destination before using Deck paste."),
                            )
                        except InsertionRequestConflict:
                            resp = {
                                "ok": False,
                                "operation_id": operation_id,
                                "reason": "operation_id_conflict",
                                "message": "This paste request is already bound to a different destination.",
                            }
                        except InsertionOperationExpired:
                            resp = {
                                "ok": False,
                                "operation_id": operation_id,
                                "reason": "operation_id_retired",
                                "message": "This paste operation has already finished or was abandoned. Begin a new Deck action.",
                            }
                        except InsertionCoordinatorCapacityError:
                            resp = {
                                "ok": False,
                                "operation_id": operation_id,
                                "reason": "insertion_capacity_reached",
                                "message": "Mumble is still protecting earlier paste operations. Finish them before beginning another Deck action.",
                            }
                elif cmd == "paste":
                    text = req.get("text") or ""
                    try:
                        prepared_lease = self._prepared_insertion_lease(
                            operation_id, "deck_history")
                        time.sleep(0.25)  # let focus return to the target
                        result = self._paste(
                            text, source="deck_history",
                            target_lease=prepared_lease,
                            operation_id=operation_id,
                        )
                        pasted = bool(result.confirmed)
                        resp.update(ok=True, pasted=pasted,
                                    **result.as_dict())
                    except (InsertionRequestConflict, InsertionOperationExpired):
                        resp.update(ok=False, pasted=False, outcome="saved_only",
                                    confirmed=False, reason="operation_id_conflict",
                                    cleanup_warning="",
                                    message="This paste request does not match its prepared destination. The result remains saved in Deck and History.")
                    except Exception as e:
                        print("cmd paste error:", type(e).__name__)
                        resp.update(ok=False, pasted=False, outcome="saved_only",
                                    confirmed=False, reason="internal_error",
                                    cleanup_warning="",
                                    message="The paste request could not be completed. The result remains saved in Deck and History.")
                elif cmd == "paste_image":
                    path = req.get("path") or ""
                    try:
                        prepared_lease = self._prepared_insertion_lease(
                            operation_id, "deck_image")
                        time.sleep(0.25)
                        result = self._paste_image(
                            path, source="deck_image",
                            target_lease=prepared_lease,
                            operation_id=operation_id,
                        )
                        pasted = bool(result.confirmed)
                        resp.update(ok=True, pasted=pasted,
                                    **result.as_dict())
                    except (InsertionRequestConflict, InsertionOperationExpired):
                        resp.update(ok=False, pasted=False, outcome="saved_only",
                                    confirmed=False, reason="operation_id_conflict",
                                    cleanup_warning="",
                                    message="This image paste request does not match its prepared destination. The image remains in Deck.")
                    except Exception as e:
                        print("cmd paste_image error:", type(e).__name__)
                        resp.update(ok=False, pasted=False, outcome="saved_only",
                                    confirmed=False, reason="internal_error",
                                    cleanup_warning="",
                                    message="The image paste request could not be completed. The image remains in Deck.")
                elif cmd == "insertion_status":
                    try:
                        self._ensure_insertion_transaction()
                        status = self._insertion_coordinator.status(operation_id)
                        if isinstance(status, InsertionResult):
                            status = status.as_dict()
                        resp = {"ok": status.get("state") != "missing", **status}
                    except (InsertionRequestConflict, InsertionOperationExpired):
                        resp = {"ok": False, "state": "rejected",
                                "reason": "operation_id_conflict",
                                "message": "This paste request no longer matches its operation."}
                elif cmd == "abandon_insertion":
                    resp.update(self._abandon_prepared_insertion(operation_id))
                elif cmd == "rebind":
                    # Hotkeys are a transaction owned by the controller: validate,
                    # register the new hook, persist it, then retire only the old
                    # hook. The Web UI must never save first and discover later
                    # that it left the user with a dead shortcut.
                    binding_key = str(req.get("key") or "")
                    binding_value = str(req.get("value") or "")
                    apply_binding = {
                        "hotkey": self.apply_hotkey,
                        "quick_paste_hotkey": self.apply_quick_paste_hotkey,
                        "history_hotkey": self.apply_history_hotkey,
                        "search_hotkey": self.apply_search_hotkey,
                    }.get(binding_key)
                    if apply_binding is None:
                        resp = {"ok": False, "applied": False,
                                "message": "Unknown shortcut setting."}
                    else:
                        ok, message = apply_binding(binding_value)
                        resp = {"ok": bool(ok), "applied": bool(ok),
                                "message": message,
                                "value": self.settings.get(binding_key, "")}
                elif cmd == "reload":
                    # The web window just saved a setting from ITS process; pull
                    # settings.json back in and live-apply anything with runtime
                    # state — a rebind or model switch must never wait for a
                    # restart to take effect.
                    changed_key = str(req.get("key") or "")
                    threading.Thread(
                        target=self._apply_settings_change,
                        args=(changed_key,), daemon=True,
                    ).start()
                elif cmd == "deck_job":
                    import presets as _presets
                    slot = req.get("slot")
                    mode = req.get("mode") or None
                    items = req.get("items") or []
                    title, instr = "", None
                    if slot:
                        for s, t, _d, ins in _presets.all_presets():
                            if s == int(slot) and ins:
                                title, instr = t, ins
                                break
                    else:
                        # No preset slot + a mode = a per-item Convert (the rotate
                        # button). Track it so the "Convert" island tip can retire
                        # once the user adopts it (tips usage-awareness).
                        if mode:
                            self._bump_feature("convert")

                    try:
                        job_lease = self._prepared_insertion_lease(
                            operation_id, "deck_job")
                    except (InsertionRequestConflict, InsertionOperationExpired):
                        job_lease = None
                        accepted = False
                        resp.update(
                            ok=False, accepted=False,
                            reason="operation_id_conflict",
                            message="This Deck job does not match its prepared destination.")
                    else:
                        accepted = self._reserve_deck_job()
                        if not accepted:
                            self._retire_prepared_insertion(operation_id)
                    resp.update(
                        ok=accepted,
                        accepted=accepted,
                        message=("" if accepted else resp.get("message") or
                                 "A Deck job is already running."),
                    )
                    if accepted:
                        job_operation_id = operation_id
                        def _wj():
                            try:
                                time.sleep(0.25)
                                self._run_deck_job(
                                    items, instr, title, mode, reserved=True,
                                    target_lease=job_lease,
                                    operation_id=job_operation_id)
                            except Exception as e:
                                print("cmd deck_job error:", e)
                                with self._deck_job_lock:
                                    self._deck_job_active = False
                        try:
                            threading.Thread(target=_wj, daemon=True).start()
                        except Exception as e:
                            with self._deck_job_lock:
                                self._deck_job_active = False
                            self._retire_prepared_insertion(operation_id)
                            resp.update(ok=False, accepted=False, message=str(e))
                elif cmd == "grab_selection":
                    # The Deck's "Capture" button (and re-pressing the Deck hotkey
                    # while it is already open) asks us to read whatever text the
                    # user currently has HIGHLIGHTED and hand it back. We own the
                    # keyboard + clipboard, so the Ctrl+C grab happens HERE; because
                    # the pinned Deck is a non-activating palette, clicking Capture
                    # never stole focus, so the foreground app still holds the live
                    # selection. Runs inline (the grab is ~10-130ms) so the reply
                    # carries the text; '' simply means nothing was highlighted.
                    #
                    # Skip mid-Deck-job: _run_deck_job pastes its result via the
                    # clipboard, and _grab_selection_quiet temporarily writes+restores
                    # the clipboard — doing both at once could cross them. Return an
                    # empty selection honestly rather than risk clobbering a paste.
                    if getattr(self, "_deck_job_active", False):
                        resp.update(selection="")
                    else:
                        try:
                            sel = self._grab_selection_quiet()
                        except Exception as e:
                            print("cmd grab_selection error:", e)
                            sel = ""
                        resp.update(selection=sel)
                elif cmd == "capture_conversation":
                    # ITEM 5: the Deck's "Capture conversation" button (and the
                    # Ctrl+Alt+C hotkey) asks us to Select-All → Copy the focused AI
                    # chat and store the WHOLE thread as prompting context. We own
                    # the keyboard + clipboard, so it runs HERE; skip mid-Deck-job so
                    # the select-all can't cross a result paste (same guard as above).
                    if getattr(self, "_deck_job_active", False):
                        resp.update(ok=False,
                                    message="Busy running a Deck job — try again.")
                    else:
                        try:
                            resp.update(self.capture_conversation())
                        except Exception as e:
                            print("cmd capture_conversation error:", e)
                            resp.update(ok=False, message=str(e))
                elif cmd == "focused":
                    # Perf: the web UI signals focus/blur so the island can pause
                    # its animation loop when the user works in another app. The
                    # island freezes its frame counter, state timers, and painting
                    # while unfocused — no CPU/GPU waste on unseen frames (VAL-PERF-005).
                    val = req.get("value", True)
                    self._note_webui_focus(val)
                    if self.island:
                        self._tk_schedule(self.island.set_focused, bool(val))
                        # When regaining focus, tick the status so the chip updates
                        if val:
                            self._set_state(self.state)
                # ── Experimental Correction Learning ──
                elif cmd == "set_island_mode":
                    active_mode = self.set_active_mode(req.get("mode") or None)
                    resp.update(active_mode=active_mode)
                elif cmd == "correction_learning_status":
                    resp = self.correction_learning_status()
                elif cmd == "correction_learning_open":
                    resp = self.open_correction_learning()
                elif cmd == "correction_learning_undo":
                    resp = self.undo_correction_learning()
                # ── Meeting Mode commands (owner 2026-06-29) ──
                elif cmd == "meeting_record_start":
                    resp = self._meeting_start()
                elif cmd == "meeting_record_stop":
                    resp = self._meeting_stop(req.get("title", ""))
                elif cmd == "meeting_record_status":
                    resp = self._meeting_status()
                elif cmd == "meeting_record_pause":
                    with self._meeting_lock():
                        if self.meeting_recorder is not None and self.meeting_recording:
                            ok = self.meeting_recorder.pause()
                            resp = self._meeting_status() if ok else {
                                "ok": False, "message": "The meeting could not be paused."}
                        else:
                            resp = {"ok": False, "message": "No meeting recording to pause."}
                elif cmd == "meeting_record_resume":
                    with self._meeting_lock():
                        if self.meeting_recorder is not None and self.meeting_recording:
                            ok = self.meeting_recorder.resume()
                            resp = self._meeting_status() if ok else {
                                "ok": False, "message": "The meeting could not be resumed."}
                        else:
                            resp = {"ok": False, "message": "No meeting recording to resume."}
                elif cmd == "meeting_import_audio":
                    path = req.get("path", "")
                    if path:
                        resp = self._meeting_import(path)
                    else:
                        resp = {"ok": False, "message": "No audio path provided."}
                elif cmd == "meeting_retry":
                    resp = self._meeting_retry(req.get("meeting_id", ""))
                else:
                    resp = {"ok": False, "message": f"unknown cmd {cmd!r}"}
                conn.sendall((_json.dumps(resp) + "\n").encode("utf-8"))
            except Exception as e:
                print("cmd server request error:", e)
                try:
                    conn.sendall((_json.dumps({
                        "ok": False,
                        "message": "The controller rejected this request safely.",
                    }) + "\n").encode("utf-8"))
                except Exception:
                    pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        def _serve():
            try:
                srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
                srv.bind(("127.0.0.1", self.CMD_PORT))
                srv.listen(4)
                # Reuse the existing command-server loop as the prepared-lease
                # backstop. Cleanup therefore follows monotonic age even when
                # paste/status transport is completely absent, without adding a
                # second background worker or depending on capacity pressure.
                srv.settimeout(1.0)
            except Exception as e:
                print("cmd server unavailable:", e)
                return
            while True:
                try:
                    conn, _ = srv.accept()
                    threading.Thread(target=_handle, args=(conn,),
                                     daemon=True).start()
                except _socket.timeout:
                    try:
                        self._cleanup_prepared_insertions()
                    except Exception as exc:
                        print("prepared insertion cleanup skipped:",
                              type(exc).__name__)
                except Exception:
                    return

        threading.Thread(target=_serve, daemon=True).start()

    def _apply_settings_change(self, key):
        """The web window writes settings.json from a separate process — without
        this, a rebind or model change sat invisible until the next restart (the
        'did that actually apply?' report). Re-read the file, then live-apply the
        pieces that hold runtime state. Everything else (language, prompt prefs,
        provider keys, mic) is read at use-time and needs only the reload."""
        try:
            self.settings.load()
        except Exception as e:
            print("settings reload error:", e)
            return False
        k = (key or "").split(".", 1)[0]
        try:
            if k == "hotkey":
                self._register_hotkey()
                print(f"[live-apply] record hotkey → {self.hotkey!r}")
            elif k == "quick_paste_hotkey":
                self._register_quick()
                print(f"[live-apply] paste-latest hotkey → {self.quick_hotkey!r}")
            elif k == "history_hotkey":
                self._register_history()
                print(f"[live-apply] History hotkey → {self.history_hotkey!r}")
            elif k == "search_hotkey":
                self._register_search()
                print(f"[live-apply] search hotkey → {self.search_hotkey!r}")
            elif k in ("correction_learning_enabled",
                        "correction_learning_auto_detect"):
                enabled = bool(
                    self.settings.get("correction_learning_enabled", False))
                if enabled:
                    self._ensure_correction_learning()
                    self._prewarm_correction_monitor()
                if (not enabled or not self.settings.get(
                        "correction_learning_auto_detect", True)):
                    self._cancel_correction_monitor()
                if not enabled:
                    with self._correction_capture_lock:
                        self._last_correction_capture = None
                    if self.island:
                        self._tk_schedule(self.island.clear_correction)
                        self._tk_schedule(self.island.close_correction_editor)
                print(f"[live-apply] correction learning → {enabled} ({k})")
            elif k == "prompt_mode_enabled":
                # Prompt is a sticky toggle (the web switch). Sync it through the
                # general active-mode selector so the island deck stays in lockstep.
                self.prompt_mode_enabled = bool(
                    self.settings.get("prompt_mode_enabled", False))
                self.active_mode = "prompt" if self.prompt_mode_enabled else (
                    None if self.active_mode == "prompt" else self.active_mode)
                self._push_island_bar_state()
                print(f"[live-apply] Prompt mode → {self.prompt_mode_enabled}")
            elif k in ("island_modes", "island_foreign_toggle", "foreign_mode",
                       "island_active_mode"):
                # ITEM 4: the island deck composition / Foreign opt-in changed in
                # Settings — re-push the bar snapshot so it reflects immediately.
                if k == "island_active_mode":
                    self.active_mode = self.settings.get("island_active_mode") or None
                self._push_island_bar_state()
                print(f"[live-apply] island deck → {k}")
            elif k in ("english_only", "primary_language"):
                # Hardware/language change → resolve the right local model and load
                # it (no manual model id picking). Skip while a cloud STT is active.
                try:
                    self._apply_hardware_model()
                except Exception as e:
                    print("hardware model apply failed:", e)
            elif k == "model":
                new = self.settings.get("model", self.model_name)
                if new != self.model_name:
                    print(f"[live-apply] model switch → {new!r}")
                    self.set_model(new)
            elif k == "resource_saver":
                # Flipping the saver changes the EFFECTIVE model (tiny.en <-> the
                # configured model) without changing the saved "model" setting, so
                # `set_model` would short-circuit (name unchanged). Reload directly
                # via the same threaded routine `set_model` uses — `_try_load`
                # resolves the effective name, swapping tiny.en in/out immediately;
                # `self.model_name` (the user's real choice) is preserved.
                eff = self._effective_model()
                print(f"[live-apply] resource_saver → "
                      f"{bool(self.settings.get('resource_saver'))} "
                      f"(reloading model as {eff!r})")
                threading.Thread(
                    target=self._load_model,
                    args=(self.model_name,),
                    daemon=True,
                ).start()
            elif k in ("transcription_mode", "cloud_transcription_provider",
                       "groq_api_key", "openai_api_key", "openrouter_api_key"):
                # Live-apply the local/cloud switch so RAM follows the choice
                # without a restart. Switching to a working cloud setup unloads
                # the heavy local model; switching back to local (or a cloud
                # setup with no key) reloads it on a background thread.
                # The cloud-STT keys are included because the COMMON flow is to
                # switch to Cloud first (no key yet → nothing to unload) and THEN
                # paste the key — that key change is what makes
                # _cloud_transcription_on() true, so it must re-run this check or
                # the local model stays resident until the next restart.
                if self._cloud_transcription_on():
                    if self.model is not None:
                        self._unload_local_model()
                else:
                    if self.model is None:
                        threading.Thread(
                            target=self._ensure_local_model, daemon=True).start()
                        print("[live-apply] local mode → reloading local model")
            elif k == "clipboard_enabled":
                # The toggle used to be read only at boot, so flipping it did
                # nothing until the next launch (and there was no way to stop the
                # watcher at all). Apply it live.
                if bool(self.settings.get("clipboard_enabled", True)):
                    self.clipboard.start()   # no-op if already running
                    print("[live-apply] clipboard history ON")
                else:
                    self.clipboard.stop()
                    print("[live-apply] clipboard history OFF")
            elif k == "history_max":
                cap = max(1, min(5000, int(
                    self.settings.get("history_max", self.history.maxlen))))
                if not self.history.resize(cap):
                    raise RuntimeError("could not apply transcript retention limit")
                print(f"[live-apply] transcript retention → {cap}")
            elif k == "clipboard_max":
                cap = max(1, min(5000, int(
                    self.settings.get("clipboard_max", self.clipboard.maxlen))))
                if not self.clipboard.resize(cap):
                    raise RuntimeError("could not apply clipboard retention limit")
                print(f"[live-apply] clipboard retention → {cap}")
            elif k == "autostart":
                desired = bool(self.settings.get("autostart", True))
                if not autostart.set_enabled(desired):
                    actual = bool(autostart.is_enabled())
                    self.settings.set("autostart", actual)
                    print("[live-apply] autostart failed; reverted setting "
                          f"to actual state {actual}")
            elif k == "cpu_threads":
                print("[live-apply] cpu_threads saved — applies on the next "
                      "model load")
        except Exception as e:
            print(f"live-apply of {key!r} failed:", e)
            return False
        return True

    def _send_webui(self, obj, timeout=0.6):
        """Send a command and require the Web UI to acknowledge its routing."""
        import json as _json
        import socket as _socket
        try:
            payload = dict(obj)
            payload.setdefault("token", self._cmd_token)  # authenticate to the webui
            with _socket.create_connection(("127.0.0.1", self.WEBUI_PORT),
                                           timeout=timeout) as s:
                s.sendall((_json.dumps(payload) + "\n").encode("utf-8"))
                data = b""
                while not data.endswith(b"\n") and len(data) < 65536:
                    chunk = s.recv(8192)
                    if not chunk:
                        break
                    data += chunk
                reply = _json.loads(data.decode("utf-8", "replace") or "{}")
                return bool(reply.get("ok"))
        except Exception:
            return False

    def _send_webui_async(self, obj):
        """Fire a command to the web window WITHOUT blocking the caller.

        Refresh broadcasts must NEVER sit on the save→paste path: a slow, busy or
        restarting webui socket could otherwise delay the user-facing PASTE by up
        to the full connect timeout (owner v6 reliability — core actions outrank
        every UI refresh). Runs on a short daemon thread; fully swallowed."""
        import threading as _threading
        try:
            _threading.Thread(target=self._send_webui, args=(obj,),
                              daemon=True).start()
        except Exception:
            pass

    def on_quick_paste(self):
        """Paste-latest hotkey (default Ctrl+Alt+V). PASTE ONLY — pastes the MOST
        RECENT transcript straight into wherever your cursor is. It no longer opens
        History (owner v9: paste and History are now two SEPARATE actions — opening
        the window is the History hotkey, default Ctrl+Alt+D). No popup, no forced
        mode, no window activation, no yellow flash — just the text, plus a clear
        island message when there is nothing to paste or the paste can't land. (The
        tray "Re-paste last" item calls this too.)"""
        self._bump_feature("quick_paste")
        operation_id = uuid.uuid4().hex
        module = getattr(self, "_insertion_module", None)
        if module is not None:
            target_lease = module.begin(
                operation_id, "paste_latest").target_lease
        else:
            target_lease = self._make_insertion_lease(
                "paste_latest", "invocation", mumble_displaced_target=False)
        with self.lock:
            if self.busy or self.recording or self.paused:
                return
            self.busy = True

        def _work():
            try:
                rec = self.history.recent(1)
                text = (rec[0].get("text") if rec else "") or ""
                if not text.strip():
                    self._quick_status("No history yet — dictate something first")
                    return
                result = None
                try:
                    result = self._paste(
                        text, source="paste_latest",
                        target_lease=target_lease,
                        operation_id=operation_id,
                    )
                except Exception as e:
                    print("quick-paste error:", e)
                if result is None:
                    self._quick_status("Not inserted — saved in Mumble")
                elif result.outcome is not InsertionOutcome.CONFIRMED:
                    self._quick_status(result.message)
                elif result.cleanup_warning:
                    self._quick_status(result.cleanup_warning)
            finally:
                self.busy = False

        threading.Thread(target=_work, daemon=True).start()

    def on_open_history(self):
        """History hotkey (default Ctrl+Alt+D) — OPEN HISTORY ONLY (owner v9).
        Brings the main window to the History tab: restores it if minimized,
        focuses it if already open, opens it if closed, and lifts it above the
        other Mumble windows. Never pastes — that's the paste-latest hotkey's job.
        Decoupled so the two concepts can never be confused again."""
        self._bump_feature("deck")
        threading.Thread(target=self._show_history_page, daemon=True).start()

    def _quick_status(self, msg):
        """A brief, non-disruptive island hint for auxiliary shortcut errors."""
        if self.island is not None:
            try:
                self._tk_schedule(self.island.hint, msg)
            except Exception:
                pass

    def _show_history_page(self):
        """Bring the MAIN window to the History tab. Usually the webui process is
        already up, so {"cmd":"history"} brings it forward and navigates instantly;
        otherwise launch it and retry briefly until it answers.

        Grab any HIGHLIGHTED selection FIRST — before the Deck window takes focus,
        because the moment it does Windows clears the highlight in the source app
        (the owner's "pulling up the Deck breaks the highlight" bug). If the user
        had text selected, it rides along in the command and lands as a temporary
        top item in the Deck they can Convert / tick / run. _grab_selection_quiet
        releases the held hotkey modifiers, restores the prior clipboard, and
        returns '' when nothing is selected — so an ordinary Deck-open (tray menu,
        no selection) is completely unaffected."""
        deck_lease = self._make_insertion_lease(
            "deck", "before_mumble_focus", mumble_displaced_target=False)
        with self._get_prepared_insertion_lock():
            self._deck_insertion_lease = deck_lease
            self._deck_focus_request_pending = bool(
                deck_lease and deck_lease.target)
            self._deck_displacement_confirmed = False
        try:
            sel = self._grab_selection_quiet()
        except Exception:
            sel = ""
        msg = {"cmd": "history"}
        if sel:
            msg["selection"] = sel
        try:
            if self._send_webui(msg):
                return
        except Exception:
            pass
        if self._open_web_ui():
            def _retry():
                for _ in range(25):
                    time.sleep(0.3)
                    if self._send_webui(msg):
                        return
                with self._get_prepared_insertion_lock():
                    self._deck_focus_request_pending = False
                    self._deck_displacement_confirmed = False
            threading.Thread(target=_retry, daemon=True).start()
        else:
            with self._get_prepared_insertion_lock():
                self._deck_focus_request_pending = False

    def on_search_hotkey(self):
        # Mumble Find owns only its resident overlay visibility. It deliberately
        # does not read or mutate recording/busy/processing state, the immutable
        # Stop-time insertion lease, or the insertion operation identity.
        self._bump_feature("search")
        operation_id = uuid.uuid4().hex

        def _toggle():
            if not self._toggle_system_search_page(operation_id):
                self._quick_status("Mumble Find couldn't toggle — try again")

        threading.Thread(
            target=_toggle,
            name="mumble-toggle-find",
            daemon=True,
        ).start()
        return True

    def _toggle_system_search_page(self, operation_id):
        """Toggle the resident local launcher without surfacing the main window."""
        lock = getattr(self, "_find_toggle_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._find_toggle_lock = lock
        with lock:
            message = {
                "cmd": "system_search_toggle",
                "operation_id": str(operation_id),
            }
            proc = getattr(self, "_webui_proc", None)
            resident_was_live = proc is not None and proc.poll() is None
            if resident_was_live:
                if self._send_webui(message, timeout=0.8):
                    return True
                # A missing reply is not proof that the toggle did not happen.
                # Retry only into the same resident process, with the same ID.
                for _ in range(25):
                    if (getattr(self, "_webui_proc", None) is not proc
                            or proc.poll() is not None):
                        return False
                    time.sleep(0.3)
                    if self._send_webui(message, timeout=0.8):
                        return True
                return False
            if not self._open_web_ui("search"):
                return False
            launched_proc = getattr(self, "_webui_proc", None)
            for _ in range(25):
                if (launched_proc is not None
                        and (getattr(self, "_webui_proc", None) is not launched_proc
                             or launched_proc.poll() is not None)):
                    return False
                time.sleep(0.3)
                if self._send_webui(message, timeout=0.8):
                    return True
            return False

    def _show_system_search_page(self):
        """Show the resident local launcher without surfacing the main window."""
        message = {"cmd": "system_search_show"}
        if self._send_webui(message, timeout=0.8):
            return True
        if not self._open_web_ui("search"):
            return False
        for _ in range(25):
            time.sleep(0.3)
            if self._send_webui(message, timeout=0.8):
                return True
        return False

    # Web providers used only for explicit Deck web-search actions.
    # {q} is the URL-encoded query.
    SEARCH_ENGINES = {
        "google": "https://www.google.com/search?q={q}",
        "perplexity": "https://www.perplexity.ai/search?q={q}",
        "brave": "https://search.brave.com/search?q={q}",
    }

    # Browser executables for the Settings → Browser choice. Each maps to the
    # standard install locations; a missing browser falls back to the default.
    BROWSERS = {
        "edge": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ],
        "chrome": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ],
        "brave": [
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        ],
        "chromium": [
            os.path.expandvars(r"%LOCALAPPDATA%\Chromium\Application\chrome.exe"),
            r"C:\Program Files\Chromium\Application\chrome.exe",
        ],
    }

    def open_in_browser(self, url):
        """Open `url` in the browser chosen in Settings ('default' = the OS
        default via webbrowser). A chosen browser that isn't installed falls
        back to the default rather than failing silently."""
        import webbrowser

        choice = (self.settings.get("browser", "default") or "default").lower()
        for exe in self.BROWSERS.get(choice, []):
            if os.path.exists(exe):
                try:
                    import subprocess

                    subprocess.Popen([exe, url])
                    return
                except Exception as e:
                    print("browser launch failed, using default:", e)
                    break
        webbrowser.open_new_tab(url)

    def _open_search(self, text):
        """Open the web search for `text`, using the engine chosen in Settings
        (Google / Perplexity / Brave) and the chosen browser. Perplexity opens
        with the question pre-filled and the search already running."""
        import urllib.parse

        query = urllib.parse.quote((text or "").strip())
        if not query:
            return
        engine = (self.settings.get("search_engine", "perplexity") or
                  "perplexity").lower()
        template = self.SEARCH_ENGINES.get(
            engine, self.SEARCH_ENGINES["perplexity"])
        self.open_in_browser(template.format(q=query))

    def _sane_press_hotkey(self, key, default):
        """Read a SINGLE-PRESS hotkey from settings and refuse a bare modifier.

        A lone Ctrl/Alt/Shift/Win as a tap hotkey fires on every press of that
        modifier — this is exactly the 'Ctrl alone starts Mumble' bug. The web-UI
        binding-capture path saves straight through set_setting (no validate()),
        so this is the last line of defence and runs on EVERY registration: if the
        stored spec decayed to a bare modifier, heal it back to the default and
        persist the fix so the bad value can never come back."""
        spec = bindings.normalize(self.settings.get(key, default) or default)
        # Cloud settings may originate from the macOS build.  The Windows
        # keyboard library accepts "option" syntactically, but Windows can
        # never emit that key, leaving a registered-looking shortcut dead.
        impossible_mac_key = (
            sys.platform == "win32"
            and "option" in {part.strip() for part in spec.split("+")}
        )
        valid, validation_message = bindings.validate(spec)
        if not valid or impossible_mac_key:
            reason = ("contains the macOS-only Option key"
                      if impossible_mac_key else
                      (validation_message or "is invalid"))
            print(f"[hotkey-heal] {key}={spec!r} {reason} — "
                  f"reverting to {default!r}")
            spec = bindings.normalize(default)
            try:
                self.settings.set(key, spec)
            except Exception:
                pass
        return spec

    _PRESS_BINDING_LABELS = {
        "hotkey": "Start / stop dictation",
        "quick_paste_hotkey": "Paste latest",
        "history_hotkey": "Open Deck",
        "search_hotkey": "Open Mumble Find",
    }

    def _press_binding_values(self):
        return {
            "hotkey": self.settings.get("hotkey", "ctrl+windows"),
            "quick_paste_hotkey": self.settings.get(
                "quick_paste_hotkey", "ctrl+alt+v"),
            "history_hotkey": self.settings.get(
                "history_hotkey", "ctrl+alt+d"),
            "search_hotkey": self.settings.get(
                "search_hotkey", SEARCH_HOTKEY_DEFAULT),
        }

    def _binding_conflict(self, key, spec):
        found = bindings.find_conflict(
            spec, self._press_binding_values(), exclude=key)
        if not found:
            return None
        other_key, other_spec = found
        label = self._PRESS_BINDING_LABELS.get(other_key, "another Mumble action")
        return (f"That shortcut overlaps {label} "
                f"({bindings.pretty(other_spec)}). Choose a different combination.")

    def _active_binding_conflict(self, key, spec):
        active = {}
        for other_key, attr, handle_attr in (
            ("hotkey", "hotkey", "_hk_main"),
            ("quick_paste_hotkey", "quick_hotkey", "_hk_quick"),
            ("history_hotkey", "history_hotkey", "_hk_history"),
            ("search_hotkey", "search_hotkey", "_hk_search"),
        ):
            if other_key != key and getattr(self, handle_attr, None) is not None:
                active[other_key] = getattr(self, attr, "")
        found = bindings.find_conflict(spec, active)
        if not found:
            return None
        other_key, other_spec = found
        return (f"{self._PRESS_BINDING_LABELS.get(key, key)} overlaps "
                f"{self._PRESS_BINDING_LABELS.get(other_key, other_key)} "
                f"({bindings.pretty(other_spec)}).")

    def _register_hotkey(self):
        spec = self._sane_press_hotkey("hotkey", "ctrl+windows")
        conflict = self._active_binding_conflict("hotkey", spec)
        if conflict:
            raise ValueError(conflict)
        new_handle = bindings.register_hotkey(spec, self.on_hotkey)
        old_handle = self._hk_main
        if not bindings.unregister(old_handle):
            bindings.unregister(new_handle)
            raise RuntimeError("the previous dictation shortcut could not be released")
        self.hotkey, self._hk_main = spec, new_handle

    def _register_quick(self):
        spec = self._sane_press_hotkey(
            "quick_paste_hotkey", "ctrl+alt+v")
        try:
            conflict = self._active_binding_conflict(
                "quick_paste_hotkey", spec)
            if conflict:
                raise ValueError(conflict)
            new_handle = bindings.register_hotkey(spec, self.on_quick_paste)
            old_handle = self._hk_quick
            if not bindings.unregister(old_handle):
                bindings.unregister(new_handle)
                raise RuntimeError("the previous paste shortcut could not be released")
            self.quick_hotkey, self._hk_quick = spec, new_handle
        except Exception as e:
            print("quick-paste hotkey error:", e)
            raise  # let _apply_settings_change log the failure with context — don't
                   # swallow it (the UI was reporting "active now" for a dead key)

    def _register_history(self):
        spec = self._sane_press_hotkey(
            "history_hotkey", "ctrl+alt+d")
        try:
            conflict = self._active_binding_conflict("history_hotkey", spec)
            if conflict:
                raise ValueError(conflict)
            new_handle = bindings.register_hotkey(spec, self.on_open_history)
            old_handle = self._hk_history
            if not bindings.unregister(old_handle):
                bindings.unregister(new_handle)
                raise RuntimeError("the previous Deck shortcut could not be released")
            self.history_hotkey, self._hk_history = spec, new_handle
        except Exception as e:
            print("history hotkey error:", e)
            raise  # surface the failure (see _register_quick)

    def _register_search(self):
        spec = self._sane_press_hotkey(
            "search_hotkey", SEARCH_HOTKEY_DEFAULT)
        try:
            conflict = self._active_binding_conflict("search_hotkey", spec)
            if conflict and spec != SEARCH_HOTKEY_DEFAULT:
                # Old builds allowed duplicate persisted shortcuts. Search is
                # registered last, so heal only its conflicting value and keep
                # the already-active dictation, paste and Deck bindings intact.
                fallback_conflict = self._active_binding_conflict(
                    "search_hotkey", SEARCH_HOTKEY_DEFAULT)
                if not fallback_conflict:
                    print(f"[hotkey-heal] search_hotkey={spec!r} {conflict} — "
                          f"reverting to {SEARCH_HOTKEY_DEFAULT!r}")
                    spec = SEARCH_HOTKEY_DEFAULT
                    if self.settings.set("search_hotkey", spec) is False:
                        raise RuntimeError(
                            "the repaired Search shortcut could not be saved")
                    conflict = None
            if conflict:
                raise ValueError(conflict)
            new_handle = bindings.register_hotkey(spec, self.on_search_hotkey)
            old_handle = self._hk_search
            if not bindings.unregister(old_handle):
                bindings.unregister(new_handle)
                raise RuntimeError("the previous Search shortcut could not be released")
            self.search_hotkey, self._hk_search = spec, new_handle
        except Exception as e:
            print("search hotkey error:", e)
            raise  # surface the failure (see _register_quick)

    # ===================================================== actions for the UI
    def _apply_press_binding(self, key, hk, attr, handle_attr, callback, success):
        with self._binding_lock:
            hk = bindings.normalize(hk)
            ok, msg = bindings.validate(hk)
            if not ok:
                return False, msg
            conflict = self._binding_conflict(key, hk)
            if conflict:
                return False, conflict
            current = bindings.normalize(self.settings.get(key, ""))
            if current == hk and getattr(self, handle_attr, None) is not None:
                return True, success.format(binding=bindings.pretty(hk))
            try:
                new_handle = bindings.register_hotkey(hk, callback)
            except Exception as e:
                return False, f"Couldn't register: {e}"
            if self.settings.set(key, hk) is False:
                released_new = bindings.unregister(new_handle)
                # Settings keeps the attempted value in memory after an I/O
                # failure. Restore both its in-memory view and the durable value
                # when possible before reporting the unchanged live shortcut.
                restored = self.settings.set(key, current)
                if not released_new or restored is False:
                    return False, ("The shortcut change could not be reconciled. "
                                   "Restart Mumble before trying another binding.")
                return False, ("Couldn't save the shortcut. Your previous "
                               "shortcut is still active.")
            old_handle = getattr(self, handle_attr, None)
            if not bindings.unregister(old_handle):
                released_new = bindings.unregister(new_handle)
                restored = self.settings.set(key, current)
                if not released_new or restored is False:
                    return False, ("The shortcut hooks could not be reconciled. "
                                   "Restart Mumble before trying another binding.")
                return False, ("Couldn't release the previous shortcut, so the "
                               "change was cancelled.")
            setattr(self, attr, hk)
            setattr(self, handle_attr, new_handle)
            return True, success.format(binding=bindings.pretty(hk))

    def apply_hotkey(self, hk):
        return self._apply_press_binding(
            "hotkey", hk, "hotkey", "_hk_main", self.on_hotkey,
            "Saved — dictation is now {binding}.")

    def apply_quick_paste_hotkey(self, hk):
        return self._apply_press_binding(
            "quick_paste_hotkey", hk, "quick_hotkey", "_hk_quick",
            self.on_quick_paste, "Saved — paste latest is now {binding}.")

    def apply_history_hotkey(self, hk):
        return self._apply_press_binding(
            "history_hotkey", hk, "history_hotkey", "_hk_history",
            self.on_open_history, "Saved — the Deck opens with {binding}.")

    def apply_search_hotkey(self, hk):
        return self._apply_press_binding(
            "search_hotkey", hk, "search_hotkey", "_hk_search",
            self.on_search_hotkey, "Saved — Mumble Find opens with {binding}.")

    def set_search_engine(self, engine):
        """Choose the provider used for an explicit Deck web search."""
        engine = (engine or "perplexity").strip().lower()
        if engine not in self.SEARCH_ENGINES:
            engine = "perplexity"
        self.settings.set("search_engine", engine)
        return engine

    def list_microphones(self):
        """Input devices, DE-DUPLICATED (owner v4). Windows/PortAudio enumerates
        the SAME physical mic once per host API (MME, DirectSound, WASAPI,
        WDM-KS) — e.g. one FIFINE mic showing up four times. We collapse those
        into one entry per physical device:
          • group by normalised name, also treating a truncated name as the same
            device as its fuller form (MME truncates names to ~31 chars, so
            "Microphone (FIFINE K669 Micropho" and the full WASAPI name are one
            device — a prefix match catches this);
          • keep the LOWEST index in each group (opens reliably), but DISPLAY the
            longest/least-truncated name (prettiest)."""
        out = [(None, "System default")]
        try:
            kept = []  # each: [index, display_name, normalised_name]
            for i, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels", 0) <= 0:
                    continue
                name = dev["name"]
                norm = " ".join(str(name).split()).lower()
                if not norm:
                    continue
                match = None
                for k in kept:
                    if norm == k[2] or norm.startswith(k[2]) or k[2].startswith(norm):
                        match = k
                        break
                if match is None:
                    kept.append([i, name, norm])
                elif len(str(name)) > len(str(match[1])):
                    match[1], match[2] = name, norm  # keep the fuller display name
            for i, name, _ in kept:
                out.append((i, name))
        except Exception as e:
            print("device query error:", e)
        return out

    def set_mic(self, idx):
        """Validate the chosen mic by briefly opening it before saving — and do it on a
        worker with a timeout, so a misbehaving driver that hangs on open can't freeze
        the app (this is the most likely cause of 'I changed the mic and it stopped').
        Only a device that actually opens gets persisted. Returns (ok, message)."""
        import secrets

        result = {}
        wake_pause_reason = (
            "microphone-validation:" + secrets.token_urlsafe(8))

        # The wake detector normally owns an idle PortAudio stream.  Release
        # that stream before probing another device so exclusive-mode drivers
        # are tested accurately and anything spoken during validation can
        # never overlap another microphone owner.
        if not self._pause_wake_word(wake_pause_reason):
            self._resume_wake_word(wake_pause_reason)
            return False, (
                "Voice activation couldn't release the microphone in time. "
                "Turn it off and try again."
            )

        def check():
            stream = None
            try:
                stream = sd.InputStream(
                    samplerate=SAMPLE_RATE, channels=1, dtype="float32", device=idx
                )
                stream.start()
                result["ok"] = True
            except Exception as e:
                result["ok"] = False
                result["err"] = str(e)
            finally:
                # A driver can fail after allocating the stream object. Always
                # attempt both operations independently so a stop failure does
                # not skip close and leave the test handle owning the mic.
                if stream is not None:
                    try:
                        stream.stop()
                    except Exception:
                        pass
                    try:
                        stream.close()
                    except Exception:
                        pass
                # On a driver timeout the worker may still be blocked inside
                # PortAudio after set_mic() has returned.  Keep the wake stream
                # excluded until this worker really releases the device.
                self._resume_wake_word(wake_pause_reason)

        worker_started = False
        try:
            t = threading.Thread(target=check, daemon=True)
            t.start()
            worker_started = True
            t.join(timeout=4.0)
            if not result:
                print(f"mic {idx!r} validation timed out (driver hang)")
                return False, "That mic didn't respond in time — kept your previous one."
            if not result.get("ok"):
                print(f"mic {idx!r} validation failed: {result.get('err')}")
                return False, "That mic couldn't be opened — kept your previous one."
            if self.settings.set("mic_device", idx) is False:
                return False, "That mic worked, but the setting could not be saved."
            return True, "Saved — Mumble is listening to this mic."
        finally:
            # Once started, the worker owns wake resumption in its cleanup. If
            # thread creation/start itself failed, no worker exists to do it.
            if not worker_started:
                self._resume_wake_word(wake_pause_reason)

    def _cleanup_orphan_audio(self):
        """Audio retention policy: audio lives only in memory during a session —
        nothing in the pipeline writes audio to disk. This boot sweep is
        defensive insurance: if a past version or crash ever left audio files
        in the data folder, remove them so recordings never accumulate."""
        try:
            removed = 0
            for fn in os.listdir(branding.DATA_DIR):
                if fn.lower().endswith((".wav", ".mp3", ".flac", ".ogg", ".webm")):
                    try:
                        os.remove(os.path.join(branding.DATA_DIR, fn))
                        removed += 1
                    except OSError:
                        pass
            if removed:
                print(f"[cleanup] removed {removed} orphaned audio file(s)")
        except Exception as e:
            print("audio cleanup error:", e)

    def _pick_device(self):
        """Choose the transcription device: CUDA when an NVIDIA GPU is usable
        (3-4× faster, float16), else CPU with the configured compute type.
        Controlled by the 'device' setting: auto (default) | cpu | cuda."""
        pref = self.settings.get("device", "auto")
        if pref == "cpu":
            return "cpu", self.settings.get("compute_type", "int8")
        try:
            import ctranslate2

            if ctranslate2.get_cuda_device_count() > 0:
                print("[model] CUDA GPU detected — using device=cuda, float16")
                return "cuda", "float16"
        except Exception as e:
            print("GPU detection failed (using CPU):", e)
        if pref == "cuda":
            print("[model] device=cuda requested but no CUDA GPU found — using CPU")
        return "cpu", self.settings.get("compute_type", "int8")

    def _effective_model(self, name=None):
        """The model name that ACTUALLY gets loaded into WhisperModel.

        Resource Saver Mode forces the lightest engine ("tiny.en") regardless of
        the user's configured/displayed model — without overwriting the saved
        "model" setting (so flipping the saver back restores their real choice).
        When the saver is off this is a pass-through to the requested/configured
        model. `name` lets callers ask "what would <name> resolve to"; with no
        argument it resolves the user's saved model."""
        if self.settings.get("resource_saver"):
            return "tiny.en"
        if name is not None:
            return name
        return self.settings.get("model", "small.en")

    # ---- Automatic hardware- & language-aware model selection --------------
    def _resolved_tier(self):
        """Detect the effective hardware tier without storing a user PC class."""
        try:
            detected = branding.detect_hardware_tier()
        except Exception:
            detected = "mid"
        print(f"[hardware] auto-detected tier → {detected}")
        return detected

    def _apply_hardware_model(self, force=False, load=True):
        """Resolve the local model from the hardware tier + English-only preference
        and load it if it changed. Hardware is detected automatically and the
        language scope maps it to a faster-whisper model. `load=False` only
        records the resolved id (used at boot, where the normal boot path loads it
        right after, so we must not kick off a second async load). Skips the live
        reload while a cloud STT is active (no local model is resident then)."""
        tier = self._resolved_tier()
        english_only = bool(self.settings.get("english_only", True))
        # Primary-language swap (the Big Shift language step): when the user names
        # the language they mostly dictate in, resolve the model for THAT language
        # (English → lean .en model; anything else → multilingual). Falls back to
        # the english_only flag when no primary language is recorded.
        primary_lang = (self.settings.get("primary_language", "") or "").strip().lower()
        if primary_lang:
            want = branding.model_for_language(tier, primary_lang)
            english_only = branding.MODEL_BY_LANGUAGE.get(primary_lang, False)
        else:
            want = branding.resolve_model(tier, english_only)
        # VAL-CROSS-018: keep the cloud provider's constitution language-aware.
        # When the user dictates in a non-English language, cloud system prompts
        # drop the British-English rule and gain a target-language directive.
        try:
            ai.set_language_context(primary_lang or "en", english_only)
        except Exception:
            pass
        cur = self.settings.get("model", "small.en")
        if want == cur and not force:
            return want
        print(f"[hardware] tier={tier} english_only={english_only} "
              f"lang={primary_lang or '-'} → model {want!r}")
        try:
            self.settings.set("model", want)
        except Exception:
            pass
        if load and not self._cloud_transcription_on():
            self.set_model(want)
        else:
            self.model_name = want
        return want

    def _try_load(self, name):
        """Load `name` into self.model. Returns True on success. On FAILURE the previous
        self.model is left untouched (the assignment only lands if WhisperModel succeeds),
        so a failed switch keeps the app working on the model it already had.

        Resource Saver Mode is honoured at THIS chokepoint: the requested `name`
        is resolved through `_effective_model`, so every load path (boot, hot-
        swap, fallback) loads the lightest model when the saver is on — while the
        caller still records the user's real choice in `self.model_name`."""
        name = self._effective_model(name)
        self._trace_mark("model_load_started", model=name)
        try:
            # Use the machine's cores — CTranslate2's own default is a fixed 4
            # threads regardless of hardware, which leaves most of a modern CPU
            # idle during transcription. Auto (setting 0) now claims every core
            # except two, so dictation gets real speed while the rest of the
            # system stays responsive. An explicit setting still wins.
            threads = self.settings.get("cpu_threads", 0) or 0
            if not threads:
                from perf.optimizer import recommended_stt_threads

                threads = recommended_stt_threads()
            print(f"[model] STT thread budget: {int(threads)}")
            device, compute = self._pick_device()
            try:
                new_model = WhisperModel(
                    name,
                    device=device,
                    compute_type=compute,
                    cpu_threads=int(threads),
                )
            except Exception as e:
                # A CUDA device that detects but won't initialise (driver/cuDNN
                # mismatch) must never brick model loading — retry on CPU.
                if device != "cpu":
                    print(f"GPU load failed ({e}) — falling back to CPU")
                    device = "cpu"
                    compute = self.settings.get("compute_type", "int8")
                    new_model = WhisperModel(
                        name,
                        device=device,
                        compute_type=compute,
                        cpu_threads=int(threads),
                    )
                else:
                    raise
            # ITEM 20: remember what actually loaded so the per-transcribe perf
            # diagnostic can name the device/compute/model without re-probing CUDA.
            self._stt_device, self._stt_compute, self._stt_model = device, compute, name
            # Release the old model BEFORE swapping to avoid two models coexisting
            # in RAM (small.en ~500MB + medium.en ~1.5GB = ~2GB memory pressure).
            old_model = None
            with self.lock:
                old_model = self.model
                self.model = new_model
            # Explicitly drop the old model reference so the GC can free it and
            # CTranslate2 can release its native thread pool / memory.
            del old_model
            # Aggressive GC after model swap — idle memory target < 1GB (VAL-PERF-003).
            try:
                import gc
                gc.collect()
            except Exception:
                pass
            self._warm_model(new_model)
            self._trace_mark(
                "model_load_finished", model=name, success=True,
                device=device, compute_type=compute,
            )
            return True
        except Exception as e:
            self._trace_mark(
                "model_load_finished", model=name, success=False,
                error_class=type(e).__name__,
            )
            print(f"model '{name}' load error:", e)
            return False

    def _ensure_local_model(self):
        """Guarantee a local faster-whisper model is loaded, loading it ON DEMAND
        if cloud mode deferred it at boot. Called from the local transcribe path
        when `self.model is None` — i.e. cloud failed and we're falling back, or a
        mode-key dictation needs local word timestamps. Returns True if a model is
        available afterwards."""
        with self.lock:
            if self.model is not None:
                return True
        name = self._effective_model()
        print(f"[model] loading local model '{name}' on demand…")
        if self._try_load(name):
            return True
        # Last resort: the default, so a cloud-failure fallback still lands.
        return self._try_load("base.en")

    def _unload_local_model(self):
        """Drop the local model from RAM (used when switching to cloud mode so the
        ~0.5–1.5 GB model isn't held for a session that never transcribes locally).
        The next local dictation reloads it via _ensure_local_model."""
        import gc
        with self.lock:
            old = self.model
            self.model = None
        del old
        gc.collect()
        print("[model] local model unloaded (cloud mode active)")

    def _warm_model(self, model):
        """Throwaway decodes straight after a model loads, so the FIRST real
        dictation runs at full speed instead of paying every lazy-init bill at
        the worst moment (when the user is judging Mumble).

        TWO passes, because a real dictation uses vad_filter=True (owner v6 — the
        missing half of the old warm-up):
          1. vad_filter=False over 0.6s silence → builds CTranslate2's thread
             pool + JIT-compiles its decoder kernels.
          2. vad_filter=True over 1s of low-amplitude noise → forces faster-
             whisper's lazy Silero VAD: the onnxruntime InferenceSession is
             created and its graph compiled here, NOT on the user's first words.
        Both run under self._tx_lock (CTranslate2 isn't safe to run concurrently
        with a real transcribe)."""
        if model is None:
            return
        self._trace_mark("model_warmup_started")
        lang = "en" if self.model_name.endswith(".en") else \
            self.settings.get("language", "en")
        if not self._tx_lock.acquire(timeout=10):
            self._trace_mark(
                "model_warmup_finished", success=False,
                state="lock_timeout",
            )
            return
        try:
            t0 = time.time()
            silence = np.zeros(int(SAMPLE_RATE * 0.6), dtype=np.float32)
            segs, _ = model.transcribe(
                silence, language=lang, beam_size=1, vad_filter=False
            )
            for _ in segs:   # a generator — decoding happens on iteration
                pass
            print(f"[model] decoder warmed in {time.time() - t0:.2f}s")
            # Warm the VAD path used by every real dictation (vad_filter=True).
            try:
                t1 = time.time()
                noise = (np.random.default_rng(7)
                         .standard_normal(SAMPLE_RATE).astype(np.float32) * 0.003)
                segs, _ = model.transcribe(
                    noise, language=lang, beam_size=1, vad_filter=True
                )
                for _ in segs:
                    pass
                print(f"[vad] warmed in {time.time() - t1:.2f}s — first dictation "
                      "runs at full speed")
            except Exception as e:
                print("[vad] warm-up skipped:", e)
        except Exception as e:
            print("[model] warm-up skipped:", e)
        finally:
            self._tx_lock.release()
            self._trace_mark("model_warmup_finished")

    def set_model(self, name, on_done=None):
        if name == self.model_name:
            if on_done:
                on_done(True, name)
            return
        # NB: do NOT persist the choice here — only after it actually loads (see below),
        # so a model that won't download can never poison settings and brick the next boot.
        threading.Thread(
            target=self._load_model,
            args=(name,),
            kwargs={"on_done": on_done},
            daemon=True,
        ).start()

    def _load_model(self, name, on_done=None):
        prev = self.model_name
        self._set_state("loading")
        if self._try_load(name):
            self.model_name = name
            self.settings.set("model", name)
            if self.recording:
                self._set_state("listening")  # don't clobber active recording state
            else:
                self._idle()
            if on_done:
                on_done(True, name)
            return
        # The switch failed (e.g. a bigger model whose first-time download didn't complete).
        # self.model still holds the previous working model, so roll the choice back and
        # keep dictating — never strand the app in "error".
        self.model_name = prev
        if self.model is not None:
            self._notify(
                "Couldn't switch model",
                f"That model didn't load — kept “{prev}”. Bigger models download "
                "the first time, so check your connection and try again.",
            )
            self._idle()
        else:
            self._notify(
                "Model failed to load", "Open Settings to choose another model."
            )
            self._set_state("error")
        if on_done:
            on_done(False, name)

    def _ai_cfg(self):
        """Active LLM config. Cerebras is the recommended default and OpenRouter
        is the curated multi-model alternative.
        The key is MANDATORY and user-supplied — it lives in local settings.json only
        (never in source/git); there is no built-in/default key."""
        provider = (self.settings.get("llm_provider", "cerebras") or "").strip().lower()
        if provider not in TEXT_PROCESSING_PROVIDERS:
            # Preserve an old/custom provider id on disk, but never reinterpret
            # it as Cerebras and send text with a different provider's key.
            return {
                "url": "", "models_url": "", "key": "", "model": "",
                "provider": provider, "key_setting": "", "supported": False,
            }
        info = ai.PROVIDERS[provider]
        key = (self.settings.get(info["key_setting"], "") or "").strip()
        # Fall back to THIS provider's default model, not Cerebras's — a user who
        # blanked the model field for anthropic/openai/local previously got
        # "gpt-oss-120b" sent to that endpoint (a guaranteed 400/404).
        model = (self.settings.get(info["model_setting"], "")
                 or info.get("default_model") or "gpt-oss-120b")
        url = info["url"]
        if provider == "cerebras":
            models_url = ai.CEREBRAS_MODELS_URL
        else:
            models_url = url.replace("/chat/completions", "/models")
        return {
            "url": url,
            "models_url": models_url,
            "key": key,
            "model": model,
            "provider": provider,
            "key_setting": info["key_setting"],
            "supported": True,
        }

    def _ai_key(self):
        return self._ai_cfg()["key"]

    def set_llm_provider(self, provider, model="", key=None):
        """Switch the AI engine (Settings → AI Provider). Saves the provider,
        model and key, then returns (ok, message)."""
        if provider not in TEXT_PROCESSING_PROVIDERS:
            return False, "Unknown provider."
        info = ai.PROVIDERS[provider]
        updates = {"llm_provider": provider}
        if model:
            updates[info["model_setting"]] = model
        if key is not None:
            updates[info["key_setting"]] = (key or "").strip()
        if self.settings.update(**updates) is False:
            return False, "Could not save the provider configuration."
        if key is not None:
            self.pro_key_failed = False
        cfg = self._ai_cfg()
        label = info["label"].split(" (")[0]
        return True, f"Using {label} — model '{cfg['model']}'."

    def test_provider(self):
        """Live connection test for the active provider. Returns (ok, message)."""
        cfg = self._ai_cfg()
        if not cfg.get("supported", True):
            return False, "Choose a supported processing provider in Settings first."
        label = ai.PROVIDERS[cfg["provider"]]["label"].split(" (")[0]
        if not cfg["key"]:
            return False, f"Add your {label} API key first."
        try:
            ok = ai.key_ok(cfg["key"] or "x", models_url=cfg["models_url"])
        except Exception as e:
            return False, f"Connection failed: {e}"
        if ok is True:
            return True, f"Connected — {label} is ready."
        if ok is False:
            return False, "Invalid key."
        return False, "Connection failed — check your network and key."

    def _pro_fallback_notice(self, err):
        """React to an AI failure: tell a rejected key apart from no internet."""
        is_http = isinstance(err, urllib.error.HTTPError)
        code = err.code if is_http else None
        if code is None:
            # ai.py wraps HTTP failures as RuntimeError("API HTTP <code>: ...")
            # (with `from None`), so the original HTTPError type is gone. Recover the
            # status from the message so a key rejected MID-SESSION is still caught.
            s = str(err)
            if "HTTP " in s:
                try:
                    code = int(s.split("HTTP ", 1)[1].split(":")[0].split()[0])
                except (ValueError, IndexError):
                    code = None
        is_key = code in (401, 402, 403)
        is_net = isinstance(err, (urllib.error.URLError, TimeoutError)) and not is_http
        if is_key:
            self.pro_key_failed = True
        if self._pro_notified:
            return
        self._pro_notified = True
        print("Cerebras unavailable:", err)
        if is_key:
            msg = (
                "Your Cerebras key was rejected — using the offline builder. "
                "Check your key in Settings  Pro Mode."
            )
        elif is_net:
            msg = (
                "No internet — Mumble used offline mode (the built-in builder) just now. "
                "It switches back to Pro AI automatically once you're online."
            )
        else:
            msg = "Pro AI hit a snag — used the offline builder just now."
        self._notify("Mumble", msg)

    def _capture_focused_copy(self, select_all=False, timeout=0.13):
        """Copy focused text without first overwriting the user's clipboard.

        A clipboard sequence change proves Ctrl+C produced data even when the
        selected text equals the clipboard's existing text. On platforms without
        a sequence counter, the conservative fallback accepts only changed text.
        Capture shares the paste lock so restore/copy/paste operations cannot cross.
        """
        if not self._paste_lock.acquire(timeout=1.5):
            return ""
        monitor = self.clipboard
        if monitor:
            monitor.pause()
        try:
            try:
                before = pyperclip.paste() or ""
            except Exception:
                before = ""
            before_seq = _clipboard_seq()
            for mod in ("ctrl", "alt", "shift", "windows"):
                try:
                    keyboard.release(mod)
                except Exception:
                    pass
            if select_all:
                keyboard.send("ctrl+a")
                time.sleep(0.06)
            keyboard.send("ctrl+c")
            deadline = time.time() + max(0.05, float(timeout))
            got = ""
            while time.time() < deadline:
                time.sleep(0.012 if not select_all else 0.02)
                after_seq = _clipboard_seq()
                try:
                    current = pyperclip.paste() or ""
                except Exception:
                    current = ""
                changed = (before_seq is not None and after_seq is not None
                           and after_seq != before_seq)
                if (changed and current) or (
                        before_seq is None and current and current != before):
                    got = current.strip()
                    break
            # Restore readable text only. If the prior clipboard was non-text,
            # doing nothing on a missed copy preserves it; arbitrary formats cannot
            # be recreated safely through pyperclip after a successful text copy.
            if before and got:
                self._set_clipboard(before)
            return got
        except Exception as e:
            print("focused copy capture error:", e)
            return ""
        finally:
            if monitor:
                try:
                    monitor._last_text = pyperclip.paste() or ""
                except Exception:
                    pass
                try:
                    monitor.resume(skip_current=True)
                except Exception as e:
                    print("clipboard resume error after capture:", e)
            self._paste_lock.release()

    def _grab_selection_quiet(self):
        """Return the current focused selection without disturbing the clipboard."""
        return self._capture_focused_copy(select_all=False, timeout=0.13)

    def _capture_conversation_quiet(self):
        """ITEM 5 (owner 2026-06-29) — capture a WHOLE AI conversation, not just one
        reply. Sends Ctrl+A then Ctrl+C to the FOCUSED chat (ChatGPT / Claude /
        Gemini / any web or desktop chat) so the ENTIRE thread is grabbed in one
        shot — INCLUDING messages scrolled out of view, because Select-All reaches
        the full document, not only the visible viewport. Restores the prior
        clipboard. Returns the captured transcript ('' if nothing came back). This
        is the robust, app-agnostic alternative to a brittle per-site DOM scraper:
        it works anywhere Ctrl+A / Ctrl+C work, with no fake/never-wired button."""
        return self._capture_focused_copy(select_all=True, timeout=0.7)

    def capture_conversation(self):
        """ITEM 5: grab the whole focused AI conversation and store it as prompting
        context (replacing any previous full-conversation capture). Returns a result
        dict the caller (hotkey / Deck button / command port) reports from. After a
        capture, dictating in Prompt mode pulls this thread in as context."""
        text = self._capture_conversation_quiet()
        if not text or len(text) < 40:
            msg = ("No conversation captured — click into the chat first, then try "
                   "again (Select-All must reach the messages).")
            if self.island:
                self._tk_schedule(self.island.hint, "No conversation found")
                self._tk_schedule(self.island.wake)
            return {"ok": False, "message": msg}
        try:
            res = self.conv_store.capture_conversation(text)
        except Exception as e:
            print("capture_conversation store error:", e)
            return {"ok": False, "message": str(e)}
        turns = int(res.get("turns", 0))
        ui = res.get("ui", "")
        self._bump_feature("capture")
        if self.island:
            tail = f" · {ui}" if ui else ""
            self._tk_schedule(self.island.hint,
                              f"Conversation captured · {turns} turns{tail}")
            self._tk_schedule(self.island.wake)
        self._send_webui_async({"cmd": "refresh", "what": "history"})
        return {"ok": True, "chars": len(text), "turns": turns, "ui": ui}

    def _gather_context(self, det_mode, clip_count, mode_active):
        """Decide what context to feed the AI and how strictly. Returns (context, strict).

        Reply's source (owner directive 2026-06-13) — HIGHLIGHTED text first, the
        most recent clipboard item as the fallback. This gives Reply a clear,
        distinct purpose: reply to whatever you've selected on screen, or to what
        you last copied. Other modes: a held-key selection is the direct source.
        1) clip_count > 0 (Reply mode): highlighted selection → else latest clip.
        2) Otherwise, if the mode key is held AND the user has text highlighted
           with the mouse → that SELECTION is the source, used directly.
        3) Else nothing — no clipboard is ever sent unasked."""
        if clip_count:
            # Reply mode: prefer the user's HIGHLIGHTED selection (the thing they're
            # looking at); then a deliberately-captured conversation (Capture chat),
            # which is richer than a single clipboard item; then the latest clipboard.
            if mode_active:
                sel = self._grab_selection_quiet()
                if sel:
                    return f"[Reference] (highlighted selection)\n{sel}", False
            # Refinement pass §6: a captured conversation feeds Reply automatically,
            # so the user no longer has to re-highlight what they're replying to.
            try:
                if self.conv_store.has_conversation():
                    conv = self.conv_store.get_conversation_context(6)
                    if conv and conv.strip():
                        return f"[Conversation]\n{conv}", False
            except Exception:
                pass
            try:
                for it in self.clipboard.recent(1):
                    if it.get("type") == "text":
                        t = (it.get("text") or "").strip()
                        if t and not self.clipboard.is_own(t):
                            return f"[Reference] (latest clipboard)\n{t}", False
            except Exception:
                pass
            return "", False
        if mode_active:
            sel = self._grab_selection_quiet()
            if sel:
                return sel, True  # highlighted selection → strict, direct source
        return "", False

    # ------------------------------------------------------- AI dispatch
    def _collect_text(self, gen):
        """Drain a streaming generator into its full raw text (no cleaning — callers that
        need to parse a MODE/CONF tail must see it intact)."""
        partial = []
        for chunk in gen:
            if chunk.startswith(ai.FULL_MARKER):
                return chunk[len(ai.FULL_MARKER) :]
            if chunk.startswith(ai.TRUNC_MARKER):
                # The model hit the token cap mid-answer (a long email/reply
                # with no continuation pass). Hand back the text we got, but tell
                # the user it may be cut off — the raw transcript is in History.
                try:
                    self._notify(
                        "Mumble",
                        "That response was very long and may be cut off at the "
                        "end — the raw transcript is saved in the Deck.")
                except Exception:
                    pass
                return chunk[len(ai.TRUNC_MARKER) :]
            partial.append(chunk)
        return "".join(partial)

    def _collect(self, gen, mode):
        raw = self._collect_text(gen)
        if mode == "prompt":
            return mode, ai._extract_final_prompt(raw)
        return mode, ai._clean(raw)

    def _cloud_generate(
        self,
        raw,
        name,
        context="",
        mode_hint="text",
        request="",
        mode_active=False,
        prefs=None,
        context_strict=False,
        keyword_template="",
        words=None,
        window_words=None,
        cfg=None,
        prompt_cfg=None,
        invocation_snapshot=None,
        route_decision=None,
        expected_feature=None,
        expected_lane=None,
        target_lease=None,
        operation_id=None,
    ):
        """Send to the AI, routed by lane:

        - prompt → the Prompt Architect constitution (sent every call).
        - email/reply → focused per-mode prompts (high reasoning).
        - foreign → annotate Arabic/Islamic terms with slash candidates, AI disambiguates.
        - text → minimal POLISH (cheap). If the mode key was held but no keyword was
          found, the polish call also returns a MODE/CONF second opinion we act on.

        When *cfg* / *prompt_cfg* are provided they are used as the frozen AI
        config for the duration of this call (VAL-CROSS-011: in-flight key
        survives a mid-call settings change).  When absent the config is read
        fresh from settings (legacy / redo paths that are effectively a new
        request).

        Returns (mode, output) or raises."""
        if isinstance(invocation_snapshot, processing_route.ProcessingInputSnapshot):
            decision = invocation_snapshot.route
            route_decision = decision
            provider_info = ai.PROVIDERS.get(decision.provider) or {}
            cfg = {
                "key": decision.api_key,
                "provider": decision.provider,
                "model": decision.model,
                "url": provider_info.get("url", ""),
                "supported": decision.provider_supported,
            }
            prompt_cfg = cfg
            name = invocation_snapshot.user_name
            prefs = invocation_snapshot.prompt_prefs_dict()
            context = invocation_snapshot.context
            context_strict = invocation_snapshot.context_strict
        actual_feature = (
            mode_hint if mode_hint in {"prompt", "email", "reply"}
            else "dictation"
        )
        if expected_feature != actual_feature or expected_lane != mode_hint:
            raise processing_route.HostedRouteBlocked(route_decision)
        if cfg is None:
            cfg = self._ai_cfg()
        if prompt_cfg is None:
            prompt_cfg = cfg
        key, model, url = cfg["key"], cfg["model"], cfg["url"]
        # VAL-CROSS-018: refresh the cloud constitution's language context from
        # the live settings on every AI call so non-English dictation strips the
        # British-English rule and gains a target-language directive.
        try:
            if isinstance(invocation_snapshot, processing_route.ProcessingInputSnapshot):
                _pl = invocation_snapshot.primary_language
                _eo = invocation_snapshot.english_only
            else:
                _pl = (self.settings.get("primary_language", "") or "").strip().lower()
                _eo = bool(self.settings.get("english_only", True))
            if _pl:
                _eo = branding.MODEL_BY_LANGUAGE.get(_pl, False)
            ai.set_language_context(_pl or "en", _eo)
        except Exception:
            pass
        req = (request or raw or "").strip()
        # For prompt/email/reply, use the FULL raw transcript as content.
        # det_request (keyword-stripped) can lose text when the keyword is at the
        # end of a long sentence. The AI is smart enough to understand intent from
        # the full text — the keyword just tells us which lane to route to.
        content = raw if mode_hint in ("prompt", "email", "reply",
                                       "convert") else req

        if mode_hint == "prompt":
            # Inject keyword template if one was matched (e.g. "blog" → blog style)
            if keyword_template:
                content = keyword_template + "\n\n" + content
            pcfg = prompt_cfg
            draft = ""
            for attempt in (1, 2):
                try:
                    gen = ai.cerebras_prompt(
                        content,
                        pcfg["key"],
                        context,
                        pcfg["model"],
                        url=pcfg["url"],
                        prefs=prefs,
                        context_strict=context_strict,
                        route_decision=route_decision,
                    )
                    draft = self._collect_text(gen)
                    break
                except RuntimeError as e:
                    # The prompt lane is the QUALITY lane — falling to the
                    # offline scaffold on a transient 429 produces exactly the
                    # weak prompts the owner complained about. Retry once, but
                    # SMARTLY: the 429 carries the server's own Retry-After
                    # (typically a few seconds), so wait that — capped at 25s
                    # (owner: a minute-long stall is unacceptable). No header →
                    # 15s. If the retry still 429s, fall back rather than camp.
                    msg = str(e)
                    if "429" in msg and attempt == 1:
                        wait = 15.0
                        m = re.search(r"RETRY_AFTER=([\d.]+)", msg)
                        if m:
                            try:
                                wait = float(m.group(1)) + 1.0
                            except ValueError:
                                pass
                        wait = max(2.0, min(wait, 25.0))
                        print(f"[prompt] 429 — server says retry in ~{wait:.0f}s; "
                              "one quick retry (capped at 25s)")
                        time.sleep(wait)
                        continue
                    raise
            return "prompt", ai._extract_final_prompt(draft)
        if mode_hint == "email":
            return self._collect(
                ai.cerebras_email(content, name, key, context, model, url=url,
                                  route_decision=route_decision), "email"
            )
        if mode_hint == "reply":
            return self._collect(
                ai.cerebras_reply(content, name, key, context, model, url=url,
                                  route_decision=route_decision), "reply"
            )
        if mode_hint == "convert":
            # Convert is ONLY a router now (owner directive 2026-06-13): _process
            # always rewrites it to a real mode or to Text before we get here.
            # If it ever slips through unrouted, clean it up as plain Text — there
            # is no generic JSON/table/prose/units conversion lane.
            return self._collect(
                ai.cerebras_text(content, name, key, context, model, url=url,
                                 route_decision=route_decision), "text"
            )
        if mode_hint == "foreign":
            # Mark low-confidence / foreign tokens with // uncertainty markers
            # using the local phonetic boost engine, then hand to cloud for
            # final disambiguation. The boost engine marks but does not resolve
            # when a cloud key is present (mark_uncertainty mode).
            if isinstance(invocation_snapshot, processing_route.ProcessingInputSnapshot):
                langs = list(invocation_snapshot.foreign_languages) or ["arabic"]
            else:
                langs = self.settings.get("foreign_languages") or ["arabic"]
            marked = foreign_boost.mark_uncertainty(content, languages=langs)
            annotated = islamic_terms.annotate_foreign(marked)
            return self._collect(
                ai.cerebras_foreign(annotated, name, key, context, model, url=url,
                                    languages=langs, route_decision=route_decision),
                "foreign",
            )

        # ---- Lane A: minimal polish (the common, cheap path) ----
        # When the mode key was NOT held, FORCE plain-text-only behaviour:
        # no second opinion, no context, no mode scanning of any kind.
        # This is a hard guard — even if upstream logic has a bug, this
        # lane will never leak clipboard/history into the polish output.
        if not mode_active:
            second_opinion = False
            poll_ctx = ""
            polish_input = content
            print("Status: Polishing mode active - no mode checks performed.")
        else:
            # Mode key was held but no keyword found locally → ALWAYS do a
            # second opinion. This isn't optional — it's the entire fallback
            # for detecting a mode the local engine missed.
            second_opinion = True
            poll_ctx = context
            # When the AI needs to detect a missed mode, annotate low-confidence
            # words with slash alternatives (e.g. "port//prompt") so it can resolve
            # mishearings that the local detector missed.
            polish_input = content
            if words:
                try:
                    annotated = formatting.annotate_uncertain(content, words)
                    if annotated != content:
                        polish_input = annotated
                        print("[uncertain] annotated low-confidence words for AI")
                except Exception as e:
                    print("annotate_uncertain error:", e)
        # Medium-confidence vocabulary candidates: tag "heard//Term" so the AI
        # can decide from context (high-confidence ones were already auto-fixed
        # in _process). On AI failure the offline fallback uses the untouched
        # `raw`, so annotations can never leak into pasted output.
        try:
            terms = (
                invocation_snapshot.vocabulary_terms
                if isinstance(invocation_snapshot, processing_route.ProcessingInputSnapshot)
                else self.settings.get("vocabulary_terms", [])
            )
            if terms:
                annotated = formatting.annotate_vocab_terms(polish_input, terms)
                if annotated != polish_input:
                    polish_input = annotated
                    print("[vocab] annotated medium-confidence terms for AI")
        except Exception as e:
            print("vocab annotate error:", e)
        if not second_opinion:
            # Plain dictation: clean, punctuated text only — no list inference,
            # no email guessing. The user speaks; Mumble transcribes and cleans up,
            # full stop. Very long dictations are split and polished in chunks to
            # avoid silent truncation. Privacy: sees ONLY the transcript — no
            # context/clipboard/history is ever passed in.
            aggr = (
                invocation_snapshot.polish_aggressiveness
                if isinstance(invocation_snapshot, processing_route.ProcessingInputSnapshot)
                else self.settings.get("polish_aggressiveness", "Light")
            )
            try:
                out, truncated = ai.polish_text(
                    polish_input,
                    key,
                    model,
                    url=url,
                    aggressiveness=aggr,
                    route_decision=route_decision,
                )
            except Exception as e:
                print("polish API failed, using offline builder:", e)
                return "text", self._builder(
                    raw, "text", raw, True, invocation_snapshot,
                )[1]
            if truncated:
                self._notify(
                    "Mumble",
                    "That dictation was very long — it was polished in parts and a "
                    "section may still be trimmed. The raw transcript is saved in the Deck.",
                )
            return "text", out
        # Second opinion (mode key held, no keyword) — keep the streaming MODE/CONF
        # path: this is a short missed-keyword check, never a long transcript.
        try:
            gen = ai.cerebras_polish(
                polish_input,
                key,
                model,
                url=url,
                second_opinion=True,
                aggressiveness=(
                    invocation_snapshot.polish_aggressiveness
                    if isinstance(invocation_snapshot, processing_route.ProcessingInputSnapshot)
                    else self.settings.get("polish_aggressiveness", "Light")
                ),
                context=poll_ctx,
                window_words=window_words,
                route_decision=route_decision,
            )
            out = self._collect_text(gen)
        except Exception as e:
            print("polish API failed, using offline builder:", e)
            return "text", self._builder(
                raw, "text", raw, True, invocation_snapshot,
            )[1]
        clean, ai_mode, conf, redo = ai.split_mode_tail(out)
        return self._handle_second_opinion(
            clean, ai_mode, conf, redo, raw, name, poll_ctx, prefs, context_strict,
            cfg=cfg, prompt_cfg=prompt_cfg,
            invocation_snapshot=invocation_snapshot,
            target_lease=target_lease,
            operation_id=operation_id,
        )

    def _conv_context_for_prompt(self):
        """Return captured conversation context for Prompt Mode, or empty string.
        Uses the existing ConversationStore — no new storage class. Zero-friction:
        context is automatically included when a conversation has been captured
        (no settings checkbox or manual setup needed)."""
        try:
            if self.conv_store.has_conversation():
                conv = self.conv_store.get_conversation_context()
                if conv and conv.strip():
                    return (
                        "CONVERSATION CONTEXT (the AI chat thread the user was "
                        "working in — use this to understand background, prior "
                        "turns, and what preceded the user's request):\n"
                        + conv.strip()
                    )
        except Exception:
            pass
        return ""

    def _handle_second_opinion(
        self, clean, ai_mode, conf, redo, raw, name, context, prefs, context_strict,
        cfg=None, prompt_cfg=None, invocation_snapshot=None,
        target_lease=None, operation_id=None,
    ):
        """Act on the AI's free 'the local detector missed a mode' second opinion.
        High-confidence prompt → auto re-run with the constitution; high-confidence
        email/reply → re-run focused; low confidence → the interactive
        'Which mode?' picker (preserves the original words; the old suggestion-chip
        path was replaced by the picker in owner v6)."""
        if cfg is None:
            cfg = self._ai_cfg()
        if prompt_cfg is None:
            prompt_cfg = cfg
        if redo or (ai_mode == "prompt" and conf == "high"):
            try:
                pcfg = prompt_cfg
                # Include captured conversation context for the redo path
                # (same zero-friction injection as the primary prompt lane).
                redo_ctx = context
                if not isinstance(
                    invocation_snapshot, processing_route.ProcessingInputSnapshot
                ):
                    try:
                        conv = self._conv_context_for_prompt()
                        if conv:
                            redo_ctx = conv + ("\n\n" + redo_ctx if redo_ctx else "")
                    except Exception:
                        pass
                gen = ai.cerebras_prompt(
                    (raw or "").strip(),
                    pcfg["key"],
                    redo_ctx,
                    pcfg["model"],
                    url=pcfg["url"],
                    prefs=prefs,
                    context_strict=context_strict,
                    route_decision=(
                        invocation_snapshot.route
                        if isinstance(invocation_snapshot, processing_route.ProcessingInputSnapshot)
                        else None
                    ),
                )
                return self._collect(gen, "prompt")
            except Exception as e:
                print("auto-redo prompt failed:", e)
                return "text", ai._clean(clean)
        if ai_mode in ("email", "reply") and conf == "high":
            try:
                rerun_ctx = context
                # Reply needs the message being replied to. The original dictation
                # had no mode keyword, so context was gathered as plain text — which
                # for reply means NONE. Re-gather reply's fallback source (the
                # latest clipboard item) so the auto-rerun reply isn't written
                # blind with no quoted message. (mode_active=False → skip a
                # now-stale selection grab and use the clipboard directly.)
                if (
                    ai_mode == "reply"
                    and not (rerun_ctx or "").strip()
                    and not isinstance(
                        invocation_snapshot,
                        processing_route.ProcessingInputSnapshot,
                    )
                ):
                    try:
                        rerun_ctx, _ = self._gather_context("reply", 1, False)
                    except Exception:
                        rerun_ctx = context
                return self._cloud_generate(
                    raw, name, rerun_ctx, ai_mode, raw,
                    prefs=prefs, context_strict=context_strict,
                    cfg=cfg,
                    prompt_cfg=prompt_cfg,
                    invocation_snapshot=invocation_snapshot,
                    expected_feature=ai_mode,
                    expected_lane=ai_mode,
                    target_lease=target_lease,
                    operation_id=operation_id,
                )
            except Exception as e:
                print("auto mode re-run failed:", e)
                return "text", ai._clean(clean)
        # Low confidence / genuinely uncertain → DON'T auto-spend AND don't tell the
        # user to "speak again" (owner v6: that was conceptually wrong). Instead ASK
        # which mode via the interactive picker, keeping the original words. The
        # picker owns the outcome (pick → re-process those words; dismiss → paste the
        # polished text), so we DEFER the paste here with a sentinel that _process
        # recognises and skips. The transcript is preserved in self._last_raw / raw.
        self._offer_mode_pick(
            raw, ai_mode, ai._clean(clean), target_lease=target_lease,
            operation_id=operation_id)
        return "__pick__", ai._clean(clean)

    def _builder(
        self, raw, det_mode="text", det_request="", fmt=True,
        invocation_snapshot=None,
    ):
        """Offline floor — routes through local_engine to decide whether the
        local LLM, cloud AI, or rules-only formatting handles each lane.

        Pipeline per lane:
          text          → rules-only formatting via format_transcript
          foreign       → foreign_boost phonetic repair → format_transcript
          smart (prompt/email/list/reply)
            local LLM ready → LLM generation (when wired)
            else            → rules-only via formatting.process

        Cloud path (key present, not local_only) is handled by _generate()
        before we reach here — _builder is the offline fallback."""
        frozen = isinstance(
            invocation_snapshot, processing_route.ProcessingInputSnapshot
        )
        name = (
            invocation_snapshot.user_name
            if frozen else self.settings.get("user_name", "")
        )
        try:
            if det_mode == "foreign":
                # Foreign Mode: run phonetic boost for uncertainty marking
                # and high-confidence term replacement on-device.
                langs = (
                    list(invocation_snapshot.foreign_languages) or ["arabic"]
                    if frozen
                    else self.settings.get("foreign_languages") or ["arabic"]
                )
                boosted = foreign_boost.boost(raw, languages=langs)
                if boosted:
                    raw = foreign_boost.strip_flags(boosted)
                base = (formatting.format_transcript(raw, commands=False)
                        if fmt else raw.strip())
                return "foreign", islamic_terms.correct_islamic_terms(base)

            # Route through local_engine for non-foreign lanes
            cloud_key = (
                invocation_snapshot.route.key_present
                if frozen else bool(self._ai_key())
            )
            local_only = (
                invocation_snapshot.route.device_only
                if frozen else self.settings.get("local_only_mode", False)
            )
            llm_ready = (
                invocation_snapshot.local_model_ready
                if frozen else local_engine.local_llm_ready()
            )
            decision = local_engine.route(
                det_mode,
                cloud_key_present=cloud_key,
                local_only_mode=local_only,
                local_llm_ready=llm_ready,
            )

            if decision.engine == local_engine.LOCAL_LLM and llm_ready:
                # Local LLM path: generate via LlamaCppBackend with GBNF grammar
                backend = local_engine.get_backend()
                system, user, grammar = local_engine.build_local_request(
                    det_mode, (det_request or raw).strip())
                try:
                    out = backend.generate(system, user, grammar=grammar)
                    if out and out.strip():
                        return det_mode, out
                except Exception as e:
                    print("local LLM generation failed, falling to rules:", e)

            # Rules-only formatting (LOCAL engine or LLM fallback)
            rpunct = (
                invocation_snapshot.rpunct_enabled
                if frozen else self.settings.get("rpunct_enabled", True)
            )
            mode, out = formatting.process(
                raw,
                invocation_snapshot.modes_dict()
                if frozen else self.settings.get("modes", {}),
                name, fmt, commands=False,
                auto=False, rpunct_enabled=rpunct,
            )
            if out and out.strip():
                return mode, out
        except Exception as e:
            print("offline builder error:", e)
        return "text", (formatting.format_transcript(raw, commands=False)
                        if fmt else raw.strip())

    def _init_local_llm(self):
        """Wire the on-device LLM backend at boot. Cheap by design: it only discovers
        a model file and constructs a backend object — the model itself is not loaded
        until the first generate() call, so boot stays instant. With no GGUF present
        (the common case) the backend stays Null and smart-mode shaping is unchanged.

        Discovery order: an explicit local_llm_model path, else the first *.gguf in
        DATA_DIR/models/. Backend: llama-cpp-python if installed, else the bundled
        llama-cli binary, else Null. Honours the local_llm_enabled toggle."""
        try:
            if not bool(self.settings.get("local_llm_enabled", True)):
                local_engine.set_backend(None, "disabled in settings")
                return
            model = local_engine.discover_model(
                [branding.MODELS_DIR],
                explicit_path=(self.settings.get("local_llm_model", "") or ""))
            backend, reason = local_engine.build_backend(
                model_path=model, cli_bin=branding.llama_cli_path())
            local_engine.set_backend(backend, reason)
            if local_engine.local_llm_ready():
                print(f"[local-llm] ready: {reason} ({model})")
            else:
                print(f"[local-llm] off: {reason}")
        except Exception as e:
            print("local-llm init error:", e)

    def local_llm_status(self):
        """Report the on-device LLM state for Settings / diagnostics."""
        try:
            st = local_engine.backend_status()
            st["models_dir"] = branding.MODELS_DIR
            st["has_binary"] = bool(branding.llama_cli_path())
            return st
        except Exception as e:
            return {"name": "null", "ready": False, "model": None,
                    "reason": str(e), "models_dir": getattr(branding, "MODELS_DIR", ""),
                    "has_binary": False}

    def _local_llm_generate(
        self, raw, det_mode, context, invocation_snapshot=None
    ):
        """On-device smart-mode shaping via the local LLM, when one is resident. The
        MIDDLE tier between the cloud lane and the deterministic builder: it lifts
        prompt/email/reply from templated to fluent output WITHOUT the cloud. Returns
        cleaned text, or None to fall through to the builder (it never hard-blocks)."""
        if det_mode not in local_engine.SMART_LANES:
            return None
        frozen = isinstance(
            invocation_snapshot, processing_route.ProcessingInputSnapshot
        )
        if frozen and not invocation_snapshot.local_model_ready:
            return None
        if not frozen and not local_engine.local_llm_ready():
            return None
        try:
            system, user, grammar = local_engine.build_local_request(
                det_mode, raw,
                prefs=(
                    invocation_snapshot.prompt_prefs_dict()
                    if frozen else self.settings.get("prompt_prefs")
                ),
                context=context or "")
            out = local_engine.get_backend().generate(
                system, user, grammar=grammar, max_tokens=768)
            return (out or "").strip() or None
        except Exception as e:
            print("local-llm generate failed:", e)
            return None

    def _generate(
        self,
        raw,
        det_mode="text",
        det_request="",
        clip_count=0,
        mode_active=False,
        words=None,
        windows=None,
        keyword_template="",
        window_words=None,
        config_snap=None,
        route_decision=None,
        invocation_snapshot=None,
        target_lease=None,
        operation_id=None,
    ):
        """AI-driven generation. Lane A (plain text) → minimal polish; Lane B (a
        mode the button armed) → focused/constitution path. (Material-as-context
        AI jobs moved to the Deck — see _run_deck_job; "context" is no longer a
        spoken trigger.) Returns (mode, text, used_offline)."""
        if isinstance(invocation_snapshot, processing_route.ProcessingInputSnapshot):
            name = invocation_snapshot.user_name
            prefs = invocation_snapshot.prompt_prefs_dict()
            route_decision = invocation_snapshot.route
        else:
            name = self.settings.get("user_name", "")
            prefs = self.settings.get("prompt_prefs")
        if route_decision is None:
            feature = det_mode if det_mode in ("prompt", "email", "reply") else "dictation"
            route_decision = processing_route.snapshot(
                self.settings, feature=feature, lane=det_mode
            )
        # Capture the AI config ONCE at the start of this dictation so that
        # an in-flight cloud request always uses the key it was launched with,
        # even if the user changes/deletes the key mid-call (VAL-CROSS-011).
        # _cloud_generate() receives this snapshot and never re-reads settings.
        if isinstance(route_decision, processing_route.RouteDecision):
            provider_info = ai.PROVIDERS.get(route_decision.provider) or {}
            cfg = {
                "key": route_decision.api_key,
                "provider": route_decision.provider,
                "model": route_decision.model,
                "url": provider_info.get("url", ""),
                "supported": route_decision.provider_supported,
            }
        else:
            cfg = self._ai_cfg()
        prompt_cfg = cfg
        key = cfg["key"]
        # Use snapshot config if provided (for settings-change isolation).
        if isinstance(invocation_snapshot, processing_route.ProcessingInputSnapshot):
            pro_mode = invocation_snapshot.route.pro_mode
            snap_fmt = invocation_snapshot.format_enabled
            context = invocation_snapshot.context
            context_strict = invocation_snapshot.context_strict
        else:
            pro_mode = config_snap["pro_mode"] if config_snap else self.settings.get("pro_mode", True)
            snap_fmt = config_snap["format_enabled"] if config_snap else self.settings.get("format_enabled", True)
            context, context_strict = self._gather_context(
                det_mode, clip_count, mode_active
            )
        # Conversation context: when Prompt Mode is active and a conversation
        # has been captured via "Capture chat", include it in the AI prompt so
        # the model understands what the user was discussing. Zero-friction —
        # no settings checkbox; context is included automatically when available.
        # Gated by Prompt Mode only (not injected for Email, Foreign, or Text).
        if det_mode == "prompt" and not isinstance(
            invocation_snapshot, processing_route.ProcessingInputSnapshot
        ):
            try:
                conv = self._conv_context_for_prompt()
                if conv:
                    context = conv + ("\n\n" + context if context else "")
            except Exception:
                pass
        if not isinstance(
            invocation_snapshot, processing_route.ProcessingInputSnapshot
        ):
            snapshot_source = config_snap or self.settings
            invocation_snapshot = processing_route.snapshot_inputs(
                snapshot_source,
                feature=(det_mode if det_mode in ("prompt", "email", "reply") else "dictation"),
                lane=det_mode,
                context=context,
                context_policy="reprocessing" if config_snap is None else "dictation",
                context_strict=context_strict,
                local_model_ready=local_engine.local_llm_ready(),
                route_decision=route_decision,
            )
            name = invocation_snapshot.user_name
            prefs = invocation_snapshot.prompt_prefs_dict()
            pro_mode = invocation_snapshot.route.pro_mode
            snap_fmt = invocation_snapshot.format_enabled
            context = invocation_snapshot.context
            context_strict = invocation_snapshot.context_strict
        cloud_allowed = bool(pro_mode and key)
        if isinstance(route_decision, processing_route.RouteDecision):
            cloud_allowed = bool(route_decision.ready and (
                route_decision.cloud_augmented or route_decision.provider == "local"
            ))
        elif config_snap and config_snap.get("local_only_mode", False):
            cloud_allowed = False
        if cloud_allowed:
            try:
                mode, out = processing_route.call_provider(
                    route_decision,
                    self._cloud_generate,
                    raw, name, context, det_mode, det_request,
                    mode_active=mode_active, prefs=prefs,
                    context_strict=context_strict,
                    keyword_template=keyword_template, words=words,
                    window_words=window_words, cfg=cfg, prompt_cfg=prompt_cfg,
                    invocation_snapshot=invocation_snapshot,
                    expected_feature=(
                        det_mode if det_mode in {"prompt", "email", "reply"}
                        else "dictation"
                    ),
                    expected_lane=det_mode,
                    target_lease=target_lease,
                    operation_id=operation_id,
                )
                if out and out.strip():
                    self._mark_llm_ok()
                    return mode, out, False
            except Exception as e:
                print("AI failed, using builder:", e)
                self._pro_fallback_notice(e)
        # On-device LLM lane: when shaping stays local (no key, local-only, or the
        # cloud call just failed) and a small GGUF model is resident, lift the smart
        # lanes from templated to fluent output BEFORE the deterministic builder.
        # Returns None (→ builder) when no model is present, so default behaviour is
        # unchanged. used_offline stays True: this is the no-cloud path.
        llm_out = self._local_llm_generate(
            raw, det_mode, context, invocation_snapshot
        )
        if llm_out and llm_out.strip():
            return det_mode, llm_out, True

        # Pipeline orchestrator: the four-stage merged-model pipeline
        # (punctuation → grammar → formatting → cleanup).  Stages 2 & 3 are
        # optional and skip gracefully when their GGUF models are absent;
        # Stages 1 & 4 are the always-available floor.  When the full pipeline
        # runs it replaces the deterministic builder below — the pipeline's
        # Stage 4 (stage_cleanup.py → model_free.process) already does
        # everything _builder() did.
        try:
            from pipeline import get_orchestrator
            orch = get_orchestrator()
            pipeline_config = config_snap
            if isinstance(
                invocation_snapshot, processing_route.ProcessingInputSnapshot
            ):
                pipeline_config = {
                    "pro_mode": invocation_snapshot.route.pro_mode,
                    "local_only_mode": invocation_snapshot.route.device_only,
                    "format_enabled": invocation_snapshot.format_enabled,
                    "instant_text": invocation_snapshot.instant_text,
                    "primary_language": invocation_snapshot.primary_language,
                    "english_only": invocation_snapshot.english_only,
                    "foreign_mode": invocation_snapshot.foreign_mode,
                    "foreign_languages": list(
                        invocation_snapshot.foreign_languages
                    ),
                    "vocabulary": invocation_snapshot.vocabulary_dict(),
                    "vocabulary_terms": list(
                        invocation_snapshot.vocabulary_terms
                    ),
                    "prompt_prefs": invocation_snapshot.prompt_prefs_dict(),
                }
            result = orch.process(raw, lane=det_mode, config=pipeline_config)
            if result and result.strip():
                return det_mode, result, True
        except ImportError:
            pass  # pipeline package not installed — fall back to builder
        except Exception as e:
            print("pipeline processing failed, using builder:", e)

        mode, out = self._builder(
            raw, det_mode, det_request, snap_fmt, invocation_snapshot,
        )
        return mode, out, True

    # ---- Pro Mode ----
    def pro_status(self):
        cfg = self._ai_cfg()
        return {
            "enabled": self.settings.get("pro_mode", True),
            "engine": cfg.get("provider", "cerebras"),
            "has_key": bool(cfg.get("key")),
            "using_own_key": bool(self.settings.get(cfg.get("key_setting", "cerebras_api_key"), "").strip()),
            "key_failed": self.pro_key_failed,
        }

    def _check_pro_key(self):
        """On launch, quietly verify the active LLM engine key."""
        if (not self.settings.get("pro_mode", True)
                or self.settings.get("local_only_mode", False)):
            return
        cfg = self._ai_cfg()
        if not cfg["key"]:
            return
        try:
            ok = ai.key_ok(cfg["key"], models_url=cfg["models_url"])
        except Exception:
            return
        if ok is False:
            self.pro_key_failed = True
            if not self._pro_notified:
                self._pro_notified = True
                self._notify(
                    "Mumble",
                    "Your Cerebras key was rejected — using the offline builder. "
                    "Check your key in Settings  Pro Mode.",
                )
        elif ok is True:
            self.pro_key_failed = False

    def set_pro_mode(self, v):
        self.settings.set("pro_mode", bool(v))

    def set_prompt_mode(self, v):
        """THE BIG SHIFT: set the sticky Prompt toggle (the web Settings switch).
        Now a thin wrapper over the general active-mode selector so Prompt and the
        island mode deck share one source of truth."""
        return bool(self.set_active_mode("prompt" if v else None) == "prompt")

    def toggle_prompt_mode(self):
        """Flip the Prompt toggle — kept for callers that toggle Prompt directly."""
        return self.set_prompt_mode(self.active_mode != "prompt")

    # ---- ITEM 4: the island mode deck (Prompt / Email) -------------------------
    def island_modes(self):
        """The processing modes the island deck offers, as (key, label) pairs — the
        user-facing lanes only (prompt/email). Reply remains a deliberate Deck
        preset/backend lane, not a floating Smart Mode. A user setting can pare the
        list down; an unknown key is dropped so we never show a mode that can't run."""
        labels = {"prompt": "Prompt", "email": "Email"}
        want = self.settings.get("island_modes") or ["prompt", "email"]
        out = [(k, labels[k]) for k in want if k in labels]
        return out or [("prompt", "Prompt")]

    def _push_island_bar_state(self):
        """Push the live mode-deck snapshot (enabled modes, the active mode, whether
        the Foreign toggle shows + its state) to the island bar."""
        if not self.island:
            return
        try:
            languages = [
                str(value).strip().replace("_", " ")
                for value in (self.settings.get("foreign_languages") or [])
                if str(value).strip()
            ]
            if len(languages) == 1:
                language_label = languages[0].title()
            elif len(languages) > 1:
                language_label = f"{len(languages)} languages"
            else:
                language_label = "Language"
            self._tk_schedule(
                self.island.set_bar_state,
                self.island_modes(),
                self.active_mode,
                bool(self.settings.get("foreign_mode", False)),
                bool(self.settings.get("island_foreign_toggle", False)),
                language_label,
            )
        except Exception as e:
            print("island bar-state push skipped:", e)

    def set_active_mode(self, key):
        """Select (or clear) the island-forced processing mode. `key` is a lane key
        (prompt/email/reply) or None to return to plain dictation. Tapping the
        already-active mode clears it (a natural toggle). Persists the choice, keeps
        prompt_mode_enabled in sync (so the web switch agrees), lights the island in
        the mode's colour, and refreshes the web Settings."""
        valid = {k for k, _ in self.island_modes()}
        if key is not None and key not in valid:
            key = None
        # tapping the active chip again turns the mode off
        if key is not None and key == self.active_mode:
            key = None
        self.active_mode = key
        self.prompt_mode_enabled = (key == "prompt")
        try:
            self.settings.set("island_active_mode", key or "")
            self.settings.set("prompt_mode_enabled", self.prompt_mode_enabled)
        except Exception:
            pass
        self._push_island_bar_state()
        self._send_webui_async({"cmd": "refresh", "what": "settings"})
        return key

    def toggle_island_foreign(self):
        """Flip Foreign mode — the island's independent Foreign toggle. Separate from
        the active mode (a dictation can be a Prompt AND in another language), so it
        never changes which lane is selected. Persists + re-lights the bar chip."""
        on = not bool(self.settings.get("foreign_mode", False))
        try:
            self.settings.set("foreign_mode", on)
        except Exception:
            pass
        self._push_island_bar_state()
        # The chip itself is the feedback. Replacing an active Listening state
        # with a toast used to hide the waveform while audio was still recording.
        return on

    def set_cerebras_key(self, k):
        self.settings.set("cerebras_api_key", (k or "").strip())
        self.pro_key_failed = False
        self._pro_notified = False

    def test_cerebras(self):
        return ai.cerebras_test(self._ai_key())

    def set_language(self, lang):
        self.settings.set("language", (lang or "en").strip() or "en")

    def set_format_enabled(self, value):
        self.settings.set("format_enabled", bool(value))

    def set_polish_aggressiveness(self, value):
        self.settings.set("polish_aggressiveness", value or "Light")

    def set_mode_enabled(self, mode, value):
        atomic = getattr(self.settings, "atomic_mapping_update", None)
        if callable(atomic):
            atomic("modes", lambda modes: modes.__setitem__(mode, bool(value)))
        else:
            modes = dict(self.settings.get("modes", {}))
            modes[mode] = bool(value)
            self.settings.set("modes", modes)

    # ---- mode button / preferences (Settings UI) ----
    def apply_mode_key(self, key):
        """Validate + save the held mode key, then re-hook it live. Returns (ok, msg)."""
        key = bindings.normalize(key)
        ok, msg = bindings.validate(key, hold=True)
        if not ok:
            return False, msg
        self.settings.set("mode_key", key)
        self.mode_key = key
        try:
            self._register_mode_key()
        except Exception as e:
            return False, f"Couldn't register: {e}"
        return True, f"Saved — hold {bindings.pretty(key)} to arm mode detection."

    def set_mode_button_enabled(self, value):
        self.settings.set("mode_button_enabled", bool(value))
        try:
            self._register_mode_key()
        except Exception as e:
            print("mode-key re-register error:", e)

    def set_prompt_pref(self, key, value):
        atomic = getattr(self.settings, "atomic_mapping_update", None)
        if callable(atomic):
            atomic("prompt_prefs", lambda prefs: prefs.__setitem__(key, value))
        else:
            prefs = dict(self.settings.get("prompt_prefs", {}))
            prefs[key] = value
            self.settings.set("prompt_prefs", prefs)

    def set_user_name(self, name):
        self.settings.set("user_name", (name or "").strip())

    def set_vocabulary(self, vocab, terms=None, baseline_vocab=None,
                       baseline_terms=None):
        """Persist the personal vocabulary: `terms` is the default workflow
        (just the correct words); `vocab` is the advanced wrong=right mapping.

        When a Settings editor supplies its original baseline, apply only that
        editor's changes to the latest locked snapshot. This prevents a stale
        classic window from replacing a correction learned in the meantime.
        """
        clean = {
            str(k).strip(): str(v).strip()
            for k, v in (vocab or {}).items()
            if str(k).strip() and str(v).strip()
        }
        clean_terms = None if terms is None else [
            str(t).strip() for t in terms if str(t).strip()
        ]
        old_vocab = None if baseline_vocab is None else {
            str(k).strip(): str(v).strip()
            for k, v in (baseline_vocab or {}).items()
            if str(k).strip() and str(v).strip()
        }
        old_terms = None if baseline_terms is None else [
            str(t).strip() for t in baseline_terms if str(t).strip()
        ]

        def merge(latest_vocab, latest_terms):
            if old_vocab is None:
                latest_vocab.clear()
                latest_vocab.update(clean)
            else:
                for key in dict.fromkeys([*old_vocab.keys(), *clean.keys()]):
                    old_has = key in old_vocab
                    current_has = key in clean
                    user_changed = (
                        (not current_has or clean[key] != old_vocab[key])
                        if old_has else current_has
                    )
                    if not user_changed:
                        continue
                    if current_has:
                        latest_vocab[key] = clean[key]
                    else:
                        latest_vocab.pop(key, None)

            if clean_terms is None:
                return
            if old_terms is None:
                latest_terms[:] = clean_terms
                return
            old_set = set(old_terms)
            clean_set = set(clean_terms)
            latest_terms[:] = [
                term for term in latest_terms
                if not (term in old_set and term not in clean_set)
            ]
            latest_set = set(latest_terms)
            for term in clean_terms:
                if term not in old_set and term not in latest_set:
                    latest_terms.append(term)
                    latest_set.add(term)

        atomic_update = getattr(self.settings, "atomic_vocabulary_update", None)
        if callable(atomic_update):
            atomic_update(merge)
        else:
            # Compatibility for lightweight Settings implementations: still use
            # one bulk write rather than two independently persisted collections.
            latest_vocab = dict(self.settings.get("vocabulary", {}) or {})
            latest_terms = list(self.settings.get("vocabulary_terms", []) or [])
            merge(latest_vocab, latest_terms)
            self.settings.update(
                vocabulary=latest_vocab, vocabulary_terms=latest_terms)

    def set_correction_learning_enabled(self, value):
        """Classic-UI bridge for the opt-in experimental learner."""
        enabled = bool(value)
        self.settings.set("correction_learning_enabled", enabled)
        if enabled:
            self._ensure_correction_learning()
            self._prewarm_correction_monitor()
        else:
            self._cancel_correction_monitor()
            with self._correction_capture_lock:
                self._last_correction_capture = None
            if self.island:
                self._tk_schedule(self.island.clear_correction)
                self._tk_schedule(self.island.close_correction_editor)
        self._send_webui_async({"cmd": "refresh", "what": "settings"})
        return enabled

    def set_correction_learning_auto_detect(self, value):
        """Classic-UI bridge for the separate, bounded target-field monitor."""
        enabled = bool(value)
        self.settings.set("correction_learning_auto_detect", enabled)
        if enabled and self.settings.get("correction_learning_enabled", False):
            self._prewarm_correction_monitor()
        else:
            # Turning this choice off takes effect immediately, even if a field
            # is currently inside the short post-paste observation window.
            self._cancel_correction_monitor()
        self._send_webui_async({"cmd": "refresh", "what": "settings"})
        return enabled

    def autostart_enabled(self):
        try:
            return autostart.is_enabled()
        except Exception:
            return False

    def set_autostart(self, value):
        try:
            ok = autostart.set_enabled(bool(value))
        except Exception as e:
            print("autostart error:", e)
            return False
        if not ok:
            print("autostart change failed; keeping the previous setting")
            return False
        self.settings.set("autostart", bool(value))
        return True

    def test_microphone(self, on_level, on_done):
        def run():
            import secrets

            wake_pause_reason = "microphone-test:" + secrets.token_urlsafe(8)
            if not self._pause_wake_word(wake_pause_reason):
                self._resume_wake_word(wake_pause_reason)
                self._notify(
                    "Microphone test failed",
                    "Voice activation could not release the microphone safely.",
                )
                try:
                    on_done()
                except Exception:
                    pass
                return
            try:

                def cb(indata, frames, t, status):
                    on_level(min(1.0, float(np.sqrt(np.mean(indata**2))) * LEVEL_GAIN))

                with sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype="float32",
                    device=self.settings.get("mic_device", None),
                    callback=cb,
                ):
                    time.sleep(5.0)  # longer sample so the level meter is easy to read
            except Exception as e:
                print("mic test error:", e)
                self._notify("Microphone test failed", e)
            finally:
                self._resume_wake_word(wake_pause_reason)
                try:
                    on_done()
                except Exception:
                    pass

        threading.Thread(target=run, daemon=True).start()

    def copy_text(self, text):
        try:
            if self._set_clipboard(text):
                self._notify("Copied", "On the clipboard.")
                return True
            self._notify("Copy failed", "The clipboard is busy. Try again.")
        except Exception as e:
            print("copy error:", e)
        return False

    def copy_image(self, path):
        """Put a clipboard-history image back onto the Windows clipboard (as CF_DIB), so the
        user can paste it anywhere with Ctrl+V — not just open the saved PNG.
        Returns True only if the image actually landed on the clipboard, so the
        caller never blind-fires Ctrl+V (which would paste stale content)."""
        if not path or not os.path.exists(path):
            self._notify("Image unavailable",
                         "That image file is missing — it may have been cleared.")
            return False
        try:
            import ctypes
            import io
            from ctypes import wintypes

            from PIL import Image

            # CF_DIB wants a BITMAPINFOHEADER + pixels — i.e. a BMP with its 14-byte file
            # header stripped off. Flatten to RGB (DIBs don't carry alpha reliably).
            img = Image.open(path).convert("RGB")
            buf = io.BytesIO()
            img.save(buf, "BMP")
            data = buf.getvalue()[14:]
            buf.close()

            CF_DIB, GMEM_MOVEABLE = 8, 0x0002
            u, k = ctypes.windll.user32, ctypes.windll.kernel32
            # 64-bit safety: handles are pointers, so the default c_int return would truncate.
            k.GlobalAlloc.restype = ctypes.c_void_p
            k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
            k.GlobalLock.restype = ctypes.c_void_p
            k.GlobalLock.argtypes = [ctypes.c_void_p]
            k.GlobalUnlock.argtypes = [ctypes.c_void_p]
            k.GlobalFree.argtypes = [ctypes.c_void_p]
            u.SetClipboardData.restype = ctypes.c_void_p
            u.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
            u.OpenClipboard.argtypes = [wintypes.HWND]

            if self.clipboard:
                self.clipboard.pause()
            try:
                if not u.OpenClipboard(0):
                    return False
                u.EmptyClipboard()
                h = k.GlobalAlloc(GMEM_MOVEABLE, len(data))
                if not h:
                    u.CloseClipboard()
                    return False
                ptr = k.GlobalLock(h)
                ctypes.memmove(ptr, data, len(data))
                k.GlobalUnlock(h)
                ok = u.SetClipboardData(CF_DIB, h)  # on success the clipboard owns `h`
                u.CloseClipboard()
                if not ok:
                    k.GlobalFree(h)
                    return False
            finally:
                if self.clipboard:
                    self.clipboard.resume()
            self._notify("Copied", "Image on the clipboard — paste it with Ctrl+V.")
            return True
        except Exception as e:
            print("copy image error:", e)
            self._notify("Couldn't copy image",
                         "The image couldn't be read — it may be corrupted.")
            return False

    def stats(self):
        # Reads the independent stat store — NOT transcripts/clipboard — so clearing
        # those never affects the numbers.
        try:
            s = self.stat_store.summary()
            cur_streak, best_streak = self.stat_store.streak()
            s["current_streak"] = cur_streak
            s["best_streak"] = best_streak
            return s
        except Exception as e:
            print("stats error:", e)
            return {
                "total_words": 0,
                "total_transcripts": 0,
                "avg_wpm": 0,
                "best_wpm": 0,
                "today_words": 0,
                "spoken_minutes": 0.0,
                "typing_minutes_saved": 0.0,
                "current_streak": 0,
                "best_streak": 0,
            }

    def daily_stats(self, days=7):
        try:
            return self.stat_store.daily_stats(days)
        except Exception:
            return []

    def mode_stats(self):
        try:
            return self.stat_store.mode_stats()
        except Exception:
            return []

    def streak(self):
        try:
            return self.stat_store.streak()
        except Exception:
            return (0, 0)

    def clipboard_recent(self, n=100):
        return self.clipboard.recent(n)

    def clear_clipboard(self):
        self.clipboard.clear()

    def delete_clipboard(self, i):
        self.clipboard.delete_index(i)

    def delete_transcript(self, i):
        self.history.delete_index(i)
        self.refresh_tray_menu()

    def open_transcripts(self):
        try:
            if not os.path.exists(branding.HISTORY_TXT):
                open(branding.HISTORY_TXT, "a", encoding="utf-8").close()
            os.startfile(branding.HISTORY_TXT)
        except Exception as e:
            print("open transcripts error:", e)

    def prompts_history(self):
        """Every prompt ever generated, newest first (History → Prompts +
        the Context Island PROMPTS section)."""
        try:
            return self.prompt_history.all_prompts()
        except Exception as e:
            print("prompts history error:", e)
            return []

    def clear_prompts(self):
        try:
            self.prompt_history.clear_history()
        except Exception as e:
            print("clear prompts error:", e)

    def switch_to_lite(self):
        """Launch the frozen Mumble Lite snapshot and quit this instance (the
        single-instance lock means only one Mumble runs at a time)."""
        lite = os.path.join(
            os.path.dirname(branding.INSTALL_DIR), "MumbleLite", "Open Mumble Lite.bat"
        )
        if not os.path.exists(lite):
            self._notify("Mumble Lite not found",
                         "The MumbleLite folder isn't next to the main app.")
            return
        try:
            os.startfile(lite)
        except Exception as e:
            self._notify("Couldn't start Mumble Lite", e)
            return
        self._quit()

    def open_data_folder(self):
        try:
            os.startfile(branding.DATA_DIR)
        except Exception as e:
            print("open folder error:", e)

    # ==================================================================== tray
    @staticmethod
    def _preview(text, n=42):
        one = " ".join(text.split())
        return (one[:n] + "…") if len(one) > n else one

    def _copy_cb(self, text):
        def cb(icon, item):
            self.copy_text(text)

        return cb

    def _recent_items(self):
        rec = self.history.recent(8)
        if not rec:
            return [pystray.MenuItem("No transcripts yet", None, enabled=False)]
        return [
            pystray.MenuItem(
                f"{e.get('time', '')}  {self._preview(e.get('text', ''))}",
                self._copy_cb(e.get("text", "")),
            )
            for e in rec
        ]

    def _build_menu(self):
        return pystray.Menu(
            pystray.MenuItem(
                "Open Mumble", lambda i, it: self.cmd_q.put("open"), default=True
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Recent transcripts", pystray.Menu(*self._recent_items())),
            pystray.MenuItem("Re-paste last", lambda i, it: self.on_quick_paste()),
            pystray.MenuItem("Open Deck", lambda i, it: self.on_open_history()),
            pystray.MenuItem(
                "Resume listening" if self.paused else "Pause listening",
                lambda i, it: self._toggle_pause(),
            ),
            pystray.MenuItem("Check for updates", lambda i, it: self._on_check_updates()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Mumble", lambda i, it: self.cmd_q.put("quit")),
        )

    def refresh_tray_menu(self):
        if self.icon is not None:
            try:
                self.icon.menu = self._build_menu()
                self.icon.update_menu()
            except Exception:
                pass

    def _toggle_pause(self):
        self.paused = not self.paused
        if self.paused:
            self._pause_wake_word("app-paused")
        else:
            self._resume_wake_word("app-paused")
        if self.paused and self.recording:
            # Stop any active recording so audio doesn't accumulate in memory
            # while paused (the hotkey is blocked by self.paused, so the user
            # can't stop it otherwise).
            with self.lock:
                self.busy = True
            threading.Thread(target=self._safe_stop, daemon=True).start()
        self.refresh_tray_menu()
        self._notify("Mumble", "Paused." if self.paused else "Listening again.")

    # ---- update handlers ----
    def _on_update_available(self, manifest):
        """Called from the background auto-check thread when an update is found.
        Schedules the notification on the Tk thread so it's safe."""
        ver = manifest.get("version", "?")
        notes = manifest.get("notes", "")
        msg = f"Mumble v{ver} is available."
        if notes:
            msg += f" {notes}"
        self._tk_schedule(self._notify, "Update available", msg)
        self._update_manifest = manifest

    def _on_check_updates(self):
        """Tray menu "Check for updates" handler. Runs the check inline and
        either notifies of an update or says "you're up to date"."""
        if not update.update_channel_enabled():
            self._notify(
                "Mumble Updates",
                "The signed update channel is not configured in this build.",
            )
            return
        ok, manifest = update.check_for_update()
        if ok and manifest:
            self._update_manifest = manifest
            ver = manifest.get("version", "?")
            notes = manifest.get("notes", "")
            msg = f"Mumble v{ver} is available."
            if notes:
                msg += f" {notes}"
            self._notify("Update available", msg)
            # Ask to install
            def ask():
                import tkinter.messagebox as mb
                if mb.askyesno("Mumble Update", f"Download and install v{ver}?"):
                    self._on_update_install()
            self._tk_schedule(ask)
        elif manifest is None:
            # Distinct from "up to date": the CHECK failed (no URL / offline /
            # bad manifest) — saying "up to date" here would be a lie.
            self._notify("Mumble", "Couldn't check for updates right now.")
        else:
            self._notify("Mumble", "You're up to date!")

    def _on_update_install(self):
        """Start the download+install sequence for the pending update."""
        manifest = getattr(self, "_update_manifest", None)
        if not manifest:
            self._notify("Mumble", "No update pending.")
            return
        self._notify("Mumble", "Downloading update…")
        update.download_and_install(
            manifest,
            branding.INSTALL_DIR,
            callback=self._update_progress,
        )

    def _update_progress(self, status, message):
        """Callback from update.download_and_install — route to Tk thread."""
        self._tk_schedule(self._notify, "Mumble Update", message)
        if status == "ready":
            # Update is extracted and swap script is written — prompt restart
            import tkinter.messagebox as mb
            self._tk_schedule(
                mb.showinfo,
                "Mumble Update",
                f"{message}\n\nClick OK to restart and apply.",
            )
            self._tk_schedule(self.cmd_q.put, "restart")

    # ==================================================================== loop
    def _pump(self):
        """Main-thread event pump. Drains the Tkinter-safe dispatch queue and the
        command queue, then reschedules itself. A top-level guard ensures this loop
        can never die silently — even an unexpected exception just reschedules."""
        drained = 0
        try:
            # Drain the thread-safe Tkinter dispatch queue — all island/window
            # calls from worker/audio/streaming threads are routed here so they
            # always execute on the main (GUI) thread.
            try:
                while True:
                    func, args, kwargs = self._tk_queue.get_nowait()
                    drained += 1
                    try:
                        func(*args, **kwargs)
                    except Exception as e:
                        # widget may have been destroyed — log instead of swallowing
                        print(f"[tk_queue] widget error: {e}")
            except queue.Empty:
                pass
            # A state setter just ran on the island — repaint it NOW rather than
            # waiting up to a full tick (owner v6: "user action should feel
            # instant"). wake() is one cheap frame; it doesn't disturb the tick.
            if drained and self.island is not None:
                try:
                    self.island.wake()
                except Exception:
                    pass
            try:
                while True:
                    cmd = self.cmd_q.get_nowait()
                    if cmd == "open":
                        self._open_window()
                    elif cmd == "quit":
                        self._quit()
                        return
                    elif cmd == "restart":
                        self._restart()
                        return
            except queue.Empty:
                pass
        except Exception as e:
            print(f"_pump unexpected error (self-healing): {e}")
        if self.root is not None:
            # Self-tuning cadence: when we just dispatched work, fast-track the next
            # pump (8ms) so bursts on the hotkey/stop path are serviced within a
            # frame; relax to a cheap idle poll (40ms, was 80) otherwise. The pump
            # is a near-no-op when the queue is empty, so this stays light.
            self.root.after(8 if drained else 40, self._pump)

    def _open_window(self):
        """Open the main window. The new HTML/CSS/JS UI (webui_shell + pywebview)
        is the primary main window; the classic tkinter AppWindow is the
        in-process fallback when pywebview / the Edge WebView2 runtime is
        unavailable, and lives on, frozen, as Mumble Lite (MumbleLite/)."""
        try:
            if self._open_web_ui():
                return
            # Fallback: the classic tkinter window, in-process.
            if self.window is None:
                from app_window import AppWindow

                self.window = AppWindow(self.root, self)
            self.settings.set("first_run", False)
            self.window.show()
        except Exception as e:
            print("window error:", e)
            self._notify("Couldn't open Mumble", e)

    def _open_web_ui(self, start="app"):
        """Launch the pywebview main-window process. Returns True if launched/already
        open, False if pywebview is unavailable so the caller falls back to classic.
        (owner v8: the History-flyout 'popup' start mode is gone — there is one
        window now; Ctrl+Alt+D navigates IT to History.)

        Note: first_run is intentionally NOT cleared here — the web UI's own
        onboarding wizard clears it via Api.finish_onboarding."""
        try:
            import importlib.util
            if importlib.util.find_spec("webview") is None:
                return False
            start = "search" if str(start).lower() == "search" else "app"
            proc = getattr(self, "_webui_proc", None)
            if proc is not None and proc.poll() is None:
                # Already running — don't spawn a second process; just reveal it.
                self._send_webui({
                    "cmd": "system_search" if start == "search" else "show"})
                return True
            import subprocess

            shell = os.path.join(branding.INSTALL_DIR, "webui_shell.py")
            exe = sys.executable or "python"
            exe_dir = os.path.dirname(exe)
            # Prefer the branded Mumble.exe (a renamed pythonw the installer
            # drops in the venv) so Task Manager / taskbar say "Mumble", not
            # "Python" — then pythonw.exe (no console flash), then whatever
            # we're running on.
            for cand in (os.path.join(exe_dir, "Mumble.exe"),
                         os.path.join(exe_dir, "pythonw.exe")):
                if os.path.exists(cand):
                    exe = cand
                    break
            env = dict(os.environ)
            env["MUMBLE_START"] = start
            self._webui_proc = subprocess.Popen([exe, shell],
                                                cwd=branding.INSTALL_DIR, env=env)
            return True
        except Exception as e:
            print("web UI launch failed, falling back to classic:", e)
            return False

    def _quit(self):
        # ---- Save partial history if a dictation was in progress ----
        # If the user quits while dictating, save whatever audio/text was
        # captured so far so it isn't lost. History is appended at the end
        # of _process(), but a mid-dictation quit bypasses that path.
        try:
            if self.recording and self.frames and len(self.frames) > 0:
                import numpy as np
                try:
                    partial_audio = np.concatenate(self.frames, axis=0).flatten()
                    partial_duration = len(partial_audio) / SAMPLE_RATE
                    if partial_duration >= self.settings.get("min_seconds", 0.3):
                        # Quick local transcribe of what we captured so far
                        raw = self._transcribe(partial_audio)
                        if raw and raw.strip():
                            # Save raw transcript to history before shutdown
                            self.history.add(raw.strip(), mode="text",
                                             duration=partial_duration,
                                             raw=raw.strip(),
                                             quality=self._take_quality())
                            print("[shutdown] saved partial dictation to history")
                except Exception:
                    pass
        except Exception:
            pass

        # Close and register an active meeting before the forced process exit.
        # Expensive transcription is resumable on next launch; the raw WAV and
        # pending metadata are made durable here synchronously.
        try:
            if getattr(self, "meeting_recording", False):
                mid = self.meeting_recorder.finish_capture("")
                self.meeting_recording = False
                if mid:
                    print(f"[shutdown] meeting {mid} saved for resume")
        except Exception as e:
            print(f"[shutdown] meeting capture finalization failed: {e}")

        # ---- Stop local LLM backend (llama-cli subprocess) ----
        # Terminate any running llama-cli subprocess to prevent zombie
        # processes after the controller exits.
        try:
            import local_engine
            backend = local_engine.get_backend()
            if backend and hasattr(backend, 'unload'):
                backend.unload()
                print("[shutdown] local LLM backend unloaded")
            # Also stop the ModelProcessManager if it's active
            mgr = getattr(local_engine, '_MODEL_MANAGER', None)
            if mgr is not None and hasattr(mgr, 'stop'):
                mgr.stop()
                print("[shutdown] model process manager stopped")
        except Exception as e:
            print("[shutdown] backend cleanup error:", e)

        # ---- Cancel any in-flight model download (VAL-RECV-004) ----
        # If the user quits while a HuggingFace model download is in progress,
        # signal every active download to stop at the next chunk boundary. The
        # partial .part file is preserved on disk for resume on next launch.
        try:
            from models.downloader import cancel_active_downloads
            n = cancel_active_downloads()
            if n:
                print(f"[shutdown] cancelled {n} active model download(s) — "
                      "partial file(s) preserved for resume")
        except Exception as e:
            print("[shutdown] download cancel error:", e)

        # ---- Stop experimental background services ----
        self._cancel_correction_monitor()

        # ---- Stop audio stream ----
        try:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
        except Exception:
            pass
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        try:
            if self.icon is not None:
                self.icon.stop()
        except Exception:
            pass
        # Tear down the web-UI window process we own (owner v7 lifecycle fix).
        # Before this, "Quit Mumble" from the tray killed only the controller and
        # ORPHANED the webui_shell.py process plus its WebView2 renderer cluster —
        # they kept running (and holding port 49520 + memory) until the user closed
        # the window by hand. On Windows proc.terminate() (TerminateProcess) does
        # NOT reap a process's children, and WebView2 spawns several msedgewebview2
        # renderers as children of the shell, so we kill the whole tree with
        # `taskkill /T /F`; terminate() is the fallback if taskkill is unavailable.
        try:
            proc = getattr(self, "_webui_proc", None)
            if proc is not None and proc.poll() is None:
                killed = False
                try:
                    import subprocess
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        capture_output=True, timeout=4,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    killed = True
                except Exception:
                    pass
                if not killed:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            # The island is now an in-process layered window (no separate
            # process/WebView2 to tear down); it dies with the Tk root below.
            if self.root is not None:
                self.root.quit()
                self.root.destroy()
        except Exception:
            pass
        # Release the single-instance lock and FORCE the process to die. tkinter +
        # pystray + keyboard can leave non-daemon threads alive, so a normal return
        # would leave the process (and the lock port) hanging — which is exactly why
        # "close then reopen" failed: the next launch saw the port still held and
        # refused to start. os._exit guarantees the port frees so reopening works.
        try:
            if _LOCK_SOCK is not None:
                _LOCK_SOCK.close()
        except Exception:
            pass
        os._exit(0)

    def _restart(self):
        """Apply a pending update by running the swap script and exiting."""
        parent = os.path.dirname(branding.INSTALL_DIR)
        script = os.path.join(parent, "apply_update.bat")
        if os.path.exists(script):
            os.startfile(script)
        else:
            print("restart requested but no apply_update.bat found")
        # Now quit normally
        self._quit()

    def _boot(self):
        self._set_state("loading")
        self._cleanup_orphan_audio()
        # Resolve a suitable local model automatically on a fresh install so a
        # modest machine is not handed a heavy model before the first dictation.
        # We only auto-set the model on first run, never silently on an upgrade
        # (existing users keep whatever model they already chose).
        try:
            if self.settings.get("first_run", True):
                self._apply_hardware_model(force=True, load=False)
        except Exception as e:
            print("[hardware] first-run model resolve skipped:", e)
        # ---- Local AI Backend: activate the LlamaCppBackend if Edge AI is enabled
        # and a GGUF model file is present. Falls back to NullBackend (no-op) when
        # disabled or unavailable -- the router degrades gracefully.
        # ---- PHASE 1: Essential services FIRST so the UI appears while the model
        # loads in the background (perf: VAL-PERF-001 - UI before model load). ----
        # Register each binding INDEPENDENTLY (owner v9 reliability): a single bad
        # or unavailable bind must never take the others down with it.
        for name, reg in (
            ("record", self._register_hotkey),
            ("paste-latest", self._register_quick),
            ("history", self._register_history),
            ("search", self._register_search),
            ("mode-key", self._register_mode_key),
        ):
            try:
                reg()
            except Exception as e:
                print(f"hotkey registration failed ({name}):", e)
        # The web window's command channel (paste / deck_job / record / status)
        self._start_cmd_server()
        # Open the main window NOW — before the slow model load — so the user sees
        # the UI immediately on every launch. This is the single biggest win for
        # perceived startup time (VAL-STARTUP-002: window visible within 2 seconds).
        self.cmd_q.put("open")
        # Audio warm-up: pre-initialize PortAudio so the first dictation feels
        # instant. Runs in the background, doesn't block anything.
        def _warm_audio():
            try:
                t0 = time.time()
                sd.query_devices()
                print(f"[audio] portaudio init in {time.time() - t0:.2f}s")
            except Exception as e:
                print("[audio] portaudio warm skipped:", e)
        threading.Thread(target=_warm_audio, daemon=True).start()
        try:
            # Self-heal machines that ever had Mumble v1.x: its installer-at-login
            # Run-key entry survives reinstalls and threw a red PowerShell error
            # at every boot. Idempotent and instant when there's nothing to do.
            autostart.remove_legacy_run_key()
            autostart.remove_legacy_startup_artifacts()
            if self.settings.get("autostart", True) and not autostart.is_enabled():
                if not autostart.enable():
                    # Keep persisted UI state aligned with the actual Startup
                    # shortcut instead of claiming a failed enable succeeded.
                    self.settings.set("autostart", False)
                    print("[autostart] enable failed; setting reverted to off")
        except Exception:
            pass
        # ---- PHASE 2: Model load (SLOW — runs while the UI is already visible). ----
        # Resource Saver forces the lightest model (tiny.en) at boot without
        # touching the saved "model" — see _effective_model().
        boot_model = self._effective_model()
        if self._cloud_transcription_on():
            # Cloud transcription is the ACTIVE mode → don't load the heavy local
            # faster-whisper model into RAM at boot (it's ~0.5–1.5 GB and would
            # sit unused). It loads lazily only if/when local is actually needed:
            # a cloud failure, or a mode-key dictation that needs word timestamps
            # (see _ensure_local_model / _local_transcribe). This is the owner's
            # "don't keep the local model loaded when a cloud model is selected".
            print(f"[model] cloud transcription active — deferring local model "
                  f"'{boot_model}' (loads on demand)")
            with self.lock:
                self.model = None
            self.model_name = boot_model
            _local_ok = True  # cloud mode: "no local model" is intentional, not a failure
        else:
            print(f"loading model '{boot_model}'…")
            _local_ok = self._try_load(boot_model)
        if not self._cloud_transcription_on() and not _local_ok:
            # The saved model wouldn't load (often a bigger model whose first download
            # failed). Fall back to the default so Mumble ALWAYS comes up usable — and,
            # crucially, fall through to register the hotkeys below no matter what, instead
            # of returning early and leaving the app with no model AND no Ctrl+Win.
            fallback = "base.en"
            if boot_model != fallback and self._try_load(fallback):
                self._notify(
                    "Using the default model",
                    f"Couldn't load “{boot_model}”, so Mumble switched back "
                    f"to “{fallback}”. Pick another in Settings any time.",
                )
                # Only persist the fallback as the user's CONFIGURED model when the
                # failure was about their real choice — NOT the forced Resource-Saver
                # model (tiny.en), which must never overwrite the saved "model".
                if not self.settings.get("resource_saver"):
                    self.model_name = fallback
                    self.settings.set("model", fallback)
            else:
                self._notify(
                    "Model didn't load",
                    "Mumble is open — choose a model in Settings to start dictating.",
                )
        if self._transcription_ready():
            # Stay IDLE (the island is hidden) on startup. The old "brief gold
            # ready pulse" called island.flash("text") — which renders the DONE
            # state, so the island flashed "Pasted!" at boot with no paste having
            # happened (owner v5: the confusing startup bubble). Removed — the
            # island only ever appears for a real dictation now; the tray icon
            # already signals "ready". Cloud mode is "ready" with no local model
            # loaded — that's the deliberate deferral, not an error.
            self._idle()
        else:
            self._set_state("error")
        # Reclaim transient memory after boot — model load + warm-up can leave
        # large temporary allocations (VAL-PERF-003).
        try:
            import gc
            gc.collect()
        except Exception:
            pass
        threading.Thread(target=self._check_pro_key, daemon=True).start()
        # Auto-check for updates (background, non-blocking)
        update.start_auto_check(
            on_available=self._on_update_available,
            on_error=lambda m: None,  # silent on network failure
        )
        print(
            "Mumble ready."
            if self._transcription_ready()
            else "Mumble open (no speech model yet)."
        )
        # Resume durable meeting jobs only after the selected transcription
        # engine is ready. Starting recovery beside model boot made both threads
        # race to load/use faster-whisper and could duplicate a large model in
        # memory on launch.
        recorder = getattr(self, "meeting_recorder", None)
        if recorder is not None and self._transcription_ready():
            def _recover_meetings():
                recorder.recover_pending(
                    on_processed=lambda _meeting_id: self._send_webui_async(
                        {"cmd": "refresh", "what": "meetings"}))
                self._send_webui_async({"cmd": "refresh", "what": "meetings"})
            threading.Thread(
                target=_recover_meetings,
                name="meeting-recovery",
                daemon=True,
            ).start()

    def run(self):
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Mumble.App")
        except Exception:
            pass
        self.root = tk.Tk()
        self.root.withdraw()
        try:
            self.root.iconbitmap(branding.ICON_ICO)
        except Exception:
            pass
        ui.apply_theme(self.root)
        self.island = Island(self.root)  # one island look (owner v6) — no style tiers
        # ITEM 3/4: wire the island MODE DECK — select a mode, open the Deck, toggle
        # Foreign — and push the live deck snapshot so it shows the enabled modes,
        # the active one (lit in its colour), and the optional Foreign toggle.
        try:
            self.island.set_widget_callbacks(
                on_mode=self.set_active_mode,
                on_deck=self.on_open_history,
                on_foreign=self.toggle_island_foreign,
                on_correct=self.open_correction_learning)
            self._push_island_bar_state()
        except Exception as e:
            print("island widget wiring skipped:", e)
        self._prewarm_correction_monitor()

        # Meeting Mode: create the recorder with transcribe_fn, settings,
        # and the island callback that marshals to the Tk thread.
        self.meeting_recorder = meeting.MeetingRecorder(
            transcribe_fn=self._transcribe,
            settings=self.settings,
            island_callback=self._meeting_island_cb)

        self.icon = pystray.Icon(
            "mumble",
            self._tray_image(STATE_COLORS["loading"]),
            "Mumble — Loading…",
            menu=self._build_menu(),
        )
        threading.Thread(target=self.icon.run, daemon=True).start()
        threading.Thread(target=self._boot, daemon=True).start()
        if self.settings.get("clipboard_enabled", True):
            self.clipboard.start()

        self._pump()
        self._watch_mode_key()  # retired no-op (Big Shift) — kept for call-site safety
        # Handle Ctrl+C cleanly so the webui process tree and port are released.
        try:
            import signal
            signal.signal(signal.SIGINT, lambda *_: self._quit())
        except Exception:
            pass
        self.root.mainloop()


_LOCK_SOCK = None


def _acquire_single_instance():
    """True if we're the only Mumble; False if another instance already holds the lock.

    Binds a fixed localhost port as the lock: a second instance can't bind the same
    port and exits. This is reliable across DIFFERENT Python interpreters (venv vs
    system) — the previous named-mutex approach let duplicates through, and duplicate
    instances fight over the mic + Ctrl+Win hotkey and can run stale in-memory code
    (e.g. an old instance still on the offline engine while a new one uses the AI).
    The socket is kept alive for the process lifetime and freed by the OS on exit.

    Crash recovery (VAL-STARTUP-005): when the previous instance was force-killed
    the OS normally releases the port immediately, but edge cases (orphaned
    WebView2 subtree, OS socket-state lag) can make a re-bind fail. We retry with
    a back-off so the launcher self-heals without a confusing "already running"
    message when no instance is actually alive."""
    global _LOCK_SOCK
    try:
        import socket
        import time as _time

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Do NOT set SO_REUSEADDR — we WANT the second bind to fail while the first holds it.
        for attempt in range(3):
            try:
                s.bind(("127.0.0.1", 49517))  # fixed Mumble single-instance port
                break  # success
            except OSError:
                if attempt < 2:
                    # Port is still held — check whether a LIVE instance actually
                    # owns it. If nothing answers we wait a tick (stale socket
                    # teardown) and retry; the third attempt is the final verdict.
                    live = False
                    try:
                        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        probe.settimeout(0.3)
                        probe.connect(("127.0.0.1", 49517))
                        probe.close()
                        live = True  # something is listening
                    except Exception:
                        pass  # port is held but nothing answers → likely stale
                    if live:
                        s.close()
                        return False  # real instance is running
                    _time.sleep(0.3 * (attempt + 1))  # back-off: 0.3, 0.6, …
                else:
                    s.close()
                    return False  # port still held after retries; another instance likely
        s.listen(1)
        _LOCK_SOCK = s
        return True
    except Exception:
        return True  # never block startup over a lock hiccup


def _signal_running_instance():
    """A second launch shouldn't scold with a popup — it should DO what the
    user wanted: open the running Mumble's window. The single-instance lock
    socket doubles as the channel: connect and send 'open' + the running
    instance's per-session token (read from the token file it wrote at start) so
    a foreign process can't pop the window unauthenticated."""
    try:
        import socket

        token = ""
        try:
            with open(branding.cmd_token_path(), "r", encoding="utf-8") as f:
                token = f.read().strip()
        except OSError:
            token = ""
        with socket.create_connection(("127.0.0.1", 49517), timeout=2) as s:
            s.sendall(b"open " + token.encode("ascii", "ignore"))
        return True
    except Exception as e:
        print("could not signal the running instance:", e)
        return False


def _serve_instance_signals(app):
    """Accept 'open' signals from later launches on the lock socket (it is
    already listen()ing) and route them to the command queue. Daemon thread —
    dies with the process; any error just ends the loop (lock stays held). The
    signal must carry this session's token (see _signal_running_instance)."""
    def _loop():
        import hmac
        while True:
            try:
                conn, _ = _LOCK_SOCK.accept()
                with conn:
                    data = conn.recv(128)
                parts = (data or b"").split(b" ", 1)
                if parts and parts[0] == b"open":
                    token = (parts[1].decode("ascii", "ignore").strip()
                             if len(parts) > 1 else "")
                    want = getattr(app, "_cmd_token", "") or ""
                    if want and hmac.compare_digest(token, want):
                        app.cmd_q.put("open")
                    else:
                        print("instance 'open' signal rejected (bad token)")
            except Exception:
                return
    threading.Thread(target=_loop, daemon=True).start()


if __name__ == "__main__":
    if not _acquire_single_instance():
        # Hand the request to the running instance (opens its window) and
        # leave quietly — no "already running" popup.
        _signal_running_instance()
        sys.exit(0)
    _app = Mumble()
    _serve_instance_signals(_app)
    _app.run()
