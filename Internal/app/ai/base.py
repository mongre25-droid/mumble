"""Base provider interface for the Mumble AI layer.

Every cloud and local provider implements this abstract class so the
controller (mumble.py / meeting.py) can dispatch to *any* engine without
knowing its internals.
"""

from abc import ABC, abstractmethod
from functools import wraps
import inspect

import processing_route


def _guard_provider_method(method):
    if inspect.isgeneratorfunction(method):
        @wraps(method)
        def guarded_stream(self, *args, **kwargs):
            decision = kwargs.pop("route_decision", None)

            def generate():
                processing_route.require_text_shaping(
                    decision, expected_provider=self.model_info.get("provider"))
                yield from method(self, *args, **kwargs)

            return generate()
        return guarded_stream

    @wraps(method)
    def guarded(self, *args, **kwargs):
        decision = kwargs.pop("route_decision", None)
        processing_route.require_text_shaping(
            decision, expected_provider=self.model_info.get("provider"))
        return method(self, *args, **kwargs)
    return guarded


class BaseProvider(ABC):
    """Abstract base class for all AI providers.

    Subclasses must implement:
      - chat(messages, **kwargs)            → str
      - chat_stream(messages, **kwargs)     → generator of delta chunks
      - headers()                           → dict of HTTP headers
      - model_info                          → dict with model metadata

    Shared concrete helpers (SSE streaming parser, token-limit resolution,
    reasoning-model detection) live here so individual providers don't
    duplicate them.
    """

    def __init_subclass__(cls, **kwargs):
        """Make route permission mandatory for every present and future adapter."""
        super().__init_subclass__(**kwargs)
        for name in ("chat", "chat_stream"):
            method = cls.__dict__.get(name)
            if method is not None and not getattr(method, "__isabstractmethod__", False):
                setattr(cls, name, _guard_provider_method(method))

    # ------------------------------------------------------------------
    # Abstract interface — every provider MUST implement these
    # ------------------------------------------------------------------

    @abstractmethod
    def chat(self, messages, **kwargs):
        """Send a non-streaming chat completion request.

        Args:
            messages: list of {"role":..., "content":...} dicts.
            **kwargs: provider-specific options (temperature, max_tokens, etc.).

        Returns:
            The model's full response text (str).
        """
        ...

    @abstractmethod
    def chat_stream(self, messages, **kwargs):
        """Send a streaming chat completion request.

        Yields:
            Delta text chunks as they arrive (str).  The final chunk is
            always FULL_MARKER+complete_text (or TRUNC_MARKER+text when
            the server-side token cap cut the response short).
        """
        ...

    @abstractmethod
    def headers(self):
        """Return HTTP headers dict for API requests (auth, content-type, UA)."""
        ...

    @property
    @abstractmethod
    def model_info(self):
        """Return a dict with provider model metadata.

        Must include at least:
          - "provider" (str):  provider id ("cerebras", "openai", …)
          - "model"    (str):  active model name
          - "url"      (str):  API endpoint URL
        """
        ...

    # ------------------------------------------------------------------
    # Shared concrete helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_reasoning_model(model):
        """True for OpenAI reasoning models (gpt-5*, o1/o3/o4 families).

        These models reject non-default temperature values with HTTP 400,
        so callers should omit the temperature parameter for them.
        """
        import re
        m = (model or "").lower()
        if "gpt-5" in m:
            return True
        return bool(re.search(r"(^|/)o[1-9](\b|-)", m))

    @staticmethod
    def _token_param(url, openai_url="https://api.openai.com/v1/chat/completions"):
        """Return the correct token-limit parameter name for a given endpoint.

        OpenAI's current models (GPT-5.x / o-series) reject ``max_tokens``
        with HTTP 400 and require ``max_completion_tokens`` instead.
        Every other provider Mumble targets still uses ``max_tokens``.
        """
        return "max_completion_tokens" if url == openai_url else "max_tokens"


# Re-export the commonly used sentinel markers so providers can import them
# from a single place without circular imports.
FULL_MARKER = "\x00FULL\x00"
TRUNC_MARKER = "\x00TRNC\x00"
if len(TRUNC_MARKER) != len(FULL_MARKER):
    raise RuntimeError("stream completion markers must have equal length")
