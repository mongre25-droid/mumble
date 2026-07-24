#!/usr/bin/env python3
"""Offline tests for tips.py (ITEM 19 — usage-aware island tips: spaced, capped,
self-retiring, and only ever offered for features the user hasn't adopted).

Standalone (no pytest): run with the venv python; a clean exit 0 = pass.
"""
import sys

import tips

FAILS = []


def check(name, cond):
    print(f"  [{'ok ' if cond else 'FAIL'}] {name}")
    if not cond:
        FAILS.append(name)


# A usage snapshot where NOTHING has been adopted yet (every feature/mode unused,
# no key, plenty of dictations) — so every tip's eligibility predicate holds. The
# `n` is overridden per-call where the dictation gate matters.
def fresh(n=10):
    return {"n": n, "modes": {}, "features": {}, "reader_sessions": 0,
            "has_key": False, "pro_on": True, "favorites": 0,
            "multilingual": False, "foreign_toggle": False}


def test_spacing_time_gate():
    # A tip right after another (within MIN_GAP_SECONDS) is refused.
    st = {"counts": {}, "last_ts": 1000.0, "last_id": "paste_last", "last_n": 0}
    tip, _ = tips.next_tip(st, now=1000.0 + 10, usage=fresh(99))
    check("no tip inside the time gap", tip is None)


def test_spacing_dictation_gate():
    # Enough time, but not enough dictations since the last tip.
    st = {"counts": {}, "last_ts": 0.0, "last_id": "paste_last", "last_n": 10}
    tip, _ = tips.next_tip(st, now=10_000.0, usage=fresh(11))
    check("no tip inside the dictation gap", tip is None)


def test_first_tip_shows():
    tip, st = tips.next_tip({}, now=10_000.0, usage=fresh(10))
    check("a first tip is shown once gates clear", tip is not None)
    check("chosen tip count incremented", st["counts"].get(tip["id"]) == 1)
    check("timing recorded", st["last_ts"] == 10_000.0 and st["last_n"] == 10)


def test_no_immediate_repeat():
    st = {}
    now, n = 0.0, 0
    seen = []
    for _ in range(2):
        now += tips.MIN_GAP_SECONDS + 1
        n += tips.MIN_DICTATIONS_BETWEEN
        tip, st = tips.next_tip(st, now=now, usage=fresh(n))
        if tip:
            seen.append(tip["id"])
    check("two consecutive tips differ", len(seen) == 2 and seen[0] != seen[1])


def test_backcompat_bare_count():
    # next_tip still accepts a bare dictation count (old callers) → treated as {n}.
    tip, _ = tips.next_tip({}, now=10_000.0, usage=40)
    check("bare-int usage still yields a tip", tip is not None)


def test_appearance_cap_and_retire():
    # Drive many eligible cycles with a fully-unadopted usage; every tip stops at
    # its cap, then the whole catalogue retires.
    st = {}
    now, n = 0.0, 0
    for _ in range(400):
        now += tips.MIN_GAP_SECONDS + 1
        n += tips.MIN_DICTATIONS_BETWEEN
        _, st = tips.next_tip(st, now=now, usage=fresh(n))
    counts = st["counts"]
    capped = all(counts.get(t["id"], 0) <= t["max"] for t in tips.TIPS)
    check("no tip ever exceeds its appearance cap", capped)
    check("all tips eventually retire", tips.all_retired(st))
    now += tips.MIN_GAP_SECONDS + 1
    n += tips.MIN_DICTATIONS_BETWEEN
    tip, _ = tips.next_tip(st, now=now, usage=fresh(n))
    check("retired catalogue shows no further tips", tip is None)


def test_total_shows_equals_sum_of_caps():
    st = {}
    now, n, total = 0.0, 0, 0
    for _ in range(800):
        now += tips.MIN_GAP_SECONDS + 1
        n += tips.MIN_DICTATIONS_BETWEEN
        tip, st = tips.next_tip(st, now=now, usage=fresh(n))
        if tip:
            total += 1
    check("lifetime tip shows == sum of caps",
          total == sum(t["max"] for t in tips.TIPS))


# ---- usage-aware gating (the new behaviour) --------------------------------

def _show_until_seen(target_id, usage_fn, cycles=400):
    """Run many spaced cycles and return whether `target_id` is ever offered."""
    st = {}
    now, n = 0.0, 0
    for _ in range(cycles):
        now += tips.MIN_GAP_SECONDS + 1
        n += tips.MIN_DICTATIONS_BETWEEN
        tip, st = tips.next_tip(st, now=now, usage=usage_fn(n))
        if tip and tip["id"] == target_id:
            return True
    return False


def test_mode_tip_suppressed_when_mode_used():
    # The Prompt-mode tip must NEVER appear for a user who already uses Prompt.
    def used_prompt(n):
        u = fresh(n)
        u["modes"] = {"prompt": 6}
        return u
    check("prompt tip suppressed once Prompt is used",
          not _show_until_seen("mode_prompt", used_prompt))
    # …but DOES appear for a user who has never used Prompt.
    check("prompt tip offered when Prompt unused",
          _show_until_seen("mode_prompt", fresh))


def test_feature_tip_suppressed_when_feature_used():
    # The Deck tip must not appear once the Deck has been opened.
    def used_deck(n):
        u = fresh(n)
        u["features"] = {"deck": 3}
        return u
    check("deck tip suppressed once Deck is opened",
          not _show_until_seen("deck", used_deck))
    check("deck tip offered when Deck never opened",
          _show_until_seen("deck", fresh))


def test_pro_tip_only_without_key():
    def has_key(n):
        u = fresh(n)
        u["has_key"] = True
        return u
    check("pro tip suppressed when a key is configured",
          not _show_until_seen("pro", has_key))
    check("pro tip offered when no key is set",
          _show_until_seen("pro", fresh))


def test_experience_gate_holds_back_advanced_tips():
    # A brand-new user (few dictations) should not be shown the late, advanced
    # tips (e.g. hotkey re-binding at min_n=22).
    seen = set()
    st = {}
    now, n = 0.0, 0
    for _ in range(3):                       # only ~12 dictations of experience
        now += tips.MIN_GAP_SECONDS + 1
        n += tips.MIN_DICTATIONS_BETWEEN
        tip, st = tips.next_tip(st, now=now, usage=fresh(n))
        if tip:
            seen.add(tip["id"])
    check("advanced 'hotkeys' tip not shown to a brand-new user",
          "hotkeys" not in seen)


def test_all_retired_respects_usage():
    # With every feature already adopted, the catalogue is effectively retired
    # even though no tip has hit its appearance cap.
    adopted = {"n": 50,
               "modes": {"prompt": 5, "email": 5, "foreign": 5, "convert": 5},
               "features": {"deck": 5, "search": 5, "quick_paste": 5,
                            "capture": 5, "preset_run": 5, "meeting": 5},
               "reader_sessions": 5, "has_key": True, "favorites": 3,
               "foreign_toggle": True}
    # The only still-eligible tips are the pure-experience ones (vocab/stats/
    # hotkeys/infer); a heavily-adopted user can still get those, so the
    # catalogue is NOT fully retired — assert the adoption-gated ones are gone.
    st = {}
    now, n = 0.0, 0
    offered = set()
    for _ in range(400):
        now += tips.MIN_GAP_SECONDS + 1
        n += tips.MIN_DICTATIONS_BETWEEN
        tip, st = tips.next_tip(st, now=now, usage=adopted)
        if tip:
            offered.add(tip["id"])
    adoption_gated = {"deck", "search", "quick_paste", "capture", "preset_run",
                      "meeting", "reader", "favourites", "mode_prompt",
                      "mode_email", "convert", "foreign", "pro"}
    check("no adoption-gated tip is ever offered to a fully-adopted user",
          offered.isdisjoint(adoption_gated))


def main():
    print("tips.py — usage-aware island tips")
    for fn in (test_spacing_time_gate, test_spacing_dictation_gate,
               test_first_tip_shows, test_no_immediate_repeat,
               test_backcompat_bare_count, test_appearance_cap_and_retire,
               test_total_shows_equals_sum_of_caps,
               test_mode_tip_suppressed_when_mode_used,
               test_feature_tip_suppressed_when_feature_used,
               test_pro_tip_only_without_key,
               test_experience_gate_holds_back_advanced_tips,
               test_all_retired_respects_usage):
        fn()
    if FAILS:
        print(f"\n{len(FAILS)} FAILED:", ", ".join(FAILS))
        sys.exit(1)
    print("\nALL GREEN")


if __name__ == "__main__":
    main()
