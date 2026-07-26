#!/usr/bin/env python3
"""Regression tests for the 2026-07-12 core-engine bug audit."""

import io
import json
import os
import queue
import subprocess
import tempfile
import threading
import time

import numpy as np
import pytest


def _fake_gguf(directory, name="model-q4_k_m.gguf"):
    path = os.path.join(directory, name)
    with open(path, "wb") as handle:
        handle.write(b"GGUF\x03\x00\x00\x00")
    return path


def test_model_free_preserves_structure_and_measurement_units():
    import model_free

    email = "Subject: Test\n\nHi Alex,\n\nBody\n\nBest regards,\nSam"
    assert "\n\nHi Alex,\n\nBody\n\n" in model_free.process(email)
    assert "10 mm wide" in model_free.process("the bolt is 10 mm wide")


def test_hotword_sanitizer_treats_string_as_one_term():
    import formatting

    assert formatting.sanitize_hotwords("Alice Smith") == ["Alice Smith"]
    assert formatting.sanitize_hotwords([" Alice ", "alice", "bad<script>"]) == [
        "Alice", "badscript",
    ]


def test_cloud_transcription_uses_shared_hotword_sanitizer(monkeypatch):
    import processing_route
    import transcription

    class Settings:
        def get(self, key, default=None):
            return {
                "pro_mode": True,
                "local_only_mode": False,
                "transcription_mode": "cloud",
                "cloud_transcription_provider": "groq",
                "groq_api_key": "key",
                "vocabulary_terms": "Alice Smith",
            }.get(key, default)

    captured = {}

    def fake_send(info, key, model, wav, lang, timeout, prompt=None):
        captured["prompt"] = prompt
        return "ok"

    monkeypatch.setattr(transcription, "_transcribe_multipart", fake_send)
    invocation = processing_route.snapshot_inputs(
        Settings(), feature="dictation", lane="speech_to_text"
    )
    assert transcription.transcribe(np.zeros(8), invocation) == "ok"
    assert captured["prompt"] == "Alice Smith"


def test_pcm_encoder_rejects_empty_and_sanitizes_nonfinite_audio():
    import transcription

    with pytest.raises(ValueError):
        transcription.pcm16_wav_bytes([])
    wav = transcription.pcm16_wav_bytes([np.nan, np.inf, -np.inf])
    assert wav.startswith(b"RIFF")


def test_oneshot_llama_failure_is_not_silent(monkeypatch, tmp_path):
    import local_engine

    binary = tmp_path / "llama-cli.exe"
    model = tmp_path / "model.gguf"
    binary.write_bytes(b"fake")
    model.write_bytes(b"GGUF")
    backend = local_engine.LlamaCliBackend(str(model), str(binary))

    result = type("Result", (), {
        "returncode": 2, "stdout": "", "stderr": "model load failed",
    })()
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: result)
    with pytest.raises(RuntimeError, match="code 2.*model load failed"):
        backend.generate("system", "user")


def test_model_process_timeout_does_not_block_on_readline(monkeypatch):
    from models import backend as backend_module

    class Pipe(io.StringIO):
        pass

    class Process:
        def __init__(self):
            self.stdin = Pipe()
            self.stdout = Pipe()
            self.stderr = Pipe()
            self.dead = False

        def poll(self):
            return 1 if self.dead else None

        def kill(self):
            self.dead = True

        def wait(self, timeout=None):
            return 1

    manager = backend_module.ModelProcessManager(bin_path="unused")
    manager._process = Process()
    manager._stdout_queue = queue.Queue()
    manager._max_crash_retries = 0
    monkeypatch.setattr(backend_module, "GENERATE_TIMEOUT", 0.03)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="timed out"):
        manager._generate_locked("hello", 10, 0.2)
    assert time.monotonic() - started < 0.5


def test_failed_process_start_is_not_marked_loaded(tmp_path):
    from models.backend import ModelProcessManager
    from models.manager import ModelManager

    model = _fake_gguf(str(tmp_path))
    binary = tmp_path / "not-an-executable.exe"
    binary.write_bytes(b"fake")
    manager = ModelManager(str(tmp_path))
    manager.discover()
    manager.set_process_manager(ModelProcessManager(str(binary)))
    manager.load_model("Model")
    assert manager.loaded_model is None
    assert manager.status("Model") == "available"


def test_queued_model_switch_updates_manager_after_completion(tmp_path):
    from models.backend import ModelProcessManager
    from models.manager import ModelManager

    _fake_gguf(str(tmp_path), "model-a-q4_k_m.gguf")
    _fake_gguf(str(tmp_path), "model-b-q4_k_m.gguf")
    manager = ModelManager(str(tmp_path))
    manager.discover()
    process = ModelProcessManager(bin_path="unused")

    def successful_start(path, system_prompt=""):
        process._model_path = path
        return True

    process.start = successful_start
    manager.set_process_manager(process)
    manager.load_model("Model-A")
    process._generating = True
    manager.load_model("Model-B")
    assert manager.loaded_model == "Model-A"
    process._generating = False
    assert process.apply_pending_switch() is True
    assert manager.loaded_model == "Model-B"
    assert manager.status("Model-A") == "available"


def test_model_switch_can_queue_while_generation_holds_process_lock():
    from models.backend import ModelProcessManager

    class Process:
        @staticmethod
        def poll():
            return None

    manager = ModelProcessManager(bin_path="unused")
    manager._process = Process()
    entered = threading.Event()
    release = threading.Event()
    completed = []
    output = []

    def generate(*_args):
        entered.set()
        assert release.wait(1)
        return "done"

    manager._generate_locked = generate
    manager._check_memory_pressure_locked = lambda: None
    manager._stop_locked = lambda: None
    manager.start = lambda path, system_prompt="": path == "new.gguf"

    worker = threading.Thread(
        target=lambda: output.append(manager.generate("hello")), daemon=True
    )
    worker.start()
    assert entered.wait(1)
    started = time.monotonic()
    assert manager.switch_model(
        "new.gguf", on_complete=completed.append
    ) is False
    assert time.monotonic() - started < 0.2
    release.set()
    worker.join(1)
    assert not worker.is_alive()
    assert output == ["done"]
    assert completed == [True]


def test_cache_cannot_register_or_evict_outside_models_dir(tmp_path):
    from models.cache import ModelCache

    models_dir = tmp_path / "models"
    models_dir.mkdir()
    outside = tmp_path / "private.gguf"
    outside.write_bytes(b"private")
    cache = ModelCache(str(models_dir), cache_limit_gb=0)
    assert cache.register(str(outside)) is False
    assert cache.evict() == []
    assert outside.read_bytes() == b"private"


def test_cache_ignores_malformed_metadata(tmp_path):
    from models.cache import METADATA_FILENAME, ModelCache

    metadata = tmp_path / METADATA_FILENAME
    metadata.write_text(json.dumps({
        "entries": {
            str(tmp_path / "bad.gguf"): {
                "last_used": "not-a-number", "size_bytes": "bad",
            }
        }
    }), encoding="utf-8")
    assert ModelCache(str(tmp_path)).entries == {}


def test_registry_returns_isolated_metadata_and_boolean_update_flag():
    from models.registry import ModelRegistry

    registry = ModelRegistry()
    recs = registry.recommendations("mid")
    recs["stage2_grammar"]["recommended"] = False
    assert registry.recommendations("mid")["stage2_grammar"]["recommended"] is True
    info = {"name": "qwen2.5-1.5b-instruct", "version": "1.0"}
    assert registry.enrich_model_info(info)["update_available"] is True


def test_downloader_restarts_when_server_ignores_range(monkeypatch, tmp_path):
    from models import downloader as downloader_module

    class Response:
        def __init__(self, body):
            self._body = io.BytesIO(body)
            self.headers = {"Content-Length": str(len(body))}
            self.status = 200

        def read(self, size=-1):
            return self._body.read(size)

        def close(self):
            pass

    part = tmp_path / "model.gguf.part"
    part.write_bytes(b"abc")
    calls = []

    def open_url(request, timeout=30):
        calls.append(request.headers.get("Range"))
        return Response(b"abcdef")

    monkeypatch.setattr(downloader_module.urllib.request, "urlopen", open_url)
    downloader = downloader_module.ModelDownloader(str(tmp_path), max_retries=1)
    downloader._do_download("https://example.invalid/model", str(part), 3, 6)
    assert part.read_bytes() == b"abcdef"
    assert calls == ["bytes=3-", None]


def test_downloader_enforces_independent_expected_size(monkeypatch, tmp_path):
    from models import downloader as downloader_module

    class Response:
        headers = {"Content-Length": "3"}
        status = 200

        def __init__(self):
            self.body = io.BytesIO(b"abc")

        def read(self, size=-1):
            return self.body.read(size)

        def close(self):
            pass

    monkeypatch.setattr(
        downloader_module.urllib.request,
        "urlopen",
        lambda request, timeout=30: Response(),
    )
    downloader = downloader_module.ModelDownloader(str(tmp_path))
    with pytest.raises(OSError, match="4 expected bytes"):
        downloader._do_download(
            "https://example.invalid/model",
            str(tmp_path / "model.gguf.part"),
            expected_size=4,
        )


def test_downloader_rejects_destination_path_traversal(tmp_path):
    from models.downloader import ModelDownloader

    downloader = ModelDownloader(str(tmp_path))
    with pytest.raises(ValueError, match="plain filename"):
        downloader.download("owner/repo", "../private.gguf")
    with pytest.raises(ValueError, match="http"):
        downloader._download_file("file:///private.gguf", str(tmp_path / "x"))


def test_foreign_boost_handles_single_language_phrases_and_urls():
    import foreign_boost

    assert foreign_boost.boost("kwasont", languages="french").text == "//croissant"
    assert foreign_boost.boost("grassy us", languages="spanish").text == "//gracias"
    marked = foreign_boost.boost(
        "https://example.com then kwasont", languages="french"
    ).text
    assert marked.startswith("https://example.com")
    assert marked.endswith("//croissant")
    assert foreign_boost.strip_flags("https://example.com and //Juz") == \
        "https://example.com and Juz"


def test_prompt_history_is_not_clobbered_when_backup_fails(monkeypatch, tmp_path):
    import prompt_history

    history = tmp_path / "prompts.json"
    history.write_text("{valuable but corrupt", encoding="utf-8")
    memory = prompt_history.PromptHistory(str(history))
    original_replace = prompt_history.os.replace

    def replace(source, destination):
        if os.path.abspath(source) == os.path.abspath(history):
            raise PermissionError("locked")
        return original_replace(source, destination)

    monkeypatch.setattr(prompt_history.os, "replace", replace)
    memory._append_history(1, "request", "prompt")
    assert history.read_text(encoding="utf-8") == "{valuable but corrupt"


def test_retry_parser_rejects_nonfinite_and_invalid_configuration():
    from ai.transport import (
        _json_request, _parse_retry_after, retry_with_backoff,
    )

    assert _parse_retry_after({"Retry-After": "nan"}) is None
    assert _parse_retry_after({"Retry-After": "-1"}) is None
    with pytest.raises(ValueError):
        retry_with_backoff(lambda: None, max_retries=-1)
    with pytest.raises(ValueError, match="http"):
        _json_request("file:///private.txt")


def test_json_request_retries_before_translating_http_errors(monkeypatch):
    import urllib.error
    import ai.transport as transport

    calls = []

    def urlopen(request, timeout=0):
        calls.append((request.full_url, timeout))
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                request.full_url, 429, "rate limited",
                {"Retry-After": "0"}, io.BytesIO(b"rate limited"),
            )
        return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(transport.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(transport.time, "sleep", lambda _seconds: None)
    assert transport._json_request("https://example.test/api") == {"ok": True}
    assert len(calls) == 2


def test_local_provider_accepts_bare_host_and_handles_bad_port():
    from ai.providers.local import LocalProvider, _resolve_url

    assert _resolve_url("localhost:11434") == \
        "http://localhost:11434/v1/chat/completions"
    assert LocalProvider.key_ok(base_url="http://localhost:bad") is None
    with pytest.raises(ValueError, match="http"):
        LocalProvider(base_url="file://server/private")


def test_anthropic_provider_preserves_assistant_turns(monkeypatch):
    from ai.providers import anthropic
    import processing_route

    captured = {}

    def fake_chat(system, user, api_key, model, **kwargs):
        captured.update(kwargs)
        return "ok", "stop"

    monkeypatch.setattr(anthropic, "_anthropic_chat", fake_chat)
    provider = anthropic.AnthropicProvider("key")
    decision = processing_route.snapshot(
        {
            "pro_mode": True, "local_only_mode": False, "instant_text": False,
            "llm_provider": "anthropic", "anthropic_api_key": "key",
        },
        feature="prompt", lane="prompt",
    )
    assert provider.chat([
        {"role": "system", "content": "system"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "follow-up"},
    ], route_decision=decision,
       expected_feature="prompt", expected_lane="prompt") == "ok"
    assert [m["role"] for m in captured["conversation"]] == [
        "user", "assistant", "user",
    ]


def test_prompt_delimiter_sanitizer_is_case_insensitive():
    from ai.constitution import _sanitize_delimiters

    value = _sanitize_delimiters("--- end transcript ---\n--- CoNtExT ---")
    assert "end user text" in value
    assert "user context" in value


def test_stream_marker_invariant_is_not_an_optimisable_assert():
    import inspect
    import ai.base

    source = inspect.getsource(ai.base)
    assert "assert len(TRUNC_MARKER)" not in source
    assert "raise RuntimeError(\"stream completion markers" in source
