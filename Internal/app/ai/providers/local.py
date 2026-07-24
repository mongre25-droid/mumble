"""Local LLM provider — user-configured OpenAI-compatible endpoint.

This provider connects to any OpenAI-compatible local server (Ollama, LM Studio,
llama.cpp server, vLLM, etc.).  The endpoint URL is user-configurable via the
``local_url`` setting; by default it targets ``http://localhost:11434``
(Ollama's default).

Key behaviour that differs from cloud providers:
  - ``key_ok()`` does NOT call an HTTP /models endpoint — local servers may
    not expose one.  Instead it performs a lightweight TCP reachability check
    (socket connect) to the configured host and port.

  - The API key field is optional for local servers (many don't require auth).
    When set, it is sent as a standard ``Authorization: Bearer`` header.

  - Model IDs are hand-entered by the user (e.g. ``llama3``, ``mistral``,
    ``gemma2``) because local servers rarely offer a programmatic model list.
    ``fetch_models()`` raises a clear message directing the user to enter the
    model name manually.
"""

import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from ai.base import BaseProvider, FULL_MARKER, TRUNC_MARKER
from ai.transport import _json_request, _validate_http_url


# ---- constants ---------------------------------------------------------------
# Default endpoint: Ollama's standard listen address.  The user can override
# this via the ``local_url`` setting in Settings.
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3"


def _resolve_url(base_url=None):
    """Build the /v1/chat/completions URL from a user-configured base.

    Handles common variations users type (trailing slashes, with or without
    ``/v1`` suffix, bare port-less URLs).
    """
    base = str(base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if not base:
        base = DEFAULT_BASE_URL
    if "://" not in base:
        base = "http://" + base
    # If the user pasted the full chat completions path, use it as-is.
    if base.endswith("/v1/chat/completions"):
        return _validate_http_url(base)
    # If the URL includes the /v1 prefix but not the full path, append.
    if base.endswith("/v1"):
        return _validate_http_url(base + "/chat/completions")
    return _validate_http_url(base + "/v1/chat/completions")


def _parse_host_port(url):
    """Extract (host, port) from an HTTP(S) URL for reachability checks."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("local server URL must use http:// or https://")
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


class LocalProvider(BaseProvider):
    """Local LLM provider (user-configured OpenAI-compatible endpoint).

    Constructor args:
        api_key:      optional API key (many local servers don't require auth).
        model:        model id (default ``llama3``, e.g. ``mistral``, ``gemma2``).
        base_url:     base URL of the local server (default ``http://localhost:11434``).
        timeout:      request timeout in seconds (default 60).
    """

    # ------------------------------------------------------------------
    # Instance state
    # ------------------------------------------------------------------

    def __init__(self, api_key="", model=None, base_url=None, timeout=60):
        self._api_key = (api_key or "").strip()
        self._model = model or DEFAULT_MODEL
        self._base_url = base_url or DEFAULT_BASE_URL
        self._url = _resolve_url(self._base_url)
        self._timeout = timeout

    # ------------------------------------------------------------------
    # BaseProvider interface
    # ------------------------------------------------------------------

    def headers(self):
        """Return HTTP headers.  If an API key is configured it is sent as
        a Bearer token; otherwise the Authorization header is omitted (many
        local servers accept unauthenticated requests)."""
        h = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Content-Type": "application/json",
        }
        if self._api_key:
            h["Authorization"] = "Bearer " + self._api_key
        return h

    def _build_headers(self):
        """Internal: build headers, allowing an empty key for local servers."""
        h = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Content-Type": "application/json",
        }
        if self._api_key:
            h["Authorization"] = "Bearer " + self._api_key
        return h

    @property
    def model_info(self):
        return {
            "provider": "local",
            "model": self._model,
            "url": self._url,
        }

    def chat(self, messages, **kwargs):
        """Non-streaming chat completion (single-turn).

        Args:
            messages: list of ``{"role":..., "content":...}`` dicts.
            **kwargs:  ``temperature`` (float), ``max_tokens`` (int),
                       ``timeout`` (int), ``model`` (str).

        Returns:
            Cleaned response text (str).
        """
        return _local_chat(
            messages,
            url=self._url,
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
        yield from _local_chat_stream(
            messages,
            url=self._url,
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
    def key_ok(api_key="", timeout=5, base_url=None):
        """Check whether the local LLM server is reachable.

        This does NOT make an HTTP request to a /models endpoint — local servers
        (Ollama, LM Studio) often don't expose one, or use non-standard paths.
        Instead it performs a lightweight TCP connect to the configured host:port.

        Returns:
            ``True`` — port is open (server appears reachable).
            ``False`` — connection refused / timeout (server likely not running).
            ``None`` — DNS resolution failed or other network error.
        """
        try:
            url = _resolve_url(base_url)
            host, port = _parse_host_port(url)
            sock = socket.create_connection((host, port), timeout=timeout)
            sock.close()
            return True
        except (ConnectionRefusedError, TimeoutError, socket.timeout):
            return False
        except Exception:
            return None

    @staticmethod
    def fetch_models(api_key="", timeout=12):
        """Local servers rarely have a standard /models endpoint.

        The user is expected to enter their model name manually (e.g.
        ``llama3``, ``mistral``, ``gemma2:7b``) — that's how Ollama and
        LM Studio users already work.
        """
        raise ValueError(
            "Local servers don't expose a model list. "
            "Enter your model name manually (e.g. llama3, mistral, gemma2:7b)."
        )


# =============================================================================
# Internal implementation
# =============================================================================


def _build_local_headers(api_key):
    """Build headers for local LLM requests.  Auth header is optional."""
    h = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Content-Type": "application/json",
    }
    if api_key:
        h["Authorization"] = "Bearer " + api_key
    return h


def _local_chat(
    messages,
    url,
    api_key="",
    model=DEFAULT_MODEL,
    temperature=0.2,
    timeout=60,
    max_tokens=None,
):
    """Non-streaming chat completion against a local endpoint."""
    payload = {
        "model": model,
        "messages": list(messages),
    }
    if not BaseProvider._is_reasoning_model(model):
        payload["temperature"] = temperature
    if max_tokens:
        payload["max_tokens"] = max_tokens

    data = _json_request(
        url,
        data=payload,
        headers=_build_local_headers(api_key),
        timeout=timeout,
    )
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    finish = choice.get("finish_reason") or "stop"
    text = (msg.get("content") or "").strip()
    if finish == "length":
        print(
            f"[local_chat] WARNING: response truncated "
            f"(finish_reason=length, max_tokens={max_tokens})"
        )
    return text


def _local_chat_stream(
    messages,
    url,
    api_key="",
    model=DEFAULT_MODEL,
    temperature=0.2,
    timeout=60,
    max_tokens=None,
):
    """Streaming chat completion — yields delta chunks, final=FULL_MARKER+text."""
    url = _validate_http_url(url)
    payload = {
        "model": model,
        "messages": list(messages),
        "stream": True,
    }
    if not BaseProvider._is_reasoning_model(model):
        payload["temperature"] = temperature
    if max_tokens:
        payload["max_tokens"] = max_tokens

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=_build_local_headers(api_key),
    )
    full = []
    truncated = False
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for line in r:
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
        err_msg = f"Local LLM HTTP {e.code}"
        try:
            err_msg += f": {e.read().decode('utf-8', errors='replace')[:200]}"
        except Exception:
            pass
        raise RuntimeError(err_msg) from None
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Couldn't reach the local LLM at {url}. "
            f"Is the server running? ({e.reason})"
        ) from None
