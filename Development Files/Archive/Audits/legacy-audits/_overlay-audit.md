# Linux Overlay Audit — `overlay_linux.py` vs Windows `overlay.py`

**Date**: 2026-07-04  
**Files audited**:
- Linux: `Internal/app/Ports/Linux/app/overlay_linux.py` (~962 lines, `_GtkIsland` + `_NoopIsland` + `Island` facade)
- Windows (canonical): `Internal/app/Ports/Linux/app/overlay.py` (~2194 lines, `_GlassPill` + `_WidgetBar` + `Island`)
- Shared renderer: `Internal/app/Ports/Linux/app/island_render.py`
- Master docs: `Development Files/Core/MUMBLE_MASTER_SYSTEM_DOCUMENTATION.md`
- GLib shim: `Internal/app/Ports/Linux/app/mumble_linux.py` (class `_GlibRoot`)

---

## 1. State Machine Comparison (Windows vs Linux)

### 1.1 States Present — IDENTICAL ✅

Both overlays have the same state set: `idle`, `listening`, `transcribing`, `building`, `done`, `hint`, `error`. The `_STATE_LABEL` dict is identical across both, mapping each state to its human-readable label.

| State | Windows (overlay.py) | Linux (overlay_linux.py) | Parity |
|---|---|---|---|
| `idle` | Yes | Yes | ✅ |
| `listening` | Yes | Yes | ✅ |
| `transcribing` | Yes | Yes | ✅ |
| `building` | Yes | Yes | ✅ |
| `done` | Yes | Yes | ✅ |
| `hint` | Yes | Yes | ✅ |
| `error` | Yes | Yes | ✅ |

### 1.2 State Transitions & Countdown Logic — IDENTICAL ✅

Both `_tick()` methods implement the same logic for countdown decrements and state expiry:

- **done countdown**: `done_left` decremented each tick; resets to `idle` at ≤0. Both use `DONE_FRAMES = 30`.
- **hint countdown**: `hint_left` decremented (frozen while `is_suggest and armed`); resets to `idle` at ≤0.
- **suggest freeze**: Both freeze the hint countdown when `is_suggest and self.armed`, so the user can re-arm and re-speak.
- **listen timer**: Both set `_listen_start = time.time()` on transition into `listening`.

### 1.3 `_build_snapshot()` — Missing `armed_color` Key 🔴 CRITICAL

**Windows** (overlay.py line ~1050-1075):
```python
return {
    ...
    "armed_color": getattr(self, "armed_color", None)
    or MODE_COLORS.get("prompt", C.gold),
    ...
}
```

**Linux** (overlay_linux.py line ~690-706):
```python
return {
    ...
    # ❌ "armed_color" is MISSING
    ...
}
```

**Impact**: `island_render.render()` uses `snap.get("armed_color")` to draw the armed ring in the ACTIVE mode's colour (lines ~360-380 of `island_render.py`). Without it, the ring always falls back to Prompt purple (`MODE_COLORS["prompt"]`). If the user has Email mode active, the Linux island ring will show purple instead of blue — a visible UX inconsistency.

### 1.4 `_listen_start` Initialization — OK ✅

Windows: `_listen_start` is not in `__init__`, relies on `_prev_state` change detection.  
Linux: `self._listen_start = 0.0` is in `_GtkIsland.__init__` (line 312). Safe.

### 1.5 Suspended State Handling — MISSING 🔴 CRITICAL

**Windows** (overlay.py `_tick`, lines ~1330-1340):
```python
if self.suspended:
    if time.time() - self._suspended_since > 30.0:
        self.suspended = False
        self._prev_state = ""   # force re-evaluation
```
And `_paint_frame` is called inside a `try/except _Skip` block; the suspended check raises `_Skip`.

**Linux** (overlay_linux.py `_tick`, lines ~809-850):  
No `suspended` check at all. The attribute exists (`self.suspended = False`, line 289, commented "unused on Linux, kept for parity / tick guard") but the tick loop never reads it. `_paint_frame` never skips on suspended.

**Impact**: If the controller ever sets `island.suspended = True` on Linux (e.g., for an interactive panel), the island will NOT hide itself. The island will continue animating and rendering while the panel is supposedly up. The 30-second self-healing safety valve is also absent.

### 1.6 `_Skip` Exception Class — MISSING 🔶 MEDIUM

Windows defines `class _Skip(Exception): ...` and uses it in the tick loop for suspended-state control flow. Linux does not define this class. Since `suspended` is not checked, the class isn't needed — but if `suspended` support is added later, it will need to be reintroduced.

### 1.7 `wake()` Suspended Guard — MISSING 🔶 MEDIUM

**Windows**: `wake()` checks `if self.suspended: return`.  
**Linux**: `wake()` does NOT check `suspended`. Combined with the missing `_tick` guard, there is no mechanism to pause the island on Linux.

---

## 2. Rendering Feature Gaps

### 2.1 Companion Bar / Mode Deck — ENTIRELY MISSING 🔴 CRITICAL

**Windows** has `_WidgetBar` (lines ~435-640 of overlay.py) which:
- Creates its own per-pixel-alpha layered Win32 window
- Renders mode chips (Prompt, Email, Reply) via `island_render.render_bar(snap)`
- Renders Deck button and optional Foreign toggle
- Handles click hit-testing via `island_render.bar_layout(snap)`
- Uses `set_bar_state()` to receive live mode deck state from the controller
- Echoes the active mode's colour ring around the whole bar

**Linux** has NO bar whatsoever. The callbacks exist (`on_prompt_toggle`, `on_deck`) but they are never connected to any visible UI. The `set_widget_callbacks()` method stores them. There is no `set_bar_state()`, no `bar_state` attribute, no `on_foreign` callback, and no `armed_color` tracking.

**Impact**: Linux users cannot see or change processing modes from the island. The entire "Big Shift" mode deck UX is missing. There is no visual indicator of which mode is active, no way to toggle Prompt/Email/Reply/Foreign from the island, and no Deck button.

### 2.2 `set_bar_state()` — MISSING 🔴 CRITICAL

Windows has this method (overlay.py lines ~920-950) which:
- Updates `bar_state` dict with modes, active mode, foreign_on, show_foreign
- Sets `self.armed` based on whether a mode is active
- Sets `self.armed_color` to the active mode's contractual colour
- Refreshes the widget bar

Linux has no equivalent method. The controller cannot push mode deck state to the Linux island.

### 2.3 `on_foreign` Callback — MISSING 🔶 MEDIUM

Windows `_WidgetBar` and `Island` have `on_foreign` for the Foreign toggle.  
Linux has only `on_prompt_toggle` and `on_deck`. The `set_widget_callbacks` signature is different:
- Windows: `set_widget_callbacks(self, on_mode=None, on_deck=None, on_foreign=None)`
- Linux: `set_widget_callbacks(self, on_prompt=None, on_deck=None)`

### 2.4 Tips Rendering — PARTIAL 🔶 MEDIUM

Both overlays support inline hint/suggest text through `hint()` and `suggest()` → `_build_snapshot()` → `island_render.render()`. However, the Windows overlay's `_paint_frame` also drives the companion bar's `render_and_push()`. Linux lacks this entirely, so any bar-related tips are impossible.

### 2.5 Timer Display — IDENTICAL ✅

Both pass `timer` in the snapshot. The shared `island_render.render()` stacks it below the waveform (refinement pass §9 per master doc). Linux `_listen_timer()` and all relevant logic match Windows exactly.

### 2.6 Drop Shadow & Gold Glow — IDENTICAL ✅

Both call `island_render.render(snap)`, which produces the same soft drop shadow, gold bloom halo, and glass body. The Linux rendering pipeline (`_blit_pil`) correctly delivers this to the GTK window.

### 2.7 Click-Through — DIFFERENT MECHANISM, SAME EFFECT ✅

- Windows: `WS_EX_TRANSPARENT` extended style on layered window
- Linux: `input_shape_combine_region(cairo.Region(), 0, 0)` with empty region

Both achieve full click-through. Linux re-applies on `realize` and each `_show()`.

### 2.8 Fade-in Appearance — GAP 🔶 MEDIUM

Windows canvas pill uses `attributes("-alpha", 0.95)` for a semi-transparent appearance.  
Linux shows the window instantly via `win.show()` with no fade transition. There is no gradual appearance animation. However, the island's internal `fade` value (from `_build_snapshot`) controls the PILL's dissolve for done/hint states — this works identically on both platforms.

---

## 3. GTK3/Cairo Implementation Bugs & Concerns

### 3.1 RGBA Visual Setup — CORRECT ✅

Lines 366-377 of overlay_linux.py:
```python
screen = win.get_screen()
rgba = screen.get_rgba_visual()
if rgba is not None:
    win.set_visual(rgba)
    self._compositing_ok = True
else:
    self._compositing_ok = False
```
Correctly queries for RGBA visual and falls back to opaque. The `screen-changed` handler (line 382) re-evaluates if the window moves to a different screen.

### 3.2 Pixbuf Blit Path — CORRECT ✅

Lines 437-455: Preferred path wraps PIL bytes in GdkPixbuf, uses `Gdk.cairo_set_source_pixbuf`. This avoids the premultiplied-BGRA byte-order issue. The fallback via `_pil_to_cairo_surface()` does a correct manual swizzle (BGRA premultiplied, little-endian). Both paths call `ctx.paint()` correctly.

### 3.3 `_on_draw` Clear Logic — CORRECT ✅

Lines 417-435: When compositing is available, clears to fully transparent with `OPERATOR_SOURCE` + zero-alpha paint. Without compositing, clears to opaque dark background. This is correct.

### 3.4 `_on_draw` Return Value — MINOR STYLE 🔵 LOW

Returns `False` (line 435). In GTK3, `draw` signal handlers should return `True` to stop further propagation. Since no other handlers are connected, this has no practical effect, but it's technically incorrect per GTK convention.

### 3.5 Pixbuf Memory — OK ✅

GdkPixbuf objects are GObjects managed by GLib's reference counting. When the Python wrapper goes out of scope, the GObject ref is released. Since the pixbuf is created and consumed within `_blit_pil`, it's freed promptly. No explicit free needed.

### 3.6 Cairo Surface Memory (Fallback Path) — OK ✅

The `cairo.ImageSurface.create_for_data()` wraps a Python `memoryview` of a `bytearray`. When the surface goes out of scope, Cairo releases its reference. The Python buffer is then GC'd. No leak.

### 3.7 Error Handling in `_on_draw` — CORRECT ✅

Line 433: Any exception is caught, logged, and the frame is skipped. The window survives a bad frame. This is the same defensive pattern as Windows.

### 3.8 `_apply_click_through` Error Handling — CORRECT ✅

Lines 400-410: Catches exceptions if compositor isn't available for input shaping. The pill still shows (not click-through but doesn't steal focus due to NOTIFICATION type hint).

---

## 4. Thread Safety Issues

### 4.1 Setter Isolation — CORRECT ✅

All public setters (`set_state`, `set_level`, `set_armed`, `set_building`, `flash`, `hint`, `suggest`) ONLY assign plain Python fields. They do NOT touch GTK. This matches the Windows pattern exactly.

### 4.2 `wake()` → GTK Thread Bounce — CORRECT ✅

`wake()` (line 780) uses `GLib.idle_add(self._wake_on_main)` to schedule the paint on the GTK main loop. `_wake_on_main` returns `False` (one-shot). This is the correct GLib equivalent of Tk's `after_idle`.

### 4.3 `_cur_img` Access — CORRECT ✅

Set in `_paint_frame()` (GTK thread), read in `_on_draw()` (GTK thread). Both on GTK thread — no data race. ✅

### 4.4 `self.frame` Access — CORRECT ✅

Only modified in `_tick()` (GTK thread), read in `_build_snapshot()` (called from `_paint_frame()` on GTK thread). No data race. ✅

### 4.5 `self.state` Access — SAFE ENOUGH ✅

Writers: worker threads via setters (plain field assignment, atomic for Python strings).  
Readers: `_tick()` on GTK thread.  
Python's GIL ensures string reference assignment is atomic. No torn reads. ✅

### 4.6 `_tick` Re-scheduling Pattern — CORRECT ✅

Returns `False` from the timeout callback and manually re-adds with `GLib.timeout_add(delay, self._tick)`. This is the standard pattern for variable-interval timers in GLib/GTK.

---

## 5. Memory & Resource Leaks

### 5.1 Timer IDs Not Tracked — MINOR LEAK RISK 🔵 LOW

`GLib.timeout_add()` returns a source ID but the Linux overlay never stores it. If the window is destroyed while a tick is pending, the callback will fire and call `_paint_frame()` → `self.win.queue_draw()` on a destroyed window. However:
- `_paint_frame` checks `self._ok` and `self.win is not None`
- `_on_draw` catches exceptions
- The callback returns `False` so it won't re-fire

Risk is low. Not a practical leak, but not as clean as it could be.

### 5.2 PIL Image Reference — OK ✅

`_cur_img` is overwritten each frame. Old PIL Image object is GC'd by Python. No leak.

### 5.3 Background Cache in `island_render.py` — SHARED CONCERN 🔵 LOW

The `_BG_CACHE` dict caches up to 8 background images (LRU). This is per-process and shared by both Windows and Linux paths. Not a leak — intentionally bounded. ✅

### 5.4 No GDI Handle Leaks — N/A ✅

Linux doesn't use GDI. The Windows overlay prevents GDI leaks with `_LayeredDC.__del__` finalizer. Linux has no equivalent concern.

---

## 6. Wayland Compatibility Issues

### 6.1 Forced XWayland — CORRECT BUT BRITTLE 🔶 MEDIUM

`mumble_linux.py` lines 59-68:
```python
if os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("GDK_BACKEND"):
    os.environ["GDK_BACKEND"] = "x11"
```

This forces the GTK island onto XWayland. The comments correctly note that native Wayland GTK can't position overlays, set keep-above, or skip taskbar — these are all client-controlled on Wayland.

**Problems**:
1. If XWayland is not installed or disabled, the island window creation may fail entirely. GTK3 with `GDK_BACKEND=x11` will error if no X11 display is available. There is no fallback detection for this case.
2. The env var is set at module import time, which may affect other GTK windows (the web UI host, if it uses GTK). The comment says "via inherited env" — children inherit `GDK_BACKEND=x11`.
3. If the user explicitly sets `GDK_BACKEND=wayland`, the force is skipped (correctly respecting user override), but then the island window will likely misbehave on Wayland (incorrect positioning, no keep-above).

### 6.2 Pure Wayland (no XWayland) — LIKELY BROKEN 🔴 CRITICAL

On a pure Wayland session without XWayland:
- `GDK_BACKEND=x11` will be set but no X11 display socket exists
- `Gtk.Window()` creation may raise an error
- `_build_window` catches the exception and sets `self._ok = False`, so `_GtkIsland` degrades gracefully
- BUT: `_NoopIsland` takes over, meaning NO island at all
- The tray icon (also GTK) would similarly fail

**Impact**: Pure Wayland users get no island overlay. The app still functions for dictation but has zero visual feedback.

### 6.3 `input_shape_combine_region` on XWayland — LIKELY WORKS ✅

Under XWayland, the X11 input shape extension is available. The empty region should make the window click-through. This is XWayland's primary purpose.

---

## 7. `_GlibRoot` Shim Audit

Located in `mumble_linux.py`.

### 7.1 `after(ms, func, *args)` — CORRECT ✅

Maps to `GLib.timeout_add(max(0, int(ms)), _cb)`. The wrapper catches exceptions so a crashing callback doesn't kill the timer. Returns `False` for one-shot behavior — correct.

Headless fallback uses `threading.Timer` with daemon thread. This is a reasonable last resort.

### 7.2 `quit()` → `Gtk.main_quit()` — CORRECT ✅

Stops the GTK main loop. Matches Tk's `quit()` semantics.

### 7.3 `destroy()` — NO-OP 🔶 MEDIUM

Does nothing. Tk's `destroy()` tears down the root window and all children. On Linux, window lifecycle is managed by GTK and individual windows are destroyed via their own methods. The no-op is acceptable but callers expecting Tk behavior (e.g., cleanup after `destroy`) won't get it.

### 7.4 `mainloop()` Headless Fallback — PROBLEMATIC 🔶 MEDIUM

```python
def mainloop(self):
    if Gtk is not None:
        Gtk.main()
    else:
        import time as _t
        while True:
            _t.sleep(3600)
```

The headless fallback is an infinite loop that can never exit. There's no mechanism to break out of it (no signal handler, no flag). If GTK fails to import (headless server), the app will hang forever. A proper implementation would use `threading.Event` or similar.

### 7.5 No `after_cancel` — MISSING 🔶 MEDIUM

Tk's `after()` returns a timer ID that can be passed to `after_cancel()`. `_GlibRoot.after()` returns nothing, so `after_cancel` is not supported. If the controller calls `root.after_cancel(id)`, it will raise `AttributeError` or similar. This may affect the controller's ability to cancel deferred operations.

### 7.6 No `update()` / `update_idletasks()` — MISSING 🔵 LOW

Tk's `update()` processes pending events. `_GlibRoot` doesn't implement this. If the controller calls it, it will crash. However, `_GlibRoot` is only used for `after()`/`quit()`/`mainloop()`, so this may not be an issue in practice.

---

## 8. `_NoopIsland` Completeness

### 8.1 Missing Methods 🔴 CRITICAL

The `_NoopIsland` class (lines 186-258) is missing several methods/attributes that the Windows `Island` (and `_GtkIsland`) have and that the controller may call:

| Method/Attribute | In `_GtkIsland` | In Windows `Island` | In `_NoopIsland` | Risk |
|---|---|---|---|---|
| `set_focused(focused)` | ✅ | ✅ | ❌ | Controller calls this — will crash |
| `set_bar_state(...)` | ❌ | ✅ | ❌ | Controller may call |
| `set_armed(on)` | ✅ | ✅ | ✅ (but doesn't refresh widget) | Low |
| `done_color` | ✅ | ✅ | ❌ | Controller may read |
| `done_colors` | ✅ | ✅ | ❌ | Controller may read |
| `build_colors` | ✅ | ✅ | ❌ | Controller may read |
| `build_label` | ✅ | ✅ | ❌ | Controller may read |
| `done_label` | ✅ | ✅ | ❌ | Controller may read |
| `flash_pasted` | ✅ | ✅ | ❌ | Controller may read |
| `hint_text` | ✅ | ✅ | ❌ | Controller may read |
| `focused` | ✅ | ✅ | ❌ | Controller may set/read |
| `on_foreign` | ❌ | ✅ | ❌ | Gap in both |
| `bar_state` | ❌ | ✅ | ❌ | Gap in both |
| `armed_color` | ❌ | ✅ | ❌ | Gap in both |

**Recommendation**: `_NoopIsland.__init__` should initialize every attribute that `_GtkIsland.__init__` does, and implement every method present on `_GtkIsland`, to guarantee the controller never crashes.

### 8.2 `_NoopIsland.set_armed` — PARTIAL 🔶 MEDIUM

Windows `set_armed` refreshes the widget bar after setting `armed`. Linux `_NoopIsland.set_armed` only sets the boolean. No practical issue since there's no bar, but the method signatures should match.

---

## 9. TODO/FIXME/HACK Comments

### 9.1 None Found ✅

A search for `TODO`, `FIXME`, `HACK`, `XXX` across `overlay_linux.py` returned no results. The code is clean of explicit markers. However, the following areas are implicitly "TODO":

- The `suspended` attribute comment says "(unused on Linux, kept for parity / tick guard)" — this is effectively an implicit "TODO: implement suspended support"
- `close_picker()` comment says "NO-OP on Linux — the interactive 'Which mode?' picker is unused here" — implies "TODO: implement mode picker if needed"
- `_picker_open` comment says "(unused on Linux)" — same

### 9.2 Implicit FIXMEs Based on Windows Parity

- `_build_snapshot()`: Missing `armed_color` — implicit parity gap
- `_tick()`: Missing `suspended` check — implicit parity gap
- No companion bar — implicit parity gap

---

## 10. Code Quality Concerns

### 10.1 Duplicated Snapshot Logic 🔶 MEDIUM

The `_build_snapshot()` methods in `overlay_linux.py` (line 627) and `overlay.py` (line ~980) are ~90% identical. The state-machine logic (color selection, label composition, hint text, fade calculation) is copy-pasted with only minor differences (Linux uses `_HOTKEY_HISTORY = "Ctrl + Alt + H"`, Windows uses `"Ctrl + Alt + H"` directly). Any change to the snapshot schema must be made in both files.

### 10.2 `_mode_names` Duplicated 🔵 LOW

Identical implementation in both files. Could be shared.

### 10.3 `_listen_timer` Duplicated 🔵 LOW

Identical implementation in both files.

### 10.4 `_blend` / `_hex` Duplicated 🔵 LOW

Both files have their own `_hex` and `_blend` functions. The `island_render.py` module also has its own copies. Three copies of the same logic.

### 10.5 Constants Defined in Module Scope — OK ✅

Both files define `W`, `H`, `BOTTOM_MARGIN`, `DONE_FRAMES`, `HINT_FRAMES`, `SUGGEST_FRAMES`, `FADE_OUT_FRAMES`, `_PILL_RIM`, `_SUGGEST` as module-level constants. This is appropriate.

### 10.6 Linux `_place()` Uses `_active_monitor_workarea()` — CORRECT ✅

The Linux implementation correctly uses Gdk's monitor API with pointer-following and primary-monitor fallback. The Windows version uses `MonitorFromWindow(GetForegroundWindow(), ...)` — different but equivalent approach for each platform.

### 10.7 GTK Window Type Hint — CORRECT ✅

`Gdk.WindowTypeHint.NOTIFICATION` is the correct hint for an overlay that shouldn't appear in taskbars or pagers and shouldn't accept focus.

### 10.8 `win.stick()` — CORRECT ✅

Makes the island appear on all virtual desktops/workspaces. Wrapped in try/except for environments where this isn't supported.

### 10.9 `_build_window` Calls `win.realize()` — CORRECT ✅

Realizing the window before showing it ensures `input_shape_combine_region` has a GdkWindow to operate on. This is necessary.

### 10.10 Module-Level Import Guard — CORRECT ✅

The `_HAVE_GTK` / `_HAVE_RENDER` guards at module level ensure clean imports on CI/Windows/macOS. The `Island.__new__` facade returns `_NoopIsland` when either is missing. This is well-designed.

---

## 11. Summary of Findings by Severity

### 🔴 Critical (4 issues)

| # | Issue | Location | Description |
|---|---|---|---|
| 1 | Missing `armed_color` in snapshot | `overlay_linux.py` line ~690 | `island_render` expects `armed_color` key for mode-colored armed ring. Linux always falls back to Prompt purple |
| 2 | No companion bar / mode deck | `overlay_linux.py` — absent | No `_WidgetBar`, no `set_bar_state()`, no `render_bar()`. Mode selection UI is entirely missing |
| 3 | Suspended state not enforced | `overlay_linux.py` `_tick()` line ~809 | Tick loop never checks `self.suspended`. Island won't hide for interactive panels |
| 4 | Pure Wayland (no XWayland) → no island | `mumble_linux.py` lines 59-68 | Forced `GDK_BACKEND=x11` fails on pure Wayland. `_GtkIsland` degrades to `_NoopIsland` |

### 🔶 Medium (9 issues)

| # | Issue | Location | Description |
|---|---|---|---|
| 5 | `_NoopIsland` missing `set_focused` | `overlay_linux.py` line ~186 | Controller may crash on headless/no-GTK runs |
| 6 | `_NoopIsland` missing many attributes | `overlay_linux.py` `_NoopIsland.__init__` | `done_color`, `build_colors`, `focused`, `bar_state`, etc. |
| 7 | Wake doesn't check suspended | `overlay_linux.py` `wake()` line ~780 | Combined with #3, no mechanism to pause island |
| 8 | `_GlibRoot.after()` returns nothing | `mumble_linux.py` `_GlibRoot.after()` | No `after_cancel` support; callers expecting Tk behavior will crash |
| 9 | `_GlibRoot.mainloop()` headless fallback | `mumble_linux.py` `_GlibRoot.mainloop()` | Infinite loop with no exit mechanism |
| 10 | `set_widget_callbacks` signature mismatch | `overlay_linux.py` line ~586 | Missing `on_foreign` callback vs Windows |
| 11 | No fade-in appearance | `overlay_linux.py` `_show()` line ~731 | Pill appears instantly vs Windows's semi-transparent fade |
| 12 | Duplicated `_build_snapshot` logic | Both files | ~90% identical, must be kept in sync manually |
| 13 | Wayland env var set at import time | `mumble_linux.py` line ~67 | May affect other GTK windows; no detection if XWayland is absent |

### 🔵 Low (5 issues)

| # | Issue | Location | Description |
|---|---|---|---|
| 14 | `_on_draw` returns `False` | `overlay_linux.py` line ~435 | Should return `True` per GTK convention |
| 15 | Timer IDs not tracked | `overlay_linux.py` `_tick()` | Cannot cancel pending tick; marginal safety concern |
| 16 | `_GlibRoot` missing `update()`/`update_idletasks()` | `mumble_linux.py` | Controller may call these |
| 17 | `_hex`/`_blend`/`_mode_names`/`_listen_timer` duplicated | Both files + `island_render.py` | Three copies of utility functions |
| 18 | `close_picker()` no-op may surprise callers | `overlay_linux.py` line ~600 | Controller expects picker to close |

---

## 12. Recommendations

1. **Add `armed_color` to Linux `_build_snapshot()`** — one-line fix. Read from `self.armed_color` or fall back to `MODE_COLORS.get("prompt", C.gold)`.

2. **Implement `_WidgetBar` equivalent for Linux** — this is the biggest gap. A pure-Cairo companion bar could be rendered in the same GTK window (above the island pill) or in a separate GTK window. The shared `island_render.render_bar()` already produces the pixels.

3. **Implement `suspended` support in `_tick()` and `wake()`** — copy the Windows pattern: self-healing timeout + `_Skip` exception.

4. **Complete `_NoopIsland`** — add `set_focused()`, `set_bar_state()`, and all missing attributes so the no-op island never crashes the controller.

5. **Add `set_bar_state()` to `_GtkIsland`** — enables the controller to push mode deck state.

6. **Add Wayland detection fallback** — if `GDK_BACKEND=x11` is forced but no X11 display is available, log a clear warning and gracefully degrade to `_NoopIsland`.

7. **Fix `_GlibRoot.mainloop()` headless fallback** — use a `threading.Event` or at minimum catch `KeyboardInterrupt`.

8. **Consider extracting shared state-engine logic** — `_build_snapshot()`, `_mode_names()`, `_listen_timer()` could live in `island_render.py` or a new `island_state.py` shared by all three platform overlays.
