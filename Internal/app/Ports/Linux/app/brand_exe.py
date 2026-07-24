#!/usr/bin/env python3
"""Make .venv\\Scripts\\Mumble.exe a REAL branded executable.

Two jobs, both pure ctypes (no build pipeline, no extra dependencies):

1. The exe itself: copy the BASE interpreter's pythonw.exe into the venv —
   NOT the venv's own pythonw.exe. The venv copy is a tiny *launcher shim*
   that spawns the base interpreter as a child process, so every Mumble
   component used to show up TWICE in Task Manager: a branded "Mumble.exe"
   shim plus an unbranded "pythonw.exe" doing the actual work. The base
   interpreter placed next to pyvenv.cfg configures itself for the venv
   directly — one process per component, every one of them named Mumble.

2. The metadata: rewrite the copied exe's Windows resources in place
   (BeginUpdateResource / UpdateResource) so Explorer, Task Manager and the
   Startup-apps list show Mumble's name, version and gold icon instead of
   "Python" — the last place the old branding survived.

Run by install.ps1 after the venv exists; safe to re-run (idempotent).
"""

import ctypes
import ctypes.wintypes as wt
import os
import struct
import sys

import branding

SCRIPTS_DIR = os.path.join(branding.INSTALL_DIR, ".venv", "Scripts")
MUMBLE_EXE = os.path.join(SCRIPTS_DIR, "Mumble.exe")

RT_ICON = 3
RT_GROUP_ICON = 14
RT_VERSION = 16
LANG_EN_US = 1033

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
# Integer resource IDs are MAKEINTRESOURCE pseudo-pointers — they MUST travel
# through a pointer-width type. ctypes' default int marshalling truncates to
# 32-bit c_int, which UpdateResourceW rejects on x64.
_k32.BeginUpdateResourceW.argtypes = (wt.LPCWSTR, wt.BOOL)
_k32.BeginUpdateResourceW.restype = wt.HANDLE
_k32.UpdateResourceW.argtypes = (wt.HANDLE, wt.LPVOID, wt.LPVOID, wt.WORD,
                                 wt.LPVOID, wt.DWORD)
_k32.UpdateResourceW.restype = wt.BOOL
_k32.EndUpdateResourceW.argtypes = (wt.HANDLE, wt.BOOL)
_k32.EndUpdateResourceW.restype = wt.BOOL
_k32.LoadLibraryExW.argtypes = (wt.LPCWSTR, wt.HANDLE, wt.DWORD)
_k32.LoadLibraryExW.restype = wt.HMODULE
_k32.FreeLibrary.argtypes = (wt.HMODULE,)


def _res_id(value):
    """An int ID or string name as the LPVOID UpdateResource/Enum* expect."""
    if isinstance(value, int):
        return ctypes.c_void_p(value)
    return ctypes.cast(ctypes.c_wchar_p(value), ctypes.c_void_p)


# --------------------------------------------------------------- exe creation
def base_pythonw():
    """The real (non-shim) pythonw.exe of the interpreter behind this venv."""
    return os.path.join(sys.base_prefix, "pythonw.exe")


def make_exe(dest=MUMBLE_EXE):
    """Copy the base interpreter to `dest`. Returns False if dest is locked
    (Mumble running) or the source is missing — caller keeps the old exe."""
    src = base_pythonw()
    if not os.path.exists(src):
        print(f"brand_exe: base interpreter not found at {src}")
        return False
    try:
        import shutil

        shutil.copyfile(src, dest)
        return True
    except OSError as e:
        print(f"brand_exe: could not write {dest}: {e}")
        return False


# ------------------------------------------------------- VS_VERSIONINFO blob
def _pad4(b):
    return b + b"\x00" * ((4 - len(b) % 4) % 4)


def _vs_block(key, wtype, value, children=b""):
    """One VERSIONINFO block: wLength wValueLength wType szKey pad value pad children.
    String values count wValueLength in WORDS incl. the terminator; binary in bytes."""
    key_b = key.encode("utf-16-le") + b"\x00\x00"
    if wtype == 1:  # text value
        val_b = value.encode("utf-16-le") + b"\x00\x00" if value else b""
        vlen = len(val_b) // 2
    else:  # binary value
        val_b = value
        vlen = len(val_b)
    body = _pad4(struct.pack("<HHH", 0, vlen, wtype) + key_b) + _pad4(val_b) + children
    return struct.pack("<H", len(body)) + body[2:]


def build_version_info(version):
    """A complete VS_VERSIONINFO resource for Mumble."""
    parts = [int(p) for p in (version.split(".") + ["0", "0", "0"])[:4]]
    ms, ls = (parts[0] << 16) | parts[1], (parts[2] << 16) | parts[3]
    fixed = struct.pack(
        "<LLLLLLLLLLLLL",
        0xFEEF04BD, 0x00010000,  # signature, struc version
        ms, ls, ms, ls,          # file + product version
        0x3F, 0,                 # flags mask, flags
        0x40004, 1, 0,           # VOS_NT_WINDOWS32, VFT_APP, subtype
        0, 0,                    # date
    )
    strings = b"".join(
        _vs_block(k, 1, v)
        for k, v in (
            ("CompanyName", branding.APP_NAME),
            ("FileDescription", f"{branding.APP_NAME} — {branding.APP_TAGLINE}"),
            ("FileVersion", version),
            ("InternalName", branding.APP_NAME),
            ("LegalCopyright", f"© {branding.APP_NAME}"),
            ("OriginalFilename", "Mumble.exe"),
            ("ProductName", branding.APP_NAME),
            ("ProductVersion", version),
        )
    )
    sfi = _vs_block("StringFileInfo", 1, None,
                    _vs_block("040904B0", 1, None, strings))
    vfi = _vs_block("VarFileInfo", 1, None,
                    _vs_block("Translation", 0, struct.pack("<HH", 0x0409, 0x04B0)))
    return _vs_block("VS_VERSION_INFO", 0, fixed, sfi + vfi)


# ------------------------------------------------------------- icon resources
def load_ico(path):
    """Parse a .ico file → (group_dir_bytes, [image_bytes, ...]). The group
    directory is the .ico header with each entry's file offset replaced by a
    resource ID (1-based), per the RT_GROUP_ICON format."""
    with open(path, "rb") as f:
        data = f.read()
    reserved, ico_type, count = struct.unpack_from("<HHH", data, 0)
    if ico_type != 1 or count == 0:
        raise ValueError(f"not an icon file: {path}")
    group = struct.pack("<HHH", reserved, ico_type, count)
    images = []
    for i in range(count):
        entry = data[6 + i * 16: 6 + (i + 1) * 16]
        w, h, colors, res, planes, bits, size, off = struct.unpack("<BBBBHHLL", entry)
        images.append(data[off: off + size])
        group += struct.pack("<BBBBHHLH", w, h, colors, res, planes, bits, size, i + 1)
    return group, images


# ------------------------------------------------- resource enumeration + swap
def _existing(path, res_type):
    """All (name, lang) pairs of `res_type` in the exe — so the patch replaces
    Python's resources instead of leaving stale ones beside ours."""
    LOAD_AS_DATA = 0x00000002 | 0x00000040  # DATAFILE | IMAGE_RESOURCE
    mod = _k32.LoadLibraryExW(path, None, LOAD_AS_DATA)
    if not mod:
        return []
    found = []
    NameProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMODULE, ctypes.c_void_p,
                                  ctypes.c_void_p, ctypes.c_void_p)
    LangProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMODULE, ctypes.c_void_p,
                                  ctypes.c_void_p, wt.WORD, ctypes.c_void_p)
    _k32.EnumResourceNamesW.argtypes = (wt.HMODULE, ctypes.c_void_p, NameProc,
                                        ctypes.c_void_p)
    _k32.EnumResourceLanguagesW.argtypes = (wt.HMODULE, ctypes.c_void_p,
                                            ctypes.c_void_p, LangProc,
                                            ctypes.c_void_p)

    def _id(v):
        v = v or 0
        return v if v < 0x10000 else ctypes.wstring_at(v)

    def on_lang(_m, _t, name, lang, _p):
        found.append((_id(name), lang))
        return True

    lang_cb = LangProc(on_lang)

    def on_name(m, t, name, _p):
        _k32.EnumResourceLanguagesW(m, t, name, lang_cb, None)
        return True

    _k32.EnumResourceNamesW(mod, _res_id(res_type), NameProc(on_name), None)
    _k32.FreeLibrary(mod)
    return found


def patch_resources(path, ico=branding.ICON_ICO, version=branding.VERSION):
    """Replace the exe's version info + icon with Mumble's. Returns bool."""
    old = {t: _existing(path, t) for t in (RT_VERSION, RT_GROUP_ICON, RT_ICON)}
    group, images = load_ico(ico)
    ver = build_version_info(version)

    h = _k32.BeginUpdateResourceW(path, False)
    if not h:
        print(f"brand_exe: BeginUpdateResource failed for {path}")
        return False

    def put(rtype, name, lang, data):
        buf = ctypes.create_string_buffer(data, len(data))
        if not _k32.UpdateResourceW(h, _res_id(rtype), _res_id(name), lang,
                                    ctypes.cast(buf, wt.LPVOID), len(data)):
            raise OSError(f"UpdateResource({rtype}, {name}) failed "
                          f"(error {ctypes.get_last_error()})")

    try:
        # out with Python's resources…
        for rtype, entries in old.items():
            for name, lang in entries:
                _k32.UpdateResourceW(h, _res_id(rtype), _res_id(name),
                                     lang, None, 0)
        # …in with Mumble's
        put(RT_VERSION, 1, LANG_EN_US, ver)
        for i, img in enumerate(images):
            put(RT_ICON, i + 1, LANG_EN_US, img)
        put(RT_GROUP_ICON, 1, LANG_EN_US, group)
    except OSError as e:
        print(f"brand_exe: {e}")
        _k32.EndUpdateResourceW(h, True)  # discard
        return False
    if not _k32.EndUpdateResourceW(h, False):
        print("brand_exe: EndUpdateResource failed")
        return False
    return True


def brand(dest=MUMBLE_EXE):
    """Full job: fresh base-interpreter copy + Mumble resources."""
    if not make_exe(dest):
        return False
    ok = patch_resources(dest)
    print(f"brand_exe: {dest} {'branded' if ok else 'created (metadata patch failed)'}")
    return ok


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else MUMBLE_EXE
    sys.exit(0 if brand(target) else 1)
