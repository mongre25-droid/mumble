#!/usr/bin/env python3
"""Regression contracts for the 2026-07-15 macOS-only audit."""

import json
import os
import re
import tempfile
from pathlib import Path

import branding
import cloud_sync
import settings


APP_DIR = Path(__file__).resolve().parent
PORT_DIR = APP_DIR.parent


def test_mac_settings_keep_native_shortcuts_without_retired_products():
    defaults = settings.DEFAULTS
    assert defaults["hotkey"] == "ctrl+option+d"
    assert defaults["quick_paste_hotkey"] == "ctrl+option+v"
    assert defaults["history_hotkey"] == "ctrl+option+h"
    assert defaults["web_search_hotkey"] == "ctrl+option+s"
    assert defaults["instant_text"] is True
    assert defaults["local_llm_enabled"] is False
    assert defaults["reader_tts_provider"] == "openrouter"
    assert defaults["reader_voice"] == "Fenrir"
    for retired in (
        "hardware_tier",
        "hardware_tier_resolved",
        "prompt_memory",
        "prompt_provider",
        "prompt_provider_cerebras_openrouter_applied",
        "mode_key",
        "mode_button_enabled",
        "mode_key_v2_applied",
        "prompt_keywords",
    ):
        assert retired not in defaults
        assert retired in settings.REMOVED_SETTINGS


def test_mac_settings_migration_removes_retired_keys_and_repairs_shortcuts():
    old_settings_path = branding.SETTINGS_PATH
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "settings.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({
                "hotkey": "ctrl+windows",
                "quick_paste_hotkey": "ctrl+alt+v",
                "history_hotkey": "ctrl+alt+d",
                "search_hotkey": "ctrl+alt+s",
                "prompt_memory": True,
                "prompt_provider": "openai",
                "hardware_tier": "powerful",
                "mode_key": "right shift",
                "mode_button_enabled": True,
                "prompt_keywords": {"blog": "legacy"},
            }, handle)
        branding.SETTINGS_PATH = path
        try:
            store = settings.Settings()
            assert store.get("hotkey") == "ctrl+option+d"
            assert store.get("quick_paste_hotkey") == "ctrl+option+v"
            assert store.get("history_hotkey") == "ctrl+option+h"
            # The old shared Search choice becomes the independent Web Search
            # shortcut when it is safe; Mumble Find receives its Mac default.
            assert store.get("web_search_hotkey") == "ctrl+alt+s"
            assert store.get("search_hotkey") == "ctrl+option+f"
            assert store.get("web_search_hotkey_default_applied") is True
            assert getattr(store, "web_search_migration_notice", None) is None
            for retired in settings.REMOVED_SETTINGS:
                assert retired not in store.data
        finally:
            branding.SETTINGS_PATH = old_settings_path


def test_cloud_sync_never_moves_device_specific_mac_shortcuts():
    safe = cloud_sync.SyncManager._syncable_settings({
        "model": "small.en",
        "hotkey": "ctrl+option+d",
        "quick_paste_hotkey": "ctrl+option+v",
        "history_hotkey": "ctrl+option+h",
        "web_search_hotkey": "ctrl+option+s",
        "mode_key": "right shift",
    })
    assert safe == {"model": "small.en"}


def test_vocabulary_transaction_preserves_a_concurrent_learning_write():
    old_settings_path = branding.SETTINGS_PATH
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "settings.json")
        branding.SETTINGS_PATH = path
        try:
            store = settings.Settings()
            assert store.update(vocabulary_terms=["baseline"], vocabulary={})
            with open(path, "r", encoding="utf-8") as handle:
                disk = json.load(handle)
            disk["vocabulary_terms"] = ["baseline", "learned"]
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(disk, handle)

            def merge(pairs, terms):
                terms.append("manual")
                return list(terms)

            result = store.atomic_vocabulary_update(merge)
            assert result == ["baseline", "learned", "manual"]
            with open(path, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
            assert saved["vocabulary_terms"] == result
        finally:
            branding.SETTINGS_PATH = old_settings_path


def test_controller_uses_current_prompt_and_low_latency_contracts():
    source = (APP_DIR / "mumble_mac.py").read_text(encoding="utf-8")
    assert "from prompt_history import PromptHistory" in source
    assert "PromptMemory" not in source
    assert "prompt_memory" not in source
    assert "prompt_provider" not in source
    assert "import mode_select" not in source
    assert "def _register_mode_key" not in source
    assert "split_mode_tail" not in source
    assert 'get("mode_button_enabled"' not in source
    assert '"processing": "Processing text' in source
    assert "STREAM_CHUNK_SECONDS = 4.0" in source
    assert "STREAM_DRAIN_TIMEOUT" in source
    assert "_stream_session_id" in source
    assert "_stream_idle" in source
    assert '"instant_text": self.settings.get("instant_text", True)' in source
    assert "best_of=1" in source
    assert "without_timestamps=not want_words" in source


def test_ui_and_bridge_do_not_resurrect_removed_product_concepts():
    paths = [
        APP_DIR / "webui_shell.py",
        APP_DIR / "webui" / "app.js",
        APP_DIR / "webui" / "index.html",
        APP_DIR / "app_window.py",
    ]
    joined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "prompt_memory" not in joined
    assert "prompt_provider" not in joined
    assert 'data-setting="hardware_tier"' not in joined
    assert "PromptMemory" not in joined
    assert "renderAiModeFields" not in joined
    shell = paths[0].read_text(encoding="utf-8")
    app_js = paths[1].read_text(encoding="utf-8")
    assert "from prompt_history import PromptHistory" in shell
    assert "processing_route.snapshot(" in shell
    assert "processing_route.call_provider(" in shell
    assert "def save_vocabulary" in shell
    assert '"save_vocabulary"' in app_js
    assert '"mode_key"' not in shell
    assert "_build_mode_button_card" not in paths[3].read_text(encoding="utf-8")

    index = paths[2].read_text(encoding="utf-8")
    picker = re.search(r'id="set-provider".*?</select>', index, re.DOTALL)
    assert picker is not None
    assert re.findall(r'<option value="([^"]+)"', picker.group()) == [
        "cerebras", "openrouter"
    ]
    for retired in ("openai", "anthropic", "deepseek", "groq"):
        assert f'data-provider-field="{retired}"' not in index


def test_retired_mac_runtime_duplicates_are_not_shipped():
    for name in (
        "ai.py",
        "mode_select.py",
        "mumble.py",
        "prompt_guide.py",
        "prompt_memory.py",
        "test_mode_select.py",
    ):
        assert not (APP_DIR / name).exists()

    ai_source = (APP_DIR / "ai" / "__init__.py").read_text(encoding="utf-8")
    constitution = (APP_DIR / "ai" / "constitution.py").read_text(encoding="utf-8")
    assert "thread=" not in ai_source
    assert "prompt memory" not in constitution.lower()

    updater = (APP_DIR / "update.py").read_text(encoding="utf-8")
    assert '"mumble_mac.py"' in updater


def test_mac_test_runner_and_release_archive_are_honest():
    runner = (APP_DIR / "run_tests.py").read_text(encoding="utf-8")
    shared_runner = (APP_DIR.parents[2] / "runner_core.py").read_text(encoding="utf-8")
    assert "from runner_core import main as run_main" in runner
    assert "os.makedirs" in shared_runner and "json_path" in shared_runner
    assert "test_mac_audit_regressions.py" in runner

    build = (PORT_DIR / "build_mac_release.sh").read_text(encoding="utf-8")
    assert '"app/test_*.py"' in build
    assert '"app/overlay.py"' in build
    assert '"app/requirements-dev.txt"' in build
    assert '"app/_test_logs/*"' in build


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    failures = []
    for test in tests:
        try:
            test()
            print(f"  ok  {test.__name__}")
        except Exception as exc:
            failures.append((test.__name__, exc))
            print(f"  FAIL {test.__name__}: {exc}")
    raise SystemExit(1 if failures else 0)
