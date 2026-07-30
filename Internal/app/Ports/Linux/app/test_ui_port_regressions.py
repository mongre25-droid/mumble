"""Focused cross-platform regressions for desktop/WebUI presentation fixes."""

import base64
import io
import json
import inspect
from pathlib import Path
import zipfile

from PIL import Image

import assets_gen
import autostart
import clipboard as clipboard_mod
from clipboard import Clipboard
import island_render
import overlay_linux as overlay
import reader_parser
import reader_store
import settings
import webui_shell


APP_DIR = Path(__file__).resolve().parent
APP_JS = APP_DIR / "webui" / "app.js"


class MemorySettings:
    def __init__(self, **values):
        self.values = dict(values)
        self.writes = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.writes.append((key, value))
        self.values[key] = value


def test_masked_key_test_uses_stored_secret(monkeypatch):
    real_key = "csk-real-secret-123456"
    store = MemorySettings(cerebras_api_key=real_key)
    api = webui_shell.Api.__new__(webui_shell.Api)
    api.settings = store
    seen = []
    monkeypatch.setattr(
        webui_shell.ai, "cerebras_test",
        lambda key: (seen.append(key) or True, "Connected"),
    )

    result = api.test_key("cerebras", webui_shell._mask_api_key(real_key))

    assert result["ok"] is True
    assert seen == [real_key]
    assert store.values["cerebras_api_key"] == real_key
    assert all(not webui_shell._is_masked_api_key(value)
               for _, value in store.writes)


def test_reader_import_bytes_rejects_invalid_and_oversize_data(monkeypatch):
    api = webui_shell.Api.__new__(webui_shell.Api)
    assert api.reader_import_bytes("not base64!", "bad.txt")["ok"] is False

    monkeypatch.setattr(webui_shell, "MAX_READER_IMPORT_BYTES", 4)
    result = api.reader_import_bytes(base64.b64encode(b"12345").decode(), "big.txt")
    assert result["ok"] is False
    assert "larger" in result["message"].lower()


def test_get_settings_projects_all_webui_state():
    projected = {
        "prompt_mode_enabled": False,
        "auto_format": False,
        "foreign_languages": ["arabic"],
        "resource_saver": True,
        "deck_pinned": False,
        "sync_settings": False,
        "sync_stats": False,
        "sync_history": False,
        "sync_reader": False,
        "sync_favorites": False,
        "sync_presets": False,
        "meeting_processing_mode": "deep",
    }
    api = webui_shell.Api.__new__(webui_shell.Api)
    api.settings = MemorySettings(**projected)

    result = api.get_settings()

    assert {key: result[key] for key in projected} == projected


def test_shortcut_api_propagates_false_service_results(monkeypatch):
    monkeypatch.setattr(autostart, "install_desktop_shortcut", lambda: False)
    monkeypatch.setattr(autostart, "install_start_menu", lambda: True)
    monkeypatch.setattr(autostart, "enable", lambda: False)
    api = webui_shell.Api.__new__(webui_shell.Api)

    result = api.apply_shortcuts({
        "desktop": True, "start_menu": True, "autostart": True,
    })

    assert result == {
        "ok": False,
        "done": ["start_menu"],
        "failed": ["desktop", "autostart"],
    }


def test_clipboard_restart_and_managed_image_boundary(monkeypatch, tmp_path):
    threads = []

    class FakeThread:
        def __init__(self, target, args=(), daemon=None):
            self.args = args
            threads.append(self)

        def start(self):
            pass

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return False

    monkeypatch.setattr(clipboard_mod.threading, "Thread", FakeThread)
    monkeypatch.setattr(clipboard_mod.pyperclip, "paste", lambda: "")

    outside = tmp_path / "outside.png"
    Image.new("RGB", (2, 2), "red").save(outside)
    db = tmp_path / "clipboard.json"
    db.write_text(json.dumps([{"type": "image", "path": str(outside)}]),
                  encoding="utf-8")
    clip = Clipboard(str(db), img_dir=str(tmp_path / "managed"))
    assert clip.recent() == []
    clip.clear()
    assert outside.exists()

    clip.start()
    first = clip._stop_event
    clip.stop()
    clip.start()
    second = clip._stop_event
    assert first is not second
    assert first.is_set() and not second.is_set()
    assert [thread.args for thread in threads] == [(first,), (second,)]


def test_clipboard_image_hash_uses_full_pixels():
    first = Image.new("RGB", (512, 512), "black")
    second = first.copy()
    second.putpixel((511, 511), (255, 255, 255))
    assert Clipboard._img_hash(first) != Clipboard._img_hash(second)


def test_clipboard_transactions_merge_and_fail_honestly(monkeypatch, tmp_path):
    path = tmp_path / "clipboard.json"
    first = Clipboard(str(path), 10)
    second = Clipboard(str(path), 10)
    assert first._add_text("first") is True
    assert second._add_text("second") is True
    assert {row["text"] for row in first.recent()} == {"first", "second"}

    before = list(second.items)
    monkeypatch.setattr(second, "_save", lambda: False)
    assert second._add_text("unsaved") is False
    assert list(second.items) == before

    monkeypatch.setattr(clipboard_mod, "_MAX_CLIPBOARD_TEXT_BYTES", 4)
    assert first._add_text("12345") is False


def test_search_and_building_island_contracts():
    assert "search" in overlay._STATE_LABEL
    assert island_render._anim_span("search") == island_render._anim_span("listening")

    island = overlay._GtkIsland.__new__(overlay._GtkIsland)
    island.state = "search"
    island.frame = 1
    island.level = 0.5
    island.armed = False
    island.is_suggest = False
    island.is_gathering = False
    island._listen_start = 0.0
    snap = island._build_snapshot()
    assert snap["label"] == "Search"
    assert snap["timer"]
    assert island_render.render(snap).size == (
        island_render.WIN_W, island_render.WIN_H,
    )

    island.state = "building"
    island.armed = True
    island.build_color = "#A855F7"
    island.build_colors = ["#A855F7"]
    island.build_label = "Prompt"
    island.build_offline = False
    assert island._build_snapshot()["armed"] is True


def test_webui_source_contracts_follow_platform_defaults():
    source = APP_JS.read_text(encoding="utf-8")
    hydrate = source[source.index("async function hydrateSettings"):
                     source.index("function nested",
                                  source.index("async function hydrateSettings"))]
    wiring = source[source.index("function wireSettingsControls"):
                    source.index('window.addEventListener("pywebviewready"')]
    meetings = source[source.index("async function renderMeetings"):
                      source.index("async function openMeeting")]
    expected_history = settings.DEFAULTS["history_hotkey"]

    assert "arrayBufferToBase64(reader.result)" in source
    assert "String.fromCharCode.apply(null, new Uint8Array(reader.result))" not in source
    assert "f.size > MAX_READER_IMPORT_BYTES" in source
    assert "hydrateAccountCard();" in hydrate
    assert "wireAccountCard();" in wiring
    assert "meetingsById.get(String(mid))" in meetings
    assert 'title: "Delete " + esc(title)' not in meetings
    assert 'case "meeting_set_processing_mode"' in source
    assert "if (!r || r.ok === false)" in hydrate
    assert f'history_hotkey: "{expected_history}"' in source


def test_static_document_and_fallback_contracts():
    html = (APP_DIR / "webui" / "index.html").read_text(encoding="utf-8")
    css = (APP_DIR / "webui" / "app.css").read_text(encoding="utf-8")
    assert '<main id="main-content">' in html
    assert 'rel="icon" href="mumble.png"' in html
    assert 'role="navigation" aria-label="Primary destinations"' in html
    assert "var(--text-primary)" not in css
    assert not (APP_DIR / "app_window.py").exists()
    app_js = APP_JS.read_text(encoding="utf-8")
    assert 'aria-label="Format: ${fmtLabel}"' in app_js
    assert 'if (fmtLabel === "MARKDOWN") fmtLabel = "MD";' in app_js
    shell_source = (APP_DIR / "webui_shell.py").read_text(encoding="utf-8")
    assert "*.m4a" not in shell_source


def test_icon_generator_accepts_current_directory_filename(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    monkeypatch.setattr(assets_gen.branding, "ASSETS_DIR", str(assets))
    assert assets_gen.make_icon("custom.ico") == "custom.ico"
    assert (tmp_path / "custom.ico").is_file()


def test_clip_thumb_accepts_only_managed_images(monkeypatch, tmp_path):
    data = tmp_path / "data"
    managed = data / "clip_images"
    managed.mkdir(parents=True)
    good = managed / "good.png"
    outside = tmp_path / "outside.png"
    Image.new("RGB", (4, 4), "green").save(good)
    Image.new("RGB", (4, 4), "red").save(outside)
    monkeypatch.setattr(webui_shell.branding, "DATA_DIR", str(data))
    api = webui_shell.Api.__new__(webui_shell.Api)
    assert api.get_clip_thumb(str(good)).startswith("data:image/png;base64,")
    assert api.get_clip_thumb(str(outside)) == ""


def test_webui_listener_commands_require_the_current_session_token(
        monkeypatch, tmp_path):
    token_path = tmp_path / "cmd.token"
    token_path.write_text("first-token", encoding="utf-8")
    monkeypatch.setattr(
        webui_shell.branding, "cmd_token_path", lambda: str(token_path))
    monkeypatch.setattr(webui_shell, "_CMD_TOKEN", None)

    payload = webui_shell._webui_payload({"cmd": "show"})
    assert payload == {"cmd": "show", "token": "first-token"}
    assert webui_shell._webui_token_ok(payload)
    assert not webui_shell._webui_token_ok({"cmd": "show"})

    # A controller restart rotates the file.  The listener must invalidate a
    # previously valid command even if the old token was cached in this process.
    token_path.write_text("second-token", encoding="utf-8")
    assert not webui_shell._webui_token_ok(payload)
    assert webui_shell._webui_token_ok(
        {"cmd": "show", "token": "second-token"})

    controller_source = (APP_DIR / "mumble_linux.py").read_text(
        encoding="utf-8")
    send_source = controller_source[
        controller_source.index("    def _send_webui("):
        controller_source.index("    def _send_webui_async(")
    ]
    assert 'payload["token"]' in send_source


def test_reader_disk_import_is_bounded_before_and_during_read(
        monkeypatch, tmp_path):
    api = webui_shell.Api.__new__(webui_shell.Api)
    path = tmp_path / "large.txt"
    path.write_text("small placeholder", encoding="utf-8")
    parsed = []
    with monkeypatch.context() as scoped:
        scoped.setattr(
            webui_shell.os.path, "getsize",
            lambda _path: webui_shell.MAX_READER_IMPORT_BYTES + 1)
        scoped.setattr(
            reader_parser.ParserRegistry, "parse_file",
            lambda *_args, **_kwargs: parsed.append(True))
        result = api.reader_import_file(str(path))
    assert result["ok"] is False
    assert "32 MB" in result["message"]
    assert parsed == []

    # The parser itself enforces the same limit, closing the stat/read race.
    path.write_bytes(b"12345")
    try:
        reader_parser.ParserRegistry.parse_file(str(path), max_bytes=4)
    except ValueError as exc:
        assert "larger" in str(exc)
    else:
        raise AssertionError("parse_file accepted a file beyond max_bytes")


def test_reader_rejects_archive_expansion_and_extracted_text_bombs(
        monkeypatch):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr("word/document.xml", b"expanded")
    monkeypatch.setattr(reader_parser, "_MAX_ARCHIVE_UNCOMPRESSED_BYTES", 1)
    try:
        reader_parser._guard_zip_archive(archive.getvalue(), "DOCX file")
    except ValueError as exc:
        assert "safety limit" in str(exc)
    else:
        raise AssertionError("archive expansion limit was not enforced")

    api = webui_shell.Api.__new__(webui_shell.Api)
    encoded = base64.b64encode(
        b"a" * (webui_shell.MAX_READER_TEXT_CHARS + 1)).decode("ascii")
    result = api.reader_import_bytes(encoded, "large.txt")
    assert result["ok"] is False
    assert "600,000 characters" in result["message"]


def test_reader_store_preserves_full_library_and_recovers_backup(
        monkeypatch, tmp_path):
    library = tmp_path / "reader_library.json"
    monkeypatch.setattr(reader_store, "PATH", str(library))
    rows = [
        {"id": str(i), "text": f"document {i}", "opened": float(i)}
        for i in range(101)
    ]
    assert reader_store._save(rows)
    assert len(reader_store._load()) == 101

    backup_rows = [{"id": "safe", "text": "recovered", "opened": 1}]
    (tmp_path / "reader_library.json.bak").write_text(
        json.dumps(backup_rows), encoding="utf-8")
    library.write_text("{broken json", encoding="utf-8")
    assert reader_store._load() == backup_rows
    assert json.loads(library.read_text(encoding="utf-8")) == backup_rows
    assert list(tmp_path.glob("reader_library.json.corrupt-*"))


def test_reader_store_reports_failed_saves_and_keeps_empty_collections(
        monkeypatch, tmp_path):
    library = tmp_path / "reader_library.json"
    monkeypatch.setattr(reader_store, "PATH", str(library))
    assert reader_store.create_collection("Empty collection")
    assert reader_store.list_collections() == [
        {"name": "Empty collection", "count": 0}
    ]

    with monkeypatch.context() as scoped:
        scoped.setattr(reader_store, "_save", lambda _rows: False)
        assert reader_store.save_doc("Unsaved", "must not claim success") is None
    assert reader_store.save_doc(
        "Too large", "x" * (reader_store.MAX_DOCUMENT_CHARS + 1)) is None


def test_reader_bridge_propagates_storage_failures(monkeypatch):
    api = webui_shell.Api.__new__(webui_shell.Api)
    saved = []
    monkeypatch.setattr(
        reader_store, "save_doc", lambda *_args, **_kwargs: saved.append(True))
    result = api.reader_save(
        "Too large", "x" * (webui_shell.MAX_READER_TEXT_CHARS + 1))
    assert result["ok"] is False
    assert saved == []

    monkeypatch.setattr(
        reader_store, "log_reading_session", lambda *_args: False)
    assert api.reader_log_session("missing", 0, 1, 10) == {"ok": False}
    monkeypatch.setattr(
        reader_store, "delete_collection", lambda _name: None)
    assert api.reader_delete_collection("Unsaved")["ok"] is False


def test_reader_voice_preview_does_not_leak_a_temp_file(monkeypatch):
    api = webui_shell.Api.__new__(webui_shell.Api)
    route_settings = {
        "pro_mode": True,
        "local_only_mode": False,
        "instant_text": False,
        "reader_tts_provider": "openrouter",
        "reader_tts_model": "google/gemini-3.1-flash-tts-preview",
        "openrouter_api_key": "sk-or-test",
    }
    api.settings = type(
        "RouteSettings", (),
        {"get": lambda _self, key, default=None: route_settings.get(key, default)},
    )()
    monkeypatch.setattr(
        webui_shell.ai, "synthesize_with_fallback",
        lambda *_args, **_kwargs: (
            b"RIFF-preview", "audio/wav", {"ok": True}))
    result = api.reader_tts_test(provider="openrouter")
    assert result["ok"] is True
    assert result["url"].startswith("data:audio/wav;base64,")
    assert "mumble_tts_test_" not in result["url"]


def test_speaker_toasts_do_not_double_escape_plain_text():
    source = APP_JS.read_text(encoding="utf-8")
    assert '"Speaker renamed to " + esc(name)' not in source
    assert '"Speaker named " + esc(name)' not in source
    assert '"Speaker renamed to " + name' in source
    assert '"Speaker named " + name' in source


def test_favorite_bridge_reports_persistence_failure(monkeypatch):
    class FailedFavorites:
        def toggle(self, *_args):
            return None

        def is_fav(self, _text):
            return True

    api = webui_shell.Api.__new__(webui_shell.Api)
    monkeypatch.setattr(api, "_favs", lambda: FailedFavorites())
    result = api.toggle_favorite("still starred")
    assert result == {
        "ok": False,
        "fav": True,
        "message": "Couldn't save favourites.",
    }


def test_settings_bridge_reports_persistence_failure(monkeypatch):
    api = webui_shell.Api.__new__(webui_shell.Api)
    api.settings = MemorySettings()
    monkeypatch.setattr(api.settings, "set", lambda *_args: False)
    sent = []
    monkeypatch.setattr(webui_shell, "_ctrl_send", lambda *args, **kwargs: sent.append(args))
    result = api.set_setting("model", "small.en")
    assert result == {"ok": False, "message": "Couldn't save settings."}
    assert sent == []
