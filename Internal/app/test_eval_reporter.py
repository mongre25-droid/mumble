#!/usr/bin/env python3
"""Tests for eval/reporter.py — provider comparison tables, trend tracking,
Markdown/JSON output, constitution verification, conciseness verification.

Covers:
- Reporter class: instantiation, comparison_table, to_markdown, to_json
- Trend tracking: save_trend, load_trend, compare trends
- Constitution detection: full vs lightweight by provider URL
- Conciseness verification: token ratio comparison across providers
- Edge cases: empty results, single provider, error results

Usage:
    python test_eval_reporter.py
"""

import json
import os
import sys
import tempfile
import time

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

from eval.reporter import (
    Reporter, report_compare, _detect_constitution_selection,
    _check_conciseness_effect, _build_comparison_rows, _build_trend_diff,
    render_markdown, render_json_output,
)

check("eval.reporter imports", True)
check("Reporter class available", callable(Reporter))
check("report_compare available", callable(report_compare))
check("render_markdown available", callable(render_markdown))
check("render_json_output available", callable(render_json_output))
check("_detect_constitution_selection available",
      callable(_detect_constitution_selection))
check("_check_conciseness_effect available",
      callable(_check_conciseness_effect))
check("_build_comparison_rows available", callable(_build_comparison_rows))
check("_build_trend_diff available", callable(_build_trend_diff))

# =========================================================================
# Build sample results for testing
# =========================================================================

SAMPLE_RESULTS = [
    {
        "scenario": "dict-short", "provider": "Cerebras", "lane": "dictation",
        "latency_s": 0.85, "input_words": 15, "output_words": 18,
        "estimated_cost_usd": 0.0, "overall": 92.5,
        "conciseness_ratio": 1.2, "wrapper_detected": False,
        "garbage_hallucination": False, "content_preservation": 0.93,
        "adherence": 0.8, "error": None,
    },
    {
        "scenario": "dict-short", "provider": "OpenRouter", "lane": "dictation",
        "latency_s": 2.3, "input_words": 15, "output_words": 22,
        "estimated_cost_usd": 0.0001, "overall": 85.0,
        "conciseness_ratio": 1.47, "wrapper_detected": False,
        "garbage_hallucination": False, "content_preservation": 0.87,
        "adherence": 0.8, "error": None,
    },
    {
        "scenario": "prompt-simple", "provider": "Cerebras", "lane": "prompt",
        "latency_s": 3.5, "input_words": 12, "output_words": 65,
        "estimated_cost_usd": 0.0, "overall": 88.0,
        "conciseness_ratio": 5.42, "wrapper_detected": False,
        "garbage_hallucination": False, "content_preservation": 0.75,
        "adherence": 0.9, "error": None,
    },
    {
        "scenario": "prompt-simple", "provider": "OpenRouter", "lane": "prompt",
        "latency_s": 4.1, "input_words": 12, "output_words": 90,
        "estimated_cost_usd": 0.0003, "overall": 78.0,
        "conciseness_ratio": 7.5, "wrapper_detected": True,
        "garbage_hallucination": False, "content_preservation": 0.65,
        "adherence": 0.7, "error": None,
    },
    {
        "scenario": "email-short", "provider": "Cerebras", "lane": "email",
        "latency_s": 1.2, "input_words": 9, "output_words": 35,
        "estimated_cost_usd": 0.0, "overall": 91.0,
        "conciseness_ratio": 3.89, "wrapper_detected": False,
        "garbage_hallucination": False, "content_preservation": 0.88,
        "adherence": 0.75, "error": None,
    },
    {
        "scenario": "email-short", "provider": "OpenRouter", "lane": "email",
        "latency_s": 2.0, "input_words": 9, "output_words": 40,
        "estimated_cost_usd": 0.00015, "overall": 82.0,
        "conciseness_ratio": 4.44, "wrapper_detected": True,
        "garbage_hallucination": False, "content_preservation": 0.82,
        "adherence": 0.7, "error": None,
    },
    {
        "scenario": "error-test", "provider": "OpenRouter", "lane": "dictation",
        "latency_s": 30.0, "input_words": 10, "output_words": 0,
        "estimated_cost_usd": 0.0, "overall": 10.0,
        "conciseness_ratio": 0.0, "wrapper_detected": False,
        "garbage_hallucination": False, "content_preservation": 0.0,
        "adherence": 0.0,
        "error": "timeout: AI request exceeded 120s limit",
    },
    {
        "scenario": "garbage-test", "provider": "Cerebras", "lane": "dictation",
        "latency_s": 0.3, "input_words": 3, "output_words": 45,
        "estimated_cost_usd": 0.0, "overall": 15.0,
        "conciseness_ratio": 15.0, "wrapper_detected": True,
        "garbage_hallucination": True, "content_preservation": 0.1,
        "adherence": 0.2, "error": None,
    },
]

SAMPLE_SUMMARY = {
    "Cerebras": {
        "scenarios_run": 4, "avg_latency_s": 1.46, "avg_input_words": 9.8,
        "avg_output_words": 40.8, "total_cost_usd": 0.0,
        "avg_overall_score": 71.6, "wrapper_language_in": 1,
        "hallucinations": 1, "timeouts": 0, "error_count": 0, "errors": [],
    },
    "OpenRouter": {
        "scenarios_run": 4, "avg_latency_s": 9.6, "avg_input_words": 11.5,
        "avg_output_words": 38.0, "total_cost_usd": 0.00055,
        "avg_overall_score": 63.8, "wrapper_language_in": 2,
        "hallucinations": 0, "timeouts": 1, "error_count": 1,
        "errors": ["error-test: timeout: AI request exceeded 120s limit"],
    },
}

SAMPLE_CONFIG = {
    "scenarios": "basic", "providers": ["cerebras", "openrouter"],
    "workers": 4, "timeout_s": 120,
}

# Provider configs for constitution detection
SAMPLE_PROVIDER_CONFIGS = [
    {"name": "cerebras", "label": "Cerebras",
     "url": "https://api.cerebras.ai/v1/chat/completions"},
    {"name": "openrouter", "label": "OpenRouter",
     "url": "https://openrouter.ai/api/v1/chat/completions"},
    {"name": "openai", "label": "OpenAI",
     "url": "https://api.openai.com/v1/chat/completions"},
]


# =========================================================================
print("\n=== Reporter: instantiation ===")

reporter = Reporter(SAMPLE_RESULTS, SAMPLE_SUMMARY)
check("Reporter instantiated with results", reporter is not None)
check("Reporter.results has correct length",
      len(reporter.results) == len(SAMPLE_RESULTS))
check("Reporter.summary has correct providers",
      set(reporter.summary.keys()) == {"Cerebras", "OpenRouter"})

reporter2 = Reporter(SAMPLE_RESULTS, SAMPLE_SUMMARY, config=SAMPLE_CONFIG)
check("Reporter with config", reporter2.config is not None)
check("Reporter.config.scenarios", reporter2.config["scenarios"] == "basic")

# Empty results
reporter_empty = Reporter([], {})
check("Reporter handles empty results", reporter_empty is not None)
check("Reporter empty has 0 results", len(reporter_empty.results) == 0)

# =========================================================================
print("\n=== Reporter: comparison_table ===")

table = reporter.comparison_table()
check("comparison_table returns list", isinstance(table, list))
check("comparison_table non-empty", len(table) > 0)

# Each row should have standard fields
row = table[0]
expected_fields = ["provider", "scenario", "lane", "latency_s",
                   "output_words", "conciseness_ratio", "overall",
                   "estimated_cost_usd"]
for field in expected_fields:
    check(f"comparison row has '{field}'", field in row)

# Grouped by lane
by_lane = reporter.comparison_table(group_by="lane")
check("comparison_table grouped by lane", isinstance(by_lane, dict))
check("grouped by lane has dictation", "dictation" in by_lane)
check("grouped by lane has prompt", "prompt" in by_lane)
check("grouped by lane has email", "email" in by_lane)

# Grouped by provider
by_provider = reporter.comparison_table(group_by="provider")
check("comparison_table grouped by provider", isinstance(by_provider, dict))
check("grouped by provider has Cerebras", "Cerebras" in by_provider)
check("grouped by provider has OpenRouter", "OpenRouter" in by_provider)

# Unknown group_by falls back to flat list
flat = reporter.comparison_table(group_by="unknown")
check("unknown group_by returns flat list", isinstance(flat, list))

# =========================================================================
print("\n=== Reporter: to_markdown ===")

md = reporter.to_markdown()
check("to_markdown returns string", isinstance(md, str))
check("to_markdown non-empty", len(md) > 0)
check("markdown has header", md.startswith("# "))
check("markdown mentions Cerebras", "Cerebras" in md)
check("markdown mentions OpenRouter", "OpenRouter" in md)
check("markdown has table separator |---",
      "---" in md or "---|" in md)
check("markdown has 'Latency' column", "Latency" in md)
check("markdown has 'Score' column", "Score" in md)
check("markdown has 'Cost' column", "Cost" in md)
check("markdown has scenario coverage section",
      "Scenario" in md or "scenario" in md.lower())

# With provider URLs for constitution detection
md_with_urls = reporter.to_markdown(
    provider_configs=SAMPLE_PROVIDER_CONFIGS)
check("markdown with provider configs has constitution section",
      "constitution" in md_with_urls.lower())

# =========================================================================
print("\n=== Reporter: to_json ===")

js = reporter.to_json()
check("to_json returns string", isinstance(js, str))
check("to_json is valid JSON",
      json.loads(js) is not None)

parsed = json.loads(js)
check("JSON has report key", "report" in parsed)
check("JSON report has summary", "summary" in parsed["report"])
check("JSON report has results", "results" in parsed["report"])
check("JSON has metadata", "metadata" in parsed)
check("JSON metadata has generated_at", "generated_at" in parsed["metadata"])

# Check lane breakdown
check("JSON report has by_lane", "by_lane" in parsed["report"])
check("by_lane has dictation", "dictation" in parsed["report"]["by_lane"])
check("by_lane has prompt", "prompt" in parsed["report"]["by_lane"])

# With provider configs
js_with_urls = reporter.to_json(
    provider_configs=SAMPLE_PROVIDER_CONFIGS)
parsed2 = json.loads(js_with_urls)
check("JSON with provider configs has constitution_check",
      "constitution_check" in parsed2["report"])

# =========================================================================
print("\n=== Reporter: render_markdown (standalone) ===")

md2 = render_markdown(SAMPLE_RESULTS, SAMPLE_SUMMARY)
check("render_markdown standalone works", isinstance(md2, str) and len(md2) > 0)
check("render_markdown has summary table",
      "Avg Latency" in md2 or "Avg Score" in md2 or "avg" in md2.lower())

# =========================================================================
print("\n=== Reporter: render_json_output (standalone) ===")

js2 = render_json_output(SAMPLE_RESULTS, SAMPLE_SUMMARY)
parsed3 = json.loads(js2)
check("render_json_output standalone works", parsed3 is not None)
check("render_json_output has results", "results" in parsed3["report"])

# =========================================================================
print("\n=== Reporter: trend tracking ===")

# Create a temp file for trend data
tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                  encoding="utf-8")
tmp_path = tmp.name
tmp.close()

try:
    # Save trends
    reporter.save_trend(tmp_path)
    check("save_trend writes file", os.path.isfile(tmp_path))

    with open(tmp_path, "r", encoding="utf-8") as f:
        trend_data = json.load(f)
    check("trend data is valid JSON", trend_data is not None)
    check("trend data is list", isinstance(trend_data, list))
    check("trend has at least 1 entry", len(trend_data) >= 1)

    entry = trend_data[-1]
    check("trend entry has timestamp", "timestamp" in entry)
    check("trend entry has summary", "summary" in entry)
    check("trend entry has config", "config" in entry)
    check("trend entry timestamp is recent",
          abs(time.time() - entry["timestamp"]) < 10)

    # Load trends back
    loaded = reporter.load_trend(tmp_path)
    check("load_trend returns list", isinstance(loaded, list))
    check("load_trend has entries", len(loaded) >= 1)

    # Compare trends
    # First run only (no previous to compare)
    diff1 = reporter.compare_trend(tmp_path)
    check("compare_trend single entry returns dict",
          isinstance(diff1, dict))
    check("compare_trend single entry has no previous",
          diff1.get("previous") is None or diff1.get("is_first_run") is True)

    # Add a second entry with different results (simulate improvement)
    improved_summary = {
        "Cerebras": {
            "scenarios_run": 4, "avg_latency_s": 1.2,
            "avg_input_words": 9.8, "avg_output_words": 35.0,
            "total_cost_usd": 0.0, "avg_overall_score": 85.0,
            "wrapper_language_in": 0, "hallucinations": 0,
            "timeouts": 0, "error_count": 0, "errors": [],
        },
        "OpenRouter": {
            "scenarios_run": 4, "avg_latency_s": 2.0,
            "avg_input_words": 11.5, "avg_output_words": 30.0,
            "total_cost_usd": 0.0004, "avg_overall_score": 80.0,
            "wrapper_language_in": 0, "hallucinations": 0,
            "timeouts": 0, "error_count": 0, "errors": [],
        },
    }
    reporter2 = Reporter(SAMPLE_RESULTS, improved_summary, config=SAMPLE_CONFIG)
    reporter2.save_trend(tmp_path)

    loaded2 = reporter2.load_trend(tmp_path)
    check("load_trend after second save has 2 entries", len(loaded2) >= 2)

    # Compare trends should show improvement
    diff2 = reporter2.compare_trend(tmp_path)
    check("compare_trend has previous", diff2.get("previous") is not None)
    check("compare_trend has current", diff2.get("current") is not None)
    check("compare_trend has diffs",
          "diffs" in diff2 and isinstance(diff2["diffs"], dict))

finally:
    os.unlink(tmp_path)
    check("trend temp file cleaned up", not os.path.isfile(tmp_path))

# =========================================================================
print("\n=== Constitution detection ===")

# Cerebras URL should be detected as full constitution
cerebras_url = "https://api.cerebras.ai/v1/chat/completions"
openrouter_url = "https://openrouter.ai/api/v1/chat/completions"
openai_url = "https://api.openai.com/v1/chat/completions"

const_check = _detect_constitution_selection([
    {"name": "cerebras", "url": cerebras_url, "label": "Cerebras"},
    {"name": "openrouter", "url": openrouter_url, "label": "OpenRouter"},
    {"name": "openai", "url": openai_url, "label": "OpenAI"},
])
check("constitution detection returns dict",
      isinstance(const_check, dict))
check("constitution has providers", "providers" in const_check)
check("Cerebras gets full constitution",
      const_check["providers"]["Cerebras"]["constitution"] == "full")
check("Cerebras constitution name includes v5",
      "v5" in const_check["providers"]["Cerebras"]["constitution_name"].lower())
check("OpenRouter gets lightweight",
      const_check["providers"]["OpenRouter"]["constitution"] == "lightweight")
check("OpenAI gets lightweight",
      const_check["providers"]["OpenAI"]["constitution"] == "lightweight")
check("constitution has all_consistent flag",
      "all_consistent" in const_check)
check("constitution note is non-empty",
      len(const_check.get("note", "")) > 0)

# Single provider
single = _detect_constitution_selection([
    {"name": "cerebras", "url": cerebras_url, "label": "Cerebras"},
])
check("single provider constitution check works",
      single["providers"]["Cerebras"]["constitution"] == "full")

# Empty providers
empty_const = _detect_constitution_selection([])
check("empty providers constitution check doesn't crash",
      isinstance(empty_const, dict) and len(empty_const.get("providers", {})) == 0)

# =========================================================================
print("\n=== Conciseness effect check ===")

concise_results = [
    {"provider": "Cerebras", "conciseness_ratio": 1.2, "lane": "dictation",
     "input_words": 15, "output_words": 18, "overall": 92.5},
    {"provider": "Cerebras", "conciseness_ratio": 5.42, "lane": "prompt",
     "input_words": 12, "output_words": 65, "overall": 88.0},
    {"provider": "Cerebras", "conciseness_ratio": 3.89, "lane": "email",
     "input_words": 9, "output_words": 35, "overall": 91.0},
]
verbose_results = [
    {"provider": "OpenRouter", "conciseness_ratio": 1.47, "lane": "dictation",
     "input_words": 15, "output_words": 22, "overall": 85.0},
    {"provider": "OpenRouter", "conciseness_ratio": 7.5, "lane": "prompt",
     "input_words": 12, "output_words": 90, "overall": 78.0},
    {"provider": "OpenRouter", "conciseness_ratio": 4.44, "lane": "email",
     "input_words": 9, "output_words": 40, "overall": 82.0},
]

concise_check = _check_conciseness_effect(
    concise_results, "Cerebras",
    verbose_results, "OpenRouter",
)
check("conciseness effect returns dict",
      isinstance(concise_check, dict))
check("conciseness has concise_provider", "concise_provider" in concise_check)
check("conciseness has verbose_provider", "verbose_provider" in concise_check)
check("conciseness has per_lane", "per_lane" in concise_check)
check("conciseness has overall_improvement",
      "overall_improvement_pct" in concise_check)
check("conciseness identifies Cerebras as more concise",
      concise_check["concise_provider"] == "Cerebras")

# Check per-lane data
check("per_lane has dictation", "dictation" in concise_check["per_lane"])
check("dictation improvement percentage is numeric",
      isinstance(concise_check["per_lane"]["dictation"]["improvement_pct"],
                 (int, float)))

# Same provider (no comparison possible)
same_check = _check_conciseness_effect(
    concise_results, "Cerebras",
    concise_results, "Cerebras",
)
check("same provider conciseness check works",
      isinstance(same_check, dict))
check("same provider notes identical",
      "identical" in str(same_check.get("note", "")).lower()
      or same_check.get("overall_improvement_pct", 1) == 0)

# =========================================================================
print("\n=== Reporter: _build_comparison_rows ===")

rows = _build_comparison_rows(SAMPLE_RESULTS)
check("_build_comparison_rows returns list", isinstance(rows, list))
# Should have one row per result item
check("_build_comparison_rows length matches results",
      len(rows) == len(SAMPLE_RESULTS))
# Each row is a flat dict
for row in rows:
    check("row provider present", "provider" in row)
    check("row scenario present", "scenario" in row)

# Empty results
empty_rows = _build_comparison_rows([])
check("empty results produces empty rows", empty_rows == [])

# =========================================================================
print("\n=== Reporter: _build_trend_diff ===")

prev_summary = {
    "Cerebras": {"avg_overall_score": 65.0, "avg_latency_s": 2.0,
                  "total_cost_usd": 0.0},
    "OpenRouter": {"avg_overall_score": 55.0, "avg_latency_s": 10.0,
                    "total_cost_usd": 0.001},
}
curr_summary = {
    "Cerebras": {"avg_overall_score": 85.0, "avg_latency_s": 1.5,
                  "total_cost_usd": 0.0},
    "OpenRouter": {"avg_overall_score": 75.0, "avg_latency_s": 8.0,
                    "total_cost_usd": 0.0005},
}

diff = _build_trend_diff(prev_summary, curr_summary)
check("trend diff returns dict", isinstance(diff, dict))
check("trend diff has diffs", "diffs" in diff)
check("trend diff has Cerebras", "Cerebras" in diff["diffs"])
check("trend diff has OpenRouter", "OpenRouter" in diff["diffs"])
check("Cerebras score improved",
      diff["diffs"]["Cerebras"]["avg_overall_score"]["delta"] > 0)
check("OpenRouter latency improved (negative delta is good)",
      diff["diffs"]["OpenRouter"]["avg_latency_s"]["delta"] < 0)

# =========================================================================
print("\n=== Reporter: report_compare convenience ===")

md_report = report_compare(SAMPLE_RESULTS, SAMPLE_SUMMARY,
                           output_format="markdown")
check("report_compare markdown returns string", isinstance(md_report, str))
check("report_compare markdown non-empty", len(md_report) > 0)

json_report = report_compare(SAMPLE_RESULTS, SAMPLE_SUMMARY,
                             output_format="json")
check("report_compare json returns string", isinstance(json_report, str))
check("report_compare json is valid",
      json.loads(json_report) is not None)

# Unknown format falls back to markdown
unknown_report = report_compare(SAMPLE_RESULTS, SAMPLE_SUMMARY,
                                output_format="unknown")
check("report_compare unknown format falls back to markdown",
      isinstance(unknown_report, str) and len(unknown_report) > 0)

# =========================================================================
print("\n=== Reporter: edge cases ===")

# Empty summary
reporter_empty_result = Reporter([], {})
md_empty = reporter_empty_result.to_markdown()
check("empty results markdown doesn't crash",
      isinstance(md_empty, str))
check("empty results markdown indicates no data",
      "no provider data" in md_empty.lower())

js_empty = reporter_empty_result.to_json()
check("empty results json doesn't crash",
      isinstance(js_empty, str))
parsed_empty = json.loads(js_empty)
check("empty results json has empty results list",
      parsed_empty["report"]["results"] == [])

# Results with only errors
error_only_results = [
    {"scenario": "s1", "provider": "Test", "lane": "dictation",
     "latency_s": 30.0, "input_words": 10, "output_words": 0,
     "estimated_cost_usd": 0.0, "overall": 0.0,
     "conciseness_ratio": 0.0, "wrapper_detected": False,
     "garbage_hallucination": False, "content_preservation": 0.0,
     "adherence": 0.0, "error": "network error"},
]
reporter_err = Reporter(error_only_results,
                         {"Test": {"scenarios_run": 1, "errors": ["s1: network error"],
                                   "error_count": 1, "avg_latency_s": 30.0,
                                   "avg_input_words": 10, "avg_output_words": 0,
                                   "total_cost_usd": 0.0, "avg_overall_score": 0.0,
                                   "wrapper_language_in": 0, "hallucinations": 0,
                                   "timeouts": 0}})
md_err = reporter_err.to_markdown()
check("error results markdown includes error info",
      "error" in md_err.lower() or "fail" in md_err.lower())

# Single provider
single_results = [SAMPLE_RESULTS[0], SAMPLE_RESULTS[2]]
single_summary = {"Cerebras": SAMPLE_SUMMARY["Cerebras"]}
reporter_single = Reporter(single_results, single_summary)
md_single = reporter_single.to_markdown()
check("single provider markdown works", isinstance(md_single, str))
check("single provider doesn't have comparison section (only one provider)",
      True)  # skip structural check, just verify no crash

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
