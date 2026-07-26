#!/usr/bin/env python3
"""Foreign Mode — local phonetic token-boosting engine for Mumble. No AI / no network.

THE PROBLEM. An English-tuned speech model force-maps foreign acoustics onto the
nearest English word: the Arabic "juz" is decoded as "just", the Spanish "amigo"
survives but "gracias" becomes "grassy us", "croissant" lands as "kwasont". The
recognizer never emits the foreign token — it emits the closest English string.

THE FIX (entirely on-device). When Foreign Mode is on, we monitor the transcript
stream for tokens that are phonetically a known mis-mapping of a term in the user's
SECONDARY-LANGUAGE packs, score each anomaly, and either:
  • BOOST  (high confidence)   — intercept the English typo and replace it with the
                                 correct foreign term, flagged in the stream as
                                 `//Juz` (leading-slash flag = an applied boost), or
  • OFFER  (medium confidence) — annotate `heard//Juz` (infix slashes = a candidate)
                                 for the cloud AI / the user to resolve from context, or
  • LEAVE  (no plausible term) — pass the token through untouched.

This is the LOCAL counterpart to ai.cerebras_foreign(): islamic_terms.annotate_foreign
only ever OFFERS candidates and leans on the cloud to decide. This module DECIDES on
device for the high-confidence cases, so an offline user still gets "Juz", and the
cloud lane (when present) only sees the residual medium-confidence candidates.

Entry points
  boost(text, languages, word_probs=None) -> BoostResult   (the pipeline)
  strip_flags(text)                       -> clean text (`//Juz` -> `Juz`)
  phonetic_signature(word)                -> cross-lingual consonant skeleton
  language_pack(name)                     -> a LanguagePack (built lazily, cached)

All deterministic -> unit-tested in test_foreign_boost.py.

Design notes
  • Precision driver = the EXPLICIT mis-hearing table (a curated list of the
    English strings the recognizer actually produces for each term). Recall driver
    = the phonetic signature + edit distance, for unlisted slips. Same two-layer
    shape as formatting._term_confidence / islamic_terms.candidates.
  • The Arabic pack REUSES islamic_terms.ISLAMIC_TERMS verbatim (~250 entries), so
    Foreign Mode inherits that table instead of forking it. Spanish/French are
    compact starter packs; packs are data, so adding a language is adding a list.
  • A token that is itself a common English word is NEVER silently boosted — the
    same guard islamic_terms uses to stop "the imam saw a shark" -> "...a Shirk".
    Such tokens can still be OFFERED as a candidate (the AI/user judges context),
    and are only auto-boosted when the acoustic slot was low-confidence (the
    recognizer itself was unsure) AND a gateway term anchors the foreign context.
"""

import math
import re

import islamic_terms


# --------------------------- phonetic signature ------------------------------
# A compact, dependency-free consonant skeleton tuned for cross-lingual matching:
# it collapses the spelling variations that separate a foreign term from its
# English mis-mapping (j/g, k/c/q, s/z, f/v, silent vowels) so "juz"/"juice"/
# "juss" and "gracias"/"grassy us" land on the same key. It is intentionally
# lossy — paired with an edit-distance bound it is accurate enough with zero deps.
# (Mirrors formatting._phonetic_key; kept self-contained so the module stands alone.)

_SIG_DIGRAPHS = [
    ("tch", "x"), ("sch", "sk"), ("chr", "kr"), ("sh", "x"), ("ch", "x"),
    ("ph", "f"), ("th", "0"), ("gh", ""), ("ck", "k"), ("qu", "k"),
    ("ll", "l"), ("rr", "r"), ("zz", "s"), ("ss", "s"),
]


def phonetic_signature(word: str) -> str:
    """Cross-lingual consonant skeleton. Leading vowel normalises to 'a'; interior
    vowels drop; voiced/voiceless and hard/soft pairs fold together. Empty for a
    token with no usable letters."""
    w = re.sub(r"[^a-z]", "", (word or "").lower())
    if not w:
        return ""
    for a, b in _SIG_DIGRAPHS:
        w = w.replace(a, b)
    # soft c/g before front vowels, else hard; fold the voiced/voiceless pairs.
    w = re.sub(r"c(?=[eiy])", "s", w)
    w = w.replace("c", "k").replace("q", "k")
    w = re.sub(r"g(?=[eiy])", "j", w)
    w = w.replace("x", "ks").replace("z", "s").replace("v", "f")
    w = w.replace("w", "").replace("h", "").replace("y", "")
    if not w:
        return ""
    head = "a" if w[0] in "aeiou" else w[0]
    out = head + re.sub(r"[aeiou]", "", w[1:])
    return re.sub(r"(.)\1+", r"\1", out)


def _lev(a: str, b: str) -> int:
    """Levenshtein distance (small strings; cheap)."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        cur = [i + 1]
        for j, cb in enumerate(b):
            cur.append(min(prev[j + 1] + 1, cur[j] + 1,
                           prev[j] + (0 if ca == cb else 1)))
        prev = cur
    return prev[-1]


# ----------------------------- language packs --------------------------------
# A pack is a list of (canonical_term, [known_english_mishearings]) plus the set
# of "gateway" terms — unmistakably-foreign words with no everyday-English reading
# whose presence proves we are in that language's context (so we can be bolder).

# Spanish + French STARTER packs — the common loanwords/idioms an English speaker
# interjects. Deliberately small and high-precision; extend by appending entries.
_SPANISH = [
    ("amigo", ["amigo", "ah me go", "a me go"]),
    ("gracias", ["gracias", "grassy us", "grass yes", "gracy us"]),
    ("hola", ["hola", "ola", "oh la"]),
    ("señor", ["senor", "sen your", "seh nyor"]),
    ("mañana", ["manana", "ma nana", "man yana"]),
    ("fiesta", ["fiesta", "fee esta"]),
    ("siesta", ["siesta", "see esta"]),
    ("jalapeño", ["jalapeno", "halla pen yo", "hala pain yo"]),
    ("por favor", ["por favor", "pour fa vor", "poor favor"]),
    ("vamonos", ["vamonos", "va mo nos", "bomb a nose"]),
    ("pueblo", ["pueblo", "pweblo", "pweb low"]),
    ("quinceañera", ["quinceanera", "keen sin yera"]),
]
_SPANISH_GATEWAYS = {"amigo", "gracias", "hola", "senor", "manana", "jalapeno"}

_FRENCH = [
    ("croissant", ["croissant", "kwasont", "kwa son", "cross ant"]),
    ("café", ["cafe", "ca fey"]),
    ("merci", ["merci", "mare see", "mer see"]),
    ("bonjour", ["bonjour", "bon zhoor", "bone jour"]),
    ("rendezvous", ["rendezvous", "ron day voo", "rond a voo"]),
    ("entrepreneur", ["entrepreneur", "on tra pre nur"]),
    ("déjà vu", ["deja vu", "day zha voo", "dayja voo"]),
    ("faux pas", ["faux pas", "foe pa", "fo paw"]),
    ("hors d'oeuvre", ["hors d'oeuvre", "or derv", "or doovra"]),
    ("voilà", ["voila", "vwa la", "wa la"]),
    ("touché", ["touche", "too shay"]),
    ("fiancé", ["fiance", "fee on say"]),
]
_FRENCH_GATEWAYS = {"croissant", "bonjour", "merci", "rendezvous", "voila"}


class LanguagePack:
    """A secondary-language lexicon with the indices the boost pipeline needs:
    mishearing -> canonical, phonetic-signature -> canonicals, and the gateway set."""

    def __init__(self, name, entries, gateways=None):
        self.name = name
        self.entries = entries
        self.by_mishearing = {}     # heard spelling (no spaces) -> canonical
        self.by_phrase = {}         # exact multi-word hearing -> canonical
        self.by_signature = {}      # phonetic signature -> set(canonical)
        self.gateways = set()
        for canonical, misses in entries:
            forms = [canonical.lower()] + [m.lower() for m in misses]
            for form in forms:
                normalised = " ".join(form.split())
                if " " in normalised:
                    self.by_phrase.setdefault(normalised, canonical)
                flat = form.replace(" ", "")
                self.by_mishearing.setdefault(flat, canonical)
                sig = phonetic_signature(flat)
                if len(sig) >= 4:   # short skeletons collide — index long ones only
                    self.by_signature.setdefault(sig, set()).add(canonical)
        # Gateways are matched by EXACT lowercased word (no signature fuzzing —
        # short signatures cause spurious "context" hits). Mirrors
        # islamic_terms._has_islamic_context.
        self.gateways = {g.lower() for g in (gateways or set())}


def _build_arabic_pack():
    """Reuse the curated ~250-term Islamic/Arabic table verbatim (single source of
    truth — islamic_terms.ISLAMIC_TERMS). Gateways come from the same module."""
    gw = set(islamic_terms._ISLAMIC_GATEWAYS) | set(islamic_terms._GATEWAY_MISHEARINGS)
    return LanguagePack("arabic", islamic_terms.ISLAMIC_TERMS, gw)


_PACK_BUILDERS = {
    "arabic": _build_arabic_pack,
    "spanish": lambda: LanguagePack("spanish", _SPANISH, _SPANISH_GATEWAYS),
    "french": lambda: LanguagePack("french", _FRENCH, _FRENCH_GATEWAYS),
}

# Aliases so settings like "español"/"fr"/"ar" resolve to a pack.
_PACK_ALIASES = {
    "ar": "arabic", "arab": "arabic", "islamic": "arabic",
    "es": "spanish", "espanol": "spanish", "español": "spanish", "castilian": "spanish",
    "fr": "french", "francais": "french", "français": "french",
}

_PACK_CACHE = {}


def language_pack(name):
    """Return the built LanguagePack for `name` (cached), or None if unknown.
    Building the Arabic pack indexes ~250 terms, so packs are built once and reused."""
    key = str(name or "").strip().lower()
    key = _PACK_ALIASES.get(key, key)
    if key not in _PACK_BUILDERS:
        return None
    if key not in _PACK_CACHE:
        _PACK_CACHE[key] = _PACK_BUILDERS[key]()
    return _PACK_CACHE[key]


def available_languages():
    """The language names the engine has packs for (for Settings / onboarding)."""
    return sorted(_PACK_BUILDERS)


# ------------------------ common-English guard set ---------------------------
# A token that is itself an everyday English word is never silently boosted —
# "I just left" must stay "just". Reuse islamic_terms' vetted list and add the
# function words most likely to collide with a foreign mishearing.
_COMMON_ENGLISH = set(islamic_terms._COMMON_ENGLISH) | {
    "just", "some", "sum", "the", "a", "an", "and", "or", "of", "to", "in", "on",
    "is", "it", "as", "at", "be", "by", "so", "we", "he", "i", "you", "for",
    "are", "was", "with", "from", "this", "that", "they", "them", "have", "will",
    "fix",
    "your", "but", "not", "if", "or", "us", "yes", "no", "ate", "ola",
}

# Pure grammatical glue — never even OFFER a candidate for these (keeps the slash
# notation meaningful), mirroring islamic_terms._FOREIGN_STOP.
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "as", "at", "be",
    "by", "on", "so", "we", "he", "i", "you", "for", "are", "was", "with", "from",
    "this", "that", "they", "them", "have", "will", "your", "but", "not", "if",
}

_WORD_RE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)*", re.UNICODE)


# ------------------------------ scoring --------------------------------------

# Confidence tiers a single token can earn against a pack.
_BOOST = "boost"        # high — auto-replace, flag as //Term
_OFFER = "offer"        # medium — annotate heard//Term for downstream resolution
_NONE = ""              # leave the token alone


def _match_token(low, packs):
    """Best (tier_term_lang) for a single lowercased token across `packs`.
    Returns (canonical, lang, base_tier) or (None, None, '').

    base_tier is BEFORE the common-English / acoustic gating in `boost`:
      • an EXACT known-mishearing hit  -> _BOOST  (the recognizer's known slip)
      • a phonetic-signature hit within a length-scaled edit bound -> _OFFER
    """
    if len(low) < 3:
        return None, None, _NONE
    sig = phonetic_signature(low)
    best = (None, None, _NONE, 99)  # term, lang, tier, distance
    for pack in packs:
        # 1) exact known mishearing — highest precision.
        canonical = pack.by_mishearing.get(low)
        if canonical:
            # Already the correct spelling ("juz", "amigo") — never re-flag it.
            if canonical.lower() == low:
                return canonical, pack.name, _NONE
            return canonical, pack.name, _BOOST
        # 2) phonetic-signature recall path — for NON-English tokens only, and
        #    only on an EXACT match of a signature >= 4 chars long. Short
        #    skeletons collide ("saw"/"Sa'i" both reduce to "s"); and a common
        #    English word may ONLY match via the curated exact-mishearing table
        #    above, never by fuzzy phonetics. This is deliberately high-precision
        #    — the exact table is the recall workhorse; the cloud lane mops up
        #    anything subtle this leaves as plain text.
        if low in _COMMON_ENGLISH or len(sig) < 4:
            continue
        for term in pack.by_signature.get(sig, ()):
            if term.lower() == low:                # already the correct spelling
                return term, pack.name, _NONE
            if best[3] > 0:
                best = (term, pack.name, _OFFER, 0)
    return best[0], best[1], best[2]


def _context_strength(low_tokens, packs):
    """How strongly the utterance is anchored in a foreign context: the count of
    gateway terms present (correct OR an unmistakable mishearing). A gateway is a
    foreign word with no everyday-English reading, so its presence lets us boost a
    homophone like "just"->Juz that we would otherwise only offer."""
    gates = set()
    for pack in packs:
        gates |= pack.gateways
    return sum(1 for low in low_tokens if low in gates)


# ------------------------------ result types ---------------------------------

class Boost:
    """One applied or offered foreign-term correction."""

    __slots__ = ("index", "heard", "term", "lang", "tier", "low_conf")

    def __init__(self, index, heard, term, lang, tier, low_conf):
        self.index = index          # token position in the stream
        self.heard = heard          # the English string the recognizer produced
        self.term = term            # the canonical foreign term
        self.lang = lang            # which pack matched
        self.tier = tier            # 'boost' (applied) | 'offer' (candidate)
        self.low_conf = low_conf    # was the acoustic slot low-confidence?

    def __repr__(self):
        return (f"Boost({self.heard!r}->{self.term!r} {self.lang} "
                f"{self.tier}{' lowconf' if self.low_conf else ''})")


class BoostResult:
    """The pipeline output. `text` is the stream with applied boosts written as
    `//Term` and offered candidates as `heard//Term`; `applied`/`offered` list what
    happened so a caller can log, undo, or show the user what changed."""

    __slots__ = ("text", "applied", "offered")

    def __init__(self, text, applied, offered):
        self.text = text
        self.applied = applied      # list[Boost] auto-replaced (//Term)
        self.offered = offered      # list[Boost] annotated (heard//Term)

    @property
    def changed(self):
        return bool(self.applied or self.offered)


# ------------------------------ the pipeline ---------------------------------

def boost(text, languages=("arabic",), word_probs=None,
          conf_threshold=0.6, flag_prefix="//", mark_only=False):
    """Run the local Foreign-Mode token-boosting pipeline over `text`.

    languages     — secondary-language pack names from Settings.foreign_languages.
    word_probs     — optional per-token acoustic confidence, aligned to the word
                     tokens in `text` (faster-whisper word.prob). When supplied, a
                     low-confidence slot (< conf_threshold) is treated as a strong
                     anomaly signal: the recognizer itself was unsure, so a homophone
                     of a foreign term is more likely a forced mis-map. Without it
                     the pipeline runs on phonetics + context alone (still useful).
    mark_only      — when True, NOTHING is auto-applied: every match is demoted to an
                     OFFER (`heard//Term`). This is the CLOUD-AUGMENTED mode — the raw
                     transcript + `//` uncertainty markers go straight to the cloud,
                     which makes the final call (no on-device resolution). The default
                     (False) is the LOCAL-ONLY mode: high-confidence matches are applied
                     on-device as `//Term`. See cloud-dominance routing in local_engine.
    Returns a BoostResult. Already-annotated tokens are left untouched while
    other tokens are still processed.
    """
    if not text or not text.strip():
        return BoostResult(text, [], [])
    if isinstance(languages, str):
        languages = [languages]
    packs = [p for p in (language_pack(n) for n in (languages or [])) if p]
    if not packs:
        return BoostResult(text, [], [])

    try:
        conf_threshold = float(conf_threshold)
    except (TypeError, ValueError, OverflowError):
        conf_threshold = 0.6
    if not math.isfinite(conf_threshold):
        conf_threshold = 0.6

    toks = list(_WORD_RE.finditer(text))
    low_tokens = [m.group(0).lower() for m in toks]
    context = _context_strength(low_tokens, packs)

    applied, offered, edits = [], [], []
    consumed_spans = []

    # Match the curated multi-word hearings before individual tokens.  The old
    # index flattened spaces (``"grassy us"`` -> ``"grassyus"``) but then only
    # ever looked up one token at a time, making every multi-word entry dead.
    phrase_hits = []
    for pack in packs:
        # The inherited Arabic table contains deliberately broad historical
        # phrases (for example ordinary English bigrams); islamic_terms keeps
        # those out of its live multi-word pass for precision.  Only the small,
        # curated language-pack phrase tables are safe to activate here.
        if pack.name == "arabic":
            continue
        for heard_phrase, term in pack.by_phrase.items():
            if heard_phrase.casefold() == term.casefold():
                continue  # already the canonical spelling
            body = r"\s+".join(re.escape(p) for p in heard_phrase.split())
            pattern = re.compile(r"(?<!\w)" + body + r"(?!\w)", re.IGNORECASE)
            for match in pattern.finditer(text):
                phrase_hits.append((match.start(), match.end(), pack, term, match))
    phrase_hits.sort(key=lambda hit: (hit[0], -(hit[1] - hit[0])))
    for start, end, pack, term, match in phrase_hits:
        if any(start < used_end and end > used_start
               for used_start, used_end in consumed_spans):
            continue
        if text[end:end + 2] == "//" or text[max(0, start - 2):start] == "//":
            continue
        heard = match.group(0)
        rendered = _match_case(heard, term)
        token_index = next(
            (idx for idx, tok in enumerate(toks) if tok.start() >= start), 0
        )
        tier = _OFFER if mark_only else _BOOST
        boost_item = Boost(token_index, heard, term, pack.name, tier, False)
        if tier == _BOOST:
            edits.append((start, end, flag_prefix + rendered))
            applied.append(boost_item)
        else:
            edits.append((start, end, heard + "//" + rendered))
            offered.append(boost_item)
        consumed_spans.append((start, end))

    for i, m in enumerate(toks):
        if any(m.start() < end and m.end() > start
               for start, end in consumed_spans):
            continue
        # Idempotency is token-local.  A global ``"//" in text`` guard made an
        # unrelated URL such as https://example.com disable Foreign Mode for
        # the entire utterance.
        if text[m.end():m.end() + 2] == "//" or \
                text[max(0, m.start() - 2):m.start()] == "//":
            continue
        heard = m.group(0)
        low = heard.lower()
        if low in _STOP:
            continue
        term, lang, base = _match_token(low, packs)
        if not term or base == _NONE:
            continue

        prob = None
        if word_probs is not None and i < len(word_probs):
            try:
                prob = float(word_probs[i])
            except (TypeError, ValueError):
                prob = None
        low_conf = prob is not None and prob < conf_threshold

        is_common = low in _COMMON_ENGLISH
        tier = base

        # ---- decision gating (the heart of the boost) ----
        if is_common:
            # A homophone of an ordinary English word. NEVER auto-boost it on
            # phonetics alone — only when the recognizer was unsure of the slot
            # AND the utterance is anchored by a gateway term. With SOME foreign
            # signal (a gateway present, or a low-confidence slot) we may still
            # OFFER it for the AI/user to judge. With NO foreign signal at all,
            # leave the plain-English word completely alone ("I just left").
            if base == _BOOST and low_conf and context >= 1:
                tier = _BOOST
            elif context >= 1 or low_conf:
                tier = _OFFER
            else:
                continue
        else:
            # Not an English word. An exact known mishearing boosts; a fuzzy
            # phonetic-only hit stays an offer unless context + low-confidence
            # corroborate it.
            if base == _OFFER and context >= 1 and low_conf:
                tier = _BOOST

        # CLOUD-AUGMENTED mode: never apply on-device — demote to an offer so the
        # raw transcript + `//` markers reach the cloud, which decides.
        if mark_only and tier == _BOOST:
            tier = _OFFER

        # Render the casing of the canonical term to match the heard token.
        rendered = _match_case(heard, term)
        b = Boost(i, heard, term, lang, tier, bool(low_conf))
        if tier == _BOOST:
            edits.append((m.start(), m.end(), flag_prefix + rendered))
            applied.append(b)
        else:
            edits.append((m.start(), m.end(), f"{heard}//{rendered}"))
            offered.append(b)

    # splice right-to-left so earlier offsets stay valid
    out = text
    for start, end, rep in sorted(edits, key=lambda e: e[0], reverse=True):
        out = out[:start] + rep + out[end:]
    return BoostResult(out, applied, offered)


def _match_case(heard, term):
    """Render `term` in the casing pattern of the `heard` token."""
    if heard.isupper() and len(heard) > 1:
        return term.upper()
    if heard[:1].isupper():
        return term[:1].upper() + term[1:]
    return term


_FLAG_RE = re.compile(r"(?<![:/])//([A-Za-z'][\w'\-]*)")
_OFFER_RE = re.compile(
    r"(?<![:/])\b([A-Za-z'][\w'\-]*)//([A-Za-z'][\w'\-]*)"
)


def strip_flags(text, keep="heard"):
    """Render a clean stream from boost() output:
      • applied boosts  `//Juz`     -> `Juz`   (always — we were confident)
      • offered pairs   `just//Juz` -> `just`  (keep='heard', the SAFE default for
                                     an offline render) or `Juz` (keep='term')
    Offers are exactly the cases we were NOT confident about, so the offline
    default keeps the recognizer's word. When the cloud Foreign lane is available,
    pass the UN-stripped boost().text to it instead — it resolves the offers from
    context (and keeps the applied `//Term` boosts)."""
    if not text:
        return text
    # Resolve offered PAIRS first — otherwise the standalone-flag rule below would
    # match the `//Term` half of `heard//Term` and strip the wrong side.
    if keep == "term":
        out = _OFFER_RE.sub(r"\2", text)
    else:
        out = _OFFER_RE.sub(r"\1", text)
    return _FLAG_RE.sub(r"\1", out)


def annotate(text, languages=("arabic",), word_probs=None):
    """Convenience: return just the annotated stream (BoostResult.text). Drop-in
    shaped like islamic_terms.annotate_foreign but multi-language and with local
    high-confidence boosts already applied as `//Term`."""
    return boost(text, languages=languages, word_probs=word_probs).text


def mark_uncertainty(text, languages=("arabic",), word_probs=None):
    """CLOUD-AUGMENTED Foreign Mode: mark every candidate as `heard//Term` (no
    on-device resolution) so the raw transcript + `//` markers go straight to the
    cloud, which makes the final call. This is the multi-language equivalent of
    islamic_terms.annotate_foreign, used when a cloud key is present (the cloud is
    primary — see local_engine's cloud-dominance routing)."""
    return boost(text, languages=languages, word_probs=word_probs,
                 mark_only=True).text
