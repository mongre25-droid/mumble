#!/usr/bin/env python3
"""Tests for the pipeline package — Stage 1 (punctuation) and PipelineOrchestrator.

Run: python test_pipeline.py

Covers:
  • Stage 1 punctuation restoration and capitalisation (VAL-PIPE-001, VAL-PIPE-002)
  • Pipeline orchestrator stage sequencing (VAL-PIPE-019)
  • Graceful degradation: missing stages, degrade-to-Stage-4 (VAL-PIPE-008)
  • Edge cases: empty input, very short text, very long text, non-English
  • Stage 4 cleanup: repetition collapse via model_free (VAL-PIPE-018)
  • import safety and lazy loading

These tests use the real BERT punctuation model (lazy-loaded).  If transformers
is not installed, the model-dependent tests are skipped gracefully.
"""

import sys
import os

# Ensure the app directory is on the path so imports resolve properly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Test framework (stdlib, matches existing pattern)
# ---------------------------------------------------------------------------

_passed = 0
_failed = 0
_skipped = 0


def _run(name, func):
    global _passed, _failed, _skipped
    try:
        func()
        _passed += 1
        print(f"  ok  {name}")
    except unittest.SkipTest:
        _skipped += 1
        print(f" SKIP {name}")
    except AssertionError as e:
        _failed += 1
        print(f" FAIL {name}: {e}")
    except Exception as e:
        _failed += 1
        print(f" FAIL {name}: {type(e).__name__}: {e}")


class unittest:
    """Minimal namespace so existing SkipTest patterns work."""
    class SkipTest(Exception):
        pass


def skip_if(condition, reason=""):
    if condition:
        raise unittest.SkipTest(reason)


# ---------------------------------------------------------------------------
# Stage 1 tests — punctuation restoration & capitalisation
# ---------------------------------------------------------------------------

def test_stage1_import():
    """Stage 1 module imports without error."""
    from pipeline.stage_punctuation import PunctuationStage
    s = PunctuationStage()
    assert s is not None
    assert hasattr(s, "process")
    assert hasattr(s, "available")
    assert hasattr(s, "unload")


def test_stage1_lazy_loading():
    """Stage 1 does NOT load the model at construction time."""
    from pipeline.stage_punctuation import PunctuationStage
    s = PunctuationStage()
    # Before first use, the internal pipe should be None.
    assert s._pipe is None
    assert s._loaded is False


def test_stage1_punctuation_restoration():
    """Unpunctuated lowercase text gets punctuation and capitalisation restored.
    (VAL-PIPE-001, VAL-PIPE-002)"""
    from pipeline.stage_punctuation import PunctuationStage
    s = PunctuationStage()
    skip_if(not s.available, "BERT punctuation model not available")

    # Basic sentence gets terminal punctuation
    out = s.process("hello world")
    assert out.endswith("."), f"Expected terminal punctuation, got: {out!r}"
    assert out[0].isupper(), f"Expected capitalised first letter, got: {out!r}"

    # Multi-sentence dictation
    out = s.process("hello world how are you")
    # Should have at least one sentence boundary marker
    assert any(m in out for m in (".", "?", "!")), \
        f"Expected punctuation, got: {out!r}"


def test_stage1_capitalisation():
    """First word of each sentence is capitalised. (VAL-PIPE-001)"""
    from pipeline.stage_punctuation import PunctuationStage
    s = PunctuationStage()
    skip_if(not s.available, "BERT punctuation model not available")

    out = s.process("my name is john and i live in london")
    assert out[0].isupper(), f"Expected capitalised first letter, got: {out!r}"
    assert "London" in out or "london" in out, \
        f"Expected output to contain location, got: {out!r}"


def test_stage1_pronoun_i_capitalised():
    """Standalone 'i' pronoun is capitalised to 'I'."""
    from pipeline.stage_punctuation import PunctuationStage
    s = PunctuationStage()
    skip_if(not s.available, "BERT punctuation model not available")

    out = s.process("i think so")
    # The word "I" (uppercase, standalone) should appear
    assert " I " in out or out.startswith("I "), \
        f"Expected 'I' pronoun capitalisation, got: {out!r}"


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------

def test_stage1_empty_input():
    """Empty string returns empty string without error."""
    from pipeline.stage_punctuation import PunctuationStage
    s = PunctuationStage()
    skip_if(not s.available, "BERT punctuation model not available")

    assert s.process("") == ""
    assert s.process("   ") == ""


def test_stage1_very_short_text():
    """Single word or very short phrase is handled. (VAL-PIPE-013)"""
    from pipeline.stage_punctuation import PunctuationStage
    s = PunctuationStage()
    skip_if(not s.available, "BERT punctuation model not available")

    out = s.process("yes")
    assert len(out) > 0, f"Expected non-empty output for short text"
    assert out[0].isupper(), f"Expected capitalised: {out!r}"

    out = s.process("okay")
    assert len(out) > 0
    assert out[0].isupper(), f"Expected capitalised: {out!r}"


def test_stage1_long_text():
    """Multi-sentence text (>230 words) is fully processed. (VAL-PIPE-014)"""
    from pipeline.stage_punctuation import PunctuationStage
    s = PunctuationStage()
    skip_if(not s.available, "BERT punctuation model not available")

    # Build a long text of ~300 words (repeated sentences)
    sentence = "hello world this is a test sentence "
    long_text = sentence * 50  # ~300 words
    out = s.process(long_text)
    assert len(out) > 0, f"Expected non-empty output for long text"
    # Output should not be truncated — roughly same number of words
    out_words = len(out.split())
    in_words = len(long_text.split())
    assert out_words >= in_words * 0.9, \
        f"Output appears truncated: {out_words} vs {in_words} input words"


def test_stage1_non_english():
    """Non-English text passes through without crash. (VAL-PIPE-015)"""
    from pipeline.stage_punctuation import PunctuationStage
    s = PunctuationStage()
    skip_if(not s.available, "BERT punctuation model not available")

    # Spanish
    out = s.process("hola como estas")
    assert len(out) > 0, f"Expected non-empty output for Spanish text"
    assert "hola" in out.lower() or "Hola" in out, \
        f"Expected Spanish content preserved, got: {out!r}"

    # German
    out = s.process("guten tag wie geht es ihnen")
    assert len(out) > 0, f"Expected non-empty output for German text"

    # French
    out = s.process("bonjour comment allez vous")
    assert len(out) > 0, f"Expected non-empty output for French text"


# ---------------------------------------------------------------------------
# Orchestrator tests
# ---------------------------------------------------------------------------

def test_orchestrator_import():
    """Pipeline orchestrator imports without error."""
    from pipeline import PipelineOrchestrator, process, get_orchestrator
    orch = PipelineOrchestrator()
    assert orch is not None
    assert hasattr(orch, "process")
    assert hasattr(orch, "available_stages")
    assert hasattr(orch, "degrade_to_stage4")

    # Module-level functions
    orch2 = get_orchestrator()
    assert orch2 is not None


def test_orchestrator_stage_ordering():
    """Pipeline runs stages in fixed order: 1 → 2 → 3 → 4. (VAL-PIPE-019)

    We verify this by checking that stages are listed in order and that
    Stage 1 output (punctuated) is different from Stage 4-only output
    (no punctuation), proving Stage 1 runs before Stage 4.
    """
    from pipeline import PipelineOrchestrator

    orch = PipelineOrchestrator()
    avail = orch.available_stages()

    # At minimum Stage 4 should be available
    assert 4 in avail, f"Stage 4 must always be available, got: {avail}"

    # Stages must be listed in order
    assert avail == sorted(avail), \
        f"Stages must be in order, got: {avail}"

    # If Stage 1 is available, its output should differ from Stage 4-only
    if 1 in avail:
        orch_full = PipelineOrchestrator()
        out_full = orch_full.process("hello world how are you")
        out_s4 = orch_full.degrade_to_stage4("hello world how are you")
        # Stage 1 adds punctuation like ".", so output should differ
        assert out_full != out_s4, \
            f"Stage 1 should change output: full={out_full!r} vs s4={out_s4!r}"


def test_orchestrator_full_pipeline():
    """Pipeline with all available stages runs start to finish."""
    from pipeline import PipelineOrchestrator

    orch = PipelineOrchestrator()
    out = orch.process("hello world this is a test")
    assert len(out) > 0, f"Expected non-empty output"
    assert out[0].isupper(), f"Expected capitalised first letter, got: {out!r}"


def test_orchestrator_empty_input():
    """Empty string returns empty string."""
    from pipeline import PipelineOrchestrator

    orch = PipelineOrchestrator()
    assert orch.process("") == ""
    assert orch.process("   ") == ""
    assert orch.process("\n\t") == ""


def test_orchestrator_graceful_degradation():
    """Pipeline degrades gracefully when stages are unavailable. (VAL-PIPE-008)

    degrade_to_stage4() always works because model_free is always present.
    """
    from pipeline import PipelineOrchestrator

    orch = PipelineOrchestrator()
    out = orch.degrade_to_stage4("hello world")
    assert len(out) > 0, f"Stage 4 degradation should produce output"
    assert out[0].isupper(), f"Stage 4 should capitalise: {out!r}"


def test_orchestrator_unload_all():
    """unload_all() releases all models without error."""
    from pipeline import PipelineOrchestrator

    orch = PipelineOrchestrator()
    # Should not raise
    orch.unload_all()

    # After unload, processing should reload lazily or degrade
    out = orch.process("hello")
    assert len(out) > 0, "Pipeline should still produce output after unload"


# ---------------------------------------------------------------------------
# Stage 4 cleanup tests (model_free integration)
# ---------------------------------------------------------------------------

def test_stage4_repetition_collapse():
    """Stage 4 collapses stuttering repeated tokens. (VAL-PIPE-018)"""
    from pipeline import PipelineOrchestrator

    orch = PipelineOrchestrator()
    # model_free's _deloop requires >=4 repeats for 1-token patterns.
    out = orch.degrade_to_stage4("the the the the meeting is at three")
    # After collapse, "the" should appear at most once at the start
    assert "the the the the" not in out.lower(), \
        f"Expected repetition to be collapsed, got: {out!r}"


def test_stage4_hallucination_stripping():
    """Stage 4 strips hallucination phrases. (VAL-PIPE-017)"""
    from pipeline import PipelineOrchestrator

    orch = PipelineOrchestrator()
    out = orch.degrade_to_stage4("thank you for watching this is a test")
    # "thank you for watching" is a known hallucination phrase
    assert "thank you for watching" not in out.lower(), \
        f"Expected hallucination phrase to be stripped, got: {out!r}"


def test_stage4_filled_pause_removal():
    """Stage 4 removes non-lexical filled pauses (um, uh)."""
    from pipeline import PipelineOrchestrator

    orch = PipelineOrchestrator()
    out = orch.degrade_to_stage4("um I think uh yes")
    assert "um" not in out.lower(), f"Expected 'um' to be removed, got: {out!r}"
    assert "uh" not in out.lower(), f"Expected 'uh' to be removed, got: {out!r}"
    assert "I think" in out or "i think" in out.lower(), \
        f"Expected content preserved, got: {out!r}"


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------

def test_module_level_process():
    """pipeline.process() convenience function works."""
    from pipeline import process

    out = process("hello world")
    assert len(out) > 0, f"Expected non-empty output from process()"
    assert out[0].isupper() or not out, \
        f"Expected capitalised output, got: {out!r}"


# ===========================================================================
# Runner
# ===========================================================================

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]

    print(f"\n{'='*60}")
    print(f"Pipeline Tests — {len(tests)} test cases")
    print(f"{'='*60}\n")

    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            _run(name, func)

    total = _passed + _failed
    print(f"\n{'='*60}")
    print(f"Results: {_passed}/{total} passed", end="")
    if _skipped:
        print(f", {_skipped} skipped", end="")
    if _failed:
        print(f", {_failed} FAILED", end="")
    print()
    print(f"{'='*60}")

    sys.exit(0 if _failed == 0 else 1)
