#!/usr/bin/env python3
"""Stage 4 — Surface cleanup via model_free.py.

This is the ALWAYS-AVAILABLE final stage of the local post-processing pipeline.
It wraps `model_free.process()` and adds enhanced hallucination phrase stripping
and repetition collapse.  It requires zero external dependencies — model_free is
a standalone, deterministic formatting module that ships with Mumble.

Entry point:
    stage = CleanupStage()
    stage.process("hello   world")  # → "Hello world."

The `available` property is always True (Stage 4 is the ultimate fallback).
`unload()` is a no-op (no model to release).

Edge-case behaviour:
  • Empty / whitespace-only input → "" (no-op)
  • Hallucination phrases (AI self-reference) → stripped from output
  • Repeated words/tokens (stuttering) → collapsed
  • Non-lexical filled pauses (um, uh, er) → removed
  • Split contractions → rejoined
  • Spacing / glued punctuation → normalised
  • Non-English text → pass-through (surface cleanup still applies)

This module provides the SAME `process(text, **kwargs)` → str interface as the
other pipeline stages so the orchestrator can treat it uniformly.
"""

import sys


class CleanupStage:
    """Always-available surface-cleanup stage wrapping model_free.process().

    This is the final stage in the pipeline — it runs AFTER punctuation
    (Stage 1), grammar correction (Stage 2), and mode formatting (Stage 3).
    It handles:
      • Hallucination phrase stripping (AI self-reference artefacts)
      • Repetition collapse (consecutive repeated tokens)
      • Non-lexical filled-pause removal (um, uh, er)
      • Surface formatting: spacing, glued punctuation, contraction rejoining,
        number/currency/percent tightening
      • Sentence-boundary capitalisation + terminal punctuation

    All of the above is delegated to model_free.py, which implements the
    strict formatting-only, non-semantic pipeline per the owner's directive.
    """

    def __init__(self):
        # Stage 4 is always available — no model, no binary, no filesystem.
        self._available = True

    # ------------------------------------------------------------------
    # Availability (always True for Stage 4)
    # ------------------------------------------------------------------

    @property
    def available(self):
        """Stage 4 is always available — it is the ultimate fallback."""
        return self._available

    def unload(self):
        """No-op: Stage 4 has no model to release."""
        pass

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def process(self, text, lane="text", **kwargs):
        """Run surface cleanup on `text` via model_free.process().

        Parameters:
            text: The (possibly formatted) transcript from upstream stages.
            lane: The mode lane — passed through to model_free so it can
                  apply lane-aware cleanup (currently, smart modes degrade
                  to formatting-only with no template building).
            **kwargs: Forwarded for interface compatibility (unused).

        Returns:
            Surface-cleaned text.  Returns "" for empty/whitespace-only
            input.  If model_free is somehow unavailable (extremely unlikely
            since it's a stdlib module), the input is returned unchanged
            as a last-resort pass-through.
        """
        if not text or not text.strip():
            return ""

        try:
            import model_free
            return model_free.process(text, mode=lane)
        except ImportError:
            print("[pipeline] Stage 4 (cleanup): model_free unavailable "
                  "— returning raw output", file=sys.stderr)
            return text  # absolute last resort
        except Exception:
            print("[pipeline] Stage 4 (cleanup) failed — returning raw output",
                  file=sys.stderr)
            return text


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_default_stage = None


def get_stage():
    """Return (or create) the module-level CleanupStage singleton."""
    global _default_stage
    if _default_stage is None:
        _default_stage = CleanupStage()
    return _default_stage
