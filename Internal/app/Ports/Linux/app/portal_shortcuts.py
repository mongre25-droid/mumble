"""Bounded XDG GlobalShortcuts portal adapter for native Wayland sessions.

The portal is compositor-owned: registration may show an approval dialog and
the compositor may replace the requested accelerator.  This module therefore
never falls back to X11 helpers merely because ``DISPLAY`` is also present.
"""

from __future__ import annotations

import os
import threading
import uuid


PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
GLOBAL_INTERFACE = "org.freedesktop.portal.GlobalShortcuts"


def _preferred_trigger(spec):
    """Translate Mumble's small binding grammar to portal trigger syntax."""
    aliases = {
        "ctrl": "CTRL", "control": "CTRL", "alt": "ALT",
        "shift": "SHIFT", "windows": "LOGO", "win": "LOGO",
        "cmd": "LOGO", "esc": "ESCAPE", "escape": "ESCAPE",
        "space": "SPACE", "enter": "RETURN", "return": "RETURN",
    }
    parts = [part.strip().lower() for part in str(spec).split("+")
             if part.strip()]
    if not parts or any(part.startswith("mouse:") for part in parts):
        raise ValueError("The Wayland shortcut portal requires a keyboard binding.")
    return "+".join(aliases.get(part, part.upper()) for part in parts)


class PortalShortcutHandle:
    def __init__(self, backend, session_handle, shortcut_id):
        self._backend = backend
        self.session_handle = session_handle
        self.shortcut_id = shortcut_id
        self._closed = False

    def close(self):
        if self._closed:
            return True
        self._closed = True
        return self._backend.close(self.session_handle)


class GioPortalBackend:
    """Small synchronous wrapper over the asynchronous request protocol."""

    def __init__(self, timeout=15.0):
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
        except Exception as exc:  # pragma: no cover - Linux dependency probe
            raise RuntimeError("PyGObject Gio is required for portal shortcuts") from exc
        self.Gio = Gio
        self.GLib = GLib
        self.timeout = float(timeout)
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.proxy = Gio.DBusProxy.new_sync(
            self.bus, Gio.DBusProxyFlags.NONE, None, PORTAL_BUS,
            PORTAL_PATH, GLOBAL_INTERFACE, None)
        self._callbacks = {}
        self._signal_id = self.bus.signal_subscribe(
            PORTAL_BUS, GLOBAL_INTERFACE, None, PORTAL_PATH, None,
            Gio.DBusSignalFlags.NONE, self._on_global_signal)
        self._loop = GLib.MainLoop()
        self._thread = threading.Thread(target=self._loop.run, daemon=True)
        self._thread.start()

    def _request(self, method, parameters):
        completed = threading.Event()
        response = {}
        expected_path = {"value": None}
        early = {}

        def on_response(_bus, _sender, path, _interface, _signal,
                        parameters, _data=None):
            values = parameters.unpack()
            if expected_path["value"] is None:
                early[path] = values
                return
            if path == expected_path["value"]:
                response["code"] = int(values[0])
                response["results"] = values[1]
                completed.set()

        subscription = self.bus.signal_subscribe(
            PORTAL_BUS, "org.freedesktop.portal.Request", "Response",
            None, None, self.Gio.DBusSignalFlags.NONE, on_response)
        try:
            request_path = self.proxy.call_sync(
                method, parameters, self.Gio.DBusCallFlags.NONE,
                int(self.timeout * 1000), None).unpack()[0]
            expected_path["value"] = request_path
            if request_path in early:
                code, results = early[request_path]
                response["code"] = int(code)
                response["results"] = results
                completed.set()
            if not completed.wait(self.timeout):
                raise RuntimeError(f"{method} portal approval timed out")
        finally:
            self.bus.signal_unsubscribe(subscription)
        if response.get("code") != 0:
            raise RuntimeError(f"{method} portal request was denied or cancelled")
        return response.get("results") or {}

    def register(self, spec, on_down, on_up=None, description="Mumble shortcut"):
        token = "mumble_" + uuid.uuid4().hex
        options = {
            "handle_token": self.GLib.Variant("s", token),
            "session_handle_token": self.GLib.Variant("s", token + "_session"),
        }
        result = self._request(
            "CreateSession", self.GLib.Variant("(a{sv})", (options,)))
        session_handle = result.get("session_handle")
        if hasattr(session_handle, "unpack"):
            session_handle = session_handle.unpack()
        if not session_handle:
            raise RuntimeError("The shortcut portal returned no session")
        shortcut_id = "mumble_" + uuid.uuid4().hex
        shortcuts = [(shortcut_id, {
            "description": self.GLib.Variant("s", description),
            "preferred_trigger": self.GLib.Variant(
                "s", _preferred_trigger(spec)),
        })]
        bind_options = {"handle_token": self.GLib.Variant(
            "s", "mumble_" + uuid.uuid4().hex)}
        bound_result = self._request("BindShortcuts", self.GLib.Variant(
            "(oa(sa{sv})sa{sv})",
            (session_handle, shortcuts, "", bind_options)))
        bound = bound_result.get("shortcuts") or []
        if hasattr(bound, "unpack"):
            bound = bound.unpack()
        bound_ids = {str(row[0]) for row in bound if row}
        if shortcut_id not in bound_ids:
            self.close(session_handle)
            raise RuntimeError(
                "The compositor did not bind the requested shortcut")
        self._callbacks[(session_handle, shortcut_id)] = (on_down, on_up)
        return PortalShortcutHandle(self, session_handle, shortcut_id)

    def _on_global_signal(self, _bus, _sender, _path, _interface, signal,
                          parameters, _data=None):
        if signal not in ("Activated", "Deactivated"):
            return
        values = parameters.unpack()
        if len(values) < 2:
            return
        callbacks = self._callbacks.get((values[0], values[1]))
        if not callbacks:
            return
        callback = callbacks[0] if signal == "Activated" else callbacks[1]
        if callback is not None:
            callback()

    def close(self, session_handle):
        for key in [key for key in self._callbacks if key[0] == session_handle]:
            self._callbacks.pop(key, None)
        try:
            self.bus.call_sync(
                PORTAL_BUS, session_handle, "org.freedesktop.portal.Session",
                "Close", None, None, self.Gio.DBusCallFlags.NONE,
                int(self.timeout * 1000), None)
            return True
        except Exception:
            return False


_BACKEND = None
_LOCK = threading.Lock()


def backend():
    global _BACKEND
    with _LOCK:
        if _BACKEND is None:
            _BACKEND = GioPortalBackend()
        return _BACKEND


def native_wayland():
    return (os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            or bool(os.environ.get("WAYLAND_DISPLAY")))


def register_hotkey(spec, callback):
    return backend().register(spec, callback, description="Mumble action")


def register_hold(spec, on_down, on_up):
    return backend().register(
        spec, on_down, on_up, description="Mumble hold action")
