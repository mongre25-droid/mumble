#!/usr/bin/env python3
"""Tests for mode_select.process_transcript — the three spec examples plus edge cases.
Run:  python test_mode_select.py   (exit 0 = all pass)"""

import sys

from mode_select import process_transcript, extract_mode

fails = []


def check(name, cond):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {name}")
    if not cond:
        fails.append(name)


def W(t, v):
    return {"time": t, "type": "word", "value": v}


def C(t):
    return {"time": t, "type": "control"}


def B(t):
    return {"time": t, "type": "button"}


print("== Example 1: Control + two buttons bracket 'edit mode' ==")
logs, modes = process_transcript([
    C("2024-06-07T12:00:01Z"),
    B("2024-06-07T12:00:02Z"),
    W("2024-06-07T12:00:03Z", "edit"),
    W("2024-06-07T12:00:04Z", "mode"),
    B("2024-06-07T12:00:05Z"),
])
check("one mode extracted", len(modes) == 1)
check("ModeName = 'edit mode'", modes and modes[0]["name"] == "edit mode")
check("start = first button", modes and modes[0]["start"] == "2024-06-07T12:00:02Z")
check("end = second button", modes and modes[0]["end"] == "2024-06-07T12:00:05Z")
keys = [log["key"] for log in logs]
check("ControlPressTimestamp logged", "ControlPressTimestamp" in keys)
check("ModeName logged", "ModeName" in keys)
check("Mode start+end logged",
      "ModeStartTimestamp" in keys and "ModeEndTimestamp" in keys)
cpt = next(log for log in logs if log["key"] == "ControlPressTimestamp")
check("ControlPressTimestamp uses control time", cpt["value"] == "2024-06-07T12:00:01Z")

print("\n== Example 2: ButtonPressed with no prior Control -> nothing ==")
logs, modes = process_transcript([B("2024-06-07T12:10:00Z")])
check("no modes", modes == [])
check("no logs", logs == [])

print("\n== Example 3: trailing stray word after second button is ignored ==")
logs, modes = process_transcript([
    C("2024-06-07T12:20:00Z"),
    B("2024-06-07T12:20:01Z"),
    W("2024-06-07T12:20:02Z", "test"),
    B("2024-06-07T12:20:03Z"),
    W("2024-06-07T12:20:04Z", "extraWord"),
])
check("ModeName = 'test'", modes and modes[0]["name"] == "test")
check("trailing word excluded", modes and "extraWord" not in modes[0]["name"])
check("exactly one mode", len(modes) == 1)

print("\n== Tolerance: one stray word BEFORE the first button is ignored ==")
logs, modes = process_transcript([
    C("t0"), W("t0a", "umm"), B("t1"), W("t2", "email"), B("t3"),
])
check("stray-before ignored, mode = 'email'", modes and modes[0]["name"] == "email")

print("\n== Reset: two full cycles in one stream ==")
logs, modes = process_transcript([
    C("a0"), B("a1"), W("a2", "prompt"), B("a3"),
    C("b0"), B("b1"), W("b2", "list"), B("b3"),
])
check("two modes", len(modes) == 2)
check("modes are prompt, list",
      modes and modes[0]["name"] == "prompt" and modes[1]["name"] == "list")

print("\n== No mode change before Control (suppression) ==")
logs, modes = process_transcript([
    W("x0", "hello"), B("x1"), W("x2", "world"), B("x3"),
])
check("no mode without Control", modes == [])

print("\n== Mumble adapter: extract_mode from a window + word timestamps ==")
words = [
    {"word": "prompt", "start": 0.10, "end": 0.55},
    {"word": "build", "start": 1.20, "end": 1.70},   # well after release -> excluded
]
windows = [(0.0, 0.58)]  # key held 0.0-0.58s
mode_words, mlogs = extract_mode(words, windows, rec_start_epoch=1_700_000_000.0)
check("adapter extracts 'prompt'", mode_words == ["prompt"])
check("adapter excludes post-release word", "build" not in mode_words)
check("adapter logged a ControlPressTimestamp",
      any(log["key"] == "ControlPressTimestamp" for log in mlogs))

print("\n" + ("ALL PASS" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
