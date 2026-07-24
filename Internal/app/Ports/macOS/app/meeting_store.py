"""Meeting store — saved meeting transcripts with speaker diarisation.

One JSON file in the data folder; each entry holds the full structured transcript
(audio path, speaker-labeled segments, speaker identities, AI summary, action
items). Parallel to reader_store.py.

Entry shape:
{
  id: str,              # UUID
  title: str,           # user-set or auto (date + duration)
  created: float,       # epoch
  duration_sec: float,
  audio_path: str,      # relative path to WAV in data dir
  capture_warning: str | null, # durable early-stop/integrity warning
  processing_mode: str, # "lightweight" or "deep"
  segments: [{          # the structured transcript
    speaker: str,       # "Speaker 1", "Speaker 2", …
    start_sec: float,
    end_sec: float,
    text: str,
    confidence: float
  }],
  speakers: [{          # identified speakers
    label: str,         # "Speaker 1"
    name: str | null,   # user-assigned name ("Alice")
    color: str,         # accent colour from palette
  }],
  summary: str | null,  # AI-generated meeting summary
  action_items: [str],  # extracted action items
  key_decisions: [str], # extracted key decisions
  open_questions: [str],# extracted open questions
  starred: bool,
  tags: [str],
}

Owner 2026-06-29 — meeting-mode milestone.
"""

import json
import glob
import os
import shutil
import threading
import time
import uuid

import branding
from storage_lock import exclusive_file_lock

PATH = os.path.join(branding.DATA_DIR, "meetings.json")
# Meetings are user-owned records.  Never silently prune transcript/audio data;
# retention lasts until the user explicitly deletes a meeting.
CAP = None
# Meeting records can contain long transcripts, and the durable write includes
# an fsync plus a backup generation. Give a simultaneous controller/web writer
# enough time to finish instead of reporting a false save failure after 3s.
WRITE_LOCK_TIMEOUT = 10.0

_LOCK = threading.RLock()
_LAST_LOAD_HEALTH = "unknown"

# Speaker accent palette — distinct, accessible colours for up to 8 speakers.
SPEAKER_COLORS = [
    "#D4AF37",  # gold
    "#5AA9E6",  # blue
    "#46C9A8",  # teal
    "#E8825A",  # coral
    "#A855F7",  # purple
    "#E0A92E",  # amber
    "#D86E9A",  # rose
    "#7C91B2",  # steel
]


def _read_store(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("meeting store root is not a list")
    return [row for row in data if isinstance(row, dict)]


def _load():
    """Read the store, recovering the last good generation when possible.

    A damaged primary must never become an innocent-looking empty list that the
    next mutation overwrites. Keep a prior atomic generation at ``.bak`` and,
    if neither generation parses, move the damaged primary aside before
    returning an empty store so its original bytes remain recoverable.
    """
    global _LAST_LOAD_HEALTH
    with _LOCK:
        try:
            rows = _read_store(PATH)
            _LAST_LOAD_HEALTH = "ok"
            return rows
        except FileNotFoundError:
            backup = PATH + ".bak"
            try:
                rows = _read_store(backup)
            except FileNotFoundError:
                _LAST_LOAD_HEALTH = "missing"
                return []
            except Exception as backup_error:
                _LAST_LOAD_HEALTH = "corrupt"
                print("meeting store backup load error:", backup_error)
                return []
            try:
                shutil.copy2(backup, PATH)
                branding.protect_private_path(PATH)
                print("meeting store restored missing primary from backup")
            except OSError as restore_error:
                print("meeting store restore error:", restore_error)
            _LAST_LOAD_HEALTH = "recovered"
            return rows
        except Exception as e:
            print("meeting store load error:", e)
            backup = PATH + ".bak"
            try:
                rows = _read_store(backup)
            except Exception as backup_error:
                if os.path.exists(PATH):
                    corrupt = PATH + ".corrupt-" + uuid.uuid4().hex[:12]
                    try:
                        os.replace(PATH, corrupt)
                    except OSError:
                        pass
                if not isinstance(backup_error, FileNotFoundError):
                    print("meeting store backup load error:", backup_error)
                _LAST_LOAD_HEALTH = "corrupt"
                return []
            try:
                corrupt = PATH + ".corrupt-" + uuid.uuid4().hex[:12]
                os.replace(PATH, corrupt)
                shutil.copy2(backup, PATH)
                branding.protect_private_path(PATH)
                print("meeting store recovered from backup")
            except OSError as restore_error:
                print("meeting store restore error:", restore_error)
            _LAST_LOAD_HEALTH = "recovered"
            return rows


def _save(rows):
    def _created(row):
        try:
            return float(row.get("created", 0))
        except (TypeError, ValueError, OverflowError, AttributeError):
            return 0.0

    ordered = sorted((r for r in rows if isinstance(r, dict)),
                     key=_created, reverse=True)
    rows = ordered
    try:
        parent = os.path.dirname(PATH)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        backup = PATH + ".bak"
        had_current = os.path.exists(PATH)
        if had_current:
            backup_tmp = backup + ".tmp"
            shutil.copy2(PATH, backup_tmp)
            os.replace(backup_tmp, backup)
        try:
            os.replace(tmp, PATH)
        except Exception:
            if had_current and os.path.exists(backup) and not os.path.exists(PATH):
                shutil.copy2(backup, PATH)
            raise
        branding.protect_private_path(PATH)
        return True
    except Exception as e:
        print(f"meeting store save error ({type(e).__name__}): {e}")
        try:
            os.remove(PATH + ".tmp")
        except OSError:
            pass
        try:
            os.remove(PATH + ".bak.tmp")
        except OSError:
            pass
        return False


# ── Public API ──────────────────────────────────────────────────────────────


def list_meetings():
    """Return meeting metadata (without full segments), newest first."""
    rows = _load()
    out = []
    for r in rows:
        segments = r.get("segments") or []
        speakers = r.get("speakers") or []
        out.append({
            "id": r.get("id"),
            "title": r.get("title", "Untitled Meeting"),
            "created": r.get("created", 0),
            "duration_sec": r.get("duration_sec", 0),
            "duration_display": _fmt_duration(r.get("duration_sec", 0)),
            "segment_count": len(segments),
            "speaker_count": len(speakers),
            "speakers": [{"label": s.get("label"), "name": s.get("name"),
                          "color": s.get("color")} for s in speakers],
            "has_summary": bool(r.get("summary")),
            "action_item_count": len(r.get("action_items") or []),
            "key_decision_count": len(r.get("key_decisions") or []),
            "open_question_count": len(r.get("open_questions") or []),
            "processing_mode": r.get("processing_mode", "lightweight"),
            "starred": bool(r.get("starred")),
            "tags": r.get("tags") or [],
            "preview": (segments[0].get("text", "") if segments else "")[:120],
            "audio_path": r.get("audio_path"),
            "status": r.get("status", "ready"),
            "error": r.get("error"),
            "capture_warning": r.get("capture_warning"),
        })
    return out


def get_meeting(meeting_id):
    """Full meeting with all segments + speakers."""
    for r in _load():
        if r.get("id") == meeting_id:
            return {
                "id": r.get("id"),
                "title": r.get("title", "Untitled Meeting"),
                "created": r.get("created", 0),
                "duration_sec": r.get("duration_sec", 0),
                "duration_display": _fmt_duration(r.get("duration_sec", 0)),
                "audio_path": r.get("audio_path"),
                "status": r.get("status", "ready"),
                "error": r.get("error"),
                "capture_warning": r.get("capture_warning"),
                "processing_mode": r.get("processing_mode", "lightweight"),
                "segments": r.get("segments") or [],
                "speakers": r.get("speakers") or [],
                "segment_count": len(r.get("segments") or []),
                "speaker_count": len(r.get("speakers") or []),
                "summary": r.get("summary"),
                "action_items": r.get("action_items") or [],
                "key_decisions": r.get("key_decisions") or [],
                "open_questions": r.get("open_questions") or [],
                "starred": bool(r.get("starred")),
                "tags": r.get("tags") or [],
            }
    return None


def save_meeting(title, audio_path, duration_sec, segments, speakers,
                 summary=None, action_items=None,
                 key_decisions=None, open_questions=None,
                 processing_mode="lightweight", status="ready", error=None,
                 capture_warning=None):
    """Persist a new meeting. Returns the meeting id."""
    title = (title or "").strip()
    if not title:
        title = _auto_title(duration_sec)
    mid = uuid.uuid4().hex[:12]
    now = time.time()
    with _LOCK, exclusive_file_lock(
            PATH, timeout=WRITE_LOCK_TIMEOUT) as acquired:
        if not acquired:
            return None
        rows = _load()
        rows.insert(0, {
            "id": mid,
            "title": title,
            "created": now,
            "duration_sec": duration_sec,
            "audio_path": audio_path,
            "processing_mode": processing_mode or "lightweight",
            "status": status or "ready",
            "error": error,
            "capture_warning": capture_warning,
            "segments": segments,
            "speakers": speakers,
            "summary": summary,
            "action_items": action_items or [],
            "key_decisions": key_decisions or [],
            "open_questions": open_questions or [],
            "starred": False,
            "tags": [],
        })
        if not _save(rows):
            return None
    return mid


def update_meeting(meeting_id, **kwargs):
    """Update mutable fields: title, summary, action_items, key_decisions,
    open_questions, processing_mode, starred, tags, speaker names."""
    with _LOCK, exclusive_file_lock(
            PATH, timeout=WRITE_LOCK_TIMEOUT) as acquired:
        if not acquired:
            return False
        rows = _load()
        hit = False
        for r in rows:
            if r.get("id") == meeting_id:
                for k in ("title", "summary", "starred", "tags", "speakers",
                          "action_items", "key_decisions", "open_questions",
                          "processing_mode", "segments", "audio_path", "status",
                          "error", "capture_warning", "duration_sec"):
                    if k in kwargs:
                        r[k] = kwargs[k]
                hit = True
                break
        if hit:
            return _save(rows)
        return False


def rename_speaker(meeting_id, label, name):
    """Assign a human-readable name to a speaker label."""
    with _LOCK, exclusive_file_lock(
            PATH, timeout=WRITE_LOCK_TIMEOUT) as acquired:
        if not acquired:
            return False
        rows = _load()
        hit = False
        for r in rows:
            if r.get("id") == meeting_id:
                for s in (r.get("speakers") or []):
                    if s.get("label") == label:
                        s["name"] = (name or "").strip() or None
                        hit = True
                break
        if hit:
            return _save(rows)
        return False


def set_starred(meeting_id, starred):
    with _LOCK, exclusive_file_lock(
            PATH, timeout=WRITE_LOCK_TIMEOUT) as acquired:
        if not acquired:
            return False
        rows = _load()
        hit = False
        for r in rows:
            if r.get("id") == meeting_id:
                r["starred"] = bool(starred)
                hit = True
                break
        if hit:
            return _save(rows)
        return False


def delete_meeting(meeting_id):
    with _LOCK, exclusive_file_lock(
            PATH, timeout=WRITE_LOCK_TIMEOUT) as acquired:
        if not acquired:
            return False
        existing = _load()
        target = next((r for r in existing if r.get("id") == meeting_id), None)
        if target is None:
            return _LAST_LOAD_HEALTH != "corrupt"
        rows = [r for r in existing if r.get("id") != meeting_id]
        if not _save(rows):
            return False
        # Metadata is the source of truth. Delete it durably first so a later
        # filesystem failure can leave only a harmless orphan, never a meeting
        # record whose audio has already been destroyed.
        _delete_audio_file(target.get("audio_path"), missing_ok=True)
        return True


def list_pending_meetings():
    """Return durable recordings whose transcription was interrupted."""
    return [r for r in _load()
            if r.get("status") in ("processing", "interrupted")
            and r.get("audio_path")]


def cleanup_orphan_audio():
    """Remove unreferenced Mumble meeting WAV files from the private audio dir."""
    audio_dir = _audio_dir()
    if not os.path.isdir(audio_dir):
        return 0
    rows = _load()
    # If metadata is missing/corrupt (or a corrupt snapshot exists), an
    # apparently unreferenced WAV may be the only recoverable copy of a meeting.
    # Cleanup is an optimisation, never a reason to destroy user audio.
    if (_LAST_LOAD_HEALTH not in ("ok", "recovered")
            or glob.glob(PATH + ".corrupt-*")):
        print("meeting audio cleanup skipped: metadata recovery is unresolved")
        return 0
    referenced = {os.path.basename(str(r.get("audio_path") or ""))
                  for r in rows if r.get("audio_path")}
    removed = 0
    for name in os.listdir(audio_dir):
        if (name.startswith("meeting_") and name.endswith(".wav")
                and name not in referenced
                and _delete_audio_file(name, missing_ok=True)):
            removed += 1
    return removed


# ── Helpers ─────────────────────────────────────────────────────────────────


def _audio_dir():
    return os.path.join(branding.DATA_DIR, "meetings_audio")


def _safe_audio_path(filename):
    if not filename or os.path.basename(str(filename)) != str(filename):
        return None
    root = os.path.realpath(_audio_dir())
    path = os.path.realpath(os.path.join(root, str(filename)))
    if path == root or not path.startswith(root + os.sep):
        return None
    return path


def _delete_audio_file(filename, missing_ok=False):
    path = _safe_audio_path(filename)
    if path is None:
        return not filename
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return bool(missing_ok)
    except OSError as e:
        print(f"[meeting_store] audio delete failed: {e}")
        return False


def _auto_title(duration_sec=0):
    """Generate a meeting title from the current date + duration."""
    from datetime import datetime
    now = datetime.now()
    return f"Meeting — {now:%b %d, %Y} · {_fmt_duration(duration_sec)}"


def _fmt_duration(seconds):
    try:
        seconds = float(seconds or 0)
    except (TypeError, ValueError, OverflowError):
        return "0:00"
    if seconds < 0 or seconds != seconds or seconds == float("inf"):
        return "0:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def list_tags():
    """Return all unique tags across meetings, with counts."""
    rows = _load()
    counts = {}
    for r in rows:
        for t in (r.get("tags") or []):
            if not isinstance(t, str):
                continue
            t = t.strip()
            if t:
                counts[t] = counts.get(t, 0) + 1
    return sorted(
        [{"name": k, "count": v} for k, v in counts.items()],
        key=lambda x: x["name"].lower())
