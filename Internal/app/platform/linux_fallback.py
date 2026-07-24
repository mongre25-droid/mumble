"""Linux ARM/Vulkan whisper.cpp acceleration — stub (not yet bundled).

Mumble's platform layer probes for hardware-accelerated STT backends per the
`select_transcriber()` contract in `platform/__init__.py`. On Linux ARM with
Vulkan support, the intended accelerator is whisper.cpp — a C++ inference
engine for Whisper models that can use Vulkan compute shaders on ARM GPUs
(e.g., Raspberry Pi 5, ARM SBCs with Mali/Adreno GPUs).

This file exists so the platform package imports cleanly and the caller gets a
clear diagnostic instead of a silent ImportError. When whisper.cpp is available
on the system, the `WhisperCppTranscriber` class can be populated with the real
subprocess-based `whisper.cpp` CLI invocation (the `whisper.cpp` binary outputs
text to stdout). For now, `is_available()` reports False — the caller falls
back to CPU-only faster-whisper, which works on every platform.

To enable whisper.cpp acceleration:
  1. Install whisper.cpp (https://github.com/ggerganov/whisper.cpp)
  2. Build with Vulkan support: cmake -DWHISPER_VULKAN=ON && make
  3. Download a GGML Whisper model to the models directory
  4. Update is_available() below to detect the binary + model

References:
  • C-005 / VAL-LIN-005 — audit requires this file to exist
  • platform/__init__.py line 233 — the import site
  • linux-audit-summary.md §4 C-005
"""

import os
import shutil
import subprocess


class WhisperCppTranscriber:
    """whisper.cpp Vulkan-accelerated STT backend for Linux ARM.

    This is currently a STUB — whisper.cpp is not bundled with Mumble and
    must be installed separately. When available, this transcriber wraps the
    `whisper.cpp` CLI binary for subprocess-based transcription.

    Public API matches the platform transcriber contract:
      - is_available() → bool
      - transcribe(audio, sample_rate) → str
      - transcribe_with_diarisation(...) → list
    """

    transcriber_name = "whisper.cpp Vulkan (Linux ARM)"

    _degraded = False
    _degraded_reason = ""

    def __init__(self, model_name="base.en", device="vulkan",
                 hardware_tier="mid", compute_type="auto"):
        self._model_name = model_name
        self._device = device
        self._hardware_tier = hardware_tier
        self._compute_type = compute_type
        self._loaded = False

    @classmethod
    def is_available(cls):
        """Return True when whisper.cpp with Vulkan support is installed and a
        compatible GGML model file is present.

        Currently returns False — whisper.cpp is not bundled and must be
        installed separately. When detection is implemented, this method should
        check for:
          1. The `whisper.cpp` (or `whisper-cli`) binary on PATH
          2. At least one .bin or .gguf Whisper model in ~/.cache/mumble/models/
          3. Vulkan ICD loader and a compatible GPU driver
        """
        if cls._degraded:
            return False
        # Stub: whisper.cpp is not bundled — always unavailable for now.
        # Uncomment and customize when whisper.cpp is installed:
        # binary = shutil.which("whisper-cli") or shutil.which("whisper.cpp")
        # if binary is None:
        #     return False
        # return True
        return False

    @classmethod
    def is_degraded(cls):
        return cls._degraded

    @classmethod
    def mark_degraded(cls, reason=""):
        cls._degraded = True
        cls._degraded_reason = reason
        print(f"[WhisperCppTranscriber] Accelerator degraded: {reason}")

    @classmethod
    def clear_degraded(cls):
        cls._degraded = False
        cls._degraded_reason = ""

    def load(self):
        """Stub — whisper.cpp is not bundled."""
        self._loaded = False
        print("[WhisperCppTranscriber] whisper.cpp is not installed — "
              "falling back to CPU-only faster-whisper. To enable Vulkan "
              "acceleration on Linux ARM, install whisper.cpp with Vulkan "
              "support and place a GGML model in ~/.cache/mumble/models/.")

    def unload(self):
        """Release any GPU resources (no-op — whishisper.cpp runs as a subprocess)."""
        self._loaded = False

    def transcribe(self, audio, sample_rate=16000, language=None,
                   task="transcribe", **kwargs):
        """Stub — returns empty string. The caller should check is_available()
        before calling this method; if called when unavailable, it returns an
        empty string so the caller falls back to CPU-only faster-whisper."""
        print("[WhisperCppTranscriber] transcribe called but whisper.cpp is "
              "not available — returning empty (caller should fall back to CPU).")
        return ""

    def transcribe_with_diarisation(self, audio, sample_rate=16000,
                                    language=None, num_speakers=None, **kwargs):
        """Stub — returns empty list."""
        return []


# Module-level probe: log availability once at import time so the operator
# knows WHY the accelerator is inactive when they check the logs.
if not WhisperCppTranscriber.is_available():
    print("[linux_fallback] whisper.cpp Vulkan accelerator is NOT available — "
          "CPU-only faster-whisper will be used. To enable: install whisper.cpp "
          "with Vulkan support.")
