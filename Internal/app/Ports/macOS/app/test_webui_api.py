#!/usr/bin/env python3
"""Smoke test for the web UI's Python bridge (webui_shell.Api).

SAFE BY DESIGN: every run owns throwaway storage and forces offline behavior
before importing the application bridge. It never reads or mutates live stores.

Run:  .venv\\Scripts\\python.exe test_webui_api.py   → prints WEBUI_API_OK
"""

import atexit
import os
import sys
import tempfile
import threading

_OWNED_TEST_DATA = tempfile.TemporaryDirectory(prefix="mumble_macos_webui_api_")
atexit.register(_OWNED_TEST_DATA.cleanup)
os.environ["MUMBLE_TEST_DATA_DIR"] = _OWNED_TEST_DATA.name
os.environ["MUMBLE_OFFLINE_TESTS"] = "1"

import webui_shell

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  [ok  ] {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        FAILED.append(name)


def main():
    api = webui_shell.Api()

    # ---- overview ----------------------------------------------------------
    o = api.get_overview()
    for key in ("version", "tagline", "total_words", "total_transcripts",
                "current_streak", "best_streak", "provider", "model",
                "pro_mode", "first_run", "key_configured", "time_saved",
                "today_words"):
        check(f"overview has {key}", key in o)
    check("overview exposes effective transcription route",
          isinstance(o.get("transcription_route"), dict)
          and o.get("effective_transcription_mode")
          == o["transcription_route"].get("effective"))
    check("overview words is int", isinstance(o["total_words"], int))

    # ---- hotkeys -----------------------------------------------------------
    hk = api.get_hotkeys()
    for key in ("hotkey", "quick_paste_hotkey", "web_search_hotkey"):
        check(f"hotkeys has {key}", bool(hk.get(key)))
    check("retired mode key is not exposed", "mode_key" not in hk)

    # ---- stores (read) -----------------------------------------------------
    ts = api.get_transcripts(5)
    check("transcripts list", isinstance(ts, list))
    if ts:
        check("transcript shape", all(k in ts[0] for k in
                                      ("time", "mode", "text", "words", "fav")))
    cb = api.get_clipboard(5)
    check("clipboard list", isinstance(cb, list))
    pr = api.get_prompts()
    check("prompts list", isinstance(pr, list))
    fv = api.get_favorites()
    check("favorites list", isinstance(fv, list))

    # ---- stats -------------------------------------------------------------
    daily = api.get_daily_stats(7)
    check("daily stats 7 rows", len(daily) == 7)
    check("daily stat shape", all(k in daily[0] for k in
                                  ("day", "words", "transcripts")))
    modes = api.get_mode_stats()
    check("mode stats list", isinstance(modes, list))
    ins = api.get_insights()
    for key in ("active_days", "avg_per_active_day", "busiest_day",
                "weekday_words", "weekday_names", "peak_weekday",
                "this_week", "prev_week", "trend_pct", "avg_wpm",
                "best_wpm", "spoken_minutes", "today_words"):
        check(f"insights has {key}", key in ins)
    check("insights weekday 7 buckets", len(ins["weekday_words"]) == 7)
    check("insights derives only real data",
          ins["this_week"] == sum(d["words"] for d in api.get_daily_stats(14)[-7:]))

    # ---- presets ------------------------------------------------------------
    ps = api.get_presets()
    check("17+ presets", len(ps) >= 17)
    check("preset shape [slot,title,desc,builtin,instr]",
          len(ps[0]) == 5 and isinstance(ps[0][3], bool))
    check("Chat context present", any(p[1] == "Chat context" for p in ps))

    # ---- settings: read + write-then-restore -------------------------------
    st = api.get_settings()
    for key in ("user_name", "hotkey", "llm_provider", "modes",
                "prompt_prefs", "ui_effects", "history_max"):
        check(f"settings has {key}", key in st)
    orig = api.settings.get("ui_effects", "standard")
    r = api.set_setting("ui_effects", "lite")
    check("set_setting ok", r.get("ok") is True)
    check("set_setting persisted", api.settings.get("ui_effects") == "lite")
    api.set_setting("ui_effects", orig)  # restore
    check("set_setting restored", api.settings.get("ui_effects") == orig)
    # nested (dotted) write + restore
    orig_tone = (api.settings.get("prompt_prefs", {}) or {}).get("tone", "Neutral")
    api.set_setting("prompt_prefs.tone", "Casual")
    check("nested set_setting",
          api.settings.get("prompt_prefs", {}).get("tone") == "Casual")
    api.set_setting("prompt_prefs.tone", orig_tone)

    # ---- favourites: toggle on, confirm, toggle off -------------------------
    probe = "webui-api-test probe item (safe to ignore)"
    r1 = api.toggle_favorite(probe, "clipboard", "")
    check("favorite toggled on", r1.get("fav") is True)
    check("favorite visible", any(f["text"] == probe for f in api.get_favorites()))
    r2 = api.toggle_favorite(probe, "clipboard", "")
    check("favorite toggled off", r2.get("fav") is False)
    check("favorite gone", not any(f["text"] == probe for f in api.get_favorites()))

    # ---- bindings -----------------------------------------------------------
    check("validate good binding", api.validate_binding("ctrl+alt+s")["ok"])
    check("validate rejects combo hold",
          not api.validate_binding("ctrl+alt", hold=True)["ok"])
    check("pretty binding", api.pretty_binding("ctrl+windows")["pretty"])

    # ---- mics / thumbs / deck-job notice ------------------------------------
    mics = api.list_microphones()
    check("mic list has default", mics and mics[0]["index"] == -1)
    check("thumb rejects outside-data path",
          api.get_clip_thumb("C:\\Windows\\notepad.exe") == "")
    # With no controller running, the deck job must fail HONESTLY (live=False,
    # explains the tray app is needed); with one running it routes for real.
    dj = api.run_deck_job(2, "email", [])
    if api.controller_alive():
        check("deck job routed to live controller", dj.get("live") is True)
    else:
        check("deck job returns honest notice",
              dj.get("ok") is False and dj.get("live") is False
              and "tray" in dj.get("message", "").lower())

    # ---- capture highlighted selection (Deck "Capture" button) --------------
    # Always returns the {ok, selection} shape. Asserted version-tolerantly (a
    # live BUT older controller without the grab_selection cmd, or no controller
    # at all, must both fail HONESTLY rather than fabricate a captured item):
    #   ok True  -> selection is a string (grabbed; '' just means nothing selected)
    #   ok False -> selection == '' AND the message points at the tray app
    cap = api.capture_selection()
    check("capture_selection has selection field", "selection" in cap)
    check("capture_selection consistent shape",
          (cap.get("ok") is True and isinstance(cap.get("selection"), str))
          or (cap.get("ok") is False and cap.get("selection") == ""
              and "mumble" in cap.get("message", "").lower()))

    race_api = webui_shell.Api()
    race_api.settings.update(pro_mode=True, local_only_mode=False, instant_text=False,
                             llm_provider="cerebras", cerebras_api_key="csk-old",
                             cerebras_model="old-model")
    first_started = threading.Event(); release_first = threading.Event()
    first_result = {}; original_fetch = webui_shell.ai.fetch_models
    original_ctrl_send = webui_shell._ctrl_send; controller_reloads = []
    def raced_fetch(_provider, key):
        if key == "csk-old":
            first_started.set(); release_first.wait(timeout=5); return ["old-model"]
        return ["new-model"]
    webui_shell.ai.fetch_models = raced_fetch
    webui_shell._ctrl_send = lambda message, timeout=0.8: (controller_reloads.append(dict(message)) or {"ok": True})
    worker = threading.Thread(target=lambda: first_result.update(race_api.list_models("cerebras")))
    try:
        worker.start(); first_started.wait(timeout=5)
        race_api.settings.update(cerebras_api_key="csk-new", cerebras_model="new-model")
        newest = race_api.list_models("cerebras")
        release_first.set(); worker.join(timeout=5)
    finally:
        release_first.set(); worker.join(timeout=5); webui_shell.ai.fetch_models = original_fetch; webui_shell._ctrl_send = original_ctrl_send
    runtime_route = webui_shell.processing_route.snapshot(type(race_api.settings)(), feature="prompt", lane="prompt")
    check("older model discovery cannot overwrite newer credential truth",
          newest.get("ok") is True and first_result.get("obsolete") is True
          and runtime_route.effective_route == "hosted" and runtime_route.model == "new-model")
    check("latest confirmation reloads the independent live controller",
          controller_reloads == [
              {"cmd": "reload", "key": "_confirmed_text_models"},
              {"cmd": "reload", "key": "_confirmed_text_models"},
              {"cmd": "reload", "key": "_confirmed_text_models"},
              {"cmd": "reload", "key": "_confirmed_text_models"},
          ])

    print()
    if FAILED:
        print(f"FAILED: {len(FAILED)} -> {FAILED}")
        sys.exit(1)
    print("WEBUI_API_OK")


if __name__ == "__main__":
    main()
