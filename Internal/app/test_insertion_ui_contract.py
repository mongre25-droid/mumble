#!/usr/bin/env python3
"""User-visible outcome language contract for every Deck insertion entry point."""

import json
from pathlib import Path
import shutil
import subprocess
import unittest


APP_JS = Path(__file__).with_name("webui") / "app.js"


def function_source(source, name):
    start = source.index("function " + name + "(")
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError("unterminated function " + name)


class InsertionUiContractTests(unittest.TestCase):
    def test_async_terminal_completion_is_presented_once_per_operation(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is not installed")
        source = APP_JS.read_text(encoding="utf-8")
        helper = function_source(source, "acceptInsertionResult")
        script = (helper + "\nconsole.log(JSON.stringify([" +
                  "acceptInsertionResult({operation_id:'one',state:'terminal'})," +
                  "acceptInsertionResult({operation_id:'one',state:'terminal'})," +
                  "acceptInsertionResult({operation_id:'two',state:'terminal'})]));")
        completed = subprocess.run(
            [node, "-e", script], check=True, capture_output=True,
            text=True, encoding="utf-8")
        self.assertEqual([True, False, True], json.loads(completed.stdout))

    def test_every_non_confirmed_outcome_is_never_called_inserted(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is not installed")
        source = APP_JS.read_text(encoding="utf-8")
        helper = function_source(source, "insertionNotice")
        cases = [
            {"outcome": "confirmed", "confirmed": True,
             "message": "Inserted."},
            {"outcome": "sent_unconfirmed", "confirmed": False,
             "message": "Sent — check the selected destination."},
            {"outcome": "not_sent", "confirmed": False,
             "message": "Not inserted — saved in Mumble."},
            {"outcome": "uncertain", "confirmed": False,
             "message": "Delivery uncertain — check the selected destination."},
            {"outcome": "saved_only", "confirmed": False,
             "message": "Not inserted — saved in Mumble."},
        ]
        script = ("{}\nconsole.log(JSON.stringify({}.map("
                  "x => insertionNotice(x, 'Inserted item'))));").format(
                      helper, json.dumps(cases, ensure_ascii=False))
        completed = subprocess.run(
            [node, "-e", script], check=True, capture_output=True,
            text=True, encoding="utf-8")
        notices = json.loads(completed.stdout)
        self.assertIn("Inserted", notices[0]["message"])
        for notice in notices[1:]:
            self.assertNotIn("Inserted", notice["message"])

    def test_all_deck_paste_surfaces_use_the_shared_truthful_presenter(self):
        source = APP_JS.read_text(encoding="utf-8")
        self.assertIn("showInsertionResult(r, `Inserted ${action.label", source)
        self.assertIn('showInsertionResult(r, "Inserted clipboard image"', source)
        self.assertIn("showInsertionResult(\n        r,", source)
        self.assertNotIn('r && r.ok ? "Inserted', source)
        self.assertNotIn('toast("Done — result inserted"', source)

    def test_no_ordinary_insertion_surface_calls_a_destination_unsupported(self):
        root = Path(__file__).parent
        source = "\n".join(
            (root / relative).read_text(encoding="utf-8")
            for relative in (
                "mumble.py", "overlay.py", "webui_shell.py", "webui/app.js")
        ).casefold()
        self.assertNotIn("unsupported field", source)
        self.assertNotIn("choose a supported field", source)


if __name__ == "__main__":
    unittest.main()
