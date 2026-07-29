"""Narrow regressions for the accepted #14/#15 convergence seam.

These checks keep the immutable processing authority and immutable insertion
identity together through delayed shaping without calling a real provider or
touching the Windows clipboard.
"""

import mumble
import processing_route
from insertion import TargetContext, TargetLease


class MemorySettings:
    def __init__(self):
        self.values = {
            "pro_mode": True,
            "local_only_mode": False,
            "instant_text": False,
            "format_enabled": True,
            "llm_provider": "cerebras",
            "cerebras_api_key": "test-only-key",
            "cerebras_model": "gpt-oss-120b",
            "_confirmed_text_models": {
                "cerebras": {
                    "credential_identity": processing_route.model_credential_identity(
                        "cerebras", "test-only-key"
                    ),
                    "generation": 1,
                    "confirmed_generation": 1,
                    "state": "confirmed",
                    "models": ["gpt-oss-120b"],
                }
            },
            "user_name": "Test User",
            "prompt_prefs": {},
            "primary_language": "en",
            "english_only": True,
            "foreign_languages": [],
            "vocabulary": {},
            "vocabulary_terms": [],
            "modes": {},
        }

    def get(self, key, default=None):
        return self.values.get(key, default)

    def authority_read(self):
        return dict(self.values)


def _snapshot(settings):
    return processing_route.snapshot_inputs(
        settings,
        feature="dictation",
        lane="text",
        context="",
        context_policy="dictation",
        context_strict=False,
        local_model_ready=False,
    )


def _lease():
    target = TargetContext(101, 201, 301, 401, "medium", "Edit", True)
    return TargetLease(target, "dictation", "stop", False)


def test_generate_forwards_route_snapshot_and_insertion_identity(monkeypatch):
    settings = MemorySettings()
    snapshot = _snapshot(settings)
    lease = _lease()
    operation_id = "a" * 32
    controller = mumble.Mumble.__new__(mumble.Mumble)
    controller.settings = settings
    controller._mark_llm_ok = lambda: None
    captured = {}

    def call_provider(decision, callback, *args, **kwargs):
        captured.update(
            decision=decision, callback=callback, args=args, kwargs=kwargs
        )
        return "text", "combined result"

    monkeypatch.setattr(processing_route, "call_provider", call_provider)

    result = controller._generate(
        "raw words",
        invocation_snapshot=snapshot,
        route_decision=snapshot.route,
        target_lease=lease,
        operation_id=operation_id,
    )

    assert result == ("text", "combined result", False)
    assert captured["decision"] is snapshot.route
    assert captured["kwargs"]["invocation_snapshot"] is snapshot
    assert captured["kwargs"]["target_lease"] is lease
    assert captured["kwargs"]["operation_id"] == operation_id
    assert captured["kwargs"]["expected_feature"] == "dictation"
    assert captured["kwargs"]["expected_lane"] == "text"


def test_second_opinion_reroute_keeps_both_authorities():
    settings = MemorySettings()
    snapshot = _snapshot(settings)
    lease = _lease()
    operation_id = "b" * 32
    controller = mumble.Mumble.__new__(mumble.Mumble)
    captured = {}

    def cloud_generate(*args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return "email", "rerouted result"

    controller._cloud_generate = cloud_generate
    cfg = {
        "key": "test-only-key",
        "provider": "cerebras",
        "model": "gpt-oss-120b",
        "url": "https://example.invalid",
        "supported": True,
    }

    result = controller._handle_second_opinion(
        "clean words",
        "email",
        "high",
        False,
        "raw words",
        "Test User",
        "",
        {},
        False,
        cfg=cfg,
        prompt_cfg=cfg,
        invocation_snapshot=snapshot,
        target_lease=lease,
        operation_id=operation_id,
    )

    assert result == ("email", "rerouted result")
    assert captured["kwargs"]["invocation_snapshot"] is snapshot
    assert captured["kwargs"]["target_lease"] is lease
    assert captured["kwargs"]["operation_id"] == operation_id
    assert captured["kwargs"]["expected_feature"] == "email"
    assert captured["kwargs"]["expected_lane"] == "email"
