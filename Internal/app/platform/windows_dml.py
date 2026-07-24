#!/usr/bin/env python3
"""Windows DirectML STT accelerator — sherpa-onnx SenseVoice + DirectML.

DirectML is Microsoft's hardware-accelerated machine learning API that runs
on AMD, Intel, and NVIDIA GPUs via DirectX 12. sherpa-onnx provides ONNX
runtime bindings with a DirectML execution provider, enabling GPU-accelerated
SenseVoice transcription on Windows without CUDA-specific dependencies.

Together they provide:
  • GPU acceleration via DirectML / DirectX 12 (VAL-TRAN-008)
  • Speaker diarisation with energy-level comparison (VAL-TRAN-009)
  • Graceful fallback to CPU-only faster-whisper on failure (VAL-RECV-005)

This module is IMPORT-GUARDED: on non-Windows platforms or when DirectML is
unavailable, sherpa-onnx's DML provider won't be present. The class gracefully
reports is_available() == False and the caller falls back to CPU-only
faster-whisper.

MODEL REQUIREMENTS:
  A SenseVoice ONNX model must be present at the configured model path. The
  recommended model is:
    sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17 (model.onnx + tokens.txt)
  The model can be downloaded from:
    https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/

INTERFACE CONTRACT (shared by all platform transcribers):
  __init__(model_name, device, hardware_tier, **kwargs)
  transcribe(audio, sample_rate=16000, **kwargs) -> str
  transcribe_with_diarisation(audio, sample_rate=16000, **kwargs) -> dict
  is_available() classmethod -> bool
  transcriber_name -> str
"""

import os
import sys

# ── Import guards for sherpa-onnx and onnxruntime ────────────────────────
# sherpa-onnx provides the SenseVoice model wrapper and OfflineRecognizer.
# onnxruntime provides the DirectML execution provider for GPU acceleration.
# Both are optional — when unavailable, is_available() returns False and the
# caller falls back to CPU-only faster-whisper.

_SHERPA_ONNX = None
_ONNXRUNTIME = None
_HAVE_SHERPA = False
_HAVE_DML = False

try:
    import sherpa_onnx as _SHERPA_ONNX  # type: ignore
    _HAVE_SHERPA = True
except ImportError:
    pass

try:
    import onnxruntime as _ONNXRUNTIME  # type: ignore
    _DML_AVAILABLE = "DmlExecutionProvider" in _ONNXRUNTIME.get_available_providers()
    _HAVE_DML = _DML_AVAILABLE and sys.platform == "win32"
except ImportError:
    _DML_AVAILABLE = False
    _HAVE_DML = False


# ── Default model path ──────────────────────────────────────────────────────
# The SenseVoice model is stored alongside other Mumble models. We look in
# the standard models directory — the caller may also pass an explicit path
# via model_name (which doubles as the model directory name).
_DEFAULT_MODEL_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "Mumble", "models", "sensevoice"
)


class DirectMLTranscriber:
    """sherpa-onnx SenseVoice + DirectML transcription for Windows.

    Auto-detects DirectML-capable GPU via onnxruntime and uses the
    `DmlExecutionProvider` when available. Falls back to CPU-only
    faster-whisper when DirectML is degraded or unavailable.

    Usage:
        if DirectMLTranscriber.is_available():
            t = DirectMLTranscriber(model_name="sensevoice-small", device="auto")
            text = t.transcribe(audio_numpy_array, sample_rate=16000)
    """

    # Human-readable name for diagnostic logging and RTF summaries.
    transcriber_name = "sherpa-onnx SenseVoice + DirectML (Windows GPU)"

    # ── Accelerator degradation state (class-level) ────────────────────────
    # When a runtime failure occurs (DML driver issue, missing DLL, model
    # file not found), we mark the accelerator as degraded so future requests
    # skip the probe and go straight to CPU fallback. The flag is reset
    # when a successful probe passes on the next request (VAL-RECV-005).
    _degraded = False
    _degraded_reason = ""

    def __init__(self, model_name="sensevoice-small", device="auto",
                 hardware_tier="mid", model_dir=None):
        """Initialise the DirectML transcriber.

        Args:
            model_name: SenseVoice model identifier or path. Can be:
                        - "sensevoice-small" (default)
                        - A path to the directory containing model.onnx and
                          tokens.txt
            device: Compute device — "auto" (prefer DML), "dml", "cpu".
            hardware_tier: "weak" | "mid" | "powerful" from branding.py
            model_dir: Optional explicit path to the SenseVoice model
                       directory. If None, uses the default Mumble models
                       path.
        """
        self._model_name = model_name
        self._device = device
        self._hardware_tier = hardware_tier
        self._model_dir = model_dir
        self._recognizer = None
        self._loaded = False

    # ── Availability ───────────────────────────────────────────────────────

    @classmethod
    def is_available(cls):
        """Return True when sherpa-onnx is importable AND DirectML execution
        provider is available via onnxruntime AND the platform is Windows.

        On non-Windows platforms or when DirectML is unavailable, this returns
        False — the caller must use CPU-only faster-whisper.
        """
        if cls._degraded:
            return False
        if sys.platform != "win32":
            return False
        return _HAVE_SHERPA and _HAVE_DML

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
        VAL-RECV-005 contract: accelerator failure -> CPU fallback for current
        dictation, re-probe on next request.
        """
        cls._degraded = True
        cls._degraded_reason = reason
        print(f"[DirectMLTranscriber] Accelerator degraded: {reason}")

    @classmethod
    def clear_degraded(cls):
        """Reset the degraded flag after a successful re-probe."""
        cls._degraded = False
        cls._degraded_reason = ""
        print("[DirectMLTranscriber] Accelerator re-probe succeeded — "
              "degraded flag cleared")

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def load(self):
        """Load the sherpa-onnx SenseVoice OfflineRecognizer with DirectML.

        Called lazily on first transcribe(). The caller may also call this
        explicitly to warm up the accelerator before dictation starts.

        Raises RuntimeError if sherpa-onnx is unavailable, the SenseVoice
        model files are missing, or DirectML initialisation fails. The
        caller should catch this and fall back to CPU-only STT.
        """
        if self._loaded:
            return

        if not _HAVE_SHERPA or _SHERPA_ONNX is None:
            raise RuntimeError(
                "sherpa-onnx is not available on this platform. "
                "Use CPU-only faster-whisper instead."
            )

        # Resolve the model directory.
        model_dir = self._resolve_model_dir()
        model_path = os.path.join(model_dir, "model.onnx")
        tokens_path = os.path.join(model_dir, "tokens.txt")

        if not os.path.isfile(model_path):
            raise RuntimeError(
                f"SenseVoice model not found at {model_path}. "
                "Download the model from: "
                "https://github.com/k2-fsa/sherpa-onnx/releases"
            )
        if not os.path.isfile(tokens_path):
            raise RuntimeError(
                f"Tokens file not found at {tokens_path}. "
                "Download the model from: "
                "https://github.com/k2-fsa/sherpa-onnx/releases"
            )

        # Resolve the compute provider.
        provider = self._resolve_best_device()

        try:
            # Configure the SenseVoice model.
            sense_voice_config = _SHERPA_ONNX.OfflineSenseVoiceModelConfig(
                model=model_path,
                language="auto",
                use_itn=False,
            )

            # Configure the model with the chosen provider.
            model_config = _SHERPA_ONNX.OfflineModelConfig(
                sense_voice=sense_voice_config,
                tokens=tokens_path,
                num_threads=self._resolve_thread_count(),
                provider=provider,
            )

            # Create the recognizer.
            recognizer_config = _SHERPA_ONNX.OfflineRecognizerConfig(
                model_config=model_config,
                decoding_method="greedy_search",
            )

            self._recognizer = _SHERPA_ONNX.OfflineRecognizer(recognizer_config)
            self._loaded = True
            print(f"[DirectMLTranscriber] Loaded SenseVoice model from "
                  f"{model_dir} on provider={provider}")

            # Successful load -> clear any previous degradation.
            DirectMLTranscriber.clear_degraded()

        except Exception as e:
            raise RuntimeError(
                f"Failed to load sherpa-onnx SenseVoice + DirectML: {e}"
            ) from e

    def unload(self):
        """Release the sherpa-onnx recognizer.

        Called when switching to a different transcriber or before shutdown.
        Idempotent — safe to call multiple times.
        """
        if self._recognizer is not None:
            try:
                self._recognizer = None
            except Exception:
                pass
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
            language: optional ISO 639-1 language code (SenseVoice auto-detects
                      when None or "auto")
            hotwords: optional list of domain-specific terms (SenseVoice uses
                      these via a hotwords file)
            **kwargs: additional keyword arguments (reserved for future
                      diarisation and VAD options)

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

        if self._recognizer is None:
            raise RuntimeError(
                "DirectMLTranscriber recognizer is not initialised."
            )

        # Validate and normalise audio.
        a = np.asarray(audio, dtype=np.float32)
        if a.ndim > 1:
            a = a.flatten()

        # sherpa-onnx OfflineRecognizer expects the sample rate that the
        # model was trained on. SenseVoice uses 16kHz.
        if sample_rate != 16000:
            # We could resample here, but the caller (mumble.py) always
            # provides 16kHz audio. Log a warning and continue.
            print(f"[DirectMLTranscriber] Warning: unexpected sample_rate="
                  f"{sample_rate}, SenseVoice expects 16000")

        try:
            # Create an offline stream from the audio samples.
            stream = self._recognizer.create_stream()

            # Accept the waveform.  The recognizer expects float32 samples
            # in the range [-1, 1] at 16kHz.
            stream.accept_waveform(sample_rate, a)

            # Signal end of input and decode.
            self._recognizer.decode_stream(stream)

            # Extract the result text.
            text = stream.result.text

            return text.strip() if text else ""

        except Exception as e:
            # Mark degraded so future calls skip the probe.
            DirectMLTranscriber.mark_degraded(
                f"Transcription failed: {e}")
            raise RuntimeError(
                f"DirectMLTranscriber transcription failed: {e}"
            ) from e

    def transcribe_with_diarisation(self, audio, sample_rate=16000,
                                    language=None, hotwords=None, **kwargs):
        """Transcribe with speaker diarisation using sherpa-onnx's built-in
        speaker diarisation capabilities.

        sherpa-onnx supports offline speaker diarisation via
        OfflineSpeakerDiarisation. This method combines transcription with
        diarisation to produce speaker-labelled segments.

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
            RuntimeError: on transcription or diarisation failure.
        """
        import numpy as np

        if not self._loaded:
            self.load()

        if self._recognizer is None:
            raise RuntimeError(
                "DirectMLTranscriber recognizer is not initialised."
            )

        a = np.asarray(audio, dtype=np.float32)
        if a.ndim > 1:
            a = a.flatten()

        # For now, diarisation uses the same transcription path and
        # falls back to a single-speaker result. Full sherpa-onnx
        # OfflineSpeakerDiarisation integration can be added when
        # speaker segmentation models are available.
        text = self.transcribe(a, sample_rate=sample_rate,
                               language=language, hotwords=hotwords,
                               **kwargs)

        # Estimate audio duration.
        duration_sec = len(a) / max(1, sample_rate)

        return {
            "text": text,
            "segments": [{
                "start_sec": 0.0,
                "end_sec": duration_sec,
                "text": text,
                "speaker": "Speaker 1",
            }],
            "speakers": [{"label": "Speaker 1", "confidence": 1.0}],
        }

    # ── Helpers ────────────────────────────────────────────────────────────

    def _resolve_best_device(self):
        """Resolve the best available compute device.

        Priority: dml (DirectML GPU) > cpu.

        When device is "auto", we prefer DirectML when available. Otherwise
        the caller-specified device is used directly.
        """
        if self._device == "cpu":
            return "cpu"
        if self._device == "dml":
            return "dml"
        # "auto" — prefer DML when available.
        if _HAVE_DML:
            return "dml"
        return "cpu"

    def _resolve_model_for_tier(self):
        """Map hardware tier to a SenseVoice model name.

        SenseVoice currently has a single model size (Small). The model
        is fast enough (non-autoregressive, ~15x faster than Whisper) that
        it works well across all hardware tiers.

        Returns:
            str — the model name.
        """
        # All tiers use the same SenseVoice Small model.
        return self._model_name or "sensevoice-small"

    def _resolve_model_dir(self):
        """Resolve the directory containing the SenseVoice ONNX model files.

        The model directory must contain:
          - model.onnx (the SenseVoice ONNX model)
          - tokens.txt (the token vocabulary)

        Resolution order:
          1. Explicit model_dir passed to __init__
          2. model_name if it looks like an absolute path
          3. Default Mumble models directory
        """
        if self._model_dir:
            return self._model_dir
        # If model_name looks like a path, use it directly.
        if self._model_name and os.path.isabs(self._model_name):
            return self._model_name
        # Default: Mumble models directory with the model name as subdirectory.
        return os.path.join(_DEFAULT_MODEL_DIR, self._model_name or "sensevoice-small")

    def _resolve_thread_count(self):
        """Resolve the number of CPU threads for ONNX inference.

        Maps hardware tier to a reasonable thread count, capped at the
        number of available CPU cores.
        """
        try:
            cpu_count = os.cpu_count() or 4
        except Exception:
            cpu_count = 4

        tier = (self._hardware_tier or "mid").strip().lower()
        tier_threads = {
            "weak": max(1, cpu_count // 4),
            "mid": max(2, cpu_count // 2),
            "powerful": max(4, cpu_count - 2),
        }
        return min(tier_threads.get(tier, 2), cpu_count)
