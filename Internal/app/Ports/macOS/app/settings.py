#!/usr/bin/env python3
"""Persisted user settings for Mumble (stored at %APPDATA%\\Mumble\\settings.json)."""

import json
import os
import shutil
import threading
import time

import branding


# --- Cross-process file lock (best-effort, portable) -----------------------
# The controller and the web window are SEPARATE processes that share one
# settings.json. A simple per-process threading.Lock cannot coordinate across
# processes, so two near-simultaneous saves could still interleave. This tiny
# advisory lock (an O_EXCL marker file with a stale-steal timeout) closes that
# window. It is portable (no msvcrt/fcntl) so the macOS/Linux ports reuse it
# verbatim. It is best-effort: if it can't be taken in time, the save proceeds
# anyway — the read-merge in _do_save is the real anti-clobber guarantee.
def _lock_path():
    return branding.SETTINGS_PATH + ".lock"


def _acquire_lock(timeout=3.0, stale=5.0):
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
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.remove(_lock_path())
    except OSError:
        pass

DEFAULTS = {
    # macOS uses Option for the global shortcuts.  Keep Deck on Option+H:
    # Option+D is already the record shortcut, so copying Windows' newer Deck
    # shortcut (Ctrl+Alt+D) would make two actions share one chord on a Mac.
    "hotkey": "ctrl+option+d",
    # Two SEPARATE history actions (owner v9): quick_paste_hotkey pastes the most
    # recent transcript straight into the focused field; history_hotkey opens the
    # History window. They used to be fused on Ctrl+Alt+V (paste + open), which
    # confused the two concepts — now each has its own bind.
    "quick_paste_hotkey": "ctrl+option+v",
    "history_hotkey": "ctrl+option+h",
    "search_hotkey": "ctrl+option+s",
    # Perplexity is the default (owner 2026-06-20): it opens with the question
    # pre-filled and the answer already generating — a better instant-search
    # result than a plain SERP. Google/Brave stay selectable in Settings.
    "search_engine": "perplexity",   # web-search target: google | perplexity | brave
    "browser": "default",            # search opens in: default | edge | chrome | brave | chromium
    # SMALL is the default (owner directive 2026-06-12): the most accurate of the
    # on-device models, and the cloud AI pass cleans up anything in the same call.
    # Tiny (fastest) and Base (balanced) stay selectable in Settings for slower PCs.
    "model": "small.en",
    "language": "en",
    # --- The Big Shift: hardware- & language-aware local model selection -------
    # Users never pick a model id. They tell us their PC class (onboarding) and
    # whether they only speak English; Mumble resolves the right faster-whisper
    # model. "auto" runs a one-time CPU/RAM probe on first run. The resolver
    # (branding.resolve_model) maps (tier x english_only) -> a concrete model id:
    #   weak+en->base.en  weak->base   mid+en->small.en  mid->small
    #   powerful+en->distil-large-v3  powerful->large-v3-turbo
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
    # error.
    "transcription_mode": "local",   # local | cloud
    # Local-only post-processing (privacy / offline). When ON, text shaping runs
    # on-device even if a cloud key is set — the local engine's dominance gate
    # (local_engine.route) keeps everything on the edge and appends a gentle
    # "enable Pro Mode for richer results" notice on tasks beyond the edge,
    # rather than blocking. OFF (default) = cloud is primary whenever a key is set.
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
    # chip on the island bar (off by default — it's an opt-in control). `foreign_mode`
    # is the live chip state: tapping it ON forces the on-device foreign-term booster
    # to run for the next dictation even for an English-primary user (multilingual
    # users get the booster automatically; see _process). Both default False.
    "island_foreign_toggle": True,
    "foreign_mode": False,
    "mic_device": None,
    "format_enabled": True,
    "ai_warmup": False,  # pre-warm AI on record-start — OFF: pointless on Cerebras + wastes a request
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
    # Personal vocabulary — two tiers, both edited in the same Settings card:
    # vocabulary_terms (DEFAULT workflow): just the correct words/names/jargon.
    #   They bias transcription (hotwords) and mis-heard variants are found
    #   automatically (phonetic + fuzzy match; high-confidence auto-fixed,
    #   medium-confidence offered to the AI as 'heard//Term').
    "vocabulary_terms": [],
    # vocabulary (ADVANCED workflow): explicit mis-hearing → correction pairs
    # for stubborn cases, e.g. {"mambo": "Mumble"}. Applied first, verbatim.
    "vocabulary": {},
    # How hard the plain-text polisher edits: Light = smallest fixes (keep exact words),
    # Standard = also smooth grammar/awkward phrasing, Thorough = full cleanup.
    "polish_aggressiveness": "Light",
    # Everyday Text uses deterministic local cleanup and pastes immediately.
    # Explicit Prompt mode keeps its provider-backed generation path.
    "instant_text": True,
    "user_name": "",
    "autostart": True,
    "first_run": True,
    "compute_type": "int8",
    "device": "auto",  # transcription device: auto (CUDA if present) | cpu | cuda —
                       # must be in DEFAULTS or load() strips a saved override
    "cpu_threads": 0,  # 0 = auto: every core except two (CTranslate2's own default
                       # is a flat 4 threads, which wastes most of a modern CPU)
    "min_seconds": 0.3,
    "history_max": 5000,
    "clipboard_enabled": True,
    "clipboard_max": 5000,
    "pro_mode": True,
    # LLM engine: Cerebras is the primary; OpenRouter is the curated alternative.
    "llm_provider": "cerebras",  # cerebras | openrouter
    # Cerebras (recommended)
    "cerebras_api_key": "",
    "cerebras_model": "gpt-oss-120b",
    # Groq/OpenAI keys are retained only for opt-in cloud transcription.
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
    "local_provider_retired_applied": False,
    "search_perplexity_default_applied": False,
    "big_shift_applied": False,
    # One-time repair for builds whose shared settings file accidentally carried
    # the Windows Ctrl+Win / Ctrl+Alt defaults into the macOS distribution.
    "mac_hotkeys_v1_applied": False,
    # Cloud sync (Supabase) — credentials entered by the owner
    "supabase_url": "",
    "supabase_anon_key": "",
    # Per-data-type sync toggles. Master toggle: sync_enabled.
    # When off, no data of that type is pushed or pulled.
    "sync_enabled": True,
    "sync_settings": True,
    "sync_stats": True,
    "sync_history": True,
    "sync_reader": False,
    "sync_favorites": True,
    "sync_presets": True,
    "sync_last_run": {},   # per-data-type last-sync timestamps
}

# Removed product concepts. Marking these keys dirty during migration prevents
# the cross-process read/merge saver from reintroducing them from settings.json.
REMOVED_SETTINGS = {
    "hardware_tier",
    "hardware_tier_resolved",
    "prompt_memory",
    "prompt_provider",
    "prompt_provider_cerebras_openrouter_applied",
    "mode_key",
    "mode_button_enabled",
    "mode_key_v2_applied",
    "prompt_keywords",
    "deepseek_api_key",
    "deepseek_model",
    "groq_model",
    "openai_model",
    "anthropic_api_key",
    "anthropic_model",
}


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
        # Keys THIS process has explicitly changed since the last flush. On save
        # we re-read the on-disk snapshot and overwrite ONLY these keys, so a
        # stale in-memory value (e.g. a blank API key the controller never saw)
        # can never clobber another process's edit. See _do_save.
        self._dirty = set()
        self.load()
        self._migrate()

    def _migrate(self):
        # Snapshot the freshly-loaded state so we can mark exactly the keys this
        # migration changes as dirty (they must win over the on-disk snapshot).
        before = json.loads(json.dumps(self.data))
        changed = False
        removed = {key for key in REMOVED_SETTINGS if key in self.data}
        for key in removed:
            self.data.pop(key, None)
        if removed:
            self._dirty.update(removed)
            changed = True
        # Processing has one provider for every mode. Heal retired selections.
        if self.data.get("llm_provider") not in ("cerebras", "openrouter"):
            self.data["llm_provider"] = "cerebras"
            changed = True
        # Pro Mode shipped OFF in 1.5 and ON by default from 1.6 (built-in key).
        if not self.data.get("pro_default_applied"):
            self.data["pro_mode"] = True
            self.data["pro_default_applied"] = True
            changed = True
        # 1.7: bump transcript + clipboard history to 100 for existing installs.
        if not self.data.get("limits_100_applied"):
            self.data["clipboard_max"] = 100
            self.data["history_max"] = 100
            self.data["limits_100_applied"] = True
            changed = True
        # 2.0: massive history expansion (100 -> 5000) because text is so lightweight.
        if not self.data.get("limits_5000_applied"):
            self.data["clipboard_max"] = 5000
            self.data["history_max"] = 5000
            self.data["limits_5000_applied"] = True
            changed = True
        # Default model is SMALL — the most accurate on-device model; the cloud AI
        # pass cleans up in the same call (owner directive 2026-06-12). The OLD
        # migration that forced small/tiny DOWN to base is retired; this one bumps
        # the previous base.en default UP to small.en once. An explicit tiny.en
        # (deliberate "fastest") is left untouched.
        if not self.data.get("model_small_default_applied"):
            if self.data.get("model") == "base.en":
                self.data["model"] = "small.en"
            self.data["model_small_default_applied"] = True
            self.data["model_small_applied"] = True  # keep the old guard set
            changed = True
        # Enhanced is the default visual tier now (owner directive 2026-06-13):
        # bump the previous "standard" default UP to "enhanced" once. A deliberate
        # "basic"/"lite" choice (low-end hardware) is left untouched.
        if not self.data.get("enhanced_default_applied"):
            if self.data.get("ui_effects") == "standard":
                self.data["ui_effects"] = "enhanced"
            self.data["enhanced_default_applied"] = True
            changed = True
        # v9: Local LLM support was removed (it could not be configured by a
        # non-technical user and implied functionality that wasn't production
        # ready — owner directive). Anyone whose provider/prompting was set to
        # "local" is moved back to a working default so the app keeps generating.
        if not self.data.get("local_provider_retired_applied"):
            if self.data.get("llm_provider") == "local":
                self.data["llm_provider"] = "cerebras"
            self.data["local_provider_retired_applied"] = True
            changed = True
        # 2026-06-20: Perplexity is the default search engine (owner) — it opens
        # with the question pre-filled and answering. Existing installs were all
        # on the OLD "google" default, so flip google -> perplexity once. A
        # deliberate "brave"/already-"perplexity" choice is left untouched.
        if not self.data.get("search_perplexity_default_applied"):
            if self.data.get("search_engine") == "google":
                self.data["search_engine"] = "perplexity"
            self.data["search_perplexity_default_applied"] = True
            changed = True
        # Historic per-mode provider and Prompt Memory settings are removed
        # below. The one current text-provider selector accepts only Cerebras
        # and OpenRouter.
        # 2026-06-28: the OpenRouter STT default and the Reader TTS default both
        # shipped DEAD model ids that don't resolve on OpenRouter — cloud STT then
        # 400s every utterance (silently failing over to local) and the Reader's
        # saved voice model is stale. Heal an install still parked on a known-bad
        # id by moving it to the working catalogue default; a user who picked a
        # different model in Settings is left untouched (only the exact dead id
        # is rewritten).
        if not self.data.get("stt_tts_dead_default_healed"):
            if self.data.get("openrouter_transcription_model") == "openai/gpt-4o-mini-transcribe":
                self.data["openrouter_transcription_model"] = "groq/whisper-large-v3-turbo"
            if self.data.get("reader_tts_model") == "openai/gpt-4o-mini-tts":
                self.data["reader_tts_model"] = "google/gemini-3.1-flash-tts-preview"
            self.data["stt_tts_dead_default_healed"] = True
            changed = True
        # Repair the legacy OpenRouter/Gemini default that paired a Gemini TTS
        # model with an OpenAI-only voice name.
        if not self.data.get("reader_tts_voice_contract_applied"):
            if (self.data.get("reader_tts_provider") == "openrouter"
                    and self.data.get("reader_tts_model") ==
                    "google/gemini-3.1-flash-tts-preview"
                    and self.data.get("reader_voice") == "onyx"):
                self.data["reader_voice"] = "Fenrir"
            self.data["reader_tts_voice_contract_applied"] = True
            changed = True
        # THE BIG SHIFT (v0.9 "Big Shift"): infer english_only from the model the
        # user is ALREADY on, so an upgrade never changes their transcription.
        # A saved multilingual model (no ".en" suffix, e.g. "small"/"large-v3")
        # means they want languages → english_only=False; an ".en" model keeps
        # the lighter English path. We do NOT touch their model id on upgrade
        # (hardware-tier resolution only runs at first-run onboarding or when the
        # user explicitly changes the tier/language in Settings).
        if not self.data.get("big_shift_applied"):
            m = str(self.data.get("model", "small.en")).lower()
            self.data["english_only"] = m.endswith(".en")
            self.data["big_shift_applied"] = True
            changed = True
        # macOS parity repair: translate only known historical platform defaults.
        # Custom shortcuts are deliberately left untouched.  In particular the
        # Deck remains Option+H because Option+D is the activation shortcut.
        if not self.data.get("mac_hotkeys_v1_applied"):
            replacements = {
                "hotkey": {
                    "ctrl+windows": "ctrl+option+d",
                    "ctrl+win": "ctrl+option+d",
                },
                "quick_paste_hotkey": {
                    "ctrl+alt+v": "ctrl+option+v",
                },
                "history_hotkey": {
                    "ctrl+alt+d": "ctrl+option+h",
                    "ctrl+alt+h": "ctrl+option+h",
                },
                "search_hotkey": {
                    "ctrl+alt+s": "ctrl+option+s",
                },
            }
            for key, mapping in replacements.items():
                current = str(self.data.get(key, "") or "").strip().lower()
                if current in mapping:
                    self.data[key] = mapping[current]
            self.data["mac_hotkeys_v1_applied"] = True
            changed = True
        if changed:
            self._dirty.update(
                k for k in self.data if self.data.get(k) != before.get(k)
            )
            self.save()

    def load(self):
        """Loads settings from disk, applying defaults for missing keys."""
        try:
            with open(branding.SETTINGS_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
        except FileNotFoundError:
            return  # first run — expected, use defaults
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
            except Exception:
                pass
            # Attempt recovery from the last-good backup (.bak).
            recovered = None
            try:
                if os.path.exists(bak):
                    with open(bak, "r", encoding="utf-8") as fb:
                        recovered = json.load(fb)
            except Exception as e2:
                print(f"[settings] backup recovery also failed "
                      f"({type(e2).__name__}): {e2}")
                recovered = None
            if isinstance(recovered, dict):
                print("[settings] recovered from .bak — corrupted file backed "
                      "up to settings.json.corrupt. Using last-good values.")
                d = recovered
                # Heal the live file immediately.  Merely loading the backup into
                # memory leaves the corrupt primary in place when no migration or
                # user edit happens during this run, so every subsequent launch
                # repeats recovery forever.  Keep .bak untouched and atomically
                # replace only the already-preserved corrupt primary.
                restore_tmp = sp + ".recover.tmp"
                try:
                    with open(restore_tmp, "w", encoding="utf-8") as restored:
                        json.dump(recovered, restored, ensure_ascii=False, indent=2)
                        restored.flush()
                        os.fsync(restored.fileno())
                    os.replace(restore_tmp, sp)
                    branding.protect_private_path(sp)
                except Exception as restore_error:
                    print(f"[settings] live-file restore failed "
                          f"({type(restore_error).__name__}): {restore_error}")
                    try:
                        if os.path.exists(restore_tmp):
                            os.remove(restore_tmp)
                    except OSError:
                        pass
            else:
                # No usable backup — rename the corrupted file to settings.json.bak
                # (clearing the live path for a fresh write) and fall back to the
                # safe defaults already in self.data. settings.json.corrupt above
                # preserves the bytes for inspection.
                try:
                    if os.path.exists(sp):
                        os.replace(sp, bak)
                except Exception:
                    pass
                print("[settings] corrupted file backed up to settings.json.bak "
                      "and settings.json.corrupt — loading safe defaults.")
                return
        if not isinstance(d, dict):
            return
        for k, v in d.items():
            if k == "modes" and isinstance(v, dict):
                m = dict(DEFAULTS["modes"])
                m.update({kk: _coerce_bool(vv) for kk, vv in v.items() if kk in m})
                self.data["modes"] = m
            elif k == "prompt_prefs" and isinstance(v, dict):
                # Merge over defaults so a saved file missing a newer pref key still
                # gets a sensible value (never a KeyError downstream).
                p = dict(DEFAULTS["prompt_prefs"])
                p.update({kk: vv for kk, vv in v.items() if kk in p})
                self.data["prompt_prefs"] = p
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
                # bool is a subclass of int in Python. Treat it as the wrong
                # type for numeric settings instead of accepting it as 0 or 1.
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

    def _read_disk(self):
        """Return the current on-disk settings dict, or {} if absent/unreadable."""
        try:
            with open(branding.SETTINGS_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
            return {}

    def save(self):
        """Thread-safe save of current settings to disk."""
        with self._save_lock:
            self._do_save()

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
        dirty = set(self._dirty)
        saved = False
        try:
            disk = self._read_disk()
            if disk:
                out = dict(self.data)            # full snapshot (defaults + ours)
                for k, v in disk.items():
                    if k not in self._dirty:
                        out[k] = v               # disk wins for keys we didn't touch
                self.data = out                  # converge memory with the merge
            saved = self._write_atomic()
            if saved:
                self._dirty.difference_update(dirty)
        finally:
            _release_lock(fd)
        return saved

    def _write_atomic(self):
        tmp = branding.SETTINGS_PATH + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, branding.SETTINGS_PATH)
            branding.protect_private_path(branding.SETTINGS_PATH)
            # Keep a backup so a crash mid-write doesn't lose everything.
            # NB: COPY, never move — os.replace(SETTINGS_PATH, .bak) would rename
            # the live settings file away, leaving no settings.json at all and
            # silently resetting every setting (key, model, modes) on next launch.
            try:
                shutil.copyfile(branding.SETTINGS_PATH, branding.SETTINGS_PATH + ".bak")
            except OSError:
                pass
            return True
        except OSError as e:
            print(f"[settings] save error ({type(e).__name__}): {e}")
            # Clean up stale .tmp
            try:
                os.remove(tmp)
            except OSError:
                pass
            return False
        except Exception as e:
            print(f"[settings] save error ({type(e).__name__}): {e}")
            # Clean up stale .tmp on non-IO errors too (e.g. JSON encode failure)
            try:
                os.remove(tmp)
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
            self.data[key] = value
            self._dirty.add(key)
            return self._do_save()

    def update(self, **kw):
        """Bulk update settings and return whether they reached disk."""
        with self._save_lock:
            self.data.update(kw)
            self._dirty.update(kw.keys())
            return self._do_save()

    def atomic_vocabulary_update(self, mutator):
        """Atomically mutate the latest persisted vocabulary collections."""
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
                disk = self._read_disk()
                merged = dict(self.data)
                if disk:
                    merged.update(disk)
                for key in original_dirty - collection_keys:
                    if key in original_data:
                        merged[key] = original_data[key]
                    else:
                        merged.pop(key, None)

                raw_vocabulary = merged.get("vocabulary", {})
                vocabulary = {
                    str(source): str(target)
                    for source, target in (
                        raw_vocabulary.items()
                        if isinstance(raw_vocabulary, dict) else ())
                    if str(source).strip() and str(target).strip()
                }
                raw_terms = merged.get("vocabulary_terms", [])
                terms = [
                    str(term).strip()
                    for term in (raw_terms if isinstance(raw_terms, list) else [])
                    if str(term).strip()
                ]
                before_vocabulary = dict(vocabulary)
                before_terms = list(terms)

                result = mutator(vocabulary, terms)

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
            disk_keys = set(self._read_disk())
            self.data = json.loads(json.dumps(DEFAULTS))
            # Include disk-only keys as deletion tombstones, otherwise the
            # cross-process merge below immediately resurrects them.
            self._dirty = set(self.data.keys()) | disk_keys
            return self._do_save()
