#!/usr/bin/env python3
"""Focused Level A contract tests for paste-anywhere delivery."""

import threading
import unittest
from unittest.mock import patch
import uuid

from insertion import (
    DeliveryIntent,
    ImagePayload,
    InsertionModule,
    InsertionOutcome,
    InsertionOperationExpired,
    InsertionRequest,
    InsertionRequestConflict,
    InsertionReason,
    InsertionTransaction,
    NativeAcceptance,
    RichPayload,
    TargetContext,
    TargetEditability,
    TargetLease,
    TextPayload,
)
from test_insertion_transaction import FakeClipboard, FakeNativeInput, FakeTarget
from windows_insertion import (
    KEYEVENTF_UNICODE,
    UIAEvidence,
    WindowsClipboardAdapter,
    WindowsNativeInputAdapter,
    WindowsTargetAdapter,
    WindowsUIAutomationEvidenceProvider,
)


def operation_id(label):
    return uuid.uuid5(uuid.NAMESPACE_OID, label).hex


def browser_target(**changes):
    values = dict(
        window=101,
        process_id=202,
        thread_id=303,
        focused_child=404,
        integrity="medium",
        control_class="Chrome_WidgetWin_1",
        has_caret=False,
        editability=TargetEditability.UNKNOWN,
        integrity_relation="same",
    )
    values.update(changes)
    return TargetContext(**values)


class ReliablePasteRedTests(unittest.TestCase):
    def test_stable_unknown_chromium_destination_attempts_delivery(self):
        selected = browser_target()
        target = FakeTarget()
        target.active = selected
        clipboard = FakeClipboard()
        native = FakeNativeInput(NativeAcceptance(
            requested=4, accepted=4, submitted=True, confirmation=None))
        result = InsertionTransaction(
            target, clipboard, native, settle_delay=lambda _seconds: None,
        ).insert(InsertionRequest(
            operation_id=operation_id("stable-chromium"),
            source="dictation",
            content_kind="text",
            activation_target=selected,
            target_lease=TargetLease(selected, "dictation", "stop", False),
            text="browser words",
        ))

        self.assertEqual(InsertionOutcome.SENT_UNCONFIRMED, result.outcome)
        self.assertEqual(1, native.send_calls)

    def test_reliably_different_uia_identity_changes_the_destination(self):
        selected = browser_target(
            uia_runtime_id=(42, 1), uia_observed=True,
        )
        replacement = browser_target(
            uia_runtime_id=(42, 2), uia_observed=True,
        )

        self.assertFalse(selected.same_destination(replacement))

    def test_windows_uia_provider_returns_consecutively_stable_focus(self):
        class Element:
            def __init__(self, runtime_id, has_keyboard_focus):
                self.runtime_id = runtime_id
                self.CurrentAutomationId = "fieldA"
                self.CurrentClassName = "field"
                self.CurrentControlType = 50004
                self.CurrentHasKeyboardFocus = has_keyboard_focus
                self.CurrentBoundingRectangle = type("Rectangle", (), {
                    "left": 10,
                    "top": 20,
                    "right": 210,
                    "bottom": 80,
                })()

            def GetRuntimeId(self):
                return self.runtime_id

        class Automation:
            def __init__(self, entries):
                self.elements = iter(Element(*entry) for entry in entries)
                self.calls = 0

            def GetFocusedElement(self):
                self.calls += 1
                return next(self.elements)

        class CUIAutomation:
            _reg_clsid_ = object()

        class UIAModule:
            pass

        UIAModule.CUIAutomation = CUIAutomation
        UIAModule.IUIAutomation = object()

        automation = Automation((
            ((42, 1), False),
            ((42, 1), False),
            ((42, 2), True),
            ((42, 2), True),
            ((42, 2), True),
            ((42, 2), True),
        ))
        automation_creations = 0

        class ComtypesModule:
            @staticmethod
            def CoInitialize():
                return None

            @staticmethod
            def CoUninitialize():
                return None

            @staticmethod
            def CoCreateInstance(_class_id, interface=None):
                nonlocal automation_creations
                self.assertIs(interface, UIAModule.IUIAutomation)
                automation_creations += 1
                return automation

        class ClientModule:
            @staticmethod
            def GetModule(_name):
                return UIAModule

        def imported(name):
            return {
                "comtypes": ComtypesModule,
                "comtypes.client": ClientModule,
            }[name]

        provider = WindowsUIAutomationEvidenceProvider()
        with patch("windows_insertion.importlib.import_module", side_effect=imported):
            result = provider.capture(0.20)
            repeated = provider.capture(0.20)

        self.assertEqual((42, 2), result.runtime_id)
        self.assertTrue(result.observed)
        self.assertEqual("observed", result.state)
        self.assertEqual((42, 2), repeated.runtime_id)
        self.assertEqual(1, automation_creations)

        automation = Automation((
            ((42, 3), True),
            ((42, 4), True),
            ((42, 3), True),
            ((42, 4), True),
        ))
        with patch("windows_insertion.importlib.import_module", side_effect=imported):
            unstable = WindowsUIAutomationEvidenceProvider().capture(0.20)

        self.assertEqual("unstable", unstable.state)
        self.assertTrue(unstable.observed)
        self.assertEqual(4, automation.calls)

    def test_public_module_never_sends_on_unstable_uia_evidence(self):
        unstable = browser_target(
            uia_runtime_id=(42, 9),
            uia_observed=True,
            uia_state="unstable",
        )
        target = FakeTarget()
        target.active = unstable
        clipboard = FakeClipboard()
        native = ModuleNative()
        module = InsertionModule(
            target, clipboard, native, settle_delay=lambda _seconds: None,
        )
        operation = operation_id("unstable-uia-public-module")

        receipt = module.begin(operation, "dictation")
        result = module.deliver(operation, TextPayload("stable words"))
        status = module.status(operation)

        self.assertIs(unstable, receipt.target)
        self.assertEqual(InsertionOutcome.SAVED_ONLY, result.outcome)
        self.assertEqual(InsertionReason.TARGET_CHANGED.value, result.reason)
        self.assertIs(result, status)
        self.assertEqual(0, clipboard.snapshot_calls)
        self.assertEqual(0, native.send_calls)
        self.assertEqual(0, native.unicode_calls)

    def test_windows_uia_provider_does_not_queue_behind_a_timeout(self):
        class Element:
            CurrentHasKeyboardFocus = True

            @staticmethod
            def GetRuntimeId():
                return (51, 7)

        class Automation:
            def __init__(self):
                self.calls = 0
                self.entered = threading.Event()
                self.release = threading.Event()

            def GetFocusedElement(self):
                self.calls += 1
                if self.calls == 1:
                    self.entered.set()
                    self.release.wait(1.0)
                return Element()

        automation = Automation()

        class CUIAutomation:
            _reg_clsid_ = object()

        class UIAModule:
            pass

        UIAModule.CUIAutomation = CUIAutomation
        UIAModule.IUIAutomation = object()

        class ComtypesModule:
            @staticmethod
            def CoInitialize():
                return None

            @staticmethod
            def CoCreateInstance(_class_id, interface=None):
                self.assertIs(interface, UIAModule.IUIAutomation)
                return automation

        class ClientModule:
            @staticmethod
            def GetModule(_name):
                return UIAModule

        def imported(name):
            return {
                "comtypes": ComtypesModule,
                "comtypes.client": ClientModule,
            }[name]

        provider = WindowsUIAutomationEvidenceProvider()
        with patch("windows_insertion.importlib.import_module", side_effect=imported):
            first = provider.capture(0.01)
            self.assertTrue(automation.entered.wait(0.20))
            repeated = [provider.capture(0.01) for _attempt in range(3)]
            automation.release.set()
            final = UIAEvidence(state="timed_out")
            for _attempt in range(20):
                final = provider.capture(0.20)
                if final.state == "observed":
                    break
                threading.Event().wait(0.01)

        self.assertEqual("timed_out", first.state)
        self.assertTrue(all(item.state == "timed_out" for item in repeated))
        self.assertEqual("observed", final.state)
        self.assertEqual((51, 7), final.runtime_id)
        self.assertEqual(4, automation.calls)

    def test_public_module_blocks_rekeyed_uia_despite_same_leaf_metadata(self):
        selected = browser_target(
            uia_runtime_id=(42, 1),
            uia_observed=True,
            uia_state="observed",
            control_class="Chrome_RenderWidgetHostHWND",
        )
        rekeyed = browser_target(
            uia_runtime_id=(42, 2),
            uia_observed=True,
            uia_state="observed",
            control_class="Chrome_RenderWidgetHostHWND",
        )
        target = FakeTarget()
        target.active = selected
        clipboard = FakeClipboard()
        native = ModuleNative()
        module = InsertionModule(
            target, clipboard, native, settle_delay=lambda _seconds: None,
        )
        target.restore_result = False
        operation = operation_id("rekeyed-uia-same-leaf-metadata")

        module.begin(operation, "dictation")
        target.active = rekeyed
        result = module.deliver(operation, TextPayload("stable words"))

        self.assertEqual(InsertionOutcome.SAVED_ONLY, result.outcome)
        self.assertEqual(InsertionReason.TARGET_CHANGED.value, result.reason)
        self.assertEqual(0, native.send_calls)

    def test_exhausted_clipboard_acquisition_falls_back_to_unicode_text(self):
        class UnicodeNative(FakeNativeInput):
            def __init__(self):
                super().__init__()
                self.unicode_calls = 0

            def send_unicode(self, text):
                self.unicode_calls += 1
                return NativeAcceptance(
                    requested=len(text) * 2,
                    accepted=len(text) * 2,
                    submitted=True,
                    confirmation=None,
                )

        target = FakeTarget()
        clipboard = FakeClipboard()
        clipboard.fail_write = True
        native = UnicodeNative()
        result = InsertionTransaction(
            target, clipboard, native, settle_delay=lambda _seconds: None,
        ).insert(InsertionRequest(
            operation_id=operation_id("clipboard-unicode-fallback"),
            source="dictation",
            content_kind="text",
            activation_target=target.active,
            target_lease=TargetLease(
                target.active, "dictation", "stop", False),
            text="fallback words",
        ))

        self.assertEqual(InsertionOutcome.SENT_UNCONFIRMED, result.outcome)
        self.assertEqual(1, native.unicode_calls)
        self.assertEqual(0, native.send_calls)


class ModuleNative(FakeNativeInput):
    def __init__(self, acceptance=None, unicode_acceptance=None):
        super().__init__(acceptance)
        self.unicode_calls = 0
        self.unicode_acceptance = unicode_acceptance or NativeAcceptance(
            requested=4, accepted=4, submitted=True, confirmation=None)

    def send_unicode(self, _text):
        self.unicode_calls += 1
        return self.unicode_acceptance


class VirtualDeliveryClock:
    def __init__(self):
        self.now = 0.0
        self.visible_at = None

    def settle(self, seconds):
        self.now += float(seconds)

    @property
    def visible(self):
        return self.visible_at is not None and self.now >= self.visible_at


class DeferredUnicodeNative(ModuleNative):
    """Model Windows accepting a long Unicode batch before it visibly drains."""

    def __init__(self, clock, visible_delay_s):
        super().__init__()
        self.clock = clock
        self.visible_delay_s = float(visible_delay_s)

    def send_unicode(self, text):
        acceptance = super().send_unicode(text)
        self.clock.visible_at = self.clock.now + self.visible_delay_s
        return acceptance


class TransientZeroPasteNative(ModuleNative):
    def __init__(self, after_zero=None):
        super().__init__()
        self.after_zero = after_zero

    def send_paste(self):
        self.send_calls += 1
        if self.send_calls == 1:
            if self.after_zero is not None:
                self.after_zero()
            return NativeAcceptance(
                requested=4, accepted=0, submitted=False, confirmation=None)
        return NativeAcceptance(
            requested=4, accepted=4, submitted=True, confirmation=None)


class ManualClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


class BlockingModuleNative(ModuleNative):
    def __init__(self):
        super().__init__(NativeAcceptance(
            requested=4, accepted=4, submitted=True, confirmation=None))
        self.started = threading.Event()
        self.release = threading.Event()

    def send_paste(self):
        self.send_calls += 1
        self.started.set()
        if not self.release.wait(2.0):
            raise AssertionError("blocked paste was not released")
        return self.acceptance


class ReliablePasteModuleTests(unittest.TestCase):
    def make_module(self, *, target=None, clipboard=None, native=None,
                    elevated_helper=None, **module_options):
        target = target or FakeTarget()
        clipboard = clipboard or FakeClipboard()
        native = native or ModuleNative(NativeAcceptance(
            requested=4, accepted=4, submitted=True, confirmation=None))
        module = InsertionModule(
            target, clipboard, native, elevated_helper=elevated_helper,
            settle_delay=lambda _seconds: None,
            **module_options,
        )
        return module, target, clipboard, native

    def test_short_unicode_fallback_settles_before_terminal_status(self):
        clock = VirtualDeliveryClock()
        clipboard = FakeClipboard()
        clipboard.fail_write = True
        native = DeferredUnicodeNative(clock, visible_delay_s=0.80)
        module = InsertionModule(
            FakeTarget(), clipboard, native, settle_delay=clock.settle)
        op = operation_id("short-unicode-visible-delivery")
        # Each symbol occupies two UTF-16 code units on Windows. This catches
        # status timing that incorrectly counts Python characters instead.
        prompt = "\U0001f642" * 128

        module.begin(op, "dictation")
        result = module.deliver(op, TextPayload(prompt))

        measured_gap_ms = max(
            0.0, ((clock.visible_at or clock.now) - clock.now) * 1000.0)
        self.assertTrue(
            clock.visible,
            "terminal status preceded visible prompt delivery by "
            "{:.0f}ms".format(measured_gap_ms),
        )
        self.assertEqual("terminal", result.state)
        self.assertEqual(1, native.unicode_calls)

    def test_long_prompt_never_uses_slow_unicode_fallback(self):
        cases = (
            ("ascii", "p" * 1000),
            ("utf16-boundary", "\U0001f642" * 129),
        )
        for label, prompt in cases:
            with self.subTest(label=label):
                clipboard = FakeClipboard()
                clipboard.fail_write = True
                module, _target, _clipboard, native = self.make_module(
                    clipboard=clipboard)
                op = operation_id(
                    "long-prompt-saved-without-slow-unicode-" + label)

                module.begin(op, "dictation")
                result = module.deliver(op, TextPayload(prompt))

                self.assertEqual(InsertionOutcome.SAVED_ONLY, result.outcome)
                self.assertEqual(0, native.send_calls)
                self.assertEqual(0, native.unicode_calls)
                self.assertEqual(
                    "payload_too_large_for_direct_input",
                    result.attempt_ledger[-1].reason,
                )

    def test_transient_zero_paste_retries_fast_clipboard_before_unicode(self):
        native = TransientZeroPasteNative()
        module, _target, _clipboard, _native = self.make_module(native=native)
        op = operation_id("transient-zero-fast-clipboard-retry")

        module.begin(op, "dictation")
        result = module.deliver(op, TextPayload("p" * 1000))

        self.assertEqual(InsertionOutcome.SENT_UNCONFIRMED, result.outcome)
        self.assertEqual(2, native.send_calls)
        self.assertEqual(0, native.unicode_calls)
        self.assertEqual(
            ["clipboard_paste", "clipboard_paste"],
            [attempt.adapter for attempt in result.attempt_ledger],
        )

    def test_transient_zero_retry_stops_when_target_changes(self):
        target = FakeTarget()
        target.restore_result = False
        original = target.active
        changed = browser_target(window=909, process_id=808, thread_id=707,
                                 focused_child=606)
        native = TransientZeroPasteNative(
            after_zero=lambda: setattr(target, "active", changed))
        module, _target, _clipboard, _native = self.make_module(
            target=target, native=native)
        op = operation_id("transient-zero-target-changed")

        receipt = module.begin(op, "dictation")
        result = module.deliver(op, TextPayload("target-bound prompt"))

        self.assertIs(original, receipt.target)
        self.assertEqual(InsertionOutcome.SAVED_ONLY, result.outcome)
        self.assertEqual(InsertionReason.TARGET_CHANGED.value, result.reason)
        self.assertEqual(1, native.send_calls)
        self.assertEqual(0, native.unicode_calls)

    def test_stalled_public_delivery_expires_once_and_late_completion_wins(self):
        clock = ManualClock()
        native = BlockingModuleNative()
        module, _target, _clipboard, _native = self.make_module(
            native=native, clock=clock)
        op = operation_id("public-stalled-deadline")
        payload = TextPayload("one original delivery")
        module.begin(op, "dictation")
        completed = []
        failures = []

        def deliver():
            try:
                completed.append(module.deliver(op, payload))
            except BaseException as exc:
                failures.append(exc)

        worker = threading.Thread(target=deliver)
        worker.start()
        self.assertTrue(native.started.wait(1.0))
        try:
            clock.advance(10_000.0)
            polled = [module.status(op) for _ in range(100)]
            abandoned = module.abandon(op)
            replayed = module.deliver(op, payload)

            self.assertEqual({"unknown"}, {item.state for item in polled})
            self.assertEqual("unknown", abandoned["state"])
            self.assertEqual("unknown", replayed.state)
            self.assertEqual(1, native.send_calls)
            with self.assertRaises(InsertionRequestConflict):
                module.deliver(op, TextPayload("changed payload"))
        finally:
            native.release.set()
            worker.join(2.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual([], failures)
        self.assertEqual(1, len(completed))
        self.assertIs(completed[0], module.status(op))
        self.assertEqual("terminal", module.status(op).state)
        self.assertEqual(1, native.send_calls)

    def test_many_overdue_public_deliveries_release_registry_capacity(self):
        clock = ManualClock()
        native = BlockingModuleNative()
        module, _target, _clipboard, _native = self.make_module(
            native=native, clock=clock, retention=8)
        workers = []
        old_operations = [operation_id("overdue-{}".format(index))
                          for index in range(8)]
        for op in old_operations:
            module.begin(op, "dictation")
            worker = threading.Thread(
                target=module.deliver,
                args=(op, TextPayload("payload-{}".format(op))),
            )
            worker.start()
            workers.append(worker)
        self.assertTrue(native.started.wait(1.0))
        try:
            for _ in range(100):
                states = [module.status(op).state for op in old_operations]
                if set(states) == {"pending"}:
                    break
            self.assertEqual({"pending"}, set(states))
            clock.advance(10_000.0)
            self.assertEqual(
                {"unknown"},
                {module.status(op).state for op in old_operations},
            )

            for index in range(8):
                module.begin(
                    operation_id("replacement-{}".format(index)),
                    "dictation",
                )
        finally:
            native.release.set()
            for worker in workers:
                worker.join(2.0)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(8, native.send_calls)

    def test_terminal_records_and_replay_protection_stay_bounded(self):
        module, _target, _clipboard, native = self.make_module(retention=8)
        operations = [operation_id("bounded-terminal-{}".format(index))
                      for index in range(24)]
        for index, op in enumerate(operations):
            module.begin(op, "dictation")
            module.deliver(op, TextPayload("payload-{}".format(index)))

        with self.assertRaises(InsertionOperationExpired):
            module.deliver(operations[0], TextPayload("payload-0"))
        with self.assertRaises(InsertionRequestConflict):
            module.deliver(operations[-1], TextPayload("changed"))
        self.assertEqual(24, native.send_calls)

    def test_begin_deliver_and_status_share_one_operation(self):
        module, _target, _clipboard, native = self.make_module()
        op = operation_id("public-interface")

        receipt = module.begin(op, "dictation")
        pending = module.status(op)
        delivered = module.deliver(op, TextPayload("one delivery"))
        polled = module.status(op)
        replayed = module.deliver(op, TextPayload("one delivery"))

        self.assertEqual(op, receipt.operation_id)
        self.assertEqual("prepared", pending.state)
        self.assertIs(delivered, polled)
        self.assertIs(delivered, replayed)
        self.assertEqual(1, native.send_calls)

    def test_same_id_with_changed_payload_is_rejected(self):
        module, _target, _clipboard, native = self.make_module()
        op = operation_id("payload-conflict")
        module.begin(op, "dictation")
        module.deliver(op, TextPayload("first"))

        with self.assertRaises(InsertionRequestConflict):
            module.deliver(op, TextPayload("changed"))
        self.assertEqual(1, native.send_calls)

    def test_reuse_destination_from_keeps_the_prior_target(self):
        module, target, _clipboard, _native = self.make_module()
        first = operation_id("reuse-first")
        second = operation_id("reuse-second")
        original = browser_target(uia_runtime_id=(7, 8), uia_observed=True)
        target.active = original
        module.begin(first, "dictation")
        target.active = browser_target(window=900, process_id=901)

        receipt = module.begin(
            second, "correction_replace", reuse_destination_from=first)

        self.assertEqual(original, receipt.target)

    def test_optional_uia_matrix_attempts_every_non_conclusive_target(self):
        cases = [
            ("equal", browser_target(uia_runtime_id=(1,), uia_observed=True),
             browser_target(uia_runtime_id=(1,), uia_observed=True)),
            ("absent", browser_target(), browser_target()),
            ("timeout", browser_target(uia_state="timed_out"),
             browser_target(uia_state="timed_out")),
            ("stop-only", browser_target(
                uia_runtime_id=(1,), uia_observed=True), browser_target()),
            ("delivery-only", browser_target(), browser_target(
                uia_runtime_id=(1,), uia_observed=True)),
        ]
        for label, selected, delivery in cases:
            with self.subTest(label=label):
                target = FakeTarget()
                target.active = selected
                module, _target, _clipboard, native = self.make_module(
                    target=target)
                op = operation_id("uia-" + label)
                module.begin(op, "dictation")
                target.active = delivery

                result = module.deliver(op, TextPayload("words"))

                self.assertEqual(
                    InsertionOutcome.SENT_UNCONFIRMED, result.outcome)
                self.assertEqual(1, native.send_calls)

    def test_changed_native_or_reliably_changed_uia_never_uses_replacement(self):
        cases = [
            ("native", browser_target(), browser_target(window=999)),
            ("uia", browser_target(
                uia_runtime_id=(1,), uia_observed=True), browser_target(
                    uia_runtime_id=(2,), uia_observed=True)),
        ]
        for label, selected, replacement in cases:
            with self.subTest(label=label):
                target = FakeTarget()
                target.active = selected
                target.restore_result = False
                module, _target, clipboard, native = self.make_module(
                    target=target)
                op = operation_id("different-" + label)
                module.begin(op, "dictation")
                target.active = replacement

                result = module.deliver(op, TextPayload("do not redirect"))

                self.assertEqual(InsertionReason.TARGET_CHANGED.value,
                                 result.reason)
                self.assertEqual(0, clipboard.write_calls)
                self.assertEqual(0, native.send_calls)

    def test_exact_original_is_restored_before_delivery_where_allowed(self):
        target = FakeTarget()
        selected = browser_target()
        target.active = selected
        module, _target, _clipboard, native = self.make_module(target=target)
        op = operation_id("restore-original")
        module.begin(op, "dictation")
        target.active = browser_target(window=999)

        result = module.deliver(op, TextPayload("restored"))

        self.assertEqual(InsertionOutcome.SENT_UNCONFIRMED, result.outcome)
        self.assertEqual(1, target.restore_calls)
        self.assertEqual(1, native.send_calls)

    def test_uia_conflict_requires_positive_same_element_restoration_evidence(self):
        selected = browser_target(
            uia_runtime_id=(1,), uia_observed=True)
        replacement = browser_target(
            uia_runtime_id=(2,), uia_observed=True)
        one_sided_after_restore = browser_target(uia_state="unavailable")

        class OneSidedRestore(FakeTarget):
            def restore(self, _target, _timeout_s):
                self.restore_calls += 1
                self.active = one_sided_after_restore
                return True

        target = OneSidedRestore()
        target.active = selected
        module, _target, _clipboard, native = self.make_module(target=target)
        op = operation_id("uia-restoration-proof")
        module.begin(op, "dictation")
        target.active = replacement

        result = module.deliver(op, TextPayload("do not redirect"))

        self.assertEqual(InsertionReason.TARGET_CHANGED.value, result.reason)
        self.assertEqual(0, native.send_calls)

    def test_control_metadata_never_globally_suppresses_clipboard_paste(self):
        cases = [
            browser_target(editability=TargetEditability.UNKNOWN),
            browser_target(editability=TargetEditability.NOT_EDITABLE),
            browser_target(read_only=True),
            browser_target(protected=True),
            browser_target(focused_child=0),
        ]
        for index, selected in enumerate(cases):
            with self.subTest(index=index):
                target = FakeTarget()
                target.active = selected
                module, _target, _clipboard, native = self.make_module(
                    target=target)
                op = operation_id("metadata-{}".format(index))
                module.begin(op, "dictation")

                result = module.deliver(op, TextPayload("attempt"))

                self.assertEqual(
                    InsertionOutcome.SENT_UNCONFIRMED, result.outcome)
                self.assertEqual(1, native.send_calls)

    def test_clipboard_no_send_falls_back_only_for_text(self):
        for payload in (ImagePayload("fixture.png"),
                        RichPayload("plain", "<b>plain</b>")):
            with self.subTest(payload=type(payload).__name__):
                clipboard = FakeClipboard()
                clipboard.fail_write = True
                module, _target, _clipboard, native = self.make_module(
                    clipboard=clipboard)
                op = operation_id("no-fallback-" + type(payload).__name__)
                module.begin(op, "deck_image")

                result = module.deliver(op, payload)

                self.assertIn(result.outcome, {
                    InsertionOutcome.NOT_SENT, InsertionOutcome.SAVED_ONLY})
                self.assertEqual(0, native.send_calls)
                self.assertEqual(0, native.unicode_calls)

    def test_zero_paste_events_permit_text_fallback(self):
        native = ModuleNative(
            NativeAcceptance(4, 0, False, None),
            NativeAcceptance(8, 8, True, None),
        )
        module, _target, _clipboard, _native = self.make_module(native=native)
        op = operation_id("zero-paste-fallback")
        module.begin(op, "dictation")

        result = module.deliver(op, TextPayload("fallback"))

        self.assertEqual(InsertionOutcome.SENT_UNCONFIRMED, result.outcome)
        self.assertEqual(2, native.send_calls)
        self.assertEqual(1, native.unicode_calls)

    def test_positive_or_partial_paste_events_are_terminal(self):
        cases = [
            ("full", NativeAcceptance(4, 4, True, None),
             InsertionOutcome.SENT_UNCONFIRMED),
            ("partial", NativeAcceptance(4, 2, True, None),
             InsertionOutcome.UNCERTAIN),
        ]
        for label, acceptance, expected in cases:
            with self.subTest(label=label):
                native = ModuleNative(acceptance)
                module, _target, _clipboard, _native = self.make_module(
                    native=native)
                op = operation_id("terminal-" + label)
                module.begin(op, "dictation")

                result = module.deliver(op, TextPayload("once"))

                self.assertEqual(expected, result.outcome)
                self.assertEqual(0, native.unicode_calls)

    def test_higher_integrity_without_helper_needs_permission(self):
        module, target, clipboard, native = self.make_module()
        target.injectable = False
        op = operation_id("higher-integrity")
        module.begin(op, "dictation")

        result = module.deliver(op, TextPayload("saved"))

        self.assertEqual(InsertionOutcome.NOT_SENT, result.outcome)
        self.assertEqual(InsertionReason.PERMISSION_NEEDED.value, result.reason)
        self.assertEqual(0, clipboard.write_calls)
        self.assertEqual(0, native.send_calls)

    def test_helper_dispatch_without_accepted_events_is_terminal_uncertain(self):
        class Helper:
            @staticmethod
            def available():
                return True

            @staticmethod
            def deliver(_request):
                return NativeAcceptance(
                    requested=4, accepted=0, submitted=True,
                    confirmation=None)

        module, target, clipboard, native = self.make_module(
            elevated_helper=Helper())
        target.injectable = False
        op = operation_id("helper-dispatched")
        module.begin(op, "dictation")

        result = module.deliver(op, TextPayload("possibly sent"))

        self.assertEqual(InsertionOutcome.UNCERTAIN, result.outcome)
        self.assertEqual("may_have_sent", result.attempt_ledger[-1].state.value)
        self.assertEqual(0, clipboard.write_calls)
        self.assertEqual(0, native.send_calls)

    def test_partial_correction_undo_prevents_replacement(self):
        native = ModuleNative()
        native.undo_acceptance = NativeAcceptance(4, 2, True, None)
        module, _target, _clipboard, _native = self.make_module(native=native)
        op = operation_id("partial-correction")
        module.begin(op, "correction_replace")

        result = module.deliver(
            op, TextPayload("corrected"), DeliveryIntent.REPLACE)

        self.assertEqual(InsertionOutcome.UNCERTAIN, result.outcome)
        self.assertEqual(1, native.undo_calls)
        self.assertEqual(0, native.send_calls)
        self.assertEqual(0, native.unicode_calls)


class WindowsAdapterContractTests(unittest.TestCase):
    class Surface:
        def GetForegroundWindow(self):
            return 101

        def GetWindowThreadProcessId(self, _hwnd, pid):
            pid._obj.value = 202
            return 303

        def GetGUIThreadInfo(self, _tid, info):
            info._obj.hwndFocus = 404
            info._obj.hwndCaret = 0
            return 1

        def GetClassNameW(self, _hwnd, buf, _size):
            buf.value = "Chrome_WidgetWin_1"
            return len(buf.value)

        def GetWindowLongW(self, _hwnd, _index):
            return 0

        def IsWindowEnabled(self, _hwnd):
            return 1

    def test_windows_capture_includes_process_creation_and_optional_uia(self):
        class Provider:
            def capture(self, _timeout_s):
                return UIAEvidence((8, 9), True, "observed")

        adapter = object.__new__(WindowsTargetAdapter)
        adapter.user32 = self.Surface()
        adapter._own_integrity = "medium"
        adapter._process_integrity = lambda _pid: "medium"
        adapter._process_creation_identity = lambda _pid: 123456789
        adapter._uia_provider = Provider()
        adapter._uia_timeout_s = 0.01

        target = adapter.current()

        self.assertEqual(123456789, target.process_creation_id)
        self.assertEqual((8, 9), target.uia_runtime_id)
        self.assertTrue(target.uia_observed)

    def test_windows_capture_treats_uia_failure_as_diagnostic(self):
        class Provider:
            def capture(self, _timeout_s):
                raise RuntimeError("provider unavailable")

        adapter = object.__new__(WindowsTargetAdapter)
        adapter.user32 = self.Surface()
        adapter._own_integrity = "medium"
        adapter._process_integrity = lambda _pid: "medium"
        adapter._process_creation_identity = lambda _pid: 123456789
        adapter._uia_provider = Provider()
        adapter._uia_timeout_s = 0.01

        target = adapter.current()

        self.assertFalse(target.uia_observed)
        self.assertEqual("transient", target.uia_state)

    def test_windows_unicode_adapter_reports_exact_accepted_event_count(self):
        class User32:
            def __init__(self):
                self.events = []

            def SendInput(self, count, events, _size):
                self.events = [events[index] for index in range(count)]
                return count

        user32 = User32()
        native = WindowsNativeInputAdapter(user32=user32)

        result = native.send_unicode("A")

        self.assertEqual(2, result.requested)
        self.assertEqual(2, result.accepted)
        self.assertTrue(all(
            event.ki.dwFlags & KEYEVENTF_UNICODE for event in user32.events))

if __name__ == "__main__":
    unittest.main()
