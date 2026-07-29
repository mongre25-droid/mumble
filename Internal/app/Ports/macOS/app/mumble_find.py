"""Resident Mumble Find window lifecycle and safe focus ownership."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import threading


class NullForegroundFocus:
    """Truthful fallback where this Stage A has no native focus adapter."""

    def capture(self):
        return None

    def restore(self, _target):
        return False


@dataclass(frozen=True)
class WindowsWindowIdentity:
    """Reuse-resistant identity for one Windows top-level window."""

    hwnd: int
    thread_id: int
    process_id: int
    process_created: int


class WindowsForegroundFocus:
    """Capture and cooperatively restore a valid Windows foreground window."""

    SW_RESTORE = 9

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def __init__(self, find_title, *, user32=None, kernel32=None,
                 find_window=None, identity_resolver=None):
        native_apis = user32 is None
        if user32 is None:
            import ctypes
            user32 = ctypes.windll.user32
        if kernel32 is None and identity_resolver is None:
            import ctypes
            kernel32 = ctypes.windll.kernel32
        self._user32 = user32
        self._kernel32 = kernel32
        self._find_title = str(find_title)
        self._find_window = find_window or self._lookup_find_window
        self._identity_resolver = identity_resolver or self._resolve_identity
        if native_apis and identity_resolver is None:
            self._configure_native_apis()

    def _configure_native_apis(self):
        """Keep 64-bit HWND/HANDLE values intact across ctypes calls."""
        try:
            import ctypes
            from ctypes import wintypes

            self._user32.FindWindowW.argtypes = [
                wintypes.LPCWSTR, wintypes.LPCWSTR,
            ]
            self._user32.FindWindowW.restype = wintypes.HWND
            self._user32.GetForegroundWindow.argtypes = []
            self._user32.GetForegroundWindow.restype = wintypes.HWND
            self._user32.GetWindowThreadProcessId.argtypes = [
                wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
            ]
            self._user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            for name in ("IsWindow", "IsWindowVisible", "IsIconic"):
                function = getattr(self._user32, name)
                function.argtypes = [wintypes.HWND]
                function.restype = wintypes.BOOL
            self._user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            self._user32.ShowWindow.restype = wintypes.BOOL
            self._user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            self._user32.SetForegroundWindow.restype = wintypes.BOOL

            self._kernel32.OpenProcess.argtypes = [
                wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
            ]
            self._kernel32.OpenProcess.restype = wintypes.HANDLE
            filetime_pointer = ctypes.POINTER(wintypes.FILETIME)
            self._kernel32.GetProcessTimes.argtypes = [
                wintypes.HANDLE, filetime_pointer, filetime_pointer,
                filetime_pointer, filetime_pointer,
            ]
            self._kernel32.GetProcessTimes.restype = wintypes.BOOL
            self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            self._kernel32.CloseHandle.restype = wintypes.BOOL
        except Exception:
            # Capture/restore still fails closed if the native declarations
            # cannot be established on this host.
            pass

    def _lookup_find_window(self):
        try:
            return int(self._user32.FindWindowW(None, self._find_title) or 0)
        except Exception:
            return 0

    @staticmethod
    def _normalise_identity(value):
        if isinstance(value, WindowsWindowIdentity):
            return value
        try:
            identity = WindowsWindowIdentity(*(int(part) for part in value))
        except (TypeError, ValueError):
            return None
        return identity if all((
            identity.hwnd, identity.thread_id, identity.process_id,
            identity.process_created,
        )) else None

    def _resolve_identity(self, hwnd):
        """Resolve HWND ownership plus process creation time at capture/use."""
        try:
            import ctypes
            from ctypes import wintypes

            process_id = wintypes.DWORD()
            thread_id = int(self._user32.GetWindowThreadProcessId(
                int(hwnd), ctypes.byref(process_id)
            ) or 0)
            if not thread_id or not process_id.value:
                return None
            process = self._kernel32.OpenProcess(
                self.PROCESS_QUERY_LIMITED_INFORMATION,
                False,
                process_id.value,
            )
            if not process:
                return None
            try:
                created = wintypes.FILETIME()
                exited = wintypes.FILETIME()
                kernel = wintypes.FILETIME()
                user = wintypes.FILETIME()
                if not self._kernel32.GetProcessTimes(
                        process, ctypes.byref(created), ctypes.byref(exited),
                        ctypes.byref(kernel), ctypes.byref(user)):
                    return None
                creation_identity = (
                    int(created.dwHighDateTime) << 32
                ) | int(created.dwLowDateTime)
            finally:
                self._kernel32.CloseHandle(process)
            return WindowsWindowIdentity(
                int(hwnd), thread_id, int(process_id.value), creation_identity
            )
        except Exception:
            return None

    def _safe_target(self, target):
        try:
            identity = self._normalise_identity(target)
            if identity is None:
                return False
            find_hwnd = int(self._find_window() or 0)
            if (identity.hwnd == find_hwnd
                    or not self._user32.IsWindow(identity.hwnd)
                    or not self._user32.IsWindowVisible(identity.hwnd)):
                return False
            current = self._normalise_identity(
                self._identity_resolver(identity.hwnd)
            )
            return current == identity
        except Exception:
            return False

    def capture(self):
        try:
            hwnd = int(self._user32.GetForegroundWindow() or 0)
            target = self._normalise_identity(self._identity_resolver(hwnd))
        except Exception:
            return None
        return target if self._safe_target(target) else None

    def restore(self, target):
        if not self._safe_target(target):
            return False
        try:
            hwnd = target.hwnd
            if self._user32.IsIconic(hwnd):
                self._user32.ShowWindow(hwnd, self.SW_RESTORE)
            # Do not alter the system foreground-lock policy or attach threads.
            return bool(self._user32.SetForegroundWindow(hwnd))
        except Exception:
            return False


class MumbleFindLifecycle:
    """Own exactly one hidden-or-visible Mumble Find window.

    The window factory and focus adapter are operating-system seams.  The
    lifecycle itself never knows about dictation, so showing or hiding Find
    cannot start, stop, or retarget a dictation session.
    """

    OPERATION_OUTCOME_LIMIT = 256

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
        self._operation_outcomes = OrderedDict()

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

    @property
    def operation_outcome_count(self):
        with self._lock:
            return len(self._operation_outcomes)

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
                        "Mumble Find closed, but Mumble could not safely restore "
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

    def toggle(self, operation_id=None):
        with self._lock:
            if operation_id is None:
                return self.hide() if self._visible else self.show()
            operation_id = str(operation_id).strip().casefold()
            if (len(operation_id) != 32
                    or any(character not in "0123456789abcdef"
                           for character in operation_id)):
                return {
                    "ok": False,
                    "state": "visible" if self._visible else "hidden",
                    "changed": False,
                    "resident": self._window is not None,
                    "message": "Mumble Find received an invalid operation identity.",
                }
            prior = self._operation_outcomes.get(operation_id)
            if prior is not None:
                return dict(prior)
            outcome = self.hide() if self._visible else self.show()
            outcome["operation_id"] = operation_id
            self._operation_outcomes[operation_id] = dict(outcome)
            while len(self._operation_outcomes) > self.OPERATION_OUTCOME_LIMIT:
                self._operation_outcomes.popitem(last=False)
            return dict(outcome)

    def window_closed(self, window):
        """Forget an externally closed window so the next action can recover."""
        with self._lock:
            if window is not self._window:
                return False
            self._window = None
            self._visible = False
            self._prior_focus = None
            return True
