"""Regression proofs for the rejected Issue #27 Linux parity candidate."""

from types import SimpleNamespace
import sys
import threading
import time
from unittest import mock

import numpy as np

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
