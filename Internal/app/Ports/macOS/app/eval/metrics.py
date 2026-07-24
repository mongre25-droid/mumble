"""Quality metrics for prompt eval harness.

Measures:
- conciseness: output/input word ratio (lower is better)
- adherence: keyword/format checks for lane-specific structure
- wrapper: detection of framing/meta language
- hallucination: garbage-in → hallucinated-output detection
- quality: heuristic scoring (proper structure, no hallucinations)
- cost: estimated dollar cost per scenario
"""

# Phrases that indicate wrapper/framing language — the AI is explaining
# rather than producing the output directly.
WRAPPER_PHRASES = [
    "here is your",
    "here's your",
    "certainly!",
    "i've written",
    "i have written",
    "below is",
    "here is the",
    "here you go",
    "let me help",
    "i'd be happy",
    "sure, here",
    "of course, here",
    "here's a",
    "no problem, here",
]


# ---------------------------------------------------------------------------
# Basic counting
# ---------------------------------------------------------------------------

def count_words(text):
    """Count words in text."""
    return len((text or "").split())


def count_chars(text):
    """Approximate token count (characters / 4)."""
    return max(1, len(text or "") // 4)


# ---------------------------------------------------------------------------
# Conciseness
# ---------------------------------------------------------------------------

def conciseness_ratio(output_text, input_text):
    """Return output/input word count ratio. Lower is more concise.

    Target: <= 2.0 for dictation polish.
    Target: <= 3.0 for prompt/email modes.
    """
    in_words = max(count_words(input_text), 1)
    out_words = count_words(output_text)
    return round(out_words / in_words, 2)


# ---------------------------------------------------------------------------
# Wrapper language detection
# ---------------------------------------------------------------------------

def wrapper_score(output_text):
    """Score 0-1 where 0 = clean, 1 = contains wrapper language.

    Case-insensitive check for known wrapper phrases.
    """
    lower = (output_text or "").lower()
    for phrase in WRAPPER_PHRASES:
        if phrase in lower:
            return 1.0
    return 0.0


# ---------------------------------------------------------------------------
# Content preservation
# ---------------------------------------------------------------------------

def content_preservation(output_text, input_text):
    """Check that key content words from input appear in output.

    Returns fraction of input substantive words preserved (approximated).
    This is a rough heuristic, not a semantic check.
    """
    in_words = [w.lower() for w in (input_text or "").split() if len(w) >= 5]
    if not in_words:
        return 1.0
    out_lower = (output_text or "").lower()
    found = sum(1 for w in in_words if w in out_lower)
    return round(found / len(in_words), 2)


# ---------------------------------------------------------------------------
# Garbage / hallucination detection
# ---------------------------------------------------------------------------

def garbage_detection(output_text, input_text):
    """Detect if the AI hallucinated a coherent response to garbage input.

    Returns 0.0 if clean (no hallucination), 1.0 if looks like hallucination.
    """
    in_w = count_words(input_text)
    out_w = count_words(output_text)
    # Input is garbage (very few words, no real words) but output is coherent
    # -> likely hallucination
    if in_w <= 5 and out_w > 20:
        return 1.0
    return 0.0


# ---------------------------------------------------------------------------
# Adherence — format/structure checks per lane
# ---------------------------------------------------------------------------

def adherence_score(output_text, lane="dictation"):
    """Check that output adheres to the expected structure for the lane.

    Returns 0.0 (no adherence) to 1.0 (perfect adherence).

    Dictation: should be clean prose, no special structure.
    Prompt: should start with role/instruction, not be a description.
    Email: should have greeting + body + sign-off.
    Reply: should be a direct response.
    Foreign: should NOT contain slash markers (should have resolved them).
    """
    text = (output_text or "").strip()
    if not text:
        return 0.0

    if lane == "dictation":
        # Dictation should NOT look like a prompt or email
        score = 1.0
        lower = text.lower()
        if lower.startswith("subject:") or lower.startswith("dear "):
            score -= 0.3
        if lower.startswith("you are a") or lower.startswith("build a"):
            score -= 0.3
        return max(0.0, score)

    elif lane == "prompt":
        # Prompt should be usable as a prompt — starts with role/instruction
        # or is clearly a task directive
        score = 0.5  # neutral start
        lower = text.lower()
        if lower.startswith("you are") or lower.startswith("act as"):
            score += 0.3
        if any(kw in lower for kw in ("build", "create", "write", "generate",
                                        "analyze", "review", "design")):
            score += 0.2
        # Should NOT be a description of a prompt
        if lower.startswith("here is") or lower.startswith("this prompt"):
            score -= 0.5
        return max(0.0, min(1.0, score))

    elif lane == "email":
        # Email should have greeting (hi/dear) and sign-off
        score = 0.0
        lower = text.lower()
        lines = text.split("\n")
        if lines and any(g in lower.split("\n")[0]
                         for g in ("hi ", "dear ", "hello ", "hey ")):
            score += 0.4
        # Check for sign-off in last few lines
        tail = "\n".join(lines[-3:]).lower()
        signoffs = ("best", "regards", "thanks", "sincerely", "cheers",
                     "warmly", "take care", "best regards", "kind regards")
        if any(s in tail for s in signoffs):
            score += 0.3
        # Has body content
        if len(lines) >= 3:
            score += 0.3
        return max(0.0, min(1.0, score))

    elif lane == "reply":
        # Reply should be a direct, conversational response
        score = 0.5
        lower = text.lower()
        if lower.startswith("here is") or lower.startswith("i've written"):
            score -= 0.4
        if len(text) > 10:  # has actual content
            score += 0.3
        return max(0.0, min(1.0, score))

    elif lane == "foreign":
        # Foreign mode should NOT contain slash markers (should be resolved)
        score = 1.0
        if "//" in text:
            score -= 0.8
        return max(0.0, score)

    return 0.5  # unknown lane


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

def estimate_cost(input_words, output_words, price_per_1k_input=0.0,
                  price_per_1k_output=0.0):
    """Estimate dollar cost based on approximate token counts.

    Tokens are roughly words * 1.3 for English text. Cost is calculated
    as (input_tokens / 1000) * price_per_1k_input +
       (output_tokens / 1000) * price_per_1k_output.
    """
    input_tokens = input_words * 1.3
    output_tokens = output_words * 1.3
    cost = ((input_tokens / 1000) * price_per_1k_input +
            (output_tokens / 1000) * price_per_1k_output)
    return round(cost, 6)


# ---------------------------------------------------------------------------
# Combined scoring
# ---------------------------------------------------------------------------

def score(output_text, input_text, lane="dictation"):
    """Return a dict of metric scores for one scenario run.

    Returns:
        dict with scores for conciseness, wrapper, content_preservation,
        adherence, garbage_detection, and a combined overall score
        (0-100, higher=better).
    """
    conciseness = conciseness_ratio(output_text, input_text)
    wrapper = wrapper_score(output_text)
    content = content_preservation(output_text, input_text)
    garbage = garbage_detection(output_text, input_text)
    adherence = adherence_score(output_text, lane)

    # Conciseness targets by lane
    conciseness_targets = {
        "dictation": 2.0,
        "prompt": 4.0,
        "email": 3.0,
        "reply": 3.0,
        "foreign": 2.5,
    }
    target = conciseness_targets.get(lane, 2.0)
    conciseness_penalty = max(0, (conciseness - target) * 5)

    # Combined score: 100 - penalties
    overall = 100.0
    overall -= wrapper * 40       # wrapper language = -40
    overall -= garbage * 50       # hallucination = -50
    overall -= conciseness_penalty  # verbosity penalty
    overall -= (1 - content) * 20   # content loss = -20
    overall -= (1 - adherence) * 15 # poor structure = -15

    return {
        "conciseness_ratio": conciseness,
        "wrapper_detected": bool(wrapper),
        "content_preservation": content,
        "adherence": round(adherence, 2),
        "garbage_hallucination": bool(garbage),
        "overall": round(max(0.0, min(100.0, overall)), 1),
        "input_words": count_words(input_text),
        "output_words": count_words(output_text),
    }
