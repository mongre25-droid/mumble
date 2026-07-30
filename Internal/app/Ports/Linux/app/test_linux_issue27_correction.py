"""Regression proofs for the rejected Issue #27 Linux parity candidate."""

from types import SimpleNamespace
import os
import sys
import threading
import time
from unittest import mock

import numpy as np
import pytest

import autostart
import linux_desktop
from linux_native_drag import LinuxNativeDragAdapter
from mumble_linux import Mumble


def test_mixed_timestamp_segments_preserve_every_word_once():
    mumble = Mumble.__new__(Mumble)
    mumble._dictation_inference_overlap_samples = 10
    results = iter([
        ("alpha draft", [
            {"word": "alpha", "start": 0.2, "end": 0.8},
            {"word": "draft", "start": 9.2, "end": 9.8},
        ]),
        ("draft beta", []),
        ("beta charlie gamma", [
            {"word": "beta", "start": 0.2, "end": 0.8},
            {"word": "charlie", "start": 0.7, "end": 0.9},
            {"word": "gamma", "start": 1.2, "end": 1.8},
        ]),
    ])
    mumble._durable_inference_audio = lambda *_args: np.zeros(100)
    mumble._transcribe = lambda *_args, **_kwargs: next(results)
    manifest = {
        "audio": {"sample_rate": 10},
        "segments": [{"sample_count": 100} for _ in range(3)],
    }

    transcript = mumble._decode_durable_session(
        SimpleNamespace(), manifest, local_only=False)

    assert transcript == "alpha draft beta charlie gamma"


def test_cloud_selected_multisegment_dictation_never_requires_local_model():
    mumble = Mumble.__new__(Mumble)
    mumble.model = None
    mumble._dictation_inference_overlap_samples = 10
    mumble._durable_transcription_snapshot = SimpleNamespace(
        route=SimpleNamespace(ready=True, cloud_augmented=True))
    cloud_results = iter(["alpha shared", "shared beta"])
    mumble._cloud_transcribe = lambda *_args: next(cloud_results)
    mumble._local_transcribe = mock.Mock(
        side_effect=AssertionError("selected cloud route was forced local"))
    mumble._durable_inference_audio = lambda *_args: np.zeros(100)
    manifest = {
        "audio": {"sample_rate": 10},
        "segments": [{"sample_count": 100} for _ in range(2)],
    }

    transcript = mumble._decode_durable_session(
        SimpleNamespace(), manifest, local_only=False)

    assert transcript == "alpha shared beta"
    mumble._local_transcribe.assert_not_called()

    mumble._cloud_transcribe = lambda *_args: ""
    assert mumble._transcribe(np.zeros(10), want_words=True) == ("", [])
    mumble._local_transcribe.assert_not_called()

    mumble._cloud_transcribe = lambda *_args: None
    mumble._local_transcribe = mock.Mock(return_value=("local fallback", []))
    assert mumble._transcribe(np.zeros(10), want_words=True) == (
        "local fallback", [])
    mumble._local_transcribe.assert_called_once()


def test_delayed_gtk_start_never_begins_drag_after_deadline(tmp_path):
    target = tmp_path / "report.txt"
    target.write_text("report", encoding="utf-8")
    drag_calls = []
    callback_done = threading.Event()

    class Window:
        @staticmethod
        def new(_kind):
            time.sleep(0.08)
            return Window()

        def __getattr__(self, _name):
            return lambda *_args, **_kwargs: None

        def get_window(self):
            return object()

    class Gtk:
        class WindowType:
            POPUP = 1

        class TargetEntry:
            new = staticmethod(lambda *_args: object())

        class TargetList:
            new = staticmethod(lambda targets: targets)

        drag_source_set = staticmethod(lambda *_args: None)

        @staticmethod
        def drag_begin_with_coordinates(*_args):
            drag_calls.append(time.monotonic())
            return object()

    Gtk.Window = Window

    class Pointer:
        def get_position(self):
            return None, 10, 10

    class Seat:
        def get_pointer(self):
            return Pointer()

    class Display:
        def get_default_seat(self):
            return Seat()

        get_default = staticmethod(lambda: Display())

    class Gdk:
        class ModifierType:
            BUTTON1_MASK = 1

        class DragAction:
            COPY = 1

        class EventType:
            BUTTON_PRESS = 1

        CURRENT_TIME = 0

        class Event:
            @staticmethod
            def new(_kind):
                return SimpleNamespace()

    Gdk.Display = Display

    class GLib:
        @staticmethod
        def idle_add(callback):
            def run():
                try:
                    callback()
                finally:
                    callback_done.set()

            threading.Thread(target=run, daemon=True).start()
            return 7

        source_remove = staticmethod(lambda _source: True)

    gi = SimpleNamespace(require_version=lambda *_args: None)
    repository = SimpleNamespace(Gdk=Gdk, GLib=GLib, Gtk=Gtk)
    with mock.patch.dict(sys.modules, {
            "gi": gi, "gi.repository": repository}):
        adapter = LinuxNativeDragAdapter(
            linux_desktop.DesktopSession("x11", "other"))
        result = adapter.start(str(target), timeout=0.02)
        assert callback_done.wait(0.5)

    assert result["status"] == "timeout"
    assert drag_calls == []


def test_missing_portal_and_unproven_autostart_are_not_reported_ready():
    wayland = linux_desktop.DesktopSession("wayland", "gnome")
    shortcut = linux_desktop.shortcut_capability(
        wayland, portal_probe=lambda: False)
    assert shortcut.status in {"degraded", "unknown"}
    assert shortcut.backend == "none"

    x11 = linux_desktop.DesktopSession("x11", "other")
    missing = linux_desktop.autostart_capability(
        x11, autostart_probe=lambda: ("degraded", "No usable route."))
    unknown = linux_desktop.autostart_capability(
        x11, autostart_probe=lambda: ("unknown", "Route was not proven."))
    assert missing.status == "degraded"
    assert unknown.status == "unknown"
    assert "route" in unknown.message.casefold()


def test_capability_probes_require_real_portal_and_autostart_routes(tmp_path):
    def portal_result(stdout, returncode=0):
        return lambda *_args, **_kwargs: SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr="")

    absent = linux_desktop._probe_global_shortcuts_portal(
        which=lambda _name: "/usr/bin/gdbus",
        runner=portal_result("interface org.freedesktop.portal.Settings {}"))
    present = linux_desktop._probe_global_shortcuts_portal(
        which=lambda _name: "/usr/bin/gdbus",
        runner=portal_result(
            "interface org.freedesktop.portal.GlobalShortcuts {}"))
    assert absent is False
    assert present is True

    missing = tmp_path / "missing"
    with mock.patch.object(autostart, "is_enabled", return_value=False), \
         mock.patch.object(autostart, "_MUMBLE_MAIN", str(missing / "main.py")), \
         mock.patch.object(autostart, "_INSTALL_LAUNCHER", str(missing / "mumble")), \
         mock.patch.object(autostart, "_VENV_PYTHON", str(missing / "python")):
        status, message = autostart.probe_route()
    assert status == "degraded"
    assert "launch route" in message.casefold()

    with mock.patch.object(autostart, "is_enabled", return_value=True), \
         mock.patch.object(autostart, "_MUMBLE_MAIN", str(missing / "main.py")), \
         mock.patch.object(autostart, "_INSTALL_LAUNCHER", str(missing / "mumble")), \
         mock.patch.object(autostart, "_VENV_PYTHON", str(missing / "python")):
        stale_status, _stale_message = autostart.probe_route()
    assert stale_status == "degraded"


def test_enabled_obsolete_exec_is_not_rescued_by_a_current_launcher(tmp_path):
    current_launcher = tmp_path / "current" / "mumble"
    current_launcher.parent.mkdir()
    current_launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    current_launcher.chmod(0o755)
    current_main = tmp_path / "current" / "app" / "mumble_linux.py"
    current_main.parent.mkdir()
    current_main.write_text("# current Mumble\n", encoding="utf-8")
    desktop = tmp_path / "autostart" / autostart.DESKTOP_NAME
    desktop.parent.mkdir()
    desktop.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Mumble\n"
        "Exec=/removed/old/mumble\n"
        "X-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )

    with mock.patch.object(autostart, "DESKTOP_PATH", str(desktop)), \
         mock.patch.object(autostart, "LEGACY_DESKTOP_PATH", str(
             tmp_path / "autostart" / "mumble.desktop")), \
         mock.patch.object(autostart, "_MUMBLE_MAIN", str(current_main)), \
         mock.patch.object(
             autostart, "_INSTALL_LAUNCHER", str(current_launcher)), \
         mock.patch.object(
             autostart, "_VENV_PYTHON", str(tmp_path / "missing-python")):
        status, message = autostart.probe_route()

    assert status in {"degraded", "unknown"}
    assert "exec" in message.casefold() or "target" in message.casefold()

    desktop.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Mumble\n"
        f'Exec="{current_launcher}"\n'
        "X-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )
    with mock.patch.object(autostart, "DESKTOP_PATH", str(desktop)), \
         mock.patch.object(autostart, "LEGACY_DESKTOP_PATH", str(
             tmp_path / "autostart" / "mumble.desktop")), \
         mock.patch.object(autostart, "_MUMBLE_MAIN", str(current_main)), \
         mock.patch.object(
             autostart, "_INSTALL_LAUNCHER", str(current_launcher)), \
         mock.patch.object(
             autostart, "_VENV_PYTHON", str(tmp_path / "missing-python")):
        ready_status, ready_message = autostart.probe_route()

    assert ready_status == "ready"
    assert "current mumble launch route" in ready_message.casefold()


def _probe_current_autostart_entry(tmp_path, content):
    current_launcher = tmp_path / "current" / "mumble"
    current_launcher.parent.mkdir(exist_ok=True)
    current_launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    current_launcher.chmod(0o755)
    current_main = tmp_path / "current" / "app" / "mumble_linux.py"
    current_main.parent.mkdir()
    current_main.write_text("# current Mumble\n", encoding="utf-8")
    other_launcher = tmp_path / "other" / "mumble"
    other_launcher.parent.mkdir()
    other_launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    other_launcher.chmod(0o755)
    desktop = tmp_path / "autostart" / autostart.DESKTOP_NAME
    desktop.parent.mkdir()
    desktop.write_text(content.format(
        launcher=current_launcher, other=other_launcher), encoding="utf-8")

    with mock.patch.object(autostart, "DESKTOP_PATH", str(desktop)), \
         mock.patch.object(autostart, "LEGACY_DESKTOP_PATH", str(
             tmp_path / "autostart" / "mumble.desktop")), \
         mock.patch.object(autostart, "_MUMBLE_MAIN", str(current_main)), \
         mock.patch.object(autostart, "_INSTALL_LAUNCHER", str(current_launcher)), \
         mock.patch.object(autostart, "_VENV_PYTHON", str(
             tmp_path / "missing-python")):
        return autostart.probe_route()


@pytest.mark.parametrize("first_exec", [
    '"{launcher}"',
    "/removed/old/mumble",
])
def test_duplicate_exec_entries_fail_closed(tmp_path, first_exec):
    status, _message = _probe_current_autostart_entry(
        tmp_path,
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Mumble\n"
        f"Exec={first_exec}\n"
        'Exec="{launcher}"\n'
        "X-GNOME-Autostart-enabled=true\n",
    )

    assert status in {"degraded", "unknown"}


@pytest.mark.parametrize("entry", [
    "[Desktop Entry]\nType=Application\nName=Mumble\n",
    "[Desktop Entry]\nType=Application\nExec=\"unterminated\n",
    '[Desktop Entry]\nType=Application\nExec="{other}"\n',
    "[Desktop Entry]\nType=Application\nExec=/removed/old/mumble\n",
    '[Desktop Entry]\nType=Application\nExec="{launcher}"\n'
    "TryExec=/removed/old/mumble\n",
    '[Desktop Entry]\nType=Application\nExec="{launcher}"\nHidden=true\n',
])
def test_invalid_startup_authority_remains_non_ready(tmp_path, entry):
    status, _message = _probe_current_autostart_entry(tmp_path, entry)

    assert status in {"degraded", "unknown"}


@pytest.mark.parametrize("duplicate_line", [
    "Type=Application",
    "Hidden=false",
    "X-GNOME-Autostart-enabled=true",
    "X-Mumble-VoiceToText=true",
])
def test_duplicate_authority_singleton_entries_fail_closed(
        tmp_path, duplicate_line):
    status, _message = _probe_current_autostart_entry(
        tmp_path,
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Mumble\n"
        'Exec="{launcher}"\n'
        "Hidden=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "X-Mumble-VoiceToText=true\n"
        f"{duplicate_line}\n",
    )

    assert status in {"degraded", "unknown"}


def test_probe_route_cannot_combine_two_desktop_entry_reads(tmp_path):
    current_launcher = tmp_path / "current" / "mumble"
    current_launcher.parent.mkdir()
    current_launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    current_launcher.chmod(0o755)
    current_main = tmp_path / "current" / "app" / "mumble_linux.py"
    current_main.parent.mkdir()
    current_main.write_text("# current Mumble\n", encoding="utf-8")
    desktop = tmp_path / "autostart" / autostart.DESKTOP_NAME
    desktop.parent.mkdir()
    stale = (
        "[Desktop Entry]\nType=Application\nName=Mumble\n"
        "Exec=/removed/old/mumble\nX-GNOME-Autostart-enabled=true\n")
    current = stale.replace(
        "/removed/old/mumble", f'"{current_launcher}"')
    desktop.write_text(stale, encoding="utf-8")
    desktop_reads = []
    real_open = open

    def changing_open(path, *args, **kwargs):
        if os.fspath(path) == str(desktop):
            desktop_reads.append(str(path))
            if len(desktop_reads) == 2:
                desktop.write_text(current, encoding="utf-8")
        return real_open(path, *args, **kwargs)

    with mock.patch.object(autostart, "DESKTOP_PATH", str(desktop)), \
         mock.patch.object(autostart, "LEGACY_DESKTOP_PATH", str(
             tmp_path / "autostart" / "mumble.desktop")), \
         mock.patch.object(autostart, "_MUMBLE_MAIN", str(current_main)), \
         mock.patch.object(autostart, "_INSTALL_LAUNCHER", str(current_launcher)), \
         mock.patch.object(autostart, "_VENV_PYTHON", str(
             tmp_path / "missing-python")), \
         mock.patch.object(autostart, "open", changing_open, create=True):
        status, _message = autostart.probe_route()

    assert status in {"degraded", "unknown"}
    assert desktop_reads == [str(desktop)]


def test_probe_route_rejects_content_from_a_replacement_file(tmp_path):
    current_launcher = tmp_path / "current" / "mumble"
    current_launcher.parent.mkdir()
    current_launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    current_launcher.chmod(0o755)
    current_main = tmp_path / "current" / "app" / "mumble_linux.py"
    current_main.parent.mkdir()
    current_main.write_text("# current Mumble\n", encoding="utf-8")
    desktop = tmp_path / "autostart" / autostart.DESKTOP_NAME
    desktop.parent.mkdir()
    desktop.write_text(
        "[Desktop Entry]\nType=Application\nExec=/removed/old/mumble\n",
        encoding="utf-8")
    replacement = tmp_path / "replacement.desktop"
    replacement.write_text(
        "[Desktop Entry]\nType=Application\n"
        f'Exec="{current_launcher}"\n', encoding="utf-8")
    real_open = open

    def substituted_open(path, *args, **kwargs):
        if os.fspath(path) == str(desktop):
            return real_open(replacement, *args, **kwargs)
        return real_open(path, *args, **kwargs)

    with mock.patch.object(autostart, "DESKTOP_PATH", str(desktop)), \
         mock.patch.object(autostart, "LEGACY_DESKTOP_PATH", str(
             tmp_path / "autostart" / "mumble.desktop")), \
         mock.patch.object(autostart, "_MUMBLE_MAIN", str(current_main)), \
         mock.patch.object(autostart, "_INSTALL_LAUNCHER", str(current_launcher)), \
         mock.patch.object(autostart, "_VENV_PYTHON", str(
             tmp_path / "missing-python")), \
         mock.patch.object(autostart, "open", substituted_open, create=True):
        status, _message = autostart.probe_route()

    assert status in {"degraded", "unknown"}


def test_probe_route_rejects_entry_changed_after_snapshot(tmp_path):
    current_launcher = tmp_path / "current" / "mumble"
    current_launcher.parent.mkdir()
    current_launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    current_launcher.chmod(0o755)
    current_main = tmp_path / "current" / "app" / "mumble_linux.py"
    current_main.parent.mkdir()
    current_main.write_text("# current Mumble\n", encoding="utf-8")
    desktop = tmp_path / "autostart" / autostart.DESKTOP_NAME
    desktop.parent.mkdir()
    desktop.write_text(
        "[Desktop Entry]\nType=Application\n"
        f'Exec="{current_launcher}"\n', encoding="utf-8")

    def mutate_after_match(_argv):
        desktop.write_text(
            "[Desktop Entry]\nType=Application\n"
            "Exec=/removed/old/mumble\n# changed-underfoot\n",
            encoding="utf-8",
        )
        return True

    with mock.patch.object(autostart, "DESKTOP_PATH", str(desktop)), \
         mock.patch.object(autostart, "LEGACY_DESKTOP_PATH", str(
             tmp_path / "autostart" / "mumble.desktop")), \
         mock.patch.object(autostart, "_MUMBLE_MAIN", str(current_main)), \
         mock.patch.object(autostart, "_INSTALL_LAUNCHER", str(current_launcher)), \
         mock.patch.object(autostart, "_VENV_PYTHON", str(
             tmp_path / "missing-python")), \
         mock.patch.object(
             autostart, "_exec_matches_current_route", mutate_after_match):
        status, _message = autostart.probe_route()

    assert status in {"degraded", "unknown"}
