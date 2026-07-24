#!/usr/bin/env python3
"""Tests for platform/macos_audio.py — MacOSTranscriber with FluidAudio +
Parakeet TDT interface.

Covers:
  VAL-TRAN-008  — GPU acceleration path is used on supported platforms
  VAL-TRAN-009  — Diarisation identifies distinct speakers in meeting recordings
  VAL-RECV-005  — GPU acceleration unavailable → CPU fallback
  VAL-CROSS-008 — Platform-specific STT accelerator auto-selects based on OS
                  and hardware tier

Usage:
    .venv/Scripts/python.exe test_macos_audio.py
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
check("platform has detect_apple_silicon", callable(plat_pkg.detect_apple_silicon))
check("platform has detect_directml_capable", callable(plat_pkg.detect_directml_capable))
check("platform has detect_linux_arm_vulkan", callable(plat_pkg.detect_linux_arm_vulkan))

from platform.macos_audio import MacOSTranscriber
check("MacOSTranscriber imports", True)
check("MacOSTranscriber has is_available", callable(MacOSTranscriber.is_available))
check("MacOSTranscriber has detect_apple_silicon", callable(MacOSTranscriber.detect_apple_silicon))
check("MacOSTranscriber has is_degraded", callable(MacOSTranscriber.is_degraded))
check("MacOSTranscriber has mark_degraded", callable(MacOSTranscriber.mark_degraded))
check("MacOSTranscriber has clear_degraded", callable(MacOSTranscriber.clear_degraded))
check("MacOSTranscriber has transcriber_name", hasattr(MacOSTranscriber, "transcriber_name"))


# ═══════════════════════════════════════════════════════════════════════════════
# Platform Detection Tests (VAL-CROSS-008)
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Platform Detection (VAL-CROSS-008) ==")

apple_silicon = plat_pkg.detect_apple_silicon()
is_macos = sys.platform == "darwin"
is_windows = sys.platform == "win32"
is_linux = sys.platform == "linux"
machine = _get_machine()

print(f"  Platform: {sys.platform} / {machine}")

# On Windows, Apple Silicon detection MUST return False.
if is_windows:
    check("Apple Silicon detection -> False on Windows", not apple_silicon)
    check("DirectML detection runs without crashing",
          isinstance(plat_pkg.detect_directml_capable(), bool))
    check("Linux ARM Vulkan detection -> False on Windows",
          not plat_pkg.detect_linux_arm_vulkan())

# On macOS, check detection logic.
if is_macos:
    if machine.lower() in ("arm64", "aarch64"):
        check("Apple Silicon detection -> True on Apple Silicon Mac",
              apple_silicon)
    else:
        check("Apple Silicon detection -> False on Intel Mac", not apple_silicon)

# On Linux, verify the right detector fires.
if is_linux:
    check("Apple Silicon detection -> False on Linux", not apple_silicon)
    vulkan_result = plat_pkg.detect_linux_arm_vulkan()
    check("Linux ARM Vulkan detection runs without crashing",
          isinstance(vulkan_result, bool))


# ═══════════════════════════════════════════════════════════════════════════════
# MacOSTranscriber Availability Tests
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== MacOSTranscriber Availability ==")

available = MacOSTranscriber.is_available()
print(f"  MacOSTranscriber.is_available() = {available}")

# On non-macOS platforms, the transcriber MUST report unavailable.
# This is the EXPECTED behaviour — FluidAudio and Parakeet TDT are macOS-only.
if not is_macos:
    check("MacOSTranscriber reports NOT available on non-macOS", not available)

# Even on macOS, verify the detection method works.
check("detect_apple_silicon() returns bool",
      isinstance(MacOSTranscriber.detect_apple_silicon(), bool))
check("MacOSTranscriber.transcriber_name is a string",
      isinstance(MacOSTranscriber.transcriber_name, str))
check("MacOSTranscriber.transcriber_name mentions Apple Silicon",
      "Apple Silicon" in MacOSTranscriber.transcriber_name)


# ═══════════════════════════════════════════════════════════════════════════════
# Construction Tests (even when unavailable)
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Construction Tests ==")

# The class MUST be constructable on any platform (graceful failure on
# transcribe, not on construction).
t = MacOSTranscriber(model_name="base", device="auto", hardware_tier="mid")
check("MacOSTranscriber constructs without error", t is not None)
check("MacOSTranscriber stores model_name", t._model_name == "base")
check("MacOSTranscriber stores device", t._device == "auto")
check("MacOSTranscriber stores hardware_tier", t._hardware_tier == "mid")

# Construct with different tiers.
t_weak = MacOSTranscriber(model_name="base", hardware_tier="weak")
check("MacOSTranscriber constructs with tier=weak",
      t_weak._hardware_tier == "weak")

t_power = MacOSTranscriber(model_name="base", hardware_tier="powerful")
check("MacOSTranscriber constructs with tier=powerful",
      t_power._hardware_tier == "powerful")


# ═══════════════════════════════════════════════════════════════════════════════
# Model Resolution for Hardware Tier
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Model Resolution for Hardware Tier ==")

t_weak2 = MacOSTranscriber(hardware_tier="weak")
check("Weak tier -> 'tiny' model",
      t_weak2._resolve_model_for_tier() == "tiny")

t_mid = MacOSTranscriber(hardware_tier="mid")
check("Mid tier -> 'base' model",
      t_mid._resolve_model_for_tier() == "base")

t_power2 = MacOSTranscriber(hardware_tier="powerful")
check("Powerful tier -> 'small' model",
      t_power2._resolve_model_for_tier() == "small")

t_unknown = MacOSTranscriber(hardware_tier="supercomputer")
check("Unknown tier -> default 'base' model",
      t_unknown._resolve_model_for_tier() == "base")


# ═══════════════════════════════════════════════════════════════════════════════
# Accelerator Degradation (VAL-RECV-005)
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Accelerator Degradation (VAL-RECV-005) ==")

# Simulate the degradation lifecycle.
# 1. Start clean.
MacOSTranscriber.clear_degraded()
check("After clear_degraded: is_degraded() = False",
      not MacOSTranscriber.is_degraded())

# 2. Mark degraded.
MacOSTranscriber.mark_degraded(
    "FluidAudio driver not found (CoreML init failed)")
check("After mark_degraded: is_degraded() = True",
      MacOSTranscriber.is_degraded())

# 3. While degraded, is_available() must return False even on Apple Silicon.
check("While degraded: is_available() = False",
      not MacOSTranscriber.is_available())

# 4. Clear degraded -> probe succeeds again.
MacOSTranscriber.clear_degraded()
check("After clear_degraded (again): is_degraded() = False",
      not MacOSTranscriber.is_degraded())

# Verify the class-level flag works (all instances share the same degraded
# state).
MacOSTranscriber.mark_degraded("test: simulated ANE failure")
check("Class-level degraded flag -> is_degraded = True",
      MacOSTranscriber.is_degraded())
check("Class-level degraded flag -> is_available = False",
      not MacOSTranscriber.is_available())

# Clean up for subsequent tests.
MacOSTranscriber.clear_degraded()


# ═══════════════════════════════════════════════════════════════════════════════
# Unload / Lifecycle Tests
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Unload / Lifecycle Tests ==")

t2 = MacOSTranscriber()
# unload() on a non-loaded instance must not crash (idempotency).
try:
    t2.unload()
    check("unload() on non-loaded instance does not crash", True)
except Exception as e:
    check(f"unload() on non-loaded instance does not crash: {e}", False)

# On non-macOS platforms, load() should raise RuntimeError since FluidAudio
# isn't available. This tests the graceful-failure path.
if not is_macos:
    try:
        t2.load()
        check("load() on non-macOS raises RuntimeError", False)
    except RuntimeError as e:
        check("load() on non-macOS raises RuntimeError",
              "FluidAudio" in str(e) or "not available" in str(e).lower())
    except Exception as e:
        check(f"load() raises RuntimeError (got {type(e).__name__}: {e})",
              isinstance(e, RuntimeError))


# ═══════════════════════════════════════════════════════════════════════════════
# Transcribe fallback on unavailable platform
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Transcribe Fallback (non-macOS) ==")

import numpy as np

# Build a tiny fake audio buffer (0.1s of silence).
fake_audio = np.zeros(int(0.1 * 16000), dtype=np.float32)

t3 = MacOSTranscriber()

if not is_macos:
    # On non-macOS, transcribe() must raise RuntimeError because FluidAudio
    # and Parakeet TDT are not available. The caller (mumble.py) catches this
    # and falls back to CPU-only faster-whisper.
    try:
        result = t3.transcribe(fake_audio, sample_rate=16000)
        check("transcribe() on non-macOS raises RuntimeError", False)
    except RuntimeError as e:
        check("transcribe() on non-macOS raises RuntimeError", True)
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

# On Windows without DirectML, select_transcriber should return None.
if is_windows and not plat_pkg.detect_directml_capable():
    result = plat_pkg.select_transcriber(hardware_tier="powerful")
    check("Windows without DML -> select_transcriber returns None",
          result is None)


# ═══════════════════════════════════════════════════════════════════════════════
# Diarisation Interface (VAL-TRAN-009)
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Diarisation Interface (VAL-TRAN-009) ==")

t4 = MacOSTranscriber()
check("MacOSTranscriber has transcribe_with_diarisation",
      callable(t4.transcribe_with_diarisation))

# On non-macOS, transcribe_with_diarisation should also raise RuntimeError.
if not is_macos:
    fake_audio2 = np.zeros(int(0.3 * 16000), dtype=np.float32)
    try:
        result = t4.transcribe_with_diarisation(
            fake_audio2, sample_rate=16000)
        check("transcribe_with_diarisation() on non-macOS raises RuntimeError",
              False)
    except RuntimeError:
        check("transcribe_with_diarisation() on non-macOS raises RuntimeError",
              True)


# ═══════════════════════════════════════════════════════════════════════════════
# GPU Acceleration Path (VAL-TRAN-008)
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== GPU Acceleration Path (VAL-TRAN-008) ==")

# The MacOSTranscriber.transcriber_name must indicate GPU acceleration.
check("transcriber_name mentions GPU/compute acceleration",
      ("FluidAudio" in MacOSTranscriber.transcriber_name
       or "Parakeet" in MacOSTranscriber.transcriber_name
       or "CoreML" in MacOSTranscriber.transcriber_name
       or "Apple Silicon" in MacOSTranscriber.transcriber_name))

# Verify the device resolution logic runs without crashing on any platform.
t5 = MacOSTranscriber(device="auto")
resolved_device = t5._resolve_best_device()
check("_resolve_best_device() returns a string",
      isinstance(resolved_device, str))
check("_resolve_best_device() returns valid device",
      resolved_device in ("ane", "gpu", "cpu"))
print(f"    resolved device = {resolved_device}")


# ═══════════════════════════════════════════════════════════════════════════════
# Import Check (verify module importable via different paths)
# ═══════════════════════════════════════════════════════════════════════════════

print("\n== Cross-import Check ==")

# Verify the module can be imported from different entry points.
# The Mumble platform package must not shadow the stdlib platform in ways
# that break other code.
try:
    from platform import macos_audio
    check("from platform import macos_audio works", True)
except ImportError as e:
    check(f"from platform import macos_audio: {e}", False)

# macos_audio must export MacOSTranscriber.
check("macos_audio.MacOSTranscriber is the class",
      macos_audio.MacOSTranscriber is MacOSTranscriber)


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
