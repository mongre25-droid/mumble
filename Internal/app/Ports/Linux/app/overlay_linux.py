#!/usr/bin/env python3
"""Mumble's floating "island" — Linux (GTK3 + Cairo) edition.

TODO (L-023): The overlay state engine (~500 lines of _tick, _build_snapshot,
set_state, set_mode, animation constants) is shared logic duplicated across
Windows (overlay.py), macOS (overlay_mac.py), and here. Extract the common
state-engine core into a platform-agnostic module (e.g., island_state.py or
island_engine.py) so a timing/behaviour fix only needs to be made ONCE. The
rendering backends (Win32 LAYERED, Cocoa NSWindow, GTK Cairo) would consume
the same snapshot dict and render it their own way.

The native Linux replacement for the Windows ``mumble/overlay.py`` (_GlassPill, a
per-pixel-alpha LAYERED Win32 window) and the macOS ``MacMumble/app/overlay_mac.py``
(a transparent Tk canvas). On Linux we get TRUE per-pixel alpha — real anti-aliased
rounded corners, a soft drop shadow and a gold glow halo — from a single
undecorated, always-on-top, click-through Gtk.Window that paints an RGBA Cairo
surface in its ``draw`` handler.

The pill is a small content-sized capsule centred near the bottom of the ACTIVE
monitor, hidden while idle, that shows dictation status:
    listening    -> gold dot + waveform reacting to your voice + "Listening"
    transcribing -> amber dot + breathing dots + "Transcribing"
    building     -> mode-colour dot + pulsing dots + "Building · <Mode>"
                    (context jobs show the gold-particle gathering MOTION)
    done         -> a brief flash in the active mode's colour + "Pasted!/Saved"
    hint/suggest -> gold-pulsing "Ctrl + Alt + H" (or violet "wrong mode?") INLINE

The visual STATE ENGINE (setters, _mode_names, _build_snapshot, the _tick
animation cadence + frame/countdown logic and every transient-duration constant)
is ported FAITHFULLY from overlay_mac.py so the timing matches across platforms.
The actual PIXELS come from ``island_render.render(snapshot)`` — the same pure
Pillow renderer the Windows/macOS builds use — so all three islands look identical.

Two non-obvious pieces make this work on Linux:
  • PIL RGBA → Cairo: we wrap the Pillow image in a GdkPixbuf and let
    ``Gdk.cairo_set_source_pixbuf`` blit it. That avoids the premultiplied-BGRA
    byte-swizzle Cairo's ARGB32 format demands on little-endian (a correct manual
    swizzle is provided as a fallback for the headless/no-Gdk path).
  • CLICK-THROUGH: ``input_shape_combine_region`` with an EMPTY cairo.Region() so
    every click falls through to the window behind. Re-applied on realize and on
    each show, alongside keep-above.

ROBUSTNESS — "the island can never go missing": the ``gi`` import is guarded, so
this module imports cleanly on CI / Windows / macOS (where GTK is absent). There
the Island degrades to a no-op that still satisfies the FULL public API (every
method present, every attribute set) and logs once. If RGBA compositing is
unavailable at runtime we still show the pill (opaque fallback) rather than crash.

THREAD-SAFETY: the public setters (set_state/set_level/set_armed/set_building/
flash/hint/suggest/wake/…) are called from non-GTK worker threads. They ONLY
assign plain Python fields — they NEVER touch GTK. Every GTK call happens on the
GTK main thread, driven by a GLib.timeout tick (and wake() bounces a repaint onto
the main loop via GLib.idle_add).
"""

import math
import os
import sys
import time

# ---- GTK import guard ------------------------------------------------------
# Import gi / Gtk / Gdk / GdkPixbuf / GLib defensively so this module IMPORTS
# everywhere (CI, Windows, macOS, headless test runners) even when GTK is not
# installed. _HAVE_GTK gates the real window; without it the Island is a no-op
# that still honours the full API (see _NoopIsland), logged exactly once.
_HAVE_GTK = False
_GTK_IMPORT_ERR = None
try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GLib, Gdk, GdkPixbuf, Gtk

    import cairo

    _HAVE_GTK = True
except Exception as _e:  # pragma: no cover - only where GTK/gi is absent
    _GTK_IMPORT_ERR = _e

# island_render is the pure-Pillow renderer (no GTK dependency). It MUST be
# available — it is the island's only look. Guarded so the import never explodes;
# if it is somehow missing the Island degrades to the no-op shell.
_HAVE_RENDER = False
try:
    import island_render

    _HAVE_RENDER = True
except Exception as _e:  # pragma: no cover
    print("island renderer unavailable (island will be a no-op):", _e)

from PIL import Image

from branding import C, MODE_COLORS, MODE_LABELS

# ---- module-level constants — PORTED 1:1 from overlay_mac.py ----------------
# The canvas bounds + transient-flash durations. The Pillow renderer owns the
# real pixel geometry (island_render.WIN_W/WIN_H/PILL_H); these drive the STATE
# ENGINE's timing (countdowns, fades) and the window size, and must match macOS.
W, H = 380, 30
BOTTOM_MARGIN = 28

# Completion-flash duration: 30 ticks ≈ 1.35s at the 45ms on-screen rate — long
# enough to read "Pasted!/Saved · Mode", short enough that nothing lingers. Every
# completion/feedback flash (paste, copy, convert done) uses this ONE value.
# This matches the Windows canonical overlay.py value exactly for cross-platform
# visual equivalence (VAL-PLAT-010).
DONE_FRAMES = 30

HINT_FRAMES = 90         # ~4s paste hint (owner v9: trimmed from ~7s)
# Clarification chips ("wrong mode?") are INTERACTIVE — the user can hold the mode
# key and re-speak while they're up. They FADE in ~2s like every other transient
# chip; the countdown FREEZES while the mode key is held (see _tick), so holding
# to respeak never races the fade.
SUGGEST_FRAMES = 46      # ~2.1s at 22fps (visible ~1s, then dissolves over ~1s)
FADE_OUT_FRAMES = 22     # the chip dissolves over its final ~1s instead of snapping
# Refinement pass §1 (motion): the island FADES IN over its first few frames when
# it appears, instead of snapping into view — a quieter, more premium entrance.
APPEAR_FRAMES = 7        # ~0.3s fade-in on first appearance
APPEAR_FLOOR = 0.30      # starting opacity of the fade-in ramp

# Pill cosmetic rim colours (used only to derive per-state rim tints for the
# snapshot — island_render does the real drawing). Mirrors overlay_mac.
_PILL_RIM = "#3A3320"
_SUGGEST = "#A855F7"      # vivid purple — the "which mode?" clarification chip

# state → readable label (HTML-island parity: the pill always says what Mumble is
# doing, next to the animation, never animation alone).
_STATE_LABEL = {
    "listening": "Listening",
    "search": "Search",
    "transcribing": "Transcribing",
    "building": "Building",
    "done": "Done",
}

# Linux island wording: the not-pasted "Saved" tail and the paste hint use the
# Linux hotkeys (Ctrl+Alt+H / Ctrl+Alt+V) — NOT the macOS Cmd+Option phrasing.
_HOTKEY_HISTORY = "Ctrl + Alt + D"

# The window is repainted as a fixed-size canvas the size of the Pillow image
# (island_render.WIN_W × WIN_H). The pill floats centred inside that canvas, so
# the window never resizes per-frame — only the painted pill width changes.
if _HAVE_RENDER:
    CANVAS_W, CANVAS_H = island_render.WIN_W, island_render.WIN_H
else:
    CANVAS_W, CANVAS_H = 460, 64
# px from the window's bottom edge to the monitor work-area bottom. The pill is
# centred inside the canvas, so the visible-pill-to-screen gap ≈ BOTTOM_GAP +
# (canvas bottom padding). Tuned to read like the Windows BOTTOM_GAP of 14.
BOTTOM_GAP = 14


def _hex(h):
    """'#rrggbb' (or an (r,g,b) tuple) → (r, g, b) ints."""
    if isinstance(h, (tuple, list)):
        return (int(h[0]), int(h[1]), int(h[2]))
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _blend(c1, c2, t):
    """Blend two colours (hex strings or RGB tuples) → a '#rrggbb' string.

    Clamps t to [0,1]: a factor outside that range extrapolates channels past
    0/255, which "%02x" turns into a malformed (>2-digit) hex string — a whole
    class of "invalid color name" bugs. A blend factor is always meant to be in
    [0,1], so clamping only ever prevents one (ported from overlay_mac._blend)."""
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    a, b = _hex(c1), _hex(c2)
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


# ===========================================================================
# Monitor placement
# ===========================================================================
def _active_monitor_workarea():
    """Work-area rect (x, y, w, h) of the ACTIVE monitor — the one under the
    pointer, else the primary — so the island follows you across laptop / desktop
    / ultrawide every time it appears. Returns None if Gdk can't tell us (the
    caller then falls back to the screen geometry)."""
    if not _HAVE_GTK:
        return None
    try:
        display = Gdk.Display.get_default()
        if display is None:
            return None
        monitor = None
        # Prefer the monitor under the pointer (the screen the user is working on).
        try:
            seat = display.get_default_seat()
            pointer = seat.get_pointer() if seat is not None else None
            if pointer is not None:
                screen, px, py = pointer.get_position()
                monitor = display.get_monitor_at_point(px, py)
        except Exception:
            monitor = None
        if monitor is None:
            try:
                monitor = display.get_primary_monitor()
            except Exception:
                monitor = None
        if monitor is None and display.get_n_monitors() > 0:
            monitor = display.get_monitor(0)
        if monitor is None:
            return None
        wa = monitor.get_workarea()  # Gdk.Rectangle in logical (scaled) px
        return (wa.x, wa.y, wa.width, wa.height)
    except Exception:
        return None


# ===========================================================================
# Companion Bar — GTK3 clickable control strip (Linux edition)
# ===========================================================================
class _NoopIsland:
    """A do-nothing island that still honours the COMPLETE public API and every
    documented attribute, so importing this module and constructing an Island can
    NEVER crash a non-Linux / headless run. Logs the reason exactly once."""

    _warned = False

    def __init__(self, root, style=None):
        self.root = root
        self.style = "enhanced"
        self.enhanced = True
        self.state = "idle"
        self.level = 0.0
        self.frame = 0
        self.visible = False
        self.armed = False
        self.is_suggest = False
        self.is_gathering = False
        self.suspended = False
        self.deck_open = False
        self._picker_open = False
        self._deck_close = None
        self._picker_done = None
        self._suspended_since = 0.0
        self.bar_state = {
            "modes": [("prompt", "Prompt")],
            "active": None,
            "foreign_on": False,
            "show_foreign": False,
            "expanded": False,
        }
        self.armed_color = None
        self.on_mode = None
        self.on_deck = None
        self.on_foreign = None
        if not _NoopIsland._warned:
            _NoopIsland._warned = True
            why = _GTK_IMPORT_ERR if not _HAVE_GTK else "island_render missing"
            print("overlay_linux: GTK island unavailable — running headless "
                  f"no-op island ({why})")

    # All setters just record state (harmless) and never touch any GUI.
    def set_state(self, s): self.state = s
    def set_level(self, lvl): self.level = lvl
    def set_armed(self, on):
        self.armed = bool(on)

    def set_building(self, mode, offline=False, gathering=None):
        self.state = "building"

    def flash(self, mode, offline=False, pasted=True):
        self.state = "done"

    def hint(self, text):
        self.state = "hint"

    def suggest(self, text):
        self.is_suggest = True
        self.state = "hint"

    def wake(self): pass
    def close_picker(self): pass
    def set_style(self, style=None): pass
    def close_deck(self): pass
    def set_widget_callbacks(self, on_mode=None, on_deck=None, on_foreign=None):
        self.on_mode, self.on_deck, self.on_foreign = on_mode, on_deck, on_foreign
    def set_bar_state(self, modes=None, active="\x00", foreign_on=None,
                      show_foreign=None):
        if modes is not None: self.bar_state["modes"] = list(modes)
        if active != "\x00": self.bar_state["active"] = active
        if foreign_on is not None: self.bar_state["foreign_on"] = bool(foreign_on)
        if show_foreign is not None: self.bar_state["show_foreign"] = bool(show_foreign)
        self.armed = bool(self.bar_state["active"])
    def set_focused(self, focused): pass
    def _widget_mode(self, key):
        if callable(self.on_mode): self.on_mode(key)
    def _widget_deck(self):
        if callable(self.on_deck): self.on_deck()
    def _widget_foreign(self):
        if callable(self.on_foreign): self.on_foreign()
    def _widget_expand(self):
        self.bar_state["expanded"] = not self.bar_state.get("expanded", False)


# ===========================================================================
# The real GTK island
# ===========================================================================
class _GtkIsland:
    """The premium island as a GTK3 window.

    Owns ONE Gtk.Window: undecorated, keep-above, skip-taskbar/pager, never
    accepts focus, type-hint NOTIFICATION — so dictation focus stays exactly
    where the user is typing. It paints an RGBA Cairo surface (the Pillow image
    from island_render) in its ``draw`` handler with real per-pixel alpha, and is
    click-through via an empty input shape. The visual state engine below is a
    faithful port of overlay_mac.Island."""

    def __init__(self, root, style=None):
        self.root = root  # the _GlibRoot shim (or None) — accepted, not required
        # ONE island look (owner v6) — no Basic/Standard/Enhanced tiers. `style`
        # and `enhanced` are accepted-but-fixed for call-site back-compat.
        self.style = "enhanced"
        self.enhanced = True

        # ---- visual state (plain fields only — written by worker threads) ----
        self.state = "idle"
        self.level = 0.0
        self.frame = 0
        self.visible = False
        self.armed = False           # mode key held → near-white "armed" ring
        self.is_suggest = False      # the chip is a mode suggestion (violet)
        self.is_gathering = False    # context job pulling material in → gold motes
        self.suspended = False       # True while the interactive question panel is up
        self.focused = True           # Perf: pause the animation when the app loses focus
        self.deck_open = False        # History window open (controller-managed)
        self._picker_open = False    # "Which mode?" picker open (unused on Linux)
        self._deck_close = None
        self._picker_done = None
        self._suspended_since = 0.0  # timestamp when suspended started (self-heal)

        # THE BIG SHIFT — the companion bar is the island's MODE DECK: a row of
        # selectable mode chips + Deck + an optional Foreign toggle. `bar_state` is
        # the live snapshot the bar renders + hit-tests from; the controller pushes
        # it via set_bar_state(). `armed_color` lets the island ring echo the active
        # mode's colour (cohesion), defaulting to Prompt purple.
        self.bar_state = {
            "modes": [("prompt", "Prompt")],
            "active": None,
            "foreign_on": False,
            "show_foreign": False,
            "expanded": False,
        }
        self.armed_color = MODE_COLORS.get("prompt", C.gold)

        self.done_color = C.gold
        self.done_colors = [C.gold]
        self.done_left = 0
        self.build_color = C.gold
        self.build_colors = [C.gold]
        self.build_label = ""
        self.build_offline = False
        self.done_label = ""
        self.flash_offline = False
        self.flash_pasted = True     # done verb: True→"Pasted!", False→"Saved"
        self.hint_text = ""
        self.hint_left = 0
        self._prev_state = ""
        self._was_active = False   # fade-in tracking: idle→active transition arms appear
        self._appear_left = 0      # counter for the fade-in animation ramp
        self._listen_start = 0.0

        # The current frame's RGBA Pillow image to blit (set on the GTK thread by
        # _paint_frame; read by the draw handler). Starts None → draw clears clear.
        self._cur_img = None
        self._compositing_ok = True   # flipped False if no RGBA visual (opaque fallback)

        # ---- widget bar callbacks (wired by the controller) ----
        self.on_mode = None
        self.on_deck = None
        self.on_foreign = None
        self._bar_hitbox = None
        self._input_shape_key = None

        # ---- build the window ----
        self.win = None
        self._ok = False
        try:
            self._build_window()
            self._ok = True
        except Exception as e:  # never let window setup crash the app
            print("overlay_linux: GTK window setup failed — island disabled:", e)
            self._ok = False

        # ---- start the animation tick on the GTK main loop ----
        if _HAVE_GTK:
            try:
                GLib.timeout_add(200, self._tick)  # relaxed idle cadence to start
            except Exception as e:
                print("overlay_linux: could not start island tick:", e)

    # ---- window construction (GTK thread) ----------------------------------
    def _build_window(self):
        win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        win.set_decorated(False)
        win.set_keep_above(True)
        win.set_skip_taskbar_hint(True)
        win.set_skip_pager_hint(True)
        win.set_accept_focus(False)
        win.set_focus_on_map(False)
        win.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        win.set_resizable(False)
        win.set_app_paintable(True)
        win.set_default_size(CANVAS_W, CANVAS_H)
        win.set_size_request(CANVAS_W, CANVAS_H)
        try:
            win.stick()  # show on all workspaces, like a status overlay should
        except Exception:
            pass

        # TRUE transparency: paint onto the screen's RGBA visual. Without a
        # compositing manager this visual may be absent — we then fall back to an
        # opaque pill (still shown) rather than crash (see _on_draw).
        screen = win.get_screen()
        try:
            rgba = screen.get_rgba_visual()
        except Exception:
            rgba = None
        if rgba is not None:
            win.set_visual(rgba)
            self._compositing_ok = True
        else:
            self._compositing_ok = False
            print("overlay_linux: no RGBA visual / compositor — opaque pill fallback")

        win.connect("draw", self._on_draw)
        win.connect("realize", self._on_realize)
        win.connect("screen-changed", self._on_screen_changed)
        win.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        win.connect("button-press-event", self._on_button_press)

        self.win = win
        # Realize now (off-screen) so input_shape_combine_region has a GdkWindow to
        # apply to; keep it hidden until an active state shows it.
        win.realize()
        self._apply_click_through()

    def _on_screen_changed(self, widget, old_screen):
        """Re-pick the RGBA visual if the screen (and thus its compositor) changes."""
        try:
            screen = widget.get_screen()
            rgba = screen.get_rgba_visual()
            if rgba is not None:
                widget.set_visual(rgba)
                self._compositing_ok = True
            else:
                self._compositing_ok = False
        except Exception:
            pass

    def _on_realize(self, widget):
        """Re-assert click-through once the GdkWindow exists (a realize can drop a
        previously-set input shape)."""
        self._input_shape_key = None
        self._apply_click_through()

    def _apply_click_through(self):
        """Keep the island click-through while exposing only the optional bar.

        The composite GTK window contains both surfaces.  Its lower island pill
        must never steal clicks from the user's editor, while the mode-deck bar
        needs a small input region for its controls.
        """
        if not _HAVE_GTK or self.win is None:
            return
        try:
            gdk_win = self.win.get_window()
            if gdk_win is not None:
                hit = self._bar_hitbox
                key = tuple(hit) if hit else ()
                if key == self._input_shape_key:
                    return
                if hit:
                    x, y, width, height = (int(v) for v in hit)
                    region = cairo.Region(cairo.RectangleInt(x, y, width, height))
                else:
                    region = cairo.Region()
                gdk_win.input_shape_combine_region(region, 0, 0)
                self._input_shape_key = key
        except Exception:
            # input shaping needs a compositor; if it fails the pill is still a
            # NOTIFICATION/keep-above window that does not take focus — acceptable.
            pass

    def _on_button_press(self, _widget, event):
        """Dispatch a click in the bar without ever focusing the GTK window."""
        hit = self._bar_hitbox
        if not hit:
            return False
        ox, oy, width, height = hit
        event_x, event_y = float(event.x), float(event.y)
        if not (ox <= event_x <= ox + width
                and oy <= event_y <= oy + height):
            return False
        snap = dict(self.bar_state)
        layout = island_render.bar_layout(snap)
        # Layout coordinates are relative to the full bar canvas, whereas the
        # input region starts at the visible pill's left edge.
        x = event_x - (ox - layout["pill"][0])
        for key, x0, x1 in layout.get("chips", []):
            if x0 <= x <= x1:
                # A collapsed chip is the selector opener even when it displays
                # the active mode. Selecting happens only from the expanded row.
                if not layout.get("expanded") or key is None:
                    self._widget_expand()
                else:
                    self._widget_mode(key)
                return True
        caret = layout.get("caret")
        if caret and caret[0] <= x <= caret[1]:
            self._widget_expand()
            return True
        deck = layout.get("deck")
        if deck and deck[0] <= x <= deck[1]:
            self._widget_deck()
            return True
        foreign = layout.get("foreign")
        if foreign and foreign[0] <= x <= foreign[1]:
            self._widget_foreign()
            return True
        return False

    # ---- the paint handler (GTK thread) ------------------------------------
    def _on_draw(self, widget, ctx):
        """Clear the whole window to fully transparent, then blit the current PIL
        RGBA image. With a compositor this yields real per-pixel alpha; without
        one (no RGBA visual) the clear is opaque black and we still show the pill
        (better an opaque pill than no island at all)."""
        try:
            if self._compositing_ok:
                # Clear to fully transparent: SOURCE operator + a zero-alpha paint
                # REPLACES whatever was there (vs OVER, which would leave residue).
                ctx.save()
                ctx.set_operator(cairo.OPERATOR_SOURCE)
                ctx.set_source_rgba(0, 0, 0, 0)
                ctx.paint()
                ctx.restore()
            else:
                # No alpha channel available → opaque dark clear so the pill reads.
                ctx.save()
                ctx.set_source_rgb(*[v / 255.0 for v in _hex(C.bg)])
                ctx.paint()
                ctx.restore()

            img = self._cur_img
            if img is None:
                return False
            self._blit_pil(ctx, img)
        except Exception as e:
            # A bad frame must never kill the window; just skip it.
            print("overlay_linux draw error (continuing):", e)
        return False

    def _blit_pil(self, ctx, pil_img):
        """Blit a PIL RGBA image onto the Cairo context.

        TRICKY BIT — Cairo's ARGB32 surface stores *premultiplied BGRA* on
        little-endian, which does NOT match PIL's straight RGBA byte order. Two
        correct routes:
          1) PREFERRED: wrap the bytes in a GdkPixbuf (which IS straight RGBA) and
             let Gdk.cairo_set_source_pixbuf do the conversion. No manual swizzle,
             no premultiply — Gdk handles both. This is the robust path.
          2) FALLBACK: build a cairo ImageSurface ourselves with a correct manual
             swizzle (RGBA → premultiplied BGRA). Used only if the pixbuf route
             raises (kept for resilience)."""
        if pil_img.mode != "RGBA":
            pil_img = pil_img.convert("RGBA")
        w, h = pil_img.size
        try:
            data = GLib.Bytes.new(pil_img.tobytes())  # straight RGBA, row-major
            pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
                data, GdkPixbuf.Colorspace.RGB, True, 8, w, h, w * 4)
            Gdk.cairo_set_source_pixbuf(ctx, pixbuf, 0, 0)
            ctx.paint()
            return
        except Exception as e:
            # Fall through to the manual swizzle route below.
            print("overlay_linux: pixbuf blit failed, using manual swizzle:", e)
        try:
            surface = self._pil_to_cairo_surface(pil_img)
            ctx.set_source_surface(surface, 0, 0)
            ctx.paint()
        except Exception as e:
            print("overlay_linux: manual surface blit failed:", e)

    @staticmethod
    def _pil_to_cairo_surface(pil_img):
        """Manual PIL RGBA → Cairo ARGB32 surface (premultiplied BGRA, little-endian).

        For each pixel Cairo wants bytes laid out B, G, R, A with R/G/B already
        multiplied by A/255. We do exactly that, then hand Cairo a writable buffer
        of the right stride."""
        import array

        w, h = pil_img.size
        src = pil_img.tobytes()  # RGBA, length w*h*4
        stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_ARGB32, w)
        buf = bytearray(stride * h)
        for y in range(h):
            row_src = y * w * 4
            row_dst = y * stride
            for x in range(w):
                si = row_src + x * 4
                r, g, b, a = src[si], src[si + 1], src[si + 2], src[si + 3]
                # premultiply
                pr = (r * a) // 255
                pg = (g * a) // 255
                pb = (b * a) // 255
                di = row_dst + x * 4
                buf[di] = pb       # little-endian ARGB32 → byte order B,G,R,A
                buf[di + 1] = pg
                buf[di + 2] = pr
                buf[di + 3] = a
        data = array.array("B", buf)
        return cairo.ImageSurface.create_for_data(
            memoryview(data), cairo.FORMAT_ARGB32, w, h, stride)

    # ===================================================================
    # Thread-safe public setters — assign PLAIN FIELDS ONLY, never touch GTK.
    # (Ported 1:1 from overlay_mac.Island.) The controller marshals a wake()
    # after each so the change repaints within a frame on the GTK thread.
    # ===================================================================
    def set_state(self, s):
        """Thread-safe setter for the island's visual state."""
        self.state = s

    def set_level(self, lvl):
        """Thread-safe setter for the current audio volume level (0.0 to 1.0)."""
        self.level = lvl

    def set_armed(self, on):
        """THE BIG SHIFT: `armed` now mirrors the sticky Prompt-mode toggle (the
        held mode key is retired). When on, the island rings in Prompt purple and
        the companion bar lights its Prompt pill — so you can SEE Prompt mode is on."""
        self.armed = bool(on)

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

    def flash(self, mode, offline=False, pasted=True):
        """Flash the island's finished state. Accepts a single mode or list of
        modes. `pasted` decides the verb: True → "Pasted!" (the text actually
        landed in a focused field), False → "Saved" (kept in History but not
        pasted) — never claim "Pasted" when nothing was."""
        names, _ = self._mode_names(mode)
        self.is_gathering = False  # the finish flash never shows gathering motion
        self.done_colors = [MODE_COLORS.get(m, C.gold) for m in names]
        self.done_color = self.done_colors[0]
        self.done_label = " + ".join(dict.fromkeys(
            MODE_LABELS.get(m, str(m).title()) for m in names))
        self.flash_offline = offline
        self.flash_pasted = bool(pasted)
        # One uniform ~2s linger for BOTH outcomes (owner v9).
        self.done_left = DONE_FRAMES
        self.state = "done"

    def hint(self, text):
        """Show the gold "Ctrl + Alt + H" paste reminder INLINE in the pill."""
        self.hint_text = text or _HOTKEY_HISTORY
        self.hint_left = HINT_FRAMES
        self.is_suggest = False
        self.state = "hint"

    def suggest(self, text):
        """Show the dismissible 'wrong mode?' chip (violet). The countdown freezes
        while the mode key is held so the user can re-press + re-speak."""
        self.hint_text = text or "wrong mode?"
        self.hint_left = SUGGEST_FRAMES
        self.is_suggest = True
        self.state = "hint"

    # ---- THE BIG SHIFT: companion control-bar callbacks ----
    def set_widget_callbacks(self, on_mode=None, on_deck=None, on_foreign=None):
        """Wire mode selection, Deck opening, and Foreign toggling."""
        self.on_mode = on_mode
        self.on_deck = on_deck
        self.on_foreign = on_foreign

    def set_bar_state(self, modes=None, active="\x00", foreign_on=None,
                      show_foreign=None):
        """Update only the supplied fields of the controller-owned snapshot."""
        if modes is not None:
            self.bar_state["modes"] = list(modes)
        if active != "\x00":
            self.bar_state["active"] = active
        if foreign_on is not None:
            self.bar_state["foreign_on"] = bool(foreign_on)
        if show_foreign is not None:
            self.bar_state["show_foreign"] = bool(show_foreign)
        self.armed = bool(self.bar_state["active"])
        if self.bar_state["active"]:
            self.armed_color = MODE_COLORS.get(
                self.bar_state["active"], C.gold)

    def _widget_mode(self, key):
        self.bar_state["expanded"] = False
        if callable(self.on_mode):
            self.on_mode(key)

    def _widget_deck(self):
        if callable(self.on_deck):
            self.on_deck()

    def _widget_foreign(self):
        if callable(self.on_foreign):
            self.on_foreign()

    def _widget_expand(self):
        self.bar_state["expanded"] = not self.bar_state.get("expanded", False)
        self.wake()

    # ---- API no-ops / controller hooks (parity with overlay_mac) -----------
    def close_picker(self):
        """NO-OP on Linux — the interactive 'Which mode?' picker is unused here."""
        return

    def set_style(self, style=None):
        """NO-OP (owner v6): there is exactly ONE island look now."""
        return

    def close_deck(self):
        """Dismiss the History window if the controller registered a closer. Safe to
        call when nothing is open. Runs on the GTK thread."""
        fn = getattr(self, "_deck_close", None)
        if fn is not None:
            try:
                fn()
            except Exception:
                pass

    # ===================================================================
    # Snapshot — the immutable per-frame visual description island_render eats.
    # Ported 1:1 from overlay_mac._build_snapshot (Linux hotkey wording aside).
    # ===================================================================
    def _listen_timer(self):
        start = self._listen_start or time.time()
        secs = max(0.0, time.time() - start)
        return f"{int(secs // 60)}:{int(secs % 60):02d}"

    def _build_snapshot(self):
        """One immutable visual snapshot the Pillow renderer turns into a frame:
        state colour, label, hint, timer, level, armed/suggest/offline flags and
        the mode-colour ramp."""
        state = self.state
        rim = _PILL_RIM
        dot = C.gold
        label = _STATE_LABEL.get(state, "")
        label_color = C.text_dim
        hint = ""
        timer = ""
        colors = None
        offline = False
        gathering = bool(getattr(self, "is_gathering", False))

        if state == "listening":
            dot = C.gold
            label_color = C.gold
            rim = _blend(_PILL_RIM, C.gold, 0.4)
            timer = self._listen_timer()
        elif state == "search":
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
            pasted = getattr(self, "flash_pasted", True)
            label = "Pasted!" if pasted else "Saved"
            # Not pasted → the tail TELLS the user how to place it (the History
            # hotkey) instead of the mode name. Linux wording: Ctrl + Alt + H.
            hint = getattr(self, "done_label", "") if pasted else _HOTKEY_HISTORY
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
        # their final frames instead of snapping to nothing (1.0 = fully opaque).
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
        active_now = state in ("listening", "search", "transcribing",
                               "building", "done", "hint", "error")
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
            "armed": bool(self.armed and state in (
                "listening", "transcribing", "building")),
            # The island ring echoes the ACTIVE mode's colour (cohesion with
            # the mode-deck above), defaulting to Prompt purple.
            "armed_color": getattr(self, "armed_color", None)
            or MODE_COLORS.get("prompt", C.gold),
            "suggest": bool(self.is_suggest and state == "hint"),
            "offline": offline, "gathering": gathering, "fade": fade,
            "label": label, "hint": hint, "timer": timer,
            "dot": dot, "rim": rim, "label_color": label_color, "colors": colors,
        }

    # ===================================================================
    # Show / hide / placement (GTK thread)
    # ===================================================================
    def _place(self):
        """Move the window so its canvas sits centred near the bottom of the ACTIVE
        monitor. Re-evaluated each show so the island follows the active monitor."""
        if self.win is None:
            return
        cur_w, cur_h = (self._cur_img.size if self._cur_img is not None
                        else (CANVAS_W, CANVAS_H))
        wa = _active_monitor_workarea()
        if wa is not None:
            x0, y0, ww, wh = wa
            x = int(x0 + (ww - cur_w) / 2)
            y = int(y0 + wh - cur_h - BOTTOM_GAP)
        else:
            try:
                screen = self.win.get_screen()
                sw, sh = screen.get_width(), screen.get_height()
            except Exception:
                sw, sh = 1920, 1080
            x = max(0, (sw - cur_w) // 2)
            y = max(0, sh - cur_h - BOTTOM_GAP)
        try:
            self.win.move(x, y)
        except Exception:
            pass

    def _show(self):
        if self.visible or self.win is None:
            return
        try:
            self._place()
            self.win.show()
            # Re-assert keep-above + click-through every show (a compositor can
            # drop these when the window maps).
            self.win.set_keep_above(True)
            self._input_shape_key = None
            self._apply_click_through()
            self.visible = True
        except Exception:
            pass

    def _hide(self):
        if not self.visible or self.win is None:
            return
        try:
            self.win.hide()
        except Exception:
            pass
        self.visible = False

    # ===================================================================
    # Rendering + animation tick (GTK thread)
    # ===================================================================
    def _paint_frame(self):
        """Render the CURRENT state once (no frame advance, no countdown). Shared by
        _tick (every cadence) and wake() (the instant a state change lands)."""
        active = self.state in ("listening", "search", "transcribing",
                                "building", "done", "hint", "error")
        if not active or not _HAVE_RENDER or not self._ok:
            self._hide()
            return
        try:
            previous_size = (self._cur_img.size
                             if self._cur_img is not None else None)
            snap = self._build_snapshot()
            pill_img = island_render.render(snap)  # PIL RGBA
            # THE BIG SHIFT: companion mode-deck bar rendered above the island pill.
            # The selector must also exist during plain dictation; otherwise the
            # user has no way to choose a mode in the first place.
            bar_state = getattr(self, "bar_state", None)
            if bar_state and bar_state.get("modes"):
                bar_snap = dict(bar_state)
                bar_snap["frame"] = self.frame
                bar_img = island_render.render_bar(bar_snap)
                bar_w, bar_h = bar_img.size
                pill_w, pill_h = pill_img.size
                # Composite pill + bar into one image: bar on top, pill below with a
                # 2px gap so they read as two separate pills.
                gap = 2
                total_h = bar_h + pill_h + gap
                total_w = max(bar_w, pill_w)
                composite = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
                composite.alpha_composite(bar_img, ((total_w - bar_w) // 2, 0))
                composite.alpha_composite(pill_img, ((total_w - pill_w) // 2, bar_h + gap))
                self._cur_img = composite
                bar_x = (total_w - bar_w) // 2
                layout = island_render.bar_layout(bar_snap)
                pill_x0, pill_x1 = layout["pill"]
                self._bar_hitbox = (
                    bar_x + pill_x0,
                    island_render.BAR_WIN_H - island_render.BAR_H,
                    pill_x1 - pill_x0,
                    island_render.BAR_H,
                )
                # Resize window to fit both bar + pill
                if self.win is not None:
                    try:
                        self.win.set_size_request(total_w, total_h)
                        self.win.resize(total_w, total_h)
                    except Exception:
                        pass
            else:
                self._cur_img = pill_img
                self._bar_hitbox = None
                # Restore default canvas size
                if self.win is not None:
                    try:
                        self.win.set_size_request(CANVAS_W, CANVAS_H)
                        self.win.resize(CANVAS_W, CANVAS_H)
                    except Exception:
                        pass
            if (self.visible and previous_size is not None
                    and self._cur_img.size != previous_size):
                # Expanding/collapsing changes the window width. Re-centre after
                # resizing so the island does not jump sideways on Linux.
                self._place()
            self._apply_click_through()
        except Exception as e:
            print("overlay_linux: island_render failed (skipping frame):", e)
            return
        if not self.visible:
            self._show()
        # Schedule a repaint of the whole canvas on the GTK thread.
        try:
            if self.win is not None:
                self.win.queue_draw()
        except Exception:
            pass

    def wake(self):
        """Repaint the island NOW, the moment a state setter runs, so a transition
        shows within a frame instead of waiting up to a full tick. Called from
        worker threads — it must reach the GTK thread, so we bounce the actual
        paint through GLib.idle_add. Does NOT advance the frame counter.
        Always paints for active states — the island must appear when recording
        starts, even when the Mumble window is not focused (bugfix-island-freeze).

        Suspended guard (C-009): no-op while the interactive question panel is up
        — state changes from dictation threads are absorbed without painting."""
        if self.suspended:
            return
        if not _HAVE_GTK:
            return
        try:
            GLib.idle_add(self._wake_on_main)
        except Exception as e:
            print("overlay_linux wake error (continuing):", e)

    def set_focused(self, focused):
        """Pause the island animation loop when the main window loses focus.
        The frame counter, state timers, and done/hint countdowns are ALL
        frozen while unfocused, so returning focus shows exactly the same
        state — no visible jump. (VAL-PERF-005)"""
        self.focused = focused

    def _wake_on_main(self):
        """The GTK-thread half of wake(): paint once, then stop (idle one-shot)."""
        try:
            self._paint_frame()
        except Exception as e:
            print("overlay_linux wake paint error (continuing):", e)
        return False  # one-shot

    def _tick(self):
        """The animation heartbeat — mirrors overlay_mac._tick. Advances the frame,
        decrements the done/hint countdowns, repaints, and re-schedules itself at a
        fast cadence (45ms ~ 22fps) while on-screen, relaxing to 200ms when idle
        (a near no-op each wake). Runs on the GTK main loop.

        Suspended state (C-009): when the interactive question panel is up, the
        island freezes its animation to avoid distracting visual noise. A 30-second
        self-healing timeout resets the suspended state in case the panel gets stuck
        (network drop, dead webview, etc.) — the island unfreezes on its own.

        bugfix-island-freeze: the island is a separate topmost overlay; its tick
        must run for active states (listening/transcribing/building/done/hint/error)
        even when the Mumble window is not focused. The focus optimization only
        applies when the island is idle."""
        # Self-healing timeout: if the suspended panel ever gets stuck, reset
        # after 30s so the island doesn't stay frozen forever.
        if self.suspended and self._suspended_since > 0.0:
            if time.time() - self._suspended_since > 30.0:
                self.suspended = False
                self._suspended_since = 0.0

        is_active = self.state in ("listening", "search", "transcribing",
                                   "building", "done", "hint", "error")
        if not self.focused and not is_active:
            # Window is unfocused AND the island is idle — skip everything.
            # Re-schedule at a relaxed 200ms cadence. (VAL-PERF-005)
            try:
                GLib.timeout_add(200, self._tick)
            except Exception:
                pass
            return False

        # Suspended: the interactive question panel is up — freeze the animation
        # but keep the tick alive at a relaxed cadence so the self-healing
        # timeout can fire. The frame counter is NOT advanced, so when the panel
        # closes, the island picks up exactly where it was.
        if self.suspended:
            try:
                GLib.timeout_add(200, self._tick)
            except Exception:
                pass
            return False

        # Run at full speed whenever the island is active (bugfix-island-freeze).
        self.frame += 1
        # Advance the appearance fade-in (armed in _build_snapshot on idle→active).
        if getattr(self, "_appear_left", 0) > 0:
            self._appear_left -= 1
        try:
            prev_state = self._prev_state
            if self.state == "done":
                self.done_left -= 1
                if self.done_left <= 0:
                    self.state = "idle"
            elif self.state == "hint":
                # A mode-suggestion chip FREEZES while the mode key is held — the
                # user is (re)arming to respeak, so it must not fade from under
                # them. A plain paste hint keeps counting down normally.
                if not (self.is_suggest and self.armed):
                    self.hint_left -= 1
                if self.hint_left <= 0:
                    self.state = "idle"
            if self.state in ("listening", "search") and \
                    prev_state not in ("listening", "search"):
                self._listen_start = time.time()  # drive the live timer
            self._prev_state = self.state

            self._paint_frame()
        except Exception as e:  # never let one bad frame kill the tick loop
            print("overlay_linux tick error (continuing):", e)

        # Re-schedule: fast while on-screen, relaxed when idle/hidden. Returning
        # False (and re-adding) lets us change the interval between ticks.
        # Idle tick CPU guard: when the island is idle AND hidden, drop to a
        # 500ms heartbeat — the island is invisible and nothing needs animating,
        # so a near-no-op wake is all that's needed.
        try:
            if self.visible:
                delay = 45
            elif self.state == "idle":
                delay = 500   # idle CPU guard: near-zero overhead
            else:
                delay = 200
            GLib.timeout_add(delay, self._tick)
        except Exception:
            pass
        return False  # we re-added explicitly above with the chosen delay


# ===========================================================================
# Public factory: real GTK island where possible, no-op shell otherwise.
# ===========================================================================
class Island:
    """The island the controller (mumble_linux.py) constructs and drives.

    This thin façade returns a fully-working GTK island when GTK + the Pillow
    renderer are available, and a no-op island (full API, no window) otherwise —
    so ``Island(root)`` NEVER raises, anywhere. Implemented via __new__ so callers
    keep using ``Island(root)`` / ``Island(root, style)`` unchanged."""

    def __new__(cls, root, style=None):
        if _HAVE_GTK and _HAVE_RENDER:
            try:
                return _GtkIsland(root, style)
            except Exception as e:
                print("overlay_linux: GTK island construction failed — "
                      "falling back to no-op:", e)
                return _NoopIsland(root, style)
        return _NoopIsland(root, style)


# ===========================================================================
# Offline visual verification — render each state to a PNG (no GTK needed).
# Mirrors island_render._demo but driven through THIS module's snapshot engine,
# so the state→snapshot mapping (not just the renderer) is exercised.
# ===========================================================================
def _self_test():
    if not _HAVE_RENDER:
        print("island_render unavailable — cannot run the offline visual test.")
        return
    import tempfile

    out = os.path.join(tempfile.gettempdir(), "mumble_island_linux_preview")
    os.makedirs(out, exist_ok=True)

    # A throwaway island instance whose setters/_build_snapshot we drive directly.
    # We bypass __new__'s GTK path so this works even with GTK absent.
    isl = _GtkIsland.__new__(_GtkIsland)
    # minimal field init the snapshot engine needs (mirrors _GtkIsland.__init__)
    isl.state = "idle"; isl.level = 0.0; isl.frame = 7
    isl.armed = False; isl.is_suggest = False; isl.is_gathering = False
    isl.build_color = C.gold; isl.build_colors = [C.gold]; isl.build_label = ""
    isl.build_offline = False; isl.done_color = C.gold; isl.done_colors = [C.gold]
    isl.done_left = DONE_FRAMES; isl.done_label = ""; isl.flash_offline = False
    isl.flash_pasted = True; isl.hint_text = ""; isl.hint_left = HINT_FRAMES
    isl._listen_start = time.time() - 4

    def snap_png(name):
        snap = isl._build_snapshot()
        snap["frame"] = 7
        im = island_render.render(snap)
        tile = Image.new("RGBA", (CANVAS_W, CANVAS_H), (22, 22, 26, 255))
        tile.alpha_composite(im)
        path = os.path.join(out, name + ".png")
        tile.convert("RGB").save(path)
        return path

    paths = []
    # idle (renders nothing visible, but exercises the snapshot path)
    isl.state = "idle"; paths.append(snap_png("idle"))
    # listening
    isl.state = "listening"; isl.level = 0.85; paths.append(snap_png("listening"))
    # transcribing
    isl.state = "transcribing"; paths.append(snap_png("transcribing"))
    # building (via the real setter, so _mode_names + labels are exercised)
    isl.set_building("email"); paths.append(snap_png("building"))
    # building + gathering
    isl.set_building("context", gathering=True); paths.append(snap_png("gathering"))
    # done / pasted
    isl.flash("email", pasted=True); paths.append(snap_png("done"))
    # done / saved (not pasted → "Ctrl + Alt + H" tail)
    isl.flash("list", pasted=False); paths.append(snap_png("saved"))
    # hint
    isl.hint(""); paths.append(snap_png("hint"))
    # suggest (violet)
    isl.suggest("wrong mode?"); paths.append(snap_png("suggest"))
    # error
    isl.state = "error"; isl.is_suggest = False; paths.append(snap_png("error"))

    # contact sheet
    names = ["idle", "listening", "transcribing", "building", "gathering",
             "done", "saved", "hint", "suggest", "error"]
    cols = 2
    rows = (len(names) + cols - 1) // cols
    sheet = Image.new("RGB", (CANVAS_W * cols, CANVAS_H * rows), (18, 18, 22))
    for i, name in enumerate(names):
        sheet.paste(Image.open(os.path.join(out, name + ".png")),
                    ((i % cols) * CANVAS_W, (i // cols) * CANVAS_H))
    sheet_path = os.path.join(out, "_contact_sheet.png")
    sheet.save(sheet_path)
    print("wrote island state PNGs to:", out)
    print("contact sheet:", sheet_path)


if __name__ == "__main__":
    _self_test()
