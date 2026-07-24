#!/usr/bin/env python3
"""Independent, persistent dictation stats.

Stats live in their OWN file (stats.json), completely separate from transcripts
(history.json) and the clipboard (clipboard.json). Clearing transcripts or the
clipboard must never reset stats — so nothing here reads those stores. Totals
accumulate until an explicit Stats reset; per-day/per-mode buckets power charts.
"""

import copy
import hashlib
import json
import math
import os
import shutil
import threading
from datetime import date, datetime, timedelta

from storage_lock import exclusive_file_lock

_HUMAN_WPM_CEILING = 300  # guard against absurd wpm from a stray short clip
_TYPING_WPM = 45.0  # baseline typing speed for the "time saved" estimate
_WORDS_PER_PAGE = 250  # reader: words → pages conversion (standard paperback page)


def _today_str():
    return date.today().strftime("%Y-%m-%d")


def _fresh_data():
    return {
        "total_words": 0,
        "total_transcripts": 0,
        "spoken_seconds": 0.0,
        "best_wpm": 0.0,
        "wpm_words": 0.0,
        "wpm_seconds": 0.0,
        "wpm_ema": 0.0,
        "days": {},
        "modes": {},
        "hours": [0] * 24,
        "reader_total_seconds": 0.0,
        "reader_pages_read": 0.0,
        "reader_docs_completed": 0,
        "reader_words_read": 0,
        "reader_sessions": 0,
        "reader_days": {},
        "feature_usage": {},
    }


def _finite_nonnegative(value, *, integer=False):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return int(number) if integer else number


def _iso_day(value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError):
        return None


class Stats:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        self.is_new = True  # True until a file is loaded — lets the app seed it once
        self.load_health = "missing"
        self.load_error = ""
        self.data = _fresh_data()
        self._load()

    # ---------------------------------------------------------------- persistence
    @staticmethod
    def _read(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("stats root is not an object")
        return data

    def _preserve_corrupt_primary(self):
        if not os.path.exists(self.path):
            return
        try:
            digest = hashlib.sha256()
            with open(self.path, "rb") as source:
                for chunk in iter(lambda: source.read(64 * 1024), b""):
                    digest.update(chunk)
            # Content-addressing preserves every distinct damaged generation
            # once, without creating another full copy on every failed mutation.
            target = f"{self.path}.corrupt-{digest.hexdigest()[:12]}"
            if os.path.exists(target):
                return
            shutil.copy2(self.path, target)
        except OSError as e:
            print(f"stats corrupt-copy error ({type(e).__name__}): {e}")

    def _load(self):
        try:
            loaded = self._read(self.path)
            self.load_health = "ok"
        except FileNotFoundError:
            try:
                loaded = self._read(self.path + ".bak")
            except FileNotFoundError:
                self.load_health = "missing"
                return
            except (json.JSONDecodeError, OSError, ValueError) as e:
                self.load_health = "corrupt"
                self.load_error = str(e)
                self.is_new = False
                return
            try:
                shutil.copy2(self.path + ".bak", self.path)
            except OSError as e:
                print(f"stats restore error ({type(e).__name__}): {e}")
            self.load_health = "recovered"
        except (json.JSONDecodeError, OSError, ValueError) as primary_error:
            try:
                loaded = self._read(self.path + ".bak")
            except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
                self._preserve_corrupt_primary()
                self.load_health = "corrupt"
                self.load_error = str(primary_error)
                # A corrupt file is not a fresh install. This prevents startup
                # migration from silently overwriting the only recoverable bytes.
                self.is_new = False
                return
            self._preserve_corrupt_primary()
            try:
                shutil.copy2(self.path + ".bak", self.path)
            except OSError as e:
                print(f"stats restore error ({type(e).__name__}): {e}")
            self.load_health = "recovered"
        self.data.update(loaded)
        self.is_new = False
        before_coercion = copy.deepcopy(self.data)
        self._coerce_types()
        if self.data != before_coercion:
            self._preserve_corrupt_primary()
            self.load_health = "repaired"
            self.load_error = "Invalid statistics values were ignored."

    def _coerce_types(self):
        """A corrupt / hand-edited stats.json can set a structural key to the wrong
        type (e.g. "days": []). The getters do d["days"].get(...) / .items(), which
        then raise AttributeError and take down the whole Stats tab. Coerce every
        key back to its expected shape, falling back to the seeded default."""
        d = self.data
        for k in ("days", "modes", "reader_days", "feature_usage"):
            if not isinstance(d.get(k), dict):
                d[k] = {}

        def _finite(value, default=0.0):
            if isinstance(value, bool):
                return default
            try:
                value = float(value)
                return value if math.isfinite(value) else default
            except (TypeError, ValueError, OverflowError):
                return default

        # Nested buckets are consumed with += during recording, so merely
        # checking the outer dict is insufficient (e.g. {"today": "bad"}
        # used to crash the next successful dictation).
        for key in ("days", "modes"):
            clean = {}
            for name, bucket in d[key].items():
                if isinstance(bucket, (list, tuple)) and len(bucket) >= 2:
                    if key == "days" and _iso_day(name) is None:
                        continue
                    clean[str(name)] = [
                        max(0, int(_finite(bucket[0]))),
                        max(0, int(_finite(bucket[1]))),
                    ]
            d[key] = clean

        clean_reader_days = {}
        for stamp, seconds in d["reader_days"].items():
            try:
                date.fromisoformat(str(stamp))
            except (TypeError, ValueError):
                continue
            clean_reader_days[str(stamp)] = max(0.0, _finite(seconds))
        d["reader_days"] = clean_reader_days
        d["feature_usage"] = {
            str(name): max(0, int(_finite(count)))
            for name, count in d["feature_usage"].items()
        }
        hrs = d.get("hours")
        if not isinstance(hrs, list) or len(hrs) != 24:
            d["hours"] = (list(hrs) if isinstance(hrs, list) else []) + [0] * 24
            d["hours"] = d["hours"][:24]
        d["hours"] = [max(0, int(_finite(value))) for value in d["hours"]]
        int_keys = ("total_words", "total_transcripts", "reader_docs_completed",
                    "reader_words_read", "reader_sessions")
        float_keys = ("spoken_seconds", "best_wpm", "wpm_words", "wpm_seconds",
                      "wpm_ema", "reader_total_seconds", "reader_pages_read")
        for key in int_keys:
            d[key] = max(0, int(_finite(d.get(key))))
        for key in float_keys:
            d[key] = max(0.0, _finite(d.get(key)))

    def _save(self):
        tmp = self.path + ".tmp"
        backup_tmp = self.path + ".bak.tmp"
        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    self.data, f, ensure_ascii=False, indent=2, allow_nan=False)
                f.flush()
                os.fsync(f.fileno())
            if (os.path.exists(self.path)
                    and self.load_health not in ("corrupt", "repaired")):
                shutil.copy2(self.path, backup_tmp)
                os.replace(backup_tmp, self.path + ".bak")
            os.replace(tmp, self.path)
            try:
                import branding
                branding.protect_private_path(self.path)
            except Exception:
                pass
            self.load_health = "ok"
            self.load_error = ""
            return True
        except (OSError, TypeError, ValueError) as e:
            print(f"stats save error ({type(e).__name__}): {e}")
            for leftover in (tmp, backup_tmp):
                try:
                    os.remove(leftover)
                except OSError:
                    pass
            return False

    def _refresh_for_mutation(self):
        """Adopt the latest complete disk snapshot while holding its file lock."""
        latest = Stats(self.path)
        if latest.load_health == "corrupt":
            self.load_health = latest.load_health
            self.load_error = latest.load_error
            self.is_new = False
            return False
        if not latest.is_new:
            self.data = latest.data
            self.is_new = False
            self.load_health = latest.load_health
            self.load_error = latest.load_error
        return True

    def _rollback_snapshot(self):
        return (
            copy.deepcopy(self.data), self.is_new,
            self.load_health, self.load_error,
        )

    def _restore_snapshot(self, snapshot):
        self.data, self.is_new, self.load_health, self.load_error = snapshot

    # ---------------------------------------------------------------- recording
    def _apply(self, words, dur, mode, day, hour=None):
        d = self.data
        d["total_words"] += max(0, words)
        d["total_transcripts"] += 1
        bucket = d["days"].setdefault(day, [0, 0])
        bucket[0] += words
        bucket[1] += 1
        mb = d["modes"].setdefault(mode, [0, 0])
        mb[0] += 1
        mb[1] += words
        # Hour-of-day word totals (back-compat: old stats.json has no "hours").
        if hour is not None:
            hrs = d.setdefault("hours", [0] * 24)
            if not isinstance(hrs, list) or len(hrs) != 24:
                hrs = d["hours"] = (list(hrs) + [0] * 24)[:24]
            hrs[hour] += words
        # WPM only from real Text-mode speech (other modes are AI-expanded, so their
        # word count says nothing about speaking speed). Cap at a human ceiling.
        if mode == "text" and dur > 0.4:
            d["spoken_seconds"] += dur
            wpm = words / (dur / 60) if dur > 0 else 0
            if 0 < wpm <= _HUMAN_WPM_CEILING:
                d["best_wpm"] = max(d["best_wpm"], wpm)
                # Seed the EMA from the lifetime average the FIRST time (so existing
                # installs start at a sensible number), then pull it ~25% toward each
                # new sample so it tracks RECENT pace and visibly moves — a lifetime
                # cumulative average barely budges once you have an hour of history.
                if d.get("wpm_ema", 0) <= 0:
                    d["wpm_ema"] = (d["wpm_words"] / (d["wpm_seconds"] / 60)
                                    if d["wpm_seconds"] > 0 else wpm)
                d["wpm_ema"] = round(0.25 * wpm + 0.75 * d["wpm_ema"], 2)
                d["wpm_words"] += words
                d["wpm_seconds"] += dur

    def record(self, words, duration, mode, day=None, hour=None):
        """Record one finished transcript. Call this once per dictation."""
        clean_words = _finite_nonnegative(words or 0, integer=True)
        clean_duration = _finite_nonnegative(duration or 0.0)
        clean_day = _today_str() if day is None else _iso_day(day)
        if hour is None:
            hour = datetime.now().hour
        clean_hour = _finite_nonnegative(hour, integer=True)
        if (clean_words is None or clean_duration is None or clean_day is None
                or clean_day > _today_str()
                or clean_hour is None or clean_hour > 23):
            return False
        with self._lock:
            with exclusive_file_lock(self.path) as acquired:
                if not acquired:
                    return False
                if not self._refresh_for_mutation():
                    return False
                snapshot = self._rollback_snapshot()
                self._apply(
                    clean_words, clean_duration, str(mode or "text"),
                    clean_day, clean_hour)
                self.is_new = False
                if self._save():
                    return True
                self._restore_snapshot(snapshot)
                return False

    def reset(self, scope="all"):
        """Permanently clear statistics (owner 2026-06-29 — ITEM 13). scope:
          "all"       — dictation + Reader activity back to a fresh store
          "dictation" — only the dictation totals / days / modes / hours / wpm
          "reader"    — only the Reader listening stats
        Writes immediately so the cleared state survives a restart. The store
        keeps recording normally afterwards. Coarse feature-adoption counters
        are deliberately preserved because they are product-tip state, not user
        activity statistics."""
        if scope not in ("all", "dictation", "reader"):
            return False
        with self._lock, exclusive_file_lock(self.path) as acquired:
            if not acquired:
                return False
            refreshed = self._refresh_for_mutation()
            if not refreshed and not (scope == "all" and self.load_health == "corrupt"):
                return False
            snapshot = self._rollback_snapshot()
            if self.load_health == "corrupt":
                # A deliberate full reset is the only safe mutation when neither
                # primary nor backup can be read: there is no trustworthy subset
                # to preserve. The corrupt primary was copied aside by _load().
                feature_usage = {}
                self.data = _fresh_data()
                self.data["feature_usage"] = feature_usage
            d = self.data
            if scope in ("all", "dictation"):
                d["total_words"] = 0
                d["total_transcripts"] = 0
                d["spoken_seconds"] = 0.0
                d["best_wpm"] = 0.0
                d["wpm_words"] = 0.0
                d["wpm_seconds"] = 0.0
                d["wpm_ema"] = 0.0
                d["days"] = {}
                d["modes"] = {}
                d["hours"] = [0] * 24
            if scope in ("all", "reader"):
                d["reader_total_seconds"] = 0.0
                d["reader_pages_read"] = 0.0
                d["reader_docs_completed"] = 0
                d["reader_words_read"] = 0
                d["reader_sessions"] = 0
                d["reader_days"] = {}
            self.is_new = False
            if self._save():
                return True
            self._restore_snapshot(snapshot)
            return False

    # ── Feature-adoption counters (drive the usage-aware island tips) ──────

    def bump_feature(self, name, n=1):
        """Increment a coarse adoption counter for `name` (e.g. "deck", "search",
        "quick_paste", "capture", "meeting", "preset_run"). Aggregate count only —
        never any content. Cheap + best-effort; a failure never blocks the action."""
        if not name:
            return
        try:
            with self._lock:
                with exclusive_file_lock(self.path) as acquired:
                    if not acquired:
                        return False
                    if not self._refresh_for_mutation():
                        return False
                    amount = _finite_nonnegative(n, integer=True)
                    if amount is None:
                        return False
                    snapshot = self._rollback_snapshot()
                    fu = self.data.setdefault("feature_usage", {})
                    fu[name] = int(fu.get(name, 0) or 0) + amount
                    if self._save():
                        return True
                    self._restore_snapshot(snapshot)
                    return False
        except Exception:
            return False

    def feature_counts(self):
        """A snapshot {feature_id: count} of adoption counters (for tips gating)."""
        with self._lock:
            return dict(self.data.get("feature_usage") or {})

    # ── Reader stats (reader-redesign milestone) ──────────────────────────

    def record_reader_session(self, words_read, duration_sec, day=None,
                              doc_completed=False):
        """Record one reader listening session. `words_read` is the net words
        advanced during this session (end_pos - start_pos). `duration_sec` is
        the wall-clock listening time. `doc_completed` is True when the user
        reached the end of a document."""
        words = _finite_nonnegative(words_read or 0, integer=True)
        dur = _finite_nonnegative(duration_sec or 0.0)
        ds = _today_str() if day is None else _iso_day(day)
        # A Reader session is actual playback time. Reject zero/non-finite calls
        # at the persistence boundary so they cannot create false sessions/streaks
        # even if a future UI caller forgets the existing sub-second guard.
        if (words is None or dur is None or dur <= 0 or ds is None
                or ds > _today_str()):
            return False
        pages = words / _WORDS_PER_PAGE
        with self._lock:
            with exclusive_file_lock(self.path) as acquired:
                if not acquired:
                    return False
                if not self._refresh_for_mutation():
                    return False
                snapshot = self._rollback_snapshot()
                d = self.data
                d["reader_total_seconds"] += dur
                d["reader_pages_read"] += pages
                d["reader_words_read"] += words
                d["reader_sessions"] += 1
                if doc_completed:
                    d["reader_docs_completed"] += 1
                rd = d.setdefault("reader_days", {})
                rd[ds] = rd.get(ds, 0.0) + dur
                self.is_new = False
                if self._save():
                    return True
                self._restore_snapshot(snapshot)
                return False

    def reader_summary(self):
        """Return the six Reader core metrics. avg_session_length is computed
        from the totals; all other values come straight from the store."""
        with self._lock:
            d = dict(self.data)
        total_sec = d.get("reader_total_seconds", 0.0) or 0.0
        sessions = d.get("reader_sessions", 0) or 0
        avg_sec = (total_sec / sessions) if sessions > 0 else 0.0
        # Adaptive formatting for reading time
        if total_sec < 60:
            reading_time_display = f"{round(total_sec)}s"
        elif total_sec < 3600:
            reading_time_display = f"{round(total_sec / 60)}m"
        else:
            reading_time_display = f"{total_sec / 3600:.1f}h"
        return {
            "total_reading_seconds": round(total_sec, 1),
            "total_reading_display": reading_time_display,
            "pages_read": round(d.get("reader_pages_read", 0.0) or 0.0, 1),
            "docs_completed": int(d.get("reader_docs_completed", 0) or 0),
            "words_read": int(d.get("reader_words_read", 0) or 0),
            "total_sessions": int(sessions),
            "avg_session_sec": round(avg_sec, 1),
            "avg_session_display": (
                f"{round(avg_sec)}s" if avg_sec < 60
                else f"{round(avg_sec / 60)}m" if avg_sec < 3600
                else f"{avg_sec / 3600:.1f}h"
            ),
        }

    def reader_streak(self):
        """(current_reading_streak_days, best_reading_streak_days) — a day with
        ≥1 reading session counts."""
        today = date.today()
        with self._lock:
            active = {
                stamp
                for stamp, seconds in (self.data.get("reader_days") or {}).items()
                if (_iso_day(stamp) is not None
                    and date.fromisoformat(stamp) <= today
                    and _finite_nonnegative(seconds) not in (None, 0))
            }
        if not active:
            return 0, 0
        current, d = 0, today
        if today.strftime("%Y-%m-%d") not in active:
            d = today - timedelta(days=1)
        while d.strftime("%Y-%m-%d") in active:
            current += 1
            d -= timedelta(days=1)
        sorted_days = sorted(active)
        best = run = 1
        for i in range(1, len(sorted_days)):
            try:
                prev = date.fromisoformat(sorted_days[i - 1])
                cur = date.fromisoformat(sorted_days[i])
            except ValueError:
                best = max(best, run)
                run = 1
                continue
            if (cur - prev).days == 1:
                run += 1
            else:
                best = max(best, run)
                run = 1
        return current, max(best, run)

    def seed(self, items, cumulative=None):
        """One-time migration from existing transcript history. `items` are history
        entries (with stamp/words/duration/mode); `cumulative` is the old
        history_cumulative.json totals, used as a floor for the grand totals because
        the history list is capped (so it may undercount lifetime words)."""
        with self._lock, exclusive_file_lock(self.path) as acquired:
            if not acquired or not self._refresh_for_mutation():
                return False
            snapshot = self._rollback_snapshot()
            for e in items:
                stamp = str(e.get("stamp", ""))
                day = (_iso_day(stamp[:10]) if len(stamp) >= 10 else None)
                day = day or _today_str()
                hour = None
                if len(stamp) >= 13:  # "YYYY-MM-DD HH:..."
                    try:
                        hour = int(stamp[11:13])
                    except ValueError:
                        hour = None
                if hour is not None and not 0 <= hour <= 23:
                    hour = None
                words = _finite_nonnegative(e.get("words", 0) or 0, integer=True)
                duration = _finite_nonnegative(e.get("duration", 0.0) or 0.0)
                self._apply(
                    words or 0,
                    duration or 0.0,
                    e.get("mode", "text") or "text",
                    day,
                    hour,
                )
            if cumulative:
                try:
                    self.data["total_words"] = max(
                        self.data["total_words"], int(cumulative.get("total_words", 0) or 0)
                    )
                    self.data["total_transcripts"] = max(
                        self.data["total_transcripts"],
                        int(cumulative.get("total_transcripts", 0) or 0),
                    )
                except (TypeError, ValueError):
                    pass
            self.is_new = False
            if self._save():
                return True
            self._restore_snapshot(snapshot)
            return False

    # ---------------------------------------------------------------- queries
    def summary(self):
        # Snapshot under the lock (like mode_stats/streak): record()->_apply mutates
        # self.data on the dictation thread, so an unlocked read here can tear (totals
        # updated but the day bucket not yet, or a half-applied EMA).
        with self._lock:
            d = dict(self.data)
            days = dict(d.get("days") or {})
        # avg_wpm tracks RECENT pace (EMA); fall back to the lifetime average only for
        # a freshly-migrated store that hasn't recorded a text dictation yet.
        ema = d.get("wpm_ema", 0)
        avg = ema if ema > 0 else (
            d["wpm_words"] / (d["wpm_seconds"] / 60) if d["wpm_seconds"] > 0 else 0)
        today_words = days.get(_today_str(), [0, 0])[0]
        mins = d["total_words"] / _TYPING_WPM
        # Adaptive formatting: <1h shows minutes, >=1h shows hours
        if mins < 60:
            typing_time_display = f"{round(mins)} min"
        else:
            typing_time_display = f"{mins / 60:.1f} h"
        return {
            "total_words": int(d["total_words"]),
            "total_transcripts": int(d["total_transcripts"]),
            "avg_wpm": round(avg),
            "best_wpm": round(d["best_wpm"]),
            "today_words": int(today_words),
            "spoken_minutes": round(d["spoken_seconds"] / 60, 1),
            "typing_minutes_saved": round(mins, 1),
            "typing_hours_saved": round(mins / 60, 1),
            "typing_time_display": typing_time_display,
        }

    def daily_stats(self, days=7):
        # Snapshot the day buckets under the lock; record()->_apply mutates them
        # on the dictation thread (see summary/mode_stats).
        with self._lock:
            day_map = dict(self.data.get("days") or {})
        today = date.today()
        out = []
        for i in range(days - 1, -1, -1):
            ds = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            # Defensive: a legacy / hand-edited stats.json could store a day
            # bucket that isn't a 2-element list — unpacking it directly would
            # raise and take down the whole Stats tab. Coerce safely instead.
            b = day_map.get(ds, [0, 0])
            if isinstance(b, (list, tuple)) and len(b) >= 2:
                wc, tc = b[0], b[1]
            else:
                wc, tc = 0, 0
            out.append((ds, wc, tc))
        return out

    def mode_stats(self):
        with self._lock:
            modes = {key: list(value) if isinstance(value, (list, tuple))
                     else value for key, value in self.data["modes"].items()}
        result = []
        for m, v in modes.items():
            if isinstance(v, (list, tuple)) and len(v) >= 2:
                try:
                    result.append((m, v[0], v[1]))
                except (TypeError, IndexError):
                    continue
        result.sort(key=lambda x: -(x[1] or 0))
        return result

    def time_of_day(self):
        """The 'When you dictate' insight — hour-of-day word totals + the peak
        window. Far more useful than the streak (which is already a stat tile):
        it surfaces a real usage PATTERN (owner v6). Returns
        {"hours":[..24..], "blocks":[morning,afternoon,evening,night],
         "peak_hour":int|None, "peak_label":str, "peak_share":int}."""
        # Copy the hours list under the lock; record()->_apply mutates it on the
        # dictation thread, so reading/iterating it unlocked can tear.
        with self._lock:
            hrs = list(self.data.get("hours") or [0] * 24)
        if not isinstance(hrs, list) or len(hrs) != 24:
            hrs = (list(hrs) + [0] * 24)[:24]
        hrs = [int(x or 0) for x in hrs]
        total = sum(hrs)
        # four human blocks: morning 5–12, afternoon 12–17, evening 17–22, night 22–5
        blocks = [
            sum(hrs[5:12]),                     # morning
            sum(hrs[12:17]),                    # afternoon
            sum(hrs[17:22]),                    # evening
            sum(hrs[22:24]) + sum(hrs[0:5]),    # night
        ]
        if total <= 0:
            return {"hours": hrs, "blocks": blocks, "peak_hour": None,
                    "peak_label": "", "peak_share": 0}

        def _fmt(h):
            ap = "am" if h < 12 else "pm"
            hh = h % 12 or 12
            return f"{hh}{ap}"

        peak = max(range(24), key=lambda h: hrs[h])
        return {
            "hours": hrs,
            "blocks": blocks,
            "peak_hour": peak,
            "peak_label": f"{_fmt(peak)}–{_fmt((peak + 1) % 24)}",
            "peak_share": round(100 * hrs[peak] / total),
        }

    def streak(self):
        """(current_streak_days, best_streak_days) — a day with ≥1 transcript counts."""
        today = date.today()
        with self._lock:
            active = {
                stamp
                for stamp, bucket in self.data["days"].items()
                if (_iso_day(stamp) is not None
                    and date.fromisoformat(stamp) <= today
                    and isinstance(bucket, (list, tuple))
                    and len(bucket) >= 2
                    and _finite_nonnegative(bucket[1]) not in (None, 0))
            }
        if not active:
            return 0, 0
        current, d = 0, today
        if today.strftime("%Y-%m-%d") not in active:
            d = today - timedelta(days=1)
        while d.strftime("%Y-%m-%d") in active:
            current += 1
            d -= timedelta(days=1)
        sorted_days = sorted(active)
        best = run = 1
        for i in range(1, len(sorted_days)):
            try:
                prev = date.fromisoformat(sorted_days[i - 1])
                cur = date.fromisoformat(sorted_days[i])
            except ValueError:
                best = max(best, run)
                run = 1
                continue
            if (cur - prev).days == 1:
                run += 1
            else:
                best = max(best, run)
                run = 1
        return current, max(best, run)

    def dashboard_snapshot(self, days=98):
        """One internally consistent Stats read for the Web dashboard.

        The Web UI used to assemble one page from several bridge calls. A write
        between those calls could combine old totals with new charts. Holding the
        re-entrant lock across this snapshot makes every dictation and Reader
        value describe the same stored generation.
        """
        requested = _finite_nonnegative(days, integer=True)
        requested = max(1, min(366, requested or 98))
        with self._lock:
            reader = self.reader_summary()
            reader_current, reader_best = self.reader_streak()
            reader["current_streak"] = reader_current
            reader["best_streak"] = reader_best
            current, best = self.streak()
            return {
                "health": self.load_health,
                "summary": self.summary(),
                "current_streak": current,
                "best_streak": best,
                "daily": self.daily_stats(requested),
                "modes": self.mode_stats(),
                "time_of_day": self.time_of_day(),
                "reader": reader,
                "all_days": copy.deepcopy(self.data.get("days") or {}),
            }
