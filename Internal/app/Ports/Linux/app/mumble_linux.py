#!/usr/bin/env python3
"""
Mumble — local-first voice-to-text for Linux (Golden Black).

Tap the hotkey, speak, tap again. Audio is transcribed locally (faster-whisper,
beam search + VAD), then shaped by Cerebras cloud AI (gpt-oss-120b) when Pro Mode is
on, saved to history, and pasted at your cursor. Mumble also remembers what you copy
(clipboard history) and tracks word stats. A golden island shows what's happening.
Transcription runs on-device by default. Optional cloud transcription and AI
features send audio or text only when the user selects and configures those
providers. With Pro Mode off (or no key), processing stays local.
"""

import os
import queue
import re
import sys
import threading
import time
import urllib.error
import uuid

import branding

branding.ensure_dirs()

# Always tee stdout/stderr to the log file — not only when launched without a
# console (pythonw). Otherwise console runs leave the log stale and a crash is
# invisible after the fact. The Tee also writes to the real console when there is one.
try:
    # Builds before the privacy audit logged short transcript/search previews.
    # Remove both bounded generations once so clearing History also means those
    # legacy snippets are no longer retained in diagnostic files.
    _privacy_marker = branding.LOG_PATH + ".metadata-only-v1"
    if not os.path.exists(_privacy_marker):
        for _old_log in (branding.LOG_PATH, branding.LOG_PATH + ".1"):
            try:
                os.remove(_old_log)
            except OSError:
                pass
        try:
            _marker_fd = os.open(
                _privacy_marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(_marker_fd)
            branding.protect_private_path(_privacy_marker)
        except OSError:
            pass
    # Keep diagnostics bounded. A permanently appended log can otherwise grow
    # without limit on a long-lived autostart installation.
    try:
        if os.path.getsize(branding.LOG_PATH) > 5 * 1024 * 1024:
            os.replace(branding.LOG_PATH, branding.LOG_PATH + ".1")
    except FileNotFoundError:
        pass
    except OSError as e:
        print("log rotation skipped:", e, file=sys.__stderr__)
    _log = open(branding.LOG_PATH, "a", encoding="utf-8", buffering=1)
    branding.protect_private_path(branding.LOG_PATH)

    class _Tee:
        def __init__(self, *streams):
            self._streams = [s for s in streams if s is not None]
            primary = self._streams[0] if self._streams else None
            self.encoding = getattr(primary, "encoding", "utf-8")
            self.errors = getattr(primary, "errors", "replace")

        def write(self, data):
            for s in self._streams:
                try:
                    s.write(data)
                    s.flush()
                except Exception:
                    pass
            return len(data)

        def flush(self):
            for s in self._streams:
                try:
                    s.flush()
                except Exception:
                    pass

        def isatty(self):
            for stream in self._streams:
                try:
                    if bool(getattr(stream, "isatty", lambda: False)()):
                        return True
                except Exception:
                    pass
            return False

        def fileno(self):
            for stream in self._streams:
                try:
                    return stream.fileno()
                except (AttributeError, OSError, ValueError):
                    continue
            raise OSError("tee has no file descriptor")

        def writable(self):
            return True

    import datetime as _dt

    _log.write(f"\n===== launch {_dt.datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
    sys.stdout = _Tee(sys.__stdout__, _log)
    sys.stderr = _Tee(sys.__stderr__, _log)
except Exception:
    pass

# On a WAYLAND session, force GTK (the island, the tray, and — via inherited env —
# the web-UI host) onto XWayland. Native-Wayland GTK can't position an overlay,
# keep it above, or skip the taskbar (all client-controlled on Wayland), and some
# WebKitGTK/Mesa combos render the webview black under Wayland. XWayland gives the
# real island + a reliable window. This MUST be set before `import gi`. It does
# NOT affect global INPUT: bindings picks evdev by the real session type (evdev
# reads /dev/input directly), so push-to-talk still works where pynput-over-
# XWayland could not. Respect an explicit user override (setdefault).
#
# C-010: Before forcing GDK_BACKEND=x11, check that X11/XWayland is available on
# this system. On a pure Wayland system with no XWayland, forcing x11 would crash
# GTK. If X11 is missing, we log a warning and continue — the island may not render
# but dictation + hotkeys still work.
def _usable_x11_display(display=None):
    """Return whether an actual X11/XWayland display can be contacted.

    Finding the Xwayland executable is insufficient: Mumble does not launch a
    display server.  This probe is intentionally dependency-light so it runs
    before importing Gtk.
    """
    display = (display if display is not None
               else os.environ.get("DISPLAY") or "").strip()
    if not display:
        return False
    try:
        import shutil as _shutil
        import subprocess as _subprocess
        xdpyinfo = _shutil.which("xdpyinfo")
        if xdpyinfo:
            probe = _subprocess.run(
                [xdpyinfo, "-display", display],
                stdout=_subprocess.DEVNULL,
                stderr=_subprocess.DEVNULL,
                timeout=2.0,
            )
            return probe.returncode == 0
        if display.startswith(":"):
            display_no = display[1:].split(".", 1)[0]
            return (display_no.isdigit()
                    and os.path.exists(f"/tmp/.X11-unix/X{display_no}"))
    except Exception:
        return False
    return False


def _external_child_env():
    """Environment for user applications launched by Mumble.

    The controller intentionally pins its own Gtk/WebKit child to XWayland, but
    a browser, xdg-open target or notification is not part of Mumble and must
    retain the user's native Wayland backend. Preserve an explicit user setting;
    scrub only the backend this controller marked as self-injected.
    """
    env = os.environ.copy()
    if env.pop("MUMBLE_FORCED_GDK_BACKEND", None):
        env.pop("GDK_BACKEND", None)
    return env


_X11_AVAILABLE = False
if os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("GDK_BACKEND"):
    # A binary on PATH does not mean an XWayland server is running.  Forcing
    # GDK_BACKEND=x11 with DISPLAY unset (or pointing at a dead display) makes
    # Gtk fail to initialise and takes the island + web window down with it.
    # Verify the actual DISPLAY.  xdpyinfo is authoritative when installed;
    # otherwise a local X11 socket is the best dependency-free signal.
    _x11_ok = _usable_x11_display()
    if _x11_ok:
        os.environ["GDK_BACKEND"] = "x11"
        os.environ["MUMBLE_FORCED_GDK_BACKEND"] = "1"
        _X11_AVAILABLE = True
    else:
        _msg = ("Wayland detected but X11/XWayland is not available — "
                "the island overlay may not render. Dictation + hotkeys will still work.")
        print("[startup] " + _msg)
        _X11_AVAILABLE = False
        # Surface a clear desktop notification so the user knows WHY the island
        # isn't appearing — not a silent failure (VAL-LIN-PLAT-001).
        try:
            import subprocess as _sp
            import shutil as _su
            if _su.which("notify-send"):
                _sp.Popen(
                    ["notify-send", "-a", "Mumble", "-i", "dialog-warning",
                     "Mumble — Pure Wayland Detected", _msg[:200]],
                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                    env=_external_child_env())
        except Exception:
            pass
else:
    _X11_AVAILABLE = True  # Not Wayland, or GDK_BACKEND already set

# GTK is the Linux GUI toolkit — it hosts the island window and drives the main
# loop, replacing the Windows/macOS Tk main loop. Guarded so this module still
# imports where GTK is absent (CI / a non-Linux box running the offline suites).
try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, GLib
    HAVE_GTK = True
except Exception:  # pragma: no cover - GTK present on a real Linux desktop
    Gtk = GLib = None
    HAVE_GTK = False


class _GlibRoot:
    """Minimal Tk-root stand-in backed by GLib/GTK.

    The controller marshals every island/window call onto the GUI thread via
    self.root.after(...) and tears down with self.root.quit()/destroy(). On Linux
    the GUI thread is GTK's, so this shim maps those calls onto GLib timers + the
    GTK main loop — letting the controller's scheduling code stay identical to
    Windows/macOS while the island is a native GTK window."""

    def __init__(self):
        self._exit = threading.Event()

    def after(self, ms, func, *args):
        def _cb():
            try:
                func(*args)
            except Exception:
                pass
            return False  # one-shot, like Tk's after()

        if GLib is not None:
            return GLib.timeout_add(max(0, int(ms)), _cb)
        # Headless fallback (gi/GTK failed to import but the app still came up via
        # evdev/pynput + the cmd server): without this, the self-rescheduling
        # _pump runs ONCE and the command queue (quit/open/restart) never drains.
        # A daemon Timer keeps the pump alive so the app stays controllable.
        import threading as _th
        t = _th.Timer(max(0.0, int(ms) / 1000.0), _cb)
        t.daemon = True
        t.start()
        return t

    def after_cancel(self, source_id):
        """Cancel a previously scheduled GLib timeout or headless Timer."""
        if source_id is None:
            return
        if GLib is not None:
            try:
                GLib.source_remove(int(source_id))
            except Exception:
                pass
            return
        try:
            source_id.cancel()
        except (AttributeError, RuntimeError):
            pass

    def withdraw(self):
        pass

    def iconbitmap(self, *a, **k):
        pass

    def quit(self):
        self._exit.set()
        if Gtk is not None:
            try:
                Gtk.main_quit()
            except Exception:
                pass

    def destroy(self):
        pass

    def mainloop(self):
        if Gtk is not None:
            Gtk.main()
        else:  # headless fallback: stay alive so hotkeys/tray still function
            import time as _t
            while not self._exit.is_set():
                _t.sleep(1.0)


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
import model_authority
import processing_route
import copy
import autostart
import bindings  # unified keyboard+mouse binding layer (record/re-paste/search/mode key)
import foreign_boost  # local (offline) Foreign-Mode phonetic term correction
import formatting
import islamic_terms  # Foreign mode: slash-candidate annotation for Arabic/Islamic terms
import local_engine  # cloud-dominance routing gate (cloud-primary-when-key, local degrade)
import meeting  # Meeting mode: record, transcribe, diarise meetings
import recording_limits
import transcription  # optional cloud STT (advanced); local faster-whisper is default
import update
from branding import MODE_LABELS, STATE_COLORS, C
from clipboard import Clipboard, read_clipboard_text
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
from overlay_linux import Island
from settings import Settings
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
        # Probe once for a pyperclip clipboard backend (xclip/xsel/wl-clipboard/
        # qt/gtk). On a headless or minimal Linux box none exists, so clipboard
        # writes fail and a blind Ctrl+V would paste stale content. Conservative:
        # only the explicit "no mechanism" error marks it unavailable; any other
        # error assumes a backend exists so we never wrongly suppress a paste.
        try:
            import pyperclip as _pc
            try:
                _pc.paste()
                self._pyperclip_ok = True
            except _pc.PyperclipException:
                self._pyperclip_ok = False
                print("clipboard: no pyperclip backend found (install xclip/xsel "
                      "or wl-clipboard) — text paste will be limited.")
            except Exception:
                self._pyperclip_ok = True
        except Exception:
            self._pyperclip_ok = True
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
        # The model name ACTUALLY loaded into self.model (vs model_name, which is
        # the user's CONFIGURED choice). They differ in Resource Saver mode, where
        # tiny.en is forced live without touching the saved choice — the live
        # resource_saver toggle uses this to know whether a reload is needed.
        self._loaded_model_name = None
        self.hotkey = self.settings.get("hotkey", "ctrl+windows")
        self.quick_hotkey = self.settings.get("quick_paste_hotkey", "ctrl+alt+v")
        self.history_hotkey = self.settings.get("history_hotkey", "ctrl+alt+d")
        self.search_hotkey = self.settings.get("search_hotkey", "ctrl+alt+f")
        self.web_search_hotkey = self.settings.get(
            "web_search_hotkey", "ctrl+alt+s")
        self._web_search_lock = threading.RLock()
        self._pending_web_searches = {}
        self._binding_lock = threading.RLock()
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
        self.active_mode = self.settings.get("island_active_mode", "") or (
            "prompt" if self.prompt_mode_enabled else None)
        if self.active_mode == "prompt" and not self.prompt_mode_enabled:
            self.prompt_mode_enabled = True

        # Legacy mode-key bookkeeping — kept for backward compat, cleared on start.
        self._mode_key_down = False
        self._mode_active = False
        self._mode_windows = []
        self._rec_start = 0.0
        self._hk_mode = None
        self._active_mode_start = None  # snapshot for per-utterance consistency
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
        self._quitting = False
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
        # Serialises meeting start/stop/finalisation.  The command server handles
        # clients concurrently, so a boolean alone cannot protect the transition
        # between "checked" and "recorder.start()/finish_capture() complete".
        self._meeting_lock = threading.Lock()
        self.state = "loading"

        # --- latency optimizations ---
        self._tx_lock = threading.Lock()  # serialize model.transcribe (defensive)
        self._paste_lock = threading.Lock()  # serialize _paste (non-reentrant clipboard)
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
        self._hk_web_search = None

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
        # Linux: libnotify (notify-send) is the reliable desktop-notification path
        # and works even with no tray host (e.g. GNOME without the AppIndicator
        # extension). Fall back to the tray icon's own notify().
        try:
            import shutil
            import subprocess
            if shutil.which("notify-send"):
                subprocess.Popen(
                    ["notify-send", "-a", "Mumble", "-i", branding.ICON_PNG,
                     str(title), str(msg)[:200]],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=_external_child_env())
                return
        except Exception:
            pass
        try:
            if self.icon:
                self.icon.notify(str(msg)[:200], title)
        except Exception:
            pass

    def _boost_priority(self):
        """Boost this process to high priority during transcription.
        Returns the old priority so it can be restored. On Linux a normal user
        can only LOWER priority (raising niceness needs CAP_SYS_NICE), so there is
        no safe unprivileged boost — this is a no-op, matching the macOS port."""
        return None

    def _restore_priority(self, old_priority):
        """Restore process priority to its previous value (no-op on Linux)."""
        pass

    def _check_input_permissions(self):
        """Check Linux /dev/input accessibility and input-group membership.

        On Linux the `keyboard` library reads /dev/input/event* directly via
        evdev. Without read access the hotkeys silently fail — the user gets no
        feedback. This check surfaces a clear notification when the permissions
        aren't right so the user knows to `sudo usermod -aG input $USER` and
        re-login (VAL-LIN-PLAT-003)."""
        issues = []
        # 1) /dev/input readability — the keyboard library opens event devices
        #    here; if none are readable by the current user, hotkeys are dead.
        try:
            _readable = any(
                bindings._linux_event_is_readable(_dev)
                for _dev in bindings._linux_event_paths("kbd"))
            if not _readable:
                issues.append(
                    "Cannot read /dev/input/event* — global hotkeys will not work.\n"
                    "Run:  sudo usermod -aG input $USER  then log out and back in.")
        except Exception:
            pass

        # 2) input group membership — the standard way to grant evdev access on
        #    systemd-based distros. Even if /dev/input is readable today, the
        #    user may not be in the group (e.g. a udev rule grants temporary
        #    access). Warn so they future-proof.
        try:
            import grp as _grp
            _input_grp = _grp.getgrnam("input")
            if _input_grp and os.geteuid() != 0:
                import pwd as _pwd
                _username = _pwd.getpwuid(os.geteuid()).pw_name
                # grp.gr_mem contains login NAMES, not numeric UIDs.  Include
                # both real/effective primary groups as well as supplementary
                # groups; omitting the primary gid produced false warnings on
                # systems where `input` is the user's primary group.
                _gids = set(os.getgroups())
                _gids.add(os.getgid())
                _gids.add(os.getegid())
                _is_member = (_username in _input_grp.gr_mem
                              or _input_grp.gr_gid in _gids)
                if not _is_member:
                    issues.append(
                        "You are not in the 'input' group — hotkeys may stop "
                        "working after a system update.\n"
                        "Run:  sudo usermod -aG input $USER  then log out and back in.")
        except Exception:
            pass

        # 3) Key injection is separate from hotkey capture. Pasting and selection
        # capture need wtype/xdotool/ydotool or write access to uinput. The old
        # check covered only /dev/input reads, so hotkeys could work while every
        # Ctrl+V/C command failed later with no actionable diagnosis.
        try:
            import shutil as _shutil
            _session = bindings._session_type()
            if _session == "wayland":
                _tool_ok = bool(
                    _shutil.which("wtype") or _shutil.which("ydotool"))
            else:
                _tool_ok = bool(
                    _shutil.which("xdotool") or _shutil.which("ydotool"))
            _uinput_ok = any(os.access(path, os.W_OK) for path in (
                "/dev/uinput", "/dev/input/uinput"))
            if not _tool_ok and not _uinput_ok:
                issues.append(
                    "Cannot inject paste/copy shortcuts — install wtype "
                    "(Wayland) or xdotool (X11), or grant access to /dev/uinput.")
        except Exception:
            pass

        if issues:
            _title = "Mumble — Input Permission Issue"
            _body = "\n\n".join(issues)
            print("[permissions] " + _body)
            # Use the same notify-send path as _notify() so it works even on
            # headless-friendly setups without a tray icon.
            try:
                import subprocess as _sp
                import shutil as _su
                if _su.which("notify-send"):
                    _sp.Popen(
                        ["notify-send", "-a", "Mumble", "-i", "dialog-warning",
                         _title, _body[:200]],
                        stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                        env=_external_child_env())
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
                  f"slept; discarding stale buffer and resetting")
            with self.lock:
                self.frames = []
                self._recorded_samples = 0
                # Reject any pre-sleep chunk that completes later. Replace the
                # idle event so old work cannot signal the fresh session.
                self._stream_session_id = (
                    getattr(self, "_stream_session_id", 0) + 1)
                self._stream_results = []
                self._stream_processed_samples = 0
                self._stream_inflight_samples = 0
                self._stream_idle = threading.Event()
                self._stream_idle.set()
        self._last_audio_cb_time = now

        # Bound ordinary dictation to ten minutes.  This prevents an accidental
        # all-day press from exhausting memory and keeps a cloud WAV below the
        # supported providers' direct-upload limit. Stop outside PortAudio's
        # callback thread because closing a stream in its callback can deadlock.
        recorded = max(0, int(getattr(self, "_recorded_samples", 0)))
        remaining = recording_limits.DICTATION_MAX_SAMPLES - recorded
        if remaining <= 0:
            block = None
        else:
            block = indata[:remaining].copy()
            self.frames.append(block)
            self._recorded_samples = recorded + len(block)

        if (self._recorded_samples >= recording_limits.DICTATION_MAX_SAMPLES
                and not self._dictation_limit_triggered):
            self._dictation_limit_triggered = True
            threading.Thread(
                target=self._stop_at_dictation_limit,
                name="mumble-dictation-limit", daemon=True).start()

        if block is None or len(block) == 0:
            return
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
            if not self.recording or self.busy:
                return
            self.busy = True
        self._notify(
            "Recording limit reached",
            "Mumble stopped at the 10-minute dictation limit and is "
            "transcribing what it captured.")
        self._safe_stop()

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
        session_id = self._stream_session_id
        consumed_blocks = 0  # cursor into self.frames (callback blocks)
        while not self._stream_done.is_set():
            if getattr(self, '_stream_worker_thread', None) is not threading.current_thread():
                break
            if self._stream_done.wait(0.1):
                break
            if not self.recording:
                break
            with self.lock:
                if (session_id != self._stream_session_id
                        or self._stream_done.is_set() or not self.recording):
                    break
                blocks = list(self.frames[consumed_blocks:])
            new_samples = sum(len(b) for b in blocks)
            if new_samples < int(SAMPLE_RATE * chunk_secs):
                continue  # not enough NEW AUDIO yet (samples vs samples)
            audio_chunk = np.concatenate(blocks, axis=0).flatten()
            consumed_blocks += len(blocks)
            idle = self._stream_idle
            with self.lock:
                if (session_id != self._stream_session_id
                        or self._stream_done.is_set() or not self.recording):
                    break
                self._stream_inflight_samples = len(audio_chunk)
                idle.clear()
            partial_text = None
            try:
                partial_text = self._local_transcribe(audio_chunk, beam=1)
            except Exception as e:
                print("stream worker chunk failed (tail will cover it):", e)
            finally:
                # A chunk finishing after key-release is still valid work for
                # this recording. Publish text + coverage atomically so stop can
                # reuse it; only a session change makes the result stale.
                with self.lock:
                    same_session = session_id == self._stream_session_id
                    if same_session and partial_text is not None:
                        if partial_text.strip():
                            self._stream_results.append(partial_text.strip())
                        self._stream_processed_samples += len(audio_chunk)
                    if same_session:
                        self._stream_inflight_samples = 0
                        idle.set()
            if partial_text is None or self._stream_done.is_set():
                break

    # ========================================================= meeting mode
    def _meeting_start(self):
        """Start meeting recording. Vetoed if dictation is active."""
        recorder = getattr(self, "meeting_recorder", None)
        if recorder is None:
            return {"ok": False, "message": "Meeting recorder not ready."}
        with self._meeting_lock:
            with self.lock:
                if self.recording or self.busy or self._processing:
                    return {"ok": False, "message":
                            "Dictation is active — stop it first."}
                if self.meeting_recording:
                    return {"ok": False, "message":
                            "A meeting is already recording."}
                # Reserve the microphone before opening it. Concurrent command
                # handlers and dictation hotkeys now see the meeting immediately.
                self.meeting_recording = True
            pause_wake = getattr(self, "_pause_wake_word", None)
            if callable(pause_wake):
                pause_wake("meeting")
            try:
                recorder.start()
                self._bump_feature("meeting")
                return {"ok": True, "recording": True}
            except Exception as e:
                with self.lock:
                    self.meeting_recording = False
                resume_wake = getattr(self, "_resume_wake_word", None)
                if callable(resume_wake):
                    resume_wake("meeting")
                print(f"[meeting] start failed: {e}")
                return {"ok": False, "message": str(e)}

    def _meeting_stop(self, title=""):
        """Stop meeting recording and run processing in a background thread.
        Returns immediately with a 'processing' response; the actual
        transcription + diarisation + save runs on a daemon thread."""
        recorder = getattr(self, "meeting_recorder", None)
        if recorder is None:
            return {"ok": False, "message": "Meeting recorder not ready."}
        with self._meeting_lock:
            with self.lock:
                if not self.meeting_recording:
                    return {"ok": False, "message": "No meeting is recording."}
                # Reserve the stop so two command connections cannot both
                # finalize and enqueue the same durable meeting.
                self.meeting_recording = False
            try:
                meeting_id = recorder.finish_capture(title)
            except Exception as e:
                resume_wake = getattr(self, "_resume_wake_word", None)
                if callable(resume_wake):
                    resume_wake("meeting")
                print(f"[meeting] stop failed: {e}")
                return {"ok": False, "message": str(e)}
        resume_wake = getattr(self, "_resume_wake_word", None)
        if callable(resume_wake):
            resume_wake("meeting")
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
                    "Split the recording and import each part separately.")
        except Exception as e:
            return {"ok": False, "message": str(e)}

        def _bg():
            try:
                meeting_id = meeting.process_audio_file(
                    path, self._transcribe, self.settings, title="",
                    on_saved=lambda _meeting_id: self._send_webui_async(
                        {"cmd": "refresh", "what": "meetings"}))
                if meeting_id:
                    self._send_webui_async({"cmd": "refresh", "what": "meetings"})
                else:
                    self._notify(
                        "Meeting import failed",
                        "Mumble could not decode or save that audio file.")
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
        if self.recording or self.busy or self._processing or self.meeting_recording:
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

        threading.Thread(target=_bg, name="meeting-retry", daemon=True).start()
        return {"ok": True, "processing": True, "meeting_id": meeting_id}

    def _meeting_island_cb(self, state, timer, speaker_count):
        """MeetingRecorder island callback — marshals to Tk/GTK thread via _tk_schedule.
        Called from the recording timer thread and processing thread."""
        if state == "limit_reached":
            if self.island is not None:
                self._tk_schedule(self.island.set_state, "transcribing")
                self._tk_schedule(
                    self.island.hint,
                    "Four-hour limit reached · saving meeting")
            threading.Thread(
                target=self._meeting_stop_at_limit,
                name="meeting-limit-stop", daemon=True).start()
            return
        if state == "capture_error":
            if self.island is not None:
                self._tk_schedule(self.island.set_state, "transcribing")
                self._tk_schedule(
                    self.island.hint,
                    "Recording stopped early · saving captured audio")
            threading.Thread(
                target=self._meeting_stop_after_capture_error,
                name="meeting-capture-error-stop", daemon=True).start()
            return
        if self.island is None:
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
            self._tk_schedule(self.island.hint, f"Identifying {speaker_count} speakers\u2026")
        elif state == "done":
            self._tk_schedule(self.island.flash, "meeting", pasted=False)
            self._tk_schedule(self.island.hint, "Meeting saved")

    def _meeting_stop_at_limit(self):
        """Finalize, surface, and process a meeting capped by the recorder."""
        if not getattr(self, "meeting_recording", False):
            return
        result = self._meeting_stop("")
        if result.get("ok"):
            self._notify(
                "Meeting saved",
                "Mumble reached the four-hour meeting limit. The audio is safe "
                "and transcription is running.")
            self._send_webui_async({"cmd": "refresh", "what": "meeting_limit"})
        else:
            self._notify(
                "Meeting could not be finalized",
                result.get("message", "Unknown meeting recording error."))

    def _meeting_stop_after_capture_error(self):
        """Finalize and surface a meeting stopped by an audio integrity error."""
        if not getattr(self, "meeting_recording", False):
            return
        result = self._meeting_stop("")
        if result.get("ok"):
            self._notify(
                "Meeting recording stopped early",
                "Mumble detected an audio capture problem. The contiguous audio "
                "recorded before it is safe, and transcription is running.")
            self._send_webui_async(
                {"cmd": "refresh", "what": "meeting_capture_error"})
        else:
            self._notify(
                "Meeting could not be finalized",
                result.get("message", "Unknown meeting recording error."))

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
        # Meeting capture owns the microphone. Dictation must not open a
        # competing stream and corrupt both recordings.
        if getattr(self, "meeting_recording", False):
            self.busy = False
            return
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
        rec_state = ("search" if getattr(self, "_search_requested", False)
                     else "listening")
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
        with self.lock:
            self._stream_results = []
            self._stream_processed_samples = 0
            self._stream_inflight_samples = 0
            self._stream_session_id = (
                getattr(self, "_stream_session_id", 0) + 1)
            self._stream_idle = threading.Event()
            self._stream_idle.set()
            self._stream_done.clear()
        # Resource Saver Mode: skip the live (streaming) worker entirely. Decoding
        # chunks during the recording is exactly the extra background compute the
        # saver exists to avoid. Leaving `_stream_worker_thread` as None makes the
        # stop-path join a no-op, and an empty `_stream_results` makes `_process`
        # take its authoritative single full pass over the whole audio — so
        # transcription still works end to end, it just runs once at stop.
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
                info = ai.PROVIDERS.get(decision.provider) or {}
                ai.cerebras_warm(decision.api_key, decision.model,
                                 url=info.get("url", ""),
                                 route_decision=decision)
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
            self._mode_active = True
            with self.lock:
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
                if self.busy or self.paused:
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
                threading.Thread(target=self._safe_stop, daemon=True).start()

    def _watch_mode_key(self):
        """THE BIG SHIFT: there is no held mode key to watch any more, so this
        safety-net poller is retired (a no-op). It used to re-sync the "armed"
        indicator to the real Right-Shift state every ~200ms; the island's Prompt
        accent now follows the sticky `prompt_mode_enabled` toggle, which has no
        stuck-key failure mode. Kept as a no-op so the boot call stays valid and
        the idle loop costs nothing."""
        if self.root is not None:
            try:
                self.root.after(1000, self._watch_mode_key)
            except Exception:
                pass

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

    def _maybe_apply_suggestion(self, det_mode, det_request, clip_count, raw):
        """Implements the chip flow: after the AI suggested a mode we missed, if the user
        re-presses the mode key and says (basically) just that mode word, reuse the
        PREVIOUS transcript as the content — 'press the key, say the mode, resend'."""
        if not self._suggested_mode:
            return det_mode, det_request, clip_count
        if (time.time() - self._suggest_ts) > 45:
            self._suggested_mode = ""
            return det_mode, det_request, clip_count
        if det_mode in ("prompt", "email", "list", "reply", "foreign", "convert"):
            if len((det_request or "").split()) <= 1 and self._last_raw:
                det_request = self._last_raw
                clip_count = max(clip_count, 1)
                self._suggested_mode = ""
        return det_mode, det_request, clip_count

    def _match_keyword_template(self, window_words, det_request):
        """Scan window words for a known prompt keyword. If matched, return
        the template fragment. Returns '' if no keyword matched."""
        keywords = self.settings.get("prompt_keywords", {})
        if not keywords:
            return ""
        window_text = " ".join(window_words).strip().lower()
        for kw, template in keywords.items():
            if kw.lower() in window_text:
                print(f"[keyword] matched '{kw}' → injecting template")
                return template
        return ""

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
    def _offer_mode_pick(self, raw, ai_guess, clean):
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
        if self.island is None:
            # Headless: no chip surface — just paste the polished default.
            threading.Thread(target=self._finalize_text, args=(clean,),
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
            self._finalize_text(clean)

        threading.Thread(target=_fade_fallback, daemon=True).start()

    def _reprocess(self, raw, mode):
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
                raw, mode, raw, clip, mode != "text", None, None)
            if not out:
                self._idle()
                return
            entry = self.history.add(out, m, 0.0, raw=raw)
            if entry is None:
                self._notify("History save failed",
                             "The converted result could not be saved to History.")
            else:
                try:
                    self.stat_store.record(entry["words"], 0.0, m)
                except Exception as e:
                    print("reprocess stats error:", e)
            self._send_webui_async({"cmd": "refresh", "what": "history"})
            landed = self._paste(out)
            print(f"[reprocess] mode={m!r} chars={len(out)} saved={entry is not None}")
            if self.island:
                self._tk_schedule(self.island.flash, m,
                                  offline=used_offline, pasted=bool(landed))
            self._set_state("idle")
        except Exception as e:
            print("reprocess error:", e)
            self._idle()
        finally:
            self.busy = False

    def _finalize_text(self, clean):
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
            entry = self.history.add(clean, "text", 0.0)
            if entry is None:
                self._notify("History save failed",
                             "The result could not be saved to History.")
            else:
                try:
                    self.stat_store.record(entry["words"], 0.0, "text")
                except Exception:
                    pass
            self._send_webui_async({"cmd": "refresh", "what": "history"})
            landed = self._paste(clean)
            if self.island:
                self._tk_schedule(self.island.flash, "text", pasted=bool(landed))
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
            # The worker takes this same lock before claiming a chunk. It may
            # finish one existing claim, but cannot start another after stop.
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
        # range for this session, so the final pass never duplicates that audio.
        idle = self._stream_idle
        if not idle.wait(timeout=STREAM_DRAIN_TIMEOUT):
            print("stream decode did not drain within 25s — cancelling dictation")
            self._notify(
                "Transcription stalled",
                "The local transcription engine stopped responding. Restart "
                "Mumble before trying again.")
            with self.lock:
                self._stream_session_id += 1
                self.frames = []
            self._search_requested = False
            self._idle()
            return
        worker = self._stream_worker_thread
        if worker and worker.is_alive():
            worker.join(timeout=0.05)  # cleanup only; inference is already idle
        audio = np.concatenate(self.frames, axis=0).flatten()
        self.frames = []
        duration = len(audio) / SAMPLE_RATE
        if duration < self.settings.get("min_seconds", 0.3):
            self._search_requested = False
            self._idle()
            return
        # Snapshot the mode-key window for this utterance; close any window still open.
        with self.lock:
            for wn in self._mode_windows:
                if wn[1] is None:
                    wn[1] = duration
            mode_active = self._mode_active and self.settings.get(
                "mode_button_enabled", True
            )
            windows = [tuple(w) for w in self._mode_windows]
        self._process(audio, duration, mode_active, windows)

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
        if not self._tx_lock.acquire(timeout=25):
            print("transcribe lock busy >25s — skipping to avoid a stuck pipeline")
            self._restore_priority(old_priority)
            return ("", []) if want_words else ""
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
            segs, _ = model.transcribe(
                audio,
                language=lang,
                beam_size=beam,
                vad_filter=True,
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
                log_prob_threshold=-1.0,
                word_timestamps=want_words,
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
            self._tx_lock.release()
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

    def _process(self, audio, duration, mode_active=False, windows=None):
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
            "local_only_mode": self.settings.get("local_only_mode", False),
            "llm_provider": self.settings.get("llm_provider", "cerebras"),
            "english_only": self.settings.get("english_only", True),
            "foreign_mode": self.settings.get("foreign_mode", False),
            "foreign_languages": self.settings.get("foreign_languages"),
            "format_enabled": self.settings.get("format_enabled", True),
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
            **processing_route.capture_text_provider_settings(self.settings),
        }
        try:
            self._processing = True
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
                    f"{processed_samples} samples / "
                    f"{len(streaming_base)} transcript chars"
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
                self._idle()
                return

            # Personal vocabulary, two passes before anything downstream sees
            # the transcript: (1) explicit wrong=right pairs (advanced), then
            # (2) term matching — high-confidence phonetic/fuzzy hits on the
            # user's term list are corrected automatically; medium-confidence
            # candidates are offered to the polish AI later (annotate_vocab_terms).
            try:
                raw = formatting.apply_vocabulary(
                    raw, _snap["vocabulary"]
                )
                raw = formatting.apply_vocabulary_terms(
                    raw, _snap["vocabulary_terms"]
                )
            except Exception as e:
                print("vocabulary error:", e)

            # ---- mode-key button state (per-utterance, reset on start) ----
            self._active_keyword_template = (
                ""  # always initialized, even for plain text
            )

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

            window_words = None
            via_convert = None

            # A highlighted selection + any mode word acts as the source
            # Stash the pre-conversion transcript so the suggestion chip can resend it.
            self._last_raw = raw

            # NOTE: "context" is no longer a spoken trigger. Material-as-context
            # lives in History (Ctrl+Alt+H): pick items + a preset there.
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
                    words,
                    windows,
                    keyword_template=self._active_keyword_template,
                    window_words=window_words if mode_active else None,
                    config_snap=_snap,
                    route_decision=_route,
                )
            # Mode was ambiguous → the interactive "Which mode?" picker is now up
            # and OWNS the outcome (re-process the preserved words, or paste the
            # polished default on dismiss). Don't paste/record here (owner v6).
            if mode == "__pick__":
                self._set_state("idle")
                return
            if not out:
                self._idle()
                return
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
            entry = self.history.add(out, mode, duration, raw=raw,
                                     quality=self._take_quality(),
                                     via=via_convert)
            if entry is None:
                self._notify("History save failed",
                             "This dictation could not be saved to History.")
            else:
                try:
                    self.stat_store.record(entry["words"], duration, mode)
                except Exception as e:
                    print("stats record error:", e)
            # Live refresh: tell the web window new data exists — fire-and-forget
            # on a daemon thread so a slow/busy webui socket can NEVER delay the
            # PASTE below (owner v6 reliability: the paste is the user-facing
            # action and outranks every UI refresh).
            self._send_webui_async({"cmd": "refresh", "what": "history"})
            print(f"[result] mode={mode!r} offline={bool(used_offline)} "
                  f"chars={len(out)} saved={entry is not None}")
            # PASTE FIRST — tray/window bookkeeping happens AFTER, so it can never
            # sit in front of the paste on this thread.
            if search_requested:
                prepared_search = self.request_web_search(out)
                landed = bool(prepared_search.get("ok"))
                print(f"[web-search] mode={mode!r} awaiting_consent={landed}")
            else:
                landed = self._paste(out)
            if self.island:
                # Honest verb (owner v4): "Pasted!" only when the text actually
                # landed in a focused field; otherwise "Saved · Ctrl+Alt+H" — it's
                # in History, the user just needs the History window to place it.
                pasted = bool(landed) or bool(search_requested)
                self._tk_schedule(self.island.flash, mode,
                                  offline=used_offline, pasted=pasted)
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
            self._notify("Processing error", str(e))
            self._set_state("error")
            time.sleep(1.0)
            self._idle()
        finally:
            self._processing = False
            # Aggressive GC after dictation — reclaim any transient audio/text
            # buffers so idle memory stays low.
            try:
                import gc
                gc.collect()
            except Exception:
                pass

    def _quick_paste_label(self):
        hk = self.settings.get("quick_paste_hotkey", "ctrl+alt+v")
        return " + ".join(p.strip().capitalize() for p in hk.split("+") if p.strip())

    def _focused_editable(self):
        """Best-effort: would a paste land in an editable field? On Windows this
        used GetGUIThreadInfo (caret/class probe); on Linux there is no equally
        reliable, low-cost, cross-desktop equivalent — AT-SPI works only when
        accessibility is enabled and is fragile under Wayland. So we default to
        True (optimistic), matching the macOS port: the Ctrl+V is sent regardless;
        this only decides the honest 'Pasted' vs 'Saved' label. A real AT-SPI
        focus probe is a documented future enhancement (best-effort, never blocks
        the paste)."""
        return True

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
                if (read_clipboard_text(timeout=0.4) or "") == value:
                    return True
            except Exception:
                pass
            time.sleep(delay)
        return False

    def _paste(self, text, keep_on_clipboard=False):
        """Paste `text` at the cursor. Returns True if it likely landed in an
        editable field. With keep_on_clipboard=True the text is LEFT on the
        clipboard afterwards (so Ctrl+V keeps working); otherwise the user's
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
            previous = read_clipboard_text()
        except Exception:
            previous = ""
        try:
            # Put our text on the clipboard and CONFIRM it before Ctrl+V — a lost
            # copy would otherwise paste stale clipboard content (an old prompt).
            wrote = self._set_clipboard(text)
            if not wrote:
                # The write provably failed (missing backend or clipboard busy), so
                # firing Ctrl+V would paste whatever stale content is on the
                # clipboard. Skip it and report not-landed so the caller shows the
                # honest "Saved · …" fallback instead of a paste that didn't happen.
                print("paste skipped: clipboard write could not be verified")
                return False
            # Release held modifiers so Ctrl+V lands cleanly (Rule 7).
            # Release the mode key specifically in case the user is physically
            # holding it during paste (e.g. right shift).
            for mod in ("ctrl", "alt", "shift", "windows"):
                try:
                    keyboard.release(mod)
                except Exception:
                    pass
            try:
                keyboard.release(self.mode_key)
            except Exception:
                pass
            time.sleep(0.04)
            keyboard.send("ctrl+v")
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
                    self.clipboard._last_text = read_clipboard_text() or ""
                except Exception:
                    pass
                try:
                    self.clipboard.resume(skip_current=True)
                except Exception:
                    pass
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
                    # Previous dictation is still transcribing/pasting — starting now
                    # would corrupt shared stream state + open a second mic stream.
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
            # Clean up a partially-started stream so the mic isn't left open.
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
        except Exception as e:
            # A device/array edge case must not strand the controller in a busy
            # or "Listening" state forever. stop_recording isolates the normal
            # stream failures; this is the final lifecycle safety net.
            print("could not stop recording:", e)
            self._stream_done.set()
            self._search_requested = False
            self._notify("Recording error", str(e))
            self._set_state("error")
            self._idle()
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
        self._bump_feature("preset_run")
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
            if entry is None:
                self._notify("History save failed",
                             "The Deck result could not be saved to History.")
            else:
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
                # "Pasted!" only when it really landed; otherwise "Saved · Ctrl+Alt+H"
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
        Ctrl+V. The image stays on the clipboard afterwards (there is no way to
        snapshot/restore arbitrary prior non-text content without pywin32)."""
        if not self.copy_image(path):
            return   # copy_image already notified WHY — never blind-fire Ctrl+V
        for mod in ("ctrl", "alt", "shift", "windows"):
            try:
                keyboard.release(mod)
            except Exception:
                pass
        time.sleep(0.05)
        keyboard.send("ctrl+v")

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
            # Supply the private mode at creation time, then atomically replace
            # the live token. Readers never observe an empty/partial token while
            # a restarting controller rotates it.
            tmp_path = f"{path}.{os.getpid()}.tmp"
            fd = os.open(tmp_path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as f:
                f.write(self._cmd_token)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
            try:
                os.chmod(path, 0o600)  # best-effort owner-only (POSIX)
            except OSError:
                pass
        except OSError as e:
            # Fail closed: keep the in-memory token (so the channel stays
            # authenticated) even though clients can't read it.
            print("cmd token write failed:", e)
            try:
                os.remove(locals().get("tmp_path", ""))
            except OSError:
                pass

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
        # Bound unauthenticated pre-parse connections. The token prevents command
        # injection, but without a cap a local process could still hold thousands
        # of five-second handler threads open and exhaust memory.
        _handler_slots = threading.BoundedSemaphore(16)

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
                    resp.update(state=st, text=txt, recording=self.recording,
                                active_mode=getattr(self, "active_mode", None),
                                prompt_mode_enabled=self.prompt_mode_enabled,
                                meeting_recording=getattr(self, "meeting_recording", False))
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
                    changed_key = str(req.get("key") or "")
                    resp = model_authority.controller_reload_response(
                        self._apply_settings_change,
                        changed_key,
                        lambda action: threading.Thread(
                            target=action, daemon=True
                        ).start(),
                    )
                elif cmd == "web_search_request":
                    resp.update(self.request_web_search(req.get("text") or ""))
                elif cmd == "web_search_confirm":
                    resp.update(self.confirm_web_search(
                        req.get("request_id") or ""))
                elif cmd == "web_search_cancel":
                    resp.update(self.cancel_web_search(
                        req.get("request_id") or ""))
                elif cmd == "rebind":
                    binding_key = str(req.get("key") or "")
                    binding_value = str(req.get("value") or "")
                    apply_binding = {
                        "hotkey": self.apply_hotkey,
                        "quick_paste_hotkey": self.apply_quick_paste_hotkey,
                        "history_hotkey": self.apply_history_hotkey,
                        "search_hotkey": self.apply_search_hotkey,
                        "web_search_hotkey": self.apply_web_search_hotkey,
                    }.get(binding_key)
                    if apply_binding is None:
                        resp = {"ok": False, "applied": False,
                                "message": "Unknown shortcut setting."}
                    else:
                        ok, message = apply_binding(binding_value)
                        resp = {"ok": bool(ok), "applied": bool(ok),
                                "message": message,
                                "value": self.settings.get(binding_key, "")}
                elif cmd == "clear_prompts":
                    ok = self.prompt_history.clear_history()
                    resp = {"ok": bool(ok), "message": (
                        "" if ok else "Couldn't clear prompt history.")}
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

                    # Track Convert usage so the "Convert" island tip can retire
                    # once the user adopts it (tips usage-awareness).
                    if mode:
                        self._bump_feature("convert")

                    def _wj():
                        try:
                            time.sleep(0.25)
                            self._run_deck_job(items, instr, title, mode)
                        except Exception as e:
                            print("cmd deck_job error:", e)
                    threading.Thread(target=_wj, daemon=True).start()
                # ── Meeting Mode commands (owner 2026-06-29) ──
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
                srv = branding.ipc_bind_server(
                    "controller", self.CMD_PORT, backlog=4)
            except Exception as e:
                print("cmd server unavailable:", e)
                return
            while True:
                try:
                    conn, _ = srv.accept()
                    if not branding.ipc_peer_is_current_user(conn):
                        conn.close()
                        continue
                    if not _handler_slots.acquire(blocking=False):
                        conn.close()
                        continue

                    def _bounded_handle(client):
                        try:
                            _handle(client)
                        finally:
                            _handler_slots.release()

                    try:
                        threading.Thread(
                            target=_bounded_handle, args=(conn,),
                            daemon=True).start()
                    except Exception:
                        _handler_slots.release()
                        conn.close()
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
            elif k == "web_search_hotkey":
                self._register_web_search()
                print(f"[live-apply] Web Search hotkey → {self.web_search_hotkey!r}")
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
                # The island deck composition / Foreign opt-in changed in Settings
                # — re-push the bar snapshot so it reflects immediately.
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
            elif k in ("mode_key", "mode_button_enabled", "modes"):
                self._register_mode_key()
                print("[live-apply] Smart Mode key re-hooked")
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
            elif k == "autostart":
                desired = bool(self.settings.get("autostart", True))
                if not autostart.set_enabled(desired):
                    self.settings.set("autostart", bool(autostart.is_enabled()))
            elif k == "cpu_threads":
                print("[live-apply] cpu_threads saved — applies on the next "
                      "model load")
        except Exception as e:
            print(f"live-apply of {key!r} failed:", e)
            return False
        return True

    def _send_webui(self, obj, timeout=0.6):
        """Send one authenticated command to the web-window listener."""
        import json as _json
        import socket as _socket
        try:
            payload = dict(obj)
            payload["token"] = getattr(self, "_cmd_token", "") or ""
            with branding.ipc_connect(
                    "webui", self.WEBUI_PORT, timeout=timeout) as s:
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
        """Paste-latest hotkey (default Ctrl+Alt+V). PASTE ONLY — pastes the MOST
        RECENT transcript straight into wherever your cursor is. It no longer opens
        History (owner v9: paste and History are now two SEPARATE actions — opening
        the window is the History hotkey, default Ctrl+Alt+D). No popup, no forced
        mode, no window activation, no yellow flash — just the text, plus a clear
        island message when there is nothing to paste or the paste can't land. (The
        tray "Re-paste last" item calls this too.)"""
        self._bump_feature("quick_paste")
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
        """History hotkey (default Ctrl+Alt+H) — OPEN HISTORY ONLY (owner v9).
        Brings the main window to the History tab: restores it if minimized,
        focuses it if already open, opens it if closed, and lifts it above the
        other Mumble windows. Never pastes — that's the paste-latest hotkey's job.
        Decoupled so the two concepts can never be confused again."""
        threading.Thread(target=self._show_history_page, daemon=True).start()

    def _quick_status(self, msg):
        """A brief island message for the Ctrl+Alt+V recall — used ONLY for the
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
        self._bump_feature("deck")
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
        self._bump_feature("search")
        threading.Thread(
            target=self._show_system_search_page,
            name="mumble-open-system-search",
            daemon=True,
        ).start()

    def on_web_search_hotkey(self):
        with self.lock:
            if self.paused or self.busy:
                return False
            if self.recording:
                self._search_requested = True
                self.busy = True
                threading.Thread(
                    target=self._safe_stop, daemon=True,
                    name="mumble-stop-for-web-search",
                ).start()
                return True
            self.busy = True

        def _prepare():
            try:
                selected = self._grab_selection_quiet()
                if selected:
                    result = self.request_web_search(selected)
                    if not result.get("ok"):
                        self._quick_status(result.get("message") or
                                           "Web Search could not prepare")
                else:
                    self._search_requested = True
                    self._safe_start()
            finally:
                self.busy = False

        threading.Thread(
            target=_prepare, daemon=True, name="mumble-prepare-web-search",
        ).start()
        return True

    def _show_system_search_page(self):
        """Open the main web window directly on the local Search launcher."""
        message = {"cmd": "system_search"}
        if self._send_webui(message):
            return True
        if not self._open_web_ui():
            return False
        for _ in range(25):
            time.sleep(0.3)
            if self._send_webui(message):
                return True
        return False

    def request_web_search(self, text):
        """Prepare an online search without sending its words online."""
        query = str(text or "").strip()
        if not query:
            return {"ok": False, "message": "Select or dictate words to search."}
        engine = str(
            self.settings.get("search_engine", "perplexity") or "perplexity"
        ).lower()
        if engine not in self.SEARCH_ENGINES:
            engine = "perplexity"
        request_id = uuid.uuid4().hex
        with self._web_search_lock:
            cutoff = time.monotonic() - 120.0
            for old_id, old in list(self._pending_web_searches.items()):
                if float(old.get("created", 0.0)) < cutoff:
                    self._pending_web_searches.pop(old_id, None)
            while len(self._pending_web_searches) >= 8:
                self._pending_web_searches.pop(next(iter(self._pending_web_searches)))
            self._pending_web_searches[request_id] = {
                "query": query,
                "engine": engine,
                "created": time.monotonic(),
            }
        provider = engine.title()
        message = {
            "cmd": "web_search_consent",
            "request_id": request_id,
            "provider": provider,
            "query": query,
            "privacy": (
                f"These selected words will be sent to {provider} over the "
                "internet only after you choose Search online. Mumble Find "
                "stays private on this device."
            ),
        }
        if self._send_webui(message):
            return {"ok": True, "request_id": request_id}
        with self._web_search_lock:
            self._pending_web_searches.pop(request_id, None)
        return {
            "ok": False,
            "message": "Mumble could not show the Web Search privacy confirmation.",
        }

    def confirm_web_search(self, request_id):
        """Consume one confirmed request and open its frozen route once."""
        with self._web_search_lock:
            prepared = self._pending_web_searches.pop(
                str(request_id or ""), None
            )
        if not prepared or time.monotonic() - prepared["created"] > 120.0:
            return {"ok": False, "message": "That Web Search request expired."}
        import urllib.parse

        template = self.SEARCH_ENGINES[prepared["engine"]]
        url = template.format(q=urllib.parse.quote(prepared["query"]))
        try:
            opened = self.open_in_browser(url)
        except Exception as exc:
            return {"ok": False, "message": f"The browser could not open: {exc}"}
        if opened is not True:
            return {
                "ok": False,
                "message": (
                    "The browser could not open Web Search. Your consent was "
                    "used once; try again after checking the default browser."
                ),
            }
        return {"ok": True, "message": "Web Search opened."}

    def cancel_web_search(self, request_id):
        with self._web_search_lock:
            self._pending_web_searches.pop(str(request_id or ""), None)
        return {"ok": True}

    # Web providers used only after explicit Web Search confirmation.
    # {q} is the URL-encoded query.
    SEARCH_ENGINES = {
        "google": "https://www.google.com/search?q={q}",
        "perplexity": "https://www.perplexity.ai/search?q={q}",
        "brave": "https://search.brave.com/search?q={q}",
    }

    # Browser COMMANDS for the Settings → Browser choice. On Linux browsers live
    # on $PATH under well-known command names (resolved via shutil.which); a
    # missing browser falls back to the OS default.
    BROWSERS = {
        "edge": ["microsoft-edge", "microsoft-edge-stable", "microsoft-edge-beta"],
        "chrome": ["google-chrome", "google-chrome-stable", "chrome"],
        "brave": ["brave-browser", "brave-browser-stable", "brave"],
        "chromium": ["chromium", "chromium-browser"],
        "firefox": ["firefox", "firefox-esr"],
    }

    def open_in_browser(self, url):
        """Open `url` in the browser chosen in Settings ('default' = the OS
        default via webbrowser/xdg-open). A chosen browser that isn't installed
        falls back to the default rather than failing silently."""
        import shutil

        choice = (self.settings.get("browser", "default") or "default").lower()
        for cmd in self.BROWSERS.get(choice, []):
            exe = shutil.which(cmd)
            if exe:
                try:
                    import subprocess

                    subprocess.Popen([exe, url], env=_external_child_env())
                    return True
                except Exception as e:
                    print("browser launch failed, using default:", e)
                    break
        return self._xdg_open(url)

    _PRESS_BINDING_LABELS = {
        "hotkey": "Dictate",
        "quick_paste_hotkey": "Paste latest",
        "history_hotkey": "Open Deck",
        "search_hotkey": "Mumble Find",
        "web_search_hotkey": "Web Search",
    }

    def _press_binding_values(self):
        return {
            "hotkey": self.settings.get("hotkey", "ctrl+windows"),
            "quick_paste_hotkey": self.settings.get(
                "quick_paste_hotkey", "ctrl+alt+v"
            ),
            "history_hotkey": self.settings.get(
                "history_hotkey", "ctrl+alt+d"
            ),
            "search_hotkey": self.settings.get(
                "search_hotkey", "ctrl+alt+f"
            ),
            "web_search_hotkey": self.settings.get(
                "web_search_hotkey", "ctrl+alt+s"
            ),
        }

    def _active_binding_conflict(self, key, spec):
        for other_key, attr, handle_attr in (
            ("hotkey", "hotkey", "_hk_main"),
            ("quick_paste_hotkey", "quick_hotkey", "_hk_quick"),
            ("history_hotkey", "history_hotkey", "_hk_history"),
            ("search_hotkey", "search_hotkey", "_hk_search"),
            ("web_search_hotkey", "web_search_hotkey", "_hk_web_search"),
        ):
            if other_key == key or getattr(self, handle_attr, None) is None:
                continue
            other_spec = getattr(self, attr, "")
            if bindings.conflicts(spec, other_spec):
                return (
                    f"{self._PRESS_BINDING_LABELS.get(key, key)} overlaps "
                    f"{self._PRESS_BINDING_LABELS.get(other_key, other_key)} "
                    f"({bindings.pretty(other_spec)}). The existing command "
                    "was preserved and the conflicting command was left "
                    "unregistered; choose a free shortcut in Settings."
                )
        return None

    def _sane_press_hotkey(self, key, default):
        """Read a SINGLE-PRESS hotkey from settings and refuse a bare modifier.

        A lone Ctrl/Alt/Shift/Win as a tap hotkey fires on every press of that
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
        spec = self._sane_press_hotkey("quick_paste_hotkey", "ctrl+alt+v")
        try:
            conflict = self._active_binding_conflict("quick_paste_hotkey", spec)
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
        spec = self._sane_press_hotkey("history_hotkey", "ctrl+alt+d")
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
        spec = self._sane_press_hotkey("search_hotkey", "ctrl+alt+f")
        try:
            conflict = self._active_binding_conflict("search_hotkey", spec)
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

    def _register_web_search(self):
        spec = self._sane_press_hotkey("web_search_hotkey", "ctrl+alt+s")
        try:
            conflict = self._active_binding_conflict("web_search_hotkey", spec)
            if conflict:
                try:
                    self._notify("Web Search shortcut conflict", conflict)
                except Exception:
                    pass
                raise ValueError(conflict)
            new_handle = bindings.register_hotkey(spec, self.on_web_search_hotkey)
            old_handle = self._hk_web_search
            if not bindings.unregister(old_handle):
                bindings.unregister(new_handle)
                raise RuntimeError(
                    "the previous Web Search shortcut could not be released"
                )
            self.web_search_hotkey, self._hk_web_search = spec, new_handle
        except Exception as e:
            print("Web Search hotkey error:", e)
            raise

    # ===================================================== actions for the UI
    def _apply_press_binding(self, key, hk, attr, handle_attr, callback, success):
        lock = getattr(self, "_binding_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._binding_lock = lock
        with lock:
            hk = bindings.normalize(hk)
            ok, msg = bindings.validate(hk)
            if not ok:
                return False, msg
            bindings_in_use = {
                "hotkey": ("Dictate", "hotkey", "ctrl+windows"),
                "quick_paste_hotkey": (
                    "Paste latest", "quick_hotkey", "ctrl+alt+v"),
                "history_hotkey": ("Open Deck", "history_hotkey", "ctrl+alt+d"),
                "search_hotkey": ("Mumble Find", "search_hotkey", "ctrl+alt+f"),
                "web_search_hotkey": (
                    "Web Search", "web_search_hotkey", "ctrl+alt+s"),
            }
            for other_key, (label, other_attr, default) in bindings_in_use.items():
                if other_key == key:
                    continue
                other = bindings.normalize(
                    self.settings.get(
                        other_key, getattr(self, other_attr, default)
                    ) or default
                )
                if bindings.conflicts(hk, other):
                    return False, (
                        f"That shortcut is already used by {label}. "
                        "Mumble kept your previous shortcut."
                    )
            current = bindings.normalize(
                self.settings.get(key, getattr(self, attr, ""))
            )
            old_handle = getattr(self, handle_attr, None)
            if current == hk and old_handle is not None:
                return True, success.format(binding=bindings.pretty(hk))
            try:
                new_handle = bindings.register_hotkey(hk, callback)
            except Exception as e:
                return False, (
                    "That shortcut could not be registered; another app may "
                    f"already use it. Mumble kept your previous binding. ({e})"
                )
            if self.settings.set(key, hk) is False:
                released_new = bindings.unregister(new_handle)
                restored = self.settings.set(key, current)
                if not released_new or restored is False:
                    return False, ("The shortcut change could not be reconciled. "
                                   "Restart Mumble before trying another binding.")
                return False, ("Couldn't save the shortcut. Your previous "
                               "shortcut is still active.")
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

    def apply_web_search_hotkey(self, hk):
        return self._apply_press_binding(
            "web_search_hotkey", hk, "web_search_hotkey", "_hk_web_search",
            self.on_web_search_hotkey,
            "Saved — Web Search starts with {binding}.")

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

    def _try_load(self, name):
        """Load `name` into self.model. Returns True on success. On FAILURE the previous
        self.model is left untouched (the assignment only lands if WhisperModel succeeds),
        so a failed switch keeps the app working on the model it already had.

        Resource Saver Mode is honoured at THIS chokepoint: the requested `name`
        is resolved through `_effective_model`, so every load path (boot, hot-
        swap, fallback) loads the lightest model when the saver is on — while the
        caller still records the user's real choice in `self.model_name`."""
        name = self._effective_model(name)
        try:
            # Use the machine's cores — CTranslate2's own default is a fixed 4
            # threads regardless of hardware, which leaves most of a modern CPU
            # idle during transcription. Auto (setting 0) now claims every core
            # except two, so dictation gets real speed while the rest of the
            # system stays responsive. An explicit setting still wins.
            threads = self.settings.get("cpu_threads", 0) or 0
            if not threads:
                threads = max(4, (os.cpu_count() or 8) - 2)
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
            # Aggressive GC after model swap — idle memory target < 1GB.
            try:
                import gc
                gc.collect()
            except Exception:
                pass
            self._warm_model(new_model)
            return True
        except Exception as e:
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
        lang = "en" if self.model_name.endswith(".en") else \
            self.settings.get("language", "en")
        if not self._tx_lock.acquire(timeout=10):
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

    def set_model(self, name, on_done=None):
        if name == self.model_name:
            if on_done:
                on_done(True, name)
            return
        # NB: do NOT persist the choice here — only after it actually loads (see below),
        # so a model that won't download can never poison settings and brick the next boot.
        # TODO (L-016): Check network connectivity before launching the download
        # thread. On offline machines the HuggingFace download hangs for ~30s
        # before failing; a quick connectivity probe would give a faster, clearer
        # error. Consider a HEAD request to huggingface.co or a DNS lookup.
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
        }

    def _ai_key(self):
        return self._ai_cfg()["key"]

    def set_llm_provider(self, provider, model="", key=None):
        """Switch the AI engine (Settings → AI Provider). Saves the provider,
        model and key, then returns (ok, message)."""
        if provider not in ("cerebras", "openrouter"):
            return False, "Unknown provider."
        self.settings.set("llm_provider", provider)
        info = ai.PROVIDERS[provider]
        if model:
            self.settings.set(info["model_setting"], model)
        if key is not None:
            self.settings.set(info["key_setting"], (key or "").strip())
            self.pro_key_failed = False
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

    def _grab_selection_quiet(self):
        """Copy the CURRENT selection (hover-over) and return it, or '' if nothing is
        actually selected. Restores the prior clipboard. Used so that saying a mode while
        text is highlighted feeds that text in as strict, direct context."""
        if self.clipboard:
            self.clipboard.pause()
        try:
            before = read_clipboard_text()
        except Exception:
            before = ""
        sel = ""
        try:
            if not self._set_clipboard("\x00", tries=3, delay=0.02):
                raise RuntimeError("clipboard busy; selection capture cancelled")
            for mod in ("ctrl", "alt", "shift", "windows"):
                try:
                    keyboard.release(mod)
                except Exception:
                    pass
            keyboard.send("ctrl+c")
            # Poll for the copy to land instead of a flat 120ms sleep — Ctrl+C
            # usually completes in 10–30ms, so the fixed wait was pure latency on
            # the context-gather path (owner: "context gathering takes too long").
            # Break the instant a real selection appears; if nothing was selected
            # the clipboard stays the sentinel and we settle at the old 130ms cap
            # — so this is never slower than before, and ~3-10× faster on a hit.
            got = "\x00"
            deadline = time.time() + 0.13
            while True:
                time.sleep(0.012)
                try:
                    got = read_clipboard_text(timeout=0.1) or ""
                except Exception:
                    got = ""
                if (got and got != "\x00") or time.time() >= deadline:
                    break
            if got and got != "\x00":
                sel = got.strip()
        except Exception as e:
            print("selection grab error:", e)
        if before:  # Don't restore empty — it would destroy non-text clipboard
            # Confirmed restore (retries) so the user's clipboard is never left
            # holding our sentinel / the grabbed selection.
            self._set_clipboard(before)
        if self.clipboard:
            try:
                self.clipboard._last_text = read_clipboard_text() or ""
            except Exception:
                pass
            try:
                self.clipboard.resume(skip_current=True)
            except Exception:
                pass
        return sel

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
    ):
        """Send to the AI, routed by lane:

        - prompt → the Prompt Architect constitution (sent every call).
        - email/reply → focused per-mode prompts (high reasoning).
        - foreign → annotate Arabic/Islamic terms with slash candidates, AI disambiguates.
        - text → minimal POLISH (cheap). If the mode key was held but no keyword was
          found, the polish call also returns a MODE/CONF second opinion we act on.

        When *cfg* / *prompt_cfg* are provided they are used as the frozen AI
        config for the duration of this call. When absent the config is read
        fresh from settings (legacy / redo paths).

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
        if prompt_cfg is None:
            prompt_cfg = self._ai_cfg()
        key, model, url = cfg["key"], cfg["model"], cfg["url"]
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
            # AI Mode: prompting can run on its own, stronger provider.
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
        if not second_opinion:
            # Plain polish (no mode key) — the long-dictation path. Route through
            # the no-truncation polisher: short inputs are one call (same latency
            # as the old fully-drained stream), long inputs are chunked + continued
            # and reassembled so a 15-min transcript is never silently cut off.
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
        # Second opinion (mode key held, no keyword) — keep the streaming MODE/CONF
        # path: this is a short missed-keyword check, never a long transcript.
        try:
            gen = ai.cerebras_polish(
                polish_input,
                key,
                model,
                url=url,
                second_opinion=True,
                aggressiveness=(invocation_snapshot.polish_aggressiveness
                                if isinstance(invocation_snapshot, processing_route.ProcessingInputSnapshot)
                                else self.settings.get("polish_aggressiveness", "Light")),
                context=poll_ctx,
                window_words=window_words,
                route_decision=route_decision,
            )
            out = self._collect_text(gen)
        except Exception as e:
            print("polish API failed, using offline builder:", e)
            return "text", self._builder(
                raw, "text", raw, invocation_snapshot=invocation_snapshot
            )[1]
        clean, ai_mode, conf, redo = ai.split_mode_tail(out)
        return self._handle_second_opinion(
            clean, ai_mode, conf, redo, raw, name, poll_ctx, prefs, context_strict,
            cfg=cfg, prompt_cfg=prompt_cfg,
            invocation_snapshot=invocation_snapshot,
        )

    def _handle_second_opinion(
        self, clean, ai_mode, conf, redo, raw, name, context, prefs, context_strict,
        cfg=None, prompt_cfg=None, invocation_snapshot=None,
    ):
        """Act on the AI's free 'the local detector missed a mode' second opinion.
        High-confidence prompt → auto re-run with the constitution; high-confidence
        email/reply → re-run focused; low confidence → the interactive
        'Which mode?' picker (preserves the original words; the old suggestion-chip
        path was replaced by the picker in owner v6)."""
        if cfg is None:
            cfg = self._ai_cfg()
        if prompt_cfg is None:
            prompt_cfg = self._ai_cfg()
        if redo or (ai_mode == "prompt" and conf == "high"):
            try:
                pcfg = prompt_cfg  # AI Mode: dedicated prompting engine
                # Include captured conversation context for the redo path
                # (same zero-friction injection as the primary prompt lane).
                redo_ctx = context
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
                    route_decision=(invocation_snapshot.route
                                    if isinstance(invocation_snapshot, processing_route.ProcessingInputSnapshot)
                                    else None),
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
                if ai_mode == "reply" and not (rerun_ctx or "").strip():
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
                    expected_feature=(
                        ai_mode if ai_mode in {"prompt", "email", "reply"}
                        else "dictation"),
                    expected_lane=ai_mode,
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
        self._offer_mode_pick(raw, ai_mode, ai._clean(clean))
        return "__pick__", ai._clean(clean)

    # ---- Conversation Capture (ITEM 5) --------------------------------------
    def _capture_conversation_quiet(self):
        """Grab a whole AI conversation via Ctrl+A/Ctrl+C from the focused chat
        window. Sends Select-All + Copy, polls the clipboard for the result,
        restores the prior clipboard. Returns the captured transcript ('' if
        nothing came back). This is the robust, app-agnostic alternative to a
        brittle per-site DOM scraper."""
        if self.clipboard:
            self.clipboard.pause()
        try:
            before = read_clipboard_text()
        except Exception:
            before = ""
        text = ""
        try:
            if not self._set_clipboard("\x00", tries=3, delay=0.02):
                raise RuntimeError("clipboard busy; conversation capture cancelled")
            for mod in ("ctrl", "alt", "shift", "windows"):
                try:
                    keyboard.release(mod)
                except Exception:
                    pass
            keyboard.send("ctrl+a")
            time.sleep(0.06)        # let the select-all settle before copying
            keyboard.send("ctrl+c")
            # A whole conversation can be large; poll a touch longer than the
            # single-selection grab, but still break the instant the copy lands.
            got = "\x00"
            deadline = time.time() + 0.7
            while True:
                time.sleep(0.02)
                try:
                    got = read_clipboard_text(timeout=0.15) or ""
                except Exception:
                    got = ""
                if (got and got != "\x00") or time.time() >= deadline:
                    break
            if got and got != "\x00":
                text = got.strip()
        except Exception as e:
            print("conversation capture error:", e)
        if before:  # never restore empty (would destroy a non-text clipboard)
            self._set_clipboard(before)
        if self.clipboard:
            try:
                self.clipboard._last_text = read_clipboard_text() or ""
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
        ui_label = res.get("ui", "")
        self._bump_feature("capture")
        if self.island:
            tail = f" · {ui_label}" if ui_label else ""
            self._tk_schedule(self.island.hint,
                              f"Conversation captured · {turns} turns{tail}")
            self._tk_schedule(self.island.wake)
        self._send_webui_async({"cmd": "refresh", "what": "history"})
        return {"ok": True, "chars": len(text), "turns": turns,
                "ui": ui_label}

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

    # ---- On-device LLM Backend ----------------------------------------------
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

    # ---- Island Mode Deck (ITEM 4) ------------------------------------------
    def island_modes(self):
        """The processing modes the island deck offers, as (key, label) pairs — the
        REAL cloud lanes only (prompt/email/reply). A user setting can pare the
        list down; an unknown key is dropped so we never show a mode that can't run."""
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
        if self.island:
            self._tk_schedule(self.island.hint,
                              "Foreign on" if on else "Foreign off")

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

    # ---- Automatic hardware-aware model selection --------------------------
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
        and load it if it changed. Users never type a model id — they pick a PC
        automatically detected class and a language scope and maps it to the right
        faster-whisper model (branding.resolve_model). `load=False` only
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
    ):
        """AI-driven generation. Lane A (plain text) → minimal polish; Lane B (a
        mode the button armed) → focused/constitution path. (Material-as-context
        AI jobs moved to the Deck — see _run_deck_job; "context" is no longer a
        spoken trigger.) Returns (mode, text, used_offline)."""
        name = self.settings.get("user_name", "")
        prefs = self.settings.get("prompt_prefs")
        # Capture the AI config ONCE at the start of this dictation so that
        # an in-flight cloud request always uses the key it was launched with,
        # even if the user changes/deletes the key mid-call.
        # _cloud_generate() receives this snapshot and never re-reads settings.
        cfg = self._ai_cfg()
        prompt_cfg = self._ai_cfg()
        key = cfg["key"]
        # Use snapshot config if provided (for settings-change isolation).
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
        pro_mode = route_decision.pro_mode
        snap_fmt = invocation_snapshot.format_enabled
        if route_decision.ready:
            try:
                mode, out = processing_route.call_provider(
                    route_decision, self._cloud_generate,
                    raw,
                    name,
                    context,
                    det_mode,
                    det_request,
                    mode_active=mode_active,
                    prefs=prefs,
                    context_strict=context_strict,
                    keyword_template=keyword_template,
                    words=words,
                    window_words=window_words,
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
        # Returns None (→ builder) when no model is present, so default behaviour is
        # unchanged. used_offline stays True: this is the no-cloud path.
        llm_out = self._local_llm_generate(
            raw, det_mode, context, invocation_snapshot=invocation_snapshot
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
            raw, det_mode, det_request, snap_fmt,
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

    def set_prompt_mode(self, v):
        """THE BIG SHIFT: set the sticky Prompt toggle (the web Settings switch).
        Now a thin wrapper over the general active-mode selector so Prompt and the
        island mode deck share one source of truth."""
        return bool(self.set_active_mode("prompt" if v else None) == "prompt")

    def toggle_prompt_mode(self):
        """Flip the Prompt toggle — kept for callers that toggle Prompt directly."""
        return self.set_prompt_mode(self.active_mode != "prompt")

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
        """Put a clipboard-history image back onto the system clipboard as PNG, so
        the user can paste it anywhere with Ctrl+V — not just open the saved file.
        Uses wl-copy on Wayland / xclip on X11. Returns True only if the image
        actually landed, so the caller never blind-fires Ctrl+V (which would paste
        stale content)."""
        if not path or not os.path.exists(path):
            self._notify("Image unavailable",
                         "That image file is missing — it may have been cleared.")
            return False
        import shutil
        import subprocess

        # Use the INPUT session type, NOT GDK_BACKEND: the app pins
        # GDK_BACKEND=x11 on Wayland (so the GTK island renders), which made the
        # old `not GDK_BACKEND.startswith("x11")` test always False on real
        # Wayland — so an image copy used xclip (XWayland clipboard) even with
        # wl-clipboard installed, and native-Wayland apps couldn't paste it.
        try:
            wayland = bindings._session_type() == "wayland"
        except Exception:
            wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
        cmd = None
        if wayland and shutil.which("wl-copy"):
            cmd = ["wl-copy", "--type", "image/png"]
            stdin_file = path
        elif shutil.which("xclip"):
            cmd = ["xclip", "-selection", "clipboard", "-t", "image/png", "-i", path]
            stdin_file = None
        elif shutil.which("wl-copy"):  # X11-less fallback
            cmd = ["wl-copy", "--type", "image/png"]
            stdin_file = path
        if cmd is None:
            self._notify("Couldn't copy image",
                         "Install xclip (X11) or wl-clipboard (Wayland) to copy "
                         "images to the clipboard.")
            return False
        paused_monitor = False
        try:
            if self.clipboard:
                self.clipboard.pause()
                paused_monitor = True
            if stdin_file is not None:
                with open(stdin_file, "rb") as f:
                    # xclip/wl-copy hold the selection by staying alive; do NOT
                    # block on them — fire, then CONFIRM ownership below.
                    subprocess.Popen(cmd, stdin=f,
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            # X11/Wayland clipboards are selection-OWNERSHIP based: the helper may
            # not own the selection the instant Popen returns. WAIT briefly for
            # image/png to actually be offered before returning, so the caller's
            # Ctrl+V can't fire ahead of the helper and paste stale text.
            if not self._clipboard_has_image(wayland):
                self._notify(
                    "Couldn't copy image",
                    "The clipboard helper did not take ownership. Try again.")
                return False
            # Keep the history monitor from re-capturing Mumble's own image as a
            # fresh entry the moment it resumes.
            if self.clipboard:
                try:
                    with Image.open(path) as source:
                        self.clipboard._last_img = self.clipboard._img_hash(source)
                except Exception:
                    pass
            self._notify("Copied", "Image on the clipboard — paste it with Ctrl+V.")
            return True
        except Exception as e:
            print("copy image error:", e)
            self._notify("Couldn't copy image",
                         "The image couldn't be read — it may be corrupted.")
            return False
        finally:
            if paused_monitor and self.clipboard:
                try:
                    self.clipboard.resume(skip_current=True)
                except Exception:
                    pass

    @staticmethod
    def _clipboard_has_image(wayland):
        """Poll (≤~1.5s) until the clipboard offers an image type, so an image
        paste never races ahead of wl-copy/xclip taking selection ownership.
        Returns True once confirmed; False if it couldn't confirm in time (the
        caller still proceeds — confirmation is best-effort)."""
        import shutil
        import subprocess
        import time as _t
        if wayland and shutil.which("wl-paste"):
            probe = ["wl-paste", "--list-types"]
        elif shutil.which("xclip"):
            probe = ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"]
        else:
            return False
        deadline = _t.time() + 1.5
        while _t.time() < deadline:
            try:
                out = subprocess.run(probe, capture_output=True, text=True,
                                     timeout=0.4).stdout or ""
                out = out.lower()
                if "image/png" in out or "image/" in out:
                    return True
            except Exception:
                pass
            _t.sleep(0.04)
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

    def _xdg_open(self, path):
        """Open a file/folder/URL with the user's default handler (xdg-open)."""
        import shutil
        import subprocess
        try:
            path = os.fspath(path).strip()
        except (TypeError, AttributeError):
            path = ""
        if not path:
            return False
        opener = shutil.which("xdg-open")
        if not opener:
            self._notify(
                "Could not open item",
                "xdg-open is not installed. Install the xdg-utils package.")
            return False
        try:
            subprocess.Popen(
                [opener, path], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, env=_external_child_env())
            return True
        except OSError as e:
            print("xdg-open failed:", e)
            self._notify("Could not open item", str(e))
            return False

    def open_transcripts(self):
        try:
            if not os.path.exists(branding.HISTORY_TXT):
                open(branding.HISTORY_TXT, "a", encoding="utf-8").close()
            self._xdg_open(branding.HISTORY_TXT)
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
            return self.prompt_history.clear_history()
        except Exception as e:
            print("clear prompts error:", e)
            return False

    def switch_to_lite(self):
        """Mumble Lite is a frozen Windows-only Tkinter snapshot — there is no
        Linux build. The Linux app ships only the (full-featured) web UI, so this
        is a friendly no-op rather than a broken launch."""
        self._notify("Mumble Lite is Windows-only",
                     "The Linux edition ships the full web UI — there's no "
                     "separate Lite build to switch to.")

    def open_data_folder(self):
        try:
            self._xdg_open(branding.DATA_DIR)
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
            self._notify("Mumble Updates",
                         "The signed update channel is not configured in this build.")
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
            # No modal Tk dialog under the GTK loop: auto-start the staged,
            # verified folder-swap install and report progress via notifications.
            self._on_update_install()
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
            # Update is extracted — notify and restart to apply (no modal dialog
            # under the GTK loop).
            self._tk_schedule(self._notify, "Mumble Update",
                              f"{message} Restarting to apply…")
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
            # No classic Tk window on Linux (app_window.py isn't shipped — the web
            # UI is the only main window). If pywebview/WebKitGTK is unavailable,
            # say so plainly instead of silently failing.
            self._notify(
                "Couldn't open the Mumble window",
                "The web UI needs WebKitGTK + PyGObject. Re-run install.sh and "
                "install the system packages it lists.")
        except Exception as e:
            print("window error:", e)
            self._notify("Couldn't open Mumble", e)

    def _open_web_ui(self):
        """Launch the pywebview main-window process. Returns True if launched/already
        open, False if pywebview is unavailable so the caller falls back to classic.
        (owner v8: the History-flyout 'popup' start mode is gone — there is one
        window now; Ctrl+Alt+H navigates IT to History.)

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
            exe = sys.executable or "python3"
            env = dict(os.environ)
            env["MUMBLE_START"] = "app"
            # start_new_session=True puts the shell (and any children it spawns)
            # in its own process group so _quit() can reap the whole tree with a
            # single killpg — the POSIX equivalent of the Windows taskkill /T.
            self._webui_proc = subprocess.Popen([exe, shell],
                                                cwd=branding.INSTALL_DIR, env=env,
                                                start_new_session=True)
            return True
        except Exception as e:
            print("web UI launch failed, falling back to classic:", e)
            return False

    def _quit(self):
        # Signals, tray actions and the command queue can converge on shutdown.
        # Only the first caller owns cleanup/finalisation.
        if getattr(self, "_quitting", False):
            return
        self._quitting = True

        # Freeze capture and close the device BEFORE snapshotting frames.  The
        # previous order transcribed a list that PortAudio was still mutating,
        # then closed the mic much later in shutdown.
        partial_frames = []
        was_recording = False
        try:
            with self.lock:
                was_recording = bool(self.recording)
                self.recording = False
                self._stream_done.set()
                partial_frames = list(self.frames)
        except Exception:
            was_recording = bool(getattr(self, "recording", False))
            partial_frames = list(getattr(self, "frames", []) or [])
            self.recording = False
        try:
            stream = self.stream
            self.stream = None
            if stream is not None:
                try:
                    stream.stop()
                finally:
                    stream.close()
        except Exception:
            pass

        # ---- Save partial history if a dictation was in progress ----
        # If the user quits while dictating, save whatever audio/text was
        # captured so far so it isn't lost. History is appended at the end
        # of _process(), but a mid-dictation quit bypasses that path.
        try:
            if was_recording and partial_frames:
                import numpy as np
                try:
                    partial_audio = np.concatenate(partial_frames, axis=0).flatten()
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

        # Make meeting capture durable before the process exits. Processing is
        # resumed from the saved WAV on the next launch.
        meeting_lock = getattr(self, "_meeting_lock", None)
        try:
            if meeting_lock is not None:
                meeting_lock.acquire()
            if getattr(self, "meeting_recording", False):
                mid = self.meeting_recorder.finish_capture("")
                self.meeting_recording = False
                if mid:
                    print(f"[shutdown] meeting {mid} saved for resume")
        except Exception as e:
            print(f"[shutdown] meeting capture finalization failed: {e}")
        finally:
            if meeting_lock is not None:
                try:
                    meeting_lock.release()
                except RuntimeError:
                    pass

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

        # ---- Cancel any in-flight model download ----
        # If the user quits while a HuggingFace model download is in progress,
        # signal every active download to stop at the next chunk boundary. The
        # partial .part file is preserved on disk for resume on next launch.
        try:
            from models.downloader import cancel_active_downloads
            n = cancel_active_downloads()
            if n:
                print(f"[shutdown] cancelled {n} active model download(s) &mdash; "
                      "partial file(s) preserved for resume")
        except Exception as e:
            print("[shutdown] download cancel error:", e)

        try:
            keyboard.unhook_all()
        except Exception:
            pass
        try:
            if self.icon is not None:
                self.icon.stop()
        except Exception:
            pass
        # Tear down the web-UI window process we own. The shell was spawned with
        # start_new_session=True, so it leads its own process group — kill the
        # whole group (SIGTERM → SIGKILL) to reap any WebKitGTK helper children,
        # the POSIX equivalent of the Windows `taskkill /T`. terminate() is the
        # fallback if the group signal can't be sent.
        try:
            proc = getattr(self, "_webui_proc", None)
            if proc is not None and proc.poll() is None:
                import signal
                killed = False
                try:
                    pgid = os.getpgid(proc.pid)
                    os.killpg(pgid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=3)
                    except Exception:
                        os.killpg(pgid, signal.SIGKILL)
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
            # The island is an in-process GTK window; it dies with the GTK main
            # loop, which _GlibRoot.quit() (Gtk.main_quit) stops below.
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
        """Apply a pending update or relaunch after this lock owner exits."""
        try:
            import shutil
            import subprocess
            pending = update.pending_swap_script(branding.INSTALL_DIR)
            if pending:
                bash = shutil.which("bash")
                if not bash:
                    raise RuntimeError(
                        "A Mumble update is ready, but bash is not available.")
                subprocess.Popen(
                    [bash, pending], cwd=os.path.dirname(branding.INSTALL_DIR),
                    start_new_session=True)
            else:
                # Starting the replacement immediately races the fixed-port
                # single-instance lock: the child sees this process, exits as a
                # duplicate, and then this process quits. A tiny detached helper
                # waits for the old PID to disappear before exec'ing the new app.
                shell = shutil.which("sh")
                if not shell:
                    raise RuntimeError("The system shell 'sh' is not available.")
                exe = sys.executable or shutil.which("python3")
                if not exe:
                    raise RuntimeError("Python could not be located for restart.")
                target = os.path.join(branding.INSTALL_DIR, "mumble_linux.py")
                wait_then_exec = (
                    'n=0; while kill -0 "$1" 2>/dev/null && [ "$n" -lt 300 ]; '
                    'do sleep 0.1; n=$((n + 1)); done; exec "$2" "$3"')
                subprocess.Popen(
                    [shell, "-c", wait_then_exec, "mumble-restart",
                     str(os.getpid()), exe, target],
                    cwd=branding.INSTALL_DIR, start_new_session=True)
        except Exception as e:
            print("restart relaunch failed:", e)
            self._notify("Restart failed", str(e))
            return False
        self._quit()
        return True  # unreachable in production (_quit uses os._exit)

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
            fallback = "small.en"
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
        # Register each binding INDEPENDENTLY (owner v9 reliability): a single bad
        # registration must never knock out every other hotkey. Each one is wrapped
        # in its own try/except so a failing platform-specific listener (evdev,
        # pynput-XWayland) only takes itself out.
        # --- wire the on-device LLM backend (lazy — just discovers model file) ---
        self._init_local_llm()
        # or unavailable bind must never take the others down with it. One shared
        # try/except meant that if (say) the paste-latest key failed to register,
        # the record hotkey, History key, search key and mode key silently never
        # hooked — the "hotkeys stopped working" class. Each gets its own guard now.
        for name, reg in (
            ("record", self._register_hotkey),
            ("paste-latest", self._register_quick),
            ("history", self._register_history),
            ("mumble-find", self._register_search),
            ("web-search", self._register_web_search),
            ("mode-key", self._register_mode_key),
        ):
            try:
                reg()
            except Exception as e:
                print(f"hotkey registration failed ({name}):", e)
        # The web window's command channel (paste / deck_job / record / status)
        self._start_cmd_server()
        try:
            # Self-heal machines that ever had Mumble v1.x: its installer-at-login
            # Run-key entry survives reinstalls and threw a red PowerShell error
            # at every boot. Idempotent and instant when there's nothing to do.
            autostart.remove_legacy_run_key()
            if self.settings.get("autostart", True) and not autostart.is_enabled():
                if not autostart.enable():
                    self.settings.set("autostart", False)
        except Exception:
            pass
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
        # Linux GUI thread = GTK's. _GlibRoot maps the controller's
        # self.root.after()/quit() onto GLib timers + the GTK main loop, so the
        # scheduling code below is unchanged from Windows/macOS while the island
        # is a native GTK window. (No AppUserModelID / iconbitmap / Tk theme —
        # those are Windows-only; the GTK window sets its own icon + WM class.)
        self.root = _GlibRoot()
        # Wire the GTK companion bar before its first state snapshot.
        self.island = Island(self.root)  # one island look (owner v6) — no style tiers
        try:
            self.island.set_widget_callbacks(
                on_mode=self.set_active_mode,
                on_deck=self.on_open_history,
                on_foreign=self.toggle_island_foreign)
            self._push_island_bar_state()
        except Exception as e:
            print("island widget wiring skipped:", e)

        # Meeting Mode: create the recorder with transcribe_fn, settings,
        # and the island callback that marshals to the GTK thread.
        self.meeting_recorder = meeting.MeetingRecorder(
            transcribe_fn=self._transcribe,
            settings=self.settings,
            island_callback=self._meeting_island_cb)
        threading.Thread(
            target=self.meeting_recorder.recover_pending,
            name="meeting-recovery", daemon=True).start()

        # Push the initial mode-deck state to the island bar after island creation.
        self._push_island_bar_state()

        self.icon = pystray.Icon(
            "mumble",
            self._tray_image(STATE_COLORS["loading"]),
            "Mumble — Loading…",
            menu=self._build_menu(),
        )
        # Linux tray: pystray's AppIndicator/GTK backend uses GTK, so we must NOT
        # spin a second Gtk.main() on a worker thread (two GTK loops in one process
        # is undefined behaviour). run_detached() registers the indicator into the
        # island's existing GLib main loop instead. Guarded: GNOME ships no SNI
        # host without the AppIndicator extension, and a headless box has no tray —
        # in either case we log and carry on (the island + hotkeys still work).
        try:
            self.icon.run_detached()
        except Exception as e:
            print(f"[tray] system tray unavailable (continuing without it): {e}")
            print("[tray] On GNOME, install the AppIndicator/KStatusNotifier "
                  "extension; the app works fully without the tray icon.")
        threading.Thread(target=self._boot, daemon=True).start()
        if self.settings.get("clipboard_enabled", True):
            self.clipboard.start()

        self._pump()
        self._watch_mode_key()  # keep the mode indicator honest to the real key state
        # Check Linux input permissions — hotkeys and push-to-talk silently fail
        # without /dev/input access. Surface a warning so the user knows WHY
        # hotkeys aren't responding (not a bug, a permission issue — VAL-LIN-PLAT-003).
        self._check_input_permissions()
        # Register SIGINT + SIGTERM handlers so the webui process tree and the
        # single-instance lock port are released on Ctrl+C / systemd stop / kill
        # (VAL-LIN-PLAT-002). Without this, SIGTERM (the standard Linux shutdown
        # signal) orphanes the webui and leaves the lock port held.
        try:
            import signal
            signal.signal(signal.SIGINT, lambda *_: self._quit())
            signal.signal(signal.SIGTERM, lambda *_: self._quit())
        except Exception:
            pass
        # Reclaim transient memory after boot — model load + warm-up can leave
        # large temporary allocations.
        try:
            import gc
            gc.collect()
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

    Crash recovery (VAL-CROSS-001): when the previous instance was force-killed
    the OS normally releases the port immediately, but edge cases (orphaned
    WebKitGTK subtree, OS socket-state lag) can make a re-bind fail. We retry with
    back-off so the launcher self-heals without a confusing "already running"
    message when no instance is actually alive.

    TODO (L-017, L-018): The socket-based lock is reliable but crude — it uses a
    fixed port that can't be configured and offers no dbus-activation or
    systemd-user-service integration. Consider a D-Bus .service file for proper
    single-instance activation, or at minimum add Flatpak/snap autostart awareness
    so the lock doesn't fail silently inside containers."""
    global _LOCK_SOCK
    try:
        _LOCK_SOCK = branding.ipc_bind_server(
            "instance", 49517, backlog=4)
        return True
    except OSError:
        return False
    except Exception as e:
        # Fail closed: an untrusted namespace cannot safely arbitrate two
        # microphone/hotkey controllers or carry the session bearer token.
        print("single-instance IPC unavailable:", e)
        return False


def _signal_running_instance():
    """A second launch shouldn't scold with a popup — it should DO what the
    user wanted: open the running Mumble's window. The single-instance lock
    socket doubles as the channel: connect and send 'open' + the running
    instance's per-session token so a foreign process can't pop the window."""
    import time as _time
    last_error = None
    # Binding precedes Mumble construction and token minting. Retry through
    # that small startup window and require an authenticated acknowledgement.
    for _attempt in range(12):
        try:
            try:
                with open(branding.cmd_token_path(), "r", encoding="utf-8") as f:
                    token = f.read().strip()
            except OSError:
                token = ""
            with branding.ipc_connect("instance", 49517, timeout=0.5) as s:
                s.sendall(b"open " + token.encode("ascii", "ignore"))
                s.settimeout(0.5)
                if s.recv(32) == b"ok":
                    return True
        except Exception as e:
            last_error = e
        _time.sleep(0.15)
    print("could not signal the running instance:", last_error or "no ack")
    return False


def _serve_instance_signals(app):
    """Accept 'open' signals from later launches on the lock socket (it is
    already listen()ing) and route them to the command queue. Daemon thread —
    dies with the process; any error just ends the loop (lock stays held)."""
    def _loop():
        import hmac
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
                with conn:
                    if not branding.ipc_peer_is_current_user(conn):
                        print("instance signal rejected (foreign peer)")
                        continue
                    conn.settimeout(1.0)
                    data = conn.recv(128)
                    parts = (data or b"").split(b" ", 1)
                    if parts and parts[0] == b"open":
                        token = (parts[1].decode("ascii", "ignore").strip()
                                 if len(parts) > 1 else "")
                        want = getattr(app, "_cmd_token", "") or ""
                        if want and hmac.compare_digest(token, want):
                            app.cmd_q.put("open")
                            conn.sendall(b"ok")
                        else:
                            conn.sendall(b"unauthorized")
                            print("instance 'open' signal rejected (bad token)")
            except _socket.timeout:
                continue
            except Exception:
                return
    threading.Thread(target=_loop, daemon=True).start()


# TODO (L-019, L-020): No CI/CD pipeline and no versioned releases for Linux.
# Automated build/test/deploy would catch port-specific regressions early. A
# versioned release pipeline (GitHub Actions building deb/rpm/AppImage/Flatpak)
# should be set up before v1.0 ships. See also install.sh which already covers
# most of the release-install flow but has no automated test gate.
if __name__ == "__main__":
    if not _acquire_single_instance():
        # Hand the request to the running instance (opens its window) and
        # leave quietly — no "already running" popup.
        _signal_running_instance()
        sys.exit(0)
    _app = Mumble()
    _serve_instance_signals(_app)
    _app.run()
