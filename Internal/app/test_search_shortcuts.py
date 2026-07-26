#!/usr/bin/env python3
"""Focused regression tests for Mumble Find shortcut defaults and conflicts."""

import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch


# The remote audit environment intentionally lacks the global-hook dependency.
# These tests exercise parsing/collision logic only; provide the smallest parser
# seam needed to import bindings without trying to install an OS keyboard hook.
try:
    import keyboard  # noqa: F401
except ModuleNotFoundError:
    fake_keyboard = types.ModuleType("keyboard")

    def _parse_hotkey(spec):
        if not spec or "!!" in spec:
            raise ValueError("invalid hotkey")
        return [[part.strip() for part in spec.split("+")]]

    fake_keyboard.parse_hotkey = _parse_hotkey
    fake_keyboard.add_hotkey = lambda *_a, **_k: "hook"
    fake_keyboard.remove_hotkey = lambda *_a, **_k: None
    fake_keyboard.on_press_key = lambda *_a, **_k: "press"
    fake_keyboard.on_release_key = lambda *_a, **_k: "release"
    fake_keyboard.unhook = lambda *_a, **_k: None
    fake_keyboard.is_pressed = lambda *_a, **_k: False
    sys.modules["keyboard"] = fake_keyboard

import bindings
import branding
from settings import (
    SEARCH_HOTKEY_DEFAULT,
    SEARCH_HOTKEY_LEGACY_DEFAULT,
    Settings,
)


class SearchShortcutConflictTests(unittest.TestCase):
    def test_default_is_valid_and_distinct_from_actual_mumble_bindings(self):
        self.assertEqual(SEARCH_HOTKEY_DEFAULT, "ctrl+alt+f")
        self.assertTrue(bindings.validate(SEARCH_HOTKEY_DEFAULT)[0])
        actual = {
            "dictation": "ctrl+windows",
            "paste_latest": "ctrl+alt+v",
            "deck": "ctrl+alt+d",
            "search": SEARCH_HOTKEY_DEFAULT,
        }
        rows = list(actual.items())
        for index, (left_name, left) in enumerate(rows):
            for right_name, right in rows[index + 1:]:
                self.assertFalse(
                    bindings.conflicts(left, right),
                    f"{left_name} conflicts with {right_name}",
                )

    def test_alias_order_and_sided_modifiers_collide_semantically(self):
        self.assertTrue(bindings.conflicts(
            "windows+control+f", "ctrl+win+f"
        ))
        self.assertTrue(bindings.conflicts(
            "left ctrl+right alt+f", "alt+ctrl+f"
        ))
        self.assertTrue(bindings.conflicts("mouse:forward", "mouse:x2"))

    def test_subset_and_superset_chords_are_rejected(self):
        self.assertTrue(bindings.conflicts("ctrl+windows", "ctrl+windows+f"))
        found = bindings.find_conflict(
            "alt+ctrl+f",
            {"search_hotkey": "ctrl+alt+f", "history_hotkey": "ctrl+alt+d"},
            exclude="history_hotkey",
        )
        self.assertEqual(found, ("search_hotkey", "ctrl+alt+f"))

    def test_self_edit_is_excluded_and_sequences_are_not_accepted(self):
        self.assertIsNone(bindings.find_conflict(
            "ctrl+alt+f", {"search_hotkey": "alt+ctrl+f"},
            exclude="search_hotkey",
        ))
        ok, message = bindings.validate("ctrl+k, ctrl+f")
        self.assertFalse(ok)
        self.assertIn("one key combination", message.lower())


class SearchShortcutMigrationTests(unittest.TestCase):
    def load_settings(self, initial=None):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        settings_path = root / "settings.json"
        if initial is not None:
            settings_path.write_text(json.dumps(initial), encoding="utf-8")
        patches = (
            patch.object(branding, "DATA_DIR", str(root)),
            patch.object(branding, "SETTINGS_PATH", str(settings_path)),
        )
        for item in patches:
            item.start()
        try:
            result = Settings()
        finally:
            for item in reversed(patches):
                item.stop()
        temp.cleanup()
        return result

    def test_first_run_uses_find_default(self):
        settings = self.load_settings()
        self.assertEqual(settings.get("search_hotkey"), SEARCH_HOTKEY_DEFAULT)
        self.assertTrue(settings.get("search_hotkey_find_default_applied"))

    def test_exact_old_default_migrates_once(self):
        settings = self.load_settings({
            "search_hotkey": SEARCH_HOTKEY_LEGACY_DEFAULT,
            "search_hotkey_find_default_applied": False,
        })
        self.assertEqual(settings.get("search_hotkey"), SEARCH_HOTKEY_DEFAULT)

    def test_custom_binding_is_preserved(self):
        settings = self.load_settings({
            "search_hotkey": "ctrl+shift+f9",
            "search_hotkey_find_default_applied": False,
        })
        self.assertEqual(settings.get("search_hotkey"), "ctrl+shift+f9")

    def test_completed_guard_preserves_later_user_choice(self):
        settings = self.load_settings({
            "search_hotkey": SEARCH_HOTKEY_LEGACY_DEFAULT,
            "search_hotkey_find_default_applied": True,
        })
        self.assertEqual(
            settings.get("search_hotkey"), SEARCH_HOTKEY_LEGACY_DEFAULT
        )


class SearchCommandAcknowledgementTests(unittest.TestCase):
    def test_find_toggle_command_uses_resident_lifecycle_without_main_window(self):
        import webui_shell

        delivered = threading.Event()
        replies = []
        main_calls = []
        show_calls = []
        toggle_calls = []
        request = json.dumps({
            "cmd": "system_search_toggle", "token": "test-token",
        }).encode("utf-8") + b"\n"

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def settimeout(self, _timeout):
                pass

            def recv(self, _size):
                return request

            def sendall(self, data):
                replies.append(data)
                delivered.set()

        class Server:
            first = True

            def accept(self):
                if self.first:
                    self.first = False
                    return Connection(), None
                raise OSError("test listener complete")

        def ensure_main(activate=False):
            main_calls.append(activate)
            return object()

        def ensure_search():
            show_calls.append(True)
            return object()

        def toggle_search():
            toggle_calls.append(True)
            return {"ok": True, "state": "visible", "message": ""}

        with patch.object(webui_shell, "_webui_token_ok", return_value=True):
            webui_shell._serve_webui_commands(
                Server(), {"main": None, "main_min": False},
                ensure_main, ensure_search, "Mumble", toggle_search,
            )
            self.assertTrue(delivered.wait(1.0))

        reply = json.loads(replies[0].decode("utf-8"))
        self.assertEqual(reply, {"ok": True, "message": ""})
        self.assertEqual(toggle_calls, [True])
        self.assertEqual(show_calls, [])
        self.assertEqual(main_calls, [])


class SearchBrowserRoutingTests(unittest.TestCase):
    def test_configured_browser_executable_is_used(self):
        import webui_shell

        api = webui_shell.Api.__new__(webui_shell.Api)
        api.settings = {"browser": "brave"}
        executable = r"C:\Browser\brave.exe"
        with patch.dict(
            webui_shell.BROWSER_EXECUTABLES,
            {"brave": [executable]}, clear=True,
        ), patch.object(
            webui_shell.os.path, "isfile", return_value=True,
        ), patch.object(webui_shell.subprocess, "Popen") as popen:
            self.assertTrue(api.open_url("https://example.test/search?q=mumble"))
        popen.assert_called_once_with([
            executable, "https://example.test/search?q=mumble",
        ])

    def test_missing_selected_executable_falls_back_to_system_browser(self):
        import webbrowser
        import webui_shell

        api = webui_shell.Api.__new__(webui_shell.Api)
        api.settings = {"browser": "edge"}
        with patch.object(
            webui_shell.os.path, "isfile", return_value=False,
        ), patch.object(
            webbrowser, "open_new_tab", return_value=True,
        ) as fallback:
            self.assertTrue(api.open_url("https://example.test/"))
        fallback.assert_called_once_with("https://example.test/")

    def test_failed_selected_browser_launch_falls_back_to_system_browser(self):
        import webbrowser
        import webui_shell

        api = webui_shell.Api.__new__(webui_shell.Api)
        api.settings = {"browser": "brave"}
        executable = r"C:\Browser\brave.exe"
        with patch.dict(
            webui_shell.BROWSER_EXECUTABLES,
            {"brave": [executable]}, clear=True,
        ), patch.object(
            webui_shell.os.path, "isfile", return_value=True,
        ), patch.object(
            webui_shell.subprocess, "Popen", side_effect=OSError("blocked"),
        ), patch.object(
            webbrowser, "open_new_tab", return_value=True,
        ) as fallback:
            self.assertTrue(api.open_url("https://example.test/"))
        fallback.assert_called_once_with("https://example.test/")


class SearchWebRebindTests(unittest.TestCase):
    class MemorySettings:
        def __init__(self):
            self.values = {
                "hotkey": "ctrl+windows",
                "quick_paste_hotkey": "ctrl+alt+v",
                "history_hotkey": "ctrl+alt+d",
                "search_hotkey": "ctrl+alt+f",
            }

        def load(self):
            pass

        def get(self, key, default=None):
            return self.values.get(key, default)

    def api(self):
        import webui_shell

        api = webui_shell.Api.__new__(webui_shell.Api)
        api.settings = self.MemorySettings()
        return api

    def test_collision_is_action_specific_and_never_reaches_controller(self):
        import webui_shell

        api = self.api()
        with patch.object(webui_shell, "_ctrl_send") as controller:
            result = api.set_setting("search_hotkey", "alt+ctrl+d")
        self.assertFalse(result["ok"])
        self.assertIn("Open Deck", result["message"])
        self.assertEqual(api.settings.get("search_hotkey"), "ctrl+alt+f")
        controller.assert_not_called()

    def test_missing_controller_fails_closed_without_persisting(self):
        import webui_shell

        api = self.api()
        with patch.object(webui_shell, "_ctrl_send", return_value=None):
            result = api.set_setting("search_hotkey", "ctrl+shift+f9")
        self.assertFalse(result["ok"])
        self.assertFalse(result["applied"])
        self.assertEqual(api.settings.get("search_hotkey"), "ctrl+alt+f")

    def test_confirmed_controller_rebind_is_reported_live(self):
        import webui_shell

        api = self.api()

        def controller(request, timeout=None):
            self.assertEqual(request["cmd"], "rebind")
            self.assertEqual(timeout, 2.0)
            api.settings.values[request["key"]] = request["value"]
            return {"ok": True, "message": "active"}

        with patch.object(webui_shell, "_ctrl_send", side_effect=controller):
            result = api.set_setting("search_hotkey", "ctrl+shift+f9")
        self.assertTrue(result["ok"])
        self.assertTrue(result["applied"])
        self.assertEqual(api.settings.get("search_hotkey"), "ctrl+shift+f9")


if __name__ == "__main__":
    unittest.main()
