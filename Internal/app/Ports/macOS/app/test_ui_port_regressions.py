"""Focused cross-platform regressions for desktop/WebUI presentation fixes."""

import base64
import json
from pathlib import Path

from PIL import Image

import assets_gen
import autostart
import clipboard as clipboard_mod
from clipboard import Clipboard
import island_render
import overlay
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


def test_search_and_building_island_contracts():
    assert "search" in overlay._ACTIVE_STATES
    assert island_render._anim_span("search") == island_render._anim_span("listening")

    island = overlay.Island.__new__(overlay.Island)
    island.state = "search"
    island.frame = 1
    island.level = 0.5
    island.armed = False
    island.is_suggest = False
    island.is_gathering = False
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
    assert "if (!r || r.ok === false)" in hydrate
    assert settings.DEFAULTS["history_hotkey"] == expected_history


def test_static_document_and_fallback_contracts():
    html = (APP_DIR / "webui" / "index.html").read_text(encoding="utf-8")
    css = (APP_DIR / "webui" / "app.css").read_text(encoding="utf-8")
    fallback = (APP_DIR / "app_window.py").read_text(encoding="utf-8")
    assert '<main id="main-content">' in html
    assert 'rel="icon" href="mumble.png"' in html
    assert 'role="navigation" aria-label="Primary destinations"' in html
    assert "var(--text-primary)" not in css
    assert "for phrase, what in []" not in fallback


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
