#!/usr/bin/env python3
"""macOS platform-specific tests: autostart (LaunchAgent plist), update
(.command swap script), os.startfile() → subprocess.run(['open', path])
replacements, and The Big Shift sync verification.

Run: python test_mac_platform.py
"""
import os
import sys
import tempfile

# Add port directory to path so imports resolve
_port_dir = os.path.dirname(os.path.abspath(__file__))
if _port_dir not in sys.path:
    sys.path.insert(0, _port_dir)

_fails = []


def check(label, cond):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {label}")
    if not cond:
        _fails.append(label)


# ============================================ autostart (LaunchAgent plist)
print("\n== autostart: macOS LaunchAgent plist API ==")

import autostart

# Verify the plist path is macOS-native
check("plist path is in ~/Library/LaunchAgents",
      "Library" in autostart.PLIST_PATH and "LaunchAgents" in autostart.PLIST_PATH)
check("plist name is com.mumble.app.plist",
      autostart.PLIST_NAME == "com.mumble.app.plist")

# Verify the plist structure
plist = autostart._build_plist()
check("plist has Label", plist.get("Label") == "com.mumble.app")
check("plist has RunAtLoad=True", plist.get("RunAtLoad") is True)
check("plist has KeepAlive=False", plist.get("KeepAlive") is False)
check("plist has ProgramArguments (list)", isinstance(plist.get("ProgramArguments"), list))
check("plist ProgramArguments non-empty", len(plist.get("ProgramArguments", [])) > 0)
check("plist does not fall back to system python",
      plist.get("ProgramArguments", [""])[0] != "/usr/bin/python3")
check("plist has StandardOutPath", bool(plist.get("StandardOutPath")))
check("plist has StandardErrorPath", bool(plist.get("StandardErrorPath")))
check("plist has ProcessType=Interactive", plist.get("ProcessType") == "Interactive")

# Verify plist validity with plistlib
import plistlib as _pl
tmp = os.path.join(tempfile.gettempdir(), f"_mumble_test_autostart_{os.getpid()}.plist")
try:
    with open(tmp, "wb") as f:
        _pl.dump(plist, f)
    with open(tmp, "rb") as f:
        loaded = _pl.load(f)
    check("plist round-trips through plistlib", loaded == plist)
finally:
    try:
        os.remove(tmp)
    except OSError:
        pass

# Verify API surface
check("is_enabled is callable", callable(autostart.is_enabled))
check("enable is callable", callable(autostart.enable))
check("disable is callable", callable(autostart.disable))
check("set_enabled is callable", callable(autostart.set_enabled))
check("install_start_menu no-op", autostart.install_start_menu() is True)
check("remove_start_menu no-op", autostart.remove_start_menu() is True)
check("install_desktop_shortcut no-op", autostart.install_desktop_shortcut() is True)
check("remove_desktop_shortcut no-op", autostart.remove_desktop_shortcut() is True)

# A plist on disk is not enough: is_enabled must reflect launchd's loaded state.
_orig_plist_valid = autostart._plist_valid
_orig_service_loaded = autostart._service_loaded
try:
    autostart._plist_valid = lambda _path: True
    autostart._service_loaded = lambda _label=autostart.LABEL: False
    check("valid but unloaded plist reports disabled", not autostart.is_enabled())
    autostart._service_loaded = lambda _label=autostart.LABEL: True
    check("valid loaded plist reports enabled", autostart.is_enabled())
finally:
    autostart._plist_valid = _orig_plist_valid
    autostart._service_loaded = _orig_service_loaded

# Verify NO Windows-isms
import autostart as _as
_as_src = open(_as.__file__, encoding="utf-8").read()
check("no winreg import", "winreg" not in _as_src)
check("no WScript.Shell", "WScript.Shell" not in _as_src)
check("no .lnk creation", "CreateShortcut" not in _as_src)
check("uses plistlib for plist generation", "plistlib" in _as_src)
check("is_enabled queries launchctl print", '"print"' in _as_src)
check("enable uses launchctl bootstrap", '"bootstrap"' in _as_src)

# Fresh-install shortcuts must remain Mac-specific. Deck uses H because D is
# already the activation shortcut.
import settings as _settings
check("mac activation default uses Option+D",
      _settings.DEFAULTS["hotkey"] == "ctrl+option+d")
check("mac quick paste default uses Option+V",
      _settings.DEFAULTS["quick_paste_hotkey"] == "ctrl+option+v")
check("mac Deck default uses non-conflicting Option+H",
      _settings.DEFAULTS["history_hotkey"] == "ctrl+option+h")
check("mac search default uses Option+S",
      _settings.DEFAULTS["search_hotkey"] == "ctrl+option+s")


# ============================================ update (.command swap script)
print("\n== update: .command swap script (macOS) ==")

import update

# Verify the _write_swap_script function exists
check("_write_swap_script is callable", callable(update._write_swap_script))

# Test the swap script generation on a temp dir
import platform as _plat
_test_parent = tempfile.mkdtemp(prefix="mumble_test_update_")
_test_new = os.path.join(_test_parent, "Mumble-1.0.0")
_test_cur = os.path.join(_test_parent, "Mumble")
os.makedirs(_test_new, exist_ok=True)
os.makedirs(_test_cur, exist_ok=True)
# Create a dummy Launch Mumble.command so the .command script uses 'open'
_launch_cmd = os.path.join(_test_parent, "Launch Mumble.command")
with open(_launch_cmd, "w") as f:
    f.write("#!/bin/bash\necho launch\n")

try:
    # Patch sys.platform to darwin for the .command branch
    orig_plat = sys.platform
    try:
        # Force macOS branch
        sys.platform = "darwin"
        update._write_swap_script(_test_parent, _test_new, _test_cur)
    finally:
        sys.platform = orig_plat

    script_path = os.path.join(_test_parent, "apply_update.command")
    check("writes apply_update.command", os.path.exists(script_path))

    if os.path.exists(script_path):
        with open(script_path) as f:
            content = f.read()
        check(".command starts with #!/bin/bash", content.startswith("#!/bin/bash"))
        check("uses kill (not taskkill)", "kill" in content and "taskkill" not in content)
        check("uses mv (not rename)", "mv " in content and "rename" not in content)
        check("uses open for relaunch", "open" in content)
        check("no .bat commands", "taskkill" not in content and "timeout" not in content)
        check("has rollback logic", "mv \"" in content and "backup" in content)
        check("is executable", os.access(script_path, os.X_OK)
              or oct(os.stat(script_path).st_mode)[-3:] == "755")
finally:
    import shutil
    shutil.rmtree(_test_parent, ignore_errors=True)

# Verify the update API
check("check_for_update is callable", callable(update.check_for_update))
check("download_and_install is callable", callable(update.download_and_install))
check("rollback is callable", callable(update.rollback))
check("start_auto_check is callable", callable(update.start_auto_check))

# Verify NO Windows-isms in update.py
check("no .bat references in write_swap for darwin",
      ".bat" not in str(update._write_swap_script.__code__.co_consts).lower()
      or sys.platform != "darwin")


# ============================================ os.startfile() replacement
print("\n== os.startfile() → subprocess.run(['open', path]) ==")

# Verify no os.startfile in mumble_mac.py
import mumble_mac as _mm
_mm_src = open(_mm.__file__, encoding="utf-8").read()
check("mumble_mac.py has no os.startfile()",
      "os.startfile(" not in _mm_src)

# The internal command client must authenticate to the server it hardens.
import json as _json
import socket as _socket
_sent = []


class _FakeSocket:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def sendall(self, data):
        _sent.append(data)


_orig_create_connection = _socket.create_connection
try:
    _socket.create_connection = lambda *_args, **_kwargs: _FakeSocket()
    _token_app = object.__new__(_mm.Mumble)
    _token_app._cmd_token = "test-session-secret"
    _token_app.WEBUI_PORT = 49520
    _request = {"cmd": "refresh", "what": "history"}
    check("internal web command sends successfully",
          _token_app._send_webui(_request))
    _wire = _json.loads(_sent[-1].decode("utf-8"))
    check("internal web command attaches session token",
          _wire.get("token") == "test-session-secret")
    check("internal web command preserves caller payload",
          _wire.get("cmd") == "refresh" and "token" not in _request)
finally:
    _socket.create_connection = _orig_create_connection

check("accessibility uses ApplicationServices",
      "from ApplicationServices import AXIsProcessTrusted" in _mm_src)
check("accessibility does not call AppKit.AXIsProcessTrusted",
      "AppKit.AXIsProcessTrusted()" not in _mm_src)
check("legacy Windows Run-key call removed",
      "remove_legacy_run_key" not in _mm_src)
check("boot reconciles both enabled and disabled autostart state",
      "desired_autostart != actual_autostart" in _mm_src
      and "autostart.set_enabled(desired_autostart)" in _mm_src)
check("subprocess is imported at module scope",
      "import subprocess" in _mm_src.splitlines()[:25])
check("update restart is nonblocking",
      'subprocess.Popen(["open", script]' in _mm_src)

_boot_start = _mm_src.index("def _boot(self)")
_run_start = _mm_src.index("def run(self)", _boot_start)
_boot_chunk = _mm_src[_boot_start:_run_start]
_run_chunk = _mm_src[_run_start:_mm_src.index("_LOCK_SOCK", _run_start)]
check("boot checks TCC before creating input bindings",
      _boot_chunk.index("_check_accessibility(prompt=True)") <
      _boot_chunk.index("_register_input_bindings()"))
check("denied TCC has a hotkey registration retry",
      "_wait_for_accessibility_and_register" in _mm_src
      and "trusted is True" in _mm_src)
check("run does not launch a duplicate accessibility probe",
      "_check_accessibility" not in _run_chunk)

_update_start = _mm_src.index("def _on_check_updates")
_update_end = _mm_src.index("def _on_update_install", _update_start)
_update_chunk = _mm_src[_update_start:_update_end]
check("manual update check runs in a daemon worker",
      "check_in_background" in _update_chunk
      and "threading.Thread" in _update_chunk)

# Verify subprocess.run with 'open' is used
check("mumble_mac.py uses subprocess.run(['open'",
      "subprocess.run(['open'" in _mm_src or "subprocess.run([\"open\"" in _mm_src)

# Verify app_window.py has no os.startfile
import app_window as _aw
_aw_src = open(_aw.__file__, encoding="utf-8").read()
check("app_window.py has no os.startfile()",
      "os.startfile(" not in _aw_src)

# webui_shell is now shared across ports; Windows-only os.startfile calls are
# valid, but every Mac branch must use the native `open` command.
import webui_shell as _ws
_ws_src = open(_ws.__file__, encoding="utf-8").read()
check("webui_shell.py has explicit Darwin open branches",
      'elif sys.platform == "darwin"' in _ws_src
      and ('subprocess.Popen(["open", path])' in _ws_src
           or 'subprocess.run(["open", path])' in _ws_src))


# ============================================ installer/runtime hardening
print("\n== installer + runtime hardening ==")
_mac_port_dir = os.path.dirname(_port_dir)
_install_path = os.path.join(_mac_port_dir, "Install Mumble.command")
_uninstall_path = os.path.join(_mac_port_dir, "Uninstall Mumble.command")
_requirements_path = os.path.join(_port_dir, "requirements.txt")
with open(_install_path, encoding="utf-8") as _f:
    _install_src = _f.read()
with open(_uninstall_path, encoding="utf-8") as _f:
    _uninstall_src = _f.read()
with open(_requirements_path, encoding="utf-8") as _f:
    _requirements = _f.read()

check("Mac runtime uses pynput", "pynput==1.8.2" in _requirements)
check("unsupported keyboard package removed from Mac runtime",
      not any(line.startswith("keyboard==") for line in _requirements.splitlines()))
check("unsupported mouse package removed from Mac runtime",
      not any(line.startswith("mouse==") for line in _requirements.splitlines()))
check("installer requires supported Python 3.12–3.13 range",
      "(3, 12) <= sys.version_info < (3, 14)" in _install_src)
check("installer preflights tkinter", "import tkinter" in _install_src)
check("installer preflights ApplicationServices",
      "from ApplicationServices import AXIsProcessTrusted" in _install_src)
check("app bundle declares microphone purpose",
      "NSMicrophoneUsageDescription" in _install_src
      and "Meeting capture" in _install_src)
check("installer validates PID identity", "is_mumble_pid()" in _install_src)
check("installer scopes fallback to listening lock port",
      "-tiTCP:49517 -sTCP:LISTEN" in _install_src)
check("installer preserves saved autostart choice",
      "AUTOSTART_WANTED" in _install_src and
      "left disabled (preserved from Settings)" in _install_src)
check("uninstaller validates PID identity", "is_mumble_pid()" in _uninstall_src)
check("uninstaller scopes fallback to listening lock port",
      "-tiTCP:49517 -sTCP:LISTEN" in _uninstall_src)
check("uninstaller has no broad pkill fallback", "pkill -f" not in _uninstall_src)


# ============================================ The Big Shift sync verification
print("\n== The Big Shift: mode system sync to macOS ==")

# Verify critical imports exist in mumble_mac
check("mumble_mac imports local_engine",
      "local_engine" in _mm_src.split("import ")[1:].__class__.__name__
      or "import local_engine" in _mm_src)
# More robust check
has_local_engine = any("local_engine" in line
                       for line in _mm_src.splitlines()
                       if line.startswith("import "))
check("mumble_mac imports local_engine (line check)", has_local_engine)

has_foreign_boost = any("foreign_boost" in line
                        for line in _mm_src.splitlines()
                        if line.startswith("import "))
check("mumble_mac imports foreign_boost (line check)", has_foreign_boost)

# Verify _processing guard
check("mumble_mac has _processing attribute",
      "_processing" in _mm_src)
check("dictation is vetoed during meeting recording",
      'getattr(self, "meeting_recording", False)' in _mm_src)
check("voice search selects its own island state",
      'rec_state = "search" if is_search else "listening"' in _mm_src)

# Verify prompt_mode_enabled
check("mumble_mac has prompt_mode_enabled",
      "prompt_mode_enabled" in _mm_src)

# Verify active_mode / set_active_mode
check("mumble_mac has active_mode",
      "active_mode" in _mm_src)
check("mumble_mac has set_active_mode",
      "set_active_mode" in _mm_src)

# Verify the retired mode-key runtime is gone.
check("retired mode-key runtime removed from mumble_mac",
      "def _register_mode_key" not in _mm_src
      and "register_hold" not in _mm_src)

# Verify _watch_mode_key is removed (DEEP-007 — wasteful 1Hz timer)
check("_watch_mode_key removed from mumble_mac",
      "def _watch_mode_key" not in _mm_src)

# Verify local_engine.route() is called
check("local_engine.route() exists in mumble_mac",
      "local_engine.route(" in _mm_src)

# Verify foreign_boost.boost() is called
check("foreign_boost.boost() exists in mumble_mac",
      "foreign_boost.boost(" in _mm_src)

# Verify pipeline orchestrator in _generate
check("pipeline orchestrator integrated in _generate",
      "from pipeline import get_orchestrator" in _mm_src
      or "get_orchestrator()" in _mm_src)

# Verify _local_llm_generate exists
check("_local_llm_generate method exists",
      "def _local_llm_generate" in _mm_src)

import overlay_mac as _overlay_mac
import island_render as _island_render
check("voice search is an active Mac island state",
      "search" in _overlay_mac._ACTIVE_STATES)
check("voice search reserves waveform space",
      _island_render._anim_span("search") ==
      _island_render._anim_span("listening"))


print("\n" + ("ALL GREEN" if not _fails else f"{len(_fails)} FAILED: {_fails}"))
sys.exit(1 if _fails else 0)
