"""Focused Linux integration checks for the shared durable session."""

import tempfile

import numpy as np

from linux_dictation import DurableLinuxCapture
from mumble_linux import Mumble


def test_capture_seals_bounded_ordered_segments_and_replays_audio_once():
    with tempfile.TemporaryDirectory() as root:
        capture = DurableLinuxCapture(
            root, sample_rate=10, segment_seconds=2, queue_blocks=8,
            session_id="c" * 32)
        source = np.linspace(-0.5, 0.5, 55, dtype=np.float32)
        assert capture.accept(source[:30]) == 30
        assert capture.accept(source[30:]) == 25
        manifest = capture.finish()
        assert [row["sample_count"] for row in manifest["segments"]] == [20, 20, 15]
        recovered = np.concatenate(list(capture.iter_audio()))
        assert len(recovered) == 55


def test_empty_microphone_open_failure_can_remove_its_session():
    with tempfile.TemporaryDirectory() as root:
        capture = DurableLinuxCapture(root, sample_rate=10,
                                      session_id="d" * 32)
        assert capture.discard_empty() is True


def test_timestamped_overlap_revises_the_segment_boundary_once():
    first = Mumble._reconcile_timestamped_segment(
        [], [], [
            {"word": "hello", "start": 0.5, "end": 1.5},
            {"word": "wor", "start": 9.2, "end": 9.8},
        ], index=0, segment_samples=100, overlap_samples=10,
        sample_rate=10)
    committed, tentative = first
    assert committed == ["hello"]
    assert tentative == ["wor"]
    second = Mumble._reconcile_timestamped_segment(
        committed, tentative, [
            {"word": "world", "start": 0.2, "end": 0.8},
            {"word": "again", "start": 1.2, "end": 1.8},
        ], index=1, segment_samples=100, overlap_samples=10,
        sample_rate=10)
    assert second == (["hello", "world", "again"], [])


def test_microphone_open_failure_discards_the_empty_durable_writer():
    source = open("mumble_linux.py", encoding="utf-8").read()
    start = source[source.index("def start_recording"):
                   source.index("def stop_recording")]
    assert "durable.discard_empty()" in start
