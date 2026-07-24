#!/usr/bin/env python3
"""Manage Mumble's macOS autostart via a LaunchAgent plist.

Uses the standard macOS LaunchAgent mechanism (launchd) to start Mumble at
login — no Windows-style .lnk shortcuts. The plist is written to
~/Library/LaunchAgents/com.mumble.app.plist and loaded/unloaded via launchctl.

Public API (shared with Windows/Linux ports):
  is_enabled() → bool
  enable()     → bool
  disable()    → bool
  set_enabled(bool) → bool
"""

import os
import plistlib
import subprocess
import sys

import branding

# LaunchAgent identity and location — follows Apple conventions:
# name ~/Library/LaunchAgents/<bundle-id>.plist, loaded per-user.
LAUNCH_AGENTS_DIR = os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents")
PLIST_NAME = "com.mumble.app.plist"
PLIST_PATH = os.path.join(LAUNCH_AGENTS_DIR, PLIST_NAME)
LABEL = "com.mumble.app"
LEGACY_PLIST_PATH = os.path.join(
    LAUNCH_AGENTS_DIR, "com.mumble.voice.plist")
LEGACY_LABEL = "com.mumble.voice"

# Prefer the installed bundle launcher; fall back to the app's private venv (or
# the current interpreter in a source checkout/test environment).
_APP_BUNDLE_LAUNCHER = os.path.join(
    os.path.expanduser("~"), "Applications", "Mumble.app", "Contents", "MacOS",
    "MumbleLauncher")
_VENV_PYTHON = os.path.join(branding.INSTALL_DIR, ".venv", "bin", "python")
_MUMBLE_MAIN = os.path.join(branding.INSTALL_DIR, "mumble_mac.py")


def _build_plist():
    """Return a LaunchAgent plist dict with KeepAlive, RunAtLoad, and the
    correct ProgramArguments pointing to the installed Mumble runtime."""
    if os.path.isfile(_APP_BUNDLE_LAUNCHER):
        program_args = [_APP_BUNDLE_LAUNCHER]
    else:
        python = _VENV_PYTHON if os.path.isfile(_VENV_PYTHON) else sys.executable
        program_args = [python, _MUMBLE_MAIN]

    return {
        "Label": LABEL,
        "ProgramArguments": program_args,
        "RunAtLoad": True,
        "KeepAlive": False,
        "StandardOutPath": os.path.join(branding.DATA_DIR, "launchd_stdout.log"),
        "StandardErrorPath": os.path.join(branding.DATA_DIR, "launchd_stderr.log"),
        "ProcessType": "Interactive",
    }


def _plist_valid(path):
    """Quick validation: file exists and is parseable XML plist."""
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as f:
            plistlib.load(f)
        return True
    except Exception:
        return False


def _launch_domain():
    """Current user's launchd GUI domain (for example ``gui/501``)."""
    getuid = getattr(os, "getuid", None)
    uid = getuid() if getuid is not None else 0
    return f"gui/{uid}"


def _launchctl(*args):
    """Run launchctl and return CompletedProcess, or None if unavailable."""
    try:
        return subprocess.run(
            ["launchctl", *args], capture_output=True, text=True, timeout=10)
    except Exception as e:
        print(f"launchctl {' '.join(args)}: {e}")
        return None


def _service_loaded(label=LABEL):
    result = _launchctl("print", f"{_launch_domain()}/{label}")
    return result is not None and result.returncode == 0


def _bootout(path, label):
    """Unload an agent using modern launchctl, with a legacy fallback."""
    domain = _launch_domain()
    result = _launchctl("bootout", f"{domain}/{label}")
    if result is None or result.returncode != 0:
        result = _launchctl("bootout", domain, path)
    if result is None or result.returncode != 0:
        _launchctl("unload", path)


def _remove_agent(path, label):
    _bootout(path, label)
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as e:
        print(f"autostart remove {path}: {e}")
        return False
    return True


# --- run at login (LaunchAgent) ---------------------------------------------
def is_enabled():
    """True when the LaunchAgent plist exists and is currently loaded."""
    if not _plist_valid(PLIST_PATH):
        return False
    # A valid file is only configuration, not proof launchd accepted it.
    return _service_loaded(LABEL)


def enable():
    """Write the LaunchAgent plist and load it via launchctl."""
    try:
        os.makedirs(LAUNCH_AGENTS_DIR, exist_ok=True)
    except OSError as e:
        print(f"autostart enable (mkdir): {e}")
        return False
    try:
        # Remove the pre-unification bundle id so only one login item can run.
        if not _remove_agent(LEGACY_PLIST_PATH, LEGACY_LABEL):
            return False
        plist = _build_plist()
        with open(PLIST_PATH, "wb") as f:
            plistlib.dump(plist, f)
        # Replace any stale loaded definition, then bootstrap into this user's
        # GUI domain. `load` remains a compatibility fallback for older systems.
        _bootout(PLIST_PATH, LABEL)
        result = _launchctl("bootstrap", _launch_domain(), PLIST_PATH)
        if result is None or result.returncode != 0:
            result = _launchctl("load", PLIST_PATH)
        if result is None or result.returncode != 0 or not _service_loaded(LABEL):
            detail = "" if result is None else (result.stderr or result.stdout or "")
            print(f"autostart enable (launchctl): "
                  f"{str(detail).strip() or getattr(result, 'returncode', 'unavailable')}")
            try:
                os.remove(PLIST_PATH)
            except OSError:
                pass
            return False
        return True
    except Exception as e:
        print(f"autostart enable: {e}")
        return False


def disable():
    """Unload the LaunchAgent and remove its plist."""
    current_ok = _remove_agent(PLIST_PATH, LABEL)
    legacy_ok = _remove_agent(LEGACY_PLIST_PATH, LEGACY_LABEL)
    return (current_ok and legacy_ok
            and not os.path.exists(PLIST_PATH)
            and not os.path.exists(LEGACY_PLIST_PATH)
            and not _service_loaded(LABEL))


def set_enabled(value):
    """Enable or disable autostart. Returns True on success."""
    return enable() if value else disable()


# --- Desktop / Start-menu stubs (macOS has no Start menu) -------------------
# The shared API surface expects these to exist so callers don't crash on
# platforms that lack the concept. They are no-ops that return True/false
# sensibly.

def install_start_menu():
    """No Start menu on macOS — no-op that reports success."""
    return True


def remove_start_menu():
    """No-op."""
    return True


def install_uninstall_start_menu():
    """No-op."""
    return True


def remove_uninstall_start_menu():
    """No-op."""
    return True


def install_desktop_shortcut():
    """No-op. macOS apps live in /Applications via .app bundle or .command."""
    return True


def remove_desktop_shortcut():
    """No-op."""
    return True
