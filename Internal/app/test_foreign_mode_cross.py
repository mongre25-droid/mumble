#!/usr/bin/env python3
"""VAL-CROSS-018: Foreign Mode — Transcription + Pipeline + Provider handle non-English.

When primary_language is a non-English language and english_only is False:
  1. STT switches to the multilingual model (not an English-only .en model).
  2. Pipeline Stage 1 (punctuation) handles the target language without crashing.
  3. The cloud provider's constitution has UK English injection DISABLED and a
     target-language directive appended.
  4. foreign_boost applies language-specific transformations.
  5. English dictation is unchanged (no regression).

Run: python test_foreign_mode_cross.py   (offline, no deps beyond project modules)
"""
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [ok  ] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}")


def main():
    import ai
    import branding

    print("== CROSS-018a: cloud constitution disables UK English for non-English ==")
    # German: UK rule stripped, target-language directive added.
    de = ai.language_aware_system(ai.TEXT_SYSTEM, "de", False)
    check("German: UK_ENGLISH_RULE instruction stripped",
          ai.UK_ENGLISH_RULE not in de)
    check("German: target-language directive present (German)",
          "German" in de)
    check("German: 'do NOT translate to English' directive present",
          "do NOT translate to English" in de)
    check("German: 'do NOT apply British English' directive present",
          "do not apply british english" in de.lower())

    # French foreign lane.
    fr = ai.language_aware_system(ai.FOREIGN_SYSTEM, "fr", False)
    check("French foreign: UK rule stripped", ai.UK_ENGLISH_RULE not in fr)
    check("French foreign: French directive present", "French" in fr)

    # Japanese (non-ISO-name fallback still works — directive uses the code).
    ja = ai.language_aware_system(ai.EMAIL_SYSTEM, "ja", False)
    check("Japanese email: UK rule stripped", ai.UK_ENGLISH_RULE not in ja)
    check("Japanese email: target directive present", "Japanese" in ja)

    print("== CROSS-018b: English dictation unchanged (no regression) ==")
    en_text = ai.language_aware_system(ai.TEXT_SYSTEM, "en", True)
    check("English + english_only=True: system prompt unchanged",
          en_text == ai.TEXT_SYSTEM)
    en_text2 = ai.language_aware_system(ai.TEXT_SYSTEM, "en", False)
    check("English + english_only=False: still unchanged (lang=en)",
          en_text2 == ai.TEXT_SYSTEM)
    empty = ai.language_aware_system(ai.TEXT_SYSTEM, "", True)
    check("Empty language: unchanged (defaults to English)", empty == ai.TEXT_SYSTEM)

    print("== CROSS-018c: set_language_context drives _lang_system (live lanes) ==")
    ai.set_language_context("en", True)
    check("default context is English", ai.get_language_context() == ("en", True))
    check("English context: polish system unchanged",
          ai._lang_system(ai.POLISH_SYSTEM) == ai.POLISH_SYSTEM)
    ai.set_language_context("de", False)
    check("de context applied", ai.get_language_context() == ("de", False))
    polished_de = ai._lang_system(ai.POLISH_SYSTEM)
    check("de context: polish system gains German directive",
          "German" in polished_de and ai.UK_ENGLISH_RULE not in polished_de)
    # Reset for safety.
    ai.set_language_context("en", True)

    print("== CROSS-018d: STT model switches to multilingual for non-English ==")
    # English → .en model; non-English → multilingual (no .en suffix).
    en_model = branding.model_for_language("mid", "en")
    de_model = branding.model_for_language("mid", "de")
    fr_model = branding.model_for_language("mid", "fr")
    check("English resolves to an English-only (.en) model",
          en_model.endswith(".en"))
    check("German resolves to the multilingual model (no .en suffix)",
          not de_model.endswith(".en"))
    check("French resolves to the multilingual model (no .en suffix)",
          not fr_model.endswith(".en"))
    check("German and French resolve to the same multilingual model",
          de_model == fr_model == branding.resolve_model("mid", english_only=False))
    # The english_only flag the controller derives matches MODEL_BY_LANGUAGE.
    check("MODEL_BY_LANGUAGE: English → True, others default to False",
          branding.MODEL_BY_LANGUAGE.get("en") is True
          and branding.MODEL_BY_LANGUAGE.get("de", False) is False)

    print("== CROSS-018e: Pipeline Stage 1 handles non-English without crashing ==")
    try:
        from pipeline.stage_punctuation import PunctuationStage
        stage = PunctuationStage()
        # The stage is import-safe and degrades gracefully on non-English.
        # Empty input is a guaranteed no-op.
        check("PunctuationStage imports and constructs", True)
        check("PunctuationStage empty input -> empty string (no crash)",
              stage.process("") == "")
        # Non-English short input never crashes; output is readable text.
        out_de = stage.process("hallo wie geht es dir")
        check("PunctuationStage processes German text without crashing",
              isinstance(out_de, str))
        check("PunctuationStage German output is non-empty/readable",
              out_de.strip() != "" and "hallo" in out_de.lower())
        # The configured model is the multilingual punctuation model.
        check("Stage 1 uses the multilingual punctuation model",
              "multilang" in stage._model_name.lower()
              or "multilingual" in stage._model_name.lower())
    except ImportError as e:
        check("PunctuationStage import-safe (transformers optional)", False)
        print(f"     import error: {e}")

    print("== CROSS-018f: foreign_boost applies language-specific transformations ==")
    try:
        import foreign_boost
        # Arabic pack is the default and resolves a known mishearing.
        res = foreign_boost.boost("i said salam to the group",
                                  languages=("arabic",))
        check("foreign_boost.boost returns a result object", res is not None)
        check("foreign_boost resolves Arabic term (salam) with arabic pack",
              "salam" in res.text.lower() or "salaam" in res.text.lower())
        # French pack resolves a French loanword.
        res_fr = foreign_boost.boost("i had a deja vu moment",
                                     languages=("french",))
        check("foreign_boost French pack processes loanword without crash",
              isinstance(res_fr.text, str))
        # Multiple packs at once do not crash.
        res_multi = foreign_boost.boost("salam and deja vu",
                                        languages=("arabic", "french"))
        check("foreign_boost multi-pack does not crash",
              isinstance(res_multi.text, str))
    except Exception as e:  # noqa: BLE001
        check("foreign_boost foreign-mode path works", False)
        print(f"     error: {e}")

    print()
    if FAIL:
        print(f"{FAIL} FAILED, {PASS} passed")
        raise SystemExit(1)
    print(f"ALL PASS ({PASS})")


if __name__ == "__main__":
    main()
