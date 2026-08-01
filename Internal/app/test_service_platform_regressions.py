#!/usr/bin/env python3
"""Regression coverage for the 2026-07 service/platform bug audit.

Standalone and offline: ``python test_service_platform_regressions.py``.
"""

import json
import inspect
import os
import subprocess
import sys
import tempfile
import threading
import types
import uuid
import wave

import autostart
import branding
import cloud_sync
import context_store
import favorites
import history
import meeting
import meeting_diarise
import meeting_store
import presets
import reader_parser
import reader_store
import settings
import stats
import tips
import update


passed = failed = 0


def check(name, condition):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [ok  ] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}")


def _process_scope_probe(script_text, section_marker):
    """Execute only the pure process-matching function with sibling paths."""
    function_start = script_text.index("function Test-MumbleProcessForApp")
    function_text = script_text[function_start:].split(section_marker, 1)[0]
    probe = function_text + r'''
$root = Join-Path $env:TEMP 'Mumble Scope Fixture\Internal\app'
$sibling = Join-Path $env:TEMP 'Mumble Scope Fixture Sibling\Internal\app'
$inside = [pscustomobject]@{
  Name = 'pythonw.exe'
  ExecutablePath = Join-Path $root '.venv\Scripts\pythonw.exe'
  CommandLine = '"' + (Join-Path $root '.venv\Scripts\pythonw.exe') + '" "' + (Join-Path $root 'mumble.py') + '"'
}
$other = [pscustomobject]@{
  Name = 'pythonw.exe'
  ExecutablePath = Join-Path $sibling '.venv\Scripts\pythonw.exe'
  CommandLine = '"' + (Join-Path $sibling '.venv\Scripts\pythonw.exe') + '" "' + (Join-Path $sibling 'mumble.py') + '"'
}
$product = Split-Path (Split-Path $root -Parent) -Parent
$launcher = [pscustomobject]@{
  Name = 'Mumble.exe'
  ExecutablePath = Join-Path $product 'Mumble.exe'
  CommandLine = '"' + (Join-Path $product 'Mumble.exe') + '"'
}
$wrongExecutable = [pscustomobject]@{
  Name = 'pythonw.exe'
  ExecutablePath = Join-Path $sibling '.venv\Scripts\pythonw.exe'
  CommandLine = '"' + (Join-Path $sibling '.venv\Scripts\pythonw.exe') + '" "' + (Join-Path $root 'mumble.py') + '"'
}
$wrongEntryPoint = [pscustomobject]@{
  Name = 'Mumble.exe'
  ExecutablePath = Join-Path $root '.venv\Scripts\Mumble.exe'
  CommandLine = '"' + (Join-Path $root '.venv\Scripts\Mumble.exe') + '" "' + (Join-Path $sibling 'mumble.py') + '"'
}
$argumentOnly = [pscustomobject]@{
  Name = 'Mumble.exe'
  ExecutablePath = Join-Path $root '.venv\Scripts\Mumble.exe'
  CommandLine = '"' + (Join-Path $root '.venv\Scripts\Mumble.exe') + '" --inspect "' + (Join-Path $root 'mumble.py') + '"'
}
if ((Test-MumbleProcessForApp $inside $root) -and
    (Test-MumbleProcessForApp $launcher $root) -and
    -not (Test-MumbleProcessForApp $other $root) -and
    -not (Test-MumbleProcessForApp $wrongExecutable $root) -and
    -not (Test-MumbleProcessForApp $wrongEntryPoint $root) -and
    -not (Test-MumbleProcessForApp $argumentOnly $root)) { exit 0 }
exit 1
'''
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", probe],
        capture_output=True, text=True, timeout=15,
    ).returncode == 0


class FakeSettings:
    def __init__(self, **values):
        self.data = dict(values)
        self.updated = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def update(self, **values):
        self.updated.update(values)
        self.data.update(values)


class Query:
    def __init__(self, capture):
        self.capture = capture

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def maybe_single(self):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        return types.SimpleNamespace(data=None)

    def upsert(self, row, **kwargs):
        self.capture.append(row)
        return self


class FakeClient:
    def __init__(self, capture=None):
        self.capture = capture if capture is not None else []

    def table(self, _name):
        return Query(self.capture)


def test_history_and_stats():
    print("\n== history/stats corruption and aggregation ==")
    with tempfile.TemporaryDirectory() as td:
        hp = os.path.join(td, "history.json")
        ht = os.path.join(td, "history.txt")
        h = history.History(hp, ht)
        h.add("one two", mode="email")
        h.add("three four five", mode="text")
        modes = {name: (count, words)
                 for name, count, words in h.mode_stats()}
        check("history mode word totals count every entry",
              modes == {"email": (1, 2), "text": (1, 3)})

        sp = os.path.join(td, "stats.json")
        with open(sp, "w", encoding="utf-8") as handle:
            json.dump({"days": {"2026-07-12": "bad"},
                       "modes": {"text": None},
                       "hours": ["bad"] * 24,
                       "reader_days": {"not-a-date": "bad"}}, handle)
        store = stats.Stats(sp)
        store.record(2, 1.0, "text", day="2026-07-12", hour=2)
        tod = store.time_of_day()
        check("nested corrupt stats buckets self-heal on record",
              store.data["days"]["2026-07-12"] == [2, 1])
        check("corrupt hourly values no longer crash time_of_day",
              tod["hours"][2] == 2)

        # Two long-lived processes can both hold stale in-memory Stats objects.
        os.remove(sp)
        first, second = stats.Stats(sp), stats.Stats(sp)
        first.record(2, 1.0, "text")
        second.record_reader_session(3, 4.0)
        combined = stats.Stats(sp)
        check("stale stats instances merge under a cross-process transaction",
              combined.data["total_words"] == 2
              and combined.data["reader_words_read"] == 3)

        fav_path = os.path.join(td, "favorites.json")
        fav_a, fav_b = favorites.Favorites(fav_path), favorites.Favorites(fav_path)
        fav_a.add("first")
        fav_b.add("second")
        check("stale favorite instances no longer clobber each other",
              {row["text"] for row in favorites.Favorites(fav_path).items}
              == {"first", "second"})


def test_settings_reset_and_types():
    print("\n== settings reset and numeric type recovery ==")
    old_path = branding.SETTINGS_PATH
    with tempfile.TemporaryDirectory() as td:
        branding.SETTINGS_PATH = os.path.join(td, "settings.json")
        try:
            s = settings.Settings()
            s.set("future_only_key", "remove me")
            s.reset_all()
            saved = json.load(open(branding.SETTINGS_PATH, encoding="utf-8"))
            check("factory reset removes disk-only unknown keys",
                  "future_only_key" not in saved)

            saved["cpu_threads"] = True
            with open(branding.SETTINGS_PATH, "w", encoding="utf-8") as handle:
                json.dump(saved, handle)
            s2 = settings.Settings()
            check("bool is rejected for integer settings",
                  s2.get("cpu_threads") == settings.DEFAULTS["cpu_threads"])
        finally:
            branding.SETTINGS_PATH = old_path


def test_reader_parser_and_store():
    print("\n== reader parsing, caps, and sync timestamps ==")
    rtf = (b"{\\rtf1 First\\par Second\\tab Caf\\'e9 "
           b"\\u937?}")
    text = reader_parser._rtf_to_text(rtf)
    check("RTF paragraph controls survive", "First\nSecond" in text)
    check("RTF tab controls survive", "Second\tCaf" in text)
    check("RTF hex and unicode escapes decode", "Caf\u00e9" in text and "\u03a9" in text)

    import csv
    old_rows = reader_parser._MAX_ROWS
    old_limit = csv.field_size_limit()
    try:
        reader_parser._MAX_ROWS = 1
        csv.field_size_limit(20)
        raw = b"head\nok\nsentinel\n" + (b"x" * 100) + b"\n"
        doc = reader_parser._parse_csv(raw, "large.csv", {})
        meta = doc.blocks[0].meta
        check("CSV stops parsing once the row cap is established",
              meta["rows"] == [["ok"]] and meta.get("truncated") is True)
    finally:
        reader_parser._MAX_ROWS = old_rows
        csv.field_size_limit(old_limit)

    old_path = reader_store.PATH
    with tempfile.TemporaryDirectory() as td:
        reader_store.PATH = os.path.join(td, "reader.json")
        try:
            import hashlib
            source = "legacy document text"
            legacy_id = hashlib.sha1(
                source.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
            reader_store._save([{
                "id": legacy_id, "title": "Legacy", "text": source,
                "length": 3, "position": 2, "opened": 1, "added": 1,
                "starred": True, "bookmarks": [{"pos": 2}],
                "collections": ["Keep"], "reading_sessions": [],
            }])
            strengthened = reader_store.save_doc("Legacy", source)
            migrated = reader_store.get_doc(strengthened)
            expected = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
            check("Reader content IDs use SHA-256 with the existing 16-char shape",
                  strengthened == expected and len(strengthened) == 16)
            check("legacy SHA-1 rows migrate without losing reader state",
                  migrated["position"] == 2 and migrated["starred"] is True
                  and migrated["collections"] == ["Keep"]
                  and len(reader_store._load()) == 1)

            did = reader_store.save_doc("Doc", "one two three")
            before = reader_store._load()[0].get("updated_at")
            reader_store.set_position(did, 1)
            after = reader_store._load()[0].get("updated_at")
            check("reader mutations carry an LWW timestamp",
                  bool(before and after and after >= before))
            check("None session duration is safely coerced",
                  reader_store.log_reading_session(did, 0, 1, None) is True)
            check("coerced session duration is zero",
                  reader_store.list_reading_history()[0]["duration_sec"] == 0.0)
            with open(reader_store.PATH, "w", encoding="utf-8") as handle:
                json.dump([5, {"id": "ok", "text": "x"}], handle)
            check("reader store skips wrong-shaped rows",
                  reader_store._load() == [{"id": "ok", "text": "x"}])
        finally:
            reader_store.PATH = old_path


def test_tips_context_and_presets():
    print("\n== persisted state sanitisation ==")
    tip, state = tips.next_tip(
        {"counts": {"deck": "2", "bad": "not-a-number"},
         "last_ts": "nan", "last_n": "broken"},
        10000, {"n": "25", "modes": {}, "features": {}})
    check("corrupt tip state normalises without crashing",
          isinstance(state["counts"], dict)
          and state["counts"]["deck"] == 2
          and state["counts"]["bad"] == 0)
    check("tip decision still returns a valid shape", tip is None or "id" in tip)

    with tempfile.TemporaryDirectory() as td:
        store = context_store.ConversationStore(os.path.join(td, "context.json"))
        copied = "A" * 140
        store._track_copy(copied)
        store._track_copy(copied)
        check("retroactive promotion reports actual inserts, not duplicates",
              store.retroactive_promote() == 1 and len(store.entries) == 1)
        check("empty user turns are rejected", store.log_user_turn("  ") is False)

        old_path = presets.PRESETS_PATH
        presets.PRESETS_PATH = os.path.join(td, "presets.json")
        try:
            slot = presets.CUSTOM_SLOTS[0]
            ok = presets.save_custom({
                "bad": None,
                1: {"title": "built-in", "instruction": "ignore"},
                slot: {"title": 123, "description": None,
                       "instruction": "Do the useful thing"},
            })
            loaded = presets.load_custom()
            check("malformed preset payloads are filtered, not fatal",
                  ok and list(loaded) == [slot]
                  and loaded[slot]["title"] == "123")
        finally:
            presets.PRESETS_PATH = old_path


def _authenticated_manager(settings_obj=None):
    values = dict(supabase_url="https://example.supabase.co",
                  supabase_anon_key="anon", sync_enabled=True,
                  sync_settings=True, sync_stats=True, sync_history=True,
                  sync_reader=True, sync_favorites=True, sync_presets=True,
                  sync_last_run={})
    cfg = settings_obj or FakeSettings(**values)
    manager = cloud_sync.SyncManager(cfg)
    manager._is_signed_in = lambda: True
    manager._get_user_id = lambda: "user-1"
    return manager


def test_cloud_auth_and_storage():
    print("\n== cloud auth/session safety ==")
    with tempfile.TemporaryDirectory() as td:
        session_path = os.path.join(td, ".session")
        with open(session_path, "w", encoding="utf-8") as handle:
            json.dump([], handle)
        storage = cloud_sync._FileStorage(session_path)
        check("wrong-shaped session JSON reads as empty",
              storage.get_item("token") is None)
        storage.set_item("token", "ok")
        check("session storage recovers on the next write",
              storage.get_item("token") == "ok")

        cfg = FakeSettings(supabase_url="https://old.supabase.co",
                           supabase_anon_key="old")
        cs = cloud_sync.CloudSync(cfg)
        cs._storage = storage
        result = cs.configure("https://new.supabase.co", None)
        check("switching cloud projects clears old refresh tokens",
              result["ok"] and not os.path.exists(session_path))

    captured = {}

    class Auth:
        def sign_in_with_password(self, payload):
            captured.update(payload)

            class User:
                def model_dump(self):
                    return {"id": "u"}

            class Session:
                def model_dump(self):
                    return None

                def __str__(self):
                    return "SESSION-OBJECT"

            return types.SimpleNamespace(user=User(), session=Session())

    cs = cloud_sync.CloudSync(FakeSettings())
    cs._get_client = lambda: (types.SimpleNamespace(auth=Auth()), None)
    result = cs.sign_in(" user@example.com ", " pass ")
    check("sign-in preserves significant password whitespace",
          captured.get("password") == " pass ")
    check("session fallback serialises the session, not the user",
          result.get("session") == "SESSION-OBJECT")

    safe = cloud_sync.SyncManager._syncable_settings({
        "model": "small.en", "hf_token": "secret",
        "future_api_key": "secret", "future_password": "secret",
        "sync_last_run": {"history": "other-device"}})
    check("future credential-like setting names fail closed",
          safe == {"model": "small.en"})
    check("naive remote timestamps normalise to UTC",
          cloud_sync._parse_ts("2026-01-01T00:00:00").tzinfo is not None)


def test_cloud_sync_paths_and_idempotency():
    print("\n== authenticated cloud data paths ==")
    manager = _authenticated_manager()
    client_capture = []
    manager._get_client = lambda: (FakeClient(client_capture), None)
    manager._upsert_single_row(
        "user_settings", "user-1", None, {"model": "small.en"})
    check("single-row inserts omit a null primary key",
          "id" not in client_capture[0])

    cfg = FakeSettings(model="local", sync_enabled=True,
                       sync_settings=True, sync_last_run={})
    manager2 = _authenticated_manager(cfg)
    manager2._pull_single_row = lambda *_: (
        {"model": "remote"}, "2099-01-01T00:00:00Z")
    result = manager2.pull_settings()
    check("remote settings use the dirty-aware update API",
          result.get("changed") == 1 and cfg.updated == {"model": "remote"})

    old_history_json = branding.HISTORY_JSON
    old_history_txt = branding.HISTORY_TXT
    old_stats_json = branding.STATS_JSON
    old_reader = reader_store.PATH
    old_favorites = favorites.FAVORITES_PATH
    with tempfile.TemporaryDirectory() as td:
        branding.HISTORY_JSON = os.path.join(td, "history.json")
        branding.HISTORY_TXT = os.path.join(td, "history.txt")
        branding.STATS_JSON = os.path.join(td, "stats.json")
        reader_store.PATH = os.path.join(td, "reader.json")
        favorites.FAVORITES_PATH = os.path.join(td, "favorites.json")
        try:
            history.History(branding.HISTORY_JSON, branding.HISTORY_TXT).add(
                "same history", mode="text")
            stats.Stats(branding.STATS_JSON).record(2, 1, "text")
            reader_store.save_doc("Cloud doc", "reader cloud text")
            favorites.Favorites().add("favorite cloud text")

            manager = _authenticated_manager()
            manager._get_client = lambda: (FakeClient(), None)
            ids = {"history": [], "reader": [], "favorites": []}
            active = ["history"]

            def capture_row(_table, row):
                ids[active[0]].append(row["id"])
                return None

            manager._upsert_row = capture_row
            manager.push_history(); manager.push_history()
            active[0] = "reader"
            manager.push_reader_library(); manager.push_reader_library()
            active[0] = "favorites"
            manager.push_favorites(); manager.push_favorites()
            check("history pushes are idempotent",
                  len(ids["history"]) == 2
                  and ids["history"][0] == ids["history"][1])
            check("reader pushes use stable valid UUIDs",
                  len(ids["reader"]) == 2
                  and ids["reader"][0] == ids["reader"][1]
                  and str(uuid.UUID(ids["reader"][0])) == ids["reader"][0])
            check("favorite pushes are idempotent",
                  len(ids["favorites"]) == 2
                  and ids["favorites"][0] == ids["favorites"][1])

            manager._upsert_row = lambda *_: {"ok": False, "message": "down"}
            failed_push = manager.push_history()
            check("partial row upload failures are not reported as success",
                  failed_push.get("ok") is False
                  and failed_push.get("errors") == 1)

            manager._last_sync["history"] = "2026-01-02T00:00:00Z"
            manager._pull_rows = lambda *_args, **_kwargs: [
                {"id": "known-deleted", "updated_at": "2026-01-01T00:00:00Z",
                 "payload": {"stamp": "old", "text": "gone"}},
                {"id": "new-on-other-device", "updated_at": "2026-01-03T00:00:00Z",
                 "payload": {"stamp": "new", "text": "keep"}},
            ]
            removed = []
            manager._delete_row = lambda _table, row_id, _uid: (
                removed.append(row_id) or None)
            deleted, delete_errors = manager._delete_known_remote_absences(
                "history", "user_history", "user-1", set(),
                lambda row: (row["payload"]["stamp"], row["payload"]["text"]))
            check("known local deletions propagate without erasing new remote rows",
                  deleted == 1 and delete_errors == 0
                  and removed == ["known-deleted"])

            manager._upsert_single_row = lambda *_: None
            check("authenticated stats path uses branding.STATS_JSON",
                  manager.push_stats().get("ok") is True)
        finally:
            branding.HISTORY_JSON = old_history_json
            branding.HISTORY_TXT = old_history_txt
            branding.STATS_JSON = old_stats_json
            reader_store.PATH = old_reader
            favorites.FAVORITES_PATH = old_favorites

    manager = _authenticated_manager()
    check("invalid background interval is rejected",
          manager.start_background_sync(0).get("ok") is False)

    class StuckThread:
        def is_alive(self):
            return True

        def join(self, timeout=None):
            return None

    stuck = StuckThread()
    manager._bg_thread = stuck
    result = manager.start_background_sync(5)
    check("a stuck worker is not replaced by a duplicate worker",
          result.get("ok") is False and manager._bg_thread is stuck
          and manager._bg_stop.is_set())
    manager._bg_thread = None


def test_meeting_safety():
    print("\n== meeting token, paths, and corrupt metadata ==")
    captured = {}

    class Pipeline:
        @classmethod
        def from_pretrained(cls, _model, use_auth_token=None):
            captured["token"] = use_auth_token
            return cls()

        def __call__(self, _payload):
            class Result:
                def itertracks(self, yield_label=False):
                    for index in range(9):
                        turn = types.SimpleNamespace(start=index, end=index + 0.5)
                        yield turn, None, f"SPEAKER_{index:02d}"
            return Result()

    old_pyannote = sys.modules.get("pyannote")
    old_audio = sys.modules.get("pyannote.audio")
    pkg = types.ModuleType("pyannote")
    audio_mod = types.ModuleType("pyannote.audio")
    audio_mod.Pipeline = Pipeline
    pkg.audio = audio_mod
    sys.modules["pyannote"] = pkg
    sys.modules["pyannote.audio"] = audio_mod
    try:
        import numpy as np
        diarised = meeting_diarise._diarise_pyannote(
            np.zeros(16000, dtype=np.float32),
            settings=FakeSettings(hf_token="hf-private"))
        check("pyannote receives the configured HF token",
              captured.get("token") == "hf-private")
        check("neural overflow clusters are safely capped at eight",
              len(diarised["speakers"]) == 8)
    finally:
        if old_pyannote is None:
            sys.modules.pop("pyannote", None)
        else:
            sys.modules["pyannote"] = old_pyannote
        if old_audio is None:
            sys.modules.pop("pyannote.audio", None)
        else:
            sys.modules["pyannote.audio"] = old_audio

    old_data = branding.DATA_DIR
    old_store = meeting_store.PATH
    with tempfile.TemporaryDirectory() as td:
        branding.DATA_DIR = td
        meeting_store.PATH = os.path.join(td, "meetings.json")
        try:
            audio_dir = os.path.join(td, "meetings_audio")
            os.makedirs(audio_dir)
            inside = os.path.join(audio_dir, "meeting_safe.wav")
            with wave.open(inside, "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
                wf.writeframes(b"\x00\x00")
            resolved = meeting._resolve_audio_path("meeting_safe.wav")
            check("meeting audio resolution accepts contained files",
                  bool(resolved) and os.path.samefile(resolved, inside))
            check("meeting audio resolution blocks traversal",
                  meeting._resolve_audio_path("../meeting_safe.wav") is None)

            empty = os.path.join(td, "empty.wav")
            with wave.open(empty, "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
                wf.writeframes(b"")
            check("empty imported recordings are rejected before persistence",
                  meeting.process_audio_file(
                      empty, lambda *_a, **_k: "should not run", FakeSettings()) is None
                  and meeting_store._load() == [])

            with open(meeting_store.PATH, "w", encoding="utf-8") as handle:
                json.dump([5, {"id": "ok", "duration_sec": "bad"}], handle)
            rows = meeting_store.list_meetings()
            check("corrupt meeting rows/durations do not crash listing",
                  len(rows) == 1 and rows[0]["duration_display"] == "0:00")
        finally:
            branding.DATA_DIR = old_data
            meeting_store.PATH = old_store


def test_updater_installer_and_autostart():
    print("\n== updater/installer/autostart safety ==")
    check("updater network URLs use a strict HTTPS allow-list",
          update._is_https_url("https://updates.example/file.zip")
          and not update._is_https_url("http://updates.example/file.zip")
          and not update._is_https_url("file:///tmp/update.zip")
          and not update._is_https_url("https://user:pass@updates.example/x"))

    import io
    old_urlopen = update.urllib.request.urlopen
    old_verify = update._verify_manifest_signature
    called = []
    event = threading.Event()
    try:
        class DowngradeRedirect(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

            def geturl(self):
                return "http://updates.example/manifest.json"

        update.urllib.request.urlopen = lambda *_a, **_k: DowngradeRedirect(
            b'{"version":"99.0.0"}')
        available, manifest = update.check_for_update(
            "https://updates.example/manifest.json")
        check("HTTPS-to-HTTP manifest redirects are refused",
              available is False and manifest is None)

        update._verify_manifest_signature = lambda _m: (False, "bad signature")
        update.download_and_install(
            {"url": "https://example.invalid/update.zip"}, tempfile.gettempdir(),
            lambda status, message: (called.append((status, message)), event.set()))
        event.wait(2)
        check("direct update install re-verifies publisher signature",
              called and called[0][0] == "error"
              and "signature" in called[0][1])
    finally:
        update.urllib.request.urlopen = old_urlopen
        update._verify_manifest_signature = old_verify

    install_source = inspect.getsource(update.download_and_install)
    check("update staging uses unique sibling directories",
          install_source.count("tempfile.mkdtemp(") >= 2
          and 'prefix=f"Mumble-{version}-extract-"' in install_source
          and 'prefix=f"Mumble-{version}-product-"' in install_source)

    with tempfile.TemporaryDirectory() as td:
        current = os.path.join(td, "Mumble")
        new = os.path.join(td, "Mumble-new-product")
        script_path = update._write_swap_script(td, new, current)
        script = open(script_path, encoding="utf-8").read()
        check("update retains the complete product launch boundary",
              "Internal\\app\\.venv\\Scripts\\Mumble.exe" in script
              and os.path.join(current, "Mumble.exe") in script
              and "Internal\\app\\mumble.py" in script)

    install_text = open(os.path.join(os.path.dirname(__file__), "install.ps1"),
                        encoding="utf-8").read()
    uninstall_text = open(os.path.join(os.path.dirname(__file__), "uninstall.ps1"),
                          encoding="utf-8").read()
    check("bootstrap Python is Authenticode-verified before execution",
          "Get-AuthenticodeSignature" in install_text
          and "Python Software Foundation" in install_text)
    check("installer checks shortcut helper return values",
          "all(results)" in install_text and "$LASTEXITCODE -eq 0" in install_text)
    check("uninstaller refuses recursive deletion of a drive root",
          "$resolvedDist -eq $driveRoot" in uninstall_text)
    check("installer stops only processes from its exact app root",
          "Test-MumbleProcessForApp $_ $root" in install_text
          and "[IO.Path]::GetFullPath($AppRoot)" in install_text
          and _process_scope_probe(install_text, "# 0. Detect"))
    check("uninstaller stops only processes from its exact app root",
          "Test-MumbleProcessForApp $_ $app" in uninstall_text
          and "[IO.Path]::GetFullPath($AppRoot)" in uninstall_text
          and _process_scope_probe(uninstall_text, "# 1. Stop"))

    old_legacy_startup = autostart.LEGACY_FINALIZE_STARTUP
    old_legacy_script = autostart.LEGACY_FINALIZE_SCRIPT
    try:
        with tempfile.TemporaryDirectory() as td:
            autostart.LEGACY_FINALIZE_STARTUP = os.path.join(
                td, "FinalizeMumble.cmd")
            autostart.LEGACY_FINALIZE_SCRIPT = os.path.join(
                td, "finalize_mumble.ps1")
            for path in (autostart.LEGACY_FINALIZE_STARTUP,
                         autostart.LEGACY_FINALIZE_SCRIPT):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("stale")
            check("legacy one-time startup finalizer is removed",
                  autostart.remove_legacy_startup_artifacts()
                  and not os.path.exists(autostart.LEGACY_FINALIZE_STARTUP)
                  and not os.path.exists(autostart.LEGACY_FINALIZE_SCRIPT))
    finally:
        autostart.LEGACY_FINALIZE_STARTUP = old_legacy_startup
        autostart.LEGACY_FINALIZE_SCRIPT = old_legacy_script

    old_app_shortcut = autostart._app_shortcut
    old_remove = autostart.remove_legacy_run_key
    old_remove_artifacts = autostart.remove_legacy_startup_artifacts
    old_startup = autostart.STARTUP_DIR
    try:
        autostart._app_shortcut = lambda _path: False
        autostart.remove_legacy_run_key = lambda: False
        autostart.remove_legacy_startup_artifacts = lambda: True
        with tempfile.TemporaryDirectory() as td:
            autostart.STARTUP_DIR = td
            check("autostart enable propagates shortcut creation failure",
                  autostart.enable() is False)
            shortcut_attempts = []
            autostart._app_shortcut = (
                lambda path: shortcut_attempts.append(path) or True)
            autostart.remove_legacy_startup_artifacts = lambda: False
            check("autostart enable propagates legacy cleanup failure",
                  autostart.enable() is False
                  and shortcut_attempts == [autostart.startup_path()])
    finally:
        autostart._app_shortcut = old_app_shortcut
        autostart.remove_legacy_run_key = old_remove
        autostart.remove_legacy_startup_artifacts = old_remove_artifacts
        autostart.STARTUP_DIR = old_startup


if __name__ == "__main__":
    test_history_and_stats()
    test_settings_reset_and_types()
    test_reader_parser_and_store()
    test_tips_context_and_presets()
    test_cloud_auth_and_storage()
    test_cloud_sync_paths_and_idempotency()
    test_meeting_safety()
    test_updater_installer_and_autostart()
    print(f"\nService/platform regressions: {passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
