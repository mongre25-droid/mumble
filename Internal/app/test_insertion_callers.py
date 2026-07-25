#!/usr/bin/env python3
"""Caller-level ownership tests for issue #15 insertion leases."""

import threading
import unittest
from collections import OrderedDict

import numpy as np

import mumble
from insertion import (
    InsertionCoordinator,
    InsertionOutcome,
    InsertionRequestConflict,
    InsertionResult,
    TargetContext,
    TargetLease,
)


TARGET_A = TargetContext(101, 201, 301, 401, "medium", "Edit", True)
TARGET_B = TargetContext(102, 202, 302, 402, "medium", "Edit", True)


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
             "target_lease": lease},
            "corrected",
        )

        self.assertFalse(replaced)
        self.assertEqual(1, len(calls))
        self.assertEqual(lease, calls[0]["target_lease"])
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
            "deck-op-current", "deck_history", ui_process_id=9999)
        not_displaced = controller._prepare_deck_insertion_lease(
            "deck-op-not-displaced", "deck_history",
            ui_process_id=TARGET_B.process_id)
        controller._deck_focus_request_pending = True
        controller._note_webui_focus(True)
        displaced = controller._prepare_deck_insertion_lease(
            "deck-op-displaced", "deck_history",
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
            "missing-operation", "deck_image")

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


if __name__ == "__main__":
    unittest.main()
