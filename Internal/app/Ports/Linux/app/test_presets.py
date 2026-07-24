#!/usr/bin/env python3
"""Tests for the Context Island intent presets (presets.py).

Covers the descriptions-per-preset change, the new 'Chat context' preset,
and the custom-slot derivation that must never collide with a built-in slot.
Run: python test_presets.py
"""

import os
import tempfile

import presets

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [ok  ] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}")


print("== presets: built-in shape ==")
check("every built-in is a (title, description, instruction) triple",
      all(isinstance(p, tuple) and len(p) == 3 for p in presets.BUILTIN))
check("every built-in title is non-empty",
      all(p[0].strip() for p in presets.BUILTIN))
check("every built-in has a non-empty description",
      all(p[1].strip() for p in presets.BUILTIN))
check("every built-in description is short enough for the UI line (<=40)",
      all(len(p[1]) <= 40 for p in presets.BUILTIN))
check("every built-in instruction is substantial (>=40 chars)",
      all(len(p[2].strip()) >= 40 for p in presets.BUILTIN))

print("\n== presets: the new Chat context preset ==")
titles = [t for t, _, _ in presets.BUILTIN]
check("'Chat context' preset exists", "Chat context" in titles)
chat = next((p for p in presets.BUILTIN if p[0] == "Chat context"), None)
check("Chat context mentions prompts + replies",
      chat is not None and "prompt" in chat[2].lower()
      and "repl" in chat[2].lower())
check("Chat context tells the AI not to answer/continue",
      chat is not None and "not" in chat[2].lower()
      and "continue" in chat[2].lower())

print("\n== presets: mode directives (intent × mode composition) ==")
check("MODE_DIRECTIVES covers exactly the five modes",
      set(presets.MODE_DIRECTIVES) == {"email", "prompt", "reply",
                                       "foreign", "convert"})
check("every directive is a real instruction (>=30 chars)",
      all(len(v.strip()) >= 30 for v in presets.MODE_DIRECTIVES.values()))

print("\n== presets: custom slots never collide with built-ins ==")
nbuiltin = len(presets.BUILTIN)
check("5 custom slots offered", len(presets.CUSTOM_SLOTS) == 5)
check("custom slots sit immediately after the built-ins",
      presets.CUSTOM_SLOTS == tuple(range(nbuiltin + 1, nbuiltin + 6)))
check("advertised preset total is exactly 25 (20 built-in + 5 custom)",
      nbuiltin == 20 and nbuiltin + len(presets.CUSTOM_SLOTS) == 25)
ap = presets.all_presets()
slots = [row[0] for row in ap]
check("all_presets slots are unique (no collision)", len(slots) == len(set(slots)))
check("all_presets returns 4-tuples (slot, title, desc, instruction|None)",
      all(len(row) == 4 for row in ap))
check("all_presets count = built-ins + 5 custom slots",
      len(ap) == nbuiltin + 5)
check("empty custom slots render as '+ Add preset' with instruction None",
      all(row[1] == "+ Add preset" and row[3] is None
          for row in ap[nbuiltin:]))

print("\n== presets: custom save/load round-trip carries description ==")
fd, tmp = tempfile.mkstemp(suffix=".json")
os.close(fd)
_orig = presets.PRESETS_PATH
try:
    presets.PRESETS_PATH = tmp
    slot = presets.CUSTOM_SLOTS[0]
    presets.save_custom({
        slot: {"title": "My intent", "description": "does my thing",
               "instruction": "Do the specific thing the user wants done."}
    })
    loaded = presets.load_custom()
    check("custom preset loads back",
          slot in loaded and loaded[slot]["title"] == "My intent")
    check("description survives the round-trip",
          loaded.get(slot, {}).get("description") == "does my thing")
    ap2 = presets.all_presets()
    row = next((r for r in ap2 if r[0] == slot), None)
    check("custom preset appears in all_presets with its description",
          row is not None and row[1] == "My intent" and row[2] == "does my thing")
    # A custom entry with no description still loads (back-compat / optional).
    presets.save_custom({
        slot: {"title": "No desc", "instruction": "Just do it cleanly."}
    })
    loaded2 = presets.load_custom()
    check("missing description defaults to empty string",
          loaded2.get(slot, {}).get("description") == "")
finally:
    presets.PRESETS_PATH = _orig
    try:
        os.remove(tmp)
    except OSError:
        pass

print()
if failed:
    print(f"{failed} FAILED, {passed} passed")
    raise SystemExit(1)
print(f"ALL PASS ({passed} checks)")
