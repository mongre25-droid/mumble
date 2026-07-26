"""Focused Level A contracts for Mumble Find Stage A."""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from experimental.system_search.engine import (
    QueryCancellation,
    SearchItem,
    SystemSearchEngine,
)
from experimental.system_search.windows_search import WindowsSearchProvider
from experimental.system_search.process_provider import WindowsSearchProcessProvider
from mumble_find import MumbleFindLifecycle, WindowsForegroundFocus


def _blocked_process_provider(connection):
    try:
        connection.recv()
        threading.Event().wait()
    except (EOFError, OSError):
        return


def _completing_process_provider(connection):
    try:
        while True:
            request = connection.recv()
            if request.get("kind") == "stop":
                return
            connection.send({
                "request_id": request["request_id"],
                "ok": True,
                "result": {
                    "items": [],
                    "state": "complete",
                    "message": "",
                },
            })
    except (EOFError, OSError):
        return


class _Window:
    def __init__(self):
        self.show_count = 0
        self.hide_count = 0
        self.restore_count = 0

    def show(self):
        self.show_count += 1

    def restore(self):
        self.restore_count += 1

    def hide(self):
        self.hide_count += 1


class _Focus:
    def __init__(self, restore_result=True):
        self.captured = []
        self.restored = []
        self.restore_result = restore_result

    def capture(self):
        target = f"editor-{len(self.captured) + 1}"
        self.captured.append(target)
        return target

    def restore(self, target):
        self.restored.append(target)
        return self.restore_result


class MumbleFindLifecycleTests(unittest.TestCase):
    def make_lifecycle(self, *, restore_result=True):
        created = []
        creation_options = []

        def create_window(*, hidden, centered):
            creation_options.append({"hidden": hidden, "centered": centered})
            window = _Window()
            created.append(window)
            return window

        focus = _Focus(restore_result=restore_result)
        lifecycle = MumbleFindLifecycle(create_window, focus)
        return lifecycle, created, creation_options, focus

    def test_twenty_toggles_reuse_one_centred_resident_window(self):
        lifecycle, created, options, focus = self.make_lifecycle()

        resident = lifecycle.ensure_resident()
        self.assertEqual(resident["state"], "hidden")
        self.assertEqual(options, [{"hidden": True, "centered": True}])

        results = [lifecycle.toggle() for _ in range(20)]

        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].show_count, 10)
        self.assertEqual(created[0].hide_count, 10)
        self.assertEqual(created[0].restore_count, 10)
        self.assertEqual(len(focus.captured), 10)
        self.assertEqual(focus.restored, focus.captured)
        self.assertEqual(results[-1]["state"], "hidden")
        self.assertFalse(lifecycle.visible)

    def test_show_and_hide_are_idempotent(self):
        lifecycle, created, _options, focus = self.make_lifecycle()

        first_show = lifecycle.show()
        second_show = lifecycle.show()
        first_hide = lifecycle.hide()
        second_hide = lifecycle.hide()

        self.assertTrue(first_show["changed"])
        self.assertFalse(second_show["changed"])
        self.assertTrue(first_hide["changed"])
        self.assertFalse(second_hide["changed"])
        self.assertEqual(created[0].show_count, 1)
        self.assertEqual(created[0].hide_count, 1)
        self.assertEqual(focus.restored, [focus.captured[0]])

    def test_duplicate_toggle_operation_returns_the_original_terminal_outcome_once(self):
        lifecycle, created, _options, _focus = self.make_lifecycle()

        first = lifecycle.toggle("1" * 32)
        duplicate = lifecycle.toggle("1" * 32)
        next_action = lifecycle.toggle("2" * 32)

        self.assertEqual(duplicate, first)
        self.assertEqual(created[0].show_count, 1)
        self.assertEqual(created[0].hide_count, 1)
        self.assertEqual(next_action["state"], "hidden")

    def test_concurrent_duplicate_toggle_delivery_applies_one_transition(self):
        lifecycle, created, _options, _focus = self.make_lifecycle()
        barrier = threading.Barrier(9)
        results = []

        def deliver():
            barrier.wait()
            results.append(lifecycle.toggle("3" * 32))

        workers = [threading.Thread(target=deliver) for _ in range(8)]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(1.0)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(created[0].show_count, 1)
        self.assertEqual({result["state"] for result in results}, {"visible"})
        self.assertLessEqual(lifecycle.operation_outcome_count, 256)

    def test_terminal_toggle_outcome_store_is_strictly_bounded(self):
        lifecycle, _created, _options, _focus = self.make_lifecycle()

        for value in range(300):
            lifecycle.toggle(f"{value:032x}")

        self.assertEqual(lifecycle.operation_outcome_count, 256)

    def test_failed_focus_restore_is_reported_without_reshowing(self):
        lifecycle, created, _options, focus = self.make_lifecycle(
            restore_result=False
        )
        lifecycle.show()

        result = lifecycle.hide()

        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "hidden")
        self.assertFalse(result["focus_restored"])
        self.assertIn("focus", result["message"].lower())
        self.assertEqual(created[0].hide_count, 1)
        self.assertEqual(focus.restored, [focus.captured[0]])

    def test_window_creation_failure_is_returned_as_an_honest_state(self):
        lifecycle = MumbleFindLifecycle(
            lambda **_options: (_ for _ in ()).throw(RuntimeError("backend failed")),
            _Focus(),
        )

        result = lifecycle.toggle()

        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "unavailable")
        self.assertFalse(result["resident"])
        self.assertIn("backend failed", result["message"])


class MumbleFindWindowsFocusTests(unittest.TestCase):
    class User32:
        def __init__(self):
            self.foreground = 101
            self.valid = {101, 202}
            self.visible = {101, 202}
            self.restored = []

        def GetForegroundWindow(self):
            return self.foreground

        def IsWindow(self, hwnd):
            return hwnd in self.valid

        def IsWindowVisible(self, hwnd):
            return hwnd in self.visible

        def IsIconic(self, _hwnd):
            return False

        def ShowWindow(self, hwnd, command):
            self.restored.append(("show", hwnd, command))
            return True

        def SetForegroundWindow(self, hwnd):
            self.restored.append(("focus", hwnd))
            return True

    def test_adapter_captures_and_restores_only_a_valid_non_find_window(self):
        user32 = self.User32()
        identities = {
            101: (101, 11, 1001, 50001),
            202: (202, 22, 2002, 50002),
        }
        adapter = WindowsForegroundFocus(
            "Mumble Find", user32=user32, find_window=lambda: 202,
            identity_resolver=lambda hwnd: identities.get(hwnd),
        )

        target = adapter.capture()
        self.assertEqual((target.hwnd, target.thread_id, target.process_id,
                          target.process_created), identities[101])
        self.assertTrue(adapter.restore(target))
        self.assertEqual(user32.restored, [("focus", 101)])

        user32.foreground = 202
        self.assertIsNone(adapter.capture())
        self.assertFalse(adapter.restore(target.__class__(*identities[202])))
        user32.valid.remove(101)
        self.assertFalse(adapter.restore(target))
        user32.valid.add(101)
        user32.visible.remove(101)
        self.assertFalse(adapter.restore(target))
        user32.visible.add(101)
        user32.SetForegroundWindow = lambda _hwnd: False
        self.assertFalse(adapter.restore(target))

    def test_focus_restore_rejects_hwnd_and_pid_reuse_or_process_recreation(self):
        user32 = self.User32()
        current = [101, 11, 1001, 50001]
        adapter = WindowsForegroundFocus(
            "Mumble Find", user32=user32, find_window=lambda: 202,
            identity_resolver=lambda hwnd: tuple(current) if hwnd == 101 else None,
        )
        captured = adapter.capture()

        for changed in (
            [101, 12, 1001, 50001],  # thread/window identity changed
            [101, 11, 2002, 50001],  # HWND reused by another process
            [101, 11, 1001, 60002],  # same PID recreated
        ):
            current[:] = changed
            self.assertFalse(adapter.restore(captured))
        current[:] = [101, 11, 1001, 50001]
        self.assertTrue(adapter.restore(captured))

    def test_focus_capture_fails_closed_when_process_identity_is_unavailable(self):
        user32 = self.User32()
        adapter = WindowsForegroundFocus(
            "Mumble Find", user32=user32, find_window=lambda: 202,
            identity_resolver=lambda _hwnd: None,
        )
        self.assertIsNone(adapter.capture())

class _IndexedProvider:
    def __init__(self, *, state="complete", message="", rows=None):
        self.state = state
        self.message = message
        self.rows = list(rows or [])
        self.calls = []
        self.cancelled = []
        self.started = threading.Event()

    def status(self):
        return {
            "available": self.state != "error",
            "state": self.state,
            "name": "Windows Search",
            "message": self.message,
        }

    def cancel(self, generation):
        self.cancelled.append(generation)

    def query(self, query, *, limit, deadline, cancellation, generation):
        self.calls.append({
            "query": query,
            "limit": limit,
            "deadline": deadline,
            "generation": generation,
        })
        if query == "old":
            self.started.set()
            while not cancellation.cancelled and time.monotonic() < deadline:
                time.sleep(0.001)
        rows = self.rows
        if query == "new":
            rows = [SearchItem.make(
                "file", "New result.txt", r"C:\\Indexed\\New result.txt",
                source="windows-search",
            )]
        elif query == "old":
            rows = [SearchItem.make(
                "file", "Old result.txt", r"C:\\Indexed\\Old result.txt",
                source="windows-search",
            )]
        return {
            "items": rows[:limit],
            "state": self.state,
            "message": self.message,
        }


class MumbleFindQueryTests(unittest.TestCase):
    def make_engine(self, root, provider):
        engine = SystemSearchEngine(
            settings={"system_search_include_files": True},
            data_dir=Path(root) / "data",
            platform="windows",
            start_background=False,
            file_provider=provider,
        )
        app = SearchItem.make(
            "app", "Mumble Notes", r"C:\\Apps\\Mumble Notes.lnk",
            source="start-menu",
        )
        engine._items[app.id] = app
        return engine

    def test_indexed_provider_is_bounded_to_twelve_and_never_adds_web_search(self):
        rows = [
            SearchItem.make(
                "file", f"Project note {index}.txt",
                rf"C:\\Indexed\\Project note {index}.txt",
                source="windows-search",
            )
            for index in range(30)
        ]
        rows.append(SearchItem.make(
            "folder", "Project notes folder", r"C:\\Indexed\\Project notes",
            source="windows-search",
        ))
        with tempfile.TemporaryDirectory() as tmp:
            provider = _IndexedProvider(rows=rows)
            engine = self.make_engine(tmp, provider)
            with patch(
                "experimental.system_search.engine.os.walk",
                side_effect=AssertionError("interactive recursive scan"),
            ):
                result = engine.search(
                    "project note", "all", limit=40,
                    generation=1, deadline_ms=75,
                )
                folder_result = engine.search(
                    "project notes", "folder", generation=2, deadline_ms=75,
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["generation"], 1)
        self.assertLessEqual(len(result["results"]), 12)
        self.assertTrue(all(row["kind"] != "web" for row in result["results"]))
        self.assertEqual(result["provider_state"], "complete")
        self.assertEqual(provider.calls[0]["generation"], 1)
        self.assertLessEqual(provider.calls[0]["limit"], 250)
        self.assertEqual(folder_result["results"][0]["kind"], "folder")

    def test_new_generation_cancels_old_work_and_old_result_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = _IndexedProvider()
            engine = self.make_engine(tmp, provider)
            old = {}

            thread = threading.Thread(
                target=lambda: old.update(engine.search(
                    "old", generation=1, deadline_ms=500,
                ))
            )
            thread.start()
            self.assertTrue(provider.started.wait(1.0))
            newest = engine.search("new", generation=2, deadline_ms=75)
            thread.join(1.0)

        self.assertFalse(thread.is_alive())
        self.assertIn(1, provider.cancelled)
        self.assertTrue(old["stale"])
        self.assertEqual(old["results"], [])
        self.assertEqual(newest["generation"], 2)
        self.assertEqual(
            [row["name"] for row in newest["results"]], ["New result.txt"]
        )

    def test_partial_provider_keeps_apps_and_complete_error_is_honest(self):
        with tempfile.TemporaryDirectory() as tmp:
            partial = _IndexedProvider(
                state="partial", message="Some indexed locations are unavailable."
            )
            engine = self.make_engine(tmp, partial)
            usable = engine.search("mumble", generation=1, deadline_ms=75)
            self.assertTrue(usable["ok"])
            self.assertEqual(usable["provider_state"], "partial")
            self.assertEqual(usable["results"][0]["kind"], "app")
            self.assertIn("unavailable", usable["message"].lower())

            failed = _IndexedProvider(
                state="error", message="Windows Search is unavailable."
            )
            engine = self.make_engine(tmp, failed)
            complete_error = engine.search(
                "missing", "file", generation=1, deadline_ms=75
            )
            self.assertFalse(complete_error["ok"])
            self.assertEqual(complete_error["provider_state"], "error")
            self.assertEqual(complete_error["results"], [])
            self.assertNotIn("web", str(complete_error).lower())

    def test_thirty_run_coordinator_distribution_meets_focused_budget(self):
        rows = [SearchItem.make(
            "file", "Budget note.txt", r"C:\\Indexed\\Budget note.txt",
            source="windows-search",
        )]
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(tmp, _IndexedProvider(rows=rows))
            durations = []
            for generation in range(1, 31):
                started = time.perf_counter()
                result = engine.search(
                    "budget", generation=generation, deadline_ms=75
                )
                durations.append((time.perf_counter() - started) * 1000)
                self.assertTrue(result["ok"])
            ordered = sorted(durations)
            p95 = ordered[28]
            retained_tokens = len(engine._query_tokens)

        self.assertLessEqual(p95, 75.0)
        self.assertEqual(retained_tokens, 1)

    def test_current_deadline_preserves_ranked_apps_as_honest_partial_results(self):
        release = threading.Event()

        class SlowProvider(_IndexedProvider):
            def query(self, *args, **kwargs):
                self.started.set()
                release.wait(1.0)
                return super().query(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            provider = SlowProvider()
            engine = self.make_engine(tmp, provider)
            result = engine.search("mumble", generation=1, deadline_ms=10)
            release.set()
            close = getattr(engine, "close", None)
            if close is not None:
                close()

        self.assertFalse(result["stale"])
        self.assertTrue(result["deadline_exceeded"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["provider_state"], "partial")
        self.assertEqual([row["kind"] for row in result["results"]], ["app"])
        self.assertNotIn("superseded", result["message"].casefold())

    def test_non_cooperative_deadlines_keep_provider_ownership_strictly_bounded(self):
        release = threading.Event()

        class BlockingProvider(_IndexedProvider):
            def query(self, *args, **kwargs):
                self.started.set()
                release.wait(2.0)
                return super().query(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            provider = BlockingProvider()
            engine = self.make_engine(tmp, provider)
            try:
                results = [
                    engine.search("mumble", generation=generation, deadline_ms=10)
                    for generation in range(1, 21)
                ]
                ownership = engine.status()["provider_work"]
                self.assertLessEqual(ownership["submitted"], 2)
                self.assertEqual(ownership["queued"], 0)
                self.assertLessEqual(ownership["owned"], 2)
                self.assertTrue(all(result["results"] for result in results))
                self.assertTrue(any(result.get("capacity_exhausted") for result in results))
            finally:
                release.set()
                close = getattr(engine, "close", None)
                if close is not None:
                    close()
                    close()

        if hasattr(engine, "close"):
            self.assertEqual(engine.status()["provider_work"]["owned"], 0)
            self.assertEqual(engine.status()["provider_work"]["threads"], 0)

    def test_api_recreation_reuses_one_process_owned_provider_boundary(self):
        from webui_shell import Api

        release = threading.Event()
        engines = []

        class BlockingProvider(_IndexedProvider):
            def query(self, *args, **kwargs):
                self.started.set()
                release.wait(5.0)
                return super().query(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            def make_engine(*args, **kwargs):
                engine = self.make_engine(tmp, BlockingProvider())
                engines.append(engine)
                return engine

            baseline_threads = sum(
                thread.name.startswith("mumble-find-query")
                for thread in threading.enumerate()
            )
            observed_threads = []
            observed_owned = []
            observed_work = []
            service_statuses = []
            release_durations = []
            newest_result = None
            try:
                with patch(
                    "experimental.system_search.SystemSearchEngine",
                    side_effect=make_engine,
                ), patch(
                    "experimental.system_search.engine.SystemSearchEngine",
                    side_effect=make_engine,
                ):
                    for lifecycle in range(3):
                        api = Api.__new__(Api)
                        api.settings = {
                            "system_search_include_files": True,
                        }
                        api._system_search = None
                        engine = api._get_system_search()
                        for offset in range(2):
                            generation = lifecycle * 2 + offset + 1
                            result = engine.search(
                                "mumble", generation=generation, deadline_ms=10,
                            )
                            self.assertTrue(result["results"])
                            newest_result = result

                        release_lease = getattr(
                            api, "_release_system_search", None,
                        )
                        if release_lease is None:
                            engine.close()
                        else:
                            release_started = time.monotonic()
                            release_lease()
                            release_durations.append(
                                time.monotonic() - release_started
                            )
                            service_statuses.append(
                                api._system_search_service.status()
                            )

                        observed_threads.append(sum(
                            thread.name.startswith("mumble-find-query")
                            for thread in threading.enumerate()
                        ) - baseline_threads)
                        observed_owned.append(sum(
                            item.status()["provider_work"]["owned"]
                            for item in engines
                        ))
                        observed_work.append(engine.status()["provider_work"])
            finally:
                release.set()
                for engine in engines:
                    engine.close()
                shutdown = getattr(
                    __import__("webui_shell"),
                    "_shutdown_process_system_search",
                    None,
                )
                if shutdown is not None:
                    shutdown()

        self.assertEqual(observed_threads, [2, 2, 2])
        self.assertEqual(observed_owned, [2, 2, 2])
        for key, expected in (
            ("executors", 1),
            ("submitted", 2),
            ("running", 2),
            ("pending", 0),
            ("queued", 0),
            ("ownership_entries", 2),
            ("processes", 0),
        ):
            self.assertEqual([work[key] for work in observed_work], [expected] * 3)
        self.assertEqual(len(engines), 1)
        self.assertTrue(newest_result["capacity_exhausted"])
        self.assertEqual(newest_result["provider_state"], "partial")
        self.assertEqual([status["leases"] for status in service_statuses], [0, 0, 0])
        self.assertTrue(all(status["engines_created"] == 1 for status in service_statuses))
        self.assertTrue(all(duration < 0.1 for duration in release_durations))

    def test_native_process_boundary_is_fixed_terminable_and_restartable(self):
        provider = WindowsSearchProcessProvider(
            worker_target=_blocked_process_provider,
        )
        token = QueryCancellation(1)
        futures = [
            provider.submit(
                "mumble", limit=12, deadline=time.monotonic() + 5,
                cancellation=token, generation=generation,
            )
            for generation in (1, 2)
        ]
        occupied = provider.work_status()
        self.assertEqual(occupied["executors"], 1)
        self.assertEqual(occupied["processes"], 2)
        self.assertEqual(occupied["threads"], 2)
        self.assertEqual(occupied["owned"], 2)
        self.assertEqual(occupied["pending"], 0)
        self.assertEqual(occupied["ownership_entries"], 2)
        with self.assertRaisesRegex(RuntimeError, "capacity"):
            provider.submit(
                "newest", limit=12, deadline=time.monotonic() + 5,
                cancellation=token, generation=3,
            )

        started = time.monotonic()
        provider.shutdown()
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertTrue(all(future.done() for future in futures))
        stopped = provider.work_status()
        self.assertEqual(stopped["executors"], 0)
        self.assertEqual(stopped["processes"], 0)
        self.assertEqual(stopped["threads"], 0)
        self.assertEqual(stopped["owned"], 0)
        self.assertEqual(stopped["ownership_entries"], 0)

        restarted = WindowsSearchProcessProvider(
            worker_target=_completing_process_provider,
        )
        try:
            clean = restarted.submit(
                "clean", limit=12, deadline=time.monotonic() + 5,
                cancellation=QueryCancellation(4), generation=4,
            ).result(timeout=2.0)
            self.assertEqual(clean["state"], "complete")
            self.assertEqual(restarted.work_status()["owned"], 0)
            again = restarted.submit(
                "again", limit=12, deadline=time.monotonic() + 5,
                cancellation=QueryCancellation(5), generation=5,
            ).result(timeout=2.0)
            self.assertEqual(again["state"], "complete")
            self.assertEqual(restarted.work_status()["processes"], 2)
            self.assertEqual(restarted.work_status()["owned"], 0)
        finally:
            restarted.shutdown()


class WindowsSearchProviderTests(unittest.TestCase):
    class _Fields:
        def __init__(self, row):
            self.row = row

        def Item(self, name):
            return type("Field", (), {"Value": self.row.get(name)})()

    class _Recordset:
        def __init__(self, rows):
            self.rows = list(rows)
            self.index = 0
            self.closed = False

        @property
        def EOF(self):
            return self.index >= len(self.rows)

        @property
        def Fields(self):
            return WindowsSearchProviderTests._Fields(self.rows[self.index])

        def MoveNext(self):
            self.index += 1

        def Close(self):
            self.closed = True

    class _Connection:
        def __init__(self, rows):
            self.recordset = WindowsSearchProviderTests._Recordset(rows)
            self.sql = ""
            self.closed = False
            self.cancelled = False

        def Execute(self, sql):
            self.sql = sql
            return self.recordset

        def Cancel(self):
            self.cancelled = True

        def Close(self):
            self.closed = True

    def test_system_index_query_is_bounded_escaped_and_returns_opaque_rows(self):
        rows = [
            {
                "System.ItemPathDisplay": r"C:\\Indexed\\O'Hare_100%.txt",
                "System.FileName": "O'Hare_100%.txt",
                "System.ItemNameDisplay": "O'Hare_100%",
                "System.ItemType": ".txt",
                "System.Kind": "document",
            },
            {
                "System.ItemPathDisplay": r"C:\\Indexed\\O'Hare projects",
                "System.FileName": "O'Hare projects",
                "System.ItemNameDisplay": "O'Hare projects",
                "System.ItemType": "",
                "System.Kind": ("folder",),
            },
        ]
        connection = self._Connection(rows)
        provider = WindowsSearchProvider(lambda: connection)
        token = QueryCancellation(7)

        result = provider.query(
            "O'Hare_[100]%", limit=999,
            deadline=time.monotonic() + 1.0,
            cancellation=token, generation=7,
        )

        self.assertEqual(result["state"], "complete")
        self.assertEqual(result["items"][0]["source"], "windows-search")
        self.assertEqual([item["kind"] for item in result["items"]], ["file", "folder"])
        self.assertIn("SELECT TOP 250", connection.sql)
        self.assertIn("O''Hare[_][[]100[]][%]", connection.sql)
        self.assertNotIn("O'Hare_[100]%", connection.sql)
        self.assertTrue(connection.recordset.closed)
        self.assertTrue(connection.closed)

    def test_system_index_unavailable_is_honest_and_keeps_no_active_query(self):
        provider = WindowsSearchProvider(
            lambda: (_ for _ in ()).throw(RuntimeError("service stopped"))
        )
        result = provider.query(
            "notes", limit=12, deadline=time.monotonic() + 1.0,
            cancellation=QueryCancellation(3), generation=3,
        )

        self.assertEqual(result["state"], "error")
        self.assertIn("installed applications remain searchable", result["message"])
        self.assertFalse(provider.status()["available"])
        self.assertFalse(provider.cancel(3))

    def test_worker_thread_initializes_queries_and_uninitializes_com_in_order(self):
        events = []
        worker_ids = []

        class ComRuntime:
            COINIT_MULTITHREADED = 0

            def CoInitializeEx(self, apartment):
                worker_ids.append(threading.get_ident())
                events.append(("initialize", apartment))

            def CoUninitialize(self):
                worker_ids.append(threading.get_ident())
                events.append(("uninitialize", None))

        connection = self._Connection([])
        original_execute = connection.Execute

        def execute(sql):
            worker_ids.append(threading.get_ident())
            events.append(("query", None))
            return original_execute(sql)

        connection.Execute = execute
        provider = WindowsSearchProvider(
            lambda: connection, com_runtime=ComRuntime()
        )
        caller_thread = threading.get_ident()
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(
                provider.query,
                "notes",
                limit=12,
                deadline=time.monotonic() + 1.0,
                cancellation=QueryCancellation(8),
                generation=8,
            ).result(timeout=1.0)

        self.assertEqual(result["state"], "complete")
        self.assertEqual([event[0] for event in events], [
            "initialize", "query", "uninitialize"
        ])
        self.assertTrue(worker_ids)
        self.assertEqual(set(worker_ids), {worker_ids[0]})
        self.assertNotEqual(worker_ids[0], caller_thread)

    def test_com_cleanup_runs_after_query_and_row_failures_and_cancellation(self):
        for mode in ("query", "row", "cancel", "deadline"):
            events = []

            class ComRuntime:
                COINIT_MULTITHREADED = 0

                def CoInitializeEx(self, _apartment):
                    events.append("initialize")

                def CoUninitialize(self):
                    events.append("uninitialize")

            connection = self._Connection([{
                "System.ItemPathDisplay": r"C:\\Indexed\\note.txt",
                "System.FileName": "note.txt",
            }])
            token = QueryCancellation(9)
            if mode == "query":
                connection.Execute = lambda _sql: (_ for _ in ()).throw(
                    RuntimeError("query failed")
                )
            elif mode == "row":
                connection.recordset.MoveNext = lambda: (_ for _ in ()).throw(
                    RuntimeError("row failed")
                )
            elif mode == "cancel":
                def cancel_during_connect():
                    token.cancel()
                    return connection
                connection_factory = cancel_during_connect
            elif mode == "deadline":
                original_move_next = connection.recordset.MoveNext

                def cross_deadline():
                    time.sleep(0.02)
                    original_move_next()
                connection.recordset.MoveNext = cross_deadline
            if mode != "cancel":
                connection_factory = lambda: connection
            provider = WindowsSearchProvider(
                connection_factory, com_runtime=ComRuntime()
            )
            provider_result = provider.query(
                "notes", limit=12,
                deadline=time.monotonic() + (0.005 if mode == "deadline" else 1.0),
                cancellation=token, generation=9,
            )
            self.assertEqual(events, ["initialize", "uninitialize"], mode)
            if mode == "deadline":
                self.assertEqual(provider_result["state"], "partial")


class MumbleFindIconTests(unittest.TestCase):
    def test_icons_are_concurrency_bounded_cached_versioned_and_cancellable(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = SystemSearchEngine(
                settings={}, data_dir=tmp, platform="windows",
                start_background=False, file_provider=_IndexedProvider(),
            )
            apps = [
                SearchItem.make(
                    "app", f"App {index}", rf"C:\\Apps\\App {index}.lnk",
                    source="start-menu",
                )
                for index in range(8)
            ]
            engine._items.update({item.id: item for item in apps})
            query = engine.search("app", generation=1, deadline_ms=75)
            version = query["icon_version"]
            lock = threading.Lock()
            active = 0
            maximum = 0
            extracted = []

            def extract(target):
                nonlocal active, maximum
                with lock:
                    active += 1
                    maximum = max(maximum, active)
                time.sleep(0.01)
                with lock:
                    active -= 1
                    extracted.append(target)
                return "data:image/png;base64,AAAA"

            with patch.object(engine, "_windows_icon_data", side_effect=extract):
                first = engine.icons(
                    [item.id for item in apps], generation=1,
                    icon_version=version,
                )
                second = engine.icons(
                    [item.id for item in apps], generation=1,
                    icon_version=version,
                )
                next_version = engine.invalidate_icon_cache()
                stale = engine.icons(
                    [apps[0].id], generation=1, icon_version=version
                )

            self.assertEqual(len(first["icons"]), 8)
            self.assertEqual(second["icons"], first["icons"])
            self.assertGreater(maximum, 1)
            self.assertLessEqual(maximum, 4)
            self.assertEqual(len(extracted), 8)
            self.assertNotEqual(next_version, version)
            self.assertTrue(stale["stale"])
            self.assertEqual(stale["icons"], {})

    def test_new_query_cancels_visible_icon_batch_without_waiting_for_workers(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = SystemSearchEngine(
                settings={}, data_dir=tmp, platform="windows",
                start_background=False, file_provider=_IndexedProvider(),
            )
            apps = [SearchItem.make(
                "app", f"App {index}", rf"C:\\Apps\\App {index}.lnk",
                source="start-menu",
            ) for index in range(8)]
            engine._items.update({item.id: item for item in apps})
            first_query = engine.search("app", generation=1, deadline_ms=75)
            started = threading.Event()
            release = threading.Event()
            icon_result = {}

            def extract(_target):
                started.set()
                release.wait(1.0)
                return "data:image/png;base64,AAAA"

            with patch.object(engine, "_windows_icon_data", side_effect=extract):
                thread = threading.Thread(target=lambda: icon_result.update(
                    engine.icons(
                        [item.id for item in apps], generation=1,
                        icon_version=first_query["icon_version"],
                    )
                ))
                thread.start()
                self.assertTrue(started.wait(0.5))
                engine.search("new", generation=2, deadline_ms=75)
                thread.join(0.5)
                release.set()
                thread.join(1.0)

            self.assertFalse(thread.is_alive())
            self.assertTrue(icon_result["stale"])
            self.assertEqual(icon_result["icons"], {})


class MumbleFindUiContractTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent
        self.ui = (self.root / "experimental" / "system_search" / "ui.js").read_text(
            encoding="utf-8"
        )
        self.css = (self.root / "experimental" / "system_search" / "ui.css").read_text(
            encoding="utf-8"
        )
        self.html = (self.root / "webui" / "index.html").read_text(
            encoding="utf-8"
        )
        self.popup = (self.root / "webui" / "search-popup.html").read_text(
            encoding="utf-8"
        )
        self.shell = (self.root / "webui_shell.py").read_text(encoding="utf-8")
        self.requirements = (self.root / "requirements.txt").read_text(encoding="utf-8")
        self.notices = (self.root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.subsystem_readme = (
            self.root / "experimental" / "system_search" / "README.md"
        ).read_text(encoding="utf-8")
        linux_root = self.root / "Ports" / "Linux" / "app"
        self.linux_launcher_sources = {
            "subsystem documentation": (
                linux_root / "experimental" / "system_search" / "README.md"
            ).read_text(encoding="utf-8"),
            "subsystem package": (
                linux_root / "experimental" / "system_search" / "__init__.py"
            ).read_text(encoding="utf-8"),
            "engine messages": (
                linux_root / "experimental" / "system_search" / "engine.py"
            ).read_text(encoding="utf-8"),
            "loaded launcher UI": (
                linux_root / "experimental" / "system_search" / "ui.js"
            ).read_text(encoding="utf-8"),
            "launcher stylesheet": (
                linux_root / "experimental" / "system_search" / "ui.css"
            ).read_text(encoding="utf-8"),
            "bridge loader": (
                linux_root / "webui" / "system-search-loader.js"
            ).read_text(encoding="utf-8"),
            "production bridge": (
                linux_root / "webui_shell.py"
            ).read_text(encoding="utf-8"),
            "production index": (
                linux_root / "webui" / "index.html"
            ).read_text(encoding="utf-8"),
        }
        self.linux_html = self.linux_launcher_sources["production index"]

    def test_visible_contract_uses_mumble_find_and_exactly_six_destinations(self):
        nav = re.findall(r'class="nav-btn[^\"]*"[^>]*data-nav="([^"]+)"', self.html)
        self.assertEqual(nav, [
            "home", "history", "stats", "meetings", "reader", "settings"
        ])
        visible_source = "\n".join((self.ui, self.html, self.popup))
        self.assertIn("Mumble Find", visible_source)
        self.assertIn("Find apps & files", visible_source)
        self.assertNotIn("Mumble Search", visible_source)
        self.assertNotIn("ss-nav-button", self.ui)
        linux_nav = re.findall(
            r'class="nav-btn[^\"]*"[^>]*data-nav="([^"]+)"',
            self.linux_html,
        )
        self.assertEqual(linux_nav, nav)
        self.assertIn("Mumble Find", self.linux_html)
        self.assertIn("Find apps &amp; files", self.linux_html)
        self.assertNotIn("Mumble Search", self.linux_html)
        self.assertNotIn("Search apps &amp; files", self.linux_html)

    def test_linux_launcher_naming_covers_loaded_production_sources(self):
        for source_name, source in self.linux_launcher_sources.items():
            with self.subTest(source=source_name):
                self.assertNotIn("Mumble Search", source)
                self.assertNotIn("Search apps & files", source)
                self.assertNotIn("Search apps &amp; files", source)

        self.assertIn(
            'aria-label="Mumble Find launcher"',
            self.linux_launcher_sources["loaded launcher UI"],
        )
        self.assertIn(
            'aria-label="Find apps &amp; files"',
            self.linux_launcher_sources["loaded launcher UI"],
        )
        self.assertIn(
            '"message": "Mumble Find UI is unavailable."',
            self.linux_launcher_sources["production bridge"],
        )
        self.assertIn(
            "Mumble Find is available on Windows and Linux.",
            self.linux_launcher_sources["engine messages"],
        )
        self.assertIn(
            "Mumble Find bridge failed",
            self.linux_launcher_sources["bridge loader"],
        )
        self.assertIn(
            "# Mumble Find",
            self.linux_launcher_sources["subsystem documentation"],
        )
        self.assertIn("Find apps &amp; files", self.linux_html)
        self.assertIn("Web Search", self.linux_html)

    def test_header_is_a_dedicated_accessible_window_drag_region(self):
        self.assertIn('class="ss-header pywebview-drag-region"', self.ui)
        self.assertIn('aria-label="Move Mumble Find window"', self.ui)
        self.assertIn('aria-label="Close Mumble Find"', self.ui)
        self.assertIn("body.search-popup-page .ss-close", self.css)
        self.assertIn("-webkit-app-region: no-drag", self.css)
        self.assertNotIn("pywebview-drag-region ss-result", self.ui)

    def test_native_window_defaults_to_screen_centre_and_keeps_drag_explicit(self):
        self.assertIn("GetSystemMetrics(0) - SEARCH_W", self.shell)
        self.assertIn("GetSystemMetrics(1) - SEARCH_H", self.shell)
        self.assertIn('kwargs.update({"x": search_x, "y": search_y})', self.shell)
        self.assertIn('"frameless": True', self.shell)
        self.assertIn('"easy_drag": False', self.shell)

    def test_query_and_icon_ui_carries_generation_deadline_and_visibility(self):
        self.assertIn('api("system_search_cancel"', self.ui)
        self.assertIn('"system_search_query"', self.ui)
        self.assertIn("request,", self.ui)
        self.assertIn("75,", self.ui)
        self.assertIn("getBoundingClientRect", self.ui)
        self.assertIn("result.icon_version", self.ui)
        self.assertIn('"system_search_icons"', self.ui)
        self.assertLess(
            self.ui.index("host.innerHTML = SS.results"),
            self.ui.index("hydrateAppIcons(host"),
        )

    def test_states_remain_usable_and_never_offer_web_search_fallback(self):
        for phrase in (
            "No local matches",
            "installed applications remain searchable",
            "provider_state",
            "Try again",
        ):
            self.assertIn(phrase, self.ui)
        self.assertNotIn("preview-web", self.ui)
        self.assertNotIn("Search the web for", self.ui)
        self.assertNotIn("web result", self.ui.lower())
        self.assertIn("Show in folder", self.ui)
        self.assertIn('data-ss-action="open"', self.ui)

    def test_find_dependency_truth_names_pinned_pywin32_and_win32com(self):
        self.assertIn("pywin32==312", self.requirements)
        self.assertIn("Mumble Find", self.requirements)
        self.assertIn("win32com", self.requirements)
        find_notice = self.notices.split("## Mumble Find", 1)[1].split("## ", 1)[0]
        self.assertIn("pywin32 312", find_notice)
        self.assertIn("win32com", find_notice)
        self.assertNotIn("uses only Python's standard library", find_notice)
        self.assertIn("pywin32 312", self.subsystem_readme)
        self.assertIn("win32com.client", self.subsystem_readme)
        self.assertIn("THIRD_PARTY_NOTICES.md", self.subsystem_readme)
        self.assertNotIn(
            "uses only Python's standard library",
            self.subsystem_readme,
        )

    def test_production_find_copy_never_promises_a_local_to_web_fallback(self):
        visible = "\n".join((self.html, self.linux_html)).casefold()
        self.assertNotIn("web fallback shown after local", visible)
        self.assertNotIn("results stay local, with an optional web fallback", visible)
        self.assertIn("web search", visible)

if __name__ == "__main__":
    unittest.main()
