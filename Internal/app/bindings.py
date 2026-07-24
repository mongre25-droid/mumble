#!/usr/bin/env python3
"""Unified hotkey / hold-key bindings over BOTH the keyboard and the mouse.

Any Mumble binding — record, re-paste, Deck, Search, and the held mode
key — can be a keyboard combo OR a mouse button (great for mice with extra side
buttons). A binding SPEC is a string:

  • keyboard: "ctrl+windows", "right shift", "ctrl+alt+f"  (parsed by `keyboard`)
  • mouse:    "mouse:x2" (forward), "mouse:x" (back), "mouse:middle",
              "mouse:left", "mouse:right" — friendly aliases also accepted
              (mouse:forward, mouse:back, mouse:button5, mouse:wheel, …).

The keyboard path is exactly the old behaviour (`keyboard.add_hotkey` /
`on_press_key` / `on_release_key`). The mouse path uses the `mouse` library.
If `mouse` isn't installed, mouse specs fail with a clear message and every
keyboard binding keeps working unchanged.
"""

import keyboard

try:
    import mouse as _mouse
    HAVE_MOUSE = True
except Exception:  # pragma: no cover - exercised only when the lib is absent
    _mouse = None
    HAVE_MOUSE = False

MOUSE_PREFIX = "mouse:"

# Canonical mouse buttons the `mouse` library understands.
_MOUSE_BUTTONS = {"left", "right", "middle", "x", "x2"}
# Friendly aliases → canonical button.
_MOUSE_ALIASES = {
    "back": "x", "button4": "x", "mouse4": "x", "side": "x", "xbutton1": "x",
    "forward": "x2", "button5": "x2", "mouse5": "x2", "xbutton2": "x2",
    "wheel": "middle", "scroll": "middle", "mmb": "middle",
    "lmb": "left", "rmb": "right",
}
# Human-readable display names.
_MOUSE_DISPLAY = {
    "left": "Mouse Left", "right": "Mouse Right",
    "middle": "Mouse Middle (wheel)",
    "x": "Mouse Back (X1)", "x2": "Mouse Forward (X2)",
}

# Modifier keys → the generic base used in a combo spec (so 'left ctrl'+x and
# 'right ctrl'+x both bind as 'ctrl+x', matching keyboard.add_hotkey semantics).
# A key NOT in here is a normal, combo-completing key. Used only by capture().
_MOD_BASE = {
    "ctrl": "ctrl", "left ctrl": "ctrl", "right ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "left alt": "alt", "right alt": "alt", "alt gr": "alt",
    "shift": "shift", "left shift": "shift", "right shift": "shift",
    "windows": "windows", "left windows": "windows", "right windows": "windows",
    "cmd": "windows", "win": "windows",
}

# Canonical order for the modifier part of a captured combo, so Ctrl+Windows
# always reads/normalises as 'ctrl+windows' regardless of which was pressed first.
_MOD_ORDER = ("ctrl", "alt", "shift", "windows")

# Friendly spellings that the keyboard package treats as the same physical key.
# Collision checks use these semantic names instead of comparing raw setting
# strings, so e.g. ``windows+control+f`` cannot evade a ``ctrl+win+f`` check.
_KEY_ALIASES = {
    "control": "ctrl",
    "cmd": "windows",
    "win": "windows",
    "left win": "windows",
    "right win": "windows",
    "escape": "esc",
    "return": "enter",
    "spacebar": "space",
    "del": "delete",
}


def _order_mods(mods):
    return sorted(mods, key=lambda m: _MOD_ORDER.index(m) if m in _MOD_ORDER else 99)


def is_mouse(spec):
    return (spec or "").strip().lower().startswith(MOUSE_PREFIX)


def is_bare_modifier(spec):
    """True if `spec` is a SINGLE modifier key with nothing else (e.g. 'ctrl',
    'left windows', 'alt'). Such a spec is perfectly fine for a HOLD binding (the
    mode key you press-and-hold) but is ILLEGITIMATE as a single-PRESS hotkey:
    `keyboard.add_hotkey('ctrl')` fires the instant ANY Ctrl goes down, so a
    record hotkey that decayed to a bare 'ctrl' starts Mumble on every Ctrl press.

    This is the root cause of the 'Ctrl alone starts Mumble' regression: capture()
    could not form the all-modifier default combo (Ctrl+Windows) and emitted a
    lone 'ctrl' instead, which the web-UI saved straight to settings (bypassing
    validate()). Callers registering a single-press hotkey must reject these."""
    s = normalize(spec)
    if not s or s.startswith(MOUSE_PREFIX) or "+" in s:
        return False
    return s in _MOD_BASE


def _button(spec):
    """Canonical mouse button for a 'mouse:…' spec, or None if it isn't one."""
    raw = (spec or "").strip().lower()
    if not raw.startswith(MOUSE_PREFIX):
        return None
    b = raw[len(MOUSE_PREFIX):].strip()
    b = _MOUSE_ALIASES.get(b, b)
    return b if b in _MOUSE_BUTTONS else None


def normalize(spec):
    """Lower/trim, and canonicalise a mouse alias to 'mouse:<button>'."""
    s = (spec or "").strip().lower()
    if s.startswith(MOUSE_PREFIX):
        b = _button(s)
        return f"{MOUSE_PREFIX}{b}" if b else s
    return s


def _binding_fingerprint(spec):
    """Return the physical chord represented by a press binding.

    The tuple identifies mouse bindings exactly. Keyboard chords are returned
    as a frozenset because modifier order and left/right modifier spelling do
    not change what ``keyboard.add_hotkey`` observes.
    """
    s = normalize(spec)
    if not s:
        return None
    if s.startswith(MOUSE_PREFIX):
        button = _button(s)
        return ("mouse", button) if button else ("mouse", s)
    parts = []
    for raw in s.split("+"):
        part = raw.strip()
        if not part:
            continue
        part = _MOD_BASE.get(part, _KEY_ALIASES.get(part, part))
        parts.append(part)
    return ("keyboard", frozenset(parts)) if parts else None


def conflicts(first, second):
    """Whether two press bindings can fire from the same physical chord.

    Keyboard hooks allow extra held keys, so both exact duplicates and
    subset/superset chords conflict (``ctrl+f`` also fires during
    ``ctrl+alt+f``). Mouse bindings conflict only when they name the same
    button.
    """
    a = _binding_fingerprint(first)
    b = _binding_fingerprint(second)
    if not a or not b or a[0] != b[0]:
        return False
    if a[0] == "mouse":
        return a == b
    left, right = a[1], b[1]
    return bool(left and right and (left.issubset(right) or right.issubset(left)))


def find_conflict(spec, named_bindings, exclude=None):
    """Return the first conflicting ``(key, value)`` pair, or ``None``."""
    for key, value in (named_bindings or {}).items():
        if key == exclude or not value:
            continue
        if conflicts(spec, value):
            return key, value
    return None


def validate(spec, hold=False):
    """(ok, message). Keyboard specs go through keyboard.parse_hotkey; mouse
    specs must name a real button and need the `mouse` library installed.

    hold=True validates for a HOLD-style binding (the mode key): those are
    hooked with on_press_key/on_release_key, which take a SINGLE key — a
    combo like 'ctrl+alt' would 'save' fine and then silently never arm, so
    reject combos here instead."""
    s = normalize(spec)
    if not s:
        return False, "Enter a hotkey or mouse button."
    if s.startswith(MOUSE_PREFIX):
        if _button(s) is None:
            return False, ("Unknown mouse button. Use mouse:x2 (forward), "
                           "mouse:x (back), mouse:middle, mouse:left, "
                           "mouse:right.")
        if not HAVE_MOUSE:
            return False, ("Mouse buttons need the 'mouse' package — re-run "
                           "Install Mumble.bat to add it.")
        return True, ""
    if hold and "+" in s:
        return False, ("The mode key must be a SINGLE key (or mouse button) "
                       "you can hold — not a combo. Try right shift, right "
                       "alt, or mouse:x2.")
    if "," in s:
        # ``keyboard.parse_hotkey`` accepts comma-separated key sequences, but
        # Mumble's press bindings and collision model intentionally represent a
        # single chord. Accepting sequences here would make conflict detection
        # incomplete and let two actions share part of the same gesture.
        return False, ("Use one key combination, not a sequence of shortcuts. "
                       "For example, use Ctrl + Alt + F.")
    if not hold and is_bare_modifier(s):
        # A lone Ctrl/Alt/Shift/Win as a TAP hotkey fires on every press of that
        # modifier — it would start Mumble whenever you hit Ctrl. Demand a real
        # combo (or a non-modifier key) for single-press bindings.
        return False, ("A single modifier key (Ctrl, Alt, Shift or the Windows "
                       "key) can't be a hotkey on its own — it would fire every "
                       "time you press it. Combine it with another key, e.g. "
                       "Ctrl + Windows.")
    try:
        keyboard.parse_hotkey(s)
        return True, ""
    except Exception:
        return False, "That doesn't look like a valid hotkey."


def pretty(spec):
    """Display string for a binding (keycaps for keys, friendly name for mice)."""
    s = normalize(spec)
    if s.startswith(MOUSE_PREFIX):
        return _MOUSE_DISPLAY.get(_button(s) or "", s)
    return " + ".join(p.strip().capitalize() for p in s.split("+") if p.strip())


class _Handle:
    """Opaque registration token that knows how to unregister itself."""
    __slots__ = ("kind", "h")

    def __init__(self, kind, h):
        self.kind = kind
        self.h = h


def register_hotkey(spec, callback):
    """Fire `callback` on a single press of the binding (record / re-paste /
    search). Returns a handle for unregister(); raises on a bad/unavailable
    spec. The callback is invoked with no arguments (both libraries support
    this), matching the existing handlers."""
    s = normalize(spec)
    if s.startswith(MOUSE_PREFIX):
        if not HAVE_MOUSE:
            raise RuntimeError("mouse library not available")
        btn = _button(s)
        if not btn:
            raise ValueError(f"bad mouse button: {spec!r}")
        h = _mouse.on_button(callback, buttons=(btn,), types=(_mouse.DOWN,))
        return _Handle("mouse", h)
    if is_bare_modifier(s):
        # Backstop for the 'Ctrl alone starts Mumble' regression: a lone modifier
        # registered as a TAP hotkey fires on every press of that modifier. The
        # web UI's validate() already refuses these, but this is the actual
        # registration primitive — reject here too so a hand-edited settings file
        # or any caller that skips validate() can't re-introduce the bug.
        raise ValueError(
            f"{spec!r} is a single modifier key — not valid as a press hotkey "
            "(it would fire on every press). Use a combo, e.g. 'ctrl+windows'."
        )
    h = keyboard.add_hotkey(s, callback, suppress=False)
    return _Handle("kb_hotkey", h)


def register_hold(spec, on_down, on_up):
    """Fire on_down on press and on_up on release — the mode key's hold window.
    Returns a handle for unregister(); raises on a bad/unavailable spec.
    on_down/on_up must tolerate being called with zero args (mouse) or one
    event arg (keyboard) — give them a default-valued parameter."""
    s = normalize(spec)
    if s.startswith(MOUSE_PREFIX):
        if not HAVE_MOUSE:
            raise RuntimeError("mouse library not available")
        btn = _button(s)
        if not btn:
            raise ValueError(f"bad mouse button: {spec!r}")
        hd = _mouse.on_button(on_down, buttons=(btn,), types=(_mouse.DOWN,))
        hu = _mouse.on_button(on_up, buttons=(btn,), types=(_mouse.UP,))
        return _Handle("mouse_hold", (hd, hu))
    hd = keyboard.on_press_key(s, on_down, suppress=False)
    hu = keyboard.on_release_key(s, on_up, suppress=False)
    return _Handle("kb_hold", (hd, hu))


def unregister(handle):
    """Remove a registered binding and report whether it was released."""
    if handle is None:
        return True
    try:
        if handle.kind == "kb_hotkey":
            keyboard.remove_hotkey(handle.h)
        elif handle.kind == "kb_hold":
            for h in handle.h:
                keyboard.unhook(h)
        elif handle.kind == "mouse":
            _mouse.unhook(handle.h)
        elif handle.kind == "mouse_hold":
            for h in handle.h:
                _mouse.unhook(h)
        return True
    except Exception as e:
        # Callers replacing a live binding need to know that retiring the old
        # hook failed; swallowing this can leave two actions active at once.
        print(f"hotkey unregister failed ({handle.kind}): {e}")
        return False


def is_pressed(spec):
    """Is the binding currently physically held? Used by the mode-key safety
    poll. Keyboard → keyboard.is_pressed; mouse → mouse.is_pressed."""
    s = normalize(spec)
    if s.startswith(MOUSE_PREFIX):
        if not HAVE_MOUSE:
            return False
        btn = _button(s)
        try:
            return bool(btn and _mouse.is_pressed(btn))
        except Exception:
            return False
    try:
        return keyboard.is_pressed(s)
    except Exception:
        return False


def capture(timeout=15.0):
    """Block until the user presses a KEY, a KEY COMBINATION, or a MOUSE BUTTON,
    and return its spec — e.g. 'ctrl+x', 'ctrl+alt+f', 'right shift', 'mouse:x2'.
    Returns '' on timeout.

    COMBINATIONS: modifiers (ctrl/alt/shift/windows) are accumulated as they go
    down; the first NON-modifier key pressed while they are held completes the
    combo ('ctrl' + 'x' → 'ctrl+x'). A bare key with no modifier returns on its
    own ('a' → 'a').

    A combo made up ONLY of modifiers — the app's own default record hotkey is
    Ctrl+Windows, two modifiers and no normal key — completes on the FIRST
    release once two or more modifiers are held, returning the whole combo
    ('ctrl+windows'). This is the fix for the 'Ctrl alone starts Mumble' bug:
    the previous version had no way to finish an all-modifier combo and dropped
    one modifier per release, ultimately emitting a lone 'ctrl', which then got
    saved as the record hotkey and fired on every Ctrl tap.

    A SINGLE modifier pressed and released alone — nothing else touched —
    returns as a single-key binding ('right shift'), which is what a hold-style
    mode key needs. (The original capture grabbed the very first key-down and
    returned instantly, so even a normal combo could never be formed — pressing
    Ctrl ended capture as 'ctrl'.)

    The keyboard hook SUPPRESSES events for the duration, so the keys you press
    to bind can't also trigger an existing global hotkey (pressing ctrl+alt+d to
    rebind History used to fire History) or leak into the focused app. The LEFT
    mouse button is ignored (it's how the capture button was clicked) — bind it
    by typing 'mouse:left'."""
    import queue

    q = queue.Queue()
    mods = []        # generic modifier bases in press order (e.g. ['ctrl','alt'])
    raw_mods = []    # the original key names, parallel to `mods`
    done = {"v": False}

    def emit(spec):
        if not done["v"]:
            done["v"] = True
            q.put(spec)

    def on_kb(e):
        if done["v"]:
            return
        name = (getattr(e, "name", "") or "").strip().lower()
        if not name:
            return
        et = getattr(e, "event_type", None)
        base = _MOD_BASE.get(name)
        if et == "down":
            if base:
                if base not in mods:
                    mods.append(base)
                    raw_mods.append(name)
            else:
                # A non-modifier key completes the combo (with or without mods).
                emit("+".join(_order_mods(mods) + [name]) if mods else name)
        elif et == "up":
            if base and base in mods:
                if len(mods) == 1:
                    # A lone modifier released with nothing else held → bind it
                    # on its own (single-key hold bindings such as 'right shift').
                    emit(raw_mods[0])
                else:
                    # Two+ modifiers held and one released with no normal key
                    # pressed → an all-modifier combo (e.g. the default
                    # Ctrl+Windows). Emit the WHOLE combo, canonically ordered,
                    # instead of dropping a modifier and decaying to a bare key.
                    emit("+".join(_order_mods(mods)))

    def on_mouse(e):
        if HAVE_MOUSE and isinstance(e, _mouse.ButtonEvent):
            if (e.event_type == _mouse.DOWN and e.button in _MOUSE_BUTTONS
                    and e.button != _mouse.LEFT):
                emit(f"{MOUSE_PREFIX}{e.button}")

    try:
        kbh = keyboard.hook(on_kb, suppress=True)
    except TypeError:        # very old `keyboard` without the suppress kwarg
        kbh = keyboard.hook(on_kb)
    mh = _mouse.hook(on_mouse) if HAVE_MOUSE else None
    try:
        return normalize(q.get(timeout=timeout))
    except queue.Empty:
        return ""
    finally:
        try:
            keyboard.unhook(kbh)
        except Exception:
            pass
        if mh is not None:
            try:
                _mouse.unhook(mh)
            except Exception:
                pass
