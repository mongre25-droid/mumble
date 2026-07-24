#!/usr/bin/env python3
"""Clipboard manager — Mumble remembers the last N things you copy, text OR images,
so it doubles as a clipboard history. Monitors the system clipboard on a background
thread; text goes to clipboard.json, images are saved as PNGs alongside it."""

import hashlib
import io
import json
import os
import selectors
import sys
import tempfile
import threading
import time
from collections import deque
from datetime import datetime

import pyperclip
from PIL import Image
from storage_lock import exclusive_file_lock


_MAX_CLIPBOARD_IMAGE_BYTES = 64 * 1024 * 1024
_MAX_CLIPBOARD_IMAGE_PIXELS = 40_000_000
_MAX_CLIPBOARD_TEXT_BYTES = 2 * 1024 * 1024


def _image_from_clipboard_command(command, timeout=2.0):
    """Read one PNG-producing clipboard helper with a hard byte/time bound."""
    import subprocess
    proc = None
    selector = None
    try:
        proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if proc.stdout is None:
            return None
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + max(0.1, float(timeout))
        payload = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("clipboard image helper timed out")
            if not selector.select(remaining):
                raise TimeoutError("clipboard image helper timed out")
            chunk = os.read(proc.stdout.fileno(), min(
                65536, _MAX_CLIPBOARD_IMAGE_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > _MAX_CLIPBOARD_IMAGE_BYTES:
                raise ValueError("clipboard image exceeds the 64 MB safety limit")
        wait_left = max(0.05, deadline - time.monotonic())
        if proc.wait(timeout=wait_left) != 0 or not payload:
            return None
        with Image.open(io.BytesIO(payload)) as source:
            if source.width * source.height > _MAX_CLIPBOARD_IMAGE_PIXELS:
                return None
            image = source.copy()
            image.load()
            return image
    except Exception:
        return None
    finally:
        if selector is not None:
            try:
                selector.close()
            except Exception:
                pass
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=0.5)
            except Exception:
                pass


def _text_from_clipboard_command(command, timeout=2.0):
    """Read a Linux text selection with hard time and memory boundaries."""
    import subprocess
    proc = None
    selector = None
    try:
        proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if proc.stdout is None:
            return None
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + max(0.1, float(timeout))
        payload = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise TimeoutError("clipboard text helper timed out")
            chunk = os.read(proc.stdout.fileno(), min(
                65536, _MAX_CLIPBOARD_TEXT_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > _MAX_CLIPBOARD_TEXT_BYTES:
                raise ValueError("clipboard text exceeds the 2 MB safety limit")
        wait_left = max(0.05, deadline - time.monotonic())
        if proc.wait(timeout=wait_left) != 0:
            return None
        return payload.decode("utf-8", errors="replace")
    except Exception:
        return None
    finally:
        if selector is not None:
            try:
                selector.close()
            except Exception:
                pass
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=0.5)
            except Exception:
                pass


def read_clipboard_text(timeout=2.0):
    """Bounded clipboard text read used by monitoring and capture workflows."""
    if sys.platform.startswith("linux"):
        import shutil as _shutil
        commands = []
        if os.environ.get("WAYLAND_DISPLAY") and _shutil.which("wl-paste"):
            commands.append([_shutil.which("wl-paste"), "--no-newline"])
        if os.environ.get("DISPLAY") and _shutil.which("xclip"):
            commands.append([
                _shutil.which("xclip"), "-selection", "clipboard", "-o"])
        if os.environ.get("DISPLAY") and _shutil.which("xsel"):
            commands.append([
                _shutil.which("xsel"), "--clipboard", "--output"])
        for command in commands:
            value = _text_from_clipboard_command(command, timeout=timeout)
            if value is not None:
                return value
        return None
    try:
        value = pyperclip.paste()
    except Exception:
        return None
    if value is None:
        return ""
    value = str(value)
    if len(value.encode("utf-8", errors="replace")) > _MAX_CLIPBOARD_TEXT_BYTES:
        return None
    return value


def _grab_clipboard_image():
    """Cross-platform clipboard image grab. Returns a PIL Image or None.
    - Windows/macOS: delegates to ImageGrab.grabclipboard()
    - Linux: tries GTK3 Gtk.Clipboard, then falls back to wl-paste (Wayland)."""
    # Windows & macOS — the well-tested Pillow path
    if sys.platform in ("win32", "darwin"):
        try:
            from PIL import ImageGrab
            grabbed = ImageGrab.grabclipboard()
            if isinstance(grabbed, Image.Image):
                return grabbed
            if isinstance(grabbed, list) and grabbed:
                _IMG_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".gif",
                            ".webp", ".tif", ".tiff")
                for p in grabbed:
                    try:
                        if isinstance(p, str) and p.lower().endswith(_IMG_EXT) \
                                and os.path.exists(p):
                            with Image.open(p) as source:
                                img = source.copy()
                                img.load()
                                return img
                    except Exception:
                        continue
            return None
        except Exception:
            return None

    # Linux: prefer native command-line helpers. The monitor runs on a worker
    # thread and GTK calls are main-thread-only; xclip/wl-paste also let us put
    # a strict size/time boundary around an untrusted clipboard owner.
    try:
        import shutil as _shutil
        commands = []
        if os.environ.get("WAYLAND_DISPLAY") and _shutil.which("wl-paste"):
            commands.append([_shutil.which("wl-paste"), "-t", "image/png"])
        if _shutil.which("xclip"):
            commands.append([_shutil.which("xclip"), "-selection", "clipboard",
                             "-t", "image/png", "-o"])
        if not commands and _shutil.which("wl-paste"):
            commands.append([_shutil.which("wl-paste"), "-t", "image/png"])
        for command in commands:
            image = _image_from_clipboard_command(command)
            if image is not None:
                return image
    except Exception:
        pass

    # GTK fallback: marshal the synchronous clipboard read to GTK's main loop
    # when called by the monitor thread. Direct worker-thread GTK access can
    # intermittently deadlock or crash X11/WebKitGTK applications.
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk, Gdk, GLib

        def _read_gtk():
            clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            pb = clip.wait_for_image() if clip is not None else None
            if pb is None:
                return None
            w, h = pb.get_width(), pb.get_height()
            if (w <= 0 or h <= 0
                    or w * h > _MAX_CLIPBOARD_IMAGE_PIXELS):
                return None
            data = bytes(pb.get_pixels())
            if not data:
                return None
            mode = "RGBA" if pb.get_has_alpha() else "RGB"
            return Image.frombytes(
                mode, (w, h), data, "raw", mode, pb.get_rowstride(), 1
            ).convert("RGBA")

        if threading.current_thread() is threading.main_thread():
            return _read_gtk()
        result = {"image": None}
        done = threading.Event()

        def _on_main():
            try:
                result["image"] = _read_gtk()
            except Exception:
                pass
            finally:
                done.set()
            return False

        GLib.idle_add(_on_main)
        if done.wait(2.0):
            return result["image"]
    except Exception:
        pass

    return None


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
        # Guards every mutation of `items` (the watcher thread captures while the
        # UI thread reads/deletes on the no-WebView2 fallback). Re-entrant so a
        # mutator can call _sync_from_disk()/_save() while already holding it.
        self._lock = threading.RLock()
        self._last_text = ""
        self._last_img = None
        self._paused = False
        self._running = False
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
        """Resolve a path only when it remains inside ``clip_images``."""
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
        """Delete only a file owned by this clipboard image store."""
        managed = self._managed_image_path(path)
        if not managed:
            return False
        try:
            os.remove(managed)
            return True
        except FileNotFoundError:
            # The file may already have been removed by another Mumble process.
            # Its intended post-condition is satisfied, so do not resurrect the
            # now-dead database entry during rollback.
            return True
        except OSError:
            return False

    def _purge_image_files(self):
        """Delete every managed clipboard image, including prior orphans."""
        ok = True
        try:
            entries = list(os.scandir(self.img_dir))
        except FileNotFoundError:
            return True
        except OSError:
            return False
        for entry in entries:
            try:
                if entry.is_file(follow_symlinks=False) or entry.is_symlink():
                    if not self._delete_image_file(entry.path):
                        ok = False
            except OSError:
                ok = False
        return ok

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
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, TypeError):
            pass

    def _sync_from_disk(self):
        """Reconcile with disk before a capture so a clip deleted in the webui
        window isn't resurrected by writing back our stale list (the resurrection
        bug — the controller never reloaded). Keeps the current list on any read
        failure (atomic os.replace → a successful read is always a whole file)."""
        try:
            self.items = self._read_items()
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, TypeError):
            pass

    def start(self):
        with self._state_lock:
            if self._running:
                return
            self._running = True
            stop_event = threading.Event()
            self._stop_event = stop_event
        try:
            self._last_text = read_clipboard_text() or ""
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
            thread = self._thread
        if stop_event is not None:
            stop_event.set()
        join = getattr(thread, "join", None)
        if (thread is not None and thread is not threading.current_thread()
                and callable(join)):
            join(timeout=3.0)
        is_alive = getattr(thread, "is_alive", None)
        alive = bool(is_alive()) if callable(is_alive) else False
        with self._state_lock:
            if self._thread is thread and (thread is None or not alive):
                self._thread = None
                self._stop_event = None

    def pause(self):
        self._paused = True
        # Refresh last_text AND last_img so we don't re-capture them on resume
        try:
            self._last_text = read_clipboard_text() or ""
        except Exception:
            pass
        try:
            img = _grab_clipboard_image()
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
                self._last_text = read_clipboard_text() or ""
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
                if have_seq:
                    if seq == getattr(self, "_last_seq", None):
                        continue
                    self._last_seq = seq
                # Text poll. With a change counter we only reach here when the
                # clipboard changed, so read every time. Without one (Linux/X11,
                # where pyperclip forks a subprocess), throttle to every Nth cycle
                # so an idle clipboard doesn't fork a process ~every 0.7s.
                self._text_tick += 1
                if have_seq or (self._text_tick % self._text_every) == 0:
                    try:
                        cur = read_clipboard_text()
                    except Exception:
                        cur = None
                    if cur is not None and cur != self._last_text:
                        if cur.strip():
                            if self._add_text(cur):
                                self._last_text = cur
                                self._fire_change()
                        else:
                            self._last_text = cur
                # Image-clipboard poll is throttled (perf: VAL-PERF-006).
                # ImageGrab.grabclipboard() is a real Win32 clipboard-open +
                # bitmap grab — the single most expensive call in the loop.
                # Throttle to every 8th cycle (~5.6s) so text-only clipboard
                # changes don't pay a GDI tax on every poll. Text-clipboard
                # history is unaffected and still runs each cycle.
                self._img_tick += 1
                if (self._img_tick % self._img_every) != 0:
                    continue
                try:
                    grabbed = _grab_clipboard_image()
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
                        if self._add_image(img, h):
                            self._last_img = h
                            self._fire_change()
            except Exception as e:
                # Log but don't crash — a transient clipboard read failure must
                # never kill the monitor thread silently.
                print(f"clipboard monitor error ({type(e).__name__}): {e}")
                if stop_event.wait(1.0):
                    break

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
            rgb = img.convert("RGB")
            digest = hashlib.sha256()
            digest.update(rgb.width.to_bytes(8, "big"))
            digest.update(rgb.height.to_bytes(8, "big"))
            digest.update(rgb.tobytes())
            return digest.hexdigest()
        except Exception:
            return None

    def _evicted_image_if_full(self):
        """When the deque is full, the upcoming append evicts items[0] — if THAT
        entry is an image, delete its PNG so no orphan file is left behind.
        Only items[0]'s file may be deleted: scanning for 'any image' deleted
        the PNG of an image still listed in history (broken thumbnail now,
        entry silently dropped on next load)."""
        if len(self.items) >= self.maxlen and self.items:
            old = self.items[0]
            if old.get("type") == "image" and old.get("path"):
                return old["path"]
        return None

    @staticmethod
    def _now():
        n = datetime.now()
        return n.strftime("%H:%M"), n.strftime("%Y-%m-%d %H:%M:%S")

    def _add_text(self, text):
        if not isinstance(text, str) or len(
                text.encode("utf-8", errors="replace")) > _MAX_CLIPBOARD_TEXT_BYTES:
            return False
        with self._lock, exclusive_file_lock(self.path) as acquired:
            if not acquired:
                return False
            self._sync_from_disk()  # pick up any webui-side deletions first
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
            previous = deque(self.items, maxlen=self.maxlen)
            for e in list(self.items):
                if e.get("type") == "text" and e.get("text") == text:
                    self.items.remove(e)
                    break
            evicted_image = self._evicted_image_if_full()
            t, stamp = self._now()
            self.items.append(
                {"type": "text", "time": t, "stamp": stamp, "text": text}
            )
            if not self._save():
                self.items = previous
                return False
            if evicted_image:
                self._delete_image_file(evicted_image)
            return True

    def _add_image(self, img, h):
        with self._lock, exclusive_file_lock(self.path) as acquired:
            if not acquired:
                return False
            self._sync_from_disk()  # pick up any webui-side deletions first
            previous = deque(self.items, maxlen=self.maxlen)
            retired_images = []
            for e in list(self.items):
                if e.get("type") == "image" and e.get("hash") == h:
                    # Delete the old PNG before dropping the entry — otherwise the
                    # file orphans on disk (eviction + delete_index both do this;
                    # this dedup path was the one place that leaked).
                    if e.get("path"):
                        retired_images.append(e["path"])
                    self.items.remove(e)
                    break
            evicted_image = self._evicted_image_if_full()
            if evicted_image:
                retired_images.append(evicted_image)
            # Use the FULL hash in the filename. Truncating to 16 hex chars meant
            # two different images whose hashes shared a 16-char prefix wrote to
            # the same PNG — the second save overwrote the first while both
            # entries remained, leaving one pointing at the wrong image.
            fpath = os.path.join(self.img_dir, h + ".png")
            try:
                img.convert("RGBA").save(fpath, "PNG")
            except Exception as e:
                print("clip image save error:", e)
                self.items = previous
                return False
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
                self.items = previous
                if fpath not in retired_images:
                    self._delete_image_file(fpath)
                return False
            for old_path in set(retired_images):
                if os.path.realpath(old_path) != os.path.realpath(fpath):
                    self._delete_image_file(old_path)
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
        if n > self.maxlen:
            self.maxlen = n
            self.items = deque(self.items, maxlen=n)

    def recent(self, n=25):
        with self._lock:
            self._load()
            items = list(self.items)
        if n <= 0:
            return []
        return items[-n:][::-1]

    def clear(self):
        with self._lock, exclusive_file_lock(self.path) as acquired:
            if not acquired:
                return False
            self._sync_from_disk()
            previous = deque(self.items, maxlen=self.maxlen)
            self.items.clear()
            if not self._save():
                self.items = previous
                return False
            return self._purge_image_files()

    def delete_index(self, i):
        """Delete the item at index i in recent() (newest-first) order."""
        with self._lock, exclusive_file_lock(self.path) as acquired:
            if not acquired:
                return False
            self._sync_from_disk()
            items = list(self.items)
            j = len(items) - 1 - i
            if 0 <= j < len(items):
                previous = deque(self.items, maxlen=self.maxlen)
                e = items[j]
                del items[j]
                self.items = deque(items, maxlen=self.maxlen)
                if not self._save():
                    self.items = previous
                    return False
                if e.get("type") == "image" and e.get("path"):
                    if not self._delete_image_file(e["path"]):
                        self.items = previous
                        self._save()
                        return False
                return True
            return False

    def delete_match(self, stamp, text=None):
        """Delete by STABLE identity (stamp [+ text]) instead of array index — a
        stale index removed the wrong clip when the list shifted under it. Reloads
        from disk first (cross-process truth). For images pass text="" to match by
        stamp alone. Returns True if one was removed."""
        with self._lock, exclusive_file_lock(self.path) as acquired:
            if not acquired:
                return False
            self._sync_from_disk()
            items = list(self.items)
            target = None
            for e in reversed(items):  # newest-first, matching the UI order
                if str(e.get("stamp", "")) != str(stamp):
                    continue
                if text and str(e.get("text", "")) != str(text):
                    continue
                target = e
                break
            if target is None:
                return False
            previous = deque(self.items, maxlen=self.maxlen)
            items.remove(target)
            self.items = deque(items, maxlen=self.maxlen)
            if not self._save():
                self.items = previous
                return False
            if target.get("type") == "image" and target.get("path"):
                if not self._delete_image_file(target["path"]):
                    self.items = previous
                    self._save()
                    return False
            return True

    def _save(self):
        tmp = None
        try:
            parent = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(parent, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                prefix=os.path.basename(self.path) + ".", suffix=".tmp",
                dir=parent, text=True)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(list(self.items), f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            return True
        except OSError as e:
            print(f"clipboard save error ({type(e).__name__}): {e}")
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            return False
