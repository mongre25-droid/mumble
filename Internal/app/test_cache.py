#!/usr/bin/env python3
"""Tests for models/cache.py — LRU disk cache for GGUF model files.

Run: python test_cache.py
Pure stdlib + project modules; no key, no network, no heavy model.

Covers:
  VAL-MODL-006: LRU eviction on exceeding size limit
  VAL-MODL-007: Configurable size limit (0 disables, large value prevents eviction)
  Cache metadata (last-used timestamps) persists across restarts
  Loaded model protection from eviction
  Atomic metadata writes
  Cache metadata corruption handling
"""

import os
import sys
import tempfile
import time

# Ensure the app directory is on sys.path so we can import project modules.
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


# ============================================================================
# Helpers
# ============================================================================

def _create_fake_gguf(dir_path, filename, size_bytes=1024):
    """Create a minimal fake GGUF file with valid magic bytes and given size."""
    path = os.path.join(dir_path, filename)
    content = b"GGUF\x03\x00\x00\x00" + (b"\x00" * (size_bytes - 8))
    with open(path, "wb") as f:
        f.write(content)
    return path


# ============================================================================
#  VAL-MODL-006: LRU eviction on exceeding size limit
# ============================================================================

def test_import():
    """ModelCache imports cleanly."""
    from models.cache import ModelCache
    assert callable(ModelCache), f"ModelCache should be callable"


def test_default_construction():
    """Default cache has limit 5GB and empty entries."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)
        assert cache.cache_limit_bytes == 5 * 1024**3
        assert cache.total_size_bytes() == 0
        assert len(cache.entries) == 0


def test_touch_updates_timestamp():
    """Touch should update last_used timestamp for a model."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)
        p = os.path.join(d, "model.gguf")
        # Create a fake file.
        with open(p, "wb") as f:
            f.write(b"GGUF\x03\x00\x00\x00" + (b"\x00" * 1016))
        t0 = time.time()
        cache.touch(p)
        t1 = time.time()
        assert p in cache.entries
        assert t0 <= cache.entries[p]["last_used"] <= t1 + 0.1


def test_touch_updates_existing():
    """Touch on an already-tracked model updates its timestamp."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)
        p = os.path.join(d, "model.gguf")
        with open(p, "wb") as f:
            f.write(b"GGUF\x03\x00\x00\x00" + (b"\x00" * 1016))
        cache.touch(p)
        first = cache.entries[p]["last_used"]
        time.sleep(0.1)
        cache.touch(p)
        second = cache.entries[p]["last_used"]
        assert second > first, "touch should update timestamp"


def test_register_new_model():
    """Register adds a model entry with current timestamp."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)
        p = os.path.join(d, "model.gguf")
        with open(p, "wb") as f:
            f.write(b"GGUF\x03\x00\x00\x00" + (b"\x00" * 1016))
        cache.register(p)
        assert p in cache.entries
        assert cache.entries[p]["size_bytes"] == 1024


def test_total_size_bytes():
    """Total size sums all tracked model sizes."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)
        p1 = os.path.join(d, "a.gguf")
        p2 = os.path.join(d, "b.gguf")
        with open(p1, "wb") as f:
            f.write(b"GGUF\x03\x00\x00\x00" + (b"\x00" * 1016))
        with open(p2, "wb") as f:
            f.write(b"GGUF\x03\x00\x00\x00" + (b"\x00" * 2040))
        cache.register(p1)
        cache.register(p2)
        total = cache.total_size_bytes()
        assert total == 1024 + 2048


def test_remove_entry():
    """Remove deletes a model entry from metadata."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)
        p = os.path.join(d, "model.gguf")
        with open(p, "wb") as f:
            f.write(b"GGUF\x03\x00\x00\x00" + (b"\x00" * 1016))
        cache.register(p)
        assert p in cache.entries
        cache.remove(p)
        assert p not in cache.entries


def test_eviction_selects_lru():
    """When cache exceeds limit, oldest model is evicted first."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)

        # Create 3 model files of 100KB each.
        p1 = os.path.join(d, "old.gguf")
        p2 = os.path.join(d, "mid.gguf")
        p3 = os.path.join(d, "new.gguf")
        for p in (p1, p2, p3):
            with open(p, "wb") as f:
                f.write(b"GGUF\x03\x00\x00\x00" + (b"\x00" * 102392))

        # Register all with staggered timestamps.
        cache.register(p1)
        time.sleep(0.05)
        cache.register(p2)
        time.sleep(0.05)
        cache.register(p3)

        total = cache.total_size_bytes()
        assert total > 100_000

        # Set limit to just enough for 1 model (200KB).
        cache.set_limit_bytes(200_000)
        evicted = cache.evict(loaded_model_name=None)

        # Only the newest model should survive.
        assert p1 not in cache.entries or not os.path.exists(p1)
        assert p2 not in cache.entries or not os.path.exists(p2)
        # p3 (newest) should survive.
        assert p3 in cache.entries
        assert os.path.exists(p3)
        # At least one model was evicted.
        assert len(evicted) >= 2


def test_loaded_model_protected():
    """Loaded model is never evicted even if it's the LRU."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)

        # Create 3 model files.
        model_paths = []
        for name in ("a.gguf", "b.gguf", "c.gguf"):
            p = os.path.join(d, name)
            with open(p, "wb") as f:
                f.write(b"GGUF\x03\x00\x00\x00" + (b"\x00" * 102392))
            cache.register(p)
            time.sleep(0.05)
            model_paths.append(p)

        total = cache.total_size_bytes()
        # Set limit to just enough for 1 model.
        cache.set_limit_bytes(150_000)

        # Protect the oldest model (first registered).
        loaded = model_paths[0]
        evicted = cache.evict(loaded_model_name=loaded)

        # The loaded model must survive.
        assert loaded in cache.entries, "loaded model was evicted"
        assert os.path.exists(loaded), "loaded model file was deleted"

        # Some other models should have been evicted.
        assert any(p not in cache.entries for p in model_paths if p != loaded), \
            "expected other models to be evicted"


# ============================================================================
#  VAL-MODL-007: Configurable size limit
# ============================================================================

def test_limit_zero_evicts_all():
    """Setting limit to 0 evicts all non-loaded models."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)
        p1 = os.path.join(d, "m1.gguf")
        p2 = os.path.join(d, "m2.gguf")
        for p in (p1, p2):
            with open(p, "wb") as f:
                f.write(b"GGUF\x03\x00\x00\x00" + (b"\x00" * 1016))
            cache.register(p)

        cache.set_limit_bytes(0)
        evicted = cache.evict(loaded_model_name=None)

        assert len(evicted) >= 2
        assert not os.path.exists(p1), "limit=0 should delete p1"
        assert not os.path.exists(p2), "limit=0 should delete p2"
        assert cache.total_size_bytes() == 0


def test_limit_zero_protects_loaded():
    """Setting limit to 0 evicts all except the loaded model."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)
        p1 = os.path.join(d, "m1.gguf")
        p2 = os.path.join(d, "m2.gguf")
        for p in (p1, p2):
            with open(p, "wb") as f:
                f.write(b"GGUF\x03\x00\x00\x00" + (b"\x00" * 1016))
            cache.register(p)

        cache.set_limit_bytes(0)
        evicted = cache.evict(loaded_model_name=p1)

        # p1 (loaded) should survive.
        assert os.path.exists(p1)
        assert p1 in cache.entries
        # p2 should be evicted.
        assert not os.path.exists(p2)
        assert p2 not in cache.entries


def test_large_limit_prevents_eviction():
    """A very large limit prevents eviction of any models."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)
        p1 = os.path.join(d, "m1.gguf")
        p2 = os.path.join(d, "m2.gguf")
        for p in (p1, p2):
            with open(p, "wb") as f:
                f.write(b"GGUF\x03\x00\x00\x00" + (b"\x00" * 1016))
            cache.register(p)

        # Set an enormous limit.
        cache.set_limit_bytes(2**60)  # 1 PiB
        evicted = cache.evict(loaded_model_name=None)

        assert len(evicted) == 0
        assert os.path.exists(p1)
        assert os.path.exists(p2)
        assert p1 in cache.entries
        assert p2 in cache.entries


def test_set_limit_then_evict():
    """Changing the limit triggers eviction on next evict call."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)

        # 3 models, ~50KB each (gives us room for meaningful limit changes).
        paths = []
        for name in ("a.gguf", "b.gguf", "c.gguf"):
            p = os.path.join(d, name)
            with open(p, "wb") as f:
                f.write(b"GGUF\x03\x00\x00\x00" + (b"\x00" * 51192))
            cache.register(p)
            time.sleep(0.05)
            paths.append(p)

        # Initially default 5GB limit — no eviction needed.
        evicted = cache.evict()
        assert len(evicted) == 0

        # Shrink limit to 80KB (fits ~1 model).
        cache.set_limit_bytes(80_000)
        evicted = cache.evict()
        assert len(evicted) >= 2, f"expected >=2 evicted with 80KB limit, got {len(evicted)}"

        # Shrink further to 30KB (won't fit any model, but loaded protected).
        cache.set_limit_bytes(30_000)
        evicted = cache.evict()
        assert len(evicted) >= 1, f"expected >=1 additional evicted, got {len(evicted)}"


# ============================================================================
#  Cache metadata persistence across restarts
# ============================================================================

def test_metadata_persists_across_instances():
    """Cache metadata (timestamps) survives creating a new cache instance."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        # First instance: register a model.
        cache1 = ModelCache(models_dir=d)
        p = os.path.join(d, "model.gguf")
        with open(p, "wb") as f:
            f.write(b"GGUF\x03\x00\x00\x00" + (b"\x00" * 1016))
        cache1.register(p)
        cache1.touch(p)
        ts = cache1.entries[p]["last_used"]
        cache1._save()  # force persist

        # Second instance: should reload the same metadata.
        cache2 = ModelCache(models_dir=d)
        assert p in cache2.entries
        # Timestamp should be within a small epsilon.
        assert abs(cache2.entries[p]["last_used"] - ts) < 0.5


def test_metadata_persists_timestamps_after_eviction():
    """After eviction, the remaining model's timestamp survives a reload."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache1 = ModelCache(models_dir=d)

        p1 = os.path.join(d, "old.gguf")
        p2 = os.path.join(d, "new.gguf")
        for p in (p1, p2):
            with open(p, "wb") as f:
                f.write(b"GGUF\x03\x00\x00\x00" + (b"\x00" * 102392))
            cache1.register(p)
            time.sleep(0.05)

        # Evict older model.
        cache1.set_limit_bytes(150_000)
        cache1.evict()

        assert p2 in cache1.entries
        ts = cache1.entries[p2]["last_used"]

        # New instance should load surviving model with its timestamp.
        cache2 = ModelCache(models_dir=d)
        assert p2 in cache2.entries
        assert abs(cache2.entries[p2]["last_used"] - ts) < 0.5


# ============================================================================
#  Cache metadata corruption handling
# ============================================================================

def test_corrupted_metadata_handled():
    """If metadata file is corrupted, cache starts fresh without crashing."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        # Write corrupted JSON.
        meta_path = os.path.join(d, ".cache_metadata.json")
        with open(meta_path, "w") as f:
            f.write("this is not valid JSON {{{")

        cache = ModelCache(models_dir=d)
        # Should load with empty entries, not crash.
        assert len(cache.entries) == 0
        assert cache.total_size_bytes() == 0


def test_partial_metadata_handled():
    """If metadata has wrong structure, start fresh."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        import json
        meta_path = os.path.join(d, ".cache_metadata.json")
        with open(meta_path, "w") as f:
            json.dump({"wrong_key": [1, 2, 3]}, f)

        cache = ModelCache(models_dir=d)
        assert len(cache.entries) == 0


# ============================================================================
#  Atomic writes
# ============================================================================

def test_atomic_save_no_temp_file_left():
    """After save, only the metadata file exists, no temp file."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)
        p = os.path.join(d, "model.gguf")
        with open(p, "wb") as f:
            f.write(b"GGUF\x03\x00\x00\x00" + (b"\x00" * 1016))
        cache.register(p)
        cache._save()

        # Only the actual metadata file should exist.
        files = os.listdir(d)
        assert ".cache_metadata.json" in files
        assert ".cache_metadata.json.tmp" not in files, "temp file should be cleaned up"


# ============================================================================
#  Edge cases
# ============================================================================

def test_eviction_empty_cache():
    """Eviction on empty cache is a no-op."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)
        evicted = cache.evict()
        assert len(evicted) == 0


def test_register_missing_file():
    """Registering a file that doesn't exist returns False gracefully."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)
        result = cache.register(os.path.join(d, "nonexistent.gguf"))
        assert result is False
        assert len(cache.entries) == 0


def test_touch_nonexistent_file():
    """Touching a file that doesn't exist is a no-op."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)
        cache.touch(os.path.join(d, "nonexistent.gguf"))
        assert len(cache.entries) == 0


def test_evict_removes_from_disk():
    """Evicted model files are actually deleted from disk."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)
        p = os.path.join(d, "model.gguf")
        with open(p, "wb") as f:
            f.write(b"GGUF\x03\x00\x00\x00" + (b"\x00" * 1016))
        cache.register(p)

        cache.set_limit_bytes(0)
        cache.evict()

        assert not os.path.exists(p), "evicted file should be deleted from disk"


def test_evict_skips_missing_files():
    """If a tracked model file is missing from disk, eviction skips it gracefully."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)
        p = os.path.join(d, "model.gguf")
        with open(p, "wb") as f:
            f.write(b"GGUF\x03\x00\x00\x00" + (b"\x00" * 1016))
        cache.register(p)

        # Delete the file from disk without updating metadata.
        os.remove(p)

        cache.set_limit_bytes(0)
        evicted = cache.evict()

        # The entry should be cleaned from metadata even though file was missing.
        assert p not in cache.entries
        assert len(evicted) >= 1


def test_save_load_roundtrip():
    """Save and load preserves all metadata fields."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache1 = ModelCache(models_dir=d)
        p = os.path.join(d, "model.gguf")
        with open(p, "wb") as f:
            f.write(b"GGUF\x03\x00\x00\x00" + (b"\x00" * 1016))
        cache1.register(p)
        cache1.touch(p)
        cache1._save()

        cache2 = ModelCache(models_dir=d)
        assert cache2.entries[p]["size_bytes"] == 1024
        assert "last_used" in cache2.entries[p]


def test_non_gguf_in_cache_dir():
    """Files that aren't .gguf are not tracked by register."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)
        p = os.path.join(d, "readme.txt")
        with open(p, "w") as f:
            f.write("not a model")
        cache.register(p)
        assert p not in cache.entries


def test_unknown_entries_not_in_metadata():
    """Entries for files that don't match metadata keys are left alone."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)
        # Manually set an entry for a file that doesn't exist.
        cache.entries["/nonexistent/file.gguf"] = {
            "last_used": time.time(),
            "size_bytes": 1000
        }
        evicted = cache.evict()
        # The phantom entry should be cleaned up.
        assert "/nonexistent/file.gguf" not in cache.entries


def test_set_limit_gb():
    """set_limit in GB correctly converts to bytes."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)
        cache.set_limit(10)
        assert cache.cache_limit_bytes == 10 * 1024**3
        cache.set_limit(0)
        assert cache.cache_limit_bytes == 0
        cache.set_limit(100)
        assert cache.cache_limit_bytes == 100 * 1024**3


def test_set_limit_bytes():
    """set_limit_bytes with exact byte values works."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d)
        cache.set_limit_bytes(12345)
        assert cache.cache_limit_bytes == 12345


def test_default_limit_from_constructor():
    """Constructor can override the default cache limit."""
    from models.cache import ModelCache
    with tempfile.TemporaryDirectory() as d:
        cache = ModelCache(models_dir=d, cache_limit_gb=10)
        assert cache.cache_limit_bytes == 10 * 1024**3

        cache2 = ModelCache(models_dir=d, cache_limit_gb=0)
        assert cache2.cache_limit_bytes == 0


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
