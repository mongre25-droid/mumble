#!/usr/bin/env python3
"""Mode-selection state machine over a timestamped token stream.

Formalizes Mumble's "hold the mode key and say the keyword" behavior as a small,
deterministic, unit-testable machine (see test_mode_select.py):

  * Nothing happens until a ControlPressed gate token is seen — and every
    ControlPressed immediately emits a `ControlPressTimestamp` log entry.
  * After a ControlPressed, the next TWO `ButtonPressed` tokens delimit the spoken
    mode: the mode string is the word tokens occurring STRICTLY BETWEEN them.
  * A stray word immediately before the first ButtonPressed or after the second is
    outside that range, so it is naturally ignored (the one-word tolerance rule).
  * On the second ButtonPressed it records ModeName / ModeStartTimestamp /
    ModeEndTimestamp log entries and RESETS to await the next ControlPressed.

Token schema (matches the rest of the transcript):
  {"time": "<ISO-8601>", "type": "control"|"button"|"word"|"log", "value": "..."}
Events may also be given as {"type":"event","value":"ControlPressed"/"ButtonPressed"}.

The machine never mutates input tokens — it only RETURNS new log entries plus the
extracted modes, so callers can append them without touching existing data.
"""

import datetime
import math


def _classify(tok):
    """-> (kind, value, time). kind in control|button|word|other."""
    if not isinstance(tok, dict):
        return "other", "", ""
    t = str(tok.get("type") or "").lower()
    val = tok.get("value", "")
    time = tok.get("time") or ""
    if t in ("control", "button", "word", "log"):
        return t, val, time
    # value-encoded events ("ControlPressed" / "ButtonPressed")
    v = str(val)
    if v == "ControlPressed":
        return "control", "", time
    if v == "ButtonPressed":
        return "button", "", time
    return "word", val, time


def _log(key, value, time):
    return {"time": time, "type": "log", "key": key, "value": value}


def process_transcript(tokens):
    """Run the state machine over `tokens`.

    Returns (log_entries, modes):
      log_entries — token-schema dicts to append: a ControlPressTimestamp per
                    ControlPressed, then ModeName/ModeStartTimestamp/ModeEndTimestamp
                    for each completed mode.
      modes       — [{"name": str, "start": <time>, "end": <time>}, ...].

    Mode changes occur ONLY after a ControlPressed and ONLY when two ButtonPressed
    tokens bracket the words; anything else leaves state untouched.
    """
    logs, modes = [], []
    armed = False  # a ControlPressed has gated us
    first_btn = None  # time of the first ButtonPressed after arming
    between = []  # word values strictly between the two ButtonPressed

    for tok in tokens or []:
        kind, val, time = _classify(tok)

        if kind == "control":
            logs.append(_log("ControlPressTimestamp", time, time))
            armed, first_btn, between = True, None, []
            continue

        if not armed:
            continue  # suppress all mode logic until a ControlPressed

        if kind == "button":
            if first_btn is None:
                first_btn, between = time, []  # open the mode window
            else:
                name = " ".join(w for w in between if w).strip()
                # A tap/click with no spoken token is not a mode selection and
                # must not create a blank ModeName entry in persisted logs.
                if name:
                    modes.append({"name": name, "start": first_btn, "end": time})
                    logs.append(_log("ModeName", name, time))
                    logs.append(_log("ModeStartTimestamp", first_btn, first_btn))
                    logs.append(_log("ModeEndTimestamp", time, time))
                armed, first_btn, between = False, None, []  # reset for next Control
        elif kind == "word":
            if first_btn is not None:  # strictly between the buttons
                value = str(val or "").strip()
                if value:
                    between.append(value)
            # words before the first button (stray-before) are ignored by design

    return logs, modes


# --------------------------- Mumble integration --------------------------------
# Adapter: turn Mumble's per-utterance mode-key window(s) + word timestamps into the
# token stream above, so the live app drives the SAME machine the tests cover.


def _iso(epoch_secs):
    try:
        return (
            datetime.datetime.fromtimestamp(
                epoch_secs, tz=datetime.timezone.utc
            ).strftime(
                "%Y-%m-%dT%H:%M:%S.%f"
            )[:-3]
            + "Z"
        )
    except Exception:
        return ""


def tokens_from_windows(words, windows, rec_start_epoch, slop=0.08):
    """Build a spec token stream from Mumble's mode-key windows.

    Each window (key down→up) becomes: ControlPressed + ButtonPressed at the down
    edge, the word tokens whose timing falls inside the window, then a ButtonPressed
    at the up edge. `words` = [{'word','start','end'}], window times are seconds
    relative to `rec_start_epoch`.
    """
    toks = []
    try:
        rec_start_epoch = float(rec_start_epoch)
        slop = max(0.0, float(slop))
    except (TypeError, ValueError, OverflowError):
        return toks
    if not math.isfinite(rec_start_epoch) or not math.isfinite(slop):
        return toks
    for win in windows or []:
        try:
            a = float(win[0]) if win and win[0] is not None else 0.0
        except (IndexError, TypeError, ValueError):
            continue
        try:
            b = float(win[1]) if (len(win) > 1 and win[1] is not None) else a
        except (TypeError, ValueError):
            b = a
        if not math.isfinite(a) or not math.isfinite(b):
            continue
        if b < a:
            a, b = b, a
        a_iso, b_iso = _iso(rec_start_epoch + a), _iso(rec_start_epoch + b)
        toks.append({"time": a_iso, "type": "control"})
        toks.append({"time": a_iso, "type": "button"})
        for w in words or []:
            if not isinstance(w, dict):
                continue
            try:
                ws = float(w.get("start", 0.0))
                we = float(w.get("end", 0.0))
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(ws) or not math.isfinite(we):
                continue
            if we < ws:
                ws, we = we, ws
            if we >= a - slop and ws <= b + slop:
                toks.append(
                    {
                        "time": _iso(rec_start_epoch + ws),
                        "type": "word",
                        "value": (w.get("word") or "").strip(),
                    }
                )
        toks.append({"time": b_iso, "type": "button"})
    return toks


def extract_mode(words, windows, rec_start_epoch):
    """Live helper: returns (mode_words, log_entries).

    mode_words — the words spoken inside the button window(s), spec-extracted, ready
                 to feed Mumble's keyword matcher. Empty if nothing qualifies.
    log_entries — the ControlPressTimestamp / ModeName / Mode*Timestamp tokens.
    """
    toks = tokens_from_windows(words, windows, rec_start_epoch)
    logs, modes = process_transcript(toks)
    mode_words = []
    for m in modes:
        if m.get("name"):
            mode_words.extend(m["name"].split())
    return mode_words, logs
