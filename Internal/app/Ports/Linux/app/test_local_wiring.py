#!/usr/bin/env python3
"""Local post-processing engine wiring (P2) — the integration contract.

Covers the pieces that join the (already unit-tested) local_engine /
foreign_boost / model_free modules to the app:
  • branding.model_for_language — primary-language → on-device model swap,
  • the new settings keys (local_only_mode, primary_language),
  • the cloud-dominance decision _process now makes via local_engine.route
    (cloud-primary-when-key, local-only forces local, no key → local).

Offline, no audio/network. Run:  .venv\\Scripts\\python.exe test_local_wiring.py
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import branding  # noqa: E402
import local_engine as le  # noqa: E402
import settings as settings_mod  # noqa: E402

_fails = []


def check(desc, cond):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {desc}")
    if not cond:
        _fails.append(desc)


# ---- branding.model_for_language: the primary-language model swap ---------
print("== model_for_language: English → .en, any other language → multilingual ==")
check("mid + en  → small.en", branding.model_for_language("mid", "en") == "small.en")
check("mid + es  → small (multilingual)", branding.model_for_language("mid", "es") == "small")
check("weak + en → base.en", branding.model_for_language("weak", "en") == "base.en")
check("weak + ar → base (multilingual)", branding.model_for_language("weak", "ar") == "base")
check("powerful + fr → large-v3-turbo",
      branding.model_for_language("powerful", "fr") == "large-v3-turbo")
check("powerful + en → distil-large-v3",
      branding.model_for_language("powerful", "en") == "distil-large-v3")
# Blank/None defaults to English here; the "other language" onboarding choice
# (primary="") is instead routed through resolve_model(english_only=False) by
# _process's `if primary_lang:` guard, so model_for_language never sees blank live.
check("blank language → English default (mid → small.en)",
      branding.model_for_language("mid", "") == "small.en")
check("MODEL_BY_LANGUAGE maps en→English-only", branding.MODEL_BY_LANGUAGE.get("en") is True)

# ---- new settings keys ----------------------------------------------------
print("\n== new settings keys exist with safe defaults ==")
d = settings_mod.DEFAULTS
check("local_only_mode default False", d.get("local_only_mode") is False)
check("primary_language default 'en'", d.get("primary_language") == "en")
check("local_llm_enabled technical preview defaults off",
      d.get("local_llm_enabled") is False)
check("local_llm_model default ''", d.get("local_llm_model") == "")

# ---- on-device LLM backend wiring (discovery → backend → route) ------------
print("\n== local LLM backend wiring ==")
check("branding exposes MODELS_DIR", isinstance(getattr(branding, "MODELS_DIR", None), str))
check("branding.llama_cli_path callable", callable(getattr(branding, "llama_cli_path", None)))
# With a ready (faked) backend installed, the router lifts smart lanes to LOCAL_LLM.


class _Ready(le.LocalLLMBackend):
    name = "fake"

    def available(self):
        return True

    def generate(self, system, user, grammar=None, max_tokens=512, temperature=0.3):
        return "ok"


le.set_backend(_Ready(), "fake-ready")
check("local_llm_ready True with a ready backend", le.local_llm_ready() is True)
check("no key + prompt + model ready → LOCAL_LLM",
      le.route("prompt", cloud_key_present=False,
               local_llm_ready=le.local_llm_ready()).engine == le.LOCAL_LLM)
check("backend_status reports ready", le.backend_status().get("ready") is True)
le.set_backend(None, "restored")  # restore Null for any later checks

# ---- the cloud-dominance decision _process now makes ----------------------
print("\n== local_engine.route: the will_cloud decision _process uses ==")
# Key present, local-only off → CLOUD (the old will_cloud == True case).
check("key + not local-only → cloud",
      le.route("text", cloud_key_present=True, local_only_mode=False).cloud_augmented is True)
check("key + not local-only (prompt) → cloud",
      le.route("prompt", cloud_key_present=True, local_only_mode=False).cloud_augmented is True)
# local_only toggle forces local even WITH a key (the new privacy path).
check("key + local-only ON → local",
      le.route("text", cloud_key_present=True, local_only_mode=True).cloud_augmented is False)
# No key → local (best-effort), never blocks.
check("no key → local",
      le.route("text", cloud_key_present=False).cloud_augmented is False)
# A smart lane with no key and no local LLM degrades to model-free (formatting only).
r = le.route("prompt", cloud_key_present=False, local_llm_ready=False)
check("no key + prompt → LOCAL engine", r.engine == le.LOCAL)
check("no key + prompt → model_free (no semantics)", r.model_free is True)

# ===================================================================== final
if _fails:
    print(f"\n{len(_fails)} FAILED: {_fails}")
    sys.exit(1)
print("\nALL GREEN")
sys.exit(0)
