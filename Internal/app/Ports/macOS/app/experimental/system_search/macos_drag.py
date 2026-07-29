"""Trusted AppKit file-drag seam for macOS Mumble Find."""

from __future__ import annotations

from pathlib import Path
import threading
import time


class MacNativeDragAdapter:
    """Consume the exact AppKit mouse-drag event observed for Mumble Find."""

    def __init__(self, *, clock=time.monotonic, max_event_age=1.0,
                 begin_drag=None):
        self._lock = threading.RLock()
        self._clock = clock
        self._max_event_age = max(0.05, float(max_event_age))
        self._begin_drag = begin_drag or self._begin_appkit_drag
        self._view = self._event = self._source = None
        self._armed_at = 0.0

    def arm(self, view, event, source):
        with self._lock:
            self._view, self._event, self._source = view, event, source
            self._armed_at = self._clock()

    def clear(self):
        with self._lock:
            self._view = self._event = self._source = None
            self._armed_at = 0.0

    @staticmethod
    def _begin_appkit_drag(target, view, event, source):
        from AppKit import NSDraggingItem, NSMakeRect, NSURL
        url = NSURL.fileURLWithPath_(str(target))
        item = NSDraggingItem.alloc().initWithPasteboardWriter_(url)
        frame = view.bounds()
        item.setDraggingFrame_contents_(
            NSMakeRect(frame.origin.x, frame.origin.y, 1, 1), None)
        return view.beginDraggingSessionWithItems_event_source_(
            [item], event, source)

    def start(self, target):
        target = Path(target).expanduser().resolve(strict=True)
        with self._lock:
            view, event, source = self._view, self._event, self._source
            armed_at = self._armed_at
            self.clear()
        if (view is None or event is None or source is None
                or self._clock() - armed_at > self._max_event_age):
            return {"ok": False,
                    "message": "Start the drag from a visible Mumble Find result."}
        try:
            session = self._begin_drag(target, view, event, source)
            return {
                "ok": session is not None,
                "message": "Drag started" if session is not None
                else "Drag could not start.",
            }
        except Exception as exc:
            return {"ok": False, "message": str(exc)[:200]}


_ADAPTER = MacNativeDragAdapter()
_DRAG_SOURCE = _DRAG_SOURCE_CLASS = _MAIN_INVOKER_CLASS = None
_MONITOR = _MONITOR_HANDLER = None
_NATIVE_LOCK = threading.RLock()


def _native_classes():
    global _DRAG_SOURCE_CLASS, _MAIN_INVOKER_CLASS
    from AppKit import NSDragOperationCopy
    from Foundation import NSObject
    if _DRAG_SOURCE_CLASS is None:
        class MumbleFindDragSource(NSObject):
            def draggingSession_sourceOperationMaskForDraggingContext_(
                    self, _session, _context):
                return NSDragOperationCopy

            def ignoreModifierKeysForDraggingSession_(self, _session):
                return False
        _DRAG_SOURCE_CLASS = MumbleFindDragSource
    if _MAIN_INVOKER_CLASS is None:
        class MumbleFindDragMainInvoker(NSObject):
            def run_(self, _unused):
                if self.action == "install":
                    self.result = _install_monitor_on_main()
                elif self.action == "release":
                    self.result = _release_monitor_on_main()
                else:
                    self.result = _ADAPTER.start(self.target)
        _MAIN_INVOKER_CLASS = MumbleFindDragMainInvoker
    return _DRAG_SOURCE_CLASS, _MAIN_INVOKER_CLASS


def _on_main(action, target=None):
    from Foundation import NSThread
    if NSThread.isMainThread():
        if action == "install":
            return _install_monitor_on_main()
        if action == "release":
            return _release_monitor_on_main()
        return _ADAPTER.start(target)
    _source_class, invoker_class = _native_classes()
    invoker = invoker_class.alloc().init()
    invoker.action, invoker.target, invoker.result = action, target, None
    invoker.performSelectorOnMainThread_withObject_waitUntilDone_(
        "run:", None, True)
    return invoker.result


def _install_monitor_on_main():
    global _DRAG_SOURCE, _MONITOR, _MONITOR_HANDLER
    from AppKit import NSEvent
    try:
        from AppKit import NSEventMaskLeftMouseDragged as mask
    except ImportError:
        from AppKit import NSLeftMouseDraggedMask as mask
    with _NATIVE_LOCK:
        if _MONITOR is not None:
            return True
        source_class, _invoker_class = _native_classes()
        _DRAG_SOURCE = source_class.alloc().init()

        def observe(event):
            try:
                window = event.window()
                if window is not None and str(window.title() or "") == "Mumble Find":
                    _ADAPTER.arm(window.contentView(), event, _DRAG_SOURCE)
            except Exception:
                _ADAPTER.clear()
            return event

        _MONITOR_HANDLER = observe
        _MONITOR = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            mask, _MONITOR_HANDLER)
        return _MONITOR is not None


def _release_monitor_on_main():
    global _DRAG_SOURCE, _MONITOR, _MONITOR_HANDLER
    from AppKit import NSEvent
    with _NATIVE_LOCK:
        if _MONITOR is not None:
            NSEvent.removeMonitor_(_MONITOR)
        _MONITOR = _MONITOR_HANDLER = _DRAG_SOURCE = None
        _ADAPTER.clear()
    return True


def install_macos_drag_monitor():
    return _on_main("install")


def release_macos_drag_monitor():
    return _on_main("release")


def arm_macos_file_drag(view, event, source):
    _ADAPTER.arm(view, event, source)


def start_macos_file_drag(target):
    return _on_main("start", str(target))
