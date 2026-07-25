#!/usr/bin/env python3
"""Smoke test for the web UI's Python bridge (webui_shell.Api).

SAFE BY DESIGN: every run owns a throwaway data directory and disables live
controller/network behavior before importing the bridge. It never reads or
mutates the user's live Mumble stores, even when launched ad hoc or alongside
the running application.

Preferred run: python run_tests.py test_webui_api.py
"""

import json
import os
import sys
import tempfile

_OWNED_TEST_DATA = None
if not (os.environ.get("MUMBLE_TEST_DATA_DIR") or "").strip():
    _OWNED_TEST_DATA = tempfile.TemporaryDirectory(
        prefix="mumble_webui_api_test_")
    os.environ["MUMBLE_TEST_DATA_DIR"] = _OWNED_TEST_DATA.name
os.environ["MUMBLE_OFFLINE_TESTS"] = "1"

import webui_shell

# Fixed controller ports can belong to a real running Mumble. This test checks
# bridge response shapes with an absent controller and must never contact it.
webui_shell._ctrl_send = lambda *_args, **_kwargs: None

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
                "today_words", "dictation_max_seconds",
                "dictation_max_display", "meeting_max_seconds",
                "meeting_max_display", "meeting_count", "meeting_minutes",
                "meeting_stats_available"):
        check(f"overview has {key}", key in o)
    check("overview words is int", isinstance(o["total_words"], int))

    # ---- hotkeys -----------------------------------------------------------
    hk = api.get_hotkeys()
    for key in ("hotkey", "quick_paste_hotkey", "search_hotkey", "mode_key"):
        check(f"hotkeys has {key}", bool(hk.get(key)))
    check("fresh Search shortcut is Ctrl + Alt + F",
          hk.get("search_hotkey") == "Ctrl + Alt + F")

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
    dashboard = api.get_stats_dashboard()
    check("stats dashboard response ok", dashboard.get("ok") is True)
    check("stats dashboard has every activity domain",
          all(key in dashboard for key in ("dictation", "reader", "meetings")))
    if dashboard["dictation"].get("available"):
        check("stats dashboard daily window is 98 days",
              len(dashboard["dictation"].get("daily", [])) == 98)
        check("stats dashboard exposes coherent dictation fields",
              all(key in dashboard["dictation"]
                  for key in ("summary", "modes", "insights", "current_streak")))
    if dashboard["reader"].get("available"):
        check("Reader dashboard labels tracked playback scope",
              dashboard["reader"].get("scope") == "tracked_playback")
    check("Meeting dashboard reports availability explicitly",
          isinstance(dashboard["meetings"].get("available"), bool))

    # ---- presets ------------------------------------------------------------
    ps = api.get_presets()
    check("9+ presets", len(ps) >= 9)
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
    check("validate Search default", api.validate_binding("ctrl+alt+f")["ok"])
    check("validate rejects multi-step press binding",
          not api.validate_binding("ctrl+k, ctrl+f")["ok"])
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

    # =====================================================================
    #  MEETING BRIDGE TESTS (VAL-MEETING-104 through VAL-MEETING-128)
    # =====================================================================
    import meeting_store

    # Create a test meeting with full data for export/read tests
    test_segments = [
        {"speaker": "Speaker 1", "start_sec": 0.0, "end_sec": 5.2,
         "text": "Good morning everyone", "confidence": 0.9},
        {"speaker": "Speaker 2", "start_sec": 5.5, "end_sec": 12.0,
         "text": "Let us review the budget today", "confidence": 0.85},
        {"speaker": "Speaker 1", "start_sec": 12.3, "end_sec": 20.1,
         "text": "I think we should approve the new proposal", "confidence": 0.88},
    ]
    test_speakers = [
        {"label": "Speaker 1", "name": None, "color": "#D4AF37"},
        {"label": "Speaker 2", "name": None, "color": "#5AA9E6"},
    ]
    test_mid = meeting_store.save_meeting(
        "Test Meeting â€” Bridge Tests", "test_audio.wav", 45.5,
        test_segments, test_speakers,
        summary="A test summary for bridge testing.",
        action_items=["Alice: Review the proposal", "Bob: Check budget figures"],
        key_decisions=["Approved the new proposal unanimously"],
        open_questions=["When will the budget be finalized?"],
        processing_mode="deep")
    check("test meeting created", bool(test_mid) and len(test_mid) == 12)
    test_mid2 = meeting_store.save_meeting(
        "Lightweight Meeting", "test_audio2.wav", 10.0,
        [], [],
        processing_mode="lightweight")
    check("test meeting 2 created", bool(test_mid2))

    # ---- VAL-MEETING-104: meeting_list returns a list (empty when no meetings) ----
    ml = api.meeting_list()
    check("meeting_list returns list", isinstance(ml, list))
    check("meeting_list contains our test meeting",
          any(m["id"] == test_mid for m in ml))
    ms = api.meeting_search("approve the new proposal")
    check("meeting_search returns structured success", ms.get("ok") is True)
    check("meeting_search scans later transcript segments",
          [m.get("id") for m in ms.get("items", [])] == [test_mid])
    check("meeting_search reports full library total", ms.get("total") == 2)

    # ---- VAL-MEETING-105: meeting_open returns full meeting or None ----------
    mo = api.meeting_open(test_mid)
    check("meeting_open valid id returns dict", isinstance(mo, dict))
    check("meeting_open has id", mo.get("id") == test_mid)
    check("meeting_open has segments", len(mo.get("segments", [])) == 3)
    check("meeting_open has speakers", len(mo.get("speakers", [])) == 2)
    check("meeting_open has summary", isinstance(mo.get("summary"), str)
          and len(mo["summary"]) > 0)
    check("meeting_open has key_decisions", isinstance(mo.get("key_decisions"), list))
    check("meeting_open has open_questions", isinstance(mo.get("open_questions"), list))
    check("meeting_open has processing_mode",
          mo.get("processing_mode") == "deep")
    mo_none = api.meeting_open("nonexistent99")
    check("meeting_open missing id returns None", mo_none is None)

    # ---- VAL-MEETING-106: meeting_delete returns ok True --------------------
    tmp_mid = meeting_store.save_meeting("To Delete", "d.wav", 5.0, [], [])
    dr = api.meeting_delete(tmp_mid)
    check("meeting_delete returns ok True", dr.get("ok") is True)
    check("meeting_delete actually removed",
          api.meeting_open(tmp_mid) is None)
    # Delete of already-deleted (idempotent) still returns ok
    dr2 = api.meeting_delete(tmp_mid)
    check("meeting_delete idempotent", dr2.get("ok") is True)
    locked_mid = meeting_store.save_meeting(
        "Locked Audio", "locked.wav", 5.0, [], [])
    original_delete_audio = meeting_store._delete_audio_file
    meeting_store._delete_audio_file = lambda *args, **kwargs: False
    locked_delete = api.meeting_delete(locked_mid)
    meeting_store._delete_audio_file = original_delete_audio
    check("meeting_delete does not claim audio unlink failure succeeded",
          locked_delete.get("ok") is False)
    check("meeting_delete surfaces partial metadata removal truthfully",
          locked_delete.get("removed_metadata") is True and
          "audio file" in locked_delete.get("message", ""))

    # ---- VAL-MEETING-107: meeting_star returns ok with set_starred result ---
    sr = api.meeting_star(test_mid, True)
    check("meeting_star set True returns ok", sr.get("ok") is True)
    check("meeting_star persist True",
          api.meeting_open(test_mid).get("starred") is True)
    sr2 = api.meeting_star(test_mid, False)
    check("meeting_star set False returns ok", sr2.get("ok") is True)
    check("meeting_star persist False",
          api.meeting_open(test_mid).get("starred") is False)

    # ---- VAL-MEETING-108: meeting_rename_speaker returns ok ------------------
    rn = api.meeting_rename_speaker(test_mid, "Speaker 1", "Alice")
    check("meeting_rename_speaker returns ok", rn.get("ok") is True)
    m_after = api.meeting_open(test_mid)
    sp1 = next((s for s in m_after["speakers"]
                if s["label"] == "Speaker 1"), None)
    check("meeting_rename_speaker name persisted",
          sp1 is not None and sp1.get("name") == "Alice")
    # Clear name with empty string
    rn2 = api.meeting_rename_speaker(test_mid, "Speaker 1", "  ")
    check("meeting_rename_speaker blank clears name", rn2.get("ok") is True)
    m_after2 = api.meeting_open(test_mid)
    sp1b = next((s for s in m_after2["speakers"]
                 if s["label"] == "Speaker 1"), None)
    check("meeting_rename_speaker name cleared",
          sp1b is not None and sp1b.get("name") is None)

    # ---- VAL-MEETING-109: meeting_update_title returns ok -------------------
    ut = api.meeting_update_title(test_mid, "Updated Bridge Test Title")
    check("meeting_update_title returns ok", ut.get("ok") is True)
    check("meeting_update_title persisted",
          api.meeting_open(test_mid)["title"] == "Updated Bridge Test Title")
    blank_title = api.meeting_update_title(test_mid, "   ")
    check("meeting_update_title rejects a blank title",
          blank_title.get("ok") is False)

    # ---- VAL-MEETING-110 & 111: meeting_summarize ----------------------------
    # With no real LLM key, summarize should return ok:False with key message
    sm = api.meeting_summarize(test_mid)
    check("meeting_summarize returns dict", isinstance(sm, dict))
    # Without a real LLM key, summary can fail honestly
    check("meeting_summarize has ok key", "ok" in sm)
    if not sm.get("ok"):
        check("meeting_summarize no-key message mentions key/Settings",
              "key" in sm.get("message", "").lower()
              or "Settings" in sm.get("message", ""))

    # ---- VAL-MEETING-112 & 113: meeting_extract_actions ---------------------
    ea = api.meeting_extract_actions(test_mid)
    check("meeting_extract_actions returns dict", isinstance(ea, dict))
    check("meeting_extract_actions has ok key", "ok" in ea)
    check("meeting_extract_actions has items key", "items" in ea)
    check("meeting_extract_actions items is list",
          isinstance(ea.get("items"), list))

    # ---- VAL-MEETING-114 & 115: meeting_start_recording ---------------------
    sr_rec = api.meeting_start_recording()
    check("meeting_start_recording returns dict", isinstance(sr_rec, dict))
    check("meeting_start_recording has ok key", "ok" in sr_rec)
    # When no controller: ok is False with a message
    if not sr_rec.get("ok"):
        check("meeting_start_recording no-controller message present",
              bool(sr_rec.get("message")))
    live_status = api.meeting_recording_status()
    check("meeting_recording_status returns a safe shape",
          isinstance(live_status, dict) and
          live_status.get("active") is False and
          live_status.get("state") == "unknown")

    # ---- VAL-MEETING-116 & 117: meeting_stop_recording ----------------------
    st_rec = api.meeting_stop_recording("Sprint Planning")
    check("meeting_stop_recording returns dict", isinstance(st_rec, dict))
    check("meeting_stop_recording has ok key", "ok" in st_rec)
    # With no controller, ok is False
    if not st_rec.get("ok"):
        check("meeting_stop_recording no-controller message present",
              bool(st_rec.get("message")))
    # Empty title should also work
    st_rec2 = api.meeting_stop_recording()
    check("meeting_stop_recording no-title returns dict",
          isinstance(st_rec2, dict))

    # ---- VAL-MEETING-118: meeting_import_audio ------------------------------
    ia = api.meeting_import_audio("C:\\nonexistent\\audio.wav")
    check("meeting_import_audio returns dict", isinstance(ia, dict))
    check("meeting_import_audio has ok key", "ok" in ia)

    # ---- VAL-MEETING-119: meeting_get_tags returns a list -------------------
    tg = api.meeting_get_tags()
    check("meeting_get_tags returns list", isinstance(tg, list))

    # ---- VAL-MEETING-120: meeting_export txt and json ------------------------
    exp_txt = api.meeting_export(test_mid, "txt")
    check("meeting_export txt ok", exp_txt.get("ok") is True)
    check("meeting_export txt has content",
          isinstance(exp_txt.get("content"), str)
          and len(exp_txt["content"]) > 0)
    check("meeting_export txt mime text/plain",
          exp_txt.get("mime") == "text/plain")
    check("meeting_export txt contains title",
          "Updated Bridge Test Title" in exp_txt.get("content", ""))
    check("meeting_export txt contains transcript",
          "Good morning everyone" in exp_txt.get("content", ""))
    check("meeting_export txt contains summary section",
          "--- Summary ---" in exp_txt.get("content", ""))
    check("meeting_export txt contains action items",
          "--- Action Items ---" in exp_txt.get("content", ""))
    check("meeting_export txt contains key decisions",
          "--- Key Decisions ---" in exp_txt.get("content", ""))

    exp_json = api.meeting_export(test_mid, "json")
    check("meeting_export json ok", exp_json.get("ok") is True)
    check("meeting_export json mime application/json",
          exp_json.get("mime") == "application/json")
    jc = exp_json.get("content", "")
    check("meeting_export json valid JSON",
          isinstance(jc, str) and len(jc) > 0)
    try:
        parsed = json.loads(jc)
        check("meeting_export json parseable",
              isinstance(parsed, dict) and parsed.get("id") == test_mid)
    except Exception:
        check("meeting_export json parseable", False)

    # ---- VAL-MEETING-121: meeting_export missing meeting --------------------
    exp_miss = api.meeting_export("doesnotexist99", "txt")
    check("meeting_export missing meeting ok False",
          exp_miss.get("ok") is False)
    check("meeting_export missing meeting has message",
          "message" in exp_miss)

    # ---- VAL-MEETING-122: meeting_export markdown and html (NEW) ------------
    exp_md = api.meeting_export(test_mid, "markdown")
    check("meeting_export markdown ok", exp_md.get("ok") is True)
    check("meeting_export markdown has content",
          isinstance(exp_md.get("content"), str)
          and len(exp_md["content"]) > 0)
    check("meeting_export markdown mime text/markdown",
          exp_md.get("mime") == "text/markdown")
    check("meeting_export markdown has ## Summary",
          "## Summary" in exp_md.get("content", ""))
    check("meeting_export markdown has ## Action Items",
          "## Action Items" in exp_md.get("content", ""))
    check("meeting_export markdown has ## Key Decisions",
          "## Key Decisions" in exp_md.get("content", ""))
    check("meeting_export markdown has ## Open Questions",
          "## Open Questions" in exp_md.get("content", ""))
    check("meeting_export markdown has ## Transcript",
          "## Transcript" in exp_md.get("content", ""))
    check("meeting_export markdown has transcript text",
          "Good morning everyone" in exp_md.get("content", ""))

    exp_html = api.meeting_export(test_mid, "html")
    check("meeting_export html ok", exp_html.get("ok") is True)
    check("meeting_export html has content",
          isinstance(exp_html.get("content"), str)
          and len(exp_html["content"]) > 0)
    check("meeting_export html mime text/html",
          exp_html.get("mime") == "text/html")
    check("meeting_export html is self-contained",
          exp_html.get("content", "").startswith("<!DOCTYPE html>")
          or "<html" in exp_html.get("content", ""))
    check("meeting_export html contains title",
          "Updated Bridge Test Title" in exp_html.get("content", ""))

    # Export lightweight meeting (no summary/actions/decisions)
    exp_md_lw = api.meeting_export(test_mid2, "markdown")
    check("meeting_export markdown lightweight ok",
          exp_md_lw.get("ok") is True)
    # Lightweight meeting should not have ## Summary (when summary is empty)
    check("meeting_export markdown lightweight no summary section",
          "## Summary" not in exp_md_lw.get("content", ""))

    # ---- VAL-MEETING-122 cont: unsupported format (VAL-MEETING-069) --------
    exp_bad = api.meeting_export(test_mid, "pdf")
    check("meeting_export unsupported format ok False",
          exp_bad.get("ok") is False)
    check("meeting_export unsupported format has message",
          "message" in exp_bad)

    # ---- VAL-MEETING-123: meeting_pause_recording (NEW) ---------------------
    pause_r = api.meeting_pause_recording()
    check("meeting_pause_recording returns dict", isinstance(pause_r, dict))
    check("meeting_pause_recording has ok key", "ok" in pause_r)
    # Without controller, ok should be False with a message
    if not pause_r.get("ok"):
        check("meeting_pause_recording no-controller message",
              bool(pause_r.get("message")))

    # ---- VAL-MEETING-124: meeting_resume_recording (NEW) --------------------
    resume_r = api.meeting_resume_recording()
    check("meeting_resume_recording returns dict", isinstance(resume_r, dict))
    check("meeting_resume_recording has ok key", "ok" in resume_r)
    if not resume_r.get("ok"):
        check("meeting_resume_recording no-controller message",
              bool(resume_r.get("message")))

    # ---- VAL-MEETING-125: meeting_set_processing_mode valid (NEW) -----------
    spm_d = api.meeting_set_processing_mode("deep")
    check("meeting_set_processing_mode deep ok", spm_d.get("ok") is True)
    check("meeting_set_processing_mode deep persisted",
          api.settings.get("meeting_processing_mode") == "deep")
    spm_l = api.meeting_set_processing_mode("lightweight")
    check("meeting_set_processing_mode lightweight ok", spm_l.get("ok") is True)
    check("meeting_set_processing_mode lightweight persisted",
          api.settings.get("meeting_processing_mode") == "lightweight")

    # ---- VAL-MEETING-126: meeting_set_processing_mode invalid (NEW) ---------
    spm_inv = api.meeting_set_processing_mode("bogus")
    check("meeting_set_processing_mode invalid ok False",
          spm_inv.get("ok") is False)
    check("meeting_set_processing_mode invalid has message",
          "message" in spm_inv)
    check("meeting_set_processing_mode invalid not persisted",
          api.settings.get("meeting_processing_mode") == "lightweight")
    spm_empty = api.meeting_set_processing_mode("")
    check("meeting_set_processing_mode empty rejected",
          spm_empty.get("ok") is False)
    spm_none = api.meeting_set_processing_mode(None)
    check("meeting_set_processing_mode None rejected",
          spm_none.get("ok") is False)

    # ---- VAL-MEETING-127 & 128: meeting_extract_deep (NEW) ------------------
    ed = api.meeting_extract_deep(test_mid)
    check("meeting_extract_deep returns dict", isinstance(ed, dict))
    check("meeting_extract_deep has ok key", "ok" in ed)
    # Without a real LLM key, deep extract should return ok:False with key message
    if not ed.get("ok"):
        check("meeting_extract_deep no-key has message", "message" in ed)
        check("meeting_extract_deep no-key message mentions key/Settings",
              "key" in ed.get("message", "").lower()
              or "Settings" in ed.get("message", ""))
    # Test with non-existent meeting
    ed_miss = api.meeting_extract_deep("nonexistent99")
    check("meeting_extract_deep missing meeting ok False",
          ed_miss.get("ok") is False)

    # ---- MIME type verification for all export formats (VAL-MEETING-067) ----
    check("export txt mime text/plain",
          api.meeting_export(test_mid, "txt")["mime"] == "text/plain")
    check("export json mime application/json",
          api.meeting_export(test_mid, "json")["mime"] == "application/json")
    check("export markdown mime text/markdown",
          api.meeting_export(test_mid, "markdown")["mime"] == "text/markdown")
    check("export md alias same as markdown",
          api.meeting_export(test_mid, "md")["mime"] == "text/markdown")
    check("export html mime text/html",
          api.meeting_export(test_mid, "html")["mime"] == "text/html")
    with tempfile.TemporaryDirectory(prefix="mumble_meeting_export_") as td:
        exported = os.path.join(td, "meeting.md")
        saved = api.meeting_save_export(test_mid, "md", exported)
        check("export save writes selected file", saved.get("ok") is True
              and os.path.isfile(exported))
        if os.path.isfile(exported):
            with open(exported, "r", encoding="utf-8") as fh:
                check("saved export contains meeting title",
                      "Updated Bridge Test Title" in fh.read())

    # ---- cleanup test meetings ----------------------------------------------
    api.meeting_delete(test_mid)
    api.meeting_delete(test_mid2)
    check("cleanup test meeting 1 gone",
          api.meeting_open(test_mid) is None)
    check("cleanup test meeting 2 gone",
          api.meeting_open(test_mid2) is None)

    print()
    if FAILED:
        print(f"FAILED: {len(FAILED)} -> {FAILED}")
        sys.exit(1)
    print("WEBUI_API_OK")


if __name__ == "__main__":
    main()
