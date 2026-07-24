#!/usr/bin/env python3
"""LIVE end-to-end test of every AI lane + the Context Island intent lane.

Not part of the offline suites — it needs a real Cerebras key (read from the
local settings.json, never printed) and the network. It calls the EXACT lane
functions the app uses (mirroring mumble._cloud_generate / _generate) with
realistic inputs, and prints the actual AI output so a human can confirm each
mode is proper and correct.

Paced for the Cerebras free tier (5 req/min) with a 429 back-off.

Run: python test_live_modes.py
"""

import json
import os
import sys
import time
import urllib.error

# Force UTF-8 on stdout so unicode (→, em-dash, ✓, Arabic) prints cleanly even
# when stdout is redirected to a file (Windows defaults to cp1252 there).
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import ai
import islamic_terms
import presets

MODEL = "gpt-oss-120b"
URL = ai.CEREBRAS_URL


def load_key():
    p = os.path.join(os.environ["APPDATA"], "Mumble", "settings.json")
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    return (d.get("cerebras_api_key") or "").strip(), d.get("user_name", ""), \
        d.get("prompt_prefs")


def collect(gen):
    """Mirror mumble._collect_text — return the full streamed text."""
    parts = []
    for chunk in gen:
        if chunk.startswith(ai.FULL_MARKER):
            return chunk[len(ai.FULL_MARKER):]
        parts.append(chunk)
    return "".join(parts)


def run(label, fn, clean="clean"):
    """Call a lane with 429 back-off; print the cleaned output."""
    for attempt in range(2):
        try:
            raw = collect(fn())
            out = (ai._extract_final_prompt(raw) if clean == "prompt"
                   else ai._clean(raw) if clean == "clean" else raw)
            print(f"\n{'='*70}\n[{label}]\n{'-'*70}\n{out}\n")
            return out
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                print(f"[{label}] rate-limited (429) — backing off 60s…")
                time.sleep(60)
                continue
            print(f"[{label}] HTTP {e.code}: {e}")
            return None
        except Exception as e:
            print(f"[{label}] ERROR: {type(e).__name__}: {e}")
            return None


def gap(secs=13):
    time.sleep(secs)


def main():
    key, name, prefs = load_key()
    if not key:
        print("No Cerebras key in settings.json — cannot run live test.")
        raise SystemExit(1)
    print(f"Key loaded (len {len(key)}); user_name={name!r}. "
          f"Pacing ~13s between calls for the 5/min limit.\n")

    # 1. TEXT / POLISH (plain dictation — fillers out, punctuation in)
    run("TEXT / polish",
        lambda: ai.cerebras_polish(
            "um so like i need to uh send the quarterly report to the team by "
            "friday and also remind everyone about the standup tomorrow morning",
            key, MODEL, url=URL, aggressiveness="Light"))
    gap()

    # 2. EMAIL
    run("EMAIL",
        lambda: ai.cerebras_email(
            "tell sarah the project is delayed by about a week because of the "
            "vendor and apologise and say we'll have it ready next friday",
            name or "Khalid", key, "", MODEL, url=URL))
    gap()

    # 3. LIST
    run("LIST",
        lambda: ai.cerebras_list(
            "milk eggs bread call the dentist book flights for the trip and "
            "renew the car insurance", key, "", MODEL, url=URL))
    gap()

    # 4. REPLY (context = the message being replied to)
    run("REPLY",
        lambda: ai.cerebras_reply(
            "yes confirm im free and suggest we meet at the italian place "
            "near the office instead",
            name or "Khalid", key,
            "Hey, are we still on for lunch tomorrow at noon? Let me know "
            "where you want to go.", MODEL, url=URL))
    gap()

    # 5. FOREIGN (annotate Islamic terms → AI resolves the slash candidates)
    annotated = islamic_terms.annotate_foreign(
        "I read surah baqarah after fajr prayer and then made dua")
    print(f"[FOREIGN] annotated input → {annotated!r}")
    run("FOREIGN",
        lambda: ai.cerebras_foreign(
            annotated, name or "Khalid", key, "", MODEL, url=URL))
    gap()

    # 6. PROMPT (the heavy constitution lane)
    run("PROMPT",
        lambda: ai.cerebras_prompt(
            "build a todo app with react that syncs across devices",
            key, "", MODEL, url=URL, prefs=prefs),
        clean="prompt")
    gap()

    # 7. CONTEXT INTENT — Summarise
    material = ai.format_context_items([
        {"source": "clipboard", "time": "09:14",
         "text": "The board approved the Q3 budget. Marketing gets a 12% "
                 "increase, engineering hiring is frozen until October, and "
                 "the new office lease was deferred to next year. Travel "
                 "budgets are cut 30%."},
    ])
    summ = next((p for p in presets.BUILTIN if p[0] == "Summarise"))
    run("CONTEXT · Summarise",
        lambda: ai.cerebras_intent(summ[2], material, "", key, MODEL, url=URL))
    gap()

    # 8. CONTEXT INTENT — the NEW 'Chat context' preset (rebuild prompts+replies)
    chat_material = ai.format_context_items([
        {"source": "clipboard", "time": "10:01",
         "text": "How do I center a div in CSS?"},
        {"source": "clipboard", "time": "10:01",
         "text": "Use flexbox on the parent: display:flex; justify-content:"
                 "center; align-items:center. That centers the child both ways."},
        {"source": "clipboard", "time": "10:03",
         "text": "And how do I make it responsive so it stacks on mobile?"},
    ])
    chat = next((p for p in presets.BUILTIN if p[0] == "Chat context"))
    run("CONTEXT · Chat context (NEW preset)",
        lambda: ai.cerebras_intent(chat[2], chat_material, "", key, MODEL,
                                   url=URL))

    print("\nDONE — review each block above for correctness.")


if __name__ == "__main__":
    main()
