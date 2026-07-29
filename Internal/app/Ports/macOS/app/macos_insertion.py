"""macOS-native adapters for the shared exact-once insertion contract."""

from __future__ import annotations

import hashlib
from pathlib import Path
import time

from insertion import (
    ClipboardOwnership,
    ClipboardRestoreResult,
    ClipboardRestoreState,
    ClipboardSnapshot,
    ClipboardWriteFailure,
    NativeAcceptance,
    TargetContext,
    TargetEditability,
)


TEXT_TYPE = "public.utf8-plain-text"
HTML_TYPE = "public.html"
RTF_TYPE = "public.rtf"
TIFF_TYPE = "public.tiff"


class _PasteboardPartialWrite(OSError):
    """A failed AppKit replacement with exact Mumble-owned partial state."""

    def __init__(self, reason, *, owned_sequence, partial_fingerprint):
        super().__init__(reason)
        self.owned_sequence = int(owned_sequence)
        self.partial_fingerprint = str(partial_fingerprint)


def _fingerprint(formats):
    digest = hashlib.sha256()
    for name, payload in sorted(formats.items()):
        digest.update(str(name).encode("utf-8", "replace"))
        digest.update(b"\0")
        digest.update(bytes(payload))
        digest.update(b"\0")
    return digest.hexdigest()


class _AppKitPasteboardBackend:
    @property
    def change_count(self):
        from AppKit import NSPasteboard
        return int(NSPasteboard.generalPasteboard().changeCount())

    def read_formats(self):
        from AppKit import NSPasteboard
        pasteboard = NSPasteboard.generalPasteboard()
        output = {}
        for value in pasteboard.types() or ():
            name = str(value)
            data = pasteboard.dataForType_(value)
            if data is None:
                raise OSError(
                    f"macOS pasteboard type {name} could not be captured safely"
                )
            output[name] = bytes(data)
        return output

    def replace_formats(self, values):
        from AppKit import NSData, NSPasteboard
        pasteboard = NSPasteboard.generalPasteboard()
        owned_sequence = int(pasteboard.clearContents())
        written = {}
        try:
            for name, payload in values.items():
                raw = bytes(payload)
                data = NSData.dataWithBytes_length_(raw, len(raw))
                if not pasteboard.setData_forType_(data, name):
                    raise OSError(f"macOS pasteboard refused {name}")
                written[name] = raw
        except Exception as error:
            raise _PasteboardPartialWrite(
                str(error),
                owned_sequence=owned_sequence,
                partial_fingerprint=_fingerprint(written),
            ) from error
        return int(pasteboard.changeCount())


class MacClipboardAdapter:
    """Snapshot every readable pasteboard type and restore only while owned."""

    def __init__(self, backend=None, image_loader=None):
        self._backend = backend or _AppKitPasteboardBackend()
        self._image_loader = image_loader or self._load_tiff

    @staticmethod
    def _load_tiff(path):
        from AppKit import NSImage
        image = NSImage.alloc().initWithContentsOfFile_(str(path))
        if image is None:
            raise ValueError("The selected image could not be read.")
        data = image.TIFFRepresentation()
        if data is None:
            raise ValueError("The selected image has no pasteboard representation.")
        return bytes(data)

    def snapshot(self):
        before = int(self._backend.change_count)
        formats = self._backend.read_formats()
        after = int(self._backend.change_count)
        if before != after:
            raise RuntimeError("pasteboard_changed_during_snapshot")
        rows = tuple(
            (index + 1, name, bytes(payload))
            for index, (name, payload) in enumerate(sorted(formats.items()))
        )
        return ClipboardSnapshot(
            sequence=after,
            formats=rows,
            restorable=True,
        )

    @staticmethod
    def _snapshot_formats(snapshot):
        return {name: bytes(payload) for _key, name, payload in snapshot.formats}

    def write(self, request, snapshot):
        if request.content_kind == "image":
            path = Path(request.image_path).expanduser().resolve(strict=True)
            formats = {TIFF_TYPE: self._image_loader(path)}
        elif request.content_kind == "rich":
            formats = {TEXT_TYPE: request.text.encode("utf-8")}
            if request.rich_html:
                formats[HTML_TYPE] = request.rich_html.encode("utf-8")
            if request.rich_rtf:
                formats[RTF_TYPE] = request.rich_rtf.encode("utf-8")
        else:
            formats = {TEXT_TYPE: request.text.encode("utf-8")}
        try:
            sequence = int(self._backend.replace_formats(formats))
        except Exception as error:
            restored = False
            changed_externally = False
            cleanup_warning = ""
            try:
                current_sequence = int(self._backend.change_count)
                current_formats = self._backend.read_formats()
                owned_sequence = getattr(error, "owned_sequence", None)
                partial_fingerprint = getattr(
                    error, "partial_fingerprint", None)
                owns_partial = (
                    owned_sequence is not None
                    and current_sequence == int(owned_sequence)
                    and partial_fingerprint is not None
                    and _fingerprint(current_formats) == partial_fingerprint
                )
                if owns_partial:
                    self._backend.replace_formats(
                        self._snapshot_formats(snapshot))
                    restored = True
                elif current_sequence != int(snapshot.sequence):
                    changed_externally = True
            except Exception as cleanup_error:
                cleanup_warning = type(cleanup_error).__name__
            raise ClipboardWriteFailure(
                type(error).__name__,
                clipboard_restored=restored,
                clipboard_changed_externally=changed_externally,
                cleanup_warning=cleanup_warning,
            ) from error
        return ClipboardOwnership(sequence=sequence, fingerprint=_fingerprint(formats))

    def still_owns(self, ownership):
        if int(self._backend.change_count) != int(ownership.sequence):
            return False
        return _fingerprint(self._backend.read_formats()) == ownership.fingerprint

    def restore(self, snapshot, ownership=None):
        if ownership is not None and not self.still_owns(ownership):
            return ClipboardRestoreResult(ClipboardRestoreState.NEWER_EXTERNAL)
        self._backend.replace_formats(self._snapshot_formats(snapshot))
        return ClipboardRestoreResult(ClipboardRestoreState.RESTORED)


class _MacTargetBackend:
    """Accessibility-backed foreground identity with no selected text access."""

    def capture(self):
        from AppKit import NSWorkspace
        from CoreFoundation import CFHash
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementCreateApplication,
            AXUIElementIsAttributeSettable,
        )

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        pid = int(app.processIdentifier())
        launched = app.launchDate()
        process_created = (
            int(float(launched.timeIntervalSince1970()) * 1_000_000)
            if launched is not None else 0
        )
        ax_app = AXUIElementCreateApplication(pid)
        error, focused = AXUIElementCopyAttributeValue(
            ax_app, "AXFocusedUIElement", None
        )
        if error or focused is None:
            return {
                "pid": pid, "focused": 0, "editable": "unknown",
                "process_created": process_created,
            }
        _err, role = AXUIElementCopyAttributeValue(focused, "AXRole", None)
        _err, subrole = AXUIElementCopyAttributeValue(focused, "AXSubrole", None)
        _err, identifier = AXUIElementCopyAttributeValue(focused, "AXIdentifier", None)
        _err, settable = AXUIElementIsAttributeSettable(focused, "AXValue", None)
        role = str(role or "")
        protected = str(subrole or "") in {"AXSecureTextField"}
        editable = bool(settable) and not protected
        try:
            element_identity = int(CFHash(focused))
        except Exception:
            element_identity = 0
        if not element_identity:
            # Role/optional AXIdentifier cannot distinguish two ordinary text
            # fields in one process. Fail closed when the native element itself
            # has no stable Core Foundation identity.
            return {
                "pid": pid, "process_created": process_created,
                "focused": 0, "editable": "unknown", "role": role,
            }
        identity = f"{pid}\0{element_identity}\0{identifier or ''}"
        focused_id = int(hashlib.sha256(identity.encode()).hexdigest()[:15], 16)
        return {
            "pid": pid,
            "process_created": process_created,
            "focused": focused_id,
            "role": role,
            "editable": editable,
            "protected": protected,
        }

    @staticmethod
    def activate(pid):
        from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid))
        return bool(app and app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps))


class MacTargetAdapter:
    def __init__(self, backend=None, permission_probe=None):
        self._backend = backend or _MacTargetBackend()
        self._permission_probe = permission_probe or self._accessibility_ready

    @staticmethod
    def _accessibility_ready():
        from ApplicationServices import AXIsProcessTrusted
        return bool(AXIsProcessTrusted())

    def current(self):
        try:
            value = self._backend.capture()
        except Exception:
            return None
        if not value:
            return None
        editable = value.get("editable", "unknown")
        editability = (
            TargetEditability.EDITABLE if editable is True else
            TargetEditability.NOT_EDITABLE if editable is False else
            TargetEditability.UNKNOWN
        )
        pid = int(value.get("pid") or 0)
        focused = int(value.get("focused") or 0)
        process_created = int(value.get("process_created") or 0)
        if not pid or not process_created or not focused:
            return None
        return TargetContext(
            window=pid,
            process_id=pid,
            thread_id=pid,
            focused_child=focused,
            integrity="same-user",
            control_class=str(value.get("role") or ""),
            has_caret=bool(focused and editability is TargetEditability.EDITABLE),
            editability=editability,
            read_only=bool(value.get("read_only")),
            protected=bool(value.get("protected")),
            integrity_relation="same",
            process_creation_id=process_created,
            uia_runtime_id=(focused,) if focused else (),
            uia_observed=bool(focused),
            uia_state="ready" if focused else "unavailable",
        )

    def restore(self, target, timeout_s):
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        if not self._backend.activate(target.process_id):
            return False
        while time.monotonic() <= deadline:
            current = self.current()
            if target.same_destination(current):
                return True
            time.sleep(0.01)
        return False

    def can_inject(self, _target):
        try:
            return bool(self._permission_probe())
        except Exception:
            return False


class MacNativeInputAdapter:
    """Quartz input seam; successful posting is never called target confirmation."""

    def __init__(self, modifier_probe=None, post_chord=None, post_unicode=None):
        self._modifier_probe = modifier_probe or self._held_modifiers
        self._post_chord = post_chord or self._quartz_chord
        self._post_unicode = post_unicode or self._quartz_unicode

    @staticmethod
    def _held_modifiers():
        from Quartz import CGEventSourceFlagsState, kCGEventSourceStateCombinedSessionState
        flags = int(CGEventSourceFlagsState(kCGEventSourceStateCombinedSessionState))
        masks = {
            "shift": 1 << 17, "control": 1 << 18,
            "option": 1 << 19, "command": 1 << 20,
        }
        return {name for name, mask in masks.items() if flags & mask}

    def ready(self, timeout_s):
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        while True:
            if not self._modifier_probe():
                return True, ""
            if time.monotonic() >= deadline:
                return False, "held_modifier"
            time.sleep(0.005)

    @staticmethod
    def _quartz_chord(key):
        from Quartz import (
            CGEventCreateKeyboardEvent, CGEventPost, CGEventSetFlags,
            kCGEventFlagMaskCommand, kCGHIDEventTap,
        )
        codes = {"v": 9, "z": 6}
        code = codes[key]
        down = CGEventCreateKeyboardEvent(None, code, True)
        up = CGEventCreateKeyboardEvent(None, code, False)
        CGEventSetFlags(down, kCGEventFlagMaskCommand)
        CGEventSetFlags(up, kCGEventFlagMaskCommand)
        CGEventPost(kCGHIDEventTap, down)
        CGEventPost(kCGHIDEventTap, up)
        return True

    @staticmethod
    def _quartz_unicode(text):
        from Quartz import (
            CGEventCreateKeyboardEvent, CGEventKeyboardSetUnicodeString,
            CGEventPost, kCGHIDEventTap,
        )
        if not str(text):
            return 0
        down = CGEventCreateKeyboardEvent(None, 0, True)
        up = CGEventCreateKeyboardEvent(None, 0, False)
        utf16_units = len(text.encode("utf-16-le")) // 2
        CGEventKeyboardSetUnicodeString(down, utf16_units, text)
        CGEventKeyboardSetUnicodeString(up, utf16_units, text)
        CGEventPost(kCGHIDEventTap, down)
        CGEventPost(kCGHIDEventTap, up)
        return True

    def _acceptance(self, key):
        requested = 4
        accepted = self._post_chord(key)
        unknown_acceptance = accepted is True
        return NativeAcceptance(
            requested=requested,
            accepted=(None if unknown_acceptance else
                      int(accepted) if accepted is not None else None),
            submitted=accepted is not None,
            confirmation=None,
        )

    def send_paste(self):
        return self._acceptance("v")

    def send_undo(self):
        return self._acceptance("z")

    def send_unicode(self, text):
        requested = 2
        accepted = self._post_unicode(text)
        unknown_acceptance = accepted is True
        return NativeAcceptance(
            requested=requested,
            accepted=(None if unknown_acceptance else
                      int(accepted) if accepted is not None else None),
            submitted=accepted is not None,
            confirmation=None,
        )
