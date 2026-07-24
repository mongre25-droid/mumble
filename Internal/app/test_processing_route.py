"""Public contract tests for one truthful text-processing route."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import meeting
import mumble
import processing_route
import webui_shell


APP_DIR = Path(__file__).resolve().parent


class MemorySettings:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def settings(**overrides):
    values = {
        "pro_mode": True,
        "local_only_mode": False,
        "instant_text": False,
        "llm_provider": "cerebras",
        "cerebras_api_key": "secret",
        "cerebras_model": "gpt-oss-120b",
    }
    values.update(overrides)
    return MemorySettings(**values)


@pytest.mark.parametrize("feature,lane", [
    ("dictation", "text"),
    ("prompt", "prompt"),
    ("email", "email"),
    ("reply", "reply"),
    ("deck", "deck_reason"),
    ("meetings", "meeting_summary"),
    ("reader", "reader_summary"),
])
def test_every_named_surface_gets_the_same_complete_immutable_snapshot(feature, lane):
    decision = processing_route.snapshot(settings(), feature=feature, lane=lane)

    assert decision.requested_route == "hosted"
    assert decision.effective_route == "hosted"
    assert decision.reason == "ready"
    assert decision.provider == "cerebras"
    assert decision.model == "gpt-oss-120b"
    assert decision.privacy_boundary == "transcript_text_leaves_device"
    assert decision.ready is True
    assert decision.feature == feature
    assert decision.lane == lane
    with pytest.raises(FrozenInstanceError):
        decision.effective_route = "local"


@pytest.mark.parametrize("overrides,reason", [
    ({"local_only_mode": True}, "device_only"),
    ({"pro_mode": False}, "hosted_processing_off"),
    ({"cerebras_api_key": ""}, "missing_key"),
    ({"llm_provider": "unsupported"}, "unsupported_provider"),
])
def test_final_provider_guard_proves_blocked_routes_make_zero_hosted_calls(overrides, reason):
    calls = []
    decision = processing_route.snapshot(
        settings(**overrides), feature="deck", lane="deck_reason"
    )

    with pytest.raises(processing_route.HostedRouteBlocked) as exc:
        processing_route.call_hosted(decision, lambda: calls.append("called"))

    assert decision.requested_route == "hosted"
    assert decision.effective_route == "local"
    assert decision.reason == reason
    assert decision.privacy_boundary == "device_only"
    assert decision.ready is False
    assert calls == []
    assert exc.value.reason == reason


def test_plain_instant_dictation_is_an_explicit_local_request():
    decision = processing_route.snapshot(
        settings(instant_text=True), feature="dictation", lane="text"
    )

    assert decision.requested_route == "local"
    assert decision.effective_route == "local"
    assert decision.reason == "instant_text"
    assert decision.ready is True


def test_selected_local_provider_remains_available_without_a_key_or_hosted_mode():
    decision = processing_route.snapshot(
        settings(llm_provider="local", local_api_key="", pro_mode=False),
        feature="meetings", lane="meeting_summary",
    )
    calls = []

    assert decision.requested_route == "local"
    assert decision.effective_route == "local"
    assert decision.reason == "local_provider"
    assert decision.ready is True
    assert processing_route.call_provider(
        decision, lambda: calls.append("local") or "ok"
    ) == "ok"
    assert calls == ["local"]


def test_snapshot_freezes_the_exact_provider_configuration_for_the_invocation():
    source = settings()
    decision = processing_route.snapshot(source, feature="prompt", lane="prompt")
    source.values.update(
        llm_provider="openrouter",
        cerebras_api_key="changed",
        cerebras_model="changed-model",
    )
    calls = []

    result = processing_route.call_hosted(
        decision,
        lambda *, provider, model, api_key: calls.append(
            (provider, model, api_key)
        ) or "ok",
        provider=decision.provider,
        model=decision.model,
        api_key=decision.api_key,
    )

    assert result == "ok"
    assert calls == [("cerebras", "gpt-oss-120b", "secret")]


@pytest.mark.parametrize("overrides", [
    {"local_only_mode": True},
    {"pro_mode": False},
    {"cerebras_api_key": ""},
    {"llm_provider": "unsupported"},
])
def test_meeting_final_provider_seam_never_calls_hosted_when_forbidden(monkeypatch, overrides):
    calls = []
    monkeypatch.setattr(
        meeting.ai,
        "cerebras_chat",
        lambda *_args, **_kwargs: calls.append("called") or "should not happen",
    )
    context = meeting._analysis_context(
        settings(**overrides), feature="meetings", lane="meeting_summary"
    )

    with pytest.raises(processing_route.HostedRouteBlocked):
        meeting._analysis_call(context, "system", "user", 100, 10)

    assert calls == []


@pytest.mark.parametrize("overrides", [
    {"local_only_mode": True},
    {"pro_mode": False},
    {"cerebras_api_key": ""},
    {"llm_provider": "unsupported"},
])
def test_reader_final_provider_seam_never_calls_hosted_when_forbidden(monkeypatch, overrides):
    calls = []
    monkeypatch.setattr(
        webui_shell.ai,
        "cerebras_chat",
        lambda *_args, **_kwargs: calls.append("called") or "should not happen",
    )
    api = webui_shell.Api.__new__(webui_shell.Api)
    api.settings = settings(**overrides)

    result = api.reader_summarize("private document")

    assert result["ok"] is False
    assert result["route"]["effective_route"] == "local"
    assert calls == []


@pytest.mark.parametrize("overrides", [
    {"local_only_mode": True},
    {"pro_mode": False},
    {"cerebras_api_key": ""},
    {"llm_provider": "unsupported"},
])
def test_deck_final_provider_seam_never_calls_hosted_when_forbidden(monkeypatch, overrides):
    calls = []
    monkeypatch.setattr(
        mumble.ai,
        "cerebras_intent",
        lambda *_args, **_kwargs: calls.append("called") or iter(["should not happen"]),
    )
    app = mumble.Mumble.__new__(mumble.Mumble)
    app.settings = settings(**overrides)
    app.island = None
    app._notify = lambda *_args: None
    app._idle = lambda: None

    app._run_deck_job_impl(
        [{"source": "transcript", "text": "private material"}],
        "Summarize faithfully",
        "Summarize",
        None,
    )

    assert calls == []


def test_ready_meeting_reader_and_deck_use_the_frozen_provider_once(monkeypatch):
    meeting_calls = []
    monkeypatch.setattr(
        meeting.ai,
        "cerebras_chat",
        lambda *_args, **kwargs: meeting_calls.append(kwargs) or "meeting result",
    )
    meeting_context = meeting._analysis_context(
        settings(), feature="meetings", lane="meeting_summary"
    )
    assert meeting._analysis_call(
        meeting_context, "system", "private transcript", 100, 10
    ) == "meeting result"
    assert len(meeting_calls) == 1
    assert meeting_calls[0]["model"] == "gpt-oss-120b"

    reader_calls = []
    monkeypatch.setattr(
        webui_shell.ai,
        "cerebras_chat",
        lambda *_args, **kwargs: reader_calls.append(kwargs) or "reader result",
    )
    api = webui_shell.Api.__new__(webui_shell.Api)
    api.settings = settings()
    reader_result = api.reader_summarize("private document")
    assert reader_result["ok"] is True
    assert reader_result["route"]["feature"] == "reader"
    assert len(reader_calls) == 1

    deck_calls = []
    monkeypatch.setattr(
        mumble.ai,
        "cerebras_intent",
        lambda *_args, **kwargs: deck_calls.append(kwargs) or iter(["deck result"]),
    )
    app = mumble.Mumble.__new__(mumble.Mumble)
    app.settings = settings()
    app.island = None
    app._notify = lambda *_args: None
    app._idle = lambda: None
    app._collect_text = lambda chunks: "".join(chunks)
    app._mark_llm_ok = lambda: None
    app._run_deck_job_impl(
        [{"source": "transcript", "text": "private material"}],
        "Summarize faithfully",
        "Summarize",
        None,
    )
    assert len(deck_calls) == 1


def test_reader_uses_a_selected_local_provider_without_external_egress(monkeypatch):
    calls = []
    monkeypatch.setattr(
        webui_shell.ai,
        "cerebras_chat",
        lambda *_args, **kwargs: calls.append(kwargs) or "local summary",
    )
    api = webui_shell.Api.__new__(webui_shell.Api)
    api.settings = settings(llm_provider="local", local_api_key="", pro_mode=False)

    result = api.reader_summarize("private document")

    assert result["ok"] is True
    assert result["route"]["provider"] == "local"
    assert result["route"]["privacy_boundary"] == "device_only"
    assert len(calls) == 1


def test_settings_uses_truthful_stage_effect_and_route_disclosure_language():
    html = (APP_DIR / "webui" / "index.html").read_text(encoding="utf-8")
    js = (APP_DIR / "webui" / "app.js").read_text(encoding="utf-8")

    assert "1 · Speech to text" in html
    assert "2 · Text shaping" in html
    assert '<option value="lite">Light effects</option>' in html
    assert '<option value="standard">Standard effects</option>' in html
    assert '<option value="enhanced">Full effects</option>' in html
    assert '<option value="lite">Basic</option>' not in html
    assert '<option value="enhanced">Enhanced</option>' not in html
    for label in (
        "Saved choice",
        "Effective route",
        "Input and engine",
        "Location",
        "What leaves this device",
        "Speed, quality, and cost",
    ):
        assert label in html
    assert "SETTINGS_HYDRATION_VERSION" in js
    assert "SETTINGS_MUTATION_VERSION" in js
    assert 'setSettingsHydrationState("loading")' in js
    assert '"error",\n      "Your existing configuration' in js
    assert 'setSettingsHydrationState("ready")' in js
