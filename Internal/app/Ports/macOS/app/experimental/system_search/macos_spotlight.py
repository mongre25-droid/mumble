"""Bounded, cancellable Spotlight provider for Mumble Find on macOS."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import threading
import time


class SpotlightSearchProvider:
    """Query the macOS metadata index without scanning the filesystem.

    The provider owns every ``mdfind`` process by generation so cancellation and
    shutdown can terminate stale native work.  Results are limited to the user's
    home directory and returned as paths only to the trusted engine boundary.
    """

    name = "Spotlight"

    def __init__(self, home=None, process_factory=None):
        self.home = Path(home or Path.home()).expanduser().resolve()
        self._custom_process_factory = process_factory is not None
        self._process_factory = process_factory or self._spawn
        self._lock = threading.RLock()
        self._processes = {}
        self._closed = False
        self._last_error = ""

    @staticmethod
    def _spawn(args):
        return subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def status(self):
        available = not self._closed and bool(
            os.path.exists("/usr/bin/mdfind") or self._custom_process_factory
        )
        return {
            "available": available and not bool(self._last_error),
            "state": "closed" if self._closed else (
                "error" if self._last_error else "ready" if available else "unavailable"
            ),
            "name": self.name,
            "message": self._last_error or (
                "" if available else "Spotlight metadata search is unavailable."
            ),
        }

    def work_status(self):
        with self._lock:
            owned = len(self._processes)
        return {
            "capacity": 2,
            "executors": 1 if not self._closed else 0,
            "processes": owned,
            "threads": 0,
            "owned": owned,
            "running": owned,
            "pending": 0,
            "ownership_entries": owned,
        }

    def _contained(self, raw):
        try:
            path = Path(raw).expanduser().resolve(strict=False)
            path.relative_to(self.home)
            return path
        except (OSError, RuntimeError, ValueError):
            return None

    @staticmethod
    def _stop(process):
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=0.2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def query(self, query, *, limit, deadline, cancellation, generation):
        query = " ".join(str(query or "").split())[:256]
        limit = max(1, min(250, int(limit)))
        if not query:
            return {"items": [], "state": "complete", "message": ""}
        with self._lock:
            if self._closed:
                raise RuntimeError("Spotlight provider is closed.")
        args = ["/usr/bin/mdfind", "-0", "-onlyin", str(self.home), query]
        process = self._process_factory(args)
        with self._lock:
            self._processes[int(generation)] = process
        try:
            stdout = stderr = b""
            while True:
                now = time.monotonic()
                if cancellation.cancelled or now >= float(deadline):
                    self._stop(process)
                    return {
                        "items": [],
                        "state": "cancelled" if cancellation.cancelled else "partial",
                        "message": (
                            "Superseded Spotlight query" if cancellation.cancelled
                            else "Spotlight reached the query deadline."
                        ),
                        "deadline_exceeded": not cancellation.cancelled,
                    }
                try:
                    stdout, stderr = process.communicate(
                        timeout=max(0.001, min(0.025, float(deadline) - now)))
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode not in (0, None):
                message = stderr.decode("utf-8", "replace").strip()[:160]
                self._last_error = message or "Spotlight query failed."
                return {"items": [], "state": "error", "message": self._last_error}
            seen = set()
            items = []
            for value in stdout.split(b"\0"):
                if not value:
                    continue
                path = self._contained(value.decode("utf-8", "replace"))
                if path is None or path in seen or not path.exists():
                    continue
                seen.add(path)
                items.append({
                    "kind": "folder" if path.is_dir() else "file",
                    "name": path.name,
                    "target": str(path),
                    "subtitle": str(path.parent),
                    "source": "spotlight",
                })
                if len(items) >= limit:
                    break
            self._last_error = ""
            return {"items": items, "state": "complete", "message": ""}
        finally:
            with self._lock:
                if self._processes.get(int(generation)) is process:
                    self._processes.pop(int(generation), None)

    def cancel(self, generation):
        with self._lock:
            process = self._processes.get(int(generation))
        if process is None:
            return False
        self._stop(process)
        return True

    def shutdown(self):
        with self._lock:
            self._closed = True
            processes = list(self._processes.values())
            self._processes.clear()
        for process in processes:
            self._stop(process)
