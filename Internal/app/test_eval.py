#!/usr/bin/env python3
"""Tests for eval/ harness — metrics, scenarios, and harness constructs.

Covers:
- Scenario packs: structure, lane coverage, counts
- Metrics: conciseness, wrapper, content, garbage, adherence, cost
- Harness: EvalHarness class, concurrent execution, timeout handling

Usage:
    python test_eval.py
"""

import os
import sys

_app_dir = os.path.dirname(os.path.abspath(__file__))
if _app_dir not in sys.path:
    sys.path.insert(0, _app_dir)

TESTS = []
FAILURES = []


def check(label, cond):
    TESTS.append(label)
    if not cond:
        FAILURES.append(label)
        print(f"  FAIL  {label}")
    else:
        print(f"  ok    {label}")


# =========================================================================
print("=== Import checks ===")

from eval.scenarios import get_scenarios, BASIC_SCENARIOS, EXTENDED_SCENARIOS, lane_counts

check("eval.scenarios imports", True)
check("BASIC_SCENARIOS is non-empty list",
      isinstance(BASIC_SCENARIOS, list) and len(BASIC_SCENARIOS) > 0)
check("EXTENDED_SCENARIOS is larger than BASIC",
      len(EXTENDED_SCENARIOS) > len(BASIC_SCENARIOS))

from eval.metrics import (
    count_words, count_chars, conciseness_ratio, wrapper_score,
    content_preservation, garbage_detection, adherence_score,
    estimate_cost, score,
)

check("eval.metrics imports", True)

from eval.harness import (
    EvalHarness, resolve_providers, _provider_from_name,
    LANE_RUNNERS, _run_with_timeout,
)

check("eval.harness imports", True)
check("EvalHarness class is available", callable(EvalHarness))

# =========================================================================
print("\n=== Scenario pack structure ===")

basic = get_scenarios("basic")
check("get_scenarios('basic') returns list", isinstance(basic, list))
check("basic scenarios have correct shape",
      all(isinstance(s, tuple) and len(s) == 3 for s in basic))

# Verify all 5 lanes are covered
lanes_in_basic = set(s[1] for s in basic)
expected_lanes = {"dictation", "prompt", "email", "reply", "foreign"}
check("basic covers all 5 lanes: dictation, prompt, email, reply, foreign",
      lanes_in_basic == expected_lanes)

extended = get_scenarios("extended")
check("get_scenarios('extended') returns list", isinstance(extended, list))
check("extended has more scenarios than basic", len(extended) > len(basic))

lanes_in_extended = set(s[1] for s in extended)
check("extended also covers all 5 lanes",
      lanes_in_extended == expected_lanes)

unknown = get_scenarios("nonexistent")
check("unknown pack defaults to basic", len(unknown) == len(basic))

# lane_counts
counts = lane_counts(basic)
check("lane_counts returns dict", isinstance(counts, dict))
check("lane_counts covers all lanes",
      set(counts.keys()) == expected_lanes)
check("each lane has at least 1 scenario",
      all(v >= 1 for v in counts.values()))

# =========================================================================
print("\n=== Metrics: count_words and count_chars ===")

check("count_words('hello world') == 2", count_words("hello world") == 2)
check("count_words('') == 0", count_words("") == 0)
check("count_words(None) == 0", count_words(None) == 0)
check("count_words handles multiple spaces",
      count_words("a  b   c") == 3)

check("count_chars non-zero", count_chars("hello world") > 0)
check("count_chars empty gives 1", count_chars("") == 1)

# =========================================================================
print("\n=== Metrics: conciseness_ratio ===")

check("conciseness_ratio('hello', 'hello world') == 0.5",
      conciseness_ratio("hello", "hello world") == 0.5)
check("conciseness_ratio('a b c d', 'a b') == 2.0",
      conciseness_ratio("a b c d", "a b") == 2.0)
check("conciseness_ratio handles empty input",
      conciseness_ratio("output text", "") > 0)

# =========================================================================
print("\n=== Metrics: wrapper_score ===")

check("wrapper_score clean text == 0.0",
      wrapper_score("This is clean output text.") == 0.0)
check("wrapper_score 'Here is your prompt' == 1.0",
      wrapper_score("Here is your prompt: do X") == 1.0)
check("wrapper_score 'Certainly!' == 1.0",
      wrapper_score("Certainly! Let me help you with that.") == 1.0)
check("wrapper_score 'I've written the following' == 1.0",
      wrapper_score("I've written the following email for you.") == 1.0)
check("wrapper_score 'Below is the' == 1.0",
      wrapper_score("Below is the completed prompt.") == 1.0)
check("wrapper_score 'Here's a prompt' == 1.0",
      wrapper_score("Here's a prompt for you.") == 1.0)
check("wrapper_score empty string == 0.0",
      wrapper_score("") == 0.0)

# =========================================================================
print("\n=== Metrics: content_preservation ===")

check("content_preservation identical text == 1.0",
      content_preservation("hello world", "hello world") == 1.0)
check("content_preservation partial match > 0",
      content_preservation("hello world", "hello there world today") > 0)
check("content_preservation no match < 1.0",
      content_preservation("hello world", "completely different") < 1.0)

# =========================================================================
print("\n=== Metrics: garbage_detection ===")

check("garbage_detection normal input == 0.0",
      garbage_detection("The meeting is at three.", "the meeting is at three") == 0.0)
check("garbage_detection nonsense input + coherent output == 1.0",
      garbage_detection(
          "The AI wrote a detailed analysis of the non-existent problem and "
          "provided a comprehensive solution with multiple paragraphs of "
          "invented content that was never requested by the user",
          "asdfghjkl qwertyuiop"
      ) == 1.0)
check("garbage_detection short outputs not flagged == 0.0",
      garbage_detection("short", "asdf") == 0.0)
check("garbage_detection empty output == 0.0",
      garbage_detection("", "asdfghjkl") == 0.0)

# =========================================================================
print("\n=== Metrics: adherence_score ===")

# Dictation: should be clean prose
check("adherence dictation clean prose > 0.5",
      adherence_score("The meeting is at three o'clock.", "dictation") >= 0.5)
check("adherence dictation with 'dear' is penalised",
      adherence_score("Dear team, the meeting is at three.", "dictation") < 1.0)

# Prompt: should be a usable prompt
check("adherence prompt with 'You are a' scores high",
      adherence_score("You are a helpful assistant. Please build a calculator.",
                      "prompt") > 0.7)
check("adherence prompt 'Here is' wrapper gets low score",
      adherence_score("Here is your prompt: build a calculator.", "prompt") < 0.5)

# Email: should have greeting + body + sign-off
check("adherence email with structure scores high",
      adherence_score("Hi John,\n\nJust checking in.\n\nBest,\nAlice", "email") > 0.7)
check("adherence email without sign-off is lower",
      adherence_score("Hi John,\n\nJust checking in.", "email") < 1.0)

# Reply: direct conversational response
check("adherence reply clean response > 0.5",
      adherence_score("Yes, that works for me. See you at three.", "reply") >= 0.5)
check("adherence reply wrapper is penalised",
      adherence_score("Here is your reply: Yes, that works.", "reply") < 0.5)

# Foreign: no slash markers
check("adherence foreign no slashes == 1.0",
      adherence_score("The meaning of salam is peace.", "foreign") == 1.0)
check("adherence foreign with slashes is penalised",
      adherence_score("The meaning of salam//salaam is peace.", "foreign") < 0.5)

# Unknown lane
check("adherence unknown lane returns 0.5",
      adherence_score("some text", "unknown_lane") == 0.5)

# Empty input
check("adherence empty text == 0.0",
      adherence_score("", "dictation") == 0.0)

# =========================================================================
print("\n=== Metrics: cost estimation ===")

check("estimate_cost zero prices == 0.0",
      estimate_cost(100, 50, 0.0, 0.0) == 0.0)
check("estimate_cost with prices > 0",
      estimate_cost(100, 50, 0.01, 0.02) > 0.0)
check("estimate_cost returns float",
      isinstance(estimate_cost(100, 50, 0.01, 0.02), float))

# Check cost scales with words
c1 = estimate_cost(10, 10, 0.01, 0.01)
c2 = estimate_cost(100, 100, 0.01, 0.01)
check("cost scales with word count", c2 > c1)

# =========================================================================
print("\n=== Metrics: score (combined) ===")

result = score("The meeting is at three o'clock on Tuesday.",
               "the meeting is at three o clock on tuesday",
               lane="dictation")
check("score returns dict", isinstance(result, dict))
check("score has all expected keys",
      all(k in result for k in ("conciseness_ratio", "wrapper_detected",
                                 "content_preservation", "adherence",
                                 "garbage_hallucination",
                                 "overall", "input_words", "output_words")))
check("clean output gets high score", result["overall"] >= 80)
check("wrapper_detected is False for clean output", not result["wrapper_detected"])
check("adherence present in result", "adherence" in result)

# Wrapper language should penalize
wrapper_result = score("Here is your prompt: build a calculator",
                        "build a calculator", lane="prompt")
check("wrapper output gets lower score",
      wrapper_result["overall"] < result["overall"])

# Nonsense input with hallucinated output (needs >20 out words to trigger)
garbage_result = score(
    "The AI determined the optimal approach based on comprehensive analysis "
    "of all available data sources and provided detailed recommendations "
    "for implementation across multiple departments with specific timelines "
    "and resource allocations",
    "asdfghjkl qwertyuiop", lane="dictation")
check("garbage hallucination detected", garbage_result["garbage_hallucination"])
check("garbage gets very low score", garbage_result["overall"] < 50)

# Empty output
empty_result = score("", "hello world", lane="dictation")
check("empty output gets low score", empty_result["overall"] < 70)

# =========================================================================
print("\n=== Harness: module structure ===")

harness_path = os.path.join(_app_dir, "eval", "harness.py")
check("harness.py exists", os.path.isfile(harness_path))

with open(harness_path, "r", encoding="utf-8") as f:
    source = f.read()
check("harness has EvalHarness class", "class EvalHarness" in source)
check("harness has _load_api_keys", "def _load_api_keys" in source)
check("harness has _provider_from_name", "def _provider_from_name" in source)
check("harness has resolve_providers", "def resolve_providers" in source)
check("harness has LANE_RUNNERS", "LANE_RUNNERS" in source)
check("harness has run_scenario", "def run_scenario" in source)
check("harness has summarize", "def summarize" in source)
check("harness has run_with_timeout", "def _run_with_timeout" in source)
check("harness has concurrent.futures",
      "concurrent.futures" in source)
check("harness has ThreadPoolExecutor",
      "ThreadPoolExecutor" in source)

# =========================================================================
print("\n=== Harness: EvalHarness class ===")

# Test instantiation with mock providers and scenarios
mock_providers = [
    {"name": "test", "label": "TestProvider", "url": "http://localhost",
     "key": "fake", "model": "test-model",
     "price_per_1k_input": 0.0, "price_per_1k_output": 0.0},
]
mock_scenarios = [("test-1", "dictation", "hello world")]

harness = EvalHarness(mock_providers, mock_scenarios,
                       max_workers=1, timeout=10)
check("EvalHarness instantiated", harness is not None)
check("EvalHarness has providers", len(harness.providers) == 1)
check("EvalHarness has scenarios", len(harness.scenarios) == 1)
check("EvalHarness has max_workers", harness.max_workers == 1)
check("EvalHarness has timeout", harness.timeout == 10)

# =========================================================================
print("\n=== Harness: _provider_from_name ===")

cfg = _provider_from_name("cerebras", {})
check("_provider_from_name with no keys returns None",
      cfg is None or (isinstance(cfg, dict) and cfg.get("key", "") == ""))

cfg2 = _provider_from_name("cerebras", {"cerebras_api_key": "sk-test"})
check("_provider_from_name with key returns dict",
      cfg2 is not None and isinstance(cfg2, dict))
check("_provider_from_name has expected fields",
      cfg2 and all(k in cfg2 for k in ("name", "url", "key", "model", "label")))

cfg3 = _provider_from_name("nonexistent", {})
check("_provider_from_name unknown returns None", cfg3 is None)

# =========================================================================
print("\n=== Harness: resolve_providers ===")

provs, skipped = resolve_providers(
    ["cerebras", "openrouter", "nonexistent"],
    {"cerebras_api_key": "sk-test", "openrouter_api_key": "sk-test2"}
)
check("resolve_providers returns valid list", isinstance(provs, list))
check("resolve_providers skips unknown", "nonexistent" in skipped)
check("resolve_providers valid count", len(provs) >= 0)

# No keys -> all skipped
provs2, skipped2 = resolve_providers(["cerebras", "openrouter"], {})
check("resolve_providers empty keys skips all",
      len(provs2) == 0 and len(skipped2) >= 1)

# =========================================================================
print("\n=== Harness: LANE_RUNNERS ===")

check("LANE_RUNNERS has dictation", "dictation" in LANE_RUNNERS)
check("LANE_RUNNERS has prompt", "prompt" in LANE_RUNNERS)
check("LANE_RUNNERS has email", "email" in LANE_RUNNERS)
check("LANE_RUNNERS has reply", "reply" in LANE_RUNNERS)
check("LANE_RUNNERS has foreign", "foreign" in LANE_RUNNERS)
check("LANE_RUNNERS values are callable",
      all(callable(v) for v in LANE_RUNNERS.values()))

# =========================================================================
print("\n=== Harness: _run_with_timeout (functional test) ===")

def _fast_func(*args, **kwargs):
    time.sleep(0.01)
    return ("result text", 0.01, None)

def _slow_func(*args, **kwargs):
    time.sleep(5.0)
    return ("slow result", 5.0, None)

import time

# Fast function completes within timeout
output, latency, error = _run_with_timeout(_fast_func, "input", timeout=5)
check("_run_with_timeout fast fn returns output",
      output == "result text")
check("_run_with_timeout fast fn no error",
      error is None)

# Slow function exceeds timeout — graceful fallback
output2, latency2, error2 = _run_with_timeout(_slow_func, "input", timeout=0.5)
check("_run_with_timeout slow fn timeout produces error",
      error2 is not None and "timeout" in str(error2).lower())
check("_run_with_timeout slow fn empty output on timeout",
      output2 == "")

# =========================================================================
print("\n=== Harness: summarize ===")

from eval.harness import summarize as harness_summarize
mock_results = [
    {"scenario": "s1", "provider": "P1", "lane": "dictation",
     "latency_s": 1.0, "input_words": 10, "output_words": 12,
     "estimated_cost_usd": 0.0001, "overall": 90.0,
     "wrapper_detected": False, "garbage_hallucination": False, "error": None},
    {"scenario": "s2", "provider": "P1", "lane": "prompt",
     "latency_s": 2.0, "input_words": 15, "output_words": 80,
     "estimated_cost_usd": 0.0005, "overall": 85.0,
     "wrapper_detected": True, "garbage_hallucination": False, "error": None},
    {"scenario": "s3", "provider": "P2", "lane": "dictation",
     "latency_s": 1.5, "input_words": 10, "output_words": 14,
     "estimated_cost_usd": 0.0002, "overall": 75.0,
     "wrapper_detected": False, "garbage_hallucination": False,
     "error": "timeout: AI request exceeded 120s limit"},
]

summary = harness_summarize(mock_results)
check("summarize returns dict", isinstance(summary, dict))
check("summarize has both providers", set(summary.keys()) == {"P1", "P2"})
check("P1 scenarios_run == 2", summary["P1"]["scenarios_run"] == 2)
check("P1 wrapper_language_in == 1", summary["P1"]["wrapper_language_in"] == 1)
check("P2 error_count == 1", summary["P2"]["error_count"] == 1)
check("P2 timeouts == 1", summary["P2"]["timeouts"] == 1)
check("avg scores are numeric", isinstance(summary["P1"]["avg_overall_score"], float))
check("total cost is numeric", isinstance(summary["P1"]["total_cost_usd"], float))

# =========================================================================
print("\n=== Backward compatibility ===")

# run_scenario should work
try:
    from eval.harness import run_scenario, summarize as mod_summarize
    check("run_scenario importable", True)
    check("summarize importable", True)
except ImportError as e:
    check(f"import error: {e}", False)

# =========================================================================
print(f"\n{'=' * 60}")
print(f"RESULTS: {len(TESTS) - len(FAILURES)}/{len(TESTS)} passed")
if FAILURES:
    print(f"FAILURES: {len(FAILURES)}")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("All tests passed.")
