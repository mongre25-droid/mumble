#!/usr/bin/env python3
"""Central branding, paths, and the Mumble design system — Golden Black edition.

App data (settings, history, clipboard, logs) lives in the OS-native per-user
data dir: %APPDATA%\\Mumble on Windows, ~/Library/Application Support/Mumble on
macOS, and $XDG_DATA_HOME/Mumble (or ~/.local/share/Mumble) on Linux. This is
the SHARED module the ports reuse verbatim, so the per-OS branch lives here.
"""

import errno
import hashlib
import os
import shutil
import stat
import sys

APP_NAME = "Mumble"
APP_TAGLINE = "Speak. It types."
VERSION = "0.95"

INSTALL_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(INSTALL_DIR, "assets")
ICON_ICO = os.path.join(ASSETS_DIR, "mumble.ico")
ICON_PNG = os.path.join(ASSETS_DIR, "mumble.png")


def _resolve_data_dir():
    """The OS-native per-user data directory for Mumble.

    Branches on sys.platform so the byte-identical port builds land in the right
    place instead of the old ~/Mumble fallback (the Windows %APPDATA% logic
    produced ~/Mumble on Mac/Linux because APPDATA is unset there — STATUS Ports
    item). Windows behaviour is unchanged: %APPDATA%\\Mumble."""
    test_dir = (os.environ.get("MUMBLE_TEST_DATA_DIR") or "").strip()
    if test_dir:
        return os.path.abspath(os.path.expanduser(test_dir))
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, APP_NAME)
    if sys.platform == "darwin":
        return os.path.join(
            os.path.expanduser("~/Library/Application Support"), APP_NAME)
    # Linux / other POSIX — XDG base-dir spec.
    configured = (os.environ.get("XDG_DATA_HOME") or "").strip()
    # The XDG Base Directory specification requires these values to be
    # absolute.  Treating a relative value as valid made data location depend
    # on the launcher's current working directory.
    base = configured if configured and os.path.isabs(configured) \
        else os.path.expanduser("~/.local/share")
    return os.path.join(base, APP_NAME)


DATA_DIR = _resolve_data_dir()
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
HISTORY_JSON = os.path.join(DATA_DIR, "history.json")
HISTORY_TXT = os.path.join(DATA_DIR, "transcripts.txt")
CLIPBOARD_JSON = os.path.join(DATA_DIR, "clipboard.json")
CONV_STORE_JSON = os.path.join(DATA_DIR, "conv_store.json")
STATS_JSON = os.path.join(DATA_DIR, "stats.json")  # independent of transcripts/clipboard
LOG_PATH = os.path.join(DATA_DIR, "mumble.log")

# Runtime directory (where this package lives) + on-device LLM locations. The
# bundled llama.cpp binaries ship in app/llama-cpp-bin/; a user drops a small GGUF
# model into DATA_DIR/models/ (or points local_llm_model at one) to light up the
# fully-offline smart-mode lane. See local_engine.LlamaCliBackend / discover_model.
APP_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(DATA_DIR, "models")
LLAMA_BIN_DIR = os.path.join(APP_DIR, "llama-cpp-bin")


def llama_cli_path():
    """Return a usable bundled or system-installed llama.cpp CLI binary."""
    exe = "llama-cli.exe" if os.name == "nt" else "llama-cli"
    p = os.path.join(LLAMA_BIN_DIR, exe)
    if os.path.isfile(p) and (os.name == "nt" or os.access(p, os.X_OK)):
        return p
    found = shutil.which(exe)
    return os.path.abspath(found) if found else ""


def cmd_token_path():
    """Per-session auth token for the localhost command channel (mumble.py ⇄
    webui_shell.py and the single-instance signal). Stored in DATA_DIR so only
    same-user processes can read it — the channel is otherwise unauthenticated,
    so any local process could inject a paste / grab the current selection. See
    mumble._ensure_cmd_token."""
    return os.path.join(DATA_DIR, "cmd_token")


# Linux IPC must not use a system-wide TCP port. Another local account could
# pre-bind that port and receive the bearer token before impersonating the web
# shell. Filesystem AF_UNIX sockets below an owner-only runtime directory give
# each account a protected namespace; SO_PEERCRED then verifies the peer before
# either client sends a token. Non-Linux source/test runs retain loopback TCP.
_IPC_ROLES = frozenset(("controller", "webui", "instance"))


def _secure_ipc_leaf(parent, name):
    """Create/validate an owner-only, non-symlink runtime directory."""
    path = os.path.join(parent, name)
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    info = os.lstat(path)
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.getuid()):
        raise RuntimeError("Mumble IPC directory is not owned by this user")
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(path, 0o700)
        info = os.lstat(path)
        if stat.S_IMODE(info.st_mode) != 0o700:
            raise RuntimeError("Mumble IPC directory is not private")
    return path


def ipc_runtime_dir():
    """Return a validated 0700 directory for this Linux user's local sockets."""
    if not sys.platform.startswith("linux"):
        return ""
    uid = os.getuid()
    identity = hashlib.sha256(
        os.path.abspath(DATA_DIR).encode("utf-8", "surrogatepass")
    ).hexdigest()[:12]
    leaf = f"mumble-vtt-{identity}"
    configured = (os.environ.get("XDG_RUNTIME_DIR") or "").strip()
    if configured and os.path.isabs(configured):
        try:
            base = os.lstat(configured)
            if (stat.S_ISDIR(base.st_mode) and not stat.S_ISLNK(base.st_mode)
                    and base.st_uid == uid
                    and stat.S_IMODE(base.st_mode) == 0o700):
                return _secure_ipc_leaf(configured, leaf)
        except OSError:
            pass
    # Fall back inside the already owner-only data directory. A predictable
    # /tmp leaf could be pre-created by another account to deny startup.
    ensure_dirs()
    base = os.lstat(DATA_DIR)
    if (not stat.S_ISDIR(base.st_mode) or stat.S_ISLNK(base.st_mode)
            or base.st_uid != uid or stat.S_IMODE(base.st_mode) != 0o700):
        raise RuntimeError("Mumble data directory is not private enough for IPC")
    return _secure_ipc_leaf(DATA_DIR, ".runtime-" + identity)


def ipc_address(role, fallback_port):
    """Address for one app-local channel (AF_UNIX on Linux, TCP elsewhere)."""
    if role not in _IPC_ROLES:
        raise ValueError("unknown Mumble IPC role")
    if sys.platform.startswith("linux"):
        path = os.path.join(ipc_runtime_dir(), role + ".sock")
        if len(os.fsencode(path)) >= 104:
            raise RuntimeError("Mumble IPC socket path is too long")
        return path
    return ("127.0.0.1", int(fallback_port))


def ipc_peer_is_current_user(sock):
    """Verify a connected AF_UNIX peer before privileged data is exchanged."""
    if not sys.platform.startswith("linux"):
        return True
    import socket
    import struct
    try:
        raw = sock.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", raw)
        return uid == os.getuid()
    except (AttributeError, OSError, struct.error):
        return False


def ipc_connect(role, fallback_port, timeout=2.0):
    """Connect to an authenticated same-user local app endpoint."""
    import socket
    address = ipc_address(role, fallback_port)
    if isinstance(address, tuple):
        return socket.create_connection(address, timeout=timeout)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(timeout)
        client.connect(address)
        if not ipc_peer_is_current_user(client):
            raise PermissionError("Mumble IPC peer belongs to another user")
        return client
    except Exception:
        client.close()
        raise


def ipc_bind_server(role, fallback_port, backlog=4):
    """Bind a local server, safely reclaiming only a stale same-user socket."""
    import socket
    address = ipc_address(role, fallback_port)
    family = socket.AF_INET if isinstance(address, tuple) else socket.AF_UNIX
    server = socket.socket(family, socket.SOCK_STREAM)
    try:
        if isinstance(address, tuple):
            server.bind(address)
        else:
            try:
                server.bind(address)
            except OSError as bind_error:
                # Reclaim only after a definite connection refusal. Timeouts,
                # full backlogs, and peer-check failures can all describe a
                # live endpoint and must never cause its pathname to be unlinked.
                stale = False
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    probe.settimeout(0.25)
                    probe.connect(address)
                    if not ipc_peer_is_current_user(probe):
                        raise PermissionError(
                            "Mumble IPC endpoint belongs to another user")
                except ConnectionRefusedError:
                    stale = True
                except OSError as probe_error:
                    if probe_error.errno == errno.ECONNREFUSED:
                        stale = True
                    else:
                        raise bind_error
                finally:
                    probe.close()
                if not stale:
                    raise bind_error
                try:
                    info = os.lstat(address)
                    if (not stat.S_ISSOCK(info.st_mode)
                            or info.st_uid != os.getuid()):
                        raise RuntimeError("unsafe stale Mumble IPC path")
                    os.unlink(address)
                except FileNotFoundError:
                    pass
                server.bind(address)
            os.chmod(address, 0o600)
        server.listen(max(1, int(backlog)))
        return server
    except Exception:
        server.close()
        raise


def _adopt_legacy_data_dir():
    """One-time: a pre-native-path port build stored data in ~/Mumble. If that
    legacy dir exists and the native dir doesn't yet, move it so a Mac/Linux user
    who ran an old build keeps their data. No-op on Windows and once migrated."""
    if sys.platform.startswith("win"):
        return
    legacy = os.path.join(os.path.expanduser("~"), APP_NAME)
    if os.path.abspath(legacy) == os.path.abspath(DATA_DIR):
        return
    try:
        if os.path.isdir(legacy) and not os.path.exists(DATA_DIR):
            os.makedirs(os.path.dirname(DATA_DIR), exist_ok=True)
            try:
                os.replace(legacy, DATA_DIR)
            except OSError as move_error:
                # XDG_DATA_HOME is commonly placed on another filesystem.  A
                # rename then raises EXDEV; copy+remove through shutil.move so
                # existing history/settings are not silently abandoned.
                if move_error.errno != errno.EXDEV:
                    raise
                shutil.move(legacy, DATA_DIR)
            print(f"Mumble: migrated data {legacy} -> {DATA_DIR}")
    except (OSError, shutil.Error) as e:
        print(f"Mumble: legacy data adopt skipped ({type(e).__name__}: {e})")


def ensure_dirs():
    try:
        _adopt_legacy_data_dir()
        os.makedirs(DATA_DIR, exist_ok=True)
        protect_private_path(DATA_DIR, directory=True)
    except OSError as e:
        print(f"FATAL: could not create data directory {DATA_DIR}: {e}")


def protect_private_path(path, directory=False):
    """Best-effort POSIX privacy mode for user data and reusable secrets."""
    if os.name == "nt" or not path:
        return
    try:
        os.chmod(path, 0o700 if directory else 0o600)
    except OSError:
        pass


# ---- Per-session IPC token (localhost command-channel authentication) ---------
# The controller (mumble.py) and the web window (webui_shell.py) talk over fixed
# loopback ports (49519 / 49520 / 49517). Those bind to 127.0.0.1, but ANY local
# process — including a malicious web page doing fetch('http://127.0.0.1:49519')
# — could otherwise inject privileged commands (paste arbitrary keystrokes,
# exfiltrate the highlighted selection, run AI jobs on the user's key). We gate
# the channels with a per-session random token the controller writes here at
# startup; both processes read it and every command carries it. A web page can
# open the socket but CANNOT read this file, so the token fully defeats that
# class of attack. (A same-user process that can read this file can already read
# the API keys in settings.json, so the token adds no new exposure there.)
SESSION_TOKEN_PATH = os.path.join(DATA_DIR, ".session_token")


def write_session_token():
    """Generate a fresh per-session IPC token, persist it (0600 where the OS
    honours it), and return it. The CONTROLLER calls this once at startup. Returns
    "" only if the data dir is unwritable — callers then run unauthenticated
    (no worse than before this scheme existed), so a token hiccup never blocks
    startup."""
    import secrets
    ensure_dirs()
    tok = secrets.token_urlsafe(32)
    try:
        fd = os.open(SESSION_TOKEN_PATH,
                     os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        try:
            os.write(fd, tok.encode("ascii"))
        finally:
            os.close(fd)
        return tok
    except OSError as e:
        print(f"session token write failed: {e}")
        return ""


def read_session_token():
    """Read the current session IPC token, or "" if absent/unreadable (e.g. no
    controller running, or a standalone web-UI preview). Callers treat "" as
    'no auth available'."""
    try:
        with open(SESSION_TOKEN_PATH, "r", encoding="ascii") as f:
            return f.read().strip()
    except OSError:
        return ""


def token_ok(supplied, expected):
    """Constant-time IPC-token comparison. Both sides must be non-empty to match,
    so a missing/empty token never authenticates."""
    import hmac
    if not supplied or not expected:
        return False
    return hmac.compare_digest(str(supplied), str(expected))


# ---- The Big Shift: hardware- & language-aware local model selection ---------
#
# Users tell us their PC class and whether they only speak English; we resolve a
# concrete faster-whisper model id. Mapping is grounded in the model research:
# .en variants are lighter / faster / more accurate for English at the small end;
# distil-large-v3 / large-v3-turbo are the best speed-accuracy point for strong
# PCs. faster-whisper auto-downloads any of these ids on first use.
MODEL_BY_TIER = {
    # tier:        (english_only,            multilingual)
    "weak": ("base.en", "base"),
    "mid": ("small.en", "small"),
    "powerful": ("distil-large-v3", "large-v3-turbo"),
}
HARDWARE_TIERS = ("weak", "mid", "powerful")


def resolve_model(hardware_tier, english_only=True):
    """Map a hardware tier + language preference to a faster-whisper model id.
    Unknown tiers fall back to the safe mid/small default."""
    tier = (hardware_tier or "mid").strip().lower()
    if tier not in MODEL_BY_TIER:
        tier = "mid"
    en, multi = MODEL_BY_TIER[tier]
    return en if english_only else multi


# Primary-language → use the lean English-only (.en) model? faster-whisper ships
# ONE multilingual model per tier (it already covers all 99 languages) plus
# lighter .en variants, so the only model-relevant distinction a primary language
# makes today is English vs not: English gets the faster/leaner .en model, any
# other language needs the multilingual one. A language absent from this map
# defaults to multilingual. (A future engine with genuine per-language models
# would extend this map; faster-whisper has none, so it stays binary.)
MODEL_BY_LANGUAGE = {
    "en": True,  # English → English-only (.en) model family
}


def model_for_language(hardware_tier, primary_language="en"):
    """Resolve the on-device model for a hardware tier + the user's primary
    dictation language. English → the lean .en model; any other language → the
    multilingual model (which faster-whisper uses for every non-English tongue)."""
    lang = (primary_language or "en").strip().lower()
    english_only = MODEL_BY_LANGUAGE.get(lang, False)
    return resolve_model(hardware_tier, english_only=english_only)


def _total_ram_gb():
    """Best-effort total physical RAM in GB (Windows GlobalMemoryStatusEx; falls
    back to os.sysconf on POSIX). Returns 0.0 if it can't be determined."""
    try:
        import ctypes

        class _MEMSTAT(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        st = _MEMSTAT()
        st.dwLength = ctypes.sizeof(_MEMSTAT)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return st.ullTotalPhys / (1024 ** 3)
    except Exception:
        pass
    try:
        return (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")) / (1024 ** 3)
    except Exception:
        return 0.0


def detect_hardware_tier():
    """One-time CPU/RAM probe → "weak" | "mid" | "powerful". Conservative so a
    weak PC is never handed a model that bricks it. Thresholds from the model
    research: weak < 4 cores or < 8GB RAM; powerful >= 8 cores AND >= 16GB RAM;
    everything else is mid."""
    try:
        cores = os.cpu_count() or 2
    except Exception:
        cores = 2
    ram = _total_ram_gb()
    # When RAM can't be read (ram == 0), decide on cores alone but stay cautious.
    if cores <= 3 or (ram and ram < 8):
        return "weak"
    if cores >= 8 and (ram == 0 or ram >= 16):
        return "powerful"
    return "mid"


def get_hardware_info():
    """Return a dict with CPU core count, total RAM (GB), and resolved tier.
    Used by the web UI to show a hardware-awareness suggestion on the homepage.
    Cached: after the first probe the resolved tier is final."""
    try:
        cores = os.cpu_count() or 2
    except Exception:
        cores = 2
    ram = round(_total_ram_gb(), 1)
    tier = detect_hardware_tier()
    return {"cpu_cores": cores, "ram_gb": ram, "tier": tier}


def app_info():
    """Return a single dict with app metadata plus hardware-awareness fields
    (hw_cpu_cores, hw_ram_gb, hw_tier).  This is the one-stop function the
    WebUI shell calls to populate the homepage hardware-suggestion banner and
    the app-info pane without needing to collate multiple calls."""
    hw = get_hardware_info()
    return {
        "app_name": APP_NAME,
        "version": VERSION,
        "tagline": APP_TAGLINE,
        "hw_cpu_cores": hw["cpu_cores"],
        "hw_ram_gb": hw["ram_gb"],
        "hw_tier": hw["tier"],
    }


# ---- design system: GOLDEN BLACK ------------------------------------------
class C:
    bg = "#0A0A0B"          # near-black window
    surface = "#121110"     # warm black panels
    surface2 = "#1A1813"    # inputs / chips
    elevated = "#241F16"    # hover
    border = "#2B2519"      # warm dark border
    border_soft = "#221E16"
    text = "#F3EEE1"        # warm white
    text_dim = "#B6AE99"
    text_mute = "#7C745F"

    gold = "#D4AF37"        # primary accent
    gold_hi = "#EBCB65"     # bright gold (hover/active)
    gold_dim = "#8C7320"
    gold_deep = "#B8941F"
    accent = gold           # alias used by shared widgets
    accent_hi = gold_hi
    accent_dim = gold_dim

    amber = "#E0A92E"
    red = "#D9544D"
    track = "#2A2418"


FONT = "Segoe UI"
FONT_SB = "Segoe UI Semibold"

# Distinct per-mode accents (used by island flash + history tags + Modes cards).
# Owner v6: each mode reads STRONGLY as its own colour — Prompt is a vivid,
# unmistakable purple (was a pale lavender that washed out to gold). There is no
# longer a dedicated "context" colour: context-gathering is communicated by the
# island's gold-particle absorption MOTION, not a tint, so "context" maps to gold.
MODE_COLORS = {
    "text": "#D4AF37",      # gold
    "prompt": "#A855F7",    # vivid purple — strongly, unmistakably purple
    "email": "#5AA9E6",     # blue
    "reply": "#E8825A",     # coral
    "foreign": "#C7A36B",   # warm sand (other-language / foreign-term pass)
    "convert": "#D86E9A",   # rose — router into other Smart Modes
    "context": "#D4AF37",   # NO dedicated colour — context = gold-particle motion
}
MODE_LABELS = {"text": "Text", "prompt": "Prompt", "email": "Email",
               "reply": "Reply", "foreign": "Foreign",
               "convert": "Convert", "context": "Context"}

# Status colors for the tray icon + window status dot.
STATE_COLORS = {
    "loading": "#8C7320",
    "idle": "#D4AF37",
    "listening": "#EBCB65",
    "transcribing": "#E0A92E",
    "error": "#D9544D",
}

MODES_INFO = [
    ("text", "Text", "Clean dictation",
     "Your everyday mode. Removes filler words, fixes capitalization and "
     "punctuation, and understands spoken commands like “new line”, "
     "“new paragraph” and “bullet point”.",
     "Just start talking."),
    ("prompt", "Prompt", "Craft an AI prompt",
     "Turns a rough request into a clean, structured prompt with a role, a clear "
     "task, and best-practice guidelines — ready to paste into any AI.",
     "Start with “prompt …”  ·  e.g. “prompt, write a poem about the sea”"),
    ("email", "Email", "Draft a tidy email",
     "Formats your speech into a polished email with a greeting, body, and "
     "sign-off. Add a recipient with “to …” and a subject with “about …”.",
     "Start with “email …”  ·  e.g. “email to Alex about the launch …”"),
]

SPOKEN_COMMANDS = [
    ("highlight + mode key + “prompt”", "Turn the text you’ve highlighted into a prompt"),
    ("Ctrl + Alt + V", "Paste your most recent transcript into the focused field"),
    ("Ctrl + Alt + D", "Open the History window (transcripts · clipboard · prompts)"),
    ("“new line”", "Start a new line"),
    ("“new paragraph”", "Insert a blank line"),
    ("“bullet point”", "Begin a bullet"),
]
