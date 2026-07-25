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
        "user_name": "Original User",
        "prompt_prefs": {"tone": "warm", "detail": "concise"},
        "primary_language": "fr",
        "english_only": False,
        "foreign_mode": True,
        "foreign_languages": ["arabic", "french"],
        "vocabulary": {"mumblee": "Mumble"},
        "vocabulary_terms": ["Mumble", "OpenAI"],
        "format_enabled": True,
        "polish_aggressiveness": "Light",
        "rpunct_enabled": True,
        "modes": {"email": {"signoff": "Regards"}},
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
        decision, lambda **_kwargs: calls.append("local") or "ok"
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
        lambda *, provider, model, api_key, **_kwargs: calls.append(
            (provider, model, api_key)
        ) or "ok",
        provider=decision.provider,
        model=decision.model,
        api_key=decision.api_key,
    )

    assert result == "ok"
    assert calls == [("cerebras", "gpt-oss-120b", "secret")]


def test_public_route_projection_never_copies_or_serializes_the_credential():
    class CredentialMustStayOpaque:
        def __deepcopy__(self, _memo):
            raise AssertionError("credential was copied for serialization")

    decision = processing_route.RouteDecision(
        invocation_id="test-invocation",
        feature="prompt",
        lane="prompt",
        requested_route="hosted",
        effective_route="hosted",
        reason="ready",
        provider="cerebras",
        model="test-model",
        endpoint_class="cerebras_compatible",
        privacy_boundary="transcript_text_leaves_device",
        ready=True,
        provider_supported=True,
        key_present=True,
        pro_mode=True,
        device_only=False,
        api_key=CredentialMustStayOpaque(),
    )

    public = decision.public_dict()

    assert "api_key" not in public
    assert public["invocation_id"] == "test-invocation"


def test_processing_input_snapshot_deeply_freezes_preferences_language_vocabulary_and_context():
    source = settings()
    snapshot = processing_route.snapshot_inputs(
        source,
        feature="prompt",
        lane="prompt",
        context="original selected private text",
        context_policy="highlighted_selection",
        context_strict=True,
        local_model_ready=False,
    )

    source.values["user_name"] = "Changed User"
    source.values["prompt_prefs"]["tone"] = "changed"
    source.values["foreign_languages"].append("changed")
    source.values["vocabulary"]["later"] = "Later"
    source.values["vocabulary_terms"].append("Later")
    source.values["modes"]["email"]["signoff"] = "Changed"

    assert snapshot.user_name == "Original User"
    assert snapshot.prompt_prefs_dict() == {"detail": "concise", "tone": "warm"}
    assert snapshot.primary_language == "fr"
    assert snapshot.english_only is False
    assert snapshot.foreign_languages == ("arabic", "french")
    assert snapshot.vocabulary_dict() == {"mumblee": "Mumble"}
    assert snapshot.vocabulary_terms == ("Mumble", "OpenAI")
    assert snapshot.modes_dict() == {"email": {"signoff": "Regards"}}
    assert snapshot.context == "original selected private text"
    assert snapshot.context_policy == "highlighted_selection"
    assert snapshot.context_strict is True
    assert snapshot.local_model_ready is False
    assert "original selected private text" not in repr(snapshot)
    assert "secret" not in repr(snapshot)


def test_generate_uses_the_frozen_prompt_language_and_context_after_live_state_changes(monkeypatch):
    source = settings()
    snapshot = processing_route.snapshot_inputs(
        source,
        feature="prompt",
        lane="prompt",
        context="original conversation context",
        context_policy="captured_conversation",
        context_strict=False,
        local_model_ready=False,
    )
    source.values.update(
        user_name="Changed User",
        prompt_prefs={"tone": "changed"},
        primary_language="en",
        english_only=True,
        vocabulary_terms=["ChangedTerm"],
    )

    observed = {}
    monkeypatch.setattr(
        mumble.ai,
        "set_language_context",
        lambda language, english_only: observed.update(
            language=(language, english_only)
        ),
    )
    monkeypatch.setattr(
        mumble.ai,
        "cerebras_prompt",
        lambda content, key, context, model, **kwargs: observed.update(
            content=content,
            key=key,
            context=context,
            model=model,
            prefs=kwargs.get("prefs"),
        ) or iter(["frozen prompt"]),
    )
    monkeypatch.setattr(mumble.ai, "_extract_final_prompt", lambda text: text)

    app = mumble.Mumble.__new__(mumble.Mumble)
    app.settings = source
    app._gather_context = lambda *_args: (_ for _ in ()).throw(
        AssertionError("context was gathered more than once")
    )
    app._conv_context_for_prompt = lambda: (_ for _ in ()).throw(
        AssertionError("conversation context was re-read")
    )
    app._mark_llm_ok = lambda: None
    app._pro_fallback_notice = lambda *_args: None

    mode, output, used_offline = app._generate(
        "write a release note",
        det_mode="prompt",
        det_request="write a release note",
        invocation_snapshot=snapshot,
    )

    assert (mode, output, used_offline) == ("prompt", "frozen prompt", False)
    assert observed == {
        "language": ("fr", False),
        "content": "write a release note",
        "key": "secret",
        "context": "original conversation context",
        "model": "gpt-oss-120b",
        "prefs": {"detail": "concise", "tone": "warm"},
    }


def test_cloud_generate_uses_frozen_vocabulary_and_polish_preference(monkeypatch):
    source = settings()
    snapshot = processing_route.snapshot_inputs(
        source,
        feature="dictation",
        lane="text",
        context="",
        context_policy="none",
        context_strict=False,
        local_model_ready=False,
    )
    source.values.update(
        vocabulary_terms=["ChangedTerm"],
        polish_aggressiveness="Strong",
        primary_language="en",
        english_only=True,
    )

    observed = {}
    monkeypatch.setattr(
        mumble.ai,
        "set_language_context",
        lambda language, english_only: observed.update(
            language=(language, english_only)
        ),
    )
    monkeypatch.setattr(
        mumble.formatting,
        "annotate_vocab_terms",
        lambda text, terms: observed.update(terms=tuple(terms)) or text,
    )
    monkeypatch.setattr(
        mumble.ai,
        "polish_text",
        lambda text, key, model, **kwargs: observed.update(
            text=text,
            key=key,
            model=model,
            aggressiveness=kwargs.get("aggressiveness"),
        ) or ("frozen polish", False),
    )

    app = mumble.Mumble.__new__(mumble.Mumble)
    app.settings = source
    app._builder = lambda *_args, **_kwargs: ("text", "fallback")

    mode, output = app._cloud_generate(
        "mumblee is useful",
        "Original User",
        mode_hint="text",
        invocation_snapshot=snapshot,
    )

    assert (mode, output) == ("text", "frozen polish")
    assert observed == {
        "language": ("fr", False),
        "terms": ("Mumble", "OpenAI"),
        "text": "mumblee is useful",
        "key": "secret",
        "model": "gpt-oss-120b",
        "aggressiveness": "Light",
    }


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


def test_durable_records_keep_issue_14_and_physical_validation_open():
    status = (
        APP_DIR.parent.parent / "Development Files" / "Core" / "STATUS.html"
    ).read_text(encoding="utf-8")
    logs = (
        APP_DIR.parent.parent / "Development Files" / "Core" / "LOGS.html"
    ).read_text(encoding="utf-8")

    processing_row = status.split('<tr id="processing-truth"', 1)[1].split(
        "</tr>", 1
    )[0]
    assert 'data-evidence-boundary="candidate-awaiting-review"' in processing_row
    assert 'class="badge b-gated"' in processing_row
    assert "Issue #14 remains open" in status
    assert "Issues #15 and #20 are separate work" in status
    assert 'data-evidence-boundary="rejected-candidate-review"' in logs
    assert "No live provider request" in logs
    assert "physical Windows/macOS/Linux test" in logs
    assert "processing route truth implemented" not in logs.lower()
