#!/usr/bin/env python3
"""Smoke test for Mumble's window: builds every section with a stub controller,
visits each, exercises the island, and tears down. Prints UI_OK if nothing throws."""

import os
import sys
import time
import tkinter as tk

# tests must never launch the real HTML island overlay window
os.environ["MUMBLE_DISABLE_WEB_ISLAND"] = "1"

import branding
import ui
from app_window import AppWindow
from history import History
from overlay import Island
from settings import Settings


class StubController:
    def __init__(self):
        from stats import Stats  # imported here to keep test_ui self-contained

        self.settings = Settings()
        self.model = None  # AppWindow reads ctrl.model for the model-status label
        self.history = History(
            branding.HISTORY_JSON + ".uitest", branding.HISTORY_TXT + ".uitest", 100
        )
        self.history.add("A sample text dictation to preview the history.", "text", 4.0)
        self.history.add(
            "You are an expert writer.\n\nTask: Write a poem.", "prompt", 3.0
        )
        self.history.add(
            "Hi Alex,\n\nWe ship Friday.\n\nBest regards,\nSam", "email", 5.0
        )
        self.stat_store = Stats(branding.STATS_JSON + ".uitest")
        # Seed the stat store from history so the smoke test has real numbers
        items = list(self.history.items)
        self.stat_store.seed(items)
        self._clip = [
            {"time": "20:31", "text": "https://example.com/link"},
            {"time": "20:30", "text": "Some copied text"},
        ]

    def status(self):
        return ("idle", "Ready")

    def autostart_enabled(self):
        return True

    def list_microphones(self):
        return [(None, "System default"), (1, "Sample Mic")]

    def apply_hotkey(self, hk):
        return (True, f"Saved — {hk}")

    def apply_quick_paste_hotkey(self, hk):
        return (True, f"Saved — {hk}")

    def apply_search_hotkey(self, hk):
        return (True, f"Saved — {hk}")

    def apply_web_search_hotkey(self, hk):
        return (True, f"Saved — {hk}")

    def test_microphone(self, onl, ond):
        onl(0.5)
        ond()

    def stats(self):
        s = self.stat_store.summary()
        cur_streak, best_streak = self.stat_store.streak()
        s["current_streak"] = cur_streak
        s["best_streak"] = best_streak
        return s

    def mode_stats(self):
        return self.stat_store.mode_stats()

    def daily_stats(self, days=7):
        return self.stat_store.daily_stats(days)

    def streak(self):
        return self.stat_store.streak()

    def clipboard_recent(self, n=25):
        return self._clip

    def clear_clipboard(self):
        self._clip = []

    def delete_clipboard(self, i):
        self._clip = [c for j, c in enumerate(self._clip) if j != i]

    def delete_transcript(self, i):
        pass

    def set_mode_enabled(self, *a):
        pass

    def set_mode_button_enabled(self, *a):
        pass

    def apply_mode_key(self, k):
        return (True, f"Saved — hold {k}.")

    def set_ai_second_opinion(self, *a):
        pass

    def set_prompt_pref(self, *a):
        pass

    def set_polish_aggressiveness(self, *a):
        pass

    def set_format_enabled(self, *a):
        pass

    def set_user_name(self, *a):
        pass

    def set_vocabulary(self, *a):
        pass

    def set_language(self, *a):
        pass

    def set_model(self, *a):
        pass

    def set_mic(self, *a):
        pass

    def set_autostart(self, *a):
        pass

    def open_transcripts(self):
        pass

    def open_data_folder(self):
        pass

    def switch_to_lite(self):
        pass

    def prompts_history(self):
        return [{"time": "2026-06-10 12:00",
                 "request": "write a poem prompt",
                 "prompt": "You are a poet. Write..."}]

    def clear_prompts(self):
        pass

    def copy_text(self, t):
        pass

    def copy_image(self, p):
        pass

    def refresh_tray_menu(self):
        pass

    def pro_status(self):
        return {
            "enabled": True,
            "has_key": True,
            "using_own_key": False,
            "key_failed": False,
        }

    def set_pro_mode(self, *a):
        pass

    def set_llm_provider(self, *a, **kw):
        return (True, "Saved.")

    def test_provider(self):
        return (True, "Connected.")

    def set_prompt_provider(self, *a, **kw):
        return (True, "Saved.")

    def set_cerebras_key(self, *a):
        pass

    def test_cerebras(self):
        return (True, "Connected — Cerebras is ready.")


def main():
    root = tk.Tk()
    root.withdraw()
    ui.apply_theme(root)
    island = Island(root)
    win = AppWindow(root, StubController())
    win.show()
    for section in ["home", "history", "stats", "settings"]:
        win.show_section(section)
        for _ in range(5):
            root.update()
            time.sleep(0.03)
    for st, lv in [("listening", 0.6), ("transcribing", 0.0)]:
        island.set_state(st)
        island.set_level(lv)
        for _ in range(4):
            root.update()
            time.sleep(0.03)
    for mode, off in [("prompt", False), ("list", True)]:
        island.set_building(mode, offline=off)
        for _ in range(4):
            root.update()
            time.sleep(0.03)
    island.flash("email")
    for _ in range(8):
        root.update()
        time.sleep(0.03)
    island.set_building("context_stream")  # context+mode rapid colour stream
    for _ in range(12):
        root.update()
        time.sleep(0.03)
    island.flash("context_stream")  # stream flash on done
    for _ in range(8):
        root.update()
        time.sleep(0.03)
    island.hint("Ctrl + Alt + D")  # paste-reminder state
    for _ in range(8):
        root.update()
        time.sleep(0.03)
    island.set_armed(True)  # mode-key-held ring
    island.set_state("listening")
    for _ in range(4):
        root.update()
        time.sleep(0.03)
    island.set_armed(False)
    island.suggest("Prompt? hold key + say")  # wrong-mode suggestion chip
    for _ in range(6):
        root.update()
        time.sleep(0.03)
    # ===== bugfix-island-freeze: wake() must paint even when unfocused =====
    island.set_state("listening")
    island.focused = False  # simulate unfocused Mumble window
    island.wake()            # this must paint despite unfocused
    assert island.visible or island.glass.visible, \
        "island must appear on wake() even when unfocused (freeze bug)"
    # _tick must run for active states even when unfocused
    island._tick()
    assert island.frame > 0, \
        "_tick must advance frame counter for active state even when unfocused"
    # suspended self-healing must fire even when unfocused
    island.suspended = True
    island._suspended_since = time.time() - 31.0  # force timeout
    island._tick()
    assert not island.suspended, \
        "suspended must self-heal even when unfocused (tick bail bug)"
    island.set_state("idle")
    island._hide_all()  # reset visibility
    island.focused = True
    # ===== The Hub (Ctrl+Alt+D): paste picker + favourites + presets + modes.
    def pump(n=6):
        for _ in range(n):
            root.update()
            time.sleep(0.03)

    hub_result = {}
    fav_calls = []
    hub_items = [
        {"source": "transcript", "mode": "text",
         "time": "2026-06-10 12:03:00", "text": "newest transcript words"},
        {"source": "clipboard", "time": "2026-06-10 12:02:00",
         "text": "some copied article text", "own": False},
        {"source": "prompt", "time": "2026-06-10 12:01:30",
         "text": "You are a poet. Write…"},
        {"source": "clipboard", "time": "2026-06-10 12:01:00",
         "text": "", "image_path": "fake.png", "size": "64×64"},
        {"source": "clipboard", "time": "2026-06-10 11:00:00",
         "text": "an old starred gem", "fav": True},
    ]

    def open_hub():
        hub_result.clear()
        island.show_deck([dict(it) for it in hub_items],
                        on_complete=lambda r: hub_result.update(r=r),
                        on_fav=lambda it, st: fav_calls.append((it["text"], st)))
        pump()
        return island._deck_api

    # --- quick-paste flow: search narrows, Enter pastes the match ---
    api = open_hub()
    assert island.deck_open and api, "hub not open"
    assert len(api["state"]["visible"]) == 5, "hub should show all 5 items"
    assert api["state"]["visible"][0]["fav"], "favourites must sort to the top"
    api["query"].set("article")
    pump(4)
    assert len(api["state"]["visible"]) == 1, "filter should leave 1 match"
    api["confirm"]()  # Enter = paste (no preset/mode picked)
    pump(4)
    r = hub_result.get("r") or {}
    assert r.get("action") == "paste" and \
        r.get("item", {}).get("text") == "some copied article text", \
        "hub paste returned the wrong item"
    assert not island.deck_open, "hub did not close after paste"

    # --- prompts appear in the unified list ---
    api = open_hub()
    api["query"].set("poet")
    pump(4)
    assert len(api["state"]["visible"]) == 1 and \
        api["state"]["visible"][0]["source"] == "prompt", \
        "saved prompts must be searchable in the hub"

    # --- job flow: check items + pick a preset (+ mode) → Go returns the job ---
    api["query"].set("")
    pump(4)
    import presets as _presets
    first_slot = 1  # slot 1 = the first built-in preset
    api["pick_intent"](first_slot)
    pump(2)
    assert api["intent"]["instruction"], "preset selection did not register"
    api["pick_mode"]("email")
    pump(2)
    assert api["mode_sel"]["key"] == "email", "mode chip did not register"
    api["state"]["sel"] = 0
    api["toggle_check"]()      # check the favourite row
    api["state"]["sel"] = 1
    api["toggle_check"]()      # check the newest transcript too
    api["go"]()
    pump(4)
    r = hub_result.get("r") or {}
    assert r.get("action") == "job", "Go must return a job"
    assert len(r.get("items", [])) == 2, "job must carry the 2 checked items"
    assert r.get("mode") == "email", "job must carry the picked mode"
    assert r.get("intent") == _presets.BUILTIN[0][2], \
        "job must carry the preset's full instruction"
    assert not island.deck_open, "hub did not close after Go"

    # --- keyboard navigation + Esc cancels with None ---
    api = open_hub()
    api["move"](+1)
    assert api["state"]["sel"] == 1, "Down should move the selection"
    api["move"](-1)
    assert api["state"]["sel"] == 0, "Up should move it back"
    api["finish"](None)  # what Esc / ✕ trigger
    pump(4)
    assert hub_result.get("r", "missing") is None, "Esc must report None"
    assert not island.deck_open, "hub did not close on cancel"

    # --- Go with nothing pickable warns instead of returning a job ---
    api = open_hub()
    api["go"]()  # no preset/mode -> must NOT close or return a job
    pump(2)
    assert island.deck_open, "Go without a preset/mode must keep the hub open"
    api["finish"](None)
    pump(2)
    win.hide()
    for _ in range(3):
        root.update()
    root.destroy()
    # Clean up test artifacts
    import os
    for suffix in (".uitest", ".uitest.bak"):
        for base in (branding.HISTORY_JSON, branding.HISTORY_TXT, branding.STATS_JSON):
            p = base + suffix
            try:
                os.remove(p)
            except OSError:
                pass
    print("UI_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
