#!/usr/bin/env python3
"""Focused regression tests for Mumble Find shortcut defaults and conflicts."""

import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
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
    WEB_SEARCH_HOTKEY_DEFAULT,
    Settings,
)


class SearchShortcutConflictTests(unittest.TestCase):
    def test_default_is_valid_and_distinct_from_actual_mumble_bindings(self):
        self.assertEqual(SEARCH_HOTKEY_DEFAULT, "ctrl+alt+f")
        self.assertTrue(bindings.validate(SEARCH_HOTKEY_DEFAULT)[0])
        self.assertEqual(WEB_SEARCH_HOTKEY_DEFAULT, "ctrl+alt+s")
        self.assertTrue(bindings.validate(WEB_SEARCH_HOTKEY_DEFAULT)[0])
        actual = {
            "dictation": "ctrl+windows",
            "paste_latest": "ctrl+alt+v",
            "deck": "ctrl+alt+d",
            "search": SEARCH_HOTKEY_DEFAULT,
            "web_search": WEB_SEARCH_HOTKEY_DEFAULT,
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

    def test_capture_keeps_the_physical_key_below_escape_layout_independent(self):
        events = [
            types.SimpleNamespace(
                name="alt", event_type="down", scan_code=56,
            ),
            types.SimpleNamespace(
                name="§", event_type="down", scan_code=41,
            ),
        ]

        def hook(callback, suppress=True):
            self.assertTrue(suppress)
            for event in events:
                callback(event)
            return "keyboard-hook"

        with patch.object(bindings.keyboard, "hook", side_effect=hook), \
                patch.object(bindings.keyboard, "unhook"), \
                patch.object(bindings, "HAVE_MOUSE", False):
            captured = bindings.capture(timeout=0.1)

        self.assertEqual(captured, "alt+physical:below-escape")
        self.assertEqual(
            bindings.pretty(captured), "Alt + Physical key below Esc"
        )

    def test_flow_launcher_oem_name_conflicts_with_the_same_physical_key(self):
        with tempfile.TemporaryDirectory() as temp:
            settings_dir = Path(temp) / "FlowLauncher" / "Settings"
            settings_dir.mkdir(parents=True)
            (settings_dir / "Settings.json").write_text(
                json.dumps({"Hotkey": "Alt + Oem8"}), encoding="utf-8"
            )
            message = bindings.external_shortcut_conflict(
                "alt+physical:below-escape", appdata=temp
            )

        self.assertIn("Flow Launcher", message)
        self.assertIn("previous binding", message)
        self.assertIn("Alt + Physical key below Esc", message)

    def test_flow_conflict_keeps_the_previous_live_web_search_binding(self):
        import mumble

        class MemorySettings:
            values = {
                "hotkey": "ctrl+windows",
                "quick_paste_hotkey": "ctrl+alt+v",
                "history_hotkey": "ctrl+alt+d",
                "search_hotkey": "ctrl+alt+f",
                "web_search_hotkey": "ctrl+alt+s",
            }

            def get(self, key, default=None):
                return self.values.get(key, default)

            def set(self, key, value):
                self.values[key] = value
                return True

        app = mumble.Mumble.__new__(mumble.Mumble)
        app.settings = MemorySettings()
        app._binding_lock = threading.RLock()
        app.web_search_hotkey = "ctrl+alt+s"
        app._hk_web_search = "old-web-search-hook"
        app._hk_main = app._hk_quick = app._hk_history = app._hk_search = None
        app.on_web_search_hotkey = lambda: None
        message = (
            "Flow Launcher already uses Alt + Physical key below Esc. "
            "Mumble kept your previous binding."
        )
        with patch.object(
            bindings, "external_shortcut_conflict", return_value=message,
        ), patch.object(bindings, "register_hotkey") as register:
            ok, received = app.apply_web_search_hotkey(
                "alt+physical:below-escape"
            )

        self.assertFalse(ok)
        self.assertEqual(received, message)
        self.assertEqual(app.web_search_hotkey, "ctrl+alt+s")
        self.assertEqual(app._hk_web_search, "old-web-search-hook")
        self.assertEqual(
            app.settings.get("web_search_hotkey"), "ctrl+alt+s"
        )
        register.assert_not_called()


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

    def load_macos_settings(self, initial=None):
        app_root = Path(__file__).resolve().parent
        settings_path = app_root / "Ports" / "macOS" / "app" / "settings.py"
        module_spec = importlib.util.spec_from_file_location(
            "shortcut_settings_macos_focused", settings_path
        )
        settings_module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(settings_module)

        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        disk_path = root / "settings.json"
        if initial is not None:
            disk_path.write_text(json.dumps(initial), encoding="utf-8")
        try:
            with patch.object(branding, "DATA_DIR", str(root)), patch.object(
                branding, "SETTINGS_PATH", str(disk_path)
            ), patch.object(branding, "ensure_dirs", lambda: None):
                result = settings_module.Settings()
        finally:
            temp.cleanup()
        return result, settings_module

    def test_macos_fresh_settings_keep_find_and_web_search_independent(self):
        settings, module = self.load_macos_settings()
        self.assertEqual(settings.get("search_hotkey"), module.SEARCH_HOTKEY_DEFAULT)
        self.assertEqual(
            settings.get("web_search_hotkey"), module.WEB_SEARCH_HOTKEY_DEFAULT
        )
        self.assertNotEqual(
            settings.get("search_hotkey"), settings.get("web_search_hotkey")
        )

    def test_macos_genuine_disk_legacy_web_search_choice_is_preserved(self):
        settings, module = self.load_macos_settings({
            "search_hotkey": "ctrl+option+g",
            "search_hotkey_find_default_applied": False,
        })
        self.assertEqual(settings.get("web_search_hotkey"), "ctrl+option+g")
        self.assertEqual(settings.get("search_hotkey"), module.SEARCH_HOTKEY_DEFAULT)
        self.assertTrue(settings.get("web_search_hotkey_default_applied"))

    def test_macos_legacy_choice_conflicting_with_find_stays_retryable(self):
        settings, _module = self.load_macos_settings({
            "search_hotkey": "ctrl+option+f",
            "search_hotkey_find_default_applied": False,
        })
        self.assertEqual(settings.get("web_search_hotkey"), "ctrl+option+f")
        self.assertFalse(settings.get("web_search_hotkey_default_applied"))
        self.assertIn("Mumble Find", settings.web_search_migration_notice)

    def test_macos_loaded_web_search_conflict_stays_retryable(self):
        settings, _module = self.load_macos_settings({
            "hotkey": "ctrl+option+s",
            "web_search_hotkey": "ctrl+option+s",
            "web_search_hotkey_default_applied": False,
            "search_hotkey_find_default_applied": True,
        })
        self.assertEqual(settings.get("web_search_hotkey"), "ctrl+option+s")
        self.assertFalse(settings.get("web_search_hotkey_default_applied"))
        self.assertIn("Dictate", settings.web_search_migration_notice)

    def test_macos_translation_created_web_search_conflict_stays_retryable(self):
        settings, _module = self.load_macos_settings({
            "hotkey": "ctrl+windows",
            "web_search_hotkey": "ctrl+option+d",
            "web_search_hotkey_default_applied": False,
            "search_hotkey_find_default_applied": True,
            "mac_hotkeys_v1_applied": False,
        })
        self.assertEqual(settings.get("hotkey"), "ctrl+option+d")
        self.assertEqual(settings.get("web_search_hotkey"), "ctrl+option+d")
        self.assertFalse(settings.get("web_search_hotkey_default_applied"))
        self.assertIn("Dictate", settings.web_search_migration_notice)

    def test_macos_safe_translated_shortcuts_complete_migration(self):
        settings, _module = self.load_macos_settings({
            "hotkey": "ctrl+windows",
            "web_search_hotkey": "ctrl+alt+s",
            "web_search_hotkey_default_applied": False,
            "search_hotkey_find_default_applied": True,
            "mac_hotkeys_v1_applied": False,
        })
        self.assertEqual(settings.get("hotkey"), "ctrl+option+d")
        self.assertEqual(settings.get("web_search_hotkey"), "ctrl+option+s")
        self.assertTrue(settings.get("web_search_hotkey_default_applied"))
        self.assertIsNone(getattr(settings, "web_search_migration_notice", None))

    def test_first_run_uses_find_default(self):
        settings = self.load_settings()
        self.assertEqual(settings.get("search_hotkey"), SEARCH_HOTKEY_DEFAULT)
        self.assertEqual(
            settings.get("web_search_hotkey"), WEB_SEARCH_HOTKEY_DEFAULT
        )
        self.assertTrue(settings.get("search_hotkey_find_default_applied"))
        self.assertTrue(settings.get("web_search_hotkey_default_applied"))

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

    def test_existing_find_install_adds_web_search_without_changing_find(self):
        settings = self.load_settings({
            "search_hotkey": "ctrl+shift+f9",
            "search_hotkey_find_default_applied": True,
        })
        self.assertEqual(settings.get("search_hotkey"), "ctrl+shift+f9")
        self.assertEqual(
            settings.get("web_search_hotkey"), WEB_SEARCH_HOTKEY_DEFAULT
        )

    def test_completed_web_search_migration_preserves_user_choice(self):
        settings = self.load_settings({
            "web_search_hotkey": "ctrl+shift+f10",
            "web_search_hotkey_default_applied": True,
        })
        self.assertEqual(settings.get("web_search_hotkey"), "ctrl+shift+f10")

    def test_upgrade_keeps_user_find_binding_and_avoids_new_web_collision(self):
        settings = self.load_settings({
            "search_hotkey": WEB_SEARCH_HOTKEY_DEFAULT,
            "search_hotkey_find_default_applied": True,
        })
        self.assertEqual(settings.get("search_hotkey"), WEB_SEARCH_HOTKEY_DEFAULT)
        self.assertFalse(settings.get("web_search_hotkey_default_applied"))
        self.assertIn(
            "Web Search was left unregistered",
            settings.web_search_migration_notice,
        )

    def test_migration_and_startup_leave_conflicting_web_search_unregistered(self):
        app_root = Path(__file__).resolve().parent
        platforms = (
            (
                "Windows",
                app_root / "settings.py",
                app_root / "mumble.py",
                "ctrl+alt+s",
                True,
            ),
            (
                "Linux",
                app_root / "Ports" / "Linux" / "app" / "settings.py",
                app_root / "Ports" / "Linux" / "app" / "mumble_linux.py",
                "ctrl+alt+s",
                True,
            ),
            (
                "macOS",
                app_root / "Ports" / "macOS" / "app" / "settings.py",
                app_root / "Ports" / "macOS" / "app" / "mumble_mac.py",
                "ctrl+option+s",
                False,
            ),
        )
        command_keys = (
            ("Dictate", "hotkey"),
            ("Paste latest", "quick_paste_hotkey"),
            ("Open Deck", "history_hotkey"),
        )

        for platform, settings_path, controller_path, chord, has_find in platforms:
            module_spec = importlib.util.spec_from_file_location(
                f"shortcut_settings_{platform.lower()}", settings_path
            )
            settings_module = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(settings_module)

            for label, key in command_keys:
                with self.subTest(platform=platform, action=label), \
                        tempfile.TemporaryDirectory() as temp:
                    settings_file = Path(temp) / "settings.json"
                    settings_file.write_text(
                        json.dumps({
                            key: chord,
                            "search_hotkey_find_default_applied": True,
                            "web_search_hotkey_default_applied": False,
                        }),
                        encoding="utf-8",
                    )
                    with patch.object(branding, "DATA_DIR", temp), \
                            patch.object(
                                branding, "SETTINGS_PATH", str(settings_file)
                            ), patch.object(branding, "ensure_dirs", lambda: None):
                        migrated = settings_module.Settings()

                    self.assertEqual(migrated.get(key), chord)
                    self.assertFalse(
                        migrated.get("web_search_hotkey_default_applied"),
                        "a conflict must stay retryable",
                    )

                    tree = ast.parse(controller_path.read_text(encoding="utf-8"))
                    owner = next(
                        node for node in tree.body
                        if isinstance(node, ast.ClassDef) and node.name == "Mumble"
                    )
                    required = {
                        "_press_binding_values",
                        "_active_binding_conflict",
                        "_sane_press_hotkey",
                        "_register_web_search",
                    }
                    methods = {
                        node.name: node
                        for node in owner.body
                        if isinstance(node, ast.FunctionDef) and node.name in required
                    }
                    self.assertEqual(set(methods), required)
                    namespace = {
                        "bindings": bindings,
                        "os": __import__("os"),
                        "sys": sys,
                        "WEB_SEARCH_HOTKEY_DEFAULT": "ctrl+alt+s",
                    }
                    exec(
                        compile(
                            ast.fix_missing_locations(
                                ast.Module(body=list(methods.values()), type_ignores=[])
                            ),
                            controller_path.name,
                            "exec",
                        ),
                        namespace,
                    )

                    values = {
                        "hotkey": "ctrl+shift+f7",
                        "quick_paste_hotkey": "ctrl+shift+f8",
                        "history_hotkey": "ctrl+shift+f9",
                        "search_hotkey": "ctrl+shift+f10",
                        "web_search_hotkey": chord,
                    }
                    values[key] = chord

                    class MemorySettings:
                        def get(self, setting_key, default=None):
                            return values.get(setting_key, default)

                        def set(self, setting_key, value):
                            values[setting_key] = value
                            return True

                    class Dummy:
                        _PRESS_BINDING_LABELS = {
                            "hotkey": "Dictate",
                            "quick_paste_hotkey": "Paste latest",
                            "history_hotkey": "Open Deck",
                            "search_hotkey": "Mumble Find",
                            "web_search_hotkey": "Web Search",
                        }

                    app = Dummy()
                    app.settings = MemorySettings()
                    app.hotkey = values["hotkey"]
                    app.quick_hotkey = values["quick_paste_hotkey"]
                    app.history_hotkey = values["history_hotkey"]
                    app.search_hotkey = values["search_hotkey"]
                    app.web_search_hotkey = chord
                    app._hk_main = object()
                    app._hk_quick = object()
                    app._hk_history = object()
                    app._hk_search = object() if has_find else None
                    app._hk_web_search = None
                    app.on_web_search_hotkey = lambda: None
                    for name in required:
                        setattr(app, name, types.MethodType(namespace[name], app))

                    with patch.object(bindings, "register_hotkey") as register:
                        with self.assertRaisesRegex(ValueError, label):
                            app._register_web_search()
                    register.assert_not_called()
                    self.assertIsNone(app._hk_web_search)
                    self.assertEqual(values[key], chord)
                    self.assertEqual(values["web_search_hotkey"], chord)


class WebSearchGlobalCommandTests(unittest.TestCase):
    class ImmediateThread:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    def test_selected_text_uses_web_search_without_toggling_mumble_find(self):
        import mumble

        app = mumble.Mumble.__new__(mumble.Mumble)
        app.lock = threading.RLock()
        app.paused = False
        app.busy = False
        app.recording = False
        app._processing = False
        app._grab_selection_quiet = lambda: "selected words"
        prepared = []
        app.request_web_search = lambda text: prepared.append(text) or {"ok": True}
        app._toggle_system_search_page = lambda *_args: self.fail(
            "Web Search must not toggle Mumble Find"
        )

        with patch.object(mumble.threading, "Thread", self.ImmediateThread):
            self.assertTrue(app.on_web_search_hotkey())

        self.assertEqual(prepared, ["selected words"])
        self.assertFalse(app.busy)

    def test_web_search_can_take_over_an_active_dictation_without_find_collision(self):
        import mumble

        app = mumble.Mumble.__new__(mumble.Mumble)
        app.lock = threading.RLock()
        app.paused = False
        app.busy = False
        app.recording = True
        app._search_requested = False
        stopped = []
        app._safe_stop = lambda: stopped.append(True)
        app._toggle_system_search_page = lambda *_args: self.fail(
            "Web Search must not toggle Mumble Find"
        )

        with patch.object(mumble.threading, "Thread", self.ImmediateThread):
            self.assertTrue(app.on_web_search_hotkey())

        self.assertTrue(app._search_requested)
        self.assertEqual(stopped, [True])

    def test_find_and_web_search_register_and_swap_independently(self):
        import mumble

        values = {
            "hotkey": "ctrl+windows",
            "quick_paste_hotkey": "ctrl+alt+v",
            "history_hotkey": "ctrl+alt+d",
            "search_hotkey": "ctrl+alt+f",
            "web_search_hotkey": "ctrl+alt+s",
        }

        class MemorySettings:
            def get(self, key, default=None):
                return values.get(key, default)

            def set(self, key, value):
                values[key] = value
                return True

        app = mumble.Mumble.__new__(mumble.Mumble)
        app.settings = MemorySettings()
        app._hk_main = app._hk_quick = app._hk_history = None
        app._hk_search = app._hk_web_search = None
        app.search_hotkey = "ctrl+alt+f"
        app.web_search_hotkey = "ctrl+alt+s"
        registrations = []

        def register(spec, callback):
            handle = object()
            registrations.append((spec, callback, handle))
            return handle

        with patch.object(bindings, "register_hotkey", side_effect=register), \
                patch.object(bindings, "unregister", return_value=True):
            app._register_search()
            find_handle = app._hk_search
            app._register_web_search()

        self.assertIs(app._hk_search, find_handle)
        self.assertIsNot(app._hk_search, app._hk_web_search)
        self.assertEqual(
            [(spec, callback.__name__) for spec, callback, _handle in registrations],
            [
                ("ctrl+alt+f", "on_search_hotkey"),
                ("ctrl+alt+s", "on_web_search_hotkey"),
            ],
        )

    def test_home_and_settings_show_five_distinct_global_commands(self):
        app_root = Path(__file__).resolve().parent
        html = (app_root / "webui" / "index.html").read_text(encoding="utf-8")
        script = (app_root / "webui" / "app.js").read_text(encoding="utf-8")
        home = html.split('data-view="home"', 1)[1].split(
            'data-view="history"', 1
        )[0]
        for label in (
            "Dictate", "Paste latest", "Open Deck", "Mumble Find", "Web Search",
        ):
            with self.subTest(label=label):
                self.assertIn(label, home)
        nav = __import__("re").findall(
            r'class="nav-btn[^\"]*"[^>]*data-nav="([^"]+)"', html
        )
        self.assertEqual(
            nav, ["home", "history", "stats", "meetings", "reader", "settings"]
        )
        self.assertIn('data-capture="search_hotkey"', html)
        self.assertIn('data-capture="web_search_hotkey"', html)
        self.assertIn('setText("#hk-web-search", hk.web_search_hotkey)', script)
        self.assertNotIn(">Finder<", html)

    def test_maintained_ports_and_windows_fallback_expose_the_same_safe_command(self):
        app_root = Path(__file__).resolve().parent
        for platform in ("Linux", "macOS"):
            with self.subTest(platform=platform):
                port = app_root / "Ports" / platform / "app"
                controller_name = (
                    "mumble_linux.py" if platform == "Linux" else "mumble_mac.py"
                )
                controller = (port / controller_name).read_text(encoding="utf-8")
                shell = (port / "webui_shell.py").read_text(encoding="utf-8")
                script = (port / "webui" / "app.js").read_text(encoding="utf-8")
                html = (port / "webui" / "index.html").read_text(encoding="utf-8")
                self.assertIn("def request_web_search", controller)
                self.assertIn("def confirm_web_search", controller)
                self.assertIn('"cmd": "web_search_consent"', controller)
                self.assertNotIn("self._open_search(out)", controller)
                self.assertIn("def request_web_search", shell)
                self.assertIn("window.pyWebSearchConsent", script)
                self.assertIn('call("request_web_search"', script)
                expected_labels = [
                    "Dictate", "Paste latest", "Open Deck", "Web Search",
                ]
                if platform == "Linux":
                    expected_labels.append("Mumble Find")
                for label in expected_labels:
                    self.assertIn(label, html)

        linux_settings = (
            app_root / "Ports" / "Linux" / "app" / "settings.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"search_hotkey": "ctrl+alt+f"', linux_settings)
        self.assertIn('"web_search_hotkey": "ctrl+alt+s"', linux_settings)
        mac_bindings = (
            app_root / "Ports" / "macOS" / "app" / "bindings.py"
        ).read_text(encoding="utf-8")
        self.assertIn("physical:below-escape", mac_bindings)

        fallback = (app_root / "app_window.py").read_text(encoding="utf-8")
        self.assertIn("apply_web_search_hotkey", fallback)
        self.assertIn("Web Search hotkey", fallback)

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
            "operation_id": "4" * 32,
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

        def toggle_search(operation_id):
            toggle_calls.append(operation_id)
            return {
                "ok": True, "state": "visible", "changed": False,
                "operation_id": operation_id, "message": "",
            }

        with patch.object(webui_shell, "_webui_token_ok", return_value=True):
            webui_shell._serve_webui_commands(
                Server(), {"main": None, "main_min": False},
                ensure_main, ensure_search, "Mumble", toggle_search,
            )
            self.assertTrue(delivered.wait(1.0))

        reply = json.loads(replies[0].decode("utf-8"))
        self.assertEqual(reply, {
            "ok": True, "message": "", "operation_id": "4" * 32,
            "state": "visible", "changed": False,
        })
        self.assertEqual(toggle_calls, ["4" * 32])
        self.assertEqual(show_calls, [])
        self.assertEqual(main_calls, [])


class WebSearchPrivacyCommandTests(unittest.TestCase):
    def test_selected_words_wait_for_explicit_consent_before_browser_egress(self):
        import mumble

        app = mumble.Mumble.__new__(mumble.Mumble)
        app.settings = {
            "search_engine": "brave",
            "browser": "default",
        }
        app._web_search_lock = threading.RLock()
        app._pending_web_searches = {}
        delivered = []
        app._send_webui = lambda message, timeout=0.8: delivered.append(message) or True

        with patch.object(app, "open_in_browser", return_value=True) as browser:
            prepared = app.request_web_search("selected private words")
            browser.assert_not_called()
            self.assertTrue(prepared["ok"])
            self.assertEqual(delivered[-1]["cmd"], "web_search_consent")
            self.assertIn("sent to Brave", delivered[-1]["privacy"])
            self.assertNotIn("http", delivered[-1]["privacy"])

            confirmed = app.confirm_web_search(prepared["request_id"])
            self.assertTrue(confirmed["ok"])
            browser.assert_called_once_with(
                "https://search.brave.com/search?q=selected%20private%20words"
            )

            repeated = app.confirm_web_search(prepared["request_id"])
            self.assertFalse(repeated["ok"])
            self.assertEqual(browser.call_count, 1)

    def test_shell_shows_the_local_privacy_confirmation_before_egress(self):
        import webui_shell

        delivered = threading.Event()
        replies = []
        scripts = []
        request = json.dumps({
            "cmd": "web_search_consent",
            "token": "test-token",
            "request_id": "5" * 32,
            "provider": "Perplexity",
            "query": "selected private words",
            "privacy": (
                "These selected words will be sent to Perplexity over the "
                "internet only after you choose Search online. Mumble Find "
                "stays private on this device."
            ),
        }).encode("utf-8") + b"\n"

        class Window:
            def evaluate_js(self, script):
                scripts.append(script)

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

        window = Window()
        with patch.object(webui_shell, "_webui_token_ok", return_value=True):
            webui_shell._serve_webui_commands(
                Server(), {"main": window, "main_min": False},
                lambda activate=False: window, lambda: object(), "Mumble",
            )
            self.assertTrue(delivered.wait(1.0))

        self.assertTrue(json.loads(replies[0])["ok"])
        self.assertEqual(len(scripts), 1)
        self.assertIn("window.pyWebSearchConsent", scripts[0])
        self.assertIn("selected private words", scripts[0])
        self.assertIn("Mumble Find", scripts[0])

    def test_every_configured_provider_route_waits_for_the_same_consent(self):
        import mumble

        expected = {
            "google": "https://www.google.com/search?q=provider%20test",
            "perplexity": "https://www.perplexity.ai/search?q=provider%20test",
            "brave": "https://search.brave.com/search?q=provider%20test",
        }
        for provider, url in expected.items():
            with self.subTest(provider=provider):
                app = mumble.Mumble.__new__(mumble.Mumble)
                app.settings = {"search_engine": provider}
                app._web_search_lock = threading.RLock()
                app._pending_web_searches = {}
                app._send_webui = lambda _message, timeout=0.8: True
                with patch.object(
                    app, "open_in_browser", return_value=True
                ) as browser:
                    prepared = app.request_web_search("provider test")
                    browser.assert_not_called()
                    result = app.confirm_web_search(prepared["request_id"])
                    self.assertTrue(result["ok"])
                    browser.assert_called_once_with(url)

    def test_failed_consent_surface_discards_the_unsent_request(self):
        import mumble

        app = mumble.Mumble.__new__(mumble.Mumble)
        app.settings = {"search_engine": "perplexity"}
        app._web_search_lock = threading.RLock()
        app._pending_web_searches = {}
        app._send_webui = lambda _message, timeout=0.8: False
        prepared = app.request_web_search("must stay local")
        self.assertFalse(prepared["ok"])
        self.assertEqual(app._pending_web_searches, {})

    def test_browser_false_result_is_reported_truthfully_on_every_platform(self):
        app_root = Path(__file__).resolve().parent
        controllers = (
            app_root / "mumble.py",
            app_root / "Ports" / "Linux" / "app" / "mumble_linux.py",
            app_root / "Ports" / "macOS" / "app" / "mumble_mac.py",
        )
        for controller_path in controllers:
            with self.subTest(controller=controller_path.name):
                tree = ast.parse(controller_path.read_text(encoding="utf-8"))
                owner = next(
                    node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == "Mumble"
                )
                method = next(
                    node for node in owner.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "confirm_web_search"
                )
                namespace = {"time": time}
                exec(
                    compile(
                        ast.fix_missing_locations(
                            ast.Module(body=[method], type_ignores=[])
                        ),
                        controller_path.name,
                        "exec",
                    ),
                    namespace,
                )

                class Dummy:
                    SEARCH_ENGINES = {
                        "brave": "https://search.brave.com/search?q={q}"
                    }

                app = Dummy()
                app._web_search_lock = threading.RLock()
                app._pending_web_searches = {
                    "request": {
                        "engine": "brave",
                        "query": "private words",
                        "created": time.monotonic(),
                    }
                }
                app.open_in_browser = lambda _url: False
                result = namespace["confirm_web_search"](app, "request")

                self.assertFalse(result["ok"])
                self.assertIn("could not open", result["message"].lower())
                self.assertEqual(app._pending_web_searches, {})


class CoreCurrentTruthTests(unittest.TestCase):
    def test_opening_core_truth_names_convergence_candidates_and_correction(self):
        core = Path(__file__).resolve().parents[2] / "Development Files" / "Core"
        status = (core / "STATUS.html").read_text(encoding="utf-8")
        readme = (core / "README.html").read_text(encoding="utf-8")
        current_truth = status.split(
            '<div class="plain"><div class="tag">Current truth</div>', 1
        )[1].split("</p>", 1)[0]
        web_guidance = readme.split(
            "<strong>Current Web Search boundary:</strong>", 1
        )[1].split("</p>", 1)[0]
        exact_refs = (
            "52b06b8ee98ba8ef3b2029347a14eae818b8ac70",
            "8e93c8139ab1a5e4bd3e84811fcccc4a2ae6d1b6",
            "e872cfdf6ace7be3cb60343a305904ca05ed52e9",
            "3d04e85d361446da58296a90aae508bb0185bf97",
        )
        for exact_ref in exact_refs:
            self.assertIn(exact_ref, current_truth)
            self.assertIn(exact_ref, web_guidance)
        self.assertIn("correction", current_truth.lower())
        self.assertIn("correction", web_guidance.lower())

        issue19_row = status.split(
            'href="https://github.com/mongre25-droid/mumble/issues/19"', 1
        )[1].split("</tr>", 1)[0]
        issue19_truth = issue19_row.lower()
        self.assertNotIn("all-gates adoption", issue19_truth)
        self.assertIn("canonically approved run receipt", issue19_truth)
        self.assertIn("eight opened gate-specific records", issue19_truth)
        self.assertIn("same bytes", issue19_truth)
        self.assertIn(
            "manual/owner evidence cannot grant automated eligibility",
            issue19_truth,
        )
        self.assertIn("approval registry is empty", issue19_truth)
        self.assertIn("all nine current candidates remain non-eligible", issue19_truth)
        self.assertIn("both baselines are retained", issue19_truth)


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

    def test_failed_port_rebind_keeps_live_chord_truth_for_next_rebind(self):
        app_root = Path(__file__).resolve().parent
        controllers = (
            app_root / "Ports" / "Linux" / "app" / "mumble_linux.py",
            app_root / "Ports" / "macOS" / "app" / "mumble_mac.py",
        )
        for controller_path in controllers:
            with self.subTest(controller=controller_path.name):
                tree = ast.parse(controller_path.read_text(encoding="utf-8"))
                owner = next(
                    node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == "Mumble"
                )
                required = {
                    "_apply_settings_change",
                    "_active_binding_conflict",
                    "_sane_press_hotkey",
                    "_register_hotkey",
                    "_register_web_search",
                }
                methods = {
                    node.name: node
                    for node in owner.body
                    if isinstance(node, ast.FunctionDef) and node.name in required
                }
                self.assertEqual(set(methods), required)
                namespace = {"bindings": bindings}
                exec(
                    compile(
                        ast.fix_missing_locations(
                            ast.Module(body=list(methods.values()), type_ignores=[])
                        ),
                        controller_path.name,
                        "exec",
                    ),
                    namespace,
                )

                class LiveSettings:
                    def __init__(self):
                        self.values = {
                            "hotkey": "ctrl+shift+f7",
                            "web_search_hotkey": "ctrl+shift+f8",
                        }

                    def load(self):
                        pass

                    def get(self, key, default=None):
                        return self.values.get(key, default)

                    def set(self, key, value):
                        self.values[key] = value
                        return True

                class Dummy:
                    _PRESS_BINDING_LABELS = {
                        "hotkey": "Dictate",
                        "quick_paste_hotkey": "Paste latest",
                        "history_hotkey": "Open Deck",
                        "search_hotkey": "Mumble Find",
                        "web_search_hotkey": "Web Search",
                    }

                app = Dummy()
                app.settings = LiveSettings()
                app.hotkey = "ctrl+shift+f7"
                app.quick_hotkey = "ctrl+shift+f9"
                app.history_hotkey = "ctrl+shift+f10"
                app.search_hotkey = "ctrl+shift+f11"
                app.web_search_hotkey = "ctrl+shift+f8"
                app._hk_main = "dictate-old"
                app._hk_quick = None
                app._hk_history = None
                app._hk_search = None
                app._hk_web_search = "web-old"
                app.on_hotkey = lambda: None
                app.on_web_search_hotkey = lambda: None
                app._notify = lambda *_args: None
                for name in required:
                    setattr(app, name, types.MethodType(namespace[name], app))

                with patch.object(bindings, "register_hotkey") as register, \
                        patch.object(bindings, "unregister", return_value=True):
                    app.settings.values["web_search_hotkey"] = "ctrl+shift+f7"
                    app._apply_settings_change("web_search_hotkey")

                    app.settings.values["hotkey"] = "ctrl+shift+f8"
                    app._apply_settings_change("hotkey")

                self.assertEqual(app.web_search_hotkey, "ctrl+shift+f8")
                self.assertEqual(app.hotkey, "ctrl+shift+f7")
                register.assert_not_called()

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

    def test_maintained_port_bridges_never_persist_before_live_rebind(self):
        app_root = Path(__file__).resolve().parent
        probe = r'''
import json
import webui_shell

class MemorySettings:
    def __init__(self):
        self.values = {
            "hotkey": "ctrl+alt+d",
            "quick_paste_hotkey": "ctrl+alt+v",
            "history_hotkey": "ctrl+alt+h",
            "search_hotkey": "ctrl+alt+f",
            "web_search_hotkey": "ctrl+alt+s",
        }
        self.writes = []
    def load(self):
        return None
    def get(self, key, default=None):
        return self.values.get(key, default)
    def set(self, key, value):
        self.writes.append((key, value))
        self.values[key] = value
        return True

api = webui_shell.Api.__new__(webui_shell.Api)
api.settings = MemorySettings()
calls = []
def controller(request, timeout=None):
    calls.append((request, timeout))
    return {"ok": False, "applied": False, "message": "occupied"}
webui_shell._ctrl_send = controller
result = api.set_setting("web_search_hotkey", "ctrl+shift+f9")
first = {"result": result, "calls": list(calls), "writes": list(api.settings.writes)}
calls.clear()
api.settings.writes.clear()
collision = api.set_setting("web_search_hotkey", "alt+ctrl+h")
print(json.dumps({
    "first": first,
    "collision": collision,
    "collision_calls": calls,
    "collision_writes": api.settings.writes,
}))
'''
        for platform in ("Linux", "macOS"):
            with self.subTest(platform=platform):
                port = app_root / "Ports" / platform / "app"
                completed = subprocess.run(
                    [sys.executable, "-c", probe], cwd=port,
                    text=True, capture_output=True, check=True,
                )
                observed = json.loads(completed.stdout.strip().splitlines()[-1])
                first = observed["first"]
                self.assertEqual(first["calls"][0][0]["cmd"], "rebind")
                self.assertEqual(first["writes"], [])
                self.assertFalse(first["result"]["ok"])
                self.assertIn("occupied", first["result"]["message"])
                self.assertFalse(observed["collision"]["ok"])
                self.assertIn("Open Deck", observed["collision"]["message"])
                self.assertEqual(observed["collision_calls"], [])
                self.assertEqual(observed["collision_writes"], [])

    def test_maintained_port_controllers_swap_bindings_transactionally(self):
        app_root = Path(__file__).resolve().parent

        class MemorySettings:
            def __init__(self, events):
                self.events = events
                self.fail_next = False
                self.values = {
                    "hotkey": "ctrl+alt+d",
                    "quick_paste_hotkey": "ctrl+alt+v",
                    "history_hotkey": "ctrl+alt+h",
                    "search_hotkey": "ctrl+alt+f",
                    "web_search_hotkey": "ctrl+alt+s",
                }

            def get(self, key, default=None):
                return self.values.get(key, default)

            def set(self, key, value):
                self.events.append(("save", key, value))
                self.values[key] = value
                if self.fail_next:
                    self.fail_next = False
                    return False
                return True

        for platform, controller_name in (
            ("Linux", "mumble_linux.py"), ("macOS", "mumble_mac.py"),
        ):
            with self.subTest(platform=platform):
                source = (
                    app_root / "Ports" / platform / "app" / controller_name
                ).read_text(encoding="utf-8")
                tree = ast.parse(source)
                owner = next(
                    node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == "Mumble"
                )
                method = next(
                    node for node in owner.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "_apply_press_binding"
                )
                method = ast.fix_missing_locations(method)
                events = []

                class FakeBindings:
                    @staticmethod
                    def normalize(value):
                        return str(value or "").strip().lower()

                    @staticmethod
                    def validate(_value):
                        return True, ""

                    @staticmethod
                    def pretty(value):
                        return value

                    @staticmethod
                    def conflicts(first, second):
                        def chord(value):
                            aliases = {
                                "control": "ctrl", "option": "alt",
                                "command": "windows", "cmd": "windows",
                                "win": "windows",
                            }
                            return frozenset(
                                aliases.get(part.strip(), part.strip())
                                for part in str(value).lower().split("+")
                                if part.strip()
                            )
                        left, right = chord(first), chord(second)
                        return bool(
                            left and right and (
                                left.issubset(right) or right.issubset(left)
                            )
                        )

                    @staticmethod
                    def register_hotkey(value, _callback):
                        events.append(("register", value))
                        return "new-handle"

                    @staticmethod
                    def unregister(handle):
                        events.append(("unregister", handle))
                        return True

                namespace = {"bindings": FakeBindings, "threading": threading}
                exec(compile(ast.Module(body=[method], type_ignores=[]),
                             controller_name, "exec"), namespace)

                class Dummy:
                    pass

                app = Dummy()
                app.settings = MemorySettings(events)
                app.hotkey = "ctrl+alt+d"
                app.quick_hotkey = "ctrl+alt+v"
                app.history_hotkey = "ctrl+alt+h"
                app.search_hotkey = "ctrl+alt+f"
                app.web_search_hotkey = "ctrl+alt+s"
                app._hk_web_search = "old-handle"
                transactional = namespace["_apply_press_binding"]
                ok, _message = transactional(
                    app, "web_search_hotkey", "ctrl+shift+f9",
                    "web_search_hotkey", "_hk_web_search", lambda: None,
                    "Saved {binding}",
                )
                self.assertTrue(ok)
                self.assertEqual(events, [
                    ("register", "ctrl+shift+f9"),
                    ("save", "web_search_hotkey", "ctrl+shift+f9"),
                    ("unregister", "old-handle"),
                ])
                self.assertEqual(app._hk_web_search, "new-handle")

                events.clear()
                ok, message = transactional(
                    app, "web_search_hotkey", "ctrl+alt+h",
                    "web_search_hotkey", "_hk_web_search", lambda: None,
                    "Saved {binding}",
                )
                self.assertFalse(ok)
                self.assertIn("Open Deck", message)
                self.assertEqual(events, [])
                self.assertEqual(app._hk_web_search, "new-handle")

                def occupied(value, _callback):
                    events.append(("register", value))
                    raise OSError("occupied")

                FakeBindings.register_hotkey = staticmethod(occupied)
                ok, message = transactional(
                    app, "web_search_hotkey", "ctrl+shift+f10",
                    "web_search_hotkey", "_hk_web_search", lambda: None,
                    "Saved {binding}",
                )
                self.assertFalse(ok)
                self.assertIn("previous binding", message)
                self.assertEqual(events, [("register", "ctrl+shift+f10")])
                self.assertEqual(app._hk_web_search, "new-handle")

                events.clear()
                FakeBindings.register_hotkey = staticmethod(
                    lambda value, _callback: events.append(
                        ("register", value)
                    ) or "should-not-register"
                )
                ok, message = transactional(
                    app, "web_search_hotkey", "alt+ctrl+h",
                    "web_search_hotkey", "_hk_web_search", lambda: None,
                    "Saved {binding}",
                )
                self.assertFalse(ok)
                self.assertIn("Open Deck", message)
                self.assertEqual(events, [])

                events.clear()
                app.settings.fail_next = True
                previous = app.settings.get("web_search_hotkey")
                ok, message = transactional(
                    app, "web_search_hotkey", "ctrl+shift+f11",
                    "web_search_hotkey", "_hk_web_search", lambda: None,
                    "Saved {binding}",
                )
                self.assertFalse(ok)
                self.assertIn("previous shortcut", message)
                self.assertEqual(
                    app.settings.get("web_search_hotkey"), previous
                )
                self.assertEqual(app._hk_web_search, "new-handle")


if __name__ == "__main__":
    unittest.main()
