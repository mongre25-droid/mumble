"""Keep Issue #47 Reader speech guidance aligned with accepted runtime truth."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HANDBOOK_RULE = (
    "Reader speech makes one attempt with the frozen selected provider and model; "
    "failure stops honestly; no sibling-model or cross-provider fallback is attempted."
)


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _between(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_reader_speech_records_match_the_one_attempt_runtime_authority():
    handbook = _read("Development Files/Core/HANDBOOK.html")
    assert HANDBOOK_RULE in handbook
    assert "Reader speech fallback may try a compatible model" not in handbook

    help_source = _read("Development Files/Marketing/Website/src/pages/help.astro")
    assert "one attempt using the frozen selected provider and model" in help_source
    assert "No sibling model or other provider is attempted" in help_source
    assert "may try a compatible model" not in help_source

    browser_contract = _read(
        "Development Files/Marketing/Website/scripts/verify-browser-contract.mjs"
    )
    assert "one-attempt frozen provider/model authority" in browser_contract
    assert "unauthorised compatible or sibling model attempt" in browser_contract

    platform_roots = (
        "Internal/app",
        "Internal/app/Ports/macOS/app",
        "Internal/app/Ports/Linux/app",
    )
    for platform_root in platform_roots:
        disclosure = _between(
            _read(f"{platform_root}/webui/app.js"),
            "async function confirmReaderCloudUse",
            "function readerBuildPane",
        )
        assert "Mumble makes one attempt with the selected provider and model" in disclosure
        assert "it does not try a sibling model or another provider" in disclosure
        assert "another compatible model" not in disclosure

        bridge = _read(f"{platform_root}/webui_shell.py")
        assert "A frozen decision authorizes one exact provider/model attempt" in bridge
        assert "no sibling model or different provider is attempted" in bridge

        runtime = _between(
            _read(f"{platform_root}/ai/__init__.py"),
            "def synthesize_with_fallback",
            "\ndef ",
        )
        assert "one exact frozen provider and model" in runtime
        assert "prov_order = [requested]" in runtime
        assert "ordered = [model or route_decision.model]" in runtime

        reader_test = _read(f"{platform_root}/test_reader.py")
        assert "frozen-model: only the authorized model was tried" in reader_test
        assert "frozen-model: OpenAI was never reached" in reader_test
