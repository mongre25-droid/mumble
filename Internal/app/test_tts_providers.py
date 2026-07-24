#!/usr/bin/env python3
"""Tests for ai/tts_providers.py — TTS provider abstraction, voice catalogue,
and transparent provider fallback.

Usage:
    python test_tts_providers.py
"""

import os
import sys

_app_dir = os.path.dirname(os.path.abspath(__file__))
if _app_dir not in sys.path:
    sys.path.insert(0, _app_dir)

TESTS = []
FAILURES = []


def check(label, cond):
    TESTS.append(label)
    if not cond:
        FAILURES.append(label)
        print(f"  FAIL  {label}")
    else:
        print(f"  ok    {label}")


print("=== Import checks ===")

import ai.tts_providers as tts
check("tts_providers module imports", True)

# Constants
check("QUALITY_HIGH defined", tts.QUALITY_HIGH == "high")
check("QUALITY_PREVIEW defined", tts.QUALITY_PREVIEW == "preview")
check("QUALITY_STANDARD defined", tts.QUALITY_STANDARD == "standard")
check("QUALITY_LEGACY defined", tts.QUALITY_LEGACY == "legacy")

check("GENDER_MALE defined", tts.GENDER_MALE == "male")
check("GENDER_FEMALE defined", tts.GENDER_FEMALE == "female")
check("GENDER_NEUTRAL defined", tts.GENDER_NEUTRAL == "neutral")

check("PERSONA_DEEP defined", tts.PERSONA_DEEP == "deep")
check("PERSONA_NARRATOR defined", tts.PERSONA_NARRATOR == "narrator")
check("PERSONA_WARM defined", tts.PERSONA_WARM == "warm")

print("\n=== TTSProvider base class ===")

check("TTSProvider is a class", isinstance(tts.TTSProvider, type))

# Verify abstract methods exist
tp = tts.TTSProvider
check("has list_voices", hasattr(tp, "list_voices"))
check("has synthesize", hasattr(tp, "synthesize"))
check("has provider_id property", hasattr(tp, "provider_id"))
check("has provider_label property", hasattr(tp, "provider_label"))
check("has default_model property", hasattr(tp, "default_model"))
check("has default_voice property", hasattr(tp, "default_voice"))
check("has auth_setting property", hasattr(tp, "auth_setting"))

print("\n=== OpenRouter TTS models ===")

check("OPENROUTER_TTS_MODELS is list", isinstance(tts.OPENROUTER_TTS_MODELS, list))
check("OPENROUTER_TTS_MODELS has entries", len(tts.OPENROUTER_TTS_MODELS) >= 3)
check("OPENROUTER_TTS_FORMATS is dict", isinstance(tts.OPENROUTER_TTS_FORMATS, dict))
check("OPENROUTER_TTS_DEFAULT_MODEL is Gemini", tts.OPENROUTER_TTS_DEFAULT_MODEL == "google/gemini-3.1-flash-tts-preview")
check("OPENROUTER_TTS_DEFAULT_VOICE is Fenrir", tts.OPENROUTER_TTS_DEFAULT_VOICE == "Fenrir")

print("\n=== OpenRouterTTSProvider ===")

check("OpenRouterTTSProvider is a class", isinstance(tts.OpenRouterTTSProvider, type))
check("OpenRouterTTSProvider extends TTSProvider", issubclass(tts.OpenRouterTTSProvider, tts.TTSProvider))

orp = tts.OpenRouterTTSProvider()
check("provider_id is 'openrouter'", orp.provider_id == "openrouter")
check("provider_label is 'OpenRouter'", orp.provider_label == "OpenRouter")
check("auth_setting is 'openrouter_api_key'", orp.auth_setting == "openrouter_api_key")
check("default_model is Gemini", orp.default_model == tts.OPENROUTER_TTS_DEFAULT_MODEL)
check("default_voice is Fenrir", orp.default_voice == "Fenrir")

# list_voices
voices = orp.list_voices()
check("list_voices returns list", isinstance(voices, list))
check("list_voices has entries", len(voices) > 0)

# Check each voice has required fields
for v in voices:
    required = ["id", "name", "gender", "quality", "persona", "provider", "model"]
    for field in required:
        check(f"voice has {field}", field in v)

# Check gender tagging for Fenrir
fenrir = next((v for v in voices if v.get("id") == "Fenrir"), None)
if fenrir:
    check("Fenrir is male", fenrir["gender"] == tts.GENDER_MALE)
    check("Fenrir has deep persona", fenrir["persona"] == tts.PERSONA_DEEP)

print("\n=== OpenAITTSProvider ===")

check("OpenAITTSProvider is a class", isinstance(tts.OpenAITTSProvider, type))
check("OpenAITTSProvider extends TTSProvider", issubclass(tts.OpenAITTSProvider, tts.TTSProvider))

oap = tts.OpenAITTSProvider()
check("provider_id is 'openai'", oap.provider_id == "openai")
check("provider_label is 'OpenAI'", oap.provider_label == "OpenAI")
check("default_model is gpt-4o-mini-tts", oap.default_model == "gpt-4o-mini-tts")
check("default_voice is onyx", oap.default_voice == "onyx")

# list_voices
voices = oap.list_voices()
check("list_voices returns list", isinstance(voices, list))
check("list_voices has entries", len(voices) > 0)

# onyx should be male and deep
onyx = next((v for v in voices if v.get("id") == "onyx"), None)
if onyx:
    check("onyx is male", onyx["gender"] == tts.GENDER_MALE)
    check("onyx has deep persona", onyx["persona"] == tts.PERSONA_DEEP)

print("\n=== Provider registry ===")

check("get_tts_provider is callable", callable(tts.get_tts_provider))
check("list_tts_providers is callable", callable(tts.list_tts_providers))
check("get_tts_voices is callable", callable(tts.get_tts_voices))
check("get_tts_defaults is callable", callable(tts.get_tts_defaults))
check("synthesize_with_fallback is callable", callable(tts.synthesize_with_fallback))

# get_tts_provider
p = tts.get_tts_provider("openrouter")
check("get_tts_provider('openrouter') returns provider", isinstance(p, tts.TTSProvider))
check("default provider is OpenRouter", isinstance(tts.get_tts_provider("nonexistent"), tts.OpenRouterTTSProvider))

# list_tts_providers
providers = tts.list_tts_providers()
check("list_tts_providers returns list", isinstance(providers, list))
check("has openrouter in providers", any(p["id"] == "openrouter" for p in providers))
check("has openai in providers", any(p["id"] == "openai" for p in providers))

# get_tts_voices
voices = tts.get_tts_voices()
check("get_tts_voices returns list", isinstance(voices, list))
check("get_tts_voices has entries", len(voices) > 0)
# All voices should be high-quality and non-female
for v in voices:
    check(f"voice {v.get('name', '?')} is high quality", v.get("quality") == tts.QUALITY_HIGH)
    check(f"voice {v.get('name', '?')} is not female", v.get("gender") != tts.GENDER_FEMALE)

# Verify male-first sorting
genders = [v.get("gender") for v in voices]
male_indices = [i for i, g in enumerate(genders) if g == tts.GENDER_MALE]
neutral_indices = [i for i, g in enumerate(genders) if g == tts.GENDER_NEUTRAL]
if male_indices and neutral_indices:
    check("male voices come before neutral", max(male_indices) < min(neutral_indices))

# get_tts_defaults
pid, model, voice = tts.get_tts_defaults()
check("get_tts_defaults returns provider_id", isinstance(pid, str))
check("get_tts_defaults returns model", isinstance(model, str))
check("get_tts_defaults returns voice", isinstance(voice, str))

print("\n=== synthesize_with_fallback() error path ===")

# Never spend a developer's saved API credit from an offline unit test. Force
# both providers' key lookups empty and verify the clean all-unavailable path.
_orig_or_key = tts.OpenRouterTTSProvider._get_key
_orig_oa_key = tts.OpenAITTSProvider._get_key
try:
    tts.OpenRouterTTSProvider._get_key = lambda self: ""
    tts.OpenAITTSProvider._get_key = lambda self: ""
    audio, ctype, meta = tts.synthesize_with_fallback(
        "hello", provider_id="openrouter")
finally:
    tts.OpenRouterTTSProvider._get_key = _orig_or_key
    tts.OpenAITTSProvider._get_key = _orig_oa_key

check("fallback returns meta dict", isinstance(meta, dict))
check("fallback with no key returns None audio", audio is None)
check("fallback with no key returns None ctype", ctype is None)
check("fallback with no key ok=False", meta.get("ok") is False)
check("fallback with no key has message", isinstance(meta.get("message"), str))

# Empty text should always fail fast
audio, ctype, meta = tts.synthesize_with_fallback("", provider_id="openrouter")
check("empty text returns None audio", audio is None)
check("empty text ok=False", meta.get("ok") is False)

print("\n=== PCM helpers ===")

check("_pcm_to_wav is callable", callable(tts._pcm_to_wav))
check("_pcm_rate_from_ctype is callable", callable(tts._pcm_rate_from_ctype))

# PCM to WAV conversion
pcm = b"\x00" * 1000  # 1000 bytes of silence
wav = tts._pcm_to_wav(pcm, rate=24000)
check("PCM to WAV returns bytes", isinstance(wav, bytes))
check("PCM to WAV has WAV header", wav[:4] == b"RIFF")
check("PCM to WAV larger than input", len(wav) > len(pcm))

# PCM rate extraction
rate = tts._pcm_rate_from_ctype("audio/L16;rate=24000")
check("rate extracted from content-type", rate == 24000)

rate = tts._pcm_rate_from_ctype("audio/pcm; rate=22050")
check("rate extracted with space", rate == 22050)

rate = tts._pcm_rate_from_ctype("audio/mpeg")
check("default rate for non-PCM", rate == 24000)

print("\n=== Backward compatibility from ai package ===")

import ai

# The TTS classes are imported into ai.__init__ with _tts_ prefix (private)
# The public API should still be accessible via the ai module for existing callers
# (These are duplicated in ai/__init__.py for backward compat)
try:
    check("ai.TTSProvider accessible", hasattr(ai, "TTSProvider"))
except Exception:
    check("ai.TTSProvider accessible", True)  # May be _tts_TTSProvider

check("ai.openrouter_tts callable", callable(ai.openrouter_tts))
check("ai.get_tts_provider callable", callable(ai.get_tts_provider))
check("ai.get_tts_voices callable", callable(ai.get_tts_voices))
check("ai.synthesize_with_fallback callable", callable(ai.synthesize_with_fallback))

# =============================================================================
print(f"\n{'=' * 60}")
print(f"RESULTS: {len(TESTS) - len(FAILURES)}/{len(TESTS)} passed")
if FAILURES:
    print(f"FAILURES: {len(FAILURES)}")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("All tests passed.")
