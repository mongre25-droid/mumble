#!/usr/bin/env python3
"""LIVE audit of the Context Island intent lane — EVERY built-in preset, the
intent × mode composition, and the no-preset default job, against the real key.

Mirrors mumble._generate's context branch exactly: the preset instruction
(+ any MODE_DIRECTIVES output form) is the SYSTEM message via ai.cerebras_intent;
the material is a labelled block; spoken words are the user content. Each case
uses a purpose-built input and an automatic sanity check; the full outputs are
printed for human review.

Paced for the free tier (5 req/min); 429s honour Retry-After. Takes ~6-8 min.
Run: python test_live_context.py
"""

import json
import os
import re
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import ai
import presets

GAP = 16  # seconds between calls — under 4 req/min


def load_key():
    p = os.path.join(os.environ["APPDATA"], "Mumble", "settings.json")
    with open(p, encoding="utf-8") as f:
        return (json.load(f).get("cerebras_api_key") or "").strip()


def collect(gen):
    parts = []
    for chunk in gen:
        if chunk.startswith(ai.FULL_MARKER):
            return chunk[len(ai.FULL_MARKER):]
        parts.append(chunk)
    return "".join(parts)


# ---- fixtures -----------------------------------------------------------------
MEMO = ("Team update: the board approved the Q3 budget on Tuesday. Marketing "
        "gets a 12% increase, engineering hiring is frozen until October, and "
        "the office lease renewal is deferred to next year. Travel budgets are "
        "cut 30%. Sarah Chen owns the migration plan and must deliver it by "
        "July 18. The vendor quote came in at £3,400 per month. Please flag "
        "any blockers to Omar before Friday's review; the next all-hands is "
        "on August 2 and attendance is mandatory for team leads.")
QUESTION = ("Quick question - if we buy 3 licences at $40 each and there's a "
            "$15 setup fee, what's the total cost for the first month?")
MESSAGE = ("Hey, are you joining the workshop on Thursday afternoon? We need "
           "a headcount by tomorrow and also let me know if you need the "
           "projector or just a whiteboard.")
STORY = ("The library closed at nine, but Maryam stayed in the archive room, "
         "tracing the ledger's faded entries with one finger. Each name had a "
         "date beside it except the last one, which had been")
TYPOS = ("i recieve alot of emails every day and its definately hard to keep "
         "track, their are atleast fifty unred ones since yesturday")
AI_SLOP = ("Moreover, it is important to note that effective time management "
           "is crucial. In conclusion, by leveraging these strategies, "
           "individuals can unlock unprecedented productivity gains and "
           "furthermore achieve a holistic work-life balance.")
CASUAL = ("gonna be late tmrw cos the car's in the shop, can u cover the 9am "
          "standup for me? ill owe u one")
ITEM_A = ("Draft agenda: 1) budget recap 2) migration timeline 3) hiring "
          "freeze questions.")
ITEM_B = ("Extra agenda idea - we should also discuss the vendor quote and "
          "whether £3,400/month is negotiable.")
CHAT = [("clipboard", "10:01", "How do I make my Python script run at startup "
         "on Windows?"),
        ("clipboard", "10:02", "Create a shortcut to the script in the "
         "shell:startup folder, or register a Run key in the registry under "
         "HKCU. The startup folder is simpler and doesn't need admin."),
        ("clipboard", "10:04", "And what if I need it to run as admin?")]


def block(*texts):
    return ai.format_context_items(
        [{"source": "clipboard", "time": "09:0%d" % i, "text": t}
         for i, t in enumerate(texts)])


def chat_block():
    return ai.format_context_items(
        [{"source": s, "time": t, "text": x} for s, t, x in CHAT])


P = {t: ins for t, _, ins in presets.BUILTIN}

# (name, instruction, material_block, spoken, check_fn, check_desc)
CASES = [
    ("Answer it", P["Answer it"], block(QUESTION), "",
     lambda o: "135" in o.replace(",", ""),
     "computes 3×40+15 = $135"),
    ("Summarise", P["Summarise"], block(MEMO), "",
     lambda o: len(o) < len(MEMO) and "12%" in o,
     "shorter than input + keeps the 12% figure"),
    ("Reply", P["Reply"], block(MESSAGE),
     "yes im coming and i just need a whiteboard",
     lambda o: len(o) > 10 and "headcount" not in o[:12].lower(),
     "writes an actual reply"),
    ("Continue", P["Continue"], block(STORY), "",
     lambda o: "library closed at nine" not in o.lower() and len(o) > 40,
     "continues without re-telling the opening"),
    ("Improve", P["Improve"], block(TYPOS), "",
     lambda o: "recieve" not in o.lower() and "definately" not in o.lower(),
     "cleans the broken text"),
    ("Fix errors", P["Fix errors"], block(TYPOS), "",
     lambda o: "receive" in o.lower() and "unread" in o.lower()
     and abs(len(o) - len(TYPOS)) < len(TYPOS) * 0.5,
     "fixes spelling, keeps the wording/length"),
    ("Make shorter", P["Make shorter"], block(MEMO), "",
     lambda o: len(o) <= len(MEMO) * 0.65,
     "≤65% of the original length"),
    ("Expand", P["Expand"], block(ITEM_A), "",
     lambda o: len(o) >= len(ITEM_A) * 1.3,
     "meaningfully longer"),
    ("Explain simply", P["Explain simply"],
     block("Prompt caching lets an LLM provider reuse the key-value tensors "
           "computed for a byte-identical prefix of the context window, "
           "amortising prefill cost across requests."), "",
     lambda o: len(o) > 120 and "amortis" not in o.lower()
     and ("like" in o.lower() or "think of" in o.lower()
          or "imagine" in o.lower()),
     "plain-language explainer with an analogy (quoting the term it "
     "explains is fine)"),
    ("Key points", P["Key points"], block(MEMO), "",
     lambda o: o.count("- ") + o.count("• ") + o.count("* ") >= 3,
     "a real bullet list"),
    ("Action items", P["Action items"], block(MEMO), "",
     lambda o: re.search(r"(?m)^\s*1[.)]", o)
     and ("July 18" in o or "18/07" in o or "18 July" in o),
     "numbered checklist incl. the July 18 deadline (any date format)"),
    ("Humanise", P["Humanise"], block(AI_SLOP), "",
     lambda o: "moreover" not in o.lower() and "in conclusion" not in o.lower()
     and "unlock" not in o.lower(),
     "kills the AI-isms"),
    ("Merge", P["Merge"], block(ITEM_A, ITEM_B), "",
     lambda o: "vendor" in o.lower() and "migration" in o.lower(),
     "one piece containing both items' content"),
    ("Extract facts", P["Extract facts"], block(MEMO), "",
     lambda o: "3,400" in o.replace("£", "")
     and ("July 18" in o or "18/07" in o or "18 July" in o)
     and "Sarah Chen" in o
     and "09:0" not in o,  # the [CLIPBOARD — 09:00] LABEL must not leak as a fact
     "pulls the number, the date (any format), the name; no metadata leak"),
    ("Make formal", P["Make formal"], block(CASUAL), "",
     lambda o: "gonna" not in o.lower()
     and not re.search(r"\bu\b", o.lower()),  # standalone 'u', not 'you cover'
     "professional register"),
    ("Critique", P["Critique"], block(ITEM_A), "",
     lambda o: len(o) > 80,
     "substantive critique (human-review the quality)"),
    ("Chat context", P["Chat context"], chat_block(), "",
     lambda o: "User:" in o and "Assistant:" in o
     and "run as admin" in o and "?" in o.split("User:")[-1],
     "labelled User/Assistant transcript, ends on the open question"),
    ("Summarise × Email mode",
     P["Summarise"] + "\n\nOUTPUT FORM:\n" + presets.MODE_DIRECTIVES["email"],
     block(MEMO), "",
     lambda o: any(g in o for g in ("Hi", "Dear", "Hello", "Team"))
     and any(s in o for s in ("Regards", "Best", "Thanks", "regards")),
     "summary SHAPED as a ready-to-send email"),
    ("No preset + spoken instruction",
     "Execute the user's spoken instruction on the material. If there is no "
     "spoken instruction, produce a clean, faithful, well-organised rendering "
     "of the material's content — never just echo it verbatim.",
     block(MEMO), "turn this into exactly three questions to ask at the review",
     lambda o: o.count("?") >= 3 and len(o) < len(MEMO) * 1.5,
     "obeys the spoken instruction (3 questions)"),
]


def run_case(name, instruction, material, spoken, key):
    for attempt in (1, 2, 3):
        try:
            t0 = time.time()
            out = ai._clean(collect(ai.cerebras_intent(
                instruction, material, spoken, key, "gpt-oss-120b")))
            return out, time.time() - t0
        except RuntimeError as e:
            msg = str(e)
            if "429" in msg and attempt < 3:
                wait = 30.0
                m = re.search(r"RETRY_AFTER=([\d.]+)", msg)
                if m:
                    wait = min(float(m.group(1)) + 1.0, 65.0)
                print(f"  [{name}] 429 — waiting {wait:.0f}s")
                time.sleep(wait)
                continue
            return f"<ERROR: {msg}>", 0.0
        except Exception as e:
            return f"<ERROR: {type(e).__name__}: {e}>", 0.0


def main():
    key = load_key()
    if not key:
        print("No Cerebras key — cannot run.")
        raise SystemExit(1)
    # Optional subset: pass case-name substrings as argv to re-run only those
    # (e.g. after a provider outage killed specific cases).
    wanted = [a.lower() for a in sys.argv[1:]]
    cases = [c for c in CASES
             if not wanted or any(w in c[0].lower() for w in wanted)]
    results = []
    for i, (name, ins, mat, spoken, check, desc) in enumerate(cases):
        out, dt = run_case(name, ins, mat, spoken, key)
        try:
            ok = bool(out and not out.startswith("<ERROR") and check(out))
        except Exception:
            ok = False
        results.append((name, ok, desc))
        print(f"\n{'='*72}\n[{i+1:02d}/{len(cases)}] {name}  "
              f"({dt:.1f}s)  -> {'PASS' if ok else 'CHECK-FAILED'}"
              f"  [{desc}]\n{'-'*72}\n{out}\n", flush=True)
        if i < len(cases) - 1:
            time.sleep(GAP)
    print("=" * 72)
    npass = sum(1 for _, ok, _ in results if ok)
    print(f"\nSUMMARY: {npass}/{len(results)} automatic checks passed")
    for name, ok, desc in results:
        print(f"  {'PASS ' if ok else 'FAIL?'} {name} — {desc}")


if __name__ == "__main__":
    main()
