#!/usr/bin/env python3
"""Regression coverage for the 2026-07-12 full bug audit."""

import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import wave

import branding
import clipboard as clipboard_mod
import favorites as favorites_mod
import history as history_mod
import meeting
import meeting_store
import presets as presets_mod
import prompt_history
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
        assert store.set("user_name", "Alice") is False
        settings.os.replace = original_replace
        assert store.set("autostart", False) is True
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


def test_binding_error_identifies_unknown_mouse_button_without_driver():
    import bindings

    original = bindings.HAVE_MOUSE
    bindings.HAVE_MOUSE = False
    try:
        ok, message = bindings.validate("mouse:bogus")
        assert ok is False
        assert "mouse:x2" in message
    finally:
        bindings.HAVE_MOUSE = original


def test_history_save_failure_rolls_back_and_reports_failure():
    root = tempfile.mkdtemp(prefix="mumble_history_failure_")
    json_path = os.path.join(root, "history.json")
    text_path = os.path.join(root, "transcripts.txt")
    store = history_mod.History(json_path, text_path, 20)
    first = store.add("first durable result", "text")
    assert first is not None
    original_replace = history_mod.os.replace
    history_mod.os.replace = lambda *_a: (_ for _ in ()).throw(
        OSError("simulated history disk failure"))
    try:
        assert store.add("must not be claimed", "text") is None
        assert [row["text"] for row in store.recent(10)] == [
            "first durable result"
        ]
        assert store.clear() is False
    finally:
        history_mod.os.replace = original_replace
    assert [row["text"] for row in history_mod.History(
        json_path, text_path, 20).recent(10)] == ["first durable result"]


def test_clipboard_delete_failure_preserves_metadata_and_reports_failure():
    root = tempfile.mkdtemp(prefix="mumble_clipboard_failure_")
    path = os.path.join(root, "clipboard.json")
    store = clipboard_mod.Clipboard(
        path, maxlen=20, img_dir=os.path.join(root, "images"))
    assert store._add_text("durable clipboard item") is True
    item = store.recent(1)[0]
    original_replace = clipboard_mod.os.replace
    clipboard_mod.os.replace = lambda *_a: (_ for _ in ()).throw(
        OSError("simulated clipboard disk failure"))
    try:
        assert store.delete_match(item["stamp"], item["text"]) is False
        assert store.clear() is False
        assert store.recent(1)[0]["text"] == "durable clipboard item"
    finally:
        clipboard_mod.os.replace = original_replace
    reloaded = clipboard_mod.Clipboard(
        path, maxlen=20, img_dir=os.path.join(root, "images"))
    assert reloaded.recent(1)[0]["text"] == "durable clipboard item"


def test_prompt_history_serialises_independent_store_instances():
    root = tempfile.mkdtemp(prefix="mumble_prompt_concurrency_")
    path = os.path.join(root, "prompts.json")
    stores = [prompt_history.PromptHistory(path), prompt_history.PromptHistory(path)]
    barrier = threading.Barrier(2)
    errors = []

    def write_rows(store, prefix):
        try:
            barrier.wait(timeout=5)
            for index in range(25):
                assert store.record(
                    f"request-{prefix}-{index}", f"prompt-{prefix}-{index}")
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=write_rows, args=(stores[0], "a")),
        threading.Thread(target=write_rows, args=(stores[1], "b")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    rows = prompt_history.PromptHistory(path).all_prompts(newest_first=False)
    assert len(rows) == 50
    assert len({row["request"] for row in rows}) == 50


def test_favorite_save_failure_preserves_state_and_is_not_success():
    root = tempfile.mkdtemp(prefix="mumble_favorite_failure_")
    path = os.path.join(root, "favorites.json")
    store = favorites_mod.Favorites(path)
    assert store.toggle("keep me") is True
    original_replace = favorites_mod.os.replace
    favorites_mod.os.replace = lambda *_a: (_ for _ in ()).throw(
        OSError("simulated favorite disk failure"))
    try:
        assert store.toggle("keep me") is None
        assert store.is_fav("keep me") is True
    finally:
        favorites_mod.os.replace = original_replace
    assert favorites_mod.Favorites(path).is_fav("keep me") is True

    class FailedFavorites:
        def toggle(self, *_args):
            return None

    api = webui_shell.Api.__new__(webui_shell.Api)
    api._favs = lambda: FailedFavorites()
    result = api.toggle_favorite("keep me")
    assert result["ok"] is False


def test_meeting_store_serialises_controller_and_webview_writers():
    root = tempfile.mkdtemp(prefix="mumble_meeting_concurrency_")
    path = os.path.join(root, "meetings.json")
    code = (
        "import sys, meeting_store; "
        "meeting_store.PATH=sys.argv[1]; "
        "prefix=sys.argv[2]; "
        "rows=[meeting_store.save_meeting(prefix+str(i), '', 1, [], []) "
        "for i in range(20)]; "
        "raise SystemExit(0 if all(rows) else 2)"
    )
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    app_dir = str(Path(__file__).resolve().parent)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (app_dir, env.get("PYTHONPATH")) if part
    )
    workers = [
        subprocess.Popen([sys.executable, "-c", code, path, prefix], env=env)
        for prefix in ("controller-", "webview-")
    ]
    for worker in workers:
        assert worker.wait(timeout=30) == 0
    original_path = meeting_store.PATH
    try:
        meeting_store.PATH = path
        rows = meeting_store.list_meetings()
    finally:
        meeting_store.PATH = original_path
    assert len(rows) == 40
    assert len({row["id"] for row in rows}) == 40


def test_web_bridge_propagates_store_clear_failures():
    class FailedStore:
        def clear(self):
            return False

        def clear_history(self):
            return False

    api = webui_shell.Api.__new__(webui_shell.Api)
    api._history = lambda: FailedStore()
    api._clipboard = lambda: FailedStore()
    api._prompts = lambda: FailedStore()
    assert api.clear_transcripts() == {"ok": False}
    assert api.clear_clipboard() == {"ok": False}
    assert api.clear_prompts() == {"ok": False}

    class FailedSettings:
        def get(self, _key, default=None):
            return default

        def set(self, _key, _value):
            return False

    api.settings = FailedSettings()
    result = api.set_setting("user_name", "Not durable")
    assert result["ok"] is False
    assert result["persisted"] is False

    original_save = presets_mod.save_custom
    presets_mod.save_custom = lambda _rows: False
    try:
        result = api.save_presets([{
            "slot": len(presets_mod.BUILTIN) + 1,
            "title": "Audit",
            "description": "",
            "instruction": "Test",
        }])
        assert result["ok"] is False
    finally:
        presets_mod.save_custom = original_save


def test_web_history_waits_for_backend_before_claiming_delete_or_clear():
    source = open(os.path.join(os.path.dirname(__file__), "webui", "app.js"),
                  encoding="utf-8").read()
    assert 'const result = await call("delete_transcript", stamp, text);' in source
    assert 'const result = await call("delete_clip", stamp, text, imageHash);' in source
    assert 'if (!result || result.ok !== true)' in source
    assert 'if (result && result.ok === true) HX.transcripts = [];' in source
    assert 'if (result && result.ok === true) HX.clipboard = [];' in source
    assert 'if (result && result.ok === true) HX.prompts = [];' in source
    assert 'if (!result || result.ok !== true)' in source
    assert 'const on = !!result.fav;' in source
    assert 'const result = await call("save_presets", rows);' in source
    assert '"Couldn\'t save custom presets"' in source


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
