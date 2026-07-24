#!/usr/bin/env python3
"""Focused regression coverage for truthful Stats dashboard persistence data."""

import copy
import glob
import json
import os
import shutil
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import meeting_store
import stats as stats_module
from stats import Stats


FAILURES = []


def check(name, condition):
    print(f"  [{'ok  ' if condition else 'FAIL'}] {name}")
    if not condition:
        FAILURES.append(name)


root = tempfile.mkdtemp(prefix="mumble_stats_dashboard_")
try:
    print("\n== atomic mutation and reset contracts ==")
    path = os.path.join(root, "stats.json")
    store = Stats(path)
    check("valid dictation persists", store.record(
        120, 60.0, "text", day="2026-07-17", hour=9))
    check("valid Reader playback persists", store.record_reader_session(
        250, 75.0, day="2026-07-17"))
    check("feature counter persists", store.bump_feature("deck") is True)
    before = copy.deepcopy(store.data)
    before_disk = Stats(path).data
    real_replace = stats_module.os.replace

    def fail_primary_replace(source, destination):
        if destination == path:
            raise OSError("simulated full disk")
        return real_replace(source, destination)

    with patch.object(stats_module.os, "replace", side_effect=fail_primary_replace):
        saved = store.record(40, 12.0, "text", day="2026-07-17", hour=10)
    check("failed dictation save reports failure", saved is False)
    check("failed dictation save rolls memory back", store.data == before)
    check("failed dictation save leaves disk generation unchanged",
          Stats(path).data == before_disk)

    with patch.object(stats_module.os, "replace", side_effect=fail_primary_replace):
        reset = store.reset("all")
    check("failed reset reports failure", reset is False)
    check("failed reset rolls memory back", store.data == before)
    check("invalid reset scope is rejected", store.reset("mystery") is False)
    check("invalid reset scope changes nothing", store.data == before)

    check("Reader-only reset succeeds", store.reset("reader") is True)
    check("Reader reset clears playback only",
          store.reader_summary()["total_sessions"] == 0
          and store.summary()["total_transcripts"] == 1)
    check("full activity reset succeeds", store.reset("all") is True)
    check("full reset preserves feature-tip counters",
          store.feature_counts().get("deck") == 1)

    print("\n== input truth and coherent snapshot ==")
    empty_reader = Stats(os.path.join(root, "reader.json"))
    check("zero Reader duration is rejected",
          empty_reader.record_reader_session(200, 0.0) is False)
    check("zero Reader duration creates no activity",
          empty_reader.reader_summary()["total_sessions"] == 0
          and empty_reader.reader_streak() == (0, 0))
    check("non-finite dictation duration is rejected",
          empty_reader.record(10, float("nan"), "text") is False)
    check("invalid dictation day is rejected",
          empty_reader.record(10, 2.0, "text", day="not-a-day") is False)
    check("future activity cannot skew dashboard periods",
          empty_reader.record(10, 2.0, "text", day="2999-01-01") is False
          and empty_reader.record_reader_session(
              10, 2.0, day="2999-01-01") is False)
    empty_reader.data["days"]["2026-07-17"] = [0, 0]
    empty_reader.data["reader_days"]["2026-07-17"] = 0.0
    check("legacy zero buckets do not create false streaks",
          empty_reader.streak() == (0, 0)
          and empty_reader.reader_streak() == (0, 0))

    coherent = Stats(os.path.join(root, "coherent.json"))
    coherent.record(60, 30.0, "text", day="2026-07-17", hour=14)
    coherent.record(20, 10.0, "email", day="2026-07-17", hour=15)
    coherent.record_reader_session(300, 90.0, day="2026-07-17")
    snapshot = coherent.dashboard_snapshot(98)
    check("dashboard snapshot has exactly 98 daily buckets",
          len(snapshot["daily"]) == 98)
    check("snapshot totals match day buckets",
          snapshot["summary"]["total_words"]
          == sum(bucket[0] for bucket in snapshot["all_days"].values()))
    check("snapshot totals match mode buckets",
          snapshot["summary"]["total_transcripts"]
          == sum(mode[1] for mode in snapshot["modes"]))
    check("start-hour buckets retain output-word total",
          sum(snapshot["time_of_day"]["hours"])
          == snapshot["summary"]["total_words"])

    print("\n== corruption recovery ==")
    recover_path = os.path.join(root, "recover.json")
    recover = Stats(recover_path)
    recover.record(10, 5.0, "text", day="2026-07-16", hour=8)
    first_generation = Stats(recover_path).summary()["total_words"]
    recover.record(20, 5.0, "text", day="2026-07-17", hour=9)
    with open(recover_path, "w", encoding="utf-8") as handle:
        handle.write("{broken")
    restored = Stats(recover_path)
    check("damaged primary recovers from last good backup",
          restored.load_health == "recovered")
    check("recovery exposes the last complete generation",
          restored.summary()["total_words"] == first_generation)
    check("damaged bytes are preserved for recovery",
          bool(glob.glob(recover_path + ".corrupt-*")))

    repaired_path = os.path.join(root, "repaired.json")
    with open(repaired_path, "w", encoding="utf-8") as handle:
        json.dump({"total_words": "bad", "days": {"not-a-day": [4, 1]}}, handle)
    repaired = Stats(repaired_path)
    check("structurally invalid values are explicitly marked repaired",
          repaired.load_health == "repaired"
          and repaired.summary()["total_words"] == 0)
    check("the structurally damaged source generation is preserved",
          len(glob.glob(repaired_path + ".corrupt-*")) == 1)
    check("a repaired store can accept and persist new valid activity",
          repaired.record(5, 2.0, "text", day="2026-07-17", hour=7) is True
          and Stats(repaired_path).load_health == "ok")

    corrupt_path = os.path.join(root, "corrupt.json")
    with open(corrupt_path, "w", encoding="utf-8") as handle:
        handle.write("not-json")
    corrupt = Stats(corrupt_path)
    check("unrecoverable stats are marked unavailable",
          corrupt.load_health == "corrupt" and not corrupt.is_new)
    check("ordinary mutation cannot overwrite corrupt stats",
          corrupt.record(1, 1.0, "text") is False)
    check("repeated corrupt reads preserve one copy per damaged generation",
          len(glob.glob(corrupt_path + ".corrupt-*")) == 1)
    check("deliberate full reset can recover a corrupt store",
          corrupt.reset("all") is True and Stats(corrupt_path).load_health == "ok")
    check("full reset preserved the damaged source bytes",
          bool(glob.glob(corrupt_path + ".corrupt-*")))

    print("\n== saved Meeting activity truth ==")
    meeting_path = os.path.join(root, "meetings.json")
    old_path = meeting_store.PATH
    old_health = meeting_store._LAST_LOAD_HEALTH
    meeting_store.PATH = meeting_path
    try:
        rows = [
            {"id": "a", "duration_sec": 60, "created": 100, "status": "ready"},
            {"id": "b", "duration_sec": "bad", "created": 300, "status": "pending"},
            {"id": "c", "duration_sec": -5, "created": 200, "status": "failed"},
            {"id": "d", "created": 250, "status": "ready"},
            {"id": "e", "duration_sec": 30, "created": 275, "status": "unknown"},
        ]
        with open(meeting_path, "w", encoding="utf-8") as handle:
            json.dump(rows, handle)
        activity = meeting_store.activity_summary()
        check("Meeting count reflects the current saved library",
              activity["available"] and activity["saved_count"] == 5)
        check("Meeting duration sums only valid retained records",
              activity["saved_duration_seconds"] == 90.0
              and activity["duration_complete"] is False)
        check("Meeting status counts are derived from saved records",
              activity["processing_count"] == 1
              and activity["attention_count"] == 2)
        check("latest Meeting save uses the latest valid timestamp",
              activity["latest_created"] == 300)

        with open(meeting_path, "w", encoding="utf-8") as handle:
            handle.write("broken")
        unavailable = meeting_store.activity_summary()
        check("corrupt Meeting data is unavailable, not an empty library",
              unavailable["available"] is False
              and unavailable["has_activity"] is None
              and unavailable["saved_count"] is None)
    finally:
        meeting_store.PATH = old_path
        meeting_store._LAST_LOAD_HEALTH = old_health

    print("\n== dashboard presentation contracts ==")
    app_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(app_dir, "webui", "index.html"),
              encoding="utf-8") as handle:
        html = handle.read()
    with open(os.path.join(app_dir, "webui", "app.js"),
              encoding="utf-8") as handle:
        javascript = handle.read()
    with open(os.path.join(app_dir, "webui", "remaster.css"),
              encoding="utf-8") as handle:
        stylesheet = handle.read()
    with open(os.path.join(app_dir, "mumble.py"), encoding="utf-8") as handle:
        controller = handle.read()
    check("Stats has an accessible heading and busy state without a hero bar",
          '<h1 id="stats-title" class="sr-only">Stats</h1>' in html
          and "stats-dashboard-head" not in html
          and 'aria-labelledby="stats-title" aria-busy="false"' in html)
    check("first viewport snapshot spans every Mumble activity surface",
          'grid-template-columns: repeat(5, minmax(0, 1fr))' in stylesheet
          and '"Hours saved"' in javascript
          and '"Reader time"' in javascript
          and '"Reader words"' in javascript
          and '"Meetings"' in javascript
          and '"Meeting time"' in javascript)
    check("daily chart has keyboard controls and a text table",
          'aria-controls="stat-chart stat-table"' in html
          and '<summary>View daily values</summary>' in html
          and 'aria-pressed="true"' in html)
    check("Stats renders one coherent bridge snapshot with race protection",
          'const payload = await call("get_stats_dashboard")' in javascript
          and 'epoch !== STATS_RENDER_EPOCH' in javascript)
    check("Reader and saved-Meeting activity have distinct empty states",
          "No Reader listening has been tracked yet" in javascript
          and "No meetings are saved yet" in javascript)
    check("lifetime-style breakdowns name their all-tracked scope",
          html.count("all tracked") >= 1
          and "all tracked · peak" in javascript)
    check("time-of-day plot and axis use flush 24-column grids",
          'grid-template-rows: 82px 18px' in stylesheet
          and 'grid-template-columns: repeat(24, minmax(0, 1fr))' in stylesheet
          and '.tod-plots,' in stylesheet
          and 'gap: 0;' in stylesheet
          and 'aria-describedby="tod-summary"' in html
          and 'Output words by dictation start hour.' in javascript)
    check("long chart ranges use readable sampled date ticks",
          'const tickCount = Math.min(6, days.length)' in javascript)
    deck_start = controller.index("    def _run_deck_job_impl")
    deck_end = controller.index("    def ", deck_start + 8)
    check("Deck-generated text no longer pollutes spoken dictation stats",
          "stat_store.record" not in controller[deck_start:deck_end])
    check("dictation day and hour are captured at recording start",
          'stat_clock = time.localtime(self._rec_start)' in controller
          and 'day=stat_context.get("day")' in controller
          and 'hour=stat_context.get("hour")' in controller)
finally:
    shutil.rmtree(root, ignore_errors=True)


print()
if FAILURES:
    print(f"{len(FAILURES)} FAILED: {FAILURES}")
    sys.exit(1)
print("STATS_DASHBOARD_GREEN")
