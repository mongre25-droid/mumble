"""Regression tests for the independent Issue #14 correction findings.

All provider seams are replaced in-process.  Nothing in this module is allowed
to contact a network service.
"""

from pathlib import Path
import hashlib
import importlib.util
import json
import logging
import subprocess
import sys
import zipfile

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
        provider = self.values["llm_provider"]
        if (
            "_confirmed_text_models" not in overrides
            and provider in ("cerebras", "openrouter")
        ):
            key = self.values.get(f"{provider}_api_key", "")
            model = self.values.get(f"{provider}_model", "")
            self.values["_confirmed_text_models"] = {
                provider: {
                    "credential_identity": processing_route.model_credential_identity(
                        provider, key
                    ),
                    "generation": 1,
                    "confirmed_generation": 1,
                    "state": "confirmed",
                    "models": [model],
                }
            }

    def get(self, key, default=None):
        return self.values.get(key, default)

    def authority_read(self):
        return dict(self.values)


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


@pytest.mark.parametrize("platform_app", PLATFORM_APP_DIRS, ids=("windows", "macos", "linux"))
def test_modular_text_adapter_rejects_replaced_key_and_model_before_transport(
    platform_app,
):
    script = r'''
import importlib
import json
import sys

sys.path.insert(0, sys.argv[1])
ai = importlib.import_module("ai")
route = importlib.import_module("processing_route")

class Settings:
    def __init__(self):
        key = "FROZEN_MODULAR_TEXT_KEY_14F"
        model = "FROZEN_MODULAR_TEXT_MODEL_14F"
        self.values = {
            "pro_mode": True,
            "local_only_mode": False,
            "instant_text": False,
            "llm_provider": "cerebras",
            "cerebras_api_key": key,
            "cerebras_model": model,
            "_confirmed_text_models": {
                "cerebras": {
                    "credential_identity": route.model_credential_identity(
                        "cerebras", key),
                    "generation": 1,
                    "confirmed_generation": 1,
                    "state": "confirmed",
                    "models": [model],
                }
            },
        }
    def get(self, key, default=None):
        return self.values.get(key, default)
    def authority_read(self):
        return dict(self.values)

decision = route.snapshot(Settings(), feature="prompt", lane="prompt")
calls = []
class Response:
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self):
        return b'{"choices":[{"message":{"content":"ok"}}]}'
def urlopen(req, timeout=None):
    calls.append(req.full_url)
    return Response()
ai.urllib.request.urlopen = urlopen

def invoke(adapter, **kwargs):
    before = len(calls)
    error = None
    rendered = ""
    try:
        adapter.chat(
            [{"role": "user", "content": "private prompt"}],
            route_decision=decision,
            expected_feature="prompt",
            expected_lane="prompt",
            **kwargs,
        )
    except Exception as exc:
        error = type(exc).__name__
        rendered = repr(exc) + str(exc)
    return {"calls": len(calls) - before, "error": error, "rendered": rendered}

valid = invoke(ai.CerebrasProvider(
    "FROZEN_MODULAR_TEXT_KEY_14F", model="FROZEN_MODULAR_TEXT_MODEL_14F"))
mutated_key = invoke(ai.CerebrasProvider(
    "MUTATED_MODULAR_TEXT_KEY_14F", model="FROZEN_MODULAR_TEXT_MODEL_14F"))
mutated_model = invoke(ai.CerebrasProvider(
    "FROZEN_MODULAR_TEXT_KEY_14F", model="FROZEN_MODULAR_TEXT_MODEL_14F"),
    model="MUTATED_MODULAR_TEXT_MODEL_14F")
print(json.dumps({
    "valid": valid,
    "mutated_key": mutated_key,
    "mutated_model": mutated_model,
}))
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(platform_app)],
        capture_output=True, text=True, timeout=30, cwd=platform_app,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence["valid"] == {"calls": 1, "error": None, "rendered": ""}
    for key in ("mutated_key", "mutated_model"):
        assert evidence[key]["calls"] == 0
        assert evidence[key]["error"] == "HostedRouteBlocked"
        assert "FROZEN_MODULAR_TEXT_KEY_14F" not in evidence[key]["rendered"]
        assert "MUTATED_MODULAR_TEXT_KEY_14F" not in evidence[key]["rendered"]


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
route = importlib.import_module("processing_route")

class Settings:
    def __init__(self, local_only=False):
        key = "frozen-key"
        model = "frozen-model"
        self.values = {
            "pro_mode": True, "local_only_mode": local_only, "instant_text": False,
            "llm_provider": "cerebras", "cerebras_api_key": key,
            "cerebras_model": model, "user_name": "Frozen User",
            "prompt_prefs": {"tone": "frozen-tone", "nested": {"detail": "frozen-detail"}},
            "primary_language": "fr", "english_only": False,
            "foreign_languages": ["arabic"], "vocabulary": {"heard": "Frozen Term"},
            "vocabulary_terms": ["Frozen Term"], "modes": {"email": {"signoff": "Frozen Signoff"}},
            "format_enabled": True, "polish_aggressiveness": "Thorough",
            "_confirmed_text_models": {
                "cerebras": {
                    "credential_identity": route.model_credential_identity(
                        "cerebras", key),
                    "generation": 1, "confirmed_generation": 1,
                    "state": "confirmed", "models": [model],
                }
            },
        }
    def get(self, key, default=None):
        return self.values.get(key, default)
    def authority_read(self):
        return dict(self.values)

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


@pytest.mark.parametrize(
    ("platform_app", "module_name"),
    ((PLATFORM_APP_DIRS[1], "mumble_mac"), (PLATFORM_APP_DIRS[2], "mumble_linux")),
    ids=("macos", "linux"),
)
def test_port_processing_applies_only_the_vocabulary_captured_for_the_invocation(
    platform_app, module_name
):
    script = r'''
import importlib
import json
import sys
import threading

sys.path.insert(0, sys.argv[1])
controller = importlib.import_module(sys.argv[2])

class StopProbe(BaseException):
    pass

class Settings:
    def __init__(self):
        self.values = {
            "pro_mode": True,
            "local_only_mode": False,
            "instant_text": False,
            "llm_provider": "cerebras",
            "cerebras_api_key": "FROZEN_KEY_14E",
            "cerebras_model": "FROZEN_MODEL_14E",
            "english_only": True,
            "foreign_mode": False,
            "foreign_languages": [],
            "format_enabled": True,
            "user_name": "",
            "prompt_prefs": {},
            "primary_language": "en",
            "vocabulary": {"mum bull": "Mumble"},
            "vocabulary_terms": ["Sarah"],
            "polish_aggressiveness": "Light",
            "rpunct_enabled": False,
            "modes": {},
            "local_llm_enabled": False,
            "local_llm_model": "",
        }
    def get(self, key, default=None):
        return self.values.get(key, default)

settings = Settings()
app = controller.Mumble.__new__(controller.Mumble)
app.settings = settings
app.lock = threading.RLock()
app._stream_results = []
app._stream_processed_samples = 0
app._search_requested = False
app._active_mode_start = "prompt"
app.island = None
app._set_state = lambda *_args, **_kwargs: None
app._notify = lambda *_args, **_kwargs: None
app._idle = lambda: None

def transcribe(*_args, **_kwargs):
    settings.values["vocabulary"] = {"mum bull": "MUTATED LIVE"}
    settings.values["vocabulary_terms"] = ["Mutated Live Term"]
    return "mum bull sara"

captured = {}
def generate(raw, *_args, **_kwargs):
    captured["raw"] = raw
    raise StopProbe()

app._transcribe = transcribe
app._generate = generate
try:
    app._process([0.0] * 16000, 1.0, mode_active=True)
except StopProbe:
    pass

print(json.dumps({
    "captured": captured,
    "live_vocabulary": settings.values["vocabulary"],
    "live_terms": settings.values["vocabulary_terms"],
}))
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(platform_app), module_name],
        capture_output=True, text=True, timeout=45, cwd=platform_app,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence == {
        "captured": {"raw": "Mumble Sarah"},
        "live_vocabulary": {"mum bull": "MUTATED LIVE"},
        "live_terms": ["Mutated Live Term"],
    }


@pytest.mark.parametrize("provider", ("anthropic", "deepseek"))
def test_linux_processing_route_keeps_internal_adapters_unselectable(provider):
    platform_app = PLATFORM_APP_DIRS[2]
    script = r'''
import importlib
import json
import sys
import threading

sys.path.insert(0, sys.argv[1])
controller = importlib.import_module("mumble_linux")
provider = sys.argv[2]

class StopProbe(BaseException):
    pass

class Settings:
    def __init__(self, key):
        model = "FROZEN_" + provider.upper() + "_MODEL_14E"
        self.values = {
            "pro_mode": True,
            "local_only_mode": False,
            "instant_text": False,
            "llm_provider": provider,
            provider + "_api_key": key,
            provider + "_model": model,
            "english_only": True,
            "foreign_mode": False,
            "foreign_languages": [],
            "format_enabled": True,
            "user_name": "",
            "prompt_prefs": {},
            "primary_language": "en",
            "vocabulary": {},
            "vocabulary_terms": [],
            "polish_aggressiveness": "Light",
            "rpunct_enabled": False,
            "modes": {},
            "local_llm_enabled": False,
            "local_llm_model": "",
        }
    def get(self, key, default=None):
        return self.values.get(key, default)
    def authority_read(self):
        return dict(self.values)

def run(key):
    settings = Settings(key)
    app = controller.Mumble.__new__(controller.Mumble)
    app.settings = settings
    app.lock = threading.RLock()
    app._stream_results = []
    app._stream_processed_samples = 0
    app._search_requested = False
    app._active_mode_start = "prompt"
    app.island = None
    app._set_state = lambda *_args, **_kwargs: None
    app._notify = lambda *_args, **_kwargs: None
    app._idle = lambda: None

    def transcribe(*_args, **_kwargs):
        settings.values[provider + "_api_key"] = "MUTATED_LIVE_KEY_14E"
        settings.values[provider + "_model"] = "MUTATED_LIVE_MODEL_14E"
        return "private prompt"

    captured = {}
    def generate(_raw, *_args, **kwargs):
        decision = kwargs["route_decision"]
        captured.update({
            "provider": decision.provider,
            "key": decision.api_key,
            "model": decision.model,
            "ready": decision.ready,
            "reason": decision.reason,
        })
        raise StopProbe()

    app._transcribe = transcribe
    app._generate = generate
    try:
        app._process([0.0] * 16000, 1.0, mode_active=True)
    except StopProbe:
        pass
    return captured

print(json.dumps({"ready": run("FROZEN_" + provider.upper() + "_KEY_14E"), "missing": run("")}))
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(platform_app), provider],
        capture_output=True, text=True, timeout=45, cwd=platform_app,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence["ready"] == {
        "provider": provider,
        "key": f"FROZEN_{provider.upper()}_KEY_14E",
        "model": f"FROZEN_{provider.upper()}_MODEL_14E",
        "ready": False,
        "reason": "unconfirmed_model",
    }
    assert evidence["missing"] == {
        "provider": provider,
        "key": "",
        "model": f"FROZEN_{provider.upper()}_MODEL_14E",
        "ready": False,
        "reason": "missing_key",
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


def test_each_text_operation_authorizes_only_itself():
    operations = (
        ("dictation", "text"),
        ("dictation", "foreign"),
        ("prompt", "prompt"),
        ("email", "email"),
        ("reply", "reply"),
        ("deck", "deck_reason"),
        ("meetings", "meeting_analysis"),
        ("reader", "reader_summary"),
    )
    decisions = {
        operation: processing_route.snapshot(
            MemorySettings(), feature=operation[0], lane=operation[1]
        )
        for operation in operations
    }

    for authority, decision in decisions.items():
        for expected in operations:
            if authority == expected:
                assert processing_route.require_text_shaping(
                    decision,
                    expected_feature=expected[0],
                    expected_lane=expected[1],
                    expected_provider=decision.provider,
                    api_key=decision.api_key,
                    model=decision.model,
                ) is decision
            else:
                with pytest.raises(processing_route.HostedRouteBlocked):
                    processing_route.require_text_shaping(
                        decision,
                        expected_feature=expected[0],
                        expected_lane=expected[1],
                        expected_provider=decision.provider,
                        api_key=decision.api_key,
                        model=decision.model,
                    )


def test_windows_processing_provider_truth_matrix_matches_every_registry_provider(
    monkeypatch,
):
    import ai
    import mumble

    supported = ("cerebras", "openrouter")
    html = (APP_DIR / "webui" / "index.html").read_text(encoding="utf-8")
    provider_options = html.split('id="set-provider"', 1)[1].split(
        "</select>", 1
    )[0]
    selectable = {
        provider
        for provider in ai.PROVIDERS
        if f'<option value="{provider}"' in provider_options
    }
    assert selectable == set(supported)
    controller_source = (APP_DIR / "mumble.py").read_text(encoding="utf-8")
    assert "capture_text_provider_settings(\n                self.settings, supported" in controller_source

    calls = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    monkeypatch.setattr(
        ai.urllib.request,
        "urlopen",
        lambda req, timeout=None: calls.append(req.full_url) or Response(),
    )

    matrix = {}
    for provider, info in ai.PROVIDERS.items():
        frozen_key = f"FROZEN_{provider.upper()}_PROCESSING_KEY_14F"
        frozen_model = f"FROZEN_{provider.upper()}_PROCESSING_MODEL_14F"
        saved = MemorySettings(
            llm_provider=provider,
            **{
                info["key_setting"]: frozen_key,
                info["model_setting"]: frozen_model,
            },
        )
        captured = processing_route.capture_text_provider_settings(
            saved, supported_providers=supported
        )
        captured.update(saved.values)
        invocation = processing_route.snapshot(
            MemorySettings(**captured), feature="prompt", lane="prompt"
        )
        settings_fact = processing_route.settings_state(
            saved, supported_providers=supported
        )["action_processing"]

        app = mumble.Mumble.__new__(mumble.Mumble)
        app.settings = saved
        controller = app._ai_cfg()
        before = len(calls)
        error = None
        try:
            ai.cerebras_chat(
                "system", "private prompt", invocation.api_key,
                model=invocation.model, url=info["url"],
                route_decision=invocation,
                expected_feature="prompt", expected_lane="prompt",
            )
        except Exception as exc:
            error = type(exc).__name__

        matrix[provider] = {
            "saved": provider,
            "selectable": provider in selectable,
            "supported": invocation.provider_supported,
            "ready": invocation.ready,
            "reason": invocation.reason,
            "key": invocation.api_key,
            "model": invocation.model,
            "settings_supported": settings_fact["provider_supported"],
            "settings_ready": settings_fact["ready"],
            "controller_supported": controller["supported"],
            "controller_key": controller["key"],
            "controller_model": controller["model"],
            "calls": len(calls) - before,
            "error": error,
        }

        missing = MemorySettings(
            llm_provider=provider,
            **{info["key_setting"]: "", info["model_setting"]: frozen_model},
        )
        missing_fact = processing_route.settings_state(
            missing, supported_providers=supported
        )["action_processing"]
        if provider in supported:
            assert missing_fact["reason"] == "missing_key"
            assert missing_fact["ready"] is False
        else:
            assert missing_fact["reason"] == "unsupported_provider"
            assert missing_fact["ready"] is False

    for provider, facts in matrix.items():
        if provider in supported:
            assert facts == {
                "saved": provider, "selectable": True,
                "supported": True, "ready": True, "reason": "ready",
                "key": f"FROZEN_{provider.upper()}_PROCESSING_KEY_14F",
                "model": f"FROZEN_{provider.upper()}_PROCESSING_MODEL_14F",
                "settings_supported": True, "settings_ready": True,
                "controller_supported": True,
                "controller_key": f"FROZEN_{provider.upper()}_PROCESSING_KEY_14F",
                "controller_model": f"FROZEN_{provider.upper()}_PROCESSING_MODEL_14F",
                "calls": 1, "error": None,
            }
        else:
            assert facts == {
                "saved": provider, "selectable": False,
                "supported": False, "ready": False,
                "reason": "unsupported_provider", "key": "", "model": "",
                "settings_supported": False, "settings_ready": False,
                "controller_supported": False, "controller_key": "",
                "controller_model": "", "calls": 0,
                "error": "HostedRouteBlocked",
            }


@pytest.mark.parametrize("platform_app", PLATFORM_APP_DIRS, ids=("windows", "macos", "linux"))
def test_exact_operation_and_frozen_transport_authority_is_enforced_at_final_adapters(
    platform_app,
):
    script = r'''
from dataclasses import replace
import importlib
import json
import sys

import numpy as np

sys.path.insert(0, sys.argv[1])
route = importlib.import_module("processing_route")
ai = importlib.import_module("ai")
tts = importlib.import_module("ai.tts_providers")
tx = importlib.import_module("transcription")

class Settings:
    def __init__(self):
        text_key = "FROZEN_TEXT_KEY_14F"
        text_model = "FROZEN_TEXT_MODEL_14F"
        self.values = {
            "pro_mode": True,
            "local_only_mode": False,
            "instant_text": False,
            "llm_provider": "cerebras",
            "cerebras_api_key": text_key,
            "cerebras_model": text_model,
            "openrouter_api_key": "FROZEN_SHARED_KEY_14E",
            "openrouter_model": "openai/gpt-oss-120b",
            "reader_tts_provider": "openrouter",
            "reader_tts_model": "google/gemini-3.1-flash-tts-preview",
            "transcription_mode": "cloud",
            "cloud_transcription_provider": "groq",
            "groq_api_key": "FROZEN_STT_KEY_14E",
            "groq_transcription_model": "FROZEN_STT_MODEL_14E",
            "language": "en",
            "vocabulary_terms": ["Frozen Term 14E"],
            "_confirmed_text_models": {
                "cerebras": {
                    "credential_identity": route.model_credential_identity(
                        "cerebras", text_key),
                    "generation": 1,
                    "confirmed_generation": 1,
                    "state": "confirmed",
                    "models": [text_model],
                }
            },
        }
    def get(self, key, default=None):
        return self.values.get(key, default)
    def authority_read(self):
        return dict(self.values)

settings = Settings()
text_transport = []
speech_transport = []
stt_transport = []

class Response:
    headers = {"Content-Type": "audio/mpeg"}
    def __init__(self, url):
        self.url = url
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def __iter__(self):
        yield b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n'
        yield b'data: [DONE]\n'
    def read(self):
        if "/audio/speech" in self.url:
            return b"audio"
        return b'{"choices":[{"message":{"content":"ok"}}]}'

def urlopen(req, timeout=None):
    url = getattr(req, "full_url", "")
    payload = json.loads((getattr(req, "data", None) or b"{}").decode("utf-8"))
    auth = req.get_header("Authorization") or ""
    call = {
        "key": auth.split(" ", 1)[1] if " " in auth else auth,
        "model": payload.get("model"),
        "url": url,
    }
    if "/audio/speech" in url:
        speech_transport.append(call)
    else:
        text_transport.append(call)
    return Response(url)

def stt_recorder(_info, key, model, _wav, _language, _timeout, prompt=None):
    stt_transport.append({"key": key, "model": model, "prompt": prompt})
    return "transcript"

ai.urllib.request.urlopen = urlopen
tts.urllib.request.urlopen = urlopen
tx._transcribe_multipart = stt_recorder
speech_adapter = tts.OpenRouterTTSProvider()

prompt_authority = route.snapshot(settings, feature="prompt", lane="prompt")
email_authority = route.snapshot(settings, feature="email", lane="email")
reply_authority = route.snapshot(settings, feature="reply", lane="reply")
deck_authority = route.snapshot(settings, feature="deck", lane="deck_reason")
speech_authority = route.snapshot(
    settings,
    feature="reader",
    lane="reader_speech",
    provider_override="openrouter",
    model_override="google/gemini-3.1-flash-tts-preview",
)
speech_test_authority = route.snapshot(
    settings,
    feature="reader",
    lane="reader_speech_test",
    provider_override="openrouter",
    model_override="google/gemini-3.1-flash-tts-preview",
)
stt_authority = route.snapshot_inputs(
    settings, feature="dictation", lane="speech_to_text"
)
wrong_endpoint_authority = replace(
    stt_authority,
    route=replace(stt_authority.route, endpoint_class="openai_speech_to_text"),
)

def invoke(call, transport):
    before = len(transport)
    error = None
    rendered = ""
    try:
        call()
    except Exception as exc:
        error = type(exc).__name__
        rendered = repr(exc) + str(exc)
    return {"calls": len(transport) - before, "error": error, "rendered": rendered}

def collect(generator):
    return list(generator)

evidence = {
    "prompt_at_email": invoke(
        lambda: collect(ai.cerebras_email(
            "private email", "", prompt_authority.api_key,
            model=prompt_authority.model, url=ai.CEREBRAS_URL,
            route_decision=prompt_authority,
        )),
        text_transport,
    ),
    "prompt_at_reply": invoke(
        lambda: collect(ai.cerebras_reply(
            "private reply", "", prompt_authority.api_key,
            model=prompt_authority.model, url=ai.CEREBRAS_URL,
            route_decision=prompt_authority,
        )),
        text_transport,
    ),
    "prompt_at_deck": invoke(
        lambda: collect(ai.cerebras_intent(
            "summarize", "private deck material", "",
            prompt_authority.api_key, prompt_authority.model,
            url=ai.CEREBRAS_URL, route_decision=prompt_authority,
            expected_feature="prompt", expected_lane="prompt",
        )),
        text_transport,
    ),
    "scoped_email": invoke(
        lambda: collect(ai.cerebras_email(
            "private email", "", email_authority.api_key,
            model=email_authority.model, url=ai.CEREBRAS_URL,
            route_decision=email_authority,
        )),
        text_transport,
    ),
    "scoped_reply": invoke(
        lambda: collect(ai.cerebras_reply(
            "private reply", "", reply_authority.api_key,
            model=reply_authority.model, url=ai.CEREBRAS_URL,
            route_decision=reply_authority,
        )),
        text_transport,
    ),
    "scoped_deck": invoke(
        lambda: collect(ai.cerebras_intent(
            "summarize", "private deck material", "",
            deck_authority.api_key, deck_authority.model,
            url=ai.CEREBRAS_URL, route_decision=deck_authority,
        )),
        text_transport,
    ),
    "mutated_text_inputs": invoke(
        lambda: ai.cerebras_chat(
            "system", "private prompt", "MUTATED_TEXT_KEY_14F",
            model="MUTATED_TEXT_MODEL_14F", url=ai.CEREBRAS_URL,
            route_decision=prompt_authority,
            expected_feature="prompt", expected_lane="prompt",
        ),
        text_transport,
    ),
    "speech_test_at_ordinary_speech": invoke(
        lambda: speech_adapter.synthesize(
            "private reader text",
            "Fenrir",
            model="google/gemini-3.1-flash-tts-preview",
            route_decision=speech_test_authority,
        ),
        speech_transport,
    ),
    "ordinary_speech_at_speech_test": invoke(
        lambda: speech_adapter.synthesize(
            "private reader test text",
            "Fenrir",
            model="google/gemini-3.1-flash-tts-preview",
            route_decision=speech_authority,
            operation_lane="reader_speech_test",
        ),
        speech_transport,
    ),
    "mutated_speech_inputs": invoke(
        lambda: tts.openrouter_tts(
            "private reader text",
            "MUTATED_READER_KEY_14F",
            model="mistralai/voxtral-mini-tts-2603",
            voice="gb_oliver_neutral",
            route_decision=speech_authority,
        ),
        speech_transport,
    ),
    "prompt_at_speech": invoke(
        lambda: speech_adapter.synthesize(
            "private reader text",
            "Fenrir",
            model="google/gemini-3.1-flash-tts-preview",
            route_decision=prompt_authority,
        ),
        speech_transport,
    ),
    "wrong_stt_endpoint": invoke(
        lambda: tx.transcribe(np.zeros(8, dtype=np.float32), wrong_endpoint_authority),
        stt_transport,
    ),
    "scoped_speech": invoke(
        lambda: speech_adapter.synthesize(
            "private reader text",
            "Fenrir",
            model="google/gemini-3.1-flash-tts-preview",
            route_decision=speech_authority,
        ),
        speech_transport,
    ),
    "scoped_speech_test": invoke(
        lambda: speech_adapter.synthesize(
            "non-private test phrase",
            "Fenrir",
            model="google/gemini-3.1-flash-tts-preview",
            route_decision=speech_test_authority,
            operation_lane="reader_speech_test",
        ),
        speech_transport,
    ),
    "scoped_stt": invoke(
        lambda: tx.transcribe(np.zeros(8, dtype=np.float32), stt_authority),
        stt_transport,
    ),
}
print(json.dumps({
    "evidence": evidence,
    "text_transport": text_transport,
    "speech_transport": speech_transport,
    "stt_transport": stt_transport,
}))
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(platform_app)],
        capture_output=True, text=True, timeout=30, cwd=platform_app,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    receipt = json.loads(result.stdout.strip().splitlines()[-1])
    for key in (
        "prompt_at_email", "prompt_at_reply", "prompt_at_deck",
        "mutated_text_inputs", "speech_test_at_ordinary_speech",
        "ordinary_speech_at_speech_test",
        "mutated_speech_inputs", "prompt_at_speech", "wrong_stt_endpoint",
    ):
        assert receipt["evidence"][key]["calls"] == 0, (key, receipt)
        assert receipt["evidence"][key]["error"] == "HostedRouteBlocked", (
            key, receipt
        )
    for key in ("scoped_email", "scoped_reply", "scoped_deck"):
        assert receipt["evidence"][key] == {
            "calls": 1, "error": None, "rendered": "",
        }
    assert receipt["evidence"]["scoped_speech"] == {
        "calls": 1, "error": None, "rendered": "",
    }
    assert receipt["evidence"]["scoped_speech_test"] == {
        "calls": 1, "error": None, "rendered": "",
    }
    assert receipt["evidence"]["scoped_stt"] == {
        "calls": 1, "error": None, "rendered": "",
    }
    assert len(receipt["text_transport"]) == 3
    assert len(receipt["speech_transport"]) == 2
    assert receipt["speech_transport"][0]["key"] == "FROZEN_SHARED_KEY_14E"
    assert receipt["speech_transport"][0]["model"] == "google/gemini-3.1-flash-tts-preview"
    assert receipt["speech_transport"][1] == receipt["speech_transport"][0]
    assert receipt["stt_transport"] == [{
        "key": "FROZEN_STT_KEY_14E",
        "model": "FROZEN_STT_MODEL_14E",
        "prompt": "Frozen Term 14E",
    }]
    rendered = json.dumps(receipt["evidence"])
    for private_value in (
        "FROZEN_TEXT_KEY_14F", "MUTATED_TEXT_KEY_14F",
        "FROZEN_TEXT_MODEL_14F", "MUTATED_TEXT_MODEL_14F",
        "FROZEN_SHARED_KEY_14E", "MUTATED_READER_KEY_14F",
        "mistralai/voxtral-mini-tts-2603",
        "FROZEN_STT_KEY_14E",
    ):
        assert private_value not in rendered


@pytest.mark.parametrize("platform_app", PLATFORM_APP_DIRS, ids=("windows", "macos", "linux"))
def test_real_meetings_wrapper_declares_its_operation_at_final_boundary(platform_app):
    script = r'''
from dataclasses import replace
import importlib
import json
import sys

sys.path.insert(0, sys.argv[1])
ai = importlib.import_module("ai")
meeting = importlib.import_module("meeting")
route = importlib.import_module("processing_route")

class Settings:
    def __init__(self, **overrides):
        key = "FROZEN_MEETING_KEY_14G"
        model = "FROZEN_MEETING_MODEL_14G"
        self.values = {
            "pro_mode": True,
            "local_only_mode": False,
            "instant_text": False,
            "llm_provider": "cerebras",
            "cerebras_api_key": key,
            "cerebras_model": model,
            "_confirmed_text_models": {
                "cerebras": {
                    "credential_identity": route.model_credential_identity(
                        "cerebras", key),
                    "generation": 1,
                    "confirmed_generation": 1,
                    "state": "confirmed",
                    "models": [model],
                }
            },
        }
        self.values.update(overrides)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def authority_read(self):
        return dict(self.values)

transport = []

class Response:
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self):
        return b'{"choices":[{"message":{"content":"transport-entered"}}]}'

def urlopen(req, timeout=None):
    payload = json.loads((getattr(req, "data", None) or b"{}").decode("utf-8"))
    auth = req.get_header("Authorization") or ""
    messages = payload.get("messages") or [{}, {}]
    transport.append({
        "url": getattr(req, "full_url", ""),
        "key": auth.split(" ", 1)[1] if " " in auth else auth,
        "model": payload.get("model"),
        "system": messages[0].get("content"),
        "user": messages[1].get("content"),
        "max_tokens": payload.get(
            "max_tokens", payload.get("max_completion_tokens")
        ),
        "timeout": timeout,
    })
    return Response()

ai.urllib.request.urlopen = urlopen
settings = Settings()

def snapshot(feature, lane, source=settings):
    return route.snapshot_inputs(
        source,
        feature=feature,
        lane=lane,
        context="FROZEN MEETING TRANSCRIPT 14G",
        context_policy="meeting_transcript",
    )

exact = snapshot("meetings", "meeting_analysis")
wrong_operations = (
    ("dictation", "text"),
    ("dictation", "foreign"),
    ("prompt", "prompt"),
    ("email", "email"),
    ("reply", "reply"),
    ("deck", "deck_reason"),
    ("reader", "reader_summary"),
    ("meetings", "meeting_follow_up"),
)
wrong = {
    f"{feature}/{lane}": snapshot(feature, lane)
    for feature, lane in wrong_operations
}
device_only = snapshot(
    "meetings", "meeting_analysis", Settings(local_only_mode=True)
)
hosted_off = snapshot(
    "meetings", "meeting_analysis", Settings(pro_mode=False)
)
missing_key = snapshot(
    "meetings", "meeting_analysis", Settings(cerebras_api_key="")
)
unavailable = snapshot(
    "meetings", "meeting_analysis", Settings(llm_provider="unsupported")
)
wrong_endpoint = replace(
    exact,
    route=replace(exact.route, endpoint_class="openai_compatible"),
)

# A later Settings mutation must not replace this invocation's frozen inputs.
settings.values["llm_provider"] = "openrouter"
settings.values["cerebras_api_key"] = "MUTATED_MEETING_KEY_14G"
settings.values["cerebras_model"] = "MUTATED_MEETING_MODEL_14G"

def invoke(invocation, info=None):
    before = len(transport)
    error = None
    rendered = ""
    result = None
    try:
        if info is None:
            info = ai.PROVIDERS.get(invocation.route.provider) or {}
        result = meeting._analysis_call(
            (ai, info, invocation),
            "FROZEN MEETING SYSTEM 14G",
            "FROZEN MEETING USER TEXT 14G",
            321,
            37,
        )
    except Exception as exc:
        error = type(exc).__name__
        rendered = repr(exc) + str(exc)
    return {
        "calls": len(transport) - before,
        "error": error,
        "rendered": rendered,
        "result": result,
    }

evidence = {
    "wrong_operations": {
        operation: invoke(invocation)
        for operation, invocation in wrong.items()
    },
    "wrong_provider": invoke(exact, ai.PROVIDERS["openrouter"]),
    "wrong_endpoint": invoke(wrong_endpoint),
    "device_only": invoke(device_only),
    "hosted_off": invoke(hosted_off),
    "missing_key": invoke(missing_key),
    "unavailable": invoke(unavailable),
    "exact": invoke(exact),
}
print(json.dumps({"evidence": evidence, "transport": transport}))
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(platform_app)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=platform_app,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    receipt = json.loads(result.stdout.strip().splitlines()[-1])

    blocked = list(receipt["evidence"]["wrong_operations"].values()) + [
        receipt["evidence"][key]
        for key in (
            "wrong_provider", "wrong_endpoint", "device_only",
            "hosted_off", "missing_key", "unavailable",
        )
    ]
    for item in blocked:
        assert item["calls"] == 0, receipt
        assert item["error"] == "HostedRouteBlocked", receipt

    assert receipt["evidence"]["exact"] == {
        "calls": 1,
        "error": None,
        "rendered": "",
        "result": "transport-entered",
    }
    assert receipt["transport"] == [{
        "url": "https://api.cerebras.ai/v1/chat/completions",
        "key": "FROZEN_MEETING_KEY_14G",
        "model": "FROZEN_MEETING_MODEL_14G",
        "system": "FROZEN MEETING SYSTEM 14G",
        "user": "FROZEN MEETING USER TEXT 14G",
        "max_tokens": 321,
        "timeout": 37,
    }]

    rendered = json.dumps(receipt["evidence"])
    for private_value in (
        "FROZEN_MEETING_KEY_14G",
        "MUTATED_MEETING_KEY_14G",
        "FROZEN_MEETING_MODEL_14G",
        "MUTATED_MEETING_MODEL_14G",
        "FROZEN MEETING TRANSCRIPT 14G",
        "FROZEN MEETING USER TEXT 14G",
    ):
        assert private_value not in rendered


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


def test_windows_package_canonicalizes_text_line_endings_and_preserves_binary(tmp_path):
    build_tool = (
        APP_DIR.parent.parent / "Development Files" / "Tooling" /
        "_rebuild_zip.py"
    )
    spec = importlib.util.spec_from_file_location("mumble_rebuild_zip", build_tool)
    builder = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(builder)

    lf_source = tmp_path / "lf" / "sample.py"
    crlf_source = tmp_path / "crlf" / "sample.py"
    lf_source.parent.mkdir()
    crlf_source.parent.mkdir()
    lf_source.write_bytes(b"alpha\nbeta\n")
    crlf_source.write_bytes(b"alpha\r\nbeta\r\n")

    archives = []
    entries = []
    for index, source in enumerate((lf_source, crlf_source)):
        archive_path = tmp_path / f"variant-{index}.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            builder.write_file(archive, source, "Mumble/Internal/app/sample.py")
        archives.append(hashlib.sha256(archive_path.read_bytes()).hexdigest())
        with zipfile.ZipFile(archive_path) as archive:
            entries.append(archive.read("Mumble/Internal/app/sample.py"))

    assert entries == [b"alpha\nbeta\n", b"alpha\nbeta\n"]
    assert archives[0] == archives[1]

    binary_source = tmp_path / "image.png"
    binary_bytes = b"\x89PNG\r\n\x1a\n\x00binary\r\nbytes"
    binary_source.write_bytes(binary_bytes)
    binary_archive = tmp_path / "binary.zip"
    with zipfile.ZipFile(binary_archive, "w") as archive:
        builder.write_file(
            archive, binary_source, "Mumble/Internal/app/assets/image.png"
        )
    with zipfile.ZipFile(binary_archive) as archive:
        assert archive.read("Mumble/Internal/app/assets/image.png") == binary_bytes
