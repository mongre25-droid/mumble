"""Cross-platform Reader contract checks for the guarded processing route."""

import json
from pathlib import Path
import subprocess
import sys

import pytest


APP_DIR = Path(__file__).resolve().parent
PLATFORM_APP_DIRS = (
    APP_DIR,
    APP_DIR / "Ports" / "macOS" / "app",
    APP_DIR / "Ports" / "Linux" / "app",
)


@pytest.mark.parametrize("platform_app", PLATFORM_APP_DIRS, ids=("windows", "macos", "linux"))
def test_reader_route_matrix_is_enforced_without_network(platform_app):
    script = r'''
import importlib
import json
import sys

sys.path.insert(0, sys.argv[1])
shell = importlib.import_module("webui_shell")


class Settings:
    def __init__(self, **overrides):
        self.values = {
            "pro_mode": True,
            "local_only_mode": False,
            "instant_text": False,
            "llm_provider": "cerebras",
            "cerebras_api_key": "test-secret",
            "cerebras_model": "gpt-oss-120b",
        }
        self.values.update(overrides)

    def get(self, key, default=None):
        return self.values.get(key, default)


def run(overrides, provider_failure=False):
    calls = []

    def provider(*_args, **_kwargs):
        calls.append("provider")
        if provider_failure:
            raise RuntimeError("mocked provider failure")
        return "guarded summary"

    shell.ai.cerebras_chat = provider
    api = shell.Api.__new__(shell.Api)
    api.settings = Settings(**overrides)
    result = api.reader_summarize("private reader text")
    return {"result": result, "calls": calls}


evidence = {
    "device_only": run({"local_only_mode": True}),
    "hosted_disabled": run({"pro_mode": False}),
    "missing_key": run({"cerebras_api_key": ""}),
    "unsupported_provider": run({"llm_provider": "unsupported"}),
    "allowed": run({}),
    "provider_failure": run({}, provider_failure=True),
}
print(json.dumps(evidence))
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(platform_app)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=platform_app,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    evidence = json.loads(result.stdout.strip().splitlines()[-1])

    for scenario, reason in (
        ("device_only", "device_only"),
        ("hosted_disabled", "hosted_processing_off"),
        ("missing_key", "missing_key"),
        ("unsupported_provider", "unsupported_provider"),
    ):
        observed = evidence[scenario]
        assert observed["calls"] == []
        assert observed["result"]["ok"] is False
        assert observed["result"]["route"]["reason"] == reason

    allowed = evidence["allowed"]
    assert allowed["calls"] == ["provider"]
    assert allowed["result"]["ok"] is True
    assert allowed["result"]["summary"] == "guarded summary"
    assert allowed["result"]["route"]["effective_route"] == "hosted"

    failed = evidence["provider_failure"]
    assert failed["calls"] == ["provider"]
    assert failed["result"]["ok"] is False
    assert failed["result"]["message"] == "mocked provider failure"


def test_every_maintained_reader_uses_the_same_guarded_seam():
    canonical_route = (APP_DIR / "processing_route.py").read_text(
        encoding="utf-8"
    ).replace("\r\n", "\n")

    for platform_app in PLATFORM_APP_DIRS:
        route_file = platform_app / "processing_route.py"
        assert route_file.read_text(encoding="utf-8").replace(
            "\r\n", "\n"
        ) == canonical_route

        shell_source = (platform_app / "webui_shell.py").read_text(encoding="utf-8")
        reader_source = shell_source.split("    def reader_summarize", 1)[1].split(
            "\n    # ---- store helpers", 1
        )[0]
        assert "processing_route.snapshot_inputs(" in reader_source
        assert "processing_route.call_provider(" in reader_source
        assert "summary = ai.cerebras_chat(" not in reader_source


def test_linux_release_allowlist_includes_the_processing_route():
    release_source = (
        APP_DIR / "Ports" / "Linux" / "build_release.py"
    ).read_text(encoding="utf-8")
    runtime_paths = release_source.split("RUNTIME_PATHS = (", 1)[1].split(
        "\n)", 1
    )[0]
    assert '"processing_route.py"' in runtime_paths
