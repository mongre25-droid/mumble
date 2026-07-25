#!/usr/bin/env python3
"""Cloud STT (speech-to-text) providers for Mumble.

Extracted from transcription.py. Provides:
  - Provider registry (Groq, OpenAI, OpenRouter Whisper endpoints)
  - Shared multipart form upload + JSON upload paths
  - Fallback chain to local transcription

API keys are SHARED with the matching LLM provider settings (groq_api_key,
openai_api_key, openrouter_api_key).
"""

import base64
import binascii
import io
import json
import os
import urllib.error
import urllib.request
import wave

import numpy as np
import processing_route

SAMPLE_RATE = 16000

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ---- Provider registry -------------------------------------------------------
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


# ---- Audio helpers -----------------------------------------------------------
def pcm16_wav_bytes(audio, sample_rate=SAMPLE_RATE):
    """float32 mono [-1, 1] numpy array -> 16-bit PCM WAV bytes, in memory."""
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
    if not np.isfinite(a).all():
        a = np.nan_to_num(a, nan=0.0, posinf=1.0, neginf=-1.0)
    a = np.clip(a, -1.0, 1.0)
    pcm = (a * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


# ---- Request helpers ---------------------------------------------------------
def _multipart_body(fields, file_field, filename, file_bytes,
                    content_type="audio/wav"):
    """Hand-roll a multipart/form-data body. Returns (body, content_type)."""
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
            + crlf + crlf)
        out.append(str(val).encode("utf-8") + crlf)
    out.append(b"--" + bb + crlf)
    out.append(
        ('Content-Disposition: form-data; name="%s"; filename="%s"'
         % (file_field, filename)).encode("utf-8") + crlf)
    out.append(("Content-Type: %s" % content_type).encode("utf-8") + crlf + crlf)
    out.append(file_bytes + crlf)
    out.append(b"--" + bb + b"--" + crlf)
    return b"".join(out), "multipart/form-data; boundary=" + boundary


def _post_and_extract(req, timeout):
    """POST a prepared Request and pull the transcript text out of the JSON."""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        raise RuntimeError(f"cloud transcription HTTP {e.code}: {detail or e.reason}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"cloud transcription network error: {e.reason}")
    try:
        obj = json.loads(raw)
    except Exception:
        raise RuntimeError("cloud transcription: response was not JSON")
    text = obj.get("text") if isinstance(obj, dict) else None
    if text is None and isinstance(obj, dict):
        res = obj.get("results")
        if isinstance(res, dict):
            text = res.get("text")
    if not isinstance(text, str):
        raise RuntimeError(f"cloud transcription: no text in response ({str(obj)[:200]})")
    return text.strip()


def _transcribe_multipart(info, key, model, wav, lang, timeout, prompt=None):
    """Transcribe via multipart/form-data (Groq, OpenAI)."""
    fields = {"model": model, "response_format": "json"}
    if lang:
        fields["language"] = lang
    if prompt:
        fields["prompt"] = prompt
    body, ctype = _multipart_body(fields, "file", "audio.wav", wav)
    req = urllib.request.Request(info["url"], data=body, method="POST")
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", ctype)
    req.add_header("User-Agent", _UA)
    return _post_and_extract(req, timeout)


def _transcribe_json(info, key, model, wav, lang, timeout, prompt=None):
    """Transcribe via JSON body with base64 audio (OpenRouter)."""
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
    req.add_header("HTTP-Referer", "https://github.com/mumble")
    req.add_header("X-Title", "Mumble")
    return _post_and_extract(req, timeout)


# ---- Public API --------------------------------------------------------------
def transcribe(audio, invocation_snapshot, timeout=30):
    """Transcribe float32 mono 16 kHz numpy array via the configured cloud provider."""
    invocation_snapshot = processing_route.require_speech_to_text(
        invocation_snapshot
    )
    decision = invocation_snapshot.route
    provider = decision.provider
    if provider not in PROVIDERS:
        raise ValueError("Unsupported cloud transcription provider.")
    info = PROVIDERS[provider]
    key = decision.api_key
    if not key:
        raise ValueError(
            f"No API key set for cloud transcription ({provider}).")
    model = decision.model
    lang = invocation_snapshot.primary_language or None
    lang = (lang or "").strip().lower() or None
    if lang and not (len(lang) == 2 and lang.isalpha()):
        lang = None
    # Bias toward the user's personal vocabulary (same terms the local decoder
    # uses as hotwords), capped to keep the prompt short. Sanitize to prevent
    # injection via special characters or excessive term length.
    from formatting import sanitize_hotwords
    terms = sanitize_hotwords(invocation_snapshot.vocabulary_terms)[:50]
    prompt = ", ".join(terms) if terms else None
    wav = pcm16_wav_bytes(audio)
    if info["shape"] == "json_base64":
        return _transcribe_json(info, key, model, wav, lang, timeout, prompt)
    return _transcribe_multipart(info, key, model, wav, lang, timeout, prompt)
