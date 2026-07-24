"""Target-bound, at-most-once insertion transaction.

The controller supplies three small platform adapters: target/focus, clipboard,
and native input.  This module owns policy and terminal user truth; it never
guesses that content landed merely because a paste chord was attempted.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
import threading
import time
from typing import Callable, Optional, Tuple


class InsertionOutcome(str, Enum):
    CONFIRMED = "confirmed"
    SENT_UNCONFIRMED = "sent_unconfirmed"
    NOT_SENT = "not_sent"
    UNCERTAIN = "uncertain"
    SAVED_ONLY = "saved_only"


@dataclass(frozen=True)
class TargetContext:
    window: int
    process_id: int
    thread_id: int
    focused_child: int
    integrity: str
    control_class: str = ""
    has_caret: bool = False

    def same_destination(self, other: Optional["TargetContext"]) -> bool:
        if other is None:
            return False
        return (
            self.window == other.window
            and self.process_id == other.process_id
            and self.focused_child == other.focused_child
        )


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
    text: str = ""
    image_path: str = ""
    restore_focus: bool = True
    restore_clipboard: bool = True
    focus_timeout_s: float = 0.35
    modifier_timeout_s: float = 0.35
    settle_seconds: float = 0.20


_MESSAGES = {
    InsertionOutcome.CONFIRMED:
        "Pasted. The result also remains in Deck and History.",
    InsertionOutcome.SENT_UNCONFIRMED:
        "Sent to the active field, but Windows could not confirm it. "
        "The result remains in Deck and History.",
    InsertionOutcome.NOT_SENT:
        "Not sent. Use Copy or Paste latest from Deck or History.",
    InsertionOutcome.UNCERTAIN:
        "Paste not confirmed. Check the field before trying again; "
        "the result remains in Deck and History.",
    InsertionOutcome.SAVED_ONLY:
        "Saved in Deck and History only. Click the intended field, then use "
        "Copy or Paste latest.",
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
            "reason": self.reason,
            "message": self.message,
            "send_count": self.send_count,
            "native_requested": self.native_requested,
            "native_accepted": self.native_accepted,
            "clipboard_restored": self.clipboard_restored,
            "clipboard_changed_externally": self.clipboard_changed_externally,
            "cleanup_warning": self.cleanup_warning,
        }


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
            result = self._execute(request)
            self._completed[request.operation_id] = result
            while len(self._completed) > self._cache_size:
                self._completed.popitem(last=False)
            return result

    def _result(self, request, outcome, reason, send_count=0,
                native_requested=0, native_accepted=0, **cleanup):
        return InsertionResult(
            operation_id=request.operation_id,
            source=request.source,
            outcome=outcome,
            reason=reason,
            message=_MESSAGES[outcome],
            send_count=send_count,
            native_requested=native_requested,
            native_accepted=native_accepted,
            **cleanup,
        )

    def _execute(self, request):
        activation = request.activation_target or self._target.current()
        if activation is None or not activation.window:
            return self._result(request, InsertionOutcome.SAVED_ONLY,
                                "no activation target")

        current = self._target.current()
        if not activation.same_destination(current):
            restored = bool(request.restore_focus) and bool(
                self._target.restore(activation, request.focus_timeout_s))
            current = self._target.current()
            if not restored or not activation.same_destination(current):
                return self._result(request, InsertionOutcome.SAVED_ONLY,
                                    "activation target could not be restored")

        injectable = self._target.can_inject(activation)
        if injectable is False:
            return self._result(request, InsertionOutcome.SAVED_ONLY,
                                "target has a higher Windows privilege level")

        try:
            snapshot = self._clipboard.snapshot()
        except Exception as exc:
            return self._result(request, InsertionOutcome.SAVED_ONLY,
                                "clipboard snapshot failed: {}".format(exc))
        if request.restore_clipboard and not snapshot.restorable:
            return self._result(
                request, InsertionOutcome.SAVED_ONLY,
                snapshot.reason or "clipboard formats cannot be restored safely")

        try:
            ownership = self._clipboard.write(request, snapshot)
        except Exception as exc:
            return self._result(request, InsertionOutcome.SAVED_ONLY,
                                "clipboard write failed: {}".format(exc))
        self._trace("clipboard_ready", operation_id=request.operation_id,
                    content_kind=request.content_kind)

        outcome = InsertionOutcome.NOT_SENT
        reason = "native paste was not sent"
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
                                 "target changed immediately before send"))

            ready, ready_reason = self._native.ready(request.modifier_timeout_s)
            if not ready:
                return self._finish_clipboard(
                    request, snapshot, ownership,
                    self._result(request, InsertionOutcome.NOT_SENT,
                                 ready_reason or "physical modifier still held"))

            send_count = 1
            try:
                acceptance = self._native.send_paste()
            except Exception as exc:
                outcome = InsertionOutcome.UNCERTAIN
                reason = "native send raised after it may have started: {}".format(exc)
                accepted = None
            else:
                requested = max(0, int(acceptance.requested))
                accepted = acceptance.accepted
                if accepted is not None and (accepted < 0 or accepted > requested):
                    outcome = InsertionOutcome.UNCERTAIN
                    reason = "native input returned an invalid acceptance count"
                elif acceptance.submitted and accepted == requested and requested > 0:
                    if acceptance.confirmation is True:
                        outcome = InsertionOutcome.CONFIRMED
                        reason = "target fixture confirmed one insertion"
                    elif acceptance.confirmation is False:
                        outcome = InsertionOutcome.NOT_SENT
                        reason = acceptance.error or "target confirmed it did not accept content"
                    else:
                        outcome = InsertionOutcome.SENT_UNCONFIRMED
                        reason = acceptance.error or "native input accepted; target did not confirm content"
                elif acceptance.submitted and accepted is None:
                    outcome = InsertionOutcome.SENT_UNCONFIRMED
                    reason = acceptance.error or "platform cannot count native acceptance"
                elif acceptance.submitted or (accepted is not None and accepted > 0):
                    outcome = InsertionOutcome.UNCERTAIN
                    reason = acceptance.error or "only part of the native paste input was accepted"
                else:
                    outcome = InsertionOutcome.NOT_SENT
                    reason = acceptance.error or "Windows accepted no native paste input"

            self._trace("paste_sent", operation_id=request.operation_id,
                        accepted=accepted, requested=requested)
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
                except Exception as exc:
                    cleanup_warning = "Clipboard cleanup failed: {}".format(exc)

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
        except Exception as exc:
            values = result.__dict__.copy()
            values["cleanup_warning"] = "Clipboard cleanup failed: {}".format(exc)
            return InsertionResult(**values)
