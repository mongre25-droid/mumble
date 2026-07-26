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

from dictation_trace import validated_operation_id


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


class ClipboardRestoreState(str, Enum):
    RESTORED = "restored"
    NEWER_EXTERNAL = "newer_external"
    FAILED = "failed"


class AdapterAttemptState(str, Enum):
    NO_SEND = "no_send"
    MAY_HAVE_SENT = "may_have_sent"
    SENT = "sent"


class DeliveryIntent(str, Enum):
    INSERT = "insert"
    REPLACE = "replace"


INSERT = DeliveryIntent.INSERT


@dataclass(frozen=True)
class TextPayload:
    text: str


@dataclass(frozen=True)
class ImagePayload:
    path: str


@dataclass(frozen=True)
class RichPayload:
    text: str
    html: str = ""
    rtf: str = ""


class InsertionReason(str, Enum):
    CONFIRMED = "confirmed"
    CONFIRMATION_UNAVAILABLE = "confirmation_unavailable"
    NO_TARGET = "no_target"
    TARGET_CHANGED = "target_changed"
    TARGET_SAFETY_CHANGED = "target_safety_changed"
    NO_FOCUS = "no_focus"
    NOT_EDITABLE = "not_editable"
    READ_ONLY = "read_only"
    PROTECTED_FIELD = "protected_field"
    EDITABILITY_UNKNOWN = "editability_unknown"
    HIGHER_INTEGRITY = "higher_integrity"
    UNKNOWN_INTEGRITY = "unknown_integrity"
    PERMISSION_NEEDED = "permission_needed"
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
    process_creation_id: int = 0
    uia_runtime_id: Tuple[int, ...] = ()
    uia_observed: bool = False
    uia_state: str = "unavailable"

    def same_native_destination(self, other: Optional["TargetContext"]) -> bool:
        if other is None:
            return False
        if (
            self.window != other.window
            or self.process_id != other.process_id
            or self.thread_id != other.thread_id
            or self.focused_child != other.focused_child
        ):
            return False
        # Process creation time prevents a recycled PID/HWND from inheriting an
        # earlier lease.  One-sided unavailability is diagnostic rather than a
        # reason to refuse an otherwise stable native target.
        return not (
            self.process_creation_id
            and other.process_creation_id
            and self.process_creation_id != other.process_creation_id
        )

    def same_destination(self, other: Optional["TargetContext"]) -> bool:
        if not self.same_native_destination(other):
            return False
        # Observed focus that never settled is not absence: it cannot safely
        # authorize native input or exact restoration.
        if (
            self.uia_state == "unstable"
            or (other is not None and other.uia_state == "unstable")
        ):
            return False
        # UI Automation strengthens identity only when both observations are
        # reliable. Missing, timed-out, transient, or one-sided evidence never
        # turns a stable native destination into a refusal.
        return not (
            self.uia_observed
            and other is not None
            and other.uia_observed
            and bool(self.uia_runtime_id)
            and bool(other.uia_runtime_id)
            and self.uia_runtime_id != other.uia_runtime_id
        )


@dataclass(frozen=True)
class TargetLease:
    """Immutable ownership decision made at the caller's safe capture moment."""

    target: Optional[TargetContext]
    source: str
    capture_phase: str
    mumble_displaced_target: bool = False


@dataclass(frozen=True)
class OperationReceipt:
    operation_id: str
    source: str
    target_lease: TargetLease
    reuse_destination_from: Optional[str] = None

    @property
    def target(self):
        return self.target_lease.target


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
class ClipboardRestoreResult:
    state: ClipboardRestoreState

    @property
    def restored(self) -> bool:
        return self.state is ClipboardRestoreState.RESTORED

    @property
    def changed_externally(self) -> bool:
        return self.state is ClipboardRestoreState.NEWER_EXTERNAL

    def __bool__(self) -> bool:
        return self.restored


@dataclass(frozen=True)
class NativeAcceptance:
    requested: int
    accepted: Optional[int]
    submitted: bool
    confirmation: Optional[bool] = None
    error: str = ""


@dataclass(frozen=True)
class AdapterAttempt:
    adapter: str
    state: AdapterAttemptState
    requested: int = 0
    accepted: Optional[int] = 0
    reason: str = ""


class ElevatedHelperAdapter:
    """Private installed-helper seam; the immediate candidate ships none."""

    def available(self):
        return False

    def deliver(self, _request):
        raise RuntimeError("elevated_helper_unavailable")


@dataclass(frozen=True)
class InsertionRequest:
    operation_id: str
    source: str
    content_kind: str
    activation_target: Optional[TargetContext]
    target_lease: Optional[TargetLease] = None
    text: str = ""
    image_path: str = ""
    rich_html: str = ""
    rich_rtf: str = ""
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
        values["rich_html"] = hashlib.sha256(
            str(values.pop("rich_html", "")).encode("utf-8")).hexdigest()
        values["rich_rtf"] = hashlib.sha256(
            str(values.pop("rich_rtf", "")).encode("utf-8")).hexdigest()
        payload = json.dumps(
            values, sort_keys=True, separators=(",", ":"),
            default=lambda value: "<{}.{}>".format(
                type(value).__module__, type(value).__qualname__),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_MESSAGES = {
    InsertionOutcome.CONFIRMED:
        "Inserted. The result also remains in Deck and History.",
    InsertionOutcome.SENT_UNCONFIRMED:
        "Sent — check the selected destination. The result remains in Deck and History.",
    InsertionOutcome.NOT_SENT:
        "Not inserted — saved in Mumble. Use Copy or Paste latest from Deck or History.",
    InsertionOutcome.UNCERTAIN:
        "Delivery uncertain — check the selected destination before trying again; "
        "the result remains in Deck and History.",
    InsertionOutcome.SAVED_ONLY:
        "Not inserted — saved in Mumble. Select the destination, then use "
        "Copy or Paste latest.",
}

_REASON_MESSAGES = {
    InsertionReason.NO_TARGET.value:
        "Saved in Deck and History. Click an editable field, then use Paste latest.",
    InsertionReason.TARGET_CHANGED.value:
        "Not sent because the selected field changed. The result is saved in Deck and History.",
    InsertionReason.TARGET_SAFETY_CHANGED.value:
        "Not sent because the selected field's safety state changed. The result is saved in Deck and History.",
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
    InsertionReason.PERMISSION_NEEDED.value:
        "Permission needed — saved in Mumble.",
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
    attempt_ledger: Tuple[AdapterAttempt, ...] = ()
    state: str = "terminal"

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
            "state": self.state,
            "attempt_ledger": [
                {
                    "adapter": attempt.adapter,
                    "state": attempt.state.value,
                    "requested": attempt.requested,
                    "accepted": attempt.accepted,
                    "reason": attempt.reason,
                }
                for attempt in self.attempt_ledger
            ],
        }


DeliveryResult = InsertionResult


class InsertionRequestConflict(ValueError):
    """An operation ID was reused for a different immutable request."""


class ClipboardWriteFailure(RuntimeError):
    """A failed replacement with explicit, non-content-bearing recovery truth."""

    def __init__(self, reason, *, clipboard_restored=False,
                 clipboard_changed_externally=False, cleanup_warning=""):
        super().__init__(str(reason or "clipboard_write_failed"))
        self.clipboard_restored = bool(clipboard_restored)
        self.clipboard_changed_externally = bool(
            clipboard_changed_externally)
        self.cleanup_warning = str(cleanup_warning or "")


class InsertionWaitTimeout(TimeoutError):
    """A same-ID observer stopped waiting; the owner may still finish."""


class InsertionOperationExpired(RuntimeError):
    """A prepared operation expired and cannot later be submitted."""


class InsertionCoordinatorCapacityError(RuntimeError):
    """The bounded identity registry is full of non-evictable operations."""


class OperationIdReplayGuard:
    """Fixed-memory, fail-closed memory of operation identities seen this run.

    Mumble operation IDs are random 32-hex values scoped to one authenticated
    controller session.  This compact guard keeps replay protection after exact
    terminal records are trimmed.  A hash collision can only reject a new
    operation; it can never authorize replay.
    """

    def __init__(self, bit_count=1 << 20, hash_count=5):
        self._bit_count = max(1024, int(bit_count))
        self._hash_count = max(2, min(8, int(hash_count)))
        self._bits = bytearray((self._bit_count + 7) // 8)

    def _indexes(self, operation_id):
        digest = hashlib.blake2b(
            operation_id.encode("ascii"), digest_size=32,
            person=b"mumble-op-id").digest()
        for index in range(self._hash_count):
            offset = (index * 4) % len(digest)
            value = int.from_bytes(digest[offset:offset + 4], "little")
            yield value % self._bit_count

    def remember(self, operation_id):
        for index in self._indexes(operation_id):
            self._bits[index // 8] |= 1 << (index % 8)

    def __contains__(self, operation_id):
        return all(
            self._bits[index // 8] & (1 << (index % 8))
            for index in self._indexes(operation_id))


@dataclass
class _CoordinatorEntry:
    fingerprint: str
    request: InsertionRequest
    state: str
    event: threading.Event
    result: Optional[InsertionResult] = None
    error: Optional[BaseException] = None
    created_at: float = 0.0
    state_changed_at: float = 0.0


class InsertionCoordinator:
    """Controller-owned pending and terminal operation identity registry."""

    def __init__(self, transaction, *, retention=256, clock=time.monotonic,
                 join_timeout_s=4.0, prepared_ttl_s=30.0,
                 pending_ttl_s=30.0):
        self._transaction = transaction
        self._retention = max(8, int(retention))
        self._clock = clock
        self._join_timeout_s = max(0.0, float(join_timeout_s))
        self._prepared_ttl_s = max(0.0, float(prepared_ttl_s))
        self._pending_ttl_s = max(0.0, float(pending_ttl_s))
        self._lock = threading.Lock()
        self._entries = OrderedDict()
        self._retired = OrderedDict()
        self._seen_operation_ids = OperationIdReplayGuard()

    @staticmethod
    def _require_operation_id(value):
        operation_id = validated_operation_id(value)
        if operation_id is None:
            raise ValueError("invalid_operation_id")
        return operation_id

    def _check(self, operation_id, fingerprint):
        entry = (self._entries.get(operation_id)
                 or self._retired.get(operation_id))
        if entry is not None and entry.fingerprint != fingerprint:
            raise InsertionRequestConflict(
                "operation_id_reused_with_different_request")
        return entry

    def prepare(self, request: InsertionRequest):
        operation_id = self._require_operation_id(request.operation_id)
        fingerprint = request.fingerprint()
        with self._lock:
            now = self._clock()
            self._expire_locked(now)
            entry = self._check(operation_id, fingerprint)
            if entry is None:
                if operation_id in self._seen_operation_ids:
                    raise InsertionOperationExpired(
                        "operation identity is no longer reusable")
                self._reserve_slot_locked()
                entry = _CoordinatorEntry(
                    fingerprint=fingerprint,
                    request=request,
                    state="prepared",
                    event=threading.Event(),
                    created_at=now,
                    state_changed_at=now,
                )
                self._seen_operation_ids.remember(operation_id)
                self._entries[operation_id] = entry
            else:
                if operation_id in self._entries:
                    self._entries.move_to_end(operation_id)
                else:
                    self._retired.move_to_end(operation_id)
            return self._status_locked(entry)

    def submit(self, request: InsertionRequest) -> InsertionResult:
        operation_id = self._require_operation_id(request.operation_id)
        fingerprint = request.fingerprint()
        owner = False
        with self._lock:
            now = self._clock()
            self._expire_locked(now)
            entry = self._check(operation_id, fingerprint)
            if entry is None:
                if operation_id in self._seen_operation_ids:
                    raise InsertionOperationExpired(
                        "operation identity is no longer reusable")
                self._reserve_slot_locked()
                entry = _CoordinatorEntry(
                    fingerprint=fingerprint,
                    request=request,
                    state="pending",
                    event=threading.Event(),
                    created_at=now,
                    state_changed_at=now,
                )
                self._seen_operation_ids.remember(operation_id)
                self._entries[operation_id] = entry
                owner = True
            elif entry.state == "prepared":
                entry.state = "pending"
                entry.state_changed_at = now
                owner = True
            elif entry.state == "terminal":
                if entry.error is not None:
                    raise entry.error
                return entry.result
            elif entry.state == "expired":
                raise InsertionOperationExpired(
                    "prepared insertion operation expired")
            elif entry.state == "unknown":
                raise InsertionWaitTimeout(
                    "insertion operation is still unresolved; do not retry")
            wait_event = entry.event

        if owner:
            try:
                result = self._transaction.insert(entry.request)
            except BaseException as exc:
                with self._lock:
                    entry.error = exc
                    entry.state = "terminal"
                    entry.state_changed_at = self._clock()
                    entry.event.set()
                    self._trim_locked()
                raise
            with self._lock:
                entry.result = result
                entry.state = "terminal"
                entry.state_changed_at = self._clock()
                entry.event.set()
                self._entries.move_to_end(operation_id)
                self._trim_locked()
            return result

        if not wait_event.wait(self._join_timeout_s):
            raise InsertionWaitTimeout(
                "timed out waiting for the existing insertion operation")
        with self._lock:
            self._expire_locked(self._clock())
            if entry.state == "expired":
                raise InsertionOperationExpired(
                    "prepared insertion operation expired")
            if entry.state == "unknown":
                raise InsertionWaitTimeout(
                    "insertion operation is still unresolved; do not retry")
            if entry.error is not None:
                raise entry.error
            return entry.result

    def status(self, operation_id, fingerprint=None):
        operation_id = self._require_operation_id(operation_id)
        with self._lock:
            self._expire_locked(self._clock())
            entry = self._entries.get(operation_id)
            if entry is None:
                entry = self._retired.get(operation_id)
            if entry is None:
                if operation_id in self._seen_operation_ids:
                    return {
                        "state": "expired",
                        "operation_id": operation_id,
                        "outcome": "unknown",
                        "confirmed": False,
                        "reason": "operation_identity_retired",
                        "message": (
                            "This paste operation is no longer reusable. Begin "
                            "a new Deck action instead of retrying it."),
                    }
                return {"state": "missing", "operation_id": operation_id}
            if fingerprint is not None and entry.fingerprint != fingerprint:
                raise InsertionRequestConflict(
                    "operation_id_reused_with_different_request")
            if operation_id in self._entries:
                self._entries.move_to_end(operation_id)
            else:
                self._retired.move_to_end(operation_id)
            return self._status_locked(entry)

    def abandon(self, operation_id):
        """Retire a not-yet-started identity without authorising later reuse.

        An executing transaction is never interrupted. Repeated abandonment is
        idempotent, including after the exact retired record has been trimmed.
        """
        operation_id = self._require_operation_id(operation_id)
        with self._lock:
            self._expire_locked(self._clock())
            entry = (self._entries.get(operation_id)
                     or self._retired.get(operation_id))
            if entry is None:
                self._seen_operation_ids.remember(operation_id)
                return {
                    "state": "expired",
                    "operation_id": operation_id,
                    "outcome": "unknown",
                    "confirmed": False,
                    "reason": "operation_identity_retired",
                    "message": (
                        "This paste operation is no longer reusable. Begin "
                        "a new Deck action instead of retrying it."),
                }
            if entry.state == "prepared":
                entry.state = "expired"
                entry.state_changed_at = self._clock()
                entry.event.set()
                self._entries.pop(operation_id, None)
                self._retired[operation_id] = entry
                self._retired.move_to_end(operation_id)
                while len(self._retired) > self._retention:
                    self._retired.popitem(last=False)
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
        if entry.state == "expired":
            return {
                "state": "expired",
                "operation_id": entry.request.operation_id,
                "outcome": "unknown",
                "confirmed": False,
                "reason": "prepared_operation_expired",
                "message": (
                    "This prepared paste expired before it started. Choose the "
                    "destination again and begin a new Deck action."),
            }
        if entry.state == "unknown":
            return {
                "state": "unknown",
                "operation_id": entry.request.operation_id,
                "outcome": "unknown",
                "confirmed": False,
                "reason": "pending_operation_expired",
                "message": (
                    "The paste result is still unknown. Do not retry; check the "
                    "selected field before using the saved result."),
            }
        return {
            "state": "pending",
            "operation_id": entry.request.operation_id,
        }

    def _expire_locked(self, now):
        expired_prepared = []
        for operation_id, entry in list(self._entries.items()):
            age = max(0.0, now - entry.state_changed_at)
            if entry.state == "prepared" and age >= self._prepared_ttl_s:
                entry.state = "expired"
                entry.state_changed_at = now
                entry.event.set()
                expired_prepared.append((operation_id, entry))
            elif entry.state == "pending" and age >= self._pending_ttl_s:
                # The worker cannot be killed safely. Tombstone the identity so
                # observers stop waiting and no later caller can submit it again.
                entry.state = "unknown"
                entry.state_changed_at = now
                entry.event.set()
        for operation_id, entry in expired_prepared:
            self._entries.pop(operation_id, None)
            self._retired[operation_id] = entry
            self._retired.move_to_end(operation_id)
        while len(self._retired) > self._retention:
            self._retired.popitem(last=False)

    def _reserve_slot_locked(self):
        self._trim_locked(limit=self._retention - 1)
        if len(self._entries) >= self._retention:
            raise InsertionCoordinatorCapacityError(
                "insertion coordinator capacity is occupied by unresolved identities")

    def _trim_locked(self, *, limit=None):
        limit = self._retention if limit is None else max(0, int(limit))
        while len(self._entries) > limit:
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
        elevated_helper=None,
        settle_delay: Callable[[float], None] = time.sleep,
        trace: Optional[Callable[..., None]] = None,
        cache_size: int = 256,
    ):
        self._target = target_adapter
        self._clipboard = clipboard_adapter
        self._native = native_input_adapter
        self._elevated_helper = elevated_helper
        self._settle_delay = settle_delay
        self._trace = trace or (lambda *_args, **_kwargs: None)
        self._cache_size = max(8, int(cache_size))
        self._lock = threading.Lock()
        self._completed = OrderedDict()
        self._seen_operation_ids = OperationIdReplayGuard()

    def insert(self, request: InsertionRequest) -> InsertionResult:
        operation_id = validated_operation_id(request.operation_id)
        if operation_id is None:
            raise ValueError("invalid_operation_id")
        with self._lock:
            prior = self._completed.get(operation_id)
            if prior is not None:
                self._completed.move_to_end(operation_id)
                return prior
            if operation_id in self._seen_operation_ids:
                raise InsertionOperationExpired(
                    "operation identity is no longer reusable")
            self._seen_operation_ids.remember(operation_id)
            target = ((request.target_lease.target
                       if request.target_lease is not None else None)
                      or request.activation_target)
            self._trace_safely(
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
            result = None
            try:
                result = self._execute(request)
                self._completed[request.operation_id] = result
                while len(self._completed) > self._cache_size:
                    self._completed.popitem(last=False)
                return result
            finally:
                if result is None:
                    terminal_fields = {
                        "requested_count": None,
                        "accepted_count": None,
                        "send_count": 0,
                        "outcome": "unknown",
                        "confirmation": "uncertain",
                        "fallback_reason": "internal_error",
                        "cleanup_warning": False,
                    }
                else:
                    confirmation = {
                        InsertionOutcome.CONFIRMED: "confirmed",
                        InsertionOutcome.SENT_UNCONFIRMED: "unavailable",
                        InsertionOutcome.NOT_SENT: "not_sent",
                        InsertionOutcome.UNCERTAIN: "uncertain",
                        InsertionOutcome.SAVED_ONLY: "not_sent",
                    }[result.outcome]
                    terminal_fields = {
                        "requested_count": result.native_requested,
                        "accepted_count": result.native_accepted,
                        "send_count": result.send_count,
                        "outcome": result.outcome.value,
                        "confirmation": confirmation,
                        "fallback_reason": result.reason,
                        "cleanup_warning": bool(result.cleanup_warning),
                    }
                self._trace_safely(
                    "insertion_finished",
                    operation_id=request.operation_id,
                    source=request.source,
                    content_kind=request.content_kind,
                    **terminal_fields,
                )

    def _trace_safely(self, name, **fields):
        """Diagnostics must never change insertion results or exceptions."""
        try:
            return self._trace(name, **fields)
        except Exception:
            return None

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

    @staticmethod
    def _prepend_attempt(result, attempt):
        values = result.__dict__.copy()
        values["attempt_ledger"] = (attempt,) + result.attempt_ledger
        return InsertionResult(**values)

    def _unicode_fallback(self, request, reason):
        """Try text-only Unicode input after a proven clipboard no-send."""
        if request.content_kind != "text" or not hasattr(
                self._native, "send_unicode"):
            return self._result(
                request, InsertionOutcome.SAVED_ONLY, reason,
                attempt_ledger=(AdapterAttempt(
                    "clipboard_paste", AdapterAttemptState.NO_SEND,
                    reason=(reason.value if isinstance(reason, Enum)
                            else str(reason))),),
            )
        current, target_reason = self._fresh_target_reason(
            (request.target_lease.target if request.target_lease else None)
            or request.activation_target, request)
        if target_reason is not None:
            if target_reason is InsertionReason.HIGHER_INTEGRITY:
                elevated = self._deliver_elevated(request)
                values = elevated.__dict__.copy()
                values["attempt_ledger"] = (
                    AdapterAttempt(
                        "clipboard_paste", AdapterAttemptState.NO_SEND,
                        reason=(reason.value if isinstance(reason, Enum)
                                else str(reason))),
                ) + elevated.attempt_ledger
                return InsertionResult(**values)
            return self._result(
                request, InsertionOutcome.SAVED_ONLY, target_reason,
                attempt_ledger=(AdapterAttempt(
                    "clipboard_paste", AdapterAttemptState.NO_SEND,
                    reason=(reason.value if isinstance(reason, Enum)
                            else str(reason))),),
            )
        ready, _ready_reason = self._native.ready(request.modifier_timeout_s)
        if not ready:
            return self._result(
                request, InsertionOutcome.NOT_SENT,
                InsertionReason.HELD_MODIFIER,
                attempt_ledger=(AdapterAttempt(
                    "clipboard_paste", AdapterAttemptState.NO_SEND,
                    reason=(reason.value if isinstance(reason, Enum)
                            else str(reason))),),
            )
        try:
            acceptance = self._native.send_unicode(request.text)
        except Exception:
            return self._result(
                request, InsertionOutcome.UNCERTAIN,
                InsertionReason.PASTE_RESULT_UNKNOWN,
                send_count=1, native_requested=max(0, len(request.text) * 2),
                native_accepted=None,
                attempt_ledger=(
                    AdapterAttempt(
                        "clipboard_paste", AdapterAttemptState.NO_SEND,
                        reason=(reason.value if isinstance(reason, Enum)
                                else str(reason))),
                    AdapterAttempt(
                        "unicode_text", AdapterAttemptState.MAY_HAVE_SENT,
                        requested=max(0, len(request.text) * 2),
                        accepted=None, reason="transport_error"),
                ),
            )
        requested = max(0, int(acceptance.requested))
        accepted = acceptance.accepted
        if accepted is not None and (accepted < 0 or accepted > requested):
            outcome = InsertionOutcome.UNCERTAIN
            terminal_reason = InsertionReason.INVALID_ACCEPTANCE
            state = AdapterAttemptState.MAY_HAVE_SENT
        elif accepted is not None and accepted > 0:
            complete = accepted == requested and requested > 0
            outcome = (InsertionOutcome.SENT_UNCONFIRMED if complete
                       else InsertionOutcome.UNCERTAIN)
            terminal_reason = (InsertionReason.CONFIRMATION_UNAVAILABLE if complete
                               else InsertionReason.PARTIAL_INPUT)
            state = (AdapterAttemptState.SENT if complete
                     else AdapterAttemptState.MAY_HAVE_SENT)
        elif acceptance.submitted:
            outcome = InsertionOutcome.SENT_UNCONFIRMED
            terminal_reason = InsertionReason.CONFIRMATION_UNAVAILABLE
            state = AdapterAttemptState.MAY_HAVE_SENT
        else:
            outcome = InsertionOutcome.NOT_SENT
            terminal_reason = InsertionReason.ZERO_INPUT
            state = AdapterAttemptState.NO_SEND
        return self._result(
            request, outcome, terminal_reason, send_count=1,
            native_requested=requested, native_accepted=accepted,
            attempt_ledger=(
                AdapterAttempt(
                    "clipboard_paste", AdapterAttemptState.NO_SEND,
                    reason=(reason.value if isinstance(reason, Enum)
                            else str(reason))),
                AdapterAttempt(
                    "unicode_text", state, requested=requested,
                    accepted=accepted, reason=terminal_reason.value),
            ),
        )

    def _target_safety_reason(self, target):
        """Return only conditions that prevent a safe delivery attempt.

        Control type, caret, editability, read-only, and protected signals are
        diagnostics for adapter selection.  They are not global eligibility
        gates because modern browser, Electron, Office, and rich-edit surfaces
        often expose incomplete or misleading Win32 control metadata.
        """
        if target is None or not target.window:
            return InsertionReason.NO_TARGET
        injectable = self._target.can_inject(target)
        if injectable is False:
            return InsertionReason.HIGHER_INTEGRITY
        return None

    def _deliver_elevated(self, request):
        helper = self._elevated_helper
        try:
            available = bool(helper and helper.available())
        except Exception:
            available = False
        if not available:
            return self._result(
                request, InsertionOutcome.NOT_SENT,
                InsertionReason.PERMISSION_NEEDED,
                attempt_ledger=(AdapterAttempt(
                    "elevated_helper", AdapterAttemptState.NO_SEND,
                    reason=InsertionReason.PERMISSION_NEEDED.value),),
            )
        try:
            acceptance = helper.deliver(request)
        except Exception:
            return self._result(
                request, InsertionOutcome.UNCERTAIN,
                InsertionReason.PASTE_RESULT_UNKNOWN,
                send_count=1, native_accepted=None,
                attempt_ledger=(AdapterAttempt(
                    "elevated_helper", AdapterAttemptState.MAY_HAVE_SENT,
                    accepted=None, reason="transport_lost_after_dispatch"),),
            )
        requested = max(0, int(acceptance.requested))
        accepted = acceptance.accepted
        if accepted is not None and (accepted < 0 or accepted > requested):
            return self._result(
                request, InsertionOutcome.UNCERTAIN,
                InsertionReason.INVALID_ACCEPTANCE,
                send_count=1, native_requested=requested,
                native_accepted=accepted,
                attempt_ledger=(AdapterAttempt(
                    "elevated_helper", AdapterAttemptState.MAY_HAVE_SENT,
                    requested=requested, accepted=accepted,
                    reason=InsertionReason.INVALID_ACCEPTANCE.value),),
            )
        if accepted is not None and accepted > 0:
            complete = accepted == requested and requested > 0
            confirmed = complete and acceptance.confirmation is True
            return self._result(
                request, (InsertionOutcome.CONFIRMED if confirmed else
                          InsertionOutcome.SENT_UNCONFIRMED if complete else
                          InsertionOutcome.UNCERTAIN),
                (InsertionReason.CONFIRMED if confirmed else
                 InsertionReason.CONFIRMATION_UNAVAILABLE if complete else
                 InsertionReason.PARTIAL_INPUT),
                send_count=1, native_requested=requested,
                native_accepted=accepted,
                attempt_ledger=(AdapterAttempt(
                    "elevated_helper",
                    (AdapterAttemptState.SENT if complete else
                     AdapterAttemptState.MAY_HAVE_SENT),
                    requested=requested, accepted=accepted),),
            )
        if acceptance.submitted:
            return self._result(
                request, InsertionOutcome.UNCERTAIN,
                InsertionReason.PASTE_RESULT_UNKNOWN,
                send_count=1, native_requested=requested,
                native_accepted=accepted,
                attempt_ledger=(AdapterAttempt(
                    "elevated_helper", AdapterAttemptState.MAY_HAVE_SENT,
                    requested=requested, accepted=accepted,
                    reason=InsertionReason.PASTE_RESULT_UNKNOWN.value),),
            )
        return self._result(
            request, InsertionOutcome.NOT_SENT,
            InsertionReason.PERMISSION_NEEDED,
            send_count=1, native_requested=requested,
            native_accepted=accepted,
            attempt_ledger=(AdapterAttempt(
                "elevated_helper", AdapterAttemptState.NO_SEND,
                requested=requested, accepted=accepted,
                reason=InsertionReason.PERMISSION_NEEDED.value),),
        )

    def _restore_changed_target(self, request, activation, current):
        uia_conflict = bool(
            activation.same_native_destination(current)
            and activation.uia_observed
            and current is not None
            and current.uia_observed
            and activation.uia_runtime_id
            and current.uia_runtime_id
            and activation.uia_runtime_id != current.uia_runtime_id
        )
        deck_source = request.source in {
            "deck_history", "deck_image", "deck_job"
        }
        lease = request.target_lease
        may_restore = bool(
            not deck_source or
            (lease is not None and lease.mumble_displaced_target))
        restored = may_restore and bool(request.restore_focus) and bool(
            self._target.restore(activation, request.focus_timeout_s))
        current = self._target.current()
        exact_restoration = activation.same_destination(current)
        if uia_conflict:
            exact_restoration = bool(
                current is not None
                and activation.same_native_destination(current)
                and activation.uia_observed
                and current.uia_observed
                and activation.uia_runtime_id == current.uia_runtime_id
            )
        return current, bool(restored and exact_restoration)

    def _fresh_target_reason(self, activation, request=None):
        current = self._target.current()
        if not activation.same_destination(current):
            if request is None:
                return current, InsertionReason.TARGET_CHANGED
            current, restored = self._restore_changed_target(
                request, activation, current)
            if not restored:
                return current, InsertionReason.TARGET_CHANGED
        reason = self._target_safety_reason(current)
        if reason is not None:
            return current, reason
        return current, None

    def _execute(self, request):
        lease = request.target_lease
        activation = ((lease.target if lease is not None else None)
                      or request.activation_target or self._target.current())
        if activation is None or not activation.window:
            return self._result(request, InsertionOutcome.SAVED_ONLY,
                                InsertionReason.NO_TARGET)

        current, target_reason = self._fresh_target_reason(
            activation, request)
        if target_reason is InsertionReason.TARGET_CHANGED:
            return self._result(request, InsertionOutcome.SAVED_ONLY,
                                InsertionReason.TARGET_CHANGED)

        safety_reason = target_reason
        if safety_reason is not None:
            if safety_reason is InsertionReason.HIGHER_INTEGRITY:
                return self._deliver_elevated(request)
            return self._result(request, InsertionOutcome.SAVED_ONLY,
                                safety_reason)

        try:
            snapshot = self._clipboard.snapshot()
        except Exception:
            return self._unicode_fallback(
                request, InsertionReason.CLIPBOARD_SNAPSHOT_FAILED)
        if request.restore_clipboard and not snapshot.restorable:
            return self._unicode_fallback(
                request, InsertionReason.CLIPBOARD_UNSAFE)

        try:
            ownership = self._clipboard.write(request, snapshot)
        except Exception as exc:
            result = self._unicode_fallback(
                request, InsertionReason.CLIPBOARD_WRITE_FAILED)
            values = result.__dict__.copy()
            values.update(
                clipboard_restored=bool(getattr(
                    exc, "clipboard_restored", False)),
                clipboard_changed_externally=bool(getattr(
                    exc, "clipboard_changed_externally", False)),
                cleanup_warning=str(getattr(exc, "cleanup_warning", "") or ""),
            )
            return InsertionResult(**values)
        self._trace_safely(
            "clipboard_ready", operation_id=request.operation_id,
            source=request.source, content_kind=request.content_kind)

        outcome = InsertionOutcome.NOT_SENT
        reason = InsertionReason.ZERO_INPUT
        send_count = 0
        requested = 0
        accepted = 0
        clipboard_restored = False
        clipboard_changed = False
        cleanup_warning = ""
        clipboard_cleanup_done = False
        attempt_ledger = []

        def finish_early(result):
            nonlocal clipboard_cleanup_done
            clipboard_cleanup_done = True
            return self._finish_clipboard(
                request, snapshot, ownership, result)

        try:
            # The target check is deliberately after the clipboard write and
            # immediately before native input.  A late focus switch therefore
            # cannot redirect the user's dictation to another field.
            current, safety_reason = self._fresh_target_reason(
                activation, request)
            if safety_reason is not None:
                result = (self._deliver_elevated(request)
                          if safety_reason is InsertionReason.HIGHER_INTEGRITY
                          else self._result(
                              request, InsertionOutcome.SAVED_ONLY,
                              safety_reason))
                result = self._prepend_attempt(result, AdapterAttempt(
                    "clipboard_paste", AdapterAttemptState.NO_SEND,
                    reason=safety_reason.value))
                return finish_early(result)

            ready, _ready_reason = self._native.ready(request.modifier_timeout_s)
            if not ready:
                result = self._result(
                    request, InsertionOutcome.NOT_SENT,
                    InsertionReason.HELD_MODIFIER)
                result = self._prepend_attempt(result, AdapterAttempt(
                    "clipboard_paste", AdapterAttemptState.NO_SEND,
                    reason=InsertionReason.HELD_MODIFIER.value))
                return finish_early(result)

            # Modifier release can itself consume the focus window. The last
            # authorization therefore uses a new complete target snapshot at
            # the native-input boundary, not facts retained from activation.
            current, safety_reason = self._fresh_target_reason(
                activation, request)
            if safety_reason is not None:
                result = (self._deliver_elevated(request)
                          if safety_reason is InsertionReason.HIGHER_INTEGRITY
                          else self._result(
                              request, InsertionOutcome.SAVED_ONLY,
                              safety_reason))
                result = self._prepend_attempt(result, AdapterAttempt(
                    "clipboard_paste", AdapterAttemptState.NO_SEND,
                    reason=safety_reason.value))
                return finish_early(result)

            if request.undo_before_paste:
                send_count = 1
                try:
                    undo = self._native.send_undo()
                except Exception:
                    return finish_early(self._result(
                        request, InsertionOutcome.UNCERTAIN,
                        InsertionReason.UNDO_RESULT_UNKNOWN,
                        send_count=send_count,
                        native_requested=4, native_accepted=None,
                        attempt_ledger=(AdapterAttempt(
                            "native_undo", AdapterAttemptState.MAY_HAVE_SENT,
                            requested=4, accepted=None,
                            reason=InsertionReason.UNDO_RESULT_UNKNOWN.value),)))
                undo_requested = max(0, int(undo.requested))
                undo_accepted = undo.accepted
                if (not undo.submitted or undo_accepted != undo_requested
                        or undo_requested <= 0):
                    undo_outcome = (InsertionOutcome.UNCERTAIN
                                    if undo.submitted or (undo_accepted or 0) > 0
                                    else InsertionOutcome.NOT_SENT)
                    return finish_early(self._result(
                        request, undo_outcome,
                        InsertionReason.UNDO_NOT_FULLY_ACCEPTED,
                        send_count=send_count,
                        native_requested=undo_requested,
                        native_accepted=undo_accepted,
                        attempt_ledger=(AdapterAttempt(
                            "native_undo",
                            (AdapterAttemptState.MAY_HAVE_SENT
                             if undo.submitted or (undo_accepted or 0) > 0
                             else AdapterAttemptState.NO_SEND),
                            requested=undo_requested,
                            accepted=undo_accepted,
                            reason=InsertionReason.UNDO_NOT_FULLY_ACCEPTED.value),)))
                requested += undo_requested
                accepted += undo_accepted
                attempt_ledger.append(AdapterAttempt(
                    "native_undo", AdapterAttemptState.SENT,
                    requested=undo_requested, accepted=undo_accepted))
                _after_undo, after_undo_reason = self._fresh_target_reason(
                    activation, request)
                if after_undo_reason is not None:
                    return finish_early(self._result(
                        request, InsertionOutcome.UNCERTAIN,
                        InsertionReason.TARGET_CHANGED_AFTER_UNDO,
                        send_count=send_count,
                        native_requested=requested,
                        native_accepted=accepted,
                        attempt_ledger=tuple(attempt_ledger)))

            send_count += 1
            try:
                acceptance = self._native.send_paste()
            except Exception:
                outcome = InsertionOutcome.UNCERTAIN
                reason = InsertionReason.PASTE_RESULT_UNKNOWN
                accepted = None
                attempt_ledger.append(AdapterAttempt(
                    "clipboard_paste", AdapterAttemptState.MAY_HAVE_SENT,
                    requested=4, accepted=None,
                    reason=InsertionReason.PASTE_RESULT_UNKNOWN.value))
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

                if outcome in {
                    InsertionOutcome.CONFIRMED,
                    InsertionOutcome.SENT_UNCONFIRMED,
                }:
                    attempt_state = AdapterAttemptState.SENT
                elif outcome is InsertionOutcome.UNCERTAIN:
                    attempt_state = AdapterAttemptState.MAY_HAVE_SENT
                else:
                    attempt_state = AdapterAttemptState.NO_SEND
                attempt_ledger.append(AdapterAttempt(
                    "clipboard_paste", attempt_state,
                    requested=paste_requested, accepted=paste_accepted,
                    reason=reason.value))

            self._trace_safely(
                "paste_sent", operation_id=request.operation_id,
                source=request.source,
                accepted_count=accepted, requested_count=requested,
                send_count=send_count)
            if (outcome is InsertionOutcome.NOT_SENT
                    and reason is InsertionReason.ZERO_INPUT
                    and request.content_kind == "text"):
                fallback = self._unicode_fallback(
                    request, InsertionReason.ZERO_INPUT)
                values = fallback.__dict__.copy()
                values.update(
                    send_count=send_count + fallback.send_count,
                    native_requested=requested + fallback.native_requested,
                    native_accepted=(
                        None if fallback.native_accepted is None else
                        (accepted or 0) + fallback.native_accepted),
                    attempt_ledger=(tuple(attempt_ledger[:-1])
                                    + fallback.attempt_ledger),
                )
                return finish_early(InsertionResult(**values))
            if outcome in {
                InsertionOutcome.CONFIRMED,
                InsertionOutcome.SENT_UNCONFIRMED,
                InsertionOutcome.UNCERTAIN,
            }:
                self._settle_delay(max(0.0, request.settle_seconds))
        finally:
            if request.restore_clipboard and not clipboard_cleanup_done:
                restore_state = self._restore_clipboard_state(
                    snapshot, ownership)
                clipboard_restored = (
                    restore_state is ClipboardRestoreState.RESTORED)
                clipboard_changed = (
                    restore_state is ClipboardRestoreState.NEWER_EXTERNAL)
                cleanup_warning = self._restore_warning(restore_state)

        return self._result(
            request, outcome, reason, send_count=send_count,
            native_requested=requested, native_accepted=accepted,
            clipboard_restored=clipboard_restored,
            clipboard_changed_externally=clipboard_changed,
            cleanup_warning=cleanup_warning,
            attempt_ledger=tuple(attempt_ledger),
        )

    def _finish_clipboard(self, request, snapshot, ownership, result):
        if not request.restore_clipboard:
            return result
        state = self._restore_clipboard_state(snapshot, ownership)
        values = result.__dict__.copy()
        values.update(
            clipboard_restored=(state is ClipboardRestoreState.RESTORED),
            clipboard_changed_externally=(
                state is ClipboardRestoreState.NEWER_EXTERNAL),
            cleanup_warning=self._restore_warning(state),
        )
        return InsertionResult(**values)

    def _restore_clipboard_state(self, snapshot, ownership):
        try:
            restored = self._clipboard.restore(snapshot, ownership)
        except Exception:
            return ClipboardRestoreState.FAILED
        state = getattr(restored, "state", None)
        if isinstance(state, ClipboardRestoreState):
            return state
        try:
            return ClipboardRestoreState(state)
        except (TypeError, ValueError):
            return (ClipboardRestoreState.RESTORED if restored else
                    ClipboardRestoreState.FAILED)

    @staticmethod
    def _restore_warning(state):
        if state is ClipboardRestoreState.RESTORED:
            return ""
        if state is ClipboardRestoreState.NEWER_EXTERNAL:
            return (
                "The clipboard changed after Mumble wrote to it, so the newer "
                "clipboard was left untouched.")
        return "The previous clipboard could not be restored."


@dataclass
class _ModuleOperation:
    receipt: OperationReceipt
    payload_fingerprint: str = ""
    result: Optional[DeliveryResult] = None
    state: str = "prepared"
    created_at: float = 0.0
    pending_deadline: Optional[float] = None


class InsertionModule:
    """One target-bound operation spanning capture, adapters, and polling."""

    def __init__(self, target_adapter, clipboard_adapter, native_input_adapter,
                 *, elevated_helper=None, settle_delay=time.sleep,
                 trace=None, retention=256, prepared_ttl_s=15 * 60.0,
                 pending_ttl_s=30.0,
                 clock=time.monotonic):
        self._target = target_adapter
        self._clipboard = clipboard_adapter
        self._native = native_input_adapter
        self._elevated_helper = elevated_helper or ElevatedHelperAdapter()
        self._transaction = InsertionTransaction(
            target_adapter, clipboard_adapter, native_input_adapter,
            elevated_helper=self._elevated_helper,
            settle_delay=settle_delay, trace=trace,
        )
        self._retention = max(8, int(retention))
        self._prepared_ttl_s = max(0.0, float(prepared_ttl_s))
        self._pending_ttl_s = max(0.0, float(pending_ttl_s))
        self._clock = clock
        self._operations = OrderedDict()
        self._seen_operation_ids = OperationIdReplayGuard()
        self._lock = threading.Lock()

    @staticmethod
    def _operation_id(value):
        operation_id = validated_operation_id(value)
        if operation_id is None:
            raise ValueError("invalid_operation_id")
        return operation_id

    def _reserve_locked(self):
        self._expire_locked()
        while len(self._operations) >= self._retention:
            removable = next((
                key for key, value in self._operations.items()
                if value.state in {"terminal", "unknown", "abandoned"}
            ), None)
            if removable is None:
                raise InsertionCoordinatorCapacityError(
                    "insertion module capacity is occupied by active operations")
            self._operations.pop(removable, None)

    def _expire_locked(self):
        now = self._clock()
        for entry in self._operations.values():
            if (entry.state == "prepared"
                    and max(0.0, now - entry.created_at)
                    >= self._prepared_ttl_s):
                entry.state = "abandoned"
            elif (entry.state == "pending"
                  and entry.pending_deadline is not None
                  and now >= entry.pending_deadline):
                entry.state = "unknown"

    def begin(self, operation_id, source, reuse_destination_from=None):
        operation_id = self._operation_id(operation_id)
        source = str(source or "").strip()
        if not source:
            raise ValueError("invalid_insertion_source")
        reuse_id = (self._operation_id(reuse_destination_from)
                    if reuse_destination_from else None)
        with self._lock:
            self._expire_locked()
            existing = self._operations.get(operation_id)
            if existing is not None:
                if existing.state == "abandoned":
                    raise InsertionOperationExpired(
                        "prepared insertion operation expired")
                receipt = existing.receipt
                if (receipt.source != source
                        or receipt.reuse_destination_from != reuse_id):
                    raise InsertionRequestConflict(
                        "operation_id_reused_with_different_begin")
                self._operations.move_to_end(operation_id)
                return receipt
            if operation_id in self._seen_operation_ids:
                raise InsertionOperationExpired(
                    "operation identity is no longer reusable")
            if reuse_id:
                reused = self._operations.get(reuse_id)
                if reused is None:
                    raise InsertionOperationExpired(
                        "reuse destination operation is unavailable")
                target = reused.receipt.target
                capture_phase = "reused_destination"
            else:
                try:
                    target = self._target.current()
                except Exception:
                    target = None
                capture_phase = "begin"
            self._reserve_locked()
            receipt = OperationReceipt(
                operation_id=operation_id,
                source=source,
                target_lease=TargetLease(
                    target=target,
                    source=source,
                    capture_phase=capture_phase,
                    mumble_displaced_target=False,
                ),
                reuse_destination_from=reuse_id,
            )
            self._operations[operation_id] = _ModuleOperation(
                receipt, created_at=self._clock())
            self._seen_operation_ids.remember(operation_id)
            return receipt

    def _bind_lease(self, operation_id, source, target_lease):
        """Bind a caller's earlier Stop/Deck capture to the same deep module."""
        operation_id = self._operation_id(operation_id)
        if not isinstance(target_lease, TargetLease):
            raise TypeError("target_lease_required")
        source = str(source or "").strip()
        with self._lock:
            self._expire_locked()
            existing = self._operations.get(operation_id)
            if existing is not None:
                if existing.state == "abandoned":
                    raise InsertionOperationExpired(
                        "prepared insertion operation expired")
                receipt = existing.receipt
                if (receipt.source != source
                        or receipt.target_lease != target_lease):
                    raise InsertionRequestConflict(
                        "operation_id_reused_with_different_destination")
                self._operations.move_to_end(operation_id)
                return receipt
            if operation_id in self._seen_operation_ids:
                raise InsertionOperationExpired(
                    "operation identity is no longer reusable")
            self._reserve_locked()
            receipt = OperationReceipt(
                operation_id=operation_id,
                source=source,
                target_lease=target_lease,
            )
            self._operations[operation_id] = _ModuleOperation(
                receipt, created_at=self._clock())
            self._seen_operation_ids.remember(operation_id)
            return receipt

    @staticmethod
    def _payload_fingerprint(payload, intent):
        if isinstance(payload, TextPayload):
            values = ("text", payload.text)
        elif isinstance(payload, ImagePayload):
            values = ("image", payload.path)
        elif isinstance(payload, RichPayload):
            values = ("rich", payload.text, payload.html, payload.rtf)
        else:
            raise TypeError("unsupported_delivery_payload")
        digest = hashlib.sha256()
        digest.update(str(intent.value).encode("ascii"))
        for value in values:
            encoded = str(value or "").encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "little"))
            digest.update(encoded)
        return digest.hexdigest()

    def deliver(self, operation_id, payload, intent=INSERT):
        operation_id = self._operation_id(operation_id)
        intent = DeliveryIntent(intent)
        fingerprint = self._payload_fingerprint(payload, intent)
        with self._lock:
            self._expire_locked()
            entry = self._operations.get(operation_id)
            if entry is None:
                if operation_id in self._seen_operation_ids:
                    raise InsertionOperationExpired(
                        "operation identity is no longer reusable")
                raise InsertionOperationExpired(
                    "begin must capture the destination before delivery")
            if entry.state == "abandoned":
                raise InsertionOperationExpired(
                    "prepared insertion operation expired")
            if (entry.payload_fingerprint
                    and entry.payload_fingerprint != fingerprint):
                raise InsertionRequestConflict(
                    "operation_id_reused_with_different_payload")
            if entry.result is not None:
                return entry.result
            if entry.state in {"pending", "unknown"}:
                return self._nonterminal_result(entry)
            entry.payload_fingerprint = fingerprint
            entry.state = "pending"
            entry.pending_deadline = self._clock() + self._pending_ttl_s
            receipt = entry.receipt

        if isinstance(payload, TextPayload):
            content_kind = "text"
            text = payload.text
            image_path = rich_html = rich_rtf = ""
        elif isinstance(payload, ImagePayload):
            content_kind = "image"
            image_path = payload.path
            text = rich_html = rich_rtf = ""
        else:
            content_kind = "rich"
            text = payload.text
            rich_html = payload.html
            rich_rtf = payload.rtf
            image_path = ""
        request = InsertionRequest(
            operation_id=operation_id,
            source=receipt.source,
            content_kind=content_kind,
            activation_target=receipt.target,
            target_lease=receipt.target_lease,
            text=text,
            image_path=image_path,
            rich_html=rich_html,
            rich_rtf=rich_rtf,
            undo_before_paste=intent is DeliveryIntent.REPLACE,
            settle_seconds=(0.30 if content_kind == "image" else
                            min(1.2, 0.18 + len(text) / 20000.0)),
        )
        try:
            result = self._transaction.insert(request)
        except BaseException:
            with self._lock:
                current = self._operations.get(operation_id)
                if current is entry:
                    current.state = "unknown"
                    self._operations.move_to_end(operation_id)
            raise
        with self._lock:
            self._expire_locked()
            current = self._operations.get(operation_id)
            if current is entry:
                current.result = result
                current.state = "terminal"
                self._operations.move_to_end(operation_id)
        return result

    @staticmethod
    def _nonterminal_result(entry):
        state = entry.state
        receipt = entry.receipt
        return DeliveryResult(
            operation_id=receipt.operation_id,
            source=receipt.source,
            outcome=InsertionOutcome.UNCERTAIN,
            reason=("delivery_uncertain" if state == "unknown" else
                    "operation_pending" if state == "pending" else
                    "operation_prepared"),
            message=(
                "Delivery uncertain — check the selected destination."
                if state == "unknown" else
                "Still working. Mumble will not send this operation twice."
                if state == "pending" else
                "The destination is captured and delivery has not started."
            ),
            send_count=0,
            target_lease=receipt.target_lease,
            state=state,
        )

    def status(self, operation_id):
        operation_id = self._operation_id(operation_id)
        with self._lock:
            self._expire_locked()
            entry = self._operations.get(operation_id)
            if entry is None:
                raise InsertionOperationExpired("insertion operation is missing")
            self._operations.move_to_end(operation_id)
            if entry.result is not None:
                return entry.result
            return self._nonterminal_result(entry)

    def abandon(self, operation_id):
        operation_id = self._operation_id(operation_id)
        with self._lock:
            self._expire_locked()
            entry = self._operations.get(operation_id)
            if entry is None:
                return {
                    "state": ("expired" if operation_id in
                              self._seen_operation_ids else "missing"),
                    "operation_id": operation_id,
                }
            if entry.result is not None:
                return entry.result.as_dict()
            if entry.state in {"pending", "unknown"}:
                entry.state = "unknown"
                self._operations.move_to_end(operation_id)
                return self._nonterminal_result(entry).as_dict()
            entry.state = "abandoned"
        return {"state": "abandoned", "operation_id": operation_id}
