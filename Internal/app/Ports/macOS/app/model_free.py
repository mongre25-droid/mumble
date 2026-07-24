#!/usr/bin/env python3
"""Deterministic, formatting-only fallback used when no model is available."""

import re

import formatting


ALLOWED = (
    "sentence-boundary capitalisation",
    "terminal punctuation",
    "spacing and punctuation cleanup",
    "split-contraction rejoin",
    "number, currency, percent and ellipsis tightening",
    "obvious transcription artefact removal",
    "non-lexical filled-pause removal",
)
PROHIBITED = (
    "semantic rewriting or paraphrase",
    "intent guessing or contextual inference",
    "confidence-based word substitution",
    "vocabulary auto-correction",
    "foreign-term or homophone resolution",
)
_FILLED_PAUSE_RE = re.compile(
    r"(?i)(?<![A-Za-z'])(?:u+m+|u+h+|e+r+h?|e+rm+|a+h+|h+m+|m+h+m+|m{3,})"
    r"(?![A-Za-z'])[ ]?,?"
)
_SPLIT_CONTRACTION_RE = re.compile(r"\b([A-Za-z]+) (n't|'re|'ve|'ll|'m|'d|'s)\b")
_DECIMAL_RE = re.compile(r"(\d)\s*\.\s*(\d)")
_CURRENCY_RE = re.compile(r"([$£€])\s+(\d)")
_PERCENT_RE = re.compile(r"(\d)\s+%")
_ELLIPSIS_RE = re.compile(r"\.{4,}")


def capabilities():
    return {"allowed": ALLOWED, "prohibited": PROHIBITED,
            "semantic": False, "model_free": True}


def _clean_spacing(text):
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]+([,.!?;:])", r"\1", text)
    text = re.sub(r"([,!?;:])([A-Za-z])", r"\1 \2", text)
    text = re.sub(r"(?<=[A-Za-z]{2})\.([A-Za-z])", r". \1", text)
    # Preserve intentional paragraph separators from upstream formatting stages.
    # Dropping every blank line flattened structured emails and prompts into one
    # paragraph during the mandatory final cleanup stage.
    lines = []
    previous_blank = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line:
            lines.append(line)
            previous_blank = False
        elif lines and not previous_blank:
            lines.append("")
            previous_blank = True
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def process(raw, mode=None, *, remove_filled_pauses=True,
            strip_artefacts=True, insert_commas=True):
    """Return one faithful, surface-cleaned string; ``mode`` is ignored."""
    if not raw or not str(raw).strip():
        return ""
    text = str(raw).strip()
    if remove_filled_pauses:
        text = _FILLED_PAUSE_RE.sub(" ", text)
    text = _clean_spacing(text)
    if strip_artefacts:
        text = formatting._deloop(text)
        text = formatting._apply_hallucination_blocklist(text)
        if not text or not text.strip():
            return ""
    text = _SPLIT_CONTRACTION_RE.sub(r"\1\2", text)
    text = _DECIMAL_RE.sub(r"\1.\2", text)
    text = _CURRENCY_RE.sub(r"\1\2", text)
    text = _PERCENT_RE.sub(r"\1%", text)
    text = _ELLIPSIS_RE.sub("...", text)
    if insert_commas:
        text = formatting._insert_commas(text)
    return formatting._capitalize(formatting._ensure_terminal(text))


def degrade_smart_mode(raw, mode):
    return process(raw, mode=mode)
