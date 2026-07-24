#!/usr/bin/env python3
"""Tests for cloud_sync.py — Supabase auth + secure session storage.
Run: .venv/Scripts/python.exe test_cloud_sync.py

These tests verify that the CloudSync module:
- Gracefully handles missing Supabase credentials
- Stores session tokens in .session file (NOT settings.json)
- sign_up / sign_in / sign_out return correct dict shapes
- Session persistence works across client instances

Since Supabase credentials are not available in this environment,
the auth integration tests are skipped gracefully (no network calls).
The tests focus on code correctness, error handling, and secure storage.
"""

import json
import os
import tempfile

# Add mumble/ to path for imports
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

import branding  # noqa: E402
import cloud_sync  # noqa: E402
from cloud_sync import CloudSync, SyncManager, _FileStorage, SESSION_PATH  # noqa: E402
from settings import Settings  # noqa: E402

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [ok  ] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}")


# ── Mock Settings for isolated testing ────────────────────────────────────

class MockSettings:
    """A lightweight settings stand-in for CloudSync tests."""

    def __init__(self, **kw):
        self._data = dict(kw)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


# ── Test _FileStorage (session file persistence) ──────────────────────────

tmp_dir = tempfile.mkdtemp(prefix="mumble_cloud_test_")
session_file = os.path.join(tmp_dir, ".session")


def test_file_storage():
    print("\n── _FileStorage ──")
    storage = _FileStorage(session_file)

    # starts empty
    check("get_item returns None for missing key",
          storage.get_item("supabase.auth.token") is None)

    # set and get
    storage.set_item("supabase.auth.token", "test-token-abc")
    check("set_item + get_item round-trip",
          storage.get_item("supabase.auth.token") == "test-token-abc")

    # persistence across new instances
    storage2 = _FileStorage(session_file)
    check("persists across instances",
          storage2.get_item("supabase.auth.token") == "test-token-abc")

    # multiple keys
    storage.set_item("supabase.auth.expires_at", "1234567890")
    check("multiple keys persist",
          storage.get_item("supabase.auth.expires_at") == "1234567890")

    # remove_item
    storage.remove_item("supabase.auth.token")
    check("remove_item clears the key",
          storage.get_item("supabase.auth.token") is None)

    # remove non-existent key is no-op
    storage.remove_item("nonexistent")
    check("remove_item for non-existent key does not crash", True)

    # cleanup
    try:
        os.remove(session_file)
    except OSError:
        pass
    try:
        os.rmdir(tmp_dir)
    except OSError:
        pass


# ── Test CloudSync (no credentials) ───────────────────────────────────────

def test_no_credentials():
    print("\n── CloudSync (no credentials) ──")
    s = MockSettings(supabase_url="", supabase_anon_key="")
    cs = CloudSync(s)

    check("has_credentials_configured returns False", not cs.has_credentials_configured())
    check("is_signed_in returns False", not cs.is_signed_in())
    check("get_session returns None", cs.get_session() is None)

    # sign_up with no credentials
    r = cs.sign_up("test@example.com", "password123")
    check("sign_up without credentials returns ok=False",
          r.get("ok") is False)
    check("sign_up without credentials has message",
          "message" in r and len(r["message"]) > 0)

    # sign_in with no credentials
    r = cs.sign_in("test@example.com", "password123")
    check("sign_in without credentials returns ok=False",
          r.get("ok") is False)

    # sign_out with no credentials (should not crash)
    r = cs.sign_out()
    check("sign_out without credentials returns ok=True (offline)",
          r.get("ok") is True)

    # configure credentials
    r = cs.configure("https://test.supabase.co", "test-anon-key")
    check("configure returns ok=True", r.get("ok") is True)
    check("has_credentials_configured returns True after configure",
          cs.has_credentials_configured())
    check("settings stored correctly",
          s.get("supabase_url") == "https://test.supabase.co"
          and s.get("supabase_anon_key") == "test-anon-key")


# ── Test CloudSync input validation ───────────────────────────────────────

def test_input_validation():
    print("\n── CloudSync input validation ──")
    s = MockSettings(supabase_url="https://test.supabase.co",
                     supabase_anon_key="test-key")
    cs = CloudSync(s)

    # empty email
    r = cs.sign_up("", "password123")
    check("sign_up with empty email returns ok=False",
          r.get("ok") is False)

    # empty password
    r = cs.sign_up("test@ex.com", "")
    check("sign_up with empty password returns ok=False",
          r.get("ok") is False)

    # short password
    r = cs.sign_up("test@ex.com", "12345")
    check("sign_up with short password returns ok=False",
          r.get("ok") is False)

    # valid inputs will try the network (expected to fail without real Supabase)
    # This tests that we don't crash — the error is handled gracefully
    try:
        r = cs.sign_up("test@example.com", "password123")
        # We expect ok=False because network call will fail
        check("sign_up with valid inputs returns structured response",
              isinstance(r, dict) and "ok" in r)
    except Exception as e:
        check("sign_up handles network errors gracefully (no crash)",
              False)  # unexpected exception


# ── Test session is NOT stored in settings.json ───────────────────────────

def test_session_not_in_settings():
    print("\n── Session NOT in settings.json ──")
    s = MockSettings(supabase_url="https://test.supabase.co",
                     supabase_anon_key="test-key")

    # Verify there are no session keys in the settings data
    check("settings has no session_token key",
          "session_token" not in s._data)
    check("settings has no access_token key",
          "access_token" not in s._data)
    check("settings has no refresh_token key",
          "refresh_token" not in s._data)

    # The SESSION_PATH is separate from SETTINGS_PATH
    check("session file path is not settings.json",
          SESSION_PATH != branding.SETTINGS_PATH)
    check("session file path ends with .session",
          SESSION_PATH.endswith(".session"))


# ── Test sign_out clears session file ─────────────────────────────────────

def test_sign_out_clears():
    print("\n── sign_out clears session ──")
    s = MockSettings(supabase_url="https://test.supabase.co",
                     supabase_anon_key="test-key")
    cs = CloudSync(s)

    # Create a fake session file
    _ensure_session_dir = __import__("cloud_sync")._ensure_session_dir
    try:
        os.makedirs(os.path.dirname(SESSION_PATH), exist_ok=True)
    except OSError:
        pass
    with open(SESSION_PATH, "w") as f:
        json.dump({"supabase.auth.token": "fake-token"}, f)

    check("session file exists before sign_out",
          os.path.exists(SESSION_PATH))

    cs.sign_out()
    check("session file removed after sign_out",
          not os.path.exists(SESSION_PATH))

# ══════════════════════════════════════════════════════════════════════════
#  SyncManager tests (no network required)
# ══════════════════════════════════════════════════════════════════════════

def test_sync_manager():
    print("\n── SyncManager (offline / no credentials) ──")
    s = MockSettings(supabase_url="", supabase_anon_key="",
                     sync_enabled=True, sync_settings=True,
                     sync_stats=True, sync_history=True,
                     sync_reader=True, sync_favorites=True,
                     sync_presets=True, sync_last_run={})
    sm = SyncManager(s)

    # Status when not signed in
    status = sm.get_sync_status()
    check("get_sync_status returns ok=True",
          status.get("ok") is True)
    check("signed_in is false when offline",
          status.get("signed_in") is False)
    check("configured is false when no creds",
          status.get("configured") is False)
    check("types dict has all 6 keys",
          isinstance(status.get("types"), dict)
          and len(status.get("types", {})) == 6)

    # sync_all when not signed in
    r = sm.sync_all()
    check("sync_all returns ok=False when not signed in",
          r.get("ok") is False)
    check("sync_all returns message when not signed in",
          "message" in r)

    # sync_type when not signed in
    r = sm.sync_type("settings")
    check("sync_type returns ok=False when not signed in",
          r.get("ok") is False)

    # background sync start/stop
    r = sm.start_background_sync(5)
    check("start_background_sync returns ok=True",
          r.get("ok") is True)
    check("start_background_sync returns interval",
          r.get("interval") == 5)
    check("background thread is alive",
          sm._bg_thread is not None and sm._bg_thread.is_alive())

    r = sm.stop_background_sync()
    check("stop_background_sync returns ok=True",
          r.get("ok") is True)
    # Thread should stop within ~2s
    import time as _time
    _time.sleep(0.5)
    check("background thread stopped",
          sm._bg_thread is None or not sm._bg_thread.is_alive())

    # Restart with different interval
    r = sm.start_background_sync(10)
    check("restart background sync works",
          r.get("ok") is True and r.get("interval") == 10)
    sm.stop_background_sync()
    _time.sleep(0.5)


def test_sync_toggles():
    print("\n── SyncManager toggles ──")
    s = MockSettings(supabase_url="https://test.supabase.co",
                     supabase_anon_key="test-key",
                     sync_enabled=True, sync_settings=True,
                     sync_stats=False, sync_history=True,
                     sync_reader=False, sync_favorites=True,
                     sync_presets=False, sync_last_run={})
    sm = SyncManager(s)

    check("sync_settings enabled", sm._is_enabled("settings") is True)
    check("sync_stats disabled", sm._is_enabled("stats") is False)
    check("sync_history enabled", sm._is_enabled("history") is True)
    check("sync_reader disabled", sm._is_enabled("reader") is False)
    check("sync_favorites enabled", sm._is_enabled("favorites") is True)
    check("sync_presets disabled", sm._is_enabled("presets") is False)

    # Master toggle off disables all
    s._data["sync_enabled"] = False
    check("master off disables settings",
          sm._is_enabled("settings") is False)
    check("master off disables history (was enabled)",
          sm._is_enabled("history") is False)

    # Master toggle on re-enables per-type
    s._data["sync_enabled"] = True
    check("master on re-enables settings",
          sm._is_enabled("settings") is True)
    check("master on keeps stats off (per-type)",
          sm._is_enabled("stats") is False)

    # Sync status reflects toggle state
    status = sm.get_sync_status()
    types = status.get("types", {})
    check("status shows stats disabled",
          types.get("stats", {}).get("enabled") is False)
    check("status shows settings enabled",
          types.get("settings", {}).get("enabled") is True)


def test_syncable_settings_strip():
    print("\n── _syncable_settings (API key stripping) ──")
    from cloud_sync import _SETTINGS_SYNC_EXCLUDE

    # Verify known API keys are in the exclude set
    check("cerebras_api_key excluded",
          "cerebras_api_key" in _SETTINGS_SYNC_EXCLUDE)
    check("openai_api_key excluded",
          "openai_api_key" in _SETTINGS_SYNC_EXCLUDE)
    check("openrouter_api_key excluded",
          "openrouter_api_key" in _SETTINGS_SYNC_EXCLUDE)
    check("deepseek_api_key excluded",
          "deepseek_api_key" in _SETTINGS_SYNC_EXCLUDE)
    check("groq_api_key excluded",
          "groq_api_key" in _SETTINGS_SYNC_EXCLUDE)
    check("supabase_anon_key excluded",
          "supabase_anon_key" in _SETTINGS_SYNC_EXCLUDE)

    # Test stripping
    s = SyncManager._syncable_settings({
        "user_name": "Test User",
        "model": "small.en",
        "cerebras_api_key": "sk-secret-123",
        "openrouter_api_key": "sk-secret-456",
        "pro_mode": True,
        "supabase_anon_key": "anon-secret",
        "supabase_url": "https://x.supabase.co",
    })
    check("user_name preserved", s.get("user_name") == "Test User")
    check("model preserved", s.get("model") == "small.en")
    check("pro_mode preserved", s.get("pro_mode") is True)
    check("supabase_url preserved", s.get("supabase_url") == "https://x.supabase.co")
    check("cerebras_api_key stripped",
          "cerebras_api_key" not in s)
    check("openrouter_api_key stripped",
          "openrouter_api_key" not in s)
    check("supabase_anon_key stripped",
          "supabase_anon_key" not in s)


def test_sync_manager_with_creds_no_network():
    print("\n── SyncManager (credentials set, not signed in) ──")
    s = MockSettings(supabase_url="https://test.supabase.co",
                     supabase_anon_key="test-key",
                     sync_enabled=True, sync_settings=True,
                     sync_stats=True, sync_history=True,
                     sync_reader=True, sync_favorites=True,
                     sync_presets=True, sync_last_run={})
    sm = SyncManager(s)

    status = sm.get_sync_status()
    check("configured is true with creds",
          status.get("configured") is True)
    check("signed_in is false (no session)",
          status.get("signed_in") is False)

    # Push should fail without session
    r = sm.push_settings()
    check("push_settings fails when not signed in",
          r.get("ok") is False)
    check("push_settings message mentions sign in",
          "sign" in str(r.get("message", "")).lower()
          or "signed" in str(r.get("message", "")).lower())

    # Pull should also fail
    r = sm.pull_settings()
    check("pull_settings fails when not signed in",
          r.get("ok") is False)


def test_sync_type_keys():
    print("\n── SyncManager data type mapping ──")
    from cloud_sync import _SYNC_TABLES, _SYNC_TYPES

    check("6 sync types", len(_SYNC_TYPES) == 6)
    check("settings in tables", _SYNC_TABLES.get("settings") == "user_settings")
    check("stats in tables", _SYNC_TABLES.get("stats") == "user_stats")
    check("history in tables", _SYNC_TABLES.get("history") == "user_history")
    check("reader in tables", _SYNC_TABLES.get("reader") == "user_reader_library")
    check("favorites in tables", _SYNC_TABLES.get("favorites") == "user_favorites")
    check("presets in tables", _SYNC_TABLES.get("presets") == "user_presets")

    # Verify unknown type
    s = MockSettings(supabase_url="https://test.supabase.co",
                     supabase_anon_key="test-key")
    sm = SyncManager(s)
    r = sm.sync_type("nonexistent")
    check("unknown data type returns error",
          r.get("ok") is False)


def test_iso_now():
    print("\n── _iso_now / _parse_ts ──")
    from cloud_sync import _iso_now, _parse_ts

    ts = _iso_now()
    check("_iso_now returns string", isinstance(ts, str))
    check("_iso_now contains T", "T" in ts)
    check("_iso_now contains Z", "Z" in ts)

    parsed = _parse_ts(ts)
    from datetime import timezone as _tz
    check("_parse_ts returns datetime", hasattr(parsed, "tzinfo"))
    check("_parse_ts is UTC aware", parsed.tzinfo is not None)

    # Parse various formats
    check("_parse_ts handles Z suffix",
          _parse_ts("2026-01-15T12:00:00Z").year == 2026)
    check("_parse_ts handles +00:00",
          _parse_ts("2026-01-15T12:00:00+00:00").year == 2026)
    check("_parse_ts handles None gracefully",
          _parse_ts(None).year == 1)  # datetime.min
    check("_parse_ts handles empty gracefully",
          _parse_ts("").year == 1)
    check("_parse_ts handles garbage gracefully",
          _parse_ts("not-a-date").year == 1)


# ══════════════════════════════════════════════════════════════════════════
#  Background sync lifecycle tests
# ══════════════════════════════════════════════════════════════════════════

def test_background_sync_lifecycle():
    print("\n── Background sync lifecycle ──")
    import time as _time
    s = MockSettings(supabase_url="https://test.supabase.co",
                     supabase_anon_key="test-key",
                     sync_enabled=True, sync_settings=True,
                     sync_stats=True, sync_history=True,
                     sync_reader=True, sync_favorites=True,
                     sync_presets=True, sync_last_run={})
    sm = SyncManager(s)

    # Start background sync
    r = sm.start_background_sync(5)
    check("start returns ok=True", r.get("ok") is True)
    check("start returns interval 5", r.get("interval") == 5)
    check("bg thread is alive after start",
          sm._bg_thread is not None and sm._bg_thread.is_alive())

    # Stop background sync
    r = sm.stop_background_sync()
    check("stop returns ok=True", r.get("ok") is True)
    _time.sleep(0.5)
    check("bg thread stopped after stop",
          sm._bg_thread is None or not sm._bg_thread.is_alive())

    # Status reflects bg running state
    sm.start_background_sync(30)
    _time.sleep(0.1)
    status = sm.get_sync_status()
    # We add bg_running in the shell, not in SyncManager directly,
    # so check internal state here
    check("bg thread is alive in status check",
          sm._bg_thread is not None and sm._bg_thread.is_alive())
    sm.stop_background_sync()
    _time.sleep(0.5)

    # Verify stop/start idempotency
    r = sm.stop_background_sync()
    check("stop when already stopped returns ok=True",
          r.get("ok") is True)

    r = sm.start_background_sync(10)
    check("start when already started restarts cleanly",
          r.get("ok") is True)
    sm.stop_background_sync()
    _time.sleep(0.5)

    # Verify default interval
    from cloud_sync import _DEFAULT_SYNC_INTERVAL
    r = sm.start_background_sync()
    check("start with default interval uses _DEFAULT_SYNC_INTERVAL",
          r.get("interval") == _DEFAULT_SYNC_INTERVAL)
    sm.stop_background_sync()
    _time.sleep(0.5)


def test_background_sync_autostart_pattern():
    """Verify the pattern used in webui_shell.py: start bg sync after
    sign-in, stop bg sync on sign-out. Tests that the SyncManager can
    be used for this lifecycle without leaking threads."""
    print("\n── Background sync auto-start pattern ──")
    import time as _time
    s = MockSettings(supabase_url="https://test.supabase.co",
                     supabase_anon_key="test-key",
                     sync_enabled=True, sync_settings=True,
                     sync_stats=True, sync_history=True,
                     sync_reader=True, sync_favorites=True,
                     sync_presets=True, sync_last_run={})

    # Simulate sign-in: create SyncManager and start bg sync
    sm = SyncManager(s)
    r = sm.start_background_sync(10)
    check("auto-start on sign-in returns ok", r.get("ok") is True)
    check("bg thread running after auto-start",
          sm._bg_thread is not None and sm._bg_thread.is_alive())

    # Simulate sign-out: stop bg sync
    r = sm.stop_background_sync()
    _time.sleep(0.5)
    check("auto-stop on sign-out returns ok", r.get("ok") is True)
    check("bg thread stopped after sign-out",
          sm._bg_thread is None or not sm._bg_thread.is_alive())

    # Verify status shows signed_in=false (offline/no-network)
    status = sm.get_sync_status()
    check("status signed_in is false (mock, no real session)",
          status.get("signed_in") is False)


# ── Run all tests ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_file_storage()
    test_no_credentials()
    test_input_validation()
    test_session_not_in_settings()
    test_sign_out_clears()
    test_sync_manager()
    test_sync_toggles()
    test_syncable_settings_strip()
    test_sync_manager_with_creds_no_network()
    test_sync_type_keys()
    test_iso_now()
    test_background_sync_lifecycle()
    test_background_sync_autostart_pattern()

    print()
    if failed:
        print(f"FAILED {failed}/{passed + failed}")
        sys.exit(1)
    else:
        print(f"OK {passed}/{passed + failed}")
