#!/usr/bin/env python3
"""Security regressions for the deterministic Linux release builder."""

from pathlib import Path
import tempfile
import unittest

import build_release


def _symlink_or_skip(test, target, link, target_is_directory=False):
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (NotImplementedError, OSError) as exc:
        test.skipTest(f"symlinks unavailable in this environment: {exc}")


class ReleaseBuilderSecurityTests(unittest.TestCase):
    def test_runtime_closure_contains_live_webui_and_prompt_dependencies(self):
        names = {name for name, _data, _mode in build_release._runtime_files()}
        required = {
            "app/prompt_history.py",
            "app/prompt_template_registry.py",
            "app/webui/remaster.css",
            "app/webui/system-search-loader.js",
            "app/experimental/system_search/engine.py",
            "app/experimental/system_search/ui.css",
            "app/experimental/system_search/ui.js",
        }
        self.assertTrue(required.issubset(names))
        self.assertNotIn("app/prompt_memory.py", names)

    def test_checked_source_accepts_regular_and_rejects_escape(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "root"
            outside = Path(raw) / "outside"
            root.mkdir()
            outside.mkdir()
            regular = root / "regular.txt"
            regular.write_text("ok", encoding="utf-8")
            self.assertEqual(
                build_release._checked_source(regular, root), regular)
            escaped_file = outside / "escaped.txt"
            escaped_file.write_text("outside", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                build_release._checked_source(escaped_file, root)

    def test_checked_source_rejects_leaf_and_parent_symlinks(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "root"
            outside = Path(raw) / "outside"
            root.mkdir()
            outside.mkdir()
            regular = root / "regular.txt"
            regular.write_text("ok", encoding="utf-8")
            leaf = root / "leaf.txt"
            _symlink_or_skip(self, regular, leaf)
            with self.assertRaises(RuntimeError):
                build_release._checked_source(leaf, root)

            escaped_file = outside / "escaped.txt"
            escaped_file.write_text("outside", encoding="utf-8")
            linked_parent = root / "linked-parent"
            _symlink_or_skip(
                self, outside, linked_parent, target_is_directory=True)
            with self.assertRaises(RuntimeError):
                build_release._checked_source(
                    linked_parent / escaped_file.name, root)

    def test_predictable_tmp_names_are_never_reused(self):
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "dist"
            output.mkdir()
            version = build_release._version()
            names = (
                f"Mumble-Linux-{version}.tar.gz",
                f"Mumble-Linux-{version}.zip",
                "SHA256SUMS",
            )
            traps = []
            for name in names:
                trap = output / f"{name}.tmp"
                trap.write_text("do not overwrite", encoding="utf-8")
                traps.append(trap)

            built = build_release.build(output)
            self.assertEqual({path.name for path in built}, set(names))
            for trap in traps:
                self.assertEqual(
                    trap.read_text(encoding="utf-8"), "do not overwrite")
            self.assertFalse([
                path for path in output.glob(".*.tmp") if path.is_file()
            ])

    def test_predictable_tmp_symlink_cannot_capture_output(self):
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "dist"
            output.mkdir()
            destination = output / "artifact.tar.gz"
            victim = Path(raw) / "victim.txt"
            victim.write_text("do not overwrite", encoding="utf-8")
            trap = output / "artifact.tar.gz.tmp"
            _symlink_or_skip(self, victim, trap)
            build_release._tar_bytes(
                "Root", [("file.txt", b"payload", 0o644)],
                build_release.ZIP_EPOCH, destination)
            self.assertEqual(
                victim.read_text(encoding="utf-8"), "do not overwrite")
            self.assertTrue(trap.is_symlink())
            self.assertTrue(destination.is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
