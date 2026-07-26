#!/usr/bin/env python3
"""Deterministic behaviour tests for the target-bound insertion transaction."""

import threading
import unittest
import uuid
from dataclasses import replace

from insertion import (
    ClipboardOwnership,
    ClipboardRestoreResult,
    ClipboardRestoreState,
    ClipboardSnapshot,
    ClipboardWriteFailure,
    InsertionOutcome,
    InsertionCoordinator,
    InsertionCoordinatorCapacityError,
    InsertionOperationExpired,
    InsertionRequestConflict,
    InsertionWaitTimeout,
    InsertionReason,
    InsertionRequest,
    InsertionResult,
    InsertionTransaction,
    NativeAcceptance,
    TargetLease,
    TargetContext,
    TargetEditability,
)


TARGET = TargetContext(
    window=101,
    process_id=202,
    thread_id=303,
    focused_child=404,
    integrity="medium",
    control_class="Edit",
    has_caret=True,
    editability=TargetEditability.EDITABLE,
)
OTHER_TARGET = TargetContext(
    window=501,
    process_id=502,
    thread_id=503,
    focused_child=504,
    integrity="medium",
    control_class="Edit",
    has_caret=True,
    editability=TargetEditability.EDITABLE,
)


class FakeTarget:
    def __init__(self):
        self.active = TARGET
        self.current_values = []
        self.injectable = True
        self.injectable_values = []
        self.restore_calls = 0
        self.restore_result = True

    def current(self):
        if self.current_values:
            self.active = self.current_values.pop(0)
        return self.active

    def restore(self, target, timeout_s):
        self.restore_calls += 1
        if self.restore_result:
            self.active = target
        return self.restore_result

    def can_inject(self, target):
        if self.injectable_values:
            return self.injectable_values.pop(0)
        return self.injectable


class FakeClipboard:
    def __init__(self):
        self.sequence = 7
        self.snapshot_value = ClipboardSnapshot(
            sequence=7,
            formats=((13, "CF_UNICODETEXT", b"prior text"),
                     (49321, "Rich Text Format", b"{\\rtf1 prior}")),
        )
        self.owner = None
        self.restore_calls = 0
        self.write_calls = 0
        self.snapshot_calls = 0
        self.fail_write = False
        self.restore_result = None
        self.still_owns_calls = 0

    def snapshot(self):
        self.snapshot_calls += 1
        return self.snapshot_value

    def write(self, request, snapshot):
        self.write_calls += 1
        if self.fail_write:
            raise RuntimeError("clipboard busy")
        self.sequence += 1
        self.owner = ClipboardOwnership(self.sequence, "mumble-payload")
        return self.owner

    def still_owns(self, ownership):
        self.still_owns_calls += 1
        return ownership == self.owner and self.sequence == ownership.sequence

    def restore(self, snapshot, ownership):
        self.restore_calls += 1
        if self.restore_result is not None:
            return self.restore_result
        if ownership != self.owner or self.sequence != ownership.sequence:
            return ClipboardRestoreResult(
                ClipboardRestoreState.NEWER_EXTERNAL)
        self.sequence += 1
        return ClipboardRestoreResult(ClipboardRestoreState.RESTORED)


class FakeNativeInput:
    def __init__(self, acceptance=None):
        self.send_calls = 0
        self.ready_result = (True, "")
        self.acceptance = acceptance or NativeAcceptance(
            requested=4, accepted=4, submitted=True, confirmation=True)
        self.on_send = lambda: None
        self.raise_after_start = False
        self.undo_calls = 0
        self.undo_acceptance = NativeAcceptance(
            requested=4, accepted=4, submitted=True, confirmation=None)

    def ready(self, timeout_s):
        return self.ready_result

    def send_paste(self):
        self.send_calls += 1
        self.on_send()
        if self.raise_after_start:
            raise RuntimeError("driver failed after submit")
        return self.acceptance

    def send_undo(self):
        self.undo_calls += 1
        return self.undo_acceptance


class InsertionTransactionTests(unittest.TestCase):
    @staticmethod
    def operation_id(label):
        if (isinstance(label, str) and len(label) == 32
                and all(character in "0123456789abcdef" for character in label)):
            return label
        return uuid.uuid5(uuid.NAMESPACE_OID, label).hex

    def make_request(self, operation_id="dictation-1", **changes):
        values = dict(
            operation_id=self.operation_id(operation_id),
            source="dictation",
            content_kind="text",
            text="new words",
            activation_target=TARGET,
        )
        values.update(changes)
        return InsertionRequest(**values)

    def test_focus_restoration_requires_a_mumble_displaced_target_lease(self):
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput()
        target.active = OTHER_TARGET
        transaction = InsertionTransaction(target, clipboard, native,
                                           settle_delay=lambda _seconds: None)
        request = self.make_request(
            "user-switched",
            activation_target=None,
            target_lease=TargetLease(
                target=TARGET,
                source="dictation",
                capture_phase="stop",
                mumble_displaced_target=False,
            ),
        )

        result = transaction.insert(request)

        self.assertEqual(InsertionOutcome.SAVED_ONLY, result.outcome)
        self.assertEqual("target_changed", result.reason)
        self.assertEqual(0, target.restore_calls)
        self.assertEqual(0, native.send_calls)

    def test_deck_lease_may_restore_the_destination_mumble_displaced(self):
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput()
        target.active = OTHER_TARGET
        transaction = InsertionTransaction(target, clipboard, native,
                                           settle_delay=lambda _seconds: None)
        request = self.make_request(
            "deck-return",
            activation_target=None,
            target_lease=TargetLease(
                target=TARGET,
                source="deck_history",
                capture_phase="before_mumble_focus",
                mumble_displaced_target=True,
            ),
        )

        result = transaction.insert(request)

        self.assertEqual(InsertionOutcome.CONFIRMED, result.outcome)
        self.assertEqual(1, target.restore_calls)
        self.assertEqual(1, native.send_calls)

    def test_all_deck_actions_share_exact_displacement_send_and_focus_policy(self):
        cases = [
            ("deck_history", "text", "", "words"),
            ("deck_image", "image", r"C:\fixture.png", ""),
            ("deck_job", "text", "", "job result"),
        ]
        for source, kind, image_path, text in cases:
            for displaced in (False, True):
                with self.subTest(source=source, displaced=displaced):
                    target, clipboard, native = (
                        FakeTarget(), FakeClipboard(), FakeNativeInput())
                    target.active = OTHER_TARGET
                    transaction = InsertionTransaction(
                        target, clipboard, native,
                        settle_delay=lambda _seconds: None)
                    request = self.make_request(
                        "{}-{}".format(source, displaced),
                        source=source, content_kind=kind,
                        text=text, image_path=image_path,
                        activation_target=None,
                        target_lease=TargetLease(
                            TARGET, source, "before_deck_dismiss", displaced),
                    )

                    result = transaction.insert(request)

                    self.assertEqual(1 if displaced else 0,
                                     target.restore_calls)
                    self.assertEqual(1 if displaced else 0,
                                     native.send_calls)
                    self.assertEqual(TARGET if displaced else OTHER_TARGET,
                                     target.active)
                    self.assertEqual(
                        InsertionOutcome.CONFIRMED if displaced else
                        InsertionOutcome.SAVED_ONLY,
                        result.outcome)

    def test_confirmed_insert_sends_once_and_restores_all_formats(self):
        target = FakeTarget()
        clipboard = FakeClipboard()
        native = FakeNativeInput()
        transaction = InsertionTransaction(target, clipboard, native,
                                           settle_delay=lambda _seconds: None)

        result = transaction.insert(self.make_request())

        self.assertEqual(InsertionOutcome.CONFIRMED, result.outcome)
        self.assertEqual(1, result.send_count)
        self.assertEqual(1, native.send_calls)
        self.assertEqual(1, clipboard.restore_calls)
        self.assertTrue(result.clipboard_restored)

    def test_same_operation_is_idempotent_even_when_called_concurrently(self):
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput()
        transaction = InsertionTransaction(target, clipboard, native,
                                           settle_delay=lambda _seconds: None)
        barrier = threading.Barrier(3)
        results = []

        def run():
            barrier.wait()
            results.append(transaction.insert(self.make_request("same-op")))

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(2)

        self.assertEqual(2, len(results))
        self.assertIs(results[0], results[1])
        self.assertEqual(1, native.send_calls)

    def test_delayed_focus_is_restored_to_exact_activation_child(self):
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput()
        target.active = OTHER_TARGET
        transaction = InsertionTransaction(target, clipboard, native,
                                           settle_delay=lambda _seconds: None)

        result = transaction.insert(self.make_request(
            "focus-restore",
            target_lease=TargetLease(
                target=TARGET,
                source="deck_history",
                capture_phase="before_mumble_focus",
                mumble_displaced_target=True,
            ),
        ))

        self.assertEqual(InsertionOutcome.CONFIRMED, result.outcome)
        self.assertEqual(1, target.restore_calls)
        self.assertEqual(TARGET, target.active)

    def test_late_target_change_is_saved_only_and_never_sent(self):
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput()
        target.current_values = [TARGET, OTHER_TARGET]
        transaction = InsertionTransaction(target, clipboard, native,
                                           settle_delay=lambda _seconds: None)

        result = transaction.insert(self.make_request("late-target-change"))

        self.assertEqual(InsertionOutcome.SAVED_ONLY, result.outcome)
        self.assertEqual(0, native.send_calls)
        self.assertEqual(1, clipboard.restore_calls)

    def test_same_control_late_safety_mutations_fail_closed_before_native_input(self):
        cases = [
            ("read-only", replace(
                TARGET, read_only=True,
                editability=TargetEditability.NOT_EDITABLE),
             [True, True], InsertionReason.READ_ONLY),
            ("protected", replace(
                TARGET, protected=True,
                editability=TargetEditability.NOT_EDITABLE),
             [True, True], InsertionReason.PROTECTED_FIELD),
            ("unsupported", replace(
                TARGET, control_class="Button", has_caret=False,
                editability=TargetEditability.NOT_EDITABLE),
             [True, True], InsertionReason.NOT_EDITABLE),
            ("supported-control-changed", replace(
                TARGET, control_class="RichEdit50W"),
             [True, True], InsertionReason.TARGET_SAFETY_CHANGED),
            ("higher-integrity", replace(
                TARGET, integrity="high", integrity_relation="higher"),
             [True, False], InsertionReason.HIGHER_INTEGRITY),
            ("unknown-integrity", replace(
                TARGET, integrity="unknown", integrity_relation="unknown"),
             [True, None], InsertionReason.UNKNOWN_INTEGRITY),
        ]
        for name, late_context, injectable, expected_reason in cases:
            with self.subTest(name=name):
                target, clipboard, native = (
                    FakeTarget(), FakeClipboard(), FakeNativeInput())
                target.current_values = [TARGET, late_context]
                target.injectable_values = list(injectable)
                transaction = InsertionTransaction(
                    target, clipboard, native,
                    settle_delay=lambda _seconds: None)

                result = transaction.insert(self.make_request("late-" + name))

                self.assertEqual(InsertionOutcome.SAVED_ONLY, result.outcome)
                self.assertEqual(expected_reason.value, result.reason)
                self.assertEqual(1, clipboard.write_calls)
                self.assertEqual(1, clipboard.restore_calls)
                self.assertEqual(0, native.send_calls)
                self.assertNotIn("Pasted", result.message)

    def test_elevation_mismatch_never_touches_clipboard_or_input(self):
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput()
        target.injectable = False
        transaction = InsertionTransaction(target, clipboard, native)

        result = transaction.insert(self.make_request("elevated"))

        self.assertEqual(InsertionOutcome.SAVED_ONLY, result.outcome)
        self.assertEqual(InsertionReason.HIGHER_INTEGRITY.value, result.reason)
        self.assertEqual(0, clipboard.snapshot_calls)
        self.assertEqual(0, native.send_calls)

    def test_unrestorable_private_clipboard_format_blocks_safe_insertion(self):
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput()
        clipboard.snapshot_value = ClipboardSnapshot(
            sequence=7, formats=(), restorable=False,
            reason="private clipboard format cannot be cloned")
        transaction = InsertionTransaction(target, clipboard, native)

        result = transaction.insert(self.make_request("private-format"))

        self.assertEqual(InsertionOutcome.SAVED_ONLY, result.outcome)
        self.assertEqual(0, clipboard.write_calls)
        self.assertEqual(0, native.send_calls)

    def test_busy_clipboard_is_saved_only_without_native_send(self):
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput()
        clipboard.fail_write = True
        transaction = InsertionTransaction(target, clipboard, native)

        result = transaction.insert(self.make_request("clipboard-busy"))

        self.assertEqual(InsertionOutcome.SAVED_ONLY, result.outcome)
        self.assertEqual(InsertionReason.CLIPBOARD_WRITE_FAILED.value,
                         result.reason)
        self.assertEqual(0, native.send_calls)

    def test_partial_clipboard_failure_reports_recovery_truth_and_never_sends(self):
        cases = [
            ("restored", ClipboardWriteFailure(
                "second format failed", clipboard_restored=True),
             True, False, ""),
            ("external", ClipboardWriteFailure(
                "third format failed", clipboard_changed_externally=True,
                cleanup_warning="The newer clipboard was left untouched."),
             False, True, "left untouched"),
            ("restore-failed", ClipboardWriteFailure(
                "readback mismatch",
                cleanup_warning="The previous clipboard could not be restored."),
             False, False, "could not be restored"),
        ]
        for name, failure, restored, changed, warning in cases:
            with self.subTest(name=name):
                target, clipboard, native = (
                    FakeTarget(), FakeClipboard(), FakeNativeInput())
                clipboard.write = lambda *_args, failure=failure: (
                    (_ for _ in ()).throw(failure))
                transaction = InsertionTransaction(target, clipboard, native)

                result = transaction.insert(self.make_request(
                    "clipboard-recovery-" + name))

                self.assertEqual(InsertionOutcome.SAVED_ONLY, result.outcome)
                self.assertEqual(InsertionReason.CLIPBOARD_WRITE_FAILED.value,
                                 result.reason)
                self.assertEqual(restored, result.clipboard_restored)
                self.assertEqual(changed, result.clipboard_changed_externally)
                self.assertIn(warning, result.cleanup_warning)
                self.assertEqual(0, native.send_calls)

    def test_incomplete_image_clipboard_write_is_saved_only_and_never_sent(self):
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput()
        failure = ClipboardWriteFailure(
            "image format failed", clipboard_restored=True)
        clipboard.write = lambda *_args: (
            (_ for _ in ()).throw(failure))
        transaction = InsertionTransaction(target, clipboard, native)

        result = transaction.insert(self.make_request(
            "image-write-failed", source="deck_image", content_kind="image",
            text="", image_path=r"C:\fixture.png"))

        self.assertEqual(InsertionOutcome.SAVED_ONLY, result.outcome)
        self.assertEqual(InsertionReason.CLIPBOARD_WRITE_FAILED.value,
                         result.reason)
        self.assertTrue(result.clipboard_restored)
        self.assertEqual(0, native.send_calls)

    def test_physical_modifier_timeout_is_not_sent(self):
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput()
        native.ready_result = (False, "left Alt is still physically held")
        transaction = InsertionTransaction(target, clipboard, native)

        result = transaction.insert(self.make_request("modifier-held"))

        self.assertEqual(InsertionOutcome.NOT_SENT, result.outcome)
        self.assertEqual(0, result.send_count)
        self.assertEqual(0, native.send_calls)
        self.assertEqual(1, clipboard.restore_calls)

    def test_swallowed_paste_is_not_sent_and_duplicate_does_not_retry(self):
        acceptance = NativeAcceptance(4, 4, True, confirmation=False,
                                      error="fixture observed no insertion")
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput(acceptance)
        transaction = InsertionTransaction(target, clipboard, native,
                                           settle_delay=lambda _seconds: None)
        request = self.make_request("swallowed")

        first = transaction.insert(request)
        second = transaction.insert(request)

        self.assertEqual(InsertionOutcome.NOT_SENT, first.outcome)
        self.assertIs(first, second)
        self.assertEqual(1, first.send_count)
        self.assertEqual(1, native.send_calls)

    def test_partial_native_acceptance_is_uncertain_and_never_retried(self):
        acceptance = NativeAcceptance(4, 2, True, error="partial SendInput")
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput(acceptance)
        transaction = InsertionTransaction(target, clipboard, native,
                                           settle_delay=lambda _seconds: None)
        request = self.make_request("partial")

        first = transaction.insert(request)
        second = transaction.insert(request)

        self.assertEqual(InsertionOutcome.UNCERTAIN, first.outcome)
        self.assertEqual(1, first.send_count)
        self.assertEqual(1, native.send_calls)
        self.assertIs(first, second)

    def test_after_send_exception_is_uncertain_and_never_retried(self):
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput()
        native.raise_after_start = True
        transaction = InsertionTransaction(target, clipboard, native,
                                           settle_delay=lambda _seconds: None)
        request = self.make_request("after-send-failure")

        first = transaction.insert(request)
        second = transaction.insert(request)

        self.assertEqual(InsertionOutcome.UNCERTAIN, first.outcome)
        self.assertEqual(1, native.send_calls)
        self.assertIs(first, second)

    def test_counted_send_without_target_proof_is_sent_unconfirmed(self):
        acceptance = NativeAcceptance(4, 4, True, confirmation=None)
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput(acceptance)
        transaction = InsertionTransaction(target, clipboard, native,
                                           settle_delay=lambda _seconds: None)

        result = transaction.insert(self.make_request("unconfirmed"))

        self.assertEqual(InsertionOutcome.SENT_UNCONFIRMED, result.outcome)
        self.assertIn("Sent—check the field", result.message)

    def test_external_clipboard_change_is_never_overwritten(self):
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput()
        native.on_send = lambda: setattr(clipboard, "sequence", clipboard.sequence + 1)
        transaction = InsertionTransaction(target, clipboard, native,
                                           settle_delay=lambda _seconds: None)

        result = transaction.insert(self.make_request("external-copy"))

        self.assertEqual(InsertionOutcome.CONFIRMED, result.outcome)
        self.assertFalse(result.clipboard_restored)
        self.assertTrue(result.clipboard_changed_externally)
        self.assertEqual(1, clipboard.restore_calls)

    def test_normal_and_early_exit_restore_use_one_authoritative_adapter_result(self):
        for kind in ("text", "image"):
            for path in ("normal", "early-exit"):
                with self.subTest(kind=kind, path=path):
                    target, clipboard, native = (
                        FakeTarget(), FakeClipboard(), FakeNativeInput())
                    clipboard.restore_result = ClipboardRestoreResult(
                        ClipboardRestoreState.NEWER_EXTERNAL)
                    if path == "early-exit":
                        target.current_values = [TARGET, OTHER_TARGET]
                    transaction = InsertionTransaction(
                        target, clipboard, native,
                        settle_delay=lambda _seconds: None)
                    request = self.make_request(
                        "atomic-{}-{}".format(kind, path),
                        content_kind=kind,
                        text="payload" if kind == "text" else "",
                        image_path=(r"C:\fixture.png"
                                    if kind == "image" else ""))

                    result = transaction.insert(request)

                    self.assertEqual(1, clipboard.restore_calls)
                    self.assertEqual(0, clipboard.still_owns_calls)
                    self.assertTrue(result.clipboard_changed_externally)
                    self.assertFalse(result.clipboard_restored)
                    self.assertIn("left untouched", result.cleanup_warning)
                    self.assertEqual(0 if path == "early-exit" else 1,
                                     native.send_calls)

    def test_image_request_uses_the_same_terminal_contract(self):
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput(
            NativeAcceptance(4, 4, True, confirmation=None))
        transaction = InsertionTransaction(target, clipboard, native,
                                           settle_delay=lambda _seconds: None)

        result = transaction.insert(self.make_request(
            "image", source="deck_image", content_kind="image", text="",
            image_path=r"C:\safe\capture.png"))

        self.assertEqual(InsertionOutcome.SENT_UNCONFIRMED, result.outcome)
        self.assertEqual("deck_image", result.source)
        self.assertEqual(1, native.send_calls)

    def test_keep_on_clipboard_skips_restore_but_still_never_retries(self):
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput()
        transaction = InsertionTransaction(target, clipboard, native,
                                           settle_delay=lambda _seconds: None)
        request = self.make_request("keep-clipboard", restore_clipboard=False)

        first = transaction.insert(request)
        second = transaction.insert(request)

        self.assertEqual(InsertionOutcome.CONFIRMED, first.outcome)
        self.assertFalse(first.clipboard_restored)
        self.assertEqual(0, clipboard.restore_calls)
        self.assertIs(first, second)
        self.assertEqual(1, native.send_calls)

    def test_coordinator_reports_pending_and_joins_the_same_operation(self):
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput()
        entered = threading.Event()
        release = threading.Event()

        def delayed_send():
            entered.set()
            release.wait(2)

        native.on_send = delayed_send
        coordinator = InsertionCoordinator(InsertionTransaction(
            target, clipboard, native, settle_delay=lambda _seconds: None))
        request = self.make_request("timeout-op")
        coordinator.prepare(request)
        first = []
        second = []
        one = threading.Thread(target=lambda: first.append(coordinator.submit(request)))
        two = threading.Thread(target=lambda: second.append(coordinator.submit(request)))
        one.start()
        self.assertTrue(entered.wait(1))

        pending = coordinator.status(request.operation_id)
        two.start()
        self.assertEqual("pending", pending["state"])
        self.assertEqual(1, native.send_calls)
        release.set()
        one.join(2)
        two.join(2)

        self.assertEqual(1, native.send_calls)
        self.assertIs(first[0], second[0])
        self.assertEqual("terminal", coordinator.status(
            request.operation_id)["state"])

    def test_coordinator_rejects_operation_id_reuse_with_new_content(self):
        coordinator = InsertionCoordinator(InsertionTransaction(
            FakeTarget(), FakeClipboard(), FakeNativeInput(),
            settle_delay=lambda _seconds: None))
        coordinator.prepare(self.make_request("reused", text="first"))

        with self.assertRaises(InsertionRequestConflict):
            coordinator.prepare(self.make_request("reused", text="second"))

    def test_coordinator_join_wait_is_explicitly_bounded(self):
        entered = threading.Event()
        release = threading.Event()

        class BlockingTransaction:
            def insert(_self, request):
                entered.set()
                release.wait(2)
                return InsertionResult(
                    request.operation_id, request.source,
                    InsertionOutcome.CONFIRMED, "confirmed", "Pasted.", 1)

        coordinator = InsertionCoordinator(
            BlockingTransaction(), join_timeout_s=0.0)
        request = self.make_request("bounded-join")
        owner = threading.Thread(target=lambda: coordinator.submit(request))
        owner.start()
        self.assertTrue(entered.wait(1))

        with self.assertRaises(InsertionWaitTimeout):
            coordinator.submit(request)

        self.assertEqual("pending", coordinator.status(
            request.operation_id)["state"])
        release.set()
        owner.join(2)
        self.assertEqual("terminal", coordinator.status(
            request.operation_id)["state"])

    def test_explicit_abandonment_is_idempotent_replay_protected_and_never_sends(self):
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput()
        coordinator = InsertionCoordinator(InsertionTransaction(
            target, clipboard, native, settle_delay=lambda _seconds: None))
        request = self.make_request("explicit-abandonment")
        coordinator.prepare(request)

        first = coordinator.abandon(request.operation_id)
        second = coordinator.abandon(request.operation_id)

        self.assertEqual("expired", first["state"])
        self.assertEqual(first, second)
        with self.assertRaises(InsertionOperationExpired):
            coordinator.submit(request)
        self.assertEqual("expired", coordinator.prepare(request)["state"])
        self.assertEqual(0, native.send_calls)

    def test_abandonment_and_expiry_never_interrupt_an_executing_operation(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []
        now = {"value": 10.0}

        class BlockingTransaction:
            def insert(_self, request):
                calls.append(request.operation_id)
                entered.set()
                release.wait(2)
                return InsertionResult(
                    request.operation_id, request.source,
                    InsertionOutcome.CONFIRMED, "confirmed", "Pasted.", 1)

        coordinator = InsertionCoordinator(
            BlockingTransaction(), clock=lambda: now["value"],
            prepared_ttl_s=1.0, pending_ttl_s=1.0)
        request = self.make_request("slow-valid-operation")
        worker = threading.Thread(target=lambda: coordinator.submit(request))
        worker.start()
        self.assertTrue(entered.wait(1))

        self.assertEqual("pending", coordinator.abandon(
            request.operation_id)["state"])
        now["value"] = 100.0
        self.assertEqual("unknown", coordinator.status(
            request.operation_id)["state"])
        release.set()
        worker.join(2)

        self.assertFalse(worker.is_alive())
        self.assertEqual([request.operation_id], calls)
        self.assertEqual("terminal", coordinator.status(
            request.operation_id)["state"])

    def test_execute_exception_emits_one_content_free_finish_and_keeps_traceback(self):
        events = []
        transaction = InsertionTransaction(
            FakeTarget(), FakeClipboard(), FakeNativeInput(),
            trace=lambda name, **fields: events.append((name, fields)))
        request = self.make_request(
            "terminal-exception", text="PRIVATE_CALLER_TEXT")

        class OriginalFailure(RuntimeError):
            pass

        failure = OriginalFailure("PRIVATE_EXCEPTION_TEXT")

        def forced_execute(_request):
            raise failure

        transaction._execute = forced_execute
        caught_exception = None
        traceback_names = []
        try:
            transaction.insert(request)
        except OriginalFailure as caught:
            caught_exception = caught
            cursor = caught.__traceback__
            while cursor is not None:
                traceback_names.append(cursor.tb_frame.f_code.co_name)
                cursor = cursor.tb_next
        else:
            self.fail("original insertion exception was not raised")
        serialized = repr(events)
        self.assertIs(failure, caught_exception)
        self.assertIn("forced_execute", traceback_names)
        self.assertEqual(1, sum(name == "insertion_finished"
                                for name, _fields in events))
        self.assertNotIn("PRIVATE_CALLER_TEXT", serialized)
        self.assertNotIn("PRIVATE_EXCEPTION_TEXT", serialized)
        with self.assertRaises(InsertionOperationExpired):
            transaction.insert(request)
        self.assertEqual(1, sum(name == "insertion_finished"
                                for name, _fields in events))

    def test_trace_failures_never_replace_the_insertion_result_or_primary_exception(self):
        def broken_trace(_name, **_fields):
            raise RuntimeError("trace sink failed")

        successful = InsertionTransaction(
            FakeTarget(), FakeClipboard(), FakeNativeInput(),
            settle_delay=lambda _seconds: None, trace=broken_trace)
        result = successful.insert(self.make_request("trace-failed-success"))
        self.assertEqual(InsertionOutcome.CONFIRMED, result.outcome)

        failed = InsertionTransaction(
            FakeTarget(), FakeClipboard(), FakeNativeInput(), trace=broken_trace)

        class OriginalFailure(RuntimeError):
            pass

        primary = OriginalFailure("original transaction failure")
        failed._execute = lambda _request: (_ for _ in ()).throw(primary)
        with self.assertRaises(OriginalFailure) as caught:
            failed.insert(self.make_request("trace-failed-exception"))
        self.assertIs(primary, caught.exception)

    def test_invalid_identifier_is_rejected_content_free_at_all_coordinator_boundaries(self):
        transaction = InsertionTransaction(
            FakeTarget(), FakeClipboard(), FakeNativeInput(),
            settle_delay=lambda _seconds: None)
        coordinator = InsertionCoordinator(transaction)
        sentinel = "PRIVATE_TRANSCRIPT_CONTENT"
        request = InsertionRequest(
            operation_id=sentinel,
            source="dictation",
            content_kind="text",
            text="content-free fixture",
            activation_target=TARGET)

        for action in (
                lambda: transaction.insert(request),
                lambda: coordinator.prepare(request),
                lambda: coordinator.submit(request),
                lambda: coordinator.status(sentinel)):
            with self.assertRaises(ValueError) as caught:
                action()
            self.assertNotIn(sentinel, str(caught.exception))

        self.assertNotIn(sentinel, coordinator._entries)
        self.assertNotIn(sentinel, coordinator._retired)
        self.assertNotIn(sentinel, transaction._completed)

    def test_trimmed_terminal_identity_cannot_start_a_second_insertion(self):
        sends = []

        class Transaction:
            def insert(_self, request):
                sends.append(request.operation_id)
                return InsertionResult(
                    request.operation_id, request.source,
                    InsertionOutcome.CONFIRMED, "confirmed", "Pasted.", 1)

        coordinator = InsertionCoordinator(Transaction(), retention=8)
        first = self.make_request("first-terminal")
        coordinator.submit(first)
        for index in range(24):
            coordinator.submit(self.make_request("terminal-{}".format(index)))

        self.assertEqual(
            "expired", coordinator.status(first.operation_id)["state"])
        with self.assertRaises(InsertionOperationExpired):
            coordinator.submit(first)
        self.assertEqual(1, sends.count(first.operation_id))

    def test_transaction_cache_trim_keeps_fixed_memory_replay_protection(self):
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput()
        transaction = InsertionTransaction(
            target, clipboard, native, cache_size=8,
            settle_delay=lambda _seconds: None)
        first = self.make_request("first-transaction-terminal")
        transaction.insert(first)
        for index in range(24):
            transaction.insert(self.make_request(
                "transaction-terminal-{}".format(index)))

        with self.assertRaises(InsertionOperationExpired):
            transaction.insert(first)
        self.assertEqual(25, native.send_calls)

    def test_prepared_operation_expires_to_a_non_reusable_tombstone(self):
        now = {"value": 10.0}
        coordinator = InsertionCoordinator(
            InsertionTransaction(FakeTarget(), FakeClipboard(), FakeNativeInput()),
            retention=8, clock=lambda: now["value"], prepared_ttl_s=2.0)
        request = self.make_request("abandoned-prepared")
        coordinator.prepare(request)
        now["value"] = 13.0

        status = coordinator.status(request.operation_id)

        self.assertEqual("expired", status["state"])
        self.assertEqual("unknown", status["outcome"])
        with self.assertRaises(InsertionOperationExpired):
            coordinator.submit(request)
        with self.assertRaises(InsertionRequestConflict):
            coordinator.prepare(self.make_request(
                request.operation_id, text="different"))

    def test_hung_pending_operation_becomes_unknown_without_a_second_send(self):
        now = {"value": 20.0}
        entered = threading.Event()
        release = threading.Event()
        sends = {"count": 0}

        class BlockingTransaction:
            def insert(_self, request):
                sends["count"] += 1
                entered.set()
                release.wait(2)
                return InsertionResult(
                    request.operation_id, request.source,
                    InsertionOutcome.CONFIRMED, "confirmed", "Pasted.", 1)

        coordinator = InsertionCoordinator(
            BlockingTransaction(), clock=lambda: now["value"],
            pending_ttl_s=2.0, join_timeout_s=0.0)
        request = self.make_request("hung-pending")
        owner = threading.Thread(target=lambda: coordinator.submit(request))
        owner.start()
        self.assertTrue(entered.wait(1))
        now["value"] = 23.0

        status = coordinator.status(request.operation_id)
        self.assertEqual("unknown", status["state"])
        self.assertIn("do not retry", status["message"].lower())
        with self.assertRaises(InsertionWaitTimeout):
            coordinator.submit(request)
        self.assertEqual(1, sends["count"])
        release.set()
        owner.join(2)
        self.assertEqual("terminal", coordinator.status(
            request.operation_id)["state"])
        self.assertEqual(1, sends["count"])

    def test_coordinator_capacity_is_bounded_for_prepared_completed_and_mixed_loads(self):
        now = {"value": 0.0}
        coordinator = InsertionCoordinator(
            InsertionTransaction(
                FakeTarget(), FakeClipboard(), FakeNativeInput(),
                settle_delay=lambda _seconds: None),
            retention=8, clock=lambda: now["value"], prepared_ttl_s=1.0)
        accepted = 0
        for index in range(24):
            request = self.make_request("load-{}".format(index))
            coordinator.prepare(request)
            accepted += 1
            if index % 3 == 0:
                coordinator.submit(request)
            now["value"] += 0.6
            self.assertLessEqual(len(coordinator._entries), 8)
            self.assertLessEqual(len(coordinator._retired), 8)
        self.assertEqual(24, accepted)

        with self.assertRaises(InsertionCoordinatorCapacityError):
            for index in range(100, 120):
                coordinator.prepare(self.make_request("prepared-{}".format(index)))
        self.assertLessEqual(len(coordinator._entries), 8)

    def test_hung_pending_load_stays_bounded_and_never_resubmits(self):
        now = {"value": 0.0}
        releases = {}
        entered = {}
        sends = {}

        class BlockingTransaction:
            def insert(_self, request):
                sends[request.operation_id] = (
                    sends.get(request.operation_id, 0) + 1)
                entered[request.operation_id].set()
                releases[request.operation_id].wait(2)
                return InsertionResult(
                    request.operation_id, request.source,
                    InsertionOutcome.CONFIRMED, "confirmed", "Pasted.", 1)

        coordinator = InsertionCoordinator(
            BlockingTransaction(), retention=8, clock=lambda: now["value"],
            pending_ttl_s=1.0, join_timeout_s=0.0)
        threads = []
        requests = []
        for index in range(8):
            request = self.make_request("hung-load-{}".format(index))
            requests.append(request)
            entered[request.operation_id] = threading.Event()
            releases[request.operation_id] = threading.Event()
            thread = threading.Thread(
                target=lambda value=request: coordinator.submit(value))
            threads.append(thread)
            thread.start()
            self.assertTrue(entered[request.operation_id].wait(1))

        now["value"] = 2.0
        for request in requests:
            self.assertEqual("unknown", coordinator.status(
                request.operation_id)["state"])
            with self.assertRaises(InsertionWaitTimeout):
                coordinator.submit(request)
        with self.assertRaises(InsertionCoordinatorCapacityError):
            coordinator.prepare(self.make_request("hung-load-overflow"))
        self.assertEqual(8, len(coordinator._entries))
        self.assertTrue(all(count == 1 for count in sends.values()))

        for event in releases.values():
            event.set()
        for thread in threads:
            thread.join(2)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertTrue(all(count == 1 for count in sends.values()))
        self.assertTrue(all(
            coordinator.status(request.operation_id)["state"] == "terminal"
            for request in requests))

    def test_correction_undo_and_replacement_share_one_operation_and_target_lease(self):
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput()
        transaction = InsertionTransaction(
            target, clipboard, native, settle_delay=lambda _seconds: None)
        lease = TargetLease(
            TARGET, "correction_replace", "confirmed_insertion", True)

        result = transaction.insert(self.make_request(
            "correction-one-operation",
            source="correction_replace",
            target_lease=lease,
            undo_before_paste=True,
        ))

        self.assertEqual(InsertionOutcome.CONFIRMED, result.outcome)
        self.assertEqual(1, native.undo_calls)
        self.assertEqual(1, native.send_calls)
        self.assertEqual(2, result.send_count)
        self.assertEqual(lease, result.target_lease)

    def test_editability_and_privilege_policy_fails_closed_with_stable_reasons(self):
        cases = [
            ("button", TargetContext(
                101, 202, 303, 404, "medium", "Button", False,
                TargetEditability.NOT_EDITABLE), True,
             InsertionReason.NOT_EDITABLE),
            ("read-only", TargetContext(
                101, 202, 303, 404, "medium", "Edit", True,
                TargetEditability.NOT_EDITABLE, read_only=True), True,
             InsertionReason.READ_ONLY),
            ("protected", TargetContext(
                101, 202, 303, 404, "medium", "Edit", True,
                TargetEditability.NOT_EDITABLE, protected=True), True,
             InsertionReason.PROTECTED_FIELD),
            ("no-focus", TargetContext(
                101, 202, 303, 0, "medium", "", False,
                TargetEditability.NOT_EDITABLE), True,
             InsertionReason.NO_FOCUS),
            ("unknown-editability", TargetContext(
                101, 202, 303, 404, "medium", "Custom", False,
                TargetEditability.UNKNOWN), True,
             InsertionReason.EDITABILITY_UNKNOWN),
            ("unknown-integrity", TARGET, None,
             InsertionReason.UNKNOWN_INTEGRITY),
        ]
        for name, context, injectable, expected_reason in cases:
            with self.subTest(name=name):
                target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput()
                target.active = context
                target.injectable = injectable
                transaction = InsertionTransaction(target, clipboard, native)
                result = transaction.insert(self.make_request(
                    "policy-" + name, activation_target=context))
                self.assertEqual(InsertionOutcome.SAVED_ONLY, result.outcome)
                self.assertEqual(expected_reason.value, result.reason)
                self.assertEqual(0, native.send_calls)
                self.assertEqual(0, clipboard.write_calls)
                self.assertNotIn("Pasted", result.message)


if __name__ == "__main__":
    unittest.main()
