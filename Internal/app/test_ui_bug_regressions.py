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
import overlay
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
    assert island_render.pill_width(snap) > island_render.PILL_MIN_W
    assert island_render.render(snap).size == (
        island_render.WIN_W, island_render.WIN_H,
    )


def test_prompt_ring_survives_the_building_state():
    island = overlay.Island.__new__(overlay.Island)
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
    # The old processing-mode selector only persisted inert metadata. Meetings
    # now loads route settings for truthful disclosure and searches the complete
    # local transcript through the bridge.
    assert 'safe("get_settings")' in meetings
    assert '"meeting_search"' in meetings
    index = (APP_DIR / "webui" / "index.html").read_text(encoding="utf-8")
    assert "meeting-proc-mode" not in index
    assert 'history_hotkey: "Ctrl + Alt + D"' in source
    assert 'history_hotkey: "ctrl+alt+h"' not in source


def test_get_settings_projects_every_webui_state_including_false_defaults():
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
    html = (APP_DIR / "webui" / "index.html").read_text(encoding="utf-8")
    bound = {
        key.split(".", 1)[0]
        for key in re.findall(r'data-setting="([^"]+)"', html)
    }
    assert bound <= result.keys()


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


def test_remastered_views_keep_the_centered_layout_contract():
    webui = APP_DIR / "webui"
    html = (webui / "index.html").read_text(encoding="utf-8")
    js = (webui / "app.js").read_text(encoding="utf-8")
    css = (webui / "remaster.css").read_text(encoding="utf-8")

    assert 'href="remaster.css?' in html
    # Settings keeps its centered intro. Stats keeps its semantic heading hidden
    # without repeating a hero the user already understands.
    assert html.count('class="view-intro view-intro-compact"') == 1
    assert '.view-intro,' in css and 'text-align: center' in css
    assert '<h1 id="stats-title" class="sr-only">Stats</h1>' in html
    assert "stats-dashboard-head" not in html
    assert 'aria-labelledby="stats-title" aria-busy="false"' in html

    # Reply remains a deliberate Deck preset, not an armable Smart Mode.
    assert 'const modes = ["prompt", "email", "foreign"]' in js
    assert 'data-mode="reply"' not in html
    assert "prompts, emails and replies" not in html.lower()

    # Rolling comparisons and daily data have one truthful, accessible home.
    assert html.count(">Last 7 days<") == 1
    assert "repeat(7" not in html
    assert 'aria-controls="stat-chart stat-table"' in html
    assert '<summary>View daily values</summary>' in html
    assert 'const payload = await call("get_stats_dashboard")' in js
    assert 'epoch !== STATS_RENDER_EPOCH' in js
    assert 'Reader listening' in html and 'Saved Meetings' in html
    assert 'grid-template-rows: 82px 18px' in css
    assert 'grid-template-columns: repeat(24, minmax(0, 1fr))' in css
    assert 'aria-describedby="tod-summary"' in html

    assert "wireSettingsCategoryNav()" in js
    assert "data-settings-tab" in html
    assert 'classList.toggle("is-reading", open)' in js
    assert "#reader-main.is-reading" in css
    assert 'class="meeting-item-open"' in js
    assert "grid-template-columns: 48px clamp(84px, 18%, 124px) minmax(0, 1fr)" in css

    # Deck, Reader, and Settings retain the shared hierarchy; Meetings now owns
    # one transforming surface rather than two unrelated primary surfaces.
    assert html.count("mumble-surface--primary") >= 2
    assert html.count("mumble-surface--workspace") >= 3
    assert 'class="deck-workspace mumble-surface mumble-surface--workspace"' in html
    assert 'class="meeting-instrument mumble-surface"' in html
    assert 'class="meeting-library-shell meeting-phase"' in html
    assert "--mumble-panel-primary:" in css
    assert "--mumble-panel-workspace:" in css
    assert ".mumble-surface--primary" in css


def test_transcription_settings_lead_with_beginner_flow():
    webui = APP_DIR / "webui"
    html = (webui / "index.html").read_text(encoding="utf-8")
    js = (webui / "app.js").read_text(encoding="utf-8")
    css = (webui / "remaster.css").read_text(encoding="utf-8")

    assert "1. Choose your microphone" in html
    assert "2. Choose where speech becomes text" in html
    assert 'class="transcription-guide"' in html
    assert "Your microphone records audio" in html
    assert 'class="settings-details tx-advanced"' in html
    assert "Mumble chooses sensible defaults automatically" in html
    assert "Optional cloud setup" in html
    assert js.index("root.insertBefore(microphone, vocabulary)") < js.index(
        "root.insertBefore(transcription, vocabulary)"
    )
    assert "#settings-microphone { order: 2; }" in css
    assert "#settings-transcription { order: 3; }" in css


def test_home_shortcuts_are_in_the_hero_and_deck_reflows_without_a_slider():
    webui = APP_DIR / "webui"
    html = (webui / "index.html").read_text(encoding="utf-8")
    js = (webui / "app.js").read_text(encoding="utf-8")
    css = (webui / "remaster.css").read_text(encoding="utf-8")

    hero_marker = re.search(r'<div\s+class="[^"]*\bhero\b[^"]*">', html)
    assert hero_marker is not None
    hero = html[hero_marker.start():
                html.index('<!-- value badges -->', hero_marker.end())]
    assert re.search(r'class="[^"]*\bhome-shortcuts-panel\b[^"]*"', hero)
    assert 'id="home-shortcuts-title">First word to finished work.' in hero
    for hotkey_id in ("hk-record", "hk-paste-latest", "hk-history", "hk-search"):
        assert html.count(f'id="{hotkey_id}"') == 1
    assert "home-shortcuts-card" not in html
    assert 'id="home-edit-shortcuts"' in html
    assert 'data-settings-jump="general" data-settings-target="settings-shortcuts"' in html
    assert 'if (CURRENT !== "settings") navTo("settings")' in js
    shortcut_css = css[css.index(".home-shortcuts-panel {"):
                       css.index(".home-shortcuts-panel::before")]
    assert "backdrop-filter" not in shortcut_css
    assert ".home-shortcut-row:hover" not in css

    assert 'id="deck-more-toggle"' in html
    assert 'id="deck-secondary-actions"' in html
    assert '.deck-toolbar .deck-type-nav {' in css
    narrow = css[css.index("@media (max-width: 760px)"):
                 css.index("@media (max-width: 560px)")]
    assert "overflow: visible" in narrow
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in narrow
    assert ".deck-secondary-actions { display: none; }" in narrow
    assert ".deck-toolbar-actions.more-open .deck-secondary-actions" in narrow
    assert 'setText("#deck-more-label", open ? "Fewer" : "More")' in js


def test_openrouter_models_stay_curated_after_live_availability_check():
    source = APP_JS.read_text(encoding="utf-8")
    assert 'provider === "openrouter"' in source
    assert "const verified = models.filter((m) => available.has(m));" in source
    assert "if (verified.length) models = verified;" in source
    assert "} else {\n          models = r.models;" in source
    assert "GPT-5.4 Mini — fast, balanced everyday processing" in source
    assert "Gemini 3.1 Flash Lite — low-cost short text (preview)" in source
    assert "OpenRouter Free — no-cost routing, model may vary" in source


def test_fallback_home_does_not_render_an_empty_controls_card():
    source = (APP_DIR / "app_window.py").read_text(encoding="utf-8")
    assert "for phrase, what in []" not in source


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


def test_meetings_refresh_keeps_state_privacy_and_keyboard_truth_visible():
    html = (APP_DIR / "webui" / "index.html").read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")
    css = (APP_DIR / "webui" / "remaster.css").read_text(encoding="utf-8")

    refresh = js[js.index("window.pyRefresh = function"):
                 js.index("/* ============================================================================\n   MEETINGS")]
    stop = js[js.index("async function meetingStopRecord"):
              js.index("async function meetingReconcileRecording")]
    assert 'what === "meeting_capture_error"' in refresh
    assert "meetingReconcileRecording" in refresh
    assert "MEET.recording = false" not in stop
    assert 'state: "finalizing"' in stop

    assert 'id="meeting-recording"' in html
    assert 'class="meeting-live-state" role="status" aria-live="polite"' in html
    assert 'id="meeting-transcript"' in html and 'role="region"' in html
    assert 'aria-label="Meeting transcript"' in html and 'tabindex="0"' in html
    assert 'id="meetings-error" role="alert"' in html
    assert "transcript text—not the original recording" in html
    assert "transcription_provider" in js
    assert 'e.key === "Escape"' in js and 'e.key === "/"' in js
    assert '.meeting-speaker-row { display: grid;' in css
    assert '@media (prefers-reduced-motion: reduce)' in css
    assert "Capture the conversation" not in html
    assert ".meeting-start-panel::after" in css
    assert "justify-items: center" in css
