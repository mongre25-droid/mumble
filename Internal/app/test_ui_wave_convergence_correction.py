"""Focused regressions for the UI-wave convergence correction."""

import json
from pathlib import Path
import subprocess
import sys

import pytest


APP_DIR = Path(__file__).resolve().parent


def _bridge_result(port):
    port_app = APP_DIR / "Ports" / port / "app"
    script = r'''
import json
import os
import sys
import tempfile

with tempfile.TemporaryDirectory(prefix="ui_wave_bridge_") as owned:
    os.environ["MUMBLE_TEST_DATA_DIR"] = owned
    os.environ["MUMBLE_OFFLINE_TESTS"] = "1"
    sys.path.insert(0, sys.argv[1])
    import webui_shell
    from settings import Settings

    api = webui_shell.Api.__new__(webui_shell.Api)
    api.settings = Settings()
    api.settings.update(llm_provider="cerebras", ui_effects="standard")

    activations = []
    def guarded_activation(provider):
        activations.append(provider)
        return {"ok": False, "message": "guarded activation denied"}
    api.activate_model_provider = guarded_activation

    reloads = []
    webui_shell._ctrl_send = lambda message, **_kwargs: (
        reloads.append(message) or {"ok": True}
    )
    provider_result = api.set_setting("llm_provider", "openrouter")
    effects_result = api.set_setting("ui_effects", "full")
    print(json.dumps({
        "provider_result": provider_result,
        "provider": api.settings.get("llm_provider"),
        "activations": activations,
        "effects_result": effects_result,
        "effects": api.settings.get("ui_effects"),
        "reloads": reloads,
    }))
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(port_app)],
        cwd=APP_DIR,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("port", ("macOS", "Linux"))
def test_ordinary_port_bridge_uses_guarded_provider_activation(port):
    evidence = _bridge_result(port)

    assert evidence["provider_result"] == {
        "ok": False,
        "message": "guarded activation denied",
    }
    assert evidence["activations"] == ["openrouter"]
    assert evidence["provider"] == "cerebras"
    assert evidence["effects_result"] == {
        "ok": True,
        "value": "full",
        "applied": True,
    }
    assert evidence["effects"] == "full"
    assert evidence["reloads"] == [{"cmd": "reload", "key": "ui_effects"}]
