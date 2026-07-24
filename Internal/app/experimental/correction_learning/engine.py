"""Conservative, dependency-free analysis of explicit transcript corrections.

The engine deliberately learns only small term substitutions.  It is not a
general grammar learner: punctuation edits, insertions/deletions, inflections,
and broad rewrites are reported but never turned into persistent vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
import difflib
import re
from typing import Any, Iterable


# A lexical token may contain punctuation when that punctuation is genuinely
# part of a name (``GPT-4``, ``Node.js``, ``C++``).  Sentence punctuation is a
# separate token, which lets the filter reject punctuation-only editing.
_TOKEN_RE = re.compile(
    r"(?P<lex>(?:[^\W_][.]){2,}(?:[^\W_])?|"
    r"[^\W_]+(?:['\N{RIGHT SINGLE QUOTATION MARK}-][^\W_]+)*"
    r"(?:[.][^\W_]+)*(?:[+][+]|#)?)|(?P<punct>[^\w\s])",
    re.UNICODE,
)
_SPACE_RE = re.compile(r"\s+")

# Function words and very frequent grammatical alternatives are unsafe global
# replacements.  This is intentionally language-neutral in behaviour: words
# not in this English safety list still have to pass every structural filter.
_GRAMMAR_WORDS = frozenset(
    """
    a about after again against all am an and any are as at be because been
    before being below between both but by can could did do does doing down
    during each few for from further had has have having he her here hers
    herself him himself his how i if in into is it its itself just me more most
    my myself no nor not now of off on once only or other ought our ours
    ourselves out over own same she should so some such than that the their
    theirs them themselves then there these they this those through to too
    under until up very was we were what when where which while who whom why
    will with would you your yours yourself yourselves
    cat cats dog dogs get gets got go goes going make makes made say says said
    see sees seen take takes took want wants wanted work works worked
    despite enough every many much either neither whether yet
    """.split()
)
_ORDINARY_EMPHASIS_WORDS = frozenset(
    "hello stop yes no now please urgent important warning help thanks thank "
    "never always really love hate sorry goodbye welcome".split()
)
_IRREGULAR_FORM_GROUPS = tuple(
    frozenset(group.split("/"))
    for group in (
        "be/am/is/are/was/were/been/being",
        "begin/began/begun",
        "break/broke/broken",
        "bring/brought",
        "buy/bought",
        "catch/caught",
        "choose/chose/chosen",
        "come/came",
        "do/did/done",
        "drink/drank/drunk",
        "drive/drove/driven",
        "eat/ate/eaten",
        "fall/fell/fallen",
        "feel/felt",
        "find/found",
        "get/got/gotten",
        "give/gave/given",
        "go/went/gone",
        "grow/grew/grown",
        "have/has/had",
        "keep/kept",
        "know/knew/known",
        "leave/left",
        "lose/lost",
        "make/made",
        "meet/met",
        "pay/paid",
        "run/ran",
        "say/said",
        "see/saw/seen",
        "sell/sold",
        "send/sent",
        "sing/sang/sung",
        "sit/sat",
        "speak/spoke/spoken",
        "stand/stood",
        "swim/swam/swum",
        "take/took/taken",
        "teach/taught",
        "tell/told",
        "think/thought",
        "understand/understood",
        "win/won",
        "write/wrote/written",
        "good/better/best",
        "bad/worse/worst",
    )
)
_TERMINAL_PUNCTUATION = frozenset({".", "!", "?"})
_INFLECTION_SUFFIXES = ("s", "es", "ed", "ing", "er", "est", "ly")
_MAX_SOURCE_WORDS = 4
_MAX_TERM_LENGTH = 100
_MAX_INPUT_LENGTH = 20_000


@dataclass(frozen=True)
class _Token:
    text: str
    lexical: bool
    start: int
    end: int


def _tokenize(text: str) -> list[_Token]:
    return [
        _Token(
            text=match.group(0),
            lexical=match.lastgroup == "lex",
            start=match.start(),
            end=match.end(),
        )
        for match in _TOKEN_RE.finditer(text)
    ]


def _clean_input(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("original and corrected text must be strings")
    return value.replace("\x00", "")


def _is_acronym(value: str) -> bool:
    letters = [character for character in value if character.isalpha()]
    return 2 <= len(letters) <= 10 and all(character.isupper() for character in letters)


def _has_distinctive_casing(value: str) -> bool:
    letters = [character for character in value if character.isalpha()]
    if not letters:
        return False
    if _is_acronym(value):
        return True
    # Title case and mixed-case product/name spellings (Mumble, iPhone, PyTorch).
    return value[:1].isupper() or (any(c.isupper() for c in letters[1:]))


def _starts_sentence(tokens: list[_Token], source_start: int) -> bool:
    for token in reversed(tokens[:source_start]):
        if token.lexical:
            return False
        if token.text in _TERMINAL_PUNCTUATION:
            return True
    return True


def _inflection_related(left: str, right: str) -> bool:
    left = left.casefold()
    right = right.casefold()
    if left == right:
        return False
    for longer, shorter in ((left, right), (right, left)):
        if len(shorter) < 3:
            continue
        for suffix in _INFLECTION_SUFFIXES:
            if longer == shorter + suffix:
                return True
            # Common doubled-consonant and dropped-e forms: run/running,
            # create/creating.  These checks need not be a full stemmer; they
            # merely prevent risky global mappings.
            if suffix in {"ed", "ing"}:
                if shorter.endswith("e") and longer == shorter[:-1] + suffix:
                    return True
                if longer.endswith(suffix):
                    stem = longer[: -len(suffix)]
                    if len(stem) >= 2 and stem[-1] == stem[-2] and stem[:-1] == shorter:
                        return True
            if suffix in {"s", "es"} and shorter.endswith("y"):
                if longer == shorter[:-1] + "ies":
                    return True
    return False


def _irregular_form_related(left: str, right: str) -> bool:
    pair = {left.casefold(), right.casefold()}
    return len(pair) == 2 and any(pair <= group for group in _IRREGULAR_FORM_GROUPS)


def _orthographic_similarity(left: str, right: str) -> float:
    left_key = "".join(character for character in left.casefold() if character.isalnum())
    right_key = "".join(character for character in right.casefold() if character.isalnum())
    if not left_key or not right_key:
        return 0.0
    return difflib.SequenceMatcher(None, left_key, right_key, autojunk=False).ratio()


def _is_punctuation_only_spelling_change(source: str, target: str) -> bool:
    if source.casefold() == target.casefold():
        return False
    source_key = "".join(c for c in source.casefold() if c.isalnum())
    target_key = "".join(c for c in target.casefold() if c.isalnum())
    return bool(source_key) and source_key == target_key


def _has_spoken_symbol_alignment(source: str, target: str) -> bool:
    """Recognize a few unambiguous spoken suffixes in technical terms."""

    words = source.casefold().split()
    target_folded = target.casefold()
    if target_folded.endswith("++") and words[-2:] == ["plus", "plus"]:
        return True
    if target_folded.endswith("#") and words[-1:] in (["sharp"], ["hash"]):
        return True
    return False


def _rewrite_is_too_broad(old_tokens: list[_Token], new_tokens: list[_Token]) -> bool:
    old_words = [token.text.casefold() for token in old_tokens if token.lexical]
    new_words = [token.text.casefold() for token in new_tokens if token.lexical]
    maximum = max(len(old_words), len(new_words))
    if maximum < 5:
        return False
    matcher = difflib.SequenceMatcher(None, old_words, new_words, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    changed_fraction = 1.0 - (matched / maximum)
    # Five-word utterances get a little more latitude for a two-word name; long
    # utterances are rejected once a majority has been rewritten.
    return (maximum >= 8 and changed_fraction > 0.55) or (
        matched <= 1 and changed_fraction > 0.65
    )


def _kind(source: str, target: str, source_words: int) -> str:
    if source.casefold() == target.casefold():
        return "acronym" if _is_acronym(target) else "casing"
    return "many_to_one" if source_words > 1 else "replacement"


def _rejection(reason: str, *, count: int = 1) -> dict[str, Any]:
    # Rejected fragments are intentionally omitted.  Callers get an actionable
    # reason without accidentally retaining unrelated transcript prose.
    return {"reason": reason, "count": max(1, int(count))}


def _merge_rejections(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, int] = {}
    for item in items:
        reason = str(item["reason"])
        totals[reason] = totals.get(reason, 0) + int(item.get("count", 1))
    return [{"reason": reason, "count": count} for reason, count in totals.items()]


def analyze_correction(original: str, corrected: str) -> dict[str, Any]:
    """Return safe term mappings inferred from an explicit before/after edit.

    The result is JSON-friendly and has ``ok``, ``message``, ``changes``, and
    ``rejected`` keys.  A change is ``{"from", "to", "kind",
    "source_word_count"}``.  No mutation or persistence occurs here.
    """

    try:
        before = _clean_input(original)
        after = _clean_input(corrected)
    except TypeError as exc:
        return {
            "ok": False,
            "message": str(exc),
            "changes": [],
            "rejected": [_rejection("invalid_input")],
        }

    if not before.strip() or not after.strip():
        return {
            "ok": False,
            "message": "Both the original and corrected text are required.",
            "changes": [],
            "rejected": [_rejection("empty_input")],
        }
    if len(before) > _MAX_INPUT_LENGTH or len(after) > _MAX_INPUT_LENGTH:
        return {
            "ok": False,
            "message": "The correction is too long to learn safely.",
            "changes": [],
            "rejected": [_rejection("input_too_long")],
        }

    old_tokens = _tokenize(before)
    new_tokens = _tokenize(after)
    if not old_tokens or not new_tokens:
        return {
            "ok": False,
            "message": "No words were found to learn.",
            "changes": [],
            "rejected": [_rejection("no_lexical_tokens")],
        }

    if _rewrite_is_too_broad(old_tokens, new_tokens):
        return {
            "ok": False,
            "message": "This edit changes too much prose to become a global correction.",
            "changes": [],
            "rejected": [_rejection("whole_prose_rewrite")],
        }

    matcher = difflib.SequenceMatcher(
        None,
        [token.text for token in old_tokens],
        [token.text for token in new_tokens],
        autojunk=False,
    )
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    # SequenceMatcher intentionally coalesces adjacent replacements.  When both
    # sides have the same number of lexical tokens, splitting that block back
    # into aligned one-to-one edits safely supports corrections such as
    # ``api sdk`` -> ``API SDK``. Unequal blocks remain intact so we never guess
    # an alignment for prose.
    opcodes: list[tuple[str, int, int, int, int]] = []
    for opcode in matcher.get_opcodes():
        tag, old_start, old_end, new_start, new_end = opcode
        old_size = old_end - old_start
        new_size = new_end - new_start
        if tag == "replace" and old_size == new_size and old_size > 1:
            if all(
                token.lexical
                for token in (
                    old_tokens[old_start:old_end] + new_tokens[new_start:new_end]
                )
            ):
                opcodes.extend(
                    (
                        "replace",
                        old_start + offset,
                        old_start + offset + 1,
                        new_start + offset,
                        new_start + offset + 1,
                    )
                    for offset in range(old_size)
                )
                continue
        opcodes.append(opcode)

    for opcode, old_start, old_end, new_start, new_end in opcodes:
        if opcode == "equal":
            continue
        old_slice = old_tokens[old_start:old_end]
        new_slice = new_tokens[new_start:new_end]
        if opcode in {"insert", "delete"}:
            changed_words = sum(token.lexical for token in old_slice + new_slice)
            rejected.append(
                _rejection(
                    "punctuation_change" if changed_words == 0 else "insertion_or_deletion"
                )
            )
            continue
        if any(not token.lexical for token in old_slice + new_slice):
            rejected.append(_rejection("punctuation_change"))
            continue
        if not old_slice or len(old_slice) > _MAX_SOURCE_WORDS or len(new_slice) != 1:
            rejected.append(_rejection("not_a_small_term_correction"))
            continue

        source = _SPACE_RE.sub(" ", " ".join(token.text for token in old_slice)).strip()
        target = new_slice[0].text.strip()
        source_folded = source.casefold()
        target_folded = target.casefold()
        distinctive = _has_distinctive_casing(target)

        if (
            not source
            or not target
            or len(source) > _MAX_TERM_LENGTH
            or len(target) > _MAX_TERM_LENGTH
        ):
            rejected.append(_rejection("invalid_term_length"))
            continue
        if source == target:
            continue
        if (
            source_folded != target_folded
            and _orthographic_similarity(source, target) < 0.30
            and not _has_spoken_symbol_alignment(source, target)
        ):
            rejected.append(_rejection("ordinary_content_rewrite"))
            continue
        if source_folded == target_folded:
            if _is_acronym(target) and source_folded in _ORDINARY_EMPHASIS_WORDS:
                rejected.append(_rejection("ordinary_emphasis_change"))
                continue
            if _starts_sentence(old_tokens, old_start) and not _is_acronym(target):
                rejected.append(_rejection("ordinary_sentence_capitalization"))
                continue
            if not distinctive:
                rejected.append(_rejection("ordinary_casing_change"))
                continue
        elif len(old_slice) == 1:
            if source_folded in _GRAMMAR_WORDS and target_folded in _GRAMMAR_WORDS:
                rejected.append(_rejection("ordinary_grammar_change"))
                continue
            if (
                (source_folded in _GRAMMAR_WORDS or target_folded in _GRAMMAR_WORDS)
                and not distinctive
            ):
                rejected.append(_rejection("ordinary_grammar_change"))
                continue
            if not distinctive:
                if _is_punctuation_only_spelling_change(source, target):
                    rejected.append(_rejection("punctuation_change"))
                    continue
                if _inflection_related(source, target) or _irregular_form_related(
                    source, target
                ):
                    rejected.append(_rejection("ordinary_inflection"))
                    continue
                # A dissimilar lower-case word swap is more likely prose editing
                # than a misspelling/mishearing. Proper names and branded casing
                # remain eligible without this spelling-confidence signal.
                if _orthographic_similarity(source, target) < 0.45:
                    rejected.append(_rejection("ordinary_content_rewrite"))
                    continue
        else:
            source_grammar_words = sum(
                token.text.casefold() in _GRAMMAR_WORDS for token in old_slice
            )
            if (
                (target_folded in _GRAMMAR_WORDS or source_grammar_words >= 2)
                and not distinctive
            ):
                rejected.append(_rejection("ordinary_grammar_change"))
                continue
            if (
                _is_punctuation_only_spelling_change(source, target)
                and not distinctive
            ):
                rejected.append(_rejection("punctuation_change"))
                continue
            if not distinctive and _orthographic_similarity(source, target) < 0.45:
                rejected.append(_rejection("ordinary_content_rewrite"))
                continue
        if len(old_slice) == 1 and len(source_folded) <= 2 and not _is_acronym(target):
            rejected.append(_rejection("term_too_short"))
            continue

        candidates.append(
            {
                "from": source,
                "to": target,
                "kind": _kind(source, target, len(old_slice)),
                "source_word_count": len(old_slice),
            }
        )

    # Multiple contradictory edits for one heard form are unsafe.  Identical
    # repeated corrections collapse into a single global mapping.
    by_source: dict[str, dict[str, dict[str, Any]]] = {}
    for candidate in candidates:
        source_key = candidate["from"].casefold()
        by_source.setdefault(source_key, {})[candidate["to"].casefold()] = candidate
    changes: list[dict[str, Any]] = []
    for targets in by_source.values():
        if len(targets) != 1:
            rejected.append(_rejection("ambiguous_repeated_correction"))
            continue
        changes.append(next(iter(targets.values())))

    merged_rejections = _merge_rejections(rejected)
    if changes:
        message = (
            f"Found {len(changes)} safe correction"
            f"{'s' if len(changes) != 1 else ''}."
        )
        return {
            "ok": True,
            "message": message,
            "changes": changes,
            "rejected": merged_rejections,
        }
    message = (
        "The texts are already equivalent."
        if before == after or not rejected
        else "No safe term correction was found."
    )
    return {
        "ok": False,
        "message": message,
        "changes": [],
        "rejected": merged_rejections,
    }


__all__ = ["analyze_correction"]
