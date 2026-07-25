#!/usr/bin/env python3
"""Settings route projection, validation, and failure-path regressions."""
import sys
import types

# The settings bridge imports the clipboard adapter at module import time. These
# tests never touch the system clipboard, so a tiny dependency seam keeps the
# route matrix offline and dependency-free.
sys.modules.setdefault(
    "pyperclip",
    types.SimpleNamespace(copy=lambda _text: None, paste=lambda: ""),
)
_pil = types.ModuleType("PIL")
_pil.Image = types.SimpleNamespace()
_pil.ImageGrab = types.SimpleNamespace(grabclipboard=lambda: None)
sys.modules.setdefault("PIL", _pil)

import transcription
import processing_route
import webui_shell


PASS = 0
FAIL = 0


def check(name, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [ok  ] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}")


class MemorySettings:
    def __init__(self, **values):
        self.data = {
            "transcription_mode": "local",
            "cloud_transcription_provider": "groq",
            "groq_api_key": "",
            "openai_api_key": "",
            "openrouter_api_key": "",
            "local_only_mode": False,
            "pro_mode": True,
            "llm_provider": "cerebras",
            "cerebras_api_key": "",
            "instant_text": True,
        }
        self.data.update(values)
        self.atomic_calls = []
        self.fail_saves = False

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        if self.fail_saves:
            return False
        self.data[key] = value
        return True

    def atomic_mapping_update(self, key, mutator):
        mapping = dict(self.data.get(key, {}))
        mutator(mapping)
        self.data[key] = mapping
        self.atomic_calls.append((key, dict(mapping)))
        return mapping


def api_for(settings):
    api = webui_shell.Api.__new__(webui_shell.Api)
    api.settings = settings
    return api


def main():
    print("== VAL-ROUTE-001: effective transcription route is key-aware ==")
    settings = MemorySettings(
        transcription_mode="cloud", cloud_transcription_provider="groq"
    )
    api = api_for(settings)
    route = api._settings_route_state()["transcription"]
    check("Cloud without its selected provider key stays on-device",
          route["effective"] == "local" and route["reason"] == "no_key")
    settings.data["groq_api_key"] = "gsk-ready"
    route = api._settings_route_state()["transcription"]
    check("Cloud with the matching key sends audio to that provider",
          route["effective"] == "cloud" and route["sends_audio"] is True)

    print("== VAL-ROUTE-002: processing and transcription remain separate ==")
    settings.data.update(
        llm_provider="cerebras", cerebras_api_key="csk-ready",
        pro_mode=True, instant_text=True,
    )
    routes = api._settings_route_state()
    check("instant plain dictation stays local",
          routes["plain_processing"]["effective"] == "local")
    check("explicit actions use hosted text processing when ready",
          routes["action_processing"]["effective"] == "cloud"
          and routes["action_processing"]["sends_text"] is True)
    check("hosted processing does not alter Cloud transcription readiness",
          routes["transcription"]["effective"] == "cloud")

    print("== VAL-ROUTE-003: device-only and unsupported routes fail closed ==")
    settings.data["local_only_mode"] = True
    routes = api._settings_route_state()
    check("device-only override blocks audio upload",
          routes["transcription"]["effective"] == "local"
          and routes["transcription"]["reason"] == "local_only")
    check("device-only override blocks transcript upload",
          routes["action_processing"]["effective"] == "local"
          and routes["action_processing"]["reason"] == "local_only")
    settings.data.update(
        local_only_mode=False,
        cloud_transcription_provider="legacy-stt",
        llm_provider="legacy-llm",
    )
    routes = api._settings_route_state()
    check("unsupported STT id is preserved but inactive",
          routes["transcription"]["provider"] == "legacy-stt"
          and routes["transcription"]["reason"] == "unsupported_provider")
    check("unsupported LLM id is preserved but inactive",
          routes["action_processing"]["provider"] == "legacy-llm"
          and routes["action_processing"]["reason"] == "unsupported_provider")

    print("== VAL-ROUTE-004: bridge nested edits use atomic mapping updates ==")
    settings = MemorySettings(prompt_prefs={"tone": "Neutral"})
    api = api_for(settings)
    original_ctrl_send = webui_shell._ctrl_send
    webui_shell._ctrl_send = lambda *_a, **_k: {"ok": True}
    try:
        result = api.set_setting("prompt_prefs.detail", "Comprehensive")
    finally:
        webui_shell._ctrl_send = original_ctrl_send
    check("nested bridge edit succeeds", result.get("ok") is True)
    check("nested bridge edit used the atomic mapping transaction",
          settings.atomic_calls and settings.atomic_calls[-1][0] == "prompt_prefs")
    check("existing sibling remains beside edited child",
          settings.data["prompt_prefs"] == {
              "tone": "Neutral", "detail": "Comprehensive"
          })

    print("== VAL-ROUTE-005: failed key durability prevents a false test ==")
    settings = MemorySettings()
    settings.fail_saves = True
    api = api_for(settings)
    called = []
    original_test = webui_shell.ai.cerebras_test
    webui_shell.ai.cerebras_test = lambda _key: called.append(True) or (True, "bad")
    try:
        result = api.test_key("cerebras", "csk-not-durable")
    finally:
        webui_shell.ai.cerebras_test = original_test
    check("failed key save is reported", result.get("persisted") is False)
    check("provider test is not run against an unsaved key", not called)

    print("== VAL-ROUTE-006: runtime STT never guesses a provider ==")
    unsupported = MemorySettings(
        pro_mode=True,
        local_only_mode=False,
        transcription_mode="cloud",
        cloud_transcription_provider="retired-provider",
        groq_api_key="gsk-must-not-be-used",
    )
    unsupported_invocation = processing_route.snapshot_inputs(
        unsupported, feature="dictation", lane="speech_to_text"
    )
    try:
        transcription.transcribe([0.0], unsupported_invocation)
        rejected = False
    except processing_route.HostedRouteBlocked as exc:
        rejected = exc.reason == "unsupported_provider"
    check("unknown runtime STT provider is rejected before any upload",
          rejected)

    print()
    if FAIL:
        print(f"{FAIL} FAILED, {PASS} passed")
        raise SystemExit(1)
    print(f"ALL PASS ({PASS})")


if __name__ == "__main__":
    main()
