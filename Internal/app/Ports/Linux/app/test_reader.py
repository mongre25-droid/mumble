#!/usr/bin/env python3
"""Reader TTS pipeline guards — model mapping, per-provider format negotiation,
validation, and PCM→WAV framing.

Roots out the v0.9 Reader-TTS failures:
  • OpenAI "model … does not exist" — a DATED model id that 404s. Guarded by
    test_catalogue_ids_undated.
  • Gemini "Unsupported response format PCM, got MP3" — every model was forced
    to mp3. Now the format is negotiated PER MODEL (Gemini→pcm) and raw PCM is
    framed to WAV so the browser can play it.

No real network: urlopen is stubbed. Run:  python test_reader.py
"""
import json
import re
import sys
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import ai  # noqa: E402  (light: json/urllib only)
import processing_route

_fails = []


def _openrouter_route(model=None):
    return processing_route.snapshot(
        {
            "pro_mode": True,
            "local_only_mode": False,
            "instant_text": False,
            "llm_provider": "openrouter",
            "openrouter_api_key": "sk-or-key",
        },
        feature="reader", lane="reader_speech",
        provider_override="openrouter",
        model_override=model or ai.OPENROUTER_TTS_DEFAULT_MODEL,
    )


def check(desc, cond):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {desc}")
    if not cond:
        _fails.append(desc)


class _FakeResp:
    def __init__(self, body, ctype):
        self._body = body
        self.headers = {"Content-Type": ctype}

    # urlopen(...) is used as a context manager returning an object with
    # .read() and .headers.get(...)
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body

    # headers.get(...) — emulate the HTTPMessage API the code uses
    def _hget(self, k, d=None):
        return self.headers.get(k, d)


_captured = {}


def _install_fake(body, ctype):
    """Patch urllib.request.urlopen to capture the request payload and return a
    canned response. Returns the original so the caller can restore it."""
    orig = urllib.request.urlopen

    def fake(req, timeout=None):
        _captured["url"] = req.full_url
        _captured["payload"] = json.loads(req.data.decode("utf-8"))
        r = _FakeResp(body, ctype)
        # .headers is a dict here; the code calls r.headers.get(...) which works.
        return r

    urllib.request.urlopen = fake
    return orig


# ============================================================ catalogue shape
print("\n== catalogue — ids are undated (the OpenAI 404 regression guard) ==")
dated = re.compile(r"-\d{4}-\d{2}-\d{2}$")
bad = [mid for (mid, _l, _f) in ai.OPENROUTER_TTS_MODELS if dated.search(mid)]
check("no TTS model id carries a date suffix", not bad)
check("every catalogue entry is a (id, label, format) triple",
      all(len(e) == 3 for e in ai.OPENROUTER_TTS_MODELS))
# Regression guard for the v0.9 "Reader silent even with a valid key" bug: the
# default WAS openai/gpt-4o-mini-tts, which is NOT hosted on OpenRouter, so the
# very first Play 400/404'd. The default must be a real, in-catalogue id.
check("default TTS model is NOT the OpenRouter-absent openai id",
      ai.OPENROUTER_TTS_DEFAULT_MODEL != "openai/gpt-4o-mini-tts")
check("default TTS model is in the catalogue",
      ai.OPENROUTER_TTS_DEFAULT_MODEL in ai.OPENROUTER_TTS_FORMATS)
check("a kept mp3 model negotiates mp3",
      ai.OPENROUTER_TTS_FORMATS.get("mistralai/voxtral-mini-tts-2603") == "mp3")
check("Gemini model negotiates pcm (it rejects mp3)",
      ai.OPENROUTER_TTS_FORMATS.get("google/gemini-3.1-flash-tts-preview") == "pcm")


# ============================================================ validation
print("\n== validation — bad model/voice fail BEFORE any network call ==")
orig = _install_fake(b"x", "audio/mpeg")
try:
    reached = {"net": False}

    def boom(req, timeout=None):
        reached["net"] = True
        return _FakeResp(b"x", "audio/mpeg")

    urllib.request.urlopen = boom
    try:
        ai.openrouter_tts(
            "hi", "sk-or-key", model="bogus/model",
            route_decision=_openrouter_route("bogus/model"))
        check("unknown model raises", False)
    except ValueError:
        check("unknown model raises ValueError", True)
    except Exception as e:
        check(f"unknown model raises ValueError (got {type(e).__name__})", False)
    check("no network call made for unknown model", reached["net"] is False)

    try:
        ai.openrouter_tts(
            "hi", "sk-or-key",
            model="google/gemini-3.1-flash-tts-preview", voice="not-a-voice",
            route_decision=_openrouter_route(
                "google/gemini-3.1-flash-tts-preview"))
        check("illegal voice raises", False)
    except ValueError:
        check("illegal voice raises ValueError", True)
    except Exception:
        check("illegal voice raises ValueError", False)

    try:
        ai.openrouter_tts("hi", "")          # no key
        check("missing key raises", False)
    except ValueError:
        check("missing key raises ValueError", True)
    except Exception:
        check("missing key raises ValueError", False)
finally:
    urllib.request.urlopen = orig


# ============================================================ format per model
print("\n== format negotiation — payload.response_format matches the model ==")
orig = _install_fake(b"ID3mp3bytes", "audio/mpeg")
try:
    audio, ctype = ai.openrouter_tts("hello", "sk-or-key",
                                     model="mistralai/voxtral-mini-tts-2603",
                                     route_decision=_openrouter_route(
                                         "mistralai/voxtral-mini-tts-2603"))
    check("mp3 model sends response_format=mp3",
          _captured["payload"].get("response_format") == "mp3")
    check("mp3 bytes pass through unchanged", audio == b"ID3mp3bytes")
    check("mp3 content-type is audio/mpeg", "mpeg" in ctype or "mp3" in ctype)
finally:
    urllib.request.urlopen = orig


print("\n== PCM→WAV — Gemini returns raw PCM; we frame it as a playable WAV ==")
# 100 samples of silence as raw little-endian 16-bit PCM
pcm = b"\x00\x00" * 100
orig = _install_fake(pcm, "audio/L16")
try:
    audio, ctype = ai.openrouter_tts("hello", "sk-or-key",
                                     model="google/gemini-3.1-flash-tts-preview",
                                     route_decision=_openrouter_route(
                                         "google/gemini-3.1-flash-tts-preview"))
    check("Gemini sends response_format=pcm",
          _captured["payload"].get("response_format") == "pcm")
    check("raw PCM is wrapped to WAV (RIFF header)", audio[:4] == b"RIFF")
    check("WAV content-type returned", "wav" in ctype.lower())
    check("WAV is larger than the raw PCM (header added)", len(audio) > len(pcm))
finally:
    urllib.request.urlopen = orig


# ===================================================================== summary
print("\n" + ("ALL GREEN" if not _fails else f"{len(_fails)} FAILED: {_fails}"))

# ============================================================ reader_tts_models API
# Verify the webui_shell.reader_tts_models endpoint through the same catalogue
# that drives it. These guards prove VAL-READER-012 (voice picker lists >0
# voices) and VAL-READER-013 (has_key field reflects the key state).
print("\n== reader_tts_models — voices catalogue integrity (VAL-READER-012) ==")
try:
    from settings import AppSettings
    import reader_store as _reader_store_dummy  # ensure the module is importable
except ImportError:
    # In offline test mode without the full app bundle, verify the raw catalogue.
    pass

# The UI's voice picker relies on OPENROUTER_TTS_VOICES being populated.
gemini_voices = ai.OPENROUTER_TTS_VOICES.get("google/gemini-3.1-flash-tts-preview", [])
check("Gemini model has >0 known voices (VAL-READER-012)", len(gemini_voices) > 0)
check("Gemini voice list starts with the male default 'Fenrir'", gemini_voices[0] == "Fenrir" if gemini_voices else False)
check("Every Gemini voice is a non-empty string", all(isinstance(v, str) and v.strip() for v in gemini_voices))
# Reader is male-only (owner): no female-tagged voice may appear in the picker.
_ui_voices = ai.get_tts_voices()
check("Reader catalogue is male-only (no female voices)",
      not any(v.get("gender") == ai.GENDER_FEMALE for v in _ui_voices))
check("Reader catalogue still has male voices", any(v.get("gender") == ai.GENDER_MALE for v in _ui_voices))
check("No purged female id leaks into the Gemini known-voice list",
      not any(g in gemini_voices for g in ("Kore", "Leda", "Aoede", "Despina")))
check("Voxtral exposes its supported British male default",
      ai.OPENROUTER_TTS_DEFAULT_VOICES.get(
          "mistralai/voxtral-mini-tts-2603") == "gb_oliver_neutral"
      and "gb_oliver_neutral" in ai.OPENROUTER_TTS_VOICES.get(
          "mistralai/voxtral-mini-tts-2603", []))
check("MAI Voice exposes its supported English default",
      ai.OPENROUTER_TTS_DEFAULT_VOICES.get(
          "microsoft/mai-voice-2") == "en-US-Harper:MAI-Voice-2"
      and "en-US-Harper:MAI-Voice-2" in ai.OPENROUTER_TTS_VOICES.get(
          "microsoft/mai-voice-2", []))

print("\n== reader_tts_models — has_key logic (VAL-READER-013) ==")
import webui_shell


class _ReaderSettings:
    def __init__(self, **values):
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


api = webui_shell.Api.__new__(webui_shell.Api)
api.settings = _ReaderSettings(
    reader_tts_provider="openrouter", openrouter_api_key="", openai_api_key="")
info_no_key = api.reader_tts_models()
check("reader_tts_models endpoint reports no key", info_no_key.get("has_key") is False)
api.settings.set("openrouter_api_key", "sk-or-v1-testkey1234")
info_with_key = api.reader_tts_models()
check("reader_tts_models endpoint reports configured key", info_with_key.get("has_key") is True)
check("reader_tts_models endpoint returns selectable voices",
      bool(info_with_key.get("voices")))
api.settings.set("reader_tts_model", "mistralai/voxtral-mini-tts-2603")
api.settings.set("reader_voice", "Fenrir")  # valid Gemini voice, invalid here
info_voxtral = api.reader_tts_models()
check("reader_tts_models heals a cross-model voice to Voxtral's default",
      info_voxtral.get("default_voice") == "gb_oliver_neutral")

# ============================================================ TTSProvider architecture
print("\n== TTSProvider abstraction (VAL-TTS-001, VAL-TTS-002, VAL-TTS-003, VAL-TTS-006) ==")

# Provider registry has >=2 providers
providers = ai.list_tts_providers()
check("list_tts_providers returns >=2 providers", len(providers) >= 2)
check("OpenRouter provider exists", any(p["id"] == "openrouter" for p in providers))
check("OpenAI provider exists", any(p["id"] == "openai" for p in providers))
check("each provider has id + label + auth_setting + has_key + default_model",
      all(all(k in p for k in ("id", "label", "auth_setting", "has_key", "default_model"))
          for p in providers))

# get_tts_provider returns a working instance
or_prov = ai.get_tts_provider("openrouter")
check("OpenRouter provider instance has provider_id", or_prov.provider_id == "openrouter")
check("OpenRouter provider instance has default_model",
      or_prov.default_model == ai.OPENROUTER_TTS_DEFAULT_MODEL)

oa_prov = ai.get_tts_provider("openai")
check("OpenAI provider instance has provider_id", oa_prov.provider_id == "openai")
check("OpenAI provider instance has default_model",
      oa_prov.default_model == "gpt-4o-mini-tts")

# list_voices returns voices with required tags
or_voices = or_prov.list_voices()
check("OpenRouter list_voices returns >0 voices", len(or_voices) > 0)
check("OpenRouter voices have required fields: id, name, gender, quality, persona, provider, model",
      all(all(k in v for k in ("id", "name", "gender", "quality", "persona", "provider", "model"))
          for v in or_voices[:5]))

oa_voices = oa_prov.list_voices()
check("OpenAI list_voices returns 13 voices (gpt-4o-mini-tts)", len(oa_voices) == 13)
check("OpenAI voices have required fields",
      all(all(k in v for k in ("id", "name", "gender", "quality", "persona", "provider", "model"))
          for v in oa_voices[:3]))

# All voices from both providers have quality="high" (VAL-TTS-003)
all_voices = ai.get_tts_voices()
check("All voices are high-quality only",
      all(v.get("quality") == ai.QUALITY_HIGH for v in all_voices))
check("No standard/preview/legacy quality voices leak through",
      not any(v.get("quality") in (ai.QUALITY_PREVIEW, ai.QUALITY_STANDARD, ai.QUALITY_LEGACY)
              for v in all_voices))

# Male voices are ordered first (VAL-TTS-002)
male_indices = [i for i, v in enumerate(all_voices) if v.get("gender") == ai.GENDER_MALE]
female_indices = [i for i, v in enumerate(all_voices) if v.get("gender") == ai.GENDER_FEMALE]
if male_indices and female_indices:
    check("Male voices ordered before female voices",
          max(male_indices) < min(female_indices))

# Every voice has provider + quality + gender tags (VAL-TTS-006)
check("Every voice has provider tag", all(v.get("provider") for v in all_voices))
check("Every voice has quality tag", all(v.get("quality") for v in all_voices))
check("Every voice has gender tag", all(v.get("gender") for v in all_voices))
check("Every voice has provider_label for UI", all(v.get("provider_label") for v in all_voices))

# Gender values are valid
valid_genders = {ai.GENDER_MALE, ai.GENDER_FEMALE, ai.GENDER_NEUTRAL}
check("All gender tags are valid (male/female/neutral)",
      all(v.get("gender") in valid_genders for v in all_voices))

# OpenAI male voices are properly tagged
oa_male = [v for v in oa_voices if v.get("gender") == ai.GENDER_MALE]
check("OpenAI has male voices (onyx, ash, echo, sage, ballad, cedar, marin)",
      len(oa_male) >= 7)
check("OpenAI onyx voice is male", any(v["id"] == "onyx" and v["gender"] == "male" for v in oa_voices))
check("OpenAI alloy voice is female", any(v["id"] == "alloy" and v["gender"] == "female" for v in oa_voices))

# Persona mapping exists
check("OpenAI onyx has persona 'deep'",
      any(v["id"] == "onyx" and v["persona"] == "deep" for v in oa_voices))
check("OpenAI cedar has persona 'narrator'",
      any(v["id"] == "cedar" and v["persona"] == "narrator" for v in oa_voices))

# get_tts_defaults returns valid defaults
pid, model, voice = ai.get_tts_defaults()
check("get_tts_defaults returns a provider id", bool(pid))
check("get_tts_defaults returns a default model", bool(model))
check("get_tts_defaults returns a default voice", bool(voice))

# Default model is NOT the broken openai/gpt-4o-mini-tts (VAL-TTS-008)
check("Default TTS model is NOT openai/gpt-4o-mini-tts",
      model != "openai/gpt-4o-mini-tts")
check("Default TTS model is a working id (google/gemini)",
      model == "google/gemini-3.1-flash-tts-preview")

# All constants are defined
check("QUALITY_HIGH defined", ai.QUALITY_HIGH == "high")
check("GENDER_MALE defined", ai.GENDER_MALE == "male")
check("GENDER_FEMALE defined", ai.GENDER_FEMALE == "female")
check("PERSONA_DEEP defined", ai.PERSONA_DEEP == "deep")

# ============================================================ fallback (VAL-TTS-007)
print("\n== frozen provider — primary failure does not cross providers ==")
# Build two fake providers: the authorized provider fails and the other would work.
class _MockProvider:
    def __init__(self, pid, should_fail=False):
        self.provider_id = pid
        self._fail = should_fail
        self.calls = 0
    def synthesize(self, text, voice_id=None, model=None,
                   response_format=None, timeout=60, route_decision=None,
                   operation_lane=None):
        self.calls += 1
        if self._fail:
            raise RuntimeError(f"{self.provider_id} is down")
        return (b"fake_audio_" + self.provider_id.encode(),
                "audio/mpeg")

# Monkey-patch get_tts_provider so we control the fallback order.
_orig_get = ai.get_tts_provider
_fake_map = {
    "openrouter": _MockProvider("openrouter", should_fail=True),
    "openai": _MockProvider("openai", should_fail=False),
}
ai.get_tts_provider = lambda pid: _fake_map.get(pid, _fake_map["openrouter"])

try:
    # One frozen route authorizes exactly one provider. It may fall back to a
    # sibling model, but it must never cross to OpenAI.
    audio, ctype, meta = ai.synthesize_with_fallback(
        "hello", voice_id="Kore", provider_id="openrouter",
        route_decision=_openrouter_route())
    check("provider failure fails closed", meta.get("ok") is False)
    check("provider failure returns no audio", audio is None)
    check("provider failure returns no content-type", ctype is None)
    check("frozen route never crosses to OpenAI", _fake_map["openai"].calls == 0)

    # Both fail → hard error.
    _fake_map["openai"]._fail = True
    audio2, ctype2, meta2 = ai.synthesize_with_fallback(
        "hello", voice_id="Kore", provider_id="openrouter",
        route_decision=_openrouter_route())
    check("all-fail returns ok=False", meta2.get("ok") is False)
    check("all-fail has an error message", bool(meta2.get("message")))
    check("all-fail returns None audio", audio2 is None)
    check("all-fail returns None content-type", ctype2 is None)
    _fake_map["openai"]._fail = False  # restore for remaining checks

    # Primary succeeds → no fallback needed.
    _fake_map["openrouter"]._fail = False
    audio3, ctype3, meta3 = ai.synthesize_with_fallback(
        "hello", voice_id="Kore", provider_id="openrouter",
        route_decision=_openrouter_route())
    check("primary-success returns ok=True", meta3.get("ok") is True)
    check("primary-success has no fallback flag",
          meta3.get("fallback") is not True)
    check("primary-success uses primary provider",
          meta3.get("provider") == "openrouter")
    check("primary-success returns audio", audio3 == b"fake_audio_openrouter")

finally:
    ai.get_tts_provider = _orig_get

# ============================================== intra-catalogue fallback (VAL-TTS-008)
print("\n== frozen model — a bad OpenRouter model does not try siblings ==")
_orig_get2 = ai.get_tts_provider
_cat = [m for (m, _l, _f) in ai.OPENROUTER_TTS_MODELS]


class _ModelAwareProvider:
    """Mock that fails for specific model ids and records what it was asked."""
    def __init__(self, pid, bad_models):
        self.provider_id = pid
        self._bad = set(bad_models)
        self.tried = []

    def synthesize(self, text, voice_id=None, model=None,
                   response_format=None, timeout=60, route_decision=None,
                   operation_lane=None):
        self.tried.append(model)
        if model in self._bad:
            raise RuntimeError(f"model {model} 404")
        return (b"ok_" + str(model).encode(), "audio/wav")


# The frozen model fails while siblings and OpenAI would succeed. Neither may
# be reached because one route authorizes one exact provider and model.
_or = _ModelAwareProvider("openrouter", bad_models=[_cat[0]])
_oa = _ModelAwareProvider("openai", bad_models=[])
ai.get_tts_provider = lambda pid: {"openrouter": _or, "openai": _oa}.get(pid, _or)
try:
    audio, ctype, meta = ai.synthesize_with_fallback(
        "hi", model=_cat[0], voice_id="Fenrir", provider_id="openrouter",
        route_decision=_openrouter_route(_cat[0]))
    check("frozen-model: failure returns ok=False", meta.get("ok") is False)
    check("frozen-model: OpenAI was never reached", _oa.tried == [])
    check("frozen-model: only the authorized model was tried",
          _or.tried == [_cat[0]])
    check("frozen-model: no audio was returned", audio is None and ctype is None)
finally:
    ai.get_tts_provider = _orig_get2

# ============================================ PCM sample-rate from Content-Type (VAL-TTS-009)
print("\n== PCM rate parsing — honour the response's signalled sample rate ==")
check("rate parsed from 'audio/L16;rate=16000'",
      ai._pcm_rate_from_ctype("audio/L16;rate=16000") == 16000)
check("rate parsed with spaces 'audio/pcm; rate=22050'",
      ai._pcm_rate_from_ctype("audio/pcm; rate=22050") == 22050)
check("missing rate → 24k default",
      ai._pcm_rate_from_ctype("audio/wav") == ai._TTS_PCM_RATE)
check("empty content-type → default",
      ai._pcm_rate_from_ctype("") == ai._TTS_PCM_RATE)
check("absurd rate rejected → default",
      ai._pcm_rate_from_ctype("audio/L16;rate=999999999") == ai._TTS_PCM_RATE)
check("non-numeric rate rejected → default",
      ai._pcm_rate_from_ctype("audio/L16;rate=abc") == ai._TTS_PCM_RATE)

# ===================================================================== final
if _fails:
    print(f"\n{len(_fails)} FAILED: {_fails}")
    sys.exit(1)
else:
    print("\nALL GREEN")
    sys.exit(0)
