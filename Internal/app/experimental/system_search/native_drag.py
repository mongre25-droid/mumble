"""Trusted native Windows shell drag source for Mumble Find results."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path


def start_windows_shell_drag(path):
    """Start one blocking OLE drag using a controller-resolved local path."""
    if os.name != "nt":
        raise RuntimeError("Native file dragging is available on Windows.")
    target = Path(path).expanduser().resolve(strict=True)
    if not (target.is_file() or target.is_dir()):
        raise FileNotFoundError("That item has moved. Search again.")

    import pythoncom
    import winerror
    import win32con
    from win32com.server.util import wrap
    from win32com.shell import shell, shellcon

    class DropSource:
        _com_interfaces_ = [pythoncom.IID_IDropSource]
        _public_methods_ = ["QueryContinueDrag", "GiveFeedback"]

        def QueryContinueDrag(self, escape_pressed, key_state):
            if escape_pressed:
                return winerror.DRAGDROP_S_CANCEL
            if not int(key_state) & win32con.MK_LBUTTON:
                return winerror.DRAGDROP_S_DROP
            return winerror.S_OK

        def GiveFeedback(self, _effect):
            return winerror.DRAGDROP_S_USEDEFAULTCURSORS

    ole32 = ctypes.windll.ole32
    ole32.OleInitialize.argtypes = [ctypes.c_void_p]
    ole32.OleInitialize.restype = ctypes.c_long
    ole32.OleUninitialize.argtypes = []
    ole32.OleUninitialize.restype = None
    initialized = ole32.OleInitialize(None)
    if initialized not in (0, 1):
        raise RuntimeError("Windows could not start native file dragging.")
    try:
        desktop = shell.SHGetDesktopFolder()
        _eaten, pidl, _attributes = desktop.ParseDisplayName(
            0, None, str(target),
        )
        folder = desktop
        while len(pidl) > 1:
            folder = folder.BindToObject(
                [pidl.pop(0)], None, shell.IID_IShellFolder,
            )
        data_result = folder.GetUIObjectOf(
            0, [pidl], pythoncom.IID_IDataObject, 0,
        )
        data_object = (
            data_result[1]
            if isinstance(data_result, tuple) else data_result
        )
        allowed = (
            shellcon.DROPEFFECT_COPY
            | shellcon.DROPEFFECT_MOVE
            | shellcon.DROPEFFECT_LINK
        )
        effect = pythoncom.DoDragDrop(
            data_object,
            wrap(DropSource(), pythoncom.IID_IDropSource),
            allowed,
        )
        names = {
            shellcon.DROPEFFECT_COPY: "copy",
            shellcon.DROPEFFECT_MOVE: "move",
            shellcon.DROPEFFECT_LINK: "link",
        }
        return {
            "ok": True,
            "dropped": bool(effect),
            "effect": names.get(int(effect), "none"),
        }
    finally:
        ole32.OleUninitialize()
