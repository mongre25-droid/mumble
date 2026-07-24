#!/usr/bin/env python3
"""Mumble's main window — Golden Black, top-nav, rounded "bubble" UI.

Logo + pill tabs across the top; soft rounded cards below. Sections: Home, Modes,
History (Transcripts + Clipboard, incl. image thumbnails), Stats, Settings (incl.
Pro Mode + Updates), Share, About. The controller (mumble.Mumble) supplies data.
"""

import math
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk

import branding
import ui
from branding import (
    FONT,
    FONT_SB,
    MODE_COLORS,
    MODE_LABELS,
    STATE_COLORS,
    C,
)
from PIL import Image, ImageDraw, ImageTk

NAV = [
    ("home", "Home"),
    ("history", "History"),
    ("stats", "Stats"),
    ("settings", "Settings"),
]


class ScrollArea(tk.Frame):
    def __init__(self, parent, bg=C.bg):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(
            self,
            orient="vertical",
            style="Mumble.Vertical.TScrollbar",
            command=self.canvas.yview,
        )
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=bg)
        self._item = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>", lambda e: self.canvas.itemconfig(self._item, width=e.width)
        )
        # NOTE: the mouse wheel is NOT bound here. The old "<Enter> -> bind_all" idiom
        # silently failed on boot — if the pointer was already inside the window (or never
        # crossed the canvas border) the <Enter> never fired, so the wheel did nothing until
        # the user manually grabbed the scrollbar. AppWindow now installs ONE persistent
        # global wheel dispatcher (see AppWindow._on_wheel) that routes to whichever
        # ScrollArea is under the pointer, so scrolling works from the first frame.

    def wheel(self, e):
        first, last = self.vsb.get()
        if first <= 0.0 and last >= 1.0:
            return  # nothing to scroll — already fully visible
        self.canvas.yview_scroll(int(-e.delta / 120), "units")


class RoundCard:
    """A soft rounded 'bubble' panel. Add children to `.body`."""

    def __init__(
        self, parent, bg=C.surface, radius=18, padx=20, pady=16, outer_pady=(0, 12)
    ):
        self.bg, self.radius, self.padx, self.pady = bg, radius, padx, pady
        self.outer_bg = parent["bg"]
        self.canvas = tk.Canvas(
            parent, bg=self.outer_bg, highlightthickness=0, bd=0, height=1
        )
        self.canvas.pack(fill="x", pady=outer_pady)
        self.body = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window(padx, pady, window=self.body, anchor="nw")
        self._h = 0
        self.body.bind("<Configure>", self._redraw)
        self.canvas.bind("<Configure>", self._redraw)

    def _redraw(self, _e=None):
        if not self.canvas.winfo_exists():
            return
        cw = self.canvas.winfo_width()
        if cw <= 1:
            return
        if not self.body.winfo_exists():
            return
        self.canvas.itemconfigure(self._win, width=cw - 2 * self.padx)
        ch = self.body.winfo_reqheight() + 2 * self.pady
        if ch != self._h:
            self._h = ch
            self.canvas.configure(height=ch)
        self.canvas.delete("bg")
        ui.round_rect(
            self.canvas,
            1,
            1,
            cw - 1,
            ch - 1,
            self.radius,
            fill=self.bg,
            outline=C.border_soft,
            tags="bg",
        )
        # Faint top hairline highlight — the "subtle white" lift, inset from the corners.
        self.canvas.create_line(
            self.radius,
            2,
            cw - self.radius,
            2,
            fill=ui._lighten(self.bg, 0.10),
            width=1,
            tags="bg",
        )
        self.canvas.tag_lower("bg")


class TabPill(tk.Canvas):
    def __init__(self, parent, text, key, on_click, bg):
        self.font = (FONT_SB, 11)
        w = ui.measure(self.font, text) + 30
        super().__init__(
            parent,
            width=w,
            height=32,
            bg=bg,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.text, self.key, self.on_click, self.w = text, key, on_click, w
        self.active = False
        self._draw()
        self.bind("<Button-1>", lambda e: self.on_click(self.key))
        self.bind("<Enter>", lambda e: self._draw(hover=True))
        self.bind("<Leave>", lambda e: self._draw())

    def _draw(self, hover=False):
        self.delete("all")
        if self.active:
            ui.round_rect(self, 1, 1, self.w - 1, 31, 15, fill=C.gold, outline="")
            fg = "#0E0B02"
        else:
            if hover:
                ui.round_rect(
                    self, 1, 1, self.w - 1, 31, 15, fill=C.surface2, outline=""
                )
            fg = C.text if hover else C.text_dim
        self.create_text(self.w // 2, 16, text=self.text, fill=fg, font=self.font)

    def set_active(self, a):
        self.active = a
        self._draw()


def _entry(parent, textvar=None, width=30):
    return tk.Entry(
        parent,
        textvariable=textvar,
        bg=C.surface2,
        fg=C.text,
        insertbackground=C.gold,
        relief="flat",
        font=(FONT, 11),
        width=width,
        highlightthickness=1,
        highlightbackground=C.border,
        highlightcolor=C.gold,
    )


class AppWindow:
    def __init__(self, root, controller):
        self.root = root
        self.ctrl = controller
        self.settings = controller.settings
        self.visible = False
        self._status_job = None
        self._img_refs = []
        self._cur_history_tab = "transcripts"
        self._built = (
            set()
        )  # sections built lazily once; switching after = instant tkraise
        self._cur_section = "home"
        self._history_dirty = (
            False  # set when a transcript lands while History isn't shown
        )

        self.win = tk.Toplevel(root)
        self.win.title("Mumble")
        self.win.configure(bg=C.bg)
        try:
            self.win.iconbitmap(branding.ICON_ICO)
        except Exception:
            pass
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        self.sections, self.nav_items = {}, {}
        self._build()
        self._apply_geometry()
        ui.dark_titlebar(self.win)
        self.win.withdraw()

    def _apply_geometry(self):
        # Height +5% of width (owner: more vertical room; 740 → 774, matching the
        # web window's taller default).
        w, h = 680, 774
        try:
            self.win.wm_state("normal")
            self.win.minsize(560, 520)
            sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
            self.win.geometry(
                f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}"
            )
        except Exception:
            pass

    # ---------------------------------------------------------- brand + status
    def _draw_logo(self, cv, phase):
        cv.delete("all")
        base = [0.4, 0.66, 1.0, 0.6, 0.44]
        for i, b in enumerate(base):
            wob = 0.22 * math.sin(phase * 0.16 + i * 0.7)
            hh = max(0.18, min(1.0, b + wob))
            x = 3 + i * 6.2 + 1.3
            half = 9 * hh
            cv.create_line(
                x, 13 - half, x, 13 + half, width=2.6, fill=C.gold_hi, capstyle="round"
            )

    def _animate_logo(self):
        if not self.visible:
            return
        self._logo_phase = getattr(self, "_logo_phase", 0) + 1
        try:
            self._draw_logo(self.logo_canvas, self._logo_phase)
            self._logo_job = self.win.after(90, self._animate_logo)
        except Exception:
            pass

    def _dot(self, color, size=11):
        cache = getattr(self, "_dot_cache", None)
        if cache is None:
            cache = self._dot_cache = {}
        key = (color, size)
        if key not in cache:
            s = size * 4
            img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
            ImageDraw.Draw(img).ellipse([1, 1, s - 1, s - 1], fill=color)
            cache[key] = ImageTk.PhotoImage(img.resize((size, size), Image.LANCZOS))
        return cache[key]

    def _build(self):
        # ---- centred brand (animated mic) ----
        top = tk.Frame(self.win, bg=C.bg)
        top.pack(fill="x", pady=(16, 2))
        brand = tk.Frame(top, bg=C.bg)
        brand.pack()
        self.logo_canvas = tk.Canvas(
            brand, width=30, height=26, bg=C.bg, highlightthickness=0
        )
        self._draw_logo(self.logo_canvas, 0)
        self.logo_canvas.pack(side="left")
        ui.label(brand, "Mumble", size=16, semibold=True, bg=C.bg).pack(
            side="left", padx=(9, 0)
        )

        # ---- version + streak + status, top-right ----
        self.status_box = tk.Frame(self.win, bg=C.bg)
        self.status_box.place(relx=1.0, y=18, x=-18, anchor="ne")
        self.streak_lbl = ui.label(
            self.status_box, "", size=9, color=C.gold_dim, bg=C.bg
        )
        self.streak_lbl.pack(side="left", padx=(0, 10))
        ui.label(
            self.status_box, "v" + branding.VERSION, size=9, color=C.text_mute, bg=C.bg
        ).pack(side="left", padx=(0, 10))
        self.status_dot = tk.Label(self.status_box, image=self._dot(C.gold), bg=C.bg)
        self.status_dot.pack(side="left")
        self.status_lbl = ui.label(
            self.status_box, "Ready", size=10, color=C.text_dim, bg=C.bg
        )
        self.status_lbl.pack(side="left", padx=(6, 0))

        # ---- centred nav ----
        navrow = tk.Frame(self.win, bg=C.bg)
        navrow.pack(pady=(10, 8))
        for key, text in NAV:
            pill = TabPill(navrow, text, key, self.show_section, C.bg)
            pill.pack(side="left", padx=3)
            self.nav_items[key] = pill

        self._nav_divider = tk.Frame(self.win, bg=C.border, height=1)
        self._nav_divider.pack(fill="x")

        self.container = tk.Frame(self.win, bg=C.bg)
        self.container.pack(fill="both", expand=True, padx=22, pady=(12, 12))
        for key, _ in NAV:
            if key == "stats":
                sec = tk.Frame(self.container, bg=C.bg)
            else:
                sec = ScrollArea(self.container, bg=C.bg)
            sec.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.sections[key] = sec

        self._build_home(self.sections["home"].body)
        self._build_settings(self.sections["settings"].body)
        self.show_section("home")

        # One persistent, app-wide wheel handler — works from boot, no <Enter> needed.
        # It walks up from the widget under the pointer to find the enclosing ScrollArea
        # and scrolls that one (so the visible tab scrolls wherever the cursor sits).
        self.win.bind_all("<MouseWheel>", self._on_wheel)

    def _on_wheel(self, e):
        try:
            w = self.win.winfo_containing(e.x_root, e.y_root)
        except Exception:
            return
        # bind_all is interpreter-global, so this also fires for other Toplevels
        # (History flyout, island) that share the Tk root — ignore anything that
        # isn't inside OUR window so we never hijack their wheel events.
        try:
            if w is None or w.winfo_toplevel() is not self.win:
                return
        except Exception:
            return
        while w is not None:
            if isinstance(w, ScrollArea):
                w.wheel(e)
                return "break"
            try:
                w = w.master
            except Exception:
                break

    def show_section(self, key):
        for k, item in self.nav_items.items():
            item.set_active(k == key)
        # Build each heavy tab's structure ONCE (lazily); after that a switch is just a
        # tkraise + a light data refresh — no destroying/recreating dozens of widgets,
        # which is what made tab-switching slow.
        if key == "history":
            if "history" not in self._built:
                self._build_history(self.sections["history"].body)
                self._built.add("history")
                self._history_dirty = False
            elif self._history_dirty:
                self._draw_history()  # only redraw if new data arrived
                self._history_dirty = False
            # else: already current — just tkraise below (instant)
        elif key == "stats":
            self._build_stats(self.sections["stats"])  # light (a few tiles)
            self._update_streak_label()
        self._cur_section = key
        self.sections[key].tkraise()

    # --------------------------------------------------------------- helpers
    def _card(self, parent):
        return RoundCard(parent).body

    def _chip(self, parent, text, bg=C.surface2):
        c = tk.Canvas(parent, height=26, bg=parent["bg"], highlightthickness=0)
        w = ui.measure((FONT, 10), text) + 22
        c.configure(width=w)
        ui.round_rect(c, 1, 1, w - 1, 25, 13, fill=bg, outline="")
        c.create_text(w // 2, 13, text=text, fill=C.text, font=(FONT, 10))
        return c

    def _hotkey_pretty(self, hk):
        return " + ".join(
            p.strip().capitalize().replace("Windows", "Win") for p in hk.split("+")
        )

    # ------------------------------------------------------------------ Home
    def _build_home(self, b):
        # 1 — Hero + 3 steps
        hero = self._card(b)
        ui.label(
            hero,
            "Speak. It types. Anywhere.",
            size=18,
            semibold=True,
            color=C.gold_hi,
            bg=C.surface,
        ).pack(anchor="center")
        ui.label(
            hero,
            "Mumble turns your voice into clean, polished text in any app — your "
            "audio is transcribed on your device, then Pro AI sharpens it.",
            size=11,
            color=C.text_dim,
            bg=C.surface,
            wrap=470,
            justify="center",
        ).pack(anchor="center", pady=(6, 12))
        steps = tk.Frame(hero, bg=C.surface)
        steps.pack(anchor="center")
        hk = self._hotkey_pretty(self.settings.get("hotkey", "ctrl+option+d"))
        for i, (n, t) in enumerate(
            [("1", "Press " + hk), ("2", "Speak"), ("3", "Press again — it pastes")]
        ):
            item = tk.Frame(steps, bg=C.surface)
            item.pack(side="left", padx=(0 if i == 0 else 16, 0))
            self._chip(item, n).pack(side="left")
            ui.label(item, t, size=10, color=C.text_dim, bg=C.surface).pack(
                side="left", padx=(7, 0)
            )

        # 2 — Shortcuts
        sc = self._card(b)
        ui.label(sc, "Shortcuts", size=13, semibold=True, bg=C.surface).pack(anchor="w")
        for keys, what in [
            (
                self._hotkey_pretty(self.settings.get("hotkey", "ctrl+option+d")),
                "Start / stop dictating",
            ),
            (
                self._hotkey_pretty(
                    self.settings.get("quick_paste_hotkey", "ctrl+option+v")
                ),
                "Re-paste your last result, anywhere",
            ),
        ]:
            row = tk.Frame(sc, bg=C.surface)
            row.pack(fill="x", pady=5)
            self._chip(row, keys, bg=C.surface2).pack(side="left")
            ui.label(row, what, size=11, color=C.text_dim, bg=C.surface).pack(
                side="left", padx=(12, 0)
            )
        ui.label(
            sc,
            "Change either one under Settings.",
            size=9,
            color=C.text_mute,
            bg=C.surface,
        ).pack(anchor="w", pady=(8, 0))

        # 3 — Smart Mode (flagship Prompt + Reply, then the rest)
        intro = self._card(b)
        ui.label(
            intro, "Smart Mode", size=15, semibold=True, color=C.gold_hi, bg=C.surface
        ).pack(anchor="w")
        ui.label(
            intro,
            "Ordinary dictation always produces clean text. Turn Prompt on from the "
            "island when you want prompt shaping; use the Deck for Email, Reply, "
            "Foreign and other deliberate transformations:",
            size=11,
            color=C.text_dim,
            bg=C.surface,
            wrap=520,
        ).pack(anchor="w", pady=(6, 0))

        self._flagship(
            b,
            "prompt",
            "Prompt",
            "Turn a rough, spoken idea into a precise, structured AI prompt — ready to paste "
            "into any AI.",
            "“prompt, build me a task manager for students with calendar integration”",
            "“You are an expert software developer. Create a task manager for students with "
            "calendar integration. Core features: add / edit / delete tasks, due dates shown "
            "on a calendar, reminders, and progress tracking…”",
            "Turn Prompt on from the island before dictating. Turn it off to return to "
            "ordinary clean text.",
        )

        self._flagship(
            b,
            "context",
            "History",
            "Press Ctrl + Option + H to open History — your recent transcripts, clipboard "
            "items, and saved prompts, all in one picker. (Saying “context” no longer "
            "opens anything; material-as-context lives in History.)",
            "Press  Ctrl + Option + H  — History opens",
            "Choose your source material, then pick what the AI should do with it from a "
            "panel of up to 20 intent presets: Summarise, Reply, De-AI, Fix errors, and more.",
            "Create your own presets in Settings → Preset Adder — they appear in History "
            "automatically.",
        )

        more = self._card(b)
        ui.label(more, "The everyday modes", size=12, semibold=True, bg=C.surface).pack(
            anchor="w"
        )
        for key, name, desc, example in [
            (
                "text",
                "Text",
                "Clean dictation — fixes punctuation and removes “um / uh” while "
                "keeping spoken command phrases literal.",
                "just start talking",
            ),
            (
                "email",
                "Email",
                "A tidy email with greeting, body and sign-off.",
                "“email Alex about the launch, we ship Friday”",
            ),
        ]:
            self._mode_mini(more, key, name, desc, example)

        # Explicit-mode explainer
        mb = self._card(b)
        ui.label(mb, "Prompt toggle", size=13, semibold=True, bg=C.surface).pack(
            anchor="w"
        )
        ui.label(
            mb,
            "Prompt is the one explicit dictation mode. Toggle it on from the island; "
            "all other transformations live in the Deck.",
            size=11,
            color=C.text_dim,
            bg=C.surface,
            wrap=520,
        ).pack(anchor="w", pady=(3, 8))
        for phrase, what in [
            ("Prompt toggle", "Builds a high-quality AI prompt from your next dictation."),
            ("Deck actions", "Transforms selected material into email, reply, lists and more."),
        ]:
            row = tk.Frame(mb, bg=C.surface)
            row.pack(fill="x", pady=3)
            self._chip(row, phrase).pack(side="left")
            ui.label(row, what, size=11, color=C.text_dim, bg=C.surface, wrap=380).pack(
                side="left", padx=(12, 0)
            )

        # Context chaining explainer
        cx = self._card(b)
        ui.label(cx, "Using context", size=13, semibold=True, bg=C.surface).pack(
            anchor="w"
        )
        for t in [
            "Highlight text in another app and use Capture in the Deck to make it the source.",
            "Press Ctrl + Option + H — History opens with your recent transcripts, "
            "clipboard items, and saved prompts to choose from.",
            "Pick an intent preset (Summarise, Reply, De-AI, …) to tell the AI what to "
            "do with the material — or pick nothing and it's loose background reference.",
        ]:
            row = tk.Frame(cx, bg=C.surface)
            row.pack(fill="x", pady=(6, 0))
            ui.label(row, "•", size=11, color=C.gold, bg=C.surface).pack(
                side="left", padx=(0, 8)
            )
            ui.label(row, t, size=11, color=C.text_dim, bg=C.surface, wrap=500).pack(
                side="left", anchor="w"
            )

        # Prompt Architect explainer — the owner's constitution powers every prompt
        pa = self._card(b)
        ui.label(
            pa,
            "✦ Prompt Architect",
            size=13,
            semibold=True,
            color=C.gold_hi,
            bg=C.surface,
        ).pack(anchor="w")
        ui.label(
            pa,
            "Every prompt you create is shaped by the Prompt Architect — a master "
            "constitution that transforms rough spoken thoughts into clear, structured, "
            "executable AI prompts. It extracts your real intent, surfaces implied "
            "requirements, removes noise, and adds success criteria — so what you get "
            "back is exactly what you meant, just far better organised.",
            size=10,
            color=C.text_dim,
            bg=C.surface,
            wrap=520,
        ).pack(anchor="w", pady=(3, 8))
        for t in [
            "Intent expansion, not scope expansion — it elaborates your meaning without inventing new projects.",
            "Converts complaints into requirements, finds hidden sub-tasks, and groups related ideas into logical sections.",
            "Adds operational instructions, success criteria, and priorities — so the AI knows when it's done.",
            "Preserves your voice, priorities, and project context — never rewrites you out of the result.",
        ]:
            row = tk.Frame(pa, bg=C.surface)
            row.pack(fill="x", pady=(4, 0))
            ui.label(row, "•", size=10, color=C.gold, bg=C.surface).pack(
                side="left", padx=(0, 8)
            )
            ui.label(row, t, size=10, color=C.text_dim, bg=C.surface, wrap=500).pack(
                side="left", anchor="w"
            )

        # Pro Mode + Cerebras key guide
        pro = self._card(b)
        ui.label(
            pro,
            " Pro Mode & Cerebras Key",
            size=13,
            semibold=True,
            color=C.gold_hi,
            bg=C.surface,
        ).pack(anchor="w")
        ui.label(
            pro,
            "Pro Mode is powered by Cerebras (gpt-oss-120b) — the fastest inference "
            "engine available. You need a free API key (no built-in key, nothing hard-coded). "
            "Without one, Mumble runs fully offline on your CPU.",
            size=10,
            color=C.text_dim,
            bg=C.surface,
            wrap=520,
        ).pack(anchor="w", pady=(3, 8))
        for step in [
            "1.  Open  cloud.cerebras.ai  in your browser.",
            "2.  Sign up (free — no credit card needed to start).",
            "3.  Go to API Keys → create a key.",
            "4.  Copy it, paste it into Settings → Pro Mode → Save & Test.",
        ]:
            row = tk.Frame(pro, bg=C.surface)
            row.pack(fill="x", pady=(2, 0))
            ui.label(row, step, size=10, color=C.text_dim, bg=C.surface).pack(
                side="left", anchor="w"
            )

        # Footer — good to know
        tip = self._card(b)
        ui.label(tip, "Good to know", size=13, semibold=True, bg=C.surface).pack(
            anchor="w"
        )
        for t in [
            "First run? Turn on cloud AI: Settings → Pro Mode has a 6-step guide to get "
            "a free Cerebras API key (takes ~2 minutes).",
            "Mumble runs in the background and starts with your computer — the golden mic in your tray.",
            "It remembers what you copy too — text and images — in History → Clipboard.",
            "Your audio is transcribed on your device; Pro Mode then polishes the text "
            "with cloud AI. Turn Pro off in Settings to keep everything on your device.",
        ]:
            row = tk.Frame(tip, bg=C.surface)
            row.pack(fill="x", pady=(7, 0))
            ui.label(row, "•", size=11, color=C.gold, bg=C.surface).pack(
                side="left", padx=(0, 8)
            )
            ui.label(row, t, size=11, color=C.text_dim, bg=C.surface, wrap=520).pack(
                side="left", anchor="w"
            )

    def _flagship(self, b, key, name, tagline, say, becomes, hint):
        color = MODE_COLORS[key]
        card = self._card(b)
        head = tk.Frame(card, bg=C.surface)
        head.pack(fill="x")
        dot = tk.Canvas(head, width=12, height=12, bg=C.surface, highlightthickness=0)
        dot.pack(side="left", pady=2)
        dot.create_oval(1, 1, 11, 11, fill=color, outline="")
        ui.label(head, name, size=15, semibold=True, color=color, bg=C.surface).pack(
            side="left", padx=(9, 0)
        )
        self._chip(head, "Flagship", bg=C.surface2).pack(side="right")
        ui.label(card, tagline, size=11, color=C.text_dim, bg=C.surface, wrap=520).pack(
            anchor="w", pady=(9, 12)
        )
        self._io_block(card, "YOU SAY", say, C.surface2, C.text, C.text_mute)
        ui.label(
            card, "↓  becomes", size=10, semibold=True, color=color, bg=C.surface
        ).pack(anchor="w", pady=(9, 9))
        self._io_block(card, "MUMBLE WRITES", becomes, "#0E0D0B", C.text_dim, color)
        ui.label(card, hint, size=10, color=C.text_mute, bg=C.surface, wrap=520).pack(
            anchor="w", pady=(12, 0)
        )

    def _io_block(self, parent, label, text, bg, fg, label_color):
        holder = RoundCard(
            parent, bg=bg, radius=12, padx=14, pady=11, outer_pady=(0, 0)
        )
        holder.canvas.pack_configure(fill="x")
        ui.label(
            holder.body, label, size=9, semibold=True, color=label_color, bg=bg
        ).pack(anchor="w")
        ui.label(holder.body, text, size=11, color=fg, bg=bg, wrap=510).pack(
            anchor="w", pady=(5, 0)
        )

    def _mode_mini(self, parent, key, name, desc, example):
        row = tk.Frame(parent, bg=C.surface)
        row.pack(fill="x", pady=(10, 0))
        dot = tk.Canvas(row, width=10, height=10, bg=C.surface, highlightthickness=0)
        dot.pack(side="left", anchor="n", pady=4)
        dot.create_oval(1, 1, 9, 9, fill=MODE_COLORS[key], outline="")
        col = tk.Frame(row, bg=C.surface)
        col.pack(side="left", fill="x", expand=True, padx=(10, 0))
        title = ui.label(
            col, name, size=12, semibold=True, color=MODE_COLORS[key], bg=C.surface
        )
        title.pack(anchor="w")
        ui.label(col, desc, size=10, color=C.text_dim, bg=C.surface, wrap=500).pack(
            anchor="w", pady=(1, 3)
        )
        self._chip(col, example, bg=C.surface2).pack(anchor="w")

        # subtle hover: brighten the title
        def on(_e):
            title.configure(fg=C.gold_hi)

        def off(_e):
            title.configure(fg=MODE_COLORS[key])

        for w in (row, col, title):
            w.bind("<Enter>", on)
            w.bind("<Leave>", off)

    # --------------------------------------------------------------- History
    def _build_history(self, b):
        for w in b.winfo_children():
            w.destroy()
        self._img_refs = []
        top = tk.Frame(b, bg=C.bg)
        top.pack(fill="x", pady=(0, 12))
        subtabs = tk.Frame(top, bg=C.bg)
        subtabs.pack(side="left")
        self.sub_pills = {}
        for key, text in [
            ("transcripts", "Transcripts"),
            ("clipboard", "Clipboard"),
            ("prompts", "Prompts"),
        ]:
            p = TabPill(subtabs, text, key, self._switch_history, C.bg)
            p.pack(side="left", padx=2)
            self.sub_pills[key] = p
        self.hist_actions = tk.Frame(top, bg=C.bg)
        self.hist_actions.pack(side="right")
        # Live filter bar — applies to whichever sub-tab is active.
        frow = tk.Frame(b, bg=C.bg)
        frow.pack(fill="x", pady=(0, 10))
        ui.label(frow, "Filter:", size=10, color=C.text_dim, bg=C.bg).pack(side="left")
        self.hist_filter_var = tk.StringVar()
        fe = _entry(frow, self.hist_filter_var, width=36)
        fe.pack(side="left", padx=(8, 0), ipady=3)
        fe.bind("<KeyRelease>", lambda e: self._draw_history())
        self.hist_list = tk.Frame(b, bg=C.bg)
        self.hist_list.pack(fill="both", expand=True)
        self._switch_history("transcripts")

    def _switch_history(self, which):
        for k, p in self.sub_pills.items():
            p.set_active(k == which)
        self._cur_history_tab = which
        self._draw_history()

    def refresh_history_tab(self):
        """Public: called by the controller after a new transcript lands, so the
        History tab stays live if it's visible. The History tab is built lazily, so
        guard on the actual widgets existing — NOT on _cur_history_tab, which is set
        in __init__ and therefore always present (that mismatch crashed _draw_history
        with AttributeError: 'hist_actions' when a transcript landed before the tab
        had ever been opened)."""
        if "history" not in self._built:
            return  # History tab not built yet
        # Redraw now only if History is the tab actually on screen; otherwise just
        # mark it dirty so it refreshes the next time you switch to it. This keeps
        # dictation snappy (no redrawing a hidden 30-row list on every transcript).
        if self.visible and self._cur_section == "history":
            try:
                if self.hist_actions.winfo_exists():
                    self._draw_history()
            except Exception:
                pass
        else:
            self._history_dirty = True

    def _hist_filter(self):
        try:
            return (self.hist_filter_var.get() or "").strip().lower()
        except Exception:
            return ""

    def _draw_history(self):
        which = self._cur_history_tab
        for w in self.hist_actions.winfo_children():
            w.destroy()
        for w in self.hist_list.winfo_children():
            w.destroy()
        self._img_refs = []
        flt = self._hist_filter()
        if which == "prompts":
            ui.RoundButton(
                self.hist_actions,
                "Clear all",
                self._clear_prompts,
                kind="danger",
                bg=C.bg,
                height=30,
                font_size=10,
            ).pack(side="left")
            entries = self.ctrl.prompts_history()
            if flt:
                entries = [e for e in entries
                           if flt in ((e.get("prompt") or "") + (e.get("request") or "")).lower()]
            if not entries:
                self._empty(
                    self.hist_list,
                    "No prompts match" if flt else "No prompts yet",
                    "Every prompt you generate with Prompt mode is kept here.",
                )
            for e in entries:  # uncapped by design (Change 4)
                self._text_row(
                    self.hist_list,
                    "prompt",
                    e.get("time", ""),
                    e.get("prompt", ""),
                    preview_chars=100,
                )
            return
        if which == "transcripts":
            self._show_pre_ai = getattr(self, "_show_pre_ai", False)
            ui.RoundButton(
                self.hist_actions,
                "Open file",
                self.ctrl.open_transcripts,
                kind="ghost",
                bg=C.bg,
                height=30,
                font_size=10,
            ).pack(side="left")
            # Toggle between the converted (Post-AI) text and the original dictation
            # (Pre-AI) — only useful when some entries carry a stored `raw`. The button
            # label shows which view is ACTIVE.
            entries = self.ctrl.history.all_newest_first()
            if any(e.get("raw") for e in entries):

                def _toggle_pre_ai():
                    self._show_pre_ai = not self._show_pre_ai
                    self._draw_history()

                ui.RoundButton(
                    self.hist_actions,
                    "Showing: Pre-AI" if self._show_pre_ai else "Showing: Post-AI",
                    _toggle_pre_ai,
                    kind="ghost",
                    bg=C.bg,
                    height=30,
                    font_size=10,
                ).pack(side="left", padx=(8, 0))
            ui.RoundButton(
                self.hist_actions,
                "Clear all",
                self._clear_transcripts,
                kind="danger",
                bg=C.bg,
                height=30,
                font_size=10,
            ).pack(side="left", padx=(8, 0))
            indexed = list(enumerate(entries))
            if flt:
                # Filter on what's DISPLAYED, but keep original indices so
                # delete removes the right item.
                indexed = [
                    (i, e) for i, e in indexed
                    if flt in ((e.get("text") or "") + (e.get("raw") or "")).lower()
                ]
            if not indexed:
                self._empty(
                    self.hist_list,
                    "No transcripts match" if flt else "No transcripts yet",
                    "Press your hotkey and start talking.",
                )
            # Render only the most recent N rows — building 100 rich rows on every
            # tab switch was what made the History tab slow. "Open file" has them all.
            CAP = 30
            for i, e in indexed[:CAP]:
                disp = (
                    e.get("raw") if (self._show_pre_ai and e.get("raw")) else e.get("text", "")
                )
                self._text_row(
                    self.hist_list,
                    e.get("mode", "text"),
                    e.get("time", ""),
                    disp,
                    words=e.get("words"),
                    on_delete=(
                        lambda i=i: (
                            self.ctrl.delete_transcript(i),
                            self._switch_history("transcripts"),
                        )
                    ),
                )
            entries = [e for _, e in indexed]
            if len(entries) > CAP:
                ui.label(
                    self.hist_list,
                    f"Showing the {CAP} most recent of {len(entries)} — use “Open file” for all.",
                    size=9,
                    color=C.text_mute,
                    bg=C.bg,
                ).pack(anchor="w", pady=(8, 0))
        else:
            ui.RoundButton(
                self.hist_actions,
                "Clear all",
                self._clear_clipboard,
                kind="danger",
                bg=C.bg,
                height=30,
                font_size=10,
            ).pack(side="left")
            clip_max = self.ctrl.settings.get("clipboard_max", 5000)
            items = self.ctrl.clipboard_recent(clip_max)
            indexed = list(enumerate(items))
            if flt:
                indexed = [
                    (i, e) for i, e in indexed
                    if flt in ((e.get("text") or "") + (e.get("path") or "")).lower()
                ]
            if not indexed:
                self._empty(
                    self.hist_list,
                    "Nothing matches" if flt else "Clipboard is empty",
                    f"Anything you copy (text or images) shows up here — your last {clip_max}.",
                )
            CAP = 30  # cap rendered rows for a snappy tab (same reason as transcripts)
            for i, e in indexed[:CAP]:
                def cb(i=i):
                    self.ctrl.delete_clipboard(i)
                    self._switch_history("clipboard")
                if e.get("type") == "image":
                    self._image_row(self.hist_list, e, on_delete=cb)
                else:
                    self._text_row(
                        self.hist_list,
                        None,
                        e.get("time", ""),
                        e.get("text", ""),
                        on_delete=cb,
                    )
            if len(indexed) > CAP:
                ui.label(
                    self.hist_list,
                    f"Showing the {CAP} most recent of {len(indexed)}.",
                    size=9,
                    color=C.text_mute,
                    bg=C.bg,
                ).pack(anchor="w", pady=(8, 0))

    def _clear_prompts(self):
        from tkinter import messagebox

        if messagebox.askyesno(
            "Clear prompts",
            "Delete ALL saved prompts? This can't be undone.",
            parent=self.win,
        ):
            self.ctrl.clear_prompts()
            self._switch_history("prompts")

    def _text_row(self, parent, mode, time_s, text, words=None, on_delete=None,
                  preview_chars=180):
        text = text or ""
        time_s = time_s or ""
        card = self._card(parent)
        top = tk.Frame(card, bg=C.surface)
        top.pack(fill="x")
        if mode is not None:
            self._chip(top, MODE_LABELS.get(mode, "Text"), bg=C.surface2).pack(
                side="left"
            )
        meta = time_s + (f"  ·  {words} words" if words else "")
        ui.label(top, meta, size=9, color=C.text_mute, bg=C.surface).pack(
            side="left", padx=(10, 0)
        )
        if on_delete:
            ui.RoundButton(
                top,
                "✕",
                on_delete,
                kind="ghost",
                bg=C.surface,
                height=26,
                font_size=10,
                padx=10,
            ).pack(side="right")
        ui.RoundButton(
            top,
            "Copy",
            lambda t=text: self.ctrl.copy_text(t),
            kind="ghost",
            bg=C.surface,
            height=26,
            font_size=10,
            padx=13,
        ).pack(side="right", padx=(0, 6))
        preview = " ".join(text.split())
        if len(preview) > preview_chars:
            preview = preview[:preview_chars] + "…"
        ui.label(card, preview, size=11, color=C.text_dim, bg=C.surface, wrap=510).pack(
            anchor="w", pady=(8, 0)
        )

    def _image_row(self, parent, e, on_delete=None):
        card = self._card(parent)
        row = tk.Frame(card, bg=C.surface)
        row.pack(fill="x")
        try:
            img = Image.open(e["path"])
            img.thumbnail((150, 84))
            ph = ImageTk.PhotoImage(img)
            self._img_refs.append(ph)
            tk.Label(row, image=ph, bg=C.surface, bd=0).pack(side="left")
        except Exception:
            ui.label(row, "[image]", size=11, color=C.text_dim, bg=C.surface).pack(
                side="left"
            )
        meta = tk.Frame(row, bg=C.surface)
        meta.pack(side="left", padx=(14, 0), anchor="n", pady=2)
        self._chip(meta, "Image", bg=C.surface2).pack(anchor="w")
        ui.label(
            meta,
            (e.get("size") or "") + "  ·  " + (e.get("time") or ""),
            size=10,
            color=C.text_mute,
            bg=C.surface,
        ).pack(anchor="w", pady=(6, 0))
        if on_delete:
            ui.RoundButton(
                row,
                "✕",
                on_delete,
                kind="ghost",
                bg=C.surface,
                height=28,
                font_size=10,
                padx=10,
            ).pack(side="right")
        ui.RoundButton(
            row,
            "Open",
            lambda p=e.get("path"): self._open_path(p),
            kind="ghost",
            bg=C.surface,
            height=28,
            font_size=10,
        ).pack(side="right", padx=(0, 6))
        ui.RoundButton(
            row,
            "Copy",
            lambda p=e.get("path"): self.ctrl.copy_image(p),
            kind="ghost",
            bg=C.surface,
            height=28,
            font_size=10,
        ).pack(side="right", padx=(0, 6))

    @staticmethod
    def _open_path(p):
        try:
            if p and os.path.exists(p):
                if sys.platform == "darwin":
                    subprocess.Popen(["open", p])
                else:
                    subprocess.Popen(["xdg-open", p])
        except Exception:
            pass

    def _empty(self, parent, title, sub):
        c = self._card(parent)
        ui.label(c, title, size=12, semibold=True, bg=C.surface).pack(anchor="w")
        ui.label(c, sub, size=11, color=C.text_dim, bg=C.surface, wrap=520).pack(
            anchor="w", pady=(4, 0)
        )

    def _clear_transcripts(self):
        self.ctrl.history.clear()
        self.ctrl.refresh_tray_menu()
        self._switch_history("transcripts")

    def _clear_clipboard(self):
        self.ctrl.clear_clipboard()
        self._switch_history("clipboard")

    # ----------------------------------------------------------------- Stats
    def _build_stats(self, b):
        for w in b.winfo_children():
            w.destroy()
        s = self.ctrl.stats()
        modes = self.ctrl.mode_stats()

        ui.label(
            b, "Lifetime totals", size=12, semibold=True, color=C.gold_hi, bg=C.bg
        ).pack(anchor="w", pady=(0, 3))
        tiles = [
            ("Words dictated (lifetime)", f"{s.get('total_words', 0):,}", C.gold_hi),
            ("Words today", f"{s.get('today_words', 0):,}", C.gold_hi),
            ("Average speed", f"{s.get('avg_wpm', 0)} wpm", C.gold_hi),
            ("Fastest", f"{s.get('best_wpm', 0)} wpm", C.text),
            (
                "≈ Typing time saved (lifetime)",
                s.get(
                    "typing_time_display", f"{s.get('typing_minutes_saved', 0):g} min"
                ),
                C.text,
            ),
            ("Transcripts (lifetime)", f"{s.get('total_transcripts', 0):,}", C.gold_hi),
        ]
        grid = tk.Frame(b, bg=C.bg)
        grid.pack(fill="x")
        grid.grid_columnconfigure(0, weight=1, uniform="s")
        grid.grid_columnconfigure(1, weight=1, uniform="s")
        for i, (label, value, color) in enumerate(tiles):
            r, col = divmod(i, 2)
            holder = tk.Frame(grid, bg=C.bg)
            holder.grid(
                row=r,
                column=col,
                sticky="nsew",
                padx=(0 if col == 0 else 4, 4 if col == 0 else 0),
                pady=(0, 1),
            )
            cell = RoundCard(holder, radius=14, padx=16, pady=6, outer_pady=(0, 2))
            ui.label(
                cell.body, value, size=18, semibold=True, color=color, bg=C.surface
            ).pack(anchor="w")
            ui.label(cell.body, label, size=9, color=C.text_mute, bg=C.surface).pack(
                anchor="w", pady=(1, 0)
            )

        # ---- daily word-count bar chart (last 7 days) ----
        # Supports 7d/30d toggle for flexible date-range view (future UI).
        self._stats_days = 7
        self._stats_daily_frame = tk.Frame(b, bg=C.bg)
        self._stats_daily_frame.pack(fill="x")
        self._draw_daily_chart(b, modes)

    def _draw_daily_chart(self, b, modes):
        for w in self._stats_daily_frame.winfo_children():
            w.destroy()
        daily = self.ctrl.daily_stats(self._stats_days)
        chart_card = RoundCard(
            self._stats_daily_frame, radius=14, padx=16, pady=8, outer_pady=(0, 4)
        )
        ui.label(
            chart_card.body, "Words per day", size=11, semibold=True, bg=C.surface
        ).pack(anchor="w")
        chart_w, chart_h = 560, 118
        base_y = chart_h - 20  # baseline (room for date labels below)
        canvas = tk.Canvas(
            chart_card.body,
            width=chart_w,
            height=chart_h,
            bg=C.surface,
            highlightthickness=0,
        )
        canvas.pack(pady=(4, 0))
        max_words = max((d[1] for d in daily), default=1)
        n = len(daily)
        if n == 0:
            return
        slot = (chart_w - 50) // n
        bar_w = max(12, slot - 14)
        for i, (d, wc, _tc) in enumerate(daily):
            x = 30 + i * slot
            bh = int((wc / max(max_words, 1)) * (base_y - 18)) if wc > 0 else 0
            y = base_y - bh
            canvas.create_rectangle(
                x,
                y,
                x + bar_w,
                base_y,
                fill=C.gold_hi if wc > 0 else C.surface2,
                outline="",
                width=0,
            )
            canvas.create_text(
                x + bar_w // 2,
                base_y + 11,
                text=d[-5:],
                fill=C.text_mute,
                font=(FONT_SB, 8),
            )
            if wc > 0:
                canvas.create_text(
                    x + bar_w // 2,
                    y - 8,
                    text=str(wc),
                    fill=C.text_dim,
                    font=(FONT_SB, 8),
                )

        # ---- mode breakdown (compact card) ----
        if modes:
            mc = RoundCard(b, radius=14, padx=16, pady=10, outer_pady=(0, 4))
            ui.label(
                mc.body, "Mode breakdown", size=10, semibold=True, bg=C.surface
            ).pack(anchor="w")
            bar_h = min(len(modes) * 20 + 6, 110)
            mode_canvas = tk.Canvas(
                mc.body, width=540, height=bar_h, bg=C.surface, highlightthickness=0
            )
            mode_canvas.pack(pady=(4, 0))
            total_count = sum(m[1] for m in modes)
            MODE_LABELS_MAP = {
                "text": "Text",
                "prompt": "Prompt",
                "email": "Email",
                "reply": "Reply",
                "foreign": "Foreign",
            }
            for i, (mode_key, count, words) in enumerate(modes):
                y = 6 + i * 20
                label = MODE_LABELS_MAP.get(mode_key, mode_key.title())
                frac = count / total_count if total_count > 0 else 0
                bar_w2 = int(frac * 180)
                col = MODE_COLORS.get(mode_key, C.gold)
                mode_canvas.create_text(
                    8, y, text=label, fill=C.text, font=(FONT_SB, 9), anchor="w"
                )
                mode_canvas.create_rectangle(
                    62, y - 5, 62 + bar_w2, y + 7, fill=col, outline=""
                )
                mode_canvas.create_text(
                    66 + bar_w2,
                    y,
                    text=f"{count}",
                    fill=C.text_mute,
                    font=(FONT_SB, 8),
                    anchor="w",
                )

        note = RoundCard(b, radius=14, padx=16, pady=8, outer_pady=(0, 0))
        ui.label(
            note.body,
            "Speed is from how long you spoke vs. how many words you got. "
            "Time saved assumes typing at ~45 wpm.",
            size=9,
            color=C.text_mute,
            bg=C.surface,
            wrap=520,
        ).pack(anchor="w")

    # --------------------------------------------------------------- Settings
    def _build_settings(self, b):
        for w in b.winfo_children():
            w.destroy()
        idc = self._card(b)
        ui.label(idc, "Your name", size=12, semibold=True, bg=C.surface).pack(
            anchor="w"
        )
        ui.label(
            idc,
            "Used to sign off emails written in Email mode.",
            size=10,
            color=C.text_mute,
            bg=C.surface,
        ).pack(anchor="w", pady=(2, 8))
        self.name_var = tk.StringVar(value=self.settings.get("user_name", ""))
        ne = _entry(idc, self.name_var, width=32)
        ne.pack(anchor="w", ipady=5)
        ne.bind("<FocusOut>", lambda e: self.ctrl.set_user_name(self.name_var.get()))
        ne.bind("<Return>", lambda e: self.ctrl.set_user_name(self.name_var.get()))

        self._hotkey_card(
            b,
            "Activation hotkey",
            "Tap once to start, tap again to stop and paste.",
            "hotkey",
            self.ctrl.apply_hotkey,
        )
        self._hotkey_card(
            b,
            "Paste-latest hotkey",
            "Pastes your most recent transcript into the focused field. "
            "For the full History window use Ctrl + Option + H.",
            "quick_paste_hotkey",
            self.ctrl.apply_quick_paste_hotkey,
        )
        self._hotkey_card(
            b,
            "Search hotkey",
            "Stop recording and instantly search your dictation — or just press it while idle to search your current selection.",
            "search_hotkey",
            self.ctrl.apply_search_hotkey,
        )
        self._build_search_engine_card(b)

        self._build_prompt_prefs_card(b)
        self._build_vocabulary_card(b)
        self._build_preset_adder_card(b)
        self._build_pro_card(b)

        mc = self._card(b)
        ui.label(mc, "Microphone", size=12, semibold=True, bg=C.surface).pack(
            anchor="w"
        )
        ui.label(
            mc,
            "Pick a device and press Save. Mumble keeps it until you change it here "
            "(or it gets unplugged).",
            size=10,
            color=C.text_mute,
            bg=C.surface,
        ).pack(anchor="w", pady=(2, 8))
        self.mic_var = tk.StringVar()
        microw = tk.Frame(mc, bg=C.surface)
        microw.pack(anchor="w", fill="x")
        self.mic_combo = ttk.Combobox(
            microw,
            textvariable=self.mic_var,
            state="readonly",
            style="Mumble.TCombobox",
            width=40,
        )
        self.mic_combo.pack(side="left")
        # No <<ComboboxSelected>> auto-apply — picking is just a pending choice until Save.
        ui.RoundButton(
            microw,
            "Save",
            self._save_mic,
            kind="primary",
            bg=C.surface,
            height=30,
            font_size=10,
        ).pack(side="left", padx=(10, 0))
        self.mic_saved_msg = ui.label(mc, "", size=9, color=C.text_mute, bg=C.surface)
        self.mic_saved_msg.pack(anchor="w", pady=(6, 0))
        testrow = tk.Frame(mc, bg=C.surface)
        testrow.pack(anchor="w", fill="x", pady=(10, 0))
        ui.RoundButton(
            testrow, "Test microphone", self._test_mic, kind="ghost", bg=C.surface
        ).pack(side="left")
        self.meter = tk.Canvas(
            testrow, width=200, height=10, bg=C.surface2, highlightthickness=0
        )
        self.meter.pack(side="left", padx=(14, 0))
        self._draw_meter(0.0)

        tc = self._card(b)
        ui.label(tc, "Transcription", size=12, semibold=True, bg=C.surface).pack(
            anchor="w"
        )
        self._row(
            tc,
            "Accuracy model",
            "All transcription runs on your device (CPU). tiny = fastest (default, "
            "near-real-time) · base = balanced · small = most accurate. Reloads the "
            "model when changed.",
            lambda h: self._model_combo(h),
        )
        # Model status indicator
        ml = tk.Frame(tc, bg=C.surface)
        ml.pack(fill="x", pady=(0, 6))
        model_name = self.settings.get("model", "base.en")
        loaded = self.ctrl.model is not None
        status_color = C.gold_hi if loaded else C.text_mute
        status_text = (
            f"● {model_name} loaded" if loaded else f"○ {model_name} (not loaded)"
        )
        self._model_status_label = ui.label(
            ml, status_text, size=9, color=status_color, bg=C.surface
        )
        self._model_status_label.pack(anchor="w", padx=(10, 0))
        self._row(
            tc, "Language", "Code like en, es or fr.", lambda h: self._lang_entry(h)
        )
        self._row(
            tc,
            "Clean up my speech",
            "When Pro Mode is off, the offline builder removes fillers and fixes "
            "capitalization and punctuation entirely on-device.",
            lambda h: ui.Switch(
                h,
                value=self.settings.get("format_enabled", True),
                bg=C.surface,
                command=self.ctrl.set_format_enabled,
            ).pack(),
        )

        sysc = self._card(b)
        ui.label(sysc, "System", size=12, semibold=True, bg=C.surface).pack(anchor="w")
        self._row(
            sysc,
            "Start Mumble at login",
            "Launch automatically and stay ready.",
            lambda h: ui.Switch(
                h,
                value=self.ctrl.autostart_enabled(),
                bg=C.surface,
                command=self._toggle_autostart,
            ).pack(),
        )

        # Reset to factory defaults
        rst = tk.Frame(sysc, bg=C.surface)
        rst.pack(fill="x", pady=(8, 0))
        ui.RoundButton(
            rst,
            "Reset all to defaults",
            self._reset_settings,
            kind="danger",
            bg=C.surface,
            height=30,
            font_size=10,
        ).pack(side="left")
        ui.label(
            rst,
            "Restores factory settings (keeps your transcripts).",
            size=9,
            color=C.text_mute,
            bg=C.surface,
        ).pack(side="left", padx=(10, 0))

        self._build_share_about(b)

    def _build_share_about(self, b):
        send = self._card(b)
        ui.label(
            send, "Share Mumble", size=13, semibold=True, color=C.gold_hi, bg=C.surface
        ).pack(anchor="w")
        ui.label(
            send,
            "Yours to pass on — no account, nothing to sign up for.",
            size=10,
            color=C.text_mute,
            bg=C.surface,
        ).pack(anchor="w", pady=(3, 8))
        for n, t in [
            (
                "1",
                "Send them the macOS release zip — email, AirDrop, Drive, or a USB stick.",
            ),
            ("2", "They open the zip, then run “Install Mumble.command”."),
            ("3", "Done — it's in their tray and Pro Mode works right away, no setup."),
        ]:
            row = tk.Frame(send, bg=C.surface)
            row.pack(fill="x", pady=(6, 0))
            self._chip(row, n).pack(side="left", anchor="n")
            ui.label(row, t, size=11, color=C.text_dim, bg=C.surface, wrap=500).pack(
                side="left", padx=(12, 0)
            )

        ab = self._card(b)
        ui.label(
            ab, "About", size=13, semibold=True, color=C.gold_hi, bg=C.surface
        ).pack(anchor="w")
        ui.label(
            ab,
            f"Mumble v{branding.VERSION}  ·  {branding.APP_TAGLINE}",
            size=10,
            color=C.text_mute,
            bg=C.surface,
        ).pack(anchor="w", pady=(3, 8))
        ui.label(
            ab,
            "Inspired by Wispr Flow as an alternative approach to voice-powered "
            "productivity.",
            size=11,
            color=C.text_dim,
            bg=C.surface,
            wrap=510,
        ).pack(anchor="w")
        ui.label(
            ab,
            "Your data: " + branding.DATA_DIR,
            size=9,
            color=C.text_mute,
            bg=C.surface,
            wrap=510,
        ).pack(anchor="w", pady=(10, 6))
        abrow = tk.Frame(ab, bg=C.surface)
        abrow.pack(anchor="w", fill="x")
        ui.RoundButton(
            abrow,
            "Open data folder",
            self.ctrl.open_data_folder,
            kind="ghost",
            bg=C.surface,
            height=30,
            font_size=10,
        ).pack(side="left")
        ui.RoundButton(
            abrow,
            "Switch to Mumble Lite",
            self.ctrl.switch_to_lite,
            kind="subtle",
            bg=C.surface,
            height=30,
            font_size=10,
        ).pack(side="left", padx=(10, 0))
        ui.label(
            ab,
            "Mumble Lite is the frozen classic interface — same data, same engine.",
            size=9,
            color=C.text_mute,
            bg=C.surface,
        ).pack(anchor="w", pady=(6, 0))

    def _pro_status_text(self, st):
        if st.get("key_failed"):
            return "  Your Cerebras key isn't working — check or re-enter it below."
        return (
            "  Ready — using your Cerebras key"
            if st.get("using_own_key")
            else "  Add your Cerebras key below to turn on Pro AI."
        )

    def _pro_status_color(self, st):
        return C.red if st.get("key_failed") else C.gold

    def _build_pro_card(self, b):
        card = self._card(b)
        head = tk.Frame(card, bg=C.surface)
        head.pack(fill="x")
        ui.label(
            head, "✦ Pro Mode", size=13, semibold=True, color=C.gold_hi, bg=C.surface
        ).pack(side="left")
        ui.label(head, "· Cerebras AI", size=10, color=C.text_mute, bg=C.surface).pack(
            side="left", padx=(8, 0)
        )
        st = self.ctrl.pro_status()
        ui.Switch(
            head,
            value=st.get("enabled", True),
            bg=C.surface,
            command=self.ctrl.set_pro_mode,
        ).pack(side="right")
        ui.label(
            card,
            "Pro Mode sends your transcribed text to Cerebras (gpt-oss-120b) for "
            "intelligent cleanup and formatting. The AI handles intent detection, "
            "spelling correction, punctuation, and output shaping — all in one pass, "
            "in about half a second. Your audio is always transcribed on your Mac and "
            "never uploaded. Turn it off to use the instant offline builder only.",
            size=10,
            color=C.text_mute,
            bg=C.surface,
            wrap=520,
        ).pack(anchor="w", pady=(8, 4))
        ui.label(
            card,
            "API keys are mandatory: Pro Mode needs your own free Cerebras key (there is "
            "no built-in key, and nothing is hard-coded). Without one, Mumble runs fully "
            "offline on your CPU.",
            size=10,
            semibold=True,
            color=C.gold,
            bg=C.surface,
            wrap=520,
        ).pack(anchor="w", pady=(0, 10))
        self.pro_status_lbl = ui.label(
            card,
            self._pro_status_text(st),
            size=11,
            color=self._pro_status_color(st),
            bg=C.surface,
        )
        self.pro_status_lbl.pack(anchor="w", pady=(0, 12))

        ui.label(card, "Cerebras API key", size=11, semibold=True, bg=C.surface).pack(
            anchor="w"
        )
        ui.label(
            card,
            "Cloud AI runs on Cerebras (spelled C-E-R-E-B-R-A-S) — free to start. "
            "First time? Follow these steps:",
            size=9,
            color=C.text_mute,
            bg=C.surface,
            wrap=510,
        ).pack(anchor="w", pady=(2, 4))
        for step in [
            "1.  Open  cloud.cerebras.ai  in your browser.",
            "2.  Sign up (Google/GitHub or email) — it's free, no card needed.",
            "3.  In the left sidebar, click  “API Keys”.",
            "4.  Click  “Create API Key”, give it any name, and Create.",
            "5.  Copy the key (it starts with  csk-…) — copy it now, it shows once.",
            "6.  Paste it in the box below and press  “Save & test”.",
        ]:
            ui.label(card, step, size=9, color=C.text_dim, bg=C.surface, wrap=510).pack(
                anchor="w", pady=(0, 1)
            )
        ui.label(
            card,
            "Tip: keep this key private — Mumble stores it only on this PC.",
            size=9,
            color=C.text_mute,
            bg=C.surface,
            wrap=510,
        ).pack(anchor="w", pady=(3, 6))
        row = tk.Frame(card, bg=C.surface)
        row.pack(fill="x", pady=(0, 0))
        self.di_key_var = tk.StringVar(value=self.settings.get("cerebras_api_key", ""))
        e = _entry(row, self.di_key_var, width=40)
        e.configure(show="•")
        e.pack(side="left", ipady=5)
        self.di_msg = ui.label(card, "", size=10, color=C.text_mute, bg=C.surface)

        def save_test():
            self.ctrl.set_cerebras_key(self.di_key_var.get())
            self.di_msg.configure(text="Testing…", fg=C.text_mute)
            self.win.update_idletasks()
            ok, m = self.ctrl.test_cerebras()
            self.di_msg.configure(text=m, fg=C.gold if ok else C.red)
            st3 = self.ctrl.pro_status()
            self.pro_status_lbl.configure(
                text=self._pro_status_text(st3), fg=self._pro_status_color(st3)
            )

        ui.RoundButton(
            row, "Save & test", save_test, kind="primary", bg=C.surface
        ).pack(side="left", padx=(10, 0))
        self.di_msg.pack(anchor="w", pady=(8, 0))
        ui.label(
            card,
            "If the key stops working, Mumble falls back to the offline builder "
            "automatically. Fix or replace it here to restore cloud AI.",
            size=9,
            color=C.text_mute,
            bg=C.surface,
            wrap=510,
        ).pack(anchor="w", pady=(8, 0))

        # ---- AI Provider (Change 10): dropdown + key / local-URL + Save & test ----
        import ai as _ai

        ui.label(
            card, "AI Provider", size=11, semibold=True, bg=C.surface
        ).pack(anchor="w", pady=(14, 0))
        ui.label(
            card,
            "Cerebras is the fastest and the default. Route through OpenAI or "
            "Anthropic with your own key, or a Local LLM (Ollama / LM Studio — "
            "no key, fully offline).",
            size=9,
            color=C.text_mute,
            bg=C.surface,
            wrap=510,
        ).pack(anchor="w", pady=(2, 4))
        _DROPDOWN = ["cerebras", "openai", "anthropic", "local"]
        self._provider_labels = {
            _ai.PROVIDERS[pid]["label"]: pid for pid in _DROPDOWN
        }
        cur = self.settings.get("llm_provider", "cerebras")
        cur_label = next(
            (lb for lb, pid in self._provider_labels.items() if pid == cur),
            list(self._provider_labels)[0],
        )
        prow = tk.Frame(card, bg=C.surface)
        prow.pack(anchor="w", fill="x", pady=(2, 0))
        self.provider_var = tk.StringVar(value=cur_label)
        pcombo = ttk.Combobox(
            prow,
            textvariable=self.provider_var,
            state="readonly",
            style="Mumble.TCombobox",
            width=38,
            values=list(self._provider_labels),
        )
        pcombo.pack(side="left")

        # Model + key + local-URL rows (key hidden for Local; URL local-only).
        mrow = tk.Frame(card, bg=C.surface)
        mrow.pack(anchor="w", fill="x", pady=(6, 0))
        ui.label(mrow, "Model:", size=9, color=C.text_dim, bg=C.surface).pack(
            side="left"
        )
        self.provider_model_var = tk.StringVar()
        _entry(mrow, self.provider_model_var, width=28).pack(
            side="left", padx=(8, 0), ipady=3
        )
        self.provider_key_row = tk.Frame(card, bg=C.surface)
        ui.label(self.provider_key_row, "API key:", size=9, color=C.text_dim,
                 bg=C.surface).pack(side="left")
        self.provider_key_var = tk.StringVar()
        ke = _entry(self.provider_key_row, self.provider_key_var, width=34)
        ke.configure(show="•")
        ke.pack(side="left", padx=(8, 0), ipady=3)
        self.provider_url_row = tk.Frame(card, bg=C.surface)
        ui.label(self.provider_url_row, "Server URL:", size=9, color=C.text_dim,
                 bg=C.surface).pack(side="left")
        self.provider_url_var = tk.StringVar(
            value=self.settings.get("local_url", "http://localhost:11434")
        )
        _entry(self.provider_url_row, self.provider_url_var, width=30).pack(
            side="left", padx=(8, 0), ipady=3
        )
        # Always-visible connection status indicator.
        self.provider_msg = ui.label(card, "", size=9, color=C.text_mute, bg=C.surface)

        def _sync_provider_fields(*_a):
            pid = self._provider_labels.get(self.provider_var.get(), "cerebras")
            info = _ai.PROVIDERS[pid]
            self.provider_model_var.set(self.settings.get(info["model_setting"], ""))
            if pid == "local":
                self.provider_key_row.pack_forget()
                self.provider_url_row.pack(anchor="w", fill="x", pady=(6, 0))
            else:
                self.provider_url_row.pack_forget()
                self.provider_key_var.set(self.settings.get(info["key_setting"], ""))
                self.provider_key_row.pack(anchor="w", fill="x", pady=(6, 0))

        pcombo.bind("<<ComboboxSelected>>", _sync_provider_fields)

        def save_test_provider():
            pid = self._provider_labels.get(self.provider_var.get(), "cerebras")
            ok, m = self.ctrl.set_llm_provider(
                pid,
                self.provider_model_var.get().strip(),
                key=(None if pid == "local" else self.provider_key_var.get()),
                local_url=(self.provider_url_var.get() if pid == "local" else None),
            )
            if not ok:
                self.provider_msg.configure(text="  " + m, fg=C.red)
                return
            self.provider_msg.configure(text="  Testing…", fg=C.text_mute)
            self.win.update_idletasks()
            ok2, m2 = self.ctrl.test_provider()
            self.provider_msg.configure(
                text=("  Connected" if ok2 else "  " + m2),
                fg=C.gold if ok2 else C.red,
            )

        brow = tk.Frame(card, bg=C.surface)
        brow.pack(anchor="w", fill="x", pady=(8, 0))
        ui.RoundButton(
            brow, "Save & test", save_test_provider, kind="primary", bg=C.surface,
            height=30, font_size=10,
        ).pack(side="left")
        self.provider_msg.pack(anchor="w", pady=(6, 0))
        _sync_provider_fields()

    def _keycap(self, parent, text):
        """A small non-interactive keycap chip (button-style) used to show a shortcut
        key visually — same rounded design language as the rest of the UI."""
        f = (ui.FONT_SB, 10)
        w = max(40, ui.measure(f, text) + 22)
        cv = tk.Canvas(
            parent, width=w, height=26, bg=C.surface, highlightthickness=0, bd=0
        )
        ui.round_rect(cv, 1, 1, w - 1, 25, 7, fill=C.surface2, outline=C.border)
        cv.create_line(7, 2, w - 7, 2, fill=ui._lighten(C.surface2, 0.30), width=1)
        cv.create_text(w / 2, 13, text=text, fill=C.text, font=f)
        return cv

    def _pref_combo(self, holder, pref_key, values):
        cur = self.settings.get("prompt_prefs", {}).get(pref_key, values[0])
        var = tk.StringVar(value=cur)
        cb = ttk.Combobox(
            holder,
            textvariable=var,
            state="readonly",
            style="Mumble.TCombobox",
            width=22,
            values=values,
        )
        cb.pack()
        cb.bind(
            "<<ComboboxSelected>>",
            lambda e: self.ctrl.set_prompt_pref(pref_key, var.get()),
        )
        return cb

    def _build_prompt_prefs_card(self, b):
        """Standing, global prompt-shaping preferences — replaces the old per-prompt
        pop-up. Folded into every Prompt call; the spoken request always wins."""
        card = self._card(b)
        ui.label(card, "Prompt preferences", size=12, semibold=True, bg=C.surface).pack(
            anchor="w"
        )
        ui.label(
            card,
            "Defaults applied whenever the island Prompt toggle is on. What you "
            "actually dictate always overrides these.",
            size=10,
            color=C.text_mute,
            bg=C.surface,
            wrap=560,
        ).pack(anchor="w", pady=(2, 8))

        for key, title, desc, values in [
            (
                "tone",
                "Tone",
                "How the writing sounds. Professional = measured and formal; Direct & blunt = short, no hedging.",
                ["Neutral", "Professional", "Casual", "Direct & blunt"],
            ),
            (
                "detail",
                "Detail level",
                "How much the AI elaborates. Concise = essentials only; Comprehensive = thorough with edge cases.",
                ["Concise", "Balanced", "Comprehensive"],
            ),
            (
                "structure",
                "Structure",
                "Output format. Bullets & headings = organised sections; Prose = flowing paragraphs; Minimal markdown = lightweight formatting.",
                ["Prose", "Bullets & headings", "Minimal markdown"],
            ),
            (
                "audience",
                "Audience",
                "Who will read this. Technical = assumes domain knowledge; Executive = high-level summaries; Myself = personal notes style.",
                ["General", "Technical", "Executive", "Myself (notes)"],
            ),
            (
                "reasoning",
                "Reasoning",
                "Show brief reasoning makes the AI explain its thinking before the answer; Just the answer hides it.",
                ["Just the answer", "Show brief reasoning"],
            ),
        ]:
            self._row(
                card, title, desc, lambda h, k=key, v=values: self._pref_combo(h, k, v)
            )

        # Polishing Aggressiveness — controls how much the plain-text (no-mode) polisher
        # may edit. Light keeps your exact words; Thorough rewrites for clarity.
        self._row(
            card,
            "Polishing aggressiveness",
            "How much everyday dictation is cleaned. Light = smallest fixes, "
            "keep your exact words · Standard = also smooth grammar · Thorough = rewrite "
            "for clarity (meaning preserved).",
            lambda h: self._polish_combo(h),
        )

    def _build_vocabulary_card(self, b):
        """Personal vocabulary: spoken mis-hearing → correct spelling, fixed on the
        raw transcript before the AI pass. One mapping per line: wrong = right."""
        card = self._card(b)
        ui.label(card, "Personal vocabulary", size=12, semibold=True, bg=C.surface).pack(
            anchor="w"
        )
        ui.label(
            card,
            "Add the names, brands, and jargon you actually use — one per line "
            "(e.g.  Sarah  ·  Mumble  ·  Cerebras). Mumble listens for them and "
            "fixes mis-heard variants automatically; you don't need to predict "
            "misspellings. Advanced: a line like  mambo = Mumble  forces an exact "
            "correction for a stubborn mis-hearing.",
            size=10,
            color=C.text_mute,
            bg=C.surface,
            wrap=560,
        ).pack(anchor="w", pady=(2, 8))
        self.vocab_text = tk.Text(
            card,
            width=48,
            height=5,
            bg=C.surface2,
            fg=C.text,
            insertbackground=C.text,
            relief="flat",
            font=(branding.FONT, 10),
            padx=8,
            pady=6,
        )
        self.vocab_text.pack(anchor="w", fill="x")
        terms = self.settings.get("vocabulary_terms", []) or []
        vocab = self.settings.get("vocabulary", {}) or {}
        lines = list(terms) + [f"{k} = {v}" for k, v in vocab.items()]
        self.vocab_text.insert("1.0", "\n".join(lines))
        row = tk.Frame(card, bg=C.surface)
        row.pack(anchor="w", fill="x", pady=(8, 0))
        ui.RoundButton(
            row, "Save", self._save_vocabulary, kind="primary", bg=C.surface,
            height=30, font_size=10,
        ).pack(side="left")
        self.vocab_msg = ui.label(row, "", size=9, color=C.text_mute, bg=C.surface)
        self.vocab_msg.pack(side="left", padx=(10, 0))

    def _build_preset_adder_card(self, b):
        """Up to 4 custom intent presets for the Context Island (the slots right
        after the built-ins)."""
        import presets as _presets

        card = self._card(b)
        ui.label(card, "Preset Adder", size=12, semibold=True, bg=C.surface).pack(
            anchor="w"
        )
        ui.label(
            card,
            "Add your own intent presets to the Context Island. Title = the "
            "button label; Description = the one-liner shown under it (what it "
            "does); Instruction = the system directive sent to the AI.",
            size=10,
            color=C.text_mute,
            bg=C.surface,
            wrap=560,
        ).pack(anchor="w", pady=(2, 8))
        # Column captions so it's clear which field is which.
        head = tk.Frame(card, bg=C.surface)
        head.pack(fill="x")
        ui.label(head, "      Title", size=8, color=C.text_dim, bg=C.surface,
                 ).pack(side="left")
        ui.label(head, "        Description", size=8, color=C.text_dim,
                 bg=C.surface).pack(side="left", padx=(70, 0))
        ui.label(head, "Instruction", size=8, color=C.text_dim, bg=C.surface,
                 ).pack(side="left", padx=(80, 0))
        custom = _presets.load_custom()
        self._preset_vars = {}
        for slot in _presets.CUSTOM_SLOTS:
            v = custom.get(slot, {})
            row = tk.Frame(card, bg=C.surface)
            row.pack(fill="x", pady=(0, 6))
            ui.label(row, f"Slot {slot}", size=9, color=C.text_dim, bg=C.surface,
                     ).pack(side="left")
            tvar = tk.StringVar(value=v.get("title", ""))
            dvar = tk.StringVar(value=v.get("description", ""))
            ivar = tk.StringVar(value=v.get("instruction", ""))
            te = _entry(row, tvar, width=14)
            te.pack(side="left", padx=(8, 0), ipady=3)
            de = _entry(row, dvar, width=20)
            de.pack(side="left", padx=(6, 0), ipady=3)
            ie = _entry(row, ivar, width=34)
            ie.pack(side="left", padx=(6, 0), ipady=3)

            def make_clear(t=tvar, d=dvar, i=ivar):
                def clear():
                    t.set("")
                    d.set("")
                    i.set("")
                    self._save_presets()
                return clear

            ui.RoundButton(row, "✕", make_clear(), kind="ghost", bg=C.surface,
                           height=24, font_size=9, padx=8).pack(side="left", padx=(6, 0))
            self._preset_vars[slot] = (tvar, dvar, ivar)
        foot = tk.Frame(card, bg=C.surface)
        foot.pack(fill="x", pady=(4, 0))
        ui.RoundButton(foot, "Save presets", self._save_presets, kind="primary",
                       bg=C.surface, height=30, font_size=10).pack(side="left")
        nslots = len(_presets.CUSTOM_SLOTS)
        filled = sum(1 for s, v in custom.items() if v)
        self.preset_count_lbl = ui.label(
            foot, f"{filled} / {nslots} custom presets", size=9, color=C.text_mute,
            bg=C.surface,
        )
        self.preset_count_lbl.pack(side="left", padx=(10, 0))

    def _save_presets(self):
        import presets as _presets

        nslots = len(_presets.CUSTOM_SLOTS)
        custom = {}
        for slot, (tvar, dvar, ivar) in self._preset_vars.items():
            t, d, i = tvar.get().strip(), dvar.get().strip(), ivar.get().strip()
            if t and i:
                custom[slot] = {"title": t, "description": d, "instruction": i}
        try:
            _presets.save_custom(custom)
            self.preset_count_lbl.configure(
                text=f"{len(custom)} / {nslots} custom presets — saved."
            )
        except Exception as e:
            self.preset_count_lbl.configure(text=f"Couldn't save: {e}")

    def _save_vocabulary(self):
        raw = self.vocab_text.get("1.0", "end")
        vocab, terms, bad = {}, [], 0
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if "=" in line:
                wrong, _, right = line.partition("=")
                wrong, right = wrong.strip(), right.strip()
                if wrong and right:
                    vocab[wrong] = right
                else:
                    bad += 1
            else:
                terms.append(line)
        self.ctrl.set_vocabulary(vocab, terms)
        msg = f"Saved — {len(terms)} term{'s' if len(terms) != 1 else ''}"
        msg += f" + {len(vocab)} exact pair{'s' if len(vocab) != 1 else ''}." if vocab else "."
        if bad:
            msg += f" Skipped {bad} incomplete “wrong = right” line{'s' if bad != 1 else ''}."
        self.vocab_msg.configure(text=msg)

    def _polish_combo(self, holder):
        var = tk.StringVar(value=self.settings.get("polish_aggressiveness", "Light"))
        cb = ttk.Combobox(
            holder,
            textvariable=var,
            state="readonly",
            style="Mumble.TCombobox",
            width=22,
            values=["Light", "Standard", "Thorough"],
        )
        cb.pack()
        cb.bind(
            "<<ComboboxSelected>>",
            lambda e: self.ctrl.set_polish_aggressiveness(var.get()),
        )
        return cb

    def _build_search_engine_card(self, b):
        """Choose which site the Search hotkey opens: Google, Perplexity, or
        Brave. Perplexity opens with the question pre-filled and running."""
        card = self._card(b)
        ui.label(card, "Search engine", size=12, semibold=True,
                 bg=C.surface).pack(anchor="w")
        ui.label(
            card,
            "Where the Search hotkey sends your query. Perplexity opens with "
            "the question already typed in and the answer generating.",
            size=10, color=C.text_mute, bg=C.surface, wrap=560,
        ).pack(anchor="w", pady=(2, 8))
        row = tk.Frame(card, bg=C.surface)
        row.pack(anchor="w", fill="x")
        _LABELS = [("Google", "google"), ("Perplexity", "perplexity"),
                   ("Brave", "brave")]
        cur = (self.settings.get("search_engine", "google") or "google").lower()
        cur_label = next((lbl for lbl, v in _LABELS if v == cur), "Google")
        var = tk.StringVar(value=cur_label)
        cb = ttk.Combobox(
            row, textvariable=var, state="readonly", width=14,
            style="Mumble.TCombobox", values=[lbl for lbl, _ in _LABELS],
        )
        cb.pack(side="left")
        msg = ui.label(card, "", size=10, color=C.text_mute, bg=C.surface)

        def on_select(_e=None):
            engine = dict(_LABELS).get(var.get(), "google")
            saved = self.ctrl.set_search_engine(engine)
            msg.configure(text=f"Saved — searches now open {var.get()}.",
                          fg=C.gold)
            return saved

        cb.bind("<<ComboboxSelected>>", on_select)
        msg.pack(anchor="w", pady=(8, 0))

    def _hotkey_card(self, b, title, desc, key, apply_fn):
        card = self._card(b)
        ui.label(card, title, size=12, semibold=True, bg=C.surface).pack(anchor="w")
        ui.label(
            card,
            desc + "  Examples: ctrl+option+d, ctrl+option+v — or a mouse "
            "side-button: mouse:x2 (forward), mouse:x (back), mouse:middle. "
            "Click “Press” and tap a key or mouse button to capture it.",
            size=10,
            color=C.text_mute,
            bg=C.surface,
            wrap=560,
        ).pack(anchor="w", pady=(2, 8))
        row = tk.Frame(card, bg=C.surface)
        row.pack(anchor="w", fill="x")
        var = tk.StringVar(value=self.settings.get(key, ""))
        e = _entry(row, var, width=22)
        e.pack(side="left", ipady=5)
        msg = ui.label(card, "", size=10, color=C.text_mute, bg=C.surface)

        def apply():
            ok, m = apply_fn(var.get().strip())
            msg.configure(text=m, fg=C.gold if ok else C.red)

        ui.RoundButton(row, "Apply", apply, kind="primary", bg=C.surface).pack(
            side="left", padx=(10, 0)
        )

        cap = ui.RoundButton(row, "Press", None, kind="ghost", bg=C.surface)

        def capture():
            cap.set_text("Press…")
            msg.configure(text="Press a key, a combo, or a mouse button…",
                          fg=C.text_mute)
            self.win.update_idletasks()

            def worker():
                if getattr(self, "_capturing_key", False):
                    return
                self._capturing_key = True
                got = ""
                try:
                    import bindings as _bindings
                    got = _bindings.capture(timeout=15.0)
                except Exception as ex:
                    print("hotkey capture error:", ex)
                finally:
                    self._capturing_key = False

                def done():
                    cap.set_text("Press")
                    if got:
                        var.set(got)
                        ok, m = apply_fn(got)
                        msg.configure(text=m, fg=C.gold if ok else C.red)
                    else:
                        msg.configure(text="Didn't catch anything — type it "
                                           "instead.", fg=C.red)
                try:
                    self.ctrl._tk_schedule(done)
                except Exception:
                    pass

            import threading
            threading.Thread(target=worker, daemon=True).start()

        cap.command = capture
        cap.pack(side="left", padx=(8, 0))
        msg.pack(anchor="w", pady=(8, 0))

    def _row(self, parent, title, desc, widget):
        r = tk.Frame(parent, bg=C.surface)
        r.pack(fill="x", pady=8)
        left = tk.Frame(r, bg=C.surface)
        left.pack(side="left", fill="x", expand=True)
        ui.label(left, title, size=11, semibold=True, bg=C.surface).pack(anchor="w")
        if desc:
            ui.label(
                left, desc, size=10, color=C.text_mute, bg=C.surface, wrap=440
            ).pack(anchor="w", pady=(2, 0))
        holder = tk.Frame(r, bg=C.surface)
        holder.pack(side="right", padx=(16, 0))
        widget(holder)

    def _model_combo(self, h):
        var = tk.StringVar(value=self.settings.get("model", "base.en"))
        cb = ttk.Combobox(
            h,
            textvariable=var,
            state="readonly",
            width=14,
            style="Mumble.TCombobox",
            values=["tiny.en", "base.en", "small.en", "medium.en"],
        )
        cb.pack()

        def on_select(e):
            name = var.get()
            # Immediate "loading" feedback — switching can take a few seconds, and
            # silence here is exactly why it felt like models "don't load".
            try:
                self._model_status_label.configure(
                    text=f"◌ loading {name}…  (a few seconds)", fg=C.text_mute
                )
            except Exception:
                pass

            def done(ok, nm):
                def upd():
                    try:
                        if ok:
                            self._model_status_label.configure(
                                text=f"● {nm} loaded", fg=C.gold_hi
                            )
                        else:
                            self._model_status_label.configure(
                                text=f"✕ couldn't load {nm} — kept the previous model",
                                fg=C.red,
                            )
                    except Exception:
                        pass

                try:
                    self.ctrl._tk_schedule(upd)
                except Exception:
                    pass

            self.ctrl.set_model(name, on_done=done)

        cb.bind("<<ComboboxSelected>>", on_select)

    def _lang_entry(self, h):
        var = tk.StringVar(value=self.settings.get("language", "en"))
        e = _entry(h, var, width=8)
        e.pack(ipady=4)
        e.bind(
            "<FocusOut>", lambda ev: self.ctrl.set_language(var.get().strip() or "en")
        )
        e.bind("<Return>", lambda ev: self.ctrl.set_language(var.get().strip() or "en"))

    # ----------------------------------------------------------- interactions
    def _save_mic(self):
        values = getattr(self, "_mic_values", {})
        mic_idx = values.get(self.mic_var.get())
        if mic_idx is None:
            self.mic_saved_msg.configure(text="Mic no longer available", fg=C.red)
            return
        res = self.ctrl.set_mic(mic_idx)
        # set_mic now validates and returns (ok, message)
        ok, msg = res if isinstance(res, tuple) else (True, "Saved.")
        try:
            self.mic_saved_msg.configure(text=msg, fg=C.gold if ok else C.red)
            if not ok:
                # revert the dropdown to the mic that's actually saved
                self._populate_mics()
        except Exception:
            pass

    def _toggle_autostart(self, value):
        self.ctrl.set_autostart(value)

    def _reset_settings(self):
        """Reset all settings to factory defaults, then reload the UI."""
        self.ctrl.settings.reset_all()
        self._build_settings(self.sections["settings"].body)

    def _draw_meter(self, level):
        self.meter.delete("all")
        ui.round_rect(self.meter, 0, 0, 200, 10, 5, fill=C.track, outline="")
        w = max(0, min(1.0, level)) * 200
        if w > 4:
            ui.round_rect(self.meter, 0, 0, w, 10, 5, fill=C.gold, outline="")

    def _test_mic(self):
        self.ctrl.test_microphone(
            lambda lv: self.ctrl._tk_schedule(self._draw_meter, lv),
            lambda: self.ctrl._tk_schedule(lambda: self.win.after(400, lambda: self._draw_meter(0.0))),
        )

    def _populate_mics(self):
        mics = self.ctrl.list_microphones()
        self._mic_values, labels, cur = {}, [], self.settings.get("mic_device", None)
        cur_label = None
        for idx, name in mics:
            self._mic_values[name] = idx
            labels.append(name)
            if idx == cur:
                cur_label = name
        self.mic_combo.configure(values=labels)
        self.mic_var.set(cur_label or (labels[0] if labels else ""))

    def _update_streak_label(self):
        """Refresh the streak badge next to the version number."""
        try:
            cur, best = self.ctrl.streak()
            if cur > 0:
                self.streak_lbl.configure(text=f"🔥 {cur}d")
            else:
                self.streak_lbl.configure(text="")
        except Exception:
            pass

    def _poll_status(self):
        if not self.visible:
            return
        state, text = self.ctrl.status()
        try:
            self.status_dot.configure(image=self._dot(STATE_COLORS.get(state, C.gold)))
        except Exception:
            pass
        self.status_lbl.configure(text=text)
        self._update_streak_label()
        self._status_job = self.win.after(500, self._poll_status)

    def show(self):
        self.visible = True
        try:
            self._populate_mics()
        except Exception:
            pass
        self._apply_geometry()  # size + position BEFORE showing
        self.win.deiconify()
        self.win.after(30, self._apply_geometry)  # re-apply after WM settles
        self.win.lift()
        self.win.attributes("-topmost", True)
        self.win.after(260, lambda: self.win.attributes("-topmost", False))
        self.win.focus_force()
        # Cancel any timer chains still live from a prior show() so we never stack
        # two (double-speed logo / double-rate status polling).
        for job in ("_status_job", "_logo_job"):
            j = getattr(self, job, None)
            if j:
                try:
                    self.win.after_cancel(j)
                except Exception:
                    pass
                setattr(self, job, None)
        self._poll_status()
        self._animate_logo()

    def hide(self):
        self.visible = False
        for job in ("_status_job", "_logo_job"):
            j = getattr(self, job, None)
            if j:
                try:
                    self.win.after_cancel(j)
                except Exception:
                    pass
        try:
            self.win.wm_state("normal")  # un-maximize so next show doesn't flash
        except Exception:
            pass
        self.win.withdraw()
