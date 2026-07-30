"""Focused conformance for Linux adapters behind shared insertion.py."""

import subprocess

from insertion import InsertionModule, InsertionOutcome, TextPayload
from linux_desktop import DesktopSession
from linux_insertion import (
    LinuxClipboardAdapter, LinuxNativeInputAdapter, LinuxTargetAdapter,
)


def _which(*available):
    available = set(available)
    return lambda name: f"/usr/bin/{name}" if name in available else None


class FakeClipboardRunner:
    def __init__(self, value=b"before"):
        self.value = value
        self.sends = []

    def __call__(self, argv, **kwargs):
        if "-o" in argv:
            return subprocess.CompletedProcess(argv, 0, self.value, b"")
        if "-t" in argv and "image/png" in argv and "-o" in argv:
            return subprocess.CompletedProcess(argv, 1, b"", b"")
        if "key" in argv:
            self.sends.append(argv)
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        self.value = kwargs.get("input_bytes") or b""
        return subprocess.CompletedProcess(argv, 0, b"", b"")


def test_wayland_has_no_fake_target_even_when_xwayland_exists():
    target = LinuxTargetAdapter(
        DesktopSession("wayland", "gnome", xwayland_available=True),
        which=_which("xdotool"))
    assert target.current() is None


def test_shared_module_returns_saved_only_on_wayland_without_target():
    runner = FakeClipboardRunner()
    session = DesktopSession("wayland", "gnome", xwayland_available=True)
    module = InsertionModule(
        LinuxTargetAdapter(session, which=_which("xdotool"), runner=runner),
        LinuxClipboardAdapter(session, which=_which("wl-copy", "wl-paste"),
                              runner=runner),
        LinuxNativeInputAdapter(session, which=_which("wtype"), runner=runner),
        settle_delay=lambda _seconds: None,
    )
    operation_id = "a" * 32
    module.begin(operation_id, "dictation")
    result = module.deliver(operation_id, TextPayload("private words"))
    assert result.outcome is InsertionOutcome.SAVED_ONLY
    assert result.send_count == 0
    assert runner.sends == []


def test_external_clipboard_change_prevents_restore():
    runner = FakeClipboardRunner()
    adapter = LinuxClipboardAdapter(
        DesktopSession("x11", "other"), which=_which("xclip"), runner=runner)
    snapshot = adapter.snapshot()
    request = type("Request", (), {
        "content_kind": "text", "text": "mumble", "image_path": ""})()
    ownership = adapter.write(request, snapshot)
    runner.value = b"newer external value"
    restored = adapter.restore(snapshot, ownership)
    assert restored.changed_externally
    assert runner.value == b"newer external value"


def test_operation_identity_polling_never_resends():
    runner = FakeClipboardRunner()
    session = DesktopSession("wayland", "gnome")
    module = InsertionModule(
        LinuxTargetAdapter(session, which=_which(), runner=runner),
        LinuxClipboardAdapter(session, which=_which("wl-copy", "wl-paste"),
                              runner=runner),
        LinuxNativeInputAdapter(session, which=_which("wtype"), runner=runner),
        settle_delay=lambda _seconds: None,
    )
    operation_id = "b" * 32
    module.begin(operation_id, "deck_history")
    first = module.deliver(operation_id, TextPayload("once"))
    second = module.deliver(operation_id, TextPayload("once"))
    assert first == second
    assert runner.sends == []
