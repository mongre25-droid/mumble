#!/usr/bin/env python3
"""Focused regressions for Deck selection, Clipboard, and action truth."""

import os
import shutil
import subprocess
import sys
import tempfile
import types
from collections import deque
from pathlib import Path

# Keep this focused storage/contract test runnable in the repository's bare
# system Python. Clipboard's image APIs are exercised through durable metadata;
# the actual Pillow codec is covered by the runtime/UI suites.
if "pyperclip" not in sys.modules:
    pyperclip_stub = types.ModuleType("pyperclip")
    pyperclip_stub.paste = lambda: ""
    pyperclip_stub.copy = lambda value: None
    sys.modules["pyperclip"] = pyperclip_stub
if "PIL" not in sys.modules:
    pil_stub = types.ModuleType("PIL")
    image_stub = types.ModuleType("PIL.Image")
    image_stub.Image = type("Image", (), {})
    grab_stub = types.ModuleType("PIL.ImageGrab")
    grab_stub.grabclipboard = lambda: None
    pil_stub.Image = image_stub
    pil_stub.ImageGrab = grab_stub
    sys.modules.update({"PIL": pil_stub, "PIL.Image": image_stub,
                        "PIL.ImageGrab": grab_stub})

from clipboard import Clipboard
from history import History


APP = Path(__file__).resolve().parent
FAILED = []


def check(name, condition):
    if condition:
        print(f"  [ok  ] {name}")
    else:
        print(f"  [FAIL] {name}")
        FAILED.append(name)


def test_cross_process_store_truth(root):
    history_path = os.path.join(root, "history.json")
    text_path = os.path.join(root, "history.txt")
    first = History(history_path, text_path, 20)
    check("history add is durable", bool(first.add("one", "text")))
    second = History(history_path, text_path, 20)
    entry = second.recent(1)[0]
    check("second history instance reads entry", entry["text"] == "one")
    check("second history instance deletes entry",
          second.delete_match(entry["stamp"], entry["text"]))
    check("first history instance sees external delete", first.recent(5) == [])

    clipboard_path = os.path.join(root, "clipboard.json")
    image_dir = os.path.join(root, "clip_images")
    clip_a = Clipboard(clipboard_path, 20, image_dir)
    check("clipboard text add is durable", clip_a._add_text("copied text") is True)
    clip_b = Clipboard(clipboard_path, 20, image_dir)
    text_entry = clip_b.recent(1)[0]
    check("second clipboard instance reads entry", text_entry["text"] == "copied text")
    check("second clipboard instance deletes entry",
          clip_b.delete_match(text_entry["stamp"], text_entry["text"]))
    check("first clipboard instance sees external delete", clip_a.recent(5) == [])


def test_image_identity_and_resize(root):
    path = os.path.join(root, "images.json")
    image_dir = os.path.join(root, "images")
    clip = Clipboard(path, 5, image_dir)
    os.makedirs(image_dir, exist_ok=True)
    red_hash = "a" * 64
    blue_hash = "b" * 64
    red_path = os.path.join(image_dir, red_hash + ".png")
    blue_path = os.path.join(image_dir, blue_hash + ".png")
    Path(red_path).write_bytes(b"red")
    Path(blue_path).write_bytes(b"blue")
    clip.items = deque([
        {"type": "image", "time": "10:00", "stamp": "2026-07-17 10:00:00",
         "path": red_path, "hash": red_hash, "size": "3x3"},
        {"type": "image", "time": "10:00", "stamp": "2026-07-17 10:00:00",
         "path": blue_path, "hash": blue_hash, "size": "3x3"},
    ], maxlen=5)
    check("same-second image metadata is durable", clip._save())
    check("same-second images coexist", len(clip.recent(5)) == 2)
    check("image hash deletes exact image",
          clip.delete_match("2026-07-17 10:00:00", None, red_hash))
    left = clip.recent(5)
    check("other same-second image remains",
          len(left) == 1 and left[0].get("hash") == blue_hash)
    check("clipboard resize applies durably", clip.resize(1) and clip.maxlen == 1)

    hist = History(os.path.join(root, "resize-history.json"),
                   os.path.join(root, "resize-history.txt"), 5)
    for word in ("a", "b", "c"):
        hist.add(word)
    check("history resize applies durably", hist.resize(2))
    check("history resize keeps newest", [e["text"] for e in hist.recent(5)] == ["c", "b"])


def test_web_contracts():
    html = (APP / "webui" / "index.html").read_text(encoding="utf-8")
    js = (APP / "webui" / "app.js").read_text(encoding="utf-8")
    css = (APP / "webui" / "app.css").read_text(encoding="utf-8")
    controller = (APP / "mumble.py").read_text(encoding="utf-8")
    shell = (APP / "webui_shell.py").read_text(encoding="utf-8")

    check("Clipboard is a first-class content tab",
          'role="tab"' in html and 'data-content-filter="clipboard"' in html)
    check("selection uses a native checkbox", 'type="checkbox" data-selcheck' in js)
    check("whole rows toggle ordered selection",
          'row.addEventListener("click"' in js and "toggleRowSelection(row)" in js)
    check("selection resolves source-aware identities",
          "HX.itemByKey.get(key)" in js and "HX.selected[0].text" not in js)
    check("image paste has an explicit action", "data-pasteimage" in js)
    check("selection bar is viewport-centred",
          ".hist-actionbar" in css and "position: fixed" in css
          and "transform: translateX(-50%)" in css)
    capture = controller[controller.index("def _capture_focused_copy"):
                         controller.index("def capture_conversation", controller.index("def _capture_focused_copy"))]
    check("selection capture no longer writes a NUL sentinel", "\\x00" not in capture)
    check("Deck job acknowledgement reserves first",
          "accepted = self._reserve_deck_job()" in controller)
    paste_branch = controller[controller.index('elif cmd == "paste":'):
                              controller.index('elif cmd == "reload":')]
    check("paste command returns the actual result",
          "pasted =" in paste_branch and "threading.Thread" not in paste_branch)
    bridge_paste = shell[shell.index("    def deck_paste(self, text):"):
                         shell.index("    def set_app_focused", shell.index("    def deck_paste(self, text):"))]
    check("bridge waits for and propagates paste truth",
          "timeout=4.0" in bridge_paste and "r.get(\"ok\")" in bridge_paste)
    check("bridge restores Deck on paste failure",
          bridge_paste.count("_restore_after_failed_paste()") >= 2)

    node = shutil.which("node")
    if not node:
        check("Node is available for Deck state behavior", False)
        return

    def function_source(name):
        start = js.index("function " + name + "(")
        brace = js.index("{", start)
        depth = 0
        quote = None
        escape = False
        for at in range(brace, len(js)):
            char = js[at]
            if quote:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == quote:
                    quote = None
                continue
            if char in ("'", '"', "`"):
                quote = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return js[start:at + 1]
        raise AssertionError("unterminated JS function " + name)

    behavior = "\n".join(function_source(name) for name in (
        "selKeyOf", "deckStampValue", "sortItems", "selectedItems"))
    probe = behavior + r'''
let HX = {sort: "new", selected: [], itemByKey: new Map()};
const transcript = selKeyOf("transcript", "2026-07-17 10:00:00", "same");
const clipboard = selKeyOf("clipboard", "2026-07-17 10:00:00", "same");
if (transcript === clipboard) throw new Error("cross-source identities collided");
HX.itemByKey.set(transcript, {kind: "TEXT", text: "same"});
HX.itemByKey.set(clipboard, {kind: "CLIP", text: "same"});
HX.selected = [clipboard, transcript];
if (selectedItems().map(x => x.kind).join(",") !== "CLIP,TEXT")
  throw new Error("ordered selection payload drifted");
const ordered = sortItems([
  {_stamp: "2026-07-17 09:00:00", _order: 0},
  {_stamp: "2026-07-17 11:00:00", _order: 1},
]);
if (ordered[0]._stamp.indexOf("11:00:00") < 0)
  throw new Error("unified timeline is not globally newest-first");
console.log("DECK_JS_BEHAVIOR_OK");
'''
    result = subprocess.run([node, "-e", probe], capture_output=True,
                            text=True, timeout=20)
    check("source-aware ordered selection behaves in JavaScript",
          result.returncode == 0 and "DECK_JS_BEHAVIOR_OK" in result.stdout)


def main():
    with tempfile.TemporaryDirectory(prefix="mumble_deck_test_") as root:
        test_cross_process_store_truth(root)
        test_image_identity_and_resize(root)
    test_web_contracts()
    if FAILED:
        print(f"\nDECK_CLIPBOARD_WORKFLOW_FAIL: {', '.join(FAILED)}")
        return 1
    print("\nDECK_CLIPBOARD_WORKFLOW_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
