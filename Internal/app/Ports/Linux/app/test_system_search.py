"""Regression tests for the bounded Windows/Linux Mumble Find engine."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experimental.system_search import SystemSearchEngine


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
        )

    def test_refresh_indexes_apps_files_and_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(tmp)
            status = engine.refresh()
            self.assertEqual(status["counts"], {"app": 1, "file": 1, "folder": 1})
            self.assertEqual(status["total"], 3)
            self.assertTrue((Path(tmp) / "data" / "system_search_index.json").is_file())

    def test_fuzzy_categories_prefixes_and_web_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(tmp)
            engine.refresh()
            fuzzy = engine.search("mn", "all")
            self.assertEqual(fuzzy["results"][0]["name"], "Mumble Notes")
            files = engine.search("file: project notes", "all")
            self.assertEqual(files["category"], "file")
            self.assertEqual(files["results"][0]["kind"], "file")
            web = engine.search("something not indexed", "all")
            self.assertEqual(web["results"][-1]["kind"], "web")

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


if __name__ == "__main__":
    unittest.main()
