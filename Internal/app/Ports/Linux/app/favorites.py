"""Favourites for the History deck (the ctrl+alt+d window).

Starred items — transcripts, clipboard snippets, prompts — pinned to the top
of the Deck. Favourites are COPIES: the capped history/clipboard stores evict
old entries, so a favourite survives independently of its origin — up to the
cap. The list itself is capped at MAX_FAVS (100): once full, starring a new
item silently drops the OLDEST favourite (so it is not literally "forever").

Stored in favorites.json (atomic writes), capped at MAX_FAVS (oldest dropped).
Identity is the exact text (hash) — starring the same text twice is a no-op.
"""

import hashlib
import json
import os
import threading
import time

import branding
from storage_lock import exclusive_file_lock

FAVORITES_PATH = os.path.join(branding.DATA_DIR, "favorites.json")
MAX_FAVS = 100


def _key(text):
    t = (text or "").strip()
    if not t:
        return None
    return hashlib.sha256(t.encode("utf-8", errors="replace")).hexdigest()


class Favorites:
    def __init__(self, path=None):
        self.path = path or FAVORITES_PATH
        self.items = []  # [{"key","text","source","time","added"}] oldest→newest
        self._lock = threading.RLock()
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.items = [
                    e for e in data
                    if isinstance(e, dict) and e.get("text") and e.get("key")
                ][-MAX_FAVS:]
        except FileNotFoundError:
            self.items = []
        except Exception as e:
            print("favorites load error:", e)

    def _save(self):
        try:
            parent = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(parent, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.items, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            branding.protect_private_path(self.path)
            return True
        except Exception as e:
            print("favorites save error:", e)
            try:
                os.remove(self.path + ".tmp")
            except OSError:
                pass
            return False

    def is_fav(self, text):
        k = _key(text)
        if not k:
            return False
        with self._lock:
            # The controller and web window are separate long-lived processes.
            # Atomic replacement makes an unlocked read safe, and reloading here
            # prevents one process from presenting a stale favourite state.
            self._load()
            return any(e["key"] == k for e in self.items)

    def add(self, text, source="clipboard", when="", mode=""):
        """Star `text`. Returns True if newly added (False = already there).
        `mode` is the original Smart Mode (email/prompt/list/…) so a favourite
        keeps its origin identity instead of collapsing to a generic transcript."""
        k = _key(text)
        if not k:
            return False
        with self._lock, exclusive_file_lock(self.path) as acquired:
            if not acquired:
                return False
            self._load()
            if any(e["key"] == k for e in self.items):
                return False
            previous = list(self.items)
            self.items.append({
                "key": k, "text": (text or "").strip(), "source": source,
                "mode": mode or "",
                "time": when or "",
                "added": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            self.items = self.items[-MAX_FAVS:]
            if self._save():
                return True
            self.items = previous
            return False

    def remove(self, text):
        """Un-star `text`. Returns True if something was removed."""
        k = _key(text)
        if not k:
            return False
        with self._lock, exclusive_file_lock(self.path) as acquired:
            if not acquired:
                return False
            self._load()
            before = len(self.items)
            previous = list(self.items)
            self.items = [e for e in self.items if e["key"] != k]
            if len(self.items) != before:
                if self._save():
                    return True
                self.items = previous
            return False

    def toggle(self, text, source="clipboard", when="", mode=""):
        """Star/un-star.

        Returns the new state (True = starred, False = unstarred), or ``None``
        when persistence failed.  False cannot represent a failed unstar because
        callers need to distinguish an honest disk error from the requested state.
        """
        with self._lock, exclusive_file_lock(self.path) as acquired:
            if not acquired:
                return False
            self._load()
            k = _key(text)
            if not k:
                return False
            if any(e["key"] == k for e in self.items):
                previous = list(self.items)
                self.items = [e for e in self.items if e["key"] != k]
                if self._save():
                    return False
                self.items = previous
                return None
            previous = list(self.items)
            self.items.append({
                "key": k, "text": (text or "").strip(), "source": source,
                "mode": mode or "",
                "time": when or "",
                "added": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            self.items = self.items[-MAX_FAVS:]
            if self._save():
                return True
            self.items = previous
            return None

    def recent(self, n=MAX_FAVS):
        """Newest-first list of favourite entries."""
        with self._lock:
            self._load()
            items = list(self.items)
        if n <= 0:
            return []
        return items[-n:][::-1]
