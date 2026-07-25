#!/usr/bin/env python3
"""Eval harness — runs scenario packs across configured providers.

Usage:
    python eval/harness.py --scenarios basic --providers cerebras,openrouter
    python eval/harness.py --scenarios extended --providers cerebras,openrouter,anthropic --workers 3

Output: structured comparison table (JSON) with per-provider metrics across
quality, latency, and cost dimensions.

The EvalHarness class supports concurrent execution with configurable
parallelism and per-request timeout handling with graceful fallback.
"""

import argparse
import concurrent.futures
import json
import os
import sys
import threading
import time

# Ensure we can import from the parent app directory
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from eval.scenarios import get_scenarios
from eval.metrics import score as score_output, estimate_cost


# ---------------------------------------------------------------------------
# API key loading
# ---------------------------------------------------------------------------

def _load_api_keys():
    """Load API keys from the Mumble settings file."""
    try:
        import branding
        settings_path = branding.SETTINGS_PATH
        with open(settings_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Provider config
# ---------------------------------------------------------------------------

_PROVIDER_TEMPLATES = {
    "cerebras": {
        "url": "https://api.cerebras.ai/v1/chat/completions",
        "key_setting": "cerebras_api_key",
        "model_setting": "cerebras_model",
        "default_model": "gpt-oss-120b",
        "label": "Cerebras",
        # Cerebras gpt-oss-120b is free tier — zero cost
        "price_per_1k_input": 0.0,
        "price_per_1k_output": 0.0,
    },
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "key_setting": "openrouter_api_key",
        "model_setting": "openrouter_model",
        "default_model": "openai/gpt-5.4-mini",
        "label": "OpenRouter",
        "price_per_1k_input": 0.002,
        "price_per_1k_output": 0.004,
    },
    "openai": {
        "url": "https://api.openai.com/v1/chat/completions",
        "key_setting": "openai_api_key",
        "model_setting": "openai_model",
        "default_model": "gpt-5.4-mini",
        "label": "OpenAI",
        "price_per_1k_input": 0.003,
        "price_per_1k_output": 0.006,
    },
    "anthropic": {
        "url": "https://api.anthropic.com/v1/messages",
        "key_setting": "anthropic_api_key",
        "model_setting": "anthropic_model",
        "default_model": "claude-opus-4-8",
        "label": "Anthropic",
        "price_per_1k_input": 0.015,
        "price_per_1k_output": 0.075,
    },
    "deepseek": {
        "url": "https://api.deepseek.com/v1/chat/completions",
        "key_setting": "deepseek_api_key",
        "model_setting": "deepseek_model",
        "default_model": "deepseek-v4-flash",
        "label": "DeepSeek",
        "price_per_1k_input": 0.00014,
        "price_per_1k_output": 0.00028,
    },
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "key_setting": "groq_api_key",
        "model_setting": "groq_model",
        "default_model": "llama-3.3-70b-versatile",
        "label": "Groq",
        "price_per_1k_input": 0.00059,
        "price_per_1k_output": 0.00079,
    },
}


def _provider_from_name(name, keys):
    """Build provider config dict from name and settings keys."""
    name = name.strip().lower()
    tmpl = _PROVIDER_TEMPLATES.get(name)
    if tmpl is None:
        return None
    return {
        "name": name,
        "url": tmpl["url"],
        "key": keys.get(tmpl["key_setting"], ""),
        "model": keys.get(tmpl["model_setting"], tmpl["default_model"]),
        "label": tmpl["label"],
        "price_per_1k_input": tmpl.get("price_per_1k_input", 0.0),
        "price_per_1k_output": tmpl.get("price_per_1k_output", 0.0),
    }


def resolve_providers(provider_names, keys):
    """Resolve a list of provider name strings into config dicts.

    Returns (valid_providers, skipped_names).
    """
    provider_names = [p.strip().lower() for p in provider_names if p.strip()]
    providers = []
    skipped = []
    for name in provider_names:
        cfg = _provider_from_name(name, keys)
        if cfg is None:
            print(f"Warning: unknown provider '{name}', skipping")
            skipped.append(name)
            continue
        if not cfg["key"]:
            print(f"Warning: no API key for '{name}', skipping")
            skipped.append(name)
            continue
        providers.append(cfg)
    return providers, skipped


def _provider_decision(provider, lane):
    """Give explicit route permission to this opt-in, live eval invocation."""
    import processing_route
    template = _PROVIDER_TEMPLATES[provider["name"]]
    values = {
        "pro_mode": True, "local_only_mode": False, "instant_text": False,
        "llm_provider": provider["name"],
        template["key_setting"]: provider["key"],
        template["model_setting"]: provider["model"],
    }
    return processing_route.snapshot(values, feature=lane, lane=lane)


# ---------------------------------------------------------------------------
# Lane runners — execute one scenario against one provider
# ---------------------------------------------------------------------------

def _run_polish(request, provider, timeout=120):
    """Run a dictation polish call. Returns (output_text, latency_s, error)."""
    import ai
    t0 = time.time()
    text = ""
    error = None
    try:
        gen = ai.cerebras_polish(
            request, provider["key"], provider["model"],
            url=provider["url"], aggressiveness="Light",
            route_decision=_provider_decision(provider, "text"),
        )
        for chunk in gen:
            if isinstance(chunk, str) and not chunk.startswith("\x00"):
                text += chunk
    except Exception as e:
        error = str(e)
        text = ""
    latency = time.time() - t0
    return text, latency, error


def _run_prompt(request, provider, timeout=120):
    """Run a prompt-mode call. Returns (output_text, latency_s, error)."""
    import ai
    t0 = time.time()
    text = ""
    error = None
    try:
        gen = ai.cerebras_prompt(
            request, provider["key"],
            model=provider["model"], url=provider["url"],
            route_decision=_provider_decision(provider, "prompt"),
        )
        for chunk in gen:
            if isinstance(chunk, str) and not chunk.startswith("\x00"):
                text += chunk
    except Exception as e:
        error = str(e)
        text = ""
    latency = time.time() - t0
    return text, latency, error


def _run_email(request, provider, timeout=120):
    """Run an email-mode call. Returns (output_text, latency_s, error)."""
    import ai
    t0 = time.time()
    text = ""
    error = None
    try:
        gen = ai.cerebras_email(
            request, "eval_user", provider["key"],
            model=provider["model"], url=provider["url"],
            route_decision=_provider_decision(provider, "email"),
        )
        for chunk in gen:
            if isinstance(chunk, str) and not chunk.startswith("\x00"):
                text += chunk
    except Exception as e:
        error = str(e)
        text = ""
    latency = time.time() - t0
    return text, latency, error


def _run_reply(request, provider, context="", timeout=120):
    """Run a reply-mode call. Returns (output_text, latency_s, error)."""
    import ai
    t0 = time.time()
    text = ""
    error = None
    try:
        gen = ai.cerebras_reply(
            request, "eval_user", provider["key"],
            context=context, model=provider["model"], url=provider["url"],
            route_decision=_provider_decision(provider, "reply"),
        )
        for chunk in gen:
            if isinstance(chunk, str) and not chunk.startswith("\x00"):
                text += chunk
    except Exception as e:
        error = str(e)
        text = ""
    latency = time.time() - t0
    return text, latency, error


def _run_foreign(request, provider, timeout=120):
    """Run a foreign-mode call. Returns (output_text, latency_s, error)."""
    import ai
    t0 = time.time()
    text = ""
    error = None
    try:
        gen = ai.cerebras_foreign(
            request, "eval_user", provider["key"],
            model=provider["model"], url=provider["url"],
            route_decision=_provider_decision(provider, "foreign"),
        )
        for chunk in gen:
            if isinstance(chunk, str) and not chunk.startswith("\x00"):
                text += chunk
    except Exception as e:
        error = str(e)
        text = ""
    latency = time.time() - t0
    return text, latency, error


LANE_RUNNERS = {
    "dictation": _run_polish,
    "prompt": _run_prompt,
    "email": _run_email,
    "reply": _run_reply,
    "foreign": _run_foreign,
}


# ---------------------------------------------------------------------------
# Scenario runner (timeout-wrapped)
# ---------------------------------------------------------------------------

def _run_with_timeout(fn, *args, timeout=120):
    """Run a function in a thread with a timeout.

    If the call completes within *timeout* seconds, returns (result, None).
    If it exceeds the timeout, returns (None, "timeout: ...") — a graceful
    fallback instead of a crash.

    The lane runner returns (output_text, latency_s, error), so this wraps
    the thread result accordingly.
    """
    result_holder = [None]
    error_holder = [None]
    done = threading.Event()

    def _target():
        try:
            result_holder[0] = fn(*args, timeout=timeout)
        except Exception as e:
            error_holder[0] = e
        finally:
            done.set()

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    finished = done.wait(timeout=timeout)

    if not finished:
        # Timeout — the thread may still be running (blocked on I/O),
        # but we return a graceful fallback immediately.
        return ("", timeout, f"timeout: AI request exceeded {timeout}s limit")

    if error_holder[0] is not None:
        return ("", 0.0, f"error: {error_holder[0]}")

    return result_holder[0]  # (output_text, latency_s, error)


# ---------------------------------------------------------------------------
# EvalHarness class
# ---------------------------------------------------------------------------

class EvalHarness:
    """Runs scenario packs across multiple providers with concurrent execution.

    Features:
    - Concurrent execution with configurable worker count
    - Per-request timeout with graceful fallback (no crashes)
    - Structured output (JSON by default, table available)
    - Per-provider latency, quality, and cost metrics
    - Provider comparison summaries
    """

    def __init__(self, providers, scenarios, max_workers=4, timeout=120):
        """Initialise the harness.

        Args:
            providers: list of provider config dicts (from resolve_providers).
            scenarios: list of (id, lane, text) tuples.
            max_workers: max concurrent provider calls (default 4).
            timeout: per-request timeout in seconds (default 120).
        """
        self.providers = providers
        self.scenarios = scenarios
        self.max_workers = max_workers
        self.timeout = timeout
        self._lock = threading.Lock()
        self._progress = {"done": 0, "total": 0}

    def run(self):
        """Run all scenarios across all providers, concurrently.

        Returns:
            (results_list, summary_dict) where:
            - results_list: per-scenario-per-provider dicts with metrics.
            - summary_dict: per-provider aggregate metrics.
        """
        total = len(self.scenarios) * len(self.providers)
        self._progress = {"done": 0, "total": total}
        results = []

        # Build the work queue: every (scenario, provider) pair
        work_items = [
            (sid, lane, text, provider)
            for sid, lane, text in self.scenarios
            for provider in self.providers
        ]

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers
        ) as executor:
            future_map = {}
            for sid, lane, text, provider in work_items:
                future = executor.submit(
                    self._run_one, sid, lane, text, provider
                )
                future_map[future] = (sid, provider["label"])

            for future in concurrent.futures.as_completed(future_map):
                sid, plabel = future_map[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    results.append({
                        "scenario": sid,
                        "provider": plabel,
                        "lane": "unknown",
                        "error": f"unhandled harness error: {e}",
                    })
                with self._lock:
                    self._progress["done"] += 1
                    done = self._progress["done"]
                    if done % max(1, total // 10) == 0 or done == total:
                        print(f"  Progress: {done}/{total}", flush=True)

        summary = self._summarize(results)
        return results, summary

    def _run_one(self, scenario_id, lane, input_text, provider):
        """Run one scenario against one provider, with timeout protection."""
        runner = LANE_RUNNERS.get(lane)
        if runner is None:
            return {
                "scenario": scenario_id,
                "provider": provider["label"],
                "lane": lane,
                "error": f"unknown lane: {lane}",
            }

        print(f"  [{provider['label']}] {scenario_id} ({lane})...",
              end=" ", flush=True)

        output, latency, error = _run_with_timeout(
            runner, input_text, provider, timeout=self.timeout
        )

        input_words = len((input_text or "").split())
        output_words = len((output or "").split())

        # Score output (even errors get scored — empty output yields low score)
        metrics = score_output(output or "", input_text, lane)

        # Estimate cost
        cost = estimate_cost(
            input_words, output_words,
            provider.get("price_per_1k_input", 0.0),
            provider.get("price_per_1k_output", 0.0),
        )

        result = {
            "scenario": scenario_id,
            "provider": provider["label"],
            "lane": lane,
            "latency_s": round(latency, 3),
            "input_words": input_words,
            "output_words": output_words,
            "estimated_cost_usd": round(cost, 6),
            "error": error,
            **metrics,
        }

        if error:
            print(f"FAIL: {error}")
        else:
            print(f"score={result['overall']}, latency={result['latency_s']}s")

        return result

    def _summarize(self, results):
        """Produce per-provider summary averages."""
        by_provider = {}
        for r in results:
            p = r["provider"]
            if p not in by_provider:
                by_provider[p] = {
                    "scenarios": 0,
                    "total_latency": 0.0,
                    "total_input_words": 0,
                    "total_output_words": 0,
                    "total_cost": 0.0,
                    "overall_sum": 0.0,
                    "wrapper_count": 0,
                    "hallucination_count": 0,
                    "timeout_count": 0,
                    "error_count": 0,
                    "errors": [],
                }
            s = by_provider[p]
            s["scenarios"] += 1
            s["total_latency"] += r.get("latency_s", 0)
            s["total_input_words"] += r.get("input_words", 0)
            s["total_output_words"] += r.get("output_words", 0)
            s["total_cost"] += r.get("estimated_cost_usd", 0)
            s["overall_sum"] += r.get("overall", 0)
            if r.get("wrapper_detected"):
                s["wrapper_count"] += 1
            if r.get("garbage_hallucination"):
                s["hallucination_count"] += 1
            err = r.get("error")
            if err:
                s["error_count"] += 1
                s["errors"].append(f"{r['scenario']}: {err}")
                if "timeout" in str(err).lower():
                    s["timeout_count"] += 1

        summaries = {}
        for p, s in by_provider.items():
            n = max(s["scenarios"], 1)
            summaries[p] = {
                "scenarios_run": s["scenarios"],
                "avg_latency_s": round(s["total_latency"] / n, 3),
                "avg_input_words": round(s["total_input_words"] / n, 1),
                "avg_output_words": round(s["total_output_words"] / n, 1),
                "total_cost_usd": round(s["total_cost"], 6),
                "avg_overall_score": round(s["overall_sum"] / n, 1),
                "wrapper_language_in": s["wrapper_count"],
                "hallucinations": s["hallucination_count"],
                "timeouts": s["timeout_count"],
                "error_count": s["error_count"],
                "errors": sorted(s["errors"]),
            }
        return summaries


# ---------------------------------------------------------------------------
# Backward-compatible module-level functions
# ---------------------------------------------------------------------------

def run_scenario(scenario_id, lane, input_text, provider):
    """Run one scenario against one provider. Returns result dict.
    (Backward-compatible wrapper for existing callers.)
    """
    harness = EvalHarness([provider], [(scenario_id, lane, input_text)],
                          max_workers=1)
    results, _summary = harness.run()
    return results[0] if results else {"error": "no results"}


def summarize(results):
    """Produce per-provider summary averages.
    (Backward-compatible wrapper for existing callers.)
    """
    harness = EvalHarness([], [])
    return harness._summarize(results)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Mumble eval harness — provider comparison benchmarking"
    )
    parser.add_argument(
        "--scenarios", default="basic",
        help="Scenario pack to run (basic, extended)"
    )
    parser.add_argument(
        "--providers", default="cerebras",
        help="Comma-separated provider names "
             "(cerebras,openrouter,openai,anthropic,deepseek,groq)"
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Max concurrent provider calls (default: 4)"
    )
    parser.add_argument(
        "--timeout", type=int, default=120,
        help="Per-request timeout in seconds (default: 120)"
    )
    parser.add_argument(
        "--output", default="json",
        help="Output format (json, table)"
    )
    args = parser.parse_args()

    # Load API keys
    keys = _load_api_keys()

    # Resolve providers
    provider_names = [p.strip() for p in args.providers.split(",") if p.strip()]
    providers, skipped = resolve_providers(provider_names, keys)

    if not providers:
        print("ERROR: no valid providers with API keys configured. "
              "Set keys in Settings -> Pro Mode.")
        for name in skipped:
            print(f"  - {name}: missing API key or unknown provider")
        sys.exit(1)

    # Load scenarios
    scenarios = get_scenarios(args.scenarios)
    print(f"Eval harness: {len(scenarios)} scenarios x "
          f"{len(providers)} providers = "
          f"{len(scenarios) * len(providers)} runs "
          f"(workers={args.workers}, timeout={args.timeout}s)\n")

    # Create and run harness
    harness = EvalHarness(
        providers, scenarios,
        max_workers=args.workers,
        timeout=args.timeout,
    )
    results, summary = harness.run()

    # Output
    if args.output == "json":
        output = {
            "config": {
                "scenarios": args.scenarios,
                "providers": provider_names,
                "workers": args.workers,
                "timeout_s": args.timeout,
            },
            "per_scenario": results,
            "summary": summary,
        }
        print("\n" + json.dumps(output, indent=2))
    else:
        # Table format
        print("\n=== Provider Comparison ===")
        header = (f"{'Provider':<15} {'Scenarios':>9} {'Avg Lat':>8} "
                  f"{'Avg Tok':>8} {'Score':>7} {'Cost':>8} "
                  f"{'Wrp':>4} {'Hal':>4} {'T/O':>4} {'Err':>4}")
        print(header)
        print("-" * len(header))
        for p, s in sorted(summary.items()):
            print(f"{p:<15} {s['scenarios_run']:>9} "
                  f"{s['avg_latency_s']:>7.1f}s "
                  f"{s['avg_output_words']:>8.1f} "
                  f"{s['avg_overall_score']:>7.1f} "
                  f"${s['total_cost_usd']:>7.4f} "
                  f"{s['wrapper_language_in']:>4} "
                  f"{s['hallucinations']:>4} "
                  f"{s['timeouts']:>4} "
                  f"{s['error_count']:>4}")

    # Exit code
    critical_issues = sum(
        1 for s in summary.values()
        if s["wrapper_language_in"] > 0
        or s["hallucinations"] > 0
        or s["timeouts"] > 0
    )
    if critical_issues:
        print(f"\nWarning: {critical_issues} provider(s) have quality issues "
              f"(wrapper language, hallucination, or timeout)")
    else:
        print("\nAll providers passed quality checks.")


if __name__ == "__main__":
    main()
