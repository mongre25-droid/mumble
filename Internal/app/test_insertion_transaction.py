#!/usr/bin/env python3
"""Deterministic behaviour tests for the target-bound insertion transaction."""

import threading
import unittest

from insertion import (
    ClipboardOwnership,
    ClipboardSnapshot,
    InsertionOutcome,
    InsertionRequest,
    InsertionTransaction,
    NativeAcceptance,
    TargetContext,
)


TARGET = TargetContext(
    window=101,
    process_id=202,
    thread_id=303,
    focused_child=404,
    integrity="medium",
    control_class="Edit",
    has_caret=True,
)
OTHER_TARGET = TargetContext(
    window=501,
    process_id=502,
    thread_id=503,
    focused_child=504,
    integrity="medium",
    control_class="Edit",
    has_caret=True,
)


class FakeTarget:
    def __init__(self):
        self.active = TARGET
        self.current_values = []
        self.injectable = True
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
        return ownership == self.owner and self.sequence == ownership.sequence

    def restore(self, snapshot, ownership):
        self.restore_calls += 1
        self.sequence += 1
        return True


class FakeNativeInput:
    def __init__(self, acceptance=None):
        self.send_calls = 0
        self.ready_result = (True, "")
        self.acceptance = acceptance or NativeAcceptance(
            requested=4, accepted=4, submitted=True, confirmation=True)
        self.on_send = lambda: None
        self.raise_after_start = False

    def ready(self, timeout_s):
        return self.ready_result

    def send_paste(self):
        self.send_calls += 1
        self.on_send()
        if self.raise_after_start:
            raise RuntimeError("driver failed after submit")
        return self.acceptance


class InsertionTransactionTests(unittest.TestCase):
    def make_request(self, operation_id="dictation-1", **changes):
        values = dict(
            operation_id=operation_id,
            source="dictation",
            content_kind="text",
            text="new words",
            activation_target=TARGET,
        )
        values.update(changes)
        return InsertionRequest(**values)

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

        result = transaction.insert(self.make_request("focus-restore"))

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

    def test_elevation_mismatch_never_touches_clipboard_or_input(self):
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput()
        target.injectable = False
        transaction = InsertionTransaction(target, clipboard, native)

        result = transaction.insert(self.make_request("elevated"))

        self.assertEqual(InsertionOutcome.SAVED_ONLY, result.outcome)
        self.assertIn("privilege", result.reason)
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
        self.assertIn("clipboard write failed", result.reason)
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
        self.assertIn("could not confirm", result.message)

    def test_external_clipboard_change_is_never_overwritten(self):
        target, clipboard, native = FakeTarget(), FakeClipboard(), FakeNativeInput()
        native.on_send = lambda: setattr(clipboard, "sequence", clipboard.sequence + 1)
        transaction = InsertionTransaction(target, clipboard, native,
                                           settle_delay=lambda _seconds: None)

        result = transaction.insert(self.make_request("external-copy"))

        self.assertEqual(InsertionOutcome.CONFIRMED, result.outcome)
        self.assertFalse(result.clipboard_restored)
        self.assertTrue(result.clipboard_changed_externally)
        self.assertEqual(0, clipboard.restore_calls)

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


if __name__ == "__main__":
    unittest.main()
