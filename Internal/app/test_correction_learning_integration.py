"""Integration contracts for the experimental correction-learning feature.

These tests stay offline and never create a real Tk window or inspect another
application. The platform UIA adapter has its own fake-reader tests inside the
isolated experimental package.
"""

from __future__ import annotations

import threading
import time
import unittest
from unittest import mock
from pathlib import Path

from app_window import AppWindow
import island_render
from mumble import Mumble
from overlay import Island
from settings import DEFAULTS
import webui_shell


class _Settings:
    def __init__(self, **values):
        self.values = {
            "correction_learning_enabled": False,
            "correction_learning_auto_detect": False,
            **values,
        }

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def update(self, **values):
        self.values.update(values)

    def atomic_vocabulary_update(self, mutator):
        vocabulary = dict(self.values.get("vocabulary", {}) or {})
        terms = list(self.values.get("vocabulary_terms", []) or [])
        result = mutator(vocabulary, terms)
        self.values["vocabulary"] = vocabulary
        self.values["vocabulary_terms"] = terms
        return result


class _Island:
    def __init__(self):
        self.offered = 0
        self.cleared = 0

    def offer_correction(self, *args, **_kwargs):
        self.offered += 1
        self.last_offer = args

    def wake(self):
        pass

    def clear_correction(self):
        self.cleared += 1


def _controller(settings):
    ctrl = Mumble.__new__(Mumble)
    ctrl.settings = settings
    ctrl.island = _Island()
    ctrl._last_correction_capture = None
    ctrl._correction_capture_lock = threading.RLock()
    ctrl._correction_monitor = None
    ctrl._foreground_hwnd = lambda: 123
    ctrl._tk_schedule = lambda fn, *args, **kwargs: fn(*args, **kwargs)
    return ctrl


class CorrectionLearningIntegrationTests(unittest.TestCase):
    def test_experiment_is_opt_in_but_auto_detection_is_ready_when_enabled(self):
        self.assertIs(DEFAULTS["correction_learning_enabled"], False)
        self.assertIs(DEFAULTS["correction_learning_auto_detect"], True)

    def test_classic_auto_detect_choice_is_disclosed_and_applies_immediately(self):
        source = Path(__file__).with_name("app_window.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("Detect corrections in the target app", source)
        self.assertIn("up to 30 seconds and in memory", source)
        self.assertIn("Password fields, focus changes", source)
        self.assertIn("approval on the island", source)
        self.assertIn("correction_auto_detect_switch = ui.Switch", source)

        settings = _Settings(
            correction_learning_enabled=True,
            correction_learning_auto_detect=True,
        )
        ctrl = _controller(settings)
        calls = []
        ctrl._prewarm_correction_monitor = lambda: calls.append("prewarm")
        ctrl._cancel_correction_monitor = lambda: calls.append("cancel")
        ctrl._send_webui_async = lambda payload: calls.append(payload)

        self.assertFalse(ctrl.set_correction_learning_auto_detect(False))
        self.assertFalse(settings.values["correction_learning_auto_detect"])
        self.assertIn("cancel", calls)

        calls.clear()
        self.assertTrue(ctrl.set_correction_learning_auto_detect(True))
        self.assertTrue(settings.values["correction_learning_auto_detect"])
        self.assertIn("prewarm", calls)
        self.assertIn({"cmd": "refresh", "what": "settings"}, calls)

    def test_classic_vocabulary_refresh_three_way_merges_unsaved_edits(self):
        merged = AppWindow._merge_vocabulary_text(
            ["Mumble", "Remove me"],
            {"mambo": "Mumble", "old": "Old"},
            ("Mumble\nCerebras\nmambo = My Mumble\nold = Old\n"
             "unfinished ="),
            ["Mumble", "OpenAI"],
            {"mambo": "Mumble", "wisper": "Whisper"},
        )
        terms, pairs, incomplete = AppWindow._parse_vocabulary_text(merged)

        self.assertIn("Cerebras", terms)
        self.assertEqual(pairs["mambo"], "My Mumble")
        self.assertIn("OpenAI", terms)
        self.assertEqual(pairs["wisper"], "Whisper")
        self.assertNotIn("Remove me", terms)
        self.assertNotIn("old", pairs)
        self.assertEqual(incomplete, ["unfinished ="])

    def test_classic_save_preserves_corrections_learned_after_its_baseline(self):
        settings = _Settings(
            vocabulary={"mambo": "Mumble", "new-hearing": "New Name"},
            vocabulary_terms=["Mumble", "New Name"],
        )
        ctrl = _controller(settings)

        ctrl.set_vocabulary(
            {"mambo": "My Mumble", "manual": "Manual"},
            ["Mumble", "Cerebras"],
            {"mambo": "Mumble"},
            ["Mumble"],
        )

        self.assertEqual(settings.values["vocabulary"]["mambo"], "My Mumble")
        self.assertEqual(settings.values["vocabulary"]["manual"], "Manual")
        self.assertEqual(
            settings.values["vocabulary"]["new-hearing"], "New Name"
        )
        self.assertEqual(
            settings.values["vocabulary_terms"],
            ["Mumble", "New Name", "Cerebras"],
        )

    def test_web_save_preserves_a_correction_learned_after_editor_load(self):
        settings = _Settings(
            vocabulary={"mambo": "Mumble", "new-hearing": "New Name"},
            vocabulary_terms=["Mumble", "New Name"],
        )
        api = webui_shell.Api.__new__(webui_shell.Api)
        api.settings = settings

        with mock.patch.object(webui_shell, "_ctrl_send", return_value={"ok": True}):
            result = api.save_vocabulary(
                ["Mumble", "Cerebras"],
                {"mambo": "My Mumble", "manual": "Manual"},
                ["Mumble"],
                {"mambo": "Mumble"},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["vocabulary"]["mambo"], "My Mumble")
        self.assertEqual(result["vocabulary"]["manual"], "Manual")
        self.assertEqual(result["vocabulary"]["new-hearing"], "New Name")
        self.assertEqual(
            result["vocabulary_terms"], ["Mumble", "Cerebras", "New Name"]
        )

    def test_only_successful_plain_dictation_becomes_a_candidate(self):
        ctrl = _controller(_Settings(correction_learning_enabled=True))
        ctrl._remember_correction_candidate("Use mambo here", "text", True)
        self.assertEqual(ctrl._last_correction_capture["text"], "Use mambo here")
        self.assertEqual(ctrl._last_correction_capture["target_hwnd"], 123)
        self.assertFalse(ctrl._last_correction_capture["replace_verified"])
        # Remembering a paste is passive. The island offers a review only after
        # the target observer finds a concrete user correction.
        self.assertEqual(ctrl.island.offered, 0)
        self.assertEqual(ctrl.island.cleared, 1)

    def test_smart_mode_failed_paste_and_disabled_feature_are_excluded(self):
        ctrl = _controller(_Settings(correction_learning_enabled=True))
        for mode, landed in (("prompt", True), ("email", True), ("text", False)):
            ctrl._remember_correction_candidate("Do not learn this", mode, landed)
            self.assertIsNone(ctrl._last_correction_capture)
        self.assertEqual(ctrl.island.offered, 0)
        self.assertEqual(ctrl.island.cleared, 3)

        ctrl.settings.values["correction_learning_enabled"] = False
        ctrl._remember_correction_candidate("Still excluded", "text", True)
        self.assertIsNone(ctrl._last_correction_capture)
        self.assertEqual(ctrl.island.cleared, 4)

    def test_island_correction_review_is_drawn_and_hit_testable(self):
        snap = {
            "modes": [("prompt", "Prompt"), ("email", "Email")],
            "active": None,
            "expanded": False,
            "show_foreign": False,
            "correction_available": True,
            "correction_label": "Learn",
            "frame": 1,
        }
        layout = island_render.bar_layout(snap)
        self.assertIsNotNone(layout["correction"])
        self.assertIsNotNone(layout["correction_dismiss"])
        self.assertIsNone(layout["deck"])
        self.assertIsNone(layout["foreign"])
        self.assertLessEqual(layout["correction"][1], layout["pill"][1])
        self.assertEqual(
            island_render.render_bar(snap).size,
            (island_render.BAR_WIN_W, island_render.BAR_WIN_H),
        )

    def test_detected_target_edit_is_review_gated_on_the_island(self):
        ctrl = _controller(_Settings(correction_learning_enabled=True))
        ctrl._last_correction_capture = {
            "id": "capture-1",
            "text": "Use mambo here",
            "replace_verified": False,
        }

        class _Manager:
            @staticmethod
            def preview(_original, _corrected):
                return {
                    "ok": True,
                    "changes": [{"from": "mambo", "to": "Mumble"}],
                }

        ctrl._ensure_correction_learning = lambda: _Manager()
        ctrl._on_target_correction(
            "capture-1", "Use mambo here", "Use Mumble here"
        )
        self.assertEqual(
            ctrl._last_correction_capture["detected_text"], "Use Mumble here"
        )
        self.assertEqual(ctrl.island.last_offer[0], "Learn")
        self.assertIn("mambo", ctrl.island.last_offer[1])
        # Detection only prepares a review. It never writes settings itself.
        self.assertNotIn("vocabulary", ctrl.settings.values)

    def test_secret_shaped_text_is_never_auto_observed(self):
        self.assertFalse(
            Mumble._correction_auto_detect_safe(
                "api_key = AbCdEf0123456789+/AbCdEf0123456789"
            )
        )
        self.assertFalse(
            Mumble._correction_auto_detect_safe(
                "AbCdEf0123456789+/AbCdEf0123456789"
            )
        )
        self.assertTrue(Mumble._correction_auto_detect_safe("Use C++ and OpenAI"))

    def test_offer_opens_a_detected_correction_review(self):
        island = Island.__new__(Island)
        island.bar_state = {
            "correction_available": False,
            "correction_label": "Review",
            "expanded": True,
        }
        island.state = "idle"
        island.hint_text = ""
        island.hint_left = 0
        island.is_suggest = False
        island.widget = None

        island.offer_correction("Learn", "Save OpenAI correction?")
        self.assertTrue(island.bar_state["correction_available"])
        self.assertEqual(island.bar_state["correction_label"], "Learn")
        self.assertFalse(island.bar_state["expanded"])
        self.assertEqual(island.hint_text, "Save OpenAI correction?")
        self.assertEqual(island.state, "hint")

    def test_companion_controls_are_limited_to_actionable_states(self):
        island = Island.__new__(Island)
        island.bar_state = {
            "correction_available": False,
            "control_review_available": False,
        }
        for state in ("transcribing", "building", "done", "hint", "idle"):
            island.state = state
            self.assertFalse(island._controls_visible(), state)

        island.state = "listening"
        self.assertTrue(island._controls_visible())
        island.state = "hint"
        island.bar_state["correction_available"] = True
        self.assertTrue(island._controls_visible())

    def test_foreign_toggle_does_not_replace_the_listening_state_with_a_hint(self):
        ctrl = _controller(_Settings(foreign_mode=False))
        pushed = []
        ctrl._push_island_bar_state = lambda: pushed.append(True)

        self.assertTrue(ctrl.toggle_island_foreign())
        self.assertTrue(ctrl.settings.values["foreign_mode"])
        self.assertEqual(pushed, [True])

    def test_parallel_lazy_initialization_creates_one_manager(self):
        ctrl = Mumble.__new__(Mumble)
        ctrl.settings = _Settings()
        ctrl._correction_learning = None
        ctrl._correction_init_lock = threading.RLock()
        created = []

        class _Manager:
            def __init__(self, *_args):
                # Widen the race window: without the controller init lock both
                # worker threads construct a manager for the same history file.
                time.sleep(0.04)
                created.append(self)

        results = []
        with mock.patch(
            "experimental.correction_learning.CorrectionLearningManager", _Manager
        ):
            workers = [
                threading.Thread(
                    target=lambda: results.append(ctrl._ensure_correction_learning())
                )
                for _ in range(2)
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=2)

        self.assertEqual(len(created), 1)
        self.assertEqual(len(results), 2)
        self.assertIs(results[0], results[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
