#!/usr/bin/env python3
"""Persisted user settings for Mumble (stored at %APPDATA%\\Mumble\\settings.json)."""

import json
import math
import os
import shutil
import threading
import time

import branding


SEARCH_HOTKEY_DEFAULT = "ctrl+alt+f"
SEARCH_HOTKEY_LEGACY_DEFAULT = "ctrl+alt+s"


# --- Cross-process file lock (portable) ------------------------------------
# The controller and the web window are SEPARATE processes that share one
# settings.json. A simple per-process threading.Lock cannot coordinate across
# processes, so two near-simultaneous saves could still interleave. This tiny
# advisory lock (an O_EXCL marker file with a stale-steal timeout) closes that
# window. It is portable (no msvcrt/fcntl) so the macOS/Linux ports reuse it
# verbatim. If it cannot be taken in time, the save fails closed: writing
# without the lock would let the controller and web process clobber each other.
def _lock_path():
    return branding.SETTINGS_PATH + ".lock"


def _acquire_lock(timeout=3.0, stale=30.0):
    """Return an open fd holding the lock, or None if it couldn't be taken."""
    path = _lock_path()
    start = time.time()
    while True:
        try:
            return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            # Steal a lock left behind by a crashed writer.
            try:
                if time.time() - os.path.getmtime(path) > stale:
                    os.remove(path)
                    continue
            except OSError:
                pass
            if time.time() - start > timeout:
                return None
            time.sleep(0.02)
        except OSError:
            return None


def _release_lock(fd):
    if fd is None:
        return
    owns_path = False
    try:
        held = os.fstat(fd)
        current = os.stat(_lock_path())
        owns_path = (
            getattr(held, "st_ino", None) == getattr(current, "st_ino", None)
            and getattr(held, "st_dev", None) == getattr(current, "st_dev", None)
        )
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass
    if owns_path:
        try:
            os.remove(_lock_path())
        except OSError:
            pass


def _fsync_parent(path):
    """Best-effort directory sync after an atomic replace."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    flags = getattr(os, "O_RDONLY", 0)
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    fd = None
    try:
        fd = os.open(directory, flags)
        os.fsync(fd)
    except (AttributeError, OSError):
        # Directory fsync is unavailable on some Windows/filesystem pairs. The
        # file itself is still fsynced before replace.
        pass
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

DEFAULTS = {
    "hotkey": "ctrl+windows",
    # Two SEPARATE history actions (owner v9): quick_paste_hotkey pastes the most
    # recent transcript straight into the focused field; history_hotkey opens the
    # History window. They used to be fused on Ctrl+Alt+V (paste + open), which
    # confused the two concepts — now each has its own bind.
    "quick_paste_hotkey": "ctrl+alt+v",
    "history_hotkey": "ctrl+alt+d",
    # Ctrl+Alt+S is used by external search/assistant workflows. "F" keeps the
    # mnemonic (Find) while staying distinct from Mumble's actual registered
    # record, paste-latest and Deck chords.
    "search_hotkey": SEARCH_HOTKEY_DEFAULT,
    # Local app/file results come first. Perplexity is the default web fallback;
    # Google and Brave remain selectable in Settings.
    "search_engine": "perplexity",   # web fallback: google | perplexity | brave
    "browser": "default",            # web fallback opens in the chosen browser
    "system_search_include_files": True,
    "system_search_roots": [],        # empty = standard user folders
    "system_search_max_items": 75000,
    # SMALL is the default (owner directive 2026-06-12): the most accurate of the
    # on-device models, and the cloud AI pass cleans up anything in the same call.
    # Tiny (fastest) and Base (balanced) stay selectable in Settings for slower PCs.
    "model": "small.en",
    "language": "en",
    # Local model sizing is detected automatically; there is no user-facing PC class.
    "english_only": True,           # English-only (.en) models: lighter, faster, more accurate for English
    # The language you mostly dictate in. When you speak more than one language
    # (english_only == False) Mumble swaps the on-device model to the best fit
    # for THIS language (branding.MODEL_BY_LANGUAGE); "en" keeps the multilingual
    # default. Set from onboarding's language step. ISO-639-1 code.
    "primary_language": "en",
    # --- Transcription engine (ADVANCED, opt-in) -----------------------------
    # "local" (DEFAULT) = on-device faster-whisper; audio NEVER leaves the
    # machine. "cloud" = upload each utterance to a low-latency hosted Whisper
    # endpoint for faster voice-to-text on modest hardware. Kept deliberately
    # hidden/advanced — most users want local. Cloud falls back to local on any
    # error, and the mode-key (word-timestamp) path always uses local.
    "transcription_mode": "local",   # local | cloud
    # Device-only privacy override. When ON, both speech-to-text and transcript
    # shaping stay on-device even if Cloud/Pro providers and keys remain saved.
    # The local engine keeps processing best-effort and appends an upgrade notice
    # for work beyond the edge rather than deleting or weakening configuration.
    "local_only_mode": False,
    # On-device LLM (fully-offline smart-mode shaping). When a small GGUF model is
    # present (dropped into DATA_DIR/models/, or pointed at by local_llm_model) and
    # local_llm_enabled is on, the prompt/email/reply lanes run through the local
    # model via the bundled llama.cpp binary instead of degrading to the templated
    # builder. No model present → this lane stays dormant and shaping is unchanged.
    "local_llm_enabled": False,
    "local_llm_model": "",          # explicit GGUF path; "" = auto-discover in models/
    # Which hosted STT provider when transcription_mode == "cloud". Groq runs
    # Whisper v3 Turbo at ~216x real-time — the fastest option and the default.
    "cloud_transcription_provider": "groq",  # groq | openai | openrouter
    # Per-provider STT model. The API KEYS are shared with the LLM provider
    # settings below (groq_api_key / openai_api_key / openrouter_api_key) — same
    # account key — so there are no separate transcription key fields.
    "groq_transcription_model": "whisper-large-v3-turbo",
    "openai_transcription_model": "gpt-4o-mini-transcribe",
    # OpenRouter routes STT to Groq/OpenAI/Google/Mistral under one key. The id
    # must be a namespaced route id; the old "openai/gpt-4o-mini-transcribe" does
    # NOT resolve on OpenRouter's /audio/transcriptions and 400s every utterance
    # (then silently fails over to local). Default to the working catalogue id —
    # matches transcription.PROVIDERS["openrouter"]["default_model"].
    "openrouter_transcription_model": "groq/whisper-large-v3-turbo",
    # Reader (document-to-audio) TTS — the AI voice the Reader speaks with. Uses
    # the shared openrouter_api_key. Persisted so the choice survives a restart
    # and is selectable in Settings (see webui_shell.reader_tts / reader_tts_models).
    # Must be an id OpenRouter actually hosts — the old "openai/gpt-4o-mini-tts"
    # is not on OpenRouter; default to the live catalogue default in ai.py.
    "reader_tts_provider": "openrouter",
    "reader_tts_model": "google/gemini-3.1-flash-tts-preview",
    "reader_voice": "Fenrir",  # Gemini voice; OpenAI's `onyx` is incompatible
    # Foreign mode prioritises these languages when resolving non-English terms.
    # Arabic by default (Foreign mode's original purpose); more added in Settings.
    # Multiple may be selected.
    "foreign_languages": ["arabic"],
    # The optional Foreign toggle. `island_foreign_toggle` shows/hides the Foreign
    # chip on the island bar (shown by default). `foreign_mode`
    # is the live chip state: tapping it ON forces the on-device foreign-term booster
    # to run for the next dictation even for an English-primary user (multilingual
    # users get the booster automatically; see _process). The chip is visible by
    # default while the live Foreign state remains off until the user chooses it.
    "island_foreign_toggle": True,
    "foreign_mode": False,
    # Ordered processing modes shown on the floating island.  These were
    # previously implicit/unknown keys, which meant a factory reset deleted a
    # user-customised list and load() could not validate its type.
    "island_modes": ["prompt", "email"],
    "island_active_mode": "",
    "mic_device": None,
    "format_enabled": True,
    "ai_warmup": False,  # pre-warm AI on record-start — OFF: pointless on Cerebras + wastes a request
    # The "mode key" — held WHILE you speak the keyword to arm mode detection. Outside
    # this button window, every utterance is treated as plain text (no mode scanning).
    # Right-SHIFT by default: it does NOT overlap the Ctrl+Windows record hotkey, so
    # tapping the record hotkey can never accidentally arm a mode (Right-Ctrl did —
    # the "Ctrl" in Ctrl+Windows matched it). Switchable in Settings.
    # The Big Shift retired the held "mode key" (Right-Shift). Modes are no longer
    # selected — Prompt is a sticky toggle (prompt_mode_enabled). Every plain
    # dictation produces clean, punctuated text only. These two keys are KEPT
    # only so an old settings.json round-trips cleanly; nothing reads them in
    # live code any more.
    "mode_key": "right shift",
    "mode_button_enabled": False,
    # THE BIG SHIFT — Prompt is the ONE explicit mode, a sticky on/off toggle
    # (island widget + Settings). OFF (default) = plain clean text; ON =
    # every dictation is crafted into a polished AI prompt (the constitution lane).
    "prompt_mode_enabled": False,
    # Auto-formatting is OFF by default — all plain dictations produce clean,
    # punctuated text only. No list inference, no email guessing.
    "auto_format": False,
    # Keep the Deck (History) permanently available — pinned always-on-top so it
    # never falls behind other apps. One-click un-pin in the Deck; survives restart.
    "deck_pinned": True,
    # Let the AI give a free "the local detector got the mode wrong" second opinion
    # (it's already reading the text to polish it, so the MODE/CONF tail costs nothing).
    # High-confidence email/reply self-correct in the same call; high-confidence
    # prompt auto-reruns with the constitution; low-confidence just shows a hint chip.
    "panel_animate": True,  # animate panels' expand (off = respect reduced motion)
    # Visual experience for the web UI: "lite" (same layout, no blur/heavy
    # shadows/ambient animation — low-end hardware) | "standard" (full
    # liquid-glass) | "enhanced" (owner-approved 2026-06-12: real glass
    # everywhere, ambient gold dust, richer motion). Chosen in onboarding;
    # changeable in Settings → System.
    "ui_effects": "enhanced",
    # Resource Saver Mode (owner v8): when True, forces the lightest config across
    # the whole app — not just visuals. The UI drops all animation/transition/blur/
    # shadow/transparency, uses lite rendering and eased polling; AND the engine
    # loads the lightest local model ("tiny.en", overriding "model" at load time
    # without changing the saved value), disables live (streaming) transcription
    # (one full pass at stop instead of background chunk decoding), and never fires
    # the cloud warm-up (regardless of "ai_warmup"). Reliability + low CPU/GPU/
    # battery over fidelity ("works everywhere"). Independent of the visual-effects
    # tier above; toggling it live reloads the model so the change applies at once.
    "resource_saver": False,
    # (owner v6) The floating island has exactly ONE premium look now — no
    # Basic/Standard/Enhanced tiers, so there is no "island_style" setting.
    "modes": {
        "prompt": True,
        "email": True,
        "foreign": True,
        "convert": True,
    },
    # Standing, global prompt-shaping preferences (replaces the old per-prompt pop-up).
    # Folded into the prompt-mode call every time; the spoken request always overrides.
    "prompt_prefs": {
        "tone": "Neutral",
        "detail": "Balanced",
        "structure": "Bullets & headings",
        "audience": "General",
        "reasoning": "Just the answer",
    },
    # Keyword-triggered prompt templates. Map spoken keywords (detected in the
    # mode-key window) to short system-prompt fragments appended to the prompt path.
    # e.g. "blog" → blog-writer prefix, "bug" → bug-report scaffold.
    "prompt_keywords": {
        "blog": "Write in a warm, engaging blog-post style with an introduction, subheadings, and a conclusion.",
        "bug": "Write a structured bug report with steps to reproduce, expected behaviour, actual behaviour, and environment details.",
        "tweet": "Write as a concise, punchy social media post — maximum 280 characters, with relevant hashtags.",
        "email2": "Write as a formal business email with a clear subject line, professional greeting, and polished sign-off.",
    },
    # Personal vocabulary — two tiers, both edited in the same Settings card:
    # vocabulary_terms (DEFAULT workflow): just the correct words/names/jargon.
    #   They bias transcription (hotwords) and mis-heard variants are found
    #   automatically (phonetic + fuzzy match; high-confidence auto-fixed,
    #   medium-confidence offered to the AI as 'heard//Term').
    "vocabulary_terms": [],
    # vocabulary (ADVANCED workflow): explicit mis-hearing → correction pairs
    # for stubborn cases, e.g. {"mambo": "Mumble"}. Applied first, verbatim.
    "vocabulary": {},
    # --- Experimental correction learning ---------------------------------
    # Opt-in and local-only. After a plain dictation, the island offers a short
    # "Fix" action; the user edits the pasted result and Mumble derives only the
    # changed word/phrase pairs. Nothing is inferred from ambient typing and no
    # correction is learned without the user's explicit confirmation.
    "correction_learning_enabled": False,
    # Best-effort Windows UI Automation monitor for the one field Mumble just
    # pasted into. It lives for at most 30 seconds, skips password controls and
    # keeps the field snapshot in memory only. Detected edits are still review-
    # gated on the island; this never means silent auto-accept.
    "correction_learning_auto_detect": True,
    # How hard the plain-text polisher edits: Light = smallest fixes (keep exact words),
    # Standard = also smooth grammar/awkward phrasing, Thorough = full cleanup.
    "polish_aggressiveness": "Light",
    # Fast default for everyday Text: deterministic local cleanup and immediate
    # paste instead of waiting for network polishing. Explicit AI modes keep
    # their full provider-backed processing.
    "instant_text": True,
    "user_name": "",
    "autostart": True,
    "first_run": True,
    "compute_type": "int8",
    "device": "auto",  # transcription device: auto (CUDA if present) | cpu | cuda —
                       # must be in DEFAULTS or load() strips a saved override
    "cpu_threads": 0,  # 0 = auto: physical-core-scale budget (avoids SMT contention)
    "min_seconds": 0.3,
    "history_max": 5000,
    "clipboard_enabled": True,
    "clipboard_max": 5000,
    "pro_mode": True,
    # Text-processing engine. Product UI deliberately offers only these two.
    "llm_provider": "cerebras",  # cerebras | openrouter
    # Cerebras (recommended)
    "cerebras_api_key": "",
    "cerebras_model": "gpt-oss-120b",
    # Groq/OpenAI keys are retained only for optional cloud transcription.
    "groq_api_key": "",
    "openai_api_key": "",
    # OpenRouter (one key → many models; OpenAI-compatible). Model ids are
    # namespaced, e.g. "openai/gpt-5.4-mini", "anthropic/claude-opus-4-8".
    "openrouter_api_key": "",
    "openrouter_model": "openai/gpt-5.4-mini",
    # Local LLM (Ollama, LM Studio, Llama.cpp)
    "local_api_key": "lm-studio",
    "local_model": "llama3",
    "local_url": "http://localhost:11434/v1",
    "pro_default_applied": False,
    "limits_100_applied": False,
    "limits_5000_applied": False,   # MUST be here, like its siblings: load()
                                    # only keeps keys present in DEFAULTS, so a
                                    # missing guard flag is stripped every launch
                                    # and its migration re-runs — re-forcing the
                                    # history limits and clobbering user changes.
    "model_small_applied": False,
    "model_small_default_applied": False,
    "enhanced_default_applied": False,
    "mode_key_v2_applied": False,
    "local_provider_retired_applied": False,
    "search_perplexity_default_applied": False,
    "search_hotkey_find_default_applied": False,
    "big_shift_applied": False,
    "foreign_island_default_applied": False,
    # Cloud sync (Supabase) — credentials entered by the owner
    "supabase_url": "",
    "supabase_anon_key": "",
    # Per-data-type sync toggles. Master toggle: sync_enabled.
    # When off, no data of that type is pushed or pulled.
    "sync_enabled": True,
    "sync_settings": True,
    "sync_stats": True,
    "sync_history": True,
    # Documents can contain substantially more sensitive material than small
    # settings/preferences. Reader cloud sync is therefore explicit opt-in.
    "sync_reader": False,
    "sync_favorites": True,
    "sync_presets": True,
    "sync_last_run": {},   # per-data-type last-sync timestamps
    # Meetings records this value on each capture.  Keep it in the settings
    # schema so reset/load semantics are explicit instead of relying on an
    # unknown forward-compatible key.
    "meeting_processing_mode": "lightweight",
    # Compatibility guards are metadata only.  Migrations may mark them
    # complete, but must never use a missing guard as permission to overwrite a
    # user preference (see Settings._migrate).
    "stt_tts_dead_default_healed": False,
    "reader_tts_voice_contract_applied": False,
}

# Retired product concepts.  They are intentionally inert, but retained in an
# existing settings.json so an upgrade never destroys user-supplied values or
# credentials.  Current code does not project or consume them; an explicit
# factory reset is the only operation that removes them.
LEGACY_SETTINGS = {
    "computer_control_enabled",
    "computer_control_allow_cloud_planning",
    "computer_control_auto_run_low_risk",
    "computer_control_auto_run_acknowledged",
    "computer_control_stop_hotkey",
    "computer_control_max_steps",
    "wake_word_enabled",
    "wake_word_threshold",
    "hardware_tier",
    "hardware_tier_resolved",
    "prompt_memory",
    "prompt_provider",
    "prompt_provider_cerebras_openrouter_applied",
    "deepseek_api_key",
    "deepseek_model",
    "groq_model",
    "openai_model",
    "anthropic_api_key",
    "anthropic_model",
}

SETTING_ENUMS = {
    "search_engine": {"google", "perplexity", "brave"},
    "browser": {"default", "edge", "chrome", "brave", "chromium"},
    "transcription_mode": {"local", "cloud"},
    "cloud_transcription_provider": {"groq", "openai", "openrouter"},
    "llm_provider": {"cerebras", "openrouter"},
    "compute_type": {"int8", "float16", "float32"},
    "device": {"auto", "cpu", "cuda"},
    "polish_aggressiveness": {"Light", "Standard", "Thorough"},
    "ui_effects": {"lite", "standard", "enhanced"},
    "meeting_processing_mode": {"lightweight", "deep"},
}

SETTING_RANGES = {
    "system_search_max_items": (1, 1_000_000),
    "cpu_threads": (0, 512),
    "min_seconds": (0.05, 600.0),
    "history_max": (1, 1_000_000),
    "clipboard_max": (1, 1_000_000),
}

STRING_LIST_SETTINGS = {
    "system_search_roots",
    "foreign_languages",
    "island_modes",
    "vocabulary_terms",
}


def validate_setting_value(key, value, *, strict_enums=True):
    """Return ``(ok, message)`` for one persisted setting value.

    Unknown keys remain forward-compatible. Known values are kept structurally
    safe for runtime consumers; interactive/cloud writes additionally enforce
    currently supported enum choices. Loading deliberately permits an old
    provider/model string so it can be shown as configured-but-unavailable
    instead of being rewritten behind the user's back.
    """
    key = str(key)
    if key not in DEFAULTS:
        try:
            json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return False, f"{key} is not JSON-serialisable."
        return True, ""

    default = DEFAULTS[key]
    if default is None:
        if value is None:
            return True, ""
        if key == "mic_device" and isinstance(value, int) and not isinstance(value, bool):
            return (value >= -1, "Microphone device must be -1 (automatic) or a non-negative index.")
        return False, f"{key} has an invalid value."

    expected = type(default)
    if expected is bool:
        if isinstance(value, bool):
            return True, ""
        if not strict_enums and (
                value in (0, 1) or
                (isinstance(value, str) and value.strip().lower() in {
                    "true", "false", "1", "0", "yes", "no", "on", "off"
                })):
            return True, ""
        return False, f"{key} must be true or false."
    if expected is int:
        candidate = value
        if (not strict_enums and isinstance(value, str)
                and value.strip().lstrip("-").isdigit()):
            candidate = int(value)
        if not isinstance(candidate, int) or isinstance(candidate, bool):
            return False, f"{key} must be a whole number."
        lo, hi = SETTING_RANGES.get(key, (-2**31, 2**31 - 1))
        if not lo <= candidate <= hi:
            return False, f"{key} must be between {lo} and {hi}."
        return True, ""
    if expected is float:
        candidate = value
        if not strict_enums and isinstance(value, (int, str)) and not isinstance(value, bool):
            try:
                candidate = float(value)
            except (TypeError, ValueError):
                candidate = value
        if not isinstance(candidate, (int, float)) or isinstance(candidate, bool):
            return False, f"{key} must be a number."
        candidate = float(candidate)
        if not math.isfinite(candidate):
            return False, f"{key} must be finite."
        lo, hi = SETTING_RANGES.get(key, (-1e12, 1e12))
        if not lo <= candidate <= hi:
            return False, f"{key} must be between {lo} and {hi}."
        return True, ""
    if expected is str:
        if not isinstance(value, str):
            return False, f"{key} must be text."
        if strict_enums and key in SETTING_ENUMS and value not in SETTING_ENUMS[key]:
            choices = ", ".join(sorted(SETTING_ENUMS[key]))
            return False, f"Choose one of: {choices}."
        return True, ""
    if expected is list:
        if not isinstance(value, list):
            return False, f"{key} must be a list."
        try:
            json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return False, f"{key} must contain JSON-serialisable values."
        if key in STRING_LIST_SETTINGS and not all(
                isinstance(item, str) for item in value):
            return False, f"{key} must contain text values only."
        return True, ""
    if expected is dict:
        if not isinstance(value, dict):
            return False, f"{key} must be an object."
        try:
            json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return False, f"{key} must contain JSON-serialisable values."
        if key == "modes":
            allowed = (bool,) if strict_enums else (bool, int, str)
            for child, state in value.items():
                if not isinstance(child, str) or not isinstance(state, allowed):
                    return False, "modes must map text names to true or false."
                if (not strict_enums and isinstance(state, int)
                        and not isinstance(state, bool) and state not in (0, 1)):
                    return False, "modes must map text names to true or false."
                if (not strict_enums and isinstance(state, str)
                        and state.strip().lower() not in {
                            "true", "false", "1", "0", "yes", "no", "on", "off"
                        }):
                    return False, "modes must map text names to true or false."
        return True, ""
    return False, f"{key} has an unsupported value type."


def _coerce_bool(v):
    """Coerce a JSON value to bool, handling STRING forms correctly. Plain
    bool('false') is True (every non-empty string is truthy), so a hand-edited
    `"autostart": "false"` would silently INVERT to enabled. Strings are matched
    explicitly: 'true'/'1'/'yes'/'on' -> True, everything else (incl. 'false',
    '0', '') -> False. Non-strings fall back to bool()."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "on")
    return bool(v)


class Settings:
    """Manages the loading, migrating, and saving of application settings."""
    def __init__(self):
        branding.ensure_dirs()
        self.data = json.loads(json.dumps(DEFAULTS))
        self._save_lock = threading.RLock()
        self._loaded_keys = set()
        # Last generation known to have reached disk.  Atomic child-map updates
        # use it as the common ancestor when a previous save is still dirty, so
        # retrying one child cannot silently discard either the pending edit or
        # a different child written by another process.
        self._base_data = json.loads(json.dumps(self.data))
        # Set when this process had to recover a damaged settings file.  The Web
        # Settings bridge projects the notice so recovery is visible to the user
        # instead of existing only as a stdout line.
        self.recovery_notice = ""
        # Keys THIS process has explicitly changed since the last flush. On save
        # we re-read the on-disk snapshot and overwrite ONLY these keys, so a
        # stale in-memory value (e.g. a blank API key the controller never saw)
        # can never clobber another process's edit. See _do_save.
        self._dirty = set()
        self.load()
        self._migrate()

    def _migrate(self):
        """Apply compatibility metadata without rewriting user choices.

        Older migrations treated a missing guard as permission to replace
        provider, privacy, retention, model, language and UI preferences.  That
        made merely starting a newer build capable of enabling a cloud route or
        downgrading a deliberately selected model.  Defaults already define the
        desired experience for a new install; upgrades must preserve every
        stored value, including inert legacy keys and credentials.

        The one compatibility inference retained here fills ``english_only``
        only when that key was genuinely absent from the loaded file.  It derives
        the missing value from the already-selected model and never changes the
        model itself.
        """
        before = json.loads(json.dumps(self.data))
        loaded = getattr(self, "_loaded_keys", set())
        if "english_only" not in loaded and "model" in loaded:
            model = str(self.data.get("model", "small.en")).lower()
            self.data["english_only"] = (
                model.endswith(".en") or model == "distil-large-v3"
            )

        # Move only the former Mumble default away from Ctrl+Alt+S, exactly
        # once. The completion guard is essential: a user may intentionally
        # choose Ctrl+Alt+S later, and a subsequent launch must preserve that
        # later choice.
        if not self.data.get("search_hotkey_find_default_applied"):
            if self.data.get("search_hotkey") == SEARCH_HOTKEY_LEGACY_DEFAULT:
                self.data["search_hotkey"] = SEARCH_HOTKEY_DEFAULT
            self.data["search_hotkey_find_default_applied"] = True

        # Guard values are internal bookkeeping only.  Mark old migrations as
        # complete while deliberately leaving all associated user values alone.
        guards = {
            "pro_default_applied",
            "limits_100_applied",
            "limits_5000_applied",
            "model_small_applied",
            "model_small_default_applied",
            "enhanced_default_applied",
            "mode_key_v2_applied",
            "local_provider_retired_applied",
            "search_perplexity_default_applied",
            "big_shift_applied",
            "foreign_island_default_applied",
            "stt_tts_dead_default_healed",
            "reader_tts_voice_contract_applied",
            "search_hotkey_find_default_applied",
        }
        for key in guards:
            self.data[key] = True

        changed_keys = {
            key for key in self.data if self.data.get(key) != before.get(key)
        }
        if changed_keys:
            self._dirty.update(changed_keys)
            self.save()

    def load(self):
        """Reload one coherent disk generation without racing local setters."""
        with self._save_lock:
            return self._load_unlocked()

    def _load_unlocked(self):
        """Loads settings from disk, applying defaults for missing keys."""
        pending_data = json.loads(json.dumps(self.data))
        pending_dirty = set(self._dirty)
        try:
            with open(branding.SETTINGS_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                raise ValueError("settings root must be a JSON object")
            for loaded_key, loaded_value in d.items():
                valid, message = validate_setting_value(
                    loaded_key, loaded_value, strict_enums=False
                )
                if not valid:
                    raise ValueError(f"invalid {loaded_key}: {message}")
        except FileNotFoundError:
            # A missing primary is a first run only when no backup exists. A
            # prior successful save leaves .bak; recover it before migration can
            # write defaults and replace the user's last-good generation.
            sp = branding.SETTINGS_PATH
            bak = sp + ".bak"
            try:
                with open(bak, "r", encoding="utf-8") as fb:
                    d = json.load(fb)
                if not isinstance(d, dict):
                    raise ValueError("settings backup root must be a JSON object")
                for backup_key, backup_value in d.items():
                    valid, message = validate_setting_value(
                        backup_key, backup_value, strict_enums=False
                    )
                    if not valid:
                        raise ValueError(f"invalid backup {backup_key}: {message}")
            except FileNotFoundError:
                return  # genuine first run — expected, use defaults
            except (json.JSONDecodeError, OSError, ValueError) as backup_error:
                preserved = bak + ".corrupt"
                try:
                    shutil.copyfile(bak, preserved)
                    branding.protect_private_path(preserved)
                except Exception:
                    pass
                self.recovery_notice = (
                    "The settings file was missing and its backup could not be "
                    "used. The backup was preserved as settings.json.bak.corrupt; "
                    "Mumble is using safe defaults."
                )
                print(f"[settings] missing primary and unusable backup "
                      f"({type(backup_error).__name__}): {backup_error}")
                return

            restore_tmp = sp + ".recover.tmp"
            lock_fd = _acquire_lock()
            try:
                if lock_fd is None:
                    raise TimeoutError(
                        "could not lock settings during missing-file recovery"
                    )
                with open(restore_tmp, "w", encoding="utf-8") as restored:
                    json.dump(d, restored, ensure_ascii=False, indent=2)
                    restored.flush()
                    os.fsync(restored.fileno())
                os.replace(restore_tmp, sp)
                branding.protect_private_path(sp)
                _fsync_parent(sp)
                self.recovery_notice = (
                    "The settings file was missing and was restored from the "
                    "last good backup."
                )
            except Exception as restore_error:
                self.recovery_notice = (
                    "The settings file was missing. Mumble loaded the last good "
                    "backup in memory, but could not restore the live file."
                )
                print(f"[settings] missing-file restore failed "
                      f"({type(restore_error).__name__}): {restore_error}")
                try:
                    if os.path.exists(restore_tmp):
                        os.remove(restore_tmp)
                except OSError:
                    pass
            finally:
                _release_lock(lock_fd)
        except (json.JSONDecodeError, OSError, ValueError) as e:
            # VAL-RECV-002: corrupted settings.json must not crash the app.
            # Recover with safe defaults (or the last-good backup), back up the
            # corrupted file so the user can inspect what went wrong, and notify.
            print(f"[settings] load error ({type(e).__name__}): {e}")
            sp = branding.SETTINGS_PATH
            bak = sp + ".bak"
            corrupt = sp + ".corrupt"
            # Always preserve the corrupted bytes in a distinct artifact (.corrupt)
            # that the save() path never touches, so they survive even after the
            # next successful save overwrites .bak with fresh defaults.
            try:
                if os.path.exists(sp):
                    shutil.copyfile(sp, corrupt)
                    branding.protect_private_path(corrupt)
            except Exception:
                pass
            # Attempt recovery from the last-good backup (.bak).
            recovered = None
            try:
                if os.path.exists(bak):
                    with open(bak, "r", encoding="utf-8") as fb:
                        recovered = json.load(fb)
                    if not isinstance(recovered, dict):
                        recovered = None
                    if isinstance(recovered, dict):
                        for recovered_key, recovered_value in recovered.items():
                            valid, _message = validate_setting_value(
                                recovered_key, recovered_value,
                                strict_enums=False,
                            )
                            if not valid:
                                recovered = None
                                break
            except Exception as e2:
                print(f"[settings] backup recovery also failed "
                      f"({type(e2).__name__}): {e2}")
                recovered = None
            if isinstance(recovered, dict):
                print("[settings] recovered from .bak — corrupted file backed "
                      "up to settings.json.corrupt. Using last-good values.")
                self.recovery_notice = (
                    "Settings were recovered from the last good backup. "
                    "The damaged file was preserved as settings.json.corrupt."
                )
                d = recovered
                # Heal the live file immediately.  Merely loading the backup into
                # memory leaves the corrupt primary in place when no migration or
                # user edit happens during this run, so every subsequent launch
                # repeats recovery forever.  Keep .bak untouched and atomically
                # replace only the already-preserved corrupt primary.
                restore_tmp = sp + ".recover.tmp"
                lock_fd = _acquire_lock()
                try:
                    if lock_fd is None:
                        raise TimeoutError(
                            "could not lock settings during backup recovery"
                        )
                    with open(restore_tmp, "w", encoding="utf-8") as restored:
                        json.dump(recovered, restored, ensure_ascii=False, indent=2)
                        restored.flush()
                        os.fsync(restored.fileno())
                    os.replace(restore_tmp, sp)
                    branding.protect_private_path(sp)
                    _fsync_parent(sp)
                except Exception as restore_error:
                    print(f"[settings] live-file restore failed "
                          f"({type(restore_error).__name__}): {restore_error}")
                    try:
                        if os.path.exists(restore_tmp):
                            os.remove(restore_tmp)
                    except OSError:
                        pass
                finally:
                    _release_lock(lock_fd)
            else:
                # No usable backup — rename the corrupted file to settings.json.bak
                # (clearing the live path for a fresh write) and fall back to the
                # safe defaults already in self.data. settings.json.corrupt above
                # preserves the bytes for inspection.
                lock_fd = _acquire_lock()
                try:
                    if lock_fd is not None and os.path.exists(sp):
                        if os.path.exists(bak):
                            try:
                                shutil.copyfile(bak, bak + ".corrupt")
                                branding.protect_private_path(bak + ".corrupt")
                            except Exception:
                                pass
                        os.replace(sp, bak)
                        branding.protect_private_path(bak)
                        _fsync_parent(bak)
                except Exception:
                    pass
                finally:
                    _release_lock(lock_fd)
                self.recovery_notice = (
                    "The settings file was damaged and no usable backup was "
                    "available. Mumble kept a recoverable copy and is using "
                    "safe in-memory defaults until settings can be saved."
                )
                print("[settings] corrupted file backed up to settings.json.bak "
                      "and settings.json.corrupt — loading safe defaults.")
                return
        # Build a fresh projection so keys deleted by another process do not
        # linger in memory and later reappear. Pending dirty keys are restored
        # after the disk generation has been fully loaded.
        self.data = json.loads(json.dumps(DEFAULTS))
        self._loaded_keys = set(d)
        for k, v in d.items():
            if k == "modes" and isinstance(v, dict):
                m = dict(DEFAULTS["modes"])
                # Preserve future/custom mode ids while normalising their
                # enabled state.  Dropping unknown children here meant an older
                # process could erase configuration written by a newer one.
                m.update({str(kk): _coerce_bool(vv) for kk, vv in v.items()})
                self.data["modes"] = m
            elif k == "prompt_prefs" and isinstance(v, dict):
                # Merge over defaults so a saved file missing a newer pref key still
                # gets a sensible value (never a KeyError downstream), while
                # retaining future/custom preference children verbatim.
                p = dict(DEFAULTS["prompt_prefs"])
                p.update({str(kk): vv for kk, vv in v.items()})
                self.data["prompt_prefs"] = p
            elif k == "prompt_keywords" and isinstance(v, dict):
                # These are user-extensible templates.  Merge new defaults but
                # never silently discard a custom or future keyword.
                kw = dict(DEFAULTS["prompt_keywords"])
                kw.update({str(kk): str(vv) for kk, vv in v.items()})
                self.data["prompt_keywords"] = kw
            elif k == "vocabulary" and isinstance(v, dict):
                # User-defined mapping — keep every string pair as-is.
                self.data["vocabulary"] = {
                    str(kk): str(vv) for kk, vv in v.items() if str(kk).strip()
                }
            elif k == "vocabulary_terms" and isinstance(v, list):
                self.data["vocabulary_terms"] = [
                    str(t).strip() for t in v if str(t).strip()
                ]
            elif k in DEFAULTS:
                # Type-check: coerce to the expected type from DEFAULTS
                # so a corrupt or hand-edited settings.json can't crash downstream.
                expected_type = type(DEFAULTS[k])
                wrong_type = (expected_type is not type(None)
                              and not isinstance(v, expected_type))
                # bool is a subclass of int in Python.  Treat it as the wrong
                # type for numeric settings (``history_max: true`` must not
                # silently become a one-item history).
                if expected_type in (int, float) and isinstance(v, bool):
                    self._dirty.add(k)
                    continue
                if wrong_type:
                    try:
                        if expected_type is bool:
                            v = _coerce_bool(v)
                        elif expected_type is int:
                            v = int(v)
                        elif expected_type is float:
                            v = float(v)
                        elif expected_type is str:
                            v = str(v)
                        else:
                            self._dirty.add(k)
                            continue  # skip uncoerceable types
                    except (ValueError, TypeError):
                        self._dirty.add(k)
                        continue  # skip uncoerceable value
                self.data[k] = v
            else:
                # Preserve unknown keys so a newly-added setting survives a
                # restart — the old code silently dropped every key not in
                # DEFAULTS, erasing new settings on the next save.
                self.data[k] = v
        self._base_data = json.loads(json.dumps(self.data))
        # A bridge refresh may call load() after a failed write.  Re-reading is
        # useful for unrelated externally changed keys, but pending local edits
        # still belong to the user and must remain dirty for an explicit retry.
        for key in pending_dirty:
            if key in pending_data:
                self.data[key] = pending_data[key]
            else:
                self.data.pop(key, None)

    def _read_disk_state(self):
        """Return ``(state, mapping)`` for the live settings file.

        Missing and damaged files are deliberately distinct.  Treating both as
        ``{}`` let a long-lived process overwrite a newly-corrupted primary with
        its stale snapshot and then copy that stale snapshot over the good
        backup.  Writers must fail closed on ``invalid``; startup recovery owns
        repair of that state.
        """
        try:
            with open(branding.SETTINGS_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                return "invalid", {}
            for key, value in d.items():
                valid, _message = validate_setting_value(
                    key, value, strict_enums=False
                )
                if not valid:
                    return "invalid", {}
            return "ok", d
        except FileNotFoundError:
            return "missing", {}
        except (json.JSONDecodeError, OSError, ValueError):
            return "invalid", {}

    def _read_disk(self):
        """Compatibility helper returning a mapping for callers that only read."""
        _state, data = self._read_disk_state()
        return data

    @staticmethod
    def _merge_snapshot(disk, local, dirty):
        """Merge a latest disk generation with only explicit local changes.

        Starting from ``local`` resurrected unknown keys deleted by another
        process (most visibly after factory reset).  Start from current defaults
        plus the latest disk generation, then overlay only keys this process has
        actually changed.  A dirty key absent from ``local`` is a tombstone.
        """
        out = json.loads(json.dumps(DEFAULTS))
        out.update(disk)
        for key in dirty:
            if key in local:
                out[key] = local[key]
            else:
                out.pop(key, None)
        return out

    @staticmethod
    def _merge_mapping_delta(current, base, local):
        """Overlay only local child changes since ``base`` onto ``current``."""
        current = dict(current) if isinstance(current, dict) else {}
        base = dict(base) if isinstance(base, dict) else {}
        local = dict(local) if isinstance(local, dict) else {}
        for child in base.keys() - local.keys():
            current.pop(child, None)
        for child, value in local.items():
            if child not in base or base.get(child) != value:
                current[child] = value
        return current

    @staticmethod
    def _merge_list_delta(current, base, local):
        """Merge pending list additions/removals without losing disk additions."""
        current = list(current) if isinstance(current, list) else []
        base = list(base) if isinstance(base, list) else []
        local = list(local) if isinstance(local, list) else []
        removed = {item for item in base if item not in local}
        merged = [item for item in current if item not in removed]
        for item in local:
            if item not in base and item not in merged:
                merged.append(item)
        return merged

    def save(self):
        """Thread-safe save of current settings to disk; report durability."""
        with self._save_lock:
            return self._do_save()

    def _do_save(self):
        branding.ensure_dirs()
        # --- Read-merge-before-write (cross-process anti-clobber) -------------
        # Two processes (controller + web window) share settings.json. Writing
        # our whole in-memory snapshot would wipe keys the OTHER process changed
        # but we never saw (the classic "API key drops after a few hours" bug:
        # the controller's stale blank key overwrites the one entered in the web
        # UI). Instead: re-read disk under a cross-process lock and overwrite
        # ONLY the keys this process actually changed (self._dirty); every other
        # key adopts the latest on-disk value. Keys we changed still win.
        fd = _acquire_lock()
        if fd is None:
            return False
        dirty = set(self._dirty)
        try:
            state, disk = self._read_disk_state()
            if state == "invalid":
                print("[settings] save refused: live settings file is invalid; "
                      "startup recovery must repair it first")
                return False
            if state == "ok":
                self.data = self._merge_snapshot(disk, self.data, dirty)
            saved = self._write_atomic()
            if saved:
                self._dirty.difference_update(dirty)
            return saved
        finally:
            _release_lock(fd)

    def _write_atomic(self):
        tmp = branding.SETTINGS_PATH + ".tmp"
        bak = branding.SETTINGS_PATH + ".bak"
        bak_tmp = bak + ".tmp"
        rollback_tmp = branding.SETTINGS_PATH + ".rollback.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                branding.protect_private_path(tmp)
                json.dump(self.data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, branding.SETTINGS_PATH)
            branding.protect_private_path(branding.SETTINGS_PATH)
            _fsync_parent(branding.SETTINGS_PATH)
        except Exception as e:
            print(f"[settings] save error ({type(e).__name__}): {e}")
            try:
                os.remove(tmp)
            except OSError:
                pass
            return False

        try:
            # Keep a last-good backup, but replace it atomically as well: a
            # process crash during copy must not turn the recovery file into a
            # half-written JSON document. COPY, never move, so the live primary
            # remains in place throughout.
            with open(branding.SETTINGS_PATH, "rb") as source, \
                    open(bak_tmp, "wb") as backup:
                branding.protect_private_path(bak_tmp)
                shutil.copyfileobj(source, backup)
                backup.flush()
                os.fsync(backup.fileno())
            os.replace(bak_tmp, bak)
            branding.protect_private_path(bak)
            _fsync_parent(bak)
            self._base_data = json.loads(json.dumps(self.data))
            return True
        except Exception as e:
            print(f"[settings] backup error ({type(e).__name__}): {e}")
            try:
                os.remove(bak_tmp)
            except OSError:
                pass
            # The primary was already replaced. If a previous last-good backup
            # exists, restore that generation so a reported failure really does
            # leave disk unchanged. The new in-memory value remains dirty and
            # can be retried explicitly.
            try:
                if os.path.exists(bak):
                    with open(bak, "rb") as source, \
                            open(rollback_tmp, "wb") as rollback:
                        branding.protect_private_path(rollback_tmp)
                        while True:
                            chunk = source.read(64 * 1024)
                            if not chunk:
                                break
                            rollback.write(chunk)
                        rollback.flush()
                        os.fsync(rollback.fileno())
                    os.replace(rollback_tmp, branding.SETTINGS_PATH)
                    branding.protect_private_path(branding.SETTINGS_PATH)
                    _fsync_parent(branding.SETTINGS_PATH)
            except Exception as rollback_error:
                print(f"[settings] rollback error "
                      f"({type(rollback_error).__name__}): {rollback_error}")
            finally:
                try:
                    if os.path.exists(rollback_tmp):
                        os.remove(rollback_tmp)
                except OSError:
                    pass
            return False

    def get(self, key, default=None):
        """Retrieve a setting by key, or return the default."""
        with self._save_lock:
            return self.data.get(key, default)

    def set(self, key, value):
        """Update one setting and return whether it reached disk."""
        with self._save_lock:
            valid, message = validate_setting_value(key, value)
            if not valid:
                print(f"[settings] rejected {key}: {message}")
                return False
            self.data[key] = value
            self._dirty.add(key)
            return self._do_save()

    def update(self, **kw):
        """Bulk update settings and return whether they reached disk."""
        with self._save_lock:
            for key, value in kw.items():
                valid, message = validate_setting_value(key, value)
                if not valid:
                    print(f"[settings] rejected {key}: {message}")
                    return False
            self.data.update(kw)
            self._dirty.update(kw.keys())
            return self._do_save()

    def atomic_mapping_update(self, key, mutator):
        """Atomically mutate one mapping-valued setting from the latest disk.

        Dotted Web UI updates such as ``prompt_prefs.tone`` and
        ``modes.email`` used to perform a read/modify/write against a stale
        in-memory parent mapping.  Two processes editing different children
        could therefore erase one another even though the top-level dirty-key
        merge was working as designed.  This transaction takes the shared file
        lock, starts from the newest persisted parent, applies one callback and
        writes the merged generation as a unit.
        """
        if not isinstance(key, str) or not key:
            raise TypeError("mapping key must be a non-empty string")
        if not callable(mutator):
            raise TypeError("mapping mutator must be callable")
        with self._save_lock:
            branding.ensure_dirs()
            fd = _acquire_lock()
            if fd is None:
                raise TimeoutError(f"could not lock settings for {key} update")
            original_data = self.data
            original_dirty = set(self._dirty)
            try:
                state, disk = self._read_disk_state()
                if state == "invalid":
                    raise ValueError(
                        "live settings file is invalid; recovery must repair it"
                    )
                if state == "ok":
                    merged = self._merge_snapshot(
                        disk, original_data, original_dirty - {key}
                    )
                else:
                    merged = json.loads(json.dumps(original_data))

                raw = merged.get(key, DEFAULTS.get(key, {}))
                mapping = dict(raw) if isinstance(raw, dict) else {}
                if key in original_dirty:
                    mapping = self._merge_mapping_delta(
                        mapping,
                        self._base_data.get(key, {}),
                        original_data.get(key, {}),
                    )
                result = mutator(mapping)
                # The callback receives the mutable object, but allowing it to
                # return a replacement is convenient for non-UI callers.
                if result is not None:
                    if not isinstance(result, dict):
                        raise TypeError("mapping mutator must return a dict or None")
                    mapping = dict(result)
                # Validate serialisability before swapping the live snapshot.
                valid, message = validate_setting_value(key, mapping)
                if not valid:
                    raise ValueError(message)
                merged[key] = mapping
                self.data = merged
                if not self._write_atomic():
                    raise OSError(f"atomic {key} settings write failed")
                self._dirty.difference_update(original_dirty)
                self._dirty.discard(key)
                return dict(mapping)
            except Exception:
                self.data = original_data
                self._dirty = original_dirty
                raise
            finally:
                _release_lock(fd)

    def atomic_vocabulary_update(self, mutator):
        """Atomically mutate the latest persisted vocabulary collections.

        ``vocabulary`` and ``vocabulary_terms`` are collection-valued settings.
        Marking those whole keys dirty is safe for an ordinary Settings form,
        but it is destructive for a background learner whose in-memory snapshot
        may predate a save from the separate web process.  This transaction
        acquires the same cross-process lock as normal saves, re-reads disk while
        holding it, and gives ``mutator(mapping, terms)`` fresh mutable copies.

        The callback's return value is passed through.  Lock acquisition fails
        closed: performing this merge without the lock would recreate the race
        this method exists to prevent.
        """
        if not callable(mutator):
            raise TypeError("vocabulary mutator must be callable")
        with self._save_lock:
            branding.ensure_dirs()
            fd = _acquire_lock()
            if fd is None:
                raise TimeoutError("could not lock settings for vocabulary update")
            original_data = self.data
            original_dirty = set(self._dirty)
            collection_keys = {"vocabulary", "vocabulary_terms"}
            try:
                state, disk = self._read_disk_state()
                if state == "invalid":
                    raise ValueError(
                        "live settings file is invalid; recovery must repair it"
                    )
                if state == "ok":
                    # The just-read disk snapshot wins by default. Preserve
                    # unrelated unsaved local dirty keys, but never carry a
                    # stale whole vocabulary collection into this transaction.
                    merged = self._merge_snapshot(
                        disk, original_data, original_dirty - collection_keys
                    )
                else:
                    merged = json.loads(json.dumps(original_data))

                raw_vocabulary = merged.get("vocabulary", {})
                if "vocabulary" in original_dirty:
                    raw_vocabulary = self._merge_mapping_delta(
                        raw_vocabulary,
                        self._base_data.get("vocabulary", {}),
                        original_data.get("vocabulary", {}),
                    )
                vocabulary = {
                    str(source): str(target)
                    for source, target in (
                        raw_vocabulary.items()
                        if isinstance(raw_vocabulary, dict)
                        else ()
                    )
                    if str(source).strip() and str(target).strip()
                }
                raw_terms = merged.get("vocabulary_terms", [])
                if "vocabulary_terms" in original_dirty:
                    raw_terms = self._merge_list_delta(
                        raw_terms,
                        self._base_data.get("vocabulary_terms", []),
                        original_data.get("vocabulary_terms", []),
                    )
                terms = [
                    str(term).strip()
                    for term in (raw_terms if isinstance(raw_terms, list) else [])
                    if str(term).strip()
                ]
                before_vocabulary = dict(vocabulary)
                before_terms = list(terms)

                result = mutator(vocabulary, terms)

                # Revalidate callback output at this trust boundary so malformed
                # values cannot make settings.json unserialisable.
                vocabulary = {
                    str(source): str(target)
                    for source, target in vocabulary.items()
                    if str(source).strip() and str(target).strip()
                }
                terms = [
                    str(term).strip() for term in terms if str(term).strip()
                ]
                merged["vocabulary"] = vocabulary
                merged["vocabulary_terms"] = terms
                self.data = merged

                needs_write = (
                    vocabulary != before_vocabulary
                    or terms != before_terms
                    or bool(original_dirty - collection_keys)
                    or not os.path.exists(branding.SETTINGS_PATH)
                )
                if needs_write:
                    if not self._write_atomic():
                        raise OSError("atomic vocabulary settings write failed")
                    self._dirty.difference_update(original_dirty)
                else:
                    # These keys now reflect the locked disk snapshot even if a
                    # prior failed save had left them marked dirty locally.
                    self._dirty.difference_update(collection_keys)
                return result
            except Exception:
                self.data = original_data
                self._dirty = original_dirty
                raise
            finally:
                _release_lock(fd)

    def reset_all(self):
        """Restore defaults and return whether they reached disk."""
        with self._save_lock:
            original_data = self.data
            original_dirty = set(self._dirty)
            disk_keys = set(self._read_disk())
            self.data = json.loads(json.dumps(DEFAULTS))
            # A factory reset intends to overwrite EVERY key, including unknown
            # forward-compatible keys.  Mark disk-only keys dirty as deletion
            # tombstones; otherwise the cross-process merge below resurrects
            # them immediately from the old file.
            self._dirty = set(self.data.keys()) | disk_keys
            saved = self._do_save()
            if not saved:
                self.data = original_data
                self._dirty = original_dirty
            return saved
