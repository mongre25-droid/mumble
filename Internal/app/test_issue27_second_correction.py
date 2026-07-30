"""Focused shared/Windows regressions for the second Issue #27 correction."""

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

import dictation_session
import mumble as mumble_module
from mumble import Mumble


def test_shared_authority_preserves_mixed_timed_and_untimed_segments():
    committed, tentative = dictation_session.reconcile_timestamped_segment(
        [], [], [
            {"word": "alpha", "start": 0.2, "end": 0.8},
            {"word": "draft", "start": 9.2, "end": 9.8},
        ], index=0, segment_samples=100, overlap_samples=10,
        sample_rate=10)
    merged = dictation_session.merge_stable_prefix(
        " ".join(committed), "draft beta")
    committed, tentative = merged.split(), []

    committed, tentative = dictation_session.reconcile_timestamped_segment(
        committed, tentative, [
            {"word": "beta", "start": 0.2, "end": 0.8},
            {"word": "charlie", "start": 0.7, "end": 0.9},
            {"word": "gamma", "start": 1.2, "end": 1.8},
        ], index=2, segment_samples=100, overlap_samples=10,
        sample_rate=10, previous_text_authoritative=True)

    assert " ".join(committed + tentative) == (
        "alpha draft beta charlie gamma")


def test_windows_selected_cloud_route_owns_timed_and_silent_results():
    mumble = Mumble.__new__(Mumble)
    snapshot = SimpleNamespace(
        route=SimpleNamespace(ready=True, cloud_augmented=True))
    mumble._dictation_session = object()
    mumble._durable_transcription_snapshot = snapshot
    mumble._transcription_snapshot = mock.Mock(
        side_effect=AssertionError("durable route was not frozen"))
    mumble._cloud_transcription_on = lambda route: bool(
        route.ready and route.cloud_augmented)
    mumble._local_transcribe = mock.Mock(
        side_effect=AssertionError("valid cloud result fell back locally"))
    mumble._cloud_transcribe = mock.Mock(return_value="")

    assert mumble._transcribe(np.zeros(10), want_words=True) == ("", [])
    mumble._local_transcribe.assert_not_called()

    mumble._cloud_transcribe.return_value = None
    mumble._local_transcribe = mock.Mock(return_value=("local fallback", []))
    assert mumble._transcribe(np.zeros(10), want_words=True) == (
        "local fallback", [])
    mumble._local_transcribe.assert_called_once()


def test_windows_silent_cloud_result_is_traced_as_success(monkeypatch):
    mumble = Mumble.__new__(Mumble)
    snapshot = SimpleNamespace(route=SimpleNamespace(provider="groq"))
    trace = []
    mumble._trace_next_inference_ordinal = lambda: 1
    mumble._trace_mark = lambda event, **facts: trace.append((event, facts))
    mumble._cloud_stt_failed = False
    monkeypatch.setattr(
        mumble_module.transcription, "transcribe", lambda *_args: "")

    assert mumble._cloud_transcribe(np.zeros(10), snapshot) == ""
    finished = [facts for event, facts in trace
                if event == "inference_finished"]
    assert finished[-1]["success"] is True


def test_linux_durable_contract_is_synchronized_from_windows_authority():
    root = Path(__file__).resolve().parent
    authority = (root / "dictation_session.py").read_text(encoding="utf-8")
    linux = (root / "Ports" / "Linux" / "app" /
             "dictation_session.py").read_text(encoding="utf-8")

    assert linux == authority
