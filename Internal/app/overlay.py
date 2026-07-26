#!/usr/bin/env python3
"""Mumble's floating "island" — Golden Black edition.

A single, click-through, content-sized capsule centred near the bottom of the
CURRENT monitor, hidden while idle — laid out as status dot · live animation ·
readable state label (HTML-island parity, v3.0.0). States:
    listening    -> gold dot + waveform reacting to your voice + "Listening"
    transcribing -> amber dot + breathing dots + "Transcribing"
    building     -> mode-colour dot + pulsing dots + "Building · <Mode>"
                    (Deck jobs show the dual-colour split + "Context + <Mode>")
    done         -> a brief flash in the active mode's colour + "Done"
    hint         -> gold-pulsing "Ctrl + Alt + D" text INLINE in the pill — your
                    cue that the result is on the clipboard (the old secondary
                    mini pill is retired; suggest = the same, tinted violet)
A small hollow ring marks OFFLINE (the local builder). Placement re-centres on the
monitor that holds the foreground window, so it follows you across laptop / desktop /
ultrawide every time it appears.
"""

import math
import time
import tkinter as tk

import ui
from branding import FONT, FONT_SB, MODE_COLORS, MODE_LABELS, C

try:
    import ctypes
    from ctypes import wintypes

    _HAVE_CTYPES = True
except Exception:
    _HAVE_CTYPES = False

# The premium renderer: a pure-Pillow glass pill painted into a per-pixel-alpha
# LAYERED Win32 window (in-process — NO separate WebView2 window). If Pillow or
# numpy is missing, _HAVE_GLASS is False and the reliable tkinter-canvas pill
# (further down) renders instead, so the island can never go missing.
try:
    import numpy as _np
    import island_render

    _HAVE_GLASS = True
except Exception as _e:  # pragma: no cover - only on a stripped environment
    _HAVE_GLASS = False
    print("glass island renderer unavailable (tkinter pill will be used):", _e)


class _Skip(Exception):
    """Internal control-flow sentinel: skip the island render this tick."""


_TRANSPARENT = "#ff00ff"
_PILL_BG = "#0C0B09"
_PILL_RIM = "#3A3320"
_PILL_HI = "#5A5240"  # warm hairline highlight (top inner edge of the pill)
_ARMED_RING = "#F3EEE1"  # near-white ring shown while the mode key is held (armed)
_SUGGEST = "#A855F7"  # vivid purple — the "which mode?" clarification chip

# Canvas bounds — the visible pill inside is sized to its CONTENT each frame
# (dot · animation · state label · hint), matching the HTML island reference:
# a roomy capsule with a status dot, live waveform and a readable state label.
W, H = 380, 30
CONTROL_GAP = 5  # breathing room between status island and companion controls
BOTTOM_MARGIN = 28 + island_render.BAR_H + CONTROL_GAP
PILL_W = 96  # minimum pill width (fallback when a state has no text)
CORNER = H // 2  # full capsule shape (pill ends are perfect semicircles)
# Completion-flash duration. Owner 2026-06-29 (ITEM 18): the post-transcription
# flash "lingers too long" — dismiss faster, no idle time. Trimmed 44→30 ticks:
# 30 × 45ms ≈ 1.35s on-screen, and because the flash FADES across its whole life
# (render: fade = done_left/DONE_FRAMES) the solid-read portion is ~0.7s — long
# enough to register "Pasted! · Mode", short enough that the island gets out of the
# way. Every completion/feedback flash (paste, copy, convert done) uses this value.
DONE_FRAMES = 30
PAD_X = 13  # pill inner padding
SEG_GAP = 8  # gap between pill segments

# The secondary "Ctrl + Alt + D" reminder island — half height, popped above.
MINI_W, MINI_H = 108, 15
MINI_PILL_W = 102
MINI_GAP = 6
HINT_FRAMES = 90         # ~4s paste hint (owner v9: trimmed from ~7s)
HINT_DELAY = 9
# Clarification chips (Convert "to what?", missed-mode redo) are INTERACTIVE — the
# user can hold the mode key and re-speak while they're up. Owner 0.9: they must
# FADE in ~2s like every other transient chip (the old ~4.5s read as "stuck"). The
# countdown FREEZES while the mode key is held (see _tick), so holding to respeak
# never races the fade.
SUGGEST_FRAMES = 46      # ~2.1s at 22fps (visible ~1s, then dissolves over ~1s)
# A correction review is offered only after Mumble observes a concrete edit in
# the just-pasted target field. Six seconds is enough to act without leaving a
# stale control surface floating over the user's work.
CORRECTION_FRAMES = 132  # ~6s at 22fps
FADE_OUT_FRAMES = 22     # the chip dissolves over its final ~1s instead of snapping
# Refinement pass §1 (motion): the island FADES IN over its first few frames when
# it appears, instead of snapping into view — a quieter, more premium entrance.
APPEAR_FRAMES = 7        # ~0.3s fade-in on first appearance
APPEAR_FLOOR = 0.30      # starting opacity of the fade-in ramp

# One source of truth for states that must keep the floating island visible.
# Voice Search used to be styled and rendered below but was absent from the
# show/tick gates, so entering it immediately hid the island.
_ACTIVE_STATES = frozenset({
    "listening", "search", "transcribing", "building",
    "done", "hint", "error",
})


class _OptionButton(tk.Canvas):
    """A full-width rounded multiple-choice option, themed to match RoundCard/RoundButton.
    States: normal (surface2), hover (elevated), selected (gold)."""

    def __init__(self, parent, text, width, command, bg=C.surface):
        super().__init__(
            parent,
            width=width,
            height=30,
            bg=bg,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.text, self.command, self.w, self.h = text, command, width, 30
        self.selected = False
        self._draw(C.surface2)
        self.bind(
            "<Enter>", lambda e: self._draw(C.gold if self.selected else C.elevated)
        )
        self.bind(
            "<Leave>", lambda e: self._draw(C.gold if self.selected else C.surface2)
        )
        self.bind("<Button-1>", lambda e: self.command and self.command())

    def _draw(self, fill):
        self.delete("all")
        ui.round_rect(self, 1, 1, self.w - 1, self.h - 1, 8, fill=fill, outline="")
        fg = C.bg if self.selected else C.text
        self.create_text(
            12, self.h // 2, text=self.text, fill=fg, font=(FONT, 9), anchor="w"
        )

    def set_selected(self, sel):
        """Update the selected state and redraw the button."""
        self.selected = bool(sel)
        self._draw(C.gold if self.selected else C.surface2)


def _monitor_rect():
    """Work-area-ish rect (left, top, right, bottom) of the monitor holding the
    FOREGROUND window, in the same virtualised coords Tk uses. Falls back to the
    primary screen metrics. Shared by the glass pill + the canvas fallback so
    the island follows you across laptop / desktop / ultrawide."""
    if _HAVE_CTYPES:
        try:
            u = ctypes.windll.user32
            hmon = u.MonitorFromWindow(u.GetForegroundWindow(), 2)  # NEAREST

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD),
                            ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT),
                            ("dwFlags", wintypes.DWORD)]

            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if u.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                try:
                    shcore = ctypes.windll.shcore
                    dpi_x = wintypes.UINT()
                    dpi_y = wintypes.UINT()
                    # MDT_EFFECTIVE_DPI = 0 — the real DPI the user sees on this monitor
                    if shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0:
                        scale = dpi_x.value / 96.0
                    else:
                        scale = u.GetDpiForSystem() / 96.0
                except Exception:
                    scale = 1.0
                if scale <= 0:
                    scale = 1.0
                # Use the WORK AREA (rcWork), not the full monitor (rcMonitor):
                # rcWork excludes a permanent taskbar on ANY edge, so the island
                # sits cleanly ABOVE a bottom taskbar instead of behind it (owner:
                # "push the island above the taskbar bounds"). This also matches
                # this function's own "work-area-ish" docstring — it used to return
                # rcMonitor by mistake.
                r = mi.rcWork
                bottom = r.bottom
                # AUTO-HIDE taskbar: it reserves NO work area (rcWork == rcMonitor),
                # so lift the island by the hidden bar's height when one hides along
                # the bottom edge, else it would pop up over the pill.
                bottom -= _autohide_bottom_px(mi.rcMonitor)
                return (r.left / scale, r.top / scale,
                        r.right / scale, bottom / scale)
        except Exception:
            pass
    return None


def _autohide_bottom_px(rc_monitor):
    """Reserved px for a BOTTOM auto-hide taskbar on this monitor, else 0.

    Auto-hide taskbars don't shrink the work area, so without this the island
    would render where the bar slides up. Best-effort via SHAppBarMessage; any
    failure returns 0 (no clearance)."""
    try:
        ABM_GETSTATE = 0x4
        ABM_GETTASKBARPOS = 0x5
        ABS_AUTOHIDE = 0x1
        ABE_BOTTOM = 0x3

        class APPBARDATA(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                        ("uCallbackMessage", wintypes.UINT), ("uEdge", wintypes.UINT),
                        ("rc", wintypes.RECT), ("lParam", wintypes.LPARAM)]

        shell = ctypes.windll.shell32
        if not (shell.SHAppBarMessage(ABM_GETSTATE, None) & ABS_AUTOHIDE):
            return 0
        abd = APPBARDATA()
        abd.cbSize = ctypes.sizeof(APPBARDATA)
        shell.SHAppBarMessage(ABM_GETTASKBARPOS, ctypes.byref(abd))
        if abd.uEdge != ABE_BOTTOM:
            return 0
        # Only when the bar belongs to THIS monitor's bottom span.
        if abd.rc.bottom < rc_monitor.top or abd.rc.top > rc_monitor.bottom:
            return 0
        return max(0, abd.rc.bottom - abd.rc.top)
    except Exception:
        return 0


# Win32 structs + properly-typed function table for the layered window. Setting
# argtypes/restype is REQUIRED on 64-bit: handles (HWND/HDC/HBITMAP) and byref
# pointers exceed a 32-bit int, and ctypes' default assumption truncates them
# ("argument N: int too long to convert"). Built once, lazily.
_W32 = None


def _w32():
    global _W32
    if _W32 is not None or not _HAVE_CTYPES:
        return _W32
    import types as _types
    from ctypes import (POINTER, c_byte, c_int, c_void_p)

    HWND = c_void_p
    HDC = c_void_p
    HBITMAP = c_void_p
    HGDIOBJ = c_void_p
    HANDLE = c_void_p
    DWORD = wintypes.DWORD
    UINT = wintypes.UINT
    LONG = wintypes.LONG
    BOOL = wintypes.BOOL

    class POINT(ctypes.Structure):
        _fields_ = [("x", LONG), ("y", LONG)]

    class SIZE(ctypes.Structure):
        _fields_ = [("cx", LONG), ("cy", LONG)]

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [("BlendOp", c_byte), ("BlendFlags", c_byte),
                    ("SourceConstantAlpha", c_byte), ("AlphaFormat", c_byte)]

    class BMIH(ctypes.Structure):
        _fields_ = [("biSize", DWORD), ("biWidth", LONG), ("biHeight", LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                    ("biCompression", DWORD), ("biSizeImage", DWORD),
                    ("biXPelsPerMeter", LONG), ("biYPelsPerMeter", LONG),
                    ("biClrUsed", DWORD), ("biClrImportant", DWORD)]

    u = ctypes.windll.user32
    g = ctypes.windll.gdi32
    u.GetAncestor.argtypes = [HWND, UINT]; u.GetAncestor.restype = HWND
    u.GetWindowLongW.argtypes = [HWND, c_int]; u.GetWindowLongW.restype = LONG
    u.SetWindowLongW.argtypes = [HWND, c_int, LONG]; u.SetWindowLongW.restype = LONG
    u.GetDC.argtypes = [HWND]; u.GetDC.restype = HDC
    u.ReleaseDC.argtypes = [HWND, HDC]; u.ReleaseDC.restype = c_int
    g.CreateCompatibleDC.argtypes = [HDC]; g.CreateCompatibleDC.restype = HDC
    g.CreateDIBSection.argtypes = [HDC, c_void_p, UINT, POINTER(c_void_p),
                                   HANDLE, DWORD]
    g.CreateDIBSection.restype = HBITMAP
    g.SelectObject.argtypes = [HDC, HGDIOBJ]; g.SelectObject.restype = HGDIOBJ
    g.DeleteObject.argtypes = [HGDIOBJ]; g.DeleteObject.restype = BOOL
    g.DeleteDC.argtypes = [HDC]; g.DeleteDC.restype = BOOL
    u.UpdateLayeredWindow.argtypes = [HWND, HDC, POINTER(POINT), POINTER(SIZE),
                                      HDC, POINTER(POINT), DWORD,
                                      POINTER(BLENDFUNCTION), DWORD]
    u.UpdateLayeredWindow.restype = BOOL

    _W32 = _types.SimpleNamespace(u=u, g=g, POINT=POINT, SIZE=SIZE,
                                  BLEND=BLENDFUNCTION, BMIH=BMIH)
    return _W32


class _LayeredDC:
    """Shared GDI memory-DC + DIB-section management for per-pixel-alpha layered
    Win32 windows. Used by both _GlassPill (click-through island) and _WidgetBar
    (clickable companion bar), eliminating the duplicated _ensure_dib / push / _blit
    code that previously lived in each class (scrutiny finding island-redesign (d))."""

    def __init__(self):
        self.memdc = None
        self.dib = None
        self.bits = ctypes.c_void_p()
        self.dib_size = None

    def ensure_dib(self, w, h):
        if self.dib is not None and self.dib_size == (w, h):
            return
        win = _w32()
        if self.memdc is not None:
            try:
                win.g.DeleteDC(self.memdc)
                if self.dib:
                    win.g.DeleteObject(self.dib)
            except Exception:
                pass
            self.memdc = None
            self.dib = None
        bmi = win.BMIH()
        bmi.biSize = ctypes.sizeof(win.BMIH)
        bmi.biWidth = w
        bmi.biHeight = -h          # negative → top-down rows (match the PIL buffer)
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0      # BI_RGB
        screen = win.u.GetDC(None)
        self.memdc = win.g.CreateCompatibleDC(screen)
        bits = ctypes.c_void_p()
        self.dib = win.g.CreateDIBSection(self.memdc, ctypes.byref(bmi), 0,
                                          ctypes.byref(bits), None, 0)
        win.u.ReleaseDC(None, screen)
        if not self.dib:
            try:
                win.g.DeleteDC(self.memdc)
            except Exception:
                pass
            self.memdc = None
            self.dib = None
            self.bits = ctypes.c_void_p()
            self.dib_size = None
            return
        self.bits = bits
        win.g.SelectObject(self.memdc, self.dib)
        self.dib_size = (w, h)

    def push(self, pil_img):
        """Convert a PIL RGBA image to BGRA bytes and memmove into the DIB."""
        w, h = pil_img.size
        self.ensure_dib(w, h)
        arr = _np.asarray(pil_img.convert("RGBA"), dtype=_np.uint8)  # (h,w,4) RGBA
        a = arr[:, :, 3:4].astype(_np.uint16)
        rgb = (arr[:, :, :3].astype(_np.uint16) * a // 255).astype(_np.uint8)
        bgra = _np.dstack([rgb[:, :, 2], rgb[:, :, 1], rgb[:, :, 0],
                           arr[:, :, 3]])
        buf = _np.ascontiguousarray(bgra).tobytes()
        ctypes.memmove(self.bits, buf, len(buf))

    def blit(self, hwnd, x, y, w, h):
        """Push the DIB to a layered window via UpdateLayeredWindow."""
        win = _w32()
        ptDst = win.POINT(int(x), int(y))
        size = win.SIZE(int(w), int(h))
        ptSrc = win.POINT(0, 0)
        blend = win.BLEND(0, 0, 255, 1)   # AC_SRC_OVER, src alpha = AC_SRC_ALPHA
        ULW_ALPHA = 0x02
        ok = win.u.UpdateLayeredWindow(hwnd, None, ctypes.byref(ptDst),
                                       ctypes.byref(size), self.memdc,
                                       ctypes.byref(ptSrc), 0,
                                       ctypes.byref(blend), ULW_ALPHA)
        if not ok:
            raise ctypes.WinError()

    def destroy(self):
        """Release GDI resources (safe to call multiple times)."""
        if self.memdc is not None:
            win = _w32()
            try:
                win.g.DeleteDC(self.memdc)
                if self.dib:
                    win.g.DeleteObject(self.dib)
            except Exception:
                pass
            self.memdc = None
            self.dib = None
            self.bits = ctypes.c_void_p()
            self.dib_size = None

    def __del__(self):
        """Finalizer: ensure GDI handles are freed even if the caller forgot
        destroy().  Every island frame creates a fresh _LayeredDC and without
        this finalizer ~22 GDI handles leak per second (Critical 4)."""
        try:
            self.destroy()
        except Exception:
            pass


def _style_layered(win, transparent):
    """Apply layered-window extended styles to a Toplevel and return its HWND.
    If transparent is True, WS_EX_TRANSPARENT is added (click-through).
    If False, WS_EX_TOPMOST is added instead (clicks land)."""
    w = _w32()
    hwnd = w.u.GetAncestor(win.winfo_id(), 2)  # GA_ROOT (real OS window)
    GWL_EXSTYLE = -20
    ex = w.u.GetWindowLongW(hwnd, GWL_EXSTYLE)
    # layered (per-pixel alpha) + noactivate + toolwindow
    ex |= 0x80000 | 0x08000000 | 0x80
    if transparent:
        ex |= 0x20     # WS_EX_TRANSPARENT: click-through
    else:
        ex |= 0x8      # WS_EX_TOPMOST: always-on-top, clicks land
    w.u.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
    return hwnd


class _GlassPill:
    """The premium island: a per-pixel-alpha LAYERED Win32 window, painted by
    island_render (Pillow) and pushed via UpdateLayeredWindow.

    In-process — NO separate WebView2 window, NO extra process. The window is
    genuinely click-through (WS_EX_TRANSPARENT), always-on-top, and never
    activates (WS_EX_NOACTIVATE), so typing focus stays exactly where the user
    is dictating. Per-pixel alpha gives real anti-aliased rounded corners, a
    soft drop-shadow and a gold glow halo — the things the transparent-colour-key
    tkinter canvas never could. ok is False if PIL/numpy/ctypes or the layered
    setup fails, and the controller falls back to the canvas pill."""

    # Reserve the old bottom slot for the secondary control rail. The status
    # island now reads first and the controls dock underneath it.
    BOTTOM_GAP = 14 + island_render.BAR_H + CONTROL_GAP

    def __init__(self, root):
        self.ok = False
        self.visible = False
        self.win = None
        self._hwnd = None
        self._styled = False
        self._geo = None
        self._ldc = _LayeredDC()  # shared GDI memory-DC + DIB manager
        if not (_HAVE_CTYPES and _HAVE_GLASS):
            return
        try:
            self.W, self.H = island_render.WIN_W, island_render.WIN_H
            self.win = tk.Toplevel(root)
            self.win.overrideredirect(True)
            self.win.attributes("-topmost", True)
            self.win.geometry(f"{self.W}x{self.H}+0+0")
            self.win.update_idletasks()
            self.win.withdraw()
            self.ok = True
        except Exception as e:
            print("glass island window unavailable (tkinter pill continues):", e)
            self.ok = False

    def _ensure_styled(self):
        if self._styled:
            return
        self._hwnd = _style_layered(self.win, transparent=True)
        self._styled = True

    def place(self):
        """Centre near the bottom of the active monitor; sets tk geometry so the
        layered window maps at the right spot (no top-left flash).

        Monitor detection (the ctypes _monitor_rect call) is CACHED on the
        foreground-window hwnd: at ~22fps the pill re-placed every frame, paying
        full MonitorFromWindow/GetMonitorInfo/GetDpi each time. We only recompute
        when the foreground window actually changed (i.e. you moved monitors) —
        otherwise the cached geometry is reused (owner v6 perf)."""
        if _HAVE_CTYPES and self._geo is not None:
            try:
                fg = ctypes.windll.user32.GetForegroundWindow()
            except Exception:
                fg = None
            if fg is not None and fg == getattr(self, "_last_fg", None):
                return self._geo            # unchanged foreground → cached spot
            self._last_fg = fg
        rect = _monitor_rect()
        if rect:
            left, top, right, bottom = rect
            x = int(left + (right - left - self.W) / 2)
            y = int(bottom - self.H - self.BOTTOM_GAP)
            min_y = int(top)
            if y < min_y:
                y = min_y
        else:
            sw = self.win.winfo_screenwidth()
            sh = self.win.winfo_screenheight()
            x = max(0, (sw - self.W) // 2)
            y = max(0, sh - self.H - self.BOTTOM_GAP)
        if (x, y) != self._geo:
            self._geo = (x, y)
            try:
                self.win.geometry(f"{self.W}x{self.H}+{x}+{y}")
            except Exception:
                pass
        return x, y

    def push(self, pil_img, x, y):
        if not self.ok:
            return False
        try:
            self._ensure_styled()
            self._ldc.push(pil_img)
            w, h = pil_img.size
            self._ldc.blit(self._hwnd, x, y, w, h)
            return True
        except Exception as e:
            print("glass push failed — falling back to canvas pill:", e)
            self.ok = False
            return False

    def show(self):
        if not self.ok or self.visible:
            return
        try:
            self.win.deiconify()
            self.win.attributes("-topmost", True)
            self.visible = True
        except Exception:
            pass

    def hide(self):
        if not self.ok or not self.visible:
            return
        try:
            self.win.withdraw()
            self.visible = False
        except Exception:
            pass


class _WidgetBar:
    """The island's companion control rail, docked below the status island.

    It shares the warm glass language but is deliberately shorter and quieter,
    so Listening remains the primary read and controls feel like a small dock.

    The bar runs in its own per-pixel-alpha LAYERED Win32 window — NOT click-
    through (no WS_EX_TRANSPARENT) so button clicks land, yet WS_EX_NOACTIVATE
    ensures clicking never steals typing focus from wherever the user is
    dictating. Fully fail-safe: if the window or the Win32 styling can't be set
    up, `ok` is False and every method is a no-op, so the island and the whole
    app keep working exactly as before — the bar just doesn't appear (it is pure
    additive chrome)."""

    def __init__(self, root, on_mode=None, on_deck=None, on_foreign=None,
                 on_correct=None, on_correct_dismiss=None, on_expand=None,
                 on_control_review=None,
                 on_control_cancel=None, on_stop=None, get_state=None):
        self.ok = False
        self.win = None
        self.visible = False
        self._styled = False
        self._geo = None
        self._hwnd = None
        # ITEM 3/4: the bar is now a mode deck. on_mode(key) selects a processing
        # mode, on_deck() opens the Deck, on_foreign() toggles Foreign; on_expand()
        # toggles the compact selector open/closed (refinement pass §1); get_state()
        # returns the live snapshot {modes, active, foreign_on, show_foreign, expanded}.
        self.on_mode = on_mode
        self.on_deck = on_deck
        self.on_foreign = on_foreign
        self.on_correct = on_correct
        self.on_correct_dismiss = on_correct_dismiss
        self.on_expand = on_expand
        self.on_control_review = on_control_review
        self.on_control_cancel = on_control_cancel
        self.on_stop = on_stop
        self.get_state = get_state or (lambda: {"modes": [("prompt", "Prompt")],
                                                 "active": None, "foreign_on": False,
                                                 "show_foreign": False})
        self._frame = 0
        self._ldc = _LayeredDC()  # shared GDI memory-DC + DIB manager

        if not (_HAVE_CTYPES and _HAVE_GLASS):
            return
        try:
            import tkinter.font as tkfont
            self._font = tkfont.Font(family=FONT_SB, size=11)
            self._W = island_render.BAR_WIN_W
            self._H = island_render.BAR_WIN_H
            self.win = tk.Toplevel(root)
            self.win.overrideredirect(True)
            self.win.attributes("-topmost", True)
            self.win.geometry(f"{self._W}x{self._H}+0+0")
            self.win.update_idletasks()
            self.win.withdraw()
            # Mouse binds for click handling (on the layered window itself)
            self.win.bind("<Button-1>", self._on_click)
            self.win.bind("<ButtonRelease-1>", self._on_release)
            self.win.bind("<Enter>", self._on_enter)
            self.win.bind("<Leave>", self._on_leave)
            self.ok = True
        except Exception as e:
            print("island widget bar unavailable (island continues):", e)
            self.ok = False

    def _ensure_styled(self):
        if self._styled:
            return
        self._hwnd = _style_layered(self.win, transparent=False)
        self._styled = True

    def _on_click(self, event):
        """Route a mouse-down to the chip / Deck / Foreign zone it landed in, using
        the SAME geometry island_render draws from (bar_layout) — so a click always
        hits what you see. x is in LOGICAL window px (matches bar_layout's units)."""
        if not self.ok:
            return
        try:
            state = self.get_state() or {}
            layout = island_render.bar_layout(state)
        except Exception as ex:
            print("bar hit-test failed:", ex)
            return
        x = event.x
        stop = layout.get("stop")
        if stop and stop[0] <= x < stop[1]:
            self._fire(self.on_stop)
            return
        review = layout.get("control_review")
        if review and review[0] <= x < review[1]:
            self._fire(self.on_control_review)
            return
        cancel = layout.get("control_cancel")
        if cancel and cancel[0] <= x < cancel[1]:
            self._fire(self.on_control_cancel)
            return
        for key, x0, x1 in layout.get("chips", []):
            if x0 <= x < x1:
                self._fire(self.on_mode, key)
                return
        dz = layout.get("deck")
        if dz and dz[0] <= x < dz[1]:
            self._fire(self.on_deck)
            return
        rz = layout.get("correction")
        if rz and rz[0] <= x < rz[1]:
            self._fire(self.on_correct)
            return
        dismiss = layout.get("correction_dismiss")
        if dismiss and dismiss[0] <= x < dismiss[1]:
            self._fire(self.on_correct_dismiss)
            return
        fz = layout.get("foreign")
        if fz and fz[0] <= x < fz[1]:
            self._fire(self.on_foreign)
            return

    def _fire(self, cb, *a):
        try:
            if callable(cb):
                cb(*a)
        except Exception as ex:
            print("bar callback failed:", ex)

    def _on_release(self, _event):
        pass

    def _on_enter(self, _event):
        pass

    def _on_leave(self, _event):
        pass

    def place_below(self, ix, iy, iw, ih):
        """Centre the rail beneath the visible status pill with a small gap."""
        if not self.ok:
            return
        if ih >= island_render.WIN_H:
            island_pill_bottom = iy + (ih + island_render.PILL_H) / 2.0
        else:
            island_pill_bottom = iy + ih
        rail_top_pad = self._H - island_render.BAR_H
        x = int(ix + (iw - self._W) / 2)
        y = int(island_pill_bottom + CONTROL_GAP - rail_top_pad)
        if (x, y) != self._geo:
            self._geo = (x, y)
            try:
                self.win.geometry(f"+{x}+{y}")
            except Exception:
                pass

    def render_and_push(self, frame, fade=1.0):
        """Render a fresh mode-deck image (from the live get_state snapshot) and
        push it to the layered window."""
        if not self.ok:
            return False
        try:
            snap = dict(self.get_state() or {})
            snap["frame"] = frame
            snap["fade"] = max(0.0, min(1.0, float(fade)))
            img = island_render.render_bar(snap)
            return self._push(img)
        except Exception as e:
            print("bar render failed (continuing):", e)
            return False

    def refresh(self):
        """No-op stub — the bar re-renders every frame via render_and_push(),
        so a standalone refresh is unnecessary. Kept for call-site compatibility
        (Island.set_armed() calls it)."""
        pass

    def _push(self, pil_img):
        if not self.ok:
            return False
        try:
            self._ensure_styled()
            self._ldc.push(pil_img)
            w, h = pil_img.size
            self._ldc.blit(self._hwnd, self._geo[0], self._geo[1], w, h)
            return True
        except Exception as e:
            print("bar push failed — bar hidden:", e)
            self.ok = False
            return False

    def show_below(self, ix, iy, iw, ih):
        if not self.ok:
            return
        self.place_below(ix, iy, iw, ih)
        if not self.visible:
            try:
                self.win.deiconify()
                self.win.attributes("-topmost", True)
                self.win.lift()
                self.visible = True
            except Exception:
                pass

    def hide(self):
        if not self.ok or not self.visible:
            return
        try:
            self.win.withdraw()
            self.visible = False
        except Exception:
            pass


class Island:
    """The main Island controller responsible for the visual state and floating window."""
    def __init__(self, root, style=None):
        self.root = root
        # ONE island now (owner v6) — there are no Basic/Standard/Enhanced tiers.
        # The single premium in-process renderer (island_render) always draws the
        # full look. `style` is accepted-but-ignored for call-site back-compat;
        # `enhanced` stays True for the canvas fallback's one path.
        self.style = "enhanced"
        self.enhanced = True
        self.state = "idle"
        self.level = 0.0
        self.frame = 0
        self.visible = False
        self.armed = False  # mode key currently held → show the near-white "armed" ring
        self.is_suggest = False  # the chip is a mode suggestion (violet) vs a paste hint
        self.is_gathering = False  # context/History job pulling material in → gold motes
        self.suspended = False   # True while the interactive question panel is up
        self.deck_open = False    # True while the History window (ctrl+alt+d) is up
        self.focused = True      # Perf: pause the animation when the app loses focus
        self._deck_api = None     # set by show_deck (test/automation hooks)
        self._deck_close = None   # closure that dismisses the open popup (toggle)
        self._picker_open = False  # True while the "Which mode?" picker is up
        self._picker_done = None   # closure that resolves the open picker
        self._correction_open = False
        self._correction_done = None
        self.correction_left = 0
        self._suspended_since = 0.0  # timestamp when suspended started (self-heal)
        self.done_color = C.gold
        self.done_left = 0
        self.build_color = C.gold
        self.build_offline = False
        self.flash_offline = False
        self.flash_pasted = True   # done state verb: True→"Pasted!", False→"Saved"
        self.flash_outcome = "confirmed"
        self.flash_reason = ""
        self.flash_message = ""
        self.flash_cleanup_warning = ""
        self.hint_text = ""
        self.hint_left = 0
        self._mx = self._my = 0  # last placed canvas-island top-left (Tk coords)

        # The premium renderer: an in-process per-pixel-alpha LAYERED window
        # painted by island_render (Pillow). NO separate process, NO WebView2.
        self.glass = _GlassPill(root)

        # THE BIG SHIFT — Prompt toggle + Deck access are now integrated into the
        # island itself (Prompt purple ring + subtle "✦" indicator). Callbacks
        # are stored for controller wiring via set_widget_callbacks().
        self.on_mode = None
        self.on_deck = None
        self.on_foreign = None
        self.on_correct = None
        self.on_control_review = None
        self.on_control_cancel = None
        self.on_stop = None
        # The companion rail is the island's direct mode deck: selectable lanes,
        # configured language, then Deck. `bar_state` is
        # the live snapshot the bar renders + hit-tests from; the controller pushes
        # it via set_bar_state(). `armed_color` lets the island ring echo the active
        # mode's colour (cohesion), defaulting to Prompt purple.
        self.bar_state = {
            "modes": [("prompt", "Prompt")],
            "active": None,
            "foreign_on": False,
            "show_foreign": False,
            "foreign_label": "Language",
            "correction_available": False,
            "correction_label": "Review",
            "control_review_available": False,
            "stop_enabled": False,
            "stop_label": "Stop",
            # Retained for snapshots/tests from the retired dropdown layout.
            "expanded": False,
        }
        self.armed_color = MODE_COLORS.get("prompt", C.gold)
        self.widget = _WidgetBar(
            root,
            on_mode=self._widget_mode,
            on_deck=self._widget_deck,
            on_foreign=self._widget_foreign,
            on_correct=self._widget_correct,
            on_correct_dismiss=self.clear_correction,
            on_expand=self._widget_expand,
            on_control_review=self._widget_control_review,
            on_control_cancel=self._widget_control_cancel,
            on_stop=self._widget_stop,
            get_state=lambda: self.bar_state,
        )

        # The reliable fallback: a transparent-colour-key tkinter canvas pill,
        # used only if the glass renderer is unavailable (no Pillow/numpy) or a
        # frame ever fails mid-session — so the island can never go missing.
        self.win = self._make_window()
        self.canvas = tk.Canvas(self.win, width=W, height=H, bg=_TRANSPARENT,
                                highlightthickness=0, bd=0)
        self.canvas.pack()
        self.win.update_idletasks()
        self._place()
        self._click_through(self.win, self.canvas)
        self.win.withdraw()
        self._tick()

    def _make_window(self):
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", 0.95)
            win.attributes("-transparentcolor", _TRANSPARENT)
        except tk.TclError:
            pass
        win.config(bg=_TRANSPARENT)
        return win

    def _place(self):
        """Place the canvas-fallback pill centred near the bottom of the active
        monitor (the glass pill places itself in _GlassPill.place())."""
        rect = _monitor_rect()
        if rect:
            left, top, right, bottom = rect
            x = int(left + (right - left - W) / 2)
            y = int(bottom - H - BOTTOM_MARGIN)
            min_y = int(top)
            if y < min_y:
                y = min_y
        else:
            sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
            x = max(0, (sw - W) // 2)
            y = max(0, sh - H - BOTTOM_MARGIN)
        self._mx, self._my = x, y
        try:
            self.win.geometry(f"{W}x{H}+{x}+{y}")
        except Exception:
            pass

    # ---- thread-safe setters ----
    def set_state(self, s):
        """Thread-safe setter for the island's visual state."""
        if s in ("listening", "search", "transcribing", "building", "error"):
            self.bar_state["correction_available"] = False
            self.correction_left = 0
        self.state = s
        self.bar_state["stop_enabled"] = s in ("listening", "search")
        self._refresh_bar()

    def set_level(self, lvl):
        """Thread-safe setter for the current audio volume level (0.0 to 1.0)."""
        self.level = lvl

    def set_dictation_progress(self, progress):
        """Show content-free elapsed time and durable-save progress by Stop."""
        progress = progress if isinstance(progress, dict) else {}
        elapsed = max(0, int(progress.get("duration_seconds", 0)))
        minutes, seconds = divmod(elapsed, 60)
        saved = max(0, int(progress.get("segments_persisted", 0)))
        self.bar_state["stop_label"] = (
            f"Stop · {minutes}:{seconds:02d} · {saved} saved"
        )
        self._refresh_bar()

    def _mode_names(self, mode):
        """Normalise a mode (string or list) → real mode names, with any
        'context'/'context_stream' pseudo-mode stripped (it carries no colour now —
        it implies the gold-particle gathering motion instead). Returns
        (names, gathering)."""
        names = mode if isinstance(mode, list) else [mode]
        gathering = any(m in ("context", "context_stream") for m in names)
        names = [m for m in names if m not in ("context", "context_stream")] or ["text"]
        return names, gathering

    def set_building(self, mode, offline=False, gathering=None):
        """Set the building state colour(s). `mode` is a mode string or a list of
        modes (a genuine hybrid). `gathering`=True (or a 'context' pseudo-mode)
        shows the gold-particle context-absorption MOTION instead of any tint."""
        names, ctx = self._mode_names(mode)
        self.is_gathering = bool(gathering) if gathering is not None else ctx
        self.build_colors = [MODE_COLORS.get(m, C.gold) for m in names]
        self.build_color = self.build_colors[0]  # primary for backward compat
        pretty = [MODE_LABELS.get(m, str(m).title()) for m in names]
        self.build_label = " + ".join(dict.fromkeys(pretty))
        self.build_offline = offline
        self.state = "building"

    def flash(self, mode, offline=False, pasted=True, outcome=None, reason="",
              message="", cleanup_warning=""):
        """Flash the island's finished state. Accepts a single mode or list of
        modes. `pasted` decides the verb: True → "Pasted!" (the text actually
        landed in a focused text field), False → "Saved" (it was kept in History
        but not pasted anywhere) — owner v4: never claim "Pasted" when nothing was."""
        # A new result supersedes any previous correction target. The controller
        # calls offer_correction() immediately afterwards only for eligible plain
        # dictation, keeping smart-mode rewrites out of the vocabulary learner.
        self.bar_state["correction_available"] = False
        self.correction_left = 0
        names, _ = self._mode_names(mode)
        self.is_gathering = False  # the finish flash never shows gathering motion
        self.done_colors = [MODE_COLORS.get(m, C.gold) for m in names]
        self.done_color = self.done_colors[0]
        # mode name(s) for the island's "Pasted! · Email" / "Saved · Email" tail
        self.done_label = " + ".join(dict.fromkeys(
            MODE_LABELS.get(m, str(m).title()) for m in names))
        self.flash_offline = offline
        self.flash_pasted = bool(pasted)
        self.flash_outcome = outcome or ("confirmed" if pasted else "saved_only")
        self.flash_reason = str(reason or "")
        self.flash_message = str(message or "")
        self.flash_cleanup_warning = str(cleanup_warning or "")
        # One uniform ~2s linger for BOTH outcomes (owner v9: transient feedback
        # should be ~2s — the old not-pasted ×3 ≈ 2.7s read as "too long"). The
        # not-pasted "Saved · …" cue is short enough to read inside the same 2s.
        self.done_left = DONE_FRAMES
        self.state = "done"

    def hint(self, text):
        self.hint_text = text or "Ctrl + Alt + D"
        self.hint_left = HINT_FRAMES
        self.is_suggest = False
        self.state = "hint"

    def set_armed(self, on):
        """THE BIG SHIFT: `armed` now mirrors the sticky Prompt-mode toggle (the
        held mode key is retired). When on, the island rings in Prompt purple and
        the companion bar lights its Prompt pill — so you can SEE Prompt mode is on."""
        self.armed = bool(on)
        try:
            if getattr(self, "widget", None):
                self.widget.refresh()
        except Exception:
            pass

    def set_bar_state(self, modes=None, active="\x00", foreign_on=None,
                      show_foreign=None, foreign_label=None):
        """ITEM 3/4: push the live mode-deck snapshot from the controller. Only the
        provided fields change (active uses a sentinel so None can be set to clear
        it). Keeps the island ring in sync — `armed` lights when ANY mode is active,
        in that mode's colour (armed_color), so the bar and island read as ONE accent."""
        st = self.bar_state
        if modes is not None:
            st["modes"] = list(modes)
        if active != "\x00":
            st["active"] = active
        if foreign_on is not None:
            st["foreign_on"] = bool(foreign_on)
        if show_foreign is not None:
            st["show_foreign"] = bool(show_foreign)
        if foreign_label is not None:
            st["foreign_label"] = str(foreign_label or "Language")[:18]
        self.armed = bool(st["active"])
        if st["active"]:
            self.armed_color = MODE_COLORS.get(st["active"], C.gold)
        try:
            if getattr(self, "widget", None):
                self.widget.refresh()
        except Exception:
            pass

    # ---- companion mode-deck callbacks ----
    def set_widget_callbacks(self, on_mode=None, on_deck=None, on_foreign=None,
                             on_correct=None, on_control_review=None,
                             on_control_cancel=None, on_stop=None):
        """Wire the bar to the controller (select a mode / open the Deck / toggle
        Foreign)."""
        self.on_mode = on_mode
        self.on_deck = on_deck
        self.on_foreign = on_foreign
        self.on_correct = on_correct
        self.on_control_review = on_control_review
        self.on_control_cancel = on_control_cancel
        self.on_stop = on_stop

    def _widget_mode(self, key):
        if callable(self.on_mode):
            self.on_mode(key)

    def _widget_deck(self):
        if callable(self.on_deck):
            self.on_deck()

    def _widget_foreign(self):
        if callable(self.on_foreign):
            self.on_foreign()

    def _widget_correct(self):
        if not self.bar_state.get("correction_available"):
            return
        self.bar_state["correction_available"] = False
        self.correction_left = 0
        self._refresh_bar()
        if callable(self.on_correct):
            self.on_correct()

    def _widget_control_review(self):
        if callable(self.on_control_review):
            self.on_control_review()

    def _widget_control_cancel(self):
        self.clear_control_review()
        if callable(self.on_control_cancel):
            self.on_control_cancel()

    def _widget_stop(self):
        if callable(self.on_stop):
            self.on_stop()

    def _widget_expand(self):
        """Compatibility no-op: the two direct modes are always visible now."""
        return

    def _refresh_bar(self):
        """Re-render immediately after a control-state change."""
        try:
            if getattr(self, "widget", None):
                self.widget.refresh()
        except Exception:
            pass

    def suggest(self, text):
        """Show the dismissible 'was this a <mode>?' chip (violet), lingering ~45s so the
        user has time to re-press the mode key and say the mode."""
        self.hint_text = text or "wrong mode?"
        self.hint_left = SUGGEST_FRAMES
        self.is_suggest = True
        self.state = "hint"

    def offer_correction(self, label="Review", hint="Review correction?"):
        """Expose a short-lived correction review in the companion bar.

        This is called only when the target-field observer has found a concrete edit
        to the most recent paste. It is an explicit consent boundary: the correction
        is not learned until the user opens and confirms the review.
        """
        self.bar_state["correction_available"] = True
        self.bar_state["correction_label"] = str(label or "Review")[:10]
        self.bar_state["expanded"] = False
        self.correction_left = CORRECTION_FRAMES
        self.correction_hint = str(hint or "Review correction?")[:80]
        if self.state == "idle":
            self.hint_text = self.correction_hint
            self.hint_left = CORRECTION_FRAMES
            self.is_suggest = False
            self.state = "hint"
        elif self.state == "hint":
            self.hint_text = self.correction_hint
            self.is_suggest = False
            self.hint_left = max(self.hint_left, CORRECTION_FRAMES)
        self._refresh_bar()

    def clear_correction(self):
        self.bar_state["correction_available"] = False
        self.bar_state["correction_label"] = "Review"
        self.correction_left = 0
        if self.state == "hint" and self.hint_text == getattr(
                self, "correction_hint", "Review correction?"):
            self.state = "idle"
        self._refresh_bar()

    def offer_control_review(self, hint="Computer plan ready"):
        """Surface a pending approval without opening or focusing a full window."""
        self.bar_state["control_review_available"] = True
        self.bar_state["expanded"] = False
        self.hint(str(hint or "Computer plan ready")[:80])
        self._refresh_bar()

    def clear_control_review(self):
        self.bar_state["control_review_available"] = False
        self._refresh_bar()

    # ---- internals (main thread) ----
    def _click_through(self, win, canvas):
        if not _HAVE_CTYPES:
            return
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetAncestor(canvas.winfo_id(), 2)
            if not hwnd:
                hwnd = win.winfo_id()
            ex = user32.GetWindowLongW(hwnd, -20)
            user32.SetWindowLongW(hwnd, -20, ex | 0x80000 | 0x20 | 0x08000000 | 0x80)
        except Exception:
            pass

    def set_style(self, style=None):
        """No-op (owner v6): there is exactly ONE island look now, so there is no
        style to switch. Kept so existing callers don't break."""
        return

    def close_deck(self):
        """Dismiss the History window if it's open (the ctrl+alt+d hotkey, owner
        v6 §6a). Safe to call when nothing is open. Runs on the Tk thread."""
        fn = getattr(self, "_deck_close", None)
        if fn is not None:
            try:
                fn()
            except Exception:
                pass

    def _show(self):
        if self.visible:
            return
        try:
            # Re-centre on the monitor holding the FOREGROUND window — but only
            # when that window actually changed since the last show. _place()
            # runs ctypes monitor detection; skipping it when the foreground
            # hasn't moved trims per-show latency (perf audit) while still
            # following you across monitors.
            cur_fg = None
            if _HAVE_CTYPES:
                try:
                    cur_fg = ctypes.windll.user32.GetForegroundWindow()
                except Exception:
                    cur_fg = None
            if cur_fg is None or cur_fg != getattr(self, "_last_fg_hwnd", None):
                self._last_fg_hwnd = cur_fg
                self._place()
            self.win.deiconify()
            self.win.attributes("-topmost", True)
            self.visible = True
        except Exception:
            pass

    def _hide(self):
        if self.visible:
            try:
                self.win.withdraw()
            except Exception:
                pass
            self.visible = False

    def _pill(self, c, pill_w, w, h, animate=False):
        x0 = (w - pill_w) / 2
        rad = (h - 2) // 2
        # UX-Pilot borderGlow: the rim breathes faintly gold on a ~3s cycle.
        # Static for the mini pill (animate=False) — only the live island glows.
        rim = _PILL_RIM
        if animate:
            gp = (math.sin(self.frame * 0.094) + 1) / 2
            rim = _blend(_PILL_RIM, C.gold, 0.22 * gp)
        ui.round_rect(
            c, x0, 1, x0 + pill_w, h - 1, rad, fill=_PILL_BG, outline=rim
        )
        # Subtle warm hairline highlight along the top inner edge — reads as "lifted
        # glass" and makes the pill feel more premium without being heavy-handed.
        c.create_line(x0 + rad, 2.0, x0 + pill_w - rad, 2.0, fill=_PILL_HI, width=1)
        if animate:
            # UX-Pilot glassSheen: a short brighter glint travels the top edge
            # every few seconds — one extra line per frame, no filters.
            span = pill_w + 90
            sx = x0 - 45 + ((self.frame * 1.6) % span)
            a, b = max(x0 + rad, sx - 16), min(x0 + pill_w - rad, sx + 16)
            if b > a:
                c.create_line(a, 2.0, b, 2.0,
                              fill=_blend(_PILL_HI, "#F3EEE1", 0.5), width=1)
        return x0

    def _armed_ring(self, c, x0, pill_w, w, h):
        """A thin near-white ring around the pill while the mode key is held."""
        pulse = 0.55 + 0.45 * (math.sin(self.frame * 0.22) + 1) / 2
        col = _blend(_PILL_BG, _ARMED_RING, pulse)
        ui.round_rect(c, x0 - 1, 0, x0 + pill_w + 1, h, (h) // 2, fill="", outline=col)

    def _offline_mark(self, c, cy, x):
        c.create_oval(x - 3, cy - 3, x + 3, cy + 3, outline=C.text_dim, width=1)

    # state → readable label (HTML-island parity: the pill always says what
    # Mumble is doing, next to the animation, never animation alone)
    _STATE_LABEL = {
        "listening": "Listening",
        "transcribing": "Transcribing",
        "building": "Building",
        "done": "Done",
        "search": "Search",
    }

    def _listen_timer(self):
        start = getattr(self, "_listen_start", 0.0) or time.time()
        secs = max(0.0, time.time() - start)
        return f"{int(secs // 60)}:{int(secs % 60):02d}"

    def _build_snapshot(self):
        """One immutable visual snapshot the Pillow renderer (island_render)
        turns into a frame: state colour, label, hint, timer, level, armed/
        suggest/offline flags and the mode-colour ramp. The canvas fallback
        reads the same underlying fields, so the two renderers always agree."""
        state = self.state
        rim = _PILL_RIM
        dot = C.gold
        label = self._STATE_LABEL.get(state, "")
        label_color = C.text_dim
        hint = ""
        timer = ""
        colors = None
        offline = False
        # `gathering` = a context/History job is pulling material in → the island
        # shows the gold-particle ABSORPTION motion (owner v6, no context colour).
        gathering = bool(getattr(self, "is_gathering", False))

        if state == "listening":
            dot = C.gold
            label_color = C.gold
            rim = _blend(_PILL_RIM, C.gold, 0.4)
            timer = self._listen_timer()
        elif state == "search":
            # Search mode: a distinct blue-white dot + label so the user always
            # knows they're in voice-search mode (not plain dictation or Prompt).
            dot = "#5AA9E6"
            label_color = "#C5DFF5"
            rim = _blend(_PILL_RIM, "#5AA9E6", 0.4)
            timer = self._listen_timer()
        elif state == "transcribing":
            dot = C.amber
            label_color = C.amber
            rim = _blend(_PILL_RIM, C.amber, 0.35)
        elif state == "building":
            colors = getattr(self, "build_colors", [self.build_color])
            accent = self.build_color
            dot = accent
            label_color = accent
            rim = _blend(_PILL_RIM, accent, 0.4)
            bl = getattr(self, "build_label", "")
            label = (f"Gathering · {bl}" if gathering else f"Building · {bl}") \
                if bl else ("Gathering" if gathering else "Building")
            offline = bool(self.build_offline)
        elif state == "done":
            colors = getattr(self, "done_colors", [self.done_color])
            accent = self.done_color
            dot = accent
            label_color = accent
            rim = _blend(_PILL_RIM, accent, 0.4)
            outcome = getattr(self, "flash_outcome", "confirmed")
            labels = {
                "confirmed": "Inserted",
                "sent_unconfirmed": "Sent",
                "not_sent": "Not inserted",
                "uncertain": "Delivery uncertain",
                "saved_only": "Not inserted",
            }
            label = labels.get(outcome, "Saved")
            pasted = outcome == "confirmed"
            # Not pasted → the tail TELLS the user how to place it (the History
            # window hotkey), instead of the mode name (owner v6: "say press
            # ctrl+alt+d", never imply it was already pasted).
            hints = {
                "sent_unconfirmed": "Saved in Deck",
                "uncertain": "No auto-retry",
            }
            reason_hints = {
                "permission_needed": "Saved in Mumble",
                "held_modifier": "Release held key",
                "target_changed": "Target changed",
            }
            hint = (getattr(self, "done_label", "") if pasted else
                    reason_hints.get(getattr(self, "flash_reason", ""),
                                     hints.get(outcome, "Ctrl + Alt + D")))
            if pasted and getattr(self, "flash_cleanup_warning", ""):
                hint = "Clipboard warning"
            offline = bool(self.flash_offline)
        elif state == "hint":
            label = self.hint_text
            if self.is_suggest:
                dot = _SUGGEST
                label_color = _SUGGEST
                rim = _blend(_PILL_RIM, _SUGGEST, 0.4)
            else:
                dot = C.gold
                label_color = C.gold_hi
        elif state == "error":
            dot = C.red
            label_color = C.red
            rim = _blend(_PILL_RIM, C.red, 0.4)
            label = label or "Error"

        # Graceful fade-out: hint/suggest chips and the done flash dissolve over
        # their final frames instead of snapping to nothing (owner v6: "it should
        # fade away"). 1.0 = fully opaque.
        fade = 1.0
        if state == "hint":
            fade = max(0.0, min(1.0, self.hint_left / float(FADE_OUT_FRAMES)))
        elif state == "done":
            fade = max(0.0, min(1.0, self.done_left / 6.0))
        # Fade-IN on first appearance (refinement pass §1): the moment the island
        # goes from idle to an active state, ramp its whole-pill opacity up over
        # APPEAR_FRAMES so it eases into view rather than snapping. The counter is
        # only ARMED here (on the idle→active transition) and DECREMENTED in _tick,
        # so a stray wake() never double-advances it.
        active_now = state in _ACTIVE_STATES
        if active_now and not getattr(self, "_was_active", False):
            self._appear_left = APPEAR_FRAMES
        self._was_active = active_now
        appear = getattr(self, "_appear_left", 0)
        if appear > 0:
            ramp = APPEAR_FLOOR + (1.0 - APPEAR_FLOOR) * (
                1.0 - appear / float(APPEAR_FRAMES))
            fade = min(fade, ramp)
        return {
            "frame": self.frame, "state": state,
            "level": max(0.0, min(1.0, self.level)),
            "armed": bool(
                self.armed and state in ("listening", "transcribing", "building")
            ),
            # ITEM 3: the island ring echoes the ACTIVE mode's colour (cohesion with
            # the mode-deck above), defaulting to Prompt purple.
            "armed_color": getattr(self, "armed_color", None)
            or MODE_COLORS.get("prompt", C.gold),
            "suggest": bool(self.is_suggest and state == "hint"),
            "offline": offline, "gathering": gathering, "fade": fade,
            "label": label, "hint": hint, "timer": timer,
            "dot": dot, "rim": rim, "label_color": label_color, "colors": colors,
        }

    def _label_font(self):
        """Lazy Font (needs a live Tk root); used to measure the pill width."""
        if getattr(self, "_font", None) is None:
            import tkinter.font as tkfont
            self._font = tkfont.Font(family=FONT_SB, size=9)
        return self._font

    def _anim_span(self):
        """Width of the animation zone for the current state ((n-1) × gap)."""
        return {"listening": 51, "transcribing": 20,
                "building": 32, "done": 51, "search": 51}.get(self.state, 0)

    def _render(self):
        c = self.canvas
        c.delete("all")
        cy = H / 2
        f = self._label_font()
        state = self.state
        is_hint = state == "hint"

        # ---- compose: [dot] [animation] [label] — pill hugs its content ----
        label = self.hint_text if is_hint else self._STATE_LABEL.get(state, "")
        if state == "building" and getattr(self, "build_label", ""):
            label = f"Building  ·  {self.build_label}"
        elif state == "done":
            outcome = getattr(self, "flash_outcome", "confirmed")
            verb = {
                "confirmed": "Inserted", "sent_unconfirmed": "Sent",
                "not_sent": "Not inserted", "uncertain": "Delivery uncertain",
                "saved_only": "Not inserted",
            }.get(outcome, "Saved")
            reason_hints = {
                "permission_needed": "Saved in Mumble",
                "held_modifier": "Release held key",
                "target_changed": "Target changed",
            }
            dl = (getattr(self, "done_label", "") if outcome == "confirmed" else
                  reason_hints.get(getattr(self, "flash_reason", ""),
                  {"sent_unconfirmed": "Saved in Deck",
                   "uncertain": "No auto-retry"}.get(outcome, "Ctrl + Alt + D")))
            if outcome == "confirmed" and getattr(
                    self, "flash_cleanup_warning", ""):
                dl = "Clipboard warning"
            label = f"{verb}  ·  {dl}" if dl else verb
        anim_w = 0 if is_hint else self._anim_span()
        text_w = f.measure(label) if label else 0
        dot_r = 3.4
        pill_w = PAD_X * 2 + dot_r * 2
        if anim_w:
            pill_w += SEG_GAP + anim_w
        if text_w:
            pill_w += SEG_GAP + text_w + 1
        pill_w = max(PILL_W, min(W - 4, pill_w))
        # Enhanced = animated rim glow + travelling glass sheen; Basic = a clean
        # static pill (lighter per-frame, no ambient glints).
        x0p = self._pill(c, pill_w, W, H, animate=self.enhanced)
        if self.armed and state in ("listening", "transcribing"):
            self._armed_ring(c, x0p, pill_w, W, H)

        # ---- status dot: state colour, gentle pulse ----
        if is_hint:
            base = _SUGGEST if self.is_suggest else C.gold
        else:
            base = {"listening": C.gold, "transcribing": C.amber,
                    "building": self.build_color,
                    "done": self.done_color}.get(state, C.gold)
        pulse = 0.55 + 0.45 * (math.sin(self.frame * 0.16) + 1) / 2
        dx = x0p + PAD_X + dot_r
        dot_col = _blend(_PILL_BG, base, 0.35 + 0.65 * pulse)
        # UX-Pilot ripple-ring while listening: a ring expands from the dot and
        # fades — drawn as one outline oval whose colour sinks into the pill.
        # Enhanced-only ambient flourish; Basic keeps just the dot.
        if state == "listening" and self.enhanced:
            rp = (self.frame % 36) / 36.0          # ~1.6s cycle at 22fps
            rr = dot_r + 1.5 + rp * 6.0
            ring = _blend(_PILL_BG, base, max(0.0, 0.5 * (1.0 - rp)))
            c.create_oval(dx - rr, cy - rr, dx + rr, cy + rr,
                          outline=ring, width=1)
        c.create_oval(dx - dot_r, cy - dot_r, dx + dot_r, cy + dot_r,
                      fill=dot_col, outline="")
        x = dx + dot_r + SEG_GAP

        # ---- animation zone (left-anchored at x) ----
        if state == "listening":
            lvl = max(0.0, min(1.0, self.level))
            # UX-Pilot bar profile: opacity swells toward the middle bars
            # (gold/55 → gold → gold/45), 0.8s cycle with a 0.11s stagger.
            profile = (0.55, 0.75, 1.0, 1.0, 0.85, 0.65, 0.45)
            n, gap, bw, maxh = 7, 8.5, 2.6, H - 11
            for i in range(n):
                ph = (math.sin(self.frame * 0.4 + i * 0.8) + 1) / 2
                h = 3 + (maxh - 3) * lvl * (0.55 + 0.45 * ph)
                xi = x + i * gap
                col = _blend(_PILL_BG, C.gold_hi,
                             profile[i] * (0.6 + 0.4 * ph))
                c.create_line(xi, cy - h / 2, xi, cy + h / 2,
                              width=bw, fill=col, capstyle="round")
        elif state == "search":
            lvl = max(0.0, min(1.0, self.level))
            profile = (0.55, 0.75, 1.0, 1.0, 0.85, 0.65, 0.45)
            n, gap, bw, maxh = 7, 8.5, 2.6, H - 11
            search_blue = "#5AA9E6"
            for i in range(n):
                ph = (math.sin(self.frame * 0.4 + i * 0.8) + 1) / 2
                h = 3 + (maxh - 3) * lvl * (0.55 + 0.45 * ph)
                xi = x + i * gap
                col = _blend(_PILL_BG, search_blue,
                             profile[i] * (0.6 + 0.4 * ph))
                c.create_line(xi, cy - h / 2, xi, cy + h / 2,
                              width=bw, fill=col, capstyle="round")
        elif state == "transcribing":
            n, gap, r = 3, 10, 2.4
            for i in range(n):
                ph = (math.sin(self.frame * 0.18 - i * 0.9) + 1) / 2
                col = _blend(_PILL_BG, C.amber, 0.25 + 0.75 * ph)
                xi = x + i * gap
                c.create_oval(xi - r, cy - r, xi + r, cy + r, fill=col, outline="")
        elif state == "building":
            n, gap, r = 5, 8, 2.3
            colors = getattr(self, "build_colors", [self.build_color])
            for i in range(n):
                ph = (math.sin(self.frame * 0.26 - i * 0.7) + 1) / 2
                if len(colors) >= 2:
                    t = i / (n - 1) if n > 1 else 0
                    idx = min(int(t * (len(colors) - 1)), len(colors) - 2)
                    lt = (t * (len(colors) - 1)) - idx
                    base_i = _blend(colors[idx], colors[idx + 1], lt)
                else:
                    base_i = colors[0]
                col = _blend(_PILL_BG, base_i, 0.55 + 0.45 * ph)
                xi = x + i * gap
                c.create_oval(xi - r, cy - r, xi + r, cy + r, fill=col, outline="")
            if self.build_offline:
                self._offline_mark(c, cy, x0p + pill_w - 11)
        elif state == "done":
            n, gap, bw = 7, 8.5, 2.6
            profile = (0.55, 0.75, 1.0, 1.0, 0.85, 0.65, 0.45)
            # CLAMP: not-pasted flashes set done_left = DONE_FRAMES*3 (longer linger,
            # v3.14.0), so an unclamped done_left/DONE_FRAMES exceeds 1.0 and drove
            # _blend past 255 → a malformed hex colour → TclError → dropped frames
            # (the canvas-fallback pill rendered nothing for ~1.5s). Clamp to [0,1].
            fade = min(1.0, self.done_left / DONE_FRAMES)
            colors = getattr(self, "done_colors", [self.done_color])
            for i in range(n):
                if len(colors) >= 2:
                    t = i / (n - 1) if n > 1 else 0
                    idx = min(int(t * (len(colors) - 1)), len(colors) - 2)
                    lt = (t * (len(colors) - 1)) - idx
                    base_i = _blend(colors[idx], colors[idx + 1], lt)
                else:
                    base_i = colors[0]
                col = _blend(_PILL_BG, base_i,
                             profile[i] * (0.45 + 0.55 * fade))
                h = 4 + (math.sin(i * 0.9) + 1) * 2.2
                xi = x + i * gap
                c.create_line(xi, cy - h / 2, xi, cy + h / 2,
                              width=bw, fill=col, capstyle="round")
            if self.flash_offline:
                self._offline_mark(c, cy, x0p + pill_w - 11)
        if anim_w:
            x += anim_w + SEG_GAP

        # ---- label / hint text ----
        if label:
            if is_hint:
                tp = 0.65 + 0.35 * (math.sin(self.frame * 0.16) + 1) / 2
                tint = _SUGGEST if self.is_suggest else C.gold_hi
                col = _blend(_PILL_BG, tint, tp)
            elif state == "building":
                # UX-Pilot shimmer: the Building label breathes between dim
                # text and the active mode's colour while the AI works.
                sp = (math.sin(self.frame * 0.12) + 1) / 2
                col = _blend(C.text_dim, self.build_color, 0.45 * sp)
            else:
                col = C.text_dim
            c.create_text(x, cy + 0.5, text=label, fill=col, font=f, anchor="w")

    def _hide_all(self):
        if self.visible:
            self._hide()
        if self.glass.ok and self.glass.visible:
            self.glass.hide()
        try:
            if getattr(self, "widget", None):
                self.widget.hide()
        except Exception:
            pass

    def _controls_visible(self):
        """Whether the companion controls are useful in the current state.

        Mode and language controls belong to active dictation. Processing and
        success feedback stay visually quiet; the only controls allowed to wake
        independently are an explicit correction review or computer-use approval.
        """
        return bool(
            self.state == "listening"
            or self.bar_state.get("correction_available")
            or self.bar_state.get("control_review_available")
        )

    def _paint_frame(self):
        """Render the CURRENT state once (no frame advance, no countdown). Shared
        by _tick (every cadence) and wake() (the instant a state change lands)."""
        active = self.state in _ACTIVE_STATES
        if active:
            snap = self._build_snapshot()
            drawn = False
            if self.glass.ok:
                # PRIMARY: the premium per-pixel-alpha glass pill (in-process).
                try:
                    x, y = self.glass.place()
                    self.glass.show()
                    drawn = self.glass.push(island_render.render(snap), x, y)
                except Exception as e:
                    print("glass render failed — using canvas pill:", e)
                    self.glass.ok = False
                    try:
                        self.glass.hide()
                    except Exception:
                        pass
                    drawn = False
                if drawn and self.visible:
                    self._hide()  # the canvas fallback was up — retire it
                if drawn:
                    if self._controls_visible():
                        try:
                            self.widget.show_below(x, y, self.glass.W, self.glass.H)
                            self.widget.render_and_push(self.frame, snap["fade"])
                        except Exception:
                            pass
                    else:
                        self.widget.hide()
            if not drawn:
                # FALLBACK: the reliable transparent-colour-key canvas pill.
                if self.glass.ok and self.glass.visible:
                    self.glass.hide()
                if not self.visible:
                    self._show()
                if self.canvas.winfo_exists():
                    self._render()
                if self._controls_visible():
                    try:
                        self.widget.show_below(self._mx, self._my, W, H)
                        self.widget.render_and_push(self.frame, snap["fade"])
                    except Exception:
                        pass
                else:
                    self.widget.hide()
        else:
            self._hide_all()

    def wake(self):
        """Repaint the island NOW, the moment a state setter runs, so a transition
        shows within a frame instead of waiting up to a full tick (owner v6
        responsiveness). Safe on the Tk thread; a no-op while suspended. Does NOT
        advance the frame counter, so it never disturbs the animation cadence.
        Always paints for active states — the island is a separate topmost overlay
        that must appear when recording starts, even when the Mumble window is not
        focused (bugfix-island-freeze: wake must fire regardless of focus)."""
        if self.suspended:
            return
        try:
            self._paint_frame()
        except tk.TclError:
            pass
        except Exception as e:
            print("island wake error (continuing):", e)

    def set_focused(self, focused):
        """Pause the island animation loop when the main window loses focus
        (the user is working in another app). The frame counter, state timers,
        and done/hint countdowns are ALL frozen while unfocused, so returning
        focus shows exactly the same state — no visible jump. (VAL-PERF-005)"""
        self.focused = focused

    def _tick(self):
        # Self-healing timeout: if the suspended panel ever gets stuck,
        # auto-release after 30s so the island can't stay hidden forever.
        # This must run regardless of focus or state to avoid a permanent
        # freeze when suspended coincides with unfocused (bugfix-island-freeze).
        if self.suspended:
            if time.time() - self._suspended_since > 30.0:
                self.suspended = False
                self._prev_state = ""  # force re-evaluation so it re-shows

        # Idle short-circuit: when the island is idle there's nothing to render.
        # _pump() → wake() handles instant repaint on state transitions, so we
        # never miss a frame. This cuts idle CPU from ~20 Hz to ~5 Hz.
        if self.state == "idle":
            try:
                if self.win.winfo_exists():
                    self.win.after(200, self._tick)
            except Exception:
                pass
            return

        is_active = self.state in _ACTIVE_STATES
        if not self.focused and not is_active:
            # Window is unfocused AND the island is idle — skip the frame
            # counter, state transitions, and painting entirely. Reschedule at
            # a relaxed 200ms cadence so we re-check focus promptly without
            # burning cycles. The moment focus returns or the island becomes
            # active, frame 0 of the resumed animation picks up exactly where
            # it left off (no jump). (VAL-PERF-005)
            try:
                if self.win.winfo_exists():
                    self.win.after(200, self._tick)
            except Exception:
                pass
            return

        # Run at full speed whenever the island is active — even if the Mumble
        # window is unfocused. The island is a separate topmost overlay window,
        # and its animations/countdowns must run regardless of main-window focus
        # so recording feedback always appears (bugfix-island-freeze).
        self.frame += 1
        # Advance the appearance fade-in (armed in _build_snapshot on idle→active).
        if getattr(self, "_appear_left", 0) > 0:
            self._appear_left -= 1
        try:
            if self.suspended:
                # Interactive question panel is up — keep the floating island
                # hidden.
                self._hide_all()
                raise _Skip
            prev_state = getattr(self, "_prev_state", "")
            if self.state == "done":
                self.done_left -= 1
                if self.done_left <= 0:
                    if self.bar_state.get("correction_available"):
                        self.state = "hint"
                        self.hint_text = getattr(
                            self, "correction_hint", "Review correction?")
                        self.hint_left = max(1, self.correction_left)
                        self.is_suggest = False
                    else:
                        self.state = "idle"
            elif self.state == "hint":
                # A mode-suggestion chip FREEZES while the mode key is held — the
                # user is (re)arming to respeak, so it must not fade from under them
                # (owner 0.9). A plain paste hint keeps counting down normally.
                if not (self.is_suggest and self.armed):
                    self.hint_left -= 1
                if self.hint_left <= 0:
                    self.bar_state["correction_available"] = False
                    self.correction_left = 0
                    self.state = "idle"
            if (self.state in ("listening", "search")
                    and prev_state != self.state):
                self._listen_start = time.time()   # drive the live timer
            self._prev_state = self.state

            self._paint_frame()
        except _Skip:
            pass  # suspended for the question panel
        except tk.TclError:
            pass  # window destroyed between check and render — harmless
        except Exception as e:  # never let one bad frame kill the tick loop
            print("island tick error (continuing):", e)
        try:
            if self.win.winfo_exists():
                # Tick rate: fast (45ms ≈ 22fps) while the pill is on screen for
                # smooth animation + an audio-reactive level. When idle/hidden the
                # tick is a no-op (state==idle → _paint_frame → _hide_all returns
                # immediately once hidden), so we relax it to a light safety re-sync.
                # Responsiveness is NOT lost: every state setter is dispatched through
                # _pump, which calls wake() to repaint within the same frame the
                # change lands, and set_level() repaints continuously while recording
                # — so the pill APPEARS instantly. 200ms (not the old 500ms, which the
                # owner found "laggy") keeps the next real tick close enough that the
                # frame-driven ripple ramps imperceptibly at dictation onset, while
                # cutting the idle island loop 20 Hz → 5 Hz (a no-op each wake).
                on_screen = self.visible or (self.glass.ok and self.glass.visible)
                delay = 45 if on_screen else 200
                self.win.after(delay, self._tick)
        except Exception:
            pass

    # ---- interactive question panel (DORMANT) ----
    # The per-prompt pop-up was replaced by the permanent "Prompt preferences" settings
    # card. This method is no longer called by the live app; kept for reference only.
    def ask_questions(self, questions, on_complete, animate=True):
        """DORMANT (not called): the old per-prompt clarification pop-up. Prompt shaping
        is now done via standing preferences in Settings. Kept for reference.

        Pop up a ROUNDED, themed multiple-choice panel to shape a prompt — the
        island in its interactive form. `questions` = [(text, [A, B, C, D]), ...].
        Calls on_complete(answers_list) when finished, or on_complete(None) if skipped.
        Runs on the Tk main thread. Fully guarded — ANY failure calls on_complete(None)
        so the caller still generates the prompt.

        The panel uses the app's rounded design language: a transparent-corner window
        with a round_rect background (so the corners are genuinely rounded), rounded
        option chips (_OptionButton), and a gentle fade+rise expand animation."""
        INNER_W = 380  # fixed inner width → option chips line up, no jitter
        PAD = 16
        done = {"v": False}

        def finish(result, win=None):
            if done["v"]:
                return
            done["v"] = True
            self.suspended = False
            try:
                if win is not None:
                    win.destroy()
            except Exception:
                pass
            try:
                on_complete(result)
            except Exception:
                pass

        try:
            self.suspended = True
            self._suspended_since = time.time()
            self._hide()
            win = tk.Toplevel(self.root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            try:
                win.attributes("-transparentcolor", _TRANSPARENT)  # rounded corners
            except tk.TclError:
                pass
            win.configure(bg=_TRANSPARENT)
            canvas = tk.Canvas(win, bg=_TRANSPARENT, highlightthickness=0, bd=0)
            canvas.pack(fill="both", expand=True)

            content = tk.Frame(
                canvas, bg=C.surface
            )  # placed on the canvas after sizing
            ui.label(
                content,
                "Shape your prompt",
                size=13,
                semibold=True,
                color=C.gold_hi,
                bg=C.surface,
            ).pack(anchor="w")
            ui.label(
                content,
                "Pick one per question — or Skip to use defaults.",
                size=9,
                color=C.text_mute,
                bg=C.surface,
            ).pack(anchor="w", pady=(0, 4))

            answers = [None] * len(questions)
            rows = []

            def select(qi, oi, opt):
                answers[qi] = opt
                for j, b in enumerate(rows[qi]):
                    b.set_selected(j == oi)

            for qi, (qtext, options) in enumerate(questions):
                ui.label(
                    content,
                    f"{qi + 1}. {qtext}",
                    size=10,
                    semibold=True,
                    color=C.text,
                    bg=C.surface,
                    wrap=INNER_W,
                ).pack(anchor="w", pady=(10, 3))
                row = []
                for oi, opt in enumerate(options):
                    ob = _OptionButton(
                        content,
                        f"{chr(65 + oi)})  {opt}",
                        INNER_W,
                        lambda qi=qi, oi=oi, opt=opt: select(qi, oi, opt),
                        bg=C.surface,
                    )
                    ob.pack(anchor="w", pady=2)
                    row.append(ob)
                rows.append(row)

            foot = tk.Frame(content, bg=C.surface)
            foot.pack(fill="x", pady=(14, 0))
            ui.RoundButton(
                foot,
                "Skip",
                lambda: finish(None, win),
                kind="subtle",
                bg=C.surface,
                height=30,
                font_size=10,
            ).pack(side="left")
            ui.RoundButton(
                foot,
                "Create prompt  →",
                lambda: finish([a for a in answers if a], win),
                kind="primary",
                bg=C.surface,
                height=32,
                font_size=10,
            ).pack(side="right")

            # Size the window to the content, then draw the rounded background.
            content.update_idletasks()
            cw = max(INNER_W + PAD * 2, content.winfo_reqwidth() + PAD * 2)
            ch = content.winfo_reqheight() + PAD * 2
            canvas.configure(width=cw, height=ch)
            ui.round_rect(
                canvas, 1, 1, cw - 1, ch - 1, 16, fill=C.surface, outline=_PILL_RIM
            )
            canvas.create_window(PAD, PAD, anchor="nw", window=content)

            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            x = max(0, (sw - cw) // 2)
            y = max(0, sh - ch - 70)

            def place(alpha, dy):
                try:
                    win.attributes("-alpha", alpha)
                except Exception:
                    pass
                win.geometry(f"{cw}x{ch}+{x}+{int(y + dy)}")

            if animate:
                # gentle fade + rise, ease-out-cubic, ~280ms — no content reflow
                steps = 14

                def step(i):
                    t = i / steps
                    e = 1 - (1 - t) ** 3
                    place(0.97 * e, (1 - e) * 18)
                    if i < steps:
                        win.after(20, lambda: step(i + 1))
                    else:
                        place(0.97, 0)

                place(0.0, 18)
                step(1)
            else:
                place(0.97, 0)
            win.lift()
        except Exception as e:
            print("ask_questions panel error:", e)
            finish(None, win)

    # ---- Context Island (the unified context UI — Changes 1/2/5) ----
    def show_deck(self, items, on_complete=None, on_fav=None):
        """The History deck — Mumble's mini dashboard (default ctrl+alt+d). ONE surface unifying:
          • the paste picker — transcripts + clipboard + prompts, searchable
          • favourites — starred items pinned at the top (click ★ to toggle)
          • the preset panel — every intent, to run an AI job ON checked items
          • the MODE chips — Prompt/Email/List/Reply/Foreign as click triggers
        Two flows, one window:
          QUICK PASTE — no preset/mode picked: Enter or click pastes the row.
          AI JOB — pick a preset and/or mode (the Deck enters job mode): clicks
          now CHECK items as material; Go (or Enter) runs the job on them.
        Ctrl+click always toggles a check; Ctrl+Space toggles at the cursor.
        Esc / ✕ cancels: nothing pastes, NO AI call.

        items: [{"source","time","text","mode"?,"own"?,"fav"?,"image_path"?,
                 "size"?}] newest-first; favourites flagged fav=True by the host.
        on_complete(result):
          None → cancelled
          {"action":"paste","item":it}
          {"action":"job","items":[...],"intent":str|None,
           "intent_title":str|None,"mode":str|None}
          {"action":"open_preset_adder"}
        on_fav(item, new_state): host persists a star toggle (hub stays open).
        Runs on the Tk main thread. Fully guarded."""
        import presets as _presets

        CTX = MODE_COLORS.get("context", "#7ABFB0")
        LIST_W, ROW_H, LIST_H, PAD = 560, 40, 430, 12
        INTENT_W = 300
        MAX_ROWS = 60  # rendered rows cap — search narrows; huge histories stay fast
        done = {"v": False}
        win = None

        def finish(result, _w=None):
            if done["v"]:
                return
            done["v"] = True
            self.deck_open = False
            self._deck_api = None
            self._deck_close = None
            try:
                if win is not None:
                    win.destroy()
            except Exception:
                pass
            try:
                if on_complete:
                    on_complete(result)
            except Exception:
                pass

        try:
            self.deck_open = True
            self._deck_close = lambda: finish(None)  # for close_deck() / the toggle

            win = tk.Toplevel(self.root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.configure(bg=C.bg)
            outer = tk.Frame(win, bg=C.bg, highlightthickness=2,
                             highlightbackground=C.gold)
            outer.pack(fill="both", expand=True)
            content = tk.Frame(outer, bg=C.bg, padx=PAD, pady=PAD)
            content.pack(fill="both", expand=True)

            # ---- header: title + dynamic hint + ✕ ----
            head = tk.Frame(content, bg=C.bg)
            head.pack(fill="x")
            ui.label(head, "History", size=13, semibold=True, color=C.gold,
                     bg=C.bg).pack(side="left")
            x_btn = ui.label(head, "  ✕  ", size=12, color=C.text_dim, bg=C.bg)
            x_btn.pack(side="right")
            x_btn.configure(cursor="hand2")
            x_btn.bind("<Button-1>", lambda e: finish(None))
            hint_lbl = ui.label(head, "", size=8, color=C.text_mute, bg=C.bg)
            hint_lbl.pack(side="right", padx=(0, 6))

            body = tk.Frame(content, bg=C.bg)
            body.pack(fill="both", expand=True, pady=(8, 0))
            left = tk.Frame(body, bg=C.bg)
            left.pack(side="left", fill="both", expand=True)
            right = tk.Frame(body, bg=C.bg)
            right.pack(side="right", fill="y", padx=(10, 0))

            # ---- left: search + unified list (★ favourites, then recents) ----
            qvar = tk.StringVar(value="")
            entry = tk.Entry(left, textvariable=qvar, bg=C.surface2,
                             fg=C.text, insertbackground=C.gold,
                             relief="flat", font=(FONT, 11))
            entry.pack(fill="x", pady=(0, 8), ipady=6)

            holder = tk.Frame(left, bg=C.bg)
            holder.pack(fill="both", expand=True)
            cv = tk.Canvas(holder, width=LIST_W, height=LIST_H, bg=C.bg,
                           highlightthickness=0, bd=0)
            sb = tk.Scrollbar(holder, orient="vertical", command=cv.yview,
                              width=8)
            cv.configure(yscrollcommand=sb.set)
            inner = tk.Frame(cv, bg=C.bg)
            cv.create_window(0, 0, anchor="nw", window=inner)
            inner.bind("<Configure>",
                       lambda e: cv.configure(scrollregion=cv.bbox("all")))

            def wheel(e):
                cv.yview_scroll(-1 if e.delta > 0 else 1, "units")
                return "break"

            for w in (cv, inner):
                w.bind("<MouseWheel>", wheel)
            cv.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            count_lbl = ui.label(left, "", size=8, color=C.text_mute, bg=C.bg)
            count_lbl.pack(anchor="w", pady=(6, 0))

            # ---- right: preset (intent) panel + MODE chips + Go ----
            ui.label(right, "PRESETS — run on selected items", size=9,
                     semibold=True, color=C.gold_hi, bg=C.bg).pack(anchor="w")
            iholder = tk.Frame(right, bg=C.bg)
            iholder.pack(pady=(4, 6))
            icv = tk.Canvas(iholder, width=INTENT_W, height=LIST_H - 130,
                            bg=C.bg, highlightthickness=0, bd=0)
            isb = tk.Scrollbar(iholder, orient="vertical", command=icv.yview,
                               width=8)
            icv.configure(yscrollcommand=isb.set)
            iinner = tk.Frame(icv, bg=C.bg)
            icv.create_window(0, 0, anchor="nw", window=iinner)
            iinner.bind("<Configure>",
                        lambda e: icv.configure(scrollregion=icv.bbox("all")))

            def iwheel(e):
                icv.yview_scroll(-1 if e.delta > 0 else 1, "units")
                return "break"

            for w in (icv, iinner):
                w.bind("<MouseWheel>", iwheel)
            icv.pack(side="left", fill="both")
            isb.pack(side="right", fill="y")

            intent = {"slot": None, "title": None, "instruction": None}
            mode_sel = {"key": None}
            state = {"visible": [], "rows": [], "sel": 0, "checked": set()}

            def job_mode():
                return bool(intent["instruction"] or mode_sel["key"])

            def _hint():
                if job_mode():
                    n = len(state["checked"])
                    hint_lbl.configure(
                        text=f"JOB: click items to select ({n} ✓) · "
                             "Enter/Go runs it · Esc close")
                else:
                    hint_lbl.configure(
                        text="↑↓ move · Enter/click paste · ★ favourite · "
                             "Esc close")

            # ---- intent buttons (title + one-line description) ----
            ibtns = {}

            def _draw_intent(c, slot, title, desc, active, empty):
                c.delete("all")
                w = int(c.winfo_reqwidth())
                if empty:
                    fill, fg, sub = C.surface, C.text_mute, C.text_mute
                else:
                    fill = CTX if active else C.surface2
                    fg = C.bg if active else C.text
                    sub = C.bg if active else C.text_mute
                ui.round_rect(c, 0, 1, w, 43, 8, fill=fill, outline="")
                if desc:
                    c.create_text(10, 14, text=title[:30], anchor="w", fill=fg,
                                  font=(FONT_SB if active else FONT, 9))
                    c.create_text(10, 31, text=desc[:40], anchor="w", fill=sub,
                                  font=(FONT, 7))
                else:
                    c.create_text(w // 2, 22, text=title[:30], fill=fg,
                                  font=(FONT_SB if active else FONT, 9))

            for slot, title, desc, instruction in _presets.all_presets():
                c = tk.Canvas(iinner, width=INTENT_W - 14, height=46, bg=C.bg,
                              highlightthickness=0, bd=0, cursor="hand2")
                c.pack(pady=1)
                c.bind("<MouseWheel>", iwheel)
                ibtns[slot] = (c, title, desc, instruction)

                def pick_intent(e=None, s=slot, t=title, ins=instruction):
                    if ins is None:
                        finish({"action": "open_preset_adder"})
                        return
                    if intent["slot"] == s:  # click again to deselect
                        intent.update(slot=None, title=None, instruction=None)
                    else:
                        intent.update(slot=s, title=t, instruction=ins)
                    for s2, (c2, t2, d2, i2) in ibtns.items():
                        _draw_intent(c2, s2, t2, d2, intent["slot"] == s2,
                                     i2 is None)
                    _hint()

                c.bind("<Button-1>", pick_intent)
                _draw_intent(c, slot, title, desc, False, instruction is None)

            # ---- MODE chips (manual mode triggers / output form) ----
            ui.label(right, "MODE — shape the output", size=9, semibold=True,
                     color=C.gold_hi, bg=C.bg).pack(anchor="w", pady=(4, 2))
            mrow = tk.Frame(right, bg=C.bg)
            mrow.pack(anchor="w")
            mode_btns = {}

            def _draw_mode(c, mkey, active):
                c.delete("all")
                col = MODE_COLORS.get(mkey, C.gold)
                fill = col if active else C.surface2
                fg = C.bg if active else col
                ui.round_rect(c, 0, 1, 56, 25, 8, fill=fill, outline="")
                c.create_text(28, 13, text=MODE_LABELS.get(mkey, mkey)[:7],
                              fill=fg, font=(FONT_SB if active else FONT, 8))

            for mkey in ("prompt", "email", "foreign"):
                c = tk.Canvas(mrow, width=58, height=27, bg=C.bg,
                              highlightthickness=0, bd=0, cursor="hand2")
                c.pack(side="left", padx=(0, 3))
                mode_btns[mkey] = c

                def pick_mode(e=None, k=mkey):
                    mode_sel["key"] = None if mode_sel["key"] == k else k
                    for k2, c2 in mode_btns.items():
                        _draw_mode(c2, k2, mode_sel["key"] == k2)
                    _hint()

                c.bind("<Button-1>", pick_mode)
                _draw_mode(c, mkey, False)

            # ---- Go + footer ----
            foot = tk.Frame(right, bg=C.bg)
            foot.pack(fill="x", pady=(10, 0))
            warn = ui.label(foot, "", size=8, color=C.gold_hi, bg=C.bg)
            warn.pack(side="left")

            def go(_e=None):
                if done["v"]:
                    return "break"
                if not job_mode():
                    warn.configure(text="Pick a preset or mode first")
                    return "break"
                chosen = [it for it in state["visible"]
                          if id(it) in state["checked"]]
                if not chosen and 0 <= state["sel"] < len(state["visible"]):
                    chosen = [state["visible"][state["sel"]]]
                chosen = [it for it in chosen if not it.get("image_path")]
                if not chosen:
                    warn.configure(text="Select at least one text item")
                    return "break"
                finish({"action": "job", "items": chosen,
                        "intent": intent["instruction"],
                        "intent_title": intent["title"],
                        "mode": mode_sel["key"]})
                return "break"

            ui.RoundButton(foot, "Go", go, kind="primary", bg=C.bg,
                           height=30, font_size=11, padx=24).pack(side="right")

            # ---- unified list rendering ----
            def _badge(it):
                if it.get("image_path"):
                    return "IMG", "#3B82F6"
                src = it.get("source", "")
                if src == "transcript":
                    m = it.get("mode", "text")
                    return (MODE_LABELS.get(m, m)[:6].upper(),
                            MODE_COLORS.get(m, C.gold))
                if src == "prompt":
                    return "PROMPT", MODE_COLORS.get("prompt", C.gold)
                return "CLIP", CTX

            def _preview(it):
                if it.get("image_path"):
                    return f"[Image {it.get('size', '')}]".replace(" ]", "]")
                return " ".join((it.get("text") or "").split())[:80]

            def _draw_row(c, it, cursor, checked):
                c.delete("all")
                w = int(c.winfo_reqwidth())
                fill = C.gold if cursor else (C.elevated if checked
                                              else C.surface2)
                fg = C.bg if cursor else C.text
                sub = C.bg if cursor else C.text_mute
                ui.round_rect(c, 0, 1, w - 4, ROW_H - 3, 8, fill=fill,
                              outline=CTX if checked else "")
                mark = "✓ " if checked else ""
                btxt, bcol = _badge(it)
                c.create_text(8, ROW_H // 2 - 1, text=f"{mark}{btxt}",
                              anchor="w", fill=(C.bg if cursor else bcol),
                              font=(FONT_SB, 7))
                own = " (Mumble)" if it.get("own") else ""
                c.create_text(64, ROW_H // 2 - 9,
                              text=f"{(it.get('time') or '')[-8:]}{own}",
                              anchor="w", fill=sub, font=(FONT, 7))
                c.create_text(64, ROW_H // 2 + 7, text=_preview(it),
                              anchor="w", fill=fg, font=(FONT, 9))
                star = "★" if it.get("fav") else "☆"
                c.create_text(w - 16, ROW_H // 2,
                              text=star,
                              fill=(C.bg if cursor else
                                    (C.gold if it.get("fav") else C.text_mute)),
                              font=(FONT, 11))

            def _select(i):
                state["sel"] = max(0, min(i, len(state["visible"]) - 1))
                _repaint()
                if state["rows"]:
                    frac = state["sel"] / max(1, len(state["rows"]))
                    top, bot = cv.yview()
                    page = max(0.01, bot - top)
                    if frac < top or frac > bot - 0.4 * page:
                        cv.yview_moveto(max(0.0, frac - page / 2))

            def _repaint():
                for j, (c2, it2) in enumerate(state["rows"]):
                    _draw_row(c2, it2, j == state["sel"],
                              id(it2) in state["checked"])

            def _toggle_check(i=None):
                i = state["sel"] if i is None else i
                if 0 <= i < len(state["visible"]):
                    it = state["visible"][i]
                    if it.get("image_path"):
                        warn.configure(text="Images can't be job material")
                        return "break"
                    k = id(it)
                    if k in state["checked"]:
                        state["checked"].discard(k)
                    else:
                        state["checked"].add(k)
                    _repaint()
                    _hint()
                return "break"

            def _row_click(e, it, i):
                # The star zone always toggles favourite.
                c = state["rows"][i][0] if i < len(state["rows"]) else None
                w = int(c.winfo_reqwidth()) if c else LIST_W
                if e.x > w - 30:
                    new = not it.get("fav")
                    it["fav"] = new
                    try:
                        if on_fav:
                            on_fav(it, new)
                    except Exception:
                        pass
                    _render()
                    return
                state["sel"] = i
                if (e.state & 0x4) or job_mode():  # Ctrl held, or job mode
                    _toggle_check(i)
                else:
                    finish({"action": "paste", "item": it})

            def _render():
                q = (qvar.get() or "").strip().lower()

                def hit(it):
                    return (not q or q in (it.get("text") or "").lower()
                            or q in (it.get("source") or "")
                            or (it.get("image_path") and "image" in q))

                favs = [it for it in items if it.get("fav") and hit(it)]
                rest = [it for it in items if not it.get("fav") and hit(it)]
                hidden = max(0, len(rest) - MAX_ROWS)
                rest = rest[:MAX_ROWS]
                vis = favs + rest
                state["visible"] = vis
                state["checked"] = {k for k in state["checked"]
                                    if any(id(it) == k for it in vis)}
                for w in inner.winfo_children():
                    w.destroy()
                state["rows"] = []

                def header(text):
                    ui.label(inner, text, size=8, semibold=True,
                             color=C.gold_hi, bg=C.bg).pack(anchor="w",
                                                            pady=(4, 2))

                def add_rows(group):
                    for it in group:
                        i = len(state["rows"])
                        c = tk.Canvas(inner, width=LIST_W - 18, height=ROW_H,
                                      bg=C.bg, highlightthickness=0, bd=0,
                                      cursor="hand2")
                        c.pack(pady=1)
                        c.bind("<MouseWheel>", wheel)
                        c.bind("<Button-1>",
                               lambda e, it=it, i=i: _row_click(e, it, i))
                        state["rows"].append((c, it))

                if favs:
                    header("★ FAVOURITES")
                    add_rows(favs)
                    if rest:
                        header("RECENT")
                add_rows(rest)
                if not vis:
                    ui.label(inner, "Nothing matches.", size=10,
                             color=C.text_mute, bg=C.bg).pack(pady=20)
                state["sel"] = 0 if vis else -1
                _repaint()
                cv.yview_moveto(0.0)
                count_lbl.configure(
                    text=f"{len(vis)} items"
                         + (f" · {hidden} more — refine the search"
                            if hidden else ""))
                _hint()

            def _move(d):
                if state["visible"]:
                    _select(state["sel"] + d)
                return "break"

            def _confirm(_e=None):
                if job_mode():
                    return go()
                if 0 <= state["sel"] < len(state["visible"]):
                    finish({"action": "paste",
                            "item": state["visible"][state["sel"]]})
                return "break"

            qvar.trace_add("write", lambda *a: _render())
            for w in (win, entry):
                w.bind("<Up>", lambda e: _move(-1))
                w.bind("<Down>", lambda e: _move(+1))
                w.bind("<Return>", _confirm)
                w.bind("<Escape>", lambda e: finish(None))
                w.bind("<Control-space>", lambda e: _toggle_check())

            _render()

            # ---- place centred, focus the search box ----
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            win.update_idletasks()
            ww, wh = win.winfo_reqwidth(), win.winfo_reqheight()
            win.geometry(f"+{max(0, (sw - ww) // 2)}+{max(0, (sh - wh) // 3)}")
            win.lift()
            win.focus_force()
            entry.focus_set()

            # Safety: never linger forever.
            win.after(180000, lambda: finish(None))

            # Test/automation hooks.
            self._deck_api = {
                "finish": finish, "render": _render, "move": _move,
                "confirm": _confirm, "go": go, "query": qvar, "state": state,
                "pick_intent": lambda slot: ibtns[slot][0].event_generate(
                    "<Button-1>"),
                "intent": intent, "mode_sel": mode_sel,
                "toggle_check": _toggle_check,
                "pick_mode": lambda k: mode_btns[k].event_generate(
                    "<Button-1>"),
            }
        except Exception as e:
            print("hub error:", e)
            finish(None)

    def show_correction_editor(self, original, on_complete, can_replace=True,
                               on_cancel=None, initial=None,
                               already_applied=False, preview_fn=None):
        """Open the island's explicit, local correction-learning editor.

        The user edits the exact text Mumble just pasted, then chooses whether to
        learn the changed phrases only or also replace the last paste. No ambient
        typing is observed. ``on_complete`` receives
        ``{"original", "corrected", "action"}``, where action is ``learn`` or
        ``replace`` (``copy`` on platforms where safe replacement is unavailable).
        """
        original = str(original or "").strip()
        initial = str(initial or original).strip()
        if not original:
            if on_cancel:
                on_cancel()
            return
        # Resolve any older editor before opening another; one captured paste must
        # have exactly one owner and callback.
        prior = getattr(self, "_correction_done", None)
        if prior is not None:
            try:
                prior(None)
            except Exception:
                pass

        finished = {"v": False}
        preview_after = {"id": None}
        win = None

        def preview(value, announce=True):
            if not callable(preview_fn):
                return None
            try:
                result = dict(preview_fn(original, value) or {})
            except Exception as e:
                result = {"ok": False, "message": str(e), "changes": []}
            if announce:
                changes = result.get("changes") or []
                if result.get("ok") and changes:
                    bits = [
                        f"{c.get('from', '')} → {c.get('to', '')}"
                        for c in changes[:3]
                    ]
                    suffix = " …" if len(changes) > 3 else ""
                    warn.configure(text="Will learn: " + " · ".join(bits) + suffix)
                elif value != original:
                    warn.configure(text=(result.get("message") or
                                          "No reusable word change found")[:110])
            return result

        def done(action):
            if finished["v"]:
                return
            if action:
                corrected = editor.get("1.0", "end-1c").strip()
                if not corrected:
                    warn.configure(text="The corrected text can't be empty")
                    return
                if corrected == original:
                    warn.configure(text="Change the word or phrase Mumble got wrong")
                    return
                checked = preview(corrected, announce=True)
                if checked is not None and (
                        not checked.get("ok") or not checked.get("changes")):
                    return
                result = {"original": original, "corrected": corrected,
                          "action": action}
            else:
                result = None
            finished["v"] = True
            self._correction_open = False
            self._correction_done = None
            self._correction_api = None
            try:
                if win is not None:
                    win.destroy()
            except Exception:
                pass
            try:
                if result is not None:
                    on_complete(result)
                elif on_cancel:
                    on_cancel()
            except Exception as e:
                print("correction editor callback failed:", e)

        try:
            self.clear_correction()
            self.state = "idle"
            self._hide_all()
            self._correction_open = True
            self._correction_done = done

            win = tk.Toplevel(self.root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.configure(bg=C.bg)
            outer = tk.Frame(win, bg=C.bg, highlightthickness=2,
                             highlightbackground=C.gold)
            outer.pack(fill="both", expand=True)
            content = tk.Frame(outer, bg=C.bg, padx=16, pady=14)
            content.pack(fill="both", expand=True)

            head = tk.Frame(content, bg=C.bg)
            head.pack(fill="x")
            title = ("Save this correction?" if already_applied
                     else "Teach Mumble a correction")
            ui.label(head, title, size=13, semibold=True,
                     color=C.gold_hi, bg=C.bg).pack(side="left")
            badge = ui.label(head, "  EXPERIMENTAL  ", size=7, semibold=True,
                             color=C.bg, bg=C.gold)
            badge.pack(side="left", padx=(9, 0), ipady=2)
            x_btn = ui.label(head, "  ✕  ", size=12, color=C.text_dim, bg=C.bg)
            x_btn.pack(side="right")
            x_btn.configure(cursor="hand2")
            x_btn.bind("<Button-1>", lambda _e: done(None))

            help_text = (
                "Mumble noticed a local edit in the field it just pasted into. "
                "Review it, then save only the changed word or short phrase."
                if already_applied else
                "Edit only what was misheard. Mumble will learn the changed "
                "word or short phrase locally — never the surrounding prose."
            )
            ui.label(
                content, help_text,
                size=9, color=C.text_mute, bg=C.bg, wrap=590,
            ).pack(anchor="w", pady=(3, 10))

            edit_wrap = tk.Frame(content, bg=C.surface2, highlightthickness=1,
                                 highlightbackground=C.border)
            edit_wrap.pack(fill="both", expand=True)
            rows = max(5, min(12, initial.count("\n") + 3 + len(initial) // 90))
            editor = tk.Text(
                edit_wrap, width=72, height=rows, wrap="word", undo=True,
                bg=C.surface2, fg=C.text, insertbackground=C.gold,
                selectbackground=C.gold, selectforeground=C.bg,
                relief="flat", bd=0, padx=11, pady=10,
                font=(FONT, 10), spacing1=2, spacing3=2,
            )
            scroll = tk.Scrollbar(edit_wrap, command=editor.yview,
                                  bg=C.surface2, troughcolor=C.bg,
                                  activebackground=C.elevated, relief="flat", bd=0)
            editor.configure(yscrollcommand=scroll.set)
            editor.pack(side="left", fill="both", expand=True)
            scroll.pack(side="right", fill="y")
            editor.insert("1.0", initial)

            meta = tk.Frame(content, bg=C.bg)
            meta.pack(fill="x", pady=(7, 0))
            warn = ui.label(meta, "", size=8, color="#F59E0B", bg=C.bg)
            warn.pack(side="left")
            count = ui.label(meta, f"{len(initial):,} characters", size=8,
                             color=C.text_mute, bg=C.bg)
            count.pack(side="right")

            def changed(_event=None):
                value = editor.get("1.0", "end-1c")
                count.configure(text=f"{len(value):,} characters")
                if warn.cget("text"):
                    warn.configure(text="")
                if preview_after["id"] is not None:
                    try:
                        win.after_cancel(preview_after["id"])
                    except Exception:
                        pass
                preview_after["id"] = win.after(
                    220, lambda: preview(editor.get("1.0", "end-1c").strip()))

            editor.bind("<<Modified>>", lambda e: (changed(e), editor.edit_modified(False)))
            editor.edit_modified(False)
            if initial != original:
                preview_after["id"] = win.after(80, lambda: preview(initial))

            foot = tk.Frame(content, bg=C.bg)
            foot.pack(fill="x", pady=(12, 0))
            ui.RoundButton(foot, "Cancel", lambda: done(None), kind="subtle",
                           bg=C.bg, height=31, font_size=9).pack(side="left")
            if not already_applied:
                ui.RoundButton(foot, "Learn only", lambda: done("learn"),
                               kind="subtle", bg=C.bg, height=31,
                               font_size=9).pack(side="right", padx=(8, 0))
            primary_action = ("learn" if already_applied else
                              "replace" if can_replace else "copy")
            primary_label = ("Learn correction" if already_applied else
                             "Learn & replace" if can_replace else "Learn & copy")
            ui.RoundButton(foot, primary_label, lambda: done(primary_action),
                           kind="primary", bg=C.bg, height=32,
                           font_size=10, padx=20).pack(side="right")

            win.bind("<Escape>", lambda _e: done(None))
            win.bind("<Control-Return>", lambda _e: done(primary_action))
            win.update_idletasks()
            ww, wh = win.winfo_reqwidth(), win.winfo_reqheight()
            rect = _monitor_rect()
            if rect:
                left, top, right, bottom = rect
                x = int(left + max(0, (right - left - ww) / 2))
                y = int(top + max(0, (bottom - top - wh) / 2))
            else:
                sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
                x, y = max(0, (sw - ww) // 2), max(0, (sh - wh) // 3)
            win.geometry(f"+{x}+{y}")
            win.lift()
            win.focus_force()
            editor.focus_set()
            # Ten-minute fail-safe for a forgotten always-on-top editor. It is long
            # enough for a substantial dictation and never learns on timeout.
            win.after(600000, lambda: done(None))
            self._correction_api = {"finish": done, "editor": editor,
                                    "original": original}
        except Exception as e:
            print("correction editor error:", e)
            done(None)

    def close_correction_editor(self):
        fn = getattr(self, "_correction_done", None)
        if fn is not None:
            fn(None)

    def show_mode_picker(self, preview, modes, suggested, on_pick, on_dismiss=None):
        """Interactive 'Which mode?' recovery popup (owner v6 — replaces the old
        'hold key + say' chip, which was conceptually wrong: it told the user to
        re-dictate instead of letting them recover).

        When Mumble can't confidently tell which mode you intended, it does NOT
        ask you to speak again. It shows your ORIGINAL words (preserved) and a row
        of clickable mode chips (the AI's best guess pre-highlighted). Click one —
        or press its number — and those exact words are processed in that mode.
        Close (Esc / ✕ / 15s) → the polished plain-text version is pasted instead,
        so your input is never lost. A small frameless, always-on-top overlay just
        above the island; it never alters your typed text.

        preview: one-line transcript preview. modes: ordered mode keys.
        suggested: a mode key to pre-highlight, or "". on_pick(mode) fires on a
        choice; on_dismiss() fires if closed without one. Exactly one fires."""
        picked = {"v": False}
        win = None

        def done(mode):
            if picked["v"]:
                return
            picked["v"] = True
            self._picker_open = False
            self._picker_done = None
            try:
                if win is not None:
                    win.destroy()
            except Exception:
                pass
            try:
                if mode:
                    on_pick(mode)
                elif on_dismiss:
                    on_dismiss()
            except Exception:
                pass

        try:
            self._picker_open = True
            self._picker_done = done
            win = tk.Toplevel(self.root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.configure(bg=C.bg)
            outer = tk.Frame(win, bg=C.bg, highlightthickness=2,
                             highlightbackground=_SUGGEST)
            outer.pack(fill="both", expand=True)
            content = tk.Frame(outer, bg=C.bg, padx=14, pady=12)
            content.pack(fill="both", expand=True)

            head = tk.Frame(content, bg=C.bg)
            head.pack(fill="x")
            ui.label(head, "Which mode?", size=13, semibold=True,
                     color=_SUGGEST, bg=C.bg).pack(side="left")
            x_btn = ui.label(head, "  ✕  ", size=12, color=C.text_dim, bg=C.bg)
            x_btn.pack(side="right")
            x_btn.configure(cursor="hand2")
            x_btn.bind("<Button-1>", lambda e: done(None))
            ui.label(content, "I couldn't tell — pick one and I'll use your words",
                     size=9, color=C.text_mute, bg=C.bg).pack(anchor="w",
                                                              pady=(2, 7))
            # one-line preview of the PRESERVED transcript (never lost)
            prev = (preview or "").strip().replace("\n", " ")
            if len(prev) > 88:
                prev = prev[:85] + "…"
            ui.label(content, f"  “{prev}”  ", size=10, color=C.text_dim,
                     bg=C.surface2).pack(fill="x", ipady=6, pady=(0, 9))

            chips = tk.Frame(content, bg=C.bg)
            chips.pack(fill="x")
            for i, k in enumerate(modes):
                color = MODE_COLORS.get(k, C.gold)
                lbl = MODE_LABELS.get(k, str(k).title())
                is_sug = (k == suggested)
                chip = tk.Frame(chips, bg=(C.elevated if is_sug else C.surface2),
                                highlightthickness=(2 if is_sug else 1),
                                highlightbackground=color,
                                highlightcolor=color, cursor="hand2")
                chip.pack(side="left", padx=(0, 7))
                num = ui.label(chip, f"{i + 1}", size=8, color=C.text_mute,
                               bg=(C.elevated if is_sug else C.surface2))
                num.pack(side="left", padx=(7, 2), pady=4)
                t = ui.label(chip, f"{lbl} ", size=11, semibold=is_sug,
                             color=color, bg=(C.elevated if is_sug else C.surface2),
                             cursor="hand2")
                t.pack(side="left", padx=(0, 7), pady=4)
                for w in (chip, num, t):
                    w.bind("<Button-1>", lambda e, kk=k: done(kk))
                if i < 9:
                    win.bind(str(i + 1), lambda e, kk=k: done(kk))
            win.bind("<Escape>", lambda e: done(None))

            win.update_idletasks()
            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
            ww, wh = win.winfo_reqwidth(), win.winfo_reqheight()
            x = max(0, (sw - ww) // 2)
            y = max(0, sh - wh - 92)   # just above where the island sits
            win.geometry(f"+{x}+{y}")
            win.lift()
            win.focus_force()
            # Auto-resolve to the plain-text default after ~8s so nothing is lost
            # if the user walks away (owner v9: trimmed from 15s — still ample time
            # to read the five chips and click, without lingering "too long").
            win.after(8000, lambda: done(None))
        except Exception as e:
            print("mode picker error:", e)
            done(None)

    def close_picker(self):
        """Dismiss the mode picker if open (defaults to the plain-text fallback)."""
        fn = getattr(self, "_picker_done", None)
        if fn is not None:
            try:
                fn(None)
            except Exception:
                pass

def _hex(h):
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def _blend(c1, c2, t):
    # Clamp the interpolation factor: t outside [0,1] extrapolates channels past
    # 0/255, which "%02x" turns into a malformed (>2-digit) hex string that Tk
    # rejects — a whole class of "invalid color name" crashes. A blend factor is
    # always meant to be in [0,1], so clamping only ever prevents a bug.
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    a, b = _hex(c1), _hex(c2)
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
