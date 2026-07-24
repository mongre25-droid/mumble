import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from experimental.correction_learning import InsertedSpanTracker, UIAEditMonitor


class InsertedSpanTrackerTests(unittest.TestCase):
    def test_extracts_only_edit_inside_exact_prefix_and_suffix(self):
        field = "private prefix | pasted words | private suffix"
        start = field.index("pasted words")
        tracker = InsertedSpanTracker(field, "pasted words", span_start=start)
        edited = "private prefix | corrected term | private suffix"
        self.assertEqual(tracker.extract(edited), "corrected term")
        retained_strings = [
            value for value in tracker.__dict__.values() if isinstance(value, str)
        ]
        self.assertNotIn(field, retained_strings)
        self.assertFalse(any("private prefix" in value for value in retained_strings))
        self.assertFalse(any("private suffix" in value for value in retained_strings))

    def test_outside_edit_invalidates_tracker(self):
        tracker = InsertedSpanTracker("before pasted after", "pasted")
        self.assertIsNone(tracker.extract("changed-before corrected after"))
        self.assertTrue(tracker.invalidated)
        self.assertIsNone(tracker.extract("before corrected after"))

    def test_debounce_emits_original_and_stable_correction(self):
        tracker = InsertedSpanTracker(
            "before pasted after",
            "pasted",
            debounce_seconds=1.2,
        )
        self.assertIsNone(tracker.observe("before fixed after", now=10.0))
        self.assertIsNone(tracker.observe("before fixed after", now=11.1))
        self.assertEqual(
            tracker.observe("before fixed after", now=11.2),
            ("pasted", "fixed"),
        )
        self.assertIsNone(tracker.observe("before fixed after", now=12.5))

    def test_new_edit_restarts_debounce_and_revert_clears_it(self):
        tracker = InsertedSpanTracker(
            "before pasted after",
            "pasted",
            debounce_seconds=1.0,
        )
        tracker.observe("before first after", now=1.0)
        tracker.observe("before second after", now=1.8)
        self.assertIsNone(tracker.observe("before second after", now=2.7))
        self.assertEqual(
            tracker.observe("before second after", now=2.8),
            ("pasted", "second"),
        )
        self.assertIsNone(tracker.observe("before pasted after", now=3.0))
        self.assertIsNone(tracker.observe("before third after", now=3.1))

    def test_duplicate_segment_requires_position_or_caret(self):
        field = "same and same"
        with self.assertRaises(ValueError):
            InsertedSpanTracker(field, "same")
        tracker = InsertedSpanTracker(field, "same", caret_end=len(field))
        self.assertEqual(tracker.extract("same and corrected"), "corrected")


class UIAEditMonitorTests(unittest.TestCase):
    def test_non_windows_path_is_gracefully_unavailable_and_import_safe(self):
        before_modules = set(sys.modules)
        with patch(
            "experimental.correction_learning.monitor.sys.platform", "linux"
        ):
            monitor = UIAEditMonitor()
            result = monitor.start("pasted", lambda _old, _new: None)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unsupported_platform")
        self.assertFalse(monitor.status()["active"])
        newly_loaded = set(sys.modules) - before_modules
        self.assertNotIn("pywinauto", newly_loaded)
        self.assertNotIn("comtypes", newly_loaded)

    def test_monitor_never_allows_more_than_thirty_seconds(self):
        monitor = UIAEditMonitor(max_seconds=300)
        self.assertEqual(monitor.max_seconds, 30.0)

    def test_prewarm_is_async_and_creates_no_backend(self):
        imports_started = threading.Event()
        release_imports = threading.Event()

        def slow_imports():
            imports_started.set()
            release_imports.wait(1.0)

        with patch(
            "experimental.correction_learning.monitor.sys.platform", "win32"
        ):
            monitor = UIAEditMonitor()
            monitor._import_optional_modules = slow_imports
            first = monitor.prewarm()
            self.assertTrue(first["ok"])
            self.assertEqual(first["state"], "warming")
            self.assertTrue(imports_started.wait(0.5))
            not_ready = monitor.start("pasted", lambda _old, _new: None)
            self.assertFalse(not_ready["ok"])
            self.assertEqual(not_ready["reason"], "warming_up")
            release_imports.set()
            ready = monitor.prewarm(wait=True, timeout=1.0)
        self.assertTrue(ready["ready"])
        self.assertEqual(monitor.status()["prewarm_state"], "ready")

    def test_backend_is_constructed_on_monitor_thread_and_password_is_skipped(self):
        caller_thread = threading.get_ident()
        factory_threads = []

        class PasswordBackend:
            @staticmethod
            def foreground_id():
                return 10

            @staticmethod
            def focused_edit():
                return SimpleNamespace(
                    identity=("password",),
                    value="",
                    caret_end=None,
                    is_password=True,
                    is_editable=False,
                )

        def factory():
            factory_threads.append(threading.get_ident())
            return PasswordBackend()

        monitor = UIAEditMonitor(backend_factory=factory)
        result = monitor.start("secret", lambda _old, _new: None)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "password_control")
        self.assertNotEqual(factory_threads, [caller_thread])

    def test_fake_backend_observes_only_a_stable_inserted_span(self):
        class FakeBackend:
            value = "before pasted after"

            @staticmethod
            def foreground_id():
                return 10

            def focused_edit(self):
                return SimpleNamespace(
                    identity=("edit", 1),
                    value=self.value,
                    caret_end=len("before pasted"),
                    is_password=False,
                    is_editable=True,
                )

        backend = FakeBackend()
        observed = []
        callback_event = threading.Event()

        def callback(original, corrected):
            observed.append((original, corrected))
            callback_event.set()

        monitor = UIAEditMonitor(
            poll_interval=0.1,
            debounce_seconds=0.0,
            max_seconds=1.0,
            backend_factory=lambda: backend,
        )
        result = monitor.start("pasted", callback)
        self.assertTrue(result["ok"])
        backend.value = "before corrected after"
        self.assertTrue(callback_event.wait(0.8))
        deadline = time.monotonic() + 0.5
        while monitor.status()["active"] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(observed, [("pasted", "corrected")])
        self.assertEqual(monitor.status()["state"], "completed")


if __name__ == "__main__":
    unittest.main()
