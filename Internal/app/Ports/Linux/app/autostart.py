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

import hashlib
import io
import os
import shlex
import shutil
import sys
import tempfile
from dataclasses import dataclass

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

_AUTHORITY_SINGLETON_KEYS = frozenset({
    "type", "name", "exec", "tryexec", "hidden", "onlyshowin",
    "notshowin", "x-gnome-autostart-enabled", "x-mumble-voicetotext",
    "comment",
})


@dataclass(frozen=True)
class _DesktopEntrySnapshot:
    """One bounded immutable reading of startup-authority fields."""

    path: str
    fields: tuple
    duplicate_authority_keys: frozenset
    file_identity: tuple
    content_digest: bytes


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


def _stat_identity(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _file_identity(path):
    return _stat_identity(os.stat(path))


def _desktop_snapshot(path):
    """Parse one bounded immutable Desktop Entry snapshot exactly once."""
    if not os.path.isfile(path):
        return None
    try:
        identity_before = _file_identity(path)
        with open(path, "rb") as handle:
            opened_identity = _stat_identity(os.fstat(handle.fileno()))
            raw_content = handle.read(65537)
            opened_identity_after = _stat_identity(os.fstat(handle.fileno()))
        identity_after = _file_identity(path)
        if (len(raw_content) > 65536
                or identity_before != opened_identity
                or opened_identity != opened_identity_after
                or opened_identity_after != identity_after):
            return None
        content = raw_content.decode("utf-8")
        fields = {}
        duplicate_authority_keys = set()
        in_desktop_group = False
        desktop_group_seen = False
        for raw in content.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                in_desktop_group = line.casefold() == "[desktop entry]"
                if in_desktop_group:
                    if desktop_group_seen:
                        duplicate_authority_keys.add("[desktop entry]")
                    desktop_group_seen = True
                continue
            if in_desktop_group and "=" in line:
                key, value = line.split("=", 1)
                key = key.strip().casefold()
                if key in fields and key in _AUTHORITY_SINGLETON_KEYS:
                    duplicate_authority_keys.add(key)
                fields[key] = value.strip()
        return _DesktopEntrySnapshot(
            path=str(path),
            fields=tuple(fields.items()),
            duplicate_authority_keys=frozenset(duplicate_authority_keys),
            file_identity=identity_after,
            content_digest=hashlib.sha256(raw_content).digest(),
        )
    except (OSError, UnicodeError):
        return None


def _snapshot_field(snapshot, key, default=""):
    for field_key, value in snapshot.fields:
        if field_key == key:
            return value
    return default


def _snapshot_unchanged(snapshot):
    try:
        with io.open(snapshot.path, "rb") as handle:
            opened_identity = _stat_identity(os.fstat(handle.fileno()))
            raw_content = handle.read(65537)
            opened_identity_after = _stat_identity(os.fstat(handle.fileno()))
        return bool(
            len(raw_content) <= 65536
            and snapshot.file_identity == opened_identity
            and opened_identity == opened_identity_after
            and opened_identity_after == _file_identity(snapshot.path)
            and hashlib.sha256(raw_content).digest() == snapshot.content_digest
        )
    except OSError:
        return False


def _desktop_list(value):
    """Parse one XDG semicolon-separated string list, or fail closed."""
    values = []
    current = []
    escaped = False
    escapes = {
        "s": " ", "n": "\n", "t": "\t", "r": "\r",
        "\\": "\\", ";": ";",
    }
    for character in value:
        if escaped:
            decoded = escapes.get(character)
            if decoded is None:
                return None
            current.append(decoded)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ";":
            if not current:
                return None
            values.append("".join(current))
            current = []
        else:
            current.append(character)
    if escaped:
        return None
    if current:
        values.append("".join(current))
    if not values or any(
            not item or any(ord(ch) < 32 or ord(ch) == 127 for ch in item)
            for item in values):
        return None
    return frozenset(values)


def _desktop_visible_in_current_environment(snapshot):
    """Apply OnlyShowIn/NotShowIn to XDG_CURRENT_DESKTOP."""
    fields = dict(snapshot.fields)
    only_value = fields.get("onlyshowin")
    not_value = fields.get("notshowin")
    if only_value is None and not_value is None:
        return True
    only_desktops = (_desktop_list(only_value)
                     if only_value is not None else frozenset())
    excluded_desktops = (_desktop_list(not_value)
                         if not_value is not None else frozenset())
    if ((only_value is not None and not only_desktops)
            or (not_value is not None and not excluded_desktops)
            or only_desktops.intersection(excluded_desktops)):
        return False
    current = tuple(
        item
        for item in (os.environ.get("XDG_CURRENT_DESKTOP") or "").split(":")
        if item
    )
    for desktop in current:
        if desktop in only_desktops:
            return True
        if desktop in excluded_desktops:
            return False
    return only_value is None


def _desktop_snapshot_valid(snapshot):
    if snapshot is None or snapshot.duplicate_authority_keys:
        return False
    return (
        _snapshot_field(snapshot, "type").casefold() == "application"
        and bool(_snapshot_field(snapshot, "exec"))
        and _snapshot_field(snapshot, "hidden", "false").casefold() != "true"
        and _snapshot_field(
            snapshot, "x-gnome-autostart-enabled", "true").casefold()
        != "false"
        and _desktop_visible_in_current_environment(snapshot)
        and _try_exec_available(snapshot)
    )


def _try_exec_available(snapshot):
    value = _snapshot_field(snapshot, "tryexec")
    if not value:
        return True
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return False
    if os.path.isabs(value):
        return os.path.isfile(value) and os.access(value, os.X_OK)
    return shutil.which(value) is not None


def _desktop_valid(path):
    """Return whether *path* is one stable, unambiguous enabled entry."""
    snapshot = _desktop_snapshot(path)
    return (_desktop_snapshot_valid(snapshot)
            and _snapshot_unchanged(snapshot))


def _desktop_exec_from_snapshot(snapshot):
    """Return Exec from the exact snapshot already used for validation."""
    if not _desktop_snapshot_valid(snapshot):
        return None
    try:
        argv = shlex.split(_snapshot_field(snapshot, "exec"), posix=True)
        if not argv or any("%" in value.replace("%%", "")
                           for value in argv):
            return None
        return [value.replace("%%", "%") for value in argv]
    except ValueError:
        return None


def _desktop_exec(path):
    """Return Exec from one stable, unambiguous desktop-entry read."""
    snapshot = _desktop_snapshot(path)
    if snapshot is None or not _snapshot_unchanged(snapshot):
        return None
    return _desktop_exec_from_snapshot(snapshot)


def _legacy_snapshot_owned(snapshot):
    if snapshot is None or snapshot.duplicate_authority_keys:
        return False
    return (
        _snapshot_field(snapshot, "x-mumble-voicetotext").casefold() == "true"
        or _snapshot_field(snapshot, "comment").casefold()
        == "private, on-device voice-to-text"
    )


def _startup_entry_snapshot():
    """Return the one entry snapshot that controls startup readiness."""
    if os.path.lexists(DESKTOP_PATH):
        return _desktop_snapshot(DESKTOP_PATH), True
    legacy = _desktop_snapshot(LEGACY_DESKTOP_PATH)
    if _legacy_snapshot_owned(legacy):
        return legacy, True
    return None, False


def _exec_matches_current_route(argv):
    """Prove that an Exec command resolves to a current installed route."""
    if not argv:
        return False

    def same_path(left, right):
        return os.path.normcase(os.path.realpath(left)) == os.path.normcase(
            os.path.realpath(right))

    executable = argv[0]
    if not (os.path.isfile(executable) and os.access(executable, os.X_OK)):
        return False
    if same_path(executable, _INSTALL_LAUNCHER):
        return len(argv) == 1
    return bool(
        len(argv) == 2
        and same_path(executable, _VENV_PYTHON)
        and os.path.isfile(_MUMBLE_MAIN)
        and same_path(argv[1], _MUMBLE_MAIN)
    )


def _legacy_entry_owned(path=None):
    """Identify our pre-migration entry without touching Mumble VoIP's file."""
    path = path or LEGACY_DESKTOP_PATH
    snapshot = _desktop_snapshot(path)
    return (_legacy_snapshot_owned(snapshot)
            and _snapshot_unchanged(snapshot))


# --- run at login (XDG autostart) -----------------------------------------
def is_enabled():
    """True when the .desktop file exists and is valid."""
    snapshot, present = _startup_entry_snapshot()
    return bool(present and _desktop_snapshot_valid(snapshot)
                and _snapshot_unchanged(snapshot))


def probe_route():
    """Read-only proof of the current XDG login-startup route."""
    try:
        main_exists = os.path.isfile(_MUMBLE_MAIN)
        launcher_exists = (
            os.path.isfile(_INSTALL_LAUNCHER)
            and os.access(_INSTALL_LAUNCHER, os.X_OK))
        python_exists = (
            os.path.isfile(_VENV_PYTHON)
            and os.access(_VENV_PYTHON, os.X_OK))
        if not main_exists or not (launcher_exists or python_exists):
            return (
                "degraded",
                "No installed Mumble launch route is available for autostart.")
        snapshot, entry_present = _startup_entry_snapshot()
        if entry_present:
            argv = _desktop_exec_from_snapshot(snapshot)
            if (_desktop_snapshot_valid(snapshot)
                    and _exec_matches_current_route(argv)
                    and _snapshot_unchanged(snapshot)):
                return (
                    "ready",
                    "The enabled XDG entry uses the current Mumble launch route.")
            return (
                "degraded",
                "The enabled XDG entry's startup authority is hidden, "
                "desktop-ineligible, missing, stale, malformed, ambiguous, "
                "mismatched, or changed while it was checked.")
        destination = AUTOSTART_DIR
        while not os.path.exists(destination):
            parent = os.path.dirname(destination)
            if parent == destination:
                return "unknown", "The XDG autostart destination was not proven."
            destination = parent
        if not os.path.isdir(destination) or not os.access(destination, os.W_OK):
            return "degraded", "The XDG autostart destination is not writable."
        return (
            "degraded",
            "The XDG autostart route is writable but no enabled entry was found.")
    except OSError as exc:
        return "unknown", f"The XDG autostart route could not be checked: {exc}"


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
