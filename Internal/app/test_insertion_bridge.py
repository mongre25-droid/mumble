#!/usr/bin/env python3
"""Bridge-level exact-once and truthful-outcome tests for issue #15."""

import unittest

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
