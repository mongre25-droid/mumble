"""Prompt quality evaluation harness for Mumble.

Runs scenario packs across configured providers and produces comparison data
(quality, latency, token count, cost).

Public API:
- EvalHarness: class for running scenarios across providers concurrently
- Reporter: generate comparison reports (Markdown, JSON) + trend tracking
- get_scenarios: load scenario packs by name
- score: quality scoring for a single output
- estimate_cost: cost estimation from word counts
- report_compare: convenience function for quick reporting
"""

from eval.harness import EvalHarness, resolve_providers  # noqa: F401
from eval.reporter import (  # noqa: F401
    Reporter, report_compare, render_markdown, render_json_output,
)
from eval.scenarios import get_scenarios  # noqa: F401
from eval.metrics import score, estimate_cost  # noqa: F401
