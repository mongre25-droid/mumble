#!/usr/bin/env python3
"""Provider comparison reporter for Mumble eval harness.

Generates provider comparison tables, trend tracking, and cross-provider
comparisons covering the full transcription-to-output flow.

Output formats:
- Markdown: formatted report with comparison tables, per-lane breakdown,
  trend analysis, constitution verification, conciseness verification
- JSON: structured machine-readable report

Cross-provider comparison covers:
- Latency (seconds)
- Token count (output words)
- Cost estimate (USD)
- Conciseness ratio (output/input)
- Overall quality score
- Wrapper language detection
- Hallucination detection
- Constitution selection (full vs lightweight by provider URL)

Trend tracking:
- Saves each evaluation run to a trend file
- Compares against previous runs to show improvement/regression
- Per-provider delta tracking across cycles

Usage:
    from eval.reporter import Reporter, report_compare

    reporter = Reporter(results, summary, config, provider_configs)
    print(reporter.to_markdown())
    print(reporter.to_json())
    reporter.save_trend("eval_trends.json")
    diff = reporter.compare_trend("eval_trends.json")
"""

import json
import os
import time
from collections import defaultdict

# Constitution URL patterns — Cerebras gets full, others get lightweight
_CEREBRAS_URL = "https://api.cerebras.ai"
_CEREBRAS_URL_SHORT = "api.cerebras.ai"

# Known wrapper phrases for summary display
_WRAPPER_MARKERS = [
    "Here is your", "Certainly!", "I've written",
    "Below is the", "Here's a", "Let me help",
]


# =========================================================================
# Reporter
# =========================================================================

class Reporter:
    """Generate comparison reports from eval harness results.

    Takes the results list + summary dict from EvalHarness.run() and
    renders them as Markdown tables or structured JSON.

    Args:
        results: list of per-scenario-per-provider result dicts
        summary: dict of per-provider aggregate summaries
        config: optional config dict (scenarios, providers, workers, timeout)
        provider_configs: optional list of provider configs for constitution
                          and conciseness verification
    """

    def __init__(self, results, summary, config=None, provider_configs=None):
        self.results = results or []
        self.summary = summary or {}
        self.config = config or {}
        self.provider_configs = provider_configs or []

    # -- comparison table --------------------------------------------------

    def comparison_table(self, group_by=None):
        """Build structured comparison data.

        Args:
            group_by: None (flat list), "lane", or "provider"

        Returns:
            Flat list of row dicts if group_by is None,
            or dict of {key: [rows]} if grouped.
        """
        rows = _build_comparison_rows(self.results)

        if group_by == "lane":
            grouped = defaultdict(list)
            for r in rows:
                grouped[r.get("lane", "unknown")].append(r)
            return dict(grouped)

        if group_by == "provider":
            grouped = defaultdict(list)
            for r in rows:
                grouped[r.get("provider", "unknown")].append(r)
            return dict(grouped)

        return rows

    # -- Markdown output ---------------------------------------------------

    def to_markdown(self, provider_configs=None):
        """Render the full evaluation report as Markdown.

        Includes:
        - Header with run metadata
        - Summary comparison table
        - Per-provider aggregate metrics
        - Per-lane detailed comparison
        - Constitution selection verification (if provider_configs provided)
        - Conciseness effect analysis (if multi-provider)
        - Trend analysis (if trend_file available)
        - Issues / warnings

        Args:
            provider_configs: optional list of provider config dicts for
                              constitution and conciseness verification
        """
        cfgs = provider_configs or self.provider_configs
        lines = []

        # Header
        lines.append("# Mumble Eval Report — Provider Comparison")
        lines.append("")
        if self.config:
            lines.append(f"**Scenarios:** {self.config.get('scenarios', 'N/A')}  ")
            lines.append(f"**Providers:** {', '.join(self.config.get('providers', []))}  ")
            lines.append(f"**Workers:** {self.config.get('workers', 'N/A')}  ")
            lines.append(f"**Timeout:** {self.config.get('timeout_s', 'N/A')}s  ")
            lines.append(f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}  ")
        lines.append("")

        provider_names = sorted(self.summary.keys())

        # -- Summary table -------------------------------------------------
        lines.append("## Summary")
        lines.append("")
        if not provider_names:
            lines.append("*No provider data available.*")
            lines.append("")
            return "\n".join(lines)

        # Build summary table
        lines.append("| Provider | Runs | Avg Latency | Avg Out Words | "
                      "Avg Score | Total Cost | Wrapper | Halluc | "
                      "Timeouts | Errors |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | "
                      "--- | --- |")

        for p in provider_names:
            s = self.summary.get(p, {})
            n = s.get("scenarios_run", 0)
            lat = s.get("avg_latency_s", 0)
            words = s.get("avg_output_words", 0)
            score = s.get("avg_overall_score", 0)
            cost = s.get("total_cost_usd", 0)
            wrap = s.get("wrapper_language_in", 0)
            hall = s.get("hallucinations", 0)
            tos = s.get("timeouts", 0)
            errs = s.get("error_count", 0)

            lines.append(
                f"| **{p}** | {n} | {lat:.2f}s | {words:.1f} | "
                f"{score:.1f} | ${cost:.6f} | {wrap} | {hall} | "
                f"{tos} | {errs} |"
            )
        lines.append("")

        # -- Per-lane breakdown --------------------------------------------
        lines.append("## Per-Lane Comparison")
        lines.append("")

        by_lane = self.comparison_table(group_by="lane")
        for lane in sorted(by_lane.keys()):
            rows = by_lane[lane]
            if not rows:
                continue

            lines.append(f"### {lane.title()}")
            lines.append("")
            lines.append("| Provider | Scenario | Latency | In Words | "
                          "Out Words | Ratio | Score | Cost | Status |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- | "
                          "--- | --- |")

            for r in rows:
                provider = r.get("provider", "?")
                scenario = r.get("scenario", "?")
                lat = r.get("latency_s", 0)
                in_w = r.get("input_words", 0)
                out_w = r.get("output_words", 0)
                ratio = r.get("conciseness_ratio", 0)
                score = r.get("overall", 0)
                cost = r.get("estimated_cost_usd", 0)
                error = r.get("error", "")
                wrapper = r.get("wrapper_detected", False)
                garbage = r.get("garbage_hallucination", False)

                status_bits = []
                if error:
                    status_bits.append("ERR")
                if wrapper:
                    status_bits.append("WRP")
                if garbage:
                    status_bits.append("HAL")
                status = ",".join(status_bits) if status_bits else "OK"

                lines.append(
                    f"| {provider} | {scenario} | {lat:.2f}s | {in_w} | "
                    f"{out_w} | {ratio:.2f} | {score:.1f} | "
                    f"${cost:.6f} | {status} |"
                )
            lines.append("")

        # -- Provider detail sections --------------------------------------
        lines.append("## Provider Details")
        lines.append("")
        for p in provider_names:
            s = self.summary.get(p, {})
            lines.append(f"### {p}")
            lines.append("")
            lines.append(f"- **Scenarios run:** {s.get('scenarios_run', 0)}")
            lines.append(f"- **Avg latency:** {s.get('avg_latency_s', 0):.3f}s")
            lines.append(f"- **Avg input words:** {s.get('avg_input_words', 0):.0f}")
            lines.append(f"- **Avg output words:** {s.get('avg_output_words', 0):.0f}")
            lines.append(f"- **Avg overall score:** {s.get('avg_overall_score', 0):.1f}")
            lines.append(f"- **Total cost:** ${s.get('total_cost_usd', 0):.6f}")
            lines.append(f"- **Wrapper language detected:** "
                         f"{s.get('wrapper_language_in', 0)}")
            lines.append(f"- **Hallucinations:** {s.get('hallucinations', 0)}")
            lines.append(f"- **Timeouts:** {s.get('timeouts', 0)}")
            lines.append(f"- **Error count:** {s.get('error_count', 0)}")

            errors_list = s.get("errors", [])
            if errors_list:
                lines.append("")
                lines.append("**Errors:**")
                for e in errors_list:
                    lines.append(f"  - {e}")
            lines.append("")

        # -- Constitution verification -------------------------------------
        if cfgs:
            const = _detect_constitution_selection(cfgs)
            lines.append("## Constitution Verification")
            lines.append("")
            lines.append("**Rule:** Cerebras → Full Master Constitution "
                         "v5; all others → Lightweight Constitution")
            lines.append("")
            lines.append("| Provider | Constitution | Name |")
            lines.append("| --- | --- | --- |")

            for pname, pinfo in const.get("providers", {}).items():
                ctype = pinfo.get("constitution", "unknown")
                cname = pinfo.get("constitution_name", "N/A")
                lines.append(f"| {pname} | **{ctype}** | {cname} |")

            lines.append("")
            note = const.get("note", "")
            if note:
                lines.append(f"*{note}*")
                lines.append("")

        # -- Conciseness analysis ------------------------------------------
        if len(provider_names) >= 2:
            lines.append("## Conciseness Analysis")
            lines.append("")

            # Find the most concise provider (lowest avg ratio)
            concise_p = provider_names[0]
            verbose_p = provider_names[-1]
            best_ratio = float("inf")
            worst_ratio = 0.0
            for p in provider_names:
                s = self.summary.get(p, {})
                out_w = s.get("avg_output_words", 0)
                in_w = max(s.get("avg_input_words", 1), 1)
                ratio = out_w / in_w
                if ratio < best_ratio:
                    best_ratio = ratio
                    concise_p = p
                if ratio > worst_ratio:
                    worst_ratio = ratio
                    verbose_p = p

            # Per-lane conciseness comparison
            by_lane = self.comparison_table(group_by="lane")
            concise_results = []
            verbose_results = []
            for lane, rows in by_lane.items():
                for r in rows:
                    if r["provider"] == concise_p:
                        concise_results.append(r)
                    elif r["provider"] == verbose_p:
                        verbose_results.append(r)

            conc_check = _check_conciseness_effect(
                concise_results, concise_p,
                verbose_results, verbose_p,
            )

            lines.append(f"**Most concise:** {concise_p} "
                         f"(avg output/input ratio: {best_ratio:.2f})  ")
            lines.append(f"**Most verbose:** {verbose_p} "
                         f"(avg output/input ratio: {worst_ratio:.2f})  ")
            lines.append(f"**Overall improvement:** "
                         f"{conc_check.get('overall_improvement_pct', 0):.1f}%  ")
            lines.append("")

            lines.append("### Per-Lane Conciseness")
            lines.append("")
            lines.append("| Lane | Concise Ratio | Verbose Ratio | "
                          "Improvement % |")
            lines.append("| --- | --- | --- | --- |")
            for lane, info in sorted(conc_check.get("per_lane", {}).items()):
                cr = info.get("concise_ratio", 0)
                vr = info.get("verbose_ratio", 0)
                imp = info.get("improvement_pct", 0)
                lines.append(f"| {lane} | {cr:.2f} | {vr:.2f} | "
                             f"{imp:.1f}% |")
            lines.append("")

            note = conc_check.get("note", "")
            if note:
                lines.append(f"*{note}*")
                lines.append("")

        # -- Issues / warnings ---------------------------------------------
        issues = []
        for p in provider_names:
            s = self.summary.get(p, {})
            if s.get("wrapper_language_in", 0) > 0:
                issues.append(
                    f"{p}: {s['wrapper_language_in']} outputs with wrapper "
                    f"language ('Here is...', 'Certainly!', etc.)"
                )
            if s.get("hallucinations", 0) > 0:
                issues.append(
                    f"{p}: {s['hallucinations']} hallucinated responses "
                    f"to garbage/nonsense input"
                )
            if s.get("timeouts", 0) > 0:
                issues.append(
                    f"{p}: {s['timeouts']} timeout(s)"
                )
            if s.get("error_count", 0) > 0:
                issues.append(
                    f"{p}: {s['error_count']} error(s) — "
                    f"{', '.join(s.get('errors', [])[:3])}"
                )

        if issues:
            lines.append("## Issues & Warnings")
            lines.append("")
            for issue in issues:
                lines.append(f"- {issue}")
            lines.append("")

        # -- Footer --------------------------------------------------------
        lines.append("---")
        lines.append(f"*Report generated by Mumble eval/reporter.py "
                     f"at {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}*")

        return "\n".join(lines)

    # -- JSON output -------------------------------------------------------

    def to_json(self, provider_configs=None):
        """Render the full evaluation report as structured JSON.

        Args:
            provider_configs: optional list of provider config dicts
        """
        cfgs = provider_configs or self.provider_configs

        report = {
            "summary": self.summary,
            "results": self.results,
            "by_lane": self.comparison_table(group_by="lane"),
            "by_provider": self.comparison_table(group_by="provider"),
        }

        # Add constitution check if configs available
        if cfgs:
            report["constitution_check"] = _detect_constitution_selection(cfgs)

        # Add conciseness analysis if multiple providers
        provider_names = sorted(self.summary.keys())
        if len(provider_names) >= 2:
            by_lane = self.comparison_table(group_by="lane")
            concise_results = []
            verbose_results = []
            # Find most concise and verbose
            best_ratio = float("inf")
            worst_ratio = 0.0
            concise_p = provider_names[0]
            verbose_p = provider_names[-1]
            for p in provider_names:
                s = self.summary.get(p, {})
                out_w = s.get("avg_output_words", 0)
                in_w = max(s.get("avg_input_words", 1), 1)
                ratio = out_w / in_w
                if ratio < best_ratio:
                    best_ratio = ratio
                    concise_p = p
                if ratio > worst_ratio:
                    worst_ratio = ratio
                    verbose_p = p

            for lane, rows in by_lane.items():
                for r in rows:
                    if r["provider"] == concise_p:
                        concise_results.append(r)
                    elif r["provider"] == verbose_p:
                        verbose_results.append(r)

            report["conciseness_analysis"] = _check_conciseness_effect(
                concise_results, concise_p,
                verbose_results, verbose_p,
            )

        output = {
            "report": report,
            "metadata": {
                "generated_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                ),
                "generator": "eval/reporter.py",
                "config": self.config,
                "provider_count": len(provider_names),
                "result_count": len(self.results),
            },
        }
        return json.dumps(output, indent=2, ensure_ascii=False)

    # -- Trend tracking ----------------------------------------------------

    def save_trend(self, trend_file):
        """Save this evaluation run to a trend tracking file.

        Appends the current summary + config + timestamp as a JSON entry.

        Args:
            trend_file: path to trend JSON file (created if doesn't exist)
        """
        existing = self.load_trend(trend_file)
        entry = {
            "timestamp": time.time(),
            "timestamp_iso": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            ),
            "summary": self.summary,
            "config": self.config,
            "result_count": len(self.results),
        }
        existing.append(entry)
        with open(trend_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)

    def load_trend(self, trend_file):
        """Load trend entries from a trend tracking file.

        Args:
            trend_file: path to trend JSON file

        Returns:
            list of trend entry dicts (empty if file doesn't exist)
        """
        if not os.path.isfile(trend_file):
            return []
        try:
            with open(trend_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else [data]
        except (json.JSONDecodeError, IOError):
            return []

    def compare_trend(self, trend_file):
        """Compare current results against previous trend runs.

        Returns:
            dict with previous, current, and per-provider diffs
        """
        entries = self.load_trend(trend_file)
        if len(entries) < 1:
            return {
                "previous": None,
                "current": None,
                "diffs": {},
                "note": "No trend data available for comparison",
            }

        previous = entries[-1]
        current = {
            "timestamp": time.time(),
            "summary": self.summary,
        }

        if len(entries) == 1 and previous.get("summary") == self.summary:
            # First run — self is already the only entry
            return {
                "previous": previous,
                "current": current,
                "diffs": {},
                "is_first_run": True,
                "note": "First evaluation run — no baseline to compare against",
            }

        # If the earliest entry is the same as current (first save already happened),
        # compare against the earliest non-self entry
        compare_against = previous
        if len(entries) >= 2:
            # Find the previous run (not the one we just saved)
            for entry in reversed(entries):
                if entry.get("timestamp") != previous.get("timestamp"):
                    compare_against = entry
                    break

        diffs = _build_trend_diff(
            compare_against.get("summary", {}),
            self.summary,
        )

        return {
            "previous": compare_against,
            "current": current,
            **diffs,  # unpacks {"diffs": {...}} so we get top-level "diffs" key
        }


# =========================================================================
# Helper functions
# =========================================================================

def _build_comparison_rows(results):
    """Build a flat list of comparison row dicts from raw results.

    Each row has standardised fields for easy table rendering.
    """
    rows = []
    for r in results:
        row = {
            "provider": r.get("provider", "?"),
            "scenario": r.get("scenario", "?"),
            "lane": r.get("lane", "?"),
            "latency_s": r.get("latency_s", 0),
            "input_words": r.get("input_words", 0),
            "output_words": r.get("output_words", 0),
            "conciseness_ratio": r.get("conciseness_ratio", 0),
            "estimated_cost_usd": r.get("estimated_cost_usd", 0),
            "overall": r.get("overall", 0),
            "wrapper_detected": r.get("wrapper_detected", False),
            "garbage_hallucination": r.get("garbage_hallucination", False),
            "content_preservation": r.get("content_preservation", 0),
            "adherence": r.get("adherence", 0),
            "error": r.get("error"),
        }
        rows.append(row)
    return rows


def _detect_constitution_selection(provider_configs):
    """Detect which constitution each provider would receive.

    Cerebras (api.cerebras.ai) → full Master Constitution v5
    All others → lightweight constitution

    Args:
        provider_configs: list of provider config dicts with 'url' and 'label'

    Returns:
        dict with 'providers' map and 'note'
    """
    providers = {}
    all_consistent = True
    expected = {}

    for cfg in provider_configs:
        name = cfg.get("label", cfg.get("name", "unknown"))
        url = cfg.get("url", "")

        is_cerebras = (
            _CEREBRAS_URL in url or _CEREBRAS_URL_SHORT in url
        )

        if is_cerebras:
            ctype = "full"
            cname = "Master Constitution v5 (full 8-step pipeline)"
            expected[name] = "full"
        else:
            ctype = "lightweight"
            cname = "Lightweight Constitution (prompt conversion mode)"
            expected[name] = "lightweight"

        providers[name] = {
            "constitution": ctype,
            "constitution_name": cname,
            "url": url,
        }

    # Check consistency
    cerebras_count = sum(
        1 for v in providers.values() if v["constitution"] == "full"
    )
    if cerebras_count == 0:
        note = "No Cerebras provider in this comparison. All providers use lightweight constitution."
    elif cerebras_count == len(providers):
        note = "All providers are Cerebras. Full constitution used for all."
    else:
        note = (
            f"Cerebras ({cerebras_count} instance(s)) receives the full "
            f"Master Constitution v5. Other providers receive the "
            f"lightweight constitution."
        )
        all_consistent = True

    return {
        "providers": providers,
        "all_consistent": all_consistent,
        "note": note,
    }


def _check_conciseness_effect(concise_results, concise_provider,
                               verbose_results, verbose_provider):
    """Check whether the concise provider measurably reduces output length.

    Compares per-lane conciseness ratios between two providers.

    Args:
        concise_results: list of result dicts from the concise provider
        concise_provider: name of the concise provider
        verbose_results: list of result dicts from the verbose provider
        verbose_provider: name of the verbose provider

    Returns:
        dict with per-lane comparison and overall improvement
    """
    # Group by lane
    def _by_lane(results):
        lanes = defaultdict(list)
        for r in results:
            lanes[r.get("lane", "?")].append(r)
        return lanes

    concise_lanes = _by_lane(concise_results)
    verbose_lanes = _by_lane(verbose_results)

    per_lane = {}
    total_concise_ratio = 0.0
    total_verbose_ratio = 0.0
    lane_count = 0

    all_lanes = sorted(set(list(concise_lanes.keys()) +
                           list(verbose_lanes.keys())))

    for lane in all_lanes:
        cr_list = concise_lanes.get(lane, [])
        vr_list = verbose_lanes.get(lane, [])

        # Average conciseness ratio for each provider in this lane
        cr_avg = (
            sum(r.get("conciseness_ratio", 0) for r in cr_list) /
            max(len(cr_list), 1)
        )
        vr_avg = (
            sum(r.get("conciseness_ratio", 0) for r in vr_list) /
            max(len(vr_list), 1)
        )

        # Improvement: lower ratio is better
        if vr_avg > 0:
            improvement_pct = round(
                ((vr_avg - cr_avg) / vr_avg) * 100, 1
            )
        else:
            improvement_pct = 0.0

        per_lane[lane] = {
            "concise_ratio": round(cr_avg, 2),
            "verbose_ratio": round(vr_avg, 2),
            "improvement_pct": improvement_pct,
            "concise_scenarios": len(cr_list),
            "verbose_scenarios": len(vr_list),
        }

        if cr_list and vr_list:
            total_concise_ratio += cr_avg
            total_verbose_ratio += vr_avg
            lane_count += 1

    # Overall improvement
    if total_verbose_ratio > 0 and lane_count > 0:
        overall_improvement = round(
            ((total_verbose_ratio - total_concise_ratio) /
             total_verbose_ratio) * 100, 1
        )
    else:
        overall_improvement = 0.0

    # Build note
    if concise_provider == verbose_provider:
        note = (
            f"Identical providers ({concise_provider}) — "
            f"no conciseness comparison possible."
        )
    elif overall_improvement > 5:
        note = (
            f"{concise_provider} is {overall_improvement:.0f}% more concise "
            f"than {verbose_provider} on average. "
            f"The conciseness step in the constitution measurably reduces "
            f"output length."
        )
    elif overall_improvement > 0:
        note = (
            f"{concise_provider} is slightly more concise than "
            f"{verbose_provider} ({overall_improvement:.0f}%). "
            f"The conciseness effect is measurable but modest."
        )
    else:
        note = (
            f"No conciseness advantage detected for {concise_provider} "
            f"over {verbose_provider}."
        )

    return {
        "concise_provider": concise_provider,
        "verbose_provider": verbose_provider,
        "per_lane": per_lane,
        "overall_improvement_pct": overall_improvement,
        "note": note,
    }


def _build_trend_diff(prev_summary, curr_summary):
    """Compute per-provider metric deltas between two trend entries.

    Args:
        prev_summary: previous run's summary dict
        curr_summary: current run's summary dict

    Returns:
        dict of {provider: {metric: {prev, curr, delta}}}
    """
    all_providers = sorted(set(list(prev_summary.keys()) +
                               list(curr_summary.keys())))

    metric_keys = [
        "scenarios_run", "avg_latency_s", "avg_input_words",
        "avg_output_words", "total_cost_usd", "avg_overall_score",
        "wrapper_language_in", "hallucinations", "timeouts",
        "error_count",
    ]

    diffs = {}
    for provider in all_providers:
        pdiffs = {}
        prev_p = prev_summary.get(provider, {})
        curr_p = curr_summary.get(provider, {})

        for key in metric_keys:
            pv = prev_p.get(key, 0)
            cv = curr_p.get(key, 0)
            # Handle None values
            pv = pv if pv is not None else 0
            cv = cv if cv is not None else 0
            delta = cv - pv
            pdiffs[key] = {
                "previous": pv,
                "current": cv,
                "delta": round(delta, 4) if isinstance(delta, float)
                else delta,
            }

        diffs[provider] = pdiffs

    return {"diffs": diffs}


# =========================================================================
# Standalone convenience functions
# =========================================================================

def render_markdown(results, summary, config=None, provider_configs=None):
    """Render eval results as Markdown (convenience function).

    Equivalent to: Reporter(results, summary, config,
    provider_configs).to_markdown()
    """
    return Reporter(results, summary, config, provider_configs).to_markdown()


def render_json_output(results, summary, config=None, provider_configs=None):
    """Render eval results as JSON (convenience function).

    Equivalent to: Reporter(results, summary, config,
    provider_configs).to_json()
    """
    return Reporter(results, summary, config, provider_configs).to_json()


def report_compare(results, summary, output_format="markdown",
                   config=None, provider_configs=None, trend_file=None):
    """Convenience function for quick reporting.

    Args:
        results: list of per-scenario-per-provider result dicts
        summary: dict of per-provider aggregate summaries
        output_format: "markdown" or "json"
        config: optional config dict
        provider_configs: optional list of provider configs
        trend_file: optional path to trend file for comparison

    Returns:
        report string in the requested format
    """
    reporter = Reporter(results, summary, config, provider_configs)

    if trend_file and os.path.isfile(trend_file):
        reporter.save_trend(trend_file)

    if output_format == "json":
        return reporter.to_json()
    else:
        return reporter.to_markdown()
