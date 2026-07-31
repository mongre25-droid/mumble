"""Focused regressions for the desktop/WebUI bug-audit fixes."""

import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest
from PIL import Image

import assets_gen
import autostart
import clipboard as clipboard_mod
from clipboard import Clipboard
import island_render
import overlay_linux as overlay
import webui_shell


APP_DIR = Path(__file__).resolve().parent
APP_JS = APP_DIR / "webui" / "app.js"


class _MemorySettings:
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
    settings = _MemorySettings(cerebras_api_key=real_key)
    api = webui_shell.Api.__new__(webui_shell.Api)
    api.settings = settings
    seen = []
    monkeypatch.setattr(
        webui_shell.ai, "cerebras_test",
        lambda key: (seen.append(key) or True, "Connected"),
    )

    result = api.test_key("cerebras", webui_shell._mask_api_key(real_key))

    assert result["ok"] is True
    assert seen == [real_key]
    assert settings.values["cerebras_api_key"] == real_key
    assert all("•" not in value for _, value in settings.writes)


def test_reader_import_bytes_rejects_invalid_and_oversize_data(monkeypatch):
    api = webui_shell.Api.__new__(webui_shell.Api)
    assert api.reader_import_bytes("not base64!", "bad.txt")["ok"] is False

    monkeypatch.setattr(webui_shell, "MAX_READER_IMPORT_BYTES", 4)
    result = api.reader_import_bytes(base64.b64encode(b"12345").decode(), "big.txt")
    assert result["ok"] is False
    assert "larger" in result["message"].lower()


def test_large_array_buffer_base64_helper_does_not_overflow():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    source = APP_JS.read_text(encoding="utf-8")
    match = re.search(
        r"^function arrayBufferToBase64\(buffer\) \{.*?^\}",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match, "arrayBufferToBase64 helper is missing"
    script = match.group(0) + r"""
const input = new Uint8Array(1024 * 1024);
for (let i = 0; i < input.length; i++) input[i] = i & 255;
const encoded = arrayBufferToBase64(input);
const decoded = Buffer.from(encoded, "base64");
if (decoded.length !== input.length || decoded[0] !== 0 || decoded[decoded.length - 1] !== 255) {
  process.exit(2);
}
"""
    result = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_clipboard_restart_invalidates_the_old_monitor(monkeypatch, tmp_path):
    threads = []

    class _Thread:
        def __init__(self, target, args=(), daemon=None):
            self.target = target
            self.args = args
            self.daemon = daemon
            threads.append(self)

        def start(self):
            pass

    monkeypatch.setattr(clipboard_mod.threading, "Thread", _Thread)
    monkeypatch.setattr(clipboard_mod.pyperclip, "paste", lambda: "")
    clip = Clipboard(str(tmp_path / "clipboard.json"), img_dir=str(tmp_path / "images"))

    clip.start()
    first = clip._stop_event
    clip.stop()
    clip.start()
    second = clip._stop_event

    assert first is not second
    assert first.is_set()
    assert not second.is_set()
    assert len(threads) == 2
    assert threads[0].args == (first,)
    assert threads[1].args == (second,)


def test_clipboard_never_deletes_an_unmanaged_image(tmp_path):
    outside = tmp_path / "outside.png"
    Image.new("RGB", (2, 2), "red").save(outside)
    store = tmp_path / "clipboard.json"
    store.write_text(json.dumps([{
        "type": "image", "path": str(outside), "stamp": "2026-01-01 00:00:00",
    }]), encoding="utf-8")

    clip = Clipboard(str(store), img_dir=str(tmp_path / "managed"))
    assert clip.recent() == []
    clip.clear()
    assert outside.exists()


def test_clipboard_image_hash_uses_full_pixels():
    first = Image.new("RGB", (512, 512), "black")
    second = first.copy()
    second.putpixel((511, 511), (255, 255, 255))
    assert Clipboard._img_hash(first) != Clipboard._img_hash(second)


def test_voice_search_is_a_visible_animated_island_state():
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
    assert island_render.pill_width(snap) > island_render.PILL_MIN_W
    assert island_render.render(snap).size == (
        island_render.WIN_W, island_render.WIN_H,
    )


def test_prompt_ring_survives_the_building_state():
    island = overlay._GtkIsland.__new__(overlay._GtkIsland)
    island.state = "building"
    island.frame = 1
    island.level = 0.0
    island.armed = True
    island.is_suggest = False
    island.is_gathering = False
    island.build_color = "#A855F7"
    island.build_colors = ["#A855F7"]
    island.build_label = "Prompt"
    island.build_offline = False
    assert island._build_snapshot()["armed"] is True


def test_account_wiring_and_live_meeting_delete_source_are_present():
    source = APP_JS.read_text(encoding="utf-8")
    hydrate = source[source.index("async function hydrateSettings"):
                     source.index("function nested", source.index("async function hydrateSettings"))]
    wiring = source[source.index("function wireSettingsControls"):
                    source.index('window.addEventListener("pywebviewready"')]
    meetings = source[source.index("async function renderMeetings"):
                      source.index("async function openMeeting")]
    assert "hydrateAccountCard();" in hydrate
    assert "wireAccountCard();" in wiring
    assert "meetingsById.get(String(mid))" in meetings
    assert 'title: "Delete " + esc(title)' not in meetings
    assert 'safe("get_settings")' in meetings
    assert 'safe("meeting_context")' in meetings
    assert "meetingApplyPrivacy(settings" in meetings
    assert 'history_hotkey: "Ctrl + Alt + D"' in source
    assert 'history_hotkey: "ctrl+alt+h"' not in source


def test_get_settings_preserves_current_false_defaults():
    projected = {
        "prompt_mode_enabled": False,
        "auto_format": False,
        "foreign_languages": ["arabic", "urdu"],
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
    api.settings = _MemorySettings(**projected)

    result = api.get_settings()

    assert {key: result[key] for key in projected} == projected
    assert result["deck_pinned"] is False


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


def test_icon_generator_accepts_a_current_directory_filename(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    monkeypatch.setattr(assets_gen.branding, "ASSETS_DIR", str(assets_dir))
    result = assets_gen.make_icon("custom.ico")
    assert result == "custom.ico"
    assert (tmp_path / "custom.ico").is_file()


def test_web_document_has_landmarks_favicon_and_valid_css_variable():
    html = (APP_DIR / "webui" / "index.html").read_text(encoding="utf-8")
    css = (APP_DIR / "webui" / "app.css").read_text(encoding="utf-8")
    assert '<main id="main-content">' in html
    assert 'rel="icon" href="mumble.png"' in html
    assert "var(--text-primary)" not in css


def test_windows_tk_fallback_is_not_present_on_linux():
    assert not (APP_DIR / "app_window.py").exists()
    assert not (APP_DIR / "ui.py").exists()


def test_clip_thumb_accepts_only_managed_images(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    managed = data_dir / "clip_images"
    managed.mkdir(parents=True)
    good = managed / "good.png"
    outside = tmp_path / "outside.png"
    Image.new("RGB", (4, 4), "green").save(good)
    Image.new("RGB", (4, 4), "red").save(outside)
    monkeypatch.setattr(webui_shell.branding, "DATA_DIR", str(data_dir))
    api = webui_shell.Api.__new__(webui_shell.Api)

    assert api.get_clip_thumb(str(good)).startswith("data:image/png;base64,")
    assert api.get_clip_thumb(str(outside)) == ""

    link = managed / "linked.png"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        return
    assert api.get_clip_thumb(str(link)) == ""
