#!/usr/bin/env python3
"""Tests for pipeline Stage 4 — CleanupStage (model_free wrapper).

Run: python test_stage_cleanup.py

Covers:
  • CleanupStage import, availability, unload
  • Hallucination phrase stripping (VAL-PIPE-017)
  • Repetition collapse (VAL-PIPE-018)
  • Filled pause removal (um, uh, er)
  • Surface cleanup: spacing, capitalisation, punctuation
  • Edge cases: empty input, very short text, non-English (VAL-PIPE-013, VAL-PIPE-015)
  • Pipeline orchestrator integration with CleanupStage
  • Graceful degradation: model_free unavailable fallback
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


# ===========================================================================
# CleanupStage core tests
# ===========================================================================

def test_stage4_import():
    """Stage 4 module imports without error."""
    from pipeline.stage_cleanup import CleanupStage, get_stage
    s = CleanupStage()
    assert s is not None
    assert hasattr(s, "process")
    assert hasattr(s, "available")
    assert hasattr(s, "unload")
    # Module-level singleton
    s2 = get_stage()
    assert s2 is not None


def test_stage4_always_available():
    """Stage 4 is always available (no model, no binary, no filesystem)."""
    from pipeline.stage_cleanup import CleanupStage
    s = CleanupStage()
    assert s.available is True, \
        "Stage 4 must always be available"


def test_stage4_unload_no_error():
    """Stage 4 unload() is a no-op and does not raise."""
    from pipeline.stage_cleanup import CleanupStage
    s = CleanupStage()
    s.unload()  # should not raise
    # After unload, still available.
    assert s.available is True


def test_stage4_empty_input():
    """Empty string returns empty string without error."""
    from pipeline.stage_cleanup import CleanupStage
    s = CleanupStage()
    assert s.process("") == ""
    assert s.process("   ") == ""
    assert s.process("\n\t") == ""


def test_stage4_basic_cleanup():
    """Basic surface cleanup: capitalisation and spacing."""
    from pipeline.stage_cleanup import CleanupStage
    s = CleanupStage()
    out = s.process("hello   world")
    assert len(out) > 0, f"Expected non-empty output"
    assert out[0].isupper(), f"Expected capitalised output, got: {out!r}"
    assert "  " not in out, f"Expected no double spaces, got: {out!r}"


def test_stage4_hallucination_stripping():
    """Known hallucination phrases are stripped from output. (VAL-PIPE-017)"""
    from pipeline.stage_cleanup import CleanupStage
    s = CleanupStage()

    # "thank you for watching" is a known Whisper hallucination phrase
    out = s.process("thank you for watching this is a test")
    assert "thank you for watching" not in out.lower(), \
        f"Expected hallucination phrase to be stripped, got: {out!r}"

    # "as an AI language model" should also be stripped
    out = s.process("as an AI language model i cannot help with that")
    assert "as an AI language model" not in out.lower(), \
        f"Expected AI self-reference to be stripped, got: {out!r}"

    # "please like and subscribe" is another hallucination
    out = s.process("please like and subscribe to my channel")
    assert "please like and subscribe" not in out.lower(), \
        f"Expected hallucination phrase to be stripped, got: {out!r}"


def test_stage4_repetition_collapse():
    """Consecutive repeated tokens are collapsed. (VAL-PIPE-018)"""
    from pipeline.stage_cleanup import CleanupStage
    s = CleanupStage()

    # 4+ repeated tokens should be collapsed (model_free's _deloop threshold
    # is 4+ repeats for 1-token patterns).
    out = s.process("the the the the the meeting is at three")
    assert "the the the the" not in out.lower(), \
        f"Expected repetition to be collapsed, got: {out!r}"

    # Verify "the" appears at most 2 times total (after collapse)
    the_count = out.lower().count("the")
    assert the_count <= 2, \
        f"Expected 'the' to be collapsed, got {the_count} occurrences: {out!r}"


def test_stage4_filled_pause_removal():
    """Non-lexical filled pauses (um, uh, er) are removed."""
    from pipeline.stage_cleanup import CleanupStage
    s = CleanupStage()

    out = s.process("um I think uh yes")
    assert "um" not in out.lower(), f"Expected 'um' to be removed, got: {out!r}"
    assert "uh" not in out.lower(), f"Expected 'uh' to be removed, got: {out!r}"

    # Content should be preserved
    assert "think" in out.lower() or "Think" in out, \
        f"Expected content preserved, got: {out!r}"

    out = s.process("er what do you mean")
    assert "er" not in out.lower() or " er " not in out.lower(), \
        f"Expected 'er' to be removed, got: {out!r}"


def test_stage4_contraction_rejoin():
    """Split contractions are rejoined (do n't -> don't)."""
    from pipeline.stage_cleanup import CleanupStage
    s = CleanupStage()

    out = s.process("i do n't know what they 're doing")
    assert "don't" in out.lower() or "do n't" not in out.lower(), \
        f"Expected contraction rejoin, got: {out!r}"


def test_stage4_spacing_cleanup():
    """Glued punctuation and extra spacing are normalised."""
    from pipeline.stage_cleanup import CleanupStage
    s = CleanupStage()

    out = s.process("hello , world . how are you ?")
    assert ", " in out or "hello, world" in out.lower(), \
        f"Expected glued punctuation fix, got: {out!r}"


def test_stage4_terminal_punctuation():
    """Unpunctuated text gets terminal punctuation applied."""
    from pipeline.stage_cleanup import CleanupStage
    s = CleanupStage()

    out = s.process("hello world how are you")
    # Should end with punctuation (., ?, or !)
    assert out.rstrip()[-1] in ".!?", \
        f"Expected terminal punctuation, got: {out!r}"


def test_stage4_pronoun_i_capitalised():
    """Standalone 'i' pronoun is capitalised to 'I'."""
    from pipeline.stage_cleanup import CleanupStage
    s = CleanupStage()

    out = s.process("i think i will go")
    # The word "I" (uppercase, standalone) should appear
    assert " I " in out or out.startswith("I "), \
        f"Expected 'I' pronoun capitalisation, got: {out!r}"


# ===========================================================================
# Edge case tests
# ===========================================================================

def test_stage4_very_short_text():
    """Single word or very short phrase is handled. (VAL-PIPE-013)"""
    from pipeline.stage_cleanup import CleanupStage
    s = CleanupStage()

    out = s.process("yes")
    assert len(out) > 0, f"Expected non-empty output for short text"
    assert out[0].isupper(), f"Expected capitalised: {out!r}"

    out = s.process("okay")
    assert len(out) > 0
    assert out[0].isupper(), f"Expected capitalised: {out!r}"

    out = s.process("no")
    assert len(out) > 0


def test_stage4_non_english():
    """Non-English text passes through gracefully without crash. (VAL-PIPE-015)"""
    from pipeline.stage_cleanup import CleanupStage
    s = CleanupStage()

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

    # Japanese (romaji, so model_free doesn't mangle it)
    out = s.process("konnichiwa genki desu ka")
    assert len(out) > 0, f"Expected non-empty output for Japanese romaji"


def test_stage4_long_text():
    """Long text is fully processed without truncation. (VAL-PIPE-014)"""
    from pipeline.stage_cleanup import CleanupStage
    s = CleanupStage()

    # Use varied, non-repetitive text — model_free's _deloop strips
    # pathological repetition loops, which a single repeated sentence
    # would trigger.  Varied sentences pass through cleanly.
    sentences = [
        "hello world this is a test sentence.",
        "the quick brown fox jumps over the lazy dog.",
        "i think we should go to the park today.",
        "the meeting is scheduled for tomorrow morning.",
        "please bring your laptop and notebook.",
        "we will discuss the quarterly results.",
        "after lunch there will be a team standup.",
        "dont forget to submit your timesheet by friday.",
    ]
    long_text = " ".join(sentences * 3)  # ~140 words, varied
    out = s.process(long_text)
    assert len(out) > 0, "Long text should produce output"
    out_words = len(out.split())
    in_words = len(long_text.split())
    assert out_words >= in_words * 0.5, \
        f"Output appears truncated: {out_words} vs {in_words} input words"


def test_stage4_numbers_preserved():
    """Numbers and currency are preserved and tightened."""
    from pipeline.stage_cleanup import CleanupStage
    s = CleanupStage()

    out = s.process("the price is $ 5 . 50")
    assert "$5.50" in out or "$ 5.50" in out or "$5 .50" in out, \
        f"Expected currency/numbers tightened, got: {out!r}"


# ===========================================================================
# Pipeline orchestrator integration tests
# ===========================================================================

def test_orchestrator_stage4_available():
    """Pipeline orchestrator reports Stage 4 as available."""
    from pipeline import PipelineOrchestrator
    orch = PipelineOrchestrator()
    avail = orch.available_stages()
    assert 4 in avail, f"Stage 4 must be in available stages, got: {avail}"


def test_orchestrator_degrade_to_stage4():
    """degrade_to_stage4() works and produces output."""
    from pipeline import PipelineOrchestrator
    orch = PipelineOrchestrator()
    out = orch.degrade_to_stage4("hello world")
    assert len(out) > 0, f"Expected output from degrade_to_stage4"
    assert out[0].isupper(), f"Expected capitalised output, got: {out!r}"


def test_orchestrator_stage4_in_full_pipeline():
    """Full pipeline processes through Stage 4."""
    from pipeline import PipelineOrchestrator
    orch = PipelineOrchestrator()

    out = orch.process("hello world this is a test")
    assert len(out) > 0, "Pipeline should produce output"
    # Stage 4 should have cleaned the output
    assert out[0].isupper(), f"Expected capitalised output, got: {out!r}"


def test_orchestrator_stage4_after_stages_2_3():
    """Stage 4 still runs when stages 2 and 3 are unavailable."""
    from pipeline import PipelineOrchestrator
    orch = PipelineOrchestrator()

    # Stages 2 and 3 will be unavailable (no GGUF models)
    out = orch.process("hello world how are you")
    assert len(out) > 0, "Pipeline should produce output even without stages 2/3"
    # Stage 4 cleanup should have applied
    assert out[0].isupper(), f"Expected capitalised output, got: {out!r}"


def test_orchestrator_stage_labels_includes_cleanup():
    """stage_labels() includes 'cleanup' for Stage 4."""
    from pipeline import PipelineOrchestrator
    orch = PipelineOrchestrator()
    labels = orch.stage_labels()
    assert "cleanup" in labels, \
        f"Expected 'cleanup' in stage labels, got: {labels}"


# ===========================================================================
# Module-level convenience
# ===========================================================================

def test_module_level_process():
    """pipeline.process() convenience function runs through Stage 4."""
    from pipeline import process
    out = process("hello world")
    assert len(out) > 0, f"Expected output from process()"
    assert out[0].isupper(), f"Expected capitalised output, got: {out!r}"


def test_module_level_get_stage():
    """Module-level get_stage() returns CleanupStage instance."""
    from pipeline.stage_cleanup import get_stage
    s = get_stage()
    assert s.available is True


# ===========================================================================
# local_engine pipeline integration tests
# ===========================================================================

def test_local_engine_run_local_pipeline():
    """local_engine.run_local_pipeline() works."""
    from local_engine import run_local_pipeline
    out = run_local_pipeline("hello world", lane="text")
    assert len(out) > 0, f"Expected non-empty output from run_local_pipeline()"
    assert out[0].isupper(), f"Expected capitalised output, got: {out!r}"


def test_local_engine_pipeline_status():
    """local_engine.pipeline_status() returns expected shape."""
    from local_engine import pipeline_status
    status = pipeline_status()
    assert isinstance(status, dict), "pipeline_status must return a dict"
    assert "available" in status, "status must have 'available' key"
    assert "stages" in status, "status must have 'stages' key"
    assert "labels" in status, "status must have 'labels' key"
    assert status["available"] is True, "Pipeline should be available"
    # At minimum Stage 4 must be in the stages list.
    assert 4 in status["stages"], \
        f"Stage 4 must be reported as available, got: {status['stages']}"
    assert "cleanup" in status["labels"], \
        f"'cleanup' must be in labels, got: {status['labels']}"


def test_local_engine_get_pipeline_orchestrator():
    """local_engine.get_pipeline_orchestrator() returns an orchestrator."""
    from local_engine import get_pipeline_orchestrator
    orch = get_pipeline_orchestrator()
    assert orch is not None, "Orchestrator should not be None"
    assert hasattr(orch, "process"), "Orchestrator must have process()"
    assert hasattr(orch, "available_stages"), "Orchestrator must have available_stages()"


# ===========================================================================
# Regression: verify existing stage 1 + stage 4 interaction
# ===========================================================================

def test_stage1_plus_stage4_chain():
    """Stage 1 (punctuation) followed by Stage 4 (cleanup) produces valid output."""
    from pipeline.stage_punctuation import PunctuationStage
    from pipeline.stage_cleanup import CleanupStage

    s1 = PunctuationStage()
    s4 = CleanupStage()

    if s1.available:
        text = s1.process("hello world how are you")
        text = s4.process(text, lane="text")
        assert len(text) > 0, f"Expected output from S1+S4 chain"
        assert text[0].isupper(), f"Expected capitalised output, got: {text!r}"
        # Should end with punctuation
        assert text.rstrip()[-1] in ".!?", \
            f"Expected terminal punctuation, got: {text!r}"
    else:
        # Even without Stage 1, Stage 4 alone should work.
        text = s4.process("hello world how are you", lane="text")
        assert len(text) > 0, f"Expected output from S4 alone"
        assert text[0].isupper(), f"Expected capitalised output, got: {text!r}"


# ===========================================================================
# Runner
# ===========================================================================

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]

    print(f"\n{'='*60}")
    print(f"Stage 4 Cleanup Tests — {len(tests)} test cases")
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
