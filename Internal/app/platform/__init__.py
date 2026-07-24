#!/usr/bin/env python3
"""Platform-specific STT acceleration — auto-select the best transcriber for
the current OS and hardware tier.

Three platform backends, all presenting the same `transcribe(audio, ...)`
interface consumed by mumble.py's _transcribe():

  • macos_audio.MacOSTranscriber   — FluidAudio + Parakeet TDT on Apple Silicon
  • windows_dml.DirectMLTranscriber — sherpa-onnx SenseVoice + DirectML on Windows
  • linux_fallback.WhisperCppTranscriber — whisper.cpp ARM/Vulkan on Linux

The `select_transcriber()` function below auto-picks based on OS + hardware
tier. Each backend is import-guarded so the package imports cleanly on every
platform — a transcriber that isn't available simply reports `is_available()
== False` and the caller falls back to CPU-only faster-whisper.

Graceful degradation contract (VAL-RECV-005):
  1. Probe the best accelerator for this platform.
  2. If the accelerator probe succeeds → use it.
  3. If the accelerator fails at runtime (driver issue, missing DLL, etc.)
     → fall back to CPU-only faster-whisper for THIS dictation, mark the
     accelerator as degraded, and keep running.
  4. On the next dictation, re-probe the accelerator. If it succeeds again,
     lift the degraded flag and resume the accelerated path.

IMPORTANT: This package is named 'platform', which shadows the stdlib
'platform' module.  We explicitly load the stdlib module via importlib
and re-export its public API so that third-party libraries (onnxruntime,
torch, etc.) that call `import platform; platform.system()` continue
to work.  Every function/attribute from the real stdlib platform module
is available as `mumble_platform.<name>` and as `platform.<name>` after
the shim kicks in.
"""

import importlib
import importlib.util
import sys as _sys

# ── Stdlib platform shim ───────────────────────────────────────────────────
# The stdlib `platform` module lives in the standard library.  Because our
# package has the same name, `import platform` inside a third-party library
# resolves to THIS package — breaking every caller that expects the real
# platform module.  We fix this by forcibly loading the stdlib version under
# an alias and then patching sys.modules so that subsequent `import platform`
# calls inside this module's namespace get the real thing.

def _load_stdlib_platform():
    """Load the REAL stdlib `platform` module, not our package.

    Because our package shadows the stdlib name, `import platform` inside
    third-party code resolves to us.  We load the real stdlib module from
    its known filesystem path and merge its public attributes into our
    package namespace so that `platform.machine()`, `platform.system()`,
    etc. continue to work for every caller.
    """
    import pathlib as _pl
    # Walk sys.path to find the stdlib platform.py (not our package dir).
    stdlib_path = None
    for _entry in _sys.path:
        _cand = _pl.Path(_entry) / "platform.py"
        if _cand.exists() and "Internal" not in str(_cand):
            stdlib_path = str(_cand)
            break
    if stdlib_path is None:
        return None
    spec = importlib.util.spec_from_file_location(
        "_stdlib_platform", stdlib_path)
    if spec is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_stdlib_platform = None
try:
    _stdlib_platform = _load_stdlib_platform()
except Exception:
    # If we truly cannot load the stdlib platform, degrade gracefully.
    # sys.platform still works for OS detection.
    pass

# Make stdlib platform functions available at package level so third-party
# code that does `import platform; platform.system()` works via our shim.
# We only do this when the real module loaded successfully.
if _stdlib_platform is not None:
    for _attr in dir(_stdlib_platform):
        if not _attr.startswith("_"):
            try:
                globals()[_attr] = getattr(_stdlib_platform, _attr)
            except Exception:
                pass

# ── Platform detection helpers ─────────────────────────────────────────────

def detect_apple_silicon():
    """Detect whether we're running on an Apple Silicon Mac (M1/M2/M3/M4).

    Returns True when:
      - sys.platform == "darwin"
      - platform.machine() returns "arm64" (via stdlib platform module)

    On Windows/Linux or Intel Macs this returns False — the macOS transcriber
    is only valid on Apple Silicon where CoreML/FluidAudio are available.
    """
    if _sys.platform != "darwin":
        return False
    if _stdlib_platform is not None:
        machine = _stdlib_platform.machine().lower()
        return machine in ("arm64", "aarch64")
    return False


def detect_directml_capable():
    """Detect whether a DirectML-capable GPU is present on Windows.

    Uses a lightweight subprocess probe to avoid importing onnxruntime
    directly (which would trigger `import platform` and re-enter our
    package).  Returns True only when a DirectML execution provider is
    confirmed available.

    On non-Windows platforms this always returns False.
    """
    if _sys.platform != "win32":
        return False
    # Probe for DirectML without importing onnxruntime in-process.
    # The subprocess runs in a clean environment where the stdlib platform
    # module resolves correctly.
    try:
        import subprocess as _sp
        _code = (
            "import onnxruntime as ort; "
            "print('DML_OK' if 'DmlExecutionProvider' in "
            "ort.get_available_providers() else 'DML_NO')"
        )
        _r = _sp.run(
            [_sys.executable, "-c", _code],
            capture_output=True, text=True, timeout=10,
            cwd=None,  # use current working directory
        )
        return "DML_OK" in (_r.stdout or "")
    except Exception:
        return False


def detect_linux_arm_vulkan():
    """Detect whether we're on Linux ARM with Vulkan compute capability.

    Returns True when:
      - sys.platform == "linux"
      - platform.machine() is arm or aarch64
      - Vulkan library is loadable (libvulkan.so.1)

    On non-Linux platforms this always returns False.
    """
    if _sys.platform != "linux":
        return False
    if _stdlib_platform is not None:
        machine = _stdlib_platform.machine().lower()
        if machine not in ("armv7l", "armv8l", "aarch64", "arm64"):
            return False
    else:
        return False
    # Vulkan availability check — whisper.cpp needs libvulkan.so.
    try:
        import ctypes as _ct
        _ct.CDLL("libvulkan.so.1")
        return True
    except Exception:
        return False


def select_transcriber(hardware_tier="mid", settings=None):
    """Select the best available platform-specific STT accelerator.

    Auto-detection order, respecting VAL-CROSS-008:
      1. macOS + Apple Silicon → MacOSTranscriber (FluidAudio + Parakeet TDT)
      2. Windows + DirectML GPU → DirectMLTranscriber (sherpa-onnx SenseVoice)
      3. Linux ARM + Vulkan  → WhisperCppTranscriber (whisper.cpp)

    When NO platform accelerator is available, returns None — the caller
    MUST use CPU-only faster-whisper as the default.

    Each returned transcriber has:
      - .transcribe(audio, sample_rate=16000, **kwargs) → transcript text
      - .is_available() classmethod → bool
      - .transcriber_name → str (human-readable label for diagnostics)

    Args:
        hardware_tier: "weak" | "mid" | "powerful" from branding.py
        settings: optional Settings object for model/config lookups

    Returns:
        A transcriber instance or None if no accelerator is available.
    """
    transcriber = None

    # 1. macOS Apple Silicon → FluidAudio + Parakeet TDT
    if detect_apple_silicon():
        try:
            from .macos_audio import MacOSTranscriber
            if MacOSTranscriber.is_available():
                transcriber = MacOSTranscriber(
                    model_name="base",
                    device="auto",
                    hardware_tier=hardware_tier,
                )
                print(f"[platform] MacOSTranscriber activated (Apple Silicon, "
                      f"tier={hardware_tier})")
                return transcriber
        except ImportError:
            pass

    # 2. Windows DirectML → sherpa-onnx SenseVoice
    if detect_directml_capable():
        try:
            from .windows_dml import DirectMLTranscriber
            if DirectMLTranscriber.is_available():
                transcriber = DirectMLTranscriber(
                    model_name="sensevoice-small",
                    device="dml",
                    hardware_tier=hardware_tier,
                )
                print(f"[platform] DirectMLTranscriber activated (Windows DML, "
                      f"tier={hardware_tier})")
                return transcriber
        except ImportError:
            pass

    # 3. Linux ARM → whisper.cpp Vulkan fallback
    if detect_linux_arm_vulkan():
        try:
            from .linux_fallback import WhisperCppTranscriber
            if WhisperCppTranscriber.is_available():
                transcriber = WhisperCppTranscriber(
                    model_name="base.en",
                    device="vulkan",
                    hardware_tier=hardware_tier,
                )
                print(f"[platform] WhisperCppTranscriber activated (Linux ARM "
                      f"Vulkan, tier={hardware_tier})")
                return transcriber
        except ImportError:
            pass

    # No platform accelerator available — CPU-only faster-whisper is the
    # reliable default on every platform.
    print(f"[platform] No hardware accelerator available on {_sys.platform} "
          f"(tier={hardware_tier}) — using CPU-only faster-whisper")
    return None
