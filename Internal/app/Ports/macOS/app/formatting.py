#!/usr/bin/env python3
"""
Offline, rule-based text processing for Mumble. No AI / no network.

Entry points
  detect_mode(raw, enabled) -> (mode, remainder, clip_count, context_select[always False])
  detect_mode_button(raw, enabled, window_words) -> (mode, remainder, clip_count)
  guess_mode(output)        -> infer mode from AI output heuristically
  format_transcript(text)   -> cleaned, capitalized text
  build_prompt(request)     -> structured prompt (offline fallback)
  build_email(request, name) -> greeting / body / sign-off (offline fallback)

All deterministic -> unit-tested in test_formatting.py.
"""

import re


def _fuzzy_dist(a: str, b: str) -> int:
    """Levenshtein distance between two strings (case-sensitive)."""
    if len(a) < len(b):
        a, b = b, a
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        cur = [i + 1]
        for j, cb in enumerate(b):
            cur.append(
                min(
                    prev[j + 1] + 1,  # deletion
                    cur[j] + 1,  # insertion
                    prev[j] + (0 if ca == cb else 1),  # substitution
                )
            )
        prev = cur
    return prev[-1]


def _fuzzy_match(word: str, candidates: dict) -> str:
    """Return the best candidate key if `word` is within 1 edit of it, else ''."""
    w = _norm(word)
    if not w or len(w) < 2:
        return ""
    if w in candidates:
        return candidates[w]
    best, best_dist = "", 99
    for cand in candidates:
        d = _fuzzy_dist(w, cand)
        if d < best_dist:
            best_dist = d
            best = candidates[cand]
    return best if best_dist <= 1 else ""


# --------------------------- shared cleanup ----------------------------------

_FILLER_RE = re.compile(
    r"(?i)(?<![A-Za-z'])(?:u+m+|u+h+|e+r+|erm|a+h+|h+m+|m{3,}|mhm|mm-hmm|you know)"
    r"(?![A-Za-z'])[ ]?,?"
)


def _remove_fillers(s: str) -> str:
    out = _FILLER_RE.sub(" ", s)
    return re.sub(r"\s+,", ",", out)


def _cap_first(s: str) -> str:
    return s[0].upper() + s[1:] if s else s


# Lowercase-leading words that carry an interior capital (iPhone, macOS, eBay,
# iOS) are intentionally cased — force-capitalising their first letter at a
# sentence start damaged them (iPhone -> IPhone). Preserve any such word verbatim.
_INTERCAPS_RE = re.compile(r"^[a-z]+[A-Z]")


def _capitalize(s: str) -> str:
    out, cap_next, i, n = [], True, 0, len(s)
    while i < n:
        ch = s[i]
        if cap_next and ch.isalpha():
            # Grab the whole word so intentional intercaps can be spotted.
            j = i
            while j < n and (s[j].isalpha() or s[j] == "'"):
                j += 1
            word = s[i:j]
            if _INTERCAPS_RE.match(word):
                out.append(word)            # leave iPhone / macOS / eBay untouched
            else:
                out.append(word[0].upper())
                out.append(word[1:])
            cap_next = False
            i = j
            continue
        out.append(ch)
        if ch in "!?\n":
            cap_next = True
        elif ch == ".":
            # A period ends a sentence only when it isn't inside a number
            # ("3.50") or glued to a following letter as an abbreviation
            # ("e.g."). Require a non-digit before it and whitespace/end after
            # it — otherwise "it costs 3.50 and works" became "...3.50 And
            # works." and "version 2.0 is out" became "...2.0 Is out".
            prev_digit = i > 0 and s[i - 1].isdigit()
            nxt = s[i + 1] if i + 1 < n else ""
            if not prev_digit and (nxt == "" or nxt.isspace()):
                cap_next = True
        i += 1
    res = "".join(out)
    # Only the pronoun "I": a standalone 'i' bounded by spaces/punctuation —
    # NOT part of an abbreviation ("i.e."), a decimal, or a word ("wifi"). The
    # old \bi\b matched the 'i' inside "i.e." and produced "I.e.".
    return re.sub(r"(?<![A-Za-z0-9.'])i(?![A-Za-z0-9.'])", "I", res)


def _ensure_terminal(s: str) -> str:
    st = s.rstrip()
    if st and st[-1] not in ".?!:,)]\"'" and not st.endswith("-"):
        # Question mark for sentences that start with wh-words but lack end
        # punctuation — the speaker's rising intonation was lost by Whisper.
        low = st.lstrip("\"'(").lower()
        wh_starters = ("who", "what", "where", "when", "why", "how",
                       "is ", "are ", "do ", "does ", "did ", "can ",
                       "could ", "would ", "will ", "shall ", "should ",
                       "may ", "might ", "have ", "has ", "had ",
                       "am ", "was ", "were ")
        if any(low.startswith(w) for w in wh_starters) and len(st.split()) <= 20:
            st += "?"
        else:
            st += "."
    return st


# ---- rule-based comma insertion (lightweight, no model) --------------------
# Inserts a comma before conjunctions ("and"/"or"/"but") that join two
# independent clauses (both sides have a subject+verb). This is the most
# impactful single comma rule for dictation output and catches ~80% of
# missing-commas in the common "I did X and Y happened" pattern.

_COMMA_CONJUNCTION_RE = re.compile(
    r"([a-zA-Z]{2,})\s+(and|or|but)\s+((?:i|you|he|she|it|we|they|that|"
    r"this|there|these|those|the|a|an|my|your|our|their|his|her|its|"
    r"[A-Z][a-z])\b)",
    re.IGNORECASE,
)


def _insert_commas(s: str) -> str:
    """Add a comma before coordination conjunctions joining independent clauses.
    Conservative — only when the right side starts with a subject-like word."""
    return _COMMA_CONJUNCTION_RE.sub(r"\1, \2 \3", s)


# ---- Hallucination Blocklist (Bag of Hallucinations) -----------------------
# Whisper produces a small, consistent set of hallucinated phrases on
# non-speech/silence input. ~35% of all hallucinations are just two phrases
# ("thank you", "thanks for watching"). We strip these known phantom strings
# from the output. Common single words ("the", "so", "you", "oh", "okay") are
# EXCLUDED from the blocklist despite appearing in the academic data — they
# have too high a false-positive risk in genuine speech. Ref: arXiv:2501.11378
# and the community-collected blocklist at r/LocalLLaMA (March 2026).

_HALLUCINATION_PHRASES = [
    # Top academic hallucinations (order: longest first for substring safety)
    "thank you for watching",
    "thanks for watching",
    "thank you so much for watching",
    "thank you very much for watching",
    "hello everyone welcome to my channel",
    "subtitles by the amara org community",
    "subtitles by the amara.org community",
    "subscribe to my channel",
    "subscribe to the channel",
    "please subscribe to my channel",
    "don't forget to subscribe",
    "please like and subscribe",
    "like and subscribe",
    "subscribe and like",
    "subscribe for more",
    "please subscribe",
    "thanks for listening",
    "thank you for listening",
    "thank you so much",
    "thank you very much",
    "see you next time",
    "thanks for coming",
    "i'm sorry",
    "oh my god",
    "goodbye",
    # Training-data artefacts leaked into output
    "subtitles by",
    "transcript",
    # Bottom: shorter phrases checked only after longer ones
    "thank you",
    "thanks",
    "bye",
    "meow",
]


def _apply_hallucination_blocklist(text: str) -> str:
    """Strip known Whisper hallucination phrases from `text`. Returns empty
    string when the ENTIRE text (whitespace/punctuation-normalised) is a known
    hallucination phrase — this catches the common "thank you" silence case."""
    if not text or not text.strip():
        return ""
    s = text.strip()
    # Full-match test: is the entire utterance just a hallucination?
    norm = re.sub(r"[.,!?;:\"'()\s]+", " ", s).strip().lower()
    for phrase in _HALLUCINATION_PHRASES:
        if norm == phrase:
            return ""
    # Substring strip: remove hallucination phrases embedded in longer output.
    # Only strip phrases with 3+ words — shorter ones have too high a
    # false-positive risk in genuine speech (e.g. "thank you" inside
    # "thank you for helping me" is real, not a hallucination).
    out = s
    changed = False
    for phrase in _HALLUCINATION_PHRASES:
        if len(phrase.split()) <= 2:
            continue  # only full-match for short phrases
        # Case-insensitive whole-phrase match
        pattern = re.compile(
            r"(?:^|[\s.,!?;:]+)" + re.escape(phrase) + r"(?:$|[\s.,!?;:]+)",
            re.IGNORECASE,
        )
        out, count = pattern.subn(" ", out)
        changed = changed or count > 0
    if not changed:
        return s
    # Clean up: collapse multiple spaces, strip. If nothing remains, return empty.
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"[ \t]*\n[ \t]*", "\n", out).strip(" .,!?;:\n\t")
    return out if out else ""


# ---- Delooping (repetition collapse) ---------------------------------------
# Whisper can enter a looping state where it repeats a fragment ("Welcome to
# New York City of New York City of New York..."). Appears in ~9-20% of
# hallucinations (ICASSP 2025 paper). Detection: find repeated token sequences
# of length >=3 tokens that repeat >=3 times and collapse to one occurrence.

_DELOOP_TOKEN_RE = re.compile(r"[A-Za-z0-9'+#&-]+|[.,!?;:]+|\S")


def _tokenize(text: str):
    """Split `text` into a list of tokens (words, punctuation, other chars)."""
    return _DELOOP_TOKEN_RE.findall(text)


def _detokenize(tokens):
    """Join tokens back into text with natural spacing."""
    if not tokens:
        return ""
    out = [tokens[0]]
    for t in tokens[1:]:
        # No space before punctuation
        if t in ".,!?;:)]]" and out[-1] != " ":
            out.append(t)
        elif out[-1] in "([{(#@$" and t not in ".,!?;:)]]":
            out.append(t)  # no space after opening brackets
        elif t.startswith("'") or (t.startswith("-") and len(t) > 1):
            out.append(t)  # contraction / hyphenated
        else:
            out.append(" " + t)
    return "".join(out)


def _deloop(text: str) -> str:
    """Collapse repetition loops: if a token sequence repeats >=3 times
    consecutively, keep only the first occurrence. Patterns of 1 token need >=4
    repeats (otherwise single-word stutters like 'I I I' could be intentional).
    Handles the common Whisper looping failure mode without damaging genuine
    repeated content (which typically repeats 2x at most in natural speech)."""
    if not text or not text.strip():
        return text
    tokens = _tokenize(text)
    n = len(tokens)
    if n < 4:  # 1 token × 4 repeats = 4 minimum
        return text
    # Search for the longest repetition pattern starting at each position.
    i = 0
    changed = False
    while i < n:
        best_len, best_count = 0, 0
        # Try pattern lengths from long to short
        for plen in range(min(15, (n - i) // 2), 0, -1):
            if plen >= 2 and i + plen * 3 > n:
                continue  # need at least 3 repeats for patterns >=2
            if plen == 1 and i + plen * 4 > n:
                continue  # need at least 4 repeats for 1-token patterns
            min_repeats = 4 if plen == 1 else 3
            if i + plen * min_repeats > n:
                continue
            pat = tokens[i:i + plen]
            count = 1
            pos = i + plen
            while pos + plen <= n:
                if tokens[pos:pos + plen] == pat:
                    count += 1
                    pos += plen
                else:
                    break
            if count >= min_repeats and plen > best_len:
                best_len, best_count = plen, count
        if best_len >= 1 and best_count >= (4 if best_len == 1 else 3):
            keep = tokens[:i + best_len]
            tokens = keep + tokens[i + best_len * best_count:]
            n = len(tokens)
            changed = True
            i += best_len
        else:
            i += 1
    return _detokenize(tokens) if changed else text


# ----------------------------- list helpers ----------------------------------

_ORDINALS = [
    "firstly",
    "secondly",
    "thirdly",
    "fourthly",
    "fifthly",
    "sixthly",
    "seventhly",
    "eighthly",
    "ninthly",
    "tenthly",
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "finally",
    "lastly",
]
_NUMWORDS = "one|two|three|four|five|six|seven|eight|nine|ten"
_LIST_RE = re.compile(
    r"(?i)(?:"
    r"(?:^|[.,;:\n]\s*)(?:" + "|".join(_ORDINALS) + r")"
    r"|"
    r"(?:^|[.,;:\n]\s*|\s+)(?:number|step)\s+(?:" + _NUMWORDS + r")"
    r")\b[,:]?\s+"
)
_MAKE_LIST_RE = re.compile(
    r"(?i)^\s*(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?"
    r"(?:(?:make|create|give\s+me|write|jot\s+down|build)\s+(?:me\s+)?(?:a\s+)?)?"
    r"list\s+(?:of\s+|for\s+)?[:,\-]?\s*(?P<items>.+)$"
)
_PARAGRAPH_RE = re.compile(r"(?i)\bnew paragraph\b[.,!?]?")
_NEWLINE_RE = re.compile(r"(?i)\bnew lines?\b[.,!?]?")
_BULLET_RE = re.compile(r"(?i)\b(?:bullet point|new bullet|next bullet)\b[.,!?]?")


def _split_list_items(text: str):
    text = re.sub(r"(?i)\b(?:new item|next item|and then|then)\b", ",", text)
    # Allow the conjunction to be glued to the comma with no space ("milk,and
    # eggs"): the old "\s+and" required whitespace before "and", so a glued case
    # left "and eggs" as an item and emitted "- And eggs". \b keeps it from
    # matching "and" inside a word (brand, command).
    text = re.sub(r"\s*,?\s*\band\s+", ", ", text)
    text = re.sub(r"\s*&\s*", ", ", text)
    parts = re.split(r"\s*[,;]\s*|\n", text)
    return [p.strip() for p in parts if p.strip(" .,")]


def _try_make_list(s: str):
    m = _MAKE_LIST_RE.match(s)
    if not m:
        return None
    items_raw = m.group("items").strip()
    heading = None
    if ":" in items_raw:
        head, rest = items_raw.split(":", 1)
        if rest.strip():
            heading, items_raw = head.strip(), rest.strip()
    items = _split_list_items(items_raw)
    if len(items) < 2:
        return None
    lines = []
    if heading:
        h = _cap_first(_remove_fillers(heading).strip())
        if h:
            lines.append(h + ":")
    for it in items:
        it = _remove_fillers(it).strip(" .,")
        if it:
            lines.append("- " + _cap_first(it))
    return "\n".join(lines) if lines else None


def _maybe_numbered_list(s: str) -> str:
    if len(list(_LIST_RE.finditer(s))) < 2:
        return s
    counter = [0]

    def repl(_m):
        counter[0] += 1
        return f"\n{counter[0]}. "

    return _LIST_RE.sub(repl, s)


# ----------------------- Personal vocabulary ---------------------------------


def sanitize_hotwords(terms):
    """Return safe, single-line STT hotwords without mutating user settings."""
    if not terms:
        return []
    if isinstance(terms, bytes):
        terms = [terms.decode("utf-8", "replace")]
    elif isinstance(terms, str):
        terms = [terms]
    else:
        try:
            terms = iter(terms)
        except TypeError:
            terms = [terms]
    cleaned = []
    seen = set()
    for term in terms:
        if term is None:
            continue
        value = str(term).replace("\r", " ").replace("\n", " ").strip()
        value = re.sub(r"[^\w\s'-]", "", value)
        value = re.sub(r"\s+", " ", value).strip()[:100].strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            cleaned.append(value)
    return cleaned


def apply_vocabulary(text: str, vocab) -> str:
    """Replace known mis-hearings with the user's correct spellings.

    `vocab` maps spoken form → replacement (e.g. "mambo" → "Mumble").
    Matching is case-insensitive on whole-word boundaries and supports
    multi-word phrases ("mum bull" → "Mumble"). Longer phrases are applied
    first so "mum bull" wins over a separate "mum" entry. When the spoken
    form starts a sentence (matched text begins with an uppercase letter),
    the replacement keeps its given capitalization — users write the
    replacement exactly as they want it to appear."""
    if not text or not vocab:
        return text
    out = text
    for wrong in sorted(vocab, key=len, reverse=True):
        right = str(vocab[wrong]).strip()
        wrong = str(wrong).strip()
        if not wrong or not right:
            continue
        # Whole-word boundaries so "sara" never rewrites the inside of "sarah".
        # \b only works against word characters, so keys that start/end with
        # punctuation ("c++") use whitespace lookarounds instead.
        prefix = r"\b" if (wrong[0].isalnum() or wrong[0] == "_") else r"(?<!\S)"
        suffix = r"\b" if (wrong[-1].isalnum() or wrong[-1] == "_") else r"(?!\S)"
        body = r"\s+".join(re.escape(w) for w in wrong.split())
        pattern = re.compile(prefix + body + suffix, re.IGNORECASE)
        # Use a function replacement so `right` is taken LITERALLY — a plain string
        # would let `\1`, `\g<name>`, `\0` etc. in the user's replacement be parsed
        # as regex backreferences (crash on bad refs, or silent control chars).
        out = pattern.sub(lambda _m: right, out)
    return out


# ------------------- Personal vocabulary: term matching ----------------------
# The default vocabulary workflow: the user lists CORRECT terms (names, brands,
# jargon) and Mumble finds mis-heard variants automatically — no need to predict
# misspellings. Three layers:
#   1. STT bias: terms are passed to faster-whisper as `hotwords` (mumble.py).
#   2. High-confidence auto-fix (here): phonetic key match + small edit distance.
#   3. Medium-confidence AI review: annotate_vocab_terms tags "heard//Term" with
#      slash alternatives for the polish AI to resolve from context.

# Words too common to ever auto-rewrite — a vocab term may legitimately sound
# like these, but silently replacing them causes worse damage than it fixes.
_COMMON_WORDS = frozenset(
    "the be to of and a in that have i it for not on with he as you do at this "
    "but his by from they we say her she or an will my one all would there their "
    "what so up out if about who get which go me when make can like time no just "
    "him know take people into year your good some could them see other than then "
    "now look only come its over think also back after use two how our work first "
    "well way even new want because any these give day most us is was are been "
    "has had were said did very am "
    # Everyday words that collide with SHORT vocab terms at edit-distance 1 and
    # would otherwise be silently rewritten (e.g. "cut"->"Cat", "code"->"Cody",
    # "marks"->"Marx"). Downgrading these to AI-reviewed "medium" is safe; a real
    # term that genuinely sounds like one is still corrected via the AI pass.
    "cut cuts code codes coding mark marks marked cat cats car cars dog dogs set "
    "sets run runs hot cold send sent read write wrote note notes list lists call "
    "calls talk talks walk help need needs want find found made make real sure "
    "full half part side line lines word words name names file files page pages "
    "view edit save open close stop start big small old left right top end map "
    "case test tests code data date type kind sort done".split()
)


def _phonetic_key(word: str) -> str:
    """A compact metaphone-style phonetic skeleton: maps a word to a consonant
    signature so 'sara'/'sarah', 'jon'/'john', 'kris'/'chris' collide. Not a
    full Double Metaphone — paired with an edit-distance bound it's accurate
    enough, with zero dependencies."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return ""
    # Digraph / context rules first (order matters).
    subs = [
        ("ough", "f"), ("augh", "f"), ("tion", "xn"), ("sion", "xn"),
        ("sch", "sk"), ("tch", "x"), ("chr", "kr"), ("ph", "f"), ("gh", ""),
        ("kn", "n"), ("gn", "n"), ("wr", "r"), ("wh", "w"), ("qu", "kw"),
        ("ck", "k"), ("sh", "x"), ("ch", "x"), ("th", "0"), ("dg", "j"),
        ("mb", "m"), ("ps", "s"), ("x", "ks"),
    ]
    for a, b in subs:
        w = w.replace(a, b)
    # c → s before e/i/y, else k; g → j before e/i/y.
    w = re.sub(r"c(?=[eiy])", "s", w)
    w = w.replace("c", "k")
    w = re.sub(r"g(?=[eiy])", "j", w)
    w = w.replace("z", "s").replace("v", "f").replace("d", "t").replace("b", "p")
    # y and any remaining h are near-silent — drop both.
    w = w.replace("y", "").replace("h", "")
    if not w:
        return ""
    # Keep the first letter (a leading vowel normalises to 'a'), drop the rest
    # of the vowels, collapse doubled letters.
    head = "a" if w[0] in "aeiou" else w[0]
    out = head + re.sub(r"[aeiou]", "", w[1:])
    return re.sub(r"(.)\1+", r"\1", out)


def _term_confidence(heard: str, term: str) -> str:
    """Classify how confidently `heard` is a mis-hearing of `term`.
    Returns 'exact' | 'high' | 'medium' | ''. Case-insensitive."""
    h, t = heard.lower(), term.lower()
    if not h or not t:
        return ""
    if h == t:
        return "exact"
    dist = _fuzzy_dist(h, t)
    phonetic = _phonetic_key(h) == _phonetic_key(t) and _phonetic_key(t) != ""
    # A phonetic match auto-replaces (high), but only for LONGER terms. Short
    # terms (3-4 chars) collide with everyday English at dist<=2 ("Cat"~"cut",
    # "Cody"~"code", "Marx"~"marks"), and "high" means a silent rewrite with no
    # review — so for short terms demand a near-exact spelling (dist<=1); a looser
    # phonetic match falls through to "medium" (AI/user-reviewed, not auto-applied).
    if phonetic and dist <= 2 and len(t) >= 5:
        return "high"
    if phonetic and dist <= 1 and len(t) >= 3:
        return "high"
    if dist == 1 and len(t) >= 5:
        return "high"
    if phonetic and dist <= 4:
        return "medium"
    if dist == 2 and len(t) >= 6:
        return "medium"
    return ""


def _vocab_scan(text: str, terms):
    """Yield (start, end, heard, term, confidence) for every vocabulary match
    in `text`. Multi-word terms are matched against equal-length word windows.
    Never matches inside an existing slash annotation."""
    if not text or not terms:
        return []
    tokens = [(m.start(), m.end(), m.group()) for m in re.finditer(r"[A-Za-z'+#&-]+", text)]
    found = []
    taken = set()
    # Longest terms first so multi-word wins over a one-word subset.
    for term in sorted({str(t).strip() for t in terms if str(t).strip()},
                       key=lambda t: -len(t)):
        n = len(term.split())
        for i in range(len(tokens) - n + 1):
            window = tokens[i:i + n]
            if any(j in taken for j in range(i, i + n)):
                continue
            heard = text[window[0][0]:window[-1][1]]
            if "//" in heard:
                continue
            conf = _term_confidence(re.sub(r"\s+", " ", heard), term)
            if conf in ("high", "medium") and heard.lower() in _COMMON_WORDS:
                conf = "medium" if conf == "high" else ""
            if conf == "exact" and heard != term:
                conf = "case"  # right word, wrong capitalization
            if conf in ("high", "medium", "case"):
                found.append((window[0][0], window[-1][1], heard, term, conf))
                for j in range(i, i + n):
                    taken.add(j)
    return sorted(found, key=lambda f: f[0])


def apply_vocabulary_terms(text: str, terms) -> str:
    """High-confidence pass: replace tokens that are confidently the user's
    terms (phonetic + edit-distance match, or just wrong capitalization).
    Medium-confidence candidates are left untouched — annotate_vocab_terms
    offers those to the AI instead."""
    out, shift = text, 0
    for start, end, heard, term, conf in _vocab_scan(text, terms):
        if conf in ("high", "case"):
            out = out[:start + shift] + term + out[end + shift:]
            shift += len(term) - (end - start)
    return out


def annotate_vocab_terms(text: str, terms) -> str:
    """Medium-confidence pass for the AI lane: tag candidates with slash
    alternatives ('heard//Term', most-likely first) so the polish AI can
    decide from context. Run AFTER apply_vocabulary_terms."""
    out, shift = text, 0
    for start, end, heard, term, conf in _vocab_scan(text, terms):
        if conf == "medium":
            tagged = f"{heard}//{term}"
            out = out[:start + shift] + tagged + out[end + shift:]
            shift += len(tagged) - (end - start)
    return out


# ----------------------------- Text mode -------------------------------------


def format_transcript(text: str, commands: bool = True) -> str:
    """Offline transcript cleanup. `commands=False` disables SPOKEN-COMMAND
    detection ("new line"/"new paragraph"/"bullet point"/auto-list building) —
    the live app passes False (owner decision 2026-06-12: voice-triggered
    formatting caused false positives; structure is the AI polish's job, and
    saying "new paragraph" should just type the words). Default True keeps
    the historical behaviour for tests and any external callers."""
    if not text or not text.strip():
        return ""
    s = text.strip()

    if commands:
        block = _try_make_list(s)
        if block is not None:
            return _capitalize(block)
        s = _PARAGRAPH_RE.sub("\n\n", s)
        s = _NEWLINE_RE.sub("\n", s)
        s = _BULLET_RE.sub("\n- ", s)
    s = _remove_fillers(s)
    if commands:
        s = _maybe_numbered_list(s)

    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"[ \t]+([,.!?;:])", r"\1", s)
    # Re-insert a space after punctuation glued to a letter ("hello.World" ->
    # "hello. World"). Periods are handled separately: only respace a period
    # that follows a 2+ letter word, so abbreviations/initialisms ("e.g.",
    # "U.S.A.") aren't shredded into "e. g." / "U. S. A.".
    s = re.sub(r"([,!?;:])([A-Za-z])", r"\1 \2", s)
    s = re.sub(r"(?<=[A-Za-z]{2})\.([A-Za-z])", r". \1", s)

    lines = [ln.strip() for ln in s.split("\n")]
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    cleaned = []
    for ln in lines:
        if ln == "" and cleaned and cleaned[-1] == "":
            continue
        cleaned.append(ln)
    s = "\n".join(cleaned)

    # ---- quality guards (rule-based, zero-model-cost) ----
    s = _deloop(s)
    s = _apply_hallucination_blocklist(s)
    if not s or not s.strip():
        return ""
    s = _insert_commas(s)

    return _ensure_terminal(_capitalize(s))


def guess_mode(output):
    """Infer the mode from AI output using lightweight heuristics.
    The AI's UNIVERSAL_SYSTEM handles intent detection internally;
    this just gives us a label for history/stats. Returns one of:
    'text', 'prompt', 'email', 'reply'."""
    out = (output or "").strip()
    if not out:
        return "text"
    lines = [line for line in out.split("\n") if line.strip()]
    # Prompt: structured output with bulleted requirements, role statements,
    # or task patterns. Look for "You are" anywhere, or bulleted requirements
    # with task-like opening line.
    has_you_are = bool(re.search(r"(?i)\bYou are (?:an? |the )", out))
    has_task_markers = "# Task" in out or "# Instructions" in out
    bullet_count = sum(
        1 for line in lines if line.strip().startswith("- ") or line.strip().startswith("* ")
    )
    if has_you_are and (has_task_markers or bullet_count >= 2):
        return "prompt"
    if bullet_count >= 3 and not any(
        line.strip().startswith(("Hi ", "Hello ", "Dear ", "Hey ", "Subject:"))
        for line in lines[:2]
    ):
        # Multiple bullets without email greeting — check if this looks like
        # a prompt (has a substantial opening line) or a simple list.
        first = lines[0].strip() if lines else ""
        prompt_keywords = (
            "build",
            "create",
            "implement",
            "design",
            "develop",
            "write",
            "generate",
            "craft",
        )
        if len(first) > 50 or any(kw in first.lower() for kw in prompt_keywords):
            return "prompt"
        # Short opening + bullets = clean text (the AI/text lane bullets dictated
        # lists natively — there is no longer a dedicated List mode to label).
        return "text"
    # Email: has a greeting + sign-off pattern, or Subject: + greeting
    if re.match(r"(?i)^(?:Hi|Hello|Dear|Hey)\b", out) and re.search(
        r"(?i)(?:Best regards|Cheers|Sincerely|Thanks|Warmly|Yours)", out
    ):
        return "email"
    if "Subject:" in out and re.search(r"(?i)\b(?:Hi|Hello|Dear|Hey)\b", out):
        return "email"
    # Primarily bullet points (no greeting/signoff) — still plain text; the AI
    # bullets dictated lists inside the normal text lane, so it reads as "text".
    if (
        lines
        and bullet_count >= len(lines) * 0.5
        and not re.match(r"(?i)^(?:Hi|Hello|Dear|Hey|Subject:)", out)
    ):
        return "text"
    # Reply: conversational tone, starts with acknowledgments
    reply_openers = (
        "thanks",
        "thank you",
        "good",
        "great",
        "sure",
        "absolutely",
        "i'd",
        "i'll",
        "let me",
        "here's",
        "that's",
        "no problem",
        "you're",
        "i think",
        "i agree",
        "got it",
    )
    first_line = lines[0].lower() if lines else ""
    if any(first_line.startswith(o) for o in reply_openers):
        return "reply"
    return "text"


# ------------------------------ mode detection -------------------------------

# Words that may precede an intentional trigger ("make this a prompt",
# "send an email", "I want to list ..."). Deliberately excludes "the" to avoid
# false triggers like "the email I got ...".
_LEAD_INS = {
    "make",
    "this",
    "that",
    "it",
    "a",
    "an",
    "into",
    "turn",
    "send",
    "write",
    "compose",
    "create",
    "draft",
    "please",
    "can",
    "could",
    "would",
    "you",
    "give",
    "me",
    "just",
    "ok",
    "okay",
    "hey",
    "so",
    "well",
    "mumble",
    "new",
    "to",
    "for",
    "of",
    "i",
    "want",
    "need",
    "wanna",
    "gonna",
    "lets",
    "let",
}


def _norm(tok: str) -> str:
    return re.sub(r"[^a-z]", "", tok.lower())


def _strip_lead(s: str) -> str:
    return re.sub(r"(?i)^[\s,:;.\-]*(?:of\s+|for\s+|that\s+)?", "", s).strip()


_MODE_WORD = {
    "prompt": "prompt",
    "email": "email",
    "reply": "reply",
    "respond": "reply",
}
_COUNT = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "couple": 2,
    "few": 3,
}


def _count_from(s):
    m = re.search(
        r"(?i)\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|couple|few)\b",
        s or "",
    )
    if not m:
        return None
    w = m.group(1).lower()
    return min(int(w), 10) if w.isdigit() else _COUNT.get(w)


def detect_mode(raw, enabled=None):
    """Return (mode, request, clip_count, context_select).

    mode           — text / prompt / email / reply (keyword can be anywhere)
    request        — the instruction, with command words stripped
    clip_count     — 1 when the phrasing references material ("make THIS a …"),
                     else 0. ("context" is no longer parsed — it's content.)
    context_select — always False (kept for signature stability; the Context
                     Island is gone, its job moved to the Hub)
    Casual mentions ("we should make a list later") stay text — a transform needs a
    demonstrative ("make THIS a prompt") or be the opening words ("make a prompt for…").
    """
    enabled = enabled or {}
    text = (raw or "").strip()
    if not text:
        return "text", "", 0, False

    # 1) "make/turn THIS/IT/… into a PROMPT/EMAIL/LIST/MESSAGE" — anywhere (needs a
    #    demonstrative, so it won't fire on "we should make a list later")
    c2 = re.search(
        r"(?i)\b(?:make|turn|put)\s+(?:this|it|that|the following|the above|"
        r"the text|what i(?:'ve| have)?\s*(?:just\s+)?said|everything|all (?:of )?this)"
        r"\s+(?:(?:in)?to\s+)?(?:a|an)\s+(prompt|email|message|point)\b",
        text,
    )
    if c2:
        g = c2.group(1).lower()
        mode = "reply" if g == "message" else ("prompt" if g == "point" else g)
        if enabled.get(mode, True):
            return (
                mode,
                _strip_lead((text[: c2.start()] + " " + text[c2.end() :]).strip()),
                1,
                False,
            )

    # 3) "make/write/create a PROMPT/EMAIL …" — only at the very start
    c3 = re.match(
        r"(?i)^(?:please\s+|can you\s+|could you\s+|just\s+|okay[\s,]+|ok[\s,]+|hey[\s,]+)*"
        r"(?:make|write|give|create|draft|compose|generate)\s+(?:me\s+)?(?:a|an)\s+"
        r"(prompt|email)\b[\s,:.\-]*",
        text,
    )
    if c3 and enabled.get(c3.group(1).lower(), True):
        mode = c3.group(1).lower()
        clip = (
            1
            if re.search(r"(?i)\b(this|that|the text|copied|clipboard)\b", text)
            else 0
        )
        return mode, _strip_lead(text[c3.end() :]), clip, False

    # 4) "reply to this / respond to …" — requires a demonstrative so casual
    #    mentions like "she didn't reply" stay text (HANDBOOK: "a transform needs
    #    a demonstrative").
    c4 = re.search(
        r"(?i)\b(?:reply|respond)\b\s+to\s+(?:this|that|it|the message)", text
    )
    if c4 and enabled.get("reply", True):
        return (
            "reply",
            _strip_lead((text[: c4.start()] + " " + text[c4.end() :]).strip()),
            1,
            False,
        )

    # 5) first meaningful word is a mode keyword (after optional lead-ins).
    #    Uses fuzzy matching — a one-letter slip like "emal" still catches "email".
    tokens = text.split()
    for i, tok in enumerate(tokens[:15]):
        w = _norm(tok)
        if not w:
            continue
        if w in _LEAD_INS:
            continue
        # Exact match first, then fuzzy
        if w in _MODE_WORD and enabled.get(_MODE_WORD[w], True):
            mode = _MODE_WORD[w]
            return (
                mode,
                _strip_lead(" ".join(tokens[i + 1 :])),
                (1 if mode == "reply" else 0),
                False,
            )
        fm = _fuzzy_match(tok, _MODE_WORD)
        if fm and enabled.get(fm, True):
            return (
                fm,
                _strip_lead(" ".join(tokens[i + 1 :])),
                (1 if fm == "reply" else 0),
                False,
            )
        break

    return "text", text, 0, False


# --------------------- button-window (aggressive) detection ------------------
# These run ONLY when the user held the mode key while speaking. Because the button
# is an explicit "a mode keyword is coming" signal, matching here is DELIBERATELY
# loose (the false-positive cost is near zero) — prefixes, a mishearing map, and
# Levenshtein <= 2 — so "port"->prompt, "emal"->email, "leest"->list all catch.

# Canonical mode words for fuzzy matching. ("context" is NOT here — it stopped
# being a trigger word when the Context Island merged into the Hub; saying it
# is ordinary content now.)
_MODE_CANON = {
    "prompt": "prompt",
    "email": "email",
    "reply": "reply",
    "foreign": "foreign",
    "convert": "convert",
}

_MODE_PREFIX = (
    ("foreign", ("forei", "fore", "four", "fari", "fora", "farr")),
    ("prompt", ("prom", "praw", "prum", "prawn")),
    ("email", ("emai", "emal", "emi", "imai", "eema")),
    ("reply", ("repl", "resp", "repi", "repp")),
    ("convert", ("conv", "kanv", "cunv", "konv")),
)

# Direct mishearing -> mode (the recognizer's favourite slips for each keyword).
_MODE_MISHEAR = {
    "port": "prompt",
    "prom": "prompt",
    "prawn": "prompt",
    "promote": "prompt",
    "promo": "prompt",
    "prompts": "prompt",
    "prompted": "prompt",
    "print": "prompt",
    "prints": "prompt",
    "prince": "prompt",
    "pronto": "prompt",
    "emil": "email",
    "emails": "email",
    "emit": "email",
    "emo": "email",
    "rip": "reply",
    "replies": "reply",
    "reified": "reply",
    "foreigner": "foreign",
    "fern": "foreign",
    "foreigns": "foreign",
    "covert": "convert",
    "concert": "convert",
    "converted": "convert",
    "converts": "convert",
    "conversion": "convert",
}


# "context"/"contacts" must stay LITERAL forever (owner contract: "context"
# stopped being a trigger when the Context Island merged into the Deck). They
# sit dangerously close to "convert" — same "co" onset, fuzzy distance 2 — so
# they're excluded BEFORE any loose matching can claim them.
_MODE_STOPWORDS = {"context", "contexts", "contact", "contacts"}


def _aggressive_mode(tok: str) -> str:
    """Loose mode match for a single token spoken inside the button window. Returns
    a mode name ('prompt'/'email'/'reply'/'foreign'/'convert') or ''."""
    w = _norm(tok)
    if not w or w in _MODE_STOPWORDS:
        return ""
    if w in _MODE_CANON:
        return _MODE_CANON[w]
    if w in _MODE_MISHEAR:
        return _MODE_MISHEAR[w]
    if len(w) >= 3:
        for mode, prefixes in _MODE_PREFIX:
            for p in prefixes:
                if w.startswith(p) or (len(w) >= 4 and p.startswith(w)):
                    return mode
    # Levenshtein <= 2 against the canonical words (loose on purpose).
    best, bd = "", 99
    for cand, mode in _MODE_CANON.items():
        d = _fuzzy_dist(w, cand)
        if d < bd:
            bd, best = d, mode
    return best if bd <= 2 else ""


# ULTRA-aggressive 2-letter ONSET map, used ONLY for words spoken INSIDE the button
# window. The user deliberately held the key, so even a 2-letter onset is treated as
# the mode (this is why a clipped "prompt" mis-heard as "print"/"pro"/"prawn" still
# becomes prompt). Order = priority; first matching onset wins.
_ONSET_MODE = (
    ("prompt", ("pr", "po")),  # prompt, print, pro, port, prawn…
    ("email", ("em", "im", "ee")),  # email, e-mail, eemail…
    ("reply", ("re", "rep")),  # reply, respond…
    ("foreign", ("fo", "fa", "fr")),  # foreign, far in, fore…
    ("convert", ("co", "ko", "cu")),  # convert, covert, concert…
)


# Last-resort FIRST-LETTER map. The six modes have unique first letters, so inside the
# button window a single leading letter is enough to commit (the held key already means
# "a mode keyword is here, the mic may have mangled it"). "k" doubles for convert —
# the recognizer often hears a clipped hard-C as K.
_FIRST_LETTER = {
    "p": "prompt",
    "e": "email",
    "r": "reply",
    "f": "foreign",
    "c": "convert",
    "k": "convert",
}


def _window_mode(tok: str) -> str:
    """Ultra-aggressive match for a word spoken INSIDE the button window. Tries the
    precise matcher, then a 2-letter onset, then a bare FIRST LETTER — because the held
    key already tells us a mode keyword is here. Returns a mode name or ''."""
    w = _norm(tok)
    if not w or w in _MODE_STOPWORDS:
        return ""
    m = _aggressive_mode(tok)
    if m:
        return m
    if len(w) >= 2:
        for mode, onsets in _ONSET_MODE:
            for o in onsets:
                if w.startswith(o):
                    return mode
    return _FIRST_LETTER.get(w[0], "")  # last resort: first letter (unique per mode)


def convert_target(request):
    """Convert is a ROUTER, not a destination (owner contract): the words right
    after 'convert' may name another Smart Mode — 'convert to email …',
    'convert into a prompt …' — and then the result belongs to THAT mode.
    Returns (mode, request-with-routing-words-stripped) when a mode is named
    directly after a routing preposition (to/into/as), else (None, request):
    the generic format converter handles 'to JSON', 'into a table', 'to miles'.
    The preposition requirement keeps content like 'convert this email to
    French' in the generic lane — 'email' there is material, not a target."""
    toks = (request or "").split()
    saw_prep = False
    for i, tok in enumerate(toks[:5]):
        w = _norm(tok)
        # prepositions FIRST — "to" is also a lead-in word and must not be
        # swallowed by the skip below before it can arm the router
        if w in ("to", "into", "as"):
            saw_prep = True
            continue
        if not w or w in _LEAD_INS or w in ("a", "an", "the"):
            continue
        # routing REQUIRES the preposition: "convert to email …" routes, but
        # "convert this to prose" routes; "convert my notes to prose" keeps the
        # noun as material in the generic lane (needs the to/into/as preposition)
        if saw_prep:
            cand = _aggressive_mode(tok)
            if cand and cand != "convert":
                return cand, " ".join(toks[i + 1:]).strip()
        break
    return None, request or ""


def _mode_enabled(mode, modes):
    # Text is always available; EVERY Smart Mode — including Foreign — obeys the
    # user's per-mode toggle (Settings shows a Foreign checkbox, so it must work;
    # it was previously hard-wired on, making that toggle a no-op). Default True,
    # so behaviour is unchanged unless the user deliberately turns a mode off.
    if mode == "text":
        return True
    return (modes or {}).get(mode, True)


def _is_scaffold(tok):
    """Leading command words that are NOT content: lead-ins only. ("context"/
    "clipboard"/count words used to be stripped here — they are ordinary CONTENT
    now that context is no longer a spoken trigger.)"""
    w = _norm(tok)
    return (not w) or w in _LEAD_INS


def _build_content(tokens, kw_pos):
    """Literal content = the whole transcript MINUS the keyword token and any
    leading lead-in words before the first real word. Everything from the first
    content word onward is kept verbatim — mode words there are literal and
    never re-scanned."""
    out = []
    started = False  # have we reached the first real content word?
    for i, tok in enumerate(tokens):
        if i == kw_pos:
            started = True  # keyword ends the leading scaffold; drop the keyword itself
            continue
        if not started:
            if _is_scaffold(tok):
                continue  # drop lead-in scaffolding
            started = True
        out.append(tok)
    return _strip_lead(" ".join(out))


def annotate_uncertain(raw, words, threshold=0.8):
    """Mark low-confidence transcription words with slash alternatives (a//b//c).
    The AI resolves these using surrounding context. `words` is a list of dicts
    with 'word', 'start', 'end', 'prob' keys (from faster-whisper). Only words
    below `threshold` confidence get annotated. Alternatives come from the mishear
    map (reverse lookup: which canonical mode does this word mishear as?) and
    fuzzy matching against _MODE_CANON. Returns the transcript with annotations."""
    if not words or not raw:
        return raw
    # Build reverse mishear map: misheard_word -> canonical_mode
    _REVERSE_MISHEAR = {}
    for misheard, canonical in _MODE_MISHEAR.items():
        _REVERSE_MISHEAR.setdefault(misheard.lower(), []).append(canonical)
    # Locate each whisper token at its OWN position in `raw` (forward cursor +
    # word-boundary match), collect edits, then splice right-to-left. The old
    # `annotated.replace(word, repl, 1)` was an unbounded substring replace at the
    # FIRST textual occurrence — so an uncertain "rip" annotated INSIDE "strip",
    # and a repeated word annotated the wrong instance.
    edits = []  # (start, end, replacement)
    cursor = 0
    for w in words:
        word = w.get("word", "").strip()
        core = word.strip(".,!?;:")
        if not core:
            continue
        try:
            m = re.compile(r"\b" + re.escape(core) + r"\b", re.IGNORECASE).search(
                raw, cursor
            )
        except re.error:
            m = None
        if not m:
            # Not found ahead of the cursor (e.g. vocab-substituted) — skip but
            # keep the cursor so later tokens still match in order.
            continue
        cursor = m.end()
        prob = w.get("prob", 1.0)
        if prob >= threshold or len(core) < 2:
            continue
        alts = []
        lw = core.lower()
        if lw in _REVERSE_MISHEAR:
            alts.extend(_REVERSE_MISHEAR[lw])
        fm = _fuzzy_match(lw, _MODE_CANON)
        if fm and fm not in alts:
            alts.append(fm)
        if alts:
            heard = raw[m.start():m.end()]  # preserve original casing
            edits.append((m.start(), m.end(), heard + "//" + "//".join(alts[:3])))
    out = raw
    for start, end, rep in sorted(edits, reverse=True):
        out = out[:start] + rep + out[end:]
    return out


def detect_mode_button(raw, modes=None, window_words=None):
    """LITERAL-MODE detection — only runs when the mode key was held. The mode is decided
    SOLELY from the COMMAND REGION (the words spoken inside the shift window, or the
    leading tokens if we have no timestamps). Everything else is literal content and is
    NEVER scanned for mode words, so an 'email'/'reply' spoken inside the body cannot
    switch modes. The mode is LOCKED on the first keyword found.

    Returns (mode, request, clip_count).
      mode       — text / prompt / email / reply / foreign / convert
      request    — the literal content, with lead-in scaffolding stripped
      clip_count — 0, except Reply mode (1 = its latest-clipboard source)

    NOTE: "context" is NOT a trigger word (the Context Island merged into
    History, ctrl+alt+d). Saying "context" here is ordinary literal content.
    """
    modes = modes or {}
    text = (raw or "").strip()
    if not text:
        return "text", "", 0
    tokens = text.split()

    # Command region: the shift-window words if known (most precise), else first 8 tokens.
    region = list(window_words) if window_words else tokens[:8]
    # Inside a real button window, match ULTRA-aggressively (2-letter onsets) — the held
    # key is high-confidence intent. Without a window we only have the leading tokens, so
    # stay conservative to avoid false triggers.
    match = _window_mode if window_words else _aggressive_mode

    mode = ""
    for tok in region:
        w = _norm(tok)
        if not w:
            continue
        if w in _LEAD_INS:
            continue
        cand = match(tok)
        if cand and _mode_enabled(cand, modes):
            mode = cand
            break
        if not window_words:
            break  # first meaningful, non-keyword word (no window) -> not a command
        # with a real window, keep scanning the window words for the keyword

    if not mode:
        return "text", text, 0

    # Locate the keyword in the full transcript (first token that maps to it), then build
    # the literal content around it. Use the same matcher, bounded to the leading tokens
    # so a body word with the same onset can't be mistaken for the keyword.
    kw_pos = -1
    bound = max(len(region), 8)
    for i, tok in enumerate(tokens[:bound]):
        if _norm(tok) not in _LEAD_INS and match(tok) == mode:
            kw_pos = i
            break
    request = _build_content(tokens, kw_pos)
    clip = 1 if mode == "reply" else 0
    return mode, request, clip


# ------------------------------ Prompt mode ----------------------------------


def _role_for(req: str):
    r = req.lower()
    # Check task-type keywords FIRST so "explain how to write an email" → teacher,
    # not communicator. Task intent overrides domain.
    if any(
        k in r
        for k in [
            "explain",
            "what is",
            "how do",
            "how does",
            "why",
            "teach",
            "summarize",
            "summary",
            "difference between",
        ]
    ):
        return (
            "a clear and knowledgeable teacher",
            "- Explain in plain language, build from the fundamentals, and give a concrete example.",
        )
    if any(
        k in r
        for k in [
            "code",
            "function",
            "script",
            "program",
            "bug",
            "python",
            "javascript",
            "typescript",
            "api",
            "regex",
            "sql",
            "html",
            "css",
            "refactor",
        ]
    ):
        return (
            "an expert senior software engineer",
            "- Provide complete, runnable code with brief comments, then a short explanation.",
        )
    if any(k in r for k in ["email", "message", "reply", "letter", "memo", "slack"]):
        return (
            "an expert communicator",
            "- Keep a clear, professional, friendly tone; make it concise and well-structured.",
        )
    if any(
        k in r
        for k in [
            "poem",
            "story",
            "essay",
            "blog",
            "article",
            "song",
            "poetry",
            "write",
            "draft",
            "tagline",
            "caption",
        ]
    ):
        return (
            "an expert writer",
            "- Use vivid, engaging language suited to the format and audience.",
        )
    if any(
        k in r
        for k in [
            "plan",
            "organize",
            "schedule",
            "strategy",
            "steps",
            "roadmap",
            "itinerary",
            "checklist",
        ]
    ):
        return (
            "an expert planner",
            "- Provide a clear, ordered, actionable plan with brief rationale for each step.",
        )
    return ("a helpful, highly capable assistant", "")


def build_prompt(request: str) -> str:
    req = re.sub(r"\s+", " ", _remove_fillers(request or "").strip())
    if not req:
        return ""
    display = _cap_first(req)
    if display[-1] not in ".?!":
        display += "."
    # Neutralize markdown headers in user content to prevent prompt injection
    display = display.replace("#", "\\#")
    role, extra = _role_for(req)
    lines = [
        f"You are {role}.",
        "",
        "# Task",
        display,
        "",
        "# Instructions",
        "- Think step by step and reason carefully before you answer.",
        "- Be specific and accurate. If anything is ambiguous, state your assumptions and continue.",
        "- Keep it concise — no filler, no repetition, no preamble.",
    ]
    if extra:
        lines.append(extra)
    lines += [
        "",
        "# Output",
        "Give a clear, well-structured response; use headings or bullet points where they help.",
    ]
    return "\n".join(lines)


# ------------------------------ Email mode -----------------------------------


def build_email(request: str, user_name: str = "") -> str:
    rem = (request or "").strip()

    # Optional recipient: "to <Name>"
    recipient = None
    m = re.match(r"(?i)^to\s+([A-Za-z][A-Za-z'\-]*[A-Za-z])[\s,]*", rem)
    if m:
        recipient = m.group(1)
        rem = rem[m.end() :].strip()

    # Optional subject -- only when clearly delimited, so we never eat the body.
    # "about <subject>, <body>" or an explicit "subject <subject>".
    subject = None
    m = re.match(r"(?i)^(?:about|regarding|re)\s+(.+?)\s*[,.;:]\s+(.+)$", rem, re.S)
    if m:
        subject, rem = m.group(1).strip(), m.group(2).strip()
    else:
        m2 = re.match(r"(?i)^subject\s+(.+?)(?:\s*[,.;:]\s+(.*))?$", rem, re.S)
        if m2:
            subject = m2.group(1).strip()
            rem = (m2.group(2) or "").strip()

    # Drop a leading connective like "saying" / "tell them".
    rem = re.sub(
        r"(?i)^(?:saying|to say|and say|that says?|just say|"
        r"telling (?:them|him|her)|tell (?:them|him|her))\b[\s,:]*",
        "",
        rem,
    ).strip()

    body = format_transcript(rem) if rem.strip() else ""
    # Capitalize first letter only, preserving the rest of the name's casing
    # (capitalize() lowercases everything after the first char, mangling
    # names like "McDonald" → "Mcdonald" or "O'Brien" → "O'brien").
    greeting = f"Hi {recipient[0].upper() + recipient[1:]}," if recipient else "Hi,"

    parts = []
    if subject:
        parts.append("Subject: " + _cap_first(subject))
    parts.append(greeting)
    if body:
        parts.append(body)
    signoff = "Best regards,"
    if user_name.strip():
        signoff += "\n" + user_name.strip()
    parts.append(signoff)
    return "\n\n".join(parts)


# --------------------------- unified dispatch --------------------------------


# ---- Segment confidence gating ---------------------------------------------
# faster-whisper exposes per-segment diagnostics: avg_logprob, compression_ratio,
# no_speech_prob. These are cheap, pre-computed signals that identify segments
# Whisper hallucinated over silence/noise. Use them to gate segments before
# they reach the user.

DEFAULT_GATE_THRESHOLDS = {
    "no_speech_prob": 0.6,       # drop segments where silence probability > 0.6
    "compression_ratio": 2.4,    # flag segments with high repetition (looping)
    "avg_logprob": -1.0,         # flag segments with very low confidence
}


def gate_segment(seg, thresholds=None):
    """Evaluate one faster-whisper segment against confidence thresholds.
    Returns (keep: bool, reason: str). A dropped segment returns keep=False
    with an explanatory reason string; a kept one returns keep=True, reason=''."""
    t = thresholds or DEFAULT_GATE_THRESHOLDS
    try:
        nsp = float(getattr(seg, "no_speech_prob", 0.0) or 0.0)
        if nsp > t.get("no_speech_prob", 0.6):
            return False, f"no_speech_prob={nsp:.2f}"
        cr = float(getattr(seg, "compression_ratio", 0.0) or 0.0)
        if cr > t.get("compression_ratio", 2.4):
            return False, f"compression_ratio={cr:.1f}"
        alp = float(getattr(seg, "avg_logprob", 0.0) or 0.0)
        if alp < t.get("avg_logprob", -1.0):
            return False, f"avg_logprob={alp:.2f}"
    except Exception:
        pass  # malformed segment metadata — keep it rather than drop
    return True, ""


def gate_segments(segments, thresholds=None):
    """Filter a list of faster-whisper segments through confidence gating.
    Returns (kept_segments, dropped_count, flags) where flags is a list of
    (text, reason) for flagged segments that were kept but marked."""
    if not segments:
        return [], 0, []
    t = thresholds or DEFAULT_GATE_THRESHOLDS
    kept, flags, dropped = [], [], 0
    for seg in segments:
        ok, reason = gate_segment(seg, t)
        if ok:
            kept.append(seg)
            # Still flag segments that are borderline
            try:
                cr = float(getattr(seg, "compression_ratio", 0.0) or 0.0)
                alp = float(getattr(seg, "avg_logprob", 0.0) or 0.0)
                if cr > t.get("compression_ratio", 2.4) * 0.7 or alp < t.get("avg_logprob", -1.0) * 0.7:
                    text = getattr(seg, "text", "") or ""
                    flags.append((text, f"low_confidence cr={cr:.1f} alp={alp:.2f}"))
            except Exception:
                pass
        else:
            dropped += 1
            print(f"[gate] dropped segment: {reason}")
    return kept, dropped, flags


# ---- ML punctuation restoration (optional, lazy-loaded) --------------------
# deepmultilingualpunctuation (FullStop) provides state-of-the-art punctuation
# restoration via a BERT model. It's opt-in (gated by ml_punctuation_enabled in
# settings) and lazy-loaded (the ~1.3 GB model is only imported when first
# needed). Falls back gracefully to rule-based punctuation when unavailable.

_ml_punctuation_model = None


def _ml_punctuation_available():
    """Check whether the ML punctuation model can be loaded (transformers present)."""
    try:
        from pipeline.stage_punctuation import _HAS_TRANSFORMERS
        return _HAS_TRANSFORMERS
    except Exception:
        return False


def _ensure_ml_punctuation():
    """Lazy-load the ML punctuation model. Returns the model or None."""
    global _ml_punctuation_model
    if _ml_punctuation_model is not None:
        return _ml_punctuation_model
    try:
        from pipeline.stage_punctuation import PunctuationStage
        stage = PunctuationStage()
        if stage.available:  # triggers lazy load of the BERT pipeline
            _ml_punctuation_model = stage
            print("[ml-punct] punctuation model loaded (pipeline PunctuationStage)")
            return _ml_punctuation_model
        else:
            print("[ml-punct] could not load model: PunctuationStage not available")
            _ml_punctuation_model = False  # sentinel: tried and failed
            return None
    except Exception as e:
        print(f"[ml-punct] could not load model: {e}")
        _ml_punctuation_model = False  # sentinel: tried and failed
        return None


def ml_restore_punctuation(text: str, enabled=True) -> str:
    """Restore punctuation using ML model (PunctuationStage) when
    enabled and available. Falls back to rule-based when disabled/unavailable.
    Returns the punctuated text."""
    global _ml_punctuation_model
    if not text or not text.strip():
        return text
    if not enabled:
        return text  # caller should apply rule-based punctuation separately
    model = _ensure_ml_punctuation()
    if not model:
        return text  # fallback: rule-based handles it
    try:
        result = model.process(text.strip())
        return result if result else text
    except Exception as e:
        print(f"[ml-punct] restore failed: {e}")
        _ml_punctuation_model = False
        return text


def process(raw: str, settings_modes=None, user_name="", format_enabled=True,
            commands: bool = True, auto: bool = False,
            ml_punctuation: bool = False):
    """Local mode routing for the offline builder (the no-AI floor).
    `commands=False` (what the live app passes) disables spoken-command
    formatting inside the cleanup — see format_transcript. `ml_punctuation=True`
    enables optional ML-based punctuation restoration (deepmultilingualpunctuation,
    lazy-loaded). Returns (mode, text)."""
    mode, request, _clip, _ = detect_mode(raw, settings_modes)
    if mode == "prompt":
        out = build_prompt(request)
    elif mode == "email":
        out = build_email(request, user_name)
    elif mode == "reply":
        out = format_transcript(request, commands=commands)
    else:
        out = (format_transcript(raw, commands=commands)
               if format_enabled else raw.strip())
    if not out:  # fall back so nothing is silently dropped
        out = (format_transcript(raw, commands=commands)
               if format_enabled else raw.strip())
        mode = "text"
    # Apply ML punctuation restoration as a post-pass when enabled
    if ml_punctuation and out:
        restored = ml_restore_punctuation(out, enabled=True)
        if restored and restored != out:
            out = restored
    return mode, out
