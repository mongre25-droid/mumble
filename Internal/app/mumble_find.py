"""Resident Mumble Find window lifecycle and safe focus ownership."""

from __future__ import annotations

import threading


class NullForegroundFocus:
    """Truthful fallback where this Stage A has no native focus adapter."""

    def capture(self):
        return None

    def restore(self, _target):
        return False


class WindowsForegroundFocus:
    """Capture and cooperatively restore a valid Windows foreground window."""

    SW_RESTORE = 9

    def __init__(self, find_title, *, user32=None, find_window=None):
        if user32 is None:
            import ctypes
            user32 = ctypes.windll.user32
        self._user32 = user32
        self._find_title = str(find_title)
        self._find_window = find_window or self._lookup_find_window

    def _lookup_find_window(self):
        try:
            return int(self._user32.FindWindowW(None, self._find_title) or 0)
        except Exception:
            return 0

    def _safe_target(self, target):
        try:
            target = int(target or 0)
            find_hwnd = int(self._find_window() or 0)
            return bool(
                target
                and target != find_hwnd
                and self._user32.IsWindow(target)
                and self._user32.IsWindowVisible(target)
            )
        except Exception:
            return False

    def capture(self):
        try:
            target = int(self._user32.GetForegroundWindow() or 0)
        except Exception:
            return None
        return target if self._safe_target(target) else None

    def restore(self, target):
        if not self._safe_target(target):
            return False
        try:
            if self._user32.IsIconic(target):
                self._user32.ShowWindow(target, self.SW_RESTORE)
            # Do not alter the system foreground-lock policy or attach threads.
            return bool(self._user32.SetForegroundWindow(target))
        except Exception:
            return False


class MumbleFindLifecycle:
    """Own exactly one hidden-or-visible Mumble Find window.

    The window factory and focus adapter are operating-system seams.  The
    lifecycle itself never knows about dictation, so showing or hiding Find
    cannot start, stop, or retarget a dictation session.
    """

    def __init__(self, create_window, focus_adapter, *, show_window=None,
                 hide_window=None):
        self._create_window = create_window
        self._focus = focus_adapter
        self._show_window = show_window or self._default_show
        self._hide_window = hide_window or self._default_hide
        self._lock = threading.RLock()
        self._window = None
        self._visible = False
        self._prior_focus = None

    @staticmethod
    def _default_show(window):
        window.show()
        restore = getattr(window, "restore", None)
        if callable(restore):
            restore()

    @staticmethod
    def _default_hide(window):
        window.hide()

    @property
    def visible(self):
        with self._lock:
            return self._visible

    @property
    def window(self):
        with self._lock:
            return self._window

    def ensure_resident(self):
        with self._lock:
            created = False
            if self._window is None:
                try:
                    self._window = self._create_window(
                        hidden=True, centered=True)
                except Exception as exc:
                    return {
                        "ok": False,
                        "state": "unavailable",
                        "changed": False,
                        "resident": False,
                        "message": (
                            "Mumble Find could not create its window: "
                            f"{str(exc)[:160]}"
                        ),
                    }
                if self._window is None:
                    return {
                        "ok": False,
                        "state": "unavailable",
                        "changed": False,
                        "message": "Mumble Find could not create its window.",
                    }
                created = True
            return {
                "ok": True,
                "state": "visible" if self._visible else "hidden",
                "changed": created,
                "resident": True,
                "message": "",
            }

    def show(self):
        with self._lock:
            resident = self.ensure_resident()
            if not resident.get("ok"):
                return resident
            if self._visible:
                return {
                    "ok": True,
                    "state": "visible",
                    "changed": False,
                    "resident": True,
                    "message": "",
                }
            try:
                prior_focus = self._focus.capture()
            except Exception:
                prior_focus = None
            try:
                self._show_window(self._window)
            except Exception as exc:
                return {
                    "ok": False,
                    "state": "hidden",
                    "changed": False,
                    "resident": True,
                    "message": (
                        "Mumble Find stayed hidden because its window could not "
                        f"be shown: {str(exc)[:160]}"
                    ),
                }
            self._prior_focus = prior_focus
            self._visible = True
            return {
                "ok": True,
                "state": "visible",
                "changed": True,
                "resident": True,
                "focus_captured": prior_focus is not None,
                "message": "",
            }

    def hide(self):
        with self._lock:
            if self._window is None or not self._visible:
                return {
                    "ok": True,
                    "state": "hidden",
                    "changed": False,
                    "resident": self._window is not None,
                    "message": "",
                }
            try:
                self._hide_window(self._window)
            except Exception as exc:
                return {
                    "ok": False,
                    "state": "visible",
                    "changed": False,
                    "resident": True,
                    "message": (
                        "Mumble Find remained visible because it could not be "
                        f"hidden: {str(exc)[:160]}"
                    ),
                }

            target = self._prior_focus
            self._prior_focus = None
            self._visible = False
            restored = None
            if target is not None:
                try:
                    restored = bool(self._focus.restore(target))
                except Exception:
                    restored = False
            if restored is False:
                return {
                    "ok": False,
                    "state": "hidden",
                    "changed": True,
                    "resident": True,
                    "focus_restored": False,
                    "message": (
                        "Mumble Find closed, but Windows could not safely restore "
                        "focus to the previous window."
                    ),
                }
            return {
                "ok": True,
                "state": "hidden",
                "changed": True,
                "resident": True,
                "focus_restored": restored,
                "message": "",
            }

    def toggle(self):
        with self._lock:
            return self.hide() if self._visible else self.show()

    def window_closed(self, window):
        """Forget an externally closed window so the next action can recover."""
        with self._lock:
            if window is not self._window:
                return False
            self._window = None
            self._visible = False
            self._prior_focus = None
            return True
