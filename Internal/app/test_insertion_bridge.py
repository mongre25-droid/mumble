#!/usr/bin/env python3
"""Bridge-level exact-once and truthful-outcome tests for issue #15."""

import unittest

import dictation_trace
import webui_shell


class Window:
    def __init__(self):
        self.minimized = 0
        self.restored = 0

    def minimize(self):
        self.minimized += 1

    def restore(self):
        self.restored += 1

    def show(self):
        pass


class InsertionBridgeTests(unittest.TestCase):
    def test_pending_first_lookup_is_polled_to_one_terminal_result_without_resubmit(self):
        api = webui_shell.Api.__new__(webui_shell.Api)
        api._window = Window()
        api._insertion_terminal_timeout_s = 1.0
        api._insertion_poll_interval_s = 0.01
        calls = []
        statuses = iter([
            {"ok": True, "state": "pending"},
            {
                "ok": True, "state": "terminal",
                "outcome": "sent_unconfirmed", "confirmed": False,
                "reason": "confirmation_unavailable",
                "message": "Sentâ€”check the field.",
                "cleanup_warning": "",
            },
        ])
        old_send = webui_shell._ctrl_send

        def send(command, timeout=2.0, _retry=True):
            calls.append(dict(command))
            if command["cmd"] == "prepare_insertion":
                return {"ok": True, "operation_id": command["operation_id"]}
            if command["cmd"] == "paste":
                return None
            return next(statuses)

        webui_shell._ctrl_send = send
        try:
            result = api.deck_paste("one native send")
        finally:
            webui_shell._ctrl_send = old_send

        self.assertEqual(1, sum(call["cmd"] == "paste" for call in calls))
        self.assertEqual(2, sum(call["cmd"] == "insertion_status"
                                for call in calls))
        self.assertEqual(1, len({call["operation_id"] for call in calls}))
        self.assertTrue(all(dictation_trace.validated_operation_id(
            call["operation_id"]) for call in calls))
        self.assertEqual("terminal", result["state"])
        self.assertEqual("sent_unconfirmed", result["outcome"])

    def test_pending_poll_has_a_bounded_explicit_unknown_result(self):
        api = webui_shell.Api.__new__(webui_shell.Api)
        api._window = Window()
        now = {"value": 0.0}
        api._insertion_terminal_timeout_s = 0.25
        api._insertion_poll_interval_s = 0.10
        api._insertion_clock = lambda: now["value"]
        api._insertion_sleep = lambda seconds: now.__setitem__(
            "value", now["value"] + seconds)
        calls = []
        old_send = webui_shell._ctrl_send

        def send(command, timeout=2.0, _retry=True):
            calls.append(dict(command))
            if command["cmd"] == "prepare_insertion":
                return {"ok": True, "operation_id": command["operation_id"]}
            if command["cmd"] == "paste":
                return None
            return {"ok": True, "state": "pending"}

        webui_shell._ctrl_send = send
        try:
            result = api.deck_paste_image(r"C:\fixture.png")
        finally:
            webui_shell._ctrl_send = old_send

        self.assertEqual(1, sum(call["cmd"] == "paste_image" for call in calls))
        self.assertEqual(1, len({call["operation_id"] for call in calls}))
        self.assertEqual("unknown", result["state"])
        self.assertEqual("unknown", result["outcome"])
        self.assertFalse(result["confirmed"])
        self.assertIn("do not retry", result["message"].lower())

    def test_deck_job_producer_uses_the_same_canonical_operation_identity(self):
        api = webui_shell.Api.__new__(webui_shell.Api)
        api._window = Window()
        calls = []
        old_send = webui_shell._ctrl_send

        def send(command, timeout=2.0, _retry=True):
            calls.append(dict(command))
            return {"ok": True, "operation_id": command["operation_id"]}

        webui_shell._ctrl_send = send
        try:
            result = api.run_deck_job(items=[])
        finally:
            webui_shell._ctrl_send = old_send

        self.assertTrue(result["ok"])
        self.assertEqual(["prepare_insertion", "deck_job"],
                         [call["cmd"] for call in calls])
        self.assertEqual(1, len({call["operation_id"] for call in calls}))
        self.assertTrue(all(dictation_trace.validated_operation_id(
            call["operation_id"]) for call in calls))

    def test_lost_status_transport_after_submit_is_explicitly_unknown(self):
        api = webui_shell.Api.__new__(webui_shell.Api)
        api._window = Window()
        calls = []
        old_send = webui_shell._ctrl_send

        def send(command, timeout=2.0, _retry=True):
            calls.append(dict(command))
            if command["cmd"] == "prepare_insertion":
                return {"ok": True, "operation_id": command["operation_id"]}
            return None

        webui_shell._ctrl_send = send
        try:
            result = api.deck_paste("transport uncertainty")
        finally:
            webui_shell._ctrl_send = old_send

        self.assertEqual(1, sum(call["cmd"] == "paste" for call in calls))
        self.assertEqual("unknown", result["state"])
        self.assertEqual("unknown", result["outcome"])
        self.assertFalse(result["confirmed"])
        self.assertIn("do not retry", result["message"].lower())

    def test_timeout_queries_the_same_operation_instead_of_resubmitting(self):
        api = webui_shell.Api.__new__(webui_shell.Api)
        api._window = Window()
        calls = []
        old_send = webui_shell._ctrl_send

        def send(command, timeout=2.0, _retry=True):
            calls.append(dict(command))
            if command["cmd"] == "prepare_insertion":
                return {"ok": True, "operation_id": command["operation_id"]}
            if command["cmd"] == "paste":
                return None
            return {
                "ok": True,
                "state": "terminal",
                "operation_id": command["operation_id"],
                "outcome": "sent_unconfirmed",
                "confirmed": False,
                "reason": "confirmation_unavailable",
                "message": "Sent—check the field.",
                "cleanup_warning": "",
            }

        webui_shell._ctrl_send = send
        try:
            result = api.deck_paste("one exact request")
        finally:
            webui_shell._ctrl_send = old_send

        self.assertEqual(["prepare_insertion", "paste", "insertion_status"],
                         [call["cmd"] for call in calls])
        self.assertEqual(1, len({call["operation_id"] for call in calls}))
        self.assertEqual("sent_unconfirmed", result["outcome"])
        self.assertFalse(result["confirmed"])
        self.assertNotIn("Pasted", result["message"])

    def test_bridge_propagates_every_authoritative_outcome_without_relabelling(self):
        outcomes = {
            "confirmed": (True, "Pasted."),
            "sent_unconfirmed": (False, "Sent—check the field."),
            "not_sent": (False, "Not sent—use Paste latest."),
            "uncertain": (False, "Paste not confirmed—check the field."),
            "saved_only": (False, "Saved in Deck and History."),
        }
        old_send = webui_shell._ctrl_send
        try:
            for outcome, (confirmed, message) in outcomes.items():
                with self.subTest(outcome=outcome):
                    webui_shell._ctrl_send = lambda command, **_kwargs: {
                        "ok": True,
                        "state": "terminal",
                        "operation_id": command["operation_id"],
                        "outcome": outcome,
                        "confirmed": confirmed,
                        "reason": "test_reason",
                        "message": message,
                        "cleanup_warning": "test cleanup" if outcome == "confirmed" else "",
                    }
                    api = webui_shell.Api.__new__(webui_shell.Api)
                    api._window = Window()
                    result = api.deck_paste("truth table")
                    self.assertEqual(outcome, result["outcome"])
                    self.assertEqual(confirmed, result["confirmed"])
                    self.assertEqual(message, result["message"])
                    if not confirmed:
                        self.assertNotIn("Pasted", result["message"])
        finally:
            webui_shell._ctrl_send = old_send


if __name__ == "__main__":
    unittest.main()
