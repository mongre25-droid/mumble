#!/usr/bin/env python3
"""Send 5 simulated ~1-minute transcripts to the Mumble AI and capture responses."""

import json
import os
import sys
import time

import ai

# --- Load API key from settings ---
_settings_path = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "Mumble", "settings.json"
)
_settings = json.load(open(_settings_path))
KEY = (_settings.get("cerebras_api_key", "") or "").strip()
MODEL = _settings.get("cerebras_model", "gpt-oss-120b")
NAME = _settings.get("user_name", "User")

if not KEY:
    print("No API key found in settings. Exiting.")
    sys.exit(1)

# --- 5 test transcripts (~1 min of speech each) ---
TESTS = [
    # 1) PLAIN TEXT — casual rambling about their morning
    {
        "mode": "polish",
        "label": "PLAIN TEXT — morning ramble",
        "raw": (
            "so I woke up today around like 7 30 which is actually pretty early for me "
            "usually I sleep in until like 8 or 8 30 but the sun was just coming through "
            "the window and I couldn't really get back to sleep so I got up and made some "
            "coffee I've been trying this new brand from the grocery store it's supposed to "
            "be like ethiopian single origin or something I don't know if I can really taste "
            "the difference but it's pretty good anyway then I went for a walk around the "
            "neighborhood which was nice there's this one house that always has really cool "
            "flowers in the front yard I think they're dahlias or maybe marigolds I'm not "
            "really sure about flowers but they look nice and then I came back and started "
            "working on some code I've been building this app that takes voice input and "
            "converts it to text it's called mumble and it's actually coming along pretty "
            "well I fixed a bunch of bugs yesterday and now I'm working on the auto update "
            "feature which is kind of tricky because you need to be able to swap out the "
            "running installation without breaking anything and anyway that's basically my "
            "morning so far I should probably eat breakfast at some point"
        ),
    },
    # 2) PROMPT — asking for a prompt to build a REST API
    {
        "mode": "prompt",
        "label": "PROMPT — build a REST API",
        "raw": (
            "prompt I need you to build me a prompt that I can use to generate a rest api "
            "using python and fast api it should handle user authentication with jwt tokens "
            "and it should have endpoints for creating reading updating and deleting posts "
            "and also it needs to support file uploads so that users can attach images to "
            "their posts and I want the database to use sqlalchemy with postgresql and I "
            "want it to be production ready so include error handling and logging and also "
            "make sure the endpoints are properly documented with automatic schema generation "
            "and I want pagination on the list endpoints and also rate limiting to prevent "
            "abuse and the authentication should support both email password and also google "
            "oauth sign in and the whole thing should be containerized with docker and have "
            "a docker compose file that sets up the app plus postgres plus redis for caching"
        ),
    },
    # 3) EMAIL — dictating a professional email to a client
    {
        "mode": "email",
        "label": "EMAIL — project update to client",
        "raw": (
            "email hi sarah this is regarding the website redesign project we discussed "
            "last week on the call I wanted to give you an update on where we stand so the "
            "design phase is now complete and we've gotten sign off from your team on all "
            "the mock ups which is great the front end development is about seventy percent "
            "done and we're on track to have the first staging build ready by next friday "
            "the back end integration with your existing crm system is taking a bit longer "
            "than expected because the api documentation your team provided has some "
            "discrepancies with the actual endpoints so we've been working closely with "
            "mark from your engineering team to resolve those issues and we expect to have "
            "that sorted out by early next week I also wanted to flag that the analytics "
            "dashboard feature you requested isn't included in the original scope so we'd "
            "need to discuss either adding it as a change order or deferring it to phase "
            "two let me know what you prefer and I'll send over a revised timeline "
            "accordingly looking forward to hearing from you"
        ),
    },
    # 4) LIST — grocery shopping list
    {
        "mode": "list",
        "label": "LIST — weekly meal prep groceries",
        "raw": (
            "list I need to make a grocery list for this week's meal prep so I'm planning "
            "to make chicken stir fry on monday so I need chicken breast bell peppers in "
            "three colors red yellow and orange also broccoli and soy sauce and sesame oil "
            "and then on tuesday I want to make pasta so I need penne pasta marinara sauce "
            "and ground beef and parmesan cheese and also garlic and onions which I should "
            "probably just buy a big bag of onions and a whole head of garlic because I use "
            "them every day and then wednesday I'm thinking tacos so I need taco shells "
            "or tortillas and ground turkey and lettuce and tomatoes and sour cream and "
            "cheddar cheese and salsa and avocados if they're ripe otherwise skip them and "
            "thursday I want to make a big salad so I need mixed greens cucumbers cherry "
            "tomatoes feta cheese olives and a lemon for dressing and friday is pizza night "
            "so I need pizza dough mozzarella pepperoni mushrooms and olives again also I "
            "need basic stuff like milk eggs bread and butter and coffee because I'm almost "
            "out of everything"
        ),
    },
    # 5) REPLY — replying to a message about a meeting
    {
        "mode": "reply",
        "label": "REPLY — meeting reschedule response",
        "raw": (
            "reply hey david thanks for letting me know about the meeting getting pushed "
            "to thursday that actually works better for me because I have a dentist "
            "appointment on wednesday afternoon that I completely forgot about when we "
            "originally scheduled this so thursday at 2 pm is perfect I'll make sure to "
            "have the quarterly report ready by then I'm still waiting on the sales "
            "figures from the western region team but jennifer said she'd have those to "
            "me by end of day tuesday so that should give me plenty of time to compile "
            "everything and put together the presentation also should I include the updated "
            "projections for next quarter or just focus on the actuals from this quarter "
            "let me know and one more thing is the meeting going to be in the conference "
            "room on the third floor or are we doing it over zoom because if it's zoom I "
            "can join from home which would be easier for me that day thanks again for "
            "organizing this"
        ),
    },
]


def drain(gen, label):
    """Drain a streaming generator and return the full text."""
    chunks = []
    chunk_count = 0
    for chunk in gen:
        chunk_count += 1
        if chunk.startswith(ai.FULL_MARKER):
            full_text = chunk[len(ai.FULL_MARKER):]
            print(f"  [drain] {chunk_count} chunks, FULL_MARKER at end, full_text len={len(full_text)}")
            return full_text
        chunks.append(chunk)
    result = "".join(chunks)
    print(f"  [drain] {chunk_count} chunks, no FULL_MARKER, result len={len(result)}")
    return result


def run_test(i, t):
    print(f"\n{'='*60}")
    print(f"TEST {i+1}/5: {t['label']}")
    print(f"{'='*60}")
    raw = t["raw"]
    mode = t["mode"]

    if mode == "polish":
        gen = ai.cerebras_polish(raw, KEY, MODEL, second_opinion=False, aggressiveness="Light")
        out = drain(gen, mode)
        out = ai._clean(out)
    elif mode == "prompt":
        # cerebras_prompt is streaming
        gen = ai.cerebras_prompt(raw, KEY, context="", model=MODEL)
        out = drain(gen, mode)
        out = ai._clean(out)
    elif mode == "email":
        gen = ai.cerebras_email(raw, NAME, KEY, context="", model=MODEL)
        out = drain(gen, mode)
        out = ai._clean(out)
    elif mode == "list":
        gen = ai.cerebras_list(raw, KEY, context="", model=MODEL)
        out = drain(gen, mode)
        out = ai._clean(out)
    elif mode == "reply":
        gen = ai.cerebras_reply(raw, NAME, KEY, context="", model=MODEL)
        out = drain(gen, mode)
        out = ai._clean(out)

    print(f"\n--- RAW INPUT ({len(raw)} chars) ---")
    print(raw[:200] + "..." if len(raw) > 200 else raw)
    print(f"\n--- AI OUTPUT ({len(out)} chars) ---")
    print(out)
    return out


if __name__ == "__main__":
    results = []
    for i, t in enumerate(TESTS):
        try:
            out = run_test(i, t)
            results.append((t["label"], "OK", len(out)))
        except Exception as e:
            print(f"\n  ERROR: {e}")
            results.append((t["label"], f"FAIL: {e}", 0))
        time.sleep(5)  # generous pacing — Cerebras can be flaky with rapid high-reasoning calls

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for label, status, length in results:
        print(f"  {status:5s} | {length:5d} chars | {label}")
