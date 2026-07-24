#!/usr/bin/env python3
"""LRU Disk Cache — manages GGUF model files within a configurable size limit.

Provides ModelCache: an LRU eviction policy for cached model files. Tracked files
that exceed the configured size limit cause eviction of the least-recently-used
model(s) until the total drops below the limit. The currently loaded model is
protected from eviction.

Cache metadata (per-file last_used timestamps and sizes) is persisted to a JSON
file at MODELS_DIR/.cache_metadata.json. Writes are atomic (write-to-temp then
rename) so a crash mid-write never corrupts the metadata. On load, corrupted or
malformed metadata is discarded and the cache starts fresh.

Set the limit to 0 to disable caching (all non-loaded models are evicted). Set
it to a very large value to effectively disable eviction.

Pure stdlib + branding (for MODELS_DIR default). No heavy dependencies.
"""

import json
import math
import os
import time

try:
    from branding import MODELS_DIR
except ImportError:
    MODELS_DIR = os.path.join(os.path.expanduser("~"), "Mumble", "models")

DEFAULT_CACHE_LIMIT_GB = 5
METADATA_FILENAME = ".cache_metadata.json"
METADATA_VERSION = 1


class ModelCache:
    """LRU disk cache for GGUF model files."""

    def __init__(self, models_dir=None, cache_limit_gb=None):
        self.models_dir = models_dir or MODELS_DIR
        self.cache_limit_bytes = (
            (cache_limit_gb if cache_limit_gb is not None else DEFAULT_CACHE_LIMIT_GB)
            * (1024 ** 3)
        )
        self.entries = {}
        self._load()

    def touch(self, model_path):
        """Update the last_used timestamp for a model file."""
        path = os.path.abspath(model_path)
        if not self._is_valid_path(path):
            return
        if not os.path.isfile(path):
            return
        if path not in self.entries:
            try:
                size = os.path.getsize(path)
            except OSError:
                return
            self.entries[path] = {
                "last_used": time.time(),
                "size_bytes": size,
            }
            self._save()
        else:
            self.entries[path]["last_used"] = time.time()
            self._save()

    def register(self, model_path):
        """Register a model file in the cache metadata."""
        path = os.path.abspath(model_path)
        if not self._is_valid_path(path):
            return False
        try:
            if not os.path.isfile(path):
                return False
            size = os.path.getsize(path)
        except OSError:
            return False
        self.entries[path] = {
            "last_used": time.time(),
            "size_bytes": size,
        }
        self._save()
        return True

    def remove(self, model_path):
        """Remove a model from the cache metadata (does NOT delete the file)."""
        path = os.path.abspath(model_path)
        if path in self.entries:
            del self.entries[path]
            self._save()

    def evict(self, loaded_model_name=None):
        """Evict least-recently-used models until total size is under the limit."""
        limit = self.cache_limit_bytes
        evicted = []
        phantoms = self._clean_phantoms()
        evicted.extend(phantoms)
        total = self.total_size_bytes()
        if total <= limit:
            self._save()
            return evicted
        sorted_entries = sorted(
            self.entries.items(),
            key=lambda kv: kv[1].get("last_used", 0),
        )
        for path, info in sorted_entries:
            if total <= limit:
                break
            if loaded_model_name and os.path.abspath(loaded_model_name) == path:
                continue
            size = info.get("size_bytes", 0)
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                # Keep failed deletions tracked.  Claiming an eviction here
                # makes the cache report space as freed while the large file is
                # still on disk, and prevents a later retry.
                continue
            del self.entries[path]
            evicted.append(path)
            total -= size
        self._save()
        return evicted

    def total_size_bytes(self):
        """Total size of all tracked model files."""
        return sum(e.get("size_bytes", 0) for e in self.entries.values())

    def set_limit(self, limit_gb):
        """Change the cache size limit in GB."""
        self.cache_limit_bytes = limit_gb * (1024 ** 3)

    def set_limit_bytes(self, limit_bytes):
        """Change the cache size limit to an exact number of bytes."""
        self.cache_limit_bytes = limit_bytes

    def _is_valid_path(self, path):
        if not path:
            return False
        if not path.lower().endswith(".gguf"):
            return False
        try:
            root = os.path.normcase(
                os.path.realpath(os.path.abspath(self.models_dir))
            )
            candidate = os.path.normcase(
                os.path.realpath(os.path.abspath(path))
            )
            return os.path.commonpath((root, candidate)) == root
        except (OSError, ValueError, TypeError):
            return False

    def _clean_phantoms(self):
        phantom = [p for p in self.entries if not os.path.exists(p)]
        for p in phantom:
            del self.entries[p]
        return phantom

    def _metadata_path(self):
        return os.path.join(self.models_dir, METADATA_FILENAME)

    def _save(self):
        try:
            os.makedirs(self.models_dir, exist_ok=True)
        except OSError:
            return
        data = {
            "version": METADATA_VERSION,
            "entries": self.entries,
        }
        meta_path = self._metadata_path()
        tmp_path = meta_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
            os.replace(tmp_path, meta_path)
        except OSError:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    def _load(self):
        meta_path = self._metadata_path()
        if not os.path.isfile(meta_path):
            self.entries = {}
            return
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError, ValueError):
            self.entries = {}
            return
        if not isinstance(data, dict):
            self.entries = {}
            return
        entries = data.get("entries")
        if not isinstance(entries, dict):
            self.entries = {}
            return
        cleaned = {}
        for path, info in entries.items():
            if not isinstance(info, dict):
                continue
            if "last_used" not in info or "size_bytes" not in info:
                continue
            if not isinstance(path, str) or not self._is_valid_path(path):
                continue
            try:
                last_used = float(info["last_used"])
                size_bytes = int(info["size_bytes"])
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(last_used) or size_bytes < 0:
                continue
            cleaned[path] = {
                "last_used": last_used,
                "size_bytes": size_bytes,
            }
        self.entries = cleaned

    def __repr__(self):
        return (
            f"ModelCache({len(self.entries)} entries, "
            f"{self.total_size_bytes() / 1024**3:.1f} GB / "
            f"{self.cache_limit_bytes / 1024**3:.1f} GB)"
        )
