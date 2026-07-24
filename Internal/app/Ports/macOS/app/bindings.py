#!/usr/bin/env python3
"""Unified hotkey / hold-key bindings over BOTH the keyboard and the mouse.

Any Mumble binding — the record hotkey, re-paste, web-search, and the held mode
key — can be a keyboard combo OR a mouse button (great for mice with extra side
buttons). A binding SPEC is a string:

  • keyboard: "ctrl+windows", "right shift", "ctrl+alt+s"  (parsed by `keyboard`)
  • mouse:    "mouse:x2" (forward), "mouse:x" (back), "mouse:middle",
              "mouse:left", "mouse:right" — friendly aliases also accepted
              (mouse:forward, mouse:back, mouse:button5, mouse:wheel, …).

macOS uses :mod:`pynput` for both devices.  The formerly pinned ``keyboard``
Darwin backend refused to listen unless the whole application ran as root, and
the standalone ``mouse`` package does not support Darwin at all.  ``pynput``
uses the normal Quartz event-tap permission model, so Mumble stays an ordinary
user process and macOS Accessibility/Input Monitoring control access.

Windows/Linux retain the established ``keyboard`` + ``mouse`` implementation.
"""

import sys
import threading

_IS_DARWIN = sys.platform == "darwin"
_PYNPUT_KEYBOARD = None
_PYNPUT_MOUSE = None
_QUARTZ = None
_HAVE_PYNPUT = False

if _IS_DARWIN:
    try:
        from pynput import keyboard as _PYNPUT_KEYBOARD  # type: ignore
        from pynput import mouse as _PYNPUT_MOUSE  # type: ignore
        _HAVE_PYNPUT = True
        try:
            import Quartz as _QUARTZ  # type: ignore
        except Exception:
            # Fake-pynput tests do not ship PyObjC. A real Darwin pynput import
            # already requires Quartz, so production always takes this branch.
            _QUARTZ = None
    except Exception as _pynput_error:  # dependency/Quartz unavailable
        print(f"macOS input backend unavailable: {_pynput_error}")
    keyboard = None
else:
    import keyboard  # type: ignore

if _IS_DARWIN:
    _mouse = None
    HAVE_MOUSE = _HAVE_PYNPUT
else:
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
    "option": "alt", "left option": "alt", "right option": "alt",
    "shift": "shift", "left shift": "shift", "right shift": "shift",
    "windows": "windows", "left windows": "windows", "right windows": "windows",
    "command": "windows", "left command": "windows", "right command": "windows",
    "cmd": "windows", "left cmd": "windows", "right cmd": "windows",
    "win": "windows",
}

# Canonical order for the modifier part of a captured combo, so Ctrl+Windows
# always reads/normalises as 'ctrl+windows' regardless of which was pressed first.
_MOD_ORDER = ("ctrl", "alt", "shift", "windows")


def _order_mods(mods):
    return sorted(mods, key=lambda m: _MOD_ORDER.index(m) if m in _MOD_ORDER else 99)


# macOS virtual key codes are stable physical positions. Use them only as a
# fallback when the layout-aware character is absent/non-ASCII: Option+D is
# reported as ``∂`` by CGEvent, but its virtual key remains 2 (the D key).
_DARWIN_VK_NAMES = {
    0: "a", 1: "s", 2: "d", 3: "f", 4: "h", 5: "g", 6: "z", 7: "x",
    8: "c", 9: "v", 11: "b", 12: "q", 13: "w", 14: "e", 15: "r",
    16: "y", 17: "t", 18: "1", 19: "2", 20: "3", 21: "4", 22: "6",
    23: "5", 24: "=", 25: "9", 26: "7", 27: "-", 28: "8", 29: "0",
    30: "]", 31: "o", 32: "u", 33: "[", 34: "i", 35: "p", 37: "l",
    38: "j", 39: "'", 40: "k", 41: ";", 42: "\\", 43: ",",
    44: "/", 45: "n", 46: "m", 47: ".", 49: "space", 50: "`",
}

_DARWIN_KEY_ALIASES = {
    "control": "ctrl",
    "option": "alt",
    "left option": "left alt",
    "right option": "right alt",
    "command": "windows",
    "cmd": "windows",
    "win": "windows",
    "left command": "left windows",
    "left cmd": "left windows",
    "right command": "right windows",
    "right cmd": "right windows",
    "return": "enter",
    "escape": "esc",
    "pageup": "page up",
    "pagedown": "page down",
}

_DARWIN_NAMED_KEYS = (
    set(_DARWIN_VK_NAMES.values())
    | {f"f{i}" for i in range(1, 21)}
    | {
        "space", "enter", "esc", "tab", "backspace", "delete",
        "home", "end", "page up", "page down", "left", "right",
        "up", "down",
    }
)


def _darwin_spec_tokens(spec):
    """Parse a keyboard spec into the tokens used by the pynput event hub.

    Kept platform-neutral so the Windows-hosted regression suite can verify the
    Darwin grammar without importing PyObjC.
    """
    raw = (spec or "").strip().lower().replace("_", " ")
    if not raw or raw.startswith(MOUSE_PREFIX):
        return frozenset()
    out = []
    for part in raw.split("+"):
        token = " ".join(part.split())
        token = _DARWIN_KEY_ALIASES.get(token, token)
        if not token:
            return frozenset()
        out.append(token)
    if len(set(out)) != len(out):
        return frozenset()
    non_mods = [t for t in out if t not in _MOD_BASE]
    if len(non_mods) > 1:
        return frozenset()
    if non_mods and non_mods[0] not in _DARWIN_NAMED_KEYS:
        return frozenset()
    return frozenset(out)


def _darwin_event_tokens(key):
    """Return generic+sided tokens for one pynput keyboard event."""
    if not _HAVE_PYNPUT or key is None:
        return set()
    Key = _PYNPUT_KEYBOARD.Key
    special = {
        Key.ctrl: {"ctrl", "left ctrl"},
        Key.ctrl_r: {"ctrl", "right ctrl"},
        Key.alt: {"alt", "left alt"},
        Key.alt_r: {"alt", "right alt"},
        Key.shift: {"shift", "left shift"},
        Key.shift_r: {"shift", "right shift"},
        Key.cmd: {"windows", "left windows"},
        Key.cmd_r: {"windows", "right windows"},
        Key.space: {"space"},
        Key.enter: {"enter"},
        Key.esc: {"esc"},
        Key.tab: {"tab"},
        Key.backspace: {"backspace"},
        Key.delete: {"delete"},
        Key.home: {"home"},
        Key.end: {"end"},
        Key.page_up: {"page up"},
        Key.page_down: {"page down"},
        Key.left: {"left"},
        Key.right: {"right"},
        Key.up: {"up"},
        Key.down: {"down"},
    }
    for i in range(1, 21):
        k = getattr(Key, f"f{i}", None)
        if k is not None:
            special[k] = {f"f{i}"}
    if key in special:
        return set(special[key])
    token = _darwin_character_token(
        getattr(key, "char", None), getattr(key, "vk", None))
    return {token} if token else set()


def _darwin_character_token(char, vk):
    """Resolve a printable key without assuming a US keyboard layout.

    pynput's ASCII ``char`` reflects the active layout, so it wins for ordinary
    keys (the physical Q position emits ``a`` on AZERTY). Option can transform D
    into the non-ASCII glyph ``∂``; only then do we use the physical virtual key
    to recover the intended base letter.
    """
    if (isinstance(char, str) and len(char) == 1
            and char.isascii() and char.isprintable()):
        return char.lower()
    return _DARWIN_VK_NAMES.get(vk)


def _darwin_key_identity(key):
    """Stable physical identity across modifier-dependent char changes."""
    vk = getattr(key, "vk", None)
    return ("vk", vk) if vk is not None else ("key", key)


def _darwin_mouse_token(button):
    if not _HAVE_PYNPUT:
        return None
    if button in ("mouse:x", "mouse:x2"):
        return button
    Button = _PYNPUT_MOUSE.Button
    for name in ("left", "right", "middle", "x1", "x2"):
        if button == getattr(Button, name, None):
            return "mouse:x" if name == "x1" else f"mouse:{name}"
    return None


def _darwin_mouse_number_token(number):
    """Map Quartz's extra-button numbers to the public X1/X2 specs."""
    return {3: "mouse:x", 4: "mouse:x2"}.get(number)


if _IS_DARWIN and _HAVE_PYNPUT and _QUARTZ is not None:
    class _DarwinMouseListener(_PYNPUT_MOUSE.Listener):
        """pynput listener that preserves Quartz side-button identity.

        pynput 1.8.2 collapses every ``kCGEventOtherMouse*`` event to its
        ``Button.middle`` enum. Read ``kCGMouseEventButtonNumber`` before the
        base handler does so back/forward buttons 3/4 remain distinguishable.
        """

        def _handle_message(self, proxy, event_type, event, refcon, injected):
            if event_type in (
                    _QUARTZ.kCGEventOtherMouseDown,
                    _QUARTZ.kCGEventOtherMouseUp):
                number = int(_QUARTZ.CGEventGetIntegerValueField(
                    event, _QUARTZ.kCGMouseEventButtonNumber))
                token = _darwin_mouse_number_token(number)
                if token:
                    try:
                        x, y = _QUARTZ.CGEventGetLocation(event)
                    except (AttributeError, TypeError):
                        return
                    self.on_click(
                        x, y, token,
                        event_type == _QUARTZ.kCGEventOtherMouseDown,
                        injected)
                    return
            return super()._handle_message(
                proxy, event_type, event, refcon, injected)
else:
    _DarwinMouseListener = None


def _new_darwin_mouse_listener(**kwargs):
    listener = _DarwinMouseListener or _PYNPUT_MOUSE.Listener
    return listener(**kwargs)


def _run_callback(callback, *args):
    try:
        callback(*args)
    except Exception as e:
        print(f"input callback failed ({type(e).__name__}): {e}")


class _MacInputHub:
    """One Quartz listener pair shared by every macOS binding."""

    def __init__(self):
        self._lock = threading.RLock()
        self._entries = {}
        self._next_id = 1
        self._pressed = set()
        self._held_keys = {}
        self._keyboard_listener = None
        self._mouse_listener = None
        self._watchdog_thread = None
        self._watchdog_stop = None
        self.suspended = False

    @staticmethod
    def _listener_healthy(listener):
        """Return thread health, not pynput's sticky ``running`` flag."""
        if listener is None:
            return False
        is_alive = getattr(listener, "is_alive", None)
        if callable(is_alive):
            try:
                return bool(is_alive())
            except Exception:
                return False
        if hasattr(listener, "started"):
            return bool(listener.started)
        return bool(getattr(listener, "running", False))

    @staticmethod
    def _stop_listener(listener):
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass

    def _ensure_watchdog(self):
        current = self._watchdog_thread
        if current is not None and current.is_alive():
            return
        stop = threading.Event()
        self._watchdog_stop = stop

        def _watch():
            # CGEventTapCreate can fail after Listener.start() returns while
            # pynput still reports running=True. Re-probe the actual listener
            # threads so later Input Monitoring approval or a transient Quartz
            # recovery does not require an app restart.
            while not stop.wait(3.0):
                with self._lock:
                    if not self._entries:
                        continue
                    healthy = (
                        self._listener_healthy(self._keyboard_listener)
                        and self._listener_healthy(self._mouse_listener)
                    )
                if not healthy:
                    print("macOS input listener stopped; retrying Quartz hooks")
                    try:
                        self._ensure_started()
                    except Exception as e:
                        print(f"macOS input listener retry failed: {e}")

        self._watchdog_thread = threading.Thread(
            target=_watch,
            name="mumble-macos-input-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

    def _ensure_started(self):
        if not _HAVE_PYNPUT:
            raise RuntimeError(
                "macOS input support is missing; re-run Install Mumble.command")
        with self._lock:
            if not self._listener_healthy(self._keyboard_listener):
                self._stop_listener(self._keyboard_listener)
                self._keyboard_listener = _PYNPUT_KEYBOARD.Listener(
                    on_press=self._on_key_press,
                    on_release=self._on_key_release,
                    suppress=False,
                )
                self._keyboard_listener.start()
            if not self._listener_healthy(self._mouse_listener):
                self._stop_listener(self._mouse_listener)
                self._mouse_listener = _new_darwin_mouse_listener(
                    on_click=self._on_click)
                self._mouse_listener.start()
            self._ensure_watchdog()

    def add(self, kind, required, down, up=None):
        self._ensure_started()
        with self._lock:
            token = self._next_id
            self._next_id += 1
            self._entries[token] = {
                "kind": kind,
                "required": frozenset(required),
                "down": down,
                "up": up,
                "active": False,
            }
            return token

    def remove(self, token):
        with self._lock:
            self._entries.pop(token, None)

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._pressed.clear()
            self._held_keys.clear()
            listeners = (self._keyboard_listener, self._mouse_listener)
            self._keyboard_listener = None
            self._mouse_listener = None
            watcher = self._watchdog_thread
            watcher_stop = self._watchdog_stop
            self._watchdog_thread = None
            self._watchdog_stop = None
            if watcher_stop is not None:
                watcher_stop.set()
        for listener in listeners:
            self._stop_listener(listener)
        if (watcher is not None
                and watcher is not threading.current_thread()):
            watcher.join(timeout=1.0)

    def is_pressed(self, required):
        self._ensure_started()
        with self._lock:
            return bool(required) and set(required).issubset(self._pressed)

    def _activate_matches(self):
        callbacks = []
        if self.suspended:
            return callbacks
        for entry in self._entries.values():
            if (not entry["active"]
                    and entry["required"].issubset(self._pressed)):
                entry["active"] = True
                callbacks.append((entry["down"], ()))
        return callbacks

    def _release_matches(self, released):
        callbacks = []
        if self.suspended:
            for entry in self._entries.values():
                entry["active"] = False
            return callbacks
        for entry in self._entries.values():
            if not entry["active"]:
                continue
            if entry["required"].intersection(released):
                entry["active"] = False
                if entry["kind"].endswith("hold") and entry["up"] is not None:
                    callbacks.append((entry["up"], ()))
        return callbacks

    def _on_key_press(self, key, *_extra):
        tokens = _darwin_event_tokens(key)
        with self._lock:
            # Key-repeat generates more press events without matching releases.
            # Track each physical key once, then rebuild the union so releasing
            # left Control cannot clear generic ``ctrl`` while right Control is
            # still down.
            self._held_keys[_darwin_key_identity(key)] = set(tokens)
            mouse_tokens = {t for t in self._pressed if t.startswith(MOUSE_PREFIX)}
            self._pressed = mouse_tokens.union(
                *(held for held in self._held_keys.values()))
            callbacks = self._activate_matches()
        for callback, args in callbacks:
            _run_callback(callback, *args)

    def _on_key_release(self, key, *_extra):
        with self._lock:
            old_pressed = set(self._pressed)
            self._held_keys.pop(_darwin_key_identity(key), None)
            mouse_tokens = {t for t in self._pressed if t.startswith(MOUSE_PREFIX)}
            self._pressed = mouse_tokens.union(
                *(held for held in self._held_keys.values()))
            callbacks = self._release_matches(old_pressed - self._pressed)
        for callback, args in callbacks:
            _run_callback(callback, *args)

    def _on_click(self, _x, _y, button, pressed, *_extra):
        token = _darwin_mouse_token(button)
        if not token:
            return
        tokens = {token}
        with self._lock:
            if pressed:
                self._pressed.add(token)
                callbacks = self._activate_matches()
            else:
                callbacks = self._release_matches(tokens)
                self._pressed.discard(token)
        for callback, args in callbacks:
            _run_callback(callback, *args)


_MAC_HUB = _MacInputHub() if _IS_DARWIN else None
_MAC_CONTROLLER = None
_MAC_CONTROLLER_LOCK = threading.Lock()


def _mac_controller():
    global _MAC_CONTROLLER
    if not _HAVE_PYNPUT:
        raise RuntimeError(
            "macOS input support is missing; re-run Install Mumble.command")
    with _MAC_CONTROLLER_LOCK:
        if _MAC_CONTROLLER is None:
            _MAC_CONTROLLER = _PYNPUT_KEYBOARD.Controller()
        return _MAC_CONTROLLER


def _darwin_controller_key(token):
    token = _DARWIN_KEY_ALIASES.get(token, token)
    Key = _PYNPUT_KEYBOARD.Key
    mapping = {
        "ctrl": Key.ctrl, "left ctrl": Key.ctrl_l, "right ctrl": Key.ctrl_r,
        "alt": Key.alt, "left alt": Key.alt_l, "right alt": Key.alt_r,
        "shift": Key.shift, "left shift": Key.shift_l,
        "right shift": Key.shift_r,
        "windows": Key.cmd, "left windows": Key.cmd_l,
        "right windows": Key.cmd_r,
        "space": Key.space, "enter": Key.enter, "esc": Key.esc,
        "tab": Key.tab, "backspace": Key.backspace, "delete": Key.delete,
        "home": Key.home, "end": Key.end, "page up": Key.page_up,
        "page down": Key.page_down, "left": Key.left, "right": Key.right,
        "up": Key.up, "down": Key.down,
    }
    if token in mapping:
        return mapping[token]
    if token.startswith("f") and token[1:].isdigit():
        key = getattr(Key, token, None)
        if key is not None:
            return key
    if len(token) == 1:
        return token
    raise ValueError(f"unsupported key: {token!r}")


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
            installer = "Install Mumble.command" if _IS_DARWIN else "Install Mumble.bat"
            return False, ("Mouse buttons need the platform input backend — re-run "
                           f"{installer} to add it (for example mouse:x2).")
        return True, ""
    if hold and "+" in s:
        return False, ("The mode key must be a SINGLE key (or mouse button) "
                       "you can hold — not a combo. Try right shift, right "
                       "alt, or mouse:x2.")
    if not hold and is_bare_modifier(s):
        # A lone Ctrl/Alt/Shift/Win as a TAP hotkey fires on every press of that
        # modifier — it would start Mumble whenever you hit Ctrl. Demand a real
        # combo (or a non-modifier key) for single-press bindings.
        return False, ("A single modifier key (Ctrl, Alt, Shift or the Windows "
                       "key) can't be a hotkey on its own — it would fire every "
                       "time you press it. Combine it with another key, e.g. "
                       "Ctrl + Windows.")
    if _IS_DARWIN:
        if not _HAVE_PYNPUT:
            return False, ("macOS input support is missing — re-run "
                           "Install Mumble.command.")
        return (True, "") if _darwin_spec_tokens(s) else (
            False, "That doesn't look like a valid hotkey.")
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
            raise RuntimeError("mouse input backend not available")
        btn = _button(s)
        if not btn:
            raise ValueError(f"bad mouse button: {spec!r}")
        if _IS_DARWIN:
            h = _MAC_HUB.add("mac_mouse", {s}, callback)
            return _Handle("mac", h)
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
    if _IS_DARWIN:
        required = _darwin_spec_tokens(s)
        if not required:
            raise ValueError(f"bad keyboard hotkey: {spec!r}")
        h = _MAC_HUB.add("mac_hotkey", required, callback)
        return _Handle("mac", h)
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
            raise RuntimeError("mouse input backend not available")
        btn = _button(s)
        if not btn:
            raise ValueError(f"bad mouse button: {spec!r}")
        if _IS_DARWIN:
            h = _MAC_HUB.add("mac_mouse_hold", {s}, on_down, on_up)
            return _Handle("mac", h)
        hd = _mouse.on_button(on_down, buttons=(btn,), types=(_mouse.DOWN,))
        hu = _mouse.on_button(on_up, buttons=(btn,), types=(_mouse.UP,))
        return _Handle("mouse_hold", (hd, hu))
    if _IS_DARWIN:
        required = _darwin_spec_tokens(s)
        if len(required) != 1:
            raise ValueError("a hold binding must be one key")
        h = _MAC_HUB.add("mac_key_hold", required, on_down, on_up)
        return _Handle("mac", h)
    hd = keyboard.on_press_key(s, on_down, suppress=False)
    hu = keyboard.on_release_key(s, on_up, suppress=False)
    return _Handle("kb_hold", (hd, hu))


def unregister(handle):
    """Remove a binding registered above. Safe to call with None or twice."""
    if handle is None:
        return
    try:
        if handle.kind == "mac":
            _MAC_HUB.remove(handle.h)
        elif handle.kind == "kb_hotkey":
            keyboard.remove_hotkey(handle.h)
        elif handle.kind == "kb_hold":
            for h in handle.h:
                keyboard.unhook(h)
        elif handle.kind == "mouse":
            _mouse.unhook(handle.h)
        elif handle.kind == "mouse_hold":
            for h in handle.h:
                _mouse.unhook(h)
    except Exception:
        pass


def is_pressed(spec):
    """Is the binding currently physically held? Used by the mode-key safety
    poll. Keyboard → keyboard.is_pressed; mouse → mouse.is_pressed."""
    s = normalize(spec)
    if _IS_DARWIN:
        required = {s} if s.startswith(MOUSE_PREFIX) else _darwin_spec_tokens(s)
        try:
            return _MAC_HUB.is_pressed(required)
        except Exception:
            return False
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


def _darwin_keys_for_spec(spec):
    raw = normalize(spec).replace("_", " ")
    keys = []
    for part in raw.split("+"):
        token = " ".join(part.split())
        token = _DARWIN_KEY_ALIASES.get(token, token)
        keys.append(_darwin_controller_key(token))
    return keys


def press(spec):
    """Press a key/combo through the active platform backend."""
    if _IS_DARWIN:
        ctl = _mac_controller()
        for key in _darwin_keys_for_spec(spec):
            ctl.press(key)
        return
    keyboard.press(spec)


def release(spec):
    """Release a key/combo through the active platform backend."""
    if _IS_DARWIN:
        ctl = _mac_controller()
        for key in reversed(_darwin_keys_for_spec(spec)):
            ctl.release(key)
        return
    keyboard.release(spec)


def send(spec):
    """Press and release a combo (for example ``cmd+v``)."""
    if _IS_DARWIN:
        ctl = _mac_controller()
        keys = _darwin_keys_for_spec(spec)
        pressed = []
        try:
            for key in keys:
                ctl.press(key)
                pressed.append(key)
        finally:
            for key in reversed(pressed):
                ctl.release(key)
        return
    keyboard.send(spec)


def unhook_all():
    """Remove every global hook installed by this module."""
    if _IS_DARWIN:
        _MAC_HUB.clear()
    else:
        keyboard.unhook_all()


def _darwin_capture(timeout):
    import queue

    if not _HAVE_PYNPUT:
        raise RuntimeError(
            "macOS input support is missing; re-run Install Mumble.command")
    q = queue.Queue()
    mods = []
    raw_mods = []
    done = {"v": False}

    def display_combo(values):
        aliases = {"alt": "option", "windows": "command"}
        return "+".join(aliases.get(v, v) for v in _order_mods(values))

    def emit(value):
        if not done["v"]:
            done["v"] = True
            q.put(value)

    def key_name(key):
        tokens = _darwin_event_tokens(key)
        if not tokens:
            return "", ""
        sided = next((t for t in tokens if t.startswith(("left ", "right "))), "")
        generic = next((t for t in tokens if t in _MOD_BASE and " " not in t), "")
        if generic:
            raw = sided or generic
            raw = raw.replace(" alt", " option").replace(" windows", " command")
            if raw == "alt":
                raw = "option"
            elif raw == "windows":
                raw = "command"
            return generic, raw
        return next(iter(tokens)), next(iter(tokens))

    def on_press(key, *_extra):
        if done["v"]:
            return
        name, raw = key_name(key)
        base = _MOD_BASE.get(name)
        if base:
            if base not in mods:
                mods.append(base)
                raw_mods.append(raw)
        elif name:
            emit(f"{display_combo(mods)}+{name}" if mods else name)

    def on_release(key, *_extra):
        if done["v"]:
            return
        name, _raw = key_name(key)
        base = _MOD_BASE.get(name)
        if base and base in mods:
            if len(mods) == 1:
                emit(raw_mods[0])
            else:
                emit(display_combo(mods))

    def on_click(_x, _y, button, pressed, *_extra):
        if not pressed or done["v"]:
            return
        token = _darwin_mouse_token(button)
        if token and token != "mouse:left":
            emit(token)

    keyboard_listener = _PYNPUT_KEYBOARD.Listener(
        on_press=on_press, on_release=on_release, suppress=True)
    mouse_listener = _new_darwin_mouse_listener(
        on_click=on_click, suppress=True)
    _MAC_HUB.suspended = True
    try:
        keyboard_listener.start()
        mouse_listener.start()
        try:
            return normalize(q.get(timeout=timeout))
        except queue.Empty:
            return ""
    finally:
        done["v"] = True
        for listener in (keyboard_listener, mouse_listener):
            try:
                listener.stop()
            except Exception:
                pass
        with _MAC_HUB._lock:
            # Suppressed capture events may not deliver every release to the
            # shared listener.  Never let a captured modifier remain stuck.
            _MAC_HUB._pressed.clear()
            _MAC_HUB._held_keys.clear()
            for entry in _MAC_HUB._entries.values():
                entry["active"] = False
            _MAC_HUB.suspended = False


def capture(timeout=15.0):
    """Block until the user presses a KEY, a KEY COMBINATION, or a MOUSE BUTTON,
    and return its spec — e.g. 'ctrl+x', 'ctrl+alt+s', 'right shift', 'mouse:x2'.
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
    to bind can't also trigger an existing global hotkey (pressing Ctrl+Alt+H to
    rebind History used to fire History) or leak into the focused app. The LEFT
    mouse button is ignored (it's how the capture button was clicked) — bind it
    by typing 'mouse:left'."""
    if _IS_DARWIN:
        return _darwin_capture(timeout)

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
