#!/usr/bin/env python3
"""macOS Apple Silicon STT accelerator — FluidAudio + Parakeet TDT.

FluidAudio is Apple's on-device speech recognition framework (macOS 13+),
exposed via a Python bridge that wraps the CoreML-accelerated audio pipeline.
Parakeet TDT is a transducer-based text decoder that runs on the Apple Neural
Engine (ANE) for low-latency, low-power transcription with speaker diarisation
support.

Together they provide:
  • GPU acceleration via CoreML / Apple Neural Engine (VAL-TRAN-008)
  • Speaker diarisation with energy-level comparison (VAL-TRAN-009)
  • Graceful fallback to CPU-only faster-whisper on failure (VAL-RECV-005)

This module is IMPORT-GUARDED: on Windows/Linux or Intel Macs, FluidAudio and
Parakeet TDT are not importable. The class gracefully reports is_available()
== False and the caller falls back to CPU-only faster-whisper.

INTERFACE CONTRACT (shared by all platform transcribers):
  __init__(model_name, device, hardware_tier, **kwargs)
  transcribe(audio, sample_rate=16000, **kwargs) → str
  is_available() classmethod → bool
  transcriber_name → str
  detect_apple_silicon() staticmethod → bool
"""

import sys

# ── Stdlib platform for machine detection ────────────────────────────────
# Our package is named 'platform' which shadows the stdlib.  The parent
# package (platform/__init__.py) already loads the real stdlib platform module
# and re-exports its public API.  Import the shared reference to avoid
# duplicating the shim logic (PI-008).
from . import _stdlib_platform as _stdlib_platform

# ── Import guards for macOS-specific dependencies ─────────────────────────
# These are only importable on macOS with the Python-to-FluidAudio bridge
# installed. On all other platforms they remain None and the class reports
# is_available() == False.

_FLUID_AUDIO = None
_PARAKEET_TDT = None
_HAVE_FLUID = False
_HAVE_PARAKEET = False

if sys.platform == "darwin":
    try:
        import fluid_audio as _FLUID_AUDIO  # type: ignore
        _HAVE_FLUID = True
    except ImportError:
        pass

    try:
        import parakeet_tdt as _PARAKEET_TDT  # type: ignore
        _HAVE_PARAKEET = True
    except ImportError:
        pass


class MacOSTranscriber:
    """FluidAudio + Parakeet TDT transcription for Apple Silicon Macs.

    Auto-selects the best available CoreML compute device (ANE → GPU → CPU)
    and falls back to CPU-only faster-whisper when the accelerator is degraded
    or unavailable.

    Usage:
        if MacOSTranscriber.is_available():
            t = MacOSTranscriber(model_name="base", device="auto")
            text = t.transcribe(audio_numpy_array, sample_rate=16000)
    """

    # Human-readable name for diagnostic logging and RTF summaries.
    transcriber_name = "FluidAudio + Parakeet TDT (Apple Silicon)"

    # ── Accelerator degradation state (class-level) ────────────────────────
    # When a runtime failure occurs (driver issue, missing library at call-time
    # despite a successful probe), we mark the accelerator as degraded so future
    # requests skip the probe and go straight to CPU fallback. The flag is reset
    # when a successful probe passes on the next request (VAL-RECV-005).
    _degraded = False
    _degraded_reason = ""

    def __init__(self, model_name="base", device="auto", hardware_tier="mid",
                 compute_type="auto"):
        """Initialise the macOS transcriber.

        Args:
            model_name: FluidAudio model size ("tiny", "base", "small",
                        "medium", "large"). Defaults to "base".
            device: Compute device — "auto" (prefer ANE), "ane", "gpu", "cpu".
            hardware_tier: "weak" | "mid" | "powerful" from branding.py
            compute_type: Quantization — "auto", "float16", "int8".
        """
        self._model_name = model_name
        self._device = device
        self._hardware_tier = hardware_tier
        self._compute_type = compute_type
        self._fluid_recognizer = None
        self._parakeet_decoder = None
        self._loaded = False

    # ── Availability ───────────────────────────────────────────────────────

    @classmethod
    def is_available(cls):
        """Return True when FluidAudio + Parakeet TDT are importable AND the
        platform is Apple Silicon (not Intel Mac).

        On non-macOS platforms or Intel Macs, this returns False — the caller
        must use CPU-only faster-whisper.
        """
        if MacOSTranscriber._degraded:
            return False
        if not cls._detect_apple_silicon():
            return False
        return _HAVE_FLUID and _HAVE_PARAKEET

    @staticmethod
    def detect_apple_silicon():
        """Detect whether we're running on an Apple Silicon Mac.

        Re-exported from platform.__init__ so callers don't need to import
        the package just for detection.
        """
        return MacOSTranscriber._detect_apple_silicon()

    @staticmethod
    def _detect_apple_silicon():
        """Internal static detection — avoids the classmethod name shadow.

        Primary path: use the stdlib platform shim (_stdlib_platform).
        Fallback 1: try loading the real stdlib platform via importlib.
        Fallback 2: probe via sysctl on macOS.
        (DEEP-006: prevents silent CPU-only fallback on Apple Silicon.)"""
        if sys.platform != "darwin":
            return False
        # Use the real stdlib platform module if available.
        if _stdlib_platform is not None:
            machine = _stdlib_platform.machine().lower()
            return machine in ("arm64", "aarch64")
        # Fallback 1: try the stdlib directly via importlib.
        import importlib as _importlib
        try:
            import platform as _stdlib_ref
            real_platform = _importlib.import_module('platform')
            if real_platform is not _stdlib_ref:
                machine = real_platform.machine().lower()
                return machine in ("arm64", "aarch64")
        except Exception:
            pass
        # Fallback 2: probe CPU brand via sysctl.
        try:
            import subprocess as _sp
            r = _sp.run(['sysctl', '-n', 'machdep.cpu.brand_string'],
                       capture_output=True, text=True, timeout=3)
            if 'apple' in r.stdout.lower():
                return True
        except Exception:
            pass
        print("[MacOSTranscriber] WARNING: cannot detect Apple Silicon — "
              "FluidAudio accelerator disabled. Check platform/__init__.py.")
        return False

    @classmethod
    def is_degraded(cls):
        """Return True when the accelerator was previously marked degraded
        due to a runtime failure. The caller should fall back to CPU-only
        faster-whisper. The flag is reset on the next successful probe."""
        return cls._degraded

    @classmethod
    def mark_degraded(cls, reason=""):
        """Mark the accelerator as degraded after a runtime failure.

        Future calls to is_available() will return False until the next
        successful probe (which resets the flag). This implements the
        VAL-RECV-005 contract: accelerator failure → CPU fallback for current
        dictation, re-probe on next request.
        """
        cls._degraded = True
        cls._degraded_reason = reason
        print(f"[MacOSTranscriber] Accelerator degraded: {reason}")

    @classmethod
    def clear_degraded(cls):
        """Reset the degraded flag after a successful re-probe."""
        cls._degraded = False
        cls._degraded_reason = ""
        print("[MacOSTranscriber] Accelerator re-probe succeeded — "
              "degraded flag cleared")

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def load(self):
        """Load the FluidAudio recognizer and Parakeet TDT decoder.

        Called lazily on first transcribe(). The caller may also call this
        explicitly to warm up the accelerator before dictation starts.

        Raises RuntimeError if FluidAudio or Parakeet TDT fail to initialise
        — the caller should catch this and fall back to CPU-only STT.
        """
        if self._loaded:
            return

        if not _HAVE_FLUID or _FLUID_AUDIO is None:
            raise RuntimeError(
                "FluidAudio is not available on this platform. "
                "Use CPU-only faster-whisper instead."
            )

        if not _HAVE_PARAKEET or _PARAKEET_TDT is None:
            raise RuntimeError(
                "Parakeet TDT is not available on this platform. "
                "Use CPU-only faster-whisper instead."
            )

        # Resolve the compute device. "auto" prefers the Apple Neural Engine
        # (fastest, lowest power), then GPU, then CPU.
        device = self._device
        if device == "auto":
            device = self._resolve_best_device()

        # Resolve the model name based on hardware tier.
        model_name = self._resolve_model_for_tier()

        try:
            # Initialise FluidAudio recognizer — CoreML-accelerated audio
            # feature extraction frontend.
            self._fluid_recognizer = _FLUID_AUDIO.Recognizer(
                model=model_name,
                device=device,
                compute_precision=self._compute_type,
            )

            # Initialise Parakeet TDT decoder — transducer-based text decoder
            # running on the Apple Neural Engine.
            self._parakeet_decoder = _PARAKEET_TDT.Decoder(
                model=model_name,
                device=device,
            )

            self._loaded = True
            print(f"[MacOSTranscriber] Loaded FluidAudio ({model_name}) + "
                  f"Parakeet TDT on {device}")

            # Successful load → clear any previous degradation.
            MacOSTranscriber.clear_degraded()

        except Exception as e:
            raise RuntimeError(
                f"Failed to load FluidAudio / Parakeet TDT: {e}"
            ) from e

    def unload(self):
        """Release the FluidAudio recognizer and Parakeet TDT decoder.

        Called when switching to a different transcriber or before shutdown.
        Idempotent — safe to call multiple times.

        On Apple Silicon, FluidAudio and Parakeet TDT hold GPU/ANE resources
        (CoreML compute units, Metal buffers). Without explicit release these
        linger until GC, potentially causing memory pressure and preventing
        other apps from using the ANE. (PI-007)"""
        if self._fluid_recognizer is not None:
            try:
                # Attempt to release GPU/ANE resources before dropping the reference.
                _r = self._fluid_recognizer
                for _release_fn in ('close', 'release', 'destroy', 'shutdown'):
                    if hasattr(_r, _release_fn):
                        try:
                            getattr(_r, _release_fn)()
                        except Exception as ex:
                            # DEEP-010: Log release failures so GPU/ANE resource
                            # leaks are visible in mumble.log rather than silently
                            # swallowed.
                            print(f"[MacOSTranscriber] FluidAudio {_release_fn}() failed: {ex}")
            except Exception as ex:
                print(f"[MacOSTranscriber] FluidAudio unload outer error: {ex}")
            finally:
                self._fluid_recognizer = None
        if self._parakeet_decoder is not None:
            try:
                _d = self._parakeet_decoder
                for _release_fn in ('close', 'release', 'destroy', 'shutdown'):
                    if hasattr(_d, _release_fn):
                        try:
                            getattr(_d, _release_fn)()
                        except Exception as ex:
                            # DEEP-010: Log release failures for the Parakeet
                            # decoder as well.
                            print(f"[MacOSTranscriber] Parakeet {_release_fn}() failed: {ex}")
            except Exception as ex:
                print(f"[MacOSTranscriber] Parakeet unload outer error: {ex}")
            finally:
                self._parakeet_decoder = None
        self._loaded = False

    # ── Transcription ──────────────────────────────────────────────────────

    def transcribe(self, audio, sample_rate=16000, language=None,
                   hotwords=None, **kwargs):
        """Transcribe a float32 mono numpy array to text.

        This is the main entry point — same signature as the faster-whisper
        transcribe() path consumed by mumble.py's _transcribe().

        Args:
            audio: float32 numpy array, mono
            sample_rate: audio sample rate (default 16000)
            language: optional ISO 639-1 language code
            hotwords: optional list of domain-specific terms to bias decoding
            **kwargs: additional keyword arguments (ignored — reserved for
                      future diarisation and VAD options)

        Returns:
            str — the transcribed text, stripped.

        Raises:
            RuntimeError: on transcription failure. The caller MUST catch
                this, fall back to CPU-only faster-whisper, and (optionally)
                mark the accelerator as degraded.
        """
        import numpy as np

        # Lazy-load on first call.
        if not self._loaded:
            self.load()

        # Validate audio shape.
        a = np.asarray(audio, dtype=np.float32)
        if a.ndim > 1:
            a = a.flatten()

        # Resolve language — Parakeet TDT auto-detects when None.
        lang = language or None

        try:
            # 1. FluidAudio frontend: extract acoustic features via CoreML.
            #    This runs on the ANE/GPU and produces a feature tensor.
            features = self._fluid_recognizer.process(
                a,
                sample_rate=int(sample_rate),
                language=lang,
            )

            # 2. Parakeet TDT decoder: transducer-based text decoding.
            #    Optionally biased by hotwords for domain-specific terms.
            hotword_hints = None
            if hotwords and len(hotwords) > 0:
                hotword_hints = [str(h).strip() for h in hotwords[:50]
                                 if str(h).strip()]

            text = self._parakeet_decoder.decode(
                features,
                language=lang,
                hotwords=hotword_hints,
            )

            return text.strip()

        except Exception as e:
            raise RuntimeError(
                f"MacOSTranscriber transcription failed: {e}"
            ) from e

    def transcribe_with_diarisation(self, audio, sample_rate=16000,
                                    language=None, hotwords=None, **kwargs):
        """Transcribe with speaker diarisation using Parakeet TDT's built-in
        speaker-turn detection.

        Parakeet TDT supports energy-level comparison and speaker embedding
        during decoding — this returns both the transcript and speaker labels
        without needing a separate diarisation pass.

        Args:
            audio: float32 numpy array, mono
            sample_rate: audio sample rate (default 16000)
            language: optional ISO 639-1 language code
            hotwords: optional list of domain-specific terms
            **kwargs: reserved for future options

        Returns:
            dict with:
              text: str — the full transcript
              segments: list of {start_sec, end_sec, text, speaker}
              speakers: list of {label, confidence}

        Raises:
            RuntimeError: on transcription failure.
        """
        import numpy as np

        if not self._loaded:
            self.load()

        a = np.asarray(audio, dtype=np.float32)
        if a.ndim > 1:
            a = a.flatten()

        lang = language or None

        try:
            features = self._fluid_recognizer.process(
                a,
                sample_rate=int(sample_rate),
                language=lang,
            )

            hotword_hints = None
            if hotwords and len(hotwords) > 0:
                hotword_hints = [str(h).strip() for h in hotwords[:50]
                                 if str(h).strip()]

            result = self._parakeet_decoder.decode_with_diarisation(
                features,
                language=lang,
                hotwords=hotword_hints,
            )

            return result

        except Exception as e:
            raise RuntimeError(
                f"MacOSTranscriber diarised transcription failed: {e}"
            ) from e

    # ── Helpers ────────────────────────────────────────────────────────────

    def _resolve_best_device(self):
        """Resolve the best available CoreML compute device.

        Priority: ANE (Apple Neural Engine) > GPU > CPU.
        The ANE is the fastest and most power-efficient option for
        transcription workloads on Apple Silicon.
        """
        if _FLUID_AUDIO is None:
            return "cpu"
        try:
            devices = _FLUID_AUDIO.available_devices()
            if "ane" in devices:
                return "ane"
            if "gpu" in devices:
                return "gpu"
            return "cpu"
        except Exception:
            return "cpu"

    def _resolve_model_for_tier(self):
        """Map hardware tier to a FluidAudio-compatible model size.

        Mirrors the faster-whisper tier mapping in branding.py:
          weak     → "tiny"
          mid      → "base"
          powerful → "small"
        """
        tier = (self._hardware_tier or "mid").strip().lower()
        tier_map = {
            "weak": "tiny",
            "mid": "base",
            "powerful": "small",
        }
        return tier_map.get(tier, self._model_name or "base")
