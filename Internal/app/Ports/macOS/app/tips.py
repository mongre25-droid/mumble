#!/usr/bin/env python3
"""Island tips — usage-aware, spaced, self-retiring onboarding hints.

The dictation island can surface a useful hint now and then ("Ctrl + Option + V
pastes your last transcription"). The owner's rules: show them NATURALLY, SPACE
them apart, NEVER spam, and NEVER repeat the same tip endlessly.

What makes a tip appear is now USAGE-AWARE — passive education that never nags:

  • A tip about a feature is only OFFERED while that feature is still UNUSED
    (and only once the user has enough dictations under their belt to benefit).
  • The moment the user ADOPTS a feature, every tip about it RETIRES on its own
    — we never recommend something the user already does.
  • Each tip also has a hard appearance cap, so even a general tip can't loop.

This keeps the island an unobtrusive teacher: it points out the *next* useful
thing you haven't discovered, then gets out of the way.

This module is pure logic: (state, now, usage) in → (tip, new_state) out, so it
is fully testable offline. Persistence (the per-tip counts + timing) lives in
settings.json under "island_tips"; rendering uses the island's own hint chip
(overlay.Island.hint), so a tip looks like the island, never a notification.

Adding a tip is just appending a dict to TIPS — give it an id, the island-hint
text, an appearance cap, and an `eligible(usage)` predicate (or None for a
general tip gated only by experience). The predicate helpers below
(`mode_new`, `feature_new`, `after`, …) cover the common cases.
"""

import math


# ---------------------------------------------------------------------------
# Usage signal helpers — read the `usage` snapshot the controller assembles
# (mumble.Mumble._tip_usage). Every helper is defensive: a missing key reads as
# zero/empty so a tip is simply not offered rather than raising.
# ---------------------------------------------------------------------------

def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_float(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _n(u):
    """Total dictations so far — the experience gate (don't nag brand-new users)."""
    return _safe_int((u or {}).get("n", 0) or 0)


def _modes(u):
    return (u or {}).get("modes") or {}


def _feat(u):
    return (u or {}).get("features") or {}


def after(min_n):
    """A general tip: eligible once the user has `min_n` dictations of experience."""
    return lambda u: _n(u) >= min_n


def mode_new(mode, min_n):
    """Eligible only if the user has NEVER used `mode`, after `min_n` dictations.
    Adopting the mode (one use) retires the tip permanently."""
    return lambda u: _n(u) >= min_n and _safe_int(
        _modes(u).get(mode, 0) or 0) == 0


def feature_new(name, min_n):
    """Eligible only if the user has NEVER used feature `name` (a coarse adoption
    counter in stats.feature_usage), after `min_n` dictations."""
    return lambda u: _n(u) >= min_n and _safe_int(
        _feat(u).get(name, 0) or 0) == 0


def all_of(*preds):
    """Combine predicates with AND (a tip is eligible only when every one holds)."""
    return lambda u: all(p(u) for p in preds)


# ---------------------------------------------------------------------------
# The tip library. Order is only a gentle tiebreak; selection prefers the
# LEAST-shown eligible tip so the rotation spreads evenly. Each tip:
#   id        a stable identifier (its persisted appearance count keys off this)
#   text      the island-hint phrasing (short — it shares the hint chip)
#   max       how many times it may EVER show before it retires permanently
#   eligible  predicate(usage)->bool: True only when the tip is RELEVANT (the
#             feature is still unused and the user has enough experience). None
#             = a general tip, gated only by the spacing rules.
# ---------------------------------------------------------------------------
TIPS = [
    # ---- Core workflow (everyone, early) ----
    {"id": "paste_last", "max": 3, "eligible": feature_new("quick_paste", 3),
     "text": "Ctrl + Option + V re-pastes your last transcription"},
    {"id": "deck", "max": 3, "eligible": feature_new("deck", 4),
     "text": "Ctrl + Option + H opens the Deck — your full history"},
    {"id": "search", "max": 2, "eligible": feature_new("search", 6),
     "text": "Ctrl + Option + S web-searches your last words"},
    {"id": "infer", "max": 2, "eligible": after(2),
     "text": "Just speak — Mumble shapes email or clean text for you"},

    # ---- Pro Mode / AI key (only while no key is set) ----
    {"id": "pro", "max": 3,
     "eligible": lambda u: _n(u) >= 4 and not (u or {}).get("has_key"),
     "text": "Add an AI key in Settings to unlock Pro Mode"},

    # ---- Smart Modes (each retires the moment that mode is first used) ----
    {"id": "mode_prompt", "max": 3, "eligible": mode_new("prompt", 5),
     "text": "Tap Prompt on the island to craft a polished AI prompt"},
    {"id": "mode_email", "max": 2, "eligible": mode_new("email", 9),
     "text": "Say “email Alex about …” for a ready-to-send draft"},
    {"id": "convert", "max": 2, "eligible": feature_new("convert", 14),
     "text": "Hit Convert on any Deck item to reshape it into another mode"},

    # ---- Foreign (only if the optional toggle is off; once on it's discovered) ----
    {"id": "foreign", "max": 2,
     "eligible": lambda u: _n(u) >= 10 and not (u or {}).get("foreign_toggle"),
     "text": "Enable the Foreign toggle in Settings for non-English terms"},

    # ---- Deck depth (favourites / presets) ----
    {"id": "favourites", "max": 2,
     "eligible": all_of(after(11),
                        lambda u: int((u or {}).get("favorites", 0) or 0) == 0),
     "text": "Star a Deck item to keep it one click away in ★ Favourites"},
    {"id": "presets", "max": 2, "eligible": feature_new("preset_run", 16),
     "text": "Tick Deck items, pick a preset, hit Go — AI runs over them"},

    # ---- Reader (retires once any reading session happens) ----
    {"id": "reader", "max": 2,
     "eligible": lambda u: _n(u) >= 8 and int((u or {}).get("reader_sessions", 0) or 0) == 0,
     "text": "Reader reads any document aloud — paste one in the Reader tab"},

    # ---- Meetings (retires once a meeting is recorded) ----
    {"id": "meeting", "max": 2, "eligible": feature_new("meeting", 13),
     "text": "Meetings records + transcribes a conversation, locally"},

    # ---- Context capture (retires once a conversation is captured) ----
    {"id": "capture", "max": 2, "eligible": feature_new("capture", 15),
     "text": "Capture chat grabs a whole AI conversation as context"},

    # ---- Power-user polish (general, later) ----
    {"id": "vocab", "max": 1, "eligible": after(20),
     "text": "Add your own words in Settings → Vocabulary for cleaner text"},
    {"id": "stats", "max": 1, "eligible": after(18),
     "text": "The Stats tab shows your words, streaks and when you dictate"},
    {"id": "hotkeys", "max": 1, "eligible": after(22),
     "text": "Re-bind any shortcut in Settings → Shortcuts"},
]


# Spacing gates: a tip may appear only when BOTH have elapsed since the last tip.
MIN_GAP_SECONDS = 8 * 60        # ~8 minutes of wall-clock between tips
MIN_DICTATIONS_BETWEEN = 4      # and at least a few dictations apart


def _norm(state):
    raw = state if isinstance(state, dict) else {}
    raw_counts = raw.get("counts")
    if not isinstance(raw_counts, dict):
        raw_counts = {}
    counts = {
        key: max(0, _safe_int(value))
        for key, value in raw_counts.items()
        if isinstance(key, str)
    }
    return {
        "counts": counts,
        "last_ts": _safe_float(raw.get("last_ts") or 0.0),
        "last_id": str(raw.get("last_id") or ""),
        "last_n": max(0, _safe_int(raw.get("last_n") or 0)),
    }


def _as_usage(usage):
    """Accept either the rich usage dict or a bare dictation count (back-compat
    with the old next_tip(state, now, n) callers)."""
    if isinstance(usage, dict):
        return usage
    return {"n": _safe_int(usage or 0)}


def _is_eligible(tip, usage):
    pred = tip.get("eligible")
    if pred is None:
        return True
    try:
        return bool(pred(usage))
    except Exception:
        return False


def all_retired(state, usage=None):
    """True once nothing can ever show again: every tip has either hit its
    appearance cap OR (given the current usage) is no longer relevant. With no
    usage supplied, falls back to the cap-only meaning (back-compat)."""
    st = _norm(state)
    counts = st["counts"]
    if usage is None:
        return all(counts.get(t["id"], 0) >= t["max"] for t in TIPS)
    u = _as_usage(usage)
    return all(counts.get(t["id"], 0) >= t["max"] or not _is_eligible(t, u)
               for t in TIPS)


def next_tip(state, now, usage):
    """Decide whether to surface a tip after a completed dictation.

    `state`  the persisted dict (counts / last_ts / last_id / last_n).
    `now`    a wall clock in seconds (time.time()).
    `usage`  the usage snapshot dict (mumble._tip_usage) — or, for back-compat,
             a bare total-dictation count.

    Returns (tip_or_None, new_state). A tip is returned ONLY when enough time AND
    enough dictations have passed since the last tip, the tip still has
    appearances left, it is RELEVANT to current usage (its `eligible` predicate
    holds — i.e. the feature is unused and the user has enough experience), and it
    isn't the one shown immediately before. The chosen tip's count is incremented
    and the timing recorded so the caller can persist new_state. When nothing is
    eligible, new_state == the normalised input (no spurious writes).
    """
    st = _norm(state)
    u = _as_usage(usage)
    dictation_count = _n(u)
    # Spacing — never spam: both the time gap and the dictation gap must clear.
    if now - st["last_ts"] < MIN_GAP_SECONDS:
        return None, st
    if dictation_count - st["last_n"] < MIN_DICTATIONS_BETWEEN:
        return None, st
    # Eligible = appearances left AND relevant to current usage AND not the
    # immediately-previous tip.
    eligible = [t for t in TIPS
                if st["counts"].get(t["id"], 0) < t["max"]
                and _is_eligible(t, u)
                and t["id"] != st["last_id"]]
    if not eligible:
        # Allow repeating the just-shown tip rather than nothing — but only if it
        # is still both live (cap) and relevant.
        eligible = [t for t in TIPS
                    if st["counts"].get(t["id"], 0) < t["max"]
                    and _is_eligible(t, u)]
    if not eligible:
        return None, st
    # Prefer the LEAST-shown eligible tip so the rotation spreads evenly.
    tip = min(eligible, key=lambda t: st["counts"].get(t["id"], 0))
    st["counts"][tip["id"]] = st["counts"].get(tip["id"], 0) + 1
    st["last_ts"] = float(now)
    st["last_id"] = tip["id"]
    st["last_n"] = int(dictation_count)
    return tip, st
