"""Regression tests for the independent Issue #14 correction findings.

All provider seams are replaced in-process.  Nothing in this module is allowed
to contact a network service.
"""

from pathlib import Path
import json
import logging
import subprocess
import sys

import pytest

import processing_route


APP_DIR = Path(__file__).resolve().parent
PLATFORM_APP_DIRS = (
    APP_DIR,
    APP_DIR / "Ports" / "macOS" / "app",
    APP_DIR / "Ports" / "Linux" / "app",
)


class MemorySettings:
    def __init__(self, **overrides):
        self.values = {
            "pro_mode": True,
            "local_only_mode": False,
            "instant_text": False,
            "llm_provider": "cerebras",
            "cerebras_api_key": "distinctive-credential-14c",
            "cerebras_model": "gpt-oss-120b",
            "reader_tts_provider": "openrouter",
            "reader_tts_model": "openai/gpt-4o-mini-tts",
            "openrouter_api_key": "distinctive-reader-key-14c",
            "user_name": "DISTINCTIVE_PRIVATE_NAME_14C",
            "prompt_prefs": {"tone": "DISTINCTIVE_PRIVATE_TONE_14C"},
            "primary_language": "private-language-14c",
            "foreign_languages": ["DISTINCTIVE_PRIVATE_LANGUAGE_14C"],
            "vocabulary": {"wrong": "DISTINCTIVE_PRIVATE_VOCAB_14C"},
            "vocabulary_terms": ["DISTINCTIVE_PRIVATE_TERM_14C"],
            "modes": {"email": {"signoff": "DISTINCTIVE_PRIVATE_SIGNOFF_14C"}},
        }
        self.values.update(overrides)

    def get(self, key, default=None):
        return self.values.get(key, default)


def _snapshot(settings=None, *, feature="prompt", lane="prompt"):
    return processing_route.snapshot_inputs(
        settings or MemorySettings(),
        feature=feature,
        lane=lane,
        context="DISTINCTIVE_PRIVATE_CONTEXT_14C",
        context_policy="highlighted_selection",
        context_strict=True,
        local_model_ready=False,
    )


def test_private_snapshot_values_never_enter_repr_logs_or_public_projection(caplog):
    snapshot = _snapshot()
    private_values = (
        "DISTINCTIVE_PRIVATE_NAME_14C",
        "DISTINCTIVE_PRIVATE_TONE_14C",
        "DISTINCTIVE_PRIVATE_LANGUAGE_14C",
        "DISTINCTIVE_PRIVATE_VOCAB_14C",
        "DISTINCTIVE_PRIVATE_TERM_14C",
        "DISTINCTIVE_PRIVATE_SIGNOFF_14C",
        "DISTINCTIVE_PRIVATE_CONTEXT_14C",
        "distinctive-credential-14c",
        "distinctive-reader-key-14c",
    )

    with caplog.at_level(logging.INFO):
        logging.getLogger("mumble.processing").info("snapshot=%r", snapshot)
    rendered = "\n".join(
        (repr(snapshot), caplog.text, json.dumps(snapshot.route.public_dict()))
    )

    for value in private_values:
        assert value not in rendered
    assert set(snapshot.route.public_dict()) == {
        "invocation_id", "feature", "lane", "requested_route",
        "effective_route", "reason", "provider", "model",
        "endpoint_class", "privacy_boundary", "ready",
        "provider_supported", "key_present", "pro_mode", "device_only",
    }


@pytest.mark.parametrize("platform_app", PLATFORM_APP_DIRS, ids=("windows", "macos", "linux"))
def test_actual_text_provider_boundaries_require_a_frozen_decision(platform_app):
    script = r'''
import importlib
import json
import sys

sys.path.insert(0, sys.argv[1])
ai = importlib.import_module("ai")
route = importlib.import_module("processing_route")

class Settings:
    def __init__(self, local_only):
        self.values = {
            "pro_mode": True,
            "local_only_mode": local_only,
            "instant_text": False,
            "llm_provider": "cerebras",
            "cerebras_api_key": "test-only-key",
            "cerebras_model": "gpt-oss-120b",
        }
    def get(self, key, default=None):
        return self.values.get(key, default)

calls = []
def recorder(*_args, **_kwargs):
    calls.append("network")
    raise AssertionError("provider recorder reached")
ai.urllib.request.urlopen = recorder

def consume(value):
    if hasattr(value, "__next__"):
        list(value)

def invoke(name, decision_marker):
    before = len(calls)
    error = None
    try:
        kwargs = {} if decision_marker == "missing" else {"route_decision": decision_marker}
        if name == "chat":
            ai.cerebras_chat("system", "user", "test-only-key", **kwargs)
        elif name == "stream":
            consume(ai.cerebras_chat_stream("system", "user", "test-only-key", **kwargs))
        elif name == "polish":
            ai.polish_text("private text", "test-only-key", **kwargs)
        elif name == "intent":
            consume(ai.cerebras_intent("instruction", "private context", "", "test-only-key", **kwargs))
        elif name == "prompt":
            consume(ai.cerebras_prompt("private request", "test-only-key", **kwargs))
    except Exception as exc:
        error = type(exc).__name__
    return {"calls": len(calls) - before, "error": error}

blocked = route.snapshot(Settings(True), feature="prompt", lane="prompt")
evidence = {}
for name in ("chat", "stream", "polish", "intent", "prompt"):
    evidence[name + "_missing"] = invoke(name, "missing")
    evidence[name + "_blocked"] = invoke(name, blocked)
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
    for name in ("chat", "stream", "polish", "intent", "prompt"):
        assert evidence[name + "_missing"] == {"calls": 0, "error": "TypeError"}
        assert evidence[name + "_blocked"] == {
            "calls": 0,
            "error": "HostedRouteBlocked",
        }


@pytest.mark.parametrize("platform_app", PLATFORM_APP_DIRS, ids=("windows", "macos", "linux"))
def test_modular_provider_adapters_require_route_permission_before_transport(platform_app):
    script = r'''
import importlib
import json
import sys

sys.path.insert(0, sys.argv[1])
ai = importlib.import_module("ai")
route = importlib.import_module("processing_route")

classes = {
    "cerebras": ai.CerebrasProvider,
    "openrouter": ai.OpenRouterProvider,
    "openai": ai.OpenAIProvider,
    "anthropic": ai.AnthropicProvider,
    "deepseek": ai.DeepSeekProvider,
    "groq": ai.GroqProvider,
}

class Settings:
    def __init__(self, provider):
        self.values = {
            "pro_mode": True, "local_only_mode": True, "instant_text": False,
            "llm_provider": provider, provider + "_api_key": "test-key",
        }
    def get(self, key, default=None):
        return self.values.get(key, default)

evidence = {}
for provider, cls in classes.items():
    adapter = cls("test-key")
    blocked = route.snapshot(Settings(provider), feature="prompt", lane="prompt")
    for method_name in ("chat", "chat_stream"):
        method = getattr(adapter, method_name)
        for marker, kwargs in (("missing", {}), ("blocked", {"route_decision": blocked})):
            error = None
            try:
                value = method([{"role": "user", "content": "private"}], **kwargs)
                if hasattr(value, "__next__"):
                    list(value)
            except Exception as exc:
                error = type(exc).__name__
            evidence[f"{provider}_{method_name}_{marker}"] = error
print(json.dumps(evidence))
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(platform_app)],
        capture_output=True, text=True, timeout=30, cwd=platform_app,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    for provider in ("cerebras", "openrouter", "openai", "anthropic", "deepseek", "groq"):
        for method in ("chat", "chat_stream"):
            assert evidence[f"{provider}_{method}_missing"] == "TypeError"
            assert evidence[f"{provider}_{method}_blocked"] == "HostedRouteBlocked"


@pytest.mark.parametrize(
    ("platform_app", "module_name"),
    ((PLATFORM_APP_DIRS[1], "mumble_mac"), (PLATFORM_APP_DIRS[2], "mumble_linux")),
    ids=("macos", "linux"),
)
def test_port_generation_entry_points_freeze_nested_inputs_and_device_only_is_zero_provider(
    platform_app, module_name
):
    script = r'''
import importlib
import json
import sys

sys.path.insert(0, sys.argv[1])
controller = importlib.import_module(sys.argv[2])

class Settings:
    def __init__(self, local_only=False):
        self.values = {
            "pro_mode": True, "local_only_mode": local_only, "instant_text": False,
            "llm_provider": "cerebras", "cerebras_api_key": "frozen-key",
            "cerebras_model": "frozen-model", "user_name": "Frozen User",
            "prompt_prefs": {"tone": "frozen-tone", "nested": {"detail": "frozen-detail"}},
            "primary_language": "fr", "english_only": False,
            "foreign_languages": ["arabic"], "vocabulary": {"heard": "Frozen Term"},
            "vocabulary_terms": ["Frozen Term"], "modes": {"email": {"signoff": "Frozen Signoff"}},
            "format_enabled": True, "polish_aggressiveness": "Thorough",
        }
    def get(self, key, default=None):
        return self.values.get(key, default)

def make_app(settings):
    app = controller.Mumble.__new__(controller.Mumble)
    app.settings = settings
    app._gather_context = lambda *_args: ("frozen private context", True)
    app._conv_context_for_prompt = lambda: ""
    app._mark_llm_ok = lambda: None
    app._pro_fallback_notice = lambda *_args: None
    app._local_llm_generate = lambda *_args, **_kwargs: None
    app._builder = lambda *_args, **_kwargs: ("text", "local result")
    return app

source = Settings(False)
app = make_app(source)
captured = {}
def provider_recorder(*_args, **kwargs):
    snap = kwargs["invocation_snapshot"]
    source.values["user_name"] = "MUTATED USER"
    source.values["prompt_prefs"]["nested"]["detail"] = "MUTATED DETAIL"
    source.values["vocabulary_terms"].append("MUTATED TERM")
    source.values["modes"]["email"]["signoff"] = "MUTATED SIGNOFF"
    captured.update({
        "name": snap.user_name,
        "prefs": snap.prompt_prefs_dict(),
        "terms": list(snap.vocabulary_terms),
        "modes": snap.modes_dict(),
        "context": snap.context,
        "provider": snap.route.provider,
        "model": snap.route.model,
    })
    return "prompt", "hosted result"
app._cloud_generate = provider_recorder
allowed = app._generate("private request", "prompt", "private request")

blocked_calls = []
local_capture = {}
for lane in ("text", "prompt"):
    blocked_source = Settings(True)
    blocked_app = make_app(blocked_source)
    blocked_app._cloud_generate = lambda *_args, **_kwargs: blocked_calls.append(lane) or (lane, "bad")
    def local_recorder(*_args, **kwargs):
        snap = kwargs["invocation_snapshot"]
        blocked_source.values["prompt_prefs"]["nested"]["detail"] = "MUTATED LOCAL"
        blocked_source.values["modes"]["email"]["signoff"] = "MUTATED LOCAL"
        local_capture[lane] = {
            "prefs": snap.prompt_prefs_dict(),
            "modes": snap.modes_dict(),
            "context": snap.context,
            "device_only": snap.route.device_only,
        }
        return "frozen local"
    blocked_app._local_llm_generate = local_recorder
    blocked_app._generate("private request", lane, "private request")

print(json.dumps({
    "captured": captured, "allowed": allowed, "blocked_calls": blocked_calls,
    "local_capture": local_capture,
}))
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(platform_app), module_name],
        capture_output=True, text=True, timeout=45, cwd=platform_app,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence["blocked_calls"] == []
    assert evidence["local_capture"] == {
        "prompt": {
            "prefs": {"nested": {"detail": "frozen-detail"}, "tone": "frozen-tone"},
            "modes": {"email": {"signoff": "Frozen Signoff"}},
            "context": "frozen private context",
            "device_only": True,
        },
        "text": {
            "prefs": {"nested": {"detail": "frozen-detail"}, "tone": "frozen-tone"},
            "modes": {"email": {"signoff": "Frozen Signoff"}},
            "context": "frozen private context",
            "device_only": True,
        },
    }
    assert evidence["allowed"][:2] == ["prompt", "hosted result"]
    assert evidence["captured"] == {
        "name": "Frozen User",
        "prefs": {"nested": {"detail": "frozen-detail"}, "tone": "frozen-tone"},
        "terms": ["Frozen Term"],
        "modes": {"email": {"signoff": "Frozen Signoff"}},
        "context": "frozen private context",
        "provider": "cerebras",
        "model": "frozen-model",
    }


@pytest.mark.parametrize("platform_app", PLATFORM_APP_DIRS, ids=("windows", "macos", "linux"))
@pytest.mark.parametrize("method", ("reader_tts", "reader_tts_test"))
def test_reader_speech_and_test_fail_closed_before_the_provider(platform_app, method):
    script = r'''
import importlib
import json
import sys

sys.path.insert(0, sys.argv[1])
shell = importlib.import_module("webui_shell")

class Settings:
    def __init__(self):
        self.values = {
            "pro_mode": True,
            "local_only_mode": True,
            "reader_tts_provider": "openrouter",
            "reader_tts_model": "openai/gpt-4o-mini-tts",
            "reader_voice": "alloy",
            "openrouter_api_key": "test-only-reader-key",
        }
    def get(self, key, default=None):
        return self.values.get(key, default)

calls = []
def provider(*_args, **_kwargs):
    calls.append("provider")
    return b"audio", "audio/mpeg", {"ok": True, "provider": "openrouter"}
shell.ai.synthesize_with_fallback = provider
api = shell.Api.__new__(shell.Api)
api.settings = Settings()
name = sys.argv[2]
result = getattr(api, name)("private reader text") if name == "reader_tts" else getattr(api, name)()
print(json.dumps({"calls": calls, "result": result}))
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(platform_app), method],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=platform_app,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence["calls"] == []
    assert evidence["result"]["ok"] is False
    assert evidence["result"]["route"]["reason"] == "device_only"


@pytest.mark.parametrize("platform_app", PLATFORM_APP_DIRS, ids=("windows", "macos", "linux"))
def test_openrouter_speech_adapter_accepts_and_propagates_the_frozen_decision(platform_app):
    script = r'''
import importlib
import json
import sys

sys.path.insert(0, sys.argv[1])
ai = importlib.import_module("ai")
route = importlib.import_module("processing_route")

class Settings:
    def get(self, key, default=None):
        return {
            "pro_mode": True,
            "local_only_mode": False,
            "reader_tts_provider": "openrouter",
            "reader_tts_model": "google/gemini-3.1-flash-tts-preview",
            "openrouter_api_key": "test-only-key",
        }.get(key, default)

decision = route.snapshot(
    Settings(), feature="reader", lane="reader_speech",
    provider_override="openrouter",
    model_override="google/gemini-3.1-flash-tts-preview",
)
seen = []
def recorder(*_args, **kwargs):
    seen.append(kwargs.get("route_decision") is decision)
    return b"audio", "audio/mpeg"
ai.openrouter_tts = recorder
provider = ai.OpenRouterTTSProvider()
error = None
try:
    provider.synthesize(
        "private reader text", "Fenrir", model=decision.model,
        route_decision=decision,
    )
except Exception as exc:
    error = type(exc).__name__
print(json.dumps({"error": error, "seen": seen}))
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(platform_app)],
        capture_output=True, text=True, timeout=30, cwd=platform_app,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence == {"error": None, "seen": [True]}


@pytest.mark.parametrize(
    ("platform_app", "module_name"),
    (
        (PLATFORM_APP_DIRS[0], "mumble"),
        (PLATFORM_APP_DIRS[1], "mumble_mac"),
        (PLATFORM_APP_DIRS[2], "mumble_linux"),
    ),
    ids=("windows", "macos", "linux"),
)
def test_cloud_stt_entry_points_fail_closed_and_pass_one_frozen_snapshot(
    platform_app, module_name
):
    script = r'''
import importlib
import json
import sys

sys.path.insert(0, sys.argv[1])
controller = importlib.import_module(sys.argv[2])

class Settings:
    def __init__(self, **overrides):
        self.values = {
            "pro_mode": True,
            "local_only_mode": False,
            "transcription_mode": "cloud",
            "cloud_transcription_provider": "groq",
            "groq_api_key": "FROZEN_STT_KEY_14D",
            "groq_transcription_model": "FROZEN_STT_MODEL_14D",
            "language": "fr",
            "vocabulary_terms": ["FROZEN_STT_TERM_14D"],
        }
        self.values.update(overrides)
    def get(self, key, default=None):
        return self.values.get(key, default)

def run(**overrides):
    settings = Settings(**overrides)
    app = controller.Mumble.__new__(controller.Mumble)
    app.settings = settings
    app._cloud_stt_failed = False
    app._notify = lambda *_args, **_kwargs: None
    app._local_transcribe = lambda *_args, **_kwargs: "local"
    calls = []
    def recorder(_audio, invocation_snapshot, **_kwargs):
        settings.values["cloud_transcription_provider"] = "openai"
        settings.values["groq_api_key"] = "MUTATED_LIVE_KEY_14D"
        settings.values["groq_transcription_model"] = "MUTATED_LIVE_MODEL_14D"
        settings.values["language"] = "de"
        settings.values["vocabulary_terms"].append("MUTATED_LIVE_TERM_14D")
        calls.append({
            "provider": invocation_snapshot.route.provider,
            "key": invocation_snapshot.route.api_key,
            "model": invocation_snapshot.route.model,
            "language": invocation_snapshot.primary_language,
            "terms": list(invocation_snapshot.vocabulary_terms),
        })
        return "cloud"
    controller.transcription.transcribe = recorder
    return {"result": app._transcribe([0.0]), "calls": calls}

evidence = {
    "device_only": run(local_only_mode=True),
    "hosted_off": run(pro_mode=False),
    "missing_key": run(groq_api_key=""),
    "unsupported": run(cloud_transcription_provider="unsupported"),
    "allowed": run(),
}
print(json.dumps(evidence))
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(platform_app), module_name],
        capture_output=True, text=True, timeout=45, cwd=platform_app,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    for scenario in ("device_only", "hosted_off", "missing_key", "unsupported"):
        assert evidence[scenario] == {"result": "local", "calls": []}
    assert evidence["allowed"] == {
        "result": "cloud",
        "calls": [{
            "provider": "groq",
            "key": "FROZEN_STT_KEY_14D",
            "model": "FROZEN_STT_MODEL_14D",
            "language": "fr",
            "terms": ["FROZEN_STT_TERM_14D"],
        }],
    }


@pytest.mark.parametrize("platform_app", PLATFORM_APP_DIRS, ids=("windows", "macos", "linux"))
def test_final_cloud_stt_adapter_requires_and_uses_the_frozen_snapshot(platform_app):
    script = r'''
import importlib
import json
import sys

import numpy as np

sys.path.insert(0, sys.argv[1])
route = importlib.import_module("processing_route")
tx = importlib.import_module("transcription")

class Settings:
    def __init__(self, local_only=False):
        self.values = {
            "pro_mode": True,
            "local_only_mode": local_only,
            "transcription_mode": "cloud",
            "cloud_transcription_provider": "groq",
            "groq_api_key": "DISTINCTIVE_FROZEN_STT_KEY_14D",
            "groq_transcription_model": "frozen-stt-model",
            "language": "fr",
            "vocabulary_terms": ["Frozen Proper Noun"],
        }
    def get(self, key, default=None):
        return self.values.get(key, default)

transport = []
def recorder(_info, key, model, _wav, language, _timeout, prompt=None):
    transport.append({
        "key": key, "model": model, "language": language, "prompt": prompt,
    })
    return "adapter-result"
tx._transcribe_multipart = recorder

def invoke(value):
    before = len(transport)
    error = None
    rendered = ""
    result = None
    try:
        result = tx.transcribe(np.zeros(8, dtype=np.float32), value)
    except Exception as exc:
        error = type(exc).__name__
        rendered = repr(exc) + str(exc)
    return {
        "calls": len(transport) - before,
        "error": error,
        "rendered": rendered,
        "result": result,
    }

missing = invoke(Settings())
blocked_settings = Settings(local_only=True)
blocked_snapshot = route.snapshot_inputs(
    blocked_settings, feature="dictation", lane="speech_to_text"
)
blocked = invoke(blocked_snapshot)
allowed_settings = Settings()
allowed_snapshot = route.snapshot_inputs(
    allowed_settings, feature="dictation", lane="speech_to_text"
)
allowed_settings.values.update({
    "cloud_transcription_provider": "openai",
    "groq_api_key": "MUTATED_LIVE_KEY_14D",
    "groq_transcription_model": "mutated-model",
    "language": "de",
    "vocabulary_terms": ["Mutated Term"],
})
allowed = invoke(allowed_snapshot)
print(json.dumps({
    "missing": missing,
    "blocked": blocked,
    "allowed": allowed,
    "transport": transport,
    "allowed_repr": repr(allowed_snapshot),
}))
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(platform_app)],
        capture_output=True, text=True, timeout=30, cwd=platform_app,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence["missing"] == {
        "calls": 0, "error": "TypeError",
        "rendered": (
            "TypeError('Cloud transcription requires an explicit "
            "ProcessingInputSnapshot')Cloud transcription requires an explicit "
            "ProcessingInputSnapshot"
        ),
        "result": None,
    }
    assert evidence["blocked"]["calls"] == 0
    assert evidence["blocked"]["error"] == "HostedRouteBlocked"
    assert evidence["allowed"] == {
        "calls": 1, "error": None, "rendered": "", "result": "adapter-result",
    }
    assert evidence["transport"] == [{
        "key": "DISTINCTIVE_FROZEN_STT_KEY_14D",
        "model": "frozen-stt-model",
        "language": "fr",
        "prompt": "Frozen Proper Noun",
    }]
    rendered = evidence["blocked"]["rendered"] + evidence["allowed_repr"]
    assert "DISTINCTIVE_FROZEN_STT_KEY_14D" not in rendered
    assert "MUTATED_LIVE_KEY_14D" not in rendered


@pytest.mark.parametrize("platform_app", PLATFORM_APP_DIRS, ids=("windows", "macos", "linux"))
@pytest.mark.parametrize("provider", ("openrouter", "openai"))
@pytest.mark.parametrize("method", ("reader_tts", "reader_tts_test"))
def test_reader_speech_entry_points_use_only_the_frozen_credential(
    platform_app, provider, method
):
    script = r'''
import importlib
import json
import sys

sys.path.insert(0, sys.argv[1])
shell = importlib.import_module("webui_shell")
provider = sys.argv[2]
method = sys.argv[3]

class Settings:
    def __init__(self):
        self.values = {
            "pro_mode": True,
            "local_only_mode": False,
            "reader_tts_provider": provider,
            "reader_tts_model": (
                "google/gemini-3.1-flash-tts-preview"
                if provider == "openrouter" else "gpt-4o-mini-tts"
            ),
            "reader_voice": "Fenrir" if provider == "openrouter" else "onyx",
            provider + "_api_key": "FROZEN_READER_KEY_14D",
        }
    def get(self, key, default=None):
        return self.values.get(key, default)
    def set(self, key, value):
        self.values[key] = value
        return True
    def update(self, values=None, **kwargs):
        self.values.update(values or {})
        self.values.update(kwargs)
        return True

settings = Settings()
api = shell.Api.__new__(shell.Api)
api.settings = settings
import settings as settings_module
settings_module.Settings = lambda: settings
seen = []
original = shell.ai.synthesize_with_fallback

def mutate_then_synthesize(*args, **kwargs):
    settings.values[provider + "_api_key"] = "MUTATED_LIVE_KEY_14D"
    return original(*args, **kwargs)

shell.ai.synthesize_with_fallback = mutate_then_synthesize
if provider == "openrouter":
    def transport(_text, api_key, **kwargs):
        seen.append({
            "key": api_key,
            "provider": kwargs["route_decision"].provider,
            "model": kwargs["model"],
            "voice": kwargs["voice"],
        })
        return b"audio", "audio/mpeg"
    shell.ai.openrouter_tts = transport
else:
    class Response:
        headers = {"Content-Type": "audio/mpeg"}
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return b"audio"
    def urlopen(req, timeout=None):
        seen.append({
            "key": req.get_header("Authorization").split(" ", 1)[1],
            "provider": provider,
            "model": json.loads(req.data.decode("utf-8"))["model"],
            "voice": json.loads(req.data.decode("utf-8"))["voice"],
        })
        return Response()
    shell.ai.urllib.request.urlopen = urlopen

result = getattr(api, method)("private reader text") if method == "reader_tts" else getattr(api, method)()
print(json.dumps({"result": result, "seen": seen}))
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(platform_app), provider, method],
        capture_output=True, text=True, timeout=45, cwd=platform_app,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence["result"]["ok"] is True
    assert len(evidence["seen"]) == 1
    assert evidence["seen"][0]["key"] == "FROZEN_READER_KEY_14D"
    assert evidence["seen"][0]["provider"] == provider
    assert evidence["seen"][0]["model"] == (
        "google/gemini-3.1-flash-tts-preview"
        if provider == "openrouter" else "gpt-4o-mini-tts"
    )
    assert evidence["seen"][0]["voice"] == (
        "Fenrir" if provider == "openrouter" else "onyx"
    )


@pytest.mark.parametrize("platform_app", PLATFORM_APP_DIRS, ids=("windows", "macos", "linux"))
@pytest.mark.parametrize("provider", ("openrouter", "openai"))
def test_modular_reader_adapters_require_and_use_only_the_frozen_credential(
    platform_app, provider
):
    script = r'''
import importlib
import json
import sys

sys.path.insert(0, sys.argv[1])
route = importlib.import_module("processing_route")
tts = importlib.import_module("ai.tts_providers")
provider = sys.argv[2]
model = (
    "google/gemini-3.1-flash-tts-preview"
    if provider == "openrouter" else "gpt-4o-mini-tts"
)
voice = "Fenrir" if provider == "openrouter" else "onyx"

class Settings:
    def __init__(self, local_only=False):
        self.values = {
            "pro_mode": True, "local_only_mode": local_only,
            "reader_tts_provider": provider, "reader_tts_model": model,
            provider + "_api_key": "FROZEN_MODULAR_READER_KEY_14D",
        }
    def get(self, key, default=None):
        return self.values.get(key, default)

source = Settings()
decision = route.snapshot(
    source, feature="reader", lane="reader_speech",
    provider_override=provider, model_override=model,
)
source.values[provider + "_api_key"] = "MUTATED_MODULAR_LIVE_KEY_14D"
seen = []
if provider == "openrouter":
    def transport(_text, api_key, **kwargs):
        seen.append(api_key)
        return b"audio", "audio/mpeg"
    tts.openrouter_tts = transport
    adapter = tts.OpenRouterTTSProvider()
else:
    class Response:
        headers = {"Content-Type": "audio/mpeg"}
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return b"audio"
    def urlopen(req, timeout=None):
        seen.append(req.get_header("Authorization").split(" ", 1)[1])
        return Response()
    tts.urllib.request.urlopen = urlopen
    adapter = tts.OpenAITTSProvider()

def invoke(marker):
    before = len(seen)
    error = None
    rendered = ""
    try:
        adapter.synthesize(
            "private reader text", voice, model=model, route_decision=marker
        )
    except Exception as exc:
        error = type(exc).__name__
        rendered = repr(exc) + str(exc)
    return {"calls": len(seen) - before, "error": error, "rendered": rendered}

missing = invoke(None)
blocked = route.snapshot(
    Settings(local_only=True), feature="reader", lane="reader_speech",
    provider_override=provider, model_override=model,
)
blocked_result = invoke(blocked)
allowed = invoke(decision)
print(json.dumps({
    "missing": missing, "blocked": blocked_result, "allowed": allowed,
    "seen": seen, "decision_repr": repr(decision),
}))
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(platform_app), provider],
        capture_output=True, text=True, timeout=30, cwd=platform_app,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence["missing"]["calls"] == 0
    assert evidence["missing"]["error"] == "TypeError"
    assert evidence["blocked"]["calls"] == 0
    assert evidence["blocked"]["error"] == "HostedRouteBlocked"
    assert evidence["allowed"] == {"calls": 1, "error": None, "rendered": ""}
    assert evidence["seen"] == ["FROZEN_MODULAR_READER_KEY_14D"]
    rendered = (
        evidence["missing"]["rendered"] + evidence["blocked"]["rendered"]
        + evidence["decision_repr"]
    )
    assert "FROZEN_MODULAR_READER_KEY_14D" not in rendered
    assert "MUTATED_MODULAR_LIVE_KEY_14D" not in rendered


@pytest.mark.parametrize("platform_app", PLATFORM_APP_DIRS, ids=("windows", "macos", "linux"))
def test_meeting_entry_point_freezes_route_preferences_and_gathered_transcript(platform_app):
    script = r'''
import importlib
import json
import sys

sys.path.insert(0, sys.argv[1])
meeting = importlib.import_module("meeting")

class Settings:
    def __init__(self):
        self.values = {
            "pro_mode": True, "local_only_mode": False, "instant_text": False,
            "llm_provider": "cerebras", "cerebras_api_key": "frozen-key",
            "cerebras_model": "frozen-model", "user_name": "Frozen User",
            "prompt_prefs": {"tone": "frozen", "nested": {"detail": "frozen"}},
            "primary_language": "fr", "vocabulary": {"heard": "Frozen Term"},
            "vocabulary_terms": ["Frozen Term"],
            "modes": {"email": {"signoff": "Frozen Signoff"}},
        }
    def get(self, key, default=None):
        return self.values.get(key, default)

settings = Settings()
context = meeting._analysis_context(settings, context="FROZEN MEETING TRANSCRIPT")
settings.values["cerebras_api_key"] = "mutated-key"
settings.values["prompt_prefs"]["nested"]["detail"] = "mutated"
settings.values["vocabulary_terms"].append("mutated")
settings.values["modes"]["email"]["signoff"] = "mutated"
_ai, _info, invocation = context
print(json.dumps({
    "key": invocation.route.api_key,
    "model": invocation.route.model,
    "prefs": invocation.prompt_prefs_dict(),
    "terms": list(invocation.vocabulary_terms),
    "modes": invocation.modes_dict(),
    "context": invocation.context,
}))
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(platform_app)],
        capture_output=True, text=True, timeout=30, cwd=platform_app,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence == {
        "key": "frozen-key",
        "model": "frozen-model",
        "prefs": {"nested": {"detail": "frozen"}, "tone": "frozen"},
        "terms": ["Frozen Term"],
        "modes": {"email": {"signoff": "Frozen Signoff"}},
        "context": "FROZEN MEETING TRANSCRIPT",
    }


def test_processing_route_copies_are_normalized_generated_content():
    canonical = (APP_DIR / "processing_route.py").read_text(encoding="utf-8")
    for platform_app in PLATFORM_APP_DIRS[1:]:
        copied = (platform_app / "processing_route.py").read_text(encoding="utf-8")
        assert copied.replace("\r\n", "\n") == canonical.replace("\r\n", "\n")

    sync_tool = (
        APP_DIR.parent.parent / "Development Files" / "Tooling" /
        "sync_processing_route.py"
    )
    result = subprocess.run(
        [sys.executable, str(sync_tool), "--check"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=APP_DIR.parent.parent,
    )
    assert result.returncode == 0, result.stderr or result.stdout
