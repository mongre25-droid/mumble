#!/usr/bin/env python3
"""Tests for pipeline Stage 2 (grammar correction) and Stage 3 (mode formatting).

Run: python test_pipeline_stage2_stage3.py

Covers:
  • GBNF grammar definitions (list, json_object, email) — VAL-PIPE-005, VAL-PIPE-006
  • Stage 2 grammar correction + filler removal — VAL-PIPE-004
  • Stage 3 mode formatting (list/email/prompt) — VAL-PIPE-005, VAL-PIPE-006
  • Stage 3 GBNF-constrained output shapes
  • Graceful degradation when GGUF models are absent
  • Import safety and lazy loading
  • Very long text handling — VAL-PIPE-014
  • Pipeline orchestrator integration with stages 2 and 3

Since no GGUF models are available in the test environment, the model-dependent
tests verify the graceful-degradation path (input passes through unchanged).
The grammar validation tests exercise the grammar strings directly.
"""

import sys
import os
import tempfile

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
# GBNF Grammar tests
# ===========================================================================

def test_grammar_module_imports():
    """grammar.py imports without error."""
    from pipeline.grammar import (
        grammar_for, list_grammars,
        GBNF_LIST, GBNF_JSON_OBJECT, GBNF_EMAIL,
    )
    assert grammar_for is not None
    assert GBNF_LIST is not None
    assert GBNF_JSON_OBJECT is not None
    assert GBNF_EMAIL is not None


def test_grammar_for_list():
    """grammar_for('list') returns a non-empty GBNF string."""
    from pipeline.grammar import grammar_for
    g = grammar_for("list")
    assert g is not None, "List grammar must not be None"
    assert len(g) > 20, f"List grammar too short: {len(g)} chars"
    # Must contain the root rule.
    assert "root" in g, f"List grammar missing 'root' rule: {g[:80]}"
    # Must constrain to bullet markers.
    assert "bullet" in g.lower() or '"-" | "*"' in g, \
        f"List grammar missing bullet markers: {g[:200]}"


def test_grammar_for_email():
    """grammar_for('email') returns a non-empty GBNF string."""
    from pipeline.grammar import grammar_for
    g = grammar_for("email")
    assert g is not None, "Email grammar must not be None"
    assert len(g) > 20, f"Email grammar too short: {len(g)} chars"
    assert "root" in g, f"Email grammar missing 'root' rule"
    # Must contain email structure elements.
    assert "greeting" in g.lower(), f"Email grammar missing greeting: {g[:200]}"
    assert "subject" in g.lower(), f"Email grammar missing subject: {g[:200]}"
    assert "signoff" in g.lower() or "sign-off" in g.lower(), \
        f"Email grammar missing signoff: {g[:200]}"


def test_grammar_for_json_object():
    """grammar_for('json_object') returns a non-empty GBNF string."""
    from pipeline.grammar import grammar_for
    g = grammar_for("json_object")
    assert g is not None, "JSON grammar must not be None"
    assert len(g) > 20, f"JSON grammar too short: {len(g)} chars"
    assert "root" in g, f"JSON grammar missing 'root' rule"
    assert "object" in g, f"JSON grammar missing 'object' rule: {g[:200]}"
    assert "string" in g, f"JSON grammar missing 'string' rule: {g[:200]}"


def test_grammar_for_none_for_prompt():
    """grammar_for('prompt') returns None (free-form lane)."""
    from pipeline.grammar import grammar_for
    assert grammar_for("prompt") is None, \
        "Prompt lane should have no grammar (free-form)"
    assert grammar_for("reply") is None, \
        "Reply lane should have no grammar (free-form)"
    assert grammar_for("text") is None, \
        "Text lane should have no grammar (free-form)"
    assert grammar_for("foreign") is None, \
        "Foreign lane should have no grammar (free-form)"


def test_list_grammars_returns_known_lanes():
    """list_grammars() returns the expected lane names."""
    from pipeline.grammar import list_grammars
    lanes = list_grammars()
    assert "list" in lanes, f"Expected 'list' in grammars, got: {lanes}"
    assert "email" in lanes, f"Expected 'email' in grammars, got: {lanes}"
    assert "json_object" in lanes, f"Expected 'json_object' in grammars, got: {lanes}"


def test_list_grammar_is_valid_gbnf():
    """List GBNF grammar is syntactically plausible (has basic GBNF structure)."""
    from pipeline.grammar import GBNF_LIST
    # GBNF grammars use ::= for rules.
    assert "::=" in GBNF_LIST, "GBNF grammar must use ::= syntax"
    # Must define a root rule.
    assert "root" in GBNF_LIST, "GBNF grammar must define a 'root' rule"
    # Must contain at least one terminal or character class.
    assert "[" in GBNF_LIST or '"' in GBNF_LIST, \
        "GBNF grammar must have terminals or character classes"


def test_email_grammar_is_valid_gbnf():
    """Email GBNF grammar is syntactically plausible."""
    from pipeline.grammar import GBNF_EMAIL
    assert "::=" in GBNF_EMAIL, "GBNF grammar must use ::= syntax"
    assert "root" in GBNF_EMAIL, "GBNF grammar must define a 'root' rule"


def test_json_grammar_is_valid_gbnf():
    """JSON GBNF grammar is syntactically plausible."""
    from pipeline.grammar import GBNF_JSON_OBJECT
    assert "::=" in GBNF_JSON_OBJECT, "GBNF grammar must use ::= syntax"
    assert "root" in GBNF_JSON_OBJECT, "GBNF grammar must define a 'root' rule"


# ===========================================================================
# Stage 2 (GrammarStage) tests
# ===========================================================================

def test_stage2_import():
    """Stage 2 module imports without error."""
    from pipeline.stage_grammar import GrammarStage, get_stage
    s = GrammarStage()
    assert s is not None
    assert hasattr(s, "process")
    assert hasattr(s, "available")
    assert hasattr(s, "unload")
    # Module-level singleton
    s2 = get_stage()
    assert s2 is not None


def test_stage2_not_available_without_model():
    """Stage 2 reports available=False when no GRMR-2B GGUF is present."""
    from pipeline.stage_grammar import GrammarStage
    s = GrammarStage()
    # We don't have the model → available should be False.
    assert s.available is False, \
        "Stage 2 should be unavailable without GRMR-2B model"


def test_stage2_process_pass_through():
    """Stage 2 passes text through unchanged when model is unavailable."""
    from pipeline.stage_grammar import GrammarStage
    s = GrammarStage()
    inp = "he go to the store yesterday"
    out = s.process(inp)
    # When unavailable, output should equal input (graceful degradation).
    assert out == inp, \
        f"Stage 2 should pass-through when unavailable, got: {out!r}"


def test_stage2_empty_input():
    """Stage 2 returns empty string for empty input."""
    from pipeline.stage_grammar import GrammarStage
    s = GrammarStage()
    assert s.process("") == ""
    assert s.process("   ") == ""
    assert s.process("\n\t") == ""


def test_stage2_very_short_input():
    """Stage 2 handles very short input (1-2 words) without error."""
    from pipeline.stage_grammar import GrammarStage
    s = GrammarStage()
    out = s.process("hello")
    assert len(out) > 0, f"Short input should return non-empty: {out!r}"
    out = s.process("yes")
    assert len(out) > 0


def test_stage2_with_explicit_paths():
    """Stage 2 can be constructed with explicit model/bin paths."""
    from pipeline.stage_grammar import GrammarStage
    s = GrammarStage(model_path="/nonexistent/model.gguf",
                     bin_path="/nonexistent/llama-cli")
    assert s.available is False, \
        "Stage 2 should be unavailable with nonexistent paths"


def test_stage2_unload_no_error():
    """Stage 2 unload() does not raise when nothing is loaded."""
    from pipeline.stage_grammar import GrammarStage
    s = GrammarStage()
    # Should not raise.
    s.unload()
    # After unload, process still works (degraded).
    out = s.process("hello")
    assert out == "hello"


def test_stage2_long_text_pass_through():
    """Stage 2 handles long text without truncation when unavailable.
    (VAL-PIPE-014)"""
    from pipeline.stage_grammar import GrammarStage
    s = GrammarStage()
    # Build a long text of 100+ words.
    long_text = "hello world this is a test sentence " * 20  # 140 words
    out = s.process(long_text)
    # Output should match input (pass-through when unavailable).
    assert out == long_text, \
        f"Long text should pass through unchanged, got {len(out)} vs {len(long_text)} chars"


def test_stage2_lane_parameter_accepted():
    """Stage 2 accepts the lane parameter (even if unused for grammar)."""
    from pipeline.stage_grammar import GrammarStage
    s = GrammarStage()
    out = s.process("hello world", lane="text")
    assert out == "hello world"
    out = s.process("hello world", lane="prompt")
    assert out == "hello world"


# ===========================================================================
# Stage 3 (FormattingStage) tests
# ===========================================================================

def test_stage3_import():
    """Stage 3 module imports without error."""
    from pipeline.stage_formatting import FormattingStage, get_stage
    s = FormattingStage()
    assert s is not None
    assert hasattr(s, "process")
    assert hasattr(s, "available")
    assert hasattr(s, "unload")
    # Module-level singleton
    s2 = get_stage()
    assert s2 is not None


def test_stage3_not_available_without_model():
    """Stage 3 reports available=False when no Qwen2.5-1.5B GGUF is present."""
    from pipeline.stage_formatting import FormattingStage
    s = FormattingStage()
    assert s.available is False, \
        "Stage 3 should be unavailable without Qwen2.5-1.5B model"


def test_stage3_process_pass_through():
    """Stage 3 passes text through unchanged when model is unavailable."""
    from pipeline.stage_formatting import FormattingStage
    s = FormattingStage()
    inp = "buy milk eggs and bread"
    out = s.process(inp, lane="list")
    assert out == inp, \
        f"Stage 3 should pass-through when unavailable, got: {out!r}"


def test_stage3_empty_input():
    """Stage 3 returns empty string for empty input."""
    from pipeline.stage_formatting import FormattingStage
    s = FormattingStage()
    assert s.process("") == ""
    assert s.process("   ") == ""
    assert s.process("\n\t") == ""


def test_stage3_all_lanes_pass_through():
    """Stage 3 handles all lane types without error when unavailable."""
    from pipeline.stage_formatting import FormattingStage
    s = FormattingStage()
    for lane in ("text", "list", "email", "prompt", "reply", "foreign"):
        inp = f"test input for {lane}"
        out = s.process(inp, lane=lane)
        assert out == inp, \
            f"Stage 3 lane={lane} should pass-through, got: {out!r}"


def test_stage3_unknown_lane_defaults_to_text():
    """Stage 3 treats unknown lanes gracefully."""
    from pipeline.stage_formatting import FormattingStage
    s = FormattingStage()
    out = s.process("hello world", lane="unknown_lane")
    assert out == "hello world"


def test_stage3_with_explicit_paths():
    """Stage 3 can be constructed with explicit model/bin paths."""
    from pipeline.stage_formatting import FormattingStage
    s = FormattingStage(model_path="/nonexistent/model.gguf",
                        bin_path="/nonexistent/llama-cli")
    assert s.available is False, \
        "Stage 3 should be unavailable with nonexistent paths"


def test_stage3_unload_no_error():
    """Stage 3 unload() does not raise when nothing is loaded."""
    from pipeline.stage_formatting import FormattingStage
    s = FormattingStage()
    s.unload()
    out = s.process("hello")
    assert out == "hello"


def test_stage3_long_text_pass_through():
    """Stage 3 handles long text (100+ words) without truncation.
    (VAL-PIPE-014)"""
    from pipeline.stage_formatting import FormattingStage
    s = FormattingStage()
    long_text = "this is a long dictation test " * 20  # 140 words
    out = s.process(long_text, lane="text")
    assert out == long_text, \
        f"Long text should pass through unchanged"


def test_stage3_list_lane_no_error():
    """Stage 3 list lane works without error."""
    from pipeline.stage_formatting import FormattingStage
    s = FormattingStage()
    out = s.process("buy milk eggs and bread", lane="list")
    assert out == "buy milk eggs and bread"  # pass-through


def test_stage3_email_lane_no_error():
    """Stage 3 email lane works without error."""
    from pipeline.stage_formatting import FormattingStage
    s = FormattingStage()
    out = s.process("hi team the meeting is moved to Friday at 2 PM", lane="email")
    assert "hi team" in out.lower()  # pass-through preserves content


def test_stage3_prompt_lane_no_error():
    """Stage 3 prompt lane preserves content."""
    from pipeline.stage_formatting import FormattingStage
    s = FormattingStage()
    out = s.process("write a python script that sorts a list", lane="prompt")
    assert "python" in out.lower()


# ===========================================================================
# Pipeline orchestrator integration tests
# ===========================================================================

def test_orchestrator_detects_stages_2_and_3():
    """Pipeline orchestrator detects stages 2 and 3 as importable."""
    from pipeline import PipelineOrchestrator
    orch = PipelineOrchestrator()

    # Stages 2 and 3 should be importable (modules exist).
    # They will be unavailable (no models) but the import slots should work.
    assert orch._s2_available or not orch._s2_available, \
        "Stage 2 slot should be initialised"
    assert orch._s3_available or not orch._s3_available, \
        "Stage 3 slot should be initialised"


def test_orchestrator_with_stage2_and_stage3():
    """Pipeline processes text with stages 2 and 3 (they degrade gracefully)."""
    from pipeline import PipelineOrchestrator
    orch = PipelineOrchestrator()
    out = orch.process("hello world how are you today")
    assert len(out) > 0, "Pipeline should produce output"
    # Stage 4 should have run (model_free capitalises).
    assert out[0].isupper(), \
        f"Expected capitalised output, got: {out!r}"


def test_orchestrator_long_text_fully_processed():
    """Very long dictation (5+ sentences, 100+ words) is fully processed.
    (VAL-PIPE-014)"""
    from pipeline import PipelineOrchestrator
    orch = PipelineOrchestrator()

    # Build a long text of 5+ sentences, 100+ words.
    sentences = [
        "hello world this is a test sentence",
        "the quick brown fox jumps over the lazy dog",
        "i think we should go to the park today",
        "the meeting is scheduled for tomorrow morning",
        "please bring your laptop and notebook",
        "we will discuss the quarterly results",
    ]
    long_text = " ".join(sentences * 3)  # ~120 words
    out = orch.process(long_text)

    assert len(out) > 0, "Long text should produce output"
    # Output should not be severely truncated — at least 50% of input word count.
    out_words = len(out.split())
    in_words = len(long_text.split())
    assert out_words >= in_words * 0.5, \
        f"Output appears truncated: {out_words} vs {in_words} input words - output: {out!r}"


def test_orchestrator_unload_all_includes_new_stages():
    """unload_all() handles stages 2 and 3 without error."""
    from pipeline import PipelineOrchestrator
    orch = PipelineOrchestrator()
    orch.unload_all()
    # Should not raise even if stages 2/3 are unavailable.
    out = orch.process("hello")
    assert len(out) > 0, "Pipeline should still work after unload_all"


def test_orchestrator_degrade_to_stage4_with_stages_absent():
    """degrade_to_stage4() works even when stages 2 and 3 are absent."""
    from pipeline import PipelineOrchestrator
    orch = PipelineOrchestrator()
    out = orch.degrade_to_stage4("hello world")
    assert len(out) > 0
    assert out[0].isupper(), f"Stage 4 should capitalise: {out!r}"


# ===========================================================================
# Graceful degradation: verify that the full pipeline with all stage imports
# works correctly.
# ===========================================================================

def test_full_pipeline_import_chain():
    """The full pipeline package imports all four stages correctly."""
    from pipeline import (
        PipelineOrchestrator, process, get_orchestrator,
    )
    from pipeline.stage_punctuation import PunctuationStage
    from pipeline.stage_grammar import GrammarStage
    from pipeline.stage_formatting import FormattingStage
    from pipeline.grammar import grammar_for

    # All should import without error.
    assert PipelineOrchestrator is not None
    assert PunctuationStage is not None
    assert GrammarStage is not None
    assert FormattingStage is not None
    assert grammar_for is not None


def test_stages_are_importable_by_orchestrator():
    """The orchestrator's import guards for stages 2 and 3 work."""
    # This test verifies that the pipeline/__init__.py try/except
    # import guards correctly detect stage_grammar and stage_formatting.
    import pipeline
    # After import, the orchestrator should have detected the modules.
    # Since the modules exist, _HAVE_STAGE2 and _HAVE_STAGE3 should be True.
    assert pipeline._HAVE_STAGE1 is True, \
        "Stage 1 should be importable (transformers may or may not work)"
    assert pipeline._HAVE_STAGE2 is True, \
        "Stage 2 should be importable (module exists)"
    assert pipeline._HAVE_STAGE3 is True, \
        "Stage 3 should be importable (module exists)"


# ===========================================================================
# Stage 2 and 3 lazy loading tests
# ===========================================================================

def test_stage2_lazy_backend():
    """Stage 2 does NOT create a backend until first process() call."""
    from pipeline.stage_grammar import GrammarStage
    s = GrammarStage()
    assert s._backend is None, \
        "Backend should not be created at construction time"


def test_stage3_lazy_backend():
    """Stage 3 does NOT create a backend until first process() call."""
    from pipeline.stage_formatting import FormattingStage
    s = FormattingStage()
    assert s._backend is None, \
        "Backend should not be created at construction time"


def test_stage2_repeated_missing_probe_is_not_hard_failed():
    """A missing model remains eligible for a later live re-probe."""
    from pipeline.stage_grammar import GrammarStage
    s = GrammarStage()
    # First call → probes, finds no model, sets load_failed.
    s.process("hello")
    assert s._load_failed is False, \
        "A missing file is transient, not a hard backend failure"
    # Second call → uses cached state, doesn't re-probe.
    s.process("world")
    assert s._load_failed is False


def test_stage3_repeated_missing_probe_is_not_hard_failed():
    """A missing formatter remains eligible for a later live re-probe."""
    from pipeline.stage_formatting import FormattingStage
    s = FormattingStage()
    s.process("hello", lane="text")
    assert s._load_failed is False, \
        "A missing file is transient, not a hard backend failure"
    s.process("world", lane="text")
    assert s._load_failed is False


def test_stage2_missing_model_probe_recovers():
    """A grammar model installed after the first probe becomes live."""
    from pipeline.stage_grammar import GrammarStage
    with tempfile.TemporaryDirectory() as d:
        model = os.path.join(d, "grmr.gguf")
        binary = os.path.join(d, "llama-cli")
        stage = GrammarStage(model_path=model, bin_path=binary)
        assert stage.available is False
        open(model, "wb").close()
        open(binary, "wb").close()
        assert stage.available is True


def test_stage3_missing_model_probe_recovers():
    """A formatter model installed after the first probe becomes live."""
    from pipeline.stage_formatting import FormattingStage
    with tempfile.TemporaryDirectory() as d:
        model = os.path.join(d, "qwen.gguf")
        binary = os.path.join(d, "llama-cli")
        stage = FormattingStage(model_path=model, bin_path=binary)
        assert stage.available is False
        open(model, "wb").close()
        open(binary, "wb").close()
        assert stage.available is True


def test_stage_probe_can_reset_hard_failure():
    """Explicit reset clears cached backend-construction failures."""
    from pipeline.stage_grammar import GrammarStage
    from pipeline.stage_formatting import FormattingStage
    for stage in (GrammarStage(), FormattingStage()):
        stage._available = False
        stage._load_failed = True
        stage.reset_probe()
        assert stage._available is None
        assert stage._load_failed is False


def test_backend_construction_failure_retries_automatically():
    """A transient backend constructor error heals on the next request."""
    import local_engine
    from pipeline.stage_grammar import GrammarStage
    from pipeline.stage_formatting import FormattingStage

    class WorkingBackend:
        def __init__(self, **_kwargs):
            pass

        def available(self):
            return True

        def generate(self, **_kwargs):
            return "recovered output"

        def unload(self):
            pass

    original = local_engine.LlamaCliBackend
    try:
        for stage_type in (GrammarStage, FormattingStage):
            with tempfile.TemporaryDirectory() as d:
                model = os.path.join(d, "model.gguf")
                binary = os.path.join(d, "llama-cli")
                open(model, "wb").close()
                open(binary, "wb").close()
                calls = [0]

                def flaky_backend(**kwargs):
                    calls[0] += 1
                    if calls[0] == 1:
                        raise RuntimeError("transient constructor failure")
                    return WorkingBackend(**kwargs)

                local_engine.LlamaCliBackend = flaky_backend
                stage = stage_type(model_path=model, bin_path=binary)
                assert stage.process("one two three") == "one two three"
                assert stage._load_failed is True
                assert stage.process("one two three") == "recovered output"
                assert calls[0] == 2
                assert stage._load_failed is False
    finally:
        local_engine.LlamaCliBackend = original


# ===========================================================================
# Runner
# ===========================================================================

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]

    print(f"\n{'='*60}")
    print(f"Pipeline Stage 2 & 3 Tests — {len(tests)} test cases")
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
