"""Public contract checks for the packaged Focus Stage UI foundation."""

import json
from pathlib import Path
import unittest


WEBUI = Path(__file__).resolve().parent / "webui"
CONTRACT = WEBUI / "focus-stage-contract.json"
REPO_ROOT = Path(__file__).resolve().parents[2]


class FocusStageContractTests(unittest.TestCase):
    def load_contract(self):
        return json.loads(CONTRACT.read_text(encoding="utf-8"))

    def test_packaged_contract_names_exactly_six_destinations(self):
        contract = self.load_contract()

        self.assertEqual(
            contract["destinations"],
            [
                {"id": "home", "label": "Home"},
                {"id": "history", "label": "Deck"},
                {"id": "stats", "label": "Stats"},
                {"id": "meetings", "label": "Meetings"},
                {"id": "reader", "label": "Reader"},
                {"id": "settings", "label": "Settings"},
            ],
        )

    def test_contract_exposes_stable_shared_primitives_and_states(self):
        contract = self.load_contract()

        self.assertEqual(
            contract["primitives"],
            [
                "focus-stage",
                "context-ledger",
                "numbered-spine",
                "command-surface",
                "state-surface",
                "control-rail",
            ],
        )
        self.assertEqual(
            list(contract["states"]),
            ["loading", "empty", "degraded", "error", "success"],
        )
        for state, description in contract["states"].items():
            with self.subTest(state=state):
                self.assertEqual(description["kind"], state)
                self.assertIn(description["tone"], {"neutral", "warning", "danger", "positive"})
                self.assertTrue(description["title"])
                self.assertTrue(description["message"])
                self.assertIn(description["live"], {"polite", "assertive"})
        self.assertEqual(contract["effects"], ["light", "standard", "full"])
        self.assertEqual(
            contract["statusSemantics"],
            {
                "gold": ["neutral", "warning"],
                "green": ["positive"],
                "red": ["danger"],
            },
        )
        self.assertEqual(contract["maxPrimaryActions"], 1)
        self.assertEqual(contract["controlRail"]["minimumTargetPx"], 28)
        self.assertEqual(contract["controlRail"]["timeCriticalTargetPx"], 36)

    def test_package_builder_verifies_every_foundation_asset(self):
        builder = (REPO_ROOT / "Development Files" / "Tooling" / "_rebuild_zip.py").read_text(encoding="utf-8")

        for filename in ("focus-stage-contract.json", "focus-stage.js", "focus-stage.css"):
            with self.subTest(filename=filename):
                self.assertIn(f'Mumble/Internal/app/webui/{filename}', builder)


if __name__ == "__main__":
    unittest.main()
