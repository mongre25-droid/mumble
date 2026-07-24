"""Provider registry — maps provider ids to their endpoint, auth, and defaults.

Every supported provider (cloud + local) has an entry in the PROVIDERS dict.
The registry is the single source of truth used by the Settings UI, the
controller (mumble.py), and the meeting module to:
  - build the provider dropdown
  - resolve which URL / API key / model to use for a request
  - fetch model lists and validate keys
"""

# ---- URL constants -----------------------------------------------------------
CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"
CEREBRAS_MODELS_URL = "https://api.cerebras.ai/v1/models"
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODELS_URL = "https://api.deepseek.com/v1/models"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
ANTHROPIC_VERSION = "2023-06-01"

# ---- Provider registry -------------------------------------------------------
# Each entry maps a provider id → endpoint / label / settings keys / default model.
# The UI uses this to build the Settings dropdown; the controller uses it to
# resolve which URL, key, and model to send for each dictation.
PROVIDERS = {
    "cerebras": {
        "url": CEREBRAS_URL,
        "label": "Cerebras (recommended — fastest, free)",
        "key_setting": "cerebras_api_key",
        "model_setting": "cerebras_model",
        "default_model": "gpt-oss-120b",
    },
    "openai": {
        "url": OPENAI_URL,
        "label": "OpenAI / ChatGPT (slower, expensive — not recommended)",
        "key_setting": "openai_api_key",
        "model_setting": "openai_model",
        "default_model": "gpt-5.4-mini",
    },
    "anthropic": {
        "url": ANTHROPIC_URL,
        "label": "Anthropic Claude (high quality, slower)",
        "key_setting": "anthropic_api_key",
        "model_setting": "anthropic_model",
        "default_model": "claude-opus-4-8",
    },
    "openrouter": {
        "url": OPENROUTER_URL,
        "label": "OpenRouter (one key → many models)",
        "key_setting": "openrouter_api_key",
        "model_setting": "openrouter_model",
        "default_model": "openai/gpt-5.4-mini",
    },
    "local": {
        "url": "http://localhost:11434/v1/chat/completions",
        "label": "Local LLM (Ollama, LM Studio)",
        "key_setting": "local_api_key",
        "model_setting": "local_model",
        "default_model": "llama3",
    },
    # Engine-level back-compat (not shown in the Settings dropdown):
    "deepseek": {
        "url": DEEPSEEK_URL,
        "label": "DeepSeek (slower — not recommended)",
        "key_setting": "deepseek_api_key",
        "model_setting": "deepseek_model",
        "default_model": "deepseek-v4-flash",
    },
    "groq": {
        "url": GROQ_URL,
        "label": "Groq (slower — not recommended)",
        "key_setting": "groq_api_key",
        "model_setting": "groq_model",
        "default_model": "llama-3.3-70b-versatile",
    },
}

# Provider → models-list endpoint (for Settings dropdown population).
# "local" is intentionally absent — local servers may not expose /v1/models.
PROVIDER_MODELS_URL = {
    "cerebras": CEREBRAS_MODELS_URL,
    "openai": OPENAI_MODELS_URL,
    "anthropic": ANTHROPIC_MODELS_URL,
    "openrouter": OPENROUTER_MODELS_URL,
    "deepseek": DEEPSEEK_MODELS_URL,
    "groq": GROQ_MODELS_URL,
}


def provider_ids():
    """Return the sorted list of provider ids from the registry."""
    return sorted(PROVIDERS.keys())


def provider_info(provider_id):
    """Look up a provider's registry entry.  Returns a dict or None."""
    return PROVIDERS.get((provider_id or "").strip().lower())


def provider_models_url(provider_id):
    """Return the /models endpoint for *provider_id*, or None."""
    return PROVIDER_MODELS_URL.get((provider_id or "").strip().lower())


def is_anthropic_provider(provider_id_or_url):
    """True when *provider_id_or_url* refers to the Anthropic provider.

    Accepts a provider id string ("anthropic") or a URL (ANTHROPIC_URL /
    ANTHROPIC_MODELS_URL).
    """
    v = (provider_id_or_url or "").strip().lower()
    return v == "anthropic" or v in (ANTHROPIC_URL, ANTHROPIC_MODELS_URL)


# ---------------------------------------------------------------------------
# Provider class imports — each provider module is self-contained.
# Import guards allow other providers to be added later without breaking
# the registry.
# ---------------------------------------------------------------------------
try:
    from ai.providers.cerebras import CerebrasProvider  # noqa: F401
except ImportError:
    CerebrasProvider = None  # type: ignore[assignment]

try:
    from ai.providers.openrouter import OpenRouterProvider  # noqa: F401
except ImportError:
    OpenRouterProvider = None  # type: ignore[assignment]

try:
    from ai.providers.openai import OpenAIProvider  # noqa: F401
except ImportError:
    OpenAIProvider = None  # type: ignore[assignment]

try:
    from ai.providers.anthropic import AnthropicProvider  # noqa: F401
except ImportError:
    AnthropicProvider = None  # type: ignore[assignment]

try:
    from ai.providers.deepseek import DeepSeekProvider  # noqa: F401
except ImportError:
    DeepSeekProvider = None  # type: ignore[assignment]

try:
    from ai.providers.groq import GroqProvider  # noqa: F401
except ImportError:
    GroqProvider = None  # type: ignore[assignment]

try:
    from ai.providers.local import LocalProvider  # noqa: F401
except ImportError:
    LocalProvider = None  # type: ignore[assignment]
