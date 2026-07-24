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
check("multi-step tap sequence is rejected",
      b.validate("ctrl+k, ctrl+f")[0] is False)

print("\n== semantic press-binding collisions ==")
check("modifier order and aliases collide",
      b.conflicts("windows+control+f", "ctrl+win+f"))
check("left/right modifiers collide with their generic chord",
      b.conflicts("left ctrl+right alt+f", "ctrl+alt+f"))
check("subset hotkeys collide with a longer chord",
      b.conflicts("ctrl+windows", "ctrl+windows+f"))
check("distinct Mumble defaults do not collide",
      not b.conflicts("ctrl+alt+f", "ctrl+alt+d")
      and not b.conflicts("ctrl+alt+f", "ctrl+alt+v")
      and not b.conflicts("ctrl+alt+f", "ctrl+windows"))
check("mouse aliases collide semantically",
      b.conflicts("mouse:forward", "mouse:x2"))
check("self setting can be excluded from conflict lookup",
      b.find_conflict("ctrl+alt+f", {"search_hotkey": "alt+ctrl+f"},
                      exclude="search_hotkey") is None)

print("\n== _order_mods — Ctrl+Windows reads canonically whatever the press order ==")
check("ctrl before windows", b._order_mods(["windows", "ctrl"]) == ["ctrl", "windows"])
check("full canonical order",
      b._order_mods(["windows", "shift", "alt", "ctrl"]) ==
      ["ctrl", "alt", "shift", "windows"])

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
