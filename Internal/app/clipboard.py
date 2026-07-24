#!/usr/bin/env python3
"""Clipboard manager — Mumble remembers the last N things you copy, text OR images,
so it doubles as a clipboard history. Monitors the system clipboard on a background
thread; text goes to clipboard.json, images are saved as PNGs alongside it."""

import hashlib
import json
import os
import threading
import time
from collections import deque
from datetime import datetime

import pyperclip
from PIL import Image, ImageGrab

from storage_lock import exclusive_file_lock


def _clipboard_seq():
    """A cheap clipboard 'change counter' so the watcher can skip the whole poll
    (text read + image grab) when nothing has changed.

      • Windows: GetClipboardSequenceNumber — a kernel DWORD that bumps on every
        clipboard change.
      • macOS:   NSPasteboard.generalPasteboard().changeCount() — increments on
        every pasteboard write (needs AppKit/PyObjC, which pywebview pulls in).

    Returns None where unavailable (Linux/X11, no AppKit, or a 0/failure result)
    so the caller falls back to the throttled always-poll path."""
    # Windows — ctypes.windll only exists there; raises elsewhere (caught below).
    try:
        import ctypes
        n = int(ctypes.windll.user32.GetClipboardSequenceNumber())
        return n or None   # 0 == failure / no WINSTA access → always-poll fallback
    except Exception:
        pass
    # macOS — NSPasteboard changeCount. +1 so a genuine 0 (nothing copied yet)
    # isn't mistaken for "unavailable"; the value only needs to be stable + nonzero.
    import sys
    if sys.platform == "darwin":
        try:
            from AppKit import NSPasteboard
            return int(NSPasteboard.generalPasteboard().changeCount()) + 1
        except Exception:
            return None
    return None


class Clipboard:
    def __init__(self, path, maxlen=25, img_dir=None):
        self.path = path
        self.maxlen = maxlen
        self.img_dir = img_dir or os.path.join(os.path.dirname(path), "clip_images")
        self.items = deque(maxlen=maxlen)
        self._disk_sig = None
        # Guards every mutation of `items` (the watcher thread captures while the
        # UI thread reads/deletes on the no-WebView2 fallback). Re-entrant so a
        # mutator can call _sync_from_disk()/_save() while already holding it.
        self._lock = threading.RLock()
        self._last_text = ""
        self._last_img = None
        self._paused = False
        self._running = False
        # Each monitoring generation owns its own stop event. A quick Settings
        # off-to-on toggle can otherwise leave both old and new loops running.
        self._state_lock = threading.Lock()
        self._stop_event = None
        self._thread = None
        # Optional hook fired after a NEW capture lands (text or image) — the
        # controller uses it to push a live refresh to the web window.
        self.on_change = None
        # Hashes of Mumble's OWN pasted output, so the "context" keyword can exclude
        # them (owner decision: Mumble must never feed its own results back in).
        self._own_hashes = deque(maxlen=120)
        # Image-clipboard throttling (perf: VAL-PERF-006 — keep clipboard CPU < 0.5%).
        # ImageGrab.grabclipboard() is a real Win32 bitmap grab — the most expensive
        # call in the loop. Throttle it to every 8th text-poll cycle (~5.6s) so
        # non-image clipboard changes don't pay a GDI tax every 0.7s. Text-clipboard
        # history is unaffected and still runs each cycle.
        self._img_every = 8   # ~ every 5.6s at the 0.7s loop interval
        self._img_tick = 0
        # Text-clipboard throttle for platforms WITHOUT a change counter (Linux/
        # X11): pyperclip.paste() there forks an xclip/xsel/wl-paste subprocess,
        # so an idle clipboard would spawn a process ~every 0.7s. Read every 3rd
        # cycle (~2.1s) instead — clipboard *history* doesn't need sub-second
        # latency. Where a change counter exists (Windows/macOS) the idle-skip
        # already gates this, so the text read still runs every change.
        self._text_every = 3   # ~ every 2.1s at the 0.7s loop interval
        self._text_tick = 0
        try:
            os.makedirs(self.img_dir, exist_ok=True)
        except OSError:
            pass
        self._load()

    def _managed_image_path(self, path):
        """Return the resolved path only when it stays inside ``img_dir``."""
        if not isinstance(path, str) or not path:
            return None
        try:
            root = os.path.normcase(os.path.realpath(self.img_dir))
            candidate = os.path.normcase(os.path.realpath(path))
            if os.path.commonpath([root, candidate]) != root:
                return None
            return candidate
        except (OSError, ValueError, TypeError):
            return None

    def _delete_image_file(self, path):
        """Delete only a Clipboard-managed image, never an arbitrary JSON path."""
        managed = self._managed_image_path(path)
        if not managed:
            return False
        try:
            os.remove(managed)
            return True
        except OSError:
            return False

    def _read_items(self):
        """Parse clipboard.json into a fresh deque. Raises on read/parse failure
        so callers can keep their existing in-memory list."""
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            data = []
        out = deque(maxlen=self.maxlen)
        for e in data[-self.maxlen :]:
            if not isinstance(e, dict):
                continue
            e.setdefault("type", "text")
            if e["type"] == "text" and "text" in e:
                out.append(e)
            elif e["type"] == "image":
                managed = self._managed_image_path(e.get("path"))
                if managed and os.path.exists(managed):
                    e["path"] = managed
                    out.append(e)
        return out

    def _load(self):
        try:
            self.items = self._read_items()
            self._disk_sig = self._file_signature()
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, TypeError):
            pass

    def _file_signature(self):
        try:
            stat = os.stat(self.path)
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    def _sync_from_disk(self):
        """Reconcile with disk before a capture so a clip deleted in the webui
        window isn't resurrected by writing back our stale list (the resurrection
        bug — the controller never reloaded). Keeps the current list on any read
        failure (atomic os.replace → a successful read is always a whole file)."""
        signature = self._file_signature()
        if signature == self._disk_sig:
            return
        try:
            self.items = self._read_items()
            self._disk_sig = signature
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, TypeError):
            pass

    def start(self):
        with self._state_lock:
            if self._running:
                return  # already monitoring — never start a duplicate watcher thread
            self._running = True
            stop_event = threading.Event()
            self._stop_event = stop_event
        try:
            self._last_text = pyperclip.paste() or ""
        except Exception:
            self._last_text = ""
        thread = threading.Thread(target=self._loop, args=(stop_event,), daemon=True)
        with self._state_lock:
            self._thread = thread
        thread.start()

    def stop(self):
        """Stop monitoring. The daemon _loop checks `self._running` each cycle and
        exits within ~0.7s. Lets the Settings 'clipboard history' toggle take
        effect live (start/stop) instead of only on the next app launch."""
        with self._state_lock:
            self._running = False
            stop_event = self._stop_event
        if stop_event is not None:
            stop_event.set()

    def pause(self):
        self._paused = True
        # Refresh last_text AND last_img so we don't re-capture them on resume
        try:
            self._last_text = pyperclip.paste() or ""
        except Exception:
            pass
        try:
            img = ImageGrab.grabclipboard()
            if isinstance(img, Image.Image):
                self._last_img = self._img_hash(img)
            else:
                self._last_img = None
        except Exception:
            self._last_img = None

    def resume(self, skip_current=False):
        """Resume monitoring. If skip_current=True (after a paste op), don't
        re-read the clipboard — avoids capturing Mumble's own output as context."""
        if not skip_current:
            try:
                self._last_text = pyperclip.paste() or ""
            except Exception:
                pass
        self._paused = False

    def _loop(self, stop_event):
        while self._running and not stop_event.wait(0.7):
            try:
                if self._paused:
                    continue
                # Skip the whole poll when the clipboard provably hasn't changed
                # since last cycle (owner v7 idle-cost fix). The old loop ran a
                # GDI `ImageGrab.grabclipboard()` — a real Win32 clipboard-open +
                # bitmap grab — every 0.7s forever, even sitting idle; that was the
                # single most expensive idle poll in the app. The sequence number
                # is a cheap kernel-counter compare; when it's unchanged neither
                # text nor image changed, so we skip both reads. (None = the API is
                # unavailable → fall through to the original always-poll path.)
                seq = _clipboard_seq()
                have_seq = seq is not None
                previous_seq = getattr(self, "_last_seq", None)
                if have_seq:
                    if seq == previous_seq:
                        continue
                    self._last_seq = seq
                # Text poll. With a change counter we only reach here when the
                # clipboard changed, so read every time. Without one (Linux/X11,
                # where pyperclip forks a subprocess), throttle to every Nth cycle
                # so an idle clipboard doesn't fork a process ~every 0.7s.
                self._text_tick += 1
                if have_seq or (self._text_tick % self._text_every) == 0:
                    try:
                        cur = pyperclip.paste()
                    except Exception:
                        cur = None
                    if cur and cur != self._last_text:
                        stored = self._add_text(cur) if cur.strip() else False
                        if stored is None:
                            # Retry this unchanged sequence after a transient
                            # lock/save failure; advancing first lost the copy.
                            self._last_seq = previous_seq
                        else:
                            self._last_text = cur
                            if stored:
                                self._fire_change()
                # Image-clipboard poll. ImageGrab.grabclipboard() is a real
                # Win32 clipboard-open + bitmap grab — the most expensive call in
                # the loop, so it's throttled on the NO-seq fallback path (Linux/
                # X11) to every 8th cycle (~5.6s) to avoid a GDI tax on idle.
                #
                # BUT when a sequence counter is available (Windows) we ONLY reach
                # here on an actual clipboard change, and that change shows up for
                # a single cycle. The old unconditional throttle dropped almost
                # every image copy: the change-cycle rarely lined up with the 8th
                # tick, so it `continue`d past the grab, and the next cycle the
                # seq was unchanged and bailed earlier — the image was never
                # captured. With a seq counter, always grab on a detected change.
                self._img_tick += 1
                if not have_seq and (self._img_tick % self._img_every) != 0:
                    continue
                try:
                    grabbed = ImageGrab.grabclipboard()
                except Exception:
                    grabbed = None
                img = None
                if isinstance(grabbed, Image.Image):
                    img = grabbed
                elif isinstance(grabbed, list) and grabbed:
                    # Windows returns a LIST of file paths (not an Image) when an
                    # image FILE is copied — e.g. from Explorer or "Copy image".
                    # That case used to be ignored, so copying a file looked like
                    # "image copy doesn't work". Load the first image file here.
                    _IMG_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".gif",
                                ".webp", ".tif", ".tiff")
                    for p in grabbed:
                        try:
                            if isinstance(p, str) and p.lower().endswith(_IMG_EXT) \
                                    and os.path.exists(p):
                                with Image.open(p) as source:
                                    img = source.copy()
                                    img.load()
                                break
                        except Exception:
                            continue
                if img is not None:
                    h = self._img_hash(img)
                    if h and h != self._last_img:
                        stored = self._add_image(img, h)
                        if stored is None:
                            self._last_seq = previous_seq
                        else:
                            self._last_img = h
                        if stored:
                            self._fire_change()
            except Exception as e:
                # Log but don't crash — a transient clipboard read failure must
                # never kill the monitor thread silently.
                print(f"clipboard monitor error ({type(e).__name__}): {e}")
                time.sleep(1.0)

    def _fire_change(self):
        cb = self.on_change
        if cb:
            try:
                cb()
            except Exception:
                pass

    @staticmethod
    def _img_hash(img):
        try:
            # Hash the real pixels and dimensions. The former 48x48 thumbnail
            # hash treated distinct screenshots as duplicates whenever their
            # small differences disappeared during downsampling.
            rgb = img.convert("RGB")
            digest = hashlib.sha256()
            digest.update(rgb.width.to_bytes(8, "big"))
            digest.update(rgb.height.to_bytes(8, "big"))
            digest.update(rgb.tobytes())
            return digest.hexdigest()
        except Exception:
            return None

    def _evicted_image_path_if_full(self):
        """Return the image path that the next bounded append will evict.

        File deletion happens only after the updated metadata is durable, so a
        failed JSON save can never leave the old entry pointing at a missing PNG.
        """
        if len(self.items) >= self.maxlen and self.items:
            old = self.items[0]
            if old.get("type") == "image" and old.get("path"):
                return old["path"]
        return None

    def _delete_unreferenced_image(self, path):
        managed = self._managed_image_path(path)
        if not managed:
            return False
        for entry in self.items:
            if self._managed_image_path(entry.get("path")) == managed:
                return False
        return self._delete_image_file(managed)

    @staticmethod
    def _now():
        n = datetime.now()
        return n.strftime("%H:%M"), n.strftime("%Y-%m-%d %H:%M:%S")

    def _add_text(self, text):
        with self._lock, exclusive_file_lock(self.path) as acquired:
            if not acquired:
                return None
            self._sync_from_disk()  # pick up any webui-side deletions first
            previous_items = deque(self.items, maxlen=self.maxlen)
            # Never capture (or classify into the conversation store) Mumble's
            # OWN pasted output. pause()/resume() usually prevents this, but if
            # the timing slips, the own-output would otherwise land in clipboard
            # history AND be fed back as context — the exact loop the design
            # forbids. is_own() compares stripped-text hashes, matching mark_own.
            if self.is_own(text):
                return False
            if (
                self.items
                and self.items[-1].get("type") == "text"
                and self.items[-1].get("text") == text
            ):
                return False
            for e in list(self.items):
                if e.get("type") == "text" and e.get("text") == text:
                    self.items.remove(e)
                    break
            evicted_path = self._evicted_image_path_if_full()
            t, stamp = self._now()
            self.items.append(
                {"type": "text", "time": t, "stamp": stamp, "text": text}
            )
            if not self._save():
                self.items = previous_items
                return None
            self._delete_unreferenced_image(evicted_path)
            return True

    def _add_image(self, img, h):
        with self._lock, exclusive_file_lock(self.path) as acquired:
            if not acquired:
                return None
            self._sync_from_disk()  # pick up any webui-side deletions first
            previous_items = deque(self.items, maxlen=self.maxlen)
            for e in list(self.items):
                if e.get("type") == "image" and e.get("hash") == h:
                    # The full hash maps to the same PNG; move the metadata entry
                    # to newest position without deleting the file underneath it.
                    self.items.remove(e)
                    break
            evicted_path = self._evicted_image_path_if_full()
            # Use the FULL hash in the filename. Truncating to 16 hex chars meant
            # two different images whose hashes shared a 16-char prefix wrote to
            # the same PNG — the second save overwrote the first while both
            # entries remained, leaving one pointing at the wrong image.
            fpath = os.path.join(self.img_dir, h + ".png")
            existed_before = os.path.exists(fpath)
            try:
                img.convert("RGBA").save(fpath, "PNG")
            except Exception as e:
                print("clip image save error:", e)
                self.items = previous_items
                return None
            t, stamp = self._now()
            self.items.append(
                {
                    "type": "image",
                    "time": t,
                    "stamp": stamp,
                    "path": fpath,
                    "hash": h,
                    "size": f"{img.width}×{img.height}",
                }
            )
            if not self._save():
                self.items = previous_items
                if not existed_before:
                    self._delete_image_file(fpath)
                return None
            self._delete_unreferenced_image(evicted_path)
            return True

    @staticmethod
    def _text_hash(text):
        t = (text or "").strip()
        if not t:
            return None
        return hashlib.sha256(t.encode("utf-8", errors="replace")).hexdigest()

    def mark_own(self, text):
        """Record `text` as Mumble's own output so it's excluded from context retrieval."""
        h = self._text_hash(text)
        if h:
            self._own_hashes.append(h)

    def is_own(self, text):
        """True if `text` matches something Mumble itself recently pasted."""
        h = self._text_hash(text)
        return bool(h) and h in self._own_hashes

    def ensure_capacity(self, n):
        """Grow (never shrink) the retained history so large 'context N' has enough to
        find N qualifying items after skipping Mumble's own + duplicate entries."""
        n = int(n or 0)
        with self._lock:
            if n > self.maxlen:
                self.maxlen = n
                self.items = deque(self.items, maxlen=n)

    def recent(self, n=25):
        with self._lock:
            # See History.recent: the Web UI and controller have independent
            # instances, so every action-facing read begins from disk truth.
            self._sync_from_disk()
            items = list(self.items)
        if n <= 0:
            return []
        return items[-n:][::-1]

    def resize(self, maxlen):
        """Apply a retention-limit change and clean only durably evicted images."""
        try:
            maxlen = max(1, min(5000, int(maxlen)))
        except (TypeError, ValueError, OverflowError):
            return False
        with self._lock, exclusive_file_lock(self.path) as acquired:
            if not acquired:
                return False
            self._sync_from_disk()
            old_maxlen = self.maxlen
            previous_items = self.items
            all_items = list(self.items)
            kept = all_items[-maxlen:]
            dropped = all_items[:-maxlen] if len(all_items) > maxlen else []
            self.maxlen = maxlen
            self.items = deque(kept, maxlen=maxlen)
            if not self._save():
                self.maxlen = old_maxlen
                self.items = previous_items
                return False
            for entry in dropped:
                if entry.get("type") == "image" and entry.get("path"):
                    self._delete_unreferenced_image(entry["path"])
            return True

    def clear(self):
        with self._lock, exclusive_file_lock(self.path) as acquired:
            if not acquired:
                return False
            self._sync_from_disk()
            previous_items = deque(self.items, maxlen=self.maxlen)
            self.items.clear()
            if not self._save():
                self.items = previous_items
                return False
            for entry in previous_items:
                if entry.get("type") == "image" and entry.get("path"):
                    self._delete_unreferenced_image(entry["path"])
            return True

    def delete_index(self, i):
        """Delete the item at index i in recent() (newest-first) order."""
        with self._lock, exclusive_file_lock(self.path) as acquired:
            if not acquired:
                return False
            self._sync_from_disk()
            items = list(self.items)
            j = len(items) - 1 - i
            if 0 <= j < len(items):
                entry = items[j]
                del items[j]
                previous_items = self.items
                self.items = deque(items, maxlen=self.maxlen)
                if not self._save():
                    self.items = previous_items
                    return False
                if entry.get("type") == "image" and entry.get("path"):
                    self._delete_unreferenced_image(entry["path"])
                return True
            return False

    def delete_match(self, stamp, text=None, image_hash=None):
        """Delete by STABLE identity (stamp [+ text]) instead of array index — a
        stale index removed the wrong clip when the list shifted under it. Reloads
        from disk first (cross-process truth). Images prefer their content hash,
        avoiding collisions when two captures share a one-second stamp. Returns
        True if one was removed."""
        with self._lock, exclusive_file_lock(self.path) as acquired:
            if not acquired:
                return False
            self._sync_from_disk()
            items = list(self.items)
            target = None
            for e in reversed(items):  # newest-first, matching the UI order
                if image_hash:
                    if str(e.get("hash", "")) == str(image_hash):
                        target = e
                        break
                    continue
                if str(e.get("stamp", "")) != str(stamp):
                    continue
                if text and str(e.get("text", "")) != str(text):
                    continue
                target = e
                break
            if target is None:
                return False
            items.remove(target)
            previous_items = self.items
            self.items = deque(items, maxlen=self.maxlen)
            if not self._save():
                self.items = previous_items
                return False
            if target.get("type") == "image" and target.get("path"):
                self._delete_unreferenced_image(target["path"])
            return True

    def _save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(list(self.items), f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            self._disk_sig = self._file_signature()
            return True
        except OSError as e:
            print(f"clipboard save error ({type(e).__name__}): {e}")
            try:
                os.remove(tmp)
            except OSError:
                pass
            return False
