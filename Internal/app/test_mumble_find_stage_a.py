"""Focused Level A contracts for Mumble Find Stage A."""

from pathlib import Path
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
from mumble_find import MumbleFindLifecycle, WindowsForegroundFocus


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
        adapter = WindowsForegroundFocus(
            "Mumble Find", user32=user32, find_window=lambda: 202
        )

        self.assertEqual(adapter.capture(), 101)
        self.assertTrue(adapter.restore(101))
        self.assertEqual(user32.restored, [("focus", 101)])

        user32.foreground = 202
        self.assertIsNone(adapter.capture())
        self.assertFalse(adapter.restore(202))
        user32.valid.remove(101)
        self.assertFalse(adapter.restore(101))

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

if __name__ == "__main__":
    unittest.main()
