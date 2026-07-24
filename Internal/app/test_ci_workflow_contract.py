"""Public evidence contract for GitHub Actions CI."""

from pathlib import Path
import re


WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"


def _source():
    return WORKFLOW.read_text(encoding="utf-8")


def test_ci_defines_triggers_permissions_and_concurrency():
    source = _source()

    assert re.search(r"(?m)^  workflow_dispatch:\s*$", source)
    assert re.search(r"(?m)^permissions:\s*\n  contents: read\s*$", source)
    assert re.search(r"(?m)^concurrency:\s*\n  group:", source)
    assert "cancel-in-progress:" in source


def test_ci_preserves_independent_evidence_jobs():
    source = _source()
    jobs = {
        match.group(1)
        for match in re.finditer(r"(?m)^  ([a-z][a-z0-9-]+):\s*$", source)
    }

    assert {
        "python-security",
        "python-tests",
        "website-security",
        "website-build",
        "windows-package",
        "macos-package",
        "linux-package",
        "linux-lifecycle",
        "ci-result",
        "promote-candidate",
    } <= jobs
    assert "fail-fast: false" in source


def test_ci_separates_diagnostics_candidates_and_manual_promotion():
    source = _source()

    assert "diagnostic-${{ matrix.platform }}-tests" in source
    assert "if: always()" in source
    assert re.search(
        r"if: success\(\).*?name: candidate-",
        source,
        flags=re.DOTALL,
    )
    promotion = source.split("  promote-candidate:", 1)[1]
    assert "github.event_name == 'workflow_dispatch'" in promotion
    assert "needs:" in promotion
    assert "name: promotable-mumble-${{ github.sha }}" in promotion


def test_ci_requests_json_and_junit_from_every_platform_runner():
    source = _source()

    assert source.count("--json test-results.json --junit test-results.xml") >= 3
