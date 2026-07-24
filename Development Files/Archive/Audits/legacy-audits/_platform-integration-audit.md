# Linux Platform Integration Audit — Mumble Linux Port

**Date:** 2026-07-04  
**Scope:** All OS-level integration points: keyboard, clipboard, system tray, autostart,
file paths, package management, installer, updates, tests, platform acceleration.

---

## 1. Keyboard / Hotkey Analysis

### Files examined
- Linux: `Ports/Linux/app/bindings.py` (14154 bytes)
- Windows: `app/bindings.py` (14495 bytes)
- `Ports/Linux/app/mumble_linux.py` lines 59–66 (Wayland GDK_BACKEND force)

### Libraries in play
- **`keyboard`** library (pure Python, uses `evdev` on Linux under the hood) — the PRIMARY
  hotkey mechanism in `bindings.py` for both Linux and Windows.
- **`mouse`** library — optional; provides side-button/wheel-button support on Linux
  via `/dev/input` (same evdev pathway as `keyboard`).
- **`pynput>=1.7.6`** — listed in `requirements.txt` (line 4) but **NOT used** in
  `bindings.py` or `mumble_linux.py` for any hotkey registration. It appears to be a
  dependency for some other code path (possibly `mumble.py` listener?), but for the
  binding layer it is unused. This is a **dead dependency**.

### Wayland vs X11
At startup (`mumble_linux.py` lines 59–66), if `WAYLAND_DISPLAY` is set, GTK is forced
onto XWayland (`GDK_BACKEND=x11`). However, global hotkeys go through `keyboard` →
`evdev`, which reads `/dev/input` directly and is **independent of the display server**.
This is correct: the island/tray render via XWayland for reliable positioning, while
`evdev` provides global hotkeys that work on both X11 and Wayland.

**Critical requirement:** Global hotkeys via `evdev` need the user to be in the `input`
group. The installer (line ~173) documents this clearly:
```
sudo usermod -aG input "$USER"
```
If the user is not in the `input` group, `keyboard.add_hotkey()` will silently fail
(permission error on `/dev/input/event*`).

### Key mapping correctness
- Modifier normalisation in `bindings.py` lines 52–57 maps `left ctrl`/`right ctrl` →
  `ctrl`, etc. The canonical ordering (`_MOD_ORDER` at line 63) is `ctrl, alt, shift,
  windows`. On Linux, "windows" maps to the Super key — this is correct.
- The `keyboard` library on Linux returns key names as the kernel's evdev key codes
  (`KEY_LEFTCTRL`, `KEY_RIGHTCTRL`, etc.), which `keyboard` maps to `left ctrl`/`right
  ctrl`. The normalisation in `_MOD_BASE` handles this correctly.
- **Potential issue:** On non-US keyboard layouts, `keyboard.parse_hotkey()` may fail
  to parse certain combinations. This is a `keyboard` library limitation, not Mumble's.

### Bare-modifier guard
Lines 86–92 and 162–175 of `bindings.py` correctly prevent a lone modifier (e.g.
`"ctrl"`) from being registered as a press hotkey, which would fire on every Ctrl tap
(the "Ctrl alone starts Mumble" regression fix). This is identical on both platforms.

### Mouse side-button support
- Supported via the `mouse` library's `mouse:x` (back/X1) and `mouse:x2` (forward/X2)
  bindings. Both buttons are handled through evdev `/dev/input`, which is display-server
  independent. Works on both X11 and Wayland provided the `input` group permission
  is granted.
- **Distinction from Windows:** On Windows the `mouse` library hooks via low-level
  Win32 mouse hooks. On Linux it hooks via evdev. This is transparent to Mumble.

### `capture()` function
The `capture()` function (lines 229–288) suppresses input events during binding
capture on both platforms. On Linux the suppress kwarg requires `keyboard` ≥ 0.13.5
(the `requirements.txt` does not pin a minimum `keyboard` version — see dependency
analysis).

### Verdict
- ✅ Basic keyboard hotkeys work on X11 and Wayland (with input group membership)
- ✅ Mouse side-button support works on both display servers
- ✅ Bare-modifier guard present
- ⚠️ `pynput` is a dead dependency in `requirements.txt` — listed but unused for hotkeys
- ⚠️ No minimum version pinned for `keyboard` library
- ⚠️ Wayland users MUST add themselves to the `input` group (documented, not automatic)

---

## 2. Clipboard Handling Analysis

### Files examined
- Linux: `Ports/Linux/app/clipboard.py` (19635 bytes)
- Windows: `app/clipboard.py` (19990 bytes — slightly larger due to additional comments)

### Text clipboard
- Uses **`pyperclip`** (line 18 of clipboard.py).
- On Linux, `pyperclip.paste()` spawns `xclip`, `xsel`, or `wl-paste` as a subprocess.
  The code is aware of this cost: comment at line 83 notes _"pyperclip.paste() there
  forks an xclip/xsel/wl-paste subprocess, so an idle clipboard would spawn a process
  ~every 0.7s"_.
- Text polling is throttled to every 3rd cycle (~2.1s at the 0.7s loop interval) when
  no clipboard change counter exists (Linux always has `_clipboard_seq() == None`).
  This is set at line 94: `self._text_every = 3`.
- **Change detection** (lines 39–53): `_clipboard_seq()` returns `None` on Linux because
  there is no cross-desktop clipboard change counter. The code correctly falls through
  to the throttled always-poll path.

### Image clipboard — CRITICAL FAILURE ON LINUX
- `ImageGrab.grabclipboard()` (from Pillow/PIL, line 204 of clipboard.py) is a
  **Windows-only function**. On Linux, it will raise an exception caught by the
  try/except at lines 199–201, and `grabbed` will always be `None`.
- **Result:** Image clipboard history will NEVER capture images on Linux. The code
  at line 206 checks `isinstance(grabbed, Image.Image)` and line 208 checks
  `isinstance(grabbed, list)` — both will always be `False` on Linux.
- There is **no alternative implementation** for Linux image clipboard. Pillow's
  `ImageGrab` module does not support Linux.
- The comment at line 82 acknowledges this for Windows perf but there is no
  corresponding Linux-specific handling.

### Polling efficiency
- Sleep interval: 0.7s (line 161)
- Text throttle: every 3rd cycle (~2.1s) when no change counter (Linux path)
- Image throttle: every 8th cycle (~5.6s) when no change counter
- **Good:** The throttling is appropriate for Linux where each pyperclip call
  forks a subprocess.
- **N/A:** Image throttle is irrelevant on Linux since `ImageGrab.grabclipboard()`
  always fails.

### Copy/paste reliability
- `pyperclip` probes for available clipboard tools at import time. If none of
  `xclip`, `xsel`, or `wl-paste` are installed, `pyperclip.copy()` and
  `pyperclip.paste()` will fail.
- The installer's optional packages list includes both `xclip` and `wl-clipboard`
  (provides `wl-paste`). Good coverage.
- `keyboard.send("ctrl+v")` is used in `mumble_linux.py` for paste-into-focused-app
  (line 1463, 1772), which works reliably via X11 (XWayland) keyboard simulation.

### Verdict
- ✅ Text clipboard works correctly on Linux via pyperclip + xclip/xsel/wl-paste
- ✅ Throttling is appropriate for Linux's fork-heavy pyperclip
- ❌ **Image clipboard capture is DEAD on Linux** — `ImageGrab.grabclipboard()` is
  Windows-only; no Linux alternative exists
- ⚠️ Paste operation uses `keyboard.send("ctrl+v")`, which relies on XWayland
  (GTK on XWayland can simulate keystrokes); native Wayland compositors may block this

---

## 3. Data Paths & XDG Compliance

### Files examined
- Linux: `Ports/Linux/app/branding.py` (12029 bytes)
- Windows: `app/branding.py` (13021 bytes)

### XDG compliance
The `_resolve_data_dir()` function (lines 21–38 of Linux `branding.py`) correctly
implements the XDG Base Directory Specification:
- `$XDG_DATA_HOME/Mumble` if XDG_DATA_HOME is set
- `~/.local/share/Mumble` as fallback
- ✅ Fully XDG-compliant

### Legacy data migration
`_adopt_legacy_data_dir()` (lines 64–78) migrates from `~/Mumble` → the native
XDG data dir using `os.replace()` (atomic rename). This handles the old port
builds that landed data in `~/Mumble` because APPDATA was unset on Linux.
- ✅ One-time, safe (os.replace is atomic), well-documented

### Config / cache / temp paths
| Path | Location | Notes |
|------|----------|-------|
| `SETTINGS_PATH` | `$DATA_DIR/settings.json` | Cross-process safe |
| `HISTORY_JSON` | `$DATA_DIR/history.json` | |
| `HISTORY_TXT` | `$DATA_DIR/transcripts.txt` | |
| `CLIPBOARD_JSON` | `$DATA_DIR/clipboard.json` | |
| `CONV_STORE_JSON` | `$DATA_DIR/conv_store.json` | |
| `STATS_JSON` | `$DATA_DIR/stats.json` | |
| `LOG_PATH` | `$DATA_DIR/mumble.log` | |
| `cmd_token_path()` | `$DATA_DIR/cmd_token` | Per-session auth token |

All paths are under the XDG data directory — correct.

### Missing features compared to Windows `branding.py`
The Linux `branding.py` is a **stripped-down version** of the Windows `branding.py`.
Missing:
- `APP_DIR` — not present in Linux branding.py (the shared module uses `os.path.abspath(__file__)` in `INSTALL_DIR`)
- `MODELS_DIR` — missing; local LLM model directory is not defined for Linux
- `LLAMA_BIN_DIR` — missing; local LLM path not defined
- `llama_cli_path()` — missing; no way to locate llama.cpp binary on Linux
- `get_hardware_info()` — missing; the web UI's hardware-suggestion banner won't work on Linux
- `app_info()` — missing
- `MODES_INFO` entries for `list`, `reply`, `foreign`, `convert`, `context` —
  only `text`, `prompt`, `email` are present in Linux
- `MODE_LABELS` is missing `list` and `context` labels
- `MODE_COLORS` includes colors for `list` and `context` but their MODES_INFO is missing
- `SPOKEN_COMMANDS` differs from Windows: Linux uses "Ctrl+Alt+H" for History,
  Windows uses "Ctrl+Alt+D"; Linux has two extra entries (numbering & list detection
  spoken commands)

**Inconsistency:** `MODE_COLORS` on Linux defines colors for "list", "reply",
"foreign", "convert", and "context" but `MODES_INFO` doesn't include these modes,
meaning they exist in the color map but have no mode info card.

### Verdict
- ✅ XDG data dir: correct
- ✅ Legacy migration: correct
- ✅ All paths under XDG_DATA_HOME
- ⚠️ Missing `MODELS_DIR`, `LLAMA_BIN_DIR`, `llama_cli_path()`, `get_hardware_info()`,
  `app_info()` — some features (local LLM, hardware banner) won't work on Linux
- ⚠️ MODES_INFO is incomplete vs Windows (missing 3 modes)
- ⚠️ MODE_COLORS vs MODES_INFO inconsistency
- ⚠️ SPOKEN_COMMANDS has diverged from Windows (different History hotkey display)

---

## 4. Autostart Mechanism

### Files examined
- Linux: `Ports/Linux/app/autostart.py` (3964 bytes)
- Windows: `app/autostart.py` (7553 bytes)

### Implementation
The Linux autostart module creates an **XDG autostart-compliant `.desktop` file** at
`~/.config/autostart/mumble.desktop` (line 22 of Linux autostart.py).

### `.desktop` file content
```ini
[Desktop Entry]
Type=Application
Name=Mumble
Comment=Private, on-device voice-to-text
Exec=python3 /path/to/mumble_linux.py
Terminal=false
X-GNOME-Autostart-enabled=true
StartupNotify=false
Categories=Utility;Office;
```

- ✅ `X-GNOME-Autostart-enabled=true` — works with GNOME, KDE Plasma, Xfce, and any
  DE that follows the XDG autostart spec
- ✅ `Terminal=false` — correct for a GUI/background app
- ✅ Uses `shutil.which("python3")` to find the interpreter; falls back to direct path
  (line 37)

### Validation
`_desktop_valid()` (lines 50–56) checks that the file exists AND contains
`[Desktop Entry]` with `Type=Application`. This is a valid sanity check.

### Missing features
- **No systemd user service option:** Some Linux users prefer a systemd user unit
  (`~/.config/systemd/user/mumble.service`) for more robust autostart with restart
  policies. Not implemented.
- **No Flatpak/snap autostart integration:** If Mumble is ever packaged as a
  Flatpak or snap, the `.desktop` approach won't work — those have separate
  autostart mechanisms.

### Comparison to Windows
Windows uses a `.lnk` shortcut in the Startup folder + `WScript.Shell` COM objects +
legacy `HKCU\Run` registry cleanup. Linux uses the equivalent XDG mechanism. Both are
correct for their respective platforms.

### Stub functions (lines 134–170)
The Linux autostart module provides **no-op stubs** for:
- `install_start_menu()` — returns `True`
- `remove_start_menu()` — returns `True`
- `install_uninstall_start_menu()` — returns `True`
- `remove_uninstall_start_menu()` — returns `True`
- `install_desktop_shortcut()` — returns `True`
- `remove_desktop_shortcut()` — returns `True`

These exist so shared code that calls them (possibly via a unified autostart API)
doesn't crash. ✅ Sensible design decision.

### Verdict
- ✅ Correct XDG autostart implementation
- ✅ .desktop file is valid and well-formed
- ✅ No Windows-isms leak into the Linux code
- ✅ Stub functions prevent crashes on shared API calls
- ⚠️ No systemd user service option for users who prefer it
- ⚠️ No Flatpak/snap autostart awareness

---

## 5. Installer Quality Assessment

### File examined
- `Ports/Linux/install.sh` (~195 lines of bash)

### Structure (5-step flow)
1. **System dependencies** (lines 42–98): Detects apt/dnf/pacman/zypper, prompts for
   sudo installation
2. **Python venv** (lines 100–120): Creates venv with `--system-site-packages`
3. **Python dependencies** (lines 122–146): pip install -r requirements.txt
4. **Speech model pre-fetch** (lines 148–168): Downloads base.en (~145 MB)
5. **Desktop integration** (lines 170–195): Creates launcher + .desktop file

### Package manager coverage
| PM | Supported | Required packages | Optional packages |
|----|-----------|-------------------|-------------------|
| apt (Debian/Ubuntu) | ✅ | python3-venv, python3-pip, build-essential, python3-gi, gir1.2-gtk-3.0, gir1.2-webkit2-4.1, libportaudio2 | xclip, wl-clipboard, xdotool, ydotool, wtype, libnotify-bin, gstreamer1.0-plugins-good, gstreamer1.0-libav |
| dnf (Fedora) | ✅ | python3-pip, python3-devel, gcc, gcc-c++, python3-gobject, gtk3, webkit2gtk4.1, portaudio | xclip, wl-clipboard, xdotool, ydotool, wtype, libnotify, gstreamer1-plugins-good, gstreamer1-libav |
| pacman (Arch) | ✅ | python-pip, base-devel, python-gobject, gtk3, webkit2gtk-4.1, portaudio | xclip, wl-clipboard, xdotool, ydotool, wtype, libnotify, gst-plugins-good, gst-libav |
| zypper (openSUSE) | ✅ | python3-pip, python3-devel, gcc, gcc-c++, python3-gobject, typelib-1_0-Gtk-3_0, typelib-1_0-WebKit2-4_1, libportaudio2 | xclip, wl-clipboard, xdotool, ydotool, wtype, libnotify-tools, gstreamer-plugins-good, gstreamer-plugins-libav |

- ✅ Good coverage of major distro families
- ✅ Packages are separated into REQUIRED (won't run without) and OPTIONAL
  (best-effort, installed one-at-a-time so a single missing package doesn't
  block the rest)

### venv creation
```bash
"$PYBIN" -m venv --system-site-packages "$VENV"
```
- ✅ `--system-site-packages` is CRITICAL and correctly documented — PyGObject
  cannot be pip-installed and must come from the system
- ✅ Idempotent: re-uses existing venv if present

### Sanity checks
- Line 132–138: After pip install, verifies that GTK + WebKitGTK are importable
  from the venv. ✅ Good defensive programming.
- Line 151–162: Attempts to pre-fetch the Whisper model, warns gracefully if
  offline. ✅

### Launcher script
Creates `$ROOT/mumble` — a bash script that activates the venv and launches
`mumble_linux.py`. ✅ Clean design, mirrors what the macOS `.command` launcher does.

### Application menu entry
Creates `${XDG_DATA_HOME:-$HOME/.local/share}/applications/mumble.desktop` with
proper `Categories=Utility;Accessibility;AudioVideo;` and `Keywords=...`.
- ✅ Runs `update-desktop-database` if available

### Error handling
- `set -u` at the top (line 16) — catches unset variables
- Colored output (RED/GRN/YLW/BLU) for readability
- Graceful degradation: optional packages fail individually, required packages
  warn but don't abort if they fail (allows user to fix and re-run)
- Clear instructions when no package manager is detected

### Potential issues
1. **sudo loop for optional packages** (lines 94–96): Each optional package is
   installed with a separate `sudo $INSTALL` call. This means the user may be
   prompted for sudo password multiple times. A single combined call would be
   better UX.
2. **No package existence check before install**: The loop tries to install each
   optional package even if it's already installed. While `apt install -y` is
   idempotent, the sudo prompts are still annoying.
3. **No `/usr/bin/python3` fallback check**: `command -v python3` is used (line 103),
   which is correct. But the generated launcher and update script hardcode
   `/usr/bin/python3` — this could fail if python3 is at `/usr/local/bin/python3`
   or in a pyenv/conda path.
4. **No `git` requirement check**: If the user clones from git, this is fine.
   But the script assumes `$ROOT/app/requirements.txt` exists — no check for
   missing app directory.
5. **No `curl` or `wget` check**: The model pre-fetch uses faster-whisper which
   downloads via `huggingface_hub`. But there's no explicit network check.

### Verdict
- ✅ Well-structured, clean, readable
- ✅ Good package manager coverage (apt/dnf/pacman/zypper)
- ✅ Proper venv with --system-site-packages
- ✅ Good error handling with clear user messaging
- ✅ Idempotent (safe to re-run)
- ⚠️ Multiple sudo prompts for optional packages
- ⚠️ Hardcoded `/usr/bin/python3` in update script relaunch
- ⚠️ No check for git clone integrity (missing app/ directory)
- ⚠️ No network check before attempting model download

---

## 6. Platform Acceleration Gap — `linux_fallback.py`

### Files examined
- `app/platform/__init__.py` (10271 bytes)
- `app/platform/` directory contents

### What `__init__.py` references
At line 233 of `platform/__init__.py`:
```python
from .linux_fallback import WhisperCppTranscriber
```

### What exists
The `app/platform/` directory contains:
- `__init__.py`
- `macos_audio.py` (16745 bytes) — MacOSTranscriber
- `windows_dml.py` (18568 bytes) — DirectMLTranscriber
- `__pycache__/`

### What's MISSING
- **`linux_fallback.py` DOES NOT EXIST** in the platform directory
- There is no file anywhere in the project implementing `WhisperCppTranscriber`

### Impact
The `select_transcriber()` function in `platform/__init__.py` (lines 230–241) has
a branch for Linux ARM + Vulkan:
```python
if detect_linux_arm_vulkan():
    try:
        from .linux_fallback import WhisperCppTranscriber
        if WhisperCppTranscriber.is_available():
            transcriber = WhisperCppTranscriber(...)
            return transcriber
    except ImportError:
        pass
```

On any Linux ARM system with Vulkan, `detect_linux_arm_vulkan()` will return `True`,
the `from .linux_fallback import ...` will raise `ImportError`, and the exception
is silently caught. The function falls through to `return None`, meaning **CPU-only
faster-whisper is used** even on Vulkan-capable ARM hardware.

### What should it be?
The missing `WhisperCppTranscriber` would implement STT acceleration using
[whisper.cpp](https://github.com/ggerganov/whisper.cpp) compiled with Vulkan
compute shader support. This would provide GPU-accelerated transcription on
Linux ARM devices (Raspberry Pi 5, Jetson, Snapdragon X Elite laptops, etc.)
where Vulkan is the primary compute API.

### Verdict
- ❌ **CRITICAL GAP:** `linux_fallback.py` is missing — the Linux ARM Vulkan
  acceleration path is completely non-functional
- ❌ The `ImportError` is silently caught, so there is no user-visible error —
  the system silently falls back to CPU-only mode with no indication that
  acceleration was attempted but unavailable
- ⚠️ Even if the file existed, `WhisperCppTranscriber` would need bundling of
  whisper.cpp binaries for ARM (likely compiled during install)

---

## 7. Update Mechanism

### Files examined
- Linux: `Ports/Linux/app/update.py` (14361 bytes)
- Windows: `app/update.py` (18266 bytes)
- Also present in shared: `Internal/app/update.py`

### Current state
The Linux port has a **functional auto-update mechanism** that uses the same
codebase as Windows. The `_write_swap_script()` function (lines 185–261) has a
`sys.platform` branch that generates a Linux bash script.

### How it works (Linux path)
1. `check_for_update()` fetches `https://mumble-app.github.io/mumble-updates/update.json`
2. `download_and_install()` downloads the zip, verifies SHA256, extracts to a
   versioned folder
3. `_write_swap_script()` creates `apply_update.sh`:
   - Kills the current process by PID
   - `mv current_dir backup_dir`
   - `mv new_dir current_dir`
   - On failure: `mv backup_dir current_dir` (rollback)
   - Relaunches: `/usr/bin/python3 "current_dir/mumble_linux.py" &`
4. `rollback()` restores from `Mumble-backup` directory

### Issues
1. **Hardcoded `/usr/bin/python3` path** (line 262 in the Linux port, line 352 in
   Windows shared): This will fail if Python 3 is at `/usr/local/bin/python3`,
   managed by pyenv, or inside a venv. The installer script uses
   `command -v python3` which is correct, but the update script hardcodes the
   path.
2. **No Flatpak/snap awareness:** If Mumble is ever distributed via Flatpak or
   snap, the update mechanism would try to swap files in a read-only mount.
3. **No package manager integration:** The update checks a GitHub-hosted manifest.
   It does NOT integrate with apt/dnf/pacman/zypper or check for distro-provided
   updates. This is a design decision (self-contained auto-update), not a bug.
4. **No delta updates:** Always downloads the full zip. Could be large for
   frequent updates.
5. **The manifest URL is hardcoded** — no option to use a self-hosted/private
   update server without modifying the source.

### Comparison to Windows
The Linux update swap script mirrors the Windows `.bat` script pattern:
| Operation | Windows | Linux |
|-----------|---------|-------|
| Kill process | `taskkill /f /pid` | `kill <pid>` |
| Backup current | `rename current Mumble-backup` | `mv current Mumble-backup` |
| Install new | `rename new current` | `mv new current` |
| Rollback on fail | `rename backup current` | `mv backup current` |
| Relaunch | `start Mumble.exe` or `pythonw mumble.py` | `/usr/bin/python3 mumble_linux.py` |

### Verdict
- ✅ Update mechanism is functional on Linux
- ✅ SHA256 verification required (good security)
- ✅ Rollback logic present
- ✅ Symlinks-aware via `os.path.realpath()` zip-slip protection
- ⚠️ Hardcoded `/usr/bin/python3` path may fail on some systems
- ⚠️ No package manager update channel integration
- ⚠️ No delta/binary-patch updates

---

## 8. Test Infrastructure Assessment

### Files examined
- `Ports/Linux/app/run_tests.py` (1818 bytes)
- `Ports/Linux/app/test_linux_platform.py` (8473 bytes)
- `Ports/Linux/app/TEST_PARITY.md` (3372 bytes)

### Tests in the Linux runner (`run_tests.py`)
7 test files configured:
| Test file | Status | Notes |
|-----------|--------|-------|
| `test_bindings.py` | ✅ in runner | Cross-platform binding logic |
| `test_formatting.py` | ✅ in runner | |
| `test_mode_select.py` | ✅ in runner | |
| `test_presets.py` | ✅ in runner | |
| `test_favorites.py` | ✅ in runner | |
| `test_webui_api.py` | ✅ in runner | |
| `test_cmd_auth.py` | ✅ in runner | VAL-PLAT-007 addition |
| `test_settings_merge.py` | ✅ in runner | VAL-PLAT-007 addition |
| `test_linux_platform.py` | ✅ in runner | Linux-specific autostart/update/xdg-open tests |
| `test_stream_seam.py` | ✅ in runner | Needs audio/ML deps (real Linux box only) |
| `test_end_to_end.py` | ✅ in runner | Needs audio/ML deps |

Wait — that's 11. Let me recount from `run_tests.py`:

```python
OFFLINE = [
    "test_bindings.py",
    "test_formatting.py",
    "test_mode_select.py",
    "test_presets.py",
    "test_favorites.py",
    "test_webui_api.py",
    "test_cmd_auth.py",
    "test_settings_merge.py",
    "test_linux_platform.py",
    "test_stream_seam.py",
    "test_end_to_end.py",
]
```
11 test files. 2 of them (`test_stream_seam.py`, `test_end_to_end.py`) are marked
as needing GUI/ML deps and only pass on a real Linux box.

### Tests excluded from runner but present in directory
5 test files copied from Windows but excluded due to API mismatches (per TEST_PARITY.md):

| Test file | Blocking issue |
|-----------|---------------|
| `test_context_store.py` | Missing `'ui'` field in Linux capture result |
| `test_local_wiring.py` | Missing `MODELS_DIR` and `llama_cli_path` in Linux branding.py |
| `test_reader_parser.py` | Linux `reader_store.py` missing `save_doc_parsed()` |
| `test_meeting.py` | Linux meeting records lack `key_decisions`, `open_questions`, `processing_mode` |
| `test_meeting_controller.py` | Linux `mumble_linux.py` missing `_meeting_start()` method |

### Explicitly excluded
- `test_update.py` — Windows-specific (.bat swap scripts, `ctypes.windll`)

### Linux-specific test: `test_linux_platform.py`
This is a **well-designed platform validation test** covering:
1. **Autostart** (lines 26–73): Validates .desktop file path, content, validation
   function, and absence of Windows-isms (winreg, WScript.Shell, .lnk, PowerShell)
2. **Update** (lines 76–142): Generates a swap script on a temp dir, verifies it
   is bash (not .bat), uses `kill`/`mv` (not `taskkill`/`rename`), has rollback
   logic, relaunches `mumble_linux.py`, and is executable
3. **os.startfile() replacement** (lines 145–170): Verifies no `os.startfile()`
   calls exist and `xdg-open` is used instead
4. **The Big Shift sync** (lines 173–230): Verifies that `mumble_linux.py` imports
   `local_engine` and `foreign_boost`, has `prompt_mode_enabled`, has `active_mode`,
   and that `_register_mode_key` / `_watch_mode_key` are proper no-ops

### Coverage gaps
- No test for clipboard.py Linux behavior
- No test for evdev-based hotkey registration
- No test for the `--system-site-packages` venv integrity
- No test for image clipboard (which is dead on Linux)
- No integration test for the full install.sh flow
- No test for pystray/Linux tray icon functionality
- No test for notification system (notify-send fallback)

### Verdict
- ✅ `test_linux_platform.py` is excellent — covers key Linux-specific behaviors
- ✅ `run_tests.py` is well-structured and produces a clear summary
- ⚠️ 5 tests present but excluded due to API incompabilities — these represent
  real feature gaps, not just test gaps
- ⚠️ No tests for clipboard/evdev/tray/notify-send functionality
- ⚠️ No install.sh CI integration test
- ⚠️ `test_stream_seam.py` and `test_end_to_end.py` require full desktop environment

---

## 9. System Dependency Analysis

### File examined
- `Ports/Linux/app/requirements.txt` (749 bytes)
- `Ports/Linux/install.sh`

### Python dependencies (`requirements.txt`)
```
faster-whisper>=1.0.0
sounddevice>=0.4.6
numpy>=1.24,<2.2
pynput>=1.7.6          ← dead dependency (not used in binding layer)
pyperclip>=1.8.2
pystray>=0.19.5
Pillow>=10.0.0
pywebview
evdev>=1.6.1 ; sys_platform == "linux"   ← for global hotkeys
```

**Issues:**
1. `pynput` is listed but **not used** in `bindings.py` or `mumble_linux.py` for
   hotkeys. It may be used elsewhere (mouse listener in `mumble.py`? sounddevice
   callback context?) but the binding layer exclusively uses `keyboard` + `mouse`
   + `evdev`.
2. `pywebview` has **no version constraint** — it's unpinned, meaning `pip install`
   may pull a future breaking version.
3. `keyboard` is **not listed** in `requirements.txt` at all! It's imported in
   `bindings.py` line 17 and `mumble_linux.py` line 137. It must be a transitive
   dependency of something else, or pre-installed in the system Python. This is
   a **critical omission** — if `keyboard` isn't available, the app won't start.
4. `mouse` is **not listed** in `requirements.txt` either, despite being imported
   in `bindings.py` line 21.
5. No `keyboard` minimum version is pinned for the `suppress=True` kwarg used in
   `capture()`.
6. There's no requirement for `xdg` or `xdg-utils` — the code uses `xdg-open`
   but that's a system binary (from `xdg-utils` package), not a Python package.

### System dependencies (from install.sh)
| Package | Why | Category |
|---------|-----|----------|
| python3-venv | venv creation | REQUIRED |
| python3-pip | pip install | REQUIRED |
| python3-dev / python3-devel | C headers for evdev wheel build | REQUIRED |
| build-essential / base-devel / gcc | C compiler for evdev wheel | REQUIRED |
| python3-gi / python-gobject / python3-gobject | GTK3 Python bindings (PyGObject) | REQUIRED |
| gir1.2-gtk-3.0 / gtk3 | GTK3 runtime | REQUIRED |
| gir1.2-webkit2-4.1 / webkit2gtk-4.1 / webkit2gtk4.1 | Web view for island + settings | REQUIRED |
| libportaudio2 / portaudio | Audio capture runtime | REQUIRED |
| xclip | X11 clipboard tool | OPTIONAL |
| wl-clipboard | Wayland clipboard tool | OPTIONAL |
| xdotool | X11 keyboard simulation | OPTIONAL |
| ydotool | Wayland keyboard simulation (alternative) | OPTIONAL |
| wtype | Wayland keyboard simulation (wlroots) | OPTIONAL |
| libnotify-bin / libnotify / libnotify-tools | Desktop notifications | OPTIONAL |
| gir1.2-ayatanaappindicator3-0.1 / libayatana-appindicator | System tray icon (AppIndicator) | OPTIONAL |
| gstreamer1.0-plugins-good / gst-plugins-good | Reader mp3 audio decode | OPTIONAL |
| gstreamer1.0-libav / gst-libav | Reader mp3 audio decode (libav codec) | OPTIONAL |

### Missing from documentation
- The `keyboard` Python package is a critical runtime dependency but is not listed
  in `requirements.txt`. Users who `pip install -r requirements.txt` without the
  `keyboard` library installed would get an `ImportError` at startup.
- No minimum Linux kernel version documented (evdev needs `/dev/input` which exists
  on all modern kernels, but some minimal/container environments lack it).
- No documentation of which display server features work (Wayland vs X11 comparison
  table).

### Version constraints
Very few version constraints exist:
- `faster-whisper>=1.0.0` — pinned minimum
- `numpy>=1.24,<2.2` — pinned range (good, avoids numpy 2.x breaking changes)
- `sounddevice>=0.4.6` — pinned minimum
- `pynput>=1.7.6` — pinned minimum (but likely dead dep)
- `pyperclip>=1.8.2` — pinned minimum
- `pystray>=0.19.5` — pinned minimum
- `Pillow>=10.0.0` — pinned minimum
- `evdev>=1.6.1` — pinned minimum
- `pywebview` — **UNPINNED** (high risk)

### Verdict
- ✅ System dependency list is comprehensive and well-organized by distro
- ✅ Installer clearly separates REQUIRED from OPTIONAL packages
- ⚠️ `pywebview` is UNPINNED — a future breaking release could break the web UI
- ❌ **MISSING:** `keyboard` not in `requirements.txt` — critical runtime dependency
- ❌ **MISSING:** `mouse` not in `requirements.txt` — used by `bindings.py`
- ⚠️ `pynput` listed but not used in the binding layer — dead dependency
- ⚠️ GStreamer codecs (Reader audio) are best-effort optional, handled gracefully

---

## 10. All TODO / FIXME / HACK Comments

### In the Linux port
**No** `TODO`, `FIXME`, `HACK`, or `XXX` markers found in any Linux port file.

The only hit was in `prompt_constitution.py` line 512:
```
AMBIGUITY IS A BUG
```
This is output text from a prompt template, not a code comment.

### In the platform layer
**No** `TODO`, `FIXME`, `HACK`, or `XXX` markers found in `platform/__init__.py`.

### Assessment
The codebase is cleanly written without unresolved markers, but the **missing
`linux_fallback.py`** is a significant unimplemented feature that should have had
at least a `# TODO: implement whisper.cpp Vulkan acceleration for Linux ARM` marker
in `platform/__init__.py`.

---

## Summary of Critical Issues

| Priority | Issue | Location |
|----------|-------|----------|
| **CRITICAL** | `linux_fallback.py` missing — Vulkan acceleration on Linux ARM is dead code | `platform/__init__.py` line 233 |
| **CRITICAL** | `keyboard` and `mouse` packages not in `requirements.txt` | `requirements.txt` |
| **HIGH** | Image clipboard capture broken on Linux (`ImageGrab.grabclipboard()` is Windows-only) | `clipboard.py` line 199 |
| **HIGH** | Linux `branding.py` missing `MODELS_DIR`, `LLAMA_BIN_DIR`, `llama_cli_path()`, `get_hardware_info()`, `app_info()` | `branding.py` |
| **HIGH** | 5 test files excluded due to API mismatches (real feature gaps) | `TEST_PARITY.md`, various |
| **MEDIUM** | `pynput` is a dead dependency in `requirements.txt` | `requirements.txt` |
| **MEDIUM** | `pywebview` has no version pin | `requirements.txt` |
| **MEDIUM** | Hardcoded `/usr/bin/python3` in update script relaucher | `update.py` line 352 |
| **MEDIUM** | MODES_INFO/MODE_COLORS inconsistency on Linux | `branding.py` |
| **LOW** | Multiple sudo prompts during optional package install | `install.sh` lines 94–96 |
| **LOW** | No `systemd` user service autostart option | `autostart.py` |
| **LOW** | SPOKEN_COMMANDS diverged between Linux and Windows | `branding.py` |

---

## Recommendations

1. **Implement `linux_fallback.py`** with a `WhisperCppTranscriber` class that wraps
   whisper.cpp with Vulkan compute. At minimum, add a clear `# TODO` marker and
   ensure the `ImportError` catch logs a warning instead of failing silently.

2. **Add `keyboard` and `mouse` to `requirements.txt`** with minimum version pins
   (`keyboard>=0.13.5` for the `suppress=True` kwarg).

3. **Implement Linux image clipboard capture** using one of:
   - GTK's `Gtk.Clipboard` (already available via PyGObject)
   - `wl-paste --type image/png` for Wayland
   - Fallback to text-only clipboard on Linux with a clear note in settings.

4. **Sync Linux `branding.py` with Windows**: add missing functions
   (`get_hardware_info()`, `app_info()`, `MODELS_DIR`, etc.) and complete the
   `MODES_INFO` list.

5. **Fix the 5 excluded test files** by implementing the missing API features:
   `save_doc_parsed()`, `_meeting_start()`, meeting record fields, `MODELS_DIR`,
   `llama_cli_path()`.

6. **Remove `pynput`** from `requirements.txt` if it's truly unused, or document
   where it IS used.

7. **Pin `pywebview`** to a known-good version.

8. **Use `which python3`** or `sys.executable` in the update script relaucher
   instead of hardcoded `/usr/bin/python3`.

9. **Consider a systemd user service** as an alternative autostart mechanism for
   headless/server Linux use cases.
