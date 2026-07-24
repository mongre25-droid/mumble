"""Reader library — saved documents + resume position (owner 2026-06-20).

The Reader keeps a small library of documents you've opened so you can come back
and resume exactly where you left off. One JSON file in the data folder; each
entry is {id, title, text, length(words), position(word index), added, opened,
starred(bool), bookmarks:[{pos, label, ts}], collections:[str],
reading_sessions:[{start, end, start_pos, end_pos, duration_sec}]}.

Identity is a content hash, so re-opening the same text updates the existing
entry (and its remembered position, star, and bookmarks) instead of duplicating
it. Collections and reading sessions are preserved across text refreshes.

REDESIGN (owner 2026-06-29): documents now carry optional structured-content
fields: `blocks`, `format`, `source_path`. `blocks` is a list of ContentBlock
dicts (type, text, level, meta) from reader_parser. `format` is the source
format ("markdown", "pdf", "docx", …). Existing plain-text-only docs continue
to work — their `blocks` are None and the Reader renders from `text`.
"""

import hashlib
import json
import math
import os
import shutil
import threading
import time
from datetime import datetime, timezone

import branding

PATH = os.path.join(branding.DATA_DIR, "reader_library.json")
MAX_QUERY_LIMIT = 1000  # response guard only; storage itself is never evicted
SPEECH_TEXT_VERSION = 1

# Mutations are load→modify→save; pywebview dispatches each JS→Python bridge
# call on its own worker thread (and the Reader saves position very often during
# playback), so serialize every read-modify-write to avoid lost/garbled writes.
_LOCK = threading.RLock()


def _int(v, default=0):
    """int(v) that never raises. A hand-edited or legacy library file can carry a
    non-numeric position/length/bookmark-pos; a bare int() there would throw inside
    a lock-free reader and make the WHOLE library list/open fail (the bridge would
    return [] / None — the library appears empty)."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _float(v, default=0.0):
    try:
        value = float(v)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _doc_id(text):
    return hashlib.sha256(
        (text or "").strip().encode("utf-8", "ignore")).hexdigest()[:16]


def _legacy_doc_id(text):
    """Pre-audit SHA-1 id, used only to adopt an existing library row."""
    return hashlib.sha1(
        (text or "").strip().encode("utf-8", "ignore"),
        usedforsecurity=False).hexdigest()[:16]


def _auto_title(text):
    first = ""
    for line in (text or "").strip().splitlines():
        if line.strip():
            first = line.strip()
            break
    if not first:
        return "Untitled"
    return (first[:60] + "…") if len(first) > 60 else first


def _canonical_blocks_text(blocks):
    """Rebuild the exact speech/index text for a structured document.

    Older Reader versions stored display markers (``#``, bullets, quote
    prefixes) in ``text`` while the browser spoke only block text.  Rebuilding
    from blocks keeps legacy documents' positions, percentages, and bookmarks
    on the same word stream the player actually uses.
    """
    if not isinstance(blocks, list) or not blocks:
        return None
    try:
        from reader_parser import ContentBlock, ParsedDocument
        parsed_blocks = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            try:
                parsed_blocks.append(ContentBlock.from_dict(block))
            except (TypeError, ValueError, OverflowError):
                continue
        if not parsed_blocks:
            return None
        text = ParsedDocument(blocks=parsed_blocks).plain_text().strip()
        return text or None
    except Exception:
        # A damaged optional structured payload must not hide the valid legacy
        # plain-text snapshot stored alongside it.
        return None


def _row_text(row):
    stored = row.get("text", "") or ""
    if _int(row.get("speech_text_version")) >= SPEECH_TEXT_VERSION:
        return stored
    return _canonical_blocks_text(row.get("blocks")) or stored


def _row_length(row, text=None):
    # Recompute from the canonical stream rather than trusting a stale or
    # hand-edited length; versioned current rows take the fast stored-text path.
    return len((text if text is not None else _row_text(row)).split())


def _read_library(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("reader library root is not a list")
    return [row for row in data if isinstance(row, dict)]


def _load():
    with _LOCK:
        try:
            return _read_library(PATH)
        except FileNotFoundError:
            backup = PATH + ".bak"
            try:
                rows = _read_library(backup)
                shutil.copy2(backup, PATH)
                branding.protect_private_path(PATH)
                print("reader library restored after interrupted save")
                return rows
            except FileNotFoundError:
                return []
            except Exception as backup_error:
                print("reader library backup load error:", backup_error)
                return []
        except Exception as e:
            print("reader library load error:", e)
            backup = PATH + ".bak"
            try:
                rows = _read_library(backup)
            except Exception as backup_error:
                # Preserve the damaged bytes before a future save creates a new
                # library. Silent [] -> overwrite used to destroy the only copy.
                if os.path.exists(PATH):
                    corrupt = PATH + ".corrupt-" + str(int(time.time() * 1000))
                    try:
                        os.replace(PATH, corrupt)
                    except OSError:
                        pass
                if not isinstance(backup_error, FileNotFoundError):
                    print("reader library backup load error:", backup_error)
                return []
            try:
                corrupt = PATH + ".corrupt-" + str(int(time.time() * 1000))
                os.replace(PATH, corrupt)
                shutil.copy2(backup, PATH)
                branding.protect_private_path(PATH)
                print("reader library recovered from backup")
            except OSError as restore_error:
                print("reader library restore error:", restore_error)
            return rows


def _save(rows):
    # Keep deterministic recency order, but never silently delete old library
    # documents. Retention is a user action (reader_delete), not a save side effect.
    rows = sorted(
        (r for r in rows if isinstance(r, dict)),
        key=lambda r: _float(r.get("opened", 0)), reverse=True)
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
            os.replace(PATH, backup)
        try:
            os.replace(tmp, PATH)
        except Exception:
            if had_current and os.path.exists(backup) and not os.path.exists(PATH):
                os.replace(backup, PATH)
            raise
        branding.protect_private_path(PATH)
        return True
    except Exception as e:
        print(f"reader store save error ({type(e).__name__}): {e}")
        try:
            if os.path.exists(PATH + ".tmp"):
                os.remove(PATH + ".tmp")
        except OSError:
            pass
        return False


def list_docs():
    """Library entries (most-recently-opened first) WITHOUT the full text — just
    metadata + a short preview, so the list stays light."""
    rows = _load()
    rows.sort(key=lambda r: _float(r.get("opened", 0)), reverse=True)
    out = []
    for r in rows:
        text = _row_text(r)
        length = _row_length(r, text)
        pos = max(0, min(_int(r.get("position")), length))
        sessions = [s for s in (r.get("reading_sessions") or [])
                    if isinstance(s, dict)]
        last_read = _float(r.get("last_read", 0)) or max(
            (_float(s.get("start", 0)) for s in sessions), default=0)
        total_seconds = _float(
            r.get("reading_total_seconds"),
            sum(_float(s.get("duration_sec", 0)) for s in sessions))
        session_count = max(
            len(sessions), _int(r.get("reading_session_count"), len(sessions)))
        total_min = round(total_seconds / 60.0, 1)
        out.append({
            "id": r.get("id"),
            "title": r.get("title") or _auto_title(text),
            "preview": text[:160],
            "length": length,
            "position": pos,
            "percent": (round(100.0 * pos / length) if length else 0),
            "opened": r.get("opened", 0),
            "added": r.get("added", 0),
            "starred": bool(r.get("starred")),
            "bookmark_count": len([b for b in (r.get("bookmarks") or [])
                                   if isinstance(b, dict)]),
            "collections": [c for c in (r.get("collections") or [])
                            if isinstance(c, str)],
            "session_count": session_count,
            "total_minutes": total_min,
            "last_read": last_read,
            "format": r.get("format"),         # redesign
            "has_blocks": bool(r.get("blocks")),  # redesign
            "source_path": r.get("source_path"),  # redesign
        })
    return out


def get_doc(doc_id):
    """Full document (incl. text + saved position + collections + bookmarks)
    for opening/resuming.

    Returns `blocks` when the doc was parsed from a structured format (else
    None — the UI renders from `text`). Also returns `format` (e.g. "markdown")
    and `source_path` when available."""
    for r in _load():
        if r.get("id") == doc_id:
            text = _row_text(r)
            length = _row_length(r, text)
            return {
                "id": r.get("id"),
                "title": r.get("title") or _auto_title(r.get("text", "")),
                "text": text,
                "blocks": r.get("blocks"),          # structured blocks (redesign)
                "format": r.get("format"),          # source format (redesign)
                "source_path": r.get("source_path"), # original file path (redesign)
                "position": max(0, min(_int(r.get("position")), length)),
                "starred": bool(r.get("starred")),
                "bookmarks": sorted(
                    (b for b in (r.get("bookmarks") or [])
                     if isinstance(b, dict)),
                    key=lambda b: _int(b.get("pos"))),
                "collections": [c for c in (r.get("collections") or [])
                                if isinstance(c, str)],
                "reading_sessions": [s for s in (r.get("reading_sessions") or [])
                                     if isinstance(s, dict)],
            }
    return None


def save_doc(title, text, blocks=None, fmt=None, source_path=None):
    """Add (or refresh) a document; returns its id. Same text → same entry, so
    the remembered position is preserved when you re-open it.

    `blocks` is an optional list of ContentBlock dicts (from reader_parser).
    `fmt` is the source format string ("markdown", "pdf", "docx", …).
    `source_path` is the original file path (if any).
    """
    text = (text or "").strip()
    if not text:
        return None
    title = (title or "").strip() or _auto_title(text)
    did = _doc_id(text)
    legacy_did = _legacy_doc_id(text)
    now = time.time()
    updated_at = _now_iso()
    with _LOCK:
        rows = _load()
        existing = next(
            (r for r in rows
             if r.get("id") in (did, legacy_did) or _row_text(r) == text),
            None)
        if existing:
            # Transparently strengthen a legacy content id while preserving
            # every mutable field on the same document row.
            existing["id"] = did
            existing["title"] = title
            existing["text"] = text
            existing["length"] = len(text.split())
            existing["speech_text_version"] = SPEECH_TEXT_VERSION
            if blocks is not None:
                existing["blocks"] = blocks
            if fmt:
                existing["format"] = fmt
            if source_path is not None:
                existing["source_path"] = source_path
            # text may have changed length on refresh — keep the remembered
            # position inside the new bounds so resume can't run off the end.
            existing["position"] = max(0, min(_int(existing.get("position")), existing["length"]))
            existing["opened"] = now
            existing["updated_at"] = updated_at
        else:
            entry = {
                "id": did, "title": title, "text": text,
                "length": len(text.split()), "position": 0,
                "speech_text_version": SPEECH_TEXT_VERSION,
                "added": now, "opened": now,
                "updated_at": updated_at,
                "collections": [], "reading_sessions": [],
            }
            if blocks is not None:
                entry["blocks"] = blocks
            if fmt:
                entry["format"] = fmt
            if source_path is not None:
                entry["source_path"] = source_path
            rows.insert(0, entry)
        saved = _save(rows)
    return did if saved else None


def save_doc_parsed(parsed_doc, fmt=None, source_path=None):
    """Save a ParsedDocument (from reader_parser) to the library.
    Returns the doc id."""
    text = parsed_doc.plain_text()
    blocks = [b.as_dict() for b in parsed_doc.blocks]
    return save_doc(parsed_doc.title, text, blocks=blocks,
                    fmt=fmt, source_path=source_path)


def set_position(doc_id, position):
    """Remember the reading position (a word index) so the user can resume."""
    with _LOCK:
        rows = _load()
        hit = False
        for r in rows:
            if r.get("id") == doc_id:
                length = _row_length(r)
                p = max(0, _int(position))
                r["position"] = min(p, length)
                r["length"] = length
                r["opened"] = time.time()
                r["updated_at"] = _now_iso()
                hit = True
                break
        return _save(rows) if hit else False


def set_starred(doc_id, starred):
    """Star/unstar a document — a favourite, surfaced at the top of the library."""
    with _LOCK:
        rows = _load()
        hit = False
        for r in rows:
            if r.get("id") == doc_id:
                r["starred"] = bool(starred)
                r["updated_at"] = _now_iso()
                hit = True
                break
        return _save(rows) if hit else False


def add_bookmark(doc_id, position, label=""):
    """Save a named bookmark at a word index. Re-bookmarking the same position
    updates its label instead of duplicating. Returns the doc's bookmark list
    (sorted by position), or None if the doc is gone."""
    with _LOCK:
        rows = _load()
        for r in rows:
            if r.get("id") == doc_id:
                length = _row_length(r)
                pos = max(0, _int(position))
                pos = min(pos, length)  # never bookmark past the end of the doc
                label = (label or "").strip()[:120]  # cap a stored label
                marks = [b for b in (r.get("bookmarks") or [])
                         if isinstance(b, dict)]
                existing = next(
                    (b for b in marks if _int(b.get("pos")) == pos), None)
                if existing:
                    existing["label"] = label or existing.get("label", "")
                    existing["ts"] = time.time()
                else:
                    marks.append({"pos": pos, "label": label, "ts": time.time()})
                r["bookmarks"] = sorted(marks, key=lambda b: _int(b.get("pos")))
                r["length"] = length
                r["updated_at"] = _now_iso()
                return r["bookmarks"] if _save(rows) else None
        return None


def remove_bookmark(doc_id, position):
    """Drop the bookmark at `position`. Returns the remaining bookmark list."""
    with _LOCK:
        rows = _load()
        for r in rows:
            if r.get("id") == doc_id:
                pos = max(0, _int(position))
                r["bookmarks"] = [
                    b for b in (r.get("bookmarks") or [])
                    if isinstance(b, dict) and _int(b.get("pos")) != pos]
                r["updated_at"] = _now_iso()
                return r["bookmarks"] if _save(rows) else None
        return None


# ── Collections ────────────────────────────────────────────────────────────


def _collections_path():
    # Derive from PATH so portable builds and tests keep the sidecar beside the
    # document library when that library is relocated.
    return os.path.join(os.path.dirname(PATH), "reader_collections.json")


def _load_collection_names():
    try:
        with open(_collections_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        return [name.strip()[:120] for name in data
                if isinstance(name, str) and name.strip()]
    except FileNotFoundError:
        return []
    except Exception as e:
        print("reader collections load error:", e)
        return []


def _save_collection_names(names):
    cleaned = sorted(set(
        name.strip()[:120] for name in names
        if isinstance(name, str) and name.strip()
    ), key=str.casefold)
    path = _collections_path()
    tmp = path + ".tmp"
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        branding.protect_private_path(path)
        return True
    except Exception as e:
        print(f"reader collections save error ({type(e).__name__}): {e}")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def create_collection(collection_name):
    """Persist an empty collection name. Returns True when it was created."""
    collection_name = (collection_name or "").strip()[:120]
    if not collection_name:
        return False
    with _LOCK:
        names = _load_collection_names()
        if collection_name in names:
            return False
        names.append(collection_name)
        return _save_collection_names(names)


def add_to_collection(doc_id, collection_name):
    """Add a document to a named collection. Collections are just labels; a
    document can be in many collections. Returns True if newly added."""
    collection_name = (collection_name or "").strip()[:120]
    if not collection_name:
        return False
    with _LOCK:
        rows = _load()
        hit = False
        for r in rows:
            if r.get("id") == doc_id:
                colls = list(r.get("collections") or [])
                if collection_name not in colls:
                    colls.append(collection_name)
                    r["collections"] = colls
                    r["updated_at"] = _now_iso()
                    hit = True
                break
        if not hit or not _save(rows):
            return False
        names = _load_collection_names()
        if collection_name not in names:
            names.append(collection_name)
            # The document label remains a valid source of truth if this small
            # sidecar write fails, so don't report the membership write as lost.
            _save_collection_names(names)
        return True


def remove_from_collection(doc_id, collection_name):
    """Remove a document from a named collection. Returns True if it was
    removed."""
    collection_name = (collection_name or "").strip()[:120]
    if not collection_name:
        return False
    with _LOCK:
        rows = _load()
        hit = False
        for r in rows:
            if r.get("id") == doc_id:
                colls = list(r.get("collections") or [])
                if collection_name in colls:
                    colls.remove(collection_name)
                    r["collections"] = colls if colls else []  # keep list, never None
                    r["updated_at"] = _now_iso()
                    hit = True
                break
        return _save(rows) if hit else False


def list_collections():
    """Return deduplicated collection names with the count of docs in each,
    sorted alphabetically."""
    rows = _load()
    counts = {name: 0 for name in _load_collection_names()}
    for r in rows:
        for c in (r.get("collections") or []):
            if not isinstance(c, str):
                continue
            c = c.strip()
            if c:
                counts[c] = counts.get(c, 0) + 1
    return sorted(
        [{"name": k, "count": v} for k, v in counts.items()],
        key=lambda x: x["name"].lower())


def list_collection_docs(collection_name, n=50):
    """Return metadata for documents in a collection, newest-opened first."""
    collection_name = (collection_name or "").strip()
    if not collection_name:
        return []
    rows = _load()
    rows.sort(key=lambda r: _float(r.get("opened", 0)), reverse=True)
    limit = max(0, min(_int(n, 50), MAX_QUERY_LIMIT))
    if not limit:
        return []
    out = []
    for r in rows:
        if collection_name in (r.get("collections") or []):
            text = _row_text(r)
            length = _row_length(r, text)
            pos = max(0, min(_int(r.get("position")), length))
            out.append({
                "id": r.get("id"),
                "title": r.get("title") or _auto_title(text),
                "preview": text[:160],
                "length": length,
                "position": pos,
                "percent": (round(100.0 * pos / length) if length else 0),
                "opened": r.get("opened", 0),
                "added": r.get("added", 0),
                "starred": bool(r.get("starred")),
                "bookmark_count": len(r.get("bookmarks") or []),
            })
        if len(out) >= limit:
            break
    return out


def delete_collection(collection_name):
    """Delete a collection (remove the label from every doc). Returns the
    number of docs affected."""
    collection_name = (collection_name or "").strip()
    if not collection_name:
        return 0
    with _LOCK:
        names = _load_collection_names()
        had_name = collection_name in names
        if had_name:
            names.remove(collection_name)
        rows = _load()
        affected = 0
        for r in rows:
            colls = list(r.get("collections") or [])
            if collection_name in colls:
                colls.remove(collection_name)
                r["collections"] = colls if colls else []
                r["updated_at"] = _now_iso()
                affected += 1
        rows_ok = _save(rows) if affected else True
        names_ok = _save_collection_names(names) if had_name else True
        return affected if rows_ok and names_ok else 0


# ── Reading history ────────────────────────────────────────────────────────


def log_reading_session(doc_id, start_pos, end_pos, duration_sec):
    """Record a finished reading session for a document. Sessions are stored
    per-document and capped at 25 per doc (oldest dropped)."""
    duration = max(0.0, _float(duration_sec))
    now = time.time()
    with _LOCK:
        rows = _load()
        hit = False
        for r in rows:
            if r.get("id") == doc_id:
                length = _row_length(r)
                start = max(0, min(_int(start_pos), length))
                end = max(0, min(_int(end_pos), length))
                sessions = [s for s in (r.get("reading_sessions") or [])
                            if isinstance(s, dict)]
                previous_count = _int(
                    r.get("reading_session_count"), len(sessions))
                previous_seconds = _float(
                    r.get("reading_total_seconds"),
                    sum(_float(s.get("duration_sec", 0)) for s in sessions))
                sessions.append({
                    "start": now - duration,
                    "end": now,
                    "start_pos": start,
                    "end_pos": end,
                    "duration_sec": duration,
                })
                # Keep the 25 most recent sessions per doc
                r["reading_sessions"] = sorted(
                    sessions, key=lambda s: _float(s.get("start", 0)),
                    reverse=True)[:25]
                r["reading_session_count"] = previous_count + 1
                r["reading_total_seconds"] = previous_seconds + duration
                r["last_read"] = now
                r["length"] = length
                r["updated_at"] = _now_iso()
                hit = True
                break
        return _save(rows) if hit else False


def list_reading_history(n=100):
    """Return the most recent reading sessions across all documents, newest
    first.  Each entry carries the document id + title, session start/end,
    positions, and duration."""
    rows = _load()
    all_sessions = []
    for r in rows:
        text = _row_text(r)
        length = _row_length(r, text)
        for s in (r.get("reading_sessions") or []):
            if not isinstance(s, dict):
                continue
            all_sessions.append({
                "doc_id": r.get("id"),
                "doc_title": r.get("title") or _auto_title(r.get("text", "")),
                "doc_length": length,
                "start": s.get("start", 0),
                "end": s.get("end", 0),
                "start_pos": _int(s.get("start_pos")),
                "end_pos": _int(s.get("end_pos")),
                "duration_sec": round(_float(s.get("duration_sec", 0)), 1),
            })
    all_sessions.sort(key=lambda s: _float(s.get("start", 0)), reverse=True)
    limit = max(0, min(_int(n, 100), MAX_QUERY_LIMIT))
    return all_sessions[:limit]


def continue_reading_doc():
    """Return metadata for the most-recently-opened document that has not been
    fully read (position > 0 and < length), for the "Continue reading" card.
    Returns None if all docs are fresh or fully finished."""
    rows = _load()
    rows.sort(key=lambda r: _float(r.get("opened", 0)), reverse=True)
    for r in rows:
        text = _row_text(r)
        length = _row_length(r, text)
        pos = max(0, min(_int(r.get("position")), length))
        if pos > 0 and length > 0 and pos < length:
            return {
                "id": r.get("id"),
                "title": r.get("title") or _auto_title(text),
                "length": length,
                "position": pos,
                "percent": round(100.0 * pos / length),
                "opened": r.get("opened", 0),
                "starred": bool(r.get("starred")),
                "bookmarks": r.get("bookmarks") or [],
            }
    return None


def delete_doc(doc_id):
    with _LOCK:
        old_rows = _load()
        rows = [r for r in old_rows if r.get("id") != doc_id]
        if len(rows) == len(old_rows):
            return False
        return _save(rows)
