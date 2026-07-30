"""Trusted GTK URI drag owner for X11 Mumble Find results.

The Web layer supplies only an opaque result id.  The search engine resolves
that id to an already-indexed absolute path before this adapter sees it.
"""

from __future__ import annotations

from pathlib import Path
import threading
import time


class LinuxNativeDragAdapter:
    def __init__(self, session):
        self.session = session

    @staticmethod
    def available():
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gdk, Gtk  # noqa: F401
            return True
        except Exception:
            return False

    def start(self, path, timeout=1.0):
        target = Path(path).expanduser()
        if (not self.session.is_x11 or not target.is_absolute()
                or not target.exists()):
            return {"ok": False, "status": "unsupported",
                    "message": "Native drag requires a trusted existing X11 path.",
                    "alternatives": ["open", "reveal"]}
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gdk, GLib, Gtk
        except Exception as exc:
            return {"ok": False, "status": "unsupported",
                    "message": str(exc), "alternatives": ["open", "reveal"]}

        finished = threading.Event()
        cancelled = threading.Event()
        start_lock = threading.Lock()
        wait_timeout = max(0.0, min(2.0, float(timeout)))
        deadline = time.monotonic() + wait_timeout
        result = {"ok": False, "status": "failed",
                  "message": "The native drag owner did not start.",
                  "alternatives": ["open", "reveal"]}

        def begin():
            window = None
            try:
                if cancelled.is_set():
                    return False
                try:
                    display = Gdk.Display.get_default()
                    seat = display.get_default_seat() if display else None
                    pointer = seat.get_pointer() if seat else None
                    _screen, x_root, y_root = pointer.get_position() if pointer else (
                        None, 0, 0)
                    window = Gtk.Window.new(Gtk.WindowType.POPUP)
                    window.set_decorated(False)
                    window.set_skip_taskbar_hint(True)
                    window.set_accept_focus(False)
                    window.set_default_size(2, 2)
                    window.move(int(x_root), int(y_root))
                    window.set_opacity(0.01)
                    uri = target.resolve().as_uri()

                    def provide(_widget, _context, selection, _info, _time):
                        selection.set_uris([uri])

                    def cleanup(*_args):
                        try:
                            window.destroy()
                        except Exception:
                            pass

                    window.connect("drag-data-get", provide)
                    window.connect("drag-end", cleanup)
                    targets = [Gtk.TargetEntry.new("text/uri-list", 0, 0)]
                    Gtk.drag_source_set(
                        window, Gdk.ModifierType.BUTTON1_MASK, targets,
                        Gdk.DragAction.COPY)
                    window.show_all()
                    event = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
                    event.button = 1
                    event.time = Gdk.CURRENT_TIME
                    event.window = window.get_window()
                    event.x = event.y = 1.0
                    event.x_root, event.y_root = float(x_root), float(y_root)
                    with start_lock:
                        if cancelled.is_set() or time.monotonic() >= deadline:
                            cancelled.set()
                            cleanup()
                            result.update({
                                "ok": False, "status": "timeout",
                                "message": "The GTK drag owner did not start in time.",
                            })
                            return False
                        context = Gtk.drag_begin_with_coordinates(
                            window, Gtk.TargetList.new(targets),
                            Gdk.DragAction.COPY, 1, event, -1, -1)
                    if context is None:
                        cleanup()
                        raise RuntimeError("GTK refused to start the drag")
                    if cancelled.is_set() or time.monotonic() >= deadline:
                        cleanup()
                        result.update({
                            "ok": False, "status": "timeout",
                            "message": "The GTK drag owner did not start in time.",
                        })
                    else:
                        result.update({
                            "ok": True, "status": "started", "dropped": False,
                            "message": "Native drag started; drop acceptance is target-owned.",
                            "alternatives": ["open", "reveal"],
                        })
                except Exception as exc:
                    if window is not None:
                        try:
                            window.destroy()
                        except Exception:
                            pass
                    result["message"] = str(exc)[:240]
                finally:
                    pass
            finally:
                finished.set()
            return False

        idle_source = GLib.idle_add(begin)
        if not finished.wait(wait_timeout):
            with start_lock:
                if finished.is_set() and result.get("status") != "started":
                    return result
                cancelled.set()
                try:
                    GLib.source_remove(idle_source)
                except Exception:
                    pass
            return {"ok": False, "status": "timeout",
                    "message": "The GTK drag owner did not start in time.",
                    "alternatives": ["open", "reveal"]}
        return result
