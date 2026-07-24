#!/usr/bin/env python3
"""The Big Shift — regression guards for the new auto-inference brain,
hardware/language model resolution, and the settings migration.

Run: .venv\\Scripts\\python.exe test_big_shift.py
"""
import sys

import branding
import formatting as f

_fails = []


def check(label, cond):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {label}")
    if not cond:
        _fails.append(label)


# ============================================ plain text dictation (no auto-inference)
print("\n== plain text: all dictation produces clean, punctuated text only ==")

# Enumerations stay plain prose — NO auto list/email inference.
out = f.format_transcript("let's add commerce", commands=False)
check("'let's add commerce' -> plain text (not list/email)",
      "Let's" in out and "commerce" in out and "- " not in out and "Hi " not in out)

out = f.format_transcript("Hey Jared, can we get this done", commands=False)
check("'Hey Jared...' -> plain text (not email)",
      "Hey" in out and "Jared" in out and "Subject:" not in out)

out = f.format_transcript("milk eggs bread", commands=False)
check("'milk eggs bread' -> plain text (not bulleted list)",
      "Milk" in out and "eggs" in out and "bread" in out and "- " not in out)

out = f.format_transcript(
    "first open the file second click save third close it", commands=False)
check("ordinals (first/second/third) -> plain text (not numbered list)",
      "first" in out.lower() and "1. " not in out)

out = f.format_transcript("shopping list milk eggs cheese and bread", commands=False)
check("'shopping list ...' -> plain text (not bullets)",
      "- " not in out and "Shopping list" in out)

out = f.format_transcript("apples, oranges, bananas, and grapes", commands=False)
check("comma enumeration -> plain text (not bullets)",
      "- " not in out and "Apples" in out)

out = f.format_transcript("can you make a list of John, Sarah and Mike", commands=False)
check("'make a list of...' -> plain text",
      "- " not in out and "John" in out)

out = f.format_transcript("hi john, thanks for the update. best regards", commands=False)
check("greeting + sign-off -> plain text (NOT auto-email)",
      "john" in out.lower() and "thanks" in out.lower() and "\n\n" not in out)

out = f.format_transcript("the weather is nice today I think I will go for a walk later",
                          commands=False)
check("ordinary sentence -> clean text", "weather" in out and out.endswith("."))

out = f.format_transcript("", commands=False)
check("empty input -> empty", out == "")


# ============================================ hardware/language model resolution
print("\n== branding.resolve_model: (tier x english_only) -> model id ==")
cases = {
    ("weak", True): "base.en",
    ("weak", False): "base",
    ("mid", True): "small.en",
    ("mid", False): "small",
    ("powerful", True): "distil-large-v3",
    ("powerful", False): "large-v3-turbo",
}
for (tier, en), expect in cases.items():
    got = branding.resolve_model(tier, en)
    check(f"{tier:9} english_only={en!s:5} -> {expect}", got == expect)

check("unknown tier falls back to mid/small.en",
      branding.resolve_model("bogus", True) == "small.en")

print("\n== branding.detect_hardware_tier: returns a valid tier ==")
tier = branding.detect_hardware_tier()
check(f"detected tier {tier!r} is one of {branding.HARDWARE_TIERS}",
      tier in branding.HARDWARE_TIERS)


# ============================================ settings migration (english_only)
print("\n== settings migration: english_only inferred from saved model ==")
import json
import os
import tempfile

import settings as settings_mod


def _migrated(model_value):
    """Run the migration over a settings.json that pins `model`, return the dict."""
    d = json.loads(json.dumps(settings_mod.DEFAULTS))
    d["model"] = model_value
    d["big_shift_applied"] = False
    # write to a temp path and point branding at it
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    orig = branding.SETTINGS_PATH
    try:
        branding.SETTINGS_PATH = path
        s = settings_mod.Settings()
        return dict(s.data)
    finally:
        branding.SETTINGS_PATH = orig
        try:
            os.remove(path)
            os.remove(path + ".bak")
        except OSError:
            pass


d = _migrated("small.en")
check("saved '.en' model -> english_only True", d.get("english_only") is True)
check("big_shift_applied set after migration", d.get("big_shift_applied") is True)
check("retired mode key removed", "mode_button_enabled" not in d)

d = _migrated("large-v3")
check("saved multilingual model -> english_only False",
      d.get("english_only") is False)


print("\n== new DEFAULTS present ==")
for k, exp in (("prompt_mode_enabled", False), ("auto_format", False),
               ("english_only", True),
               ("deck_pinned", True)):
    check(f"default {k} = {exp!r}", settings_mod.DEFAULTS.get(k) == exp)


print("\n" + ("ALL GREEN" if not _fails else f"{len(_fails)} FAILED: {_fails}"))
sys.exit(1 if _fails else 0)
