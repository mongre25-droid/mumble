#!/usr/bin/env python3
"""Caller-level ownership tests for issue #15 insertion leases."""

import threading
import unittest
from collections import OrderedDict

import numpy as np

import mumble
from insertion import InsertionOutcome, InsertionResult, TargetContext, TargetLease


TARGET_A = TargetContext(101, 201, 301, 401, "medium", "Edit", True)
TARGET_B = TargetContext(102, 202, 302, 402, "medium", "Edit", True)


class Settings:
    def get(self, key, default=None):
        return {"min_seconds": 0.3}.get(key, default)


class InsertionCallerTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
