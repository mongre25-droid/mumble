#!/usr/bin/env python3
"""Tests for the modular AI provider layer (Cerebras, OpenRouter).

Tests cover:
  - Provider instantiation (valid / missing key)
  - BaseProvider interface conformance
  - headers(), model_info
  - key_ok() for valid and invalid keys (live API)
  - fetch_models() (live API)
  - get_openrouter_credits() (live API)
  - backward compatibility (ai.key_ok, ai.fetch_models, etc.)
  - reasoning_effort scoping (Cerebras-only)
  - Provider registry entries

Usage:
    python test_providers.py
"""

import json
import os
import sys

# Ensure the app directory is on sys.path
_app_dir = os.path.dirname(os.path.abspath(__file__))
if _app_dir not in sys.path:
    sys.path.insert(0, _app_dir)

TESTS = []
FAILURES = []


def check(label, cond):
    """Register a test result."""
    TESTS.append(label)
    if not cond:
        FAILURES.append(label)
        print(f"  FAIL  {label}")
    else:
        print(f"  ok    {label}")


def test(label):
    """Decorator that runs a test function and records pass/fail."""
    def decorator(fn):
        TESTS.append(label)
        try:
            fn()
            print(f"  ok    {label}")
        except Exception as e:
            FAILURES.append(label)
            print(f"  FAIL  {label} — {e}")
    return decorator


# =============================================================================
# Import checks
# =============================================================================

print("=== Import checks ===")

import ai
from ai.base import BaseProvider, FULL_MARKER, TRUNC_MARKER

check("ai module imports", True)
check("BaseProvider available", BaseProvider is not None)
check("FULL_MARKER defined", isinstance(FULL_MARKER, str) and len(FULL_MARKER) > 0)
check("TRUNC_MARKER defined", isinstance(TRUNC_MARKER, str) and len(TRUNC_MARKER) > 0)

# ---- CerebrasProvider --------------------------------------------------------
try:
    from ai.providers.cerebras import CerebrasProvider
    CEREBRAS_AVAILABLE = True
except ImportError:
    CerebrasProvider = None
    CEREBRAS_AVAILABLE = False
check("CerebrasProvider importable", CEREBRAS_AVAILABLE)

# ---- OpenRouterProvider ------------------------------------------------------
try:
    from ai.providers.openrouter import OpenRouterProvider
    OPENROUTER_AVAILABLE = True
except ImportError:
    OpenRouterProvider = None
    OPENROUTER_AVAILABLE = False
check("OpenRouterProvider importable", OPENROUTER_AVAILABLE)

# ---- GroqProvider -----------------------------------------------------------
try:
    from ai.providers.groq import GroqProvider
    GROQ_AVAILABLE = True
except ImportError:
    GroqProvider = None
    GROQ_AVAILABLE = False
check("GroqProvider importable", GROQ_AVAILABLE)

# ---- DeepSeekProvider --------------------------------------------------------
try:
    from ai.providers.deepseek import DeepSeekProvider
    DEEPSEEK_AVAILABLE = True
except ImportError:
    DeepSeekProvider = None
    DEEPSEEK_AVAILABLE = False
check("DeepSeekProvider importable", DEEPSEEK_AVAILABLE)

# ---- LocalProvider -----------------------------------------------------------
try:
    from ai.providers.local import LocalProvider
    LOCAL_AVAILABLE = True
except ImportError:
    LocalProvider = None
    LOCAL_AVAILABLE = False
check("LocalProvider importable", LOCAL_AVAILABLE)

# ---- Provider registry -------------------------------------------------------
from ai.providers import PROVIDERS, provider_ids, provider_info, provider_models_url
check("PROVIDERS dict exists", isinstance(PROVIDERS, dict))
check("cerebras in PROVIDERS", "cerebras" in PROVIDERS)
check("openrouter in PROVIDERS", "openrouter" in PROVIDERS)
check("provider_ids() returns list", isinstance(provider_ids(), list))
check("provider_info('cerebras') returns dict", isinstance(provider_info("cerebras"), dict))
check("provider_info('openrouter') returns dict", isinstance(provider_info("openrouter"), dict))
check("provider_models_url('cerebras') is not None", provider_models_url("cerebras") is not None)
check("provider_models_url('openrouter') is not None", provider_models_url("openrouter") is not None)


# =============================================================================
# CerebrasProvider unit tests
# =============================================================================

print("\n=== CerebrasProvider unit tests ===")

if CEREBRAS_AVAILABLE:
    # Missing key
    try:
        CerebrasProvider("")
        check("CerebrasProvider('') raises ValueError", False)
    except ValueError:
        check("CerebrasProvider('') raises ValueError", True)

    try:
        CerebrasProvider(None)
        check("CerebrasProvider(None) raises ValueError", False)
    except (ValueError, AttributeError):
        check("CerebrasProvider(None) raises", True)

    # Valid construction
    cp = CerebrasProvider("sk-test-key")
    check("CerebrasProvider('sk-test-key') creates instance", isinstance(cp, BaseProvider))

    # headers()
    h = cp.headers()
    check("headers() returns dict", isinstance(h, dict))
    check("headers() has Authorization", "Authorization" in h)
    check("headers() has Bearer token", "Bearer sk-test-key" in h.get("Authorization", ""))
    check("headers() has Content-Type", "Content-Type" in h)

    # model_info
    info = cp.model_info
    check("model_info['provider'] == 'cerebras'", info.get("provider") == "cerebras")
    check("model_info has 'model'", "model" in info)
    check("model_info has 'url'", "url" in info)
    check("model_info default model is gpt-oss-120b", info.get("model") == "gpt-oss-120b")

    # Custom model
    cp2 = CerebrasProvider("sk-test", model="custom-model")
    check("custom model in model_info", cp2.model_info["model"] == "custom-model")

    # reasoning_effort stored
    cp3 = CerebrasProvider("sk-test", reasoning_effort="high")
    check("reasoning_effort stored", cp3._reasoning_effort == "high")

    # Abstract methods implemented
    check("has chat()", hasattr(cp, "chat") and callable(cp.chat))
    check("has chat_stream()", hasattr(cp, "chat_stream") and callable(cp.chat_stream))

    # key_ok with empty key
    result = CerebrasProvider.key_ok("")
    check("key_ok('') returns False", result is False)

    # fetch_models with empty key
    try:
        CerebrasProvider.fetch_models("")
        check("fetch_models('') raises ValueError", False)
    except ValueError:
        check("fetch_models('') raises ValueError", True)


# =============================================================================
# OpenRouterProvider unit tests
# =============================================================================

print("\n=== OpenRouterProvider unit tests ===")

if OPENROUTER_AVAILABLE:
    # Missing key
    try:
        OpenRouterProvider("")
        check("OpenRouterProvider('') raises ValueError", False)
    except ValueError:
        check("OpenRouterProvider('') raises ValueError", True)

    # Valid construction
    op = OpenRouterProvider("sk-or-test-key")
    check("OpenRouterProvider('sk-or-test-key') creates instance", isinstance(op, BaseProvider))

    # headers()
    h = op.headers()
    check("headers() returns dict", isinstance(h, dict))
    check("headers() has Authorization", "Authorization" in h)
    check("headers() has Bearer token", "Bearer sk-or-test-key" in h.get("Authorization", ""))

    # model_info
    info = op.model_info
    check("model_info['provider'] == 'openrouter'", info.get("provider") == "openrouter")
    check("model_info has 'model'", "model" in info)
    check("model_info has 'url'", "url" in info)

    # Abstract methods
    check("has chat()", hasattr(op, "chat") and callable(op.chat))
    check("has chat_stream()", hasattr(op, "chat_stream") and callable(op.chat_stream))

    # get_credits with empty key
    try:
        OpenRouterProvider.get_credits("")
        check("get_credits('') raises ValueError", False)
    except ValueError:
        check("get_credits('') raises ValueError", True)

    # key_ok with empty key
    result = OpenRouterProvider.key_ok("")
    check("key_ok('') returns False", result is False)

    # fetch_models with empty key
    try:
        OpenRouterProvider.fetch_models("")
        check("fetch_models('') raises ValueError", False)
    except ValueError:
        check("fetch_models('') raises ValueError", True)


# =============================================================================
# Backward compatibility
# =============================================================================

print("\n=== Backward compatibility ===")

# ai.key_ok should still work with default args
check("ai.key_ok is callable", callable(ai.key_ok))
check("ai.fetch_models is callable", callable(ai.fetch_models))
check("ai.get_openrouter_credits is callable", callable(ai.get_openrouter_credits))
check("ai.cerebras_chat is callable", callable(ai.cerebras_chat))
check("ai.cerebras_chat_stream is callable", callable(ai.cerebras_chat_stream))
check("ai.cerebras_test is callable", callable(ai.cerebras_test))

# URL constants from ai module
check("ai.CEREBRAS_URL defined", hasattr(ai, "CEREBRAS_URL"))
check("ai.CEREBRAS_MODELS_URL defined", hasattr(ai, "CEREBRAS_MODELS_URL"))
check("ai.OPENROUTER_URL defined", hasattr(ai, "OPENROUTER_URL"))
check("ai.OPENROUTER_MODELS_URL defined", hasattr(ai, "OPENROUTER_MODELS_URL"))
check("ai.OPENROUTER_CREDITS_URL defined", hasattr(ai, "OPENROUTER_CREDITS_URL"))

# PROVIDERS from ai module
check("ai.PROVIDERS exists", hasattr(ai, "PROVIDERS"))
check("cerebras in ai.PROVIDERS", "cerebras" in ai.PROVIDERS)
check("openrouter in ai.PROVIDERS", "openrouter" in ai.PROVIDERS)

# key_ok with empty key (should return False)
check("ai.key_ok('') returns False", ai.key_ok("") is False)

# fetch_models with empty key (should raise ValueError)
try:
    ai.fetch_models("cerebras", "")
    check("ai.fetch_models('cerebras', '') raises ValueError", False)
except ValueError:
    check("ai.fetch_models('cerebras', '') raises ValueError", True)

# get_openrouter_credits with empty key
try:
    ai.get_openrouter_credits("")
    check("ai.get_openrouter_credits('') raises ValueError", False)
except ValueError:
    check("ai.get_openrouter_credits('') raises ValueError", True)

# URL constants for OpenAI and Anthropic
check("ai.OPENAI_URL defined", hasattr(ai, "OPENAI_URL"))
check("ai.OPENAI_MODELS_URL defined", hasattr(ai, "OPENAI_MODELS_URL"))
check("ai.ANTHROPIC_URL defined", hasattr(ai, "ANTHROPIC_URL"))
check("ai.ANTHROPIC_MODELS_URL defined", hasattr(ai, "ANTHROPIC_MODELS_URL"))
check("ai.ANTHROPIC_VERSION defined", hasattr(ai, "ANTHROPIC_VERSION"))

# URL constants for DeepSeek, Groq, and Local
check("ai.DEEPSEEK_URL defined", hasattr(ai, "DEEPSEEK_URL"))
check("ai.DEEPSEEK_MODELS_URL defined", hasattr(ai, "DEEPSEEK_MODELS_URL"))
check("ai.GROQ_URL defined", hasattr(ai, "GROQ_URL"))
check("ai.GROQ_MODELS_URL defined", hasattr(ai, "GROQ_MODELS_URL"))

# openai, anthropic, deepseek, groq, local in ai.PROVIDERS
check("openai in ai.PROVIDERS", "openai" in ai.PROVIDERS)
check("anthropic in ai.PROVIDERS", "anthropic" in ai.PROVIDERS)
check("deepseek in ai.PROVIDERS", "deepseek" in ai.PROVIDERS)
check("groq in ai.PROVIDERS", "groq" in ai.PROVIDERS)
check("local in ai.PROVIDERS", "local" in ai.PROVIDERS)

# fetch_models for openai with empty key
try:
    ai.fetch_models("openai", "")
    check("ai.fetch_models('openai', '') raises ValueError", False)
except ValueError:
    check("ai.fetch_models('openai', '') raises ValueError", True)

# fetch_models for anthropic with empty key
try:
    ai.fetch_models("anthropic", "")
    check("ai.fetch_models('anthropic', '') raises ValueError", False)
except ValueError:
    check("ai.fetch_models('anthropic', '') raises ValueError", True)

# fetch_models for deepseek with empty key
try:
    ai.fetch_models("deepseek", "")
    check("ai.fetch_models('deepseek', '') raises ValueError", False)
except ValueError:
    check("ai.fetch_models('deepseek', '') raises ValueError", True)

# fetch_models for groq with empty key
try:
    ai.fetch_models("groq", "")
    check("ai.fetch_models('groq', '') raises ValueError", False)
except ValueError:
    check("ai.fetch_models('groq', '') raises ValueError", True)

# fetch_models for local raises ValueError (no models endpoint)
try:
    ai.fetch_models("local", "")
    check("ai.fetch_models('local', '') raises ValueError", False)
except ValueError as e:
    check("ai.fetch_models('local', '') raises ValueError", True)
    # Should mention that local servers don't expose a model list
    check("fetch_models('local') error mentions no model list",
          "model list" in str(e).lower()
          or "manually" in str(e).lower()
          or "don't expose" in str(e).lower())

# ai._anthropic_chat and ai._anthropic_headers are callable
check("ai._anthropic_chat is callable", callable(ai._anthropic_chat))
check("ai._anthropic_headers is callable", callable(ai._anthropic_headers))

# _token_param checks
check("ai._token_param(OPENAI_URL) == 'max_completion_tokens'",
      ai._token_param(ai.OPENAI_URL) == "max_completion_tokens")
check("ai._token_param(CEREBRAS_URL) == 'max_tokens'",
      ai._token_param(ai.CEREBRAS_URL) == "max_tokens")

# _is_reasoning_model checks
check("ai._is_reasoning_model('gpt-5.4-mini') is True",
      ai._is_reasoning_model("gpt-5.4-mini") is True)
check("ai._is_reasoning_model('gpt-oss-120b') is False",
      ai._is_reasoning_model("gpt-oss-120b") is False)


# =============================================================================
# OpenAIProvider unit tests
# =============================================================================

print("\n=== OpenAIProvider unit tests ===")

try:
    from ai.providers.openai import OpenAIProvider
    OPENAI_AVAILABLE = True
except ImportError:
    OpenAIProvider = None
    OPENAI_AVAILABLE = False
check("OpenAIProvider importable", OPENAI_AVAILABLE)

if OPENAI_AVAILABLE:
    # Missing key
    try:
        OpenAIProvider("")
        check("OpenAIProvider('') raises ValueError", False)
    except ValueError:
        check("OpenAIProvider('') raises ValueError", True)

    try:
        OpenAIProvider(None)
        check("OpenAIProvider(None) raises", False)
    except (ValueError, AttributeError):
        check("OpenAIProvider(None) raises", True)

    # Valid construction
    op = OpenAIProvider("sk-test-key")
    check("OpenAIProvider('sk-test-key') creates instance", isinstance(op, BaseProvider))

    # headers()
    h = op.headers()
    check("headers() returns dict", isinstance(h, dict))
    check("headers() has Authorization", "Authorization" in h)
    check("headers() has Bearer token", "Bearer sk-test-key" in h.get("Authorization", ""))

    # model_info
    info = op.model_info
    check("model_info['provider'] == 'openai'", info.get("provider") == "openai")
    check("model_info has 'model'", "model" in info)
    check("model_info has 'url'", "url" in info)
    check("model_info default model is gpt-5.4-mini", info.get("model") == "gpt-5.4-mini")

    # Token param: OpenAI uses max_completion_tokens
    from ai.base import BaseProvider as BP
    token_param = BP._token_param("https://api.openai.com/v1/chat/completions")
    check("OpenAI uses max_completion_tokens", token_param == "max_completion_tokens")

    # Reasoning model detection
    check("gpt-5.4-mini is reasoning model", BP._is_reasoning_model("gpt-5.4-mini") is True)
    check("gpt-5.4 is reasoning model", BP._is_reasoning_model("gpt-5.4") is True)
    check("o3-mini is reasoning model", BP._is_reasoning_model("o3-mini") is True)
    check("openai/o3-mini is reasoning model", BP._is_reasoning_model("openai/o3-mini") is True)
    check("gpt-oss-120b is NOT reasoning model", BP._is_reasoning_model("gpt-oss-120b") is False)
    check("llama-3.3 is NOT reasoning model", BP._is_reasoning_model("llama-3.3-70b") is False)

    # Abstract methods implemented
    check("has chat()", hasattr(op, "chat") and callable(op.chat))
    check("has chat_stream()", hasattr(op, "chat_stream") and callable(op.chat_stream))

    # key_ok with empty key
    result = OpenAIProvider.key_ok("")
    check("key_ok('') returns False", result is False)

    # fetch_models with empty key
    try:
        OpenAIProvider.fetch_models("")
        check("fetch_models('') raises ValueError", False)
    except ValueError:
        check("fetch_models('') raises ValueError", True)


# =============================================================================
# AnthropicProvider unit tests
# =============================================================================

print("\n=== AnthropicProvider unit tests ===")

try:
    from ai.providers.anthropic import (
        AnthropicProvider,
        _anthropic_headers as anthro_headers,
    )
    ANTHROPIC_AVAILABLE = True
except ImportError:
    AnthropicProvider = None
    anthro_headers = None
    ANTHROPIC_AVAILABLE = False
check("AnthropicProvider importable", ANTHROPIC_AVAILABLE)

if ANTHROPIC_AVAILABLE:
    # Missing key
    try:
        AnthropicProvider("")
        check("AnthropicProvider('') raises ValueError", False)
    except ValueError:
        check("AnthropicProvider('') raises ValueError", True)

    try:
        AnthropicProvider(None)
        check("AnthropicProvider(None) raises", False)
    except (ValueError, AttributeError):
        check("AnthropicProvider(None) raises", True)

    # Valid construction
    ap = AnthropicProvider("sk-ant-test-key")
    check("AnthropicProvider('sk-ant-test-key') creates instance", isinstance(ap, BaseProvider))

    # headers() — must use x-api-key, NOT Authorization: Bearer
    h = ap.headers()
    check("headers() returns dict", isinstance(h, dict))
    check("headers() has x-api-key (not Bearer)", "x-api-key" in h)
    check("headers() does NOT have Authorization", "Authorization" not in h)
    check("headers() has anthropic-version", "anthropic-version" in h)
    check("headers() anthropic-version is 2023-06-01",
          h.get("anthropic-version") == "2023-06-01")
    check("headers() x-api-key contains test key",
          h.get("x-api-key") == "sk-ant-test-key")

    # _anthropic_headers standalone
    h2 = anthro_headers("sk-ant-test", json_body=False)
    check("_anthropic_headers() has x-api-key", "x-api-key" in h2)
    check("_anthropic_headers() has anthropic-version", "anthropic-version" in h2)
    check("_anthropic_headers(json_body=False) no Content-Type",
          "Content-Type" not in h2)

    # model_info
    info = ap.model_info
    check("model_info['provider'] == 'anthropic'", info.get("provider") == "anthropic")
    check("model_info has 'model'", "model" in info)
    check("model_info has 'url'", "url" in info)
    check("model_info url is /v1/messages",
          "api.anthropic.com/v1/messages" in info.get("url", ""))
    check("model_info default model is claude-opus-4-8",
          info.get("model") == "claude-opus-4-8")

    # Abstract methods implemented
    check("has chat()", hasattr(ap, "chat") and callable(ap.chat))
    check("has chat_stream()", hasattr(ap, "chat_stream") and callable(ap.chat_stream))

    # key_ok with empty key
    result = AnthropicProvider.key_ok("")
    check("key_ok('') returns False", result is False)

    # fetch_models with empty key
    try:
        AnthropicProvider.fetch_models("")
        check("fetch_models('') raises ValueError", False)
    except ValueError:
        check("fetch_models('') raises ValueError", True)

    # Messages API body shape (non-streaming): chat_stream yields FULL_MARKER
    # This is a unit test — we don't make a real API call, but we verify that
    # chat_stream() with mocked messages produces a generator.
    gen = ap.chat_stream([
        {"role": "system", "content": "Test system prompt."},
        {"role": "user", "content": "Hello, world."},
    ])
    check("chat_stream() returns generator", hasattr(gen, "__iter__"))
    # We can't iterate without a real API call, but the generator is created.

    # Provider registry entries
    from ai.providers import PROVIDERS
    check("openai in ai.providers.PROVIDERS", "openai" in PROVIDERS)
    check("anthropic in ai.providers.PROVIDERS", "anthropic" in PROVIDERS)
    check("openai provider has key_setting 'openai_api_key'",
          PROVIDERS.get("openai", {}).get("key_setting") == "openai_api_key")
    check("anthropic provider has key_setting 'anthropic_api_key'",
          PROVIDERS.get("anthropic", {}).get("key_setting") == "anthropic_api_key")

    # is_anthropic_provider check
    from ai.providers import is_anthropic_provider
    check("is_anthropic_provider('anthropic') is True",
          is_anthropic_provider("anthropic") is True)
    check("is_anthropic_provider('cerebras') is False",
          is_anthropic_provider("cerebras") is False)
    check("is_anthropic_provider(ANTHROPIC_URL) is True",
          is_anthropic_provider("https://api.anthropic.com/v1/messages") is True)


# =============================================================================
# DeepSeekProvider unit tests
# =============================================================================

print("\n=== DeepSeekProvider unit tests ===")

if DEEPSEEK_AVAILABLE:
    # Missing key
    try:
        DeepSeekProvider("")
        check("DeepSeekProvider('') raises ValueError", False)
    except ValueError:
        check("DeepSeekProvider('') raises ValueError", True)

    # Valid construction
    dp = DeepSeekProvider("sk-deepseek-test-key")
    check("DeepSeekProvider('sk-deepseek-test-key') creates instance",
          isinstance(dp, BaseProvider))

    # headers()
    h = dp.headers()
    check("headers() returns dict", isinstance(h, dict))
    check("headers() has Authorization", "Authorization" in h)
    check("headers() has Bearer token",
          "Bearer sk-deepseek-test-key" in h.get("Authorization", ""))

    # model_info
    info = dp.model_info
    check("model_info['provider'] == 'deepseek'",
          info.get("provider") == "deepseek")
    check("model_info has 'model'", "model" in info)
    check("model_info has 'url'", "url" in info)
    check("model_info default model is deepseek-v4-flash",
          info.get("model") == "deepseek-v4-flash")

    # Custom model
    dp2 = DeepSeekProvider("sk-test", model="deepseek-chat")
    check("custom model in model_info", dp2.model_info["model"] == "deepseek-chat")

    # Abstract methods implemented
    check("has chat()", hasattr(dp, "chat") and callable(dp.chat))
    check("has chat_stream()", hasattr(dp, "chat_stream") and callable(dp.chat_stream))

    # key_ok with empty key
    result = DeepSeekProvider.key_ok("")
    check("key_ok('') returns False", result is False)

    # fetch_models with empty key
    try:
        DeepSeekProvider.fetch_models("")
        check("fetch_models('') raises ValueError", False)
    except ValueError:
        check("fetch_models('') raises ValueError", True)

    # Provider URL is correct
    from ai.providers.deepseek import DEEPSEEK_URL
    check("DEEPSEEK_URL is api.deepseek.com",
          "api.deepseek.com/v1/chat/completions" in DEEPSEEK_URL)


# =============================================================================
# GroqProvider unit tests
# =============================================================================

print("\n=== GroqProvider unit tests ===")

if GROQ_AVAILABLE:
    # Missing key
    try:
        GroqProvider("")
        check("GroqProvider('') raises ValueError", False)
    except ValueError:
        check("GroqProvider('') raises ValueError", True)

    # Valid construction
    gp = GroqProvider("gsk-test-key")
    check("GroqProvider('gsk-test-key') creates instance",
          isinstance(gp, BaseProvider))

    # headers()
    h = gp.headers()
    check("headers() returns dict", isinstance(h, dict))
    check("headers() has Authorization", "Authorization" in h)
    check("headers() has Bearer token",
          "Bearer gsk-test-key" in h.get("Authorization", ""))

    # model_info
    info = gp.model_info
    check("model_info['provider'] == 'groq'",
          info.get("provider") == "groq")
    check("model_info has 'model'", "model" in info)
    check("model_info has 'url'", "url" in info)
    check("model_info default model is llama-3.3-70b-versatile",
          info.get("model") == "llama-3.3-70b-versatile")

    # Custom model
    gp2 = GroqProvider("gsk-test", model="mixtral-8x7b-32768")
    check("custom model in model_info",
          gp2.model_info["model"] == "mixtral-8x7b-32768")

    # Abstract methods implemented
    check("has chat()", hasattr(gp, "chat") and callable(gp.chat))
    check("has chat_stream()", hasattr(gp, "chat_stream") and callable(gp.chat_stream))

    # key_ok with empty key
    result = GroqProvider.key_ok("")
    check("key_ok('') returns False", result is False)

    # fetch_models with empty key
    try:
        GroqProvider.fetch_models("")
        check("fetch_models('') raises ValueError", False)
    except ValueError:
        check("fetch_models('') raises ValueError", True)

    # Provider URL is correct
    from ai.providers.groq import GROQ_URL
    check("GROQ_URL is api.groq.com",
          "api.groq.com/openai/v1/chat/completions" in GROQ_URL)


# =============================================================================
# LocalProvider unit tests
# =============================================================================

print("\n=== LocalProvider unit tests ===")

if LOCAL_AVAILABLE:
    # Empty key is fine for local (no auth required by many servers)
    lp = LocalProvider("")
    check("LocalProvider('') creates instance (no key required)",
          isinstance(lp, BaseProvider))

    lp2 = LocalProvider("optional-local-key")
    check("LocalProvider with key creates instance",
          isinstance(lp2, BaseProvider))

    # headers() — without key: no Authorization header
    h = lp.headers()
    check("headers() without key has no Authorization",
          "Authorization" not in h)
    check("headers() without key has Content-Type",
          "Content-Type" in h)

    # headers() — with key: has Authorization
    h2 = lp2.headers()
    check("headers() with key has Authorization",
          "Authorization" in h2)
    check("headers() with key has Bearer token",
          "Bearer optional-local-key" in h2.get("Authorization", ""))

    # model_info
    info = lp.model_info
    check("model_info['provider'] == 'local'",
          info.get("provider") == "local")
    check("model_info has 'model'", "model" in info)
    check("model_info has 'url'", "url" in info)
    check("model_info default model is llama3",
          info.get("model") == "llama3")

    # Custom model
    lp3 = LocalProvider("", model="mistral")
    check("custom model in model_info", lp3.model_info["model"] == "mistral")

    # Custom base URL
    from ai.providers.local import _resolve_url
    url1 = _resolve_url("http://localhost:1234")
    check("_resolve_url appends /v1/chat/completions",
          url1 == "http://localhost:1234/v1/chat/completions")
    url2 = _resolve_url("http://localhost:1234/v1")
    check("_resolve_url with /v1 appends /chat/completions",
          url2 == "http://localhost:1234/v1/chat/completions")
    url3 = _resolve_url("http://localhost:1234/v1/chat/completions")
    check("_resolve_url full path is left as-is",
          url3 == "http://localhost:1234/v1/chat/completions")
    url4 = _resolve_url("")
    check("_resolve_url empty defaults to localhost:11434",
          "localhost:11434/v1/chat/completions" in url4)

    # Abstract methods implemented
    check("has chat()", hasattr(lp, "chat") and callable(lp.chat))
    check("has chat_stream()", hasattr(lp, "chat_stream") and callable(lp.chat_stream))

    # key_ok: VAL-PROV-017 — Local key validation skips HTTP and checks
    # reachability instead.  We test with localhost:11434 (Ollama default
    # port), which may or may not be running — the function must return a
    # tri-state (True/False/None) without raising an exception.
    result = LocalProvider.key_ok("", timeout=3)
    check("key_ok returns tri-state without crashing",
          result is True or result is False or result is None)
    print(f"    Local server reachable on localhost:11434: {result}")

    # With an obviously unreachable port, should return False
    unreachable = LocalProvider.key_ok(
        base_url="http://127.0.0.1:1", timeout=2
    )
    check("key_ok with unreachable port returns False",
          unreachable is False)
    print(f"    Unreachable port check: {unreachable}")

    # fetch_models: local servers don't expose a model list (VAL-PROV-017)
    try:
        LocalProvider.fetch_models("")
        check("fetch_models('') raises ValueError", False)
    except ValueError as e:
        check("fetch_models('') raises ValueError", True)
        # The message should tell the user to enter manually
        check("fetch_models error mentions manual entry",
              "manually" in str(e).lower()
              or "enter" in str(e).lower()
              or "model name" in str(e).lower())

    # Provider registry entries (VAL-PROV-016 — Local in PROVIDERS)
    from ai.providers import PROVIDERS
    check("local in ai.providers.PROVIDERS", "local" in PROVIDERS)
    check("local provider has key_setting 'local_api_key'",
          PROVIDERS.get("local", {}).get("key_setting") == "local_api_key")
    check("local provider has model_setting 'local_model'",
          PROVIDERS.get("local", {}).get("model_setting") == "local_model")

    # DeepSeek and Groq are in PROVIDERS but hidden from UI (benchmarking-only)
    check("deepseek in ai.providers.PROVIDERS", "deepseek" in PROVIDERS)
    check("groq in ai.providers.PROVIDERS", "groq" in PROVIDERS)


# =============================================================================
# Model isolation: VAL-PROV-023 — Each provider uses its own saved model
# =============================================================================

print("\n=== Model isolation (VAL-PROV-023) ===")

# Simulate what the controller does when resolving provider config.
# Each provider must use its own model_setting, not Cerebras's default.

# Build a mock settings dict
_mock_settings = {
    "cerebras_model": "gpt-oss-120b",
    "deepseek_model": "deepseek-v4-flash",
    "groq_model": "llama-3.3-70b-versatile",
    "openai_model": "gpt-5.4-mini",
    "anthropic_model": "claude-opus-4-8",
    "openrouter_model": "openai/gpt-5.4-mini",
    "local_model": "llama3",
}

from ai.providers import PROVIDERS

for pid in ["cerebras", "deepseek", "groq", "openai", "anthropic", "openrouter", "local"]:
    info = PROVIDERS.get(pid)
    check(f"PROVIDERS has entry for {pid}", info is not None)
    if info:
        # Each provider has its own model_setting key
        ms = info.get("model_setting")
        check(f"{pid} has model_setting", ms is not None and isinstance(ms, str))
        # Resolve the model: get from settings or fall back to default_model
        model = _mock_settings.get(ms, "") or info.get("default_model", "")
        check(f"{pid} model is not empty", bool(model))
        # No cross-contamination: Cerebras model is gpt-oss-120b,
        # DeepSeek is deepseek-v4-flash, etc.
        if pid == "cerebras":
            check(f"{pid} uses Cerebras model, not cross-contaminated",
                  model == "gpt-oss-120b")
        elif pid == "deepseek":
            check(f"{pid} uses DeepSeek model, not cross-contaminated",
                  model == "deepseek-v4-flash")
        elif pid == "groq":
            check(f"{pid} uses Groq model, not cross-contaminated",
                  model == "llama-3.3-70b-versatile")
        elif pid == "local":
            check(f"{pid} uses Local model, not cross-contaminated",
                  model == "llama3")

print("\n=== Reasoning effort scoping ===")

if CEREBRAS_AVAILABLE:
    from ai.providers.cerebras import _url_is_cerebras, CEREBRAS_URL as C_URL

    check("_url_is_cerebras(CEREBRAS_URL) is True", _url_is_cerebras(C_URL) is True)
    check("_url_is_cerebras(OPENROUTER_URL) is False",
          not _url_is_cerebras("https://openrouter.ai/api/v1/chat/completions"))
    check("_url_is_cerebras(OPENAI_URL) is False",
          not _url_is_cerebras("https://api.openai.com/v1/chat/completions"))


# =============================================================================
# Live API tests (require valid keys)
# =============================================================================

print("\n=== Live API tests ===")

_settings_path = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "Mumble", "settings.json"
)

_settings = {}
_allow_live = os.environ.get("MUMBLE_RUN_LIVE_TESTS") == "1"
if _allow_live and os.path.exists(_settings_path):
    try:
        with open(_settings_path, "r") as f:
            _settings = json.load(f)
    except Exception as e:
        print(f"  WARNING: Could not load settings: {e}")

CEREBRAS_KEY = (_settings.get("cerebras_api_key") or "").strip()
OPENROUTER_KEY = (_settings.get("openrouter_api_key") or "").strip()
OPENROUTER_MODEL = _settings.get("openrouter_model", "openai/gpt-5.4-mini")

# ---- Cerebras key_ok (live) --------------------------------------------------
if CEREBRAS_KEY and CEREBRAS_AVAILABLE:
    print(f"\n  Testing Cerebras key_ok with live key...")
    try:
        result = CerebrasProvider.key_ok(CEREBRAS_KEY, timeout=15)
        check(f"Cerebras key_ok with valid key returns True", result is True)
    except Exception as e:
        check(f"Cerebras key_ok with valid key", False)
        print(f"    Exception: {e}")

    # Also test via backward-compat ai.key_ok
    try:
        result = ai.key_ok(CEREBRAS_KEY, timeout=15, models_url=ai.CEREBRAS_MODELS_URL)
        check(f"ai.key_ok (Cerebras, valid) returns True", result is True)
    except Exception as e:
        check(f"ai.key_ok (Cerebras, valid)", False)
        print(f"    Exception: {e}")

    # Invalid key
    try:
        result = CerebrasProvider.key_ok("sk-deadbeef-invalid", timeout=15)
        check(f"Cerebras key_ok with invalid key returns False", result is False)
    except Exception as e:
        check(f"Cerebras key_ok with invalid key", False)
        print(f"    Exception: {e}")

    # fetch_models (live)
    print(f"\n  Testing Cerebras fetch_models...")
    try:
        models = CerebrasProvider.fetch_models(CEREBRAS_KEY, timeout=15)
        check(f"Cerebras fetch_models returns list", isinstance(models, list))
        check(f"Cerebras fetch_models has entries", len(models) > 0)
        if models:
            print(f"    Models ({len(models)}): {', '.join(models[:5])}...")
    except Exception as e:
        check(f"Cerebras fetch_models", False)
        print(f"    Exception: {e}")

    # ai.fetch_models (backward compat)
    try:
        models = ai.fetch_models("cerebras", CEREBRAS_KEY, timeout=15)
        check(f"ai.fetch_models('cerebras', ...) returns list", isinstance(models, list))
        check(f"ai.fetch_models('cerebras', ...) has entries", len(models) > 0)
    except Exception as e:
        check(f"ai.fetch_models('cerebras', ...)", False)
        print(f"    Exception: {e}")
else:
    print("\n  SKIP: No Cerebras API key found — live Cerebras tests skipped.")

# ---- OpenRouter key_ok (live) ------------------------------------------------
if OPENROUTER_KEY and OPENROUTER_AVAILABLE:
    print(f"\n  Testing OpenRouter key_ok with live key...")
    try:
        result = OpenRouterProvider.key_ok(OPENROUTER_KEY, timeout=15)
        check(f"OpenRouter key_ok with valid key returns True", result is True)
    except Exception as e:
        check(f"OpenRouter key_ok with valid key", False)
        print(f"    Exception: {e}")

    # Invalid key — Note: OpenRouter's /models endpoint is public (returns 200
    # even for invalid keys), so key_ok may return True or None.  Cerebras
    # correctly returns False for invalid keys.  Both are valid behavior.
    try:
        result = OpenRouterProvider.key_ok("sk-or-deadbeef-invalid", timeout=15)
        # OpenRouter /models is public — can't detect invalid keys via /models
        print(f"    (OpenRouter /models is public — invalid keys still return 200)")
        check(f"OpenRouter key_ok with invalid key does not crash", result is not None or True)
    except Exception as e:
        check(f"OpenRouter key_ok with invalid key", False)
        print(f"    Exception: {e}")

    # fetch_models (live)
    print(f"\n  Testing OpenRouter fetch_models...")
    try:
        models = OpenRouterProvider.fetch_models(OPENROUTER_KEY, timeout=15)
        check(f"OpenRouter fetch_models returns list", isinstance(models, list))
        check(f"OpenRouter fetch_models has entries", len(models) > 0)
        if models:
            print(f"    Models ({len(models)}): {', '.join(models[:5])}...")
    except Exception as e:
        check(f"OpenRouter fetch_models", False)
        print(f"    Exception: {e}")

    # get_credits (live)
    print(f"\n  Testing OpenRouter get_credits...")
    try:
        credits = OpenRouterProvider.get_credits(OPENROUTER_KEY, timeout=15)
        check(f"get_credits returns dict", isinstance(credits, dict))
        check(f"get_credits has 'total'", "total" in credits)
        check(f"get_credits has 'used'", "used" in credits)
        check(f"get_credits has 'remaining'", "remaining" in credits)
        check(f"get_credits remaining >= 0", credits.get("remaining", -1) >= 0)
        print(f"    Credits: total={credits.get('total')}, used={credits.get('used')}, "
              f"remaining={credits.get('remaining')}")
    except Exception as e:
        check(f"OpenRouter get_credits", False)
        print(f"    Exception: {e}")

    # ai.get_openrouter_credits (backward compat)
    try:
        credits = ai.get_openrouter_credits(OPENROUTER_KEY, timeout=15)
        check(f"ai.get_openrouter_credits returns dict", isinstance(credits, dict))
        check(f"ai.get_openrouter_credits has 'remaining'", "remaining" in credits)
    except Exception as e:
        check(f"ai.get_openrouter_credits", False)
        print(f"    Exception: {e}")
else:
    print("\n  SKIP: No OpenRouter API key found — live OpenRouter tests skipped.")

# ---- OpenAI key_ok (live) ----------------------------------------------------
OPENAI_KEY = (_settings.get("openai_api_key") or "").strip()
if OPENAI_KEY and OPENAI_AVAILABLE:
    print(f"\n  Testing OpenAI key_ok with live key...")
    try:
        result = OpenAIProvider.key_ok(OPENAI_KEY, timeout=15)
        check(f"OpenAI key_ok with valid key returns True", result is True)
    except Exception as e:
        check(f"OpenAI key_ok with valid key", False)
        print(f"    Exception: {e}")

    # Also test via backward-compat ai.key_ok
    try:
        result = ai.key_ok(OPENAI_KEY, timeout=15, models_url=ai.OPENAI_MODELS_URL)
        check(f"ai.key_ok (OpenAI, valid) returns True", result is True)
    except Exception as e:
        check(f"ai.key_ok (OpenAI, valid)", False)
        print(f"    Exception: {e}")

    # Invalid key
    try:
        result = OpenAIProvider.key_ok("sk-deadbeef-invalid", timeout=15)
        check(f"OpenAI key_ok with invalid key returns False", result is False)
    except Exception as e:
        check(f"OpenAI key_ok with invalid key", False)
        print(f"    Exception: {e}")

    # fetch_models (live)
    print(f"\n  Testing OpenAI fetch_models...")
    try:
        models = OpenAIProvider.fetch_models(OPENAI_KEY, timeout=15)
        check(f"OpenAI fetch_models returns list", isinstance(models, list))
        check(f"OpenAI fetch_models has entries", len(models) > 0)
        if models:
            print(f"    Models ({len(models)}): {', '.join(models[:5])}...")
    except Exception as e:
        check(f"OpenAI fetch_models", False)
        print(f"    Exception: {e}")
else:
    print("\n  SKIP: No OpenAI API key found — live OpenAI tests skipped.")

# ---- Anthropic key_ok (live) ------------------------------------------------
ANTHROPIC_KEY = (_settings.get("anthropic_api_key") or "").strip()
if ANTHROPIC_KEY and ANTHROPIC_AVAILABLE:
    print(f"\n  Testing Anthropic key_ok with live key...")
    try:
        result = AnthropicProvider.key_ok(ANTHROPIC_KEY, timeout=15)
        check(f"Anthropic key_ok with valid key returns True", result is True)
    except Exception as e:
        check(f"Anthropic key_ok with valid key", False)
        print(f"    Exception: {e}")

    # Also test via backward-compat ai.key_ok
    try:
        result = ai.key_ok(ANTHROPIC_KEY, timeout=15, models_url=ai.ANTHROPIC_MODELS_URL)
        check(f"ai.key_ok (Anthropic, valid) returns True", result is True)
    except Exception as e:
        check(f"ai.key_ok (Anthropic, valid)", False)
        print(f"    Exception: {e}")

    # Invalid key — Anthropic /models should reject with 401
    try:
        result = AnthropicProvider.key_ok("sk-ant-deadbeef-invalid", timeout=15)
        check(f"Anthropic key_ok with invalid key returns False", result is False)
    except Exception as e:
        check(f"Anthropic key_ok with invalid key", False)
        print(f"    Exception: {e}")

    # fetch_models (live)
    print(f"\n  Testing Anthropic fetch_models...")
    try:
        models = AnthropicProvider.fetch_models(ANTHROPIC_KEY, timeout=15)
        check(f"Anthropic fetch_models returns list", isinstance(models, list))
        check(f"Anthropic fetch_models has entries", len(models) > 0)
        if models:
            print(f"    Models ({len(models)}): {', '.join(models[:5])}...")
    except Exception as e:
        check(f"Anthropic fetch_models", False)
        print(f"    Exception: {e}")
else:
    print("\n  SKIP: No Anthropic API key found — live Anthropic tests skipped.")


# =============================================================================
# Summary
# =============================================================================

print(f"\n{'=' * 60}")
print(f"RESULTS: {len(TESTS) - len(FAILURES)}/{len(TESTS)} passed")
if FAILURES:
    print(f"FAILURES:")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("All tests passed.")
    sys.exit(0)
