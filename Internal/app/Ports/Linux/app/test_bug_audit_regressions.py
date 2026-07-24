#!/usr/bin/env python3
"""Regression coverage for the 2026-07-12 full bug audit."""

import os
import queue
import tempfile
import threading
import wave

import branding
import meeting
import meeting_store
import settings
import webui_shell


def _temp_store():
    root = tempfile.mkdtemp(prefix="mumble_bug_audit_")
    return root, os.path.join(root, "meetings.json")


def test_failed_mic_open_cleans_recorder_resources():
    import sounddevice as sd

    class BrokenStream:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("simulated mic failure")

    original = sd.InputStream
    sd.InputStream = BrokenStream
    recorder = meeting.MeetingRecorder(lambda *_a, **_k: "", settings.Settings())
    try:
        try:
            recorder.start()
        except RuntimeError:
            pass
        else:
            raise AssertionError("microphone failure was not propagated")
        assert recorder._recording is False
        assert recorder._stream is None
        assert recorder._writer_thread is None
        assert recorder._writer_queue is None
        assert recorder._audio_full_path is None
    finally:
        sd.InputStream = original


def test_full_dead_writer_queue_does_not_block_finish():
    class AlwaysFull:
        def put(self, *_args, **_kwargs):
            raise queue.Full

        def put_nowait(self, *_args, **_kwargs):
            raise queue.Full

    recorder = meeting.MeetingRecorder(lambda *_a, **_k: "", settings.Settings())
    recorder._audio_filename, recorder._audio_full_path = meeting._new_meeting_audio_path()
    audio_path = recorder._audio_full_path
    with wave.open(audio_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(meeting.SAMPLE_RATE)
        wf.writeframes(b"\x00\x00")
    recorder._sample_count = 1
    recorder._writer_queue = AlwaysFull()
    recorder._writer_thread = threading.Thread(target=lambda: None)
    recorder._writer_thread.start()
    recorder._writer_thread.join()
    original_save = meeting.meeting_store.save_meeting
    meeting.meeting_store.save_meeting = lambda **_kwargs: "saved-id"
    try:
        assert recorder.finish_capture() == "saved-id"
    finally:
        meeting.meeting_store.save_meeting = original_save
        try:
            os.remove(audio_path)
        except FileNotFoundError:
            pass


def test_stereo_wav_is_downmixed_without_doubling_duration():
    path = os.path.join(tempfile.mkdtemp(prefix="mumble_stereo_"), "stereo.wav")
    frames = 16000
    with wave.open(path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes((b"\xe8\x03\x18\xfc") * frames)
    audio, sample_rate = meeting._load_wav_audio(path)
    assert sample_rate == 16000
    assert len(audio) == frames
    assert abs(float(audio.mean())) < 1e-6


def test_meeting_store_does_not_claim_failed_save():
    original_path = meeting_store.PATH
    original_replace = meeting_store.os.replace
    _root, meeting_store.PATH = _temp_store()
    meeting_store.os.replace = lambda *_a: (_ for _ in ()).throw(
        OSError("simulated disk failure"))
    try:
        mid = meeting_store.save_meeting("Audit", "audit.wav", 1, [], [])
        assert mid is None
        assert meeting_store.list_meetings() == []
    finally:
        meeting_store.os.replace = original_replace
        meeting_store.PATH = original_path


def test_settings_retry_retains_dirty_value():
    original_data_dir = branding.DATA_DIR
    original_settings_path = branding.SETTINGS_PATH
    original_replace = settings.os.replace
    root = tempfile.mkdtemp(prefix="mumble_settings_retry_")
    branding.DATA_DIR = root
    branding.SETTINGS_PATH = os.path.join(root, "settings.json")
    try:
        store = settings.Settings()
        settings.os.replace = lambda *_a: (_ for _ in ()).throw(
            OSError("simulated disk failure"))
        store.set("user_name", "Alice")
        settings.os.replace = original_replace
        store.set("autostart", False)
        assert store.get("user_name") == "Alice"
        assert settings.Settings().get("user_name") == "Alice"
    finally:
        settings.os.replace = original_replace
        branding.DATA_DIR = original_data_dir
        branding.SETTINGS_PATH = original_settings_path


def test_live_meeting_bridge_exposes_all_ui_actions():
    assert hasattr(webui_shell.Api, "meeting_extract_decisions")
    assert hasattr(webui_shell.Api, "meeting_extract_questions")
    source = open(os.path.join(os.path.dirname(__file__), "webui", "app.js"),
                  encoding="utf-8").read()
    assert 'CURRENT === "meetings" && what === "meetings"' in source
    assert "m.key_decision_count" in source
    assert "m.open_question_count" in source


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().copy().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("  ok  " + name)
            except Exception as exc:
                failures += 1
                print("  FAIL " + name + ": " + repr(exc))
    raise SystemExit(1 if failures else 0)
