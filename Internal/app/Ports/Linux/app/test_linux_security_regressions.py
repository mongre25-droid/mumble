"""Focused regressions for Linux security/reliability audit findings."""

import io
import json
import os
import stat
import sys
import threading

import pytest

import branding
import meeting
from ai.transport import (
    iter_response_lines_limited,
    read_response_limited,
)
from prompt_history import PromptHistory


class _Response(io.BytesIO):
    def __init__(self, body, content_length=None):
        super().__init__(body)
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)


def test_http_response_limits_reject_declared_and_streamed_overflow():
    with pytest.raises(RuntimeError, match="safety limit"):
        read_response_limited(_Response(b"", 99), 10)
    with pytest.raises(RuntimeError, match="line exceeds"):
        list(iter_response_lines_limited(
            _Response(b"x" * 20 + b"\n"), max_bytes=100,
            max_line_bytes=10,
        ))


def test_prompt_history_never_keeps_or_injects_live_context(tmp_path):
    thread_path = tmp_path / "prompt_thread.json"
    history_path = str(tmp_path / "prompts.json")
    thread_path.write_text('[{"request":"legacy secret"}]', "utf-8")
    controller = PromptHistory(history_path)
    web_shell = PromptHistory(history_path)
    assert not thread_path.exists()
    assert controller.record("make a plan", "A detailed plan")
    assert not hasattr(controller, "recent")
    assert web_shell.clear_history()
    assert controller.all_prompts() == []
    assert json.loads((tmp_path / "prompts.json").read_text("utf-8")) == []


def test_selected_deep_mode_runs_after_durable_transcription(monkeypatch):
    record = {
        "id": "m1", "status": "processing", "audio_path": "meeting.wav",
        "processing_mode": "deep", "duration_sec": 1,
    }
    updates = []
    deep_calls = []
    monkeypatch.setattr(meeting.meeting_store, "get_meeting", lambda _mid: record)
    monkeypatch.setattr(
        meeting.meeting_store, "update_meeting",
        lambda mid, **fields: updates.append((mid, fields)) or True,
    )
    monkeypatch.setattr(meeting, "_resolve_audio_path", lambda _path: "/tmp/a.wav")
    monkeypatch.setattr(
        meeting, "_process_wav_path",
        lambda *_args, **_kwargs: ([{"text": "hello"}], [{"label": "Speaker 1"}]),
    )
    monkeypatch.setattr(
        meeting, "process_meeting_deep",
        lambda mid, settings: deep_calls.append((mid, settings)) or {"summary": "ok"},
    )
    recorder = meeting.MeetingRecorder.__new__(meeting.MeetingRecorder)
    recorder._settings = object()
    recorder._transcribe = lambda *_args, **_kwargs: ""
    recorder._island_cb = None
    assert recorder._process_pending_once("m1") == "m1"
    assert deep_calls and deep_calls[0][0] == "m1"
    assert any(fields.get("status") == "ready" for _mid, fields in updates)


def test_deep_analysis_is_cross_process_claimed_single_flight(
        tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def slow_analysis(_meeting_id, _settings):
        entered.set()
        assert release.wait(2)
        return {"summary": "done"}

    monkeypatch.setattr(branding, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(meeting, "_process_meeting_deep_locked", slow_analysis)
    result = []
    worker = threading.Thread(
        target=lambda: result.append(meeting.process_meeting_deep("m1", object())))
    worker.start()
    assert entered.wait(1)
    assert meeting.process_meeting_deep(
        "m1", object()) is meeting.DEEP_IN_PROGRESS
    assert meeting.extract_action_items(
        "m1", object()) is meeting.ANALYSIS_IN_PROGRESS
    release.set()
    worker.join(2)
    assert result == [{"summary": "done"}]


def test_pywebview_host_waits_for_bridge_instead_of_mock_boot():
    here = os.path.dirname(__file__)
    source = open(
        os.path.join(here, "webui", "app.js"), encoding="utf-8").read()
    shell = open(
        os.path.join(here, "webui_shell.py"), encoding="utf-8").read()
    assert '"webui", "index.html"' in shell
    assert "else if (MOCK_PREVIEW)" in source
    assert 'get("mock") === "1"' in source
    assert 'window.addEventListener("pywebviewready"' in source
    assert 'window.__mumbleBootBackend = HAS_PY() ? "python" : "mock"' in source


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="requires Linux AF_UNIX peer credentials")
def test_linux_ipc_is_private_same_user_and_live_socket_is_preserved(
        tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    os.chmod(runtime, 0o700)
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    os.chmod(data, 0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(branding, "DATA_DIR", str(data))

    server = branding.ipc_bind_server("controller", 49519)
    address = branding.ipc_address("controller", 49519)
    try:
        assert isinstance(address, str)
        assert stat.S_IMODE(os.lstat(os.path.dirname(address)).st_mode) == 0o700
        assert stat.S_IMODE(os.lstat(address).st_mode) == 0o600
        client = branding.ipc_connect("controller", 49519)
        conn, _ = server.accept()
        try:
            assert branding.ipc_peer_is_current_user(client)
            assert branding.ipc_peer_is_current_user(conn)
        finally:
            conn.close()
            client.close()
        with pytest.raises(OSError):
            branding.ipc_bind_server("controller", 49519)
        assert os.path.exists(address)
    finally:
        server.close()
        try:
            os.unlink(address)
        except OSError:
            pass


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="requires Linux AF_UNIX sockets")
def test_linux_ipc_reclaims_refused_stale_socket(tmp_path, monkeypatch):
    import socket

    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    os.chmod(runtime, 0o700)
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    os.chmod(data, 0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(branding, "DATA_DIR", str(data))
    address = branding.ipc_address("instance", 49517)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(address)
    stale.close()
    server = branding.ipc_bind_server("instance", 49517)
    try:
        assert os.path.exists(address)
    finally:
        server.close()
        os.unlink(address)


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="requires POSIX symlinks/ownership")
def test_linux_ipc_rejects_symlink_runtime_leaf(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    os.chmod(runtime, 0o700)
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    os.chmod(data, 0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(branding, "DATA_DIR", str(data))
    identity = branding.hashlib.sha256(
        os.path.abspath(str(data)).encode("utf-8", "surrogatepass")
    ).hexdigest()[:12]
    (runtime / f"mumble-vtt-{identity}").symlink_to(data, target_is_directory=True)
    with pytest.raises(RuntimeError, match="not owned"):
        branding.ipc_runtime_dir()
