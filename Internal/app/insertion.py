"""Target-bound, at-most-once insertion transaction.

The controller supplies three small platform adapters: target/focus, clipboard,
and native input.  This module owns policy and terminal user truth; it never
guesses that content landed merely because a paste chord was attempted.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
import threading
import time
from typing import Callable, Optional, Tuple


class InsertionOutcome(str, Enum):
    CONFIRMED = "confirmed"
    SENT_UNCONFIRMED = "sent_unconfirmed"
    NOT_SENT = "not_sent"
    UNCERTAIN = "uncertain"
    SAVED_ONLY = "saved_only"


class TargetEditability(str, Enum):
    EDITABLE = "editable"
    NOT_EDITABLE = "not_editable"
    UNKNOWN = "unknown"


class InsertionReason(str, Enum):
    CONFIRMED = "confirmed"
    CONFIRMATION_UNAVAILABLE = "confirmation_unavailable"
    NO_TARGET = "no_target"
    TARGET_CHANGED = "target_changed"
    NO_FOCUS = "no_focus"
    NOT_EDITABLE = "not_editable"
    READ_ONLY = "read_only"
    PROTECTED_FIELD = "protected_field"
    EDITABILITY_UNKNOWN = "editability_unknown"
    HIGHER_INTEGRITY = "higher_integrity"
    UNKNOWN_INTEGRITY = "unknown_integrity"
    CLIPBOARD_SNAPSHOT_FAILED = "clipboard_snapshot_failed"
    CLIPBOARD_UNSAFE = "clipboard_unsafe"
    CLIPBOARD_WRITE_FAILED = "clipboard_write_failed"
    HELD_MODIFIER = "held_modifier"
    ZERO_INPUT = "zero_input"
    PARTIAL_INPUT = "partial_input"
    INVALID_ACCEPTANCE = "invalid_acceptance"
    TARGET_REJECTED = "target_rejected"
    PASTE_RESULT_UNKNOWN = "paste_result_unknown"
    UNDO_RESULT_UNKNOWN = "undo_result_unknown"
    UNDO_NOT_FULLY_ACCEPTED = "undo_not_fully_accepted"
    TARGET_CHANGED_AFTER_UNDO = "target_changed_after_undo"


@dataclass(frozen=True)
class TargetContext:
    window: int
    process_id: int
    thread_id: int
    focused_child: int
    integrity: str
    control_class: str = ""
    has_caret: bool = False
    editability: TargetEditability = TargetEditability.UNKNOWN
    read_only: bool = False
    protected: bool = False
    integrity_relation: str = "unknown"

    def same_destination(self, other: Optional["TargetContext"]) -> bool:
        if other is None:
            return False
        return (
            self.window == other.window
            and self.process_id == other.process_id
            and self.focused_child == other.focused_child
        )


@dataclass(frozen=True)
class TargetLease:
    """Immutable ownership decision made at the caller's safe capture moment."""

    target: Optional[TargetContext]
    source: str
    capture_phase: str
    mumble_displaced_target: bool = False


@dataclass(frozen=True)
class ClipboardSnapshot:
    sequence: int
    formats: Tuple[Tuple[int, str, bytes], ...]
    restorable: bool = True
    reason: str = ""


@dataclass(frozen=True)
class ClipboardOwnership:
    sequence: int
    fingerprint: str
    format_id: int = 0


@dataclass(frozen=True)
class NativeAcceptance:
    requested: int
    accepted: Optional[int]
    submitted: bool
    confirmation: Optional[bool] = None
    error: str = ""


@dataclass(frozen=True)
class InsertionRequest:
    operation_id: str
    source: str
    content_kind: str
    activation_target: Optional[TargetContext]
    target_lease: Optional[TargetLease] = None
    text: str = ""
    image_path: str = ""
    restore_focus: bool = True
    restore_clipboard: bool = True
    focus_timeout_s: float = 0.35
    modifier_timeout_s: float = 0.35
    settle_seconds: float = 0.20
    undo_before_paste: bool = False

    def fingerprint(self) -> str:
        """Return a content-hiding identity for immutable request comparison."""
        values = asdict(self)
        values.pop("operation_id", None)
        values["text"] = hashlib.sha256(
            str(values.pop("text", "")).encode("utf-8")).hexdigest()
        values["image_path"] = hashlib.sha256(
            str(values.pop("image_path", "")).encode("utf-8")).hexdigest()
        payload = json.dumps(
            values, sort_keys=True, separators=(",", ":"),
            default=lambda value: "<{}.{}>".format(
                type(value).__module__, type(value).__qualname__),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_MESSAGES = {
    InsertionOutcome.CONFIRMED:
        "Pasted. The result also remains in Deck and History.",
    InsertionOutcome.SENT_UNCONFIRMED:
        "Sent—check the field. The result remains in Deck and History.",
    InsertionOutcome.NOT_SENT:
        "Not sent. Use Copy or Paste latest from Deck or History.",
    InsertionOutcome.UNCERTAIN:
        "Paste not confirmed. Check the field before trying again; "
        "the result remains in Deck and History.",
    InsertionOutcome.SAVED_ONLY:
        "Saved in Deck and History only. Click the intended field, then use "
        "Copy or Paste latest.",
}

_REASON_MESSAGES = {
    InsertionReason.NO_TARGET.value:
        "Saved in Deck and History. Click an editable field, then use Paste latest.",
    InsertionReason.TARGET_CHANGED.value:
        "Not sent because the selected field changed. The result is saved in Deck and History.",
    InsertionReason.NO_FOCUS.value:
        "Not sent because no editable field is focused. The result is saved in Deck and History.",
    InsertionReason.NOT_EDITABLE.value:
        "Not sent because the focused control is not editable. The result is saved in Deck and History.",
    InsertionReason.READ_ONLY.value:
        "Not sent because the focused field is read-only. The result is saved in Deck and History.",
    InsertionReason.PROTECTED_FIELD.value:
        "Not sent because the focused field is protected. The result is saved in Deck and History.",
    InsertionReason.EDITABILITY_UNKNOWN.value:
        "Not sent because Mumble could not verify that the focused control is writable. The result is saved in Deck and History.",
    InsertionReason.HIGHER_INTEGRITY.value:
        "Not sent because the destination has higher Windows privileges. Copy the saved result from Deck and paste it manually.",
    InsertionReason.UNKNOWN_INTEGRITY.value:
        "Not sent because Mumble could not verify the Windows privilege boundary. Copy the saved result from Deck and paste it manually.",
    InsertionReason.HELD_MODIFIER.value:
        "Not sent because a Ctrl, Alt, Shift, or Windows key is still held. Release it, then use Paste latest.",
    InsertionReason.CLIPBOARD_UNSAFE.value:
        "Not sent because the current clipboard cannot be preserved safely. The result remains saved in Deck and History.",
    InsertionReason.CLIPBOARD_SNAPSHOT_FAILED.value:
        "Not sent because the clipboard could not be read safely. The result remains saved in Deck and History.",
    InsertionReason.CLIPBOARD_WRITE_FAILED.value:
        "Not sent because the clipboard stayed unavailable. The result remains saved in Deck and History.",
}


@dataclass(frozen=True)
class InsertionResult:
    operation_id: str
    source: str
    outcome: InsertionOutcome
    reason: str
    message: str
    send_count: int
    native_requested: int = 0
    native_accepted: Optional[int] = 0
    clipboard_restored: bool = False
    clipboard_changed_externally: bool = False
    cleanup_warning: str = ""
    target_lease: Optional[TargetLease] = None

    @property
    def confirmed(self) -> bool:
        return self.outcome is InsertionOutcome.CONFIRMED

    @property
    def sent(self) -> bool:
        return self.outcome in {
            InsertionOutcome.CONFIRMED,
            InsertionOutcome.SENT_UNCONFIRMED,
            InsertionOutcome.UNCERTAIN,
        }

    def as_dict(self):
        return {
            "operation_id": self.operation_id,
            "source": self.source,
            "outcome": self.outcome.value,
            "confirmed": self.confirmed,
            "reason": self.reason,
            "message": self.message,
            "send_count": self.send_count,
            "native_requested": self.native_requested,
            "native_accepted": self.native_accepted,
            "clipboard_restored": self.clipboard_restored,
            "clipboard_changed_externally": self.clipboard_changed_externally,
            "cleanup_warning": self.cleanup_warning,
        }


class InsertionRequestConflict(ValueError):
    """An operation ID was reused for a different immutable request."""


@dataclass
class _CoordinatorEntry:
    fingerprint: str
    request: InsertionRequest
    state: str
    event: threading.Event
    result: Optional[InsertionResult] = None
    error: Optional[BaseException] = None


class InsertionCoordinator:
    """Controller-owned pending and terminal operation identity registry."""

    def __init__(self, transaction, *, retention=256):
        self._transaction = transaction
        self._retention = max(8, int(retention))
        self._lock = threading.Lock()
        self._entries = OrderedDict()

    def _check(self, operation_id, fingerprint):
        entry = self._entries.get(operation_id)
        if entry is not None and entry.fingerprint != fingerprint:
            raise InsertionRequestConflict(
                "operation_id_reused_with_different_request")
        return entry

    def prepare(self, request: InsertionRequest):
        if not request.operation_id:
            raise ValueError("operation_id is required")
        fingerprint = request.fingerprint()
        with self._lock:
            entry = self._check(request.operation_id, fingerprint)
            if entry is None:
                entry = _CoordinatorEntry(
                    fingerprint=fingerprint,
                    request=request,
                    state="prepared",
                    event=threading.Event(),
                )
                self._entries[request.operation_id] = entry
                self._trim_locked()
            else:
                self._entries.move_to_end(request.operation_id)
            return self._status_locked(entry)

    def submit(self, request: InsertionRequest) -> InsertionResult:
        fingerprint = request.fingerprint()
        owner = False
        with self._lock:
            entry = self._check(request.operation_id, fingerprint)
            if entry is None:
                entry = _CoordinatorEntry(
                    fingerprint=fingerprint,
                    request=request,
                    state="pending",
                    event=threading.Event(),
                )
                self._entries[request.operation_id] = entry
                owner = True
            elif entry.state == "prepared":
                entry.state = "pending"
                owner = True
            elif entry.state == "terminal":
                if entry.error is not None:
                    raise entry.error
                return entry.result
            wait_event = entry.event

        if owner:
            try:
                result = self._transaction.insert(entry.request)
            except BaseException as exc:
                with self._lock:
                    entry.error = exc
                    entry.state = "terminal"
                    entry.event.set()
                    self._trim_locked()
                raise
            with self._lock:
                entry.result = result
                entry.state = "terminal"
                entry.event.set()
                self._entries.move_to_end(request.operation_id)
                self._trim_locked()
            return result

        wait_event.wait()
        with self._lock:
            if entry.error is not None:
                raise entry.error
            return entry.result

    def status(self, operation_id, fingerprint=None):
        with self._lock:
            entry = self._entries.get(str(operation_id or ""))
            if entry is None:
                return {"state": "missing", "operation_id": str(operation_id or "")}
            if fingerprint is not None and entry.fingerprint != fingerprint:
                raise InsertionRequestConflict(
                    "operation_id_reused_with_different_request")
            self._entries.move_to_end(str(operation_id))
            return self._status_locked(entry)

    def _status_locked(self, entry):
        if entry.state == "terminal" and entry.result is not None:
            values = entry.result.as_dict()
            values["state"] = "terminal"
            return values
        if entry.state == "terminal":
            return {
                "state": "terminal",
                "operation_id": entry.request.operation_id,
                "error": "internal_error",
            }
        return {
            "state": "pending",
            "operation_id": entry.request.operation_id,
        }

    def _trim_locked(self):
        while len(self._entries) > self._retention:
            removable = next(
                (key for key, value in self._entries.items()
                 if value.state == "terminal"), None)
            if removable is None:
                break
            self._entries.pop(removable, None)

class InsertionTransaction:
    """Serialize insertion attempts and cache terminal results by operation ID."""

    def __init__(
        self,
        target_adapter,
        clipboard_adapter,
        native_input_adapter,
        *,
        settle_delay: Callable[[float], None] = time.sleep,
        trace: Optional[Callable[..., None]] = None,
        cache_size: int = 256,
    ):
        self._target = target_adapter
        self._clipboard = clipboard_adapter
        self._native = native_input_adapter
        self._settle_delay = settle_delay
        self._trace = trace or (lambda *_args, **_kwargs: None)
        self._cache_size = max(8, int(cache_size))
        self._lock = threading.Lock()
        self._completed = OrderedDict()

    def insert(self, request: InsertionRequest) -> InsertionResult:
        if not request.operation_id:
            raise ValueError("operation_id is required")
        with self._lock:
            prior = self._completed.get(request.operation_id)
            if prior is not None:
                self._completed.move_to_end(request.operation_id)
                return prior
            target = ((request.target_lease.target
                       if request.target_lease is not None else None)
                      or request.activation_target)
            self._trace(
                "insertion_started",
                operation_id=request.operation_id,
                source=request.source,
                content_kind=request.content_kind,
                target_captured=target is not None,
                target_present=bool(target and target.window),
                focus_present=bool(target and target.focused_child),
                caret_present=bool(target and target.has_caret),
                editable=bool(
                    target and target.editability is TargetEditability.EDITABLE),
                integrity_relation=(target.integrity_relation
                                    if target else "unknown"),
                input_api="SendInput",
                payload_size_bucket=self._payload_size_bucket(request),
            )
            result = self._execute(request)
            self._completed[request.operation_id] = result
            while len(self._completed) > self._cache_size:
                self._completed.popitem(last=False)
            confirmation = {
                InsertionOutcome.CONFIRMED: "confirmed",
                InsertionOutcome.SENT_UNCONFIRMED: "unavailable",
                InsertionOutcome.NOT_SENT: "not_sent",
                InsertionOutcome.UNCERTAIN: "uncertain",
                InsertionOutcome.SAVED_ONLY: "not_sent",
            }[result.outcome]
            self._trace(
                "insertion_finished",
                operation_id=request.operation_id,
                source=request.source,
                content_kind=request.content_kind,
                requested_count=result.native_requested,
                accepted_count=result.native_accepted,
                send_count=result.send_count,
                outcome=result.outcome.value,
                confirmation=confirmation,
                fallback_reason=result.reason,
                cleanup_warning=bool(result.cleanup_warning),
            )
            return result

    @staticmethod
    def _payload_size_bucket(request):
        size = len(request.text.encode("utf-8")) if request.content_kind == "text" else 0
        if size == 0:
            return "none_or_image"
        if size <= 256:
            return "tiny"
        if size <= 4096:
            return "small"
        if size <= 65536:
            return "medium"
        return "large"

    def _result(self, request, outcome, reason, send_count=0,
                native_requested=0, native_accepted=0, **cleanup):
        reason_value = reason.value if isinstance(reason, Enum) else str(reason)
        return InsertionResult(
            operation_id=request.operation_id,
            source=request.source,
            outcome=outcome,
            reason=reason_value,
            message=_REASON_MESSAGES.get(reason_value, _MESSAGES[outcome]),
            send_count=send_count,
            native_requested=native_requested,
            native_accepted=native_accepted,
            target_lease=request.target_lease,
            **cleanup,
        )

    def _execute(self, request):
        lease = request.target_lease
        activation = ((lease.target if lease is not None else None)
                      or request.activation_target or self._target.current())
        if activation is None or not activation.window:
            return self._result(request, InsertionOutcome.SAVED_ONLY,
                                InsertionReason.NO_TARGET)

        current = self._target.current()
        if not activation.same_destination(current):
            may_restore = bool(
                lease is not None and lease.mumble_displaced_target)
            restored = may_restore and bool(request.restore_focus) and bool(
                self._target.restore(activation, request.focus_timeout_s))
            current = self._target.current()
            if not restored or not activation.same_destination(current):
                return self._result(request, InsertionOutcome.SAVED_ONLY,
                                    InsertionReason.TARGET_CHANGED)

        if not activation.focused_child:
            return self._result(request, InsertionOutcome.SAVED_ONLY,
                                InsertionReason.NO_FOCUS)
        if activation.protected:
            return self._result(request, InsertionOutcome.SAVED_ONLY,
                                InsertionReason.PROTECTED_FIELD)
        if activation.read_only:
            return self._result(request, InsertionOutcome.SAVED_ONLY,
                                InsertionReason.READ_ONLY)
        if activation.editability is TargetEditability.NOT_EDITABLE:
            return self._result(request, InsertionOutcome.SAVED_ONLY,
                                InsertionReason.NOT_EDITABLE)
        if activation.editability is not TargetEditability.EDITABLE:
            return self._result(request, InsertionOutcome.SAVED_ONLY,
                                InsertionReason.EDITABILITY_UNKNOWN)

        injectable = self._target.can_inject(activation)
        if injectable is False:
            return self._result(request, InsertionOutcome.SAVED_ONLY,
                                InsertionReason.HIGHER_INTEGRITY)
        if injectable is None:
            return self._result(request, InsertionOutcome.SAVED_ONLY,
                                InsertionReason.UNKNOWN_INTEGRITY)

        try:
            snapshot = self._clipboard.snapshot()
        except Exception:
            return self._result(request, InsertionOutcome.SAVED_ONLY,
                                InsertionReason.CLIPBOARD_SNAPSHOT_FAILED)
        if request.restore_clipboard and not snapshot.restorable:
            return self._result(
                request, InsertionOutcome.SAVED_ONLY,
                InsertionReason.CLIPBOARD_UNSAFE)

        try:
            ownership = self._clipboard.write(request, snapshot)
        except Exception:
            return self._result(request, InsertionOutcome.SAVED_ONLY,
                                InsertionReason.CLIPBOARD_WRITE_FAILED)
        self._trace("clipboard_ready", operation_id=request.operation_id,
                    source=request.source, content_kind=request.content_kind)

        outcome = InsertionOutcome.NOT_SENT
        reason = InsertionReason.ZERO_INPUT
        send_count = 0
        requested = 0
        accepted = 0
        clipboard_restored = False
        clipboard_changed = False
        cleanup_warning = ""
        try:
            # The target check is deliberately after the clipboard write and
            # immediately before native input.  A late focus switch therefore
            # cannot redirect the user's dictation to another field.
            current = self._target.current()
            if not activation.same_destination(current):
                return self._finish_clipboard(
                    request, snapshot, ownership,
                    self._result(request, InsertionOutcome.SAVED_ONLY,
                                 InsertionReason.TARGET_CHANGED))

            ready, _ready_reason = self._native.ready(request.modifier_timeout_s)
            if not ready:
                return self._finish_clipboard(
                    request, snapshot, ownership,
                    self._result(request, InsertionOutcome.NOT_SENT,
                                 InsertionReason.HELD_MODIFIER))

            if request.undo_before_paste:
                send_count = 1
                try:
                    undo = self._native.send_undo()
                except Exception:
                    return self._finish_clipboard(
                        request, snapshot, ownership,
                        self._result(
                            request, InsertionOutcome.UNCERTAIN,
                            InsertionReason.UNDO_RESULT_UNKNOWN,
                            send_count=send_count,
                            native_requested=4, native_accepted=None))
                undo_requested = max(0, int(undo.requested))
                undo_accepted = undo.accepted
                if (not undo.submitted or undo_accepted != undo_requested
                        or undo_requested <= 0):
                    undo_outcome = (InsertionOutcome.UNCERTAIN
                                    if undo.submitted or (undo_accepted or 0) > 0
                                    else InsertionOutcome.NOT_SENT)
                    return self._finish_clipboard(
                        request, snapshot, ownership,
                        self._result(
                            request, undo_outcome,
                            InsertionReason.UNDO_NOT_FULLY_ACCEPTED,
                            send_count=send_count,
                            native_requested=undo_requested,
                            native_accepted=undo_accepted))
                requested += undo_requested
                accepted += undo_accepted
                if not activation.same_destination(self._target.current()):
                    return self._finish_clipboard(
                        request, snapshot, ownership,
                        self._result(
                            request, InsertionOutcome.UNCERTAIN,
                            InsertionReason.TARGET_CHANGED_AFTER_UNDO,
                            send_count=send_count,
                            native_requested=requested,
                            native_accepted=accepted))

            send_count += 1
            try:
                acceptance = self._native.send_paste()
            except Exception:
                outcome = InsertionOutcome.UNCERTAIN
                reason = InsertionReason.PASTE_RESULT_UNKNOWN
                accepted = None
            else:
                paste_requested = max(0, int(acceptance.requested))
                paste_accepted = acceptance.accepted
                requested += paste_requested
                accepted = (None if paste_accepted is None else
                            accepted + paste_accepted)
                if paste_accepted is not None and (
                        paste_accepted < 0 or paste_accepted > paste_requested):
                    outcome = InsertionOutcome.UNCERTAIN
                    reason = InsertionReason.INVALID_ACCEPTANCE
                elif (acceptance.submitted and paste_accepted == paste_requested
                      and paste_requested > 0):
                    if acceptance.confirmation is True:
                        outcome = InsertionOutcome.CONFIRMED
                        reason = InsertionReason.CONFIRMED
                    elif acceptance.confirmation is False:
                        outcome = (InsertionOutcome.UNCERTAIN
                                   if request.undo_before_paste
                                   else InsertionOutcome.NOT_SENT)
                        reason = InsertionReason.TARGET_REJECTED
                    else:
                        outcome = InsertionOutcome.SENT_UNCONFIRMED
                        reason = InsertionReason.CONFIRMATION_UNAVAILABLE
                elif acceptance.submitted and paste_accepted is None:
                    outcome = InsertionOutcome.SENT_UNCONFIRMED
                    reason = InsertionReason.CONFIRMATION_UNAVAILABLE
                elif (acceptance.submitted
                      or (paste_accepted is not None and paste_accepted > 0)):
                    outcome = InsertionOutcome.UNCERTAIN
                    reason = InsertionReason.PARTIAL_INPUT
                else:
                    outcome = InsertionOutcome.NOT_SENT
                    reason = InsertionReason.ZERO_INPUT

            self._trace("paste_sent", operation_id=request.operation_id,
                        source=request.source,
                        accepted_count=accepted, requested_count=requested,
                        send_count=send_count)
            if outcome in {
                InsertionOutcome.CONFIRMED,
                InsertionOutcome.SENT_UNCONFIRMED,
                InsertionOutcome.UNCERTAIN,
            }:
                self._settle_delay(max(0.0, request.settle_seconds))
        finally:
            if request.restore_clipboard:
                try:
                    if self._clipboard.still_owns(ownership):
                        clipboard_restored = bool(
                            self._clipboard.restore(snapshot, ownership))
                        if not clipboard_restored:
                            cleanup_warning = "The previous clipboard could not be restored."
                    else:
                        clipboard_changed = True
                        cleanup_warning = (
                            "The clipboard changed after Mumble wrote to it, so the newer "
                            "clipboard was left untouched.")
                except Exception:
                    cleanup_warning = "The previous clipboard could not be restored."

        return self._result(
            request, outcome, reason, send_count=send_count,
            native_requested=requested, native_accepted=accepted,
            clipboard_restored=clipboard_restored,
            clipboard_changed_externally=clipboard_changed,
            cleanup_warning=cleanup_warning,
        )

    def _finish_clipboard(self, request, snapshot, ownership, result):
        if not request.restore_clipboard:
            return result
        try:
            if not self._clipboard.still_owns(ownership):
                values = result.__dict__.copy()
                values.update(
                    clipboard_changed_externally=True,
                    cleanup_warning=(
                        "The clipboard changed after Mumble wrote to it, so the newer "
                        "clipboard was left untouched."),
                )
                return InsertionResult(**values)
            restored = bool(self._clipboard.restore(snapshot, ownership))
            values = result.__dict__.copy()
            values.update(
                clipboard_restored=restored,
                cleanup_warning="" if restored else
                    "The previous clipboard could not be restored.",
            )
            return InsertionResult(**values)
        except Exception:
            values = result.__dict__.copy()
            values["cleanup_warning"] = "The previous clipboard could not be restored."
            return InsertionResult(**values)
