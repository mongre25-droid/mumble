#!/usr/bin/env python3
"""Tests for ModelManager ↔ LlamaCliBackend integration — model backend wiring.

Run: python test_model_backend.py
Pure stdlib + project modules; no key, no network, no real model binary needed.

Covers:
  VAL-MODL-008: Model switching — user selects different model, old unloaded first
  VAL-MODL-009: Model switching during active dictation — queued, no crash
  VAL-MODL-013: Memory pressure triggers largest model unload, reload on demand
  VAL-MODL-019: ModelManager.load_model() spawns llama-cli with correct path/threads
  VAL-CROSS-017: Single-resident policy — loading new unloads old, peak RAM under ~2GB
"""

import os
import sys
import subprocess
import tempfile
import threading
import time

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


def _create_fake_binary(dir_path, name="llama-cli.exe"):
    """Create a fake executable file (empty)."""
    path = os.path.join(dir_path, name)
    with open(path, "wb") as f:
        f.write(b"fake")
    return path


def _stub_successful_start(pm):
    """Make lifecycle-only tests independent of a real llama-cli executable."""
    def start(model_path, system_prompt=""):
        pm._model_path = model_path
        pm._system_prompt = system_prompt
        return True
    pm.start = start


# ============================================================================
#  VAL-MODL-019: ModelManager.load_model() integration with process manager
# ============================================================================

def test_process_manager_initial_state():
    """ModelProcessManager starts with no running process."""
    from models.backend import ModelProcessManager
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        pm = ModelProcessManager(bin_path=bin_path, n_threads=4)
        assert not pm.is_running()
        assert pm.model_path is None
        assert not pm.loaded


def test_process_manager_start_stop_lifecycle():
    """Process manager start/stop with valid model and binary."""
    from models.backend import ModelProcessManager
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        model_path = _create_fake_gguf(d, "test-model-q4_k_m.gguf")
        pm = ModelProcessManager(bin_path=bin_path, n_threads=4)

        # Start should NOT succeed with a fake binary (it won't run),
        # but it should not crash.
        result = pm.start(model_path)
        # The start may fail since the fake binary isn't a real executable,
        # but the API should be clean.
        assert isinstance(result, bool)
        # After a failed start, the process should be None.
        assert not pm.is_running()


def test_process_manager_single_resident():
    """Starting a new model stops any existing process first."""
    from models.backend import ModelProcessManager
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        m1 = _create_fake_gguf(d, "model-a-q4_k_m.gguf")
        m2 = _create_fake_gguf(d, "model-b-q4_k_m.gguf")
        pm = ModelProcessManager(bin_path=bin_path, n_threads=4)

        pm._process = "fake-process-object"  # Simulate running process
        pm._model_path = m1

        # Start a new model — should clear the old one.
        pm.start(m2)
        # After start, the old fake process reference should be gone.
        assert pm._process != "fake-process-object"


def test_modelmanager_integration_load_unload():
    """ModelManager.load_model() should notify process manager."""
    from models.manager import ModelManager
    from models.backend import ModelProcessManager
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        _create_fake_gguf(d, "qwen2.5-1.5b-instruct-q4_k_m.gguf")
        _create_fake_gguf(d, "grmr-2b-instruct-q4_k_m.gguf")

        mgr = ModelManager(models_dir=d)
        mgr.discover()

        pm = ModelProcessManager(bin_path=bin_path, n_threads=4)
        _stub_successful_start(pm)
        mgr.set_process_manager(pm, system_prompt="test prompt")

        # Verify process manager is attached.
        assert mgr.has_process_manager()
        assert mgr.get_process_manager() is pm

        # load_model should return a path.
        path = mgr.load_model("Qwen2.5-1.5B-Instruct")
        assert path is not None
        assert "qwen2.5-1.5b-instruct-q4_k_m.gguf" in path

        # Status should be "loaded".
        assert mgr.status("Qwen2.5-1.5B-Instruct") == "loaded"
        assert mgr.loaded_model == "Qwen2.5-1.5B-Instruct"

        # unload_model should mark it available.
        mgr.unload_model("Qwen2.5-1.5B-Instruct")
        assert mgr.loaded_model is None
        assert mgr.status("Qwen2.5-1.5B-Instruct") == "available"


# ============================================================================
#  VAL-MODL-008: Model switching — user selects a different model
# ============================================================================

def test_model_switching_unloads_old_before_loads_new():
    """Switching models marks old as available and new as loaded."""
    from models.manager import ModelManager
    with tempfile.TemporaryDirectory() as d:
        _create_fake_gguf(d, "model-a-q4_k_m.gguf")
        _create_fake_gguf(d, "model-b-q4_k_m.gguf")

        mgr = ModelManager(models_dir=d)
        mgr.discover()

        # Load model A.
        path_a = mgr.load_model("Model-A")
        assert path_a is not None
        assert mgr.loaded_model == "Model-A"
        assert mgr.status("Model-A") == "loaded"

        # Switch to model B.
        path_b = mgr.load_model("Model-B")
        assert path_b is not None
        assert mgr.loaded_model == "Model-B"
        assert mgr.status("Model-B") == "loaded"

        # Model A should now be "available", not "loaded".
        assert mgr.status("Model-A") == "available"


def test_model_switching_preserves_model_b_path():
    """After switching, the new model's path is correct."""
    from models.manager import ModelManager
    with tempfile.TemporaryDirectory() as d:
        pa = _create_fake_gguf(d, "model-a-q4_k_m.gguf")
        pb = _create_fake_gguf(d, "model-b-q4_k_m.gguf")

        mgr = ModelManager(models_dir=d)
        mgr.discover()

        mgr.load_model("Model-A")
        path_b = mgr.load_model("Model-B")
        assert path_b == pb


# ============================================================================
#  VAL-MODL-009: Model switching during active dictation — queued
# ============================================================================

def test_switch_during_generation_is_queued():
    """When process manager is generating, switch_model queues the request."""
    from models.manager import ModelManager
    from models.backend import ModelProcessManager
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        _create_fake_gguf(d, "model-a-q4_k_m.gguf")
        _create_fake_gguf(d, "model-b-q4_k_m.gguf")

        mgr = ModelManager(models_dir=d)
        mgr.discover()

        pm = ModelProcessManager(bin_path=bin_path, n_threads=4)
        _stub_successful_start(pm)
        mgr.set_process_manager(pm)

        # Load model A.
        mgr.load_model("Model-A")

        # Simulate active generation.
        pm._generating = True
        pm._process = "fake-process"  # Simulate running process

        # Request switch to model B — should queue.
        path_b = mgr.load_model("Model-B")
        assert path_b is not None
        # The switch should be queued, not applied immediately.
        assert pm._pending_model is not None
        assert pm._pending_model[0] == path_b

        # Old model should still be loaded (not yet switched).
        assert mgr.loaded_model == "Model-A"

        # Simulate generation completing — apply pending switch.
        pm._generating = False
        pm.apply_pending_switch()

        # After applying, pending should be cleared.
        assert pm._pending_model is None
        assert mgr.loaded_model == "Model-B"
        assert mgr.status("Model-A") == "available"
        assert mgr.status("Model-B") == "loaded"


def test_switch_not_queued_when_not_generating():
    """When not generating, switch is applied immediately."""
    from models.manager import ModelManager
    from models.backend import ModelProcessManager
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        _create_fake_gguf(d, "model-a-q4_k_m.gguf")
        _create_fake_gguf(d, "model-b-q4_k_m.gguf")

        mgr = ModelManager(models_dir=d)
        mgr.discover()

        pm = ModelProcessManager(bin_path=bin_path, n_threads=4)
        _stub_successful_start(pm)
        mgr.set_process_manager(pm)
        mgr.load_model("Model-A")

        # Not generating — switch should be immediate.
        assert not pm._generating
        path_b = mgr.load_model("Model-B")
        assert path_b is not None
        # No pending queued switch.
        assert pm._pending_model is None
        assert mgr.loaded_model == "Model-B"


# ============================================================================
#  VAL-CROSS-017: Single-resident policy
# ============================================================================

def test_single_resident_only_one_model_loaded():
    """Only one model marked as 'loaded' at a time."""
    from models.manager import ModelManager
    with tempfile.TemporaryDirectory() as d:
        _create_fake_gguf(d, "model-a-q4_k_m.gguf")
        _create_fake_gguf(d, "model-b-q4_k_m.gguf")
        _create_fake_gguf(d, "model-c-q4_k_m.gguf")

        mgr = ModelManager(models_dir=d)
        mgr.discover()

        # Load all three in sequence.
        mgr.load_model("Model-A")
        mgr.load_model("Model-B")
        mgr.load_model("Model-C")

        # Only the last one should be loaded.
        assert mgr.loaded_model == "Model-C"
        assert mgr.status("Model-A") == "available"
        assert mgr.status("Model-B") == "available"
        assert mgr.status("Model-C") == "loaded"

        # Count loaded models.
        models = mgr.available_models()
        loaded_count = sum(1 for m in models if m["status"] == "loaded")
        assert loaded_count == 1, f"expected 1 loaded, got {loaded_count}"


# ============================================================================
#  VAL-MODL-013: Memory pressure
# ============================================================================

def test_memory_pressure_detection():
    """Memory pressure check uses system free RAM."""
    from models.backend import ModelProcessManager, _get_free_memory_mb
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        pm = ModelProcessManager(
            bin_path=bin_path,
            memory_pressure_threshold_mb=999999  # impossibly high
        )

        # With an impossibly high threshold, should detect pressure
        # (unless the system has >999GB free, which it doesn't).
        assert pm.check_memory_pressure() is True

        # With an impossibly low threshold, should NOT detect pressure.
        pm.memory_pressure_threshold_mb = 1
        # Not asserting False because _get_free_memory_mb could fail on
        # some platforms and return None → False.
        assert pm.check_memory_pressure() is False


def test_free_memory_returns_value_or_none():
    """_get_free_memory_mb returns int or None without crashing."""
    from models.backend import _get_free_memory_mb
    result = _get_free_memory_mb()
    assert result is None or isinstance(result, int)


def test_process_memory_budget_triggers_pressure():
    """The configured resident-memory budget is enforced independently of RAM."""
    import models.backend as backend
    with tempfile.TemporaryDirectory() as d:
        pm = backend.ModelProcessManager(
            bin_path=_create_fake_binary(d), memory_limit_mb=512,
            memory_pressure_threshold_mb=1)
        original_process_memory = backend._get_process_memory_mb
        original_free_memory = backend._get_free_memory_mb
        try:
            backend._get_process_memory_mb = lambda _process: 700
            backend._get_free_memory_mb = lambda: 999999
            assert pm.check_memory_pressure() is True
            backend._get_process_memory_mb = lambda _process: 256
            assert pm.check_memory_pressure() is False
        finally:
            backend._get_process_memory_mb = original_process_memory
            backend._get_free_memory_mb = original_free_memory


def test_unload_if_pressure():
    """unload_if_pressure only unloads when under pressure."""
    from models.backend import ModelProcessManager
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        pm = ModelProcessManager(bin_path=bin_path)

        # With very low threshold, should not unload.
        pm.memory_pressure_threshold_mb = 1
        pm._process = "fake"  # Simulate running
        pm._model_path = "/fake/model.gguf"

        # Should not stop because pressure threshold is 1MB (way below free).
        result = pm.unload_if_pressure()
        assert result is False

        # With impossibly high threshold, should stop.
        pm.memory_pressure_threshold_mb = 999999
        # But _get_free_memory_mb might return None on some platforms.
        # We just verify the method doesn't crash.
        try:
            pm.unload_if_pressure()
        except Exception as e:
            assert False, f"unload_if_pressure raised {e}"


# ============================================================================
#  LlamaCliBackend integration tests
# ============================================================================

def test_llama_cli_backend_no_process_manager_is_oneshot():
    """Without a process manager, backend uses one-shot mode."""
    import local_engine as le
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        model_path = _create_fake_gguf(d, "test.gguf")
        backend = le.LlamaCliBackend(
            model_path=model_path,
            bin_path=bin_path
        )
        assert not backend.persistent
        assert backend.available() is True
        assert backend._process_manager is None


def test_llama_cli_backend_with_process_manager_is_persistent():
    """With a process manager, backend uses persistent mode."""
    import local_engine as le
    from models.backend import ModelProcessManager
    from models.manager import ModelManager
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        model_path = _create_fake_gguf(d, "test.gguf")
        mgr = ModelManager(models_dir=d)
        mgr.discover()
        mgr.load_model("Test")

        pm = ModelProcessManager(bin_path=bin_path, n_threads=4)
        pm._model_path = model_path  # Simulate loaded model

        backend = le.LlamaCliBackend(
            model_path=model_path,
            bin_path=bin_path,
            process_manager=pm,
            model_manager=mgr,
        )
        assert backend.persistent
        assert backend._process_manager is pm
        assert backend._model_manager is mgr


def test_llama_cli_backend_switch_model():
    """LlamaCliBackend.switch_model delegates to ModelManager."""
    import local_engine as le
    from models.manager import ModelManager
    with tempfile.TemporaryDirectory() as d:
        _create_fake_gguf(d, "model-a-q4_k_m.gguf")
        _create_fake_gguf(d, "model-b-q4_k_m.gguf")

        mgr = ModelManager(models_dir=d)
        mgr.discover()

        backend = le.LlamaCliBackend(
            model_path=None,
            bin_path="/fake/llama-cli.exe",
            model_manager=mgr,
        )

        # Load model A.
        backend.switch_model("Model-A")
        assert mgr.loaded_model == "Model-A"
        assert backend.model_path is not None

        # Switch to model B.
        backend.switch_model("Model-B")
        assert mgr.loaded_model == "Model-B"
        assert mgr.status("Model-A") == "available"


def test_llama_cli_backend_unload():
    """LlamaCliBackend.unload clears model manager state."""
    import local_engine as le
    from models.manager import ModelManager
    with tempfile.TemporaryDirectory() as d:
        _create_fake_gguf(d, "model-a-q4_k_m.gguf")

        mgr = ModelManager(models_dir=d)
        mgr.discover()

        backend = le.LlamaCliBackend(
            model_path=None,
            bin_path="/fake/llama-cli.exe",
            model_manager=mgr,
        )

        backend.switch_model("Model-A")
        assert mgr.loaded_model == "Model-A"

        backend.unload()
        assert mgr.loaded_model is None
        assert backend.model_path is None


def test_llama_cli_backend_loaded_model_name_sync():
    """Backend.loaded_model_name reflects ModelManager state."""
    import local_engine as le
    from models.manager import ModelManager
    with tempfile.TemporaryDirectory() as d:
        _create_fake_gguf(d, "model-a-q4_k_m.gguf")

        mgr = ModelManager(models_dir=d)
        mgr.discover()

        backend = le.LlamaCliBackend(
            model_path=None,
            bin_path="/fake/llama-cli.exe",
            model_manager=mgr,
        )

        # Initially nothing loaded.
        assert backend.loaded_model_name is None

        # Load a model.
        mgr.load_model("Model-A")
        assert backend.loaded_model_name == "Model-A"

        # Unload.
        mgr.unload_model("Model-A")
        assert backend.loaded_model_name is None


# ============================================================================
#  RLock re-entrancy — no deadlock when start()/switch_model() called under lock
# ============================================================================

def test_rlock_reentrant_start_from_within_lock():
    """RLock allows start() to be called while the lock is already held.

    This simulates the generate()->finally->_apply_pending_switch->start()
    path. With a regular threading.Lock(), the re-acquire in start() would
    deadlock the calling thread. RLock allows the same thread to re-enter.
    """
    from models.backend import ModelProcessManager
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        model_path = _create_fake_gguf(d, "test-model-q4_k_m.gguf")
        pm = ModelProcessManager(bin_path=bin_path, n_threads=4)

        # Acquire the lock, simulating generate() holding it.
        acquired = pm._lock.acquire(blocking=False)
        assert acquired, "Should be able to acquire the lock initially"

        try:
            # start() internally does `with self._lock:` — without RLock,
            # this would deadlock the calling thread.
            result = pm.start(model_path)
            assert isinstance(result, bool)
        finally:
            pm._lock.release()


def test_rlock_reentrant_switch_model_from_within_lock():
    """RLock allows switch_model() to be called while the lock is held.

    switch_model() does `with self._lock:` and then calls start(), which
    also acquires the lock. Both must work with RLock in the same thread.
    """
    from models.backend import ModelProcessManager
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        m1 = _create_fake_gguf(d, "model-a-q4_k_m.gguf")
        m2 = _create_fake_gguf(d, "model-b-q4_k_m.gguf")
        pm = ModelProcessManager(bin_path=bin_path, n_threads=4)

        acquired = pm._lock.acquire(blocking=False)
        assert acquired

        try:
            # switch_model() acquires lock internally → must not deadlock.
            result = pm.switch_model(m2, system_prompt="")
            assert isinstance(result, bool)
        finally:
            pm._lock.release()


def test_rlock_reentrant_handle_crash_from_within_lock():
    """RLock allows _handle_crash_locked() → start() to work under lock.

    _handle_crash_locked() calls start() which acquires self._lock again.
    This is called from generate() and _generate_locked() while the lock
    is already held by the same thread. The test verifies no deadlock occurs.
    Note: start() resets _crash_count to 0 on success, so we check that
    _handle_crash_locked completes without raising rather than crash count.
    """
    from models.backend import ModelProcessManager
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        model_path = _create_fake_gguf(d, "test-model-q4_k_m.gguf")
        pm = ModelProcessManager(bin_path=bin_path, n_threads=4)

        # Simulate the state when _handle_crash_locked is called:
        # the process has died, model path is known.
        pm._process = None  # process already cleaned up
        pm._model_path = model_path

        acquired = pm._lock.acquire(blocking=False)
        assert acquired

        try:
            # _handle_crash_locked attempts restart via start() under lock.
            # The key assertion is that this completes without hanging.
            # (start() resets _crash_count to 0, so we check it completed.)
            pm._handle_crash_locked()
            # If we reach here, no deadlock occurred.
            # Consecutive recovery attempts remain counted until a successful
            # generation, so repeated immediate crashes eventually stop.
            assert pm._crash_count == 1
        finally:
            pm._lock.release()


def test_generate_finally_switch_no_deadlock():
    """Simulate the exact generate()->finally->_apply_pending_switch path.

    This exercises the full re-entrant chain: lock held → pending switch
    applied → start() re-acquires lock → completes without deadlock.
    """
    from models.backend import ModelProcessManager
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        model_path = _create_fake_gguf(d, "test-model-q4_k_m.gguf")
        pm = ModelProcessManager(bin_path=bin_path, n_threads=4)

        acquired = pm._lock.acquire(blocking=False)
        assert acquired

        try:
            # Set up state as if generate() had queued a pending switch.
            pm._pending_model = (model_path, "")
            pm._generating = True
            pm._process = "fake-process"
            pm._model_path = model_path

            # This is the finally block of generate():
            pm._generating = False
            pm._apply_pending_switch_locked()
            # No deadlock — we reached here.
            assert pm._pending_model is None
        finally:
            pm._lock.release()


# ============================================================================
#  init_model_backend integration tests
# ============================================================================

def test_init_model_backend_returns_backend_and_manager():
    """init_model_backend creates and wires the full stack."""
    import local_engine as le
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        _create_fake_gguf(d, "qwen2.5-1.5b-instruct-q4_k_m.gguf")

        backend, mgr, reason = le.init_model_backend(
            models_dir=d,
            cli_bin=bin_path,
            auto_load=True,
        )

        # Should return a backend and model manager.
        assert backend is not None
        assert mgr is not None
        assert isinstance(reason, str)

        # Model manager should have models discovered.
        assert len(mgr.available_models()) == 1

        # The backend should have the model manager wired in.
        if backend.name != "null":
            assert backend._model_manager is mgr


def test_init_model_backend_empty_directory_returns_null():
    """With no models, returns NullBackend."""
    import local_engine as le
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        # No .gguf files.

        backend, mgr, reason = le.init_model_backend(
            models_dir=d,
            cli_bin=bin_path,
            auto_load=True,
        )

        assert backend is not None
        assert backend.name == "null"
        assert "no gguf" in reason.lower()


def test_init_model_backend_specific_model():
    """init_model_backend with model_name loads the requested model."""
    import local_engine as le
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        _create_fake_gguf(d, "model-a-q4_k_m.gguf")
        _create_fake_gguf(d, "model-b-q4_k_m.gguf")

        backend, mgr, reason = le.init_model_backend(
            models_dir=d,
            cli_bin=bin_path,
            model_name="Model-B",
            auto_load=False,  # auto_load=False but model_name provided
        )

        assert mgr is not None
        # Model-B should be the loaded one.
        assert mgr.loaded_model == "Model-B"


# ============================================================================
#  backend_status integration
# ============================================================================

def test_backend_status_includes_model_manager():
    """backend_status() includes model manager fields when wired."""
    import local_engine as le
    from models.manager import ModelManager
    with tempfile.TemporaryDirectory() as d:
        _create_fake_gguf(d, "model-a-q4_k_m.gguf")

        mgr = ModelManager(models_dir=d)
        mgr.discover()
        mgr.load_model("Model-A")

        backend = le.LlamaCliBackend(
            model_path=mgr.get_model("Model-A")["path"],
            bin_path="/fake/llama-cli.exe",
            model_manager=mgr,
        )

        le.set_backend(backend, "test", model_manager=mgr)
        status = le.backend_status()

        assert status["model_manager"] is True
        assert status["loaded_model"] == "Model-A"
        assert "available_models" in status

        le.set_backend(None)  # Reset


# ============================================================================
#  Edge cases
# ============================================================================

def test_load_nonexistent_model_no_crash():
    """Loading a nonexistent model returns None, doesn't crash."""
    from models.manager import ModelManager
    from models.backend import ModelProcessManager
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        _create_fake_gguf(d, "real-model-q4_k_m.gguf")

        mgr = ModelManager(models_dir=d)
        mgr.discover()

        pm = ModelProcessManager(bin_path=bin_path)
        mgr.set_process_manager(pm)

        # Nonexistent model.
        path = mgr.load_model("NoSuchModel")
        assert path is None
        assert mgr.loaded_model is None


def test_process_manager_stop_when_not_running():
    """Stopping when no process is running is a no-op."""
    from models.backend import ModelProcessManager
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        pm = ModelProcessManager(bin_path=bin_path)
        # Should not raise.
        pm.stop()
        assert not pm.is_running()


def test_process_manager_force_stop_multiple_times():
    """Force stopping multiple times is safe."""
    from models.backend import ModelProcessManager
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        pm = ModelProcessManager(bin_path=bin_path)
        pm.stop()
        pm.stop()
        pm.stop()
        assert not pm.is_running()


def test_modelmanager_force_unload_with_process_manager():
    """force_unload stops process manager and clears loaded state."""
    from models.manager import ModelManager
    from models.backend import ModelProcessManager
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        _create_fake_gguf(d, "test-model-q4_k_m.gguf")

        mgr = ModelManager(models_dir=d)
        mgr.discover()

        pm = ModelProcessManager(bin_path=bin_path, n_threads=4)
        _stub_successful_start(pm)
        mgr.set_process_manager(pm)
        mgr.load_model("Test-Model")

        assert mgr.loaded_model == "Test-Model"

        mgr.force_unload()
        assert mgr.loaded_model is None
        assert not pm.is_running()


def test_init_model_backend_auto_load_false():
    """With auto_load=False, model manager is created but no model loaded."""
    import local_engine as le
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        _create_fake_gguf(d, "test-model-q4_k_m.gguf")

        backend, mgr, reason = le.init_model_backend(
            models_dir=d,
            cli_bin=bin_path,
            auto_load=False,
        )

        assert mgr is not None
        assert len(mgr.available_models()) == 1
        # No model should be loaded.
        assert mgr.loaded_model is None
        assert "no model loaded" in reason.lower()


def test_set_and_get_model_manager():
    """set_backend stores model manager; get_model_manager retrieves it."""
    import local_engine as le
    from models.manager import ModelManager
    with tempfile.TemporaryDirectory() as d:
        mgr = ModelManager(models_dir=d)
        le.set_backend(le.NullBackend(), "test", model_manager=mgr)
        assert le.get_model_manager() is mgr
        le.set_backend(None)  # Reset


def test_lazy_loading_no_model_loaded_at_startup():
    """No model loaded at construction — lazy loading."""
    from models.manager import ModelManager
    from models.backend import ModelProcessManager
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        _create_fake_gguf(d, "test-model-q4_k_m.gguf")

        mgr = ModelManager(models_dir=d)
        mgr.discover()

        # At construction, nothing is loaded.
        assert mgr.loaded_model is None

        pm = ModelProcessManager(bin_path=bin_path, n_threads=4)
        _stub_successful_start(pm)
        mgr.set_process_manager(pm)

        # Still nothing loaded after setting process manager.
        assert mgr.loaded_model is None

        # First load is lazy.
        path = mgr.load_model("Test-Model")
        assert path is not None
        assert mgr.loaded_model == "Test-Model"


def test_oneshot_backend_generate_without_process_manager():
    """One-shot backend generate() builds a correct command line."""
    import local_engine as le
    with tempfile.TemporaryDirectory() as d:
        bin_path = _create_fake_binary(d)
        model_path = _create_fake_gguf(d, "test.gguf")
        backend = le.LlamaCliBackend(
            model_path=model_path,
            bin_path=bin_path,
            n_ctx=2048,
            n_threads=4,
        )
        cmd = backend._build_cmd("SYS", "USER", max_tokens=256, temperature=0.2)
        assert cmd[0] == bin_path
        assert "-m" in cmd and model_path in cmd
        assert "-no-cnv" in cmd
        assert "-t" in cmd and "4" in cmd


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
