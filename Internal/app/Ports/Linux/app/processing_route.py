"""Immutable, privacy-enforcing route decisions for text shaping.

Every product surface asks this module for one decision before text processing
starts.  Hosted adapters must pass that same decision through ``call_hosted``;
the guard deliberately sits at the last egress seam so a caller cannot bypass a
device-only, hosted-off, missing-key, or unsupported-provider decision.
"""

from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4


LOCAL = "local"
HOSTED = "hosted"

_PROVIDERS = {
    "cerebras": {
        "key_setting": "cerebras_api_key",
        "model_setting": "cerebras_model",
        "default_model": "gpt-oss-120b",
        "endpoint_class": "cerebras_compatible",
    },
    "openrouter": {
        "key_setting": "openrouter_api_key",
        "model_setting": "openrouter_model",
        "default_model": "openai/gpt-oss-120b",
        "endpoint_class": "openai_compatible",
    },
    # ``local`` is a user-selected, on-device OpenAI-compatible endpoint such
    # as Ollama or LM Studio.  It is deliberately a separate route from a
    # missing/unsupported hosted provider: device-only must still permit local
    # processing, while the hosted egress guard must never mistake it for a
    # cloud call.
    "local": {
        "key_setting": "local_api_key",
        "model_setting": "local_model",
        "default_model": "llama3",
        "endpoint_class": "local_endpoint",
        "requires_key": False,
    },
}


class HostedRouteBlocked(RuntimeError):
    """Raised before provider code runs when a snapshot forbids egress."""

    def __init__(self, decision: "RouteDecision"):
        self.decision = decision
        self.reason = decision.reason
        super().__init__(f"Hosted processing blocked: {decision.reason}")


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """One complete text-processing decision, frozen for one invocation."""

    invocation_id: str
    feature: str
    lane: str
    requested_route: str
    effective_route: str
    reason: str
    provider: str
    model: str
    endpoint_class: str
    privacy_boundary: str
    ready: bool
    provider_supported: bool
    key_present: bool
    pro_mode: bool
    device_only: bool
    api_key: str = field(repr=False, compare=False)

    @property
    def cloud_augmented(self):
        """Compatibility name used by the existing dictation path."""
        return self.effective_route == HOSTED

    @property
    def engine(self):
        """Compatibility with the legacy local-engine decision."""
        return "cloud" if self.cloud_augmented else "local"

    def public_dict(self):
        """Return UI-safe facts; the credential is intentionally omitted."""
        return {
            "invocation_id": self.invocation_id,
            "feature": self.feature,
            "lane": self.lane,
            "requested_route": self.requested_route,
            "effective_route": self.effective_route,
            "reason": self.reason,
            "provider": self.provider,
            "model": self.model,
            "endpoint_class": self.endpoint_class,
            "privacy_boundary": self.privacy_boundary,
            "ready": self.ready,
            "provider_supported": self.provider_supported,
            "key_present": self.key_present,
            "pro_mode": self.pro_mode,
            "device_only": self.device_only,
        }


@dataclass(frozen=True, slots=True)
class _FrozenMapping:
    """A recursively immutable mapping used inside an invocation snapshot."""

    items: tuple[tuple[str, Any], ...]


def _freeze_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _FrozenMapping(tuple(
            (str(key), _freeze_value(item))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        ))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze_value(item) for item in value), key=repr))
    return value


def _thaw_value(value: Any) -> Any:
    if isinstance(value, _FrozenMapping):
        return {key: _thaw_value(item) for key, item in value.items}
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    return value


def _freeze_mapping(value: Any) -> _FrozenMapping:
    frozen = _freeze_value(value if isinstance(value, dict) else {})
    return frozen if isinstance(frozen, _FrozenMapping) else _FrozenMapping(())


@dataclass(frozen=True, slots=True)
class ProcessingInputSnapshot:
    """All mutable text-shaping inputs captured once for one invocation.

    Provider credentials remain only inside ``route`` and are excluded from its
    public projection and representation. Private context is also excluded from
    the representation so routine diagnostics cannot accidentally disclose it.
    """

    route: RouteDecision
    user_name: str
    prompt_preferences: _FrozenMapping
    primary_language: str
    english_only: bool
    foreign_mode: bool
    foreign_languages: tuple[str, ...]
    vocabulary: _FrozenMapping
    vocabulary_terms: tuple[str, ...]
    format_enabled: bool
    polish_aggressiveness: str
    rpunct_enabled: bool
    modes: _FrozenMapping
    instant_text: bool
    local_model_ready: bool
    context_policy: str
    context_strict: bool
    context: str = field(repr=False)

    def prompt_prefs_dict(self) -> dict[str, Any]:
        return _thaw_value(self.prompt_preferences)

    def vocabulary_dict(self) -> dict[str, Any]:
        return _thaw_value(self.vocabulary)

    def modes_dict(self) -> dict[str, Any]:
        return _thaw_value(self.modes)


def _get(settings: Any, key: str, default: Any = None) -> Any:
    return settings.get(key, default)


def snapshot(settings: Any, *, feature: str, lane: str) -> RouteDecision:
    """Resolve and freeze the requested/effective route for one action."""
    provider = str(_get(settings, "llm_provider", "cerebras") or "").strip().lower()
    provider_info = _PROVIDERS.get(provider)
    supported = provider_info is not None
    if supported:
        api_key = str(_get(settings, provider_info["key_setting"], "") or "").strip()
        model = str(
            _get(settings, provider_info["model_setting"], "")
            or provider_info["default_model"]
        ).strip()
        endpoint_class = provider_info["endpoint_class"]
    else:
        api_key = ""
        model = ""
        endpoint_class = "unsupported"

    pro_mode = bool(_get(settings, "pro_mode", True))
    device_only = bool(_get(settings, "local_only_mode", False))
    instant_text = bool(_get(settings, "instant_text", True))
    explicit_local = (
        (feature == "dictation" and lane == "text" and instant_text)
        or provider == "local"
    )
    requested = LOCAL if explicit_local else HOSTED

    if provider == "local":
        effective, reason, ready = LOCAL, "local_provider", True
    elif explicit_local:
        effective, reason, ready = LOCAL, "instant_text", True
    elif device_only:
        effective, reason, ready = LOCAL, "device_only", False
    elif not pro_mode:
        effective, reason, ready = LOCAL, "hosted_processing_off", False
    elif not supported:
        effective, reason, ready = LOCAL, "unsupported_provider", False
    elif not api_key:
        effective, reason, ready = LOCAL, "missing_key", False
    else:
        effective, reason, ready = HOSTED, "ready", True

    privacy = (
        "transcript_text_leaves_device" if effective == HOSTED else "device_only"
    )
    return RouteDecision(
        invocation_id=uuid4().hex,
        feature=str(feature or "unknown"),
        lane=str(lane or "text"),
        requested_route=requested,
        effective_route=effective,
        reason=reason,
        provider=provider,
        model=model,
        endpoint_class=endpoint_class,
        privacy_boundary=privacy,
        ready=ready,
        provider_supported=supported,
        key_present=bool(api_key) or (supported and not provider_info.get("requires_key", True)),
        pro_mode=pro_mode,
        device_only=device_only,
        api_key=api_key,
    )


def snapshot_inputs(
    settings: Any,
    *,
    feature: str,
    lane: str,
    context: str,
    context_policy: str,
    context_strict: bool,
    local_model_ready: bool,
) -> ProcessingInputSnapshot:
    """Capture every mutable input used by one text-shaping invocation."""
    return ProcessingInputSnapshot(
        route=snapshot(settings, feature=feature, lane=lane),
        user_name=str(_get(settings, "user_name", "") or "").strip(),
        prompt_preferences=_freeze_mapping(
            _get(settings, "prompt_prefs", {}) or {}
        ),
        primary_language=str(
            _get(settings, "primary_language", "") or ""
        ).strip().lower(),
        english_only=bool(_get(settings, "english_only", True)),
        foreign_mode=bool(_get(settings, "foreign_mode", False)),
        foreign_languages=tuple(
            str(language) for language in (
                _get(settings, "foreign_languages", []) or []
            )
        ),
        vocabulary=_freeze_mapping(_get(settings, "vocabulary", {}) or {}),
        vocabulary_terms=tuple(
            str(term) for term in (_get(settings, "vocabulary_terms", []) or [])
        ),
        format_enabled=bool(_get(settings, "format_enabled", True)),
        polish_aggressiveness=str(
            _get(settings, "polish_aggressiveness", "Light") or "Light"
        ),
        rpunct_enabled=bool(_get(settings, "rpunct_enabled", True)),
        modes=_freeze_mapping(_get(settings, "modes", {}) or {}),
        instant_text=bool(_get(settings, "instant_text", True)),
        local_model_ready=bool(local_model_ready),
        context_policy=str(context_policy or "none"),
        context_strict=bool(context_strict),
        context=str(context or ""),
    )


def require_hosted(decision: RouteDecision) -> RouteDecision:
    """Final egress guard. Call immediately before a hosted provider adapter."""
    if not isinstance(decision, RouteDecision):
        raise TypeError("Hosted calls require an explicit RouteDecision")
    if (
        decision.effective_route != HOSTED
        or not decision.ready
        or not decision.provider_supported
        or not decision.key_present
        or decision.device_only
        or not decision.pro_mode
    ):
        raise HostedRouteBlocked(decision)
    return decision


def call_hosted(
    decision: RouteDecision,
    provider_call: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Guard then enter provider code without re-reading mutable settings."""
    require_hosted(decision)
    return provider_call(*args, **kwargs)


def require_provider(decision: RouteDecision) -> RouteDecision:
    """Authorize one frozen provider invocation at the final call seam.

    Local endpoints remain available in device-only mode.  Every other provider
    goes through the stricter hosted guard, which rejects any non-ready route
    before adapter code can run.
    """
    if not isinstance(decision, RouteDecision):
        raise TypeError("Provider calls require an explicit RouteDecision")
    if decision.provider == "local" and decision.effective_route == LOCAL and decision.ready:
        return decision
    return require_hosted(decision)


def call_provider(
    decision: RouteDecision,
    provider_call: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Guard then invoke either the local endpoint or an allowed hosted one."""
    require_provider(decision)
    return provider_call(*args, **kwargs)


def settings_state(settings: Any) -> dict[str, Any]:
    """UI-safe saved/effective route facts from the same policy as actions."""
    plain = snapshot(settings, feature="dictation", lane="text")
    action = snapshot(settings, feature="prompt", lane="prompt")
    return {
        "plain_processing": plain.public_dict(),
        "action_processing": action.public_dict(),
        "local_only": action.device_only,
    }
