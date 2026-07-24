"""Bounded observation of edits to the field that just received pasted text.

Optional Windows UI Automation packages are imported only when monitoring is
started. The pure :class:`InsertedSpanTracker` is platform independent and
retains hashes of surrounding text rather than the whole field value.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import importlib
import sys
import threading
import time
from typing import Any, Callable


_MAX_MONITOR_SECONDS = 30.0
_DEFAULT_POLL_SECONDS = 0.5
_DEFAULT_DEBOUNCE_SECONDS = 1.2
_DEFAULT_MAX_FIELD_CHARS = 200_000
_DEFAULT_MAX_SEGMENT_CHARS = 20_000


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).digest()


class InsertedSpanTracker:
    """Track edits confined to one exactly anchored inserted span.

    ``post_paste_text`` is used only during construction. The tracker keeps the
    inserted segment, prefix/suffix lengths, and cryptographic digests of the
    prefix/suffix; it never retains the complete field or its surrounding
    prose. ``observe()`` returns ``(original, corrected)`` once a changed span
    has remained stable for the debounce period.
    """

    def __init__(
        self,
        post_paste_text: str,
        original_segment: str,
        *,
        span_start: int | None = None,
        caret_end: int | None = None,
        debounce_seconds: float = _DEFAULT_DEBOUNCE_SECONDS,
        max_field_chars: int = _DEFAULT_MAX_FIELD_CHARS,
        max_segment_chars: int = _DEFAULT_MAX_SEGMENT_CHARS,
    ) -> None:
        if not isinstance(post_paste_text, str) or not isinstance(original_segment, str):
            raise TypeError("field text and inserted segment must be strings")
        if not original_segment:
            raise ValueError("inserted segment is required")
        if len(post_paste_text) > max_field_chars:
            raise ValueError("focused field is too large to monitor safely")
        if len(original_segment) > max_segment_chars:
            raise ValueError("inserted segment is too large to monitor safely")
        if span_start is None and caret_end is not None:
            if isinstance(caret_end, bool) or not isinstance(caret_end, int):
                raise TypeError("caret_end must be an integer")
            span_start = caret_end - len(original_segment)
        if span_start is None:
            occurrences: list[int] = []
            position = post_paste_text.find(original_segment)
            while position >= 0 and len(occurrences) < 2:
                occurrences.append(position)
                position = post_paste_text.find(original_segment, position + 1)
            if len(occurrences) != 1:
                raise ValueError(
                    "inserted segment cannot be located uniquely; provide its span start"
                )
            span_start = occurrences[0]
        if isinstance(span_start, bool) or not isinstance(span_start, int):
            raise TypeError("span_start must be an integer")
        span_end = span_start + len(original_segment)
        if (
            span_start < 0
            or span_end > len(post_paste_text)
            or post_paste_text[span_start:span_end] != original_segment
        ):
            raise ValueError("inserted segment does not match the focused field")

        prefix = post_paste_text[:span_start]
        suffix = post_paste_text[span_end:]
        self._original_segment = original_segment
        self._prefix_length = len(prefix)
        self._suffix_length = len(suffix)
        self._prefix_digest = _digest(prefix)
        self._suffix_digest = _digest(suffix)
        self._debounce_seconds = max(0.0, float(debounce_seconds))
        self._max_field_chars = max(1, int(max_field_chars))
        self._max_segment_chars = max(1, int(max_segment_chars))
        self._pending_segment: str | None = None
        self._pending_since: float | None = None
        self._emitted_segment: str | None = None
        self._invalidated = False

    @property
    def original_segment(self) -> str:
        return self._original_segment

    @property
    def invalidated(self) -> bool:
        return self._invalidated

    def extract(self, field_text: str) -> str | None:
        """Return the current anchored span, or invalidate on outside edits."""

        if self._invalidated:
            return None
        if not isinstance(field_text, str):
            self._invalidated = True
            return None
        minimum = self._prefix_length + self._suffix_length
        if len(field_text) < minimum or len(field_text) > self._max_field_chars:
            self._invalidated = True
            return None
        suffix_start = len(field_text) - self._suffix_length
        prefix = field_text[: self._prefix_length]
        suffix = field_text[suffix_start:] if self._suffix_length else ""
        if not hmac.compare_digest(_digest(prefix), self._prefix_digest):
            self._invalidated = True
            return None
        if not hmac.compare_digest(_digest(suffix), self._suffix_digest):
            self._invalidated = True
            return None
        segment = field_text[self._prefix_length : suffix_start]
        if len(segment) > self._max_segment_chars:
            self._invalidated = True
            return None
        return segment

    def observe(
        self,
        field_text: str,
        *,
        now: float | None = None,
    ) -> tuple[str, str] | None:
        """Observe a sample and emit a stable changed segment at most once."""

        timestamp = time.monotonic() if now is None else float(now)
        segment = self.extract(field_text)
        if segment is None:
            return None
        if segment == self._original_segment:
            self._pending_segment = None
            self._pending_since = None
            self._emitted_segment = None
            return None
        if segment != self._pending_segment:
            self._pending_segment = segment
            self._pending_since = timestamp
            return None
        if self._pending_since is None or timestamp < self._pending_since:
            self._pending_since = timestamp
            return None
        # A tiny tolerance avoids binary floating-point making an exact 1.2s
        # boundary appear infinitesimally shorter.
        if timestamp - self._pending_since + 1e-9 < self._debounce_seconds:
            return None
        if segment == self._emitted_segment:
            return None
        self._emitted_segment = segment
        return self._original_segment, segment


@dataclass(frozen=True)
class _FocusedEdit:
    identity: tuple[Any, ...]
    value: str
    caret_end: int | None
    is_password: bool
    is_editable: bool


class _RawUIABackend:
    """Raw UIAutomationCore adapter owned by one initialized COM thread."""

    _EM_GETSEL = 0x00B0

    def __init__(self) -> None:
        comtypes_module = importlib.import_module("comtypes")
        client = importlib.import_module("comtypes.client")
        self._uia = client.GetModule("UIAutomationCore.dll")
        self._automation = comtypes_module.CoCreateInstance(
            self._uia.CUIAutomation._reg_clsid_,
            interface=self._uia.IUIAutomation,
        )

    @staticmethod
    def foreground_id() -> int:
        import ctypes

        return int(ctypes.windll.user32.GetForegroundWindow())

    def _identity(self, element: Any) -> tuple[Any, ...]:
        try:
            runtime_id = tuple(int(value) for value in element.GetRuntimeId())
            if runtime_id:
                return ("runtime",) + runtime_id
        except Exception:
            pass
        return (
            "fallback",
            int(getattr(element, "CurrentProcessId", 0) or 0),
            int(getattr(element, "CurrentNativeWindowHandle", 0) or 0),
            str(getattr(element, "CurrentAutomationId", "") or ""),
            int(getattr(element, "CurrentControlType", 0) or 0),
        )

    @classmethod
    def _caret_end(cls, native_handle: int) -> int | None:
        if native_handle <= 0:
            return None
        try:
            import ctypes

            selection_start = ctypes.c_uint(0)
            selection_end = ctypes.c_uint(0)
            ctypes.windll.user32.SendMessageW(
                native_handle,
                cls._EM_GETSEL,
                ctypes.byref(selection_start),
                ctypes.byref(selection_end),
            )
            return int(selection_end.value)
        except Exception:
            return None

    def _value(self, element: Any) -> tuple[str, bool]:
        try:
            pattern = element.GetCurrentPattern(self._uia.UIA_ValuePatternId)
            value_pattern = pattern.QueryInterface(
                self._uia.IUIAutomationValuePattern
            )
            value = value_pattern.CurrentValue
            read_only = bool(value_pattern.CurrentIsReadOnly)
            if isinstance(value, str):
                return value, not read_only
        except Exception:
            pass
        try:
            pattern = element.GetCurrentPattern(self._uia.UIA_TextPatternId)
            text_pattern = pattern.QueryInterface(self._uia.IUIAutomationTextPattern)
            document_range = text_pattern.DocumentRange
            value = document_range.GetText(-1)
            if isinstance(value, str):
                return value, True
        except Exception:
            pass
        raise RuntimeError("focused control does not expose editable text")

    def focused_edit(self) -> _FocusedEdit:
        element = self._automation.GetFocusedElement()
        if element is None:
            raise RuntimeError("there is no focused UI Automation element")
        password = bool(element.CurrentIsPassword)
        control_type = int(getattr(element, "CurrentControlType", 0) or 0)
        editable_type = control_type in {
            self._uia.UIA_EditControlTypeId,
            self._uia.UIA_DocumentControlTypeId,
        }
        if password:
            return _FocusedEdit(self._identity(element), "", None, True, False)
        if not editable_type:
            return _FocusedEdit(self._identity(element), "", None, False, False)
        native_handle = int(getattr(element, "CurrentNativeWindowHandle", 0) or 0)
        value, writable = self._value(element)
        return _FocusedEdit(
            identity=self._identity(element),
            value=value,
            caret_end=self._caret_end(native_handle),
            is_password=False,
            is_editable=editable_type and writable,
        )


class UIAEditMonitor:
    """Watch only the focused post-paste edit span for at most 30 seconds."""

    def __init__(
        self,
        *,
        poll_interval: float = _DEFAULT_POLL_SECONDS,
        debounce_seconds: float = _DEFAULT_DEBOUNCE_SECONDS,
        max_seconds: float = _MAX_MONITOR_SECONDS,
        backend_factory: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.poll_interval = min(1.0, max(0.1, float(poll_interval)))
        self.debounce_seconds = max(0.0, float(debounce_seconds))
        self.max_seconds = min(
            _MAX_MONITOR_SECONDS,
            max(self.poll_interval, float(max_seconds)),
        )
        self._backend_factory = backend_factory
        self._clock = clock
        self._lock = threading.RLock()
        self._cancel_event: threading.Event | None = None
        self._generation = 0
        self._warm_lock = threading.RLock()
        self._warm_event = threading.Event()
        self._warm_state = "ready" if backend_factory is not None else "idle"
        self._warm_reason: str | None = None
        if backend_factory is not None:
            self._warm_event.set()
        self._status: dict[str, Any] = {
            "active": False,
            "state": "idle",
            "reason": None,
            "supported": None if sys.platform == "win32" else False,
        }

    @staticmethod
    def platform_supported() -> bool:
        return sys.platform == "win32"

    @staticmethod
    def _import_optional_modules() -> None:
        """Warm imports only; deliberately create no COM/UIA backend object."""

        client = importlib.import_module("comtypes.client")
        client.GetModule("UIAutomationCore.dll")

    def _prewarm_worker(self) -> None:
        try:
            self._import_optional_modules()
        except (ImportError, ModuleNotFoundError):
            state, reason = "unavailable", "missing_dependency"
        except Exception:
            state, reason = "unavailable", "backend_unavailable"
        else:
            state, reason = "ready", None
        with self._warm_lock:
            self._warm_state = state
            self._warm_reason = reason
            self._warm_event.set()

    def prewarm(
        self,
        *,
        wait: bool = False,
        timeout: float = _MAX_MONITOR_SECONDS,
    ) -> dict[str, Any]:
        """Warm optional modules asynchronously without creating UIA objects."""

        if self._backend_factory is not None:
            return {"ok": True, "state": "ready", "ready": True, "reason": None}
        if not self.platform_supported():
            return {
                "ok": False,
                "state": "unavailable",
                "ready": False,
                "reason": "unsupported_platform",
            }
        with self._warm_lock:
            if self._warm_state == "idle":
                self._warm_state = "warming"
                self._warm_reason = None
                self._warm_event.clear()
                threading.Thread(
                    target=self._prewarm_worker,
                    name="mumble-correction-prewarm",
                    daemon=True,
                ).start()
            event = self._warm_event
        if wait:
            event.wait(min(_MAX_MONITOR_SECONDS, max(0.0, float(timeout))))
        with self._warm_lock:
            state = self._warm_state
            reason = self._warm_reason
        ready = state == "ready"
        return {
            "ok": state in {"warming", "ready"},
            "state": state,
            "ready": ready,
            "reason": reason,
        }

    def status(self) -> dict[str, Any]:
        with self._warm_lock:
            warm_state = self._warm_state
        with self._lock:
            result = dict(self._status)
        result["prewarm_state"] = warm_state
        return result

    def _set_terminal(self, generation: int, state: str, reason: str) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._status = {
                "active": False,
                "state": state,
                "reason": reason,
                "supported": self._status.get("supported"),
            }
            self._cancel_event = None

    def start(
        self,
        original_segment: str,
        callback: Callable[[str, str], Any],
        *,
        span_start: int | None = None,
    ) -> dict[str, Any]:
        """Start after paste; return immediately with a status dictionary."""

        self.cancel()
        if not isinstance(original_segment, str) or not original_segment:
            return {
                "ok": False,
                "message": "The pasted segment is required.",
                "reason": "invalid_segment",
            }
        if not callable(callback):
            return {
                "ok": False,
                "message": "A correction callback is required.",
                "reason": "invalid_callback",
            }
        if self._backend_factory is None and not self.platform_supported():
            with self._lock:
                self._status = {
                    "active": False,
                    "state": "unavailable",
                    "reason": "unsupported_platform",
                    "supported": False,
                }
            return {
                "ok": False,
                "message": "Focused edit monitoring is unavailable on this system.",
                "reason": "unsupported_platform",
            }
        if self._backend_factory is None:
            warm = self.prewarm()
            if not warm["ready"]:
                reason = warm["reason"] or "warming_up"
                message = (
                    "Focused edit monitoring dependencies are still warming up."
                    if reason == "warming_up"
                    else "Focused edit monitoring is unavailable on this system."
                )
                with self._lock:
                    self._status = {
                        "active": False,
                        "state": "unavailable",
                        "reason": reason,
                        "supported": False if warm["state"] == "unavailable" else None,
                    }
                return {"ok": False, "message": message, "reason": reason}

        cancel_event = threading.Event()
        ready_event = threading.Event()
        setup_result: dict[str, Any] = {}
        started_at = self._clock()
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._cancel_event = cancel_event
            self._status = {
                "active": True,
                "state": "starting",
                "reason": None,
                "supported": None,
            }
        thread = threading.Thread(
            target=self._bootstrap_and_run,
            args=(
                generation,
                cancel_event,
                ready_event,
                setup_result,
                original_segment,
                span_start,
                callback,
                started_at,
            ),
            name="mumble-correction-monitor",
            daemon=True,
        )
        thread.start()
        # COM and focus capture belong to the monitor's apartment. Wait only a
        # short bounded interval so callers still receive a truthful setup
        # result without handing a COM object across threads.
        if not ready_event.wait(min(2.0, self.max_seconds)):
            cancel_event.set()
            self._set_terminal(generation, "unavailable", "setup_timeout")
            return {
                "ok": False,
                "message": "Focused edit monitoring took too long to start.",
                "reason": "setup_timeout",
            }
        return dict(setup_result)

    def _setup_failed(
        self,
        generation: int,
        ready_event: threading.Event,
        setup_result: dict[str, Any],
        reason: str,
        message: str,
        *,
        supported: bool,
    ) -> None:
        with self._lock:
            if generation == self._generation:
                self._status = {
                    "active": False,
                    "state": "unavailable",
                    "reason": reason,
                    "supported": supported,
                }
                self._cancel_event = None
        setup_result.update({"ok": False, "message": message, "reason": reason})
        ready_event.set()

    def _bootstrap_and_run(
        self,
        generation: int,
        cancel_event: threading.Event,
        ready_event: threading.Event,
        setup_result: dict[str, Any],
        original_segment: str,
        span_start: int | None,
        callback: Callable[[str, str], Any],
        started_at: float,
    ) -> None:
        comtypes_module: Any | None = None
        com_initialized = False
        try:
            if self._backend_factory is not None:
                try:
                    backend = self._backend_factory()
                except Exception:
                    self._setup_failed(
                        generation,
                        ready_event,
                        setup_result,
                        "backend_unavailable",
                        "Focused edit monitoring is unavailable on this system.",
                        supported=False,
                    )
                    return
            else:
                try:
                    comtypes_module = importlib.import_module("comtypes")
                    initializer = getattr(comtypes_module, "CoInitialize", None)
                    if callable(initializer):
                        initializer()
                        com_initialized = True
                    backend = _RawUIABackend()
                except (ImportError, ModuleNotFoundError):
                    self._setup_failed(
                        generation,
                        ready_event,
                        setup_result,
                        "missing_dependency",
                        "Focused edit monitoring is unavailable on this system.",
                        supported=False,
                    )
                    return
                except Exception:
                    self._setup_failed(
                        generation,
                        ready_event,
                        setup_result,
                        "backend_unavailable",
                        "Focused edit monitoring is unavailable on this system.",
                        supported=False,
                    )
                    return
            if cancel_event.is_set():
                self._setup_failed(
                    generation,
                    ready_event,
                    setup_result,
                    "cancelled",
                    "Focused edit monitoring was cancelled.",
                    supported=True,
                )
                return
            try:
                foreground = int(backend.foreground_id())
                focused = backend.focused_edit()
            except Exception:
                self._setup_failed(
                    generation,
                    ready_event,
                    setup_result,
                    "focus_unavailable",
                    "The focused text field could not be inspected.",
                    supported=True,
                )
                return
            if not foreground:
                self._setup_failed(
                    generation,
                    ready_event,
                    setup_result,
                    "no_foreground_window",
                    "There is no foreground window to monitor.",
                    supported=True,
                )
                return
            if focused.is_password:
                self._setup_failed(
                    generation,
                    ready_event,
                    setup_result,
                    "password_control",
                    "Password fields are never monitored.",
                    supported=True,
                )
                return
            if not focused.is_editable:
                self._setup_failed(
                    generation,
                    ready_event,
                    setup_result,
                    "not_editable",
                    "The focused control is not an editable text field.",
                    supported=True,
                )
                return
            try:
                tracker = InsertedSpanTracker(
                    focused.value,
                    original_segment,
                    span_start=span_start,
                    caret_end=None if span_start is not None else focused.caret_end,
                    debounce_seconds=self.debounce_seconds,
                )
            except (TypeError, ValueError):
                # Some UIA providers report a stale/meaningless caret index.
                # Fall back to an exact unique occurrence before failing closed.
                try:
                    tracker = InsertedSpanTracker(
                        focused.value,
                        original_segment,
                        span_start=span_start,
                        debounce_seconds=self.debounce_seconds,
                    )
                except (TypeError, ValueError):
                    self._setup_failed(
                        generation,
                        ready_event,
                        setup_result,
                        "span_not_isolated",
                        "The pasted span could not be isolated safely.",
                        supported=True,
                    )
                    return
            identity = focused.identity
            del focused
            with self._lock:
                if generation != self._generation or cancel_event.is_set():
                    self._setup_failed(
                        generation,
                        ready_event,
                        setup_result,
                        "cancelled",
                        "Focused edit monitoring was cancelled.",
                        supported=True,
                    )
                    return
                self._status = {
                    "active": True,
                    "state": "monitoring",
                    "reason": None,
                    "supported": True,
                }
            setup_result.update(
                {
                    "ok": True,
                    "message": "Monitoring the pasted text for a correction.",
                    "reason": None,
                }
            )
            ready_event.set()
            self._run(
                generation,
                cancel_event,
                backend,
                foreground,
                identity,
                tracker,
                callback,
                started_at,
            )
        finally:
            if not ready_event.is_set():
                self._setup_failed(
                    generation,
                    ready_event,
                    setup_result,
                    "backend_unavailable",
                    "Focused edit monitoring could not be started.",
                    supported=False,
                )
            if com_initialized and comtypes_module is not None:
                try:
                    uninitializer = getattr(comtypes_module, "CoUninitialize", None)
                    if callable(uninitializer):
                        uninitializer()
                except Exception:
                    pass

    def _run(
        self,
        generation: int,
        cancel_event: threading.Event,
        backend: Any,
        foreground: int,
        identity: tuple[Any, ...],
        tracker: InsertedSpanTracker,
        callback: Callable[[str, str], Any],
        started_at: float,
    ) -> None:
        deadline = started_at + self.max_seconds
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                self._set_terminal(generation, "expired", "time_limit")
                return
            if cancel_event.wait(min(self.poll_interval, remaining)):
                self._set_terminal(generation, "cancelled", "cancelled")
                return
            try:
                if int(backend.foreground_id()) != foreground:
                    self._set_terminal(
                        generation, "cancelled", "foreground_changed"
                    )
                    return
                focused = backend.focused_edit()
            except Exception:
                self._set_terminal(generation, "cancelled", "focus_unavailable")
                return
            if focused.identity != identity:
                self._set_terminal(generation, "cancelled", "focus_changed")
                return
            if focused.is_password:
                self._set_terminal(generation, "cancelled", "password_control")
                return
            sample_value = focused.value
            del focused
            correction = tracker.observe(sample_value, now=self._clock())
            del sample_value
            if tracker.invalidated:
                self._set_terminal(generation, "cancelled", "outside_span_changed")
                return
            if correction is None:
                continue
            if cancel_event.is_set():
                self._set_terminal(generation, "cancelled", "cancelled")
                return
            try:
                callback(*correction)
            except Exception:
                self._set_terminal(generation, "failed", "callback_error")
                return
            self._set_terminal(generation, "completed", "correction_observed")
            return

    def cancel(self) -> None:
        """Stop an active observation without waiting on UI Automation."""

        with self._lock:
            cancel_event = self._cancel_event
            if cancel_event is None:
                return
            self._status = {
                "active": False,
                "state": "cancelled",
                "reason": "cancelled",
                "supported": self._status.get("supported"),
            }
            self._cancel_event = None
        cancel_event.set()


__all__ = ["InsertedSpanTracker", "UIAEditMonitor"]
