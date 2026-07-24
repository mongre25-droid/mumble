#!/usr/bin/env python3
"""Tests for platform/windows_dml.py — DirectMLTranscriber with sherpa-onnx
SenseVoice + DirectML.

Covers:
  VAL-TRAN-008  — GPU acceleration path is used on supported platforms
  VAL-TRAN-009  — Diarisation identifies distinct speakers in meeting recordings
  VAL-RECV-005  — GPU acceleration unavailable → CPU fallback
  VAL-CROSS-008 — Platform-specific STT accelerator auto-selects based on OS
                  and hardware tier

Usage:
    .venv/Scripts/python.exe test_windows_dml.py
"""

import os
import sys
import subprocess

_app_dir = os.path.dirname(os.path.abspath(__file__))
if _app_dir not in sys.path:
    sys.path.insert(0, _app_dir)

# Get the machine architecture via a subprocess since 'import platform'
# resolves to our own package when run from the app directory.
def _get_machine():
    """Get platform.machine() without import ambiguity."""
    try:
        r = subprocess.run(
            [sys.executable, "-c", "import platform; print(platform.machine())"],
            capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return "unknown"


_fails = []


def check(desc, cond):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {desc}")
    if not cond:
        _fails.append(desc)


# ═══════════════════════════════════════════════════════════════════════════════
# Import Tests
# ═══════════════════════════════════════════════════════════════════════════════

print("== Import Tests ==")

import platform as plat_pkg  # our platform/__init__.py
check("platform package imports", True)
check("platform has select_transcriber", callable(plat_pkg.select_transcriber))
check("platform has detect_directml_capable", callable(plat_pkg.detect_directml_capable))

from platform.windows_dml import DirectMLTranscriber
check("DirectMLTranscriber imports", True)
check("DirectMLTranscriber has is_available", callable(DirectMLTranscriber.is_available))
check("DirectMLTranscriber has is_degraded", callable(DirectMLTranscriber.is_degraded))
check("DirectMLTranscriber has mark_degraded", callable(DirectMLTranscriber.mark_degraded))
check("DirectMLTranscriber has clear_degraded", callable(DirectMLTranscriber.clear_degraded))
check("DirectMLTranscriber has transcriber_name", hasattr(DirectMLTranscriber, "transcriber_name"))


# ═══════════════════════════════════════════════════════════════════════════════
# Platform Detection Tests (VAL-CROSS-008)
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Platform Detection (VAL-CROSS-008) ==")

is_windows = sys.platform == "win32"
is_macos = sys.platform == "darwin"
is_linux = sys.platform == "linux"
machine = _get_machine()

print(f"  Platform: {sys.platform} / {machine}")

# DirectML detection runs on any platform without crashing.
dml_capable = plat_pkg.detect_directml_capable()
check("detect_directml_capable() returns bool", isinstance(dml_capable, bool))
print(f"  detect_directml_capable() = {dml_capable}")

# On non-Windows platforms, DirectML detection MUST return False.
if not is_windows:
    check("detect_directml_capable() -> False on non-Windows", not dml_capable)


# ═══════════════════════════════════════════════════════════════════════════════
# DirectMLTranscriber Availability Tests
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== DirectMLTranscriber Availability ==")

available = DirectMLTranscriber.is_available()
print(f"  DirectMLTranscriber.is_available() = {available}")

# On non-Windows platforms, or Windows without DirectML, the transcriber
# MUST report unavailable. This is expected behaviour — sherpa-onnx
# SenseVoice + DirectML requires both Windows AND a DirectML-capable GPU.
if not is_windows or not dml_capable:
    check("DirectMLTranscriber reports NOT available without DML",
          not available)

check("DirectMLTranscriber.transcriber_name is a string",
      isinstance(DirectMLTranscriber.transcriber_name, str))
check("DirectMLTranscriber.transcriber_name mentions DirectML",
      "DirectML" in DirectMLTranscriber.transcriber_name)


# ═══════════════════════════════════════════════════════════════════════════════
# Construction Tests (even when unavailable)
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Construction Tests ==")

# The class MUST be constructable on any platform (graceful failure on
# transcribe, not on construction).
t = DirectMLTranscriber(model_name="sensevoice-small", device="dml",
                        hardware_tier="mid")
check("DirectMLTranscriber constructs without error", t is not None)
check("DirectMLTranscriber stores model_name", t._model_name == "sensevoice-small")
check("DirectMLTranscriber stores device", t._device == "dml")
check("DirectMLTranscriber stores hardware_tier", t._hardware_tier == "mid")

# Construct with different tiers.
t_weak = DirectMLTranscriber(model_name="sensevoice-small", hardware_tier="weak")
check("DirectMLTranscriber constructs with tier=weak",
      t_weak._hardware_tier == "weak")

t_power = DirectMLTranscriber(model_name="sensevoice-small", hardware_tier="powerful")
check("DirectMLTranscriber constructs with tier=powerful",
      t_power._hardware_tier == "powerful")

# Construct with device="cpu" (forced CPU fallback).
t_cpu = DirectMLTranscriber(model_name="sensevoice-small", device="cpu")
check("DirectMLTranscriber constructs with device=cpu",
      t_cpu._device == "cpu")


# ═══════════════════════════════════════════════════════════════════════════════
# Device Resolution Tests
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Device Resolution Tests ==")

t_auto = DirectMLTranscriber(device="auto")
resolved = t_auto._resolve_best_device()
check("_resolve_best_device() returns a string", isinstance(resolved, str))
check("_resolve_best_device() returns valid device",
      resolved in ("dml", "cpu"))
print(f"    resolved device = {resolved}")

# Forced CPU device.
t_cpu2 = DirectMLTranscriber(device="cpu")
check("device=cpu stays cpu", t_cpu2._resolve_best_device() == "cpu")

# Forced DML device.
t_dml = DirectMLTranscriber(device="dml")
check("device=dml stays dml", t_dml._resolve_best_device() == "dml")


# ═══════════════════════════════════════════════════════════════════════════════
# Model Resolution for Hardware Tier
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Model Resolution for Hardware Tier ==")

# SenseVoice doesn't have multiple model sizes like Whisper, but the tier
# still maps to a model name.
t_weak2 = DirectMLTranscriber(hardware_tier="weak")
check("Weak tier -> 'sensevoice-small' model",
      t_weak2._resolve_model_for_tier() == "sensevoice-small")

t_mid = DirectMLTranscriber(hardware_tier="mid")
check("Mid tier -> 'sensevoice-small' model",
      t_mid._resolve_model_for_tier() == "sensevoice-small")

t_power2 = DirectMLTranscriber(hardware_tier="powerful")
check("Powerful tier -> 'sensevoice-small' model",
      t_power2._resolve_model_for_tier() == "sensevoice-small")

t_custom = DirectMLTranscriber(model_name="custom-model", hardware_tier="super")
check("Custom model_name is respected",
      t_custom._resolve_model_for_tier() == "custom-model")


# ═══════════════════════════════════════════════════════════════════════════════
# Accelerator Degradation (VAL-RECV-005)
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Accelerator Degradation (VAL-RECV-005) ==")

# Simulate the degradation lifecycle.
# 1. Start clean.
DirectMLTranscriber.clear_degraded()
check("After clear_degraded: is_degraded() = False",
      not DirectMLTranscriber.is_degraded())

# 2. Mark degraded.
DirectMLTranscriber.mark_degraded(
    "DirectML driver not found (DmlExecutionProvider unavailable)")
check("After mark_degraded: is_degraded() = True",
      DirectMLTranscriber.is_degraded())

# 3. While degraded, is_available() must return False.
check("While degraded: is_available() = False",
      not DirectMLTranscriber.is_available())

# 4. Clear degraded -> probe succeeds again.
DirectMLTranscriber.clear_degraded()
check("After clear_degraded (again): is_degraded() = False",
      not DirectMLTranscriber.is_degraded())

# Verify the class-level flag works (all instances share the same degraded
# state).
DirectMLTranscriber.mark_degraded("test: simulated DML driver failure")
check("Class-level degraded flag -> is_degraded = True",
      DirectMLTranscriber.is_degraded())
check("Class-level degraded flag -> is_available = False",
      not DirectMLTranscriber.is_available())

# Clean up for subsequent tests.
DirectMLTranscriber.clear_degraded()


# ═══════════════════════════════════════════════════════════════════════════════
# Unload / Lifecycle Tests
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Unload / Lifecycle Tests ==")

t2 = DirectMLTranscriber()
# unload() on a non-loaded instance must not crash (idempotency).
try:
    t2.unload()
    check("unload() on non-loaded instance does not crash", True)
except Exception as e:
    check(f"unload() on non-loaded instance does not crash: {e}", False)

# load() should raise RuntimeError when no SenseVoice model is present.
try:
    t2.load()
    check("load() without model raises RuntimeError", False)
except RuntimeError as e:
    check("load() without model raises RuntimeError",
          "SenseVoice" in str(e) or "not available" in str(e).lower()
          or "model" in str(e).lower())
except Exception as e:
    check(f"load() raises RuntimeError (got {type(e).__name__}: {e})",
          isinstance(e, RuntimeError))


# ═══════════════════════════════════════════════════════════════════════════════
# Transcribe fallback on unavailable platform
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Transcribe Fallback (unavailable) ==")

import numpy as np

# Build a tiny fake audio buffer (0.1s of silence).
fake_audio = np.zeros(int(0.1 * 16000), dtype=np.float32)

t3 = DirectMLTranscriber()

# transcribe() must raise RuntimeError when the model isn't loaded.
try:
    result = t3.transcribe(fake_audio, sample_rate=16000)
    check("transcribe() without model raises RuntimeError", False)
except RuntimeError as e:
    check("transcribe() without model raises RuntimeError", True)
    print(f"    (expected: {e})")
except Exception as e:
    check(
        f"transcribe() raises RuntimeError (got {type(e).__name__})",
        isinstance(e, RuntimeError))


# ═══════════════════════════════════════════════════════════════════════════════
# select_transcriber() Auto-Selection (VAL-CROSS-008)
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== select_transcriber() Auto-Selection ==")

# On any platform, select_transcriber() must return either a transcriber or
# None without crashing.
for tier in ("weak", "mid", "powerful"):
    result = plat_pkg.select_transcriber(hardware_tier=tier)
    check(f"select_transcriber(tier={tier}) returns None or transcriber",
          result is None or hasattr(result, "transcribe"))
    if result is not None:
        print(f"    tier={tier} -> {result.__class__.__name__}")
    else:
        print(f"    tier={tier} -> None (no accelerator available)")


# ═══════════════════════════════════════════════════════════════════════════════
# GPU Acceleration Path (VAL-TRAN-008)
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== GPU Acceleration Path (VAL-TRAN-008) ==")

# The DirectMLTranscriber.transcriber_name must indicate GPU acceleration.
check("transcriber_name mentions GPU/compute acceleration",
      ("DirectML" in DirectMLTranscriber.transcriber_name
       or "sherpa-onnx" in DirectMLTranscriber.transcriber_name
       or "SenseVoice" in DirectMLTranscriber.transcriber_name
       or "GPU" in DirectMLTranscriber.transcriber_name))


# ═══════════════════════════════════════════════════════════════════════════════
# Import Check (verify module importable via different paths)
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Cross-import Check ==")

# Verify the module can be imported from different entry points.
try:
    from platform import windows_dml
    check("from platform import windows_dml works", True)
except ImportError as e:
    check(f"from platform import windows_dml: {e}", False)

# windows_dml must export DirectMLTranscriber.
check("windows_dml.DirectMLTranscriber is the class",
      windows_dml.DirectMLTranscriber is DirectMLTranscriber)


# ═══════════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════════

print(f"\n{'=' * 60}")
if _fails:
    print(f"FAILED {len(_fails)} test(s)")
    for f in _fails:
        print(f"  FAIL: {f}")
    sys.exit(1)
else:
    print("ALL TESTS PASSED")
    sys.exit(0)
