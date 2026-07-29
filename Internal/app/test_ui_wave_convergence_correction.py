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


def test_macos_backup_settings_uses_current_confirmed_acknowledged_activation():
    port_app = APP_DIR / "Ports" / "macOS" / "app"
    script = r'''
import json
import os
import ast
from pathlib import Path
import sys
import tempfile

with tempfile.TemporaryDirectory(prefix="ui_wave_macos_backup_") as owned:
    os.environ["MUMBLE_TEST_DATA_DIR"] = owned
    os.environ["MUMBLE_OFFLINE_TESTS"] = "1"
    sys.path.insert(0, sys.argv[1])
    import ai
    import model_authority
    from settings import Settings

    app_window_source = Path(sys.argv[1], "app_window.py").read_text(encoding="utf-8")
    tree = ast.parse(app_window_source)
    app_window_class = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AppWindow"
    )
    activation_method = next(
        node for node in app_window_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "_activate_model_provider"
    )
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[activation_method], type_ignores=[]
    )), "app_window.py", "exec"), namespace)

    settings = Settings()
    settings.update(
        llm_provider="openrouter",
        cerebras_api_key="new-key",
        cerebras_model="exact-model",
        _confirmed_text_models={},
    )

    class Controller:
        def __init__(self):
            self.settings = settings
            self.pro_key_failed = True
            self.acks = []
            self.deny_provider = False

        def _apply_settings_change(self, key):
            self.acks.append(key)
            return not (self.deny_provider and key == "llm_provider")

    controller = Controller()
    window = type("BackupWindow", (), {})()
    window._activate_model_provider = namespace["_activate_model_provider"].__get__(window)
    window.ctrl = controller
    window.settings = settings

    def record():
        state = settings.authority_read()
        entry = state.get(model_authority.AUTHORITY_KEY, {}).get("cerebras", {})
        return {
            "provider": state.get("llm_provider"),
            "state": entry.get("state"),
            "generation": entry.get("generation"),
            "request_id": entry.get("request_id"),
            "confirmed_generation": entry.get("confirmed_generation"),
            "models": entry.get("models"),
        }

    ai.fetch_models = lambda _provider, _key: []
    failed = window._activate_model_provider(
        "cerebras", "exact-model", key="new-key"
    )
    failed_record = record()

    def stale_fetch(provider, _key):
        rival = model_authority.ModelDiscoveryAuthority(
            settings=settings,
            providers=ai.PROVIDERS,
            fetch_models=lambda *_args: ["exact-model"],
            controller_reload=lambda key: {
                "ok": bool(controller._apply_settings_change(key))
            },
        )
        rival.begin(provider, provider_activation=True)
        return ["exact-model"]

    ai.fetch_models = stale_fetch
    stale = window._activate_model_provider(
        "cerebras", "exact-model", key="new-key"
    )
    stale_record = record()

    ai.fetch_models = lambda _provider, _key: ["exact-model"]
    controller.deny_provider = True
    denied = window._activate_model_provider(
        "cerebras", "exact-model", key="new-key"
    )
    denied_record = record()

    controller.deny_provider = False
    ack_start = len(controller.acks)
    accepted = window._activate_model_provider(
        "cerebras", "exact-model", key="new-key"
    )
    accepted_record = record()
    accepted_acks = controller.acks[ack_start:]

    controller_source = Path(sys.argv[1], "mumble_mac.py").read_text(encoding="utf-8")
    print(json.dumps({
        "failed": failed,
        "failed_record": failed_record,
        "stale": stale,
        "stale_record": stale_record,
        "denied": denied,
        "denied_record": denied_record,
        "accepted": accepted,
        "accepted_record": accepted_record,
        "accepted_acks": accepted_acks,
        "request_ids": [
            failed_record["request_id"], stale_record["request_id"],
            denied_record["request_id"], accepted_record["request_id"],
        ],
        "backup_route_guarded": "self._activate_model_provider(" in app_window_source,
        "backup_route_direct": "self.ctrl.set_llm_provider(" in app_window_source,
        "controller_direct_setter": "def set_llm_provider(" in controller_source,
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
    evidence = json.loads(result.stdout.strip().splitlines()[-1])

    assert evidence["failed"][0] is False
    assert evidence["failed_record"]["provider"] == "openrouter"
    assert evidence["stale"][0] is False
    assert evidence["stale_record"]["provider"] == "openrouter"
    assert evidence["denied"][0] is False
    assert evidence["denied_record"]["provider"] == "openrouter"
    assert evidence["accepted"][0] is True
    assert evidence["accepted_record"] == {
        "provider": "cerebras",
        "state": "confirmed",
        "generation": evidence["accepted_record"]["generation"],
        "request_id": evidence["accepted_record"]["request_id"],
        "confirmed_generation": evidence["accepted_record"]["generation"],
        "models": ["exact-model"],
    }
    assert evidence["accepted_acks"] == [
        "_confirmed_text_models",
        "_confirmed_text_models",
        "_confirmed_text_models",
        "llm_provider",
    ]
    assert all(evidence["request_ids"])
    assert len(set(evidence["request_ids"])) == len(evidence["request_ids"])
    assert evidence["backup_route_guarded"] is True
    assert evidence["backup_route_direct"] is False
    assert evidence["controller_direct_setter"] is False
