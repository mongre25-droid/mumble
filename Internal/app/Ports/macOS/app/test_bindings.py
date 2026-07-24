#!/usr/bin/env python3
"""Tests for bindings.py — the keyboard+mouse binding layer.

Covers the pure logic (normalize / validate / pretty / is_mouse / aliases) and
the safety of unregister(None). Actual hook registration needs the live OS hook
and is exercised by running the app, not here.

Run: python test_bindings.py
"""

import bindings as b

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [ok  ] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}")


print("== is_mouse / normalize ==")
check("keyboard spec is not mouse", not b.is_mouse("ctrl+windows"))
check("mouse spec is mouse", b.is_mouse("mouse:x2"))
check("normalize lowercases + trims", b.normalize("  Ctrl+Windows ") == "ctrl+windows")
check("alias forward -> x2", b.normalize("mouse:forward") == "mouse:x2")
check("alias back -> x", b.normalize("mouse:back") == "mouse:x")
check("alias button5 -> x2", b.normalize("mouse:button5") == "mouse:x2")
check("alias wheel -> middle", b.normalize("mouse:wheel") == "mouse:middle")
check("canonical mouse:x2 stays", b.normalize("mouse:x2") == "mouse:x2")
check("bad mouse button left unresolved", b.normalize("mouse:bogus") == "mouse:bogus")

print("\n== validate ==")
check("valid keyboard combo", b.validate("ctrl+windows")[0] is True)
# A non-modifier single key is a fine tap hotkey; a bare MODIFIER is not (see the
# is_bare_modifier section below) — that's the 'Ctrl alone starts Mumble' guard.
check("valid single key", b.validate("f9")[0] is True)
check("empty rejected", b.validate("")[0] is False)
check("gibberish keyboard rejected", b.validate("!!nope!!")[0] is False)
check("valid mouse button (when lib present)",
      b.validate("mouse:x2")[0] is b.HAVE_MOUSE)
check("unknown mouse button rejected",
      b.validate("mouse:bogus")[0] is False)
ok, msg = b.validate("mouse:bogus")
check("unknown-button message is helpful", "mouse:x2" in msg)

print("\n== validate(hold=True) — the mode key must be holdable ==")
check("single key OK for hold", b.validate("right shift", hold=True)[0] is True)
check("mouse button OK for hold",
      b.validate("mouse:x2", hold=True)[0] is b.HAVE_MOUSE)
check("combo REJECTED for hold (register_hold can't hook combos)",
      b.validate("ctrl+alt", hold=True)[0] is False)
ok, msg = b.validate("ctrl+alt", hold=True)
check("hold-combo message explains the fix", "SINGLE key" in msg)
check("combo still fine for tap-style bindings",
      b.validate("ctrl+alt", hold=False)[0] is True)

print("\n== is_bare_modifier — the 'Ctrl alone starts Mumble' guard ==")
check("bare ctrl is a bare modifier", b.is_bare_modifier("ctrl"))
check("left ctrl is a bare modifier", b.is_bare_modifier("left ctrl"))
check("windows is a bare modifier", b.is_bare_modifier("windows"))
check("left windows is a bare modifier", b.is_bare_modifier("left windows"))
check("alt / shift are bare modifiers",
      b.is_bare_modifier("alt") and b.is_bare_modifier("right shift"))
check("a real combo is NOT a bare modifier", not b.is_bare_modifier("ctrl+windows"))
check("a normal key is NOT a bare modifier", not b.is_bare_modifier("f9"))
check("a mouse button is NOT a bare modifier", not b.is_bare_modifier("mouse:x2"))

print("\n== validate rejects a bare modifier as a TAP (press) hotkey ==")
check("bare ctrl rejected for a tap hotkey", b.validate("ctrl")[0] is False)
check("bare windows rejected for a tap hotkey", b.validate("windows")[0] is False)
ok, msg = b.validate("ctrl")
check("bare-modifier message names the fix (Ctrl + Windows)", "Windows" in msg)
check("bare modifier still OK for a HOLD binding (mode key)",
      b.validate("ctrl", hold=True)[0] is True)
check("the default record hotkey still validates", b.validate("ctrl+windows")[0] is True)

print("\n== _order_mods — Ctrl+Windows reads canonically whatever the press order ==")
check("ctrl before windows", b._order_mods(["windows", "ctrl"]) == ["ctrl", "windows"])
check("full canonical order",
      b._order_mods(["windows", "shift", "alt", "ctrl"]) ==
      ["ctrl", "alt", "shift", "windows"])

print("\n== macOS pynput grammar (platform-neutral regression tests) ==")
check("Option canonicalizes to alt",
      b._darwin_spec_tokens("ctrl+option+d") == frozenset({"ctrl", "alt", "d"}))
check("Command canonicalizes to windows",
      b._darwin_spec_tokens("command+shift+p") ==
      frozenset({"windows", "shift", "p"}))
check("sided Option remains holdable",
      b._darwin_spec_tokens("right option") == frozenset({"right alt"}))
check("two non-modifier keys are rejected",
      not b._darwin_spec_tokens("d+v"))
check("unknown key names are rejected",
      not b._darwin_spec_tokens("ctrl+!!nope!!"))
check("duplicate aliases are rejected",
      not b._darwin_spec_tokens("ctrl+control+d"))
check("ASCII layout character wins over US virtual-key position",
      b._darwin_character_token("a", 12) == "a")
check("Option's non-ASCII glyph falls back to base virtual key",
      b._darwin_character_token("∂", 2) == "d")
check("Quartz side-button numbers map to X1/X2",
      b._darwin_mouse_number_token(3) == "mouse:x"
      and b._darwin_mouse_number_token(4) == "mouse:x2")
check("paste wrapper is available", callable(b.send))
_bindings_src = open(b.__file__, encoding="utf-8").read()
check("Darwin backend uses pynput", "from pynput import keyboard" in _bindings_src)
check("Darwin backend never asks for root", "geteuid" not in _bindings_src)

print("\n== fake pynput Darwin event hub ==")
import importlib.util
import os
import sys
import types


class _FakeValue:
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"<{self.name}>"


class _FakeKey:
    pass


for _name in (
        "ctrl", "ctrl_r", "alt", "alt_r", "shift", "shift_r", "cmd", "cmd_r",
        "space", "enter", "esc", "tab", "backspace", "delete", "home", "end",
        "page_up", "page_down", "left", "right", "up", "down"):
    setattr(_FakeKey, _name, _FakeValue(_name))
_FakeKey.ctrl_l = _FakeKey.ctrl
_FakeKey.alt_l = _FakeKey.alt
_FakeKey.shift_l = _FakeKey.shift
_FakeKey.cmd_l = _FakeKey.cmd
for _i in range(1, 21):
    setattr(_FakeKey, f"f{_i}", _FakeValue(f"f{_i}"))


class _FakeKeyCode:
    def __init__(self, char=None, vk=None):
        self.char = char
        self.vk = vk


class _FakeButton:
    left = _FakeValue("mouse-left")
    right = _FakeValue("mouse-right")
    middle = _FakeValue("mouse-middle")
    x1 = _FakeValue("mouse-x1")
    x2 = _FakeValue("mouse-x2")


class _FakeListener:
    def __init__(self, **callbacks):
        self.callbacks = callbacks
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def is_alive(self):
        return self.started


class _FakeController:
    def __init__(self):
        self.events = []

    def press(self, key):
        self.events.append(("down", key))

    def release(self, key):
        self.events.append(("up", key))


_fake_keyboard = types.ModuleType("pynput.keyboard")
_fake_keyboard.Key = _FakeKey
_fake_keyboard.KeyCode = _FakeKeyCode
_fake_keyboard.Listener = _FakeListener
_fake_keyboard.Controller = _FakeController
_fake_mouse = types.ModuleType("pynput.mouse")
_fake_mouse.Button = _FakeButton
_fake_mouse.Listener = _FakeListener
_fake_pynput = types.ModuleType("pynput")
_fake_pynput.keyboard = _fake_keyboard
_fake_pynput.mouse = _fake_mouse

_module_names = ("pynput", "pynput.keyboard", "pynput.mouse")
_saved_modules = {name: sys.modules.get(name) for name in _module_names}
_saved_platform = sys.platform
try:
    sys.modules["pynput"] = _fake_pynput
    sys.modules["pynput.keyboard"] = _fake_keyboard
    sys.modules["pynput.mouse"] = _fake_mouse
    sys.platform = "darwin"
    _spec = importlib.util.spec_from_file_location(
        "_mumble_bindings_fake_darwin", os.path.abspath(b.__file__))
    _mac_b = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mac_b)

    _hotkey_hits = []
    _mac_b.register_hotkey(
        "ctrl+option+d", lambda: _hotkey_hits.append("hotkey"))
    _hub = _mac_b._MAC_HUB
    _old_keyboard_listener = _hub._keyboard_listener
    _old_keyboard_listener.started = False
    _hub._ensure_started()
    check("Darwin hub replaces a dead listener thread",
          _hub._keyboard_listener is not _old_keyboard_listener
          and _hub._keyboard_listener.is_alive())
    _d_event = _FakeKeyCode(char="∂", vk=2)
    _hub._on_key_press(_FakeKey.ctrl)
    _hub._on_key_press(_FakeKey.alt)
    _hub._on_key_press(_d_event)
    _hub._on_key_press(_d_event)  # key repeat must not double-fire
    check("Ctrl+Option+D fires once through Darwin hub",
          _hotkey_hits == ["hotkey"])
    # Quartz may report a different char on release if Option was released in
    # between; the physical vk still has to clear/re-arm the binding.
    _hub._on_key_release(_FakeKeyCode(char="d", vk=2))
    _hub._on_key_press(_d_event)
    check("Darwin hotkey re-arms after release",
          _hotkey_hits == ["hotkey", "hotkey"])

    _mouse_hits = []
    _mac_b.register_hotkey("mouse:x2", lambda: _mouse_hits.append("x2"))
    _hub._on_click(0, 0, "mouse:x2", True)
    _hub._on_click(0, 0, "mouse:x2", False)
    check("Darwin X2 mouse binding fires", _mouse_hits == ["x2"])

    _hold_hits = []
    _mac_b.register_hold(
        "right shift", lambda: _hold_hits.append("down"),
        lambda: _hold_hits.append("up"))
    _hub._on_key_press(_FakeKey.shift_r)
    _hub._on_key_release(_FakeKey.shift_r)
    check("Darwin hold binding reports down and up",
          _hold_hits == ["down", "up"])
    _mac_b.unhook_all()
finally:
    sys.platform = _saved_platform
    for _name, _saved in _saved_modules.items():
        if _saved is None:
            sys.modules.pop(_name, None)
        else:
            sys.modules[_name] = _saved

print("\n== pretty ==")
check("keyboard combo prettifies", b.pretty("ctrl+alt+v") == "Ctrl + Alt + V")
check("mouse x2 prettifies", b.pretty("mouse:x2") == "Mouse Forward (X2)")
check("mouse x prettifies", b.pretty("mouse:x") == "Mouse Back (X1)")
check("mouse middle prettifies", b.pretty("mouse:middle") == "Mouse Middle (wheel)")

print("\n== unregister safety ==")
try:
    b.unregister(None)
    b.unregister(None)
    check("unregister(None) is a safe no-op", True)
except Exception:
    check("unregister(None) is a safe no-op", False)

print("\n== register_hotkey rejects a bad mouse spec ==")
raised = False
try:
    b.register_hotkey("mouse:bogus", lambda: None)
except Exception:
    raised = True
check("register_hotkey raises on a bad mouse button", raised)

print()
if failed:
    print(f"{failed} FAILED, {passed} passed")
    raise SystemExit(1)
print(f"ALL PASS ({passed} checks)")
