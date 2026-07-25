"""Offline contract tests for Reader TTS models, voices, audio and fallback."""

import copy
import json
import urllib.request

import pytest

import ai
import ai.tts_providers as modular_tts
import processing_route
import settings


IMPLEMENTATIONS = (ai, modular_tts)
GEMINI = "google/gemini-3.1-flash-tts-preview"
VOXTRAL = "mistralai/voxtral-mini-tts-2603"
MAI = "microsoft/mai-voice-2"
EXPECTED_DEFAULTS = {
    GEMINI: "Fenrir",
    VOXTRAL: "gb_oliver_neutral",
    MAI: "en-US-Harper:MAI-Voice-2",
}


def _openrouter_route():
    return processing_route.snapshot(
        {
            "pro_mode": True,
            "local_only_mode": False,
            "instant_text": False,
            "llm_provider": "openrouter",
            "openrouter_api_key": "sk-or-test",
        },
        feature="reader", lane="reader_speech",
        provider_override="openrouter",
    )


class _Response:
    def __init__(self, body, content_type):
        self._body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


@pytest.mark.parametrize("impl", IMPLEMENTATIONS)
def test_curated_models_have_supported_model_specific_defaults(impl):
    assert {row[0] for row in impl.OPENROUTER_TTS_MODELS} == set(EXPECTED_DEFAULTS)
    assert impl.OPENROUTER_TTS_DEFAULT_VOICES == EXPECTED_DEFAULTS
    for model, voice in EXPECTED_DEFAULTS.items():
        assert voice in impl.OPENROUTER_TTS_VOICES[model]


@pytest.mark.parametrize("impl", IMPLEMENTATIONS)
@pytest.mark.parametrize(
    "model,expected_format,content_type,response,expected_prefix",
    [
        (GEMINI, "pcm", "audio/pcm; rate=24000", b"\x00\x00" * 20, b"RIFF"),
        (VOXTRAL, "mp3", "audio/mpeg", b"ID3mock", b"ID3"),
        (MAI, "mp3", "audio/mpeg", b"ID3mock", b"ID3"),
    ],
)
def test_openrouter_payload_and_audio_contract(
        monkeypatch, impl, model, expected_format, content_type, response,
        expected_prefix):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _Response(response, content_type)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    audio, returned_type = impl.openrouter_tts(
        "Reader contract", "sk-or-test", model=model,
        route_decision=_openrouter_route())

    assert captured["url"] == "https://openrouter.ai/api/v1/audio/speech"
    assert captured["payload"] == {
        "model": model,
        "input": "Reader contract",
        "voice": EXPECTED_DEFAULTS[model],
        "response_format": expected_format,
    }
    assert audio.startswith(expected_prefix)
    assert returned_type == ("audio/wav" if expected_format == "pcm" else content_type)


@pytest.mark.parametrize("impl", IMPLEMENTATIONS)
def test_intra_openrouter_fallback_uses_sibling_voice(monkeypatch, impl):
    calls = []

    class OpenRouterRecorder:
        provider_id = "openrouter"
        default_model = GEMINI
        default_voice = "Fenrir"

        def synthesize(self, text, voice_id=None, model=None, **_kwargs):
            calls.append((model, voice_id))
            if model == GEMINI:
                raise RuntimeError("preview model unavailable")
            return b"sibling-audio", "audio/mpeg"

    class OpenAIRecorder:
        provider_id = "openai"
        default_model = "gpt-4o-mini-tts"
        default_voice = "onyx"

        def synthesize(self, *_args, **_kwargs):
            raise AssertionError("OpenAI should not be reached")

    providers = {
        "openrouter": OpenRouterRecorder(),
        "openai": OpenAIRecorder(),
    }
    monkeypatch.setattr(impl, "get_tts_provider", lambda provider: providers[provider])

    audio, content_type, meta = impl.synthesize_with_fallback(
        "fallback", model=GEMINI, provider_id="openrouter",
        route_decision=_openrouter_route())

    assert calls[:2] == [
        (GEMINI, "Fenrir"),
        (VOXTRAL, "gb_oliver_neutral"),
    ]
    assert audio == b"sibling-audio"
    assert content_type == "audio/mpeg"
    assert meta == {
        "ok": True,
        "provider": "openrouter",
        "model": VOXTRAL,
        "voice": "gb_oliver_neutral",
        "fallback": True,
        "fallback_provider": "openrouter",
        "fallback_model": VOXTRAL,
    }


def _migrate_reader(provider, model, voice):
    instance = settings.Settings.__new__(settings.Settings)
    instance.data = copy.deepcopy(settings.DEFAULTS)
    instance.data.update({
        "reader_tts_provider": provider,
        "reader_tts_model": model,
        "reader_voice": voice,
        "reader_tts_voice_contract_applied": False,
    })
    instance._dirty = set()
    instance.save = lambda: None
    instance._migrate()
    return instance.data


def test_settings_default_and_legacy_voice_migration_are_provider_safe():
    assert settings.DEFAULTS["reader_tts_provider"] == "openrouter"
    assert settings.DEFAULTS["reader_tts_model"] == GEMINI
    assert settings.DEFAULTS["reader_voice"] == "Fenrir"

    healed = _migrate_reader("openrouter", GEMINI, "onyx")
    assert healed["reader_voice"] == "Fenrir"

    selected = _migrate_reader("openrouter", GEMINI, "Puck")
    assert selected["reader_voice"] == "Puck"

    openai = _migrate_reader("openai", "gpt-4o-mini-tts", "onyx")
    assert openai["reader_voice"] == "onyx"
