#!/usr/bin/env python3
"""Caller-level ownership tests for issue #15 insertion leases."""

import threading
import unittest
import uuid
from collections import OrderedDict

import numpy as np

import mumble
from insertion import (
    InsertionCoordinator,
    InsertionCoordinatorCapacityError,
    InsertionOperationExpired,
    InsertionOutcome,
    InsertionModule,
    InsertionRequestConflict,
    InsertionResult,
    OperationReceipt,
    TargetContext,
    TargetLease,
)
from test_insertion_transaction import FakeClipboard, FakeNativeInput, FakeTarget


TARGET_A = TargetContext(101, 201, 301, 401, "medium", "Edit", True)
TARGET_B = TargetContext(102, 202, 302, 402, "medium", "Edit", True)


def operation_id(label):
    return uuid.uuid5(uuid.NAMESPACE_OID, label).hex


class Settings:
    def get(self, key, default=None):
        return {"min_seconds": 0.3}.get(key, default)


class InsertionCallerTests(unittest.TestCase):
    def _prepared_controller(self, target=TARGET_A):
        controller = mumble.Mumble.__new__(mumble.Mumble)
        active = {"target": target}
        controller._capture_insertion_target = lambda: active["target"]
        controller._prepared_insertion_leases = OrderedDict()
        controller._prepared_insertion_lock = threading.Lock()
        controller._deck_insertion_lease = TargetLease(
            TARGET_A, "deck", "before_mumble_focus", False)
        controller._deck_focus_request_pending = False
        controller._deck_displacement_confirmed = False
        return controller, active

    def _delayed_controller(self):
        controller = mumble.Mumble.__new__(mumble.Mumble)
        controller.busy = False
        controller.island = None
        controller.history = type("History", (), {
            "add": lambda _self, text, mode, duration, **_kwargs: {"words": 2},
        })()
        controller.stat_store = type("Stats", (), {
            "record": lambda _self, *_args, **_kwargs: True,
        })()
        controller._pending_stat_context = {"duration": 0.5}
        controller._send_webui_async = lambda *_args, **_kwargs: None
        controller._remember_correction_candidate = lambda *_args, **_kwargs: None
        controller._set_state = lambda *_args, **_kwargs: None
        controller._idle = lambda: None
        controller._notify = lambda *_args, **_kwargs: None
        controller._maybe_island_tip = lambda *_args, **_kwargs: None
        return controller

    def _cleanup_precedence_fixture(self, content_kind, outcome,
                                    cleanup_seam, primary_error=None,
                                    diagnostic_error=None):
        """Build one controller-level text/image cleanup-failure scenario."""
        controller, _active = self._prepared_controller()
        current_id = operation_id(
            "cleanup-{}-{}-{}-{}".format(
                content_kind, outcome.value, cleanup_seam,
                "exception" if primary_error is not None else "result"))
        source = ("deck_history" if content_kind == "text"
                  else "deck_image")
        lease = controller._prepare_deck_insertion_lease(
            current_id, source, ui_process_id=9999)
        calls = []
        diagnostics = []

        class CleanupFailure(RuntimeError):
            pass

        cleanup_failure = CleanupFailure(
            "PRIVATE_CALLER_CONTROLLED_CLEANUP_TEXT")
        result = InsertionResult(
            operation_id=current_id,
            source=source,
            outcome=outcome,
            reason=outcome.value,
            message="Authoritative insertion outcome.",
            send_count=1,
            native_requested=2,
            native_accepted=(2 if outcome is InsertionOutcome.CONFIRMED
                             else None),
            target_lease=lease,
        )

        class Coordinator:
            def prepare(self, _request):
                calls.append("prepare")

            def submit(self, _request):
                calls.append("submit")
                if primary_error is not None:
                    raise primary_error
                return result

            def abandon(self, _operation_id):
                calls.append("coordinator_abandonment")
                if cleanup_seam == "coordinator_abandonment":
                    raise cleanup_failure

        class Clipboard:
            def __init__(self):
                self._last_text = ""

            def pause(self):
                calls.append("clipboard_pause")

            def mark_own(self, _text):
                return None

            def resume(self, skip_current=False):
                calls.append("clipboard_resume")
                if cleanup_seam == "clipboard_resume":
                    raise cleanup_failure

        coordinator = Coordinator()
        controller._insertion_transaction = object()
        controller._insertion_coordinator = coordinator
        controller._ensure_insertion_transaction = (
            lambda: controller._insertion_transaction)
        controller._paste_lock = threading.Lock()
        controller._trace_mark = lambda *_args, **_kwargs: None
        controller.clipboard = Clipboard()
        original_retire = controller._retire_prepared_insertion

        def retire(operation):
            calls.append("prepared_binding_retirement")
            if cleanup_seam == "prepared_binding_retirement":
                raise cleanup_failure
            return original_retire(operation)

        def diagnostic(**fields):
            calls.append("diagnostic")
            if diagnostic_error is not None:
                raise diagnostic_error
            diagnostics.append(fields)

        controller._retire_prepared_insertion = retire
        controller._record_insertion_cleanup_diagnostic = diagnostic
        if content_kind == "text":
            action = lambda: controller._paste(
                "PRIVATE_TRANSCRIPT_TEXT", source=source,
                target_lease=lease, operation_id=current_id)
        else:
            action = lambda: controller._paste_image(
                "PRIVATE_IMAGE_PATH.png", source=source,
                target_lease=lease, operation_id=current_id)
        return action, result, calls, diagnostics, cleanup_failure

    def test_ordinary_text_and_deck_image_use_the_same_deep_module(self):
        controller = mumble.Mumble.__new__(mumble.Mumble)
        target = FakeTarget()
        clipboard = FakeClipboard()
        native = FakeNativeInput()
        module = InsertionModule(
            target, clipboard, native, settle_delay=lambda _seconds: None)
        controller._insertion_module = module
        controller._insertion_target = target
        controller._insertion_clipboard = clipboard
        controller._insertion_native = native
        controller._paste_lock = threading.Lock()
        controller._trace_mark = lambda *_args, **_kwargs: None
        controller.clipboard = None
        controller._prepared_insertion_leases = OrderedDict()
        controller._prepared_insertion_created_at = {}
        controller._prepared_insertion_executing = set()
        controller._prepared_insertion_tombstones = None

        text_lease = TargetLease(TARGET_A, "dictation", "stop", False)
        target.active = TARGET_A
        text_result = controller._paste(
            "ordinary words", source="finalize_text",
            target_lease=text_lease,
            operation_id=operation_id("module-text-caller"),
        )
        image_lease = TargetLease(
            TARGET_A, "deck_image", "before_deck_dismiss", False)
        image_result = controller._paste_image(
            "fixture.png", source="deck_image",
            target_lease=image_lease,
            operation_id=operation_id("module-image-caller"),
        )

        self.assertEqual(InsertionOutcome.CONFIRMED, text_result.outcome)
        self.assertEqual(InsertionOutcome.CONFIRMED, image_result.outcome)
        self.assertEqual(2, native.send_calls)

    def test_delayed_finalization_keeps_the_stop_time_lease_and_operation(self):
        controller = self._delayed_controller()
        lease = TargetLease(TARGET_B, "dictation", "stop", False)
        calls = []
        controller._paste = lambda text, **kwargs: (
            calls.append((text, kwargs)) or InsertionResult(
                operation_id=kwargs["operation_id"],
                source=kwargs["source"],
                outcome=InsertionOutcome.SAVED_ONLY,
                reason="target_changed",
                message="Not sent.",
                send_count=0,
                target_lease=kwargs["target_lease"],
            )
        )

        controller._finalize_text(
            "fallback words", {"duration": 0.5},
            target_lease=lease, operation_id="stop-operation")

        self.assertEqual(1, len(calls))
        self.assertEqual(lease, calls[0][1]["target_lease"])
        self.assertEqual("stop-operation", calls[0][1]["operation_id"])

    def test_every_delayed_mode_keeps_the_stop_time_lease_and_operation(self):
        for mode in ("text", "prompt", "email", "reply"):
            with self.subTest(mode=mode):
                controller = self._delayed_controller()
                lease = TargetLease(TARGET_B, "dictation", "stop", False)
                calls = []
                controller._generate = lambda *_args, **_kwargs: (
                    mode, "shaped words", True)
                controller._paste = lambda text, **kwargs: (
                    calls.append((text, kwargs)) or InsertionResult(
                        operation_id=kwargs["operation_id"],
                        source=kwargs["source"],
                        outcome=InsertionOutcome.SAVED_ONLY,
                        reason="target_changed",
                        message="Not sent.",
                        send_count=0,
                        target_lease=kwargs["target_lease"],
                    )
                )

                controller._reprocess(
                    "raw words", mode,
                    target_lease=lease, operation_id="stop-" + mode)

                self.assertEqual(1, len(calls))
                self.assertEqual(lease, calls[0][1]["target_lease"])
                self.assertEqual("stop-" + mode,
                                 calls[0][1]["operation_id"])

    def test_headless_mode_fallback_forwards_the_immutable_stop_context(self):
        controller = self._delayed_controller()
        controller.lock = threading.Lock()
        controller._last_raw = ""
        controller._suggested_mode = ""
        controller._clarify_until = 0.0
        controller._clarify_token = None
        lease = TargetLease(TARGET_B, "dictation", "stop", False)
        calls = []
        controller._finalize_text = lambda *args, **kwargs: calls.append(
            (args, kwargs))

        controller._offer_mode_pick(
            "raw", "prompt", "clean",
            target_lease=lease, operation_id="stop-picker")

        self.assertEqual(1, len(calls))
        self.assertEqual(lease, calls[0][1]["target_lease"])
        self.assertEqual("stop-picker", calls[0][1]["operation_id"])

    def test_dictation_owns_the_stop_time_target_not_the_start_target(self):
        controller = mumble.Mumble.__new__(mumble.Mumble)
        controller.settings = Settings()
        controller.lock = threading.Lock()
        controller.recording = True
        controller.stream = None
        controller.frames = [np.zeros((8000, 1), dtype=np.float32)]
        controller._stream_done = threading.Event()
        controller._stream_idle = threading.Event()
        controller._stream_idle.set()
        controller._stream_worker_thread = None
        controller._search_requested = False
        controller._mode_active = False
        controller._processing = False
        controller._dictation_insertion_lease = TargetLease(
            TARGET_A, "dictation", "start", False)
        controller._capture_insertion_target = lambda: TARGET_B
        beginnings = []
        controller._insertion_module = type("Module", (), {
            "begin": lambda _self, operation, source: (
                beginnings.append((operation, source)) or OperationReceipt(
                    operation,
                    source,
                    TargetLease(TARGET_B, source, "stop", False),
                )
            ),
        })()
        controller._trace_mark = lambda *_args, **_kwargs: None
        controller._trace_finish = lambda *_args, **_kwargs: None
        controller._set_state = lambda *_args, **_kwargs: None
        controller._resume_wake_word = lambda *_args, **_kwargs: None
        controller._tk_schedule = lambda fn, *args, **kwargs: fn(*args, **kwargs)
        controller._restore_meeting_island = lambda: None
        controller.island = None
        observed = {}

        def process(*_args, **_kwargs):
            observed["lease"] = controller._dictation_insertion_lease

        controller._process = process

        controller.stop_recording()

        lease = observed["lease"]
        self.assertEqual(TARGET_B, lease.target)
        self.assertEqual("stop", lease.capture_phase)
        self.assertFalse(lease.mumble_displaced_target)
        self.assertEqual(1, len(beginnings))
        self.assertEqual(controller._dictation_insertion_operation_id,
                         beginnings[0][0])
        self.assertEqual("dictation", beginnings[0][1])

    def test_deck_retains_the_external_destination_before_mumble_takes_focus(self):
        controller = mumble.Mumble.__new__(mumble.Mumble)
        active = {"target": TARGET_A}
        controller._capture_insertion_target = lambda: active["target"]

        def grab_selection():
            active["target"] = TARGET_B
            return ""

        controller._grab_selection_quiet = grab_selection
        controller._send_webui = lambda _message: True
        controller._open_web_ui = lambda: False

        controller._show_history_page()

        lease = controller._deck_insertion_lease
        self.assertEqual(TARGET_A, lease.target)
        self.assertEqual("before_mumble_focus", lease.capture_phase)
        self.assertFalse(lease.mumble_displaced_target)
        self.assertTrue(controller._deck_focus_request_pending)
        self.assertFalse(controller._deck_displacement_confirmed)

    def test_correction_carries_one_lease_and_unconfirmed_is_not_success(self):
        controller = mumble.Mumble.__new__(mumble.Mumble)
        controller._correction_can_replace = lambda *_args, **_kwargs: True
        lease = TargetLease(
            TARGET_A, "correction_replace", "confirmed_insertion", True)
        calls = []

        def paste(_text, **kwargs):
            calls.append(kwargs)
            return InsertionResult(
                operation_id=kwargs["operation_id"],
                source="correction_replace",
                outcome=InsertionOutcome.SENT_UNCONFIRMED,
                reason="confirmation_unavailable",
                message="Sent—check the field.",
                send_count=2,
                target_lease=kwargs["target_lease"],
            )

        controller._paste = paste

        replaced = controller._replace_correction_capture(
            {"id": "capture-1", "target_hwnd": TARGET_A.window,
             "target_lease": lease, "operation_id": "4" * 32},
            "corrected",
        )

        self.assertFalse(replaced)
        self.assertEqual(1, len(calls))
        self.assertEqual(lease, calls[0]["target_lease"])
        self.assertEqual("4" * 32, calls[0]["operation_id"])
        self.assertTrue(calls[0]["undo_before_paste"])

    def test_each_deck_action_freezes_the_current_external_target_before_dismiss(self):
        controller = mumble.Mumble.__new__(mumble.Mumble)
        controller._deck_insertion_lease = TargetLease(
            TARGET_A, "deck", "before_mumble_focus", False)
        controller._prepared_insertion_leases = OrderedDict()
        controller._capture_insertion_target = lambda: TARGET_B
        controller._deck_focus_request_pending = False
        controller._deck_displacement_confirmed = False

        current = controller._prepare_deck_insertion_lease(
            operation_id("deck-op-current"), "deck_history", ui_process_id=9999)
        not_displaced = controller._prepare_deck_insertion_lease(
            operation_id("deck-op-not-displaced"), "deck_history",
            ui_process_id=TARGET_B.process_id)
        controller._deck_focus_request_pending = True
        controller._note_webui_focus(True)
        displaced = controller._prepare_deck_insertion_lease(
            operation_id("deck-op-displaced"), "deck_history",
            ui_process_id=TARGET_B.process_id)

        self.assertEqual(TARGET_B, current.target)
        self.assertEqual(TARGET_A, not_displaced.target)
        self.assertEqual(TARGET_A, displaced.target)
        self.assertEqual("before_deck_dismiss", current.capture_phase)
        self.assertFalse(current.mumble_displaced_target)
        self.assertFalse(not_displaced.mumble_displaced_target)
        self.assertTrue(displaced.mumble_displaced_target)
        self.assertFalse(controller._deck_displacement_confirmed)

    def test_deck_displacement_proof_is_invalidated_by_focus_loss(self):
        controller = mumble.Mumble.__new__(mumble.Mumble)
        controller._deck_focus_request_pending = True
        controller._deck_displacement_confirmed = False

        controller._note_webui_focus(True)
        self.assertTrue(controller._deck_displacement_confirmed)

        controller._note_webui_focus(False)
        controller._note_webui_focus(True)  # Manual return, no Mumble request.

        self.assertFalse(controller._deck_displacement_confirmed)

    def test_missing_deck_lease_never_authorizes_focus_restoration(self):
        controller = mumble.Mumble.__new__(mumble.Mumble)
        controller._prepared_insertion_leases = OrderedDict()

        lease = controller._prepared_insertion_lease(
            operation_id("missing-operation"), "deck_image")

        self.assertIsNone(lease.target)
        self.assertFalse(lease.mumble_displaced_target)

    def test_same_id_mismatch_cannot_rebind_the_final_deck_insertion(self):
        controller, active = self._prepared_controller()
        operation_id = "a" * 32
        first = controller._prepare_deck_insertion_lease(
            operation_id, "deck_history", ui_process_id=9999)

        active["target"] = TARGET_B
        with self.assertRaises(InsertionRequestConflict):
            controller._prepare_deck_insertion_lease(
                operation_id, "deck_history", ui_process_id=9999)

        retained = controller._prepared_insertion_lease(
            operation_id, "deck_history")
        observed = []

        class Transaction:
            def insert(self, request):
                observed.append(request.target_lease)
                return InsertionResult(
                    operation_id=request.operation_id,
                    source=request.source,
                    outcome=InsertionOutcome.SAVED_ONLY,
                    reason="fixture",
                    message="Saved.",
                    send_count=0,
                    target_lease=request.target_lease,
                )

        transaction = Transaction()
        controller._insertion_transaction = transaction
        controller._insertion_coordinator = InsertionCoordinator(transaction)
        controller._ensure_insertion_transaction = lambda: transaction
        controller._paste_lock = threading.Lock()
        controller._trace_mark = lambda *_args, **_kwargs: None
        controller.clipboard = None

        controller._paste(
            "content-free fixture", source="deck_history",
            target_lease=retained, operation_id=operation_id)

        self.assertIs(first, retained)
        self.assertEqual([TARGET_A], [lease.target for lease in observed])

    def test_same_id_identical_prepare_is_idempotent(self):
        controller, _active = self._prepared_controller()
        operation_id = "b" * 32

        first = controller._prepare_deck_insertion_lease(
            operation_id, "deck_history", ui_process_id=9999)
        replay = controller._prepare_deck_insertion_lease(
            operation_id, "deck_history", ui_process_id=9999)

        self.assertIs(first, replay)
        self.assertIs(
            first,
            controller._prepared_insertion_lease(
                operation_id, "deck_history"))

    def test_only_the_first_valid_target_creates_the_immutable_binding(self):
        controller, active = self._prepared_controller(target=None)
        controller._deck_insertion_lease = None
        operation_id = "c" * 32

        missing = controller._prepare_deck_insertion_lease(
            operation_id, "deck_history", ui_process_id=9999)
        self.assertIsNone(missing.target)
        self.assertNotIn(operation_id, controller._prepared_insertion_leases)

        active["target"] = TARGET_A
        accepted = controller._prepare_deck_insertion_lease(
            operation_id, "deck_history", ui_process_id=9999)

        self.assertEqual(TARGET_A, accepted.target)
        self.assertIs(
            accepted,
            controller._prepared_insertion_lease(
                operation_id, "deck_history"))

    def test_same_id_rejects_different_source_window_or_security_lease(self):
        cases = ("source", "window", "displacement")
        for index, changed in enumerate(cases, start=1):
            with self.subTest(changed=changed):
                controller, active = self._prepared_controller()
                operation_id = ("{:x}".format(index) * 32)[:32]
                original = controller._prepare_deck_insertion_lease(
                    operation_id, "deck_history", ui_process_id=9999)

                source = "deck_history"
                ui_process_id = 9999
                if changed == "source":
                    source = "deck_image"
                elif changed == "window":
                    active["target"] = TARGET_B
                else:
                    active["target"] = TargetContext(
                        900, 9999, 901, 902, "medium", "Edit", True)
                    controller._deck_displacement_confirmed = True

                with self.assertRaises(InsertionRequestConflict):
                    controller._prepare_deck_insertion_lease(
                        operation_id, source, ui_process_id=ui_process_id)

                if changed == "source":
                    with self.assertRaises(InsertionRequestConflict):
                        controller._prepared_insertion_lease(
                            operation_id, "deck_image")

                self.assertIs(
                    original,
                    controller._prepared_insertion_lease(
                        operation_id, "deck_history"))

    def test_concurrent_same_id_prepare_has_one_immutable_winner(self):
        controller, _active = self._prepared_controller()
        operation_id = "d" * 32
        barrier = threading.Barrier(3)
        results = []
        results_lock = threading.Lock()

        def capture_for_thread():
            return threading.current_thread().fixture_target

        controller._capture_insertion_target = capture_for_thread

        def prepare(target):
            threading.current_thread().fixture_target = target
            barrier.wait()
            try:
                lease = controller._prepare_deck_insertion_lease(
                    operation_id, "deck_history", ui_process_id=9999)
                result = ("accepted", lease)
            except InsertionRequestConflict:
                result = ("rejected", target)
            with results_lock:
                results.append(result)

        workers = [
            threading.Thread(target=prepare, args=(TARGET_A,)),
            threading.Thread(target=prepare, args=(TARGET_B,)),
        ]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(2)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(["accepted", "rejected"],
                         sorted(result[0] for result in results))
        accepted = next(result[1] for result in results
                        if result[0] == "accepted")
        self.assertIs(
            accepted,
            controller._prepared_insertion_lease(
                operation_id, "deck_history"))

    def test_active_capacity_fails_closed_and_preserves_the_original_target(self):
        controller, active = self._prepared_controller()
        original_id = "e" * 32
        original = controller._prepare_deck_insertion_lease(
            original_id, "deck_history", ui_process_id=9999)
        for index in range(1, 256):
            controller._prepare_deck_insertion_lease(
                "{:032x}".format(index), "deck_history", ui_process_id=9999)
        before = list(controller._prepared_insertion_leases.items())

        with self.assertRaises(InsertionCoordinatorCapacityError):
            controller._prepare_deck_insertion_lease(
                "f" * 32, "deck_history", ui_process_id=9999)

        self.assertEqual(before, list(controller._prepared_insertion_leases.items()))
        active["target"] = TARGET_B
        with self.assertRaises(InsertionRequestConflict):
            controller._prepare_deck_insertion_lease(
                original_id, "deck_history", ui_process_id=9999)
        self.assertIs(
            original,
            controller._prepared_insertion_lease(
                original_id, "deck_history"))
        self.assertEqual(TARGET_A, original.target)
        observed = []

        class Transaction:
            def insert(self, request):
                observed.append(request.target_lease.target)
                return InsertionResult(
                    request.operation_id, request.source,
                    InsertionOutcome.SAVED_ONLY, "fixture", "Saved.", 0,
                    target_lease=request.target_lease)

        transaction = Transaction()
        controller._insertion_transaction = transaction
        controller._insertion_coordinator = InsertionCoordinator(transaction)
        controller._ensure_insertion_transaction = lambda: transaction
        controller._paste_lock = threading.Lock()
        controller._trace_mark = lambda *_args, **_kwargs: None
        controller.clipboard = None
        controller._paste(
            "content-free fixture", source="deck_history",
            target_lease=original, operation_id=original_id)

        self.assertEqual([TARGET_A], observed)

    def test_terminal_completion_releases_active_binding_and_blocks_reuse(self):
        controller, _active = self._prepared_controller()
        operation = "f" * 32
        lease = controller._prepare_deck_insertion_lease(
            operation, "deck_history", ui_process_id=9999)
        sends = []

        class Transaction:
            def insert(self, request):
                sends.append(request.target_lease.target)
                return InsertionResult(
                    operation_id=request.operation_id,
                    source=request.source,
                    outcome=InsertionOutcome.SAVED_ONLY,
                    reason="fixture",
                    message="Saved.",
                    send_count=0,
                    target_lease=request.target_lease,
                )

        transaction = Transaction()
        controller._insertion_transaction = transaction
        controller._insertion_coordinator = InsertionCoordinator(transaction)
        controller._ensure_insertion_transaction = lambda: transaction
        controller._paste_lock = threading.Lock()
        controller._trace_mark = lambda *_args, **_kwargs: None
        controller.clipboard = None

        first = controller._paste(
            "content-free fixture", source="deck_history",
            target_lease=lease, operation_id=operation)
        second = controller._paste(
            "content-free fixture", source="deck_history",
            target_lease=lease, operation_id=operation)

        self.assertIs(first, second)
        self.assertEqual([TARGET_A], sends)
        self.assertNotIn(operation, controller._prepared_insertion_leases)
        with self.assertRaises(InsertionOperationExpired):
            controller._prepare_deck_insertion_lease(
                operation, "deck_history", ui_process_id=9999)

    def test_repeated_prepare_complete_cycles_keep_state_bounded_and_replay_closed(self):
        controller, _active = self._prepared_controller()
        sends = []

        class Transaction:
            def insert(self, request):
                sends.append(request.operation_id)
                return InsertionResult(
                    request.operation_id, request.source,
                    InsertionOutcome.SAVED_ONLY, "fixture", "Saved.", 0,
                    target_lease=request.target_lease)

        transaction = Transaction()
        controller._insertion_transaction = transaction
        controller._insertion_coordinator = InsertionCoordinator(
            transaction, retention=8)
        controller._ensure_insertion_transaction = lambda: transaction
        controller._paste_lock = threading.Lock()
        controller._trace_mark = lambda *_args, **_kwargs: None
        controller.clipboard = None
        first_operation = "1" * 32

        for index in range(300):
            current_id = (first_operation if index == 0
                          else "{:032x}".format(index + 1000))
            lease = controller._prepare_deck_insertion_lease(
                current_id, "deck_history", ui_process_id=9999)
            controller._paste(
                "content-free fixture", source="deck_history",
                target_lease=lease, operation_id=current_id)

        self.assertEqual(300, len(sends))
        self.assertEqual(0, len(controller._prepared_insertion_leases))
        self.assertLessEqual(len(controller._insertion_coordinator._entries), 8)
        self.assertLessEqual(len(controller._insertion_coordinator._retired), 8)
        self.assertEqual(
            1 << 17,
            len(controller._get_prepared_insertion_tombstones()._bits))
        with self.assertRaises(InsertionOperationExpired):
            controller._prepare_deck_insertion_lease(
                first_operation, "deck_history", ui_process_id=9999)

    def test_invalid_identifier_is_rejected_before_lower_registry_state(self):
        controller, _active = self._prepared_controller()
        sentinel = "PRIVATE_TRANSCRIPT_CONTENT"

        for action in (
                lambda: controller._prepare_deck_insertion_lease(
                    sentinel, "deck_history", ui_process_id=9999),
                lambda: controller._prepared_insertion_lease(
                    sentinel, "deck_history"),
                lambda: controller._paste(
                    "content-free fixture", source="dictation",
                    target_lease=TargetLease(
                        TARGET_A, "dictation", "stop", False),
                    operation_id=sentinel),
                lambda: controller._paste_image(
                    "content-free-fixture.png", source="deck_image",
                    target_lease=TargetLease(
                        TARGET_A, "deck_image", "before_deck_dismiss", False),
                    operation_id=sentinel)):
            with self.assertRaises(ValueError) as caught:
                action()
            self.assertNotIn(sentinel, str(caught.exception))

        self.assertNotIn(sentinel, controller._prepared_insertion_leases)

    def test_stalled_target_inspection_does_not_block_existing_lookup(self):
        controller, _active = self._prepared_controller()
        existing_id = "2" * 32
        existing = controller._prepare_deck_insertion_lease(
            existing_id, "deck_history", ui_process_id=9999)
        inspection_started = threading.Event()
        release_inspection = threading.Event()
        lookup_finished = threading.Event()
        lookup_result = []

        def stalled_capture():
            inspection_started.set()
            release_inspection.wait(2)
            return TARGET_B

        controller._capture_insertion_target = stalled_capture
        preparing = threading.Thread(
            target=lambda: controller._prepare_deck_insertion_lease(
                "3" * 32, "deck_history", ui_process_id=9999))

        def lookup():
            lookup_result.append(controller._prepared_insertion_lease(
                existing_id, "deck_history"))
            lookup_finished.set()

        looking_up = threading.Thread(target=lookup)
        preparing.start()
        self.assertTrue(inspection_started.wait(1))
        looking_up.start()
        lookup_completed_while_inspection_stalled = lookup_finished.wait(0.5)
        release_inspection.set()
        preparing.join(2)
        looking_up.join(2)

        self.assertTrue(lookup_completed_while_inspection_stalled)
        self.assertTrue(all(not worker.is_alive()
                            for worker in (preparing, looking_up)))
        self.assertEqual([existing], lookup_result)

    def test_every_fallible_text_and_image_setup_seam_retires_the_binding(self):
        class OriginalFailure(RuntimeError):
            pass

        class Clipboard:
            def __init__(self, pause_failure=None):
                self.pause_failure = pause_failure
                self.pause_calls = 0
                self.resume_calls = 0

            def pause(self):
                self.pause_calls += 1
                if self.pause_failure is not None:
                    raise self.pause_failure

            def mark_own(self, _text):
                return None

            def resume(self, skip_current=False):
                self.resume_calls += 1

        original_request = mumble.InsertionRequest
        cases = []
        for content_kind in ("text", "image"):
            for seam in ("transaction", "request", "clipboard_pause"):
                cases.append((content_kind, seam))

        try:
            for content_kind, seam in cases:
                with self.subTest(content_kind=content_kind, seam=seam):
                    controller, _active = self._prepared_controller()
                    current_id = operation_id(
                        "early-{}-{}".format(content_kind, seam))
                    source = ("deck_history" if content_kind == "text"
                              else "deck_image")
                    lease = controller._prepare_deck_insertion_lease(
                        current_id, source, ui_process_id=9999)
                    failure = OriginalFailure(
                        "{}-{}".format(content_kind, seam))
                    clipboard = Clipboard(
                        failure if seam == "clipboard_pause" else None)
                    controller.clipboard = clipboard
                    controller._paste_lock = threading.Lock()
                    controller._trace_mark = lambda *_args, **_kwargs: None
                    controller._insertion_transaction = object()
                    controller._insertion_coordinator = object()
                    if seam == "transaction":
                        controller._ensure_insertion_transaction = (
                            lambda failure=failure: (_ for _ in ()).throw(failure))
                    else:
                        controller._ensure_insertion_transaction = (
                            lambda: controller._insertion_transaction)
                    if seam == "request":
                        mumble.InsertionRequest = (
                            lambda **_kwargs: (_ for _ in ()).throw(failure))
                    else:
                        mumble.InsertionRequest = original_request

                    if content_kind == "text":
                        action = lambda: controller._paste(
                            "content-free fixture", source=source,
                            target_lease=lease, operation_id=current_id)
                    else:
                        action = lambda: controller._paste_image(
                            "content-free-fixture.png", source=source,
                            target_lease=lease, operation_id=current_id)
                    with self.assertRaises(OriginalFailure) as caught:
                        action()

                    self.assertIs(failure, caught.exception)
                    self.assertNotIn(
                        current_id, controller._prepared_insertion_leases)
                    with self.assertRaises(InsertionOperationExpired):
                        controller._prepared_insertion_lease(current_id, source)
                    with self.assertRaises(InsertionOperationExpired):
                        controller._prepare_deck_insertion_lease(
                            current_id, source, ui_process_id=9999)
        finally:
            mumble.InsertionRequest = original_request

    def test_cleanup_failures_preserve_every_sent_outcome_and_finish_other_cleanup(self):
        outcomes = (
            InsertionOutcome.CONFIRMED,
            InsertionOutcome.SENT_UNCONFIRMED,
            InsertionOutcome.UNCERTAIN,
        )
        cleanup_seams = (
            "coordinator_abandonment",
            "prepared_binding_retirement",
            "clipboard_resume",
        )
        old_paste = mumble.pyperclip.paste
        mumble.pyperclip.paste = lambda: ""
        try:
            for content_kind in ("text", "image"):
                for cleanup_seam in cleanup_seams:
                    for outcome in outcomes:
                        with self.subTest(
                                content_kind=content_kind,
                                cleanup_seam=cleanup_seam,
                                outcome=outcome.value):
                            action, expected, calls, diagnostics, _failure = (
                                self._cleanup_precedence_fixture(
                                    content_kind, outcome, cleanup_seam))

                            returned = action()
                            response = returned.as_dict()

                            self.assertIs(expected, returned)
                            self.assertEqual(outcome.value, response["outcome"])
                            self.assertEqual(
                                outcome is InsertionOutcome.CONFIRMED,
                                response["confirmed"])
                            self.assertEqual(1, calls.count("submit"))
                            for stage in cleanup_seams:
                                self.assertEqual(1, calls.count(stage))
                            self.assertEqual(1, calls.count("diagnostic"))
                            self.assertEqual([{
                                "stage": cleanup_seam,
                                "category": "lifecycle_cleanup",
                                "outcome": "failed",
                            }], diagnostics)
                            self.assertNotIn(
                                "PRIVATE_", repr(diagnostics))
        finally:
            mumble.pyperclip.paste = old_paste

    def test_cleanup_failures_preserve_primary_exception_identity_and_traceback(self):
        class PrimaryFailure(RuntimeError):
            pass

        cleanup_seams = (
            "coordinator_abandonment",
            "prepared_binding_retirement",
            "clipboard_resume",
        )
        old_paste = mumble.pyperclip.paste
        mumble.pyperclip.paste = lambda: ""
        try:
            for content_kind in ("text", "image"):
                for cleanup_seam in cleanup_seams:
                    with self.subTest(
                            content_kind=content_kind,
                            cleanup_seam=cleanup_seam):
                        primary = PrimaryFailure("PRIVATE_PRIMARY_TEXT")
                        action, _result, calls, diagnostics, _failure = (
                            self._cleanup_precedence_fixture(
                                content_kind, InsertionOutcome.CONFIRMED,
                                cleanup_seam, primary_error=primary))
                        caught = None
                        traceback_names = []

                        try:
                            action()
                        except PrimaryFailure as exc:
                            caught = exc
                            cursor = exc.__traceback__
                            while cursor is not None:
                                traceback_names.append(
                                    cursor.tb_frame.f_code.co_name)
                                cursor = cursor.tb_next
                        else:
                            self.fail("primary insertion exception was lost")

                        self.assertIs(primary, caught)
                        self.assertIn("submit", traceback_names)
                        self.assertEqual(1, calls.count("submit"))
                        for stage in cleanup_seams:
                            self.assertEqual(1, calls.count(stage))
                        self.assertEqual([{
                            "stage": cleanup_seam,
                            "category": "lifecycle_cleanup",
                            "outcome": "failed",
                        }], diagnostics)
                        self.assertNotIn("PRIVATE_", repr(diagnostics))
        finally:
            mumble.pyperclip.paste = old_paste

    def test_cleanup_diagnostic_failure_cannot_replace_result_or_exception(self):
        class DiagnosticFailure(RuntimeError):
            pass

        class PrimaryFailure(RuntimeError):
            pass

        old_paste = mumble.pyperclip.paste
        mumble.pyperclip.paste = lambda: ""
        try:
            for content_kind in ("text", "image"):
                for primary_kind in ("result", "exception"):
                    with self.subTest(content_kind=content_kind,
                                      primary_kind=primary_kind):
                        primary = (PrimaryFailure("PRIVATE_PRIMARY_TEXT")
                                   if primary_kind == "exception" else None)
                        diagnostic = DiagnosticFailure(
                            "PRIVATE_DIAGNOSTIC_TEXT")
                        action, expected, calls, _diagnostics, _failure = (
                            self._cleanup_precedence_fixture(
                                content_kind, InsertionOutcome.CONFIRMED,
                                "coordinator_abandonment",
                                primary_error=primary,
                                diagnostic_error=diagnostic))

                        if primary is None:
                            self.assertIs(expected, action())
                        else:
                            with self.assertRaises(PrimaryFailure) as caught:
                                action()
                            self.assertIs(primary, caught.exception)
                        self.assertEqual(1, calls.count("submit"))
                        self.assertEqual(1, calls.count("diagnostic"))
        finally:
            mumble.pyperclip.paste = old_paste

    def test_256_early_setup_failures_leave_capacity_recoverable(self):
        controller, _active = self._prepared_controller()
        controller.clipboard = None
        controller._ensure_insertion_transaction = lambda: (_ for _ in ()).throw(
            RuntimeError("fixture setup failure"))

        for index in range(256):
            current_id = "{:032x}".format(index + 10000)
            lease = controller._prepare_deck_insertion_lease(
                current_id, "deck_history", ui_process_id=9999)
            with self.assertRaises(RuntimeError):
                controller._paste(
                    "content-free fixture", source="deck_history",
                    target_lease=lease, operation_id=current_id)

        next_id = "f" * 32
        recovered = controller._prepare_deck_insertion_lease(
            next_id, "deck_history", ui_process_id=9999)
        self.assertEqual(TARGET_A, recovered.target)
        self.assertEqual([next_id], list(controller._prepared_insertion_leases))

    def test_prepared_lease_expiry_uses_monotonic_time_and_never_expires_execution(self):
        controller, active = self._prepared_controller()
        now = {"value": 10.0}
        controller._prepared_insertion_clock = lambda: now["value"]
        controller._prepared_insertion_ttl_s = 5.0
        expired_id = operation_id("transport-lost-prepared")
        controller._prepare_deck_insertion_lease(
            expired_id, "deck_history", ui_process_id=9999)

        now["value"] = 16.0
        expired = controller._cleanup_prepared_insertions()

        self.assertEqual([expired_id], expired)
        self.assertNotIn(expired_id, controller._prepared_insertion_leases)
        with self.assertRaises(InsertionOperationExpired):
            controller._prepared_insertion_lease(expired_id, "deck_history")
        active["target"] = TARGET_B
        with self.assertRaises(InsertionOperationExpired):
            controller._prepare_deck_insertion_lease(
                expired_id, "deck_history", ui_process_id=9999)

        active["target"] = TARGET_A
        executing_id = operation_id("slow-executing-prepared")
        original = controller._prepare_deck_insertion_lease(
            executing_id, "deck_history", ui_process_id=9999)
        self.assertIs(original, controller._prepared_insertion_lease(
            executing_id, "deck_history"))
        now["value"] = 100.0

        self.assertEqual([], controller._cleanup_prepared_insertions())
        self.assertIs(original, controller._prepared_insertion_leases[executing_id])
        self.assertEqual(
            {"abandoned": False, "state": "pending"},
            controller._abandon_prepared_insertion(executing_id))
        self.assertIs(original, controller._prepared_insertion_leases[executing_id])
        active["target"] = TARGET_B
        with self.assertRaises(InsertionRequestConflict):
            controller._prepare_deck_insertion_lease(
                executing_id, "deck_history", ui_process_id=9999)
        controller._retire_prepared_insertion(executing_id)

    def test_expired_capacity_is_cleaned_without_evicting_a_live_target(self):
        controller, _active = self._prepared_controller()
        now = {"value": 0.0}
        controller._prepared_insertion_clock = lambda: now["value"]
        controller._prepared_insertion_ttl_s = 5.0
        live_id = operation_id("live-target-at-capacity")
        live = controller._prepare_deck_insertion_lease(
            live_id, "deck_history", ui_process_id=9999)
        controller._prepared_insertion_lease(live_id, "deck_history")
        for index in range(255):
            controller._prepare_deck_insertion_lease(
                "{:032x}".format(index + 20000),
                "deck_history", ui_process_id=9999)

        now["value"] = 6.0
        replacement_id = operation_id("capacity-after-expiry")
        replacement = controller._prepare_deck_insertion_lease(
            replacement_id, "deck_history", ui_process_id=9999)

        self.assertIs(live, controller._prepared_insertion_leases[live_id])
        self.assertIs(replacement,
                      controller._prepared_insertion_leases[replacement_id])
        self.assertEqual(2, len(controller._prepared_insertion_leases))

    def test_controller_abandonment_is_idempotent_and_late_submit_fails_closed(self):
        controller, _active = self._prepared_controller()
        current_id = operation_id("controller-abandonment")
        controller._prepare_deck_insertion_lease(
            current_id, "deck_history", ui_process_id=9999)

        first = controller._abandon_prepared_insertion(current_id)
        second = controller._abandon_prepared_insertion(current_id)

        self.assertTrue(first["abandoned"])
        self.assertEqual(first, second)
        self.assertNotIn(current_id, controller._prepared_insertion_leases)
        with self.assertRaises(InsertionOperationExpired):
            controller._prepared_insertion_lease(current_id, "deck_history")
        with self.assertRaises(InsertionOperationExpired):
            controller._prepare_deck_insertion_lease(
                current_id, "deck_history", ui_process_id=9999)


if __name__ == "__main__":
    unittest.main()
