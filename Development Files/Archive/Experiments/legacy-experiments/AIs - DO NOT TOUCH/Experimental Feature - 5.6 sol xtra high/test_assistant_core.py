"""Offline tests for the experimental desktop-assistant planner."""

import unittest

from assistant_core import build_plan


class PlannerTests(unittest.TestCase):
    def test_open_allowlisted_app_is_executable(self):
        plan = build_plan("open calculator")
        self.assertTrue(plan.executable)
        self.assertEqual(plan.actions[0].kind, "open_app")
        self.assertEqual(plan.actions[0].target, "calculator")

    def test_unknown_app_is_not_silently_launched(self):
        plan = build_plan("launch Mystery Studio")
        self.assertFalse(plan.executable)
        self.assertIn("allow-list", plan.actions[0].note)

    def test_known_folder_has_stable_alias(self):
        plan = build_plan("open my downloads folder")
        self.assertTrue(plan.executable)
        self.assertEqual(plan.actions[0].target, "~/Downloads")

    def test_focus_requires_confirmation(self):
        plan = build_plan("switch to Notepad")
        self.assertTrue(plan.confirmation_required)
        self.assertEqual(plan.actions[0].kind, "focus_window")

    def test_default_search_uses_brave(self):
        plan = build_plan("search for Mumble privacy")
        self.assertTrue(plan.executable)
        self.assertIn("search.brave.com", plan.actions[0].value)
        self.assertIn("Mumble+privacy", plan.actions[0].value)

    def test_specific_site_search(self):
        plan = build_plan("find deterministic rendering on GitHub")
        self.assertTrue(plan.executable)
        self.assertIn("github.com/search", plan.actions[0].value)

    def test_existing_tab_is_honestly_gated(self):
        plan = build_plan("search Mumble automation in the existing Brave tab")
        self.assertFalse(plan.executable)
        self.assertEqual(plan.actions[0].kind, "search_existing_tab")

    def test_workflow_order_is_preserved(self):
        plan = build_plan("open calculator, then open downloads")
        self.assertEqual(len(plan.actions), 2)
        self.assertEqual(
            [a.kind for a in plan.actions],
            ["open_app", "open_path"],
        )
        self.assertTrue(plan.confirmation_required)

    def test_file_edit_is_blocked(self):
        plan = build_plan("edit the project notes")
        self.assertFalse(plan.executable)
        self.assertEqual(plan.actions[0].risk, "blocked")

    def test_paste_never_claims_clipboard_safety(self):
        plan = build_plan("paste the latest transcript into Notepad")
        self.assertFalse(plan.executable)
        self.assertEqual(plan.actions[0].kind, "paste_text")

    def test_url_is_normalised(self):
        plan = build_plan("navigate to example.com/docs")
        self.assertTrue(plan.executable)
        self.assertEqual(plan.actions[0].value, "https://example.com/docs")

    def test_empty_command_is_rejected(self):
        with self.assertRaises(ValueError):
            build_plan("   ")


if __name__ == "__main__":
    unittest.main()
