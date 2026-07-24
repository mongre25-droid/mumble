"""Cerebras AI provider — gpt-oss-120b via OpenAI-compatible API.

Cerebras is the default and recommended Mumble engine: fastest (~0.4-0.5s
round-trip), free tier, supports ``reasoning_effort`` for deeper thinking on
prompt/email/reply modes.  The API sits behind Cloudflare so every request
needs a browser User-Agent (provided by ``ai.transport``).
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
CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"
CEREBRAS_MODELS_URL = "https://api.cerebras.ai/v1/models"
DEFAULT_MODEL = "gpt-oss-120b"


class CerebrasProvider(BaseProvider):
    """Cerebras LLM provider (OpenAI-compatible API).

    Constructor args:
        api_key:        Cerebras API key (required).
        model:          model id (default ``gpt-oss-120b``).
        timeout:        request timeout in seconds (default 60).
        reasoning_effort: ``"low"`` / ``"medium"`` / ``"high"`` — controls how
                          hard the reasoning model thinks.  Only sent to
                          Cerebras's own URL; other endpoints ignore it.
    """

    # ------------------------------------------------------------------
    # Instance state
    # ------------------------------------------------------------------

    def __init__(self, api_key, model=None, timeout=60, reasoning_effort=None):
        if not (api_key or "").strip():
            raise ValueError("Cerebras API key required")
        self._api_key = (api_key or "").strip()
        self._model = model or DEFAULT_MODEL
        self._timeout = timeout
        self._reasoning_effort = reasoning_effort

    # ------------------------------------------------------------------
    # BaseProvider interface
    # ------------------------------------------------------------------

    def headers(self):
        """Return Bearer-token headers for Cerebras requests."""
        return _build_headers(self._api_key)

    @property
    def model_info(self):
        return {
            "provider": "cerebras",
            "model": self._model,
            "url": CEREBRAS_URL,
        }

    def chat(self, messages, **kwargs):
        """Non-streaming chat completion (single-turn).

        Args:
            messages: list of ``{"role":..., "content":...}`` dicts.
            **kwargs:  ``temperature`` (float), ``max_tokens`` (int),
                       ``reasoning_effort`` (str), ``timeout`` (int).

        Returns:
            Cleaned response text (str).
        """
        return _cerebras_chat(
            messages,
            api_key=self._api_key,
            model=kwargs.get("model", self._model),
            temperature=kwargs.get("temperature", 0.2),
            timeout=kwargs.get("timeout", self._timeout),
            reasoning_effort=kwargs.get("reasoning_effort", self._reasoning_effort),
            max_tokens=kwargs.get("max_tokens"),
        )

    def chat_stream(self, messages, **kwargs):
        """Streaming chat completion — yields delta chunks as they arrive.

        The final chunk is ``FULL_MARKER+complete_text`` (or
        ``TRUNC_MARKER+text`` when the token cap cut the answer short).
        """
        yield from _cerebras_chat_stream(
            messages,
            api_key=self._api_key,
            model=kwargs.get("model", self._model),
            temperature=kwargs.get("temperature", 0.2),
            timeout=kwargs.get("timeout", self._timeout),
            reasoning_effort=kwargs.get("reasoning_effort", self._reasoning_effort),
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
        return _key_ok(api_key, timeout=timeout, models_url=CEREBRAS_MODELS_URL)

    @staticmethod
    def fetch_models(api_key, timeout=12):
        """Fetch available model ids from the Cerebras /models endpoint.

        Returns:
            Sorted, de-duped list of model id strings.

        Raises:
            ValueError if key is empty.
            RuntimeError on HTTP / network failure.
        """
        return _fetch_models(api_key, CEREBRAS_MODELS_URL, timeout=timeout)


# =============================================================================
# Internal implementation (shared with backward-compat ai/__init__.py)
# =============================================================================


def _cerebras_chat(
    messages,
    api_key,
    model=DEFAULT_MODEL,
    temperature=0.2,
    timeout=60,
    reasoning_effort=None,
    max_tokens=None,
):
    """Non-streaming chat completion.  Single-turn, returns cleaned text.

    ``reasoning_effort`` is ONLY sent to Cerebras's own endpoint — other
    providers either ignore it or burn real time on it (OpenAI reasoning
    models).
    """
    payload = {
        "model": model,
        "messages": list(messages),
    }
    # Reasoning models reject custom temperature — omit for them.
    if not BaseProvider._is_reasoning_model(model):
        payload["temperature"] = temperature
    if reasoning_effort and _url_is_cerebras(CEREBRAS_URL):
        payload["reasoning_effort"] = reasoning_effort
    if max_tokens:
        payload[BaseProvider._token_param(CEREBRAS_URL)] = max_tokens

    data = _json_request(
        CEREBRAS_URL,
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
            f"[cerebras_chat] WARNING: response truncated (finish_reason=length, "
            f"max_tokens={max_tokens})"
        )
    return text


def _cerebras_chat_stream(
    messages,
    api_key,
    model=DEFAULT_MODEL,
    temperature=0.2,
    timeout=60,
    reasoning_effort=None,
    max_tokens=None,
):
    """Streaming chat completion — yields delta chunks, final=FULL_MARKER+text."""
    payload = {
        "model": model,
        "messages": list(messages),
        "stream": True,
    }
    if not BaseProvider._is_reasoning_model(model):
        payload["temperature"] = temperature
    if reasoning_effort and _url_is_cerebras(CEREBRAS_URL):
        payload["reasoning_effort"] = reasoning_effort
    if max_tokens:
        payload[BaseProvider._token_param(CEREBRAS_URL)] = max_tokens

    req = urllib.request.Request(
        CEREBRAS_URL,
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


def _key_ok(api_key, timeout=10, models_url=CEREBRAS_MODELS_URL):
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
    """Fetch model ids from a /models endpoint.

    Returns sorted, de-duped list of model id strings.
    """
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


def _url_is_cerebras(url):
    """True when *url* is the Cerebras chat endpoint."""
    return (url or "").rstrip("/") == CEREBRAS_URL.rstrip("/")
