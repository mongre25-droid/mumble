"""OpenRouter AI provider — multi-model aggregator via OpenAI-compatible API.

OpenRouter is a model aggregator: one API key gives access to hundreds of
models.  Uses standard ``Authorization: Bearer`` auth.
"""

import json
import urllib.error
import urllib.request

from ai.base import BaseProvider, FULL_MARKER, TRUNC_MARKER
from ai.transport import (
    _build_headers, _json_request, iter_response_lines_limited,
    read_error_body,
)


# ---- constants ---------------------------------------------------------------
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_CREDITS_URL = "https://openrouter.ai/api/v1/credits"
DEFAULT_MODEL = "openai/gpt-5.4-mini"


class OpenRouterProvider(BaseProvider):
    """OpenRouter LLM provider (OpenAI-compatible API).

    Constructor args:
        api_key:  OpenRouter API key (required, ``sk-or-…``).
        model:    model id (default ``openai/gpt-5.4-mini``).
        timeout:  request timeout in seconds (default 60).
    """

    # ------------------------------------------------------------------
    # Instance state
    # ------------------------------------------------------------------

    def __init__(self, api_key, model=None, timeout=60):
        if not (api_key or "").strip():
            raise ValueError("OpenRouter API key required")
        self._api_key = (api_key or "").strip()
        self._model = model or DEFAULT_MODEL
        self._timeout = timeout

    # ------------------------------------------------------------------
    # BaseProvider interface
    # ------------------------------------------------------------------

    def headers(self):
        """Return Bearer-token headers for OpenRouter requests."""
        return _build_headers(self._api_key)

    @property
    def model_info(self):
        return {
            "provider": "openrouter",
            "model": self._model,
            "url": OPENROUTER_URL,
        }

    def chat(self, messages, **kwargs):
        """Non-streaming chat completion (single-turn)."""
        return _openrouter_chat(
            messages,
            api_key=self._api_key,
            model=kwargs.get("model", self._model),
            temperature=kwargs.get("temperature", 0.2),
            timeout=kwargs.get("timeout", self._timeout),
            max_tokens=kwargs.get("max_tokens"),
        )

    def chat_stream(self, messages, **kwargs):
        """Streaming chat completion — yields delta chunks as they arrive.

        The final chunk is ``FULL_MARKER+complete_text`` (or
        ``TRUNC_MARKER+text`` when the token cap cut the answer short).
        """
        yield from _openrouter_chat_stream(
            messages,
            api_key=self._api_key,
            model=kwargs.get("model", self._model),
            temperature=kwargs.get("temperature", 0.2),
            timeout=kwargs.get("timeout", self._timeout),
            max_tokens=kwargs.get("max_tokens"),
        )

    # ------------------------------------------------------------------
    # Static helpers (used by Settings UI)
    # ------------------------------------------------------------------

    @staticmethod
    def key_ok(api_key, timeout=10):
        """Validate an API key via GET /models (no token cost).

        Returns:
            ``True`` (valid), ``False`` (rejected — 401/402/403),
            or ``None`` (unknown — network / other).
        """
        return _key_ok(api_key, timeout=timeout, models_url=OPENROUTER_MODELS_URL)

    @staticmethod
    def fetch_models(api_key, timeout=12):
        """Fetch available model ids from the OpenRouter /models endpoint.

        Returns:
            Sorted, de-duped list of model id strings.

        Raises:
            ValueError if key is empty.
            RuntimeError on HTTP / network failure.
        """
        return _fetch_models(api_key, OPENROUTER_MODELS_URL, timeout=timeout)

    @staticmethod
    def get_credits(api_key, timeout=12):
        """Fetch the OpenRouter account balance.

        Returns:
            ``{"total": float, "used": float, "remaining": float}``
            (USD credits).

        Raises:
            ValueError if key is empty.
            RuntimeError on HTTP / network / unexpected response.
        """
        return _get_credits(api_key, timeout=timeout)


# =============================================================================
# Internal implementation (shared with backward-compat ai/__init__.py)
# =============================================================================


def _openrouter_chat(
    messages,
    api_key,
    model=DEFAULT_MODEL,
    temperature=0.2,
    timeout=60,
    max_tokens=None,
):
    """Non-streaming chat completion for OpenRouter."""
    payload = {
        "model": model,
        "messages": list(messages),
    }
    if not BaseProvider._is_reasoning_model(model):
        payload["temperature"] = temperature
    if max_tokens:
        payload[BaseProvider._token_param(OPENROUTER_URL)] = max_tokens

    data = _json_request(
        OPENROUTER_URL,
        data=payload,
        headers=_build_headers(api_key),
        timeout=timeout,
    )
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    finish = choice.get("finish_reason") or "stop"
    text = (msg.get("content") or "").strip()
    if finish == "length":
        print(
            f"[openrouter_chat] WARNING: response truncated (finish_reason=length, "
            f"max_tokens={max_tokens})"
        )
    return text


def _openrouter_chat_stream(
    messages,
    api_key,
    model=DEFAULT_MODEL,
    temperature=0.2,
    timeout=60,
    max_tokens=None,
):
    """Streaming chat completion for OpenRouter."""
    payload = {
        "model": model,
        "messages": list(messages),
        "stream": True,
    }
    if not BaseProvider._is_reasoning_model(model):
        payload["temperature"] = temperature
    if max_tokens:
        payload[BaseProvider._token_param(OPENROUTER_URL)] = max_tokens

    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=_build_headers(api_key),
    )
    full = []
    truncated = False
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for line in iter_response_lines_limited(r, timeout=timeout):
                line = line.decode("utf-8").strip()
                if not line or line.startswith(":"):
                    continue
                if line == "data: [DONE]" or line == "data:[DONE]":
                    break
                if line.startswith("data: "):
                    raw = line[6:]
                elif line.startswith("data:"):
                    raw = line[5:]
                else:
                    continue
                try:
                    chunk = json.loads(raw)
                    choices = chunk.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            full.append(content)
                            yield content
                        if choices[0].get("finish_reason") == "length":
                            truncated = True
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
        complete = "".join(full)
        yield (TRUNC_MARKER if truncated else FULL_MARKER) + complete
    except urllib.error.HTTPError as e:
        err_msg = f"API HTTP {e.code}"
        try:
            err_msg += f": {read_error_body(e)[:200]}"
        except Exception:
            pass
        if e.code == 429:
            try:
                hdrs = e.headers or {}
                ra = (
                    hdrs.get("Retry-After")
                    or hdrs.get("retry-after")
                    or hdrs.get("x-ratelimit-reset-tokens")
                    or hdrs.get("x-ratelimit-reset-requests") or ""
                )
                ra = str(ra).strip()
                try:
                    float(ra)
                except ValueError:
                    stripped = ra.rstrip("s")
                    try:
                        float(stripped)
                        ra = stripped
                    except ValueError:
                        ra = ""
                if ra:
                    val = float(ra)
                    if val > 3600:
                        ra = ""
                    err_msg += f" RETRY_AFTER={ra}"
            except Exception:
                pass
        raise RuntimeError(err_msg) from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e.reason}") from None


def _key_ok(api_key, timeout=10, models_url=OPENROUTER_MODELS_URL):
    """Cheap validity check via GET /models (no token cost)."""
    key = (api_key or "").strip()
    if not key:
        return False
    try:
        req = urllib.request.Request(
            models_url, headers=_build_headers(key, json_body=False)
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= getattr(r, "status", 200) < 300
    except urllib.error.HTTPError as e:
        if e.code in (401, 402, 403):
            return False
        return None
    except Exception:
        return None


def _fetch_models(api_key, models_url, timeout=12):
    """Fetch model ids from a /models endpoint."""
    key = (api_key or "").strip()
    if not key:
        raise ValueError("Enter the API key first, then fetch the model list.")
    data = _json_request(
        models_url,
        headers=_build_headers(key, json_body=False),
        timeout=timeout,
        method="GET",
    )
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        items = data if isinstance(data, list) else []
    ids = []
    for it in items:
        mid = (it.get("id") if isinstance(it, dict) else str(it)) or ""
        mid = mid.strip()
        if mid:
            ids.append(mid)
    return sorted(set(ids), key=str.lower)


def _get_credits(api_key, timeout=12):
    """Fetch the OpenRouter account balance.

    Returns ``{"total": float, "used": float, "remaining": float}``.
    """
    key = (api_key or "").strip()
    if not key:
        raise ValueError("Enter your OpenRouter key first to see your balance.")
    data = _json_request(
        OPENROUTER_CREDITS_URL,
        headers=_build_headers(key, json_body=False),
        timeout=timeout,
        method="GET",
    )
    d = (data or {}).get("data") if isinstance(data, dict) else None
    if not isinstance(d, dict):
        raise RuntimeError("OpenRouter returned an unexpected credits response.")
    total = float(d.get("total_credits") or 0.0)
    used = float(d.get("total_usage") or 0.0)
    return {"total": total, "used": used, "remaining": max(0.0, total - used)}
