"""Focused Issue #27 Linux desktop-contract tests.

These tests use injected environment/tool probes, so they are deterministic on
CI hosts and do not pretend to be physical GNOME, KDE, X11, or Wayland proof.
"""

import os
import subprocess
import threading
from unittest import mock

import linux_desktop
import island_render
import overlay_linux


def _which(*available):
    available = set(available)
    return lambda name: f"/usr/bin/{name}" if name in available else None


def test_session_detection_never_treats_xwayland_as_native_x11():
    session = linux_desktop.DesktopSession.detect({
        "XDG_SESSION_TYPE": "wayland",
        "WAYLAND_DISPLAY": "wayland-0",
        "DISPLAY": ":0",
        "XDG_CURRENT_DESKTOP": "GNOME",
    })
    assert session.session_type == "wayland"
    assert session.desktop == "gnome"
    assert session.xwayland_available is True


def test_gnome_local_search_is_bounded_and_cancellable():
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv, 0, stdout="file:///home/me/Report.md\n", stderr="")

    adapter = linux_desktop.LinuxSearchAdapter(
        linux_desktop.DesktopSession("wayland", "gnome"),
        which=_which("localsearch"), runner=run)
    result = adapter.search("report", limit=12, timeout=0.5,
                            cancel_event=threading.Event())
    assert result.status == "ready"
    assert result.provider == "gnome-localsearch"
    assert result.paths == ("/home/me/Report.md",)
    assert calls[0][0][:2] == ["/usr/bin/localsearch", "search"]
    assert calls[0][1]["timeout"] == 0.5


def test_active_desktop_index_process_is_terminated_on_cancel():
    created = threading.Event()
    processes = []

    class Process:
        returncode = -15

        def __init__(self, *_args, **_kwargs):
            self.terminated = False
            created.set()

        def poll(self):
            return self.returncode if self.terminated else None

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.terminated = True

        def wait(self, timeout=None):
            return self.returncode

        def communicate(self):
            return "", ""

    def factory(*args, **kwargs):
        process = Process(*args, **kwargs)
        processes.append(process)
        return process

    adapter = linux_desktop.LinuxSearchAdapter(
        linux_desktop.DesktopSession("wayland", "gnome"),
        which=_which("localsearch"), process_factory=factory)
    result = []
    worker = threading.Thread(
        target=lambda: result.append(adapter.search("report", timeout=2)))
    worker.start()
    assert created.wait(1)
    adapter.cancel()
    worker.join(1)
    assert result[0].status == "cancelled"
    assert processes[0].terminated is True


def test_kde_baloo_route_is_bounded():
    def run(argv, **_kwargs):
        assert argv == ["/usr/bin/baloosearch", "-l", "7", "notes"]
        return subprocess.CompletedProcess(argv, 0,
                                           stdout="/home/me/notes.txt\n",
                                           stderr="")

    adapter = linux_desktop.LinuxSearchAdapter(
        linux_desktop.DesktopSession("wayland", "kde"),
        which=_which("baloosearch"), runner=run)
    result = adapter.search("notes", limit=7)
    assert result.status == "ready"
    assert result.provider == "kde-baloo"


def test_plocate_is_truthfully_degraded_and_never_uses_a_shell():
    def run(argv, **kwargs):
        assert argv == ["/usr/bin/plocate", "--limit", "5", "draft"]
        assert kwargs["shell"] is False
        return subprocess.CompletedProcess(argv, 0,
                                           stdout="/home/me/draft.txt\n",
                                           stderr="")

    adapter = linux_desktop.LinuxSearchAdapter(
        linux_desktop.DesktopSession("x11", "other"),
        which=_which("plocate"), runner=run)
    result = adapter.search("draft", limit=5)
    assert result.status == "degraded"
    assert result.provider == "plocate"
    assert "stale" in result.message.lower()
    assert "filename" in result.message.lower()


def test_search_has_no_folder_walk_fallback():
    adapter = linux_desktop.LinuxSearchAdapter(
        linux_desktop.DesktopSession("wayland", "gnome"), which=_which())
    result = adapter.search("anything")
    assert result.status == "unsupported"
    assert result.paths == ()
    assert "index" in result.message.lower()


def test_wayland_shortcuts_prefer_portal_and_report_missing_route():
    session = linux_desktop.DesktopSession("wayland", "gnome")
    ready = linux_desktop.shortcut_capability(
        session, which=_which("gdbus"), readable_input=False,
        portal_available=True)
    assert ready.status == "degraded"
    assert ready.backend == "xdg-global-shortcuts-portal"
    missing = linux_desktop.shortcut_capability(
        session, which=_which(), readable_input=False)
    assert missing.status == "unsupported"
    assert "compositor" in missing.message.lower()


def test_wayland_clipboard_and_insertion_do_not_fall_back_to_x11():
    session = linux_desktop.DesktopSession("wayland", "kde",
                                           xwayland_available=True)
    cap = linux_desktop.clipboard_capability(
        session, which=_which("xclip", "xdotool"))
    assert cap.status == "unsupported"
    assert cap.backend == "none"
    assert "wl-clipboard" in cap.message


def test_wayland_drag_exposes_open_and_reveal_alternatives():
    cap = linux_desktop.drag_capability(
        linux_desktop.DesktopSession("wayland", "gnome"))
    assert cap.status == "unsupported"
    assert cap.alternatives == ("open", "reveal")
    assert "compositor" in cap.message.lower()


def test_x11_drag_is_ready_only_with_the_trusted_gtk_owner():
    session = linux_desktop.DesktopSession("x11", "other")
    ready = linux_desktop.drag_capability(session, gtk_available=True)
    assert ready.status == "ready"
    assert ready.backend == "gtk-uri-drag"
    missing = linux_desktop.drag_capability(session, gtk_available=False)
    assert missing.status == "unsupported"
    assert missing.alternatives == ("open", "reveal")


def test_audio_probe_distinguishes_pipewire_ready_denied_and_no_device():
    pipewire = mock.Mock()
    pipewire.query_hostapis.return_value = [
        {"name": "ALSA"}, {"name": "PulseAudio (on PipeWire)"}]
    pipewire.query_devices.return_value = [
        {"name": "Mic", "max_input_channels": 1, "hostapi": 1}]
    ready = linux_desktop.audio_capability(pipewire)
    assert ready.status == "ready"
    assert ready.backend == "pipewire"

    denied = mock.Mock()
    denied.query_hostapis.side_effect = PermissionError("sandbox denied")
    assert linux_desktop.audio_capability(denied).status == "denied"

    empty = mock.Mock()
    empty.query_hostapis.return_value = [{"name": "PulseAudio"}]
    empty.query_devices.return_value = []
    assert linux_desktop.audio_capability(empty).status == "degraded"


def test_capability_snapshot_keeps_sandbox_and_physical_gate_truth():
    with mock.patch.dict(os.environ, {
            "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME",
            "FLATPAK_ID": "com.mumble.Mumble"}, clear=True):
        snapshot = linux_desktop.capability_snapshot(which=_which("gdbus"))
    assert snapshot["session"]["sandbox"] == "flatpak"
    assert snapshot["evidence"] == "source-probe-only"
    assert snapshot["physical_parity"] is False
    assert set(("focus_window", "tray", "autostart", "permissions")) <= set(snapshot)
    assert all("recovery" in snapshot[name] for name in (
        "focus_window", "tray", "autostart", "permissions"))


def test_linux_island_exposes_labelled_stop_and_reduced_motion():
    island = overlay_linux._NoopIsland(None)
    stopped = []
    island.set_widget_callbacks(on_stop=lambda: stopped.append(True))
    island.set_state("listening")
    island.bar_state["reduced_motion"] = True
    layout = island_render.bar_layout(island.bar_state)
    assert layout["stop_label"] == "Stop"
    assert layout["stop_target_height"] >= 40
    assert layout["processing_cancel"] is None
    island._widget_stop()
    assert stopped == [True]


def test_wayland_portal_trigger_and_callbacks_are_bounded():
    import portal_shortcuts

    assert portal_shortcuts._preferred_trigger("ctrl+alt+v") == "CTRL+ALT+V"
    try:
        portal_shortcuts._preferred_trigger("mouse:x2")
    except ValueError as exc:
        assert "keyboard" in str(exc)
    else:
        raise AssertionError("Wayland portal must reject mouse pseudo-bindings")


def test_wayland_binding_uses_portal_without_touching_evdev():
    import bindings

    portal_handle = mock.Mock()
    with mock.patch.object(
            bindings.portal_shortcuts, "native_wayland", return_value=True), \
         mock.patch.object(
            bindings.portal_shortcuts, "register_hotkey",
            return_value=portal_handle) as register, \
         mock.patch.object(bindings, "_ensure_keyboard_backend_ready") as evdev:
        handle = bindings.register_hotkey("ctrl+alt+f", lambda: None)
        assert handle.kind == "portal"
        register.assert_called_once()
        evdev.assert_not_called()
        assert bindings.unregister(handle) is True
        portal_handle.close.assert_called_once()


def test_durable_delivery_claim_is_persisted_before_external_side_effect():
    source = open("mumble_linux.py", encoding="utf-8").read()
    committed = source.index("durable_session.mark_history_committed(")
    claimed = source.index("durable_session.claim_final_insertion(")
    pasted = source.index("landed = self._paste(out)", claimed)
    completed = source.index("durable_session.complete_finalization(", pasted)
    assert committed < claimed < pasted < completed


def test_recovery_is_local_and_segment_boundaries_are_reconciled():
    source = open("mumble_linux.py", encoding="utf-8").read()
    recovery = source[source.index("def _recover_durable_dictations"):
                      source.index("def _stream_worker")]
    assert "local_only=True" in recovery
    assert "_reconcile_timestamped_segment" in source
    assert "_dictation_inference_overlap_samples" in source


def test_portal_refuses_an_unbound_shortcut_handle():
    source = open("portal_shortcuts.py", encoding="utf-8").read()
    assert 'bound_result.get("shortcuts")' in source
    assert "shortcut_id not in bound_ids" in source


def test_x11_native_drag_cancels_queued_start_after_timeout():
    source = open("linux_native_drag.py", encoding="utf-8").read()
    assert "idle_source = GLib.idle_add(begin)" in source
    assert "cancelled.set()" in source
    assert "GLib.source_remove(idle_source)" in source
    assert source.index("if cancelled.is_set()") < source.index(
        "window = Gtk.Window.new")
