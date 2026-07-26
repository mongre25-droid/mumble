"""Regression tests for the bounded Mumble Find engine."""

from pathlib import Path
import importlib.util
import os
import tempfile
import unittest
from unittest.mock import patch

from experimental.system_search import SystemSearchEngine
from experimental.system_search.engine import SearchItem


class _IndexedFileProvider:
    def __init__(self, paths):
        self.paths = [Path(path) for path in paths]

    def status(self):
        return {
            "available": True,
            "state": "ready",
            "name": "Windows Search test seam",
            "message": "",
        }

    def cancel(self, _generation):
        return None

    def query(self, query, *, limit, deadline, cancellation, generation):
        del deadline, generation
        words = str(query or "").casefold().split()
        items = []
        for path in self.paths:
            if cancellation.cancelled:
                break
            if words and not all(word in path.name.casefold() for word in words):
                continue
            items.append(SearchItem.make(
                "folder" if path.is_dir() else "file",
                path.name,
                str(path),
                source="windows-search",
            ))
        return {"items": items[:limit], "state": "complete", "message": ""}


class SystemSearchTests(unittest.TestCase):
    def make_engine(self, root, **settings):
        root = Path(root)
        apps = root / "apps"
        files = root / "files"
        data = root / "data"
        apps.mkdir()
        files.mkdir()
        (apps / "mumble-notes.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Mumble Notes\n"
            "Comment=Write project notes\nExec=mumble-notes %U\n",
            encoding="utf-8",
        )
        (apps / "mumble-notes-duplicate.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Mumble Notes\n"
            "Exec=mumble-notes-duplicate\n",
            encoding="utf-8",
        )
        (files / "Project notes.md").write_text("agenda", encoding="utf-8")
        (files / "Designs").mkdir()
        return SystemSearchEngine(
            settings={"system_search_include_files": True, **settings},
            data_dir=data,
            platform="linux",
            home=root,
            file_roots=[files],
            app_roots=[apps],
            start_background=False,
            file_provider=_IndexedFileProvider([
                files / "Project notes.md", files / "Designs"
            ]),
        )

    def test_refresh_versions_only_the_application_catalogue(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(tmp)
            status = engine.refresh()
            self.assertEqual(status["counts"], {"app": 1, "file": 0, "folder": 0})
            self.assertEqual(status["total"], 1)
            self.assertTrue((Path(tmp) / "data" / "system_search_index.json").is_file())
            files = engine.search("project notes", "file")
            self.assertEqual(files["results"][0]["source"], "windows-search")

    def test_idle_launcher_is_app_first_and_public_metadata_hides_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(tmp)
            engine.refresh()
            idle = engine.search("", "all")
            self.assertTrue(idle["results"])
            self.assertTrue(all(row["kind"] == "app" for row in idle["results"]))
            result = engine.search("Project notes", "file")["results"][0]
            self.assertEqual(result["meta"], "Markdown document")
            self.assertEqual(result["subtitle"], "Markdown document")
            self.assertNotIn(str(Path(tmp)), str(result))

    def test_icon_bridge_is_bounded_to_indexed_apps(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = SystemSearchEngine(
                settings={}, data_dir=tmp, platform="windows",
                start_background=False,
            )
            app = SearchItem.make("app", "Mumble", r"C:\\Apps\\Mumble.lnk")
            file_item = SearchItem.make("file", "notes.txt", r"C:\\notes.txt")
            engine._items.update({app.id: app, file_item.id: file_item})
            with patch.object(
                engine, "_windows_icon_data",
                return_value="data:image/png;base64,AAAA",
            ) as extract:
                icons = engine.icons([app.id, file_item.id, "unknown"])
            self.assertEqual(icons["icons"], {
                app.id: "data:image/png;base64,AAAA",
            })
            extract.assert_called_once_with(app.target)

    def test_linux_desktop_icons_resolve_from_the_local_theme(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apps = root / "apps"
            icon_dir = root / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps"
            apps.mkdir()
            icon_dir.mkdir(parents=True)
            (apps / "mumble-notes.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=Mumble Notes\n"
                "Exec=mumble-notes\nIcon=mumble-notes\n",
                encoding="utf-8",
            )
            (icon_dir / "mumble-notes.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
                '<circle cx="16" cy="16" r="14" fill="#d4af37"/></svg>',
                encoding="utf-8",
            )
            engine = SystemSearchEngine(
                settings={"system_search_include_files": False},
                data_dir=root / "data", platform="linux", home=root,
                app_roots=[apps], start_background=False,
            )
            engine.refresh()
            app = engine.search("Mumble Notes", "app")["results"][0]
            result = engine.icons([app["id"]])
            self.assertTrue(
                result["icons"][app["id"]].startswith(
                    "data:image/svg+xml;base64,"
                )
            )

    @unittest.skipUnless(
        os.name == "nt" and importlib.util.find_spec("win32com") is not None,
        "Windows shell icon integration requires the locked pywin32 runtime",
    )
    def test_windows_settings_virtual_app_has_its_real_shell_icon(self):
        target = (
            "shell:AppsFolder\\windows.immersivecontrolpanel_"
            "cw5n1h2txyewy!microsoft.windows.immersivecontrolpanel"
        )
        icon = SystemSearchEngine._windows_icon_data(target)
        self.assertTrue(icon.startswith("data:image/png;base64,"))
        self.assertGreater(len(icon), 500)

    def test_fuzzy_categories_prefixes_and_no_web_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(tmp)
            engine.refresh()
            fuzzy = engine.search("mn", "all")
            self.assertEqual(fuzzy["results"][0]["name"], "Mumble Notes")
            files = engine.search("file: project notes", "all")
            self.assertEqual(files["category"], "file")
            self.assertEqual(files["results"][0]["kind"], "file")
            web = engine.search("something not indexed", "all")
            self.assertEqual(web["results"], [])
            self.assertNotIn("web", str(web).casefold())

    def test_favorites_are_local_and_influence_ranking(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(tmp)
            engine.refresh()
            result = engine.search("project", "file")["results"][0]
            toggled = engine.execute(result["id"], "favorite")
            self.assertTrue(toggled["ok"])
            self.assertTrue(toggled["favorite"])
            again = engine.search("project", "file")["results"][0]
            self.assertTrue(again["favorite"])

    def test_actions_only_accept_ids_from_the_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(tmp)
            engine.refresh()
            self.assertFalse(engine.execute("made-up-id", "open")["ok"])
            item = engine.search("Project notes", "file")["results"][0]
            self.assertFalse(engine.execute(item["id"], "delete")["ok"])
            with patch("experimental.system_search.engine.shutil.which", return_value="/usr/bin/xdg-open"), \
                    patch("experimental.system_search.engine.subprocess.Popen") as popen:
                opened = engine.execute(item["id"], "open")
            self.assertTrue(opened["ok"])
            popen.assert_called_once()

    def test_mac_and_unknown_platforms_are_not_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = SystemSearchEngine(
                settings={}, data_dir=tmp, platform="darwin", start_background=False
            )
            self.assertFalse(engine.status()["supported"])
            self.assertFalse(engine.search("notes")["ok"])

    def test_configured_web_provider_is_not_used_by_local_results(self):
        opened = []
        with tempfile.TemporaryDirectory() as tmp:
            engine = SystemSearchEngine(
                settings={"search_engine": "brave"},
                data_dir=tmp,
                platform="windows",
                start_background=False,
                url_opener=lambda url: opened.append(url) or True,
                file_provider=_IndexedFileProvider([]),
            )
            result = engine.search("Mumble privacy", generation=1)
            self.assertTrue(result["ok"])
            self.assertEqual(result["results"], [])
            self.assertEqual(opened, [])

    def test_windows_open_reveal_and_store_app_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            file_path = root / "Project notes.md"
            file_path.write_text("notes", encoding="utf-8")
            engine = SystemSearchEngine(
                settings={}, data_dir=data, platform="windows",
                start_background=False,
            )
            local = SearchItem.make("file", file_path.name, str(file_path))
            store = SearchItem.make(
                "app", "Calculator",
                r"shell:AppsFolder\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
                source="start-apps",
            )
            engine._items.update({local.id: local, store.id: store})
            with patch(
                "experimental.system_search.engine.os.startfile", create=True
            ) as startfile, patch(
                "experimental.system_search.engine.subprocess.Popen"
            ) as popen:
                self.assertTrue(engine.execute(local.id, "open")["ok"])
                startfile.assert_called_once_with(str(file_path))
                self.assertTrue(engine.execute(local.id, "reveal")["ok"])
                popen.assert_called_once_with(
                    ["explorer.exe", "/select,", str(file_path)],
                    creationflags=getattr(__import__("subprocess"),
                                          "CREATE_NO_WINDOW", 0),
                )
                popen.reset_mock()
                self.assertTrue(engine.execute(store.id, "open")["ok"])
                popen.assert_called_once_with(
                    ["explorer.exe", store.target],
                    creationflags=getattr(__import__("subprocess"),
                                          "CREATE_NO_WINDOW", 0),
                )

    def test_windows_native_drag_resolves_only_an_opaque_indexed_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dragged = []
            file_path = root / "Project notes.md"
            file_path.write_text("notes", encoding="utf-8")
            engine = SystemSearchEngine(
                settings={"system_search_include_files": True},
                data_dir=root / "data",
                platform="windows",
                start_background=False,
                file_provider=_IndexedFileProvider([file_path]),
                native_drag_starter=lambda path: dragged.append(path) or {
                    "ok": True,
                    "effect": "copy",
                },
            )

            public = engine.search(
                "Project notes", "file", generation=1, deadline_ms=75,
            )["results"][0]
            self.assertNotIn("target", public)
            self.assertIn("drag", public["actions"])

            started = engine.execute(public["id"], "drag")
            self.assertTrue(started["ok"])
            self.assertEqual(dragged, [str(file_path)])

            rejected = engine.execute(str(file_path), "drag")
            self.assertFalse(rejected["ok"])
            self.assertEqual(dragged, [str(file_path)])

    def test_windows_missing_target_fails_honestly(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = SystemSearchEngine(
                settings={}, data_dir=tmp, platform="windows",
                start_background=False,
            )
            missing = SearchItem.make(
                "file", "Moved document", str(Path(tmp) / "missing.txt")
            )
            engine._items[missing.id] = missing
            result = engine.execute(missing.id, "open")
            self.assertFalse(result["ok"])
            self.assertIn("moved", result["message"].lower())


class SearchUiAndPortTests(unittest.TestCase):
    def setUp(self):
        self.app_root = Path(__file__).resolve().parent

    def test_search_loader_and_reader_meeting_find_controls_are_wired(self):
        html = (self.app_root / "webui" / "index.html").read_text(encoding="utf-8")
        javascript = (self.app_root / "webui" / "app.js").read_text(encoding="utf-8")
        loader = (self.app_root / "webui" / "system-search-loader.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('src="system-search-loader.js"', html)
        self.assertNotIn("computer-control-loader.js", html)
        self.assertIn('id="reader-find-input"', html)
        self.assertIn('id="meetings-search"', html)
        self.assertIn('id="meeting-transcript-search"', html)
        self.assertIn("function readerFindUpdate", javascript)
        self.assertIn("function meetingRenderTranscript", javascript)
        search_ui = (
            self.app_root / "experimental" / "system_search" / "ui.js"
        ).read_text(encoding="utf-8")
        search_css = (
            self.app_root / "experimental" / "system_search" / "ui.css"
        ).read_text(encoding="utf-8")
        popup = (self.app_root / "webui" / "search-popup.html").read_text(
            encoding="utf-8"
        )
        popup_loader = (
            self.app_root / "webui" / "search-popup-loader.js"
        ).read_text(encoding="utf-8")
        self.assertIn("window.openSystemSearch", loader)
        self.assertNotIn("system_search_status", loader)
        self.assertIn("system_search_show", loader)
        self.assertIn('class="search-popup-page enhanced"', popup)
        self.assertIn("system_search_assets", popup_loader)
        self.assertIn('api("system_search_status")', search_ui)
        self.assertIn('"system_search_icons"', search_ui)
        self.assertNotIn('class="ss-type"', search_ui)
        self.assertIn('id="ss-dialog" role="dialog"', search_ui)
        self.assertIn('role="list"', search_ui)
        self.assertNotIn('role="listbox"', search_ui)
        self.assertIn("Find apps & files", search_ui)
        self.assertNotIn("Search the web for", search_ui)
        self.assertIn('src="mumble.png"', search_ui)
        self.assertNotIn("../assets/mumble.png", search_ui)
        self.assertNotIn("ss-app-fallback", search_ui)
        self.assertIn("svg\\+xml", search_ui)
        self.assertIn("Golden-black liquid glass", search_css)
        self.assertIn("Shared Mumble surface hierarchy", search_css)
        self.assertIn(".ss-kind-icon.native-icon", search_css)
        self.assertIn("Ctrl + Alt + F", html)
        self.assertIn("Ctrl + Alt + S", html)
        self.assertIn("Mumble Find", html)
        self.assertIn("Web Search", html)

    def test_failed_web_capture_keeps_the_old_visible_binding(self):
        javascript = (self.app_root / "webui" / "app.js").read_text(
            encoding="utf-8"
        )
        failure_gate = javascript.index("if (!r || r.ok === false)")
        local_update = javascript.index("SET[key] = savedValue", failure_gate)
        self.assertLess(failure_gate, local_update)
        capture_start = javascript.index("async function captureBinding")
        deck_start = javascript.index("// Deck search button")
        capture_source = javascript[capture_start:deck_start]
        self.assertIn("bindingCaptureActive", capture_source)
        self.assertIn("Your previous shortcut was kept", capture_source)

    def test_linux_has_search_and_macos_intentionally_does_not(self):
        linux = self.app_root / "Ports" / "Linux" / "app"
        mac = self.app_root / "Ports" / "macOS" / "app"
        linux_html = (linux / "webui" / "index.html").read_text(encoding="utf-8")
        mac_html = (mac / "webui" / "index.html").read_text(encoding="utf-8")
        self.assertIn('src="system-search-loader.js"', linux_html)
        self.assertNotIn("system-search-loader.js", mac_html)
        self.assertTrue((linux / "experimental" / "system_search" / "engine.py").is_file())
        self.assertFalse((mac / "experimental" / "system_search" / "engine.py").exists())


if __name__ == "__main__":
    unittest.main()
