#!/usr/bin/env python3
"""Offline tests for the island MODE DECK geometry + render (ITEM 3/4).

bar_layout() is the single source of geometry shared by island_render.render_bar
(drawing) and overlay._WidgetBar (click hit-testing). If they ever drift, a click
lands on the wrong chip — so these tests pin the invariants: zones are ordered,
non-overlapping, inside the pill, and present exactly when they should be. Also a
smoke test that render_bar() produces a correctly-sized image for each case.

Standalone (no pytest): run with the venv python; a clean exit 0 = pass.
"""
import sys

import island_render as ir

FAILS = []
MODES = [("prompt", "Prompt"), ("email", "Email"),
         ("list", "List"), ("reply", "Reply")]


def check(name, cond):
    print(f"  [{'ok ' if cond else 'FAIL'}] {name}")
    if not cond:
        FAILS.append(name)


def test_layout_ordered_and_inside_pill():
    lay = ir.bar_layout({"modes": MODES, "show_foreign": False})
    px0, px1 = lay["pill"]
    chips = lay["chips"]
    check("one zone per mode", len(chips) == len(MODES))
    check("chip keys match + in order",
          [k for k, _, _ in chips] == [k for k, _ in MODES])
    # chips strictly increasing + non-overlapping
    ok = True
    prev = px0
    for _k, x0, x1 in chips:
        if not (x0 < x1) or x0 < prev - 0.01:
            ok = False
        prev = x1
    check("chips non-overlapping, left→right", ok)
    deck = lay["deck"]
    check("deck after the last chip", deck[0] >= chips[-1][2] - 0.01)
    check("everything inside the pill",
          chips[0][1] >= px0 - 0.01 and deck[1] <= px1 + 0.01)
    check("no foreign zone when show_foreign is False", lay["foreign"] is None)


def test_foreign_zone_only_when_enabled():
    on = ir.bar_layout({"modes": MODES, "show_foreign": True})
    check("foreign zone present when enabled", on["foreign"] is not None)
    if on["foreign"]:
        check("foreign sits after the deck", on["foreign"][0] >= on["deck"][1] - 0.01)
        check("foreign inside the pill", on["foreign"][1] <= on["pill"][1] + 0.01)


def test_hit_test_maps_x_to_chip():
    lay = ir.bar_layout({"modes": MODES, "show_foreign": True})
    # the midpoint of each chip must resolve back to that chip's key
    for key, x0, x1 in lay["chips"]:
        mid = (x0 + x1) / 2
        hit = next((k for k, a, b in lay["chips"] if a <= mid < b), None)
        check(f"midpoint of '{key}' hits '{key}'", hit == key)
    # a point in the deck zone is not inside any chip
    dmid = (lay["deck"][0] + lay["deck"][1]) / 2
    in_chip = any(a <= dmid < b for _k, a, b in lay["chips"])
    check("deck midpoint is not inside a chip", not in_chip)


def test_render_smoke():
    cases = [
        {"modes": MODES, "active": None, "show_foreign": False},
        {"modes": MODES, "active": "prompt", "show_foreign": False},
        {"modes": MODES, "active": "email", "show_foreign": True, "foreign_on": True},
        {"modes": [("prompt", "Prompt")], "active": "prompt", "show_foreign": False},
    ]
    ok = True
    for c in cases:
        c["frame"] = 5
        try:
            im = ir.render_bar(c)
            if im.size != (ir.BAR_WIN_W, ir.BAR_WIN_H):
                ok = False
        except Exception as e:
            print("    render error:", e)
            ok = False
    check("render_bar produces correctly-sized images for every case", ok)


def test_single_mode_default():
    lay = ir.bar_layout({})  # no modes → defaults to Prompt only
    check("empty snap falls back to one Prompt chip",
          len(lay["chips"]) == 1 and lay["chips"][0][0] == "prompt")


def main():
    print("island mode deck — bar_layout + render")
    for fn in (test_layout_ordered_and_inside_pill,
               test_foreign_zone_only_when_enabled,
               test_hit_test_maps_x_to_chip, test_render_smoke,
               test_single_mode_default):
        fn()
    if FAILS:
        print(f"\n{len(FAILS)} FAILED:", ", ".join(FAILS))
        sys.exit(1)
    print("\nALL GREEN")


if __name__ == "__main__":
    main()
