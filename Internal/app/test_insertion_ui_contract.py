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
    def test_every_non_confirmed_outcome_is_never_called_pasted(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is not installed")
        source = APP_JS.read_text(encoding="utf-8")
        helper = function_source(source, "insertionNotice")
        cases = [
            {"outcome": "confirmed", "confirmed": True, "message": "Pasted."},
            {"outcome": "sent_unconfirmed", "confirmed": False,
             "message": "Sent—check the field."},
            {"outcome": "not_sent", "confirmed": False,
             "message": "Not sent—use Paste latest."},
            {"outcome": "uncertain", "confirmed": False,
             "message": "Paste not confirmed—check the field."},
            {"outcome": "saved_only", "confirmed": False,
             "message": "Saved in Deck and History."},
        ]
        script = "{}\nconsole.log(JSON.stringify({}.map(x => insertionNotice(x, 'Pasted item'))));".format(
            helper, json.dumps(cases, ensure_ascii=False))
        completed = subprocess.run(
            [node, "-e", script], check=True, capture_output=True,
            text=True, encoding="utf-8")
        notices = json.loads(completed.stdout)
        self.assertIn("Pasted", notices[0]["message"])
        for notice in notices[1:]:
            self.assertNotIn("Pasted", notice["message"])

    def test_all_deck_paste_surfaces_use_the_shared_truthful_presenter(self):
        source = APP_JS.read_text(encoding="utf-8")
        self.assertIn("showInsertionResult(r, `Pasted ${action.label", source)
        self.assertIn('showInsertionResult(r, "Pasted clipboard image"', source)
        self.assertIn("showInsertionResult(\n        r,", source)
        self.assertNotIn('r && r.ok ? "Pasted', source)
        self.assertNotIn('toast("Done — result pasted"', source)


if __name__ == "__main__":
    unittest.main()
