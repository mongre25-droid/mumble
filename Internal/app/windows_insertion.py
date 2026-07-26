"""Windows adapters for Mumble's target-bound insertion transaction."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import importlib
import io
import os
import struct
import threading
import time

from insertion import (
    ClipboardOwnership,
    ClipboardRestoreResult,
    ClipboardRestoreState,
    ClipboardSnapshot,
    ClipboardWriteFailure,
    NativeAcceptance,
    TargetContext,
    TargetEditability,
)


CF_BITMAP = 2
CF_METAFILEPICT = 3
CF_DIB = 8
CF_PALETTE = 9
CF_UNICODETEXT = 13
CF_ENHMETAFILE = 14
CF_HDROP = 15
CF_DIBV5 = 17
GMEM_MOVEABLE = 0x0002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_INTEGRITY_LEVEL = 25
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1
VK_CONTROL = 0x11
VK_V = 0x56
VK_Z = 0x5A
GWL_STYLE = -16
ES_PASSWORD = 0x0020
ES_READONLY = 0x0800


class _PartialClipboardReplacement(RuntimeError):
    def __init__(self, reason, sequence, written, *, ownership_verifiable=True):
        super().__init__(str(reason))
        self.sequence = int(sequence)
        self.written = tuple(written)
        self.ownership_verifiable = bool(ownership_verifiable)


class _ClipboardOwnershipChanged(RuntimeError):
    pass


class _ClipboardOwnershipUnverifiable(RuntimeError):
    pass


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", wintypes.DWORD), ("value", _INPUT_UNION)]


def _keyboard_input(vk, key_up=False):
    return _INPUT(
        type=INPUT_KEYBOARD,
        value=_INPUT_UNION(ki=_KEYBDINPUT(
            wVk=vk,
            wScan=0,
            dwFlags=KEYEVENTF_KEYUP if key_up else 0,
            time=0,
            dwExtraInfo=0,
        )),
    )


def _unicode_input(code_unit, key_up=False):
    return _INPUT(
        type=INPUT_KEYBOARD,
        value=_INPUT_UNION(ki=_KEYBDINPUT(
            wVk=0,
            wScan=int(code_unit),
            dwFlags=KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if key_up else 0),
            time=0,
            dwExtraInfo=0,
        )),
    )


@dataclass(frozen=True)
class UIAEvidence:
    runtime_id: tuple = ()
    observed: bool = False
    state: str = "unavailable"


class WindowsUIAutomationEvidenceProvider:
    """Best-effort focused-element identity with a strict observation bound."""

    def __init__(self):
        self._lock = threading.Lock()
        self._in_flight = False

    def capture(self, timeout_s=0.05):
        with self._lock:
            if self._in_flight:
                return UIAEvidence(state="timed_out")
            self._in_flight = True
        completed = threading.Event()
        result = {}

        def worker():
            comtypes_module = None
            initialized = False
            try:
                comtypes_module = importlib.import_module("comtypes")
                initializer = getattr(comtypes_module, "CoInitialize", None)
                if callable(initializer):
                    initializer()
                    initialized = True
                client = importlib.import_module("comtypes.client")
                uia = client.GetModule("UIAutomationCore.dll")
                automation = comtypes_module.CoCreateInstance(
                    uia.CUIAutomation._reg_clsid_,
                    interface=uia.IUIAutomation,
                )
                element = automation.GetFocusedElement()
                runtime_id = tuple(
                    int(value) for value in element.GetRuntimeId()
                ) if element is not None else ()
                result["value"] = UIAEvidence(
                    runtime_id=runtime_id,
                    observed=bool(runtime_id),
                    state="observed" if runtime_id else "unavailable",
                )
            except (ImportError, ModuleNotFoundError):
                result["value"] = UIAEvidence(state="unavailable")
            except Exception:
                result["value"] = UIAEvidence(state="transient")
            finally:
                if initialized and comtypes_module is not None:
                    try:
                        uninitializer = getattr(
                            comtypes_module, "CoUninitialize", None)
                        if callable(uninitializer):
                            uninitializer()
                    except Exception:
                        pass
                with self._lock:
                    self._in_flight = False
                completed.set()

        threading.Thread(
            target=worker, name="mumble-uia-target", daemon=True,
        ).start()
        if not completed.wait(max(0.0, float(timeout_s))):
            return UIAEvidence(state="timed_out")
        return result.get("value", UIAEvidence(state="transient"))


class WindowsTargetAdapter:
    def __init__(self, user32=None, kernel32=None, advapi32=None,
                 uia_provider=None, *, uia_timeout_s=0.05):
        self.user32 = user32 or ctypes.windll.user32
        self.kernel32 = kernel32 or ctypes.windll.kernel32
        self.advapi32 = advapi32 or ctypes.windll.advapi32
        if user32 is None and kernel32 is None and advapi32 is None:
            self._configure_ctypes()
        self._own_integrity = self._process_integrity(os.getpid())
        self._uia_provider = (uia_provider if uia_provider is not None else
                              WindowsUIAutomationEvidenceProvider())
        self._uia_timeout_s = max(0.0, float(uia_timeout_s))

    def _configure_ctypes(self):
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.kernel32.OpenProcess.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        self.kernel32.GetProcessTimes.restype = wintypes.BOOL
        self.advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        self.advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
        self.advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
        self.advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        self.advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)

    def current(self):
        hwnd = int(self.user32.GetForegroundWindow() or 0)
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        tid = int(self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or 0)
        info = _GUITHREADINFO()
        info.cbSize = ctypes.sizeof(_GUITHREADINFO)
        focus = hwnd
        caret = False
        if tid and self.user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
            focus = int(info.hwndFocus or hwnd)
            caret = bool(info.hwndCaret)
        class_name = ""
        style = 0
        if focus:
            buf = ctypes.create_unicode_buffer(128)
            if self.user32.GetClassNameW(focus, buf, len(buf)):
                class_name = buf.value
            try:
                style = int(self.user32.GetWindowLongW(focus, GWL_STYLE) or 0)
            except Exception:
                style = 0
        class_key = class_name.casefold()
        protected = bool(style & ES_PASSWORD)
        read_only = bool(style & ES_READONLY)
        enabled = bool(focus and self.user32.IsWindowEnabled(focus))
        if not focus or not enabled or protected or read_only:
            editability = TargetEditability.NOT_EDITABLE
        elif caret or class_key == "edit" or class_key.startswith("richedit"):
            editability = TargetEditability.EDITABLE
        elif class_key in {"button", "static", "listbox", "syslistview32",
                           "systreeview32", "toolbarwindow32"}:
            editability = TargetEditability.NOT_EDITABLE
        else:
            # Browser/editor class names alone are not proof that the focused
            # child is a writable field. Physical coverage supplies that evidence.
            editability = TargetEditability.UNKNOWN
        target_integrity = self._process_integrity(int(pid.value)) or "unknown"
        own_rank = _integrity_rank(self._own_integrity)
        target_rank = _integrity_rank(target_integrity)
        if own_rank is None or target_rank is None:
            integrity_relation = "unknown"
        elif target_rank == own_rank:
            integrity_relation = "same"
        elif target_rank > own_rank:
            integrity_relation = "higher"
        else:
            integrity_relation = "lower"
        process_creation_id = self._process_creation_identity(int(pid.value))
        provider = getattr(self, "_uia_provider", None)
        if provider is None:
            uia = UIAEvidence(state="unavailable")
        else:
            try:
                uia = provider.capture(getattr(self, "_uia_timeout_s", 0.05))
            except Exception:
                uia = UIAEvidence(state="transient")
        return TargetContext(
            window=hwnd,
            process_id=int(pid.value),
            thread_id=tid,
            focused_child=focus,
            integrity=target_integrity,
            control_class=class_name,
            has_caret=caret,
            editability=editability,
            read_only=read_only,
            protected=protected,
            integrity_relation=integrity_relation,
            process_creation_id=process_creation_id,
            uia_runtime_id=tuple(getattr(uia, "runtime_id", ()) or ()),
            uia_observed=bool(getattr(uia, "observed", False)),
            uia_state=str(getattr(uia, "state", "unavailable") or
                          "unavailable"),
        )

    def restore(self, target, timeout_s):
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            try:
                self.user32.SetForegroundWindow(target.window)
                if target.focused_child:
                    own_tid = int(self.kernel32.GetCurrentThreadId())
                    attached = bool(self.user32.AttachThreadInput(
                        own_tid, target.thread_id, True)) if target.thread_id else False
                    try:
                        self.user32.SetFocus(target.focused_child)
                    finally:
                        if attached:
                            self.user32.AttachThreadInput(own_tid, target.thread_id, False)
            except Exception:
                pass
            if target.same_destination(self.current()):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)

    def can_inject(self, target):
        target_level = _integrity_rank(target.integrity)
        own_level = _integrity_rank(self._own_integrity)
        if target_level is None or own_level is None:
            return None
        return own_level >= target_level

    def _process_creation_identity(self, pid):
        process = self.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not process:
            return 0
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        try:
            if not self.kernel32.GetProcessTimes(
                    process, ctypes.byref(creation), ctypes.byref(exit_time),
                    ctypes.byref(kernel), ctypes.byref(user)):
                return 0
            return ((int(creation.dwHighDateTime) << 32)
                    | int(creation.dwLowDateTime))
        except Exception:
            return 0
        finally:
            self.kernel32.CloseHandle(process)

    def _process_integrity(self, pid):
        process = self.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not process:
            return None
        token = wintypes.HANDLE()
        try:
            if not self.advapi32.OpenProcessToken(
                    process, TOKEN_QUERY, ctypes.byref(token)):
                return None
            needed = wintypes.DWORD()
            self.advapi32.GetTokenInformation(
                token, TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(needed))
            if not needed.value:
                return None
            buffer = ctypes.create_string_buffer(needed.value)
            if not self.advapi32.GetTokenInformation(
                    token, TOKEN_INTEGRITY_LEVEL, buffer, needed,
                    ctypes.byref(needed)):
                return None
            sid_ptr = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            count_ptr = self.advapi32.GetSidSubAuthorityCount(sid_ptr)
            if not count_ptr:
                return None
            count = count_ptr[0]
            rid_ptr = self.advapi32.GetSidSubAuthority(sid_ptr, count - 1)
            if not rid_ptr:
                return None
            rid = rid_ptr[0]
            if rid < 0x2000:
                return "low"
            if rid < 0x3000:
                return "medium"
            if rid < 0x4000:
                return "high"
            return "system"
        except Exception:
            return None
        finally:
            if token:
                self.kernel32.CloseHandle(token)
            self.kernel32.CloseHandle(process)


def _integrity_rank(value):
    return {"low": 1, "medium": 2, "high": 3, "system": 4}.get(value)


class WindowsClipboardAdapter:
    """Clone HGLOBAL clipboard formats and restore them only while still owned."""

    _non_hglobal = {CF_BITMAP, CF_METAFILEPICT, CF_PALETTE, CF_ENHMETAFILE}

    _safe_registered_names = {
        "Rich Text Format",
        "HTML Format",
        "CanIncludeInClipboardHistory",
        "CanUploadToCloudClipboard",
    }

    def __init__(self, user32=None, kernel32=None, image_loader=None, *,
                 max_formats=64, max_total_bytes=32 * 1024 * 1024,
                 max_format_bytes=16 * 1024 * 1024,
                 acquisition_timeout_s=0.30, clock=time.monotonic,
                 sleeper=time.sleep):
        self.user32 = user32 or ctypes.windll.user32
        self.kernel32 = kernel32 or ctypes.windll.kernel32
        self._image_loader = image_loader
        self.max_formats = max(1, int(max_formats))
        self.max_total_bytes = max(1, int(max_total_bytes))
        self.max_format_bytes = max(1, int(max_format_bytes))
        self._acquisition_timeout_s = max(
            0.0, float(acquisition_timeout_s))
        self._clock = clock
        self._sleep = sleeper
        if user32 is None and kernel32 is None:
            self._configure_ctypes()

    def _configure_ctypes(self):
        self.user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
        self.user32.EnumClipboardFormats.restype = wintypes.UINT
        self.user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
        self.user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
        self.user32.RegisterClipboardFormatW.restype = wintypes.UINT
        self.user32.GetClipboardData.argtypes = [wintypes.UINT]
        self.user32.GetClipboardData.restype = wintypes.HANDLE
        self.user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        self.user32.SetClipboardData.restype = wintypes.HANDLE
        self.kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        self.kernel32.GlobalAlloc.restype = wintypes.HANDLE
        self.kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
        self.kernel32.GlobalLock.restype = ctypes.c_void_p
        self.kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
        self.kernel32.GlobalUnlock.restype = wintypes.BOOL
        self.kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
        self.kernel32.GlobalFree.restype = wintypes.HANDLE
        self.kernel32.GlobalSize.argtypes = [wintypes.HANDLE]
        self.kernel32.GlobalSize.restype = ctypes.c_size_t

    def snapshot(self):
        clock = getattr(self, "_clock", time.monotonic)
        timeout_s = getattr(self, "_acquisition_timeout_s", 0.30)
        deadline = clock() + timeout_s
        with self._opened(deadline=deadline):
            sequence = self._sequence()
            format_ids = self._enumerate_format_ids_open()
            if len(format_ids) > self.max_formats:
                return ClipboardSnapshot(
                    sequence=sequence,
                    formats=(),
                    restorable=False,
                    reason="clipboard_format_count_over_budget",
                )
            formats = []
            total_bytes = 0
            for format_id in format_ids:
                name = self._format_name(format_id)
                if format_id in self._non_hglobal:
                    if format_id in {CF_BITMAP, CF_PALETTE} and (
                            CF_DIB in format_ids or CF_DIBV5 in format_ids):
                        continue
                    return ClipboardSnapshot(
                        sequence=sequence, formats=tuple(formats),
                        restorable=False,
                        reason="unsupported_non_hglobal_format")
                if not self._format_supported(format_id, name):
                    return ClipboardSnapshot(
                        sequence=sequence, formats=tuple(formats),
                        restorable=False,
                        reason="unsupported_private_format")
                data = self._read_format_bytes_open(format_id)
                if data is None:
                    return ClipboardSnapshot(
                        sequence=sequence, formats=tuple(formats),
                        restorable=False,
                        reason="delayed_or_unreadable_format")
                if len(data) > self.max_format_bytes:
                    return ClipboardSnapshot(
                        sequence=sequence, formats=tuple(formats),
                        restorable=False,
                        reason="clipboard_format_over_budget")
                total_bytes += len(data)
                if total_bytes > self.max_total_bytes:
                    return ClipboardSnapshot(
                        sequence=sequence, formats=tuple(formats),
                        restorable=False,
                        reason="clipboard_total_over_budget")
                formats.append((format_id, name, data))
            if self._sequence() != sequence:
                return ClipboardSnapshot(
                    sequence=sequence, formats=tuple(formats),
                    restorable=False,
                    reason="clipboard_changed_during_snapshot")
            return ClipboardSnapshot(
                sequence=sequence, formats=tuple(formats))

    def write(self, request, snapshot):
        if request.content_kind in {"text", "rich"}:
            format_id = CF_UNICODETEXT
            data = request.text.encode("utf-16-le") + b"\x00\x00"
            history_id, cloud_id = self._privacy_formats()
            payloads = [
                (format_id, data),
                (history_id, struct.pack("<I", 0)),
                (cloud_id, struct.pack("<I", 0)),
            ]
            if request.content_kind == "rich" and request.rich_html:
                payloads.append((
                    self._registered_format("HTML Format"),
                    self._html_clipboard_bytes(request.rich_html),
                ))
            if request.content_kind == "rich" and request.rich_rtf:
                payloads.append((
                    self._registered_format("Rich Text Format"),
                    request.rich_rtf.encode("utf-8") + b"\x00",
                ))
            payloads = tuple(payloads)
        elif request.content_kind == "image":
            format_id = CF_DIB
            data = self._image_dib(request.image_path)
            payloads = ((format_id, data),)
        else:
            raise ValueError("unsupported insertion content kind: {}".format(
                request.content_kind))
        try:
            sequence = self._replace_formats(payloads)
        except _PartialClipboardReplacement as exc:
            raise self._recover_failed_write(snapshot, exc) from exc
        for payload_id, payload_data in payloads:
            if self._read_format_bytes(payload_id) != payload_data:
                observed = tuple(
                    (observed_id, observed_data)
                    for observed_id, _expected in payloads
                    for observed_data in (self._read_format_bytes(observed_id),)
                    if observed_data is not None)
                failure = _PartialClipboardReplacement(
                    "clipboard_readback_mismatch", sequence, observed,
                    ownership_verifiable=bool(observed))
                raise self._recover_failed_write(snapshot, failure)
        return ClipboardOwnership(
            sequence, hashlib.sha256(data).hexdigest(), format_id)

    def _recover_failed_write(self, snapshot, failure):
        """Restore only when the failed partial replacement is still ours."""
        try:
            self._replace_formats(
                ((format_id, data)
                 for format_id, _name, data in snapshot.formats),
                before_clear=lambda: self._failed_write_still_owned(failure),
            )
        except _ClipboardOwnershipChanged:
            return ClipboardWriteFailure(
                str(failure), clipboard_changed_externally=True,
                cleanup_warning=(
                    "The clipboard changed during Mumble's failed write, so the "
                    "newer clipboard was left untouched."),
            )
        except _ClipboardOwnershipUnverifiable:
            return ClipboardWriteFailure(
                str(failure),
                cleanup_warning=(
                    "Mumble could not verify clipboard ownership after its "
                    "failed write, so it did not clear the clipboard again."),
            )
        except Exception:
            restored = False
        else:
            restored = all(
                self._read_format_bytes(format_id) == data
                for format_id, _name, data in snapshot.formats)
        return ClipboardWriteFailure(
            str(failure), clipboard_restored=restored,
            cleanup_warning=("" if restored else
                "The previous clipboard could not be restored after Mumble's "
                "write failed."),
        )

    def _failed_write_still_owned(self, failure):
        """Called while the clipboard is open, before any recovery clear."""
        if self._sequence() != failure.sequence:
            return False
        if not failure.ownership_verifiable:
            raise _ClipboardOwnershipUnverifiable(
                "clipboard ownership could not be verified")
        for format_id, data in failure.written:
            current = self._read_format_bytes_open(format_id)
            if current is None:
                raise _ClipboardOwnershipUnverifiable(
                    "clipboard payload could not be read")
            if current != data:
                return False
        return True

    def still_owns(self, ownership):
        with self._opened():
            return self._still_owns_open(ownership)

    def _still_owns_open(self, ownership):
        if self._sequence() != ownership.sequence:
            return False
        data = self._read_format_bytes_open(ownership.format_id)
        return bool(data is not None and
                    hashlib.sha256(data).hexdigest() == ownership.fingerprint)

    def _restore_still_owned_open(self, ownership):
        if self._sequence() != ownership.sequence:
            return False
        data = self._read_format_bytes_open(ownership.format_id)
        if data is None:
            raise _ClipboardOwnershipUnverifiable(
                "clipboard payload could not be read")
        return hashlib.sha256(data).hexdigest() == ownership.fingerprint

    def restore(self, snapshot, ownership=None):
        try:
            self._replace_formats(
                ((format_id, data)
                 for format_id, _name, data in snapshot.formats),
                before_clear=(None if ownership is None else
                              lambda: self._restore_still_owned_open(ownership)),
            )
        except _ClipboardOwnershipChanged:
            return ClipboardRestoreResult(
                ClipboardRestoreState.NEWER_EXTERNAL)
        except _ClipboardOwnershipUnverifiable:
            return ClipboardRestoreResult(ClipboardRestoreState.FAILED)
        except Exception:
            return ClipboardRestoreResult(ClipboardRestoreState.FAILED)
        try:
            restored = all(
                self._read_format_bytes(format_id) == data
                for format_id, _name, data in snapshot.formats)
        except Exception:
            restored = False
        return ClipboardRestoreResult(
            ClipboardRestoreState.RESTORED if restored else
            ClipboardRestoreState.FAILED)

    def _format_supported(self, format_id, name):
        if 1 <= format_id < 0x0200:
            return True
        if 0x0200 <= format_id <= 0x02FF:
            return False
        if format_id >= 0xC000:
            return name in self._safe_registered_names
        return False

    def _sequence(self):
        return int(self.user32.GetClipboardSequenceNumber())

    def _enumerate_format_ids(self):
        with self._opened():
            return self._enumerate_format_ids_open()

    def _enumerate_format_ids_open(self):
        values = []
        current = 0
        while True:
            current = int(self.user32.EnumClipboardFormats(current) or 0)
            if not current:
                break
            values.append(current)
        return values

    def _read_format_bytes(self, format_id):
        with self._opened():
            return self._read_format_bytes_open(format_id)

    def _read_format_bytes_open(self, format_id):
        handle = self.user32.GetClipboardData(format_id)
        size = int(self.kernel32.GlobalSize(handle) or 0) if handle else 0
        if not handle or not size:
            return None
        pointer = self.kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.string_at(pointer, size)
        finally:
            self.kernel32.GlobalUnlock(handle)

    def _replace_formats(self, formats, *, before_clear=None):
        payloads = tuple(formats)
        written = []
        cleared = False
        try:
            with self._opened():
                if before_clear is not None and not before_clear():
                    raise _ClipboardOwnershipChanged(
                        "clipboard ownership changed before recovery")
                if not self.user32.EmptyClipboard():
                    raise RuntimeError("clipboard_clear_failed")
                cleared = True
                for format_id, data in payloads:
                    self._set_format(format_id, data)
                    # SetClipboardData owns the successful HGLOBAL from here.
                    written.append((format_id, data))
        except Exception as exc:
            # Opening or clearing failed before Mumble changed clipboard data.
            # Recovery here could overwrite an external owner unnecessarily.
            if not cleared:
                raise
            raise _PartialClipboardReplacement(
                str(exc), self._sequence(), written) from exc
        return self._sequence()

    def _privacy_formats(self):
        history_id = int(self.user32.RegisterClipboardFormatW(
            "CanIncludeInClipboardHistory") or 0)
        cloud_id = int(self.user32.RegisterClipboardFormatW(
            "CanUploadToCloudClipboard") or 0)
        if not history_id or not cloud_id:
            raise RuntimeError("clipboard_privacy_format_registration_failed")
        return history_id, cloud_id

    def _registered_format(self, name):
        format_id = int(self.user32.RegisterClipboardFormatW(name) or 0)
        if not format_id:
            raise RuntimeError("clipboard_format_registration_failed")
        return format_id

    @staticmethod
    def _html_clipboard_bytes(fragment):
        fragment_bytes = str(fragment).encode("utf-8")
        prefix = b"<html><body><!--StartFragment-->"
        suffix = b"<!--EndFragment--></body></html>"
        header_template = (
            "Version:1.0\r\n"
            "StartHTML:{:010d}\r\n"
            "EndHTML:{:010d}\r\n"
            "StartFragment:{:010d}\r\n"
            "EndFragment:{:010d}\r\n"
        )
        placeholder = header_template.format(0, 0, 0, 0).encode("ascii")
        start_html = len(placeholder)
        start_fragment = start_html + len(prefix)
        end_fragment = start_fragment + len(fragment_bytes)
        end_html = end_fragment + len(suffix)
        header = header_template.format(
            start_html, end_html, start_fragment, end_fragment,
        ).encode("ascii")
        return header + prefix + fragment_bytes + suffix + b"\x00"

    def _set_format(self, format_id, data):
        handle = self.kernel32.GlobalAlloc(GMEM_MOVEABLE, max(1, len(data)))
        if not handle:
            raise MemoryError("Windows could not allocate clipboard memory")
        pointer = self.kernel32.GlobalLock(handle)
        if not pointer:
            self.kernel32.GlobalFree(handle)
            raise RuntimeError("Windows could not lock clipboard memory")
        try:
            if data:
                ctypes.memmove(pointer, data, len(data))
        finally:
            self.kernel32.GlobalUnlock(handle)
        if not self.user32.SetClipboardData(format_id, handle):
            self.kernel32.GlobalFree(handle)
            raise RuntimeError("Windows rejected clipboard format {}".format(format_id))

    def _format_name(self, format_id):
        standard = {
            CF_BITMAP: "CF_BITMAP", CF_METAFILEPICT: "CF_METAFILEPICT",
            CF_DIB: "CF_DIB", CF_PALETTE: "CF_PALETTE",
            CF_UNICODETEXT: "CF_UNICODETEXT", CF_ENHMETAFILE: "CF_ENHMETAFILE",
            CF_HDROP: "CF_HDROP", CF_DIBV5: "CF_DIBV5",
        }
        if format_id in standard:
            return standard[format_id]
        buf = ctypes.create_unicode_buffer(128)
        if self.user32.GetClipboardFormatNameW(format_id, buf, len(buf)):
            return buf.value
        return "format-{}".format(format_id)

    def _image_dib(self, path):
        if not path or not os.path.isfile(path):
            raise FileNotFoundError("image is no longer available")
        if self._image_loader:
            return self._image_loader(path)
        from PIL import Image
        with Image.open(path) as image:
            output = io.BytesIO()
            image.convert("RGB").save(output, "BMP")
        return output.getvalue()[14:]

    class _OpenClipboard:
        def __init__(self, owner, deadline=None):
            self.owner = owner
            self.deadline = deadline

        def __enter__(self):
            clock = getattr(self.owner, "_clock", time.monotonic)
            timeout_s = getattr(
                self.owner, "_acquisition_timeout_s", 0.30)
            sleeper = getattr(self.owner, "_sleep", time.sleep)
            deadline = self.deadline
            if deadline is None:
                deadline = clock() + timeout_s
            while not self.owner.user32.OpenClipboard(None):
                if clock() >= deadline:
                    raise RuntimeError("Windows clipboard stayed busy")
                sleeper(0.01)
            return self

        def __exit__(self, exc_type, exc, tb):
            self.owner.user32.CloseClipboard()

    def _opened(self, deadline=None):
        return self._OpenClipboard(self, deadline)


class WindowsNativeInputAdapter:
    _physical_modifiers = {
        0xA2: "left Ctrl", 0xA3: "right Ctrl",
        0xA4: "left Alt", 0xA5: "right Alt",
        0xA0: "left Shift", 0xA1: "right Shift",
        0x5B: "left Windows", 0x5C: "right Windows",
    }

    def __init__(self, user32=None):
        self.user32 = user32 or ctypes.windll.user32
        if user32 is None:
            self.user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
            self.user32.GetAsyncKeyState.restype = wintypes.SHORT
            self.user32.SendInput.argtypes = [
                wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
            self.user32.SendInput.restype = wintypes.UINT

    def ready(self, timeout_s):
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            held = [name for vk, name in self._physical_modifiers.items()
                    if self.user32.GetAsyncKeyState(vk) & 0x8000]
            if not held:
                return True, ""
            if time.monotonic() >= deadline:
                return False, "{} is still physically held".format(held[0])
            time.sleep(0.01)

    def _send_control_chord(self, virtual_key):
        events = (_INPUT * 4)(
            _keyboard_input(VK_CONTROL),
            _keyboard_input(virtual_key),
            _keyboard_input(virtual_key, key_up=True),
            _keyboard_input(VK_CONTROL, key_up=True),
        )
        accepted = int(self.user32.SendInput(
            len(events), events, ctypes.sizeof(_INPUT)) or 0)
        return NativeAcceptance(
            requested=len(events),
            accepted=accepted,
            submitted=accepted > 0,
            confirmation=None,
            error="" if accepted == len(events) else
                "Windows accepted {} of {} native input events".format(
                    accepted, len(events)),
        )

    def send_paste(self):
        return self._send_control_chord(VK_V)

    def send_undo(self):
        return self._send_control_chord(VK_Z)

    def send_unicode(self, text):
        units = str(text or "").encode("utf-16-le")
        code_units = [
            int.from_bytes(units[index:index + 2], "little")
            for index in range(0, len(units), 2)
        ]
        if not code_units:
            return NativeAcceptance(
                requested=0, accepted=0, submitted=False,
                confirmation=None, error="empty_text")
        events = []
        for code_unit in code_units:
            events.extend((
                _unicode_input(code_unit),
                _unicode_input(code_unit, key_up=True),
            ))
        inputs = (_INPUT * len(events))(*events)
        accepted = int(self.user32.SendInput(
            len(events), inputs, ctypes.sizeof(_INPUT)) or 0)
        return NativeAcceptance(
            requested=len(events), accepted=accepted,
            submitted=accepted > 0, confirmation=None,
            error="" if accepted == len(events) else
                "Windows accepted {} of {} Unicode input events".format(
                    accepted, len(events)),
        )
