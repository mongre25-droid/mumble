#!/usr/bin/env python3
"""Tests for ai/stt_providers.py — cloud STT provider registry and helpers.

Usage:
    python test_stt_providers.py
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

import ai.stt_providers as stt
check("stt_providers module imports", True)
check("PROVIDERS is dict", isinstance(stt.PROVIDERS, dict))
check("DEFAULT_PROVIDER is 'groq'", stt.DEFAULT_PROVIDER == "groq")

print("\n=== Provider registry ===")

check("groq in PROVIDERS", "groq" in stt.PROVIDERS)
check("openai in PROVIDERS", "openai" in stt.PROVIDERS)
check("openrouter in PROVIDERS", "openrouter" in stt.PROVIDERS)

# Groq
g = stt.PROVIDERS["groq"]
check("Groq has url", bool(g.get("url")))
check("Groq has key_setting", g.get("key_setting") == "groq_api_key")
check("Groq has model_setting", g.get("model_setting") == "groq_transcription_model")
check("Groq default_model", g.get("default_model") == "whisper-large-v3-turbo")
check("Groq shape is multipart", g.get("shape") == "multipart")

# OpenAI
o = stt.PROVIDERS["openai"]
check("OpenAI has url", bool(o.get("url")))
check("OpenAI key_setting", o.get("key_setting") == "openai_api_key")
check("OpenAI shape is multipart", o.get("shape") == "multipart")

# OpenRouter
r = stt.PROVIDERS["openrouter"]
check("OpenRouter has url", bool(r.get("url")))
check("OpenRouter key_setting", r.get("key_setting") == "openrouter_api_key")
check("OpenRouter shape is json_base64", r.get("shape") == "json_base64")

print("\n=== provider_info() ===")

check("provider_info is callable", callable(stt.provider_info))

info = stt.provider_info("groq")
check("provider_info('groq') returns dict", isinstance(info, dict))
check("provider_info('groq') has url", bool(info.get("url")))

info = stt.provider_info("openrouter")
check("provider_info('openrouter') returns dict", isinstance(info, dict))

# Unknown provider falls back to default
info = stt.provider_info("nonexistent")
check("unknown provider falls back to groq", info["key_setting"] == "groq_api_key")

info = stt.provider_info("")
check("empty provider falls back to default", isinstance(info, dict))

print("\n=== pcm16_wav_bytes() ===")

import numpy as np
check("pcm16_wav_bytes is callable", callable(stt.pcm16_wav_bytes))

# Generate 1 second of silence
audio = np.zeros(16000, dtype=np.float32)
wav = stt.pcm16_wav_bytes(audio)
check("pcm16_wav_bytes returns bytes", isinstance(wav, bytes))
check("pcm16_wav_bytes returns non-empty", len(wav) > 44)  # WAV header is 44 bytes
check("pcm16_wav_bytes has WAV header", wav[:4] == b"RIFF")

# 1 second of 16kHz mono 16-bit should be ~32044 bytes
check("pcm16_wav_bytes correct size", abs(len(wav) - 32044) < 100)

print("\n=== transcribe() error paths ===")

# transcribe requires a settings object — test the error path
class FakeSettings:
    def get(self, key, default=None):
        if key == "cloud_transcription_provider":
            return "groq"
        if key == "groq_api_key":
            return ""
        return default

try:
    stt.transcribe(np.zeros(16000, dtype=np.float32), FakeSettings())
    check("transcribe raises on missing key", False)
except ValueError as e:
    check("transcribe raises ValueError on missing key", "No API key" in str(e) or "key" in str(e).lower())
except Exception as e:
    check(f"transcribe raises on missing key (got {type(e).__name__})", True)

class UnknownProviderSettings:
    def get(self, key, default=None):
        if key == "cloud_transcription_provider":
            return "retired-provider"
        if key == "groq_api_key":
            return "gsk-must-not-be-used"
        return default


try:
    stt.transcribe(np.zeros(1600, dtype=np.float32), UnknownProviderSettings())
    unknown_raised = False
except ValueError as e:
    unknown_raised = "Unsupported cloud transcription provider" in str(e)
check("transcribe never reinterprets an unknown provider as Groq",
      unknown_raised)

print("\n=== Backward compatibility from ai package ===")

import ai
check("ai.STT_PROVIDERS accessible", isinstance(ai.STT_PROVIDERS, dict))
check("ai.STT_DEFAULT_PROVIDER is 'groq'", ai.STT_DEFAULT_PROVIDER == "groq")
check("ai.stt_provider_info callable", callable(ai.stt_provider_info))
check("ai.pcm16_wav_bytes callable", callable(ai.pcm16_wav_bytes))
check("ai.cloud_transcribe callable", callable(ai.cloud_transcribe))

# Cross-check: ai.STT_PROVIDERS should match transcription.PROVIDERS
# (transcription.py may have its own copy, but the structure should match)
import transcription as tx
check("STT providers keys match transcription", set(stt.PROVIDERS.keys()) == set(tx.PROVIDERS.keys()))

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
