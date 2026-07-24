#!/usr/bin/env python3
"""Tests for ai/constitution.py — prompt system, constitution routing,
polish levels, system prompts, and text cleanup helpers.

Usage:
    python test_constitution.py
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


print("=== Import checks ===")

import ai.constitution as const
check("constitution module imports", True)
check("CONSTITUTION is non-empty string", isinstance(const.CONSTITUTION, str) and len(const.CONSTITUTION) > 100)
check("LIGHTWEIGHT_CONSTITUTION is non-empty string", isinstance(const.LIGHTWEIGHT_CONSTITUTION, str) and len(const.LIGHTWEIGHT_CONSTITUTION) > 50)
check("render_prefs is callable", callable(const.render_prefs))

print("\n=== Constitution routing ===")

check("build_prompt_system is callable", callable(const.build_prompt_system))

# Cerebras gets full constitution
cerebras_sys = const.build_prompt_system(None, url=const.CEREBRAS_URL)
check("Cerebras prompt system contains constitution", const.CONSTITUTION[:50] in cerebras_sys)

# Non-Cerebras gets lightweight
lightweight_sys = const.build_prompt_system(None, url="https://api.openai.com/v1/chat/completions")
check("Lightweight prompt system contains lightweight constitution",
      const.LIGHTWEIGHT_CONSTITUTION[:50] in lightweight_sys)
check("Lightweight prompt system does NOT contain full constitution",
      "PART ONE: WHAT YOU ARE" not in lightweight_sys)

# Back-compat constants
check("PROMPT_SYSTEM is non-empty", isinstance(const.PROMPT_SYSTEM, str) and len(const.PROMPT_SYSTEM) > 100)
check("LIGHTWEIGHT_PROMPT_SYSTEM is non-empty", isinstance(const.LIGHTWEIGHT_PROMPT_SYSTEM, str) and len(const.LIGHTWEIGHT_PROMPT_SYSTEM) > 50)

# Preferences folding
prefs_sys = const.build_prompt_system({"tone": "professional", "detail": "high"}, url=const.CEREBRAS_URL)
check("prefs folded into output", "professional" in prefs_sys and "high" in prefs_sys)

print("\n=== Polish levels ===")

check("POLISH_LEVELS is a dict", isinstance(const.POLISH_LEVELS, dict))
check("Light level present", "Light" in const.POLISH_LEVELS)
check("Standard level present", "Standard" in const.POLISH_LEVELS)
check("Thorough level present", "Thorough" in const.POLISH_LEVELS)
check("Light is minimal (empty clause)", const.POLISH_LEVELS["Light"] == "")
check("Standard has content", len(const.POLISH_LEVELS["Standard"]) > 0)
check("Thorough has content", len(const.POLISH_LEVELS["Thorough"]) > 0)
check("Standard and Thorough differ", const.POLISH_LEVELS["Standard"] != const.POLISH_LEVELS["Thorough"])

# Verify progressive aggressiveness
check("Standard mentions 'awkward phrasing'", "awkward" in const.POLISH_LEVELS["Standard"].lower())
check("Thorough mentions 'rewrite'", "rewrite" in const.POLISH_LEVELS["Thorough"].lower())

print("\n=== System prompts ===")

check("POLISH_SYSTEM contains UK English rule", "British English" in const.POLISH_SYSTEM or "colour" in const.POLISH_SYSTEM)
check("TEXT_SYSTEM is non-empty", isinstance(const.TEXT_SYSTEM, str) and len(const.TEXT_SYSTEM) > 50)
check("EMAIL_SYSTEM is non-empty", isinstance(const.EMAIL_SYSTEM, str) and len(const.EMAIL_SYSTEM) > 50)
check("REPLY_SYSTEM is non-empty", isinstance(const.REPLY_SYSTEM, str) and len(const.REPLY_SYSTEM) > 50)
check("FOREIGN_SYSTEM is non-empty", isinstance(const.FOREIGN_SYSTEM, str) and len(const.FOREIGN_SYSTEM) > 50)
check("UNIVERSAL_SYSTEM is non-empty", isinstance(const.UNIVERSAL_SYSTEM, str) and len(const.UNIVERSAL_SYSTEM) > 100)

# Foreign system mentions other languages
check("FOREIGN_SYSTEM mentions multiple languages",
      "Arabic" in const.FOREIGN_SYSTEM or "French" in const.FOREIGN_SYSTEM or "Spanish" in const.FOREIGN_SYSTEM)
check("FOREIGN_SYSTEM does not translate", "not translate" in const.FOREIGN_SYSTEM.lower() or "do NOT translate" in const.FOREIGN_SYSTEM)

# Email system mentions structure elements
check("EMAIL_SYSTEM mentions greeting", "greeting" in const.EMAIL_SYSTEM.lower() or "Hi" in const.EMAIL_SYSTEM)
check("EMAIL_SYSTEM mentions sign-off", "sign-off" in const.EMAIL_SYSTEM.lower() or "Best regards" in const.EMAIL_SYSTEM)

print("\n=== Second opinion tail ===")

check("split_mode_tail is callable", callable(const.split_mode_tail))

# Test mode detection
text, mode, conf, redo = const.split_mode_tail("Some cleaned text.\n\nMODE: prompt CONF: high")
check("mode detected: prompt", mode == "prompt")
check("confidence: high", conf == "high")
check("cleaned text extracted", "Some cleaned text." in text)
check("not redo", not redo)

# Test redo prompt
text, mode, conf, redo = const.split_mode_tail("__REDO_PROMPT__\n\nMODE: prompt CONF: high")
check("redo_prompt detected", redo)
check("redo mode is prompt", mode == "prompt")

# Test no mode
text, mode, conf, redo = const.split_mode_tail("Just some cleaned text.")
check("no mode returns None", mode is None)
check("no conf returns None", conf is None)
check("clean text untouched", "Just some cleaned text." in text)

# Test email/low
text, mode, conf, redo = const.split_mode_tail("Hi there.\n\nMODE: email CONF: low")
check("email mode detected", mode == "email")
check("low confidence", conf == "low")

print("\n=== Text cleanup ===")

check("_clean is callable", callable(const._clean))

# Quote stripping
check("_clean strips matching quotes", const._clean('"hello"') == "hello")
check("_clean strips matching single quotes", const._clean("'hello'") == "hello")
check("_clean leaves mismatched quotes", const._clean('"hello') == '"hello')
check("_clean preserves normal text", const._clean("hello world") == "hello world")

# Trailing instruction stripping
check("_clean strips trailing instruction", const._clean("hello\nClean it up now") == "hello")

# Context section stripping
dirty = "----- RECENT CONTEXT (to help infer the intended mode; do NOT clean or echo this) -----\nsome context\n----- END CONTEXT -----\nReal text"
cleaned = const._clean(dirty)
check("_clean strips context section", "some context" not in cleaned)
check("_clean preserves real text", "Real text" in cleaned)

print("\n=== _extract_final_prompt ===")

check("_extract_final_prompt is callable", callable(const._extract_final_prompt))

# With markers
marked = "Reasoning...\nFINAL_PROMPT_START\nYou are an expert.\nFINAL_PROMPT_END"
result = const._extract_final_prompt(marked)
check("extracts from markers", "You are an expert." in result)
check("markers stripped", "FINAL_PROMPT_START" not in result)
check("reasoning stripped", "Reasoning" not in result)

# Without markers (fallback)
plain = "You are an expert AI assistant."
result = const._extract_final_prompt(plain)
check("fallback returns text", len(result) > 0)
check("no markers in output", "FINAL_PROMPT" not in result)

print("\n=== _sanitize_delimiters ===")

check("_sanitize_delimiters replaces END TRANSCRIPT", "----- end user text -----" in const._sanitize_delimiters("----- END TRANSCRIPT -----"))
check("_sanitize_delimiters replaces END CONTEXT", "----- end user context -----" in const._sanitize_delimiters("----- END CONTEXT -----"))

print("\n=== format_context_items ===")

check("format_context_items is callable", callable(const.format_context_items))
items = [{"source": "clipboard", "time": "2024-01-01", "text": "Hello world"}]
result = const.format_context_items(items)
check("format_context_items includes source", "CLIPBOARD" in result)
check("format_context_items includes text", "Hello world" in result)

print("\n=== _prompt_user_msg ===")

check("_prompt_user_msg is callable", callable(const._prompt_user_msg))

# Basic request
msg = const._prompt_user_msg("build a calculator", "", lightweight=True)
check("lightweight prompt includes request", "build a calculator" in msg)
check("lightweight prompt is concise", len(msg) < 500)

# Full path
msg = const._prompt_user_msg("build a calculator", "", lightweight=False)
check("full prompt includes classification step", "CLASSIFY" in msg)
check("full prompt includes FINAL_PROMPT_START", "FINAL_PROMPT_START" in msg)

# With context
msg = const._prompt_user_msg("refactor it", "class User:\n    pass", lightweight=True)
check("lightweight with context includes context", "class User" in msg)

# Prompt requests are stateless; visible Deck history is not model context.
msg = const._prompt_user_msg("extend it", "", lightweight=True)
check("prompt request has no retained thread", "PROMPT THREAD" not in msg)

print("\n=== Backward compatibility from ai package ===")

import ai
check("ai.build_prompt_system callable", callable(ai.build_prompt_system))
check("ai.POLISH_LEVELS accessible", isinstance(ai.POLISH_LEVELS, dict))
check("ai.split_mode_tail callable", callable(ai.split_mode_tail))
check("ai._clean callable", callable(ai._clean))
check("ai._extract_final_prompt callable", callable(ai._extract_final_prompt))
check("ai.format_context_items callable", callable(ai.format_context_items))

print("\n=== PROMPT_GUIDE removal ===")

# PROMPT_GUIDE must not exist in any of the refactored modules
check("PROMPT_GUIDE removed from ai.constitution",
      not hasattr(const, "PROMPT_GUIDE"))
check("PROMPT_GUIDE removed from ai package",
      not hasattr(ai, "PROMPT_GUIDE"))

# Ensure build_prompt_system still works without PROMPT_GUIDE
sys_result = const.build_prompt_system(None, url=const.CEREBRAS_URL)
check("build_prompt_system works after PROMPT_GUIDE removal",
      len(sys_result) > 100)

print("\n=== Trivial request gating ===")

# build_prompt_system accepts optional request parameter
check("build_prompt_system accepts request kwarg",
      callable(lambda: const.build_prompt_system(request="test")))

# Trivial request (<= 16 words) on Cerebras → lightweight
trivial = const.build_prompt_system(None, url=const.CEREBRAS_URL,
                                     request="hello world test one two")
check("trivial request gates to lightweight on Cerebras",
      len(trivial) < 5000)

# Normal request (> 16 words) on Cerebras → full constitution
long_req = "this is a much longer request with many more words to test the full constitution routing behavior correctly"
normal = const.build_prompt_system(None, url=const.CEREBRAS_URL,
                                    request=long_req)
check("normal request gets full constitution on Cerebras",
      len(normal) > 30000)
check("full constitution contains PART ONE",
      "PART ONE" in normal)

# Backward compat: no request → same as before
no_req = const.build_prompt_system(None, url=const.CEREBRAS_URL)
check("no request kwarg still works (backward compat)",
      len(no_req) > 30000)

# Non-Cerebras always gets lightweight regardless of request length
other = const.build_prompt_system(None, url="https://api.openai.com",
                                   request=long_req)
check("non-Cerebras gets lightweight even with long request",
      len(other) < 5000)

print("\n=== TRIM step in 8-step pipeline ===")

# Full pipeline (not lightweight) must include the TRIM step
full_msg = const._prompt_user_msg("build a calculator", "", lightweight=False)
check("TRIM step present in full pipeline", "8. TRIM" in full_msg or "TRIM:" in full_msg)
check("OUTPUT renumbered to step 9", "9. OUTPUT" in full_msg)

# Lightweight path should NOT have TRIM or numbered steps
light_msg = const._prompt_user_msg("build a calculator", "", lightweight=True)
check("lightweight path has no numbered steps", "1. CLASSIFY" not in light_msg)

print("\n=== Conciseness awareness in output rules ===")

# _PROMPT_OUTPUT_RULE should contain conciseness language
check("_PROMPT_OUTPUT_RULE has CONCISENESS",
      "CONCISENESS" in const._PROMPT_OUTPUT_RULE
      or "conciseness" in const._PROMPT_OUTPUT_RULE.lower())
check("_PROMPT_OUTPUT_RULE mentions LEAN",
      "LEAN" in const._PROMPT_OUTPUT_RULE)

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
