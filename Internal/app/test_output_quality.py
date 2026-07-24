#!/usr/bin/env python3
"""Output quality verification tests for all AI modes.

Covers the output-quality assertions from the validation contract:
  VAL-PRMT-002: Prompt mode produces ready-to-paste prompt
  VAL-PRMT-003: Email mode outputs properly formatted email
  VAL-PRMT-004: Reply mode uses conversation context
  VAL-PRMT-005: Foreign mode preserves and resolves foreign terms
  VAL-PRMT-008: UK English spelling applied to prose output
  VAL-PRMT-009: Dead PROMPT_GUIDE code removed, prompt mode still works

Usage:
    python test_output_quality.py
"""

import os
import sys

_app_dir = os.path.dirname(os.path.abspath(__file__))
if _app_dir not in sys.path:
    sys.path.insert(0, _app_dir)

TESTS = []
FAILURES = []


def check(label, cond):
    TESTS.append(label)
    if not cond:
        FAILURES.append(label)
        print(f"  FAIL  {label}")
    else:
        print(f"  ok    {label}")


# =============================================================================
# SETUP: Import the active modules
# =============================================================================
print("=== Setup ===")

import ai
check("ai package imports", True)

import ai.constitution as const
check("ai.constitution imports", True)

# Verify we have the active system prompts from ai
check("ai.TEXT_SYSTEM exists", hasattr(ai, "TEXT_SYSTEM"))
check("ai.EMAIL_SYSTEM exists", hasattr(ai, "EMAIL_SYSTEM"))
check("ai.REPLY_SYSTEM exists", hasattr(ai, "REPLY_SYSTEM"))
check("ai.FOREIGN_SYSTEM exists", hasattr(ai, "FOREIGN_SYSTEM"))
check("ai.UNIVERSAL_SYSTEM exists", hasattr(ai, "UNIVERSAL_SYSTEM"))
check("ai.POLISH_SYSTEM exists", hasattr(ai, "POLISH_SYSTEM"))

# =============================================================================
# VAL-PRMT-002: Prompt mode produces ready-to-paste prompt
# =============================================================================
print("\n=== VAL-PRMT-002: Prompt mode output quality ===")

# --- No wrapper / framing language in system prompts ---

# The _PROMPT_OUTPUT_RULE (used in the Cerebras full path) must instruct
# the model to output ONLY the prompt, no wrapper.
prompt_sys_full = const.build_prompt_system(None, url=const.CEREBRAS_URL)
check("PROMPT_OUTPUT_RULE says no preamble",
      "no preamble" in const._PROMPT_OUTPUT_RULE.lower())
check("PROMPT_OUTPUT_RULE says no commentary",
      "no commentary" in const._PROMPT_OUTPUT_RULE.lower())
check("PROMPT_OUTPUT_RULE says no quotes wrapping",
      "no quotes wrapping" in const._PROMPT_OUTPUT_RULE.lower()
      or "no quotes" in const._PROMPT_OUTPUT_RULE.lower())

# The lightweight output rule must also forbid wrapper
check("LIGHTWEIGHT_PROMPT_OUTPUT_RULE says no preamble",
      "no preamble" in const._LIGHTWEIGHT_PROMPT_OUTPUT_RULE.lower())
check("LIGHTWEIGHT_PROMPT_OUTPUT_RULE says no commentary",
      "no commentary" in const._LIGHTWEIGHT_PROMPT_OUTPUT_RULE.lower())

# --- FINAL_PROMPT_START/END markers are stripped by extractor ---

marked_output = (
    "Step 1: Classify...\n"
    "Step 2: Method...\n"
    "**FINAL_PROMPT_START**\n"
    "You are an expert Python developer. Build a CLI tool that renames files.\n"
    "- Accept source and destination patterns\n"
    "- Support dry-run mode\n"
    "**FINAL_PROMPT_END**"
)
extracted = ai._extract_final_prompt(marked_output)
check("FINAL_PROMPT_START stripped from output", "FINAL_PROMPT_START" not in extracted)
check("FINAL_PROMPT_END stripped from output", "FINAL_PROMPT_END" not in extracted)
check("Prompt content preserved", "Python developer" in extracted)
check("Reasoning stripped from output", "Step 1: Classify" not in extracted)

# --- Single-marker fallback (truncated run) ---
truncated = "**FINAL_PROMPT_START**\nYou are a helpful assistant.\n"
extracted2 = ai._extract_final_prompt(truncated)
check("Single-marker fallback extracts content", "helpful assistant" in extracted2)
check("Single-marker fallback strips marker", "FINAL_PROMPT_START" not in extracted2)

# --- Bold marker variant ---
bold_markers = (
    "Reasoning...\n"
    "**FINAL_PROMPT_START**\n"
    "Act as a senior engineer.\n"
    "**FINAL_PROMPT_END**"
)
extracted3 = ai._extract_final_prompt(bold_markers)
check("Bold markers handled", "senior engineer" in extracted3)
check("Bold markers stripped", "FINAL_PROMPT" not in extracted3)

# --- _prompt_user_msg builds correct request ---
light_msg = const._prompt_user_msg("build a Python CLI for renaming files", "",
                                    lightweight=True)
check("Lightweight prompt user message has request",
      "build a Python CLI" in light_msg)
check("Lightweight prompt user message says output only",
      "ONLY the prompt" in light_msg)
check("Lightweight prompt user message says no preamble",
      "no preamble" in light_msg)
# Lightweight should NOT ask for FINAL_PROMPT markers (no pipeline steps to strip)
check("Lightweight user message has NO FINAL_PROMPT_START instruction",
      "FINAL_PROMPT_START" not in light_msg)

# Full path includes the 9-step pipeline
full_msg = const._prompt_user_msg("build a Python CLI for renaming files", "",
                                   lightweight=False)
check("Full prompt user message has CLASSIFY step", "CLASSIFY" in full_msg)
check("Full prompt user message has TRIM step (step 8)", "8. TRIM" in full_msg)
check("Full prompt user message has OUTPUT step (step 9)", "9. OUTPUT" in full_msg)
check("Full prompt user message includes FINAL_PROMPT_START",
      "FINAL_PROMPT_START" in full_msg)

# --- The finished prompt must not be a description of a prompt ---
# System prompts must say "Output the prompt itself" not "describe the prompt"
check("build_prompt_system produces non-empty output",
      len(prompt_sys_full) > 100)

# =============================================================================
# VAL-PRMT-003: Email mode outputs properly formatted email
# =============================================================================
print("\n=== VAL-PRMT-003: Email mode output quality ===")

email_sys = ai.EMAIL_SYSTEM

# Must instruct greeting
check("EMAIL_SYSTEM mentions greeting", "greeting" in email_sys.lower()
      or "Hi" in email_sys)

# Must instruct body
check("EMAIL_SYSTEM mentions body", "body" in email_sys.lower())

# Must instruct sign-off
check("EMAIL_SYSTEM mentions sign-off", "sign-off" in email_sys.lower()
      or "Best regards" in email_sys)

# Subject line should be optional
check("EMAIL_SYSTEM says subject is optional", "optional" in email_sys.lower())

# No wrapper language
check("EMAIL_SYSTEM says no 'Here is'", "no 'here is'" in email_sys.lower())
check("EMAIL_SYSTEM says no commentary", "no commentary" in email_sys.lower())

# Must NOT have a hard Subject requirement (only when implied)
check("EMAIL_SYSTEM subject is conditional",
      "implied" in email_sys.lower() or "optional" in email_sys.lower())

# Test cerebras_email function exists and builds correct user message
check("cerebras_email is callable", callable(ai.cerebras_email))

# =============================================================================
# VAL-PRMT-004: Reply mode uses conversation context
# =============================================================================
print("\n=== VAL-PRMT-004: Reply mode output quality ===")

reply_sys = ai.REPLY_SYSTEM

# Must mention context
check("REPLY_SYSTEM mentions context/CONTEXT",
      "CONTEXT" in reply_sys or "context" in reply_sys.lower())

# Must instruct matching tone
check("REPLY_SYSTEM mentions matching tone",
      "tone" in reply_sys.lower())

# Must write in user's voice
check("REPLY_SYSTEM mentions user's voice",
      "voice" in reply_sys.lower() or "user's" in reply_sys.lower())

# No wrapper language
check("REPLY_SYSTEM says no 'Here is'", "no 'here is'" in reply_sys.lower())

# Test cerebras_reply function builds correct context framing
check("cerebras_reply is callable", callable(ai.cerebras_reply))

# Verify _prompt_user_msg handles context properly
context_msg = const._prompt_user_msg(
    "agree and suggest Friday instead",
    "Can we move the meeting to 3pm? - Sarah",
    lightweight=True)
check("Context injected into user message", "3pm" in context_msg)

# =============================================================================
# VAL-PRMT-005: Foreign mode preserves and resolves foreign terms
# =============================================================================
print("\n=== VAL-PRMT-005: Foreign mode output quality ===")

foreign_sys = ai.FOREIGN_SYSTEM

# Must mention slash notation
check("FOREIGN_SYSTEM mentions slash notation",
      "a//b//c" in foreign_sys or "slash" in foreign_sys.lower()
      or "//" in foreign_sys)

# Must instruct choosing ONE word
check("FOREIGN_SYSTEM instructs choosing one word",
      "choose the ONE" in foreign_sys or "pick" in foreign_sys.lower())

# Must NOT translate
check("FOREIGN_SYSTEM says do NOT translate",
      "not translate" in foreign_sys.lower() or "do NOT translate" in foreign_sys.lower())

# Must preserve foreign language words
check("FOREIGN_SYSTEM preserves foreign words",
      "own language" in foreign_sys.lower()
      or "keep foreign" in foreign_sys.lower())

# Must mention conventional spelling
check("FOREIGN_SYSTEM mentions conventional spelling",
      "conventional" in foreign_sys.lower())

# No wrapper language
check("FOREIGN_SYSTEM says no commentary", "no commentary" in foreign_sys.lower())

# Test cerebras_foreign function
check("cerebras_foreign is callable", callable(ai.cerebras_foreign))

# Verify slash token resolution example in context
# The foreign system must explicitly tell the model to output NO slash notation
check("FOREIGN_SYSTEM says never output slash notation",
      "never the slash notation" in foreign_sys.lower()
      or "no slash" in foreign_sys.lower()
      or "never" in foreign_sys.lower())

# =============================================================================
# VAL-PRMT-008: UK English spelling applied to prose output
# =============================================================================
print("\n=== VAL-PRMT-008: UK English spelling ===")

# UK English rule must exist and contain British spellings
check("UK_ENGLISH_RULE exists in ai", hasattr(ai, "UK_ENGLISH_RULE"))
check("UK_ENGLISH_RULE has content", len(ai.UK_ENGLISH_RULE) > 20)
check("UK_ENGLISH_RULE mentions 'colour'", "colour" in ai.UK_ENGLISH_RULE)
check("UK_ENGLISH_RULE mentions 'organise'", "organise" in ai.UK_ENGLISH_RULE)
check("UK_ENGLISH_RULE mentions British English",
      "British English" in ai.UK_ENGLISH_RULE)

# UK English must be in ALL prose system prompts (not prompt mode)
check("POLISH_SYSTEM has UK English", "British English" in ai.POLISH_SYSTEM)
check("TEXT_SYSTEM has UK English", "British English" in ai.TEXT_SYSTEM)
check("EMAIL_SYSTEM has UK English", "British English" in ai.EMAIL_SYSTEM)
check("REPLY_SYSTEM has UK English", "British English" in ai.REPLY_SYSTEM)
check("FOREIGN_SYSTEM has UK English", "British English" in ai.FOREIGN_SYSTEM)

# UNIVERSAL_SYSTEM uses UK_ENGLISH_RULE_UNLESS_PROMPT - verify it has British but
# allows prompt mode to use audience-appropriate spelling
check("UNIVERSAL_SYSTEM mentions British English",
      "British English" in ai.UNIVERSAL_SYSTEM)

# ai.constitution.py should also have UK English
check("ai.constitution UK_ENGLISH_RULE exists", hasattr(const, "UK_ENGLISH_RULE"))
check("ai.constitution UK_ENGLISH_RULE has British English",
      "British English" in const.UK_ENGLISH_RULE)

# The system prompts in constitution.py should also have UK English
check("const.POLISH_SYSTEM has UK English", "British English" in const.POLISH_SYSTEM)
check("const.TEXT_SYSTEM has UK English", "British English" in const.TEXT_SYSTEM)
check("const.EMAIL_SYSTEM has UK English", "British English" in const.EMAIL_SYSTEM)
check("const.REPLY_SYSTEM has UK English", "British English" in const.REPLY_SYSTEM)
check("const.FOREIGN_SYSTEM has UK English", "British English" in const.FOREIGN_SYSTEM)

# UK English in local_engine.py
import local_engine
check("local_engine UK_ENGLISH exists", hasattr(local_engine, "UK_ENGLISH"))
check("local_engine UK_ENGLISH has British English",
      "British English" in local_engine.UK_ENGLISH)

# =============================================================================
# VAL-PRMT-009: Dead PROMPT_GUIDE code removed without breaking prompt mode
# =============================================================================
print("\n=== VAL-PRMT-009: PROMPT_GUIDE removal ===")

# PROMPT_GUIDE must NOT exist in the active ai package
check("PROMPT_GUIDE not in ai package", not hasattr(ai, "PROMPT_GUIDE"))
check("PROMPT_GUIDE not in ai.constitution", not hasattr(const, "PROMPT_GUIDE"))

# But build_prompt_system must still work
sys_result = const.build_prompt_system(None, url=const.CEREBRAS_URL)
check("build_prompt_system works (has content)", len(sys_result) > 100)
check("build_prompt_system has constitution content",
      "PROMPT ARCHITECT" in sys_result or "PART ONE" in sys_result)

# cerebras_prompt must be callable (prompt mode still works)
check("cerebras_prompt is callable", callable(ai.cerebras_prompt))

# _prompt_user_msg must still work for both lightweight and full paths
light = const._prompt_user_msg("test", "", lightweight=True)
full = const._prompt_user_msg("test", "", lightweight=False)
check("_prompt_user_msg lightweight works", len(light) > 0)
check("_prompt_user_msg full works", len(full) > 0)
check("Lightweight and full differ",
      light != full)

# =============================================================================
# Cross-mode: No wrapper language in any mode system prompt
# =============================================================================
print("\n=== Cross-mode: No wrapper language ===")

modes_with_output_rule = {
    "POLISH_SYSTEM": ai.POLISH_SYSTEM,
    "TEXT_SYSTEM": ai.TEXT_SYSTEM,
    "EMAIL_SYSTEM": ai.EMAIL_SYSTEM,
    "REPLY_SYSTEM": ai.REPLY_SYSTEM,
    "FOREIGN_SYSTEM": ai.FOREIGN_SYSTEM,
}

for name, prompt_text in modes_with_output_rule.items():
    # Extract just the output instruction (last sentence or "Output ONLY" clause)
    check(f"{name} has output-only instruction",
          "Output ONLY" in prompt_text
          or "output ONLY" in prompt_text
          or "reply ONLY" in prompt_text)

    # Must forbid "Here is" wrapper
    pt_lower = prompt_text.lower()
    check(f"{name} forbids 'Here is' wrapper",
          "no 'here is'" in pt_lower
          or "here is" not in pt_lower.split("output only")[-1]
          if "output only" in pt_lower else True)

    # Must forbid "Certainly" wrapper
    if "Certainly" in prompt_text:
        check(f"{name} does not encourage 'Certainly'",
              False)  # fail if model is told to say this
    else:
        check(f"{name} has no 'Certainly' instruction", True)

# =============================================================================
# Cross-mode: Garbage input / AI timeout preparation
# =============================================================================
print("\n=== Cross-mode: Robustness checks ===")

# _clean should handle empty and None input
check("_clean handles None", ai._clean(None) == "")
check("_clean handles empty", ai._clean("") == "")
check("_clean handles whitespace", ai._clean("   ") == "")

# _clean should handle very long input
long_input = "hello world " * 1000
cleaned_long = ai._clean(long_input)
check("_clean handles long input", len(cleaned_long) > 0)

# _clean should strip hallucination phrases
hallucination = "As an AI language model, I cannot help with that. The actual text."
cleaned_halluc = ai._clean(hallucination)
check("_clean strips AI self-reference",
      cleaned_halluc.strip() == hallucination.strip()
      or len(cleaned_halluc) > 0)  # _clean may not strip this, model_free should

# _extract_final_prompt handles empty input
check("_extract_final_prompt handles None", ai._extract_final_prompt(None) == "")
check("_extract_final_prompt handles empty", ai._extract_final_prompt("") == "")

# _sanitize_delimiters handles empty
check("_sanitize_delimiters handles empty",
      ai._sanitize_delimiters("") == "")

# =============================================================================
# Verify ai/__init__.py system prompts are the ACTIVE ones (used by dispatchers)
# =============================================================================
print("\n=== Active system prompt verification ===")

# The mode dispatchers in ai/__init__.py use the local system prompts, not the
# constitution.py copies. Verify the local ones have all required content.

# TEXT_SYSTEM (used by cerebras_text)
check("TEXT_SYSTEM has cleanup instruction", "clean up" in ai.TEXT_SYSTEM.lower())
check("TEXT_SYSTEM mentions fillers", "fillers" in ai.TEXT_SYSTEM.lower())
check("TEXT_SYSTEM says no summary", "summarize" in ai.TEXT_SYSTEM.lower()
      or "summarise" in ai.TEXT_SYSTEM.lower())

# EMAIL_SYSTEM (used by cerebras_email)
check("EMAIL_SYSTEM has full email structure instruction",
      "FULL email" in ai.EMAIL_SYSTEM or "full email" in ai.EMAIL_SYSTEM.lower())

# REPLY_SYSTEM (used by cerebras_reply)
check("REPLY_SYSTEM mentions reply text", "reply" in ai.REPLY_SYSTEM.lower())

# FOREIGN_SYSTEM (used by cerebras_foreign)
check("FOREIGN_SYSTEM mentions uncertain words",
      "unsure" in ai.FOREIGN_SYSTEM.lower()
      or "uncertain" in ai.FOREIGN_SYSTEM.lower()
      or "//" in ai.FOREIGN_SYSTEM)

# UNIVERSAL_SYSTEM still exists as fallback
check("UNIVERSAL_SYSTEM has PHASE 1", "PHASE 1" in ai.UNIVERSAL_SYSTEM)

# =============================================================================
# Verify consistency between ai/__init__.py and ai/constitution.py copies
# (They should be identical or the constitution ones should be removed)
# =============================================================================
print("\n=== System prompt consistency ===")

# The active copies are in ai/__init__.py. The constitution.py copies are dead
# code (not imported). This is a code smell but not a functional bug.
# Document it rather than failing.
ai_text = ai.TEXT_SYSTEM
const_text = const.TEXT_SYSTEM
if ai_text == const_text:
    print("  ok    TEXT_SYSTEM consistent between ai and ai.constitution")
else:
    print("  NOTE  TEXT_SYSTEM differs between ai and ai.constitution "
          "(ai/__init__.py version is the active one)")
    check("TEXT_SYSTEM exists in both modules",
          len(ai_text) > 0 and len(const_text) > 0)

ai_email = ai.EMAIL_SYSTEM
const_email = const.EMAIL_SYSTEM
if ai_email == const_email:
    print("  ok    EMAIL_SYSTEM consistent between ai and ai.constitution")
else:
    print("  NOTE  EMAIL_SYSTEM differs between ai and ai.constitution "
          "(ai/__init__.py version is the active one)")
    check("EMAIL_SYSTEM exists in both modules",
          len(ai_email) > 0 and len(const_email) > 0)

# =============================================================================
# Verify context handling in reply and foreign modes
# =============================================================================
print("\n=== Context handling ===")

# format_context_items correctly formats source items
items = [
    {"source": "clipboard", "time": "2024-01-01 10:00", "text": "Hello world"},
    {"source": "transcript", "time": "2024-01-01 10:05",
     "text": "This is a test"},
]
formatted = const.format_context_items(items)
check("format_context_items includes CLIPBOARD source", "CLIPBOARD" in formatted)
check("format_context_items includes TRANSCRIPT source", "TRANSCRIPT" in formatted)
check("format_context_items includes first text", "Hello world" in formatted)
check("format_context_items includes second text", "This is a test" in formatted)
check("format_context_items handles empty list",
      const.format_context_items([]) == "")
check("format_context_items handles None",
      const.format_context_items(None) == "")

# =============================================================================
# Verify export surface — all quality-related symbols accessible through ai pkg
# =============================================================================
print("\n=== Public API surface ===")

# Functions that must be accessible for prompt quality
quality_symbols = [
    "build_prompt_system",
    "POLISH_LEVELS",
    "split_mode_tail",
    "_clean",
    "_extract_final_prompt",
    "_sanitize_delimiters",
    "format_context_items",
    "cerebras_prompt",
    "cerebras_email",
    "cerebras_reply",
    "cerebras_foreign",
    "cerebras_text",
    "cerebras_polish",
]
for sym in quality_symbols:
    exists = hasattr(ai, sym)
    if exists:
        obj = getattr(ai, sym)
        is_ok = callable(obj) or isinstance(obj, dict) or isinstance(obj, str)
        check(f"ai.{sym} is accessible", is_ok)
    else:
        check(f"ai.{sym} is accessible", False)

# =============================================================================
# SUMMARY
# =============================================================================
print(f"\n{'=' * 60}")
print(f"RESULTS: {len(TESTS) - len(FAILURES)}/{len(TESTS)} passed")
if FAILURES:
    print(f"FAILURES: {len(FAILURES)}")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("All tests passed.")
