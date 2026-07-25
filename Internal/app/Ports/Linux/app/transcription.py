#!/usr/bin/env python3
"""Optional CLOUD transcription for Mumble (advanced, opt-in).

Local faster-whisper stays the DEFAULT and the privacy-preserving path — audio
never leaves the machine. Only when the user deliberately flips Settings →
Transcription → Mode to "Cloud" is a short utterance uploaded to a low-latency
hosted Whisper endpoint; on Groq the text comes back in well under a second.

Providers all speak an OpenAI-compatible `/audio/transcriptions` contract, with
ONE request-shape difference handled below:

  • groq        — Whisper Large v3 Turbo, ~216x real-time. The fastest hosted
                  speech-to-text available, and the recommended default.
                  multipart/form-data file upload.
  • openai      — gpt-4o-mini-transcribe (fast) / whisper-1. multipart upload.
  • openrouter  — ONE key → many STT models (it routes to Groq / OpenAI /
                  Google Chirp / Mistral Voxtral under the hood). Uses a JSON
                  body with base64-encoded audio, NOT multipart.

API keys are SHARED with the matching LLM provider settings (groq_api_key,
openai_api_key, openrouter_api_key) — it is literally the same account key — so a
user who already pasted a Groq key for prompting gets cloud STT for free.

Everything here raises on failure; the caller (mumble.py `_transcribe`) catches
and falls back to local transcription, so a flaky network never loses a dictation.

RTF DIAGNOSTICS — built-in performance monitoring:
  The module provides `compute_rtf()` and `rtf_summary()` for measuring
  real-time factor and diagnosing transcription performance. These are
  consumed by the STT path in mumble.py to surface slow-performance
  warnings in the UI.  An RTF > 1.0 means transcription is slower than
  real-time; > 1.5 triggers a SLOW warning; > 2.0 is a hardware-level
  concern.
"""

import base64
import binascii
import io
import json
import math
import os
import time
import urllib.error
import urllib.request
import wave

import numpy as np
import processing_route
import recording_limits
from ai.transport import read_error_body, read_response_limited

SAMPLE_RATE = 16000
MAX_CLOUD_TRANSCRIPT_RESPONSE_BYTES = 4 * 1024 * 1024

# A browser User-Agent — some of these endpoints sit behind Cloudflare, which
# 403s the default "Python-urllib/x.y" UA (same reason ai.py sets one).
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# provider id → endpoint + which setting holds its key/model + request shape.
PROVIDERS = {
    "groq": {
        "label": "Groq — Whisper v3 Turbo (fastest, recommended)",
        "url": "https://api.groq.com/openai/v1/audio/transcriptions",
        "key_setting": "groq_api_key",
        "model_setting": "groq_transcription_model",
        "default_model": "whisper-large-v3-turbo",
        "shape": "multipart",
        "key_url": "https://console.groq.com/keys",
        "key_prefix": "gsk_",
    },
    "openai": {
        "label": "OpenAI — gpt-4o-mini-transcribe",
        "url": "https://api.openai.com/v1/audio/transcriptions",
        "key_setting": "openai_api_key",
        "model_setting": "openai_transcription_model",
        "default_model": "gpt-4o-mini-transcribe",
        "shape": "multipart",
        "key_url": "https://platform.openai.com/api-keys",
        "key_prefix": "sk-",
    },
    "openrouter": {
        "label": "OpenRouter — one key, many STT models",
        "url": "https://openrouter.ai/api/v1/audio/transcriptions",
        "key_setting": "openrouter_api_key",
        "model_setting": "openrouter_transcription_model",
        "default_model": "groq/whisper-large-v3-turbo",
        "shape": "json_base64",
        "key_url": "https://openrouter.ai/keys",
        "key_prefix": "sk-or-",
    },
}
DEFAULT_PROVIDER = "groq"


def provider_info(provider):
    """Resolve a provider id to its config, falling back to the default."""
    return PROVIDERS.get((provider or "").strip().lower()) or PROVIDERS[DEFAULT_PROVIDER]


def pcm16_wav_bytes(audio, sample_rate=SAMPLE_RATE):
    """float32 mono [-1, 1] numpy array → 16-bit PCM WAV bytes, in memory.

    The recorder hands us exactly this (sounddevice float32, 1 channel, 16 kHz),
    so we just clip, scale to int16 and wrap in a WAV container — no temp file,
    no extra dependency beyond numpy + the stdlib `wave` module."""
    if audio is None:
        raise ValueError("audio is required")
    try:
        rate = int(sample_rate)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("sample_rate must be a positive integer") from None
    if rate <= 0:
        raise ValueError("sample_rate must be a positive integer")
    a = np.asarray(audio, dtype=np.float32).flatten()
    if a.size == 0:
        raise ValueError("audio is empty")
    # A broken audio driver can occasionally yield NaN/Inf samples.  Encoding
    # those directly produces implementation-dependent int16 values, so make
    # them silence while preserving the rest of the captured utterance.
    if not np.isfinite(a).all():
        a = np.nan_to_num(a, nan=0.0, posinf=1.0, neginf=-1.0)
    a = np.clip(a, -1.0, 1.0)
    pcm = (a * 32767.0).astype("<i2")  # little-endian int16
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _multipart_body(fields, file_field, filename, file_bytes,
                    content_type="audio/wav"):
    """Hand-roll a multipart/form-data body (urllib has no built-in for it, and
    we deliberately avoid pulling in `requests`). Returns (body, content_type)."""
    boundary = "----Mumble" + binascii.hexlify(os.urandom(12)).decode("ascii")
    bb = boundary.encode("ascii")
    crlf = b"\r\n"
    out = []
    for name, val in fields.items():
        if val is None:
            continue
        out.append(b"--" + bb + crlf)
        out.append(
            ('Content-Disposition: form-data; name="%s"' % name).encode("utf-8")
            + crlf + crlf
        )
        out.append(str(val).encode("utf-8") + crlf)
    out.append(b"--" + bb + crlf)
    out.append(
        ('Content-Disposition: form-data; name="%s"; filename="%s"'
         % (file_field, filename)).encode("utf-8") + crlf
    )
    out.append(("Content-Type: %s" % content_type).encode("utf-8") + crlf + crlf)
    out.append(file_bytes + crlf)
    out.append(b"--" + bb + b"--" + crlf)
    return b"".join(out), "multipart/form-data; boundary=" + boundary


def _post_and_extract(req, timeout):
    """POST a prepared Request and pull the transcript text out of the JSON.
    All providers return {"text": "..."} (OpenAI-compatible)."""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = read_response_limited(
                r, MAX_CLOUD_TRANSCRIPT_RESPONSE_BYTES,
                label="cloud transcription response", timeout=timeout,
            ).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = read_error_body(e)[:400]
        raise RuntimeError(
            f"cloud transcription HTTP {e.code}: {detail or e.reason}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"cloud transcription network error: {e.reason}")
    try:
        obj = json.loads(raw)
    except Exception:
        raise RuntimeError("cloud transcription: response was not JSON")
    text = obj.get("text") if isinstance(obj, dict) else None
    # Tolerate the occasional alternative shape (e.g. {"results":{"text":...}}).
    if text is None and isinstance(obj, dict):
        res = obj.get("results")
        if isinstance(res, dict):
            text = res.get("text")
    if not isinstance(text, str):
        raise RuntimeError(
            f"cloud transcription: no text in response ({str(obj)[:200]})")
    return text.strip()


def _transcribe_multipart(info, key, model, wav, lang, timeout, prompt=None):
    fields = {"model": model, "response_format": "json"}
    if lang:
        fields["language"] = lang
    # `prompt` biases the decoder toward the user's personal vocabulary
    # (names/jargon) — the cloud parallel of faster-whisper `hotwords`. The
    # OpenAI-compatible transcription contract accepts it; _multipart_body skips
    # None so an empty vocabulary sends nothing.
    if prompt:
        fields["prompt"] = prompt
    body, ctype = _multipart_body(fields, "file", "audio.wav", wav)
    req = urllib.request.Request(info["url"], data=body, method="POST")
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", ctype)
    req.add_header("User-Agent", _UA)
    return _post_and_extract(req, timeout)


def _transcribe_json(info, key, model, wav, lang, timeout, prompt=None):
    payload = {
        "model": model,
        "input_audio": {"data": base64.b64encode(wav).decode("ascii"),
                        "format": "wav"},
    }
    if lang:
        payload["language"] = lang
    if prompt:
        payload["prompt"] = prompt
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(info["url"], data=data, method="POST")
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", _UA)
    # OpenRouter recommends (but doesn't require) these attribution headers.
    req.add_header("HTTP-Referer", "https://github.com/mumble")
    req.add_header("X-Title", "Mumble")
    return _post_and_extract(req, timeout)


def transcribe(audio, invocation_snapshot, timeout=30):
    """Transcribe a float32 mono 16 kHz numpy array via the configured cloud
    provider and return the transcript text (stripped).

    `invocation_snapshot` is the immutable route and input decision captured
    before work starts. Raises on any failure (blocked route, network, HTTP, bad
    response) so the caller can fall back to local transcription. The adapter
    never re-reads mutable Settings."""
    invocation_snapshot = processing_route.require_speech_to_text(
        invocation_snapshot
    )
    decision = invocation_snapshot.route
    try:
        sample_count = int(np.asarray(audio).size)
    except Exception:
        sample_count = 0
    if sample_count > recording_limits.DICTATION_MAX_SAMPLES:
        raise ValueError(
            "Cloud transcription accepts at most 10 minutes per request. "
            "Split long-form audio into bounded chunks.")

    provider = decision.provider
    if provider not in PROVIDERS:
        raise ValueError("Unsupported cloud transcription provider.")
    info = PROVIDERS[provider]
    key = decision.api_key
    if not key:
        raise ValueError(
            f"No API key set for cloud transcription ({provider}). Add one in "
            f"Settings → Transcription → Cloud.")
    model = decision.model
    lang = invocation_snapshot.primary_language or None
    # Only forward a sane ISO-639-1 code. A blank/garbage value would otherwise
    # 400 on every utterance (then fall back to local) — wasting a round-trip
    # each time. Anything that isn't 2 alpha chars → let the provider auto-detect.
    lang = (lang or "").strip().lower() or None
    if lang and not (len(lang) == 2 and lang.isalpha()):
        lang = None
    # Keep cloud decoder bias identical to the local hotword path.  In
    # particular, a malformed string setting must be one term rather than 50
    # one-character terms, and duplicate spellings should not waste the cap.
    from formatting import sanitize_hotwords
    terms = sanitize_hotwords(invocation_snapshot.vocabulary_terms)[:50]
    prompt = ", ".join(terms) if terms else None
    wav = pcm16_wav_bytes(audio)
    if info["shape"] == "json_base64":
        return _transcribe_json(info, key, model, wav, lang, timeout, prompt)
    return _transcribe_multipart(info, key, model, wav, lang, timeout, prompt)


# ===========================================================================
# RTF (Real-Time Factor) Diagnostics
# ===========================================================================
# RTF = processing_time_sec / audio_duration_sec.
#
#   RTF < 0.5   → healthy (much faster than real-time, comfortable margin)
#   RTF 0.5–1.0 → ok (faster than real-time, but getting tight)
#   RTF 1.0–1.5 → marginal (slower than real-time, user may notice lag)
#   RTF 1.5–2.0 → slow (clearly slower than real-time, warnings shown)
#   RTF > 2.0   → very slow (hardware bottleneck, strong upgrade notice)
#
# These thresholds are used by mumble.py's perf logging and by the
# `rtf_summary()` helper below to classify performance.

RTF_HEALTHY = 0.5     # below this is great
RTF_OK = 1.0          # below this is acceptable
RTF_MARGINAL = 1.5    # below this is a mild concern
RTF_SLOW = 2.0        # above this is a hardware-level concern


def compute_rtf(audio_duration_sec, processing_time_sec):
    """Compute the Real-Time Factor for a transcription run.

    Args:
        audio_duration_sec: Length of the audio in seconds.
        processing_time_sec: Wall-clock time the transcription took.

    Returns:
        float — RTF value (lower is better; < 1.0 = faster than real-time).
    """
    if audio_duration_sec <= 0:
        return float("inf")
    return processing_time_sec / max(0.001, audio_duration_sec)


def rtf_verdict(rtf):
    """Human-readable verdict for an RTF value.

    Returns a dict with:
      • label: "healthy" | "ok" | "marginal" | "slow" | "very_slow"
      • warning: bool — should the user see a performance warning?
      • message: str — human-readable description
    """
    if rtf <= RTF_HEALTHY:
        return {"label": "healthy", "warning": False,
                "message": "Healthy — transcription is comfortably faster "
                           "than real-time."}
    if rtf <= RTF_OK:
        return {"label": "ok", "warning": False,
                "message": "OK — transcription keeps up with real-time."}
    if rtf <= RTF_MARGINAL:
        return {"label": "marginal", "warning": True,
                "message": "Marginal — transcription is slightly slower than "
                           "real-time. Consider a smaller model or closing "
                           "other apps."}
    if rtf <= RTF_SLOW:
        return {"label": "slow", "warning": True,
                "message": "Slow — your PC decodes slower than real-time. "
                           "Enable Resource Saver (tiny.en model) or pick a "
                           "smaller model in Settings → Transcription."}
    return {"label": "very_slow", "warning": True,
            "message": "Very slow — hardware bottleneck. Switch to the "
                       "tiny.en model and close CPU-heavy apps. Cloud STT "
                       "(Groq) is also an option for instant transcription."}


def rtf_summary(audio_duration_sec, processing_time_sec, model_name="unknown",
                device="cpu", compute_type="int8"):
    """Produce a complete RTF diagnostic summary.

    This is the main entry point for callers that want a structured RTF
    report suitable for logging, API responses, and UI display.

    Returns a dict with:
      • rtf: float — the computed RTF
      • audio_duration_sec, processing_time_sec: the input values
      • verdict: str — "healthy" | "ok" | "marginal" | "slow" | "very_slow"
      • warning: bool — whether a UI warning should be shown
      • message: str — human-readable diagnostic message
      • model: str — the STT model used
      • device: str — inference device (cpu, cuda, dml, etc.)
      • compute_type: str — quantization type
    """
    rtf = compute_rtf(audio_duration_sec, processing_time_sec)
    verdict = rtf_verdict(rtf)
    return {
        "rtf": round(rtf, 3),
        "audio_duration_sec": round(audio_duration_sec, 3),
        "processing_time_sec": round(processing_time_sec, 3),
        "verdict": verdict["label"],
        "warning": verdict["warning"],
        "message": verdict["message"],
        "model": model_name,
        "device": device,
        "compute_type": compute_type,
    }


# ===========================================================================
# Noise robustness helpers
# ===========================================================================

def estimate_snr(audio, sample_rate=SAMPLE_RATE):
    """Estimate the signal-to-noise ratio of an audio segment in dB.

    Uses a simple energy-percentile heuristic: the 10th-percentile RMS
    energy (over short windows) approximates the noise floor, while the
    90th-percentile approximates the signal. This is NOT a proper SNR
    meter but is fast, dependency-free, and good enough to detect
    whether the audio is excessively noisy vs. clean.

    Returns:
        float — estimated SNR in dB. Higher = cleaner.  Values below
        ~8 dB suggest significant background noise that may degrade
        transcription accuracy.
    """
    if audio is None:
        return float("inf")
    a = np.asarray(audio, dtype=np.float32).flatten()
    if len(a) < sample_rate // 10:  # need at least 100ms
        return float("inf")
    # Compute RMS in 30ms windows (a reasonable energy envelope).
    window_len = int(sample_rate * 0.03)
    n_windows = max(1, len(a) // window_len)
    energies = []
    for i in range(n_windows):
        chunk = a[i * window_len:(i + 1) * window_len]
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        if rms > 0:
            energies.append(20.0 * math.log10(rms))
    if not energies or len(energies) < 3:
        return 30.0  # assume clean if insufficient data
    energies.sort()
    # 10th percentile = noise floor; 90th percentile = signal
    noise_idx = max(0, len(energies) // 10)
    signal_idx = min(len(energies) - 1, len(energies) * 9 // 10)
    noise_floor = energies[noise_idx]
    signal_level = energies[signal_idx]
    snr = signal_level - noise_floor
    return max(0.0, snr)


def noise_quality_label(snr_db):
    """Classify the audio quality based on estimated SNR.

    Returns one of: "clean", "fair", "noisy", "very_noisy"
    """
    if snr_db >= 20.0:
        return "clean"
    if snr_db >= 12.0:
        return "fair"
    if snr_db >= 6.0:
        return "noisy"
    return "very_noisy"


def noise_robust_transcribe_settings(settings, snr_db=None):
    """Adjust transcription parameters for noisy conditions.

    When background noise is detected (SNR < 12 dB), returns recommended
    parameter overrides to improve accuracy:
      • Increase beam_size (from 1 to 3) for better search in noise
      • Enable VAD filter more aggressively
      • Reduce no_speech_threshold slightly
      • Keep language set explicitly (auto-detect is worse in noise)

    Args:
        settings: app Settings object
        snr_db: optional pre-computed SNR value

    Returns:
        dict of parameter overrides (or empty dict if clean audio).
    """
    overrides = {}
    if snr_db is not None and snr_db < 12.0:
        overrides["beam_size"] = 3
        overrides["vad_filter"] = True
        overrides["no_speech_threshold"] = 0.5
    return overrides


# ===========================================================================
# Platform accelerator dispatch (VAL-TRAN-008, VAL-RECV-005, VAL-CROSS-008)
# ===========================================================================
# These helpers manage the platform-specific STT accelerator lifecycle.
# The caller (mumble.py's _transcribe) calls get_platform_transcriber() once
# at startup to resolve the best accelerator for the current platform and
# hardware tier.  If the accelerator fails at runtime, the caller calls
# mark_accelerator_degraded() and falls back to CPU-only faster-whisper.
#
# The accelerator is re-probed on the next dictation (VAL-RECV-005 contract).

_platform_transcriber = None      # cached instance or None
_platform_transcriber_name = None  # human-readable label for RTF diagnostics
_platform_accelerator_degraded = False


def get_platform_transcriber(hardware_tier="mid", settings=None):
    """Resolve and cache the best available platform-specific STT accelerator.

    Called once by the STT path (mumble.py) to determine whether a platform
    accelerator should replace CPU-only faster-whisper.  The result is cached
    for the lifetime of the process; when the accelerator degrades at runtime,
    the caller must mark it via mark_accelerator_degraded().

    Returns:
        A transcriber instance with a .transcribe(audio, sample_rate, ...)
        method, or None if no accelerator is available.
    """
    global _platform_transcriber, _platform_transcriber_name
    if _platform_transcriber is None and not _platform_accelerator_degraded:
        try:
            from platform import select_transcriber as _select
            _platform_transcriber = _select(
                hardware_tier=hardware_tier,
                settings=settings,
            )
            if _platform_transcriber is not None:
                name = getattr(_platform_transcriber, "transcriber_name",
                               type(_platform_transcriber).__name__)
                _platform_transcriber_name = name
        except Exception as e:
            print(f"[transcription] Platform transcriber probe failed: {e}")
            _platform_transcriber = None
    return _platform_transcriber


def mark_accelerator_degraded(reason=""):
    """Mark the platform accelerator as degraded after a runtime failure.

    Future calls to get_platform_transcriber() will return None, causing
    the caller to use CPU-only faster-whisper.  The degraded flag is reset
    on the next successful probe (clear_accelerator_degraded).

    This implements VAL-RECV-005: accelerator fails at runtime → fall back
    to CPU-only for this dictation, re-probe on next request.
    """
    global _platform_accelerator_degraded, _platform_transcriber
    _platform_accelerator_degraded = True
    _platform_transcriber = None
    if reason:
        print(f"[transcription] Platform accelerator degraded: {reason}")


def clear_accelerator_degraded():
    """Reset the accelerator degraded flag after a successful re-probe."""
    global _platform_accelerator_degraded
    _platform_accelerator_degraded = False


def get_platform_transcriber_name():
    """Return the human-readable name of the active platform accelerator,
    or "faster-whisper (CPU)" when running on the CPU fallback path.

    Used by RTF diagnostics to report the inference device accurately.
    """
    if _platform_transcriber is not None:
        return _platform_transcriber_name or "Platform Accelerator"
    if _platform_accelerator_degraded:
        return "faster-whisper (CPU, accelerator degraded)"
    return "faster-whisper (CPU)"


def is_platform_accelerated():
    """Return True when a platform-specific accelerator is active."""
    return _platform_transcriber is not None and not _platform_accelerator_degraded
