#!/usr/bin/env python3
"""Tests for favorites.py — the Hub's ★ store.
Run: python test_favorites.py
"""

import os
import tempfile

from favorites import Favorites

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [ok  ] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}")


fd, tmp = tempfile.mkstemp(suffix=".json")
os.close(fd)
os.remove(tmp)  # Favorites handles a missing file
try:
    f = Favorites(tmp)
    check("starts empty", f.recent() == [])
    check("add returns True", f.add("hello world", "clipboard", "12:00"))
    check("duplicate add returns False", not f.add("hello world"))
    check("is_fav true after add", f.is_fav("hello world"))
    check("is_fav whitespace-insensitive", f.is_fav("  hello world  "))
    check("toggle off returns False", f.toggle("hello world") is False)
    check("toggle removed it", not f.is_fav("hello world"))
    check("toggle on returns True", f.toggle("again", "transcript") is True)

    # Persistence round-trip
    f2 = Favorites(tmp)
    check("survives reload", f2.is_fav("again"))
    check("recent newest-first",
          [e["text"] for e in f2.recent()] == ["again"])
    check("empty text rejected", not f2.add("   "))
    check("remove missing returns False", not f2.remove("never added"))

    # Cap: oldest dropped beyond MAX_FAVS
    import favorites as favmod
    for i in range(favmod.MAX_FAVS + 5):
        f2.add(f"item {i}")
    check("capped at MAX_FAVS", len(f2.recent()) == favmod.MAX_FAVS)
    check("newest kept after cap",
          f2.is_fav(f"item {favmod.MAX_FAVS + 4}"))
finally:
    try:
        os.remove(tmp)
    except OSError:
        pass

print()
if failed:
    print(f"{failed} FAILED, {passed} passed")
    raise SystemExit(1)
print(f"ALL PASS ({passed} checks)")
