"""Immutable, privacy-enforcing route decisions for text shaping.

Every product surface asks this module for one decision before text processing
starts.  Hosted adapters must pass that same decision through ``call_hosted``;
the guard deliberately sits at the last egress seam so a caller cannot bypass a
device-only, hosted-off, missing-key, or unsupported-provider decision.
"""

from dataclasses import dataclass, field, replace
from typing import Any, Callable
from uuid import uuid4

import model_authority


LOCAL = "local"
HOSTED = "hosted"
_UNSET = object()

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
        "default_model": "openai/gpt-5.4-mini",
        "endpoint_class": "openai_compatible",
    },
    "openai": {
        "key_setting": "openai_api_key",
        "model_setting": "openai_model",
        "default_model": "gpt-5.4-mini",
        "endpoint_class": "openai_native",
    },
    "anthropic": {
        "key_setting": "anthropic_api_key",
        "model_setting": "anthropic_model",
        "default_model": "claude-opus-4-8",
        "endpoint_class": "anthropic_native",
    },
    "deepseek": {
        "key_setting": "deepseek_api_key",
        "model_setting": "deepseek_model",
        "default_model": "deepseek-v4-flash",
        "endpoint_class": "openai_compatible",
    },
    "groq": {
        "key_setting": "groq_api_key",
        "model_setting": "groq_model",
        "default_model": "llama-3.3-70b-versatile",
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

_SPEECH_PROVIDERS = {
    "openrouter": {
        "key_setting": "openrouter_api_key",
        "model_setting": "reader_tts_model",
        "default_model": "openai/gpt-4o-mini-tts",
        "endpoint_class": "openrouter_speech",
    },
    "openai": {
        "key_setting": "openai_api_key",
        "model_setting": "reader_tts_model",
        "default_model": "gpt-4o-mini-tts",
        "endpoint_class": "openai_speech",
    },
}

_TRANSCRIPTION_PROVIDERS = {
    "groq": {
        "key_setting": "groq_api_key",
        "model_setting": "groq_transcription_model",
        "default_model": "whisper-large-v3-turbo",
        "endpoint_class": "groq_speech_to_text",
    },
    "openai": {
        "key_setting": "openai_api_key",
        "model_setting": "openai_transcription_model",
        "default_model": "gpt-4o-mini-transcribe",
        "endpoint_class": "openai_speech_to_text",
    },
    "openrouter": {
        "key_setting": "openrouter_api_key",
        "model_setting": "openrouter_transcription_model",
        "default_model": "groq/whisper-large-v3-turbo",
        "endpoint_class": "openrouter_speech_to_text",
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


def confirm_selected_model(
    decision: RouteDecision, confirmed_models: Any
) -> RouteDecision:
    """Fail a hosted decision closed unless its exact model was confirmed.

    Model discovery is owned by the host bridge.  This policy helper keeps the
    resulting readiness decision in the same immutable route authority used by
    runtime and Settings projections; renderers must not invent a second answer.
    """
    if decision.requested_route != HOSTED or decision.effective_route != HOSTED:
        return decision
    confirmed = frozenset(str(model).strip() for model in (confirmed_models or ()))
    if decision.model in confirmed:
        return decision
    return replace(
        decision,
        effective_route=LOCAL,
        reason="unconfirmed_model",
        privacy_boundary="device_only",
        ready=False,
    )


model_credential_identity = model_authority.model_credential_identity


def _confirmed_models_for(settings: Any, decision: RouteDecision) -> tuple[str, ...]:
    return model_authority.confirmed_models_for(
        settings, decision.provider, decision.api_key
    )


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


def _string_tuple(value: Any) -> tuple[str, ...]:
    """Freeze a string-or-sequence setting without splitting one string."""
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in (value or []))


@dataclass(frozen=True, slots=True)
class ProcessingInputSnapshot:
    """All mutable text-shaping inputs captured once for one invocation.

    Provider credentials remain only inside ``route`` and are excluded from its
    public projection and representation. Private context is also excluded from
    the representation so routine diagnostics cannot accidentally disclose it.
    """

    route: RouteDecision
    user_name: str = field(repr=False)
    prompt_preferences: _FrozenMapping = field(repr=False)
    primary_language: str = field(repr=False)
    english_only: bool = field(repr=False)
    foreign_mode: bool = field(repr=False)
    foreign_languages: tuple[str, ...] = field(repr=False)
    vocabulary: _FrozenMapping = field(repr=False)
    vocabulary_terms: tuple[str, ...] = field(repr=False)
    format_enabled: bool = field(repr=False)
    polish_aggressiveness: str = field(repr=False)
    rpunct_enabled: bool = field(repr=False)
    modes: _FrozenMapping = field(repr=False)
    instant_text: bool = field(repr=False)
    local_model_ready: bool = field(repr=False)
    context_policy: str = field(repr=False)
    context_strict: bool = field(repr=False)
    context: str = field(repr=False)

    def prompt_prefs_dict(self) -> dict[str, Any]:
        return _thaw_value(self.prompt_preferences)

    def vocabulary_dict(self) -> dict[str, Any]:
        return _thaw_value(self.vocabulary)

    def modes_dict(self) -> dict[str, Any]:
        return _thaw_value(self.modes)


def _get(settings: Any, key: str, default: Any = None) -> Any:
    return settings.get(key, default)


def capture_text_provider_settings(
    settings: Any, *, supported_providers: tuple[str, ...] | None = None
) -> dict[str, Any]:
    """Capture the exact provider contract used by one platform invocation."""
    selected = tuple(supported_providers or _PROVIDERS)
    captured: dict[str, Any] = {
        "_text_processing_providers": selected,
        "_confirmed_text_models": _get(settings, "_confirmed_text_models", {}),
    }
    for provider in selected:
        provider_info = _PROVIDERS[provider]
        key_setting = provider_info["key_setting"]
        model_setting = provider_info["model_setting"]
        captured[key_setting] = _get(settings, key_setting, "")
        captured[model_setting] = _get(settings, model_setting, "")
    return captured


def snapshot(
    settings: Any,
    *,
    feature: str,
    lane: str,
    provider_override: str | None = None,
    model_override: str | None = None,
    supported_providers: tuple[str, ...] | None = None,
) -> RouteDecision:
    """Resolve and freeze the requested/effective route for one action."""
    speech_lane = feature == "reader" and lane in {
        "reader_speech", "reader_speech_test"
    }
    transcription_lane = feature == "dictation" and lane == "speech_to_text"
    if transcription_lane:
        provider = str(
            _get(settings, "cloud_transcription_provider", "groq") or ""
        ).strip().lower()
        provider_info = _TRANSCRIPTION_PROVIDERS.get(provider)
    elif speech_lane:
        provider = str(
            provider_override
            if provider_override is not None
            else _get(settings, "reader_tts_provider", "openrouter") or ""
        ).strip().lower()
        provider_info = _SPEECH_PROVIDERS.get(provider)
    else:
        provider = str(
            _get(settings, "llm_provider", "cerebras") or ""
        ).strip().lower()
        provider_info = _PROVIDERS.get(provider)
        platform_contract = supported_providers
        if platform_contract is None:
            platform_contract = _get(settings, "_text_processing_providers", None)
        if platform_contract is not None and provider not in platform_contract:
            provider_info = None
    supported = provider_info is not None
    if supported:
        api_key = str(_get(settings, provider_info["key_setting"], "") or "").strip()
        selected_model = _get(settings, provider_info["model_setting"], "")
        model = str(
            model_override if model_override is not None else selected_model
        ).strip()
        endpoint_class = provider_info["endpoint_class"]
    else:
        api_key = ""
        model = ""
        endpoint_class = "unsupported"

    pro_mode = bool(_get(settings, "pro_mode", True))
    device_only = bool(_get(settings, "local_only_mode", False))
    instant_text = bool(_get(settings, "instant_text", True))
    if transcription_lane:
        explicit_local = str(
            _get(settings, "transcription_mode", "local") or "local"
        ).strip().lower() != "cloud"
    else:
        explicit_local = not speech_lane and (
            (feature == "dictation" and lane == "text" and instant_text)
            or (provider == "local" and supported)
        )
    requested = LOCAL if explicit_local else HOSTED

    if provider == "local" and supported:
        effective, reason, ready = LOCAL, "local_provider", True
    elif explicit_local:
        effective, reason, ready = LOCAL, (
            "local_transcription" if transcription_lane else "instant_text"
        ), True
    elif device_only:
        effective, reason, ready = LOCAL, "device_only", False
    elif not pro_mode:
        effective, reason, ready = LOCAL, "hosted_processing_off", False
    elif not supported:
        effective, reason, ready = LOCAL, "unsupported_provider", False
    elif not api_key:
        effective, reason, ready = LOCAL, "missing_key", False
    elif not model:
        effective, reason, ready = LOCAL, "missing_model", False
    else:
        effective, reason, ready = HOSTED, "ready", True

    privacy = (
        "recorded_audio_leaves_device"
        if transcription_lane and effective == HOSTED
        else (
            "transcript_text_leaves_device"
            if effective == HOSTED else "device_only"
        )
    )
    decision = RouteDecision(
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
    if not transcription_lane and not speech_lane and provider != "local":
        decision = confirm_selected_model(
            decision, _confirmed_models_for(settings, decision)
        )
    return decision


def snapshot_inputs(
    settings: Any,
    *,
    feature: str,
    lane: str,
    context: str = "",
    context_policy: str = "none",
    context_strict: bool = False,
    local_model_ready: bool = False,
    route_decision: RouteDecision | None = None,
) -> ProcessingInputSnapshot:
    """Capture every mutable input used by one text-shaping invocation."""
    return ProcessingInputSnapshot(
        route=route_decision or snapshot(settings, feature=feature, lane=lane),
        user_name=str(_get(settings, "user_name", "") or "").strip(),
        prompt_preferences=_freeze_mapping(
            _get(settings, "prompt_prefs", {}) or {}
        ),
        primary_language=str(
            _get(
                settings,
                "primary_language",
                _get(settings, "language", ""),
            ) or ""
        ).strip().lower(),
        english_only=bool(_get(settings, "english_only", True)),
        foreign_mode=bool(_get(settings, "foreign_mode", False)),
        foreign_languages=_string_tuple(
            _get(settings, "foreign_languages", [])
        ),
        vocabulary=_freeze_mapping(_get(settings, "vocabulary", {}) or {}),
        vocabulary_terms=_string_tuple(
            _get(settings, "vocabulary_terms", [])
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


def _require_provider_identity(
    decision: RouteDecision,
    *,
    providers: dict[str, dict[str, Any]],
    expected_provider: str | None,
) -> dict[str, Any]:
    """Bind one decision to the exact provider and endpoint family."""
    if not isinstance(decision, RouteDecision):
        raise TypeError("Provider calls require an explicit RouteDecision")
    provider_info = providers.get(decision.provider)
    if (
        provider_info is None
        or (expected_provider is not None and decision.provider != expected_provider)
        or decision.endpoint_class != provider_info["endpoint_class"]
    ):
        raise HostedRouteBlocked(decision)
    return provider_info


def _require_exact_operation(
    decision: RouteDecision, *, expected_feature: str, expected_lane: str
) -> None:
    """Bind authority to one exact product surface and operation."""
    if (
        not expected_feature
        or not expected_lane
        or decision.feature != expected_feature
        or decision.lane != expected_lane
    ):
        raise HostedRouteBlocked(decision)


def _require_frozen_transport(
    decision: RouteDecision, *, api_key: Any = _UNSET, model: Any = _UNSET
) -> None:
    """Reject transport inputs that were replaced after the decision froze."""
    if api_key is not _UNSET and str(api_key or "").strip() != decision.api_key:
        raise HostedRouteBlocked(decision)
    if model is not _UNSET and str(model or "").strip() != decision.model:
        raise HostedRouteBlocked(decision)


def require_text_shaping(
    decision: RouteDecision,
    *,
    expected_feature: str,
    expected_lane: str,
    expected_provider: str | None = None,
    api_key: Any = _UNSET,
    model: Any = _UNSET,
) -> RouteDecision:
    """Authorize one exact text operation with its frozen transport inputs."""
    _require_provider_identity(
        decision, providers=_PROVIDERS, expected_provider=expected_provider
    )
    _require_exact_operation(
        decision,
        expected_feature=expected_feature,
        expected_lane=expected_lane,
    )
    _require_frozen_transport(decision, api_key=api_key, model=model)
    if decision.provider == "local":
        if (
            decision.requested_route != LOCAL
            or decision.effective_route != LOCAL
            or decision.privacy_boundary != "device_only"
            or not decision.ready
            or not decision.provider_supported
            or not decision.key_present
        ):
            raise HostedRouteBlocked(decision)
        return decision
    require_hosted(decision)
    if (
        decision.requested_route != HOSTED
        or decision.privacy_boundary != "transcript_text_leaves_device"
    ):
        raise HostedRouteBlocked(decision)
    return decision


def require_reader_speech(
    decision: RouteDecision,
    *,
    expected_lane: str,
    expected_provider: str | None = None,
    api_key: Any = _UNSET,
    model: Any = _UNSET,
) -> RouteDecision:
    """Authorize ordinary Reader speech or its distinct connection test."""
    _require_provider_identity(
        decision, providers=_SPEECH_PROVIDERS, expected_provider=expected_provider
    )
    if expected_lane not in {"reader_speech", "reader_speech_test"}:
        raise HostedRouteBlocked(decision)
    _require_exact_operation(
        decision, expected_feature="reader", expected_lane=expected_lane
    )
    _require_frozen_transport(decision, api_key=api_key, model=model)
    require_hosted(decision)
    if (
        decision.requested_route != HOSTED
        or decision.privacy_boundary != "transcript_text_leaves_device"
    ):
        raise HostedRouteBlocked(decision)
    return decision


def call_hosted(
    decision: RouteDecision,
    provider_call: Callable[..., Any],
    *args: Any,
    expected_feature: str,
    expected_lane: str,
    **kwargs: Any,
) -> Any:
    """Guard one exact hosted operation, then enter provider code."""
    require_text_shaping(
        decision,
        expected_feature=expected_feature,
        expected_lane=expected_lane,
    )
    kwargs.setdefault("route_decision", decision)
    kwargs.setdefault("expected_feature", expected_feature)
    kwargs.setdefault("expected_lane", expected_lane)
    return provider_call(*args, **kwargs)


def require_provider(
    decision: RouteDecision,
    *,
    expected_feature: str,
    expected_lane: str,
    expected_provider: str | None = None,
) -> RouteDecision:
    """Authorize one frozen provider invocation at the final call seam.

    Local endpoints remain available in device-only mode.  Every other provider
    goes through the stricter hosted guard, which rejects any non-ready route
    before adapter code can run.
    """
    if not isinstance(decision, RouteDecision):
        raise TypeError("Provider calls require an explicit RouteDecision")
    if expected_feature == "reader" and expected_lane in {
        "reader_speech", "reader_speech_test",
    }:
        return require_reader_speech(
            decision,
            expected_lane=expected_lane,
            expected_provider=expected_provider,
        )
    if expected_feature == "dictation" and expected_lane == "speech_to_text":
        raise TypeError(
            "Cloud transcription requires an explicit ProcessingInputSnapshot"
        )
    return require_text_shaping(
        decision,
        expected_feature=expected_feature,
        expected_lane=expected_lane,
        expected_provider=expected_provider,
    )


def call_provider(
    decision: RouteDecision,
    provider_call: Callable[..., Any],
    *args: Any,
    expected_feature: str,
    expected_lane: str,
    expected_provider: str | None = None,
    **kwargs: Any,
) -> Any:
    """Guard then invoke either the local endpoint or an allowed hosted one."""
    require_provider(
        decision,
        expected_feature=expected_feature,
        expected_lane=expected_lane,
        expected_provider=expected_provider,
    )
    kwargs.setdefault("route_decision", decision)
    kwargs.setdefault("expected_feature", expected_feature)
    kwargs.setdefault("expected_lane", expected_lane)
    return provider_call(*args, **kwargs)


def require_speech_to_text(
    invocation_snapshot: ProcessingInputSnapshot,
) -> ProcessingInputSnapshot:
    """Validate a frozen cloud speech-to-text invocation at its adapter seam."""
    if not isinstance(invocation_snapshot, ProcessingInputSnapshot):
        raise TypeError(
            "Cloud transcription requires an explicit ProcessingInputSnapshot"
        )
    decision = invocation_snapshot.route
    if (
        decision.feature != "dictation"
        or decision.lane != "speech_to_text"
    ):
        raise TypeError("Cloud transcription requires a speech-to-text route")
    _require_provider_identity(
        decision,
        providers=_TRANSCRIPTION_PROVIDERS,
        expected_provider=decision.provider,
    )
    require_hosted(decision)
    if (
        decision.requested_route != HOSTED
        or decision.privacy_boundary != "recorded_audio_leaves_device"
    ):
        raise HostedRouteBlocked(decision)
    return invocation_snapshot


def require_transcription(
    invocation_snapshot: ProcessingInputSnapshot,
) -> ProcessingInputSnapshot:
    """Backward-compatible name for the speech-to-text contract."""
    return require_speech_to_text(invocation_snapshot)


def settings_state(
    settings: Any, *, supported_providers: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """UI-safe saved/effective route facts from the same policy as actions."""
    plain = snapshot(
        settings, feature="dictation", lane="text",
        supported_providers=supported_providers,
    )
    action = snapshot(
        settings, feature="prompt", lane="prompt",
        supported_providers=supported_providers,
    )
    return {
        "plain_processing": plain.public_dict(),
        "action_processing": action.public_dict(),
        "local_only": action.device_only,
    }
