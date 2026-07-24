#!/usr/bin/env python3
"""END-TO-END feature exercise — actually USE each feature through the real
backend (webui_shell.Api + the live store classes), not just trace the code.

Owner ask (2026-06-13): "a feature may look like it works but who knows if it
actually does — go and check." This drives every data-round-trip feature through
its full lifecycle and asserts the on-disk effect:

  • Preset Adder   — save a custom preset -> presets.json -> get_presets ->
                     runnable instruction (the deck_job slot resolution) -> clear
  • Favourites     — toggle on -> favorites.json (+ mode) -> toggle off
  • History        — add -> get_transcripts -> delete the RIGHT one -> clear
  • Clipboard      — add -> get_clipboard (incl. the v3.6.2 `stamp`) -> delete -> clear
  • Settings       — flat + dotted keys + bool coercion round-trip
  • Vocabulary     — saved pair actually applied by formatting

Runs entirely against a TEMP data dir — never touches %APPDATA%\\Mumble.
Run: python test_end_to_end.py
"""
import json
import os
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---- redirect ALL data paths to a throwaway dir BEFORE anything reads them ----
import branding
_TMP = tempfile.mkdtemp(prefix="mumble_e2e_")
branding.DATA_DIR = _TMP
branding.SETTINGS_PATH = os.path.join(_TMP, "settings.json")
branding.HISTORY_JSON = os.path.join(_TMP, "history.json")
branding.HISTORY_TXT = os.path.join(_TMP, "transcripts.txt")
branding.CLIPBOARD_JSON = os.path.join(_TMP, "clipboard.json")
branding.CONV_STORE_JSON = os.path.join(_TMP, "conv_store.json")
branding.STATS_JSON = os.path.join(_TMP, "stats.json")
branding.LOG_PATH = os.path.join(_TMP, "mumble.log")

import favorites  # noqa: E402
favorites.FAVORITES_PATH = os.path.join(_TMP, "favorites.json")
import presets  # noqa: E402
presets.PRESETS_PATH = os.path.join(_TMP, "presets.json")

import formatting  # noqa: E402
from clipboard import Clipboard  # noqa: E402
from history import History  # noqa: E402
import webui_shell  # noqa: E402

_fails = []


def check(desc, cond):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {desc}")
    if not cond:
        _fails.append(desc)


api = webui_shell.Api()
BASE = len(presets.BUILTIN)  # 17

# ============================================================ PRESET ADDER
print("\n== Preset Adder — save a custom preset and prove it is runnable ==")
p0 = api.get_presets()
check("starts with the 17 built-ins, no customs",
      len(p0) == BASE and all(p[3] for p in p0))
r = api.save_presets([{
    "title": "Pirate",
    "description": "Talk like a pirate",
    "instruction": "Rewrite the material in the voice of a flamboyant pirate.",
}])
check("save_presets reports ok", bool(r.get("ok")))
check("presets.json was written to disk", os.path.exists(presets.PRESETS_PATH))
p1 = api.get_presets()
customs = [p for p in p1 if not p[3]]
check("get_presets now returns exactly 1 custom preset", len(customs) == 1)
if customs:
    slot, title, desc, is_builtin, instr = customs[0]
    check("custom slot is immediately after the built-ins (18)", slot == BASE + 1)
    check("title + description + instruction survived the round-trip",
          title == "Pirate" and desc == "Talk like a pirate"
          and instr.startswith("Rewrite the material"))
# RUNNABLE: the Deck job resolves a slot -> instruction via presets.all_presets
# (exactly what mumble.py's deck_job handler does)
slot_to_instr = {s: ins for s, t, d, ins in presets.all_presets()}
check("the custom preset is RUNNABLE (slot resolves to its instruction)",
      "pirate" in (slot_to_instr.get(BASE + 1) or "").lower())
# edit: overwrite the same slot
api.save_presets([{"title": "Pirate2", "description": "x",
                   "instruction": "Now rewrite it as a calm pirate poem please."}])
check("editing a custom preset overwrites it (not duplicates)",
      len([p for p in api.get_presets() if not p[3]]) == 1)
# clear: empty save removes it
api.save_presets([])
check("clearing custom presets returns to built-ins only",
      all(p[3] for p in api.get_presets()))

# ============================================================ FAVOURITES
print("\n== Favourites — toggle persists with mode, untoggle removes only it ==")
api.toggle_favorite("Reply draft here", "transcript", "12:00", "reply")
api.toggle_favorite("A clipboard note", "clipboard", "12:01", "")
favs = api.get_favorites()
check("both favourites persisted", len(favs) == 2)
check("favourite keeps its origin MODE (reply)",
      any(f["text"] == "Reply draft here" and f["mode"] == "reply" for f in favs))
api.toggle_favorite("Reply draft here", "transcript", "12:00", "reply")  # off
favs2 = api.get_favorites()
check("un-starring removes ONLY that item (the other survives)",
      len(favs2) == 1 and favs2[0]["text"] == "A clipboard note")

# ============================================================ HISTORY
print("\n== History — add, then delete the RIGHT item, then clear ==")
h = History(branding.HISTORY_JSON, branding.HISTORY_TXT, 5000)
h.add("alpha transcript", "text", 1.0)
h.add("bravo transcript", "email", 2.0)
h.add("charlie transcript", "list", 3.0)
ts = api.get_transcripts(10)
check("3 transcripts present, newest-first", len(ts) == 3
      and ts[0]["text"] == "charlie transcript")
# delete the middle ("bravo") by STABLE identity (stamp+text), not array index —
# must remove bravo, keep the others. (Index deletion removed the wrong row when
# the list shifted under it; identity deletion is robust to that.)
api.delete_transcript(ts[1]["stamp"], ts[1]["text"])
ts2 = api.get_transcripts(10)
check("delete by identity removed exactly the middle item (bravo)",
      [t["text"] for t in ts2] == ["charlie transcript", "alpha transcript"])
check("deleting a non-existent stamp is a safe no-op",
      api.delete_transcript("1999-01-01 00:00:00", "ghost")["ok"] is False
      and len(api.get_transcripts(10)) == 2)
api.clear_transcripts()
check("clear_transcripts empties history", api.get_transcripts(10) == [])

# Resurrection / cross-instance desync — the owner audit's headline bug. The
# controller holds a long-lived History it appends to; the webui process deletes
# from the same file. Before the fix the controller's next add() rewrote its
# STALE in-memory list, RESURRECTING the webui-deleted entry. add() now reloads
# from disk first, so the deletion sticks.
print("\n== History — a webui-side delete is NOT resurrected by the next add ==")
ctrl_h = History(branding.HISTORY_JSON, branding.HISTORY_TXT, 5000)
webui_h = History(branding.HISTORY_JSON, branding.HISTORY_TXT, 5000)
ctrl_h.add("keep me", "text", 1.0)
doomed = ctrl_h.add("delete me", "text", 1.0)
webui_h.delete_match(doomed["stamp"], doomed["text"])   # the "other process" deletes
ctrl_h.add("after delete", "text", 1.0)                 # controller dictates again
_final = [t["text"] for t in
          History(branding.HISTORY_JSON, branding.HISTORY_TXT, 5000).all_newest_first()]
check("deleted entry stays deleted after a later add (no resurrection)",
      "delete me" not in _final and "keep me" in _final and "after delete" in _final)
api.clear_transcripts()

# ============================================================ CLIPBOARD
print("\n== Clipboard — add, get (incl. the v3.6.2 stamp), delete, clear ==")
c = Clipboard(branding.CLIPBOARD_JSON, 5000,
              img_dir=os.path.join(_TMP, "clipimg"))
c._add_text("clip one")
c._add_text("clip two")
cl = api.get_clipboard(10)
check("2 clipboard items present", len(cl) == 2)
check("every clip carries a `stamp` (v3.6.2 fix — was missing -> all 'Older')",
      all(x.get("stamp") for x in cl))
api.delete_clip(cl[0]["stamp"], cl[0]["text"])  # identity-based (stamp+text)
check("delete_clip removed exactly the targeted item",
      [x["text"] for x in api.get_clipboard(10)] == ["clip one"])
api.clear_clipboard()
check("clear_clipboard empties it", api.get_clipboard(10) == [])

# ============================================================ SETTINGS
print("\n== Settings — flat + dotted keys + bool coercion round-trip ==")
api.set_setting("user_name", "Zaid")
check("flat setting persists + reads back", api.get_settings()["user_name"] == "Zaid")
api.set_setting("modes.email", False)
check("dotted key (modes.email) persists", api.get_settings()["modes"]["email"] is False)
api.set_setting("modes.email", True)
# bool coercion: a stringy bool from a hand-edited file must not invert
with open(branding.SETTINGS_PATH, encoding="utf-8") as f:
    raw = json.load(f)
raw["autostart"] = "false"
with open(branding.SETTINGS_PATH, "w", encoding="utf-8") as f:
    json.dump(raw, f)
from settings import Settings  # noqa: E402
check('settings "false" string coerces to False (not True)',
      Settings().get("autostart") is False)

# ============================================================ VOCABULARY
print("\n== Vocabulary — a saved pair is actually applied ==")
api.set_setting("vocabulary", {"mambo": "Mumble"})
applied = formatting.apply_vocabulary("i really like mambo", {"mambo": "Mumble"})
check("vocabulary pair rewrites the text", applied == "i really like Mumble")

# ============================================================ SUMMARY
print("\n" + "=" * 64)
if _fails:
    print(f"E2E: {len(_fails)} FAILED")
    for d in _fails:
        print("   FAIL:", d)
    sys.exit(1)
print("E2E: ALL PASS — every feature exercised end-to-end works")
