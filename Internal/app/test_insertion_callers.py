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
        self.assertTrue(lease.mumble_displaced_target)

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
            TARGET_A, "deck", "before_mumble_focus", True)
        controller._prepared_insertion_leases = OrderedDict()
        controller._capture_insertion_target = lambda: TARGET_B

        current = controller._prepare_deck_insertion_lease(
            "deck-op-current", "deck_history", ui_process_id=9999)
        retained = controller._prepare_deck_insertion_lease(
            "deck-op-retained", "deck_history",
            ui_process_id=TARGET_B.process_id)

        self.assertEqual(TARGET_B, current.target)
        self.assertEqual(TARGET_A, retained.target)
        self.assertEqual("before_deck_dismiss", current.capture_phase)
        self.assertTrue(current.mumble_displaced_target)


if __name__ == "__main__":
    unittest.main()
