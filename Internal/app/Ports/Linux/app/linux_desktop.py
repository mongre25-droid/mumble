"""Truthful Linux desktop capability and adapter seams.

This module deliberately separates native X11, native Wayland, XWayland, and
sandbox state.  It never turns an installed helper into proof that a physical
desktop action succeeded.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from urllib.parse import unquote, urlparse


@dataclass(frozen=True)
class DesktopSession:
    session_type: str
    desktop: str
    xwayland_available: bool = False
    sandbox: str = "none"

    @property
    def is_x11(self):
        return self.session_type == "x11"

    @property
    def is_wayland(self):
        return self.session_type == "wayland"

    @property
    def is_headless(self):
        return self.session_type == "headless"

    @classmethod
    def detect(cls, env=None):
        env = os.environ if env is None else env
        raw_type = str(env.get("XDG_SESSION_TYPE") or "").casefold()
        if raw_type not in {"x11", "wayland"}:
            raw_type = "wayland" if env.get("WAYLAND_DISPLAY") else (
                "x11" if env.get("DISPLAY") else "headless")
        desktop_text = " ".join(str(env.get(key) or "") for key in (
            "XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION"
        )).casefold()
        desktop = ("gnome" if "gnome" in desktop_text else
                   "kde" if any(value in desktop_text for value in
                                ("kde", "plasma")) else "other")
        sandbox = ("flatpak" if env.get("FLATPAK_ID") else
                   "snap" if env.get("SNAP") else "none")
        return cls(raw_type, desktop,
                   xwayland_available=bool(env.get("DISPLAY") and
                                           raw_type == "wayland"),
                   sandbox=sandbox)


@dataclass(frozen=True)
class Capability:
    status: str
    backend: str
    message: str
    recovery: str = ""
    alternatives: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchResponse:
    status: str
    provider: str
    paths: tuple[str, ...]
    message: str = ""


class LinuxSearchAdapter:
    """Bounded desktop-index queries with cancellable operation ownership."""

    def __init__(self, session=None, *, which=shutil.which,
                 runner=subprocess.run, process_factory=subprocess.Popen):
        self.session = session or DesktopSession.detect()
        self.which = which
        self.runner = runner
        self.process_factory = process_factory
        self._lock = threading.Lock()
        self._generation = 0

    def cancel(self):
        with self._lock:
            self._generation += 1
            return self._generation

    def _route(self):
        if self.session.desktop == "gnome":
            command = self.which("localsearch") or self.which("tracker3")
            if command:
                return "gnome-localsearch", command
        if self.session.desktop == "kde":
            command = self.which("baloosearch")
            if command:
                return "kde-baloo", command
        command = self.which("plocate")
        if command:
            return "plocate", command
        return "", ""

    @staticmethod
    def _clean_path(value):
        value = str(value or "").strip().strip("'\"")
        if value.startswith("file://"):
            parsed = urlparse(value)
            value = unquote(parsed.path)
        if value.startswith("/"):
            return value
        try:
            path = Path(value).expanduser()
        except (TypeError, ValueError):
            return ""
        return str(path) if path.is_absolute() else ""

    def search(self, query, *, limit=12, timeout=1.5, cancel_event=None,
               operation_id=None):
        query = " ".join(str(query or "").split())[:300]
        try:
            limit = max(1, min(40, int(limit)))
            timeout = max(0.1, min(4.0, float(timeout)))
        except (TypeError, ValueError):
            limit, timeout = 12, 1.5
        if not query:
            return SearchResponse("ready", "none", ())
        if cancel_event is not None and cancel_event.is_set():
            return SearchResponse("cancelled", "none", (), "Search cancelled.")
        provider, executable = self._route()
        if not executable:
            return SearchResponse(
                "unsupported", "none", (),
                "No supported desktop file index is available. Install GNOME "
                "LocalSearch, KDE Baloo, or plocate; Mumble will not scan your "
                "folders interactively.")
        if provider == "gnome-localsearch":
            subcommand = "search"
            argv = [executable, subcommand, "--limit", str(limit), query]
            if os.path.basename(executable) == "tracker3":
                argv = [executable, "search", "--limit", str(limit), query]
        elif provider == "kde-baloo":
            argv = [executable, "-l", str(limit), query]
        else:
            argv = [executable, "--limit", str(limit), query]
        with self._lock:
            self._generation += 1
            generation = self._generation
        if cancel_event is not None and cancel_event.is_set():
            return SearchResponse("cancelled", provider, (), "Search cancelled.")
        try:
            if self.runner is subprocess.run:
                process = self.process_factory(
                    argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, shell=False)
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    with self._lock:
                        cancelled = generation != self._generation
                    if cancelled or (cancel_event is not None
                                     and cancel_event.is_set()):
                        process.terminate()
                        try:
                            process.wait(timeout=0.25)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=0.25)
                        return SearchResponse(
                            "cancelled", provider, (), "Search cancelled.")
                    if time.monotonic() >= deadline:
                        process.terminate()
                        try:
                            process.wait(timeout=0.25)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=0.25)
                        return SearchResponse(
                            "degraded", provider, (),
                            "The desktop index did not answer in time.")
                    time.sleep(0.01)
                stdout, stderr = process.communicate()
                completed = subprocess.CompletedProcess(
                    argv, process.returncode, stdout, stderr)
            else:
                completed = self.runner(
                    argv, capture_output=True, text=True, timeout=timeout,
                    check=False, shell=False)
        except subprocess.TimeoutExpired:
            return SearchResponse("degraded", provider, (),
                                  "The desktop index did not answer in time.")
        except OSError as exc:
            return SearchResponse("degraded", provider, (), str(exc)[:200])
        with self._lock:
            if generation != self._generation:
                return SearchResponse("cancelled", provider, (),
                                      "A newer search replaced this one.")
        if cancel_event is not None and cancel_event.is_set():
            return SearchResponse("cancelled", provider, (), "Search cancelled.")
        paths = []
        for line in (completed.stdout or "").splitlines():
            path = self._clean_path(line)
            if path and path not in paths:
                paths.append(path)
            if len(paths) >= limit:
                break
        if provider == "plocate":
            message = ("Degraded filename-only plocate results may be stale and "
                       "do not include desktop index metadata.")
            return SearchResponse("degraded", provider, tuple(paths), message)
        if completed.returncode:
            return SearchResponse("degraded", provider, tuple(paths),
                                  (completed.stderr or "Index query failed.")[:200])
        return SearchResponse("ready", provider, tuple(paths))


def _probe_global_shortcuts_portal(*, which=shutil.which,
                                   runner=subprocess.run):
    """Return True/False for a real portal interface, or None if unproven."""
    executable = which("gdbus")
    if not executable:
        return None
    try:
        completed = runner(
            [executable, "introspect", "--session", "--dest",
             "org.freedesktop.portal.Desktop", "--object-path",
             "/org/freedesktop/portal/desktop"],
            capture_output=True, text=True, timeout=0.75, check=False,
            shell=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode:
        return False
    return "org.freedesktop.portal.GlobalShortcuts" in (completed.stdout or "")


def shortcut_capability(session=None, *, which=shutil.which,
                        readable_input=False, portal_available=None,
                        portal_probe=None):
    session = session or DesktopSession.detect()
    if session.is_wayland:
        if portal_available is None:
            try:
                probe = portal_probe or (
                    lambda: _probe_global_shortcuts_portal(which=which))
                portal_available = probe()
            except Exception:
                portal_available = None
        if portal_available is True:
            return Capability(
                "degraded", "xdg-global-shortcuts-portal",
                "The Wayland portal client is available, but the shortcut is "
                "not ready until the compositor accepts registration and the "
                "user approves it.",
                "Approve the requested shortcuts in the desktop prompt.")
        if portal_available is None:
            return Capability(
                "unknown", "none",
                "The current session did not prove an XDG GlobalShortcuts "
                "portal interface.",
                "Check the desktop portal service or use application controls.")
        if readable_input:
            return Capability(
                "degraded", "evdev-read-only",
                "The Wayland shortcut portal is unavailable; direct device "
                "listening is permission-dependent and not compositor-managed.",
                "Install xdg-desktop-portal support or grant narrow input access.")
        return Capability(
            "degraded", "none",
            "The current session does not expose an XDG GlobalShortcuts "
            "portal route and input devices are not readable.",
            "Enable the desktop portal or use the application controls.")
    if session.is_x11:
        if readable_input:
            return Capability("ready", "evdev", "X11 global input is readable.")
        return Capability(
            "denied", "evdev",
            "X11 global shortcut input is not readable.",
            "Grant narrow input-device access, then sign out and back in.")
    return Capability("unsupported", "none",
                      "Global shortcuts are unavailable without a desktop session.")


def clipboard_capability(session=None, *, which=shutil.which):
    session = session or DesktopSession.detect()
    if session.is_wayland:
        if not (which("wl-copy") and which("wl-paste")):
            return Capability(
                "unsupported", "none",
                "Install wl-clipboard for native Wayland text and image clipboard access.")
        if which("wtype"):
            return Capability("ready", "wl-clipboard+wtype",
                              "Native Wayland clipboard and text insertion are available.")
        if which("ydotool"):
            return Capability(
                "degraded", "wl-clipboard+ydotool",
                "Clipboard is native Wayland; insertion uses permission-dependent ydotool.")
        return Capability(
            "degraded", "wl-clipboard",
            "Clipboard copy is available, but this compositor has no supported "
            "text insertion helper.", alternatives=("copy",))
    if session.is_x11:
        clipboard = "xclip" if which("xclip") else "xsel" if which("xsel") else ""
        if clipboard and which("xdotool"):
            return Capability("ready", f"{clipboard}+xdotool",
                              "Native X11 clipboard and text insertion are available.")
        if clipboard:
            return Capability("degraded", clipboard,
                              "X11 clipboard copy is available; automatic insertion is not.",
                              alternatives=("copy",))
        return Capability("unsupported", "none",
                          "Install xclip or xsel for X11 clipboard access.")
    return Capability("unsupported", "none",
                      "Clipboard insertion is unavailable without a desktop session.")


def drag_capability(session=None, *, gtk_available=None):
    session = session or DesktopSession.detect()
    if session.is_wayland:
        return Capability(
            "unsupported", "compositor-policy",
            "Native cross-application drag cannot be initiated reliably under "
            "this Wayland compositor.", alternatives=("open", "reveal"))
    if session.is_x11:
        if gtk_available is None:
            try:
                from linux_native_drag import LinuxNativeDragAdapter
                gtk_available = LinuxNativeDragAdapter.available()
            except Exception:
                gtk_available = False
        if gtk_available:
            return Capability(
                "ready", "gtk-uri-drag",
                "Trusted indexed files and folders can start a native X11 URI drag.",
                alternatives=("open", "reveal"))
        return Capability(
            "unsupported", "none",
            "This Linux WebView build has no trusted native X11 drag owner.",
            "Use Open or Show in folder.", alternatives=("open", "reveal"))
    return Capability("unsupported", "none", "Drag is unavailable headlessly.",
                      alternatives=("open", "reveal"))


def focus_window_capability(session=None, *, which=shutil.which):
    session = session or DesktopSession.detect()
    if session.is_x11 and which("xdotool"):
        return Capability(
            "ready", "xdotool",
            "X11 window identity, activation, and positioning are available.")
    if session.is_wayland:
        return Capability(
            "degraded", "compositor-policy",
            "Wayland controls cross-application focus and window placement; "
            "Mumble cannot claim exact restoration.",
            "Use the resident Mumble controls and return to the target manually.",
            alternatives=("resident-controls", "manual-focus"))
    return Capability(
        "unsupported", "none",
        "Window focus and positioning are unavailable in this session.")


def tray_capability(session=None, *, appindicator_available=None):
    session = session or DesktopSession.detect()
    if session.is_headless:
        return Capability("unsupported", "none",
                          "A system tray requires a graphical desktop session.")
    if appindicator_available is None:
        try:
            import gi
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3  # noqa: F401
            appindicator_available = True
        except Exception:
            appindicator_available = False
    if appindicator_available:
        return Capability(
            "degraded", "ayatana-appindicator",
            "The tray library is available; visibility still depends on the "
            "desktop shell and its indicator extension.",
            "Enable the desktop's application-indicator support if hidden.")
    return Capability(
        "unsupported", "none",
        "No supported application-indicator library is installed.",
        "Install Ayatana AppIndicator support or use the main window.",
        alternatives=("main-window",))


def autostart_capability(session=None, *, autostart_probe=None):
    session = session or DesktopSession.detect()
    if session.is_headless:
        return Capability("unsupported", "none",
                          "Desktop login autostart is unavailable headlessly.")
    if session.sandbox != "none":
        return Capability(
            "degraded", "xdg-autostart-sandboxed",
            f"{session.sandbox.title()} may block writes to the host autostart folder.",
            "Use the sandbox's background/startup permission or host settings.")
    try:
        if autostart_probe is None:
            import autostart
            autostart_probe = autostart.probe_route
        status, message = autostart_probe()
    except Exception as exc:
        status, message = "unknown", f"Autostart route probe failed: {exc}"
    if status not in {"ready", "degraded", "unknown"}:
        status = "unknown"
    return Capability(
        status, "xdg-autostart" if status == "ready" else "none", message,
        "Enable login startup in Settings after the route is available.")


def permissions_capability(session=None, *, readable_input=False,
                           microphone=None):
    session = session or DesktopSession.detect()
    microphone_status = getattr(microphone, "status", "unknown")
    if microphone_status == "denied":
        return Capability(
            "denied", "microphone",
            "Microphone permission is denied.",
            "Allow microphone access in desktop or sandbox settings.")
    if session.is_x11 and not readable_input:
        return Capability(
            "denied", "input-devices",
            "Global shortcut input devices are not readable.",
            "Grant narrow input access, then sign out and back in.")
    if session.sandbox != "none":
        return Capability(
            "degraded", f"{session.sandbox}-permissions",
            "Sandbox permissions may restrict microphone, clipboard, files, "
            "shortcuts, tray, or autostart.",
            "Review the sandbox permission panel for Mumble.")
    return Capability(
        "degraded" if microphone_status == "unknown" else "ready",
        "desktop-permissions",
        "Physical permission prompts and device access still require live verification.")


def audio_capability(sounddevice_module):
    try:
        hostapis = list(sounddevice_module.query_hostapis())
        devices = list(sounddevice_module.query_devices())
    except PermissionError as exc:
        return Capability("denied", "audio-device",
                          f"Microphone access was denied: {exc}",
                          "Allow microphone access for Mumble, then retry.")
    except Exception as exc:
        return Capability("degraded", "portaudio",
                          f"Audio devices could not be queried: {exc}",
                          "Check PipeWire or PulseAudio and reconnect the microphone.")
    names = " ".join(str(row.get("name", "")) for row in hostapis).casefold()
    backend = "pipewire" if "pipewire" in names else (
        "pulseaudio" if "pulse" in names else "alsa")
    inputs = [row for row in devices
              if int(row.get("max_input_channels", 0) or 0) > 0]
    if not inputs:
        return Capability("degraded", backend,
                          "No usable microphone input device is currently available.",
                          "Connect or permit a microphone, then refresh devices.")
    return Capability("ready", backend,
                      f"{len(inputs)} microphone input device(s) are available.")


def prefers_reduced_motion(env=None, *, which=shutil.which,
                           runner=subprocess.run):
    env = os.environ if env is None else env
    if str(env.get("MUMBLE_REDUCED_MOTION") or "").casefold() in {
            "1", "true", "yes", "on"}:
        return True
    executable = which("gsettings")
    if not executable:
        return False
    try:
        completed = runner(
            [executable, "get", "org.gnome.desktop.interface",
             "enable-animations"], capture_output=True, text=True,
            timeout=0.5, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and completed.stdout.strip() == "false"


def capability_snapshot(*, env=None, which=shutil.which,
                        readable_input=False, sounddevice_module=None,
                        appindicator_available=None, portal_probe=None,
                        autostart_probe=None):
    session = DesktopSession.detect(env)
    audio = (audio_capability(sounddevice_module)
             if sounddevice_module is not None else None)
    payload = {
        "session": asdict(session),
        "shortcuts": asdict(shortcut_capability(
            session, which=which, readable_input=readable_input,
            portal_probe=portal_probe)),
        "clipboard_insertion": asdict(clipboard_capability(session, which=which)),
        "drag": asdict(drag_capability(session)),
        "focus_window": asdict(focus_window_capability(session, which=which)),
        "tray": asdict(tray_capability(
            session, appindicator_available=appindicator_available)),
        "autostart": asdict(autostart_capability(
            session, autostart_probe=autostart_probe)),
        "permissions": asdict(permissions_capability(
            session, readable_input=readable_input, microphone=audio)),
        "evidence": "source-probe-only",
        "physical_parity": False,
    }
    if audio is not None:
        payload["audio"] = asdict(audio)
    return payload
