#!/usr/bin/env python3
"""Mumble's tiny dark UI toolkit -- consistent, modern, premium widgets so the
app window and island share one design language. Pure tkinter (no extra deps)."""

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from branding import C, FONT, FONT_SB


def _lighten(hex_color, t=0.22):
    """Blend a hex colour toward white by fraction t (for subtle highlights)."""
    try:
        h = hex_color.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        r = int(r + (255 - r) * t)
        g = int(g + (255 - g) * t)
        b = int(b + (255 - b) * t)
        return "#%02x%02x%02x" % (r, g, b)
    except Exception:
        return hex_color


def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


def measure(font_tuple, text):
    if text is None:
        text = ""
    try:
        return tkfont.Font(family=font_tuple[0], size=font_tuple[1]).measure(text)
    except Exception:
        return int(len(text) * font_tuple[1] * 0.62)


def _safe_bg(parent, fallback=C.bg):
    """Get parent bg color safely — ttk widgets don't support direct key access."""
    try:
        return parent["bg"]
    except (tk.TclError, KeyError):
        try:
            return parent.cget("bg")
        except (tk.TclError, AttributeError):
            return fallback


def label(parent, text, size=11, color=C.text, bg=None, semibold=False,
          wrap=None, justify="left", **kw):
    f = (FONT_SB if semibold else FONT, size)
    lbl = tk.Label(parent, text=text, font=f, fg=color,
                   bg=bg or _safe_bg(parent), justify=justify, anchor="w", **kw)
    if wrap:
        lbl.configure(wraplength=wrap)
    return lbl


class RoundButton(tk.Canvas):
    def __init__(self, parent, text, command=None, kind="primary", height=36,
                 padx=20, bg=None, font_size=11, min_width=0):
        self.bg_outer = bg or _safe_bg(parent)  # ttk parents raise on ["bg"]
        self.kind, self.command, self.text = kind, command, text
        self.font = (FONT_SB, font_size)
        self._set_colors()
        w = max(min_width, measure(self.font, text) + padx * 2)
        super().__init__(parent, width=w, height=height, bg=self.bg_outer,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.w, self.h = w, height
        self._render(self.fill)
        self.bind("<Enter>", lambda e: self._render(self.hover))
        self.bind("<Leave>", lambda e: self._render(self.fill))
        self.bind("<Button-1>", lambda e: self.command and self.command())

    def _set_colors(self):
        if self.kind == "primary":
            self.fill, self.hover, self.fg = C.accent, C.accent_hi, "#0E0B02"
        elif self.kind == "ghost":
            self.fill, self.hover, self.fg = C.surface2, C.elevated, C.text
        elif self.kind == "danger":
            self.fill, self.hover, self.fg = "#3A1D20", "#4C2429", C.red
        else:  # subtle / link
            self.fill, self.hover, self.fg = self.bg_outer, C.surface2, C.text_dim

    def _render(self, fill):
        self.delete("all")
        rad = self.h // 2
        round_rect(self, 1, 1, self.w - 1, self.h - 1, rad,
                   fill=fill, outline="")
        # Subtle top highlight — a 1px line lightened toward white, inset from the
        # rounded ends. Reads as a soft light source; skipped for flat/subtle buttons.
        if self.kind in ("primary", "ghost", "danger"):
            self.create_line(rad, 2, self.w - rad, 2,
                             fill=_lighten(fill, 0.30), width=1)
        self.create_text(self.w // 2, self.h // 2, text=self.text,
                         fill=self.fg, font=self.font)

    def set_text(self, text):
        self.text = text
        self._render(self.fill)


class Switch(tk.Canvas):
    def __init__(self, parent, value=False, command=None, bg=None):
        self.bg_outer = bg or _safe_bg(parent)  # ttk parents raise on ["bg"]
        self.value, self.command = bool(value), command
        super().__init__(parent, width=46, height=26, bg=self.bg_outer,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._render()
        self.bind("<Button-1>", self._toggle)

    def _render(self):
        self.delete("all")
        track = C.accent if self.value else C.track
        round_rect(self, 2, 5, 44, 21, 8, fill=track, outline="")
        kx = 34 if self.value else 12
        self.create_oval(kx - 8, 4, kx + 8, 22, fill="#FFFFFF", outline="")

    def _toggle(self, _e):
        self.value = not self.value
        self._render()
        if self.command:
            self.command(self.value)

    def set(self, v):
        self.value = bool(v)
        self._render()


def card(parent, bg=C.surface, pad=20):
    f = tk.Frame(parent, bg=bg, highlightbackground=C.border,
                 highlightthickness=1, bd=0)
    f.pad = pad
    return f


def separator(parent, bg=None):
    return tk.Frame(parent, bg=C.border, height=1, bd=0)


def apply_theme(root):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("Mumble.TCombobox", fieldbackground=C.surface2,
                    background=C.surface2, foreground=C.text,
                    arrowcolor=C.text_dim, bordercolor=C.border,
                    lightcolor=C.border, darkcolor=C.border, relief="flat",
                    padding=6)
    style.map("Mumble.TCombobox",
              fieldbackground=[("readonly", C.surface2)],
              foreground=[("readonly", C.text)],
              selectbackground=[("readonly", C.surface2)],
              selectforeground=[("readonly", C.text)])
    style.configure("Mumble.Vertical.TScrollbar", background=C.surface2,
                    troughcolor=C.bg, bordercolor=C.bg, arrowcolor=C.text_mute,
                    relief="flat")
    root.option_add("*TCombobox*Listbox.background", C.surface2)
    root.option_add("*TCombobox*Listbox.foreground", C.text)
    root.option_add("*TCombobox*Listbox.selectBackground", C.accent)
    root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
    root.option_add("*TCombobox*Listbox.font", (FONT, 10))


def dark_titlebar(win):
    """Make a window's title bar dark (Win 10/11)."""
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (20, older 19)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass
