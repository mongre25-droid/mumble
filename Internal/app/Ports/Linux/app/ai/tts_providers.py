#!/usr/bin/env python3
"""TTS (text-to-speech) provider abstraction for Mumble.

Extracted from the monolithic ai.py. Provides:
  - TTSProvider abstract base class
  - OpenRouterTTSProvider (wraps OpenRouter's /audio/speech endpoint)
  - OpenAITTSProvider (OpenAI's native /v1/audio/speech endpoint)
  - Voice catalogue with quality/gender/persona metadata
  - One exact frozen Reader provider/model attempt via synthesize_with_fallback()
  - PCM-to-WAV framing for Gemini-class TTS
  - Male-first voice sorting for the Reader UI
"""

import io
import json
import urllib.error
import urllib.request
import wave

import processing_route
from ai.transport import read_error_body, read_response_limited

_MAX_TTS_AUDIO_BYTES = 32 * 1024 * 1024

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ---- Quality, gender, persona constants --------------------------------------
QUALITY_HIGH = "high"
QUALITY_PREVIEW = "preview"
QUALITY_STANDARD = "standard"
QUALITY_LEGACY = "legacy"

GENDER_MALE = "male"
GENDER_FEMALE = "female"
GENDER_NEUTRAL = "neutral"

PERSONA_DEEP = "deep"
PERSONA_NARRATOR = "narrator"
PERSONA_WARM = "warm"
PERSONA_YOUNG = "young"
PERSONA_CALM = "calm"
PERSONA_BRIGHT = "bright"
PERSONA_RASPY = "raspy"
PERSONA_ENERGETIC = "energetic"

_PERSONA_MAP = {
    PERSONA_DEEP: {
        "openai": "onyx",
        "openrouter:google/gemini-3.1-flash-tts-preview": "Fenrir",
    },
    PERSONA_NARRATOR: {"openai": "cedar"},
    PERSONA_WARM: {"openai": "echo"},
    PERSONA_YOUNG: {"openai": "ash"},
    PERSONA_CALM: {"openai": "sage"},
    PERSONA_BRIGHT: {"openai": "alloy"},
    PERSONA_RASPY: {"openai": "ballad"},
    PERSONA_ENERGETIC: {"openai": "marin"},
}


# ---- TTSProvider abstract base -----------------------------------------------
class TTSProvider:
    """Abstract base for a text-to-speech provider."""

    def list_voices(self):
        raise NotImplementedError

    def synthesize(self, text, voice_id, model=None,
                   response_format=None, timeout=60):
        raise NotImplementedError

    @property
    def provider_id(self):
        raise NotImplementedError

    @property
    def provider_label(self):
        raise NotImplementedError

    @property
    def default_model(self):
        raise NotImplementedError

    @property
    def default_voice(self):
        raise NotImplementedError

    @property
    def auth_setting(self):
        raise NotImplementedError


# ---- OpenRouter TTS models and voices ----------------------------------------
OPENROUTER_TTS_URL = "https://openrouter.ai/api/v1/audio/speech"
OPENROUTER_TTS_MODELS = [
    ("google/gemini-3.1-flash-tts-preview", "Google Gemini Flash TTS (recommended)", "pcm"),
    ("mistralai/voxtral-mini-tts-2603", "Mistral Voxtral mini TTS", "mp3"),
    ("microsoft/mai-voice-2", "Microsoft MAI Voice 2", "mp3"),
]
OPENROUTER_TTS_FORMATS = {mid: fmt for (mid, _label, fmt) in OPENROUTER_TTS_MODELS}
OPENROUTER_TTS_VOICES = {
    "google/gemini-3.1-flash-tts-preview": [
        "Fenrir", "Puck", "Charon", "Zephyr", "Orus", "Enceladus", "Iapetus",
        "Algieba", "Algenib", "Rasalgethi", "Achernar", "Alnilam", "Schedar",
        "Gacrux", "Zubenelgenubi", "Sadaltager", "Umbriel",
    ],
}
OPENROUTER_TTS_DEFAULT_MODEL = "google/gemini-3.1-flash-tts-preview"
OPENROUTER_TTS_DEFAULT_VOICE = "Fenrir"
OPENROUTER_TTS_DEFAULT_VOICES = {
    "google/gemini-3.1-flash-tts-preview": "Fenrir",
}
_TTS_PCM_RATE = 24000


def _pcm_to_wav(pcm_bytes, rate=_TTS_PCM_RATE, channels=1, sampwidth=2):
    """Frame raw little-endian 16-bit PCM as an in-memory WAV."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(rate)
        w.writeframes(pcm_bytes)
    return buf.getvalue()


def _pcm_rate_from_ctype(ctype, default=_TTS_PCM_RATE):
    """Pull the sample rate out of a PCM/L16 Content-Type."""
    try:
        for part in (ctype or "").split(";"):
            part = part.strip().lower()
            if part.startswith("rate="):
                r = int(part[5:].strip())
                if 8000 <= r <= 192000:
                    return r
    except (ValueError, TypeError):
        pass
    return default


# ---- OpenRouter TTS ----------------------------------------------------------
def openrouter_tts(text, api_key, model=None, voice=None,
                   response_format=None, timeout=60, route_decision=None,
                   operation_lane="reader_speech"):
    """Synthesize text to speech through OpenRouter's /audio/speech endpoint."""
    key = (api_key or "").strip()
    if not key:
        raise ValueError("Add your OpenRouter API key in Settings to use the Reader.")
    txt = (text or "").strip()
    if not txt:
        raise ValueError("Nothing to read.")
    if len(txt) > 4000:
        raise ValueError(f"That passage is too long ({len(txt)} chars).")
    mid = (model or OPENROUTER_TTS_DEFAULT_MODEL).strip()
    processing_route.require_reader_speech(
        route_decision, expected_lane=operation_lane,
        expected_provider="openrouter", api_key=key, model=mid)
    if mid not in OPENROUTER_TTS_FORMATS:
        raise ValueError(f"Unknown Reader voice model '{mid}'.")
    vc = (voice or OPENROUTER_TTS_DEFAULT_VOICES.get(mid) or "").strip()
    if not vc:
        raise ValueError(f"The voice model '{mid}' needs a voice name.")
    known_voices = OPENROUTER_TTS_VOICES.get(mid)
    if known_voices and vc not in known_voices:
        raise ValueError(f"Voice '{vc}' isn't available for {mid}.")
    fmt = (response_format or OPENROUTER_TTS_FORMATS.get(mid, "mp3"))
    payload = {"model": mid, "input": txt, "voice": vc, "response_format": fmt}
    req = urllib.request.Request(
        OPENROUTER_TTS_URL, data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", _UA)
    req.add_header("HTTP-Referer", "https://github.com/mumble")
    req.add_header("X-Title", "Mumble")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            audio = read_response_limited(
                r, _MAX_TTS_AUDIO_BYTES, label="TTS audio", timeout=timeout)
            ctype = r.headers.get("Content-Type") or ""
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = read_error_body(e)[:200]
        except Exception:
            pass
        raise RuntimeError(f"TTS HTTP {e.code}: {body or e.reason}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"TTS network error: {e.reason}") from None
    if not audio:
        raise RuntimeError("TTS returned no audio.")
    if fmt in ("pcm", "l16", "wav") and "wav" not in (ctype or "").lower():
        audio = _pcm_to_wav(audio, rate=_pcm_rate_from_ctype(ctype))
        ctype = "audio/wav"
    if not ctype:
        ctype = "audio/mpeg" if fmt == "mp3" else "audio/wav"
    return audio, ctype


# ---- Voice metadata ----------------------------------------------------------
_GEMINI_VOICE_GENDERS = {
    "Fenrir": GENDER_MALE, "Puck": GENDER_MALE, "Charon": GENDER_MALE,
    "Zephyr": GENDER_MALE, "Orus": GENDER_MALE, "Enceladus": GENDER_MALE,
    "Iapetus": GENDER_MALE, "Algieba": GENDER_MALE, "Algenib": GENDER_MALE,
    "Rasalgethi": GENDER_MALE, "Achernar": GENDER_MALE, "Alnilam": GENDER_MALE,
    "Schedar": GENDER_MALE, "Gacrux": GENDER_MALE, "Zubenelgenubi": GENDER_MALE,
    "Sadaltager": GENDER_MALE,
    "Kore": GENDER_FEMALE, "Leda": GENDER_FEMALE, "Aoede": GENDER_FEMALE,
    "Callirrhoe": GENDER_FEMALE, "Autonoe": GENDER_FEMALE, "Despina": GENDER_FEMALE,
    "Erinome": GENDER_FEMALE, "Laomedeia": GENDER_FEMALE, "Pulcherrima": GENDER_FEMALE,
    "Achird": GENDER_FEMALE, "Vindemiatrix": GENDER_FEMALE, "Sadachbia": GENDER_FEMALE,
    "Sulafat": GENDER_FEMALE,
    "Umbriel": GENDER_NEUTRAL,
}
_GEMINI_VOICE_PERSONAS = {
    "Fenrir": PERSONA_DEEP, "Puck": PERSONA_WARM, "Charon": PERSONA_DEEP,
    "Zephyr": PERSONA_BRIGHT, "Leda": PERSONA_CALM, "Orus": PERSONA_NARRATOR,
    "Aoede": PERSONA_BRIGHT, "Kore": PERSONA_CALM,
}
_OPENAI_VOICE_GENDERS = {
    "onyx": GENDER_MALE, "ash": GENDER_MALE, "echo": GENDER_MALE,
    "sage": GENDER_MALE, "ballad": GENDER_MALE, "cedar": GENDER_MALE,
    "marin": GENDER_MALE,
    "alloy": GENDER_FEMALE, "coral": GENDER_FEMALE, "nova": GENDER_FEMALE,
    "shimmer": GENDER_FEMALE, "fable": GENDER_FEMALE, "verse": GENDER_FEMALE,
}
_OPENAI_VOICE_PERSONAS = {
    "onyx": PERSONA_DEEP, "cedar": PERSONA_NARRATOR, "echo": PERSONA_WARM,
    "ash": PERSONA_YOUNG, "sage": PERSONA_CALM, "alloy": PERSONA_BRIGHT,
    "ballad": PERSONA_RASPY, "marin": PERSONA_ENERGETIC,
    "coral": PERSONA_CALM, "nova": PERSONA_BRIGHT, "shimmer": PERSONA_BRIGHT,
    "fable": PERSONA_WARM, "verse": PERSONA_CALM,
}


# ---- OpenRouter TTS Provider -------------------------------------------------
class OpenRouterTTSProvider(TTSProvider):
    provider_id = "openrouter"
    provider_label = "OpenRouter"
    auth_setting = "openrouter_api_key"
    default_model = OPENROUTER_TTS_DEFAULT_MODEL
    default_voice = OPENROUTER_TTS_DEFAULT_VOICE

    def list_voices(self):
        voices = []
        for mid, label, fmt in OPENROUTER_TTS_MODELS:  # noqa: F841
            vnames = OPENROUTER_TTS_VOICES.get(mid, [])
            if vnames:
                for vn in vnames:
                    gender = _GEMINI_VOICE_GENDERS.get(vn, GENDER_NEUTRAL)
                    persona = _GEMINI_VOICE_PERSONAS.get(vn, "")
                    voices.append({
                        "id": vn, "name": vn, "gender": gender,
                        "quality": QUALITY_HIGH, "persona": persona,
                        "provider": self.provider_id,
                        "provider_label": self.provider_label,
                        "model": mid, "model_label": label,
                    })
            else:
                voices.append({
                    "id": "", "name": label, "gender": GENDER_NEUTRAL,
                    "quality": QUALITY_HIGH, "persona": "",
                    "provider": self.provider_id,
                    "provider_label": self.provider_label,
                    "model": mid, "model_label": label,
                })
        return voices

    def synthesize(self, text, voice_id, model=None,
                   response_format=None, timeout=60, route_decision=None,
                   operation_lane="reader_speech"):
        processing_route.require_reader_speech(
            route_decision, expected_lane=operation_lane,
            expected_provider=self.provider_id)
        key = route_decision.api_key
        if not key:
            raise ValueError("Add your OpenRouter API key in Settings to use the Reader.")
        mid = model or self.default_model
        processing_route.require_reader_speech(
            route_decision, expected_lane=operation_lane,
            expected_provider=self.provider_id, api_key=key, model=mid)
        audio, ctype = openrouter_tts(
            text, key, model=mid,
            voice=voice_id or self.default_voice,
            response_format=response_format, timeout=timeout,
            route_decision=route_decision, operation_lane=operation_lane)
        return audio, ctype

# ---- OpenAI TTS Provider -----------------------------------------------------
OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
OPENAI_TTS_DEFAULT_MODEL = "gpt-4o-mini-tts"
_OPENAI_TTS_VOICES = [
    "alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx",
    "sage", "shimmer", "verse", "marin", "cedar",
]


class OpenAITTSProvider(TTSProvider):
    provider_id = "openai"
    provider_label = "OpenAI"
    auth_setting = "openai_api_key"
    default_model = OPENAI_TTS_DEFAULT_MODEL
    default_voice = "onyx"

    def list_voices(self):
        voices = []
        for vn in _OPENAI_TTS_VOICES:
            gender = _OPENAI_VOICE_GENDERS.get(vn, GENDER_NEUTRAL)
            persona = _OPENAI_VOICE_PERSONAS.get(vn, "")
            voices.append({
                "id": vn, "name": vn.capitalize(), "gender": gender,
                "quality": QUALITY_HIGH, "persona": persona,
                "provider": self.provider_id,
                "provider_label": self.provider_label,
                "model": self.default_model,
                "model_label": "GPT-4o mini TTS",
            })
        return voices

    def synthesize(self, text, voice_id, model=None,
                   response_format=None, timeout=60, route_decision=None,
                   operation_lane="reader_speech"):
        processing_route.require_reader_speech(
            route_decision, expected_lane=operation_lane,
            expected_provider=self.provider_id)
        key = route_decision.api_key
        if not key:
            raise ValueError("Add your OpenAI API key in Settings.")
        txt = (text or "").strip()
        if not txt:
            raise ValueError("Nothing to read.")
        if len(txt) > 4000:
            raise ValueError(f"That passage is too long ({len(txt)} chars).")
        mid = model or self.default_model
        vc = voice_id or self.default_voice
        fmt = response_format or "mp3"
        payload = {"model": mid, "input": txt, "voice": vc, "response_format": fmt}
        processing_route.require_reader_speech(
            route_decision, expected_lane=operation_lane,
            expected_provider=self.provider_id, api_key=key, model=mid)
        try:
            req = urllib.request.Request(
                OPENAI_TTS_URL, data=json.dumps(payload).encode("utf-8"), method="POST")
        except Exception as e:
            raise RuntimeError(f"Failed to prepare TTS request: {e}") from None
        req.add_header("Authorization", "Bearer " + key)
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", _UA)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                audio = read_response_limited(
                    r, _MAX_TTS_AUDIO_BYTES, label="OpenAI TTS audio",
                    timeout=timeout)
                ctype = r.headers.get("Content-Type") or "audio/mpeg"
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = read_error_body(e)[:200]
            except Exception:
                pass
            raise RuntimeError(f"OpenAI TTS HTTP {e.code}: {body or e.reason}") from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"OpenAI TTS network error: {e.reason}") from None
        if not audio:
            raise RuntimeError("OpenAI TTS returned no audio.")
        if not ctype:
            ctype = "audio/mpeg"
        return audio, ctype

# ---- Provider registry and public API ----------------------------------------
_TTS_PROVIDERS = {
    "openrouter": OpenRouterTTSProvider(),
    "openai": OpenAITTSProvider(),
}


def get_tts_provider(provider_id):
    return _TTS_PROVIDERS.get(provider_id, _TTS_PROVIDERS.get("openrouter"))


def list_tts_providers():
    result = []
    try:
        from settings import Settings
        s = Settings()
    except Exception:
        s = None
    for pid, p in _TTS_PROVIDERS.items():
        has_key = False
        try:
            key = s.get(p.auth_setting, "") if s else ""
            has_key = bool((key or "").strip())
        except Exception:
            pass
        result.append({
            "id": pid, "label": p.provider_label,
            "auth_setting": p.auth_setting, "has_key": has_key,
            "default_model": p.default_model,
        })
    return result


def _sort_male_first(voices):
    _order = {GENDER_MALE: 0, GENDER_FEMALE: 1, GENDER_NEUTRAL: 2}
    voices.sort(key=lambda v: (_order.get(v.get("gender"), 2), v.get("name", "").lower()))
    return voices


def get_tts_voices(provider_id=None):
    if provider_id and provider_id in _TTS_PROVIDERS:
        voices = _TTS_PROVIDERS[provider_id].list_voices()
    else:
        voices = []
        for p in _TTS_PROVIDERS.values():
            voices.extend(p.list_voices())
    voices = [v for v in voices if v.get("quality") == QUALITY_HIGH]
    voices = [v for v in voices if v.get("gender") != GENDER_FEMALE]
    return _sort_male_first(voices)


def get_tts_defaults(provider_id=None):
    p = get_tts_provider(provider_id or "openrouter")
    return (p.provider_id, p.default_model, p.default_voice)


def synthesize_with_fallback(text, voice_id=None, model=None, provider_id=None,
                             route_decision=None, expected_feature="reader",
                             expected_lane="reader_speech"):
    """Synthesize using exactly the provider/model frozen for this operation."""
    if route_decision is None:
        return None, None, {
            "ok": False,
            "message": "Reader speech requires an explicit frozen route decision.",
        }
    if expected_feature != "reader":
        raise processing_route.HostedRouteBlocked(route_decision)
    ALL_IDS = ["openrouter", "openai"]
    requested = provider_id if provider_id in ALL_IDS else "openrouter"
    try:
        processing_route.require_reader_speech(
            route_decision,
            expected_lane=expected_lane,
            expected_provider=requested,
            api_key=route_decision.api_key,
            model=model or route_decision.model,
        )
    except Exception as e:
        return None, None, {"ok": False, "message": str(e)}
    prov_order = [requested]
    attempts = []
    for i, pid in enumerate(prov_order):
        if pid == "openrouter":
            ordered = [model or route_decision.model]
            for j, m in enumerate(ordered):
                primary = (i == 0 and j == 0)
                attempts.append((pid, m, voice_id if primary else None, primary))
        else:
            primary = (i == 0)
            attempts.append((pid, model if primary else None,
                             voice_id if primary else None, primary))
    last_error = None
    for pid, m, v, is_primary in attempts:
        try:
            p = get_tts_provider(pid)
            audio, ctype = p.synthesize(
                text, voice_id=v, model=m, route_decision=route_decision,
                operation_lane=expected_lane)
            meta = {"ok": True, "provider": pid,
                    "model": m or getattr(p, "default_model", None),
                    "voice": v or getattr(p, "default_voice", None)}
            if not is_primary:
                meta["fallback"] = True
                meta["fallback_provider"] = pid
                meta["fallback_model"] = (
                    m or getattr(p, "default_model", None)
                )
            return audio, ctype, meta
        except Exception as e:
            last_error = str(e)
    return None, None, {"ok": False, "message": last_error or "All voice services are unavailable."}
