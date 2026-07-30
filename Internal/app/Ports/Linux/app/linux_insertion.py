"""Linux adapters for the shared exact-once insertion contract.

X11 can expose a stable foreground-window identity.  Wayland intentionally
does not, so the adapter fails closed to copy/saved-only rather than pretending
that an XWayland window is the user's native target.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

from insertion import (
    ClipboardOwnership, ClipboardRestoreResult, ClipboardRestoreState,
    ClipboardSnapshot, NativeAcceptance, TargetContext, TargetEditability,
)
from linux_desktop import DesktopSession, clipboard_capability


_TEXT_FORMAT = 1
_IMAGE_FORMAT = 2


def _run(argv, *, input_bytes=None, timeout=1.0):
    return subprocess.run(
        argv, input=input_bytes, capture_output=True, timeout=timeout,
        check=False, shell=False)


class LinuxTargetAdapter:
    def __init__(self, session=None, *, which=shutil.which, runner=_run):
        self.session = session or DesktopSession.detect()
        self.which = which
        self.runner = runner

    def _xdotool(self, *args):
        executable = self.which("xdotool")
        if not executable or self.session.session_type != "x11":
            return None
        try:
            completed = self.runner([executable, *args], timeout=0.4)
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode:
            return None
        value = completed.stdout.decode("utf-8", "replace").strip()
        return value

    def current(self):
        # Wayland deliberately prevents arbitrary clients from inspecting the
        # focused surface.  DISPLAY/XWayland presence must not bypass that rule.
        if self.session.session_type != "x11":
            return None
        window_text = self._xdotool("getwindowfocus")
        if not window_text or not window_text.isdigit():
            return None
        window = int(window_text)
        pid_text = self._xdotool("getwindowpid", str(window)) or "0"
        pid = int(pid_text) if pid_text.isdigit() else 0
        creation = 0
        try:
            creation = int(os.stat(f"/proc/{pid}").st_ctime_ns) if pid else 0
        except OSError:
            pass
        return TargetContext(
            window=window, process_id=pid, thread_id=pid,
            focused_child=window, integrity="user",
            editability=TargetEditability.UNKNOWN,
            integrity_relation="same", process_creation_id=creation,
            uia_state="unavailable")

    def can_inject(self, _target):
        return True if self.session.session_type == "x11" else False

    def restore(self, target, timeout_s):
        if self.session.session_type != "x11" or not target:
            return False
        executable = self.which("xdotool")
        if not executable:
            return False
        try:
            completed = self.runner(
                [executable, "windowactivate", "--sync", str(target.window)],
                timeout=max(0.1, min(1.0, float(timeout_s))))
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0 and target.same_destination(self.current())


class LinuxClipboardAdapter:
    """Bounded text/PNG snapshot with read-back ownership verification."""

    def __init__(self, session=None, *, which=shutil.which, runner=_run,
                 max_bytes=32 * 1024 * 1024):
        self.session = session or DesktopSession.detect()
        self.which = which
        self.runner = runner
        self.max_bytes = max(1024, int(max_bytes))
        self._sequence = 0
        self._lock = threading.Lock()

    def _read(self, image=False):
        if self.session.session_type == "wayland":
            executable = self.which("wl-paste")
            if not executable:
                return None
            argv = [executable, "--no-newline", "--type",
                    "image/png" if image else "text/plain;charset=utf-8"]
        elif self.session.session_type == "x11":
            executable = self.which("xclip") or self.which("xsel")
            if not executable:
                return None
            if os.path.basename(executable) == "xsel":
                if image:
                    return None
                argv = [executable, "--clipboard", "--output"]
            else:
                argv = [executable, "-selection", "clipboard", "-o"]
                if image:
                    argv += ["-t", "image/png"]
        else:
            return None
        try:
            completed = self.runner(argv, timeout=0.6)
        except (OSError, subprocess.SubprocessError):
            return None
        data = bytes(completed.stdout or b"")
        return data if completed.returncode == 0 and len(data) <= self.max_bytes else None

    def _write(self, data, image=False):
        if self.session.session_type == "wayland":
            executable = self.which("wl-copy")
            argv = ([executable, "--type", "image/png" if image else
                     "text/plain;charset=utf-8"] if executable else [])
        elif self.session.session_type == "x11":
            executable = self.which("xclip") or self.which("xsel")
            if executable and os.path.basename(executable) == "xsel" and not image:
                argv = [executable, "--clipboard", "--input"]
            elif executable:
                argv = [executable, "-selection", "clipboard", "-i"]
                if image:
                    argv += ["-t", "image/png"]
            else:
                argv = []
        else:
            argv = []
        if not argv:
            raise RuntimeError("clipboard_helper_unavailable")
        completed = self.runner(argv, input_bytes=data, timeout=1.0)
        if completed.returncode:
            raise RuntimeError("clipboard_write_failed")

    def snapshot(self):
        text = self._read(False)
        image = self._read(True)
        formats = []
        if text is not None:
            formats.append((_TEXT_FORMAT, "text/plain;charset=utf-8", text))
        if image is not None:
            formats.append((_IMAGE_FORMAT, "image/png", image))
        restorable = bool(formats)
        return ClipboardSnapshot(
            sequence=self._sequence, formats=tuple(formats),
            restorable=restorable,
            reason="" if restorable else "clipboard_unreadable_or_empty")

    def write(self, request, _snapshot):
        if request.content_kind == "image":
            data = Path(request.image_path).read_bytes()
            format_id = _IMAGE_FORMAT
            self._write(data, True)
        elif request.content_kind in {"text", "rich"}:
            data = request.text.encode("utf-8")
            format_id = _TEXT_FORMAT
            self._write(data, False)
        else:
            raise ValueError("unsupported insertion content kind")
        observed = self._read(format_id == _IMAGE_FORMAT)
        if observed != data:
            raise RuntimeError("clipboard_readback_mismatch")
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        return ClipboardOwnership(sequence, hashlib.sha256(data).hexdigest(),
                                  format_id)

    def still_owns(self, ownership):
        current = self._read(ownership.format_id == _IMAGE_FORMAT)
        return bool(current is not None and
                    hashlib.sha256(current).hexdigest() == ownership.fingerprint)

    def restore(self, snapshot, ownership=None):
        if ownership is not None and not self.still_owns(ownership):
            return ClipboardRestoreResult(ClipboardRestoreState.NEWER_EXTERNAL)
        try:
            # A Linux selection owner exposes one primary payload. Prefer the
            # richer image snapshot; otherwise restore text.
            chosen = next((row for row in snapshot.formats
                           if row[0] == _IMAGE_FORMAT), None)
            chosen = chosen or next((row for row in snapshot.formats
                                     if row[0] == _TEXT_FORMAT), None)
            if chosen is None:
                return ClipboardRestoreResult(ClipboardRestoreState.FAILED)
            self._write(chosen[2], chosen[0] == _IMAGE_FORMAT)
        except Exception:
            return ClipboardRestoreResult(ClipboardRestoreState.FAILED)
        return ClipboardRestoreResult(ClipboardRestoreState.RESTORED)


class LinuxNativeInputAdapter:
    def __init__(self, session=None, *, which=shutil.which, runner=_run):
        self.session = session or DesktopSession.detect()
        self.which = which
        self.runner = runner

    def ready(self, _timeout_s):
        capability = clipboard_capability(self.session, which=self.which)
        return (capability.status in {"ready", "degraded"}
                and bool(capability.backend not in {"none", "wl-clipboard"}),
                capability.message)

    def _send(self, key):
        if self.session.session_type == "wayland":
            if self.which("wtype"):
                argv = [self.which("wtype"), "-M", "ctrl", "-P", key,
                        "-p", key, "-m", "ctrl"]
            elif self.which("ydotool"):
                code = 47 if key == "v" else 44
                argv = [self.which("ydotool"), "key", "29:1", f"{code}:1",
                        f"{code}:0", "29:0"]
            else:
                return NativeAcceptance(1, 0, False, None,
                                        "wayland_insertion_unsupported")
        elif self.session.session_type == "x11" and self.which("xdotool"):
            argv = [self.which("xdotool"), "key", "--clearmodifiers", f"ctrl+{key}"]
        else:
            return NativeAcceptance(1, 0, False, None,
                                    "native_insertion_unsupported")
        try:
            completed = self.runner(argv, timeout=1.0)
        except (OSError, subprocess.SubprocessError) as exc:
            return NativeAcceptance(1, None, False, None, str(exc))
        accepted = 1 if completed.returncode == 0 else 0
        return NativeAcceptance(1, accepted, accepted > 0, None,
                                "" if accepted else "helper_rejected_input")

    def send_paste(self):
        return self._send("v")

    def send_undo(self):
        return self._send("z")
