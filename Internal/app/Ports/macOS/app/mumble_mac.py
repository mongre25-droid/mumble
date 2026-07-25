#!/usr/bin/env python3
"""
Mumble — private voice-to-text for macOS (Golden Black).

Tap the hotkey, speak, tap again. Audio is transcribed locally (faster-whisper,
greedy decoding + VAD), then shaped by the selected text provider when Pro Mode is
on, saved to history, and pasted at your cursor. Mumble also remembers what you copy
(clipboard history) and tracks word stats. A golden island shows what's happening.
Transcription is on-device by default. The optional Cloud transcription mode sends
audio to the provider selected by the user; AI processing sends text only. With Pro
Mode off (or no key), text shaping runs locally via the offline builder.
"""

import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.error

import branding

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
import bindings as keyboard
import numpy as np
import pyperclip
import pystray
import sounddevice as sd
from PIL import Image, ImageDraw
print("[startup] input/audio libs ok", flush=True)

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
import ai
import processing_route
import copy
import autostart
import bindings  # unified keyboard+mouse binding layer (record/re-paste/search)
import foreign_boost  # local (offline) Foreign-Mode phonetic term correction
import formatting
import islamic_terms  # Foreign mode: slash-candidate annotation for Arabic/Islamic terms
import local_engine  # cloud-dominance routing gate (cloud-primary-when-key, local degrade)
import recording_limits
import transcription  # optional cloud STT (advanced); local faster-whisper is default
import ui
import update
from branding import STATE_COLORS, C
from clipboard import Clipboard
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
from overlay_mac import Island
from settings import Settings
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
        # NON-blocking send (owner reliability): a slow/closed webui socket must
        # never stall the clipboard monitor thread that fires this callback.
        self.clipboard.on_change = lambda: self._send_webui_async(
            {"cmd": "refresh", "what": "clipboard"})
        # experimental: wire compensation tracker into conversation pipeline
        if _comp_tracker is not None:
            try:
                self.conv_store._on_reply_callback = _comp_tracker.log_reply
            except Exception:
                pass
        self.model = None
        # Model default (PARITY-014): `small.en` (~466MB) — aligned with the
        # Windows canonical. Windows and macOS now share the same default model
        # so that first-run transcription quality is consistent across platforms.
        # Users on low-memory Macs (8GB) can switch to `base.en` (~142MB) via
        # Settings → Model. The Resource Saver toggle (Settings → Performance)
        # also keeps the model unloaded between dictations, reducing memory
        # pressure further.
        self.model_name = self.settings.get("model", "small.en")
        # Wire the on-device LLM backend (cheap — nothing loads until first use).
        self._init_local_llm()
        # The model name ACTUALLY loaded into self.model (vs model_name, which is
        # the user's CONFIGURED choice). They differ in Resource Saver mode, where
        # tiny.en is forced live without touching the saved choice — the live
        # resource_saver toggle uses this to know whether a reload is needed.
        self._loaded_model_name = None
        self.hotkey = self.settings.get("hotkey", "ctrl+option+d")
        self.quick_hotkey = self.settings.get("quick_paste_hotkey", "ctrl+option+v")
        self.history_hotkey = self.settings.get("history_hotkey", "ctrl+option+h")
        self.search_hotkey = self.settings.get("search_hotkey", "ctrl+option+s")

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
        self.active_mode = self.settings.get("island_active_mode", "") or (
            "prompt" if self.prompt_mode_enabled else None)
        if self.active_mode == "prompt" and not self.prompt_mode_enabled:
            self.prompt_mode_enabled = True

        # Snapshot of the explicit Island/Deck lane for one utterance.
        self._mode_active = False
        self._active_mode_start = None  # snapshot for per-utterance consistency
        self._update_manifest = None  # pending update manifest (set by auto-check)

        self.recording = False
        # self.busy (CTRL-007): gate that prevents overlapping start/stop cycles
        # from double-arming or double-stopping the mic. Checked in on_hotkey and
        # _toggle_pause before launching a start/stop daemon thread, and cleared in
        # the finally block of each operation once it completes. Semantically
        # equivalent to self._processing (which guards _process() specifically) —
        # both prevent re-entry during a critical section. on_hotkey uses self.busy
        # because it guards the broader start/stop lifecycle, not just _process().
        self.busy = False
        # _processing guard: True while _process() is running — prevents
        # opening a second mic stream. Checked in the two start paths.
        self._processing = False
        # Meeting Mode state (owner 2026-06-29 — meeting-mode milestone).
        self.meeting_recorder = None
        self.meeting_recording = False
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
        self._last_llm_ok = (
            0.0  # time of last successful AI call (for cold-start warm-up)
        )
        self._deck_job_lock = threading.Lock()  # in-flight guard for Deck/History jobs
        self._deck_job_active = False           # True while a Deck job is running
        # --- streaming transcription ---
        self._stream_results = []  # accumulated partial transcriptions
        self._stream_done = threading.Event()  # signals worker to stop
        self._shutting_down = threading.Event()  # signals coordinated shutdown
        self._stream_worker_thread = None
        self._stream_processed_samples = 0  # sample count covered by stream chunks
        self._stream_inflight_samples = 0
        self._stream_session_id = 0  # rejects publication from an older recording
        self._stream_idle = threading.Event()  # set while no chunk is decoding
        self._stream_idle.set()

        # --- thread-safe Tkinter dispatch queue ---
        self._tk_queue = queue.Queue()
        self._last_level_queued = 0.0
        self._cmd_server_thread = None  # set when _start_cmd_server runs

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
        self._input_bindings_lock = threading.Lock()
        self._input_bindings_registered = False

    # ============================================================ status/tray
    def status(self):
        return self.state, STATUS_LABELS.get(self.state, self.state.title())

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
                    "Four-hour limit reached - saving meeting",
                )
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
                    "Recording stopped early - saving captured audio",
                )
            threading.Thread(
                target=self._meeting_stop_after_capture_error,
                name="meeting-capture-error-stop",
                daemon=True,
            ).start()
            return
        # DEEP-009: Copy self.island to a local to prevent TOCTOU race with
        # shutdown (main thread setting self.island = None between the check
        # and the _tk_schedule calls).
        island = self.island
        if island is None:
            return
        if state == "recording":
            self._tk_schedule(island.set_state, "listening")
            self._tk_schedule(island.hint, f"Meeting \u00b7 {timer // 60}:{timer % 60:02d}")
        elif state == "paused":
            self._tk_schedule(island.set_state, "idle")
            self._tk_schedule(island.hint, "Meeting Paused")
        elif state == "transcribing":
            self._tk_schedule(island.set_state, "transcribing")
            self._tk_schedule(island.hint, "Transcribing Meeting")
        elif state == "diarising":
            self._tk_schedule(island.set_state, "transcribing")
            self._tk_schedule(island.hint, "Diarising Meeting")
        elif state == "done":
            self._tk_schedule(island.set_state, "idle")
            mins = int(timer // 60)
            secs = int(timer % 60)
            self._tk_schedule(
                island.hint,
                f"Meeting Saved \u00b7 {speaker_count} speaker{'' if speaker_count == 1 else 's'} \u00b7 {mins}:{secs:02d}")

    def _meeting_stop_at_limit(self):
        """Finalize and process a meeting capped by the four-hour watchdog."""
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
        """Preserve the contiguous audio captured before a device failure."""
        if not getattr(self, "meeting_recording", False):
            return
        result = self._meeting_stop("")
        resume_wake_word = getattr(self, "_resume_wake_word", None)
        if callable(resume_wake_word):
            resume_wake_word("meeting")
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
            # Let the ~1.35s done-flash clear first, then show the tip, so they don't
            # stack. Marshalled to the Tk thread (root.after is not thread-safe).
            self._tk_schedule(self._show_island_tip, tip["text"])
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
        return None

    def _restore_priority(self, old_priority):
        pass

    # =================================================================== audio
    def _audio_cb(self, indata, frames, time_info, status):
        if status:
            print(f"[audio] callback status: {status}", flush=True)
        # ---- System sleep / resume detection (CTRL-006) ----
        # If the gap between audio callbacks exceeds 2 seconds, the system
        # likely went to sleep. Discard stale pre-sleep audio and reset the
        # recording so the user gets a clean start after resume.
        now = time.time()
        last_cb = getattr(self, '_last_audio_cb_time', 0)
        if last_cb > 0 and now - last_cb > 2.0:
            sleep_dur = now - last_cb
            print(f"[audio] {sleep_dur:.0f}s gap in audio — system may have "
                  f"slept; discarding stale buffer and resetting")
            with self.lock:
                self.frames = []
                self._recorded_samples = 0
                # Reject pre-sleep work that completes after the fresh session.
                self._stream_session_id = getattr(self, "_stream_session_id", 0) + 1
                self._stream_results = []
                self._stream_processed_samples = 0
                self._stream_inflight_samples = 0
                self._stream_idle = threading.Event()
                self._stream_idle.set()
        self._last_audio_cb_time = now
        # Keep exactly the remaining samples at the ten-minute cap. Stream
        # shutdown is delegated to a worker because doing it in this PortAudio
        # callback can deadlock.
        recorded = max(0, int(getattr(self, "_recorded_samples", 0)))
        remaining = recording_limits.DICTATION_MAX_SAMPLES - recorded
        block = None if remaining <= 0 else indata[:remaining].copy()
        if block is not None and len(block):
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
        if block is None or len(block) == 0:
            return
        # Detect "stream opens fine but only digital silence comes back" — the
        # signature of a denied/blocked Microphone permission on macOS. sounddevice
        # does NOT raise in that case (it just hands back zeros), so without this the
        # failure is totally silent: flat soundbars, empty transcript, no history.
        peak = float(np.max(np.abs(block))) if block.size else 0.0
        if peak > 1e-5:
            self._mic_signal_seen = True
        elif (not getattr(self, "_mic_signal_seen", False)
              and not getattr(self, "_mic_warned", False)
              and len(self.frames) > 60):  # ~0.6s of pure silence in
            self._mic_warned = True
            self._tk_schedule(
                self._notify, "No microphone signal",
                "Mumble isn't receiving any audio. Turn on Microphone for Mumble in "
                "System Settings > Privacy & Security > Microphone, then reopen Mumble.")
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
        with self.lock:
            if self.busy:
                return
            self.busy = True
        try:
            self._notify(
                "Recording limit reached",
                "Mumble stopped at the 10-minute dictation limit and is "
                "transcribing what it captured.",
            )
        finally:
            try:
                self.stop_recording()
            finally:
                with self.lock:
                    self.busy = False

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
        shutdown_event = getattr(self, "_shutting_down", None)
        shutting_down = lambda: bool(
            shutdown_event is not None and shutdown_event.is_set())
        while not self._stream_done.is_set() and not shutting_down():
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
                # Work claimed before key-release belongs to this recording.
                # Publish it once unless a newer session superseded it.
                with self.lock:
                    same_session = (
                        session_id == getattr(self, "_stream_session_id", 0))
                    if same_session and partial_text is not None:
                        if partial_text.strip():
                            self._stream_results.append(partial_text.strip())
                        self._stream_processed_samples += len(audio_chunk)
                    if same_session:
                        self._stream_inflight_samples = 0
                        if idle is not None:
                            idle.set()
            if partial_text is None or self._stream_done.is_set() or shutting_down():
                break

    def start_recording(self):
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
            return
        # Dictation and Meeting Mode share the microphone. Never open a second
        # stream while a meeting is recording.
        if getattr(self, "meeting_recording", False):
            self.busy = False
            return
        self.frames = []
        self._recorded_samples = 0
        self._dictation_limit_triggered = False
        self._last_audio_cb_time = 0
        self._q_acc = []   # fresh audio-quality accumulator per dictation
        # Reset the level-throttle baseline so the FIRST audio level of this
        # recording always reaches the island (stale value from the previous
        # recording's final level could otherwise swallow the opening update).
        self._last_level_queued = 0.0
        # THE BIG SHIFT: the per-utterance "mode" is simply whether the sticky
        # Prompt toggle is on. No key-window bookkeeping any more.
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
        # With no selected text the Search shortcut starts voice search. Surface
        # that as its own state instead of looking like ordinary dictation.
        is_search = getattr(self, "_search_requested", False)
        rec_state = "search" if is_search else "listening"
        if self.island:
            self._tk_schedule(self.island.set_state, rec_state)
            try:
                # The island's "armed" accent now means "Prompt mode is on".
                self._tk_schedule(self.island.set_armed, self.prompt_mode_enabled)
            except Exception:
                pass
        self._set_state(rec_state)
        self.stream = self._open_input_stream()
        self.stream.start()
        self.recording = True
        # Launch streaming transcription worker — transcribes chunks in the
        # background while the user speaks, so on stop the final paste is
        # near-instant (only the last chunk needs decoding).
        #
        # Resource Saver (owner v8): DON'T run the streaming worker at all — that
        # background decode is a continuous CPU/battery draw for the whole
        # recording. With no worker the stream state stays empty, so _process
        # takes its authoritative single full pass at stop (which covers the
        # entire utterance). stop_recording's `_stream_done.set()` + alive-check
        # are both no-ops with the worker thread left at None.
        self._stream_results = []
        self._stream_processed_samples = 0
        self._stream_inflight_samples = 0
        self._stream_session_id = getattr(self, "_stream_session_id", 0) + 1
        if not hasattr(self, "_stream_idle"):
            self._stream_idle = threading.Event()
        self._stream_idle.set()
        self._stream_done.clear()
        if self.settings.get("resource_saver"):
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
        # Resource Saver (owner v8): never fire a warm-up call, regardless of the
        # ai_warmup setting — a warm-up burns a request and CPU/network for a
        # latency win that Resource Saver explicitly trades away.
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
                info = ai.PROVIDERS.get(decision.provider) or {}
                ai.cerebras_warm(decision.api_key, decision.model,
                                 url=info.get("url", ""),
                                 route_decision=decision)
                self._last_llm_ok = time.time()  # worker is now warm
                print("AI warmed up")
            except Exception as e:
                print("warm-up skipped:", e)

        threading.Thread(target=run, daemon=True).start()

    def _check_accessibility(self, prompt=False, notify=True):
        """OLUI-011: Check macOS Accessibility permissions.

        On macOS, the pynput/Quartz backend requires Accessibility permission
        to register global hotkeys and simulate keystrokes. Without it, hotkeys
        silently fail — the user presses Ctrl+Option+D and nothing happens,
        with no indication WHY. Startup prompts once; a background watcher then
        notices a grant and arms the hotkeys without an application restart.

        Uses AXIsProcessTrusted() via PyObjC when available, with a fallback
        to AppleScript for environments where PyObjC isn't installed."""
        try:
            # AXIsProcessTrusted is exported by ApplicationServices, not AppKit.
            # Calling AppKit.AXIsProcessTrusted raises AttributeError on a normal
            # PyObjC installation and used to abort startup.
            from ApplicationServices import AXIsProcessTrusted
            if prompt:
                from ApplicationServices import (
                    AXIsProcessTrustedWithOptions,
                    kAXTrustedCheckOptionPrompt,
                )
                trusted = bool(AXIsProcessTrustedWithOptions(
                    {kAXTrustedCheckOptionPrompt: True}))
            else:
                trusted = bool(AXIsProcessTrusted())
        except Exception as primary_error:
            # Framework unavailable or its bridge failed — query System Events
            # instead of letting a permission diagnostic crash the application.
            try:
                result = subprocess.run(
                    ["osascript", "-e",
                     'tell application "System Events" to return (UI elements enabled)'],
                    capture_output=True, text=True, timeout=5)
                trusted = "true" in (result.stdout or "").lower().strip()
            except Exception as fallback_error:
                print("[accessibility] permission check unavailable:",
                      primary_error, fallback_error)
                return None  # unknown: caller should not disable hotkeys

        if not trusted and notify:
            print("[accessibility] Accessibility permission NOT granted — hotkeys may be silent")
            self._tk_schedule(
                self._notify,
                "Accessibility permission needed",
                "Mumble needs Accessibility access for the hotkey (Ctrl+Option+D). "
                "Turn it on in System Settings > Privacy & Security > Accessibility, "
                "and Mumble will enable hotkeys automatically.")
            # Also push a hint to the island so it's visible in the UI.
            if self.island:
                self._tk_schedule(
                    self.island.hint,
                    "Accessibility permission needed — hotkeys disabled")
        return trusted

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
        with self.lock:
            if not self.recording:
                return
            self.recording = False
            # The worker claims chunks under this lock. It may finish one
            # existing claim, but it cannot claim another after key-release.
            self._stream_done.set()
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
        if self.island:
            self._tk_schedule(self.island.set_level, 0.0)
            try:
                self._tk_schedule(self.island.set_armed, False)
            except Exception:
                pass
        if not self.frames:
            self._search_requested = False
            self._idle()
            return
        # Flip the pill to "Transcribing" the MOMENT the user stops — BEFORE the
        # worker join below (which can block up to 3s). Previously the island sat
        # on "Listening" through the whole join, so stopping felt unresponsive.
        if self.island:
            self._tk_schedule(self.island.set_state, "transcribing")
        self._set_state("transcribing")
        # Drain only a chunk already in flight. It publishes its exact covered
        # range, so the final tail never decodes the same audio twice.
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
            self._idle()
            return
        worker = self._stream_worker_thread
        if worker and worker.is_alive():
            worker.join(timeout=0.05)
        audio = np.concatenate(self.frames, axis=0).flatten()
        self.frames = []
        duration = len(audio) / SAMPLE_RATE
        if duration < self.settings.get("min_seconds", 0.3):
            self._search_requested = False
            self._idle()
            return
        mode_active = self._mode_active
        self._process(audio, duration, mode_active)

    def _transcribe(self, audio, want_words=False):
        """Transcribe audio and return the raw transcript untouched — all cleanup,
        mode detection, and terminology fixes happen later in the AI pass.

        Transcription is on-device (faster-whisper) by DEFAULT — audio never leaves
        the machine. An advanced, opt-in Cloud mode (Settings → Transcription)
        uploads the utterance to a low-latency hosted Whisper endpoint instead, for
        faster voice-to-text on modest hardware. It falls back to local on ANY
        error so a flaky network never loses a dictation.

        Internal callers may request local word timestamps for diagnostics or
        alignment. Returns a plain string normally, or (text, words) when
        want_words=True."""
        invocation_snapshot = self._transcription_snapshot()
        if not want_words and self._cloud_transcription_on(
            invocation_snapshot.route
        ):
            text = self._cloud_transcribe(audio, invocation_snapshot)
            if text and text.strip():
                return text
            # cloud failed → fall through to local so the dictation still lands
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
        is present for the chosen provider (otherwise stay on local silently)."""
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
        try:
            t0 = time.time()
            text = transcription.transcribe(audio, invocation_snapshot)
            print(f"[cloud-stt] {time.time() - t0:.2f}s "
                  f"({provider})")
            return text
        except Exception as e:
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
        where words = [{'word','start','end'}, ...]."""
        lang = self.settings.get("language", "en")
        with self.lock:
            model = self.model
        if model is None:
            # Cloud mode deferred the local load — bring it up now. This path is
            # hit on a cloud failure/fallback or an explicit internal request for
            # local word timestamps (cloud can't supply them).
            if self._ensure_local_model():
                with self.lock:
                    model = self.model
            if model is None:
                return ("", []) if want_words else ""

        # Boost process priority during transcription — gives Mumble more CPU time
        # slices, reducing transcription latency by 15-30% on busy systems.
        old_priority = self._boost_priority()
        # DEEP-014: Use try/finally so _restore_priority() is reachable even when
        # the method returns from inside the with self._tx_lock block. Without
        # finally, a return (or exception) inside the lock block bypasses the
        # restore call.
        try:
            # Serialize model access with a context manager — ensures the lock is always
            # released even on exception, and avoids the silent-data-loss from timeout
            # returns (CQ-009). faster-whisper isn't safe for concurrent transcribe().
            with self._tx_lock:
                # Personal vocabulary terms bias the decoder toward the user's
                # names/jargon at the STT level (faster-whisper `hotwords`) — the
                # cheapest, most effective accuracy lever for proper nouns.
                terms = self.settings.get("vocabulary_terms", []) or []
                terms = formatting.sanitize_hotwords(terms)
                hotwords = ", ".join(terms[:50]) if terms else None
                kwargs = {}
                if want_words:
                    # Suppress Whisper's classic silence hallucinations ("Thank
                    # you." etc.) — only effective with word timestamps enabled.
                    kwargs["hallucination_silence_threshold"] = 2.0
                segs, _ = model.transcribe(
                    audio,
                    language=lang,
                    beam_size=beam,
                    best_of=1,
                    temperature=0.0,
                    vad_filter=True,
                    condition_on_previous_text=False,
                    no_speech_threshold=0.6,
                    log_prob_threshold=-1.0,
                    word_timestamps=want_words,
                    without_timestamps=not want_words,
                    hotwords=hotwords,
                    **kwargs,
                )
                if want_words:
                    text_parts, words = [], []
                    for s in segs:
                        self._note_quality(s)
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
                    parts.append(s.text)
                return "".join(parts).strip()
        finally:
            self._restore_priority(old_priority)

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

    def _process(self, audio, duration, mode_active=False):
        # Consume the search request up front so an error or early return can
        # never leave a stale flag that would hijack the NEXT dictation into a
        # browser search.
        search_requested = getattr(self, "_search_requested", False)
        self._search_requested = False
        # Snapshot the relevant settings fields BEFORE any AI calls so a rapid
        # providers/options change during the run doesn't give us a half-old
        # half-new config (VAL-CROSS-020). Read ONCE per utterance.
        _snap = {
            "pro_mode": self.settings.get("pro_mode", True),
            "format_enabled": self.settings.get("format_enabled", True),
            "foreign_mode": self.settings.get("foreign_mode", False),
            "foreign_languages": self.settings.get("foreign_languages"),
            "english_only": self.settings.get("english_only", True),
            "local_only_mode": self.settings.get("local_only_mode", False),
            "llm_provider": self.settings.get("llm_provider", "cerebras"),
            "instant_text": self.settings.get("instant_text", True),
            "user_name": self.settings.get("user_name", ""),
            "prompt_prefs": copy.deepcopy(self.settings.get("prompt_prefs", {})),
            "primary_language": self.settings.get("primary_language", "en"),
            "vocabulary": copy.deepcopy(self.settings.get("vocabulary", {})),
            "vocabulary_terms": copy.deepcopy(self.settings.get("vocabulary_terms", [])),
            "polish_aggressiveness": self.settings.get("polish_aggressiveness", "Light"),
            "rpunct_enabled": self.settings.get("rpunct_enabled", False),
            "modes": copy.deepcopy(self.settings.get("modes", {})),
            "local_llm_enabled": self.settings.get("local_llm_enabled", False),
            "local_llm_model": self.settings.get("local_llm_model", ""),
            "cerebras_api_key": self.settings.get("cerebras_api_key", ""),
            "cerebras_model": self.settings.get("cerebras_model", ""),
            "openrouter_api_key": self.settings.get("openrouter_api_key", ""),
            "openrouter_model": self.settings.get("openrouter_model", ""),
            "openai_api_key": self.settings.get("openai_api_key", ""),
            "openai_model": self.settings.get("openai_model", ""),
            "anthropic_api_key": self.settings.get("anthropic_api_key", ""),
            "anthropic_model": self.settings.get("anthropic_model", ""),
            "groq_api_key": self.settings.get("groq_api_key", ""),
            "groq_model": self.settings.get("groq_model", ""),
            "deepseek_api_key": self.settings.get("deepseek_api_key", ""),
            "deepseek_model": self.settings.get("deepseek_model", ""),
        }
        try:
            self._processing = True
            # Use streaming results as the base transcription when available —
            # for both everyday text and explicit modes. Mode selection is an
            # Island/Deck state snapshot and no longer needs word timestamps.
            # Snapshot the streaming state as ONE consistent pair under the lock:
            # the worker may still be mid-chunk if its join timed out, and reading
            # the text list and the sample seam separately could drop a chunk.
            with self.lock:
                stream_results = list(self._stream_results)
                processed_samples = max(0, self._stream_processed_samples)
            if stream_results:
                streaming_base = " ".join(stream_results)
                print(
                    f"[stream] {len(stream_results)} chunks / "
                    f"{processed_samples} samples → {streaming_base[:80]!r}..."
                )
                words = []
                tail_audio = (
                    audio[processed_samples:]
                    if processed_samples < len(audio)
                    else audio[:0]
                )
                tail_text = ""
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
                try:
                    result = self._transcribe(audio, want_words=False)
                    raw, words = result if isinstance(result, tuple) else (result, [])
                except Exception as e:
                    print("transcription error:", e)
                    self._notify("Transcription failed", str(e))
                    self._set_state("error")
                    time.sleep(1.0)
                    self._idle()
                    return
            if not raw:
                if self.island:
                    self._tk_schedule(
                        self.island.hint, "No speech detected — try again")
                self._idle()
                return

            # Personal vocabulary, two passes before anything downstream sees
            # the transcript.
            try:
                raw = formatting.apply_vocabulary(
                    raw, _snap["vocabulary"]
                )
                raw = formatting.apply_vocabulary_terms(
                    raw, _snap["vocabulary_terms"]
                )
            except Exception as e:
                print("vocabulary error:", e)

            # THE BIG SHIFT: modes are INFERRED, not detected. Prompt toggle
            # ON → prompt lane; OFF → plain clean text. The pre-Big-Shift
            # mode-key detection is retired. ITEM 4: the island mode deck can
            # force ANY lane (not just Prompt). Capture WHICH.
            if mode_active:
                forced = getattr(self, "_active_mode_start", None) or "prompt"
                det_mode, det_request, clip_count = forced, raw, 0
            else:
                det_mode, det_request, clip_count = "text", raw, 0

            # Cloud-dominance routing (local_engine.route): cloud is the primary
            # authority whenever a key is present and local-only is OFF; otherwise
            # the local engine runs best-effort and never hard-blocks.
            _route = processing_route.snapshot(
                _snap,
                feature=(det_mode if det_mode in ("prompt", "email", "reply") else "dictation"),
                lane=det_mode,
            )
            will_cloud = _route.cloud_augmented
            instant_text = bool(_snap.get("instant_text", True)) and not mode_active
            if instant_text:
                will_cloud = False

            # Foreign Mode, fully on-device: when shaping stays LOCAL, run the local
            # phonetic booster so high-confidence foreign terms the English model
            # mis-decoded are corrected on the edge.
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

            if self.island:
                self._tk_schedule(
                    self.island.set_building, det_mode, offline=not will_cloud
                )
            if instant_text:
                mode = "text"
                out = (formatting.format_transcript(raw, commands=False)
                       if _snap["format_enabled"] else raw.strip())
                used_offline = True
                print("[latency] instant local text — skipped network polishing")
            else:
                self._set_state("processing")
                mode, out, used_offline = self._generate(
                    raw,
                    det_mode,
                    det_request,
                    clip_count,
                    mode_active,
                    config_snap=_snap,
                    route_decision=_route,
                )
            if not out:
                self._idle()
                return
            if mode in ("prompt",) and _comp_tracker is not None:
                try:
                    _comp_tracker.log_prompt(out)
                except Exception:
                    pass
            if mode == "prompt":
                try:
                    self.prompt_history.record(det_request or raw, out)
                except Exception as e:
                    print("prompt history record error:", e)
            entry = self.history.add(out, mode, duration, raw=raw,
                                     quality=self._take_quality())
            try:
                self.stat_store.record(entry["words"], duration, mode)
            except Exception as e:
                print("stats record error:", e)
            self._send_webui_async({"cmd": "refresh", "what": "history"})
            print(f"[{mode}{' · offline' if used_offline else ''}] {out!r}")
            if search_requested:
                self._open_search(out)
                landed = True
                print(f"[{mode}] Searched: {out}")
            else:
                landed = self._paste(out)
            if self.island:
                pasted = bool(landed) or bool(search_requested)
                self._tk_schedule(self.island.flash, mode,
                                  offline=used_offline, pasted=pasted)
            try:
                self.refresh_tray_menu()
                if self.window:
                    self._tk_schedule(self.window.refresh_history_tab)
            except Exception as e:
                print("UI refresh error (non-fatal):", e)
            self._set_state("idle")
        except Exception as e:
            print(f"_process unhandled error: {e}")
            import traceback

            traceback.print_exc()
            self._notify("Processing error", str(e))
            self._set_state("error")
            time.sleep(1.0)
            self._idle()
        finally:
            self._processing = False

    def _quick_paste_label(self):
        hk = self.settings.get("quick_paste_hotkey", "ctrl+option+v")
        return " + ".join(p.strip().capitalize() for p in hk.split("+") if p.strip())

    def _focused_editable(self):
        """Best-effort: is the foreground window's focused control something a paste would
        land in? A blinking caret, or an edit/rich-text/browser class, says yes. When we
        can't tell, default to True so we don't nag on a paste that actually worked."""
        # macOS seam: the Windows port introspects the focused control via Win32
        # GetGUIThreadInfo. The cross-app equivalent is an AXUIElement probe of the
        # system-wide focused element, gated on the Accessibility permission the
        # app already needs for pynput hotkeys. BEST-EFFORT and NEVER raises: on
        # any error (framework missing, permission not granted, role unreadable)
        # it falls back to True, so the label is never worse than the old
        # always-optimistic behaviour. The paste is sent regardless; this only
        # decides the honest "Pasted!" vs "Saved · …" label.
        try:
            from ApplicationServices import (
                AXUIElementCreateSystemWide, AXUIElementCopyAttributeValue)
            import HIServices  # noqa: F401 — ensures the AX symbols are loaded
            syswide = AXUIElementCreateSystemWide()
            err, focused = AXUIElementCopyAttributeValue(
                syswide, "AXFocusedUIElement", None)
            if err or focused is None:
                return True  # can't tell → assume editable (no regression)
            err, role = AXUIElementCopyAttributeValue(focused, "AXRole", None)
            if err or not role:
                return True
            editable = {"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"}
            return str(role) in editable or str(role) == "AXUnknown"
        except Exception:
            return True

    def _set_clipboard(self, value, tries=6, delay=0.04):
        """Put `value` on the clipboard and CONFIRM it actually stuck.

        Windows clipboard writes fail silently when another app is holding the
        clipboard open — including the app we just sent Cmd+V to, which keeps
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
                    return True
            except Exception:
                pass
            time.sleep(delay)
        return False

    def _paste(self, text, keep_on_clipboard=False):
        """Paste `text` at the cursor. Returns True if it likely landed in an
        editable field. With keep_on_clipboard=True the text is LEFT on the
        clipboard afterwards (so Cmd+V keeps working); otherwise the user's
        previous clipboard is restored — Mumble must NOT silently replace what
        the user had copied (Rule 2).

        SERIALIZED (owner v6 bug audit): the dictation paste path and the
        flyout/cmd click-to-paste path call this from different threads, and the
        clipboard pause/resume + previous-clipboard save/restore are NOT reentrant
        — two overlapping pastes could corrupt the clipboard or re-capture
        Mumble's own output. The lock lets one finish before the next starts."""
        with self._paste_lock:
            return self._paste_impl(text, keep_on_clipboard)

    def _paste_impl(self, text, keep_on_clipboard=False):
        landed = self._focused_editable()
        if self.clipboard:
            self.clipboard.pause()
            try:
                # Tag this as Mumble's OWN output so it's excluded from "context" later
                # (owner decision: Mumble must not feed its own results back to itself).
                self.clipboard.mark_own(text)
            except Exception:
                pass
        try:
            previous = pyperclip.paste()
        except Exception:
            previous = ""
        try:
            # Put our text on the clipboard and CONFIRM it before Cmd+V — a lost
            # copy would otherwise paste stale clipboard content (an old prompt).
            if not self._set_clipboard(text):
                raise RuntimeError(
                    "clipboard stayed busy; paste cancelled to protect existing content")
            # Release held modifiers so Cmd+V lands cleanly (Rule 7).
            # DEEP-008: use 'command' on macOS instead of the non-existent
            # 'windows' key to avoid confusing remapping tools (Karabiner, etc.).
            if sys.platform == "darwin":
                mods_to_release = ("ctrl", "alt", "shift", "command")
            else:
                mods_to_release = ("ctrl", "alt", "shift", "windows")
            for mod in mods_to_release:
                try:
                    keyboard.release(mod)
                except Exception:
                    pass
            time.sleep(0.04)
            keyboard.send("cmd+v")
            # Give the target app time to READ and RELEASE the clipboard before
            # we restore. Restoring too early loses the race: the restore copy
            # fails while the app still holds the clipboard open, leaving our
            # text behind — the exact "it replaced what I'd copied" bug.
            # SCALED BY LENGTH (long-paste fix): big transcripts take editors
            # noticeably longer to ingest — restoring after a fixed 0.18s
            # could yank the clipboard mid-paste and truncate/abort the paste.
            time.sleep(min(1.2, 0.18 + len(text) / 20000.0))
        except Exception as e:
            print("paste error:", e)
            landed = False
        finally:
            # Restore the user's previous clipboard (unless keep_on_clipboard).
            # Use the CONFIRMED setter with retries so a brief clipboard lock
            # can't leave Mumble's text behind. If `previous` is empty, the prior
            # clipboard was either empty or non-text (image/RTF/files) which
            # pyperclip can't read or restore — we can't bring that back, so our
            # text stays (the documented tradeoff for non-text content).
            if keep_on_clipboard:
                self._set_clipboard(text)
            elif previous:
                self._set_clipboard(previous)
            if self.clipboard:
                # Sync the monitor to whatever is actually on the clipboard now,
                # so it never re-captures the result as a fresh history item.
                try:
                    self.clipboard._last_text = pyperclip.paste() or ""
                except Exception:
                    pass
                self.clipboard.resume(skip_current=True)
        return landed

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
                    return None
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
            # Clean up a partially-started stream so the mic isn't left open (CTRL-009).
            if self.recording:
                try:
                    self.stop_recording()
                except Exception:
                    pass
            else:
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
            self._search_requested = False
            self._notify("Microphone error", e)
            self._set_state("error")
            time.sleep(1.0)
            self._idle()
        finally:
            self.busy = False

    def _safe_stop(self):
        try:
            self.stop_recording()
        finally:
            self.busy = False

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

    def _run_deck_job(self, items, intent, intent_title, mode):
        """Guard wrapper: the web flyout's Go/Convert buttons have no disabled
        state, so a fast double-click fired two `deck_job` commands → two AI calls
        and two pastes. Serialise — a second job while one is in flight is
        dropped (the island/paste then run exactly once)."""
        with self._deck_job_lock:
            if self._deck_job_active:
                print("[history] a Deck job is already running — ignoring duplicate Go")
                return
            self._deck_job_active = True
        try:
            self._run_deck_job_impl(items, intent, intent_title, mode)
        finally:
            self._deck_job_active = False

    def _run_deck_job_impl(self, items, intent, intent_title, mode):
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
        invocation_snapshot = processing_route.snapshot_inputs(
            self.settings, feature="deck", lane=mode or "deck_reason",
            context=ctx_block, context_policy="deck_selection",
            local_model_ready=local_engine.local_llm_ready(),
        )
        route_decision = invocation_snapshot.route
        ctx_block = invocation_snapshot.context
        key = route_decision.api_key
        if self.island:
            build = ["context", mode] if mode else "context"
            self._tk_schedule(self.island.set_building, build,
                              offline=not route_decision.cloud_augmented)
        out, used_offline = "", True
        if route_decision.ready:
            last_err = None
            for attempt in (1, 2):
                try:
                    info = ai.PROVIDERS[route_decision.provider]
                    gen = processing_route.call_provider(
                        route_decision, ai.cerebras_intent,
                        instruction, ctx_block, "", key, route_decision.model,
                        url=info["url"], expected_feature="deck",
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
            if not route_decision.ready:
                self._notify("Deck presets need AI",
                             "Add your free Cerebras key in Settings → AI to run "
                             "presets like Summarise, Merge or Answer it.")
            else:
                self._notify("The Deck couldn't reach the AI",
                             "Nothing was generated — check your connection and "
                             "try again.")
            self._idle()
            return
        try:
            entry = self.history.add(out, out_mode, 0.0)
            self.stat_store.record(entry["words"], 0.0, out_mode)
        except Exception as e:
            print("hub history/stats error:", e)
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
            landed = self._paste(out)
            if self.island:
                flash = ["context", mode] if mode else "context"
                # "Pasted!" only when it really landed; otherwise "Saved · Ctrl+Option+H"
                # (it's in History) — owner v4: never claim a paste that didn't happen.
                self._tk_schedule(self.island.flash, flash,
                                  offline=used_offline, pasted=bool(landed))
        finally:
            # Tray + main-window refresh AFTER the paste — never block it.
            try:
                self.refresh_tray_menu()
                if self.window:
                    self._tk_schedule(self.window.refresh_history_tab)
            except Exception:
                pass
            self._set_state("idle")

    def _paste_image(self, path):
        """Paste a clipboard-history image: put it back as CF_DIB and send
        Cmd+V. The image stays on the clipboard afterwards (there is no way to
        snapshot/restore arbitrary prior non-text content without pywin32)."""
        # Serialize with the text-paste path: the clipboard pause/restore +
        # previous-clipboard dance is NOT reentrant, so a text dictation paste on
        # another thread could resume the monitor mid-image-paste and re-capture
        # the image (or race the restore). _paste() takes the same lock.
        with self._paste_lock:
            if not self.copy_image(path):
                return   # copy_image already notified WHY — never blind-fire Cmd+V
            # DEEP-008: use 'command' on macOS instead of 'windows'.
            if sys.platform == "darwin":
                mods_to_release = ("ctrl", "alt", "shift", "command")
            else:
                mods_to_release = ("ctrl", "alt", "shift", "windows")
            for mod in mods_to_release:
                try:
                    keyboard.release(mod)
                except Exception:
                    pass
            time.sleep(0.05)
            keyboard.send("cmd+v")

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
        `grab_selection` (reads the highlighted text), plus record/quit/Deck jobs.
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
            # authenticated) even though clients can't read it.
            print("cmd token write failed:", e)

    def _cmd_token_ok(self, req):
        """Constant-time check that a command carries this session's token."""
        import hmac
        want = getattr(self, "_cmd_token", "") or ""
        got = req.get("token") or ""
        return bool(want) and hmac.compare_digest(str(got), str(want))

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
                # Authenticate BEFORE dispatch: without a valid per-session
                # token any local process could inject paste/grab_selection/
                # deck_job on this port. Reject unauthenticated callers.
                if not self._cmd_token_ok(req):
                    conn.sendall(
                        (_json.dumps({"ok": False, "message": "unauthorized"})
                         + "\n").encode("utf-8"))
                    return
                cmd = req.get("cmd", "")
                resp = {"ok": True}
                if cmd == "status":
                    st, txt = self.status()
                    resp.update(
                        state=st,
                        text=txt,
                        recording=self.recording,
                        active_mode=getattr(self, "active_mode", None),
                    )
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
                elif cmd == "paste":
                    text = req.get("text") or ""

                    def _wp():
                        try:
                            time.sleep(0.25)  # let focus return to the target
                            self._paste(text)
                        except Exception as e:
                            print("cmd paste error:", e)
                    threading.Thread(target=_wp, daemon=True).start()
                elif cmd == "paste_image":
                    path = req.get("path") or ""

                    def _wi():
                        try:
                            time.sleep(0.25)
                            self._paste_image(path)
                        except Exception as e:
                            print("cmd paste_image error:", e)
                    threading.Thread(target=_wi, daemon=True).start()
                elif cmd == "reload":
                    # The web window just saved a setting from ITS process; pull
                    # settings.json back in and live-apply anything with runtime
                    # state — a rebind or model switch must never wait for a
                    # restart to take effect.
                    threading.Thread(
                        target=self._apply_settings_change,
                        args=(req.get("key") or "",), daemon=True,
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

                    def _wj():
                        try:
                            time.sleep(0.25)
                            self._run_deck_job(items, instr, title, mode)
                        except Exception as e:
                            print("cmd deck_job error:", e)
                    threading.Thread(target=_wj, daemon=True).start()
                # ── Meeting Mode commands (owner 2026-06-29) ──
                elif cmd == "grab_selection":
                    if getattr(self, "_deck_job_active", False):
                        resp.update(selection="")
                    else:
                        try:
                            selection = self._grab_selection_quiet()
                        except Exception as e:
                            print("cmd grab_selection error:", e)
                            selection = ""
                        resp.update(selection=selection)
                elif cmd == "capture_conversation":
                    if getattr(self, "_deck_job_active", False):
                        resp.update(
                            ok=False,
                            message="Busy running a Deck job - try again.",
                        )
                    else:
                        try:
                            resp.update(self.capture_conversation())
                        except Exception as e:
                            print("cmd capture_conversation error:", e)
                            resp.update(ok=False, message=str(e))
                elif cmd == "focused":
                    focused = bool(req.get("value", True))
                    if self.island:
                        self._tk_schedule(self.island.set_focused, focused)
                        if focused:
                            self._set_state(self.state)
                elif cmd == "set_island_mode":
                    active_mode = self.set_active_mode(req.get("mode") or None)
                    resp.update(active_mode=active_mode)
                elif cmd == "meeting_record_start":
                    resp = self._meeting_start()
                elif cmd == "meeting_record_stop":
                    resp = self._meeting_stop(req.get("title", ""))
                elif cmd == "meeting_record_pause":
                    if self.meeting_recorder is not None and self.meeting_recording:
                        ok = self.meeting_recorder.pause()
                        resp = {"ok": bool(ok), "paused": bool(ok)}
                    else:
                        resp = {"ok": False, "message": "No meeting recording to pause."}
                elif cmd == "meeting_record_resume":
                    if self.meeting_recorder is not None and self.meeting_recording:
                        ok = self.meeting_recorder.resume()
                        resp = {"ok": bool(ok), "paused": not bool(ok)}
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
                srv.settimeout(0.5)  # allow periodic shutdown check
            except Exception as e:
                print("cmd server unavailable:", e)
                return
            while True:
                try:
                    conn, _ = srv.accept()
                    threading.Thread(target=_handle, args=(conn,),
                                     daemon=True).start()
                except _socket.timeout:
                    if self._shutting_down.is_set():
                        try:
                            srv.close()
                        except Exception:
                            pass
                        return
                    continue
                except Exception:
                    return

        self._cmd_server_thread = threading.Thread(target=_serve, daemon=True)
        self._cmd_server_thread.start()

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
            return
        k = (key or "").split(".", 1)[0]
        try:
            if k == "hotkey":
                self.hotkey = self.settings.get("hotkey", self.hotkey)
                self._register_hotkey()
                print(f"[live-apply] record hotkey → {self.hotkey!r}")
            elif k == "quick_paste_hotkey":
                self.quick_hotkey = self.settings.get(
                    "quick_paste_hotkey", self.quick_hotkey)
                self._register_quick()
                print(f"[live-apply] paste-latest hotkey → {self.quick_hotkey!r}")
            elif k == "history_hotkey":
                self.history_hotkey = self.settings.get(
                    "history_hotkey", self.history_hotkey)
                self._register_history()
                print(f"[live-apply] History hotkey → {self.history_hotkey!r}")
            elif k == "search_hotkey":
                self.search_hotkey = self.settings.get(
                    "search_hotkey", self.search_hotkey)
                self._register_search()
                print(f"[live-apply] search hotkey → {self.search_hotkey!r}")
            elif k == "prompt_mode_enabled":
                self.prompt_mode_enabled = bool(
                    self.settings.get("prompt_mode_enabled", False))
                self.active_mode = "prompt" if self.prompt_mode_enabled else (
                    self.active_mode if self.active_mode != "prompt" else None)
                print(f"[live-apply] Prompt mode → {self.prompt_mode_enabled}")
                self._push_island_bar_state()
            elif k == "model":
                new = self.settings.get("model", self.model_name)
                if new != self.model_name:
                    print(f"[live-apply] model switch → {new!r}")
                    self.set_model(new)
            elif k == "resource_saver":
                # Resource Saver toggled live: swap the loaded STT model between the
                # forced tiny.en (saver ON) and the user's configured model (saver
                # OFF), mirroring how a "model" change is hot-applied — but WITHOUT
                # persisting (the saved "model" stays the user's real choice; only
                # what's in memory changes). The streaming worker / AI warm-up read
                # the flag at use-time, so they need no live action here.
                target = self._effective_model()
                if target != self._loaded_model_name:
                    print(f"[live-apply] resource_saver → reloading model {target!r}")
                    threading.Thread(
                        target=self._reload_effective_model, daemon=True,
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
            elif k == "autostart":
                desired = bool(self.settings.get("autostart", True))
                if not autostart.set_enabled(desired):
                    self.settings.set("autostart", bool(autostart.is_enabled()))
            elif k == "cpu_threads":
                print("[live-apply] cpu_threads saved — applies on the next "
                      "model load")
        except Exception as e:
            print(f"live-apply of {key!r} failed:", e)

    def _send_webui(self, obj, timeout=0.6):
        """Send one command to the web window's listener. True on success."""
        import json as _json
        import socket as _socket
        try:
            payload = dict(obj)
            # The command server rejects unauthenticated requests. Internal
            # refresh/paste calls must carry the same per-session credential.
            payload["token"] = getattr(self, "_cmd_token", "")
            with _socket.create_connection(("127.0.0.1", self.WEBUI_PORT),
                                           timeout=timeout) as s:
                s.sendall((_json.dumps(payload) + "\n").encode("utf-8"))
                return True
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
        """Paste-latest hotkey (default Ctrl+Option+V). PASTE ONLY — pastes the MOST
        RECENT transcript straight into wherever your cursor is. It no longer opens
        History (owner v9: paste and History are now two SEPARATE actions — opening
        the window is the History hotkey, default Ctrl+Option+H). No popup, no forced
        mode, no window activation, no yellow flash — just the text, plus a clear
        island message when there is nothing to paste or the paste can't land. (The
        tray "Re-paste last" item calls this too.)"""
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
                landed = False
                try:
                    landed = self._paste(text)   # silent: no mode flash on success
                except Exception as e:
                    print("quick-paste error:", e)
                if not landed:
                    # The text is on the clipboard but nothing editable was focused,
                    # so it didn't paste anywhere — say so plainly (the requested
                    # failure feedback) instead of silently doing nothing.
                    self._quick_status("Click a text field, then press the paste key")
            finally:
                self.busy = False

        threading.Thread(target=_work, daemon=True).start()

    def on_open_history(self):
        """History hotkey (default Ctrl+Option+H) — OPEN HISTORY ONLY (owner v9).
        Brings the main window to the History tab: restores it if minimized,
        focuses it if already open, opens it if closed, and lifts it above the
        other Mumble windows. Never pastes — that's the paste-latest hotkey's job.
        Decoupled so the two concepts can never be confused again."""
        threading.Thread(target=self._show_history_page, daemon=True).start()

    def _quick_status(self, msg):
        """A brief island message for the Ctrl+Option+V recall — used ONLY for the
        nothing-to-paste / can't-paste cases. It's the island's quiet hint state, not
        a flash, so it never interrupts an ordinary paste."""
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
        because the moment it does the OS clears the highlight in the source app
        (the owner's "pulling up the Deck breaks the highlight" bug). If the user
        had text selected, it rides along in the command and lands as a temporary
        top item in the Deck they can Convert / tick / run. _grab_selection_quiet
        releases the held hotkey modifiers, restores the prior clipboard, and
        returns '' when nothing is selected — so an ordinary Deck-open (tray menu,
        no selection) is completely unaffected."""
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
            threading.Thread(target=_retry, daemon=True).start()

    def on_search_hotkey(self):
        with self.lock:
            if self.paused:
                return
            if self.busy:
                return
            if self.recording:
                self._search_requested = True
                self.busy = True
                threading.Thread(target=self._safe_stop, daemon=True).start()
            else:
                # Not recording: search the current selection instantly; with no
                # selection, start a voice-search recording (stop it with either
                # hotkey and the transcript is searched). _grab_selection_quiet
                # releases held modifiers and restores the prior clipboard.
                self.busy = True

                def _do_search():
                    try:
                        selected = self._grab_selection_quiet()
                        if selected:
                            self._open_search(selected)
                        else:
                            self._search_requested = True
                            self._safe_start()
                    finally:
                        self.busy = False

                threading.Thread(target=_do_search, daemon=True).start()

    # Web-search engines for the search hotkey. {q} is the URL-encoded query.
    SEARCH_ENGINES = {
        "google": "https://www.google.com/search?q={q}",
        "perplexity": "https://www.perplexity.ai/search?q={q}",
        "brave": "https://search.brave.com/search?q={q}",
    }

    # Browser executables for the Settings → Browser choice. Each maps to the
    # standard install locations; a missing browser falls back to the default.
    BROWSERS = {
        "edge": [
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ],
        "chrome": [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ],
        "brave": [
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            os.path.expanduser("~/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
        ],
        "safari": [
            "/Applications/Safari.app/Contents/MacOS/Safari",
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
        engine = (self.settings.get("search_engine", "google") or "google").lower()
        template = self.SEARCH_ENGINES.get(engine, self.SEARCH_ENGINES["google"])
        self.open_in_browser(template.format(q=query))

    def _sane_press_hotkey(self, key, default):
        """Read a SINGLE-PRESS hotkey from settings and refuse a bare modifier.

        A lone Ctrl/Option/Shift/Cmd as a tap hotkey fires on every press of that
        modifier — this is exactly the 'Ctrl alone starts Mumble' bug. The web-UI
        binding-capture path saves straight through set_setting (no validate()),
        so this is the last line of defence and runs on EVERY registration: if the
        stored spec decayed to a bare modifier, heal it back to the default and
        persist the fix so the bad value can never come back."""
        spec = bindings.normalize(self.settings.get(key, default) or default)
        if bindings.is_bare_modifier(spec):
            print(f"[hotkey-heal] {key}={spec!r} is a bare modifier — "
                  f"reverting to {default!r} (a lone modifier would fire on "
                  f"every press)")
            spec = bindings.normalize(default)
            try:
                self.settings.set(key, spec)
            except Exception:
                pass
        return spec

    def _register_hotkey(self):
        self.hotkey = self._sane_press_hotkey("hotkey", "ctrl+option+d")
        bindings.unregister(self._hk_main)
        self._hk_main = bindings.register_hotkey(self.hotkey, self.on_hotkey)

    def _register_quick(self):
        self.quick_hotkey = self._sane_press_hotkey(
            "quick_paste_hotkey", "ctrl+option+v")
        bindings.unregister(self._hk_quick)
        self._hk_quick = None
        try:
            self._hk_quick = bindings.register_hotkey(
                self.quick_hotkey, self.on_quick_paste
            )
        except Exception as e:
            print("quick-paste hotkey error:", e)
            raise  # let _apply_settings_change log the failure with context — don't
                   # swallow it (the UI was reporting "active now" for a dead key)

    def _register_history(self):
        self.history_hotkey = self._sane_press_hotkey(
            "history_hotkey", "ctrl+option+h")
        bindings.unregister(self._hk_history)
        self._hk_history = None
        try:
            self._hk_history = bindings.register_hotkey(
                self.history_hotkey, self.on_open_history
            )
        except Exception as e:
            print("history hotkey error:", e)
            raise  # surface the failure (see _register_quick)

    def _register_search(self):
        self.search_hotkey = self._sane_press_hotkey(
            "search_hotkey", "ctrl+option+s")
        bindings.unregister(self._hk_search)
        self._hk_search = None
        try:
            self._hk_search = bindings.register_hotkey(
                self.search_hotkey, self.on_search_hotkey
            )
        except Exception as e:
            print("search hotkey error:", e)
            raise  # surface the failure (see _register_quick)

    # ===================================================== actions for the UI
    def apply_hotkey(self, hk):
        hk = bindings.normalize(hk)
        ok, msg = bindings.validate(hk)
        if not ok:
            return False, msg
        self.hotkey = hk
        self.settings.set("hotkey", hk)
        try:
            self._register_hotkey()
        except Exception as e:
            return False, f"Couldn't register: {e}"
        return True, f"Saved — your hotkey is now {bindings.pretty(hk)}."

    def apply_quick_paste_hotkey(self, hk):
        hk = bindings.normalize(hk)
        ok, msg = bindings.validate(hk)
        if not ok:
            return False, msg
        self.quick_hotkey = hk
        self.settings.set("quick_paste_hotkey", hk)
        try:
            self._register_quick()
        except Exception as e:
            return False, f"Couldn't register: {e}"
        return True, f"Saved — paste-latest is now {bindings.pretty(hk)}."

    def apply_history_hotkey(self, hk):
        hk = bindings.normalize(hk)
        ok, msg = bindings.validate(hk)
        if not ok:
            return False, msg
        self.history_hotkey = hk
        self.settings.set("history_hotkey", hk)
        try:
            self._register_history()
        except Exception as e:
            return False, f"Couldn't register: {e}"
        return True, f"Saved — the Deck opens with {bindings.pretty(hk)}."

    def apply_search_hotkey(self, hk):
        hk = bindings.normalize(hk)
        ok, msg = bindings.validate(hk)
        if not ok:
            return False, msg
        self.search_hotkey = hk
        self.settings.set("search_hotkey", hk)
        try:
            self._register_search()
        except Exception as e:
            return False, f"Couldn't register: {e}"
        return True, f"Saved — search is now {bindings.pretty(hk)}."

    def set_search_engine(self, engine):
        """Choose which site the web-search hotkey opens (google/perplexity/brave)."""
        engine = (engine or "google").strip().lower()
        if engine not in self.SEARCH_ENGINES:
            engine = "google"
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
        result = {}

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
                if stream is not None:
                    try:
                        stream.stop()
                    except Exception:
                        pass
                    try:
                        stream.close()
                    except Exception:
                        pass

        t = threading.Thread(target=check, daemon=True)
        t.start()
        t.join(timeout=4.0)
        if not result:
            print(f"mic {idx!r} validation timed out (driver hang)")
            return False, "That mic didn't respond in time — kept your previous one."
        if not result.get("ok"):
            print(f"mic {idx!r} validation failed: {result.get('err')}")
            return False, "That mic couldn't be opened — kept your previous one."
        self.settings.set("mic_device", idx)
        return True, "Saved — Mumble is listening to this mic."

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
        """Resolve the local model from automatic hardware detection and language.
        `load=False` only
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

    def _effective_model(self):
        """The model name that should ACTUALLY be loaded into WhisperModel right now.

        Resource Saver Mode (owner v8) forces the lightest model — "tiny.en" — to
        cut CPU/battery, regardless of the configured "model". The saved "model" is
        left untouched (so turning Resource Saver off restores the user's choice).
        Everywhere a model name is handed to WhisperModel goes through here."""
        if self.settings.get("resource_saver"):
            return "tiny.en"
        return self.settings.get("model", "small.en")

    def _try_load(self, name):
        """Load `name` into self.model. Returns True on success. On FAILURE the previous
        self.model is left untouched (the assignment only lands if WhisperModel succeeds),
        so a failed switch keeps the app working on the model it already had."""
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
                    new_model = WhisperModel(
                        name,
                        device="cpu",
                        compute_type=self.settings.get("compute_type", "int8"),
                        cpu_threads=int(threads),
                    )
                else:
                    raise
            # Release the old model BEFORE swapping to avoid two models coexisting
            # in RAM (small.en ~500MB + medium.en ~1.5GB = ~2GB memory pressure).
            old_model = None
            with self.lock:
                old_model = self.model
                self.model = new_model
            # Explicitly drop the old model reference so the GC can free it and
            # CTranslate2 can release its native thread pool / memory.
            del old_model
            self._loaded_model_name = name  # track what's actually in self.model
            self._warm_model(new_model)
            return True
        except Exception as e:
            print(f"model '{name}' load error:", e)
            return False

    def _ensure_local_model(self):
        """Guarantee a local faster-whisper model is loaded, loading it ON DEMAND
        if cloud mode deferred it at boot. Called from the local transcribe path
        when `self.model is None` — normally because cloud failed and we're
        falling back. Returns True if a model is available afterwards."""
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
        lang = "en" if self.model_name.endswith(".en") else \
            self.settings.get("language", "en")
        with self._tx_lock:
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

    def _reload_effective_model(self, on_done=None):
        """Reload self.model to whatever _effective_model() now returns — used by
        the live Resource Saver toggle to swap tiny.en <-> the configured model.

        Unlike _load_model this does NOT persist a model name: the user's saved
        "model" is unchanged (only the resource_saver flag flipped), and
        self.model_name stays the CONFIGURED choice. On failure self.model still
        holds the previously working model (_try_load only swaps on success), so
        dictation keeps working — we just leave _loaded_model_name as-is."""
        target = self._effective_model()
        was_recording = self.recording
        self._set_state("loading")
        ok = self._try_load(target)
        if not ok:
            print(f"[resource_saver] reload of {target!r} failed — kept "
                  f"{self._loaded_model_name!r}")
        if was_recording or self.recording:
            self._set_state("listening")  # don't clobber an active recording
        else:
            self._idle()
        if on_done:
            on_done(ok, target)

    def _ai_cfg(self):
        """Active LLM engine config. Cerebras is the recommended (default) provider.
        Alternate providers (DeepSeek, Groq, OpenAI) are supported but slower.
        The key is MANDATORY and user-supplied — it lives in local settings.json only
        (never in source/git); there is no built-in/default key."""
        provider = self.settings.get("llm_provider", "cerebras")
        if provider not in ("cerebras", "openrouter"):
            provider = "cerebras"
        info = ai.PROVIDERS.get(provider, ai.PROVIDERS["cerebras"])
        key = (self.settings.get(info["key_setting"], "") or "").strip()
        # Fall back to THIS provider's default model, not Cerebras's — a user who
        # blanked the model field for anthropic/openai/local previously got
        # "gpt-oss-120b" sent to that endpoint (a guaranteed 400/404).
        model = (self.settings.get(info["model_setting"], "")
                 or info.get("default_model") or "gpt-oss-120b")
        url = info["url"]
        if provider == "local":
            # The Local LLM endpoint is user-configurable (Ollama, LM Studio,
            # llama.cpp — any OpenAI-compatible server).
            base = (self.settings.get("local_url", "") or "http://localhost:11434").rstrip("/")
            if base.endswith("/v1"):
                base = base[:-3]
            url = base + "/v1/chat/completions"
        if provider == "anthropic":
            models_url = ai.ANTHROPIC_MODELS_URL
        elif provider == "cerebras":
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
        }

    def _ai_key(self):
        return self._ai_cfg()["key"]

    def set_llm_provider(self, provider, model="", key=None, local_url=None):
        """Switch the AI engine (Settings → AI Provider). Saves the provider,
        model, key, and local URL, then returns (ok, message)."""
        if provider not in ("cerebras", "openrouter"):
            return False, "Unknown provider."
        self.settings.set("llm_provider", provider)
        info = ai.PROVIDERS[provider]
        if model:
            self.settings.set(info["model_setting"], model)
        if key is not None:
            self.settings.set(info["key_setting"], (key or "").strip())
            self.pro_key_failed = False
        if local_url is not None and provider == "local":
            self.settings.set("local_url", (local_url or "").strip()
                              or "http://localhost:11434")
        cfg = self._ai_cfg()
        label = info["label"].split(" (")[0]
        return True, f"Using {label} — model '{cfg['model']}'."

    def test_provider(self):
        """Live connection test for the active provider. Returns (ok, message)."""
        cfg = self._ai_cfg()
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
        return False, ("Connection failed — is the local server running?"
                       if cfg["provider"] == "local"
                       else "Connection failed — check your internet.")

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

    def _capture_conversation_quiet(self):
        """ITEM 5 (owner 2026-06-29) — capture a WHOLE AI conversation, not just one
        reply. Sends Cmd+A then Cmd+C to the FOCUSED chat (ChatGPT / Claude /
        Gemini / any web or desktop chat) so the ENTIRE thread is grabbed in one
        shot — INCLUDING messages scrolled out of view, because Select-All reaches
        the full document, not only the visible viewport. Restores the prior
        clipboard. Returns the captured transcript ('' if nothing came back). This
        is the robust, app-agnostic alternative to a brittle per-site DOM scraper:
        it works anywhere Cmd+A / Cmd+C work, with no fake/never-wired button.

        DEEP-004: No sentinel is written to the clipboard — we compare the
        post-copy clipboard against the pre-copy value instead.  Additionally,
        non-text clipboard content (images, files, RTF) is detected via
        NSPasteboard types and preserved by skipping the capture entirely."""
        if self.clipboard:
            self.clipboard.pause()
        try:
            before = pyperclip.paste()
        except Exception:
            before = ""

        # DEEP-004: detect non-text clipboard content and preserve it.
        # pyperclip returns "" for non-text content (images, files, RTF).
        # If the NSPasteboard has types but none of them are text types,
        # the clipboard holds non-text data — skip the capture so we never
        # overwrite it with Cmd+A / Cmd+C.
        if not before:
            try:
                from AppKit import NSPasteboard
                pb = NSPasteboard.generalPasteboard()
                available_types = pb.types()
                if available_types is not None and len(available_types) > 0:
                    text_type_ids = {
                        "public.utf8-plain-text",
                        "NSStringPboardType",
                        "public.text",
                        "NSPasteboardTypeString",
                    }
                    has_text = any(t in text_type_ids for t in available_types)
                    if not has_text:
                        # Non-text clipboard (image / file / RTF) — leave it alone.
                        if self.clipboard:
                            self.clipboard.resume(skip_current=True)
                        return ""
            except Exception:
                pass

        text = ""
        try:
            # DEEP-008: use 'command' on macOS instead of 'windows'.
            if sys.platform == "darwin":
                mods_to_release = ("ctrl", "alt", "shift", "command")
            else:
                mods_to_release = ("ctrl", "alt", "shift", "windows")
            for mod in mods_to_release:
                try:
                    keyboard.release(mod)
                except Exception:
                    pass
            keyboard.send("command+a")
            time.sleep(0.06)        # let the select-all settle before copying
            keyboard.send("command+c")
            # A whole conversation can be large; poll a touch longer than the
            # single-selection grab, but still break the instant the copy lands.
            got = before
            deadline = time.time() + 0.7
            while True:
                time.sleep(0.02)
                try:
                    got = pyperclip.paste() or ""
                except Exception:
                    got = ""
                if got != before or time.time() >= deadline:
                    break
            if got and got != before:
                text = got.strip()
        except Exception as e:
            print("conversation capture error:", e)
        if before:  # never restore empty (would destroy a non-text clipboard)
            self._set_clipboard(before)
        if self.clipboard:
            try:
                self.clipboard._last_text = pyperclip.paste() or ""
            except Exception:
                pass
            try:
                self.clipboard.resume(skip_current=True)
            except Exception as e:
                print("clipboard resume error after capture:", e)
        return text

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

    def _grab_selection_quiet(self):
        """Copy the CURRENT selection (hover-over) and return it, or '' if nothing is
        actually selected. Restores the prior clipboard. Used so that saying a mode while
        text is highlighted feeds that text in as strict, direct context.

        DEEP-002: No sentinel is written to the clipboard — we compare the
        post-copy clipboard against the pre-copy value instead.  This avoids
        clipboard corruption when the grab times out or the clipboard holds
        non-text content."""
        if self.clipboard:
            self.clipboard.pause()
        try:
            before = pyperclip.paste()
        except Exception:
            before = ""
        sel = ""
        try:
            # DEEP-008: use 'command' on macOS instead of 'windows'.
            if sys.platform == "darwin":
                mods_to_release = ("ctrl", "alt", "shift", "command")
            else:
                mods_to_release = ("ctrl", "alt", "shift", "windows")
            for mod in mods_to_release:
                try:
                    keyboard.release(mod)
                except Exception:
                    pass
            keyboard.send("cmd+c")
            # Poll for the copy to land instead of a flat 120ms sleep — Cmd+C
            # usually completes in 10–30ms, so the fixed wait was pure latency on
            # the context-gather path (owner: "context gathering takes too long").
            # Break the instant the clipboard differs from before; if nothing was
            # selected the clipboard stays unchanged and we settle at the old cap
            # — so this is never slower than before, and ~3-10× faster on a hit.
            got = before
            deadline = time.time() + 0.13
            while True:
                time.sleep(0.012)
                try:
                    got = pyperclip.paste() or ""
                except Exception:
                    got = ""
                if got != before or time.time() >= deadline:
                    break
            if got and got != before:
                sel = got.strip()
        except Exception as e:
            print("selection grab error:", e)
        if before:  # Don't restore empty — it would destroy non-text clipboard
            # Confirmed restore (retries) so the user's clipboard is never left
            # holding the grabbed selection.
            self._set_clipboard(before)
        if self.clipboard:
            try:
                self.clipboard._last_text = pyperclip.paste() or ""
            except Exception:
                pass
            self.clipboard.resume(skip_current=True)
        return sel

    def _gather_context(self, det_mode, clip_count, mode_active):
        """Decide what context to feed the AI and how strictly. Returns (context, strict).

        Reply's source (owner directive 2026-06-13) — HIGHLIGHTED text first, the
        most recent clipboard item as the fallback. This gives Reply a clear,
        distinct purpose: reply to whatever you've selected on screen, or to what
        you last copied. Other explicit modes may use a highlighted selection.
        1) clip_count > 0 (Reply mode): highlighted selection → else latest clip.
        2) Otherwise, if an explicit mode is active and the user has text
           highlighted with the mouse → that SELECTION is the source, used directly.
        3) Else nothing — no clipboard is ever sent unasked."""
        if clip_count:
            # Reply mode: prefer the user's HIGHLIGHTED selection (the thing they're
            # looking at); fall back to the latest clipboard item.
            if mode_active:
                sel = self._grab_selection_quiet()
                if sel:
                    return f"[Reference] (highlighted selection)\n{sel}", False
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
        prefs=None,
        context_strict=False,
        cfg=None,
        prompt_cfg=None,
        invocation_snapshot=None,
        route_decision=None,
        expected_feature=None,
        expected_lane=None,
    ):
        """Send to the AI, routed by lane:

        - prompt → the Prompt Architect constitution (sent every call).
        - email/reply → focused per-mode prompts (high reasoning).
        - foreign → annotate Arabic/Islamic terms with slash candidates, AI disambiguates.
        - text → minimal POLISH (cheap), with no mode inference.

        When *cfg* / *prompt_cfg* are provided they are used as the frozen AI
        config for the duration of this call (VAL-CROSS-011: in-flight key
        survives a mid-call settings change).  When absent the config is read
        fresh from settings (legacy / redo paths that are effectively a new
        request).

        Returns (mode, output) or raises."""
        if isinstance(invocation_snapshot, processing_route.ProcessingInputSnapshot):
            route_decision = invocation_snapshot.route
            info = ai.PROVIDERS.get(route_decision.provider) or {}
            cfg = {"key": route_decision.api_key, "model": route_decision.model,
                   "url": info.get("url", ""), "provider": route_decision.provider}
            prompt_cfg = cfg
            name = invocation_snapshot.user_name
            prefs = invocation_snapshot.prompt_prefs_dict()
            context = invocation_snapshot.context
            context_strict = invocation_snapshot.context_strict
        actual_feature = mode_hint if mode_hint in {"prompt", "email", "reply"} else "dictation"
        if expected_feature != actual_feature or expected_lane != mode_hint:
            raise processing_route.HostedRouteBlocked(route_decision)
        if cfg is None:
            cfg = self._ai_cfg()
        key, model, url = cfg["key"], cfg["model"], cfg["url"]
        req = (request or raw or "").strip()
        # For prompt/email/reply, use the FULL raw transcript as content.
        # det_request (keyword-stripped) can lose text when the keyword is at the
        # end of a long sentence. The AI is smart enough to understand intent from
        # the full text — the keyword just tells us which lane to route to.
        content = raw if mode_hint in ("prompt", "email", "reply",
                                       "convert") else req

        if mode_hint == "prompt":
            # AI Mode: prompting can run on its own, stronger provider.
            if prompt_cfg is None:
                prompt_cfg = cfg
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
            annotated = islamic_terms.annotate_foreign(content)
            return self._collect(
                ai.cerebras_foreign(annotated, name, key, context, model, url=url,
                                    languages=(list(invocation_snapshot.foreign_languages)
                                               if isinstance(invocation_snapshot, processing_route.ProcessingInputSnapshot)
                                               else self.settings.get("foreign_languages")),
                                    route_decision=route_decision),
                "foreign",
            )

        # Plain text is always a polish operation. Explicit modes are routed
        # above; the retired held-key MODE/CONF second-opinion path must never
        # reinterpret ordinary dictation or expose clipboard context.
        polish_input = content
        print("Status: Polishing mode active - no mode checks performed.")
        # Medium-confidence vocabulary candidates: tag "heard//Term" so the AI
        # can decide from context (high-confidence ones were already auto-fixed
        # in _process). On AI failure the offline fallback uses the untouched
        # `raw`, so annotations can never leak into pasted output.
        try:
            terms = (invocation_snapshot.vocabulary_terms
                     if isinstance(invocation_snapshot, processing_route.ProcessingInputSnapshot)
                     else self.settings.get("vocabulary_terms", []))
            if terms:
                annotated = formatting.annotate_vocab_terms(polish_input, terms)
                if annotated != polish_input:
                    polish_input = annotated
                    print("[vocab] annotated medium-confidence terms for AI")
        except Exception as e:
            print("vocab annotate error:", e)
        # The no-truncation polisher keeps short inputs to one call and chunks
        # long dictations without silently cutting off the tail.
        try:
            out, truncated = ai.polish_text(
                polish_input,
                key,
                model,
                url=url,
                aggressiveness=(invocation_snapshot.polish_aggressiveness
                                if isinstance(invocation_snapshot, processing_route.ProcessingInputSnapshot)
                                else self.settings.get("polish_aggressiveness", "Light")),
                route_decision=route_decision,
            )
        except Exception as e:
            print("polish API failed, using offline builder:", e)
            return "text", self._builder(
                raw, "text", raw, invocation_snapshot=invocation_snapshot
            )[1]
        if truncated:
            self._notify(
                "Mumble",
                "That dictation was very long — it was polished in parts and a "
                "section may still be trimmed. The raw transcript is saved in the Deck.",
            )
        return "text", out

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

    def _builder(
        self, raw, det_mode="text", det_request="", fmt=True,
        invocation_snapshot=None,
    ):
        """Offline floor — mode-aware. Routes the locally-detected mode through
        formatting.process so the offline path still produces the right KIND of
        output and the right label/island colour."""
        if isinstance(invocation_snapshot, processing_route.ProcessingInputSnapshot):
            name = invocation_snapshot.user_name
            modes = invocation_snapshot.modes_dict()
            fmt = invocation_snapshot.format_enabled
        else:
            name = self.settings.get("user_name", "")
            modes = self.settings.get("modes", {})
        try:
            if det_mode == "foreign":
                # Offline floor for Foreign mode: clean up, then apply the local Islamic
                # correction map (gated to clearly-Islamic context inside the helper).
                base = (formatting.format_transcript(raw, commands=False)
                        if fmt else raw.strip())
                return "foreign", islamic_terms.correct_islamic_terms(base)
            mode, out = formatting.process(
                raw, modes, name, fmt, commands=False
            )
            if out and out.strip():
                return mode, out
        except Exception as e:
            print("offline builder error:", e)
        # Spoken-command scanning is OFF in the live app (commands=False):
        # "new line"/"bullet point" etc. type literally; the AI polish owns
        # structure. (formatting.process above is the dormant mode router and
        # is not reached for plain text.)
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
            if not bool(self.settings.get("local_llm_enabled", False)):
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
        self, raw, det_mode, context, invocation_snapshot=None,
    ):
        """On-device smart-mode shaping via the local LLM, when one is resident. The
        MIDDLE tier between the cloud lane and the deterministic builder: it lifts
        prompt/email/reply from templated to fluent output WITHOUT the cloud. Returns
        cleaned text, or None to fall through to the builder (it never hard-blocks)."""
        if det_mode not in local_engine.SMART_LANES:
            return None
        if not local_engine.local_llm_ready():
            return None
        try:
            prefs = (
                invocation_snapshot.prompt_prefs_dict()
                if isinstance(invocation_snapshot, processing_route.ProcessingInputSnapshot)
                else self.settings.get("prompt_prefs")
            )
            system, user, grammar = local_engine.build_local_request(
                det_mode, raw, prefs=prefs, context=context or ""
            )
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
        config_snap=None,
        route_decision=None,
        invocation_snapshot=None,
    ):
        """AI-driven generation. Lane A (plain text) → minimal polish; Lane B (an
        explicit Island/Deck mode) → focused/constitution path. (Material-as-context
        AI jobs moved to the Deck — see _run_deck_job; "context" is no longer a
        spoken trigger.) Returns (mode, text, used_offline)."""
        name = self.settings.get("user_name", "")
        prefs = self.settings.get("prompt_prefs")
        # Capture the AI config ONCE at the start of this dictation so that
        # an in-flight cloud request always uses the key it was launched with,
        # even if the user changes/deletes the key mid-call (VAL-CROSS-011).
        # _cloud_generate() receives this snapshot and never re-reads settings.
        cfg = self._ai_cfg()
        prompt_cfg = cfg
        key = cfg["key"]
        # Use snapshot config if provided (for settings-change isolation).
        pro_mode = config_snap["pro_mode"] if config_snap else self.settings.get("pro_mode", True)

        context, context_strict = self._gather_context(
            det_mode, clip_count, mode_active
        )
        # Conversation context: when Prompt Mode is active and a conversation
        # has been captured via "Capture chat", include it in the AI prompt so
        # the model understands what the user was discussing.
        if det_mode == "prompt":
            try:
                conv = self._conv_context_for_prompt()
                if conv:
                    context = conv + ("\n\n" + context if context else "")
            except Exception:
                pass
        if not isinstance(invocation_snapshot, processing_route.ProcessingInputSnapshot):
            invocation_snapshot = processing_route.snapshot_inputs(
                config_snap or self.settings,
                feature=(det_mode if det_mode in ("prompt", "email", "reply") else "dictation"),
                lane=det_mode, context=context,
                context_policy="reprocessing" if config_snap is None else "dictation",
                context_strict=context_strict,
                local_model_ready=local_engine.local_llm_ready(),
                route_decision=(route_decision if isinstance(route_decision, processing_route.RouteDecision) else None),
            )
        route_decision = invocation_snapshot.route
        info = ai.PROVIDERS.get(route_decision.provider) or {}
        cfg = {"key": route_decision.api_key, "model": route_decision.model,
               "url": info.get("url", ""), "provider": route_decision.provider}
        prompt_cfg = cfg
        key = route_decision.api_key
        name = invocation_snapshot.user_name
        prefs = invocation_snapshot.prompt_prefs_dict()
        context = invocation_snapshot.context
        context_strict = invocation_snapshot.context_strict
        if route_decision.ready:
            try:
                mode, out = processing_route.call_provider(
                    route_decision, self._cloud_generate,
                    raw,
                    name,
                    context,
                    det_mode,
                    det_request,
                    prefs=prefs,
                    context_strict=context_strict,
                    cfg=cfg,
                    prompt_cfg=prompt_cfg,
                    invocation_snapshot=invocation_snapshot,
                    expected_feature=(
                        det_mode if det_mode in {"prompt", "email", "reply"}
                        else "dictation"),
                    expected_lane=det_mode,
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
        llm_out = self._local_llm_generate(
            raw, det_mode, context, invocation_snapshot=invocation_snapshot
        )
        if llm_out and llm_out.strip():
            return det_mode, llm_out, True

        # Pipeline orchestrator: the four-stage merged-model pipeline
        # (punctuation → grammar → formatting → cleanup).  Stages 2 & 3 are
        # optional and skip gracefully when their GGUF models are absent;
        # Stages 1 & 4 are the always-available floor.
        try:
            from pipeline import get_orchestrator
            orch = get_orchestrator()
            pipeline_config = {
                "user_name": invocation_snapshot.user_name,
                "prompt_prefs": invocation_snapshot.prompt_prefs_dict(),
                "modes": invocation_snapshot.modes_dict(),
                "format_enabled": invocation_snapshot.format_enabled,
                "primary_language": invocation_snapshot.primary_language,
            }
            result = orch.process(raw, lane=det_mode, config=pipeline_config)
            if result and result.strip():
                return det_mode, result, True
        except ImportError:
            pass  # pipeline package not installed — fall back to builder
        except Exception as e:
            print("pipeline processing failed, using builder:", e)

        mode, out = self._builder(
            raw, det_mode, det_request,
            invocation_snapshot.format_enabled,
            invocation_snapshot=invocation_snapshot,
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
        if not self.settings.get("pro_mode", True):
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
            self._pro_fallback_notice("startup key check: rejected")
        elif ok is True:
            self.pro_key_failed = False

    def set_pro_mode(self, v):
        self.settings.set("pro_mode", bool(v))

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
        modes = dict(self.settings.get("modes", {}))
        modes[mode] = bool(value)
        self.settings.set("modes", modes)

    def set_prompt_mode(self, v):
        """THE BIG SHIFT: set the sticky Prompt toggle (the web Settings switch).
        Now a thin wrapper over the general active-mode selector so Prompt and the
        island mode deck share one source of truth."""
        return bool(self.set_active_mode("prompt" if v else None) == "prompt")

    def toggle_prompt_mode(self):
        """Flip the Prompt toggle — kept for callers that toggle Prompt directly."""
        return self.set_prompt_mode(self.active_mode != "prompt")

    # ---- ITEM 4: the island mode deck (Prompt / Email / Reply) -----------------
    def island_modes(self):
        """The processing modes the island deck offers, as (key, label) pairs — the
        REAL cloud lanes only (prompt/email/reply)."""
        labels = {"prompt": "Prompt", "email": "Email", "reply": "Reply"}
        want = self.settings.get("island_modes") or ["prompt", "email", "reply"]
        out = [(k, labels[k]) for k in want if k in labels]
        return out or [("prompt", "Prompt")]

    def _push_island_bar_state(self):
        """Push the live mode-deck snapshot (enabled modes, the active mode, whether
        the Foreign toggle shows + its state) to the island bar."""
        if not self.island:
            return
        try:
            self._tk_schedule(
                self.island.set_bar_state,
                self.island_modes(),
                self.active_mode,
                bool(self.settings.get("foreign_mode", False)),
                bool(self.settings.get("island_foreign_toggle", False)),
            )
        except Exception as e:
            print("island bar-state push skipped:", e)

    def set_active_mode(self, key):
        """Select (or clear) the island-forced processing mode. `key` is a lane key
        (prompt/email/reply) or None to return to plain dictation. Tapping the
        already-active mode clears it (a natural toggle). Persists the choice, keeps
        prompt_mode_enabled in sync, lights the island in the mode's colour, and
        refreshes the web Settings."""
        valid = {k for k, _ in self.island_modes()}
        if key is not None and key not in valid:
            key = None
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
        self._send_webui_async({"cmd": "refresh", "what": "settings"})
        return on

    def set_prompt_pref(self, key, value):
        prefs = dict(self.settings.get("prompt_prefs", {}))
        prefs[key] = value
        self.settings.set("prompt_prefs", prefs)

    def set_user_name(self, name):
        self.settings.set("user_name", (name or "").strip())

    def set_vocabulary(self, vocab, terms=None):
        """Persist the personal vocabulary: `terms` is the default workflow
        (just the correct words); `vocab` is the advanced wrong=right mapping."""
        clean = {
            str(k).strip(): str(v).strip()
            for k, v in (vocab or {}).items()
            if str(k).strip() and str(v).strip()
        }
        self.settings.set("vocabulary", clean)
        if terms is not None:
            self.settings.set(
                "vocabulary_terms",
                [str(t).strip() for t in terms if str(t).strip()],
            )

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
            return False
        self.settings.set("autostart", bool(value))
        return True

    def test_microphone(self, on_level, on_done):
        def run():
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
        """Put a clipboard-history image back onto the system clipboard via
        PyObjC NSPasteboard (DEEP-001: no osascript) so the user can paste it
        anywhere with Cmd+V — not just open the saved PNG. Returns True only
        if the image actually landed on the clipboard, so the caller never
        blind-fires Cmd+V (which would paste stale content)."""
        if not path or not os.path.exists(path):
            self._notify("Image unavailable",
                         "That image file is missing — it may have been cleared.")
            return False
        try:
            if self.clipboard:
                self.clipboard.pause()
            try:
                from AppKit import NSImage, NSPasteboard
                img = NSImage.alloc().initWithContentsOfFile_(path)
                if img is None:
                    raise RuntimeError("NSImage could not load the file")
                pb = NSPasteboard.generalPasteboard()
                pb.clearContents()
                pb.writeObjects_([img])
            finally:
                if self.clipboard:
                    # skip_current so the monitor doesn't re-ingest the image Mumble
                    # just wrote as a fresh clipboard-history item (every other paste
                    # path resumes with skip_current=True for the same reason).
                    self.clipboard.resume(skip_current=True)
            self._notify("Copied", "Image on the clipboard — paste it with Cmd+V.")
            return True
        except Exception as e:
            # NSPasteboard/NPImage failed (bad/corrupt image, sandbox, or no
            # Automation permission) — tell the user instead of silently
            # blind-firing Cmd+V.
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
            import subprocess
            subprocess.run(["open", branding.HISTORY_TXT])
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
            os.path.dirname(branding.INSTALL_DIR), "MumbleLite", "Open Mumble Lite.command"
        )
        if not os.path.exists(lite):
            self._notify("Mumble Lite not found",
                         "The MumbleLite folder isn't next to the main app.")
            return
        try:
            import subprocess
            subprocess.run(["open", lite])
        except Exception as e:
            self._notify("Couldn't start Mumble Lite", str(e))
            return
        self._quit()

    def open_data_folder(self):
        try:
            import subprocess
            subprocess.run(["open", branding.DATA_DIR])
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
        if self.paused and self.recording:
            # Stop any active recording so audio doesn't accumulate in memory
            # while paused (the hotkey is blocked by self.paused, so the user
            # can't stop it otherwise). (CTRL-010)
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
        """Tray menu update check without blocking the UI/tray callback."""
        if not update.update_channel_enabled():
            self._notify("Mumble Updates",
                         "The signed update channel is not configured in this build.")
            return
        if getattr(self, "_update_check_in_progress", False):
            self._notify("Mumble Updates", "An update check is already running.")
            return
        self._update_check_in_progress = True

        def finish(ok, manifest, error):
            self._update_check_in_progress = False
            if error is not None:
                print("manual update check failed:", error)
                self._notify("Mumble", "Couldn't check for updates right now.")
            elif ok and manifest:
                self._update_manifest = manifest
                ver = manifest.get("version", "?")
                notes = manifest.get("notes", "")
                msg = f"Mumble v{ver} is available."
                if notes:
                    msg += f" {notes}"
                self._notify("Update available", msg)

                def ask():
                    import tkinter.messagebox as mb
                    if mb.askyesno("Mumble Update",
                                   f"Download and install v{ver}?"):
                        self._on_update_install()
                self._tk_schedule(ask)
            elif manifest is None:
                # Distinct from "up to date": the CHECK failed (no URL /
                # offline / bad manifest) — saying "up to date" is a lie.
                self._notify("Mumble", "Couldn't check for updates right now.")
            else:
                self._notify("Mumble", "You're up to date!")

        def check_in_background():
            try:
                ok, manifest = update.check_for_update()
                error = None
            except Exception as exc:
                ok, manifest, error = False, None, exc
            self._tk_schedule(finish, ok, manifest, error)

        try:
            threading.Thread(target=check_in_background, daemon=True).start()
        except Exception as exc:
            self._update_check_in_progress = False
            print("couldn't start update check:", exc)
            self._notify("Mumble", "Couldn't check for updates right now.")

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

    def _open_web_ui(self):
        """Launch the pywebview main-window process. Returns True if launched/already
        open, False if pywebview is unavailable so the caller falls back to classic.
        (owner v8: the History-flyout 'popup' start mode is gone — there is one
        window now; Ctrl+Option+H navigates IT to History.)

        Note: first_run is intentionally NOT cleared here — the web UI's own
        onboarding wizard clears it via Api.finish_onboarding."""
        try:
            import importlib.util
            if importlib.util.find_spec("webview") is None:
                return False
            proc = getattr(self, "_webui_proc", None)
            if proc is not None and proc.poll() is None:
                # Already running — don't spawn a second process; just reveal it.
                self._send_webui({"cmd": "show"})
                return True
            import subprocess

            shell = os.path.join(branding.INSTALL_DIR, "webui_shell.py")
            exe = sys.executable or "python"

            env = dict(os.environ)
            env["MUMBLE_START"] = "app"
            # macOS seam: spawn the shell as its own session leader so _quit can
            # signal the whole process group (killpg) — Windows kills the tree with
            # taskkill /T instead. Keep start_new_session here.
            self._webui_proc = subprocess.Popen(
                [exe, shell], cwd=branding.INSTALL_DIR, env=env,
                start_new_session=True)  # own process group → killpg on quit
            return True
        except Exception as e:
            print("web UI launch failed, falling back to classic:", e)
            return False

    def _quit(self):
        import sys

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
                        raw = self._transcribe(partial_audio)
                        if raw and raw.strip():
                            self.history.add(raw.strip(), mode="text",
                                             duration=partial_duration,
                                             raw=raw.strip(),
                                             quality=self._take_quality())
                            print("[shutdown] saved partial dictation to history")
                except Exception as e:
                    print(f"[shutdown] partial dictation save error: {e}", file=sys.stderr)
        except Exception as e:
            print(f"[shutdown] partial dictation outer error: {e}", file=sys.stderr)

        # Make meeting capture durable before the process exits. Processing is
        # resumed from the saved WAV on the next launch.
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
            mgr = getattr(local_engine, '_MODEL_MANAGER', None)
            if mgr is not None and hasattr(mgr, 'stop'):
                mgr.stop()
                print("[shutdown] model process manager stopped")
        except Exception as e:
            print(f"[shutdown] backend cleanup error: {e}", file=sys.stderr)

        # ---- Cancel any in-flight model download ----
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
            print(f"[shutdown] download cancel error: {e}", file=sys.stderr)

        # --- Signal all daemon threads to stop (coordinated shutdown) ---
        self._shutting_down.set()

        # Join streaming worker thread
        if self._stream_worker_thread is not None:
            try:
                self._stream_worker_thread.join(timeout=2.0)
            except Exception as e:
                print(f"[shutdown] stream worker join error: {e}", file=sys.stderr)

        # Join command server thread
        if self._cmd_server_thread is not None:
            try:
                self._cmd_server_thread.join(timeout=2.0)
            except Exception as e:
                print(f"[shutdown] cmd server join error: {e}", file=sys.stderr)

        try:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
        except Exception as e:
            print(f"[shutdown] stream close error: {e}", file=sys.stderr)
        try:
            keyboard.unhook_all()
        except Exception as e:
            print(f"[shutdown] keyboard unhook error: {e}", file=sys.stderr)
        try:
            if self.icon is not None:
                self.icon.stop()
        except Exception as e:
            print(f"[shutdown] icon stop error: {e}", file=sys.stderr)
        # Tear down the web-UI window process we own (owner v7 lifecycle parity).
        # Before this, "Quit Mumble" from the menu bar killed only the controller
        # and ORPHANED the webui_shell.py pywebview host (plus its WebKit helper
        # processes), which kept running and holding the command port + memory. The
        # child is spawned as its own session leader (start_new_session=True), so
        # signal the whole group — SIGTERM first, SIGKILL as the fallback.
        #
        # DEEP-013: Track whether ANY kill method succeeded. If all fail, log a
        # prominent warning so the user/developer knows the webui is orphaned.
        try:
            proc = getattr(self, "_webui_proc", None)
            if proc is not None and proc.poll() is None:
                import signal
                webui_killed = False
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    webui_killed = True
                except Exception as e:
                    print(f"[shutdown] webui SIGTERM error: {e}", file=sys.stderr)
                    try:
                        proc.terminate()
                        webui_killed = True
                    except Exception as e2:
                        print(f"[shutdown] webui terminate error: {e2}", file=sys.stderr)
                try:
                    proc.wait(timeout=3)
                    webui_killed = True
                except Exception as e:
                    print(f"[shutdown] webui wait error: {e}", file=sys.stderr)
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                        webui_killed = True
                    except Exception as e2:
                        print(f"[shutdown] webui SIGKILL error: {e2}", file=sys.stderr)
                        try:
                            proc.kill()
                            webui_killed = True
                        except Exception as e3:
                            print(f"[shutdown] webui kill error: {e3}", file=sys.stderr)
                if not webui_killed:
                    print("[shutdown] WARNING: all webui kill methods failed — "
                          "webui_shell.py may still be running", file=sys.stderr)
        except Exception as e:
            print(f"[shutdown] webui teardown error: {e}", file=sys.stderr)
        try:
            # The island is now an in-process layered window (no separate
            # process/WebView2 to tear down); it dies with the Tk root below.
            if self.root is not None:
                self.root.quit()
                self.root.destroy()
        except Exception as e:
            print(f"[shutdown] root destroy error: {e}", file=sys.stderr)
        # Release the single-instance lock and FORCE the process to die. tkinter +
        # pystray + keyboard can leave non-daemon threads alive, so a normal return
        # would leave the process (and the lock port) hanging — which is exactly why
        # "close then reopen" failed: the next launch saw the port still held and
        # refused to start. os._exit guarantees the port frees so reopening works.
        try:
            if _LOCK_SOCK is not None:
                _LOCK_SOCK.close()
        except Exception as e:
            print(f"[shutdown] lock socket close error: {e}", file=sys.stderr)
        # Clean up the PID file so the next launch doesn't see a stale PID (PI-009).
        try:
            _pid_path = os.path.join(branding.DATA_DIR, "mumble.pid")
            if os.path.exists(_pid_path):
                os.remove(_pid_path)
        except Exception as e:
            print(f"[shutdown] pid file cleanup error: {e}", file=sys.stderr)
        # Flush and close the log file handle opened at module level — since
        # os._exit(0) bypasses Python's normal interpreter shutdown (atexit +
        # __del__), the log handle would otherwise stay open-and-unflushed,
        # potentially losing the last crash breadcrumb. Close it explicitly
        # right before the hard exit.
        try:
            _log.flush()
            _log.close()
        except Exception as e:
            print(f"[shutdown] log close error: {e}", file=sys.stderr)
        os._exit(0)

    # ---- Meeting Mode methods (owner 2026-06-29) ----
    def _meeting_start(self):
        """Start meeting recording. Vetoed if dictation is active."""
        recorder = getattr(self, "meeting_recorder", None)
        if recorder is None:
            return {"ok": False, "message": "Meeting recorder not ready."}
        if self.recording or self._processing:
            return {"ok": False, "message": "Dictation is active — stop it first."}
        if getattr(self, "meeting_recording", False):
            return {"ok": False, "message": "A meeting is already recording."}
        # Recheck and reserve microphone ownership under the same gate used by
        # on_hotkey. Dictation may have set busy before its worker flips the
        # steady-state recording flag.
        with self.lock:
            if self.busy or self.recording or self._processing:
                return {"ok": False,
                        "message": "Dictation is active - stop it first."}
            if getattr(self, "meeting_recording", False):
                return {"ok": False,
                        "message": "A meeting is already recording."}
            self.busy = True
        try:
            recorder.start()
            self.meeting_recording = True
            self._bump_feature("meeting")
            return {"ok": True, "recording": True}
        except Exception as e:
            print(f"[meeting] start failed: {e}")
            return {"ok": False, "message": str(e)}
        finally:
            with self.lock:
                self.busy = False

    def _meeting_stop(self, title=""):
        """Stop meeting recording and run processing in a background thread.
        Returns immediately with a 'processing' response; the actual
        transcription + diarisation + save runs on a daemon thread."""
        recorder = getattr(self, "meeting_recorder", None)
        if recorder is None:
            return {"ok": False, "message": "Meeting recorder not ready."}
        if not getattr(self, "meeting_recording", False):
            # Not recording, but maybe there's dangling state — try stop anyway
            return {"ok": False, "message": "No meeting is recording."}

        try:
            meeting_id = recorder.finish_capture(title)
            self.meeting_recording = False
        except Exception as e:
            self.meeting_recording = False
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
                        {"cmd": "refresh", "what": "meetings"}),
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
        if record.get("status") not in ("processing", "interrupted", "failed"):
            return {"ok": False, "message": "This meeting cannot be retried."}
        meeting.meeting_store.update_meeting(
            meeting_id, status="processing", error=None)
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

    def _restart(self):
        """Apply a pending update by launching the .command swap script."""
        parent = os.path.dirname(branding.INSTALL_DIR)
        script = os.path.join(parent, "apply_update.command")
        if os.path.exists(script):
            print(f"applying update via {script}")
            try:
                # Hand the swap script to Terminal without delaying shutdown.
                subprocess.Popen(["open", script], start_new_session=True)
            except Exception as e:
                print(f"failed to launch update script: {e}")
        else:
            print("restart requested but no apply_update.command found")
        # Now quit normally
        self._quit()

    def _register_input_bindings(self):
        """Register the startup bindings exactly once.

        On first-run macOS, creating a Quartz listener before TCC permission is
        granted can permanently stop that listener.  `_boot` therefore calls
        this only after trust is known, and the permission watcher calls it when
        a user grants access without needing to restart Mumble.
        """
        with self._input_bindings_lock:
            if self._input_bindings_registered:
                return
            self._input_bindings_registered = True
        for name, reg in (
            ("record", self._register_hotkey),
            ("paste-latest", self._register_quick),
            ("history", self._register_history),
            ("search", self._register_search),
        ):
            try:
                reg()
            except Exception as e:
                print(f"hotkey registration failed ({name}):", e)

    def _wait_for_accessibility_and_register(self):
        """Poll TCC off the UI thread and arm hooks as soon as access is granted."""
        while not self._shutting_down.wait(3.0):
            trusted = self._check_accessibility(prompt=False, notify=False)
            if trusted is True:
                print("[accessibility] permission granted — registering hotkeys")
                self._register_input_bindings()
                self._tk_schedule(
                    self._notify, "Mumble", "Accessibility granted — hotkeys are ready.")
                return
            if trusted is None:
                # The probe is unavailable; do not make that equivalent to a
                # denial. Try the listener once and let its own diagnostics win.
                self._register_input_bindings()
                return

    def _boot(self):
        self._set_state("loading")
        self._cleanup_orphan_audio()
        # Resource Saver forces the lightest model (tiny.en) at boot without
        # touching the saved "model" — see _effective_model().
        boot_model = self._effective_model()
        if self._cloud_transcription_on():
            # Cloud transcription is the ACTIVE mode → don't load the heavy local
            # faster-whisper model into RAM at boot (it's ~0.5–1.5 GB and would
            # sit unused). It loads lazily only if/when local is actually needed:
            # a cloud failure (see _ensure_local_model / _local_transcribe). This is the owner's
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
            # of returning early and leaving the app with no model AND no Ctrl+Option.
            fallback = "base.en"
            if boot_model != fallback and self._try_load(fallback):
                self._notify(
                    "Using the default model",
                    f"Couldn't load “{boot_model}”, so Mumble switched back "
                    f"to “{fallback}”. Pick another in Settings any time.",
                )
                # Only persist the fallback as the user's CONFIGURED model when the
                # failure was about their real choice. In Resource Saver mode the
                # boot model is the forced tiny.en — a failure there must not
                # overwrite the saved "model" the user picked.
                if not self.settings.get("resource_saver"):
                    self.model_name = fallback
                    self.settings.set("model", fallback)
            else:
                self._notify(
                    "Model didn't load",
                    "Mumble is open — choose a model in Settings to start dictating.",
                )
        # Probe/request TCC before constructing pynput listeners. A listener
        # created while access is denied can die and never recover, even after
        # the user flips the switch. This runs on the boot worker, never the UI.
        trusted = (self._check_accessibility(prompt=True)
                   if sys.platform == "darwin" else True)
        if trusted is False:
            threading.Thread(
                target=self._wait_for_accessibility_and_register,
                name="accessibility-hotkey-waiter", daemon=True).start()
        else:
            # True = permission present; None = probe unavailable, so fail open
            # and let the backend report any listener error.
            self._register_input_bindings()
        # The web window's command channel (paste / deck_job / record / status)
        self._start_cmd_server()
        try:
            # LaunchAgent repair is platform-specific; never call the Windows
            # port's legacy Run-key API from the Mac build.
            desired_autostart = bool(self.settings.get("autostart", True))
            actual_autostart = bool(autostart.is_enabled())
            if desired_autostart != actual_autostart:
                if not autostart.set_enabled(desired_autostart):
                    self.settings.set("autostart", bool(autostart.is_enabled()))
        except Exception as e:
            print("autostart repair failed:", e)
        # Warm the audio path: PortAudio's host-API init + device enumeration is
        # slow the first time and would otherwise land on the first hotkey press.
        # query_devices() forces that init now (cheaper + safer than opening a real
        # InputStream, which would flip the OS mic-in-use indicator) so the first
        # record opens a warm device (owner v6 startup readiness).
        try:
            t0 = time.time()
            sd.query_devices()
            print(f"[audio] portaudio init in {time.time() - t0:.2f}s")
        except Exception as e:
            print("[audio] portaudio warm skipped:", e)
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
        if self.settings.get("first_run", True):
            self.cmd_q.put("open")
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

    def run(self):
    
        self.root = tk.Tk()
        self.root.withdraw()
        try:
            # iconbitmap only supports .ico on Windows. On macOS, the application icon is
            # provided entirely by the .app bundle (Info.plist -> mumble.icns).
            pass
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
                on_foreign=self.toggle_island_foreign)
            self._push_island_bar_state()
        except Exception as e:
            print("island widget wiring skipped:", e)

        # Meeting Mode: create the recorder with transcribe_fn, settings,
        # and the island callback that marshals to the Tk thread.
        self.meeting_recorder = meeting.MeetingRecorder(
            transcribe_fn=self._transcribe,
            settings=self.settings,
            island_callback=self._meeting_island_cb)
        threading.Thread(
            target=self.meeting_recorder.recover_pending,
            name="meeting-recovery", daemon=True).start()

        self.icon = pystray.Icon(
            "mumble",
            self._tray_image(STATE_COLORS["loading"]),
            "Mumble — Loading…",
            menu=self._build_menu(),
        )
        # On macOS, pystray's run() loop MUST execute on the main thread, or it hangs.
        # run_detached() tells pystray to spawn a background thread for its own loop
        # and safely pipe updates back to the main thread, avoiding Tkinter conflicts.
        self.icon.run_detached()
        threading.Thread(target=self._boot, daemon=True).start()
        if self.settings.get("clipboard_enabled", True):
            self.clipboard.start()

        self._pump()
        self._push_island_bar_state()
        # `_boot` performs the non-blocking TCC prompt/check before it constructs
        # any pynput listener, then watches for first-run permission changes.
        # Handle Ctrl+C cleanly so the webui process tree and port are released.
        try:
            import signal
            signal.signal(signal.SIGINT, lambda *_: self._quit())
            signal.signal(signal.SIGTERM, lambda *_: self._quit())
        except Exception:
            pass
        self.root.mainloop()


_LOCK_SOCK = None


def _acquire_single_instance():
    """True if we're the only Mumble; False if another instance already holds the lock.

    Binds a fixed localhost port as the lock: a second instance can't bind the same
    port and exits. This is reliable across DIFFERENT Python interpreters (venv vs
    system) — the previous named-mutex approach let duplicates through, and duplicate
    instances fight over the mic + Ctrl+Option hotkey and can run stale in-memory code
    (e.g. an old instance still on the offline engine while a new one uses the AI).
    The socket is kept alive for the process lifetime and freed by the OS on exit.

    Crash recovery (VAL-CROSS-001): when the previous instance was force-killed
    the OS normally releases the port immediately, but edge cases (orphaned
    WKWebView subtree, OS socket-state lag) can make a re-bind fail. We retry with
    back-off so the launcher self-heals without a confusing "already running"
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
    socket doubles as the channel: HMAC challenge-response authentication
    (DEEP-003) so the token is never transmitted in plaintext and a passive
    listener or token-file reader cannot replay a valid open signal."""
    try:
        import socket
        import hmac

        token = ""
        try:
            with open(branding.cmd_token_path(), "r", encoding="utf-8") as f:
                token = f.read().strip()
        except OSError:
            token = ""
        with socket.create_connection(("127.0.0.1", 49517), timeout=2) as s:
            s.sendall(b"open\n")
            challenge = s.recv(32)
            if len(challenge) < 32:
                print("instance signal: challenge too short")
                return False
            response = hmac.new(token.encode("utf-8"), challenge, "sha256").digest()
            s.sendall(response)
        return True
    except Exception as e:
        print("could not signal the running instance:", e)
        return False


def _serve_instance_signals(app):
    """Accept 'open' signals from later launches on the lock socket (it is
    already listen()ing) and route them to the command queue. Uses HMAC
    challenge-response (DEEP-003) so the token is never transmitted in
    plaintext. Daemon thread — dies with the process; any error just ends
    the loop (lock stays held)."""
    def _loop():
        import hmac
        import secrets
        import socket as _socket
        # Timeout so accept() doesn't block the daemon thread forever —
        # without this, the signal server thread can't be interrupted
        # on shutdown (DEEP-005 regression).
        try:
            _LOCK_SOCK.settimeout(1.0)
        except Exception:
            pass
        while True:
            try:
                conn, _ = _LOCK_SOCK.accept()
                try:
                    data = conn.recv(128)
                    if not data or not data.startswith(b"open"):
                        continue
                    want = getattr(app, "_cmd_token", "") or ""
                    if not want:
                        # No token set — accept unconditionally
                        app.cmd_q.put("open")
                        continue
                    # Generate a fresh challenge for each connection
                    challenge = secrets.token_bytes(32)
                    conn.sendall(challenge)
                    response = conn.recv(64)
                    expected = hmac.new(
                        want.encode("utf-8"), challenge, "sha256").digest()
                    if hmac.compare_digest(response, expected):
                        app.cmd_q.put("open")
                    else:
                        print("instance 'open' signal rejected (bad token)")
                finally:
                    conn.close()
            except _socket.timeout:
                continue
            except Exception:
                return
    threading.Thread(target=_loop, daemon=True).start()


if __name__ == "__main__":
    if not _acquire_single_instance():
        # Hand the request to the running instance (opens its window) and
        # leave quietly — no "already running" popup.
        if not _signal_running_instance():
            # The lock port is held but NOTHING answered: a stuck/half-dead copy is
            # squatting on it, which would make EVERY relaunch silently do nothing
            # (the classic "it just won't open anymore"). Don't vanish — tell the
            # user how to clear it. (macOS only; harmless elsewhere.)
            if sys.platform == "darwin":
                try:
                    subprocess.run(
                        ["osascript", "-e",
                         'display dialog "Mumble looks like it is already running but '
                         'is not responding - a stuck copy may be holding on.\\n\\n'
                         'To reset it: open Terminal, paste this line and press Return:\\n\\n'
                         'pkill -f mumble_mac.py; pkill -f webui_shell.py\\n\\n'
                         'then open Mumble again." with title "Mumble" '
                         'buttons {"OK"} default button "OK" with icon caution'],
                        timeout=25, capture_output=True)
                except Exception:
                    pass
        sys.exit(0)
    _app = Mumble()
    # PI-009: Write the PID file so the installer (and other tooling) can
    # precisely target THIS process for shutdown instead of using fragile
    # pkill -f pattern matching. The file lives in DATA_DIR alongside settings.
    try:
        _pid_path = os.path.join(branding.DATA_DIR, "mumble.pid")
        with open(_pid_path, "w") as _pf:
            _pf.write(str(os.getpid()))
        # Register cleanup so the PID file is removed on normal exit. os._exit
        # bypasses atexit, but _quit() handles the hard-exit case via explicit
        # removal before calling os._exit(0).
        import atexit as _atexit
        _atexit.register(lambda: os.path.exists(_pid_path) and os.remove(_pid_path))
    except Exception:
        pass  # never block startup over a PID file write failure
    _serve_instance_signals(_app)
    _app.run()
