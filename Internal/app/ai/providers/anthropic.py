"""Anthropic Claude provider via native Messages API.

Anthropic uses its OWN auth scheme (``x-api-key`` + ``anthropic-version``)
and a different request-body shape (top-level ``system``, ``messages`` array
with ``role: "user"``) — NOT the OpenAI-compatible ``Bearer`` + nested system
message.  Streaming is not supported via the native API; this module delivers
complete text in one chunk with ``FULL_MARKER`` (or ``TRUNC_MARKER`` when the
model hits its token cap).

Key validation and model discovery also use ``x-api-key`` auth.
"""

import json
import urllib.error
import urllib.request

from ai.base import BaseProvider, FULL_MARKER, TRUNC_MARKER


# ---- constants ---------------------------------------------------------------
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-opus-4-8"

# Browser UA needed for all outbound requests (Cloudflare compatibility)
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _anthropic_headers(api_key, json_body=True):
    """Build Anthropic-native auth headers: ``x-api-key`` + ``anthropic-version``.

    Anthropic uses its own auth scheme — NOT ``Authorization: Bearer``.
    """
    if not (api_key or "").strip():
        raise ValueError("API key required — set one in Settings → Pro Mode")
    h = {
        "x-api-key": (api_key or "").strip(),
        "anthropic-version": ANTHROPIC_VERSION,
        "User-Agent": _UA,
    }
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _anthropic_chat(system, user, api_key, model, timeout=60, max_tokens=None,
                    conversation=None):
    """Native Anthropic Messages API call (single turn, non-streaming).

    Constructs the Anthropic body shape::

        {
          "model": "...",
          "max_tokens": ...,
          "system": "...",
          "messages": [{"role": "user", "content": "..."}]
        }

    Returns ``(text, stop_reason)`` so callers can detect a length-truncated
    response (Anthropic ``stop_reason="max_tokens"`` is normalised to the
    OpenAI-compatible ``"length"``).
    """
    payload = {
        "model": model or DEFAULT_MODEL,
        "max_tokens": min(int(max_tokens or 4096), 32000),
        "system": system,
        "messages": (conversation if conversation is not None
                     else [{"role": "user", "content": user}]),
    }
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=_anthropic_headers(api_key),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"API HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}"
        ) from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e.reason}") from None
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON response: {e}") from None
    parts = [
        b.get("text", "")
        for b in (data.get("content") or [])
        if b.get("type") == "text"
    ]
    text = "".join(parts)
    sr = data.get("stop_reason") or "stop"
    # Normalise: Anthropic "max_tokens" → OpenAI-compatible "length"
    stop_reason = "length" if sr == "max_tokens" else sr
    return text, stop_reason


class AnthropicProvider(BaseProvider):
    """Anthropic Claude LLM provider (native Messages API).

    Constructor args:
        api_key:   Anthropic API key (required).
        model:     model id (default ``claude-opus-4-8``).
        timeout:   request timeout in seconds (default 60).
    """

    # ------------------------------------------------------------------
    # Instance state
    # ------------------------------------------------------------------

    def __init__(self, api_key, model=None, timeout=60):
        if not (api_key or "").strip():
            raise ValueError("Anthropic API key required")
        self._api_key = (api_key or "").strip()
        self._model = model or DEFAULT_MODEL
        self._timeout = timeout

    # ------------------------------------------------------------------
    # BaseProvider interface
    # ------------------------------------------------------------------

    def headers(self):
        """Return Anthropic-native auth headers (x-api-key)."""
        return _anthropic_headers(self._api_key)

    @property
    def model_info(self):
        return {
            "provider": "anthropic",
            "model": self._model,
            "url": ANTHROPIC_URL,
        }

    def chat(self, messages, **kwargs):
        """Non-streaming chat completion via native Messages API.

        Args:
            messages: list of ``{"role":..., "content":...}`` dicts.
                      Anthropic uses top-level ``system`` + ``messages``
                      with ``role: "user"`` — NOT the OpenAI shape.
            **kwargs:  ``timeout`` (int), ``max_tokens`` (int).

        Returns:
            Cleaned response text (str).
        """
        # Extract system and user from messages list (OpenAI-shaped input
        # is converted to Anthropic's top-level system + messages shape).
        system, conversation = _prepare_anthropic_messages(messages)
        user = "\n\n".join(
            m["content"] for m in conversation if m["role"] == "user"
        )
        text, _stop_reason = _anthropic_chat(
            system,
            user,
            api_key=self._api_key,
            model=kwargs.get("model", self._model),
            timeout=kwargs.get("timeout", self._timeout),
            max_tokens=kwargs.get("max_tokens"),
            conversation=conversation,
        )
        return text

    def chat_stream(self, messages, **kwargs):
        """Streaming via native Messages API — non-streaming wrapped.

        Anthropic does not support SSE streaming through the native API;
        the full response is fetched in one call and yielded as a single
        ``FULL_MARKER+text`` chunk (or ``TRUNC_MARKER+text`` when truncated).
        """
        # Extract system and user from messages list
        system, conversation = _prepare_anthropic_messages(messages)
        user = "\n\n".join(
            m["content"] for m in conversation if m["role"] == "user"
        )
        text, stop_reason = _anthropic_chat(
            system,
            user,
            api_key=self._api_key,
            model=kwargs.get("model", self._model),
            timeout=kwargs.get("timeout", self._timeout),
            max_tokens=kwargs.get("max_tokens"),
            conversation=conversation,
        )
        yield (TRUNC_MARKER if stop_reason == "length" else FULL_MARKER) + text

    # ------------------------------------------------------------------
    # Static helpers (used by Settings UI)
    # ------------------------------------------------------------------

    @staticmethod
    def key_ok(api_key, timeout=10):
        """Validate an Anthropic API key via GET /models.

        Uses ``x-api-key`` + ``anthropic-version`` headers — NOT Bearer auth.

        Returns:
            ``True`` (valid), ``False`` (rejected — 401/402/403),
            or ``None`` (unknown — network / other).
        """
        return _key_ok(api_key, timeout=timeout, models_url=ANTHROPIC_MODELS_URL)

    @staticmethod
    def fetch_models(api_key, timeout=12):
        """Fetch available model ids from the Anthropic /models endpoint.

        Uses native Anthropic auth headers.

        Returns:
            Sorted, de-duped list of model id strings.

        Raises:
            ValueError if key is empty.
            RuntimeError on HTTP / network failure.
        """
        return _fetch_models(api_key, ANTHROPIC_MODELS_URL, timeout=timeout)


# =============================================================================
# Internal implementation
# =============================================================================


def _prepare_anthropic_messages(messages):
    """Preserve user/assistant turn order while lifting system text to the top."""
    systems = []
    conversation = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        content = content.strip()
        if role == "system":
            systems.append(content)
        elif role in ("user", "assistant"):
            conversation.append({"role": role, "content": content})
    if not conversation:
        conversation = [{"role": "user", "content": ""}]
    return "\n\n".join(systems), conversation


def _key_ok(api_key, timeout=10, models_url=ANTHROPIC_MODELS_URL):
    """Cheap validity check via GET /models (no token cost).

    Uses ``x-api-key`` + ``anthropic-version`` auth headers.
    """
    key = (api_key or "").strip()
    if not key:
        return False
    try:
        req = urllib.request.Request(
            models_url,
            headers=_anthropic_headers(key, json_body=False),
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
    """Fetch model ids from the Anthropic /models endpoint.

    Returns sorted, de-duped list of model id strings.
    """
    key = (api_key or "").strip()
    if not key:
        raise ValueError("Enter the API key first, then fetch the model list.")
    req = urllib.request.Request(
        models_url,
        headers=_anthropic_headers(key, json_body=False),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:160]
        raise RuntimeError(f"HTTP {e.code}: {body}") from None
    except Exception as e:
        raise RuntimeError(f"Couldn't reach Anthropic: {e}") from None
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


def _anthropic_warm(api_key, model=DEFAULT_MODEL, timeout=20):
    """Fire-and-forget warm-up ping via native Messages API.

    Sends a minimal request (``max_tokens=1``) to spin up the serverless
    endpoint in parallel with the user speaking.
    """
    payload = {
        "model": model or DEFAULT_MODEL,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "."}],
    }
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=_anthropic_headers(api_key),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
        return True
    except Exception:
        return False
