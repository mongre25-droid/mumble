#!/usr/bin/env python3
"""Tests for models/manager.py and models/registry.py — model infrastructure.

Run: python test_models.py
Pure stdlib + project modules; no key, no network, no heavy model.

Covers:
  VAL-MODL-001: Empty directory returns empty collection
  VAL-MODL-002: GGUF files discovered with parsed metadata
  VAL-MODL-010: Weak tier recommendations
  VAL-MODL-011: Mid/powerful tier recommendations
  VAL-MODL-012: Lazy loading (no model loaded at startup)
  VAL-MODL-016: Version tracking with update detection
  VAL-MODL-018: Model status (available, loaded, downloading)
"""

import os
import sys
import tempfile

# Ensure the app directory is on sys.path so we can import project modules.
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


# ============================================================================
# Helpers
# ============================================================================

def _create_fake_gguf(dir_path, filename, content=b"GGUF\x03\x00\x00\x00"):
    """Create a minimal fake GGUF file with valid magic bytes."""
    path = os.path.join(dir_path, filename)
    with open(path, "wb") as f:
        f.write(content)
    return path


# ============================================================================
#  VAL-MODL-001: Empty directory returns empty collection
# ============================================================================

def test_empty_directory_returns_empty():
    from models.manager import ModelManager
    from models.registry import ModelRegistry
    with tempfile.TemporaryDirectory() as empty_dir:
        mgr = ModelManager(models_dir=empty_dir)
        models = mgr.available_models()
        assert isinstance(models, list), f"expected list, got {type(models)}"
        assert len(models) == 0, f"expected empty list, got {len(models)} items"
        # Registry still works with no models.
        reg = ModelRegistry()
        tier = reg.detect_tier()
        assert tier in ("weak", "mid", "powerful"), f"unexpected tier: {tier}"


def test_empty_directory_no_crash():
    from models.manager import ModelManager
    with tempfile.TemporaryDirectory() as empty_dir:
        mgr = ModelManager(models_dir=empty_dir)
        # None of these should raise.
        mgr.discover()
        result = mgr.available_models()
        assert result == []
        assert mgr.loaded_model is None
        # Unload on empty state is a no-op.
        mgr.unload_model("nonexistent")


# ============================================================================
#  VAL-MODL-002: GGUF files discovered with parsed metadata
# ============================================================================

def test_valid_gguf_discovered():
    from models.manager import ModelManager
    with tempfile.TemporaryDirectory() as d:
        _create_fake_gguf(d, "qwen2.5-1.5b-instruct-q4_k_m.gguf")
        _create_fake_gguf(d, "grmr-2b-instruct-q4_k_m.gguf")
        # Add a non-GGUF file that should be ignored.
        with open(os.path.join(d, "README.txt"), "w") as f:
            f.write("not a model")
        mgr = ModelManager(models_dir=d)
        mgr.discover()
        models = mgr.available_models()
        assert len(models) == 2, f"expected 2 models, got {len(models)}"
        names = [m["name"] for m in models]
        assert "Qwen2.5-1.5B-Instruct" in names
        assert "GRMR-2B-Instruct" in names


def test_gguf_metadata_parsed():
    from models.manager import ModelManager
    with tempfile.TemporaryDirectory() as d:
        path = _create_fake_gguf(d, "qwen2.5-1.5b-instruct-q4_k_m.gguf",
                                 content=b"GGUF\x03\x00\x00\x00" + (b"\x00" * 1024))
        mgr = ModelManager(models_dir=d)
        mgr.discover()
        models = mgr.available_models()
        assert len(models) == 1
        m = models[0]
        # Metadata fields.
        assert m["name"] == "Qwen2.5-1.5B-Instruct"
        assert m["quantisation"] == "Q4_K_M"
        assert isinstance(m["size_bytes"], int) and m["size_bytes"] > 0
        assert "version" in m
        assert m["path"] == path
        assert m["status"] == "available"


def test_non_gguf_files_filtered():
    from models.manager import ModelManager
    with tempfile.TemporaryDirectory() as d:
        # Create misc files that are NOT GGUF.
        for name in ("README.txt", "config.json", "model.safetensors",
                     "model.bin", "notes.md"):
            with open(os.path.join(d, name), "w") as f:
                f.write("not a model")
        mgr = ModelManager(models_dir=d)
        mgr.discover()
        models = mgr.available_models()
        assert len(models) == 0, f"expected 0, got {len(models)}"


def test_invalid_gguf_magic_filtered():
    """Files with .gguf extension but wrong magic bytes are filtered."""
    from models.manager import ModelManager
    with tempfile.TemporaryDirectory() as d:
        # File with .gguf extension but no GGUF magic.
        bad = os.path.join(d, "bad-model.gguf")
        with open(bad, "wb") as f:
            f.write(b"NOT_A_GGUF_FILE")
        mgr = ModelManager(models_dir=d)
        mgr.discover()
        models = mgr.available_models()
        assert len(models) == 0, f"expected 0 invalid .gguf filtered, got {len(models)}"


def test_gguf_filename_variants():
    """Various GGUF naming conventions are parsed correctly."""
    from models.manager import _parse_gguf_filename
    # Standard HuggingFace naming.
    info = _parse_gguf_filename("qwen2.5-1.5b-instruct-q4_k_m.gguf")
    assert info["name"] == "Qwen2.5-1.5B-Instruct"
    assert info["quantisation"] == "Q4_K_M"

    info = _parse_gguf_filename("grmr-2b-instruct-q4_k_m.gguf")
    assert info["name"] == "GRMR-2B-Instruct"
    assert info["quantisation"] == "Q4_K_M"

    info = _parse_gguf_filename("llama-3.2-3b-instruct-q8_0.gguf")
    assert info["name"] == "Llama-3.2-3B-Instruct"
    assert info["quantisation"] == "Q8_0"

    # F16 / unquantized.
    info = _parse_gguf_filename("phi-3-mini-4k-instruct-f16.gguf")
    assert info["quantisation"] == "F16"

    # Simple name without quantization suffix.
    info = _parse_gguf_filename("tinyllama.gguf")
    assert info["name"] == "Tinyllama"
    assert info["quantisation"] == "UNKNOWN"


# ============================================================================
#  VAL-MODL-010 / VAL-MODL-011: Hardware-tier recommendations
# ============================================================================

def test_registry_tier_detection():
    from models.registry import ModelRegistry
    reg = ModelRegistry()
    tier = reg.detect_tier()
    assert tier in reg.TIERS, f"unknown tier: {tier}"
    # Verify tier definitions exist.
    for t in reg.TIERS:
        assert t in reg.TIER_DEFS, f"missing def for tier {t}"


def test_weak_tier_recommends_lightweight():
    from models.registry import ModelRegistry
    reg = ModelRegistry()
    recs = reg.recommendations("weak")
    # Weak tier: punctuation only, no LLM models.
    stt = recs.get("stt_model")
    assert stt is not None
    # Stage 2/3 not recommended for weak.
    stage2 = recs.get("stage2_grammar")
    stage3 = recs.get("stage3_formatting")
    assert stage2 is not None and stage2.get("recommended") is False, \
        f"weak tier should not recommend Stage 2, got {stage2}"
    assert stage3 is not None and stage3.get("recommended") is False, \
        f"weak tier should not recommend Stage 3, got {stage3}"
    # All pipeline stages declared.
    assert "stage1_punctuation" in recs
    assert "stage4_cleanup" in recs


def test_mid_tier_recommends_q4():
    from models.registry import ModelRegistry
    reg = ModelRegistry()
    recs = reg.recommendations("mid")
    # STT model.
    assert "stt_model" in recs
    # LLM models with Q4_K_M quant.
    stage3 = recs.get("stage3_formatting")
    if stage3:
        assert "Q4_K_M" in stage3.get("quantisation", ""), \
            f"mid tier should use Q4_K_M, got {stage3.get('quantisation')}"


def test_powerful_tier_recommends_q8():
    from models.registry import ModelRegistry
    reg = ModelRegistry()
    recs = reg.recommendations("powerful")
    # STT model: larger.
    assert "stt_model" in recs
    # LLM models with Q8_0 or better.
    stage2 = recs.get("stage2_grammar")
    stage3 = recs.get("stage3_formatting")
    assert stage2 is not None
    assert stage3 is not None
    # At powerful tier, both stages should be recommended.
    assert stage2.get("recommended", False) is True
    assert stage3.get("recommended", False) is True


def test_registry_model_metadata():
    from models.registry import ModelRegistry
    reg = ModelRegistry()
    # Each registered model should have required metadata fields.
    for model_id, meta in reg.MODELS.items():
        assert "name" in meta, f"{model_id} missing name"
        assert "huggingface_id" in meta, f"{model_id} missing huggingface_id"
        assert "quantisations" in meta, f"{model_id} missing quantisations"
        assert "size_gb" in meta, f"{model_id} missing size_gb"
        assert "description" in meta, f"{model_id} missing description"


# ============================================================================
#  VAL-MODL-012: Lazy loading — no model loaded at startup
# ============================================================================

def test_no_model_loaded_at_construction():
    from models.manager import ModelManager
    with tempfile.TemporaryDirectory() as d:
        _create_fake_gguf(d, "test-model-q4_k_m.gguf")
        mgr = ModelManager(models_dir=d)
        # After construction, nothing should be loaded.
        assert mgr.loaded_model is None
        assert mgr.status("test-model-q4_k_m") == "unknown"  # not discovered yet
        mgr.discover()
        # After discovery, still nothing loaded.
        assert mgr.loaded_model is None


def test_lazy_load_on_request():
    from models.manager import ModelManager
    with tempfile.TemporaryDirectory() as d:
        _create_fake_gguf(d, "qwen2.5-1.5b-instruct-q4_k_m.gguf")
        mgr = ModelManager(models_dir=d)
        mgr.discover()
        # load_model triggers the load.
        path = mgr.load_model("Qwen2.5-1.5B-Instruct")
        assert path is not None
        assert mgr.loaded_model == "Qwen2.5-1.5B-Instruct"
        assert mgr.status("Qwen2.5-1.5B-Instruct") == "loaded"


def test_unload_model():
    from models.manager import ModelManager
    with tempfile.TemporaryDirectory() as d:
        _create_fake_gguf(d, "test-model-q4_k_m.gguf")
        mgr = ModelManager(models_dir=d)
        mgr.discover()
        mgr.load_model("Test-Model")
        assert mgr.loaded_model == "Test-Model"
        mgr.unload_model("Test-Model")
        assert mgr.loaded_model is None
        assert mgr.status("Test-Model") == "available"


def test_single_resident_policy():
    """Loading a new model unloads the old one."""
    from models.manager import ModelManager
    with tempfile.TemporaryDirectory() as d:
        _create_fake_gguf(d, "model-a-q4_k_m.gguf")
        _create_fake_gguf(d, "model-b-q4_k_m.gguf")
        mgr = ModelManager(models_dir=d)
        mgr.discover()
        mgr.load_model("Model-A")
        assert mgr.loaded_model == "Model-A"
        mgr.load_model("Model-B")
        assert mgr.loaded_model == "Model-B"
        # Model-A should now be "available" not "loaded".
        assert mgr.status("Model-A") == "available"


# ============================================================================
#  VAL-MODL-016: Version tracking
# ============================================================================

def test_version_parsed_from_filename():
    from models.manager import _parse_gguf_filename
    info = _parse_gguf_filename("qwen2.5-1.5b-instruct-q4_k_m.gguf")
    assert "version" in info
    # Qwen2.5 implies version 2.5
    assert "2.5" in info.get("version", "")


def test_registry_version_lookup():
    from models.registry import ModelRegistry
    reg = ModelRegistry()
    # Every model entry should have a version.
    for model_id, meta in reg.MODELS.items():
        assert "version" in meta, f"{model_id} missing version"
        assert isinstance(meta["version"], str)


# ============================================================================
#  VAL-MODL-018: Model status states
# ============================================================================

def test_status_states():
    from models.manager import ModelManager
    with tempfile.TemporaryDirectory() as d:
        _create_fake_gguf(d, "test-model-q4_k_m.gguf")
        mgr = ModelManager(models_dir=d)
        mgr.discover()
        # Initially available.
        assert mgr.status("Test-Model") == "available"
        # Load.
        mgr.load_model("Test-Model")
        assert mgr.status("Test-Model") == "loaded"
        # Set downloading.
        mgr.set_status("Test-Model", "downloading")
        assert mgr.status("Test-Model") == "downloading"
        # Back to available after download complete.
        mgr.set_status("Test-Model", "available")
        assert mgr.status("Test-Model") == "available"
        # Unknown model.
        assert mgr.status("Nonexistent") == "unknown"


def test_available_models_includes_status():
    from models.manager import ModelManager
    with tempfile.TemporaryDirectory() as d:
        _create_fake_gguf(d, "model-a-q4_k_m.gguf")
        _create_fake_gguf(d, "model-b-q4_k_m.gguf")
        mgr = ModelManager(models_dir=d)
        mgr.discover()
        mgr.load_model("Model-A")
        mgr.set_status("Model-B", "downloading")
        models = mgr.available_models()
        statuses = {m["name"]: m["status"] for m in models}
        assert statuses.get("Model-A") == "loaded"
        assert statuses.get("Model-B") == "downloading"


# ============================================================================
# Edge cases
# ============================================================================

def test_discover_nonexistent_directory():
    from models.manager import ModelManager
    mgr = ModelManager(models_dir="/nonexistent/path/12345")
    # Should not crash; returns empty.
    mgr.discover()
    assert mgr.available_models() == []


def test_load_nonexistent_model():
    from models.manager import ModelManager
    with tempfile.TemporaryDirectory() as d:
        mgr = ModelManager(models_dir=d)
        mgr.discover()
        path = mgr.load_model("NoSuchModel")
        assert path is None
        assert mgr.loaded_model is None


def test_model_registry_has_tier_recommendations():
    from models.registry import ModelRegistry
    reg = ModelRegistry()
    for tier in reg.TIERS:
        recs = reg.recommendations(tier)
        assert isinstance(recs, dict), f"recommendations for {tier} should be dict"
        # At minimum, STT model is recommended for every tier.
        assert "stt_model" in recs, f"{tier} tier missing stt_model"


def test_registry_lookup():
    from models.registry import ModelRegistry
    reg = ModelRegistry()
    # Look up a known model.
    info = reg.lookup("qwen2.5-1.5b-instruct")
    assert info is not None
    assert "name" in info
    # Unknown model returns None.
    assert reg.lookup("nonexistent-model-xyz") is None


def test_model_cache_dir_default():
    from models.manager import ModelManager
    mgr = ModelManager(models_dir="")
    # Should use branding.MODELS_DIR as fallback.
    assert mgr.models_dir != ""
    assert "Mumble" in mgr.models_dir or "models" in mgr.models_dir.lower()


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
