"""Windows adapters for Mumble's target-bound insertion transaction."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import io
import os
import time

from insertion import (
    ClipboardOwnership,
    ClipboardSnapshot,
    NativeAcceptance,
    TargetContext,
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
INPUT_KEYBOARD = 1
VK_CONTROL = 0x11
VK_V = 0x56


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
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT)]


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
            dwExtraInfo=None,
        )),
    )


class WindowsTargetAdapter:
    def __init__(self, user32=None, kernel32=None, advapi32=None):
        self.user32 = user32 or ctypes.windll.user32
        self.kernel32 = kernel32 or ctypes.windll.kernel32
        self.advapi32 = advapi32 or ctypes.windll.advapi32
        if user32 is None and kernel32 is None and advapi32 is None:
            self._configure_ctypes()
        self._own_integrity = self._process_integrity(os.getpid())

    def _configure_ctypes(self):
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.kernel32.OpenProcess.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
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
        if focus:
            buf = ctypes.create_unicode_buffer(128)
            if self.user32.GetClassNameW(focus, buf, len(buf)):
                class_name = buf.value
        return TargetContext(
            window=hwnd,
            process_id=int(pid.value),
            thread_id=tid,
            focused_child=focus,
            integrity=self._process_integrity(int(pid.value)) or "unknown",
            control_class=class_name,
            has_caret=caret,
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

    def __init__(self, user32=None, kernel32=None, image_loader=None):
        self.user32 = user32 or ctypes.windll.user32
        self.kernel32 = kernel32 or ctypes.windll.kernel32
        self._image_loader = image_loader
        if user32 is None and kernel32 is None:
            self._configure_ctypes()

    def _configure_ctypes(self):
        self.user32.GetClipboardData.argtypes = [wintypes.UINT]
        self.user32.GetClipboardData.restype = wintypes.HANDLE
        self.user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        self.user32.SetClipboardData.restype = wintypes.HANDLE
        self.kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        self.kernel32.GlobalAlloc.restype = wintypes.HANDLE
        self.kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
        self.kernel32.GlobalLock.restype = ctypes.c_void_p
        self.kernel32.GlobalSize.argtypes = [wintypes.HANDLE]
        self.kernel32.GlobalSize.restype = ctypes.c_size_t

    def snapshot(self):
        with self._opened():
            sequence = int(self.user32.GetClipboardSequenceNumber())
            format_ids = []
            current = 0
            while True:
                current = int(self.user32.EnumClipboardFormats(current) or 0)
                if not current:
                    break
                format_ids.append(current)

            formats = []
            unsafe = []
            for format_id in format_ids:
                if format_id in self._non_hglobal:
                    if format_id == CF_BITMAP and (
                            CF_DIB in format_ids or CF_DIBV5 in format_ids):
                        continue
                    if format_id == CF_PALETTE and (
                            CF_DIB in format_ids or CF_DIBV5 in format_ids):
                        continue
                    unsafe.append(self._format_name(format_id))
                    continue
                handle = self.user32.GetClipboardData(format_id)
                size = int(self.kernel32.GlobalSize(handle) or 0) if handle else 0
                if not handle or not size:
                    unsafe.append(self._format_name(format_id))
                    continue
                pointer = self.kernel32.GlobalLock(handle)
                if not pointer:
                    unsafe.append(self._format_name(format_id))
                    continue
                try:
                    data = ctypes.string_at(pointer, size)
                finally:
                    self.kernel32.GlobalUnlock(handle)
                formats.append((format_id, self._format_name(format_id), data))
        if unsafe:
            return ClipboardSnapshot(
                sequence=sequence,
                formats=tuple(formats),
                restorable=False,
                reason="Clipboard contains a format Mumble cannot safely restore: {}".format(
                    ", ".join(unsafe)),
            )
        return ClipboardSnapshot(sequence=sequence, formats=tuple(formats))

    def write(self, request, snapshot):
        if request.content_kind == "text":
            format_id = CF_UNICODETEXT
            data = request.text.encode("utf-16-le") + b"\x00\x00"
        elif request.content_kind == "image":
            format_id = CF_DIB
            data = self._image_dib(request.image_path)
        else:
            raise ValueError("unsupported insertion content kind: {}".format(
                request.content_kind))
        with self._opened():
            if not self.user32.EmptyClipboard():
                raise RuntimeError("Windows could not clear the clipboard")
            try:
                self._set_format(format_id, data)
            except Exception:
                # EmptyClipboard has already changed user state. Restore the
                # pre-transaction formats while this process still owns the
                # open clipboard, then report the write failure to policy.
                self.user32.EmptyClipboard()
                for old_id, _name, old_data in snapshot.formats:
                    self._set_format(old_id, old_data)
                raise
        sequence = int(self.user32.GetClipboardSequenceNumber())
        return ClipboardOwnership(sequence, hashlib.sha256(data).hexdigest())

    def still_owns(self, ownership):
        return int(self.user32.GetClipboardSequenceNumber()) == ownership.sequence

    def restore(self, snapshot, ownership=None):
        with self._opened():
            if ownership is not None:
                current = int(self.user32.GetClipboardSequenceNumber())
                if current != ownership.sequence:
                    return False
            if not self.user32.EmptyClipboard():
                return False
            for format_id, _name, data in snapshot.formats:
                self._set_format(format_id, data)
        return True

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
        def __init__(self, owner):
            self.owner = owner

        def __enter__(self):
            deadline = time.monotonic() + 0.30
            while not self.owner.user32.OpenClipboard(None):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Windows clipboard stayed busy")
                time.sleep(0.01)
            return self

        def __exit__(self, exc_type, exc, tb):
            self.owner.user32.CloseClipboard()

    def _opened(self):
        return self._OpenClipboard(self)


class WindowsNativeInputAdapter:
    _physical_modifiers = {
        0xA2: "left Ctrl", 0xA3: "right Ctrl",
        0xA4: "left Alt", 0xA5: "right Alt",
        0xA0: "left Shift", 0xA1: "right Shift",
        0x5B: "left Windows", 0x5C: "right Windows",
    }

    def __init__(self, user32=None):
        self.user32 = user32 or ctypes.windll.user32

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

    def send_paste(self):
        events = (_INPUT * 4)(
            _keyboard_input(VK_CONTROL),
            _keyboard_input(VK_V),
            _keyboard_input(VK_V, key_up=True),
            _keyboard_input(VK_CONTROL, key_up=True),
        )
        accepted = int(self.user32.SendInput(
            len(events), ctypes.byref(events), ctypes.sizeof(_INPUT)) or 0)
        return NativeAcceptance(
            requested=len(events),
            accepted=accepted,
            submitted=accepted > 0,
            confirmation=None,
            error="" if accepted == len(events) else
                "Windows accepted {} of {} native input events".format(
                    accepted, len(events)),
        )
