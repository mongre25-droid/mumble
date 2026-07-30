"""macOS focus capture/restore seam for momentary Mumble windows."""

from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class MacAppIdentity:
    pid: int
    bundle_id: str
    launched_at: float


class MacForegroundFocus:
    def __init__(self, workspace=None, own_pid=None):
        self._workspace = workspace
        self._own_pid = int(own_pid or os.getpid())

    def _ws(self):
        if self._workspace is not None:
            return self._workspace
        from AppKit import NSWorkspace
        return NSWorkspace.sharedWorkspace()

    def capture(self):
        try:
            app = self._ws().frontmostApplication()
            pid = int(app.processIdentifier()) if app is not None else 0
            if not pid or pid == self._own_pid:
                return None
            launched = app.launchDate()
            launched_at = (
                float(launched.timeIntervalSince1970())
                if launched is not None else 0.0
            )
            bundle_id = str(app.bundleIdentifier() or "")
            if not bundle_id and not launched_at:
                return None
            return MacAppIdentity(pid, bundle_id, launched_at)
        except Exception:
            return None

    def restore(self, target):
        try:
            if not isinstance(target, MacAppIdentity):
                return False
            pid = int(target.pid or 0)
            if not pid or pid == self._own_pid:
                return False
            from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            if app is None:
                return False
            launched = app.launchDate()
            current = MacAppIdentity(
                pid,
                str(app.bundleIdentifier() or ""),
                float(launched.timeIntervalSince1970()) if launched is not None else 0.0,
            )
            if current != target:
                return False
            return bool(
                app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            )
        except Exception:
            return False
