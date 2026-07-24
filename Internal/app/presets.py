"""Context Island intent presets.

Built-in presets (slots 1..N) plus up to 5 user-created custom presets
(the slots right after the built-ins, stored in presets.json in the data
folder). Each preset is a triple:

  (title, description, instruction)

  • title       — the short label on the button.
  • description — a one-line, user-facing explanation of what the preset
                  DOES, shown under the title in the Context Island so the
                  user can tell the presets apart at a glance.
  • instruction — the SYSTEM directive sent to the AI. It is never
                  transcribed and never appears in the pasted output.
"""

import json
import os

import branding

PRESETS_PATH = os.path.join(branding.DATA_DIR, "presets.json")

# Built-in presets. Redesigned 2026-06-10 around what people actually DO with
# collected material in a dictation tool: act on it (answer/reply/continue),
# transform it (summarise/shorter/expand/simplify), repair it (improve/fix/
# humanise), mine it (key points/actions/facts/merge/critique), or rebuild a
# prior AI conversation from copied turns (chat context).
BUILTIN = [
    # --- High-priority presets first (owner): the four people reach for most. ---
    ("Chat context",
     "Rebuilds copied turns into a chat",
     "The selected items are turns copied out of an AI chat — some are "
     "PROMPTS the user sent, some are the AI's REPLIES. Reconstruct them into "
     "ONE clean, chronologically-ordered conversation transcript that a fresh "
     "AI could read to understand the full prior exchange. Label every turn "
     "clearly ('User:' for the user's prompts, 'Assistant:' for the AI's "
     "replies), inferring which is which from the wording and content. "
     "Preserve the substance of each turn faithfully — do NOT summarise, "
     "answer, or continue the conversation, and never invent turns that "
     "aren't in the material. If the user's spoken instruction adds a closing "
     "turn or asks you to frame the transcript a particular way, honour it. "
     "Output only the assembled chat, ready to paste as context for a new "
     "prompt."),
    ("Focus",
     "Cuts to what actually matters",
     "Strip the material down to what actually matters. Identify the single "
     "core point, decision, or ask and lead with it, then keep only what "
     "directly supports it. Remove tangents, throat-clearing, caveats, and "
     "nice-to-knows. The reader should finish knowing exactly the one thing to "
     "focus on and why."),
    ("Deep Thinking",
     "Reasons through it thoroughly",
     "Think the material through deeply and show your reasoning. Surface the "
     "key questions it raises, weigh the trade-offs and implications, name the "
     "assumptions and risks, and reach a clear, well-justified conclusion. Go "
     "beyond restating the material — add genuine analytical insight, but stay "
     "grounded in what is actually there; never invent facts."),
    ("Summarise",
     "Condenses it to the key points",
     "Summarise the material concisely. Lead with the single most important "
     "point, then the rest in priority order. Keep every load-bearing fact, "
     "number, and decision; cut everything else. Never add content that is "
     "not in the material."),
    # --- The rest, grouped: act on it / mine it / transform it / repair it. ---
    ("Answer it",
     "Answers the question or task",
     "The material contains a question, problem, or task. Answer it / solve it "
     "directly and completely. Give the answer first, then only the essential "
     "supporting detail. If the user's spoken instruction narrows what to "
     "answer, follow it."),
    ("Reply",
     "Drafts your reply to it",
     "Write the reply the user would send to this material. Match the "
     "sender's register and length conventions (a chat message gets a chat "
     "reply, an email gets an email). Address every point that needs "
     "addressing; commit to clear positions rather than hedging. The user's "
     "spoken instruction tells you WHAT to say — you make it land well."),
    ("Continue",
     "Carries on where it left off",
     "Continue writing from exactly where the material ends. Match its voice, "
     "tone, vocabulary, and formatting so seamlessly that no reader could "
     "spot the join. Do not repeat or summarise what is already written."),
    ("Improve",
     "Clearer & tighter, same meaning",
     "Rewrite the material to be clearer, tighter, and better organised while "
     "preserving its meaning, voice, and intent. Fix awkward phrasing, "
     "strengthen weak sentences, smooth the flow. This is an edit, not a "
     "re-imagining — no new ideas."),
    ("Fix errors",
     "Spelling & grammar only",
     "Correct spelling, grammar, and punctuation errors in the material — and "
     "nothing else. Keep the wording, structure, tone, and formatting exactly "
     "as they are. Output the corrected text in full."),
    ("Make shorter",
     "Cuts it to half or less",
     "Cut the material down hard — aim for half the length or less. Keep "
     "every essential point and fact; remove repetition, filler, hedging, and "
     "anything a busy reader would skip."),
    ("Expand",
     "Adds depth & detail",
     "Develop the material into something fuller: add depth, concrete detail, "
     "examples, and connective reasoning where they genuinely help. Never pad "
     "— every added sentence must earn its place."),
    ("Explain simply",
     "Plain-language explainer",
     "Explain what the material says and means in plain language for someone "
     "with zero background. Short sentences, everyday words, a concrete "
     "example or analogy where it helps. By the end the reader should "
     "actually understand it, not just have read it."),
    ("Key points",
     "Bulleted main points",
     "Distil the material into its key points as a clean bullet list, ordered "
     "by importance. One point per bullet, each a single self-contained "
     "sentence. Group related points under short bold headers only if there "
     "are more than ~7 points."),
    ("Action items",
     "Tasks, owners & deadlines",
     "Extract every task, commitment, deadline, and next step from the "
     "material into a numbered checklist. For each: what must be done, who "
     "owns it (if identifiable), and by when (if stated). Skip vague "
     "aspirations — only real actions."),
    ("Humanise",
     "Makes AI text read naturally",
     "Rewrite the material so it reads as natural human writing rather than "
     "AI output. Vary sentence length and rhythm, cut formulaic transitions "
     "('moreover', 'in conclusion', 'it's important to note'), drop symmetric "
     "list-like prose, and let it have a point of view. Keep all the "
     "substance."),
    ("Merge",
     "Blends all items into one",
     "Combine ALL the selected items into one coherent piece of writing. "
     "Resolve overlaps and contradictions (prefer the most recent item when "
     "they conflict), order the content logically, and produce a single "
     "unified text — not a stitched-together sequence."),
    ("Extract facts",
     "Pulls out names, dates, numbers",
     "Pull out every concrete fact in the material — names, dates, numbers, "
     "amounts, decisions, links, addresses — as a labelled list, grouped by "
     "kind. Exact values only; nothing inferred."),
    ("Make formal",
     "Professional, polished tone",
     "Rewrite the material in a polished, professional register suitable for "
     "work: no slang, no contractions, measured tone, precise wording. "
     "Preserve all meaning — elevate the delivery, not the claims."),
    ("Critique",
     "Honest feedback + fixes",
     "Give a direct, useful critique of the material: what works, what is "
     "weak or wrong, and the 3-5 most valuable concrete improvements, in "
     "priority order. Be specific enough that the user can act on every "
     "point without asking follow-ups."),
    ("Outline",
     "Structures it into an outline",
     "Reorganise the material into a clear hierarchical outline: top-level "
     "sections with nested sub-points, ordered logically. Capture the full "
     "structure and substance — not just headline bullets — so the outline "
     "could be expanded back into the complete piece. Use concise, "
     "heading-style phrasing."),
]

# Custom slots sit immediately after the built-ins, so adding a built-in never
# collides with a user's custom preset slot. FIVE custom slots are offered on top
# of the 20 built-ins → 25 presets total (owner's requested count, 2026-06-20).
# Slots are derived from len(BUILTIN), so the built-in count can change without
# touching this line. Empty slots render as "Custom 1..5" in the UI.
CUSTOM_SLOTS = tuple(range(len(BUILTIN) + 1, len(BUILTIN) + 6))

# Mode directives — when the user also picks a MODE in the Context Island
# (or armed one by voice: "context email …"), this shapes the FORM of the
# final output, composed after the intent's instruction.
MODE_DIRECTIVES = {
    "email": ("Format the final output as a complete, ready-to-send email: "
              "greeting, body, sign-off. Subject line on top if one is implied."),
    "prompt": ("Format the final output as a polished, well-structured prompt "
               "for an AI: clear role, task, constraints, and expected output."),
    "reply": ("Format the final output as a direct reply to the material, "
              "matching its register."),
    "foreign": ("Preserve any non-English terms in the output exactly; do not "
                "translate them."),
    "convert": ("Routes dictation into the most appropriate Smart Mode "
                "(email, reply, prompt, or plain text) and formats "
                "accordingly. The output must match the chosen mode's "
                "conventions exactly."),
}


def load_custom():
    """Custom presets as {slot: {"title", "description", "instruction"}}.
    Absent slots are empty. `description` is optional (older files / blank
    entries default to "")."""
    try:
        with open(PRESETS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print("presets load error:", e)
        return {}
    out = {}
    for e in data:
        try:
            slot = int(e.get("slot", 0))
            title = str(e.get("title", "")).strip()
            desc = str(e.get("description", "")).strip()
            instr = str(e.get("instruction", "")).strip()
            if slot in CUSTOM_SLOTS and title and instr:
                out[slot] = {"title": title, "description": desc,
                             "instruction": instr}
        except (TypeError, ValueError, AttributeError):
            continue  # skip one bad entry, keep the rest
    return out


def save_custom(custom):
    """Persist {slot: {"title", "description", "instruction"}} — empty slots
    are simply not written (absence from the file means empty). `description`
    is optional and stored as "" when not provided."""
    rows = []
    try:
        for raw_slot, value in (custom.items()
                                if isinstance(custom, dict) else []):
            if not isinstance(value, dict):
                continue
            try:
                slot = int(raw_slot)
            except (TypeError, ValueError, OverflowError):
                continue
            if slot not in CUSTOM_SLOTS:
                continue
            title = str(value.get("title", "") or "").strip()
            description = str(
                value.get("description", "") or "").strip()
            instruction = str(
                value.get("instruction", "") or "").strip()
            if title and instruction:
                rows.append({"slot": slot, "title": title,
                             "description": description,
                             "instruction": instruction})
        rows.sort(key=lambda row: row["slot"])
        branding.ensure_dirs()
        tmp = PRESETS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        os.replace(tmp, PRESETS_PATH)
        branding.protect_private_path(PRESETS_PATH)
        return True
    except Exception as e:
        print("presets save error:", e)
        try:
            os.remove(PRESETS_PATH + ".tmp")
        except OSError:
            pass
        return False


def all_presets():
    """The full slot list for the Context Island panel:
    [(slot, title, description, instruction_or_None), ...] — instruction None
    means an empty custom slot (render as a '+ Add preset' placeholder)."""
    out = [(i + 1, t, d, ins) for i, (t, d, ins) in enumerate(BUILTIN)]
    custom = load_custom()
    for slot in CUSTOM_SLOTS:
        v = custom.get(slot)
        if v:
            out.append((slot, v["title"], v.get("description", ""),
                        v["instruction"]))
        else:
            out.append((slot, "+ Add preset", "", None))
    return out
