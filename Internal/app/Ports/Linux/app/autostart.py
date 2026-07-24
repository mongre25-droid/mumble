#!/usr/bin/env python3
"""Manage Mumble's Linux autostart via a .desktop file per the XDG autostart spec.

Uses the standard XDG autostart mechanism: a .desktop file at
~/.config/autostart/mumble-voice-to-text.desktop that desktop environments read
at login.
No Windows-specific mechanisms (shortcuts, registry keys, or shell scripting).

Public API (shared with Windows/macOS ports):
  is_enabled() → bool
  enable()     → bool
  disable()    → bool
  set_enabled(bool) → bool
"""

import os
import shutil
import sys
import tempfile

import branding


def _xdg_config_home():
    """Return the absolute XDG config root, ignoring invalid relative values."""
    configured = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if configured and os.path.isabs(configured):
        return configured
    return os.path.expanduser("~/.config")


# XDG autostart directory and .desktop file location.  This must use the same
# XDG_CONFIG_HOME resolution as uninstall.sh; hard-coding ~/.config made the UI
# create an entry that the uninstaller never removed for custom-XDG users.
AUTOSTART_DIR = os.path.join(_xdg_config_home(), "autostart")
DESKTOP_NAME = "mumble-voice-to-text.desktop"
DESKTOP_PATH = os.path.join(AUTOSTART_DIR, DESKTOP_NAME)
LEGACY_DESKTOP_PATH = os.path.join(AUTOSTART_DIR, "mumble.desktop")

# Launch through the installer-owned script/venv, never the system interpreter.
_MUMBLE_MAIN = os.path.join(branding.INSTALL_DIR, "mumble_linux.py")
_VENV_PYTHON = os.path.join(branding.INSTALL_DIR, ".venv", "bin", "python")
_INSTALL_LAUNCHER = os.path.abspath(os.path.join(
    branding.INSTALL_DIR, os.pardir, "mumble"))


def _desktop_quote(value):
    """Quote one path for an XDG Desktop Entry ``Exec=`` field."""
    value = str(value or "")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("desktop Exec arguments cannot contain control characters")
    escaped = (value.replace("\\", "\\\\")
               .replace('"', '\\"')
               .replace("`", "\\`")
               .replace("$", "\\$")
               # A literal percent in Exec must be doubled or it is parsed
               # as a field code such as %f/%u by the desktop launcher.
               .replace("%", "%%"))
    return f'"{escaped}"'


def _launch_argv():
    """Return the installed launch command without escaping the virtualenv."""
    if os.path.isfile(_INSTALL_LAUNCHER) and os.access(_INSTALL_LAUNCHER, os.X_OK):
        return [_INSTALL_LAUNCHER]
    if os.path.isfile(_VENV_PYTHON) and os.access(_VENV_PYTHON, os.X_OK):
        return [_VENV_PYTHON, _MUMBLE_MAIN]
    return [sys.executable or shutil.which("python3") or "python3", _MUMBLE_MAIN]


def _build_desktop_entry():
    """Return a valid .desktop file content string with Type=Application,
    X-GNOME-Autostart-enabled=true, and the correct Exec line per the
    XDG Desktop Entry Specification."""
    exec_cmd = " ".join(_desktop_quote(arg) for arg in _launch_argv())

    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Mumble\n"
        "Comment=Private, on-device voice-to-text\n"
        "X-Mumble-VoiceToText=true\n"
        f"Exec={exec_cmd}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "StartupNotify=false\n"
        "Categories=Utility;Office;\n"
    )


def _desktop_valid(path):
    """Return whether *path* is an enabled, minimally valid autostart entry."""
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        fields = {}
        in_desktop_group = False
        for raw in content.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                in_desktop_group = line.casefold() == "[desktop entry]"
                continue
            if in_desktop_group and "=" in line:
                key, value = line.split("=", 1)
                fields[key.strip().casefold()] = value.strip()
        return (
            fields.get("type", "").casefold() == "application"
            and bool(fields.get("exec", ""))
            and fields.get("hidden", "false").casefold() != "true"
            and fields.get("x-gnome-autostart-enabled", "true").casefold()
            != "false"
        )
    except Exception:
        return False


def _legacy_entry_owned(path=None):
    """Identify our pre-migration entry without touching Mumble VoIP's file."""
    path = path or LEGACY_DESKTOP_PATH
    try:
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read(65537)
        if len(content) > 65536:
            return False
        lines = {line.strip().casefold() for line in content.splitlines()}
        return ("x-mumble-voicetotext=true" in lines
                or "comment=private, on-device voice-to-text" in lines)
    except (OSError, UnicodeError):
        return False


# --- run at login (XDG autostart) -----------------------------------------
def is_enabled():
    """True when the .desktop file exists and is valid."""
    return (_desktop_valid(DESKTOP_PATH)
            or (_legacy_entry_owned() and _desktop_valid(LEGACY_DESKTOP_PATH)))


def enable():
    """Atomically write the .desktop file to the XDG autostart directory."""
    tmp_path = ""
    try:
        os.makedirs(AUTOSTART_DIR, exist_ok=True)
        entry = _build_desktop_entry()
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{DESKTOP_NAME}.", dir=AUTOSTART_DIR, text=True)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(entry)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, DESKTOP_PATH)
        tmp_path = ""
        if _legacy_entry_owned():
            try:
                os.remove(LEGACY_DESKTOP_PATH)
            except OSError:
                # Avoid two login launches if migration cannot remove our old
                # entry; retain the old working entry and report the failure.
                os.remove(DESKTOP_PATH)
                raise
        return True
    except Exception as e:
        print(f"autostart enable: {e}")
        return False
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def disable():
    """Remove the .desktop file from the autostart directory."""
    targets = [DESKTOP_PATH]
    if _legacy_entry_owned():
        targets.append(LEGACY_DESKTOP_PATH)
    for path in targets:
        if not os.path.exists(path):
            continue
        try:
            os.remove(path)
        except OSError as e:
            print(f"autostart disable (remove): {e}")
            return False
    return (not os.path.exists(DESKTOP_PATH)
            and not _legacy_entry_owned())


def set_enabled(value):
    """Enable or disable autostart. Returns True on success."""
    return enable() if value else disable()


def remove_legacy_run_key():
    """Windows compatibility hook; Linux has no registry Run key."""
    return True


# --- Desktop / Start-menu stubs (Linux has no Start menu) -----------------
# The shared API surface expects these to exist so callers don't crash on
# platforms that lack the concept. They are no-ops that return True/false
# sensibly.

def install_start_menu():
    """No Start menu on Linux — no-op that reports success."""
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
    """No-op. Linux apps live in /usr/local/bin or via .desktop files."""
    return True


def remove_desktop_shortcut():
    """No-op."""
    return True
