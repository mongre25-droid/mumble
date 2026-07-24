#!/usr/bin/env python3
"""Run each Mumble Smart Mode with test text to capture REAL outputs for the website."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "mumble"))

settings_path = os.path.join(os.environ.get("APPDATA", ""), "Mumble", "settings.json")
with open(settings_path, "r") as f:
    settings = json.load(f)

API_KEY = settings.get("cerebras_api_key", "")
MODEL = settings.get("cerebras_model", "gpt-oss-120b")
USER_NAME = settings.get("user_name", "")

import ai
from islamic_terms import annotate_foreign

CEREBRAS_URL = ai.CEREBRAS_URL
FULL = ai.FULL_MARKER

def collect(gen):
    """Drain a streaming generator into full text."""
    parts = []
    for chunk in gen:
        if chunk.startswith(FULL):
            return chunk[len(FULL):]
        if chunk.startswith(ai.TRUNC_MARKER):
            return chunk[len(ai.TRUNC_MARKER):] + " [TRUNCATED]"
        parts.append(chunk)
    return "".join(parts)

test_text_email = "tell sarah i'll be ten minutes late to the call"
test_text_list = "we need milk eggs bread and some coffee for the office"
test_text_reply = "yeah that time works and i'll bring the slides"
reply_context = "Can you do the 2pm review?"
test_text_foreign = "send my salaam to khaled and say jazakallah for the help"
test_text_prompt = "i want a python script that renames my photos by the date they were taken"
test_text_polish = "um so hey can you let the team know the launch got pushed to friday and uh we'll do a quick sync at ten"

results = {}

print("=" * 60)
print("TESTING ALL SMART MODES - REAL CEREBRAS OUTPUTS")
print("=" * 60)

print("\n--- TEXT (Polish) ---")
try:
    gen = ai.cerebras_polish(test_text_polish, API_KEY, model=MODEL, url=CEREBRAS_URL)
    out = collect(gen)
    results["text"] = out
    print(f"INPUT: {test_text_polish}")
    print(f"OUTPUT: {out}")
except Exception as e:
    print(f"ERROR: {e}")
    results["text"] = f"[error: {e}]"

print("\n--- EMAIL ---")
try:
    gen = ai.cerebras_email(test_text_email, USER_NAME, API_KEY, model=MODEL, url=CEREBRAS_URL)
    out = collect(gen)
    results["email"] = out
    print(f"INPUT: {test_text_email}")
    print(f"OUTPUT: {out}")
except Exception as e:
    print(f"ERROR: {e}")
    results["email"] = f"[error: {e}]"

print("\n--- LIST ---")
try:
    gen = ai.cerebras_list(test_text_list, API_KEY, model=MODEL, url=CEREBRAS_URL)
    out = collect(gen)
    results["list"] = out
    print(f"INPUT: {test_text_list}")
    print(f"OUTPUT: {out}")
except Exception as e:
    print(f"ERROR: {e}")
    results["list"] = f"[error: {e}]"

print("\n--- REPLY ---")
try:
    gen = ai.cerebras_reply(test_text_reply, USER_NAME, API_KEY, context=reply_context, model=MODEL, url=CEREBRAS_URL)
    out = collect(gen)
    results["reply"] = out
    print(f"INPUT: {test_text_reply}")
    print(f"CONTEXT: {reply_context}")
    print(f"OUTPUT: {out}")
except Exception as e:
    print(f"ERROR: {e}")
    results["reply"] = f"[error: {e}]"

print("\n--- FOREIGN ---")
try:
    annotated = annotate_foreign(test_text_foreign)
    gen = ai.cerebras_foreign(annotated, USER_NAME, API_KEY, model=MODEL, url=CEREBRAS_URL)
    out = collect(gen)
    results["foreign"] = out
    print(f"INPUT: {test_text_foreign}")
    print(f"ANNOTATED: {annotated}")
    print(f"OUTPUT: {out}")
except Exception as e:
    print(f"ERROR: {e}")
    results["foreign"] = f"[error: {e}]"

print("\n--- PROMPT ---")
try:
    prefs = settings.get("prompt_prefs", {})
    gen = ai.cerebras_prompt(test_text_prompt, prefs, API_KEY, model=MODEL, url=CEREBRAS_URL)
    out = collect(gen)
    results["prompt"] = out
    print(f"INPUT: {test_text_prompt}")
    print(f"OUTPUT: {out}")
except Exception as e:
    print(f"ERROR: {e}")
    results["prompt"] = f"[error: {e}]"

print("\n" + "=" * 60)
out_path = os.path.join(os.path.dirname(__file__), "website_mode_outputs.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print(f"Saved to {out_path}")
