#!/usr/bin/env python3
"""Offline regression checks for Linux-only lifecycle and overlay seams."""

import inspect
import json
import os
import subprocess
import sys
import tempfile

import autostart
import clipboard
import island_render
import overlay_linux
import run_tests
import update
from PIL import Image


def test_autostart_uses_installed_launcher_and_quotes_spaces():
    old = autostart._INSTALL_LAUNCHER
    try:
        with tempfile.TemporaryDirectory(prefix="Mumble path with spaces ") as td:
            launcher = os.path.join(td, "mumble")
            with open(launcher, "w", encoding="utf-8") as handle:
                handle.write("#!/usr/bin/env bash\n")
            os.chmod(launcher, 0o755)
            autostart._INSTALL_LAUNCHER = launcher
            entry = autostart._build_desktop_entry()
            assert autostart._launch_argv() == [launcher]
            assert f"Exec={autostart._desktop_quote(launcher)}" in entry
    finally:
        autostart._INSTALL_LAUNCHER = old


def test_island_contract_supports_search_and_controller_bar_shape():
    assert len(inspect.signature(
        overlay_linux._GtkIsland.set_bar_state).parameters) == 5
    assert island_render._anim_span("search") == \
        island_render._anim_span("listening")
    snap = {
        "state": "search", "frame": 2, "level": 0.5,
        "label": "Search", "timer": "0:01", "dot": "#5AA9E6",
        "rim": "#334455", "label_color": "#C5DFF5",
    }
    assert island_render.render(snap).size == (
        island_render.WIN_W, island_render.WIN_H)


def test_noop_island_keeps_bar_api_in_headless_runs():
    island = overlay_linux._NoopIsland(None)
    selected = []
    island.set_widget_callbacks(on_mode=selected.append)
    island.set_bar_state([("prompt", "Prompt")], "prompt", False, True)
    island._widget_mode("prompt")
    assert island.bar_state["active"] == "prompt"
    assert island.armed is True
    assert selected == ["prompt"]


def test_gtk_bar_hit_testing_uses_full_canvas_coordinates():
    island = object.__new__(overlay_linux._GtkIsland)
    island.bar_state = {
        "modes": [("prompt", "Prompt"), ("email", "Email")],
        "active": "prompt", "foreign_on": False,
        "show_foreign": False, "expanded": True,
    }
    selected = []
    island.on_mode = selected.append
    island.on_deck = None
    island.on_foreign = None
    layout = island_render.bar_layout(island.bar_state)
    pill_x0, pill_x1 = layout["pill"]
    island._bar_hitbox = (
        pill_x0, island_render.BAR_WIN_H - island_render.BAR_H,
        pill_x1 - pill_x0, island_render.BAR_H,
    )
    email = next(row for row in layout["chips"] if row[0] == "email")
    event = type("Event", (), {
        "x": (email[1] + email[2]) / 2,
        "y": island_render.BAR_WIN_H - island_render.BAR_H / 2,
    })()
    assert island._on_button_press(None, event) is True
    assert selected == ["email"]


def test_collapsed_active_mode_chip_expands_before_selecting():
    island = object.__new__(overlay_linux._GtkIsland)
    island.bar_state = {
        "modes": [("prompt", "Prompt"), ("email", "Email")],
        "active": "prompt", "foreign_on": False,
        "show_foreign": False, "expanded": False,
    }
    selected = []
    island.on_mode = selected.append
    island.on_deck = None
    island.on_foreign = None
    island.wake = lambda: None
    layout = island_render.bar_layout(island.bar_state)
    pill_x0, pill_x1 = layout["pill"]
    island._bar_hitbox = (
        pill_x0, island_render.BAR_WIN_H - island_render.BAR_H,
        pill_x1 - pill_x0, island_render.BAR_H,
    )
    chip = layout["chips"][0]
    event = type("Event", (), {
        "x": (chip[1] + chip[2]) / 2,
        "y": island_render.BAR_WIN_H - island_render.BAR_H / 2,
    })()
    assert island._on_button_press(None, event) is True
    assert island.bar_state["expanded"] is True
    assert selected == []


def test_plain_dictation_paints_mode_bar_and_recentres_on_resize():
    class FakeWindow:
        def set_size_request(self, *_args):
            pass

        def resize(self, *_args):
            pass

        def queue_draw(self):
            pass

    island = object.__new__(overlay_linux._GtkIsland)
    island.state = "listening"
    island._ok = True
    island.frame = 0
    island.visible = True
    island.win = FakeWindow()
    island._cur_img = Image.new("RGBA", (10, 10))
    island.bar_state = {
        "modes": [("prompt", "Prompt"), ("email", "Email")],
        "active": None, "foreign_on": False,
        "show_foreign": False, "expanded": False,
    }
    island._build_snapshot = lambda: {}
    island._apply_click_through = lambda: None
    placements = []
    island._place = lambda: placements.append(True)

    old_render = island_render.render
    old_render_bar = island_render.render_bar
    try:
        island_render.render = lambda _snap: Image.new("RGBA", (80, 20))
        island_render.render_bar = lambda _snap: Image.new("RGBA", (120, 24))
        island._paint_frame()
    finally:
        island_render.render = old_render
        island_render.render_bar = old_render_bar

    assert island._bar_hitbox is not None
    assert island._cur_img.size == (120, 46)
    assert placements == [True]


def test_clipboard_never_adopts_or_deletes_an_unmanaged_image():
    with tempfile.TemporaryDirectory() as td:
        managed = os.path.join(td, "clip_images")
        os.makedirs(managed)
        outside = os.path.join(td, "outside.png")
        Image.new("RGB", (2, 2), "red").save(outside)
        db = os.path.join(td, "clipboard.json")
        with open(db, "w", encoding="utf-8") as handle:
            json.dump([{"type": "image", "path": outside}], handle)
        store = clipboard.Clipboard(db, img_dir=managed)
        assert store.recent() == []
        store.clear()
        assert os.path.exists(outside)


def test_missing_managed_clipboard_image_is_already_deleted():
    with tempfile.TemporaryDirectory() as td:
        managed = os.path.join(td, "clip_images")
        os.makedirs(managed)
        store = clipboard.Clipboard(
            os.path.join(td, "clipboard.json"), img_dir=managed)
        missing = os.path.join(managed, "already-gone.png")
        assert store._delete_image_file(missing) is True


def test_clipboard_monitor_restart_invalidates_old_generation():
    threads = []

    class FakeThread:
        def __init__(self, *args, **kwargs):
            self.args = kwargs.get("args", ())
            threads.append(self)

        def start(self):
            pass

    old_thread = clipboard.threading.Thread
    old_paste = clipboard.pyperclip.paste
    clipboard.threading.Thread = FakeThread
    clipboard.pyperclip.paste = lambda: ""
    try:
        with tempfile.TemporaryDirectory() as td:
            store = clipboard.Clipboard(os.path.join(td, "clipboard.json"))
            store.start()
            first = store._stop_event
            store.stop()
            store.start()
            second = store._stop_event
            assert first is not second
            assert first.is_set() and not second.is_set()
            assert threads[0].args == (first,)
            assert threads[1].args == (second,)
    finally:
        clipboard.threading.Thread = old_thread
        clipboard.pyperclip.paste = old_paste


def test_controller_source_has_runtime_veto_and_cleanup_contracts():
    path = os.path.join(os.path.dirname(__file__), "mumble_linux.py")
    source = open(path, encoding="utf-8").read()
    assert 'getattr(self, "meeting_recording", False)' in source
    assert 'rec_state = ("search"' in source
    assert 'self.settings.get("resource_saver") or self._mode_active' in source
    assert "on_mode=self.set_active_mode" in source
    assert "self._stream_done.is_set()" in source


def test_installer_and_uninstaller_lifecycle_contract():
    root = os.path.dirname(os.path.dirname(__file__))
    install = open(os.path.join(root, "install.sh"), encoding="utf-8").read()
    uninstall_path = os.path.join(root, "uninstall.sh")
    uninstall = open(uninstall_path, encoding="utf-8").read()
    assert uninstall.startswith("#!/usr/bin/env bash\n")
    assert 'autostart.enable()' in install
    assert 'chmod +x "$ROOT/uninstall.sh"' in install
    assert 'mumble-uninstall.desktop' in install
    assert 'autostart/mumble-voice-to-text.desktop' in uninstall
    assert 'LEGACY_AUTOSTART=' in uninstall
    assert 'applications/mumble.desktop' in uninstall
    assert '--purge-data' in uninstall
    assert 'readlink -m -- "$XDG_DATA"' in uninstall
    assert '[ "$XDG_DATA_CANON" = "/" ]' in uninstall
    assert '[ -L "$DATA_DIR" ]' in uninstall


def test_runner_executes_pytest_modules_instead_of_only_importing_them():
    with tempfile.TemporaryDirectory() as td:
        pytest_file = os.path.join(td, "test_pytest_style.py")
        class_file = os.path.join(td, "test_class_style.py")
        script_file = os.path.join(td, "test_script_style.py")
        with open(pytest_file, "w", encoding="utf-8") as handle:
            handle.write("def test_real_check():\n    assert True\n")
        with open(script_file, "w", encoding="utf-8") as handle:
            handle.write(
                "def test_real_check():\n    assert True\n\n"
                "if __name__ == '__main__':\n    test_real_check()\n"
            )
        with open(class_file, "w", encoding="utf-8") as handle:
            handle.write(
                "class TestOnlyClass:\n"
                "    def test_real_check(self):\n        assert True\n"
            )

        pytest_cmd = run_tests._test_command(td, os.path.basename(pytest_file))
        class_cmd = run_tests._test_command(td, os.path.basename(class_file))
        script_cmd = run_tests._test_command(td, os.path.basename(script_file))
        assert pytest_cmd[1:4] == ["-m", "pytest", "-q"]
        assert class_cmd[1:4] == ["-m", "pytest", "-q"]
        assert script_cmd == [run_tests.sys.executable, script_file]


def test_runner_creates_nested_json_report_directory():
    with tempfile.TemporaryDirectory() as td:
        report = os.path.join(td, "nested", "reports", "linux.json")
        result = subprocess.run(
            [sys.executable, run_tests.__file__, "--pattern",
             "definitely-no-such-test", "--json", report],
            check=False,
        )
        assert result.returncode == 0
        with open(report, encoding="utf-8") as handle:
            payload = json.load(handle)
        assert payload["total"] == 0


def test_linux_updater_rejects_paths_outside_declared_parent():
    with tempfile.TemporaryDirectory() as parent, tempfile.TemporaryDirectory() as outside:
        current = os.path.join(parent, "current")
        payload = os.path.join(outside, "payload")
        os.makedirs(current)
        os.makedirs(payload)
        try:
            update._write_linux_swap_script(
                parent, payload, current, os.getpid())
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe updater paths were accepted")


def main():
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  [ok] {test.__name__}")
    print(f"Linux parity regressions: {len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
