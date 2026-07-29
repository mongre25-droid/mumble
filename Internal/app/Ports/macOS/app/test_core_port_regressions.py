#!/usr/bin/env python3
"""Focused regressions mirrored from the 2026-07-12 core-engine audit."""

import inspect
import io
from pathlib import Path
import threading
import time

import numpy as np
import pytest


def test_model_free_preserves_paragraphs_and_measurement_units():
    import formatting
    import model_free

    email = "Subject: Test\n\nHi Alex,\n\nBody\n\nBest regards,\nSam"
    assert "\n\nHi Alex,\n\nBody\n\n" in model_free.process(email)
    assert "10 mm wide" in model_free.process("the bolt is 10 mm wide")
    assert formatting.sanitize_hotwords("Alice Smith") == ["Alice Smith"]


def test_transcription_sanitizes_audio_and_string_hotwords(monkeypatch):
    import processing_route
    import transcription

    with pytest.raises(ValueError):
        transcription.pcm16_wav_bytes([])
    assert transcription.pcm16_wav_bytes(
        [np.nan, np.inf, -np.inf]
    ).startswith(b"RIFF")

    class Settings:
        def get(self, key, default=None):
            return {
                "pro_mode": True,
                "local_only_mode": False,
                "transcription_mode": "cloud",
                "cloud_transcription_provider": "groq",
                "groq_api_key": "key",
                "groq_transcription_model": "whisper-large-v3-turbo",
                "vocabulary_terms": "Alice Smith",
            }.get(key, default)

    captured = {}

    def send(info, key, model, wav, lang, timeout, prompt=None):
        captured["prompt"] = prompt
        return "ok"

    monkeypatch.setattr(transcription, "_transcribe_multipart", send)
    invocation = processing_route.snapshot_inputs(
        Settings(), feature="dictation", lane="speech_to_text"
    )
    assert transcription.transcribe(np.zeros(8), invocation) == "ok"
    assert captured["prompt"] == "Alice Smith"


def test_foreign_annotations_do_not_treat_urls_as_flags():
    import foreign_boost
    import islamic_terms

    marked = foreign_boost.boost(
        "https://example.com then kwasont", languages="french"
    ).text
    assert marked.startswith("https://example.com")
    assert marked.endswith("//croissant")
    assert foreign_boost.strip_flags("https://example.com and //Juz") == \
        "https://example.com and Juz"
    assert "koran//Quran" in islamic_terms.annotate_foreign(
        "https://example.com then koran"
    )


def test_ai_transport_blocks_non_http_and_assert_survives_optimization():
    import ai
    import ai.base
    from ai.transport import _json_request, retry_with_backoff

    with pytest.raises(ValueError, match="http"):
        _json_request("file:///private.txt")
    with pytest.raises(ValueError):
        retry_with_backoff(lambda: None, max_retries=-1)
    source = inspect.getsource(ai.base)
    assert "assert len(TRUNC_MARKER)" not in source
    assert "raise RuntimeError(\"stream completion markers" in source
    assert Path(ai.__file__).name == "__init__.py"
    assert Path(ai.__file__).parent.name == "ai"
    assert not Path(__file__).with_name("ai.py").exists()


def test_cache_does_not_evict_external_models(tmp_path):
    from models.cache import ModelCache

    models = tmp_path / "models"
    models.mkdir()
    outside = tmp_path / "private.gguf"
    outside.write_bytes(b"private")
    cache = ModelCache(str(models), cache_limit_gb=0)
    assert cache.register(str(outside)) is False
    assert cache.evict() == []
    assert outside.read_bytes() == b"private"


def test_downloader_enforces_expected_size_and_url_scheme(monkeypatch, tmp_path):
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
    with pytest.raises(ValueError, match="http"):
        downloader._download_file("file:///private", str(tmp_path / "x"))
    with pytest.raises(OSError, match="4 expected bytes"):
        downloader._do_download(
            "https://example.invalid/model",
            str(tmp_path / "model.gguf.part"),
            expected_size=4,
        )


def test_model_switch_queues_without_waiting_for_generation():
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

    def generate(*_args):
        entered.set()
        assert release.wait(1)
        return "done"

    manager._generate_locked = generate
    manager._check_memory_pressure_locked = lambda: None
    manager._stop_locked = lambda: None
    manager.start = lambda path, system_prompt="": path == "new.gguf"
    worker = threading.Thread(target=lambda: manager.generate("hello"), daemon=True)
    worker.start()
    assert entered.wait(1)
    started = time.monotonic()
    assert manager.switch_model("new.gguf", on_complete=completed.append) is False
    assert time.monotonic() - started < 0.2
    release.set()
    worker.join(1)
    assert not worker.is_alive()
    assert completed == [True]
