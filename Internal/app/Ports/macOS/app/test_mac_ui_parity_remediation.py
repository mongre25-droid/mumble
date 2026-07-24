"""Focused contracts for the macOS UI/parity remediation batch."""

import ast
import json
import re
import socket
import threading
import time
from pathlib import Path

import overlay_mac
import webui_shell


APP_DIR = Path(__file__).resolve().parent
APP_JS = APP_DIR / "webui" / "app.js"
INDEX_HTML = APP_DIR / "webui" / "index.html"
SHELL = APP_DIR / "webui_shell.py"
CONTROLLER = APP_DIR / "mumble_mac.py"


def test_design_size_clamps_to_short_macos_work_areas():
    assert webui_shell._clamp_design_size(1440, 900) == (960, 852)
    assert webui_shell._clamp_design_size(1024, 600) == (960, 552)
    assert webui_shell._clamp_design_size(None, None) == (960, 1180)


def test_webui_listener_token_rotates_and_fails_closed(monkeypatch):
    values = iter(["old", "new"])
    monkeypatch.setattr(webui_shell, "_read_cmd_token",
                        lambda force=False: next(values))
    assert webui_shell._webui_token_ok({"token": "new"}) is True

    monkeypatch.setattr(webui_shell, "_read_cmd_token",
                        lambda force=False: "")
    assert webui_shell._webui_token_ok({"token": ""}) is False
    assert webui_shell._webui_token_ok({}) is False


def test_webui_listener_rejects_unauthenticated_show(monkeypatch):
    monkeypatch.setattr(webui_shell, "_read_cmd_token",
                        lambda force=False: "session-secret")
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    address = srv.getsockname()
    shown = threading.Event()

    def ensure_main():
        shown.set()
        return object()

    webui_shell._serve_webui_commands(
        srv, {"main": None, "main_min": False}, ensure_main, "Mumble"
    )

    def send(payload):
        with socket.create_connection(address, timeout=1) as conn:
            conn.sendall((json.dumps(payload) + "\n").encode("utf-8"))

    try:
        send({"cmd": "show"})
        time.sleep(0.05)
        assert not shown.is_set()
        send({"cmd": "show", "token": "session-secret"})
        assert shown.wait(1)
    finally:
        srv.close()


def test_cocoa_and_tk_coordinate_conversion_are_distinct():
    convert = overlay_mac._cocoa_rect_to_tk
    assert convert(0, 0, 1440, 900, 900) == (0, 0, 1440, 900)
    assert convert(0, 900, 1440, 900, 900) == (0, -900, 1440, 0)
    assert convert(-1920, -1080, 1920, 1080, 900) == (
        -1920, 900, 0, 1980)


def test_glass_island_uses_cocoa_bottom_and_tk_companion_origin():
    source = (APP_DIR / "overlay_mac.py").read_text(encoding="utf-8")
    assert "y = int(cbottom + BOTTOM_MARGIN)" in source
    assert "self._tk_geo = (tx, ty)" in source
    assert "tx, ty = self.glass._tk_geo" in source
    assert "frame_geo != self._frame_geo" in source
    assert "setIgnoresMouseEvents_(True)" in source


def test_reader_and_meeting_ui_contracts_match_bridge():
    tree = ast.parse(SHELL.read_text(encoding="utf-8"))
    api = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "Api")
    methods = {node.name for node in api.body
               if isinstance(node, ast.FunctionDef)}
    js = APP_JS.read_text(encoding="utf-8")
    calls = set(re.findall(r"\bcall\(\s*['\"]([A-Za-z_]\w*)", js))
    assert not calls.difference(methods)

    for name in ("reader_create_collection", "meeting_retry",
                 "meeting_play_audio", "meeting_save_export"):
        assert name in methods
        assert name in js

    for marker in ("READER.fetching", "readerPruneAudioCache",
                   "openGen !== READER.gen", "MEET.maxSeconds",
                   "meetingPlayAudio", "meetingRetry", "capture_warning"):
        assert marker in js
    assert 'what === "meeting_capture_error"' in js


def test_webui_controller_routes_are_complete():
    source = CONTROLLER.read_text(encoding="utf-8")
    for command in (
        "grab_selection",
        "capture_conversation",
        "focused",
        "set_island_mode",
        "meeting_retry",
    ):
        assert f'cmd == "{command}"' in source
    assert 'active_mode=getattr(self, "active_mode", None)' in source


def test_meeting_bridge_timeouts_cover_macos_device_finalization():
    source = SHELL.read_text(encoding="utf-8")
    assert '{"cmd": "meeting_record_start"}, timeout=15.0' in source
    assert '"title": title or ""}, timeout=20.0' in source


def test_mac_copy_and_assets_are_platform_truthful():
    html = INDEX_HTML.read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")
    assert "Ctrl + Option + D" in html
    assert "Ctrl + Option + H" in html
    assert '<option value="safari">Safari</option>' in html
    assert "Start with Windows" not in html
    assert "Start-menu entry" not in html
    assert "Desktop shortcut" not in html
    assert "%APPDATA%" not in html
    assert "experimental/computer_control" not in html
    assert "anywhere on your Mac" in js


def test_mac_pin_and_native_save_dialog_paths_are_present():
    source = SHELL.read_text(encoding="utf-8")
    assert "window.on_top = bool(on)" in source
    assert "instance.window.focus = not bool(on)" in source
    assert "webview.FileDialog.SAVE" in source
    assert '"deck_pin_supported": sys.platform in ("darwin", "win32")' in source
    assert '"token": _read_cmd_token(force=True)' in source
    assert "if not _webui_token_ok(req):" in source
