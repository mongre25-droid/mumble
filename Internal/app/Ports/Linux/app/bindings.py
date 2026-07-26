#!/usr/bin/env python3
"""Unified hotkey / hold-key bindings over BOTH the keyboard and the mouse.

Any Mumble binding — the record hotkey, re-paste, web-search, and the held mode
key — can be a keyboard combo OR a mouse button (great for mice with extra side
buttons). A binding SPEC is a string:

  • keyboard: "ctrl+windows", "right shift", "ctrl+alt+s"  (parsed by `keyboard`)
  • mouse:    "mouse:x2" (forward), "mouse:x" (back), "mouse:middle",
              "mouse:left", "mouse:right" — friendly aliases also accepted
              (mouse:forward, mouse:back, mouse:button5, mouse:wheel, …).

The keyboard path is exactly the old behaviour (`keyboard.add_hotkey` /
`on_press_key` / `on_release_key`). The mouse path uses the `mouse` library.
If `mouse` isn't installed, mouse specs fail with a clear message and every
keyboard binding keeps working unchanged.
"""

import keyboard
import glob
import os
import re
import shutil
import subprocess
import sys

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
_PHYSICAL_BELOW_ESCAPE = "physical:below-escape"
_PHYSICAL_SCAN_CODE = 41


_LINUX_KEY_CODES = {
    "esc": (1,), "1": (2,), "2": (3,), "3": (4,), "4": (5,),
    "5": (6,), "6": (7,), "7": (8,), "8": (9,), "9": (10,), "0": (11,),
    "minus": (12,), "-": (12,), "equal": (13,), "backspace": (14,),
    "tab": (15,),
    "q": (16,), "w": (17,), "e": (18,), "r": (19,), "t": (20,),
    "y": (21,), "u": (22,), "i": (23,), "o": (24,), "p": (25,),
    "[": (26,), "]": (27,),
    "enter": (28,), "ctrl": (29, 97), "left ctrl": (29,),
    "right ctrl": (97,), "a": (30,), "s": (31,), "d": (32,),
    "f": (33,), "g": (34,), "h": (35,), "j": (36,), "k": (37,),
    "l": (38,), "semicolon": (39,), "quote": (40,), "'": (40,),
    "`": (41,),
    _PHYSICAL_BELOW_ESCAPE: (_PHYSICAL_SCAN_CODE,),
    "shift": (42, 54), "left shift": (42,), "backslash": (43,),
    "z": (44,), "x": (45,), "c": (46,), "v": (47,), "b": (48,),
    "n": (49,), "m": (50,), "comma": (51,), "dot": (52,),
    "period": (52,),
    "slash": (53,), "right shift": (54,), "alt": (56, 100),
    "left alt": (56,), "space": (57,), "caps lock": (58,),
    "f1": (59,), "f2": (60,), "f3": (61,), "f4": (62,), "f5": (63,),
    "f6": (64,), "f7": (65,), "f8": (66,), "f9": (67,), "f10": (68,),
    "num lock": (69,), "scroll lock": (70,), "f11": (87,), "f12": (88,),
    "right alt": (100,), "home": (102,), "up": (103,), "page up": (104,),
    "left": (105,), "right": (106,), "end": (107,), "down": (108,),
    "page down": (109,), "insert": (110,), "delete": (111,),
    "volume mute": (113,), "volume down": (114,), "volume up": (115,),
    "pause": (119,), "windows": (125, 126), "left windows": (125,),
    "right windows": (126,), "menu": (127,),
}


def _linux_paths_from_proc(description, kind=None):
    """Parse `/proc/bus/input/devices`, skipping blocks without event nodes."""
    paths = []
    for block in re.split(r"\n\s*\n", description):
        handlers_match = re.search(r"^H:\s*Handlers=(.+)$", block,
                                   re.MULTILINE)
        if not handlers_match:
            continue
        handlers = handlers_match.group(1).split()
        if kind == "kbd" and "kbd" not in handlers:
            continue
        if kind == "mouse" and not any(
                token.startswith("mouse") for token in handlers):
            continue
        event = next((token for token in handlers
                      if re.fullmatch(r"event\d+", token)), None)
        if event:
            paths.append(os.path.join("/dev/input", event))
    return paths


def _linux_event_paths(kind=None):
    """Return evdev nodes matching ``kbd`` or ``mouse`` (all when omitted)."""
    paths = []
    try:
        with open("/proc/bus/input/devices", "r", encoding="utf-8") as handle:
            description = handle.read()
        paths.extend(_linux_paths_from_proc(description, kind))
    except OSError:
        pass

    if not paths:
        suffix = {"kbd": "kbd", "mouse": "mouse"}.get(kind, "*")
        for directory in ("by-id", "by-path"):
            paths.extend(glob.glob(
                f"/dev/input/{directory}/*-event-{suffix}"))
    if not paths and kind is None:
        paths.extend(glob.glob("/dev/input/event*"))

    result = []
    seen = set()
    for path in paths:
        resolved = os.path.realpath(path)
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


def _linux_event_is_readable(path):
    """Probe a device synchronously; ``os.access`` alone is racy/ACL-blind."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return False
    else:
        os.close(fd)
        return True


def _linux_evdev_permission_gate(kind=None):
    """Replace upstream's root-only policy with actual evdev permissions."""
    devices = _linux_event_paths(kind)
    if not any(_linux_event_is_readable(path) for path in devices):
        label = "keyboard" if kind == "kbd" else kind or "input"
        raise ImportError(
            f"Mumble cannot read a {label} /dev/input/event device. Add this "
            "user to the input group, then log out and back in.")


class _ReadOnlyEvdevOutput:
    """Prevent listener-only aggregation from silently writing real devices."""

    @staticmethod
    def write_event(_event_type, _code, _value):
        raise PermissionError(
            "evdev hooks are read-only; use wtype, xdotool, or ydotool "
            "for key injection")


def _patch_nixcommon(common):
    """Filter unreadable nodes and aggregate listeners without `/dev/uinput`."""
    if hasattr(common, "_mumble_unfiltered_from_proc"):
        return
    common._mumble_unfiltered_from_proc = common.list_devices_from_proc
    common._mumble_unfiltered_from_by_id = common.list_devices_from_by_id

    def _readable(devices):
        for device in devices:
            if _linux_event_is_readable(device.path):
                yield device

    def _readable_from_proc(type_name):
        kind = "kbd" if type_name in ("kbd", "keyboard") else type_name
        devices = (common.EventDevice(path)
                   for path in _linux_event_paths(kind))
        return _readable(devices)

    def _readable_from_by_id(name_suffix, by_id=True):
        source = common._mumble_unfiltered_from_by_id
        try:
            devices = source(name_suffix, by_id=by_id)
        except TypeError:  # mouse 0.7.1 has no by_id parameter.
            if not by_id:
                return iter(())
            devices = source(name_suffix)
        return _readable(devices)

    def _listener_aggregate(type_name):
        devices = list(_readable_from_proc(type_name))
        if not devices:
            devices = list(_readable_from_by_id(type_name))
        if not devices:
            devices = list(_readable_from_by_id(type_name, by_id=False))
        if not devices:
            raise ImportError(
                f"Mumble cannot open a readable {type_name} evdev device")
        output = _ReadOnlyEvdevOutput()
        # Keep python-keyboard's optional raw-injection fallback when uinput is
        # genuinely writable.  Mouse injection never uses this path.
        if type_name == "kbd":
            try:
                uinput = common.make_uinput()
                output = common.EventDevice("uinput Mumble Virtual Keyboard")
                output._input_file = uinput
                output._output_file = uinput
            except OSError:
                pass
        return common.AggregatedEventDevice(
            devices, output=output)

    common.list_devices_from_proc = _readable_from_proc
    common.list_devices_from_by_id = _readable_from_by_id
    common.aggregate_devices = _listener_aggregate


def _install_permission_based_linux_input():
    """Make the upstream libraries honour evdev group permissions on Linux.

    keyboard 0.13.5 rejects every non-root user before attempting access and
    also calls dumpkeys, which often requires a controlling console. Mumble
    already grants narrowly-scoped /dev/input access through the input group;
    use that real permission check and a stable Linux input-event key map.
    """
    if not sys.platform.startswith("linux"):
        return
    try:
        from keyboard import _nixcommon, _nixkeyboard
        _patch_nixcommon(_nixcommon)

        def _build_tables():
            if _nixkeyboard.to_name and _nixkeyboard.from_name:
                return
            for name, codes in _LINUX_KEY_CODES.items():
                canonical = _nixkeyboard.normalize_name(name)
                for code in codes:
                    _nixkeyboard.register_key((code, ()), canonical)

        _nixkeyboard.ensure_root = lambda: _linux_evdev_permission_gate("kbd")
        _nixkeyboard.aggregate_devices = _nixcommon.aggregate_devices
        _nixkeyboard.build_tables = _build_tables
    except Exception as e:
        print(f"keyboard evdev preparation failed: {e}")
    if HAVE_MOUSE:
        try:
            from mouse import _nixcommon as mouse_common
            from mouse import _nixmouse
            _patch_nixcommon(mouse_common)
            _nixmouse.ensure_root = (
                lambda: _linux_evdev_permission_gate("mouse"))
            _nixmouse.aggregate_devices = mouse_common.aggregate_devices
        except Exception as e:
            print(f"mouse evdev preparation failed: {e}")


_install_permission_based_linux_input()


def _ensure_keyboard_backend_ready():
    """Initialise Linux evdev synchronously so permission errors reach callers."""
    if sys.platform.startswith("linux"):
        _linux_evdev_permission_gate("kbd")
        backend = getattr(keyboard, "_os_keyboard", None)
        if backend is not None and hasattr(backend, "init"):
            backend.init()


def _ensure_mouse_backend_ready():
    if sys.platform.startswith("linux") and HAVE_MOUSE:
        _linux_evdev_permission_gate("mouse")
        backend = getattr(_mouse, "_os_mouse", None)
        if backend is not None and hasattr(backend, "init"):
            backend.init()


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


def _keyboard_hotkey(spec):
    parts = [part.strip() for part in normalize(spec).split("+") if part.strip()]
    translated = [
        _PHYSICAL_SCAN_CODE if part == _PHYSICAL_BELOW_ESCAPE else part
        for part in parts
    ]
    return translated if _PHYSICAL_SCAN_CODE in translated else normalize(spec)


def _binding_fingerprint(spec):
    """Return the physical chord represented by a press binding."""
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
        part = _MOD_BASE.get(part, part)
        if part == "`":
            part = _PHYSICAL_BELOW_ESCAPE
        parts.append(part)
    return ("keyboard", frozenset(parts)) if parts else None


def conflicts(first, second):
    """Whether two bindings can fire from the same physical chord."""
    left = _binding_fingerprint(first)
    right = _binding_fingerprint(second)
    if not left or not right or left[0] != right[0]:
        return False
    if left[0] == "mouse":
        return left == right
    a, b = left[1], right[1]
    return bool(a and b and (a.issubset(b) or b.issubset(a)))


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
                           "./install.sh to add it.")
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
    try:
        keyboard.parse_hotkey(_keyboard_hotkey(s))
        return True, ""
    except Exception:
        return False, "That doesn't look like a valid hotkey."


def pretty(spec):
    """Display string for a binding (keycaps for keys, friendly name for mice)."""
    s = normalize(spec)
    if s.startswith(MOUSE_PREFIX):
        return _MOUSE_DISPLAY.get(_button(s) or "", s)
    return " + ".join(
        "Physical key below Esc" if p.strip() == _PHYSICAL_BELOW_ESCAPE
        else p.strip().capitalize()
        for p in s.split("+") if p.strip()
    )


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
        _ensure_mouse_backend_ready()
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
    _ensure_keyboard_backend_ready()
    h = keyboard.add_hotkey(_keyboard_hotkey(s), callback, suppress=False)
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
        _ensure_mouse_backend_ready()
        btn = _button(s)
        if not btn:
            raise ValueError(f"bad mouse button: {spec!r}")
        hd = _mouse.on_button(on_down, buttons=(btn,), types=(_mouse.DOWN,))
        hu = _mouse.on_button(on_up, buttons=(btn,), types=(_mouse.UP,))
        return _Handle("mouse_hold", (hd, hu))
    _ensure_keyboard_backend_ready()
    hd = keyboard.on_press_key(s, on_down, suppress=False)
    hu = keyboard.on_release_key(s, on_up, suppress=False)
    return _Handle("kb_hold", (hd, hu))


def unregister(handle):
    """Remove a binding registered above. Safe to call with None or twice."""
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
    except Exception:
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
        _ensure_keyboard_backend_ready()
        return keyboard.is_pressed(s)
    except Exception:
        return False


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
    import queue

    q = queue.Queue()
    mods = []        # generic modifier bases in press order (e.g. ['ctrl','alt'])
    raw_mods = []    # the original key names, parallel to `mods`
    done = {"v": False}
    mouse_ready = HAVE_MOUSE
    if mouse_ready:
        try:
            _ensure_mouse_backend_ready()
        except Exception as e:
            print(f"mouse capture unavailable: {e}")
            mouse_ready = False

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
                physical_name = (
                    _PHYSICAL_BELOW_ESCAPE
                    if getattr(e, "scan_code", None) == _PHYSICAL_SCAN_CODE
                    else name
                )
                emit("+".join(_order_mods(mods) + [physical_name])
                     if mods else physical_name)
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
        if mouse_ready and isinstance(e, _mouse.ButtonEvent):
            if (e.event_type == _mouse.DOWN and e.button in _MOUSE_BUTTONS
                    and e.button != _mouse.LEFT):
                emit(f"{MOUSE_PREFIX}{e.button}")

    try:
        _ensure_keyboard_backend_ready()
        kbh = keyboard.hook(on_kb, suppress=True)
    except TypeError:        # very old `keyboard` without the suppress kwarg
        kbh = keyboard.hook(on_kb)
    mh = _mouse.hook(on_mouse) if mouse_ready else None
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


def _session_type():
    """Return the display session type: 'wayland' or 'x11'.

    Detects the session type from the WAYLAND_DISPLAY and XDG_SESSION_TYPE
    environment variables. Used by the controller's copy_image() to pick the
    correct clipboard tool (wl-copy for Wayland, xclip for X11/XWayland)."""
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return "wayland"
    return "x11"


# ---------------------------------------------------------------------------
# Controller compatibility + Linux injection
# ---------------------------------------------------------------------------
# mumble_linux imports this facade as ``keyboard``.  Registration has always
# lived here, but the controller also calls send/release/unhook_all; omitting
# those delegates made every paste/copy and shutdown cleanup raise AttributeError.

_WTYPE_MODIFIERS = {
    "ctrl": "ctrl", "control": "ctrl", "alt": "alt",
    "shift": "shift", "windows": "logo", "win": "logo", "super": "logo",
}
_YDOTOOL_CODES = {
    "ctrl": 29, "control": 29, "alt": 56, "shift": 42,
    "windows": 125, "win": 125, "super": 125,
    "a": 30, "c": 46, "v": 47,
}


def _run_inject(command):
    """Run a short input helper; False covers missing daemon/compositor support."""
    try:
        return subprocess.run(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=2.0, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _wtype_send(spec):
    exe = shutil.which("wtype")
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not exe or not parts:
        return False
    key = parts[-1]
    modifiers = [_WTYPE_MODIFIERS.get(p) for p in parts[:-1]]
    if any(mod is None for mod in modifiers):
        return False
    command = [exe]
    for modifier in modifiers:
        command += ["-M", modifier]
    command += ["-P", key, "-p", key]
    for modifier in reversed(modifiers):
        command += ["-m", modifier]
    return _run_inject(command)


def _ydotool_send(spec):
    exe = shutil.which("ydotool")
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not exe or not parts or any(p not in _YDOTOOL_CODES for p in parts):
        return False
    codes = [_YDOTOOL_CODES[p] for p in parts]
    events = [f"{code}:1" for code in codes]
    events.extend(f"{code}:0" for code in reversed(codes))
    return _run_inject([exe, "key", *events])


def _xdotool_send(spec):
    exe = shutil.which("xdotool")
    if not exe or not os.environ.get("DISPLAY"):
        return False
    xspec = spec.lower().replace("windows", "super").replace("win+", "super+")
    return _run_inject([exe, "key", "--clearmodifiers", xspec])


def send(spec):
    """Inject a key/combo across Wayland, X11, then raw evdev/uinput fallback."""
    s = normalize(spec)
    if not s:
        raise ValueError("empty key specification")
    if _session_type() == "wayland":
        if _wtype_send(s) or _ydotool_send(s) or _xdotool_send(s):
            return True
    elif _xdotool_send(s) or _ydotool_send(s):
        return True
    try:
        keyboard.send(s)
        return True
    except Exception as exc:
        raise RuntimeError(
            "Could not inject keys. Install/enable wtype (Wayland), xdotool "
            "(X11), or grant write access to /dev/uinput.") from exc


def release(key):
    """Best-effort modifier release matching python-keyboard's public API."""
    name = normalize(key)
    try:
        keyboard.release(name)
        return True
    except Exception:
        pass
    if _session_type() == "wayland":
        exe = shutil.which("wtype")
        modifier = _WTYPE_MODIFIERS.get(name)
        if exe and modifier and _run_inject([exe, "-m", modifier]):
            return True
    exe = shutil.which("xdotool")
    if exe and os.environ.get("DISPLAY"):
        xname = name.replace("windows", "super").replace("win", "super")
        if _run_inject([exe, "keyup", xname]):
            return True
    code = _YDOTOOL_CODES.get(name)
    ydotool = shutil.which("ydotool")
    if ydotool and code is not None:
        return _run_inject([ydotool, "key", f"{code}:0"])
    return False


def unhook_all():
    """Remove every raw keyboard hook; platform helper commands hold no hooks."""
    try:
        keyboard.unhook_all()
        return True
    except Exception:
        return False
