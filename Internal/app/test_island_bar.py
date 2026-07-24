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
MODES = [("prompt", "Prompt"), ("email", "Email")]


def check(name, cond):
    print(f"  [{'ok ' if cond else 'FAIL'}] {name}")
    if not cond:
        FAILS.append(name)


def test_layout_ordered_and_inside_pill():
    lay = ir.bar_layout({"modes": MODES, "show_foreign": False})
    px0, px1 = lay["pill"]
    chips = lay["chips"]
    check("one visible zone per direct mode", len(chips) == len(MODES))
    check("chip keys match + stay in order",
          [k for k, _, _ in chips] == [k for k, _ in MODES])
    check("the old dropdown caret is gone", lay.get("caret") is None)
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
        check("foreign sits with modes before the Deck separator",
              on["foreign"][1] <= on["sep_x"] + 0.01
              and on["foreign"][1] <= on["deck"][0] + 0.01)
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


def test_control_review_replaces_the_mode_row_with_safe_actions():
    lay = ir.bar_layout({
        "modes": MODES,
        "active": "prompt",
        "expanded": True,
        "show_foreign": True,
        "control_review_available": True,
    })
    review = lay.get("control_review")
    cancel = lay.get("control_cancel")
    check("computer plan exposes Review plan", review is not None)
    check("computer plan exposes Cancel", cancel is not None)
    check("computer plan hides unrelated mode chips", lay["chips"] == [])
    check("review and cancel are ordered and non-overlapping",
          bool(review and cancel and review[0] < review[1] <= cancel[0] < cancel[1]))
    check("computer review bar renders", ir.render_bar({
        "frame": 5,
        "control_review_available": True,
        "control_review_hint": "Computer plan ready",
    }).size == (ir.BAR_WIN_W, ir.BAR_WIN_H))


def test_correction_review_is_focused_and_dismissible():
    lay = ir.bar_layout({
        "modes": MODES,
        "active": "prompt",
        "expanded": True,
        "show_foreign": True,
        "correction_available": True,
    })
    review = lay.get("correction")
    dismiss = lay.get("correction_dismiss")
    check("detected correction exposes Review", review is not None)
    check("detected correction exposes Dismiss", dismiss is not None)
    check("correction review hides unrelated mode controls", lay["chips"] == [])
    check("correction review hides Deck and Foreign",
          lay["deck"] is None and lay["foreign"] is None)
    check("Review and Dismiss are ordered and non-overlapping",
          bool(review and dismiss and review[0] < review[1] <= dismiss[0] < dismiss[1]))
    faded = ir.render_bar({
        "frame": 5,
        "correction_available": True,
        "fade": 0.5,
    })
    check("correction review renders",
          faded.size == (ir.BAR_WIN_W, ir.BAR_WIN_H))
    check("bar follows the island fade",
          0 < faded.getchannel("A").getextrema()[1] <= 128)


def test_direct_modes_stay_visible():
    lay = ir.bar_layout({})
    check("empty snapshot falls back to one real Prompt chip",
          len(lay["chips"]) == 1 and lay["chips"][0][0] == "prompt")
    check("no dropdown caret is present", lay.get("caret") is None)
    lay2 = ir.bar_layout({"modes": MODES, "active": "email"})
    check("all direct modes remain visible when one is active",
          [key for key, _x0, _x1 in lay2["chips"]] == ["prompt", "email"])
    lay3 = ir.bar_layout({"modes": MODES, "active": "email", "expanded": True})
    check("legacy expanded state does not change the layout",
          lay3["chips"] == lay2["chips"] and lay3["caret"] is None)
    cw = lay2["pill"][1] - lay2["pill"][0]
    ew = lay3["pill"][1] - lay3["pill"][0]
    check("rail width is stable across legacy expanded state", cw == ew)


def test_language_label_is_specific():
    generic = ir.bar_layout({"modes": MODES, "show_foreign": True,
                             "foreign_label": "Language"})
    arabic = ir.bar_layout({"modes": MODES, "show_foreign": True,
                            "foreign_label": "Arabic"})
    check("configured language has a real hit target", arabic["foreign"] is not None)
    check("language label participates in sizing",
          (arabic["pill"][1] - arabic["pill"][0])
          != (generic["pill"][1] - generic["pill"][0]))


def test_listening_timer_does_not_move_waveform():
    # The right zone uses a fixed-width timer template so the waveform bars stay
    # rock-steady regardless of which digits the clock shows. The timer is drawn
    # right-aligned inline within that zone, right of the separator bar.
    base = {"state": "listening", "label": "Listening", "level": 0.6, "frame": 7}
    widths = {t: ir.pill_width({**base, "timer": t})
              for t in ("0:00", "0:09", "1:07", "12:45")}
    check("listening pill width is invariant to the timer text (fixed right zone)",
          len(set(widths.values())) == 1)
    # The two-row stacked-timer layout is removed; the timer lives inline in the
    # right zone. The `_two_row` function no longer exists — pill height is
    # PILL_H for every state.
    check("all states share the same pill height (PILL_H only)",
          ir._pill_h({"state": "listening", "timer": "0:04"}) == ir.PILL_H
          and ir._pill_h({"state": "transcribing", "timer": ""}) == ir.PILL_H
          and ir._pill_h({"state": "idle"}) == ir.PILL_H
          and ir._pill_h({"state": "building"}) == ir.PILL_H)
    # The listening pill still renders at canvas size, now at the default height.
    im = ir.render({**base, "timer": "1:07"})
    check("listening renders at canvas size (single-row)", im.size == (ir.WIN_W, ir.WIN_H))


def main():
    print("island mode deck — bar_layout + render")
    for fn in (test_layout_ordered_and_inside_pill,
               test_foreign_zone_only_when_enabled,
               test_hit_test_maps_x_to_chip, test_render_smoke,
               test_control_review_replaces_the_mode_row_with_safe_actions,
               test_correction_review_is_focused_and_dismissible,
               test_direct_modes_stay_visible, test_language_label_is_specific,
               test_listening_timer_does_not_move_waveform):
        fn()
    if FAILS:
        print(f"\n{len(FAILS)} FAILED:", ", ".join(FAILS))
        sys.exit(1)
    print("\nALL GREEN")


if __name__ == "__main__":
    main()
