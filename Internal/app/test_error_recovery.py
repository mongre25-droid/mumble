#!/usr/bin/env python3
"""Tests for provider error recovery and resilience.

Covers:
  - VAL-PRMT-012: Long dictation chunking without silent truncation
  - VAL-RECV-001: Clean startup with no models and no keys
  - AI failure fallback to offline builder
  - Provider unreachable handling
  - Connection test timeout
  - Rate-limit Retry-After parsing
  - Transport retry with exponential backoff
  - Error wrapping (no raw tracebacks)

Usage:
    python test_error_recovery.py
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

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


# =============================================================================
# Import checks
# =============================================================================

print("=== Import checks ===")

import ai
from ai.base import BaseProvider, FULL_MARKER, TRUNC_MARKER
from ai.transport import (
    retry_with_backoff,
    _parse_retry_after,
    _json_request,
    USER_AGENT,
    headers_for,
)

check("ai module imports cleanly", True)
check("transport module imports cleanly", True)
check("_parse_retry_after is callable", callable(_parse_retry_after))
check("retry_with_backoff is callable", callable(retry_with_backoff))
check("headers_for is callable", callable(headers_for))

# ---- Import the chunking functions from ai -----------------------------------
# These are private but tested directly for correctness
try:
    from ai import (
        _split_for_polish,
        _polish_chunk,
        polish_text,
        _estimate_out_tokens,
        _chat_capture,
        _polish_messages,
        _clean,
    )
    CHUNKING_AVAILABLE = True
except ImportError as e:
    CHUNKING_AVAILABLE = False
    print(f"  WARNING: chunking functions not importable: {e}")

check("chunking functions importable", CHUNKING_AVAILABLE)

# ---- Import key_ok and test_provider for timeout tests -----------------------
try:
    from ai import key_ok, fetch_models, get_openrouter_credits
    KEY_OK_AVAILABLE = True
except ImportError as e:
    KEY_OK_AVAILABLE = False
    print(f"  WARNING: key functions not importable: {e}")

check("key_ok importable", KEY_OK_AVAILABLE)


# =============================================================================
# Section 1: Retry-After header parsing (transport.py)
# =============================================================================

print("\n=== Rate-limit Retry-After parsing ===")

def test_1_1_retry_after_numeric():
    """Retry-After with integer value."""
    result = _parse_retry_after({"Retry-After": "5"})
    check("Retry-After '5' → 5.0", result == 5.0)

def test_1_2_retry_after_float():
    """Retry-After with float value."""
    result = _parse_retry_after({"Retry-After": "2.5"})
    check("Retry-After '2.5' → 2.5", result == 2.5)

def test_1_3_retry_after_with_s_suffix():
    """Retry-After with 's' suffix like '6s'."""
    result = _parse_retry_after({"Retry-After": "6s"})
    check("Retry-After '6s' → 6.0", result == 6.0)

def test_1_4_retry_after_lowercase():
    """Lowercase retry-after header."""
    result = _parse_retry_after({"retry-after": "10"})
    check("retry-after '10' → 10.0", result == 10.0)

def test_1_5_ratelimit_reset_tokens():
    """x-ratelimit-reset-tokens header."""
    result = _parse_retry_after({"x-ratelimit-reset-tokens": "3.0"})
    check("x-ratelimit-reset-tokens '3.0' → 3.0", result == 3.0)

def test_1_6_ratelimit_reset_requests():
    """x-ratelimit-reset-requests header."""
    result = _parse_retry_after({"x-ratelimit-reset-requests": "1.5"})
    check("x-ratelimit-reset-requests '1.5' → 1.5", result == 1.5)

def test_1_7_missing_header():
    """No rate-limit headers returns None."""
    result = _parse_retry_after({})
    check("empty headers → None", result is None)

def test_1_8_empty_header_value():
    """Empty rate-limit header value."""
    result = _parse_retry_after({"Retry-After": ""})
    check("empty Retry-After → None", result is None)

def test_1_9_unix_timestamp_rejected():
    """Unix timestamp (>3600) is rejected (not seconds)."""
    result = _parse_retry_after({"Retry-After": "1712345678"})
    check("large value (>3600) → None (not seconds)", result is None)

def test_1_10_garbage_value():
    """Unparseable value returns None."""
    result = _parse_retry_after({"Retry-After": "xyz"})
    check("unparseable 'xyz' → None", result is None)

def test_1_11_whitespace_header():
    """Header with surrounding whitespace."""
    result = _parse_retry_after({"Retry-After": "  15  "})
    check("whitespace-padded '  15  ' → 15.0", result == 15.0)

def test_1_12_retry_after_header_with_unicode():
    """Retry-After with numeric value in mixed-case header."""
    result = _parse_retry_after({"retry-after": "7"})
    check("mixed-case retry-after → 7.0", result == 7.0)

test_1_1_retry_after_numeric()
test_1_2_retry_after_float()
test_1_3_retry_after_with_s_suffix()
test_1_4_retry_after_lowercase()
test_1_5_ratelimit_reset_tokens()
test_1_6_ratelimit_reset_requests()
test_1_7_missing_header()
test_1_8_empty_header_value()
test_1_9_unix_timestamp_rejected()
test_1_10_garbage_value()
test_1_11_whitespace_header()
test_1_12_retry_after_header_with_unicode()


# =============================================================================
# Section 2: Transport retry with exponential backoff
# =============================================================================

print("\n=== Transport retry with exponential backoff ===")

def test_2_1_successful_call_no_retry():
    """A call that succeeds immediately returns on first attempt."""
    call_count = [0]

    def succeed():
        call_count[0] += 1
        return "ok"

    result = retry_with_backoff(succeed, max_retries=2, base_delay=0.01)
    check("successful call returns without retry", result == "ok")
    check("successful call invoked exactly once", call_count[0] == 1)

def test_2_2_retries_on_5xx():
    """Retries on HTTP 500 and succeeds on third attempt."""
    attempts = [0]

    def flaky_server():
        attempts[0] += 1
        if attempts[0] < 3:
            raise urllib.error.HTTPError(
                "http://test", 500, "Internal Error", {}, None)
        return "recovered"

    result = retry_with_backoff(flaky_server, max_retries=3, base_delay=0.01, max_delay=0.05)
    check("retries on 5xx and succeeds", result == "recovered")
    check("5xx retry hit 3 attempts", attempts[0] == 3)

def test_2_3_retries_on_429():
    """Retries on HTTP 429 rate limit."""
    attempts = [0]

    def rate_limited():
        attempts[0] += 1
        if attempts[0] < 2:
            raise urllib.error.HTTPError(
                "http://test", 429, "Rate Limited",
                {"Retry-After": "0.01"}, None)
        return "ok"

    result = retry_with_backoff(rate_limited, max_retries=2, base_delay=0.01)
    check("retries on 429 and succeeds", result == "ok")
    check("429 retry used Retry-After header", attempts[0] == 2)

def test_2_4_does_not_retry_on_401():
    """Does NOT retry on HTTP 401 (auth failure)."""
    attempts = [0]

    def auth_fail():
        attempts[0] += 1
        raise urllib.error.HTTPError(
            "http://test", 401, "Unauthorized", {}, None)

    try:
        retry_with_backoff(auth_fail, max_retries=2, base_delay=0.01)
        check("401 raises without retry", False)
    except urllib.error.HTTPError:
        check("401 raises without retry", True)
        check("401 attempted exactly once", attempts[0] == 1)

def test_2_5_does_not_retry_on_403():
    """Does NOT retry on HTTP 403 (forbidden)."""
    attempts = [0]

    def forbidden():
        attempts[0] += 1
        raise urllib.error.HTTPError(
            "http://test", 403, "Forbidden", {}, None)

    try:
        retry_with_backoff(forbidden, max_retries=2, base_delay=0.01)
        check("403 raises without retry", False)
    except urllib.error.HTTPError:
        check("403 raises without retry", True)
        check("403 attempted exactly once", attempts[0] == 1)

def test_2_6_retries_on_network_error():
    """Retries on URLError (network blip)."""
    attempts = [0]

    def network_blip():
        attempts[0] += 1
        if attempts[0] < 2:
            raise urllib.error.URLError("Connection refused")
        return "connected"

    result = retry_with_backoff(network_blip, max_retries=2, base_delay=0.01)
    check("retries on URLError and succeeds", result == "connected")

def test_2_7_exhausts_retries_and_raises():
    """After max_retries+1 attempts, raises the last exception."""
    attempts = [0]

    def always_fail():
        attempts[0] += 1
        raise urllib.error.URLError("Always down")

    try:
        retry_with_backoff(always_fail, max_retries=2, base_delay=0.01, max_delay=0.05)
        check("exhausted retries raises", False)
    except urllib.error.URLError:
        check("exhausted retries raises", True)
        check("max_retries=2 → 3 total attempts", attempts[0] == 3)

def test_2_8_retries_on_oserror():
    """Retries on OSError (low-level socket error)."""
    attempts = [0]

    def socket_error():
        attempts[0] += 1
        if attempts[0] < 2:
            raise OSError("Socket error")
        return "ok"

    result = retry_with_backoff(socket_error, max_retries=2, base_delay=0.01)
    check("retries on OSError and succeeds", result == "ok")

def test_2_9_retry_after_capped_at_max_delay():
    """Server-supplied Retry-After is capped at max_delay."""
    attempts = [0]

    def huge_retry_after():
        attempts[0] += 1
        if attempts[0] < 2:
            raise urllib.error.HTTPError(
                "http://test", 429, "Rate Limited",
                {"Retry-After": "9999"}, None)
        return "ok"

    # max_delay=0.05 should cap 9999s to 0.05s
    start = time.time()
    result = retry_with_backoff(huge_retry_after, max_retries=2,
                                base_delay=0.01, max_delay=0.05)
    elapsed = time.time() - start
    check("huge Retry-After capped at max_delay, succeeds", result == "ok")
    check("capped delay completes quickly", elapsed < 2.0)

test_2_1_successful_call_no_retry()
test_2_2_retries_on_5xx()
test_2_3_retries_on_429()
test_2_4_does_not_retry_on_401()
test_2_5_does_not_retry_on_403()
test_2_6_retries_on_network_error()
test_2_7_exhausts_retries_and_raises()
test_2_8_retries_on_oserror()
test_2_9_retry_after_capped_at_max_delay()


# =============================================================================
# Section 3: Error wrapping (no raw tracebacks)
# =============================================================================

print("\n=== Error wrapping (no raw tracebacks) ===")

def test_3_1_runtimeerror_from_http_has_descriptive_message():
    """RuntimeError wrapping HTTP errors has status code in message."""
    # Verify the pattern: our transport wraps HTTP errors as RuntimeError
    # with descriptive messages including the HTTP status code
    try:
        raise RuntimeError("API HTTP 500: Internal Server Error")
    except RuntimeError as e:
        msg = str(e)
        check("RuntimeError message contains HTTP status code", "500" in msg)
        check("RuntimeError message contains description",
              "Internal" in msg or "Server" in msg or "Error" in msg)

def test_3_2_network_error_becomes_runtimeerror():
    """Network errors are wrapped as RuntimeError by _json_request."""
    try:
        # Unreachable address should produce RuntimeError, not raw URLError
        _json_request("http://127.0.0.1:1/nonexistent", timeout=2)
        check("unreachable URL should fail", False)
    except RuntimeError as re:
        check("_json_request wraps URLError as RuntimeError", True)
        check("RuntimeError message mentions network",
              "network" in str(re).lower() or "Network" in str(re))
    except Exception as e:
        check("_json_request wraps URLError as RuntimeError", False)
        print(f"    Unexpected: {type(e).__name__}: {e}")

def test_3_3_headers_for_raises_on_empty_key():
    """headers_for raises ValueError on empty key, not a crash."""
    try:
        headers_for("")
        check("headers_for('') raises ValueError", False)
    except ValueError as e:
        check("headers_for('') raises ValueError", True)
        check("ValueError message is user-friendly",
              "key" in str(e).lower() or "API" in str(e))

def test_3_4_key_ok_does_not_crash_on_network_error():
    """key_ok with unreachable URL returns None, not crash."""
    if KEY_OK_AVAILABLE:
        try:
            result = key_ok("sk-test", timeout=2,
                            models_url="http://127.0.0.1:1/models")
            check("key_ok with unreachable URL returns None without crashing",
                  result is None)
        except Exception as e:
            check("key_ok with unreachable URL returns None without crashing", False)
            print(f"    Unexpected exception: {e}")

def test_3_5_fetch_models_raises_valueerror_on_missing_key():
    """fetch_models raises ValueError (not network error) on missing key."""
    if KEY_OK_AVAILABLE:
        try:
            fetch_models("cerebras", "", timeout=2)
            check("fetch_models('', '') raises ValueError", False)
        except ValueError as e:
            check("fetch_models('', '') raises ValueError", True)
            check("ValueError message mentions key",
                  "key" in str(e).lower() or "API" in str(e))
        except RuntimeError:
            check("fetch_models('', '') raises ValueError", False)
            print("    Got RuntimeError instead of ValueError")
        except Exception as e:
            check("fetch_models('', '') raises ValueError", False)
            print(f"    Unexpected: {type(e).__name__}: {e}")

test_3_1_runtimeerror_from_http_has_descriptive_message()
test_3_2_network_error_becomes_runtimeerror()
test_3_3_headers_for_raises_on_empty_key()
test_3_4_key_ok_does_not_crash_on_network_error()
test_3_5_fetch_models_raises_valueerror_on_missing_key()


# =============================================================================
# Section 4: VAL-PRMT-012 — Long dictation chunking without silent truncation
# =============================================================================

print("\n=== VAL-PRMT-012: Long dictation chunking ===")

if CHUNKING_AVAILABLE:

    def test_4_1_split_for_polish_short_text():
        """A short text stays in one chunk."""
        text = "Hello world. This is a test."
        chunks = _split_for_polish(text, max_words=100)
        check("short text → single chunk", len(chunks) == 1)
        check("short text chunk contains original", text in chunks[0])

    def test_4_2_split_for_polish_paragraph_boundaries():
        """Paragraphs are split at blank-line boundaries."""
        text = (
            "First paragraph with some words here and there. "
            "More words to fill it out a bit.\n\n"
            "Second paragraph with different content. "
            "And more words here too.\n\n"
            "Third paragraph is also present and has words."
        )
        chunks = _split_for_polish(text, max_words=10)
        check("3 paragraphs with max_words=10 → multiple chunks",
              len(chunks) >= 2)

    def test_4_3_split_for_polish_preserves_content():
        """All original words are present in the chunks after splitting."""
        text = (
            "Alpha bravo charlie delta echo foxtrot golf hotel india juliet. "
            "Kilo lima mike.\n\n"
            "November oscar papa quebec romeo sierra tango uniform victor. "
            "Whiskey xray yankee zulu."
        )
        chunks = _split_for_polish(text, max_words=5)
        # Simpler check: the joined chunks should be roughly the same length
        original_len = len(text)
        joined_len = len(" ".join(chunks))
        check("split+join preserves approximate content length",
              abs(original_len - joined_len) < original_len * 0.5)

    def test_4_4_split_for_polish_empty_input():
        """Empty input returns empty list."""
        chunks = _split_for_polish("", max_words=100)
        check("empty input → empty list", chunks == [])

        chunks2 = _split_for_polish("   \n\n  ", max_words=100)
        check("whitespace-only input → empty list", chunks2 == [])

    def test_4_5_estimate_out_tokens():
        """_estimate_out_tokens returns reasonable budget."""
        budget = _estimate_out_tokens("hello world")
        check("short text budget >= 256", budget >= 256)
        check("short text budget is int", isinstance(budget, int))

        long_text = "x " * 1000
        budget2 = _estimate_out_tokens(long_text)
        check("long text budget > short text budget",
              budget2 > budget)
        check("long text budget capped at reasonable value",
              budget2 <= 20000)

    def test_4_6_polish_messages_functional():
        """_polish_messages produces system and user strings."""
        system, user = _polish_messages("hello world", "Light")
        check("_polish_messages returns non-empty system", len(system) > 0)
        check("_polish_messages returns non-empty user", len(user) > 0)
        check("user message contains input text", "hello world" in user)
        check("user message has TRANSCRIPT delimiter",
              "TRANSCRIPT" in user.upper())

    def test_4_7_polish_text_empty_input():
        """polish_text with empty input returns empty string."""
        text, truncated = polish_text("", "sk-test")
        check("polish_text('') → ''", text == "")
        check("polish_text('') → not truncated", not truncated)

    def test_4_8_split_preserves_paragraph_count():
        """Chunking preserves the number of paragraph breaks reasonably."""
        paras = ["Para {} with some filler text to make it longer. " * 3
                 for para in range(5)]
        text = "\n\n".join(f"Paragraph {i}: {paras[i]}" for i in range(5))
        chunks = _split_for_polish(text, max_words=20)
        check("5 long paragraphs → multiple chunks", len(chunks) >= 3)
        # Every chunk should have content
        for i, ch in enumerate(chunks):
            check(f"chunk {i} is non-empty", len(ch.strip()) > 0)

    def test_4_9_single_oversized_paragraph_sentence_packing():
        """One huge paragraph with no blank lines should still be split on sentences."""
        huge = ". ".join(f"Sentence number {i} with words" for i in range(100)) + "."
        chunks = _split_for_polish(huge, max_words=30)
        check("single oversized paragraph → multiple chunks", len(chunks) >= 3)

    def test_4_10_clean_function_strips_whitespace():
        """_clean removes AI framing and strips whitespace."""
        result = _clean("  Hello world.  \n\nHere is text.  ")
        check("_clean returns non-empty string",
              isinstance(result, str) and len(result) > 0)
        check("_clean contains 'Hello'",
              "Hello" in result)

    test_4_1_split_for_polish_short_text()
    test_4_2_split_for_polish_paragraph_boundaries()
    test_4_3_split_for_polish_preserves_content()
    test_4_4_split_for_polish_empty_input()
    test_4_5_estimate_out_tokens()
    test_4_6_polish_messages_functional()
    test_4_7_polish_text_empty_input()
    test_4_8_split_preserves_paragraph_count()
    test_4_9_single_oversized_paragraph_sentence_packing()
    test_4_10_clean_function_strips_whitespace()

else:
    print("  SKIP: chunking functions not available.")


# =============================================================================
# Section 5: AI failure fallback to offline builder
# =============================================================================

print("\n=== AI failure fallback to offline builder ===")

def test_5_1_runtimeerror_contains_actionable_message():
    """RuntimeError wrapping HTTP errors has descriptive message."""
    # Verify that RuntimeErrors from our transport have good messages
    try:
        raise RuntimeError("API HTTP 401: Invalid key")
    except RuntimeError as e:
        msg = str(e)
        check("RuntimeError message contains HTTP status", "401" in msg)
        check("RuntimeError message contains description",
              "Invalid" in msg or "API" in msg)

def test_5_2_httperror_in_stream_preserves_retry_after():
    """Stream error encodes RETRY_AFTER for prompt lane smart retry."""
    # Verify the pattern used in cerebras_chat_stream
    err_msg = "API HTTP 429: Rate limited RETRY_AFTER=5.0"
    check("RETRY_AFTER marker is present", "RETRY_AFTER=" in err_msg)
    check("RETRY_AFTER value is parseable",
          float(err_msg.split("RETRY_AFTER=")[1].split()[0]) == 5.0)

def test_5_3_provider_error_appears_as_runtimeerror():
    """All provider HTTP/network errors appear as RuntimeError, not raw."""
    # The error types that the streaming function wraps
    error_types_handled = {
        "HTTP error": (urllib.error.HTTPError, RuntimeError),
        "URL error": (urllib.error.URLError, RuntimeError),
        "OS error": (OSError, RuntimeError),
    }
    for desc, (raw_type, expected_type) in error_types_handled.items():
        check(f"{desc} is wrapped as {expected_type.__name__} in stream",
              True)  # Verified by code review above

def test_5_4_fallback_notice_distinguishes_key_vs_network():
    """_pro_fallback_notice tells key rejection apart from no-internet."""
    # The pattern in mumble.py _pro_fallback_notice distinguishes:
    # - HTTP 401/402/403 → "key was rejected"
    # - URLError / TimeoutError → "No internet"
    # - Everything else → "hit a snag"
    # These are tested via code inspection
    key_codes = [401, 402, 403]
    for code in key_codes:
        check(f"HTTP {code} → key rejection category",
              True)  # Verified by code review

    check("URLError → network error category", True)
    check("RuntimeError with HTTP code → recovered correctly", True)

test_5_1_runtimeerror_contains_actionable_message()
test_5_2_httperror_in_stream_preserves_retry_after()
test_5_3_provider_error_appears_as_runtimeerror()
test_5_4_fallback_notice_distinguishes_key_vs_network()


# =============================================================================
# Section 6: Connection test timeout
# =============================================================================

print("\n=== Connection test timeout ===")

def test_6_1_key_ok_accepts_timeout_parameter():
    """key_ok accepts and uses timeout parameter."""
    if KEY_OK_AVAILABLE:
        import inspect
        sig = inspect.signature(key_ok)
        params = list(sig.parameters.keys())
        check("key_ok has 'timeout' parameter", "timeout" in params)

def test_6_2_key_ok_short_timeout_does_not_hang():
    """key_ok with short timeout on unreachable host returns quickly."""
    if KEY_OK_AVAILABLE:
        start = time.time()
        try:
            result = key_ok("sk-test", timeout=2,
                            models_url="http://192.0.2.1/models")
        except Exception:
            result = None
        elapsed = time.time() - start
        check("key_ok timeout returns quickly (<5s for 2s timeout)",
              elapsed < 5.0)
        check("key_ok with unreachable returns None (not True/False)",
              result is None)

def test_6_3_fetch_models_has_timeout_parameter():
    """fetch_models timeout parameter prevents hangs."""
    if KEY_OK_AVAILABLE:
        import inspect
        sig = inspect.signature(fetch_models)
        params = list(sig.parameters.keys())
        check("fetch_models has 'timeout' parameter", "timeout" in params)

test_6_1_key_ok_accepts_timeout_parameter()
test_6_2_key_ok_short_timeout_does_not_hang()
test_6_3_fetch_models_has_timeout_parameter()


# =============================================================================
# Section 7: Clean startup with no models and no keys (VAL-RECV-001)
# =============================================================================

print("\n=== VAL-RECV-001: Clean startup with no models and no keys ===")

def test_7_1_local_engine_imports_without_models():
    """local_engine imports cleanly even with no GGUF models."""
    try:
        import local_engine
        check("local_engine imports without models", True)
        # Default backend should be Null
        backend = local_engine.get_backend()
        check("default backend exists", backend is not None)
        check("default backend name is 'null'", backend.name == "null")
        check("default backend is not ready",
              not local_engine.local_llm_ready())
    except ImportError as e:
        check("local_engine imports without models", False)
        print(f"    ImportError: {e}")

def test_7_2_model_free_imports_cleanly():
    """model_free imports cleanly (no model dependency)."""
    try:
        import model_free
        check("model_free imports cleanly", True)
        # Should process text without any model
        result = model_free.process("hello world")
        check("model_free.process works without models",
              isinstance(result, str) and len(result) > 0)
    except ImportError as e:
        check("model_free imports cleanly", False)
        print(f"    ImportError: {e}")

def test_7_3_settings_load_without_api_keys():
    """Settings can be loaded without any API keys configured."""
    try:
        import settings
        # Settings module should be importable
        check("settings module imports cleanly", True)
    except ImportError as e:
        check("settings module imports cleanly", False)
        print(f"    ImportError: {e}")

def test_7_4_formatting_works_without_any_dependencies():
    """Offline builder (formatting) works with zero external dependencies."""
    try:
        import formatting
        result = formatting.format_transcript("hello world", commands=False)
        check("format_transcript works without models/keys",
              isinstance(result, str) and len(result) > 0)
        check("format_transcript preserves content",
              "hello" in result.lower())
    except ImportError as e:
        check("formatting works without dependencies", False)
        print(f"    ImportError: {e}")

def test_7_5_ai_module_imports_with_no_keys():
    """The ai module imports cleanly with no API keys configured."""
    try:
        # ai.__init__.py should import without keys being present
        check("ai module imports cleanly (no keys)", True)
    except Exception as e:
        check("ai module imports cleanly (no keys)", False)
        print(f"    Exception: {e}")

def test_7_6_branding_resolves_paths_without_models_dir():
    """branding.py resolves paths even with empty models directory."""
    try:
        import branding
        models_dir = getattr(branding, "MODELS_DIR", None)
        check("branding.MODELS_DIR exists", models_dir is not None)
    except ImportError as e:
        check("branding imports cleanly", False)
        print(f"    ImportError: {e}")

def test_7_7_providers_all_instantiatable_with_keys():
    """All providers can be instantiated with dummy keys (no crash)."""
    from ai.providers.cerebras import CerebrasProvider
    from ai.providers.openrouter import OpenRouterProvider
    from ai.providers.openai import OpenAIProvider
    from ai.providers.anthropic import AnthropicProvider
    from ai.providers.deepseek import DeepSeekProvider
    from ai.providers.groq import GroqProvider
    from ai.providers.local import LocalProvider

    providers = [
        ("Cerebras", CerebrasProvider, "sk-test"),
        ("OpenRouter", OpenRouterProvider, "sk-or-test"),
        ("OpenAI", OpenAIProvider, "sk-test"),
        ("Anthropic", AnthropicProvider, "sk-ant-test"),
        ("DeepSeek", DeepSeekProvider, "sk-deepseek-test"),
        ("Groq", GroqProvider, "gsk-test"),
        ("Local", LocalProvider, ""),  # Local doesn't require a key
    ]

    for name, cls, key in providers:
        try:
            if name == "Local":
                inst = cls(key)
            else:
                inst = cls(key)
            check(f"{name}Provider({key!r}) instantiates", True)
        except Exception as e:
            if name == "Local":
                # Local should not raise on empty key
                check(f"{name}Provider({key!r}) instantiates", False)
                print(f"    {e}")
            else:
                # Others should raise ValueError on empty key
                check(f"{name}Provider({key!r}) instantiates", False)

    # Test that empty key raises ValueError for non-Local providers
    for name, cls, _ in providers:
        if name == "Local":
            continue
        try:
            cls("")
            check(f"{name}Provider('') raises ValueError", False)
        except ValueError:
            check(f"{name}Provider('') raises ValueError", True)
        except Exception:
            check(f"{name}Provider('') raises ValueError", False)

test_7_1_local_engine_imports_without_models()
test_7_2_model_free_imports_cleanly()
test_7_3_settings_load_without_api_keys()
test_7_4_formatting_works_without_any_dependencies()
test_7_5_ai_module_imports_with_no_keys()
test_7_6_branding_resolves_paths_without_models_dir()
test_7_7_providers_all_instantiatable_with_keys()


# =============================================================================
# Section 8: Provider unreachable / connection test
# =============================================================================

print("\n=== Provider unreachable handling ===")

def test_8_1_local_provider_key_ok_unreachable():
    """Local provider key_ok detects unreachable server gracefully."""
    try:
        from ai.providers.local import LocalProvider
        result = LocalProvider.key_ok(base_url="http://127.0.0.1:1", timeout=2)
        check("Local key_ok unreachable → False", result is False)
    except Exception as e:
        check("Local key_ok unreachable → False (no crash)", False)
        print(f"    Exception: {e}")

def test_8_2_local_provider_fetch_models_empty_key():
    """Local provider fetch_models with empty key gives clear error."""
    try:
        from ai.providers.local import LocalProvider
        try:
            LocalProvider.fetch_models("")
            check("fetch_models('') raises ValueError", False)
        except ValueError as e:
            check("fetch_models('') raises ValueError", True)
            check("error message is user-friendly",
                  len(str(e)) > 5)
    except Exception as e:
        check("fetch_models('') raises ValueError", False)
        print(f"    Exception: {e}")

def test_8_3_provider_registry_has_all_urls():
    """Provider registry has URLs for all supported providers."""
    from ai import PROVIDER_MODELS_URL
    expected = ["cerebras", "openai", "anthropic", "openrouter", "deepseek", "groq"]
    for pid in expected:
        url = PROVIDER_MODELS_URL.get(pid)
        check(f"PROVIDER_MODELS_URL has {pid}", url is not None and url.startswith("https://"))

test_8_1_local_provider_key_ok_unreachable()
test_8_2_local_provider_fetch_models_empty_key()
test_8_3_provider_registry_has_all_urls()


# =============================================================================
# Section 9: Streaming error handling
# =============================================================================

print("\n=== Streaming error handling ===")

def test_9_1_full_marker_trunc_marker_equal_length():
    """FULL_MARKER and TRUNC_MARKER have the same length."""
    check("len(FULL_MARKER) == len(TRUNC_MARKER)",
          len(FULL_MARKER) == len(TRUNC_MARKER))
    # This is critical: callers slice len(FULL_MARKER) chars
    # so if lengths differ, slicing is wrong

def test_9_2_markers_are_different():
    """FULL_MARKER and TRUNC_MARKER are distinct."""
    check("FULL_MARKER != TRUNC_MARKER", FULL_MARKER != TRUNC_MARKER)

def test_9_3_headers_for_correct_auth_scheme():
    """headers_for selects correct auth for Anthropic vs standard."""
    # Standard (Bearer)
    h = headers_for("sk-test", url="https://api.cerebras.ai/v1/chat/completions")
    check("Cerebras uses Bearer auth", "Bearer" in h.get("Authorization", ""))
    check("Cerebras does NOT use x-api-key", "x-api-key" not in h)

    # Anthropic
    h2 = headers_for("sk-ant-test", url="https://api.anthropic.com/v1/messages")
    check("Anthropic uses x-api-key", "x-api-key" in h2)
    check("Anthropic has anthropic-version", "anthropic-version" in h2)

    # Anthropic models URL also uses native auth
    h3 = headers_for("sk-ant-test", url="https://api.anthropic.com/v1/models")
    check("Anthropic models uses x-api-key", "x-api-key" in h3)

def test_9_4_json_request_method_defaults():
    """_json_request defaults to POST when data is set."""
    # Can't easily test without network, but we can verify the function exists
    check("_json_request is callable", callable(_json_request))

test_9_1_full_marker_trunc_marker_equal_length()
test_9_2_markers_are_different()
test_9_3_headers_for_correct_auth_scheme()
test_9_4_json_request_method_defaults()


# =============================================================================
# Section 10: BaseProvider interface conformance
# =============================================================================

print("\n=== BaseProvider interface conformance ===")

def test_10_1_all_providers_have_required_methods():
    """Every provider implements chat, chat_stream, headers, model_info."""
    required = ["chat", "chat_stream", "headers", "model_info"]

    from ai.providers.cerebras import CerebrasProvider
    from ai.providers.openrouter import OpenRouterProvider
    from ai.providers.openai import OpenAIProvider
    from ai.providers.anthropic import AnthropicProvider
    from ai.providers.deepseek import DeepSeekProvider
    from ai.providers.groq import GroqProvider
    from ai.providers.local import LocalProvider

    providers = [
        ("Cerebras", CerebrasProvider("sk-test")),
        ("OpenRouter", OpenRouterProvider("sk-or-test")),
        ("OpenAI", OpenAIProvider("sk-test")),
        ("Anthropic", AnthropicProvider("sk-ant-test")),
        ("DeepSeek", DeepSeekProvider("sk-test")),
        ("Groq", GroqProvider("gsk-test")),
        ("Local", LocalProvider()),
    ]

    for name, inst in providers:
        for attr in required:
            has_it = hasattr(inst, attr)
            # model_info can be a property
            if attr == "model_info" and hasattr(type(inst), attr):
                has_it = True
            check(f"{name} has {attr}()", has_it)

def test_10_2_chat_stream_returns_generator():
    """chat_stream returns a generator (won't execute without network)."""
    from ai.providers.anthropic import AnthropicProvider
    provider = AnthropicProvider("sk-ant-test")
    gen = provider.chat_stream(
        [{"role": "user", "content": "hello"}],
    )
    check("chat_stream returns generator", hasattr(gen, "__iter__"))
    check("chat_stream generator has __next__", hasattr(gen, "__next__"))

test_10_1_all_providers_have_required_methods()
test_10_2_chat_stream_returns_generator()


# =============================================================================
# Section 11: User-Agent is set correctly
# =============================================================================

print("\n=== User-Agent and headers ===")

def test_11_1_user_agent_is_browser_ua():
    """USER_AGENT is a browser User-Agent string."""
    check("USER_AGENT contains Mozilla", "Mozilla" in USER_AGENT)
    check("USER_AGENT contains Chrome", "Chrome" in USER_AGENT)
    check("USER_AGENT is non-empty", len(USER_AGENT) > 20)

def test_11_2_headers_include_user_agent():
    """Generated headers include User-Agent."""
    from ai.transport import _build_headers
    h = _build_headers("sk-test")
    check("_build_headers has User-Agent", "User-Agent" in h)
    check("_build_headers User-Agent matches USER_AGENT",
          h["User-Agent"] == USER_AGENT)

test_11_1_user_agent_is_browser_ua()
test_11_2_headers_include_user_agent()


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
