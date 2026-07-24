#!/usr/bin/env python3
"""Local transcript history + word stats. Mumble's safety net (nothing dictated is
ever lost) and the source for the Stats screen."""

import json
import math
import os
import threading
from collections import deque
from datetime import date, datetime

from storage_lock import exclusive_file_lock


class History:
    def __init__(self, json_path, text_path, maxlen=100):
        self.json_path = json_path
        self.text_path = text_path
        self.maxlen = maxlen
        # Serializes the load→mutate→save sequences (add/delete/clear). The
        # dictation thread appends while the webui thread deletes; without this,
        # a delete landing between add()'s _sync_from_disk and _save_json wrote
        # back the stale list and RESURRECTED the just-deleted entry (the very
        # bug _sync_from_disk only narrowed). Re-entrant: mutators call helpers
        # that may re-acquire. Mirrors Clipboard's lock.
        self._lock = threading.RLock()
        self.items = deque(maxlen=maxlen)
        self._cumulative_path = json_path.replace(".json", "_cumulative.json")
        self._cumulative = {"total_words": 0, "total_transcripts": 0}
        self._load()
        self._load_cumulative()

    def _read_items(self):
        """Parse history.json into a fresh deque. Raises on read/parse failure so
        callers can decide whether to keep their existing in-memory list."""
        with open(self.json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # A corrupt / schema-changed file may parse to a non-list (e.g. a bare
        # number or object). Slicing that raises TypeError; treat as empty.
        if not isinstance(data, list):
            data = []
        out = deque(maxlen=self.maxlen)
        for e in data[-self.maxlen :]:
            if isinstance(e, dict) and "text" in e:
                e = dict(e)
                e["text"] = str(e.get("text") or "")
                e.setdefault("mode", e.get("kind", "text"))
                try:
                    e["words"] = max(0, int(e.get(
                        "words", len(e["text"].split())) or 0))
                except (TypeError, ValueError, OverflowError):
                    e["words"] = len(e["text"].split())
                try:
                    duration = float(e.get("duration", 0.0) or 0.0)
                    e["duration"] = (max(0.0, duration)
                                     if math.isfinite(duration) else 0.0)
                except (TypeError, ValueError, OverflowError):
                    e["duration"] = 0.0
                out.append(e)
        return out

    def _load(self):
        try:
            self.items = self._read_items()
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, TypeError):
            pass

    def _sync_from_disk(self):
        """Reconcile the in-memory list with disk BEFORE appending, so a deletion
        made by the OTHER process (the webui main window writes the same files) is
        not resurrected by writing back our stale list. The controller never
        reloaded after init, so deleted transcripts reappeared on the next
        dictation. Keeps the current list on any read failure (atomic os.replace
        means a successful read is always a whole file, never partial)."""
        try:
            self.items = self._read_items()
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, TypeError):
            pass
        self._load_cumulative()

    def _load_cumulative(self):
        try:
            with open(self._cumulative_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Only trust a dict; a wrong-typed cumulative would break every += later.
            if isinstance(data, dict):
                self._cumulative.update(
                    {k: data[k] for k in ("total_words", "total_transcripts")
                     if isinstance(data.get(k), (int, float))}
                )
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, TypeError):
            pass

    def _save_cumulative(self):
        try:
            with open(self._cumulative_path, "w", encoding="utf-8") as f:
                json.dump(self._cumulative, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def add(self, text, mode="text", duration=0.0, raw=None, quality=None,
            via=None):
        # Reconcile with disk first so a deletion made in the webui window isn't
        # resurrected by writing back our stale in-memory list (the resurrection
        # bug: the controller never reloaded, so deleted transcripts reappeared
        # after the next dictation). The lock makes reconcile→append→save atomic
        # against a concurrent delete on another thread.
        with self._lock:
            with exclusive_file_lock(self.json_path) as acquired:
                if not acquired:
                    return None
                self._sync_from_disk()
                previous_items = deque(self.items, maxlen=self.maxlen)
                previous_cumulative = dict(self._cumulative)
                entry = self._build_entry(text, mode, duration, raw, quality, via)
                self.items.append(entry)
                # Update cumulative stats (persist independently of history clearing)
                self._cumulative["total_words"] += entry["words"]
                self._cumulative["total_transcripts"] += 1
                if not self._save_json():
                    self.items = previous_items
                    self._cumulative = previous_cumulative
                    return None
                self._append_text(entry)
                self._save_cumulative()
                return entry

    def _build_entry(self, text, mode, duration, raw, quality, via):
        now = datetime.now()
        text = text or ""
        return {
            "time": now.strftime("%H:%M"),
            "stamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": mode,
            "text": text,
            # Pre-conversion transcript (what was actually said before any mode shaping).
            # Lets the user re-feed the original if a mode came out wrong. Only stored
            # when it differs from the final text (plain Text mode → no point).
            "raw": (
                raw
                if (raw and raw.strip() and raw.strip() != str(text).strip())
                else None
            ),
            "words": len(text.split()),
            "duration": round(float(duration), 2),
            # Locally derived audio-quality verdict (good/fair/bad) from the
            # decoder's own confidence — shown as a chip in History.
            "quality": quality,
            # Routing provenance: "convert" when the Convert router produced
            # this entry ("convert to email …") — it lives in the RESULTING
            # mode's category with a small via-label.
            "via": via,
        }

    def recent(self, n=10):
        with self._lock:
            items = list(self.items)
        if n <= 0:
            return []
        return items[-n:][::-1]

    def all_newest_first(self):
        with self._lock:
            return list(self.items)[::-1]

    def clear(self):
        with self._lock:
            with exclusive_file_lock(self.json_path) as acquired:
                if not acquired:
                    return False
                previous_items = deque(self.items, maxlen=self.maxlen)
                self.items.clear()
                if self._save_json():
                    return True
                self.items = previous_items
                return False

    def delete_index(self, i):
        """Delete the entry at index i in all_newest_first() order. Returns True if removed."""
        # Reconcile with disk first (mirrors delete_match): without this, a change
        # by another process makes `i` map to the wrong in-memory entry.
        with self._lock, exclusive_file_lock(self.json_path) as acquired:
            if not acquired:
                return False
            self._sync_from_disk()
            items = list(self.items)
            j = len(items) - 1 - i
            if 0 <= j < len(items):
                # NOTE: _cumulative is the MONOTONIC lifetime seed-floor for the
                # Stats store ("words ever dictated"), not a count of currently-kept
                # transcripts — so deleting one does NOT decrement it, matching the
                # displayed Stats store (which also never drops on delete). The old
                # decrement made the two lifetime totals diverge after a deletion.
                del items[j]
                previous_items = self.items
                self.items = deque(items, maxlen=self.maxlen)
                if self._save_json():
                    return True
                self.items = previous_items
                return False
            return False

    def delete_match(self, stamp, text=None):
        """Delete by STABLE identity (stamp [+ text]) instead of array index.
        Index deletion sent a stale position whenever the rendered list shifted
        under it (a prior delete, a refresh, or a controller insert between two
        deletes), removing the WRONG entry. Reloads from disk first so it deletes
        against the current cross-process truth. Returns True if one was removed."""
        with self._lock, exclusive_file_lock(self.json_path) as acquired:
            if not acquired:
                return False
            self._sync_from_disk()
            items = list(self.items)
            target = None
            for e in reversed(items):  # newest-first, matching the UI order
                if str(e.get("stamp", "")) == str(stamp) and (
                    text is None or str(e.get("text", "")) == str(text)
                ):
                    target = e
                    break
            if target is None:
                return False
            # _cumulative is the monotonic lifetime seed-floor (see delete_index) —
            # deleting a transcript does not decrement it.
            items.remove(target)
            previous_items = self.items
            self.items = deque(items, maxlen=self.maxlen)
            if self._save_json():
                return True
            self.items = previous_items
            return False

    def stats(self):
        with self._lock:
            items = list(self.items)
            cumulative = dict(self._cumulative)
        # WPM ONLY from Text mode: other modes' output is AI-expanded, so its word count
        # has nothing to do with speaking speed (that produced the absurd "fastest" glitch).
        # A human ceiling (300 wpm) is a final guard against any stray short clip.
        timed = [
            e
            for e in items
            if e.get("mode", "text") == "text" and e.get("duration", 0) > 0.4
        ]
        spoken_sec = sum(e.get("duration", 0) for e in timed)
        best_wpm = 0
        sane_words = sane_sec = 0.0
        for e in timed:
            words, dur = e.get("words", 0), e.get("duration", 0)
            wpm = words / (dur / 60) if dur > 0 else 0
            if 0 < wpm <= 300:
                best_wpm = max(best_wpm, wpm)
                sane_words += words
                sane_sec += dur
        avg_wpm = (sane_words / (sane_sec / 60)) if sane_sec > 0 else 0
        today = date.today().strftime("%Y-%m-%d")
        today_words = sum(
            e.get("words", 0)
            for e in items
            if str(e.get("stamp", "")).startswith(today)
        )
        # rough "time saved" vs typing at 45 wpm (realistic average)
        typing_min = cumulative["total_words"] / 45.0
        return {
            "total_words": cumulative["total_words"],
            "total_transcripts": cumulative["total_transcripts"],
            "avg_wpm": round(avg_wpm),
            "best_wpm": round(best_wpm),
            "today_words": today_words,
            "spoken_minutes": round(spoken_sec / 60, 1),
            "typing_minutes_saved": round(typing_min, 1),
        }

    def daily_stats(self, days=7):
        """Returns [(date_str, word_count, transcript_count), …] for the last N days."""
        from collections import defaultdict
        from datetime import timedelta

        today = date.today()
        buckets = defaultdict(lambda: [0, 0])
        with self._lock:
            for e in self.items:
                stamp = str(e.get("stamp", ""))
                if len(stamp) >= 10:
                    buckets[stamp[:10]][0] += e.get("words", 0)
                    buckets[stamp[:10]][1] += 1
        result = []
        for i in range(days - 1, -1, -1):
            d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            wc, tc = buckets.get(d, (0, 0))
            result.append((d, wc, tc))
        return result

    def mode_stats(self):
        """Returns [(mode_label, count, total_words), …] sorted by count desc."""
        from collections import defaultdict

        buckets = defaultdict(lambda: [0, 0])
        with self._lock:
            for e in self.items:
                m = e.get("mode", "text")
                buckets[m][0] += 1
                buckets[m][1] += e.get("words", 0)
        return [
            (m, v[0], v[1]) for m, v in sorted(buckets.items(), key=lambda x: -x[1][0])
        ]

    def streak(self):
        """Return (current_streak_days, best_streak_days). A 'day' with at least one
        transcript counts. Consecutive = no gap day since the most recent active day."""
        from datetime import timedelta

        today = date.today()
        active_days = set()
        with self._lock:
            for e in self.items:
                stamp = str(e.get("stamp", ""))
                if len(stamp) >= 10:
                    active_days.add(stamp[:10])
        if not active_days:
            return 0, 0
        # Current streak: walk backwards from today (or yesterday if today has none)
        current = 0
        d = today
        if today.strftime("%Y-%m-%d") not in active_days:
            d = today - timedelta(days=1)
        while d.strftime("%Y-%m-%d") in active_days:
            current += 1
            d -= timedelta(days=1)
        # Best streak: find the longest run in sorted active days
        valid_days = []
        for stamp in active_days:
            try:
                date.fromisoformat(stamp)
                valid_days.append(stamp)
            except ValueError:
                continue
        if not valid_days:
            return 0, 0
        active_days = set(valid_days)
        sorted_days = sorted(active_days)
        best = run = 1
        for i in range(1, len(sorted_days)):
            prev = date.fromisoformat(sorted_days[i - 1])
            cur = date.fromisoformat(sorted_days[i])
            if (cur - prev).days == 1:
                run += 1
            else:
                best = max(best, run)
                run = 1
        best = max(best, run)
        return current, best

    def _save_json(self):
        """Atomically write history.json (tmp + rename)."""
        tmp = self.json_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(list(self.items), f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.json_path)
            return True
        except OSError as e:
            print(f"history save error ({type(e).__name__}): {e}")
            try:
                os.remove(tmp)
            except OSError:
                pass
            return False

    def _append_text(self, entry):
        try:
            with open(self.text_path, "a", encoding="utf-8") as f:
                f.write(f"[{entry['stamp']}] ({entry['mode']})\n{entry['text']}\n\n")
        except OSError as e:
            print(f"history text append error ({type(e).__name__}): {e}")
