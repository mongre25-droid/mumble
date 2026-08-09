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


def test_linux_ci_installs_the_tray_namespace_for_tests_and_lifecycle():
    source = _source()

    assert source.count("gir1.2-ayatanaappindicator3-0.1") >= 2


def test_linux_lifecycle_smoke_import_runs_from_the_installed_app_directory():
    source = _source()

    lifecycle = source.split("  linux-lifecycle:", 1)[1]
    assert 'cd "$RELEASE_ROOT/app"' in lifecycle
    assert "./.venv/bin/python" in lifecycle


def test_windows_tests_checkout_the_history_required_by_provenance():
    source = _source()
    tests = source.split("  python-tests:", 1)[1].split(
        "  website-security:", 1
    )[0]

    assert re.search(
        r"uses: actions/checkout@v4\s+"
        r"if: matrix\.platform == 'windows'\s+"
        r"with:\s+fetch-depth: 0",
        tests,
    )
    assert re.search(
        r"uses: actions/checkout@v4\s+"
        r"if: matrix\.platform != 'windows'",
        tests,
    )


def test_website_candidate_build_upload_contract_is_complete():
    source = _source()
    website = source.split("  website-build:", 1)[1].split(
        "  website-windows-cleanliness:", 1
    )[0]

    assert "npm ci" in website
    assert "npm run build" in website
    assert "uses: actions/upload-artifact@v4" in website
    assert "name: candidate-website" in website
    assert "path: Development Files/Marketing/Website/dist" in website


def test_website_provenance_jobs_checkout_complete_history():
    source = _source()
    website_build = source.split("  website-build:", 1)[1].split(
        "  website-windows-cleanliness:", 1
    )[0]
    website_cleanliness = source.split(
        "  website-windows-cleanliness:", 1
    )[1].split("  windows-package:", 1)[0]

    for job in (website_build, website_cleanliness):
        assert re.search(
            r"uses: actions/checkout@v4\s+with:\s+fetch-depth: 0",
            job,
        )


def test_linux_lifecycle_provisions_the_exact_packaged_python_minor():
    source = _source()
    lifecycle = source.split("  linux-lifecycle:", 1)[1].split(
        "  ci-result:", 1
    )[0]

    assert "container: debian:13" in lifecycle
    assert "python3.13" in lifecycle
    assert "python3.13-venv" in lifecycle
    assert "sys.version_info[:2] == (3, 13)" in lifecycle
    assert lifecycle.index("sys.version_info[:2] == (3, 13)") < lifecycle.index(
        'bash "$RELEASE_ROOT/install.sh"'
    )


def test_security_matrix_audits_the_complete_development_lock():
    source = _source()

    security = source.split("  python-security:", 1)[1].split(
        "  python-tests:", 1
    )[0]
    assert "pip_audit -r requirements-dev.txt" in security
