#!/usr/bin/env python3
"""Manage Mumble's Windows shortcuts (Desktop, Start menu, run-at-login,
uninstall) so the app behaves like an installed product. Uses PowerShell's
WScript.Shell to author .lnk files -- no extra Python dependencies."""

import os
import subprocess

import branding

_APPDATA = os.environ.get("APPDATA", "")
_LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
STARTUP_DIR = os.path.join(_APPDATA, r"Microsoft\Windows\Start Menu\Programs\Startup")
PROGRAMS_DIR = os.path.join(_APPDATA, r"Microsoft\Windows\Start Menu\Programs")
LNK_NAME = "Mumble.lnk"
UNINSTALL_LNK = "Uninstall Mumble.lnk"
LEGACY_FINALIZE_STARTUP = (
    os.path.join(STARTUP_DIR, "FinalizeMumble.cmd") if _APPDATA else "")
LEGACY_FINALIZE_SCRIPT = (
    os.path.join(_LOCALAPPDATA, "finalize_mumble.ps1")
    if _LOCALAPPDATA else "")

# Prefer the branded executable (a renamed pythonw the installer creates) so
# shortcuts, the taskbar and Task Manager all say "Mumble" — never "Python".
_MUMBLE_EXE = os.path.join(branding.INSTALL_DIR, ".venv", "Scripts", "Mumble.exe")
_PYTHONW = os.path.join(branding.INSTALL_DIR, ".venv", "Scripts", "pythonw.exe")
PYW = _MUMBLE_EXE if os.path.exists(_MUMBLE_EXE) else _PYTHONW
TARGET = os.path.join(branding.INSTALL_DIR, "mumble.py")
# Launching the controller twice is safe: its single-instance socket asks the
# existing process to reveal its window, so shortcuts never terminate it.
UNINSTALL_PS1 = os.path.join(branding.INSTALL_DIR, "uninstall.ps1")
POWERSHELL = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          r"System32\WindowsPowerShell\v1.0\powershell.exe")

_NO_WINDOW = 0x08000000


def _q(s):
    return str(s).replace("'", "''")


def _run_ps(ps):
    try:
        proc = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", ps],
                              capture_output=True, creationflags=_NO_WINDOW, timeout=30)
        if proc.returncode != 0:
            stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
            if stderr:
                print(f"autostart PowerShell error (exit {proc.returncode}): {stderr}")
            return False
        return True
    except subprocess.TimeoutExpired as e:
        print(f"autostart _run_ps timeout: {e}")
        return False
    except FileNotFoundError:
        print(f"autostart: PowerShell not found at {POWERSHELL}")
        return False
    except OSError as e:
        print(f"autostart _run_ps OS error: {e}")
        return False


def _shortcut_ps(path_expr, target, args, workdir, desc):
    """Build a PowerShell snippet that writes a .lnk. `path_expr` is a PS
    expression yielding the destination path (so we can resolve special folders)."""
    return (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$p = {path_expr}; "
        "$s = $ws.CreateShortcut($p); "
        f"$s.TargetPath = '{_q(target)}'; "
        f"$s.Arguments = '{_q(args)}'; "
        f"$s.WorkingDirectory = '{_q(workdir)}'; "
        "$s.WindowStyle = 7; "
        f"$s.IconLocation = '{_q(branding.ICON_ICO)}'; "
        f"$s.Description = '{_q(desc)}'; "
        "$s.Save()"
    )


def _create(path, target, args, workdir, desc):
    return _run_ps(_shortcut_ps(f"'{_q(path)}'", target, args, workdir, desc))


def _app_shortcut(path):
    # Launch directly. Mumble's single-instance channel reveals a healthy
    # existing window without force-killing the controller or orphaning WebView.
    return _create(path, PYW, f'"{TARGET}"', branding.INSTALL_DIR,
                   "Mumble - voice to text")


# --- legacy cleanup -----------------------------------------------------------
def remove_legacy_run_key():
    """Mumble v1.x registered the INSTALLER batch in HKCU\\..\\Run, so Windows
    re-ran it at every login — a red PowerShell error at boot once the old
    download folder moved. v2+ owns startup via the Startup-folder shortcut,
    so ANY Run value named Mumble is stale. Idempotent; called at install and
    at every app boot so old machines self-heal without reinstalling."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, "Mumble")
        print("autostart: removed a stale v1.x Run-key startup entry")
        return True
    except FileNotFoundError:
        return False    # nothing stale — the common case
    except OSError as e:
        print(f"autostart: legacy Run-key cleanup failed: {e}")
        return False


def remove_legacy_startup_artifacts():
    """Remove the abandoned one-time finalizer used by early Mumble builds.

    Its Startup-folder command survived after its LocalAppData PowerShell
    payload deleted itself, so Windows displayed a missing ``.ps1`` error at
    every login. Only the two exact Mumble-owned legacy paths are removed.
    """
    ok = True
    for path in (LEGACY_FINALIZE_STARTUP, LEGACY_FINALIZE_SCRIPT):
        if not path or not os.path.exists(path):
            continue
        try:
            os.remove(path)
            print(f"autostart: removed stale one-time finalizer {path}")
        except OSError as e:
            print(f"autostart: could not remove stale finalizer {path}: {e}")
            ok = False
    return ok


# --- run at login (Startup folder) ------------------------------------------
def startup_path():
    return os.path.join(STARTUP_DIR, LNK_NAME)


def is_enabled():
    return os.path.exists(startup_path())


def enable():
    remove_legacy_run_key()
    legacy_cleanup_ok = remove_legacy_startup_artifacts()
    try:
        os.makedirs(STARTUP_DIR, exist_ok=True)
    except OSError:
        pass
    shortcut_ok = _app_shortcut(startup_path())
    return legacy_cleanup_ok and shortcut_ok


def disable():
    p = startup_path()
    if not os.path.exists(p):
        return True    # already gone — no work to do
    try:
        os.remove(p)
        return not os.path.exists(p)
    except OSError as e:
        print(f"autostart disable: could not remove {p}: {e}")
        return False


def set_enabled(value):
    return enable() if value else disable()


# --- Start menu --------------------------------------------------------------
def install_start_menu():
    try:
        os.makedirs(PROGRAMS_DIR, exist_ok=True)
    except OSError:
        pass
    return _app_shortcut(os.path.join(PROGRAMS_DIR, LNK_NAME))


def remove_start_menu():
    try:
        os.remove(os.path.join(PROGRAMS_DIR, LNK_NAME))
    except OSError:
        pass


def install_uninstall_start_menu():
    try:
        os.makedirs(PROGRAMS_DIR, exist_ok=True)
    except OSError:
        pass
    return _create(os.path.join(PROGRAMS_DIR, UNINSTALL_LNK), POWERSHELL,
                   f'-NoProfile -ExecutionPolicy Bypass -File "{UNINSTALL_PS1}"',
                   branding.INSTALL_DIR, "Uninstall Mumble")


def remove_uninstall_start_menu():
    try:
        os.remove(os.path.join(PROGRAMS_DIR, UNINSTALL_LNK))
    except OSError:
        pass


# --- Desktop icon (resolved at PS-time so it respects OneDrive redirection) --
def install_desktop_shortcut():
    return _run_ps(_shortcut_ps(
        "(Join-Path $ws.SpecialFolders.Item('Desktop') 'Mumble.lnk')",
        PYW, f'"{TARGET}"', branding.INSTALL_DIR, "Mumble - voice to text"))


def remove_desktop_shortcut():
    return _run_ps(
        "$ws = New-Object -ComObject WScript.Shell; "
        "$p = Join-Path $ws.SpecialFolders.Item('Desktop') 'Mumble.lnk'; "
        "if (Test-Path $p) { Remove-Item $p -Force }")
