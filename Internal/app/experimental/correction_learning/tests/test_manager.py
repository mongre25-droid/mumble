import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import branding
import settings as settings_module
from experimental.correction_learning import CorrectionLearningManager


class MemorySettings:
    def __init__(self, **values):
        self.data = {
            "vocabulary": {},
            "vocabulary_terms": [],
            "correction_learning_enabled": False,
        }
        self.data.update(values)
        self.update_calls = 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def update(self, **values):
        self.update_calls += 1
        self.data.update(values)

    def set(self, key, value):
        self.data[key] = value


class SetOnlySettings:
    def __init__(self):
        self.data = {"vocabulary": {}, "vocabulary_terms": []}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class CorrectionManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.settings = MemorySettings()
        self.manager = CorrectionLearningManager(self.settings, self.temporary.name)

    def test_preview_does_not_mutate_settings_or_history(self):
        result = self.manager.preview("Open mum bull", "Open Mumble")
        self.assertTrue(result["ok"])
        self.assertEqual(result["changes"][0]["mapping_action"], "add")
        self.assertEqual(result["changes"][0]["term_action"], "add")
        self.assertEqual(self.settings.data["vocabulary"], {})
        self.assertFalse(Path(self.temporary.name, "correction_learning.json").exists())

    def test_learn_updates_both_settings_in_one_update(self):
        result = self.manager.learn("Open mum bull now", "Open Mumble now")
        self.assertTrue(result["ok"])
        self.assertIsNotNone(result["session_id"])
        self.assertEqual(self.settings.data["vocabulary"], {"mum bull": "Mumble"})
        self.assertEqual(self.settings.data["vocabulary_terms"], ["Mumble"])
        self.assertEqual(self.settings.update_calls, 1)
        status = self.manager.status()
        self.assertEqual(status["learned_count"], 1)
        self.assertEqual(
            status["last"],
            {
                "id": result["session_id"],
                "changes": [{"from": "mum bull", "to": "Mumble"}],
            },
        )

    def test_learn_operates_while_status_toggle_is_off(self):
        self.assertFalse(self.manager.status()["enabled"])
        learned = self.manager.learn("Use the api client", "Use the API client")
        self.assertTrue(learned["ok"])
        self.settings.data["correction_learning_enabled"] = True
        self.assertTrue(self.manager.status()["enabled"])

    def test_history_is_atomic_json_and_contains_no_full_transcripts(self):
        original = "SECRET-CONTEXT-91 please open mum bull settings"
        corrected = "SECRET-CONTEXT-91 please open Mumble settings"
        result = self.manager.learn(original, corrected)
        self.assertTrue(result["ok"])
        path = Path(self.temporary.name, "correction_learning.json")
        raw = path.read_text(encoding="utf-8")
        state = json.loads(raw)
        self.assertNotIn(original, raw)
        self.assertNotIn(corrected, raw)
        self.assertNotIn("SECRET-CONTEXT-91", raw)
        self.assertEqual(state["sessions"][0]["id"], result["session_id"])
        self.assertEqual(list(path.parent.glob(".*.tmp")), [])

    def test_undo_restores_previous_mapping_and_term_casing(self):
        self.settings.data["vocabulary"] = {"ay pee eye": "Api"}
        self.settings.data["vocabulary_terms"] = ["Api"]
        learned = self.manager.learn("Use ay pee eye client", "Use API client")
        self.assertTrue(learned["ok"])
        self.assertEqual(self.settings.data["vocabulary"]["ay pee eye"], "API")
        self.assertEqual(self.settings.data["vocabulary_terms"], ["API"])

        undone = self.manager.undo_last()
        self.assertTrue(undone["ok"])
        self.assertEqual(undone["session_id"], learned["session_id"])
        self.assertEqual(self.settings.data["vocabulary"], {"ay pee eye": "Api"})
        self.assertEqual(self.settings.data["vocabulary_terms"], ["Api"])
        self.assertFalse(self.manager.status()["undo_available"])

    def test_undo_removes_new_mapping_and_term(self):
        learned = self.manager.learn("Open mum bull", "Open Mumble")
        undone = self.manager.undo_last()
        self.assertTrue(learned["ok"])
        self.assertTrue(undone["ok"])
        self.assertEqual(self.settings.data["vocabulary"], {})
        self.assertEqual(self.settings.data["vocabulary_terms"], [])

    def test_undo_does_not_clobber_a_later_mapping_edit(self):
        self.manager.learn("Open mum bull", "Open Mumble")
        self.settings.data["vocabulary"]["mum bull"] = "MUMBLE-USER-CHOICE"
        undone = self.manager.undo_last()
        self.assertTrue(undone["ok"])
        self.assertEqual(
            self.settings.data["vocabulary"]["mum bull"],
            "MUMBLE-USER-CHOICE",
        )
        self.assertEqual(self.settings.data["vocabulary_terms"], ["Mumble"])
        self.assertEqual(undone["skipped"][0]["reason"], "mapping_changed_later")

    def test_undo_does_not_clobber_a_later_term_edit(self):
        self.manager.learn("Open mum bull", "Open Mumble")
        self.settings.data["vocabulary_terms"].append("LaterTerm")
        undone = self.manager.undo_last()
        self.assertTrue(undone["ok"])
        self.assertEqual(self.settings.data["vocabulary_terms"], ["Mumble", "LaterTerm"])
        self.assertIn("terms_changed_later", {item["reason"] for item in undone["skipped"]})

    def test_already_learned_is_successful_noop_without_new_session(self):
        first = self.manager.learn("Open mum bull", "Open Mumble")
        second = self.manager.learn("Open mum bull", "Open Mumble")
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertIsNone(second["session_id"])
        self.assertEqual(self.manager.status()["history_count"], 1)

    def test_explicit_json_path_and_set_only_settings_are_supported(self):
        settings = SetOnlySettings()
        path = Path(self.temporary.name, "private", "audit.json")
        manager = CorrectionLearningManager(settings, path)
        result = manager.learn("Use the api client", "Use the API client")
        self.assertTrue(result["ok"])
        self.assertTrue(path.exists())
        self.assertEqual(settings.data["vocabulary"], {"api": "API"})

    def test_corrupt_history_fails_closed_and_is_not_overwritten(self):
        path = Path(self.temporary.name, "correction_learning.json")
        path.write_text("{not json", encoding="utf-8")
        manager = CorrectionLearningManager(self.settings, self.temporary.name)
        status = manager.status()
        learned = manager.learn("Open mum bull", "Open Mumble")
        self.assertFalse(status["ready"])
        self.assertFalse(learned["ok"])
        self.assertEqual(path.read_text(encoding="utf-8"), "{not json")

    def test_well_formed_but_invalid_history_also_fails_closed(self):
        path = Path(self.temporary.name, "correction_learning.json")
        invalid = {"schema_version": 1, "sessions": [{"id": "partial"}]}
        path.write_text(json.dumps(invalid), encoding="utf-8")
        manager = CorrectionLearningManager(self.settings, self.temporary.name)
        self.assertFalse(manager.status()["ready"])
        self.assertFalse(manager.undo_last()["ok"])
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), invalid)

    def test_plain_mutable_mapping_is_supported(self):
        settings = {"vocabulary": {}, "vocabulary_terms": []}
        manager = CorrectionLearningManager(settings, self.temporary.name)
        result = manager.learn("Use the api client", "Use the API client")
        self.assertTrue(result["ok"])
        self.assertEqual(settings["vocabulary_terms"], ["API"])

    def test_failed_history_save_compensates_settings_without_name_error(self):
        with patch.object(
            self.manager._store,
            "save",
            side_effect=OSError("forced history failure"),
        ):
            result = self.manager.learn("Open mum bull", "Open Mumble")
        self.assertFalse(result["ok"])
        self.assertIn("Could not save correction history", result["message"])
        self.assertEqual(self.settings.data["vocabulary"], {})
        self.assertEqual(self.settings.data["vocabulary_terms"], [])

    def test_failed_undo_history_save_restores_learned_settings(self):
        learned = self.manager.learn("Open mum bull", "Open Mumble")
        self.assertTrue(learned["ok"])
        with patch.object(
            self.manager._store,
            "save",
            side_effect=OSError("forced undo history failure"),
        ):
            result = self.manager.undo_last()
        self.assertFalse(result["ok"])
        self.assertEqual(self.settings.data["vocabulary"], {"mum bull": "Mumble"})
        self.assertEqual(self.settings.data["vocabulary_terms"], ["Mumble"])


class RealSettingsConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.original_data_dir = branding.DATA_DIR
        self.original_settings_path = branding.SETTINGS_PATH
        branding.DATA_DIR = self.temporary.name
        branding.SETTINGS_PATH = os.path.join(self.temporary.name, "settings.json")
        self.addCleanup(self._restore_branding)

    def _restore_branding(self):
        branding.DATA_DIR = self.original_data_dir
        branding.SETTINGS_PATH = self.original_settings_path

    def test_atomic_settings_update_fails_closed_without_shared_lock(self):
        instance = settings_module.Settings()
        callback_called = False

        def mutate(vocabulary, terms):
            nonlocal callback_called
            callback_called = True
            vocabulary["unsafe"] = "Unsafe"

        with patch.object(settings_module, "_acquire_lock", return_value=None):
            with self.assertRaises(TimeoutError):
                instance.atomic_vocabulary_update(mutate)
        self.assertFalse(callback_called)
        self.assertNotIn("unsafe", settings_module.Settings().get("vocabulary"))

    def test_stale_controller_learn_and_undo_preserve_other_instance_edits(self):
        controller = settings_module.Settings()
        web = settings_module.Settings()

        # The controller remains stale while the independent web Settings
        # instance commits a manual vocabulary edit.
        web.update(
            vocabulary={"manual heard": "ManualTerm"},
            vocabulary_terms=["ManualTerm"],
        )
        self.assertEqual(controller.get("vocabulary"), {})

        audit_dir = os.path.join(self.temporary.name, "corrections")
        manager = CorrectionLearningManager(controller, audit_dir)
        learned = manager.learn("Open mum bull now", "Open Mumble now")
        self.assertTrue(learned["ok"])

        fresh = settings_module.Settings()
        self.assertEqual(
            fresh.get("vocabulary"),
            {"manual heard": "ManualTerm", "mum bull": "Mumble"},
        )
        self.assertEqual(
            set(fresh.get("vocabulary_terms")), {"ManualTerm", "Mumble"}
        )

        # A second process edits both collections after learning. Undo must
        # re-read that latest disk state, remove only its own exact mapping, and
        # preserve the newer collection revision.
        other = settings_module.Settings()

        def add_later_edit(vocabulary, terms):
            vocabulary["later heard"] = "LaterTerm"
            terms.append("LaterTerm")

        other.atomic_vocabulary_update(add_later_edit)
        undone = manager.undo_last()
        self.assertTrue(undone["ok"])

        final = settings_module.Settings()
        self.assertEqual(
            final.get("vocabulary"),
            {"manual heard": "ManualTerm", "later heard": "LaterTerm"},
        )
        self.assertIn("ManualTerm", final.get("vocabulary_terms"))
        self.assertIn("LaterTerm", final.get("vocabulary_terms"))

    def test_real_settings_history_failure_rolls_back_without_losing_manual_edit(self):
        controller = settings_module.Settings()
        web = settings_module.Settings()
        web.update(
            vocabulary={"manual heard": "ManualTerm"},
            vocabulary_terms=["ManualTerm"],
        )
        manager = CorrectionLearningManager(
            controller, os.path.join(self.temporary.name, "failed-history")
        )
        with patch.object(
            manager._store,
            "save",
            side_effect=OSError("forced history failure"),
        ):
            result = manager.learn("Open mum bull", "Open Mumble")
        self.assertFalse(result["ok"])
        fresh = settings_module.Settings()
        self.assertEqual(
            fresh.get("vocabulary"), {"manual heard": "ManualTerm"}
        )
        self.assertEqual(fresh.get("vocabulary_terms"), ["ManualTerm"])

    def test_two_managers_keep_both_concurrent_learns_and_history_sessions(self):
        settings_a = settings_module.Settings()
        settings_b = settings_module.Settings()
        audit_dir = os.path.join(self.temporary.name, "shared-corrections")
        manager_a = CorrectionLearningManager(settings_a, audit_dir)
        manager_b = CorrectionLearningManager(settings_b, audit_dir)

        # If history read/modify/write is not serialized, this barrier makes both
        # managers load the same old state and deterministically lose one save.
        barrier = threading.Barrier(2)
        for manager in (manager_a, manager_b):
            original_load = manager._store.load

            def delayed_load(load=original_load):
                state = load()
                try:
                    barrier.wait(timeout=0.2)
                except threading.BrokenBarrierError:
                    pass
                return state

            manager._store.load = delayed_load

        results = []

        def learn(manager, original, corrected):
            results.append(manager.learn(original, corrected))

        threads = [
            threading.Thread(
                target=learn,
                args=(manager_a, "Open mum bull now", "Open Mumble now"),
            ),
            threading.Thread(
                target=learn,
                args=(manager_b, "Use the api client", "Use the API client"),
            ),
        ]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 30.0
        for thread in threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result["ok"] for result in results))
        fresh = settings_module.Settings()
        self.assertEqual(
            fresh.get("vocabulary"),
            {"mum bull": "Mumble", "api": "API"},
        )
        self.assertEqual(set(fresh.get("vocabulary_terms")), {"Mumble", "API"})
        status = CorrectionLearningManager(settings_module.Settings(), audit_dir).status()
        self.assertEqual(status["history_count"], 2)
        self.assertEqual(status["learned_count"], 2)


if __name__ == "__main__":
    unittest.main()
