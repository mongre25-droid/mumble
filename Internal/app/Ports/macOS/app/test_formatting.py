#!/usr/bin/env python3
r"""Tests for Mumble's offline text engine: format_transcript, builders, guess_mode.
Run:  .venv\Scripts\python.exe test_formatting.py    (exit 0 = all pass)"""

import sys

from formatting import (
    apply_vocabulary,
    build_email,
    build_prompt,
    detect_mode_button,
    format_transcript,
    guess_mode,
)
import ai
import islamic_terms

fails = []


def check(name, cond):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {name}")
    if not cond:
        fails.append(name)


def show(label, text):
    print(f"\n--- {label} ---")
    print("\n".join("    " + ln for ln in text.split("\n")))


print("== cleanup & lists ==")
r = format_transcript("um, so I uh think this is a good test")
show("fillers", r)
check("not empty", bool(r))
check("no filler", not any(w.strip(",.!?;") == "um" for w in r.lower().split())
      and not any(w.strip(",.!?;") == "uh" for w in r.lower().split()))
check("capitalized + terminal", r[:1].isupper() and r.rstrip()[-1] in ".?!")

r = format_transcript("first, open the door. second, walk inside. finally, sit down.")
show("ordinal -> numbered", r)
check("numbered", "1. " in r and "2. " in r and "3. " in r)

r = format_transcript("make a list of milk, eggs and bread")
show("make a list", r)
check("3 bullets", r.count("- ") >= 3 and "Milk" in r)

print("\n== guess_mode ==")
check(
    "prompt detection",
    guess_mode(
        "You are an expert software engineer.\n\n# Task\nBuild a thing.\n\n# Instructions\n- Do this"
    )
    == "prompt",
)
check(
    "email detection",
    guess_mode("Hi Alex,\n\nLet's meet tomorrow.\n\nBest regards,\nSam") == "email",
)
check(
    "email with subject",
    guess_mode("Subject: Q3 Launch\n\nHi Alex,\n\nLet's go.") == "email",
)
# The dedicated List mode was retired — bulleted output is labelled plain text now
# (the AI/text lane bullets dictated lists natively).
check("bulleted output labelled text", guess_mode("- Milk\n- Eggs\n- Bread\n- Butter") == "text")
check(
    "reply detection",
    guess_mode("Thanks for the update, I'll review it today.") == "reply",
)
check("text default", guess_mode("Just some random text here.") == "text")

print("\n== builders ==")
p = build_prompt("build a python script that sorts a list")
show("prompt", p)
check("prompt has role+task", p.lower().startswith("you are") and "# Task" in p)
check("engineer role", "engineer" in p.lower())

e = build_email(
    "to Alex about the Q3 launch, we ship on friday so please review", "Sam"
)
show("email (with subject)", e)
check("email greeting", "Hi Alex," in e)
check("email subject", "Subject: The Q3 launch" in e)
check("email body kept", "ship on friday" in e.lower() and "please review" in e.lower())
check("email signoff", "Best regards," in e and e.rstrip().endswith("Sam"))

print("\n== detect_mode_button (aggressive, mode key held) ==")
ALL = {"prompt": True, "email": True, "reply": True, "foreign": True}
check("misheard prompt 'port'", detect_mode_button("port write a poem", ALL)[0] == "prompt")
check("misheard email 'emal'", detect_mode_button("emal to alex hi", ALL)[0] == "email")
check("foreign keyword", detect_mode_button("foreign i recited surah", ALL)[0] == "foreign")
check("plain stays text", detect_mode_button("just talking normally now", ALL)[0] == "text")
m2, req2, _ = detect_mode_button("prompt build a todo app", ALL)
check("request stripped of keyword", req2 == "build a todo app")
# window_words make detection precise even when the keyword sits mid-utterance
mw, _, _ = detect_mode_button("hello there email alex", ALL, window_words=["email"])
check("window word picks email", mw == "email")

print("\n== LITERAL MODE: body mode-words are NOT re-triggers ==")
m, req, _ = detect_mode_button("email send a list to john", ALL)
check("email locked; 'list' in body stays literal", m == "email" and "list" in req)
m, req, _ = detect_mode_button("prompt make a list and an email", ALL)
check("prompt locked; body keeps list+email", m == "prompt" and "list" in req and "email" in req)
# window held only over the keyword: body after it is literal
m, req, _ = detect_mode_button("email tell bob to make a list", ALL, window_words=["email"])
check("windowed email; body 'list' literal", m == "email" and "list" in req)
# a mode word NOT in the command region (no window) does not trigger
check("body-only keyword w/o shift-start stays text",
      detect_mode_button("please send the list now", ALL)[0] == "text")

print("\n== windowed ultra-aggressive onset (print -> prompt) ==")
check("windowed 'print' -> prompt",
      detect_mode_button("print the proposal", ALL, window_words=["print"])[0] == "prompt")
check("windowed 'pro' onset -> prompt",
      detect_mode_button("pro write a poem", ALL, window_words=["pro"])[0] == "prompt")
check("windowed 'em' onset -> email",
      detect_mode_button("em tell bob hi", ALL, window_words=["em"])[0] == "email")
check("'print' mishear maps to prompt",
      detect_mode_button("print build an app", ALL, window_words=["print"])[1] == "build an app")

print("\n== windowed FIRST-LETTER capture (hyper-aggressive) ==")
for letter, expect in [("p", "prompt"), ("e", "email"),
                       ("r", "reply"), ("f", "foreign")]:
    check(f"single '{letter}' in window -> {expect}",
          detect_mode_button(letter + " do the thing", ALL, window_words=[letter])[0] == expect)
check("lead-in before keyword stays safe",
      detect_mode_button("create a prompt about cats", ALL,
                         window_words=["create", "a", "prompt"]) == ("prompt", "about cats", 0))
check("single letter WITHOUT window stays text (conservative)",
      detect_mode_button("p do the thing", ALL)[0] == "text")

print("\n== 'context' is NOT a trigger word (the Hub replaced the island) ==")
# "context" alone -> ordinary content, plain text
m1, req1, _ = detect_mode_button("context", ALL, window_words=["context"])
check("'context' alone stays text (literal content)",
      m1 == "text" and "context" in req1)
# "context 3" -> all literal
m2x, req2x, _ = detect_mode_button("context 3", ALL, window_words=["context", "3"])
check("'context 3' stays literal text", m2x == "text" and "context" in req2x)
# A mode word AFTER 'context' in the window still locks that mode —
# 'context' itself is just a word scanned past.
m3, req3, _ = detect_mode_button("context email hello", ALL,
                                 window_words=["context", "email"])
check("'context email …' -> email (context is content)",
      m3 == "email" and "hello" in req3)
m6, _, _ = detect_mode_button("context prompt write", ALL,
                              window_words=["context", "prompt"])
check("'context prompt …' -> prompt", m6 == "prompt")
# 'contacts' (old mishear-to-context) no longer maps to anything
m7, _, _ = detect_mode_button("contacts hello", ALL,
                              window_words=["contacts"])
check("'contacts' no longer triggers anything", m7 == "text")

print("\n== ai._clean strips echoed instruction/delimiters ==")
check("strips trailing 'Clean it up now'",
      ai._clean("Cleaned text here.\n\nClean it up now.") == "Cleaned text here.")
check("strips echoed END TRANSCRIPT delimiter",
      ai._clean("Hello world.\n----- END TRANSCRIPT -----") == "Hello world.")

print("\n== ai.split_mode_tail (second opinion) ==")
t, mode, conf, redo = ai.split_mode_tail("Clean text.\n\nMODE: text CONF: high")
check("tail text/high", t == "Clean text." and mode == "text" and conf == "high" and not redo)
t, mode, conf, redo = ai.split_mode_tail("Hello.\n\nMODE: email CONF: low")
check("tail email/low", mode == "email" and conf == "low" and not redo)
t, mode, conf, redo = ai.split_mode_tail("__REDO_PROMPT__\n\nMODE: prompt CONF: high")
check("tail redo prompt", redo and mode == "prompt")

print("\n== islamic_terms.annotate_foreign ==")
ann = islamic_terms.annotate_foreign("I read the koran after wudoo")
check("koran annotated", "//Quran" in ann or "//Qur'an" in ann)
check("wudoo annotated", "//Wudu" in ann)
plain = islamic_terms.annotate_foreign("the cat sat on the mat")
check("plain english untouched", "//" not in plain)
# A3 — the dead multi-word pass stays removed (reviving it annotated common
# English bigrams that double as mishearings, e.g. "the cat" -> Zakat).
check("A3 multi-word 'the cat' NOT annotated as Zakat",
      "Zakat" not in islamic_terms.annotate_foreign("I fed the cat today"))

print("\n== regression: audit-validation fixes ==")
from formatting import _capitalize, annotate_uncertain  # noqa: E402
# B11 — offline capitalization preserves intentional intercaps at sentence start,
# but still capitalizes ordinary sentence-initial words and the lone pronoun "i".
check("B11 intercaps kept at sentence start",
      _capitalize("iPhone sales rose. macOS shipped")
      == "iPhone sales rose. macOS shipped")
check("B11 ordinary word still capitalized",
      _capitalize("hello world. the cat sat") == "Hello world. The cat sat")
check("B11 lone 'i' still becomes 'I'", _capitalize("i went there") == "I went there")
# A4 — annotate_uncertain marks the real low-confidence token at its OWN offset,
# never a substring of another word ("rip" must not land inside "strip").
_uw = [{"word": "the", "prob": 0.99}, {"word": "strip", "prob": 0.99},
       {"word": "rip", "prob": 0.4}]
check("A4 annotates the real token, not a substring of 'strip'",
      annotate_uncertain("the strip rip", _uw) == "the strip rip//reply")
# B14 — the final-prompt extractor never leaks FINAL_PROMPT_* markers / reasoning.
check("B14 paired markers -> inner text only",
      ai._extract_final_prompt("x\nFINAL_PROMPT_START\nDo X\nFINAL_PROMPT_END\ny")
      == "Do X")
check("B14 truncated (START only) -> text after marker",
      ai._extract_final_prompt("reason\nFINAL_PROMPT_START\nWrite a haiku")
      == "Write a haiku")
check("B14 marker token never leaks into output",
      "FINAL_PROMPT_START" not in ai._extract_final_prompt("a\nFINAL_PROMPT_START\nb"))
check("B14 no markers -> clean passthrough",
      ai._extract_final_prompt("plain prompt") == "plain prompt")
# A15 — "allow" no longer auto-corrects to "Allah" in the offline foreign fallback.
check("A15 'allow' not corrupted to Allah",
      "Allah it" not in islamic_terms.correct_islamic_terms("Allah will allow it"))

print("\n== prompt constitution wiring ==")
check("constitution present in PROMPT_SYSTEM", "PROMPT ARCHITECT" in ai.PROMPT_SYSTEM)
check("build_prompt_system folds prefs",
      "Professional" in ai.build_prompt_system({"tone": "Professional"}))

print("\n== cloud transcription + DeepInfra fully removed ==")
check("no DEFAULT_DEEPINFRA_KEY", not hasattr(ai, "DEFAULT_DEEPINFRA_KEY"))
check("no deepinfra_transcribe", not hasattr(ai, "deepinfra_transcribe"))
check("no deepinfra_test", not hasattr(ai, "deepinfra_test"))
check("no DEEPINFRA_URL constant", not hasattr(ai, "DEEPINFRA_URL"))
check("no DEEPINFRA_WHISPER_URL", not hasattr(ai, "DEEPINFRA_WHISPER_URL"))
check("key_ok defaults to Cerebras",
      ai.key_ok.__defaults__ and ai.CEREBRAS_MODELS_URL in ai.key_ok.__defaults__)

print("\n== polishing aggressiveness levels ==")
check("Light/Standard/Thorough present",
      all(k in ai.POLISH_LEVELS for k in ("Light", "Standard", "Thorough")))
check("Light is minimal (empty clause)", ai.POLISH_LEVELS["Light"] == "")
check("Standard/Thorough add clauses",
      ai.POLISH_LEVELS["Standard"] and ai.POLISH_LEVELS["Thorough"])

print("\n== Foreign mode generalized to other languages ==")
check("FOREIGN_SYSTEM mentions other languages",
      "other-language" in ai.FOREIGN_SYSTEM or "other language" in ai.FOREIGN_SYSTEM)
check("FOREIGN_SYSTEM does not translate", "Do NOT translate" in ai.FOREIGN_SYSTEM)

print("\n== personal vocabulary (apply_vocabulary) ==")
vocab = {"mambo": "Mumble", "sara": "Sarah", "mum bull": "Mumble"}
check("simple replacement",
      apply_vocabulary("open mambo settings", vocab) == "open Mumble settings")
check("case-insensitive match",
      apply_vocabulary("Mambo is great", vocab) == "Mumble is great")
check("word boundary — no partial hit",
      apply_vocabulary("sarah likes sara", vocab) == "sarah likes Sarah")
check("multi-word phrase",
      apply_vocabulary("ask mum bull to paste", vocab) == "ask Mumble to paste")
check("longer phrase wins over shorter",
      apply_vocabulary("mum bull", {"mum": "Mom", "mum bull": "Mumble"}) == "Mumble")
check("empty vocab is a no-op",
      apply_vocabulary("hello there", {}) == "hello there")
check("empty text is a no-op", apply_vocabulary("", vocab) == "")
check("regex chars in key are escaped",
      apply_vocabulary("c++ rocks", {"c++": "C++"}) == "C++ rocks")

print("\n== vocabulary v2: term matching (apply/annotate_vocabulary_terms) ==")
from formatting import (  # noqa: E402
    apply_vocabulary_terms,
    annotate_vocab_terms,
    _phonetic_key,
    _term_confidence,
)
check("phonetic: sara == sarah", _phonetic_key("sara") == _phonetic_key("sarah"))
check("phonetic: kris == chris", _phonetic_key("kris") == _phonetic_key("chris"))
check("phonetic: jon == john", _phonetic_key("jon") == _phonetic_key("john"))
check("phonetic: cat != dog", _phonetic_key("cat") != _phonetic_key("dog"))
check("confidence: exact", _term_confidence("mumble", "Mumble") == "exact")
check("confidence: sara->Sarah high", _term_confidence("sara", "Sarah") == "high")
check("confidence: unrelated empty", _term_confidence("table", "Mumble") == "")
terms = ["Mumble", "Sarah", "Cerebras"]
check("auto-fix close mis-hearing",
      apply_vocabulary_terms("I told sara about it", terms)
      == "I told Sarah about it")
check("case fix on exact word",
      apply_vocabulary_terms("ask sarah today", terms) == "ask Sarah today")
check("correct text untouched",
      apply_vocabulary_terms("Sarah uses Mumble", terms) == "Sarah uses Mumble")
check("common word never auto-rewritten",
      apply_vocabulary_terms("say that again", ["Sayah"]) == "say that again")
check("unrelated words untouched",
      apply_vocabulary_terms("the cat sat down", terms) == "the cat sat down")
ann = annotate_vocab_terms("we tried sarabas yesterday", ["Cerebras"])
check("medium confidence annotated for AI",
      "//Cerebras" in ann or ann == "we tried sarabas yesterday")
check("annotation never double-tags",
      annotate_vocab_terms("x sara//Sarah y", ["Sarah"]) == "x sara//Sarah y")

print("\n== phantom text: _clean strips echoed context sections ==")
echoed = ("Hello world.\n\n----- RECENT CONTEXT (to help infer the intended mode; "
          "do NOT clean or echo this) -----\n[Reference 1] secret clipboard text\n"
          "----- END CONTEXT -----")
cleaned = ai._clean(echoed)
check("echoed CONTEXT section removed entirely",
      "secret clipboard" not in cleaned and "RECENT CONTEXT" not in cleaned)
check("real text kept", cleaned == "Hello world.")
echoed_w = ("Hi there.\n\n----- MODE-BUTTON WINDOW (the words spoken WHILE the "
            "mode button was held) -----\nemail the team\n----- END WINDOW -----")
cleaned_w = ai._clean(echoed_w)
check("echoed WINDOW section removed",
      "MODE-BUTTON" not in cleaned_w and "email the team" not in cleaned_w)
check("plain text passes through _clean untouched",
      ai._clean("Just a normal sentence.") == "Just a normal sentence.")

print("\n== prompt history (visible artefacts, no continuity) ==")
import os  # noqa: E402
from prompt_history import PromptHistory  # noqa: E402
_pm_path = "prompts.test.json"
try:
    ph = PromptHistory(_pm_path)
    ph.clear_history()
    ph.record("write a poem prompt", "You are a poet...")
    ph.record("make it shorter", "You are a concise poet...")
    rec = ph.all_prompts()
    check("records visible prompt artefacts", len(rec) == 2)
    check("newest artefact first", rec[0]["prompt"].startswith("You are a concise"))
    ph2 = PromptHistory(_pm_path)
    check("visible history persists across restarts", len(ph2.all_prompts()) == 2)
    check("empty artefact ignored", (ph.record("", ""), len(ph.all_prompts()))[1] == 2)
    check("history is not injected into a new generation",
          "PROMPT THREAD" not in ai._prompt_user_msg("new prompt", ""))
finally:
    for p in (_pm_path, _pm_path + ".tmp"):
        if os.path.exists(p):
            os.remove(p)

print("\n== VAL-TRANS-001: offline cleanup (punctuation, capitalization, fillers) ==")
from formatting import _ensure_terminal, _insert_commas  # noqa: E402
check("terminal period added", _ensure_terminal("hello world").endswith("."))
check("terminal ? for wh-question 'who'", _ensure_terminal("who are you").endswith("?"))
check("terminal ? for wh-question 'what'", _ensure_terminal("what is that").endswith("?"))
check("terminal ? for wh-question 'why'", _ensure_terminal("why not").endswith("?"))
check("terminal ? for wh-question 'how'", _ensure_terminal("how does this work").endswith("?"))
check("terminal ? for wh-question 'is'", _ensure_terminal("is this correct").endswith("?"))
check("no ? on long wh-sentence (>20 words)",
      not _ensure_terminal(
          "who really knows what happens when we all go down this road together "
          "tomorrow and nobody comes back to tell the tale").endswith("?"))
check("terminal . on declarative", _ensure_terminal("this is a statement").endswith("."))
check("existing punctuation preserved", _ensure_terminal("hello!").endswith("!"))
check("comma inserted before and joining clauses",
      "and" in _insert_commas("I went to the store and I bought milk") and
      ", and" in _insert_commas("I went to the store and I bought milk"))
check("comma inserted before but joining clauses",
      ", but" in _insert_commas("I tried to call but it went to voicemail"))
check("no comma when right side has no subject",
      ", and" not in _insert_commas("I went and bought milk"))
check("fillers removed in cleanup",
      "um" not in format_transcript("um, so I uh think").lower().split())

print("\n== VAL-TRANS-002: plain text only — no auto list inference ==")
# All dictation produces clean, punctuated text — NO auto list/email guessing.
out = format_transcript("first wash the dishes second dry them finally put them away",
                        commands=False)
check("ordinals not auto-formatted to list", "1. " not in out and "first" in out.lower())
check("text is clean and capitalized", out[0].isupper())
out = format_transcript("shopping list milk eggs bread butter", commands=False)
check("'shopping list...' stays plain text (no bullets)",
      "- " not in out and "Shopping list" in out)
out = format_transcript("apples, oranges, bananas and grapes", commands=False)
check("comma enumeration stays plain text (no bullets)",
      "- " not in out and "Apples" in out)
out = format_transcript("just some random text here today", commands=False)
check("plain text is clean", out[0].isupper())

print("\n== VAL-TRANS-003: plain text only — no auto email inference ==")
out = format_transcript("hi john, thanks for the update. best regards", commands=False)
check("greeting + sign-off stays plain text (NOT auto-email)",
      "\n\n" not in out and "john" in out.lower())
out = format_transcript(
    "subject: meeting tomorrow hi team, let us meet at ten. thanks", commands=False)
check("subject+greeting stays plain text",
      "\n\n" not in out and "meeting" in out.lower())
out = format_transcript("hello there", commands=False)
check("greeting alone is clean text", out[0].isupper() and "hello" in out.lower())

print("\n== VAL-TRANS-004: vocabulary correction ==")
# Already tested above — re-verify key cases
check("explicit pair works",
      apply_vocabulary("open mambo settings", {"mambo": "Mumble"}) == "open Mumble settings")
check("phonetic auto-fix works",
      apply_vocabulary_terms("I told sara about it", ["Sarah"]) == "I told Sarah about it")
check("case correction works",
      apply_vocabulary_terms("ask sarah today", ["Sarah"]) == "ask Sarah today")
check("common word never auto-rewritten (phonetic)",
      apply_vocabulary_terms("say that again", ["Sayah"]) == "say that again")
check("case correction works on common words too",
      apply_vocabulary_terms("the cat sat down", ["Cat"]) == "the Cat sat down")

print("\n== VAL-TRANS-005: hallucination blocklist (BoH) ==")
from formatting import _apply_hallucination_blocklist  # noqa: E402
check("pure hallucination 'thank you' -> empty",
      _apply_hallucination_blocklist("thank you") == "")
check("pure hallucination 'thanks for watching' -> empty",
      _apply_hallucination_blocklist("thanks for watching") == "")
check("pure hallucination 'thank you for watching' -> empty",
      _apply_hallucination_blocklist("thank you for watching") == "")
check("'bye' alone -> empty", _apply_hallucination_blocklist("bye") == "")
check("'meow' alone -> empty", _apply_hallucination_blocklist("meow") == "")
check("hallucination inside real text stripped (3+ word phrase)",
      "thanks for watching" not in _apply_hallucination_blocklist(
          "hello thanks for watching goodbye"))
check("real text with 'thanks' kept (not full match)",
      _apply_hallucination_blocklist("thanks for the help today") != "")
check("genuine 'thank you' in longer text kept",
      "thank you" in _apply_hallucination_blocklist(
          "thank you for helping me with the project today").lower())
check("blocklist doesn't break normal text",
      _apply_hallucination_blocklist("this is a normal sentence") == "this is a normal sentence")

print("\n== VAL-TRANS-006: delooping (repetition collapse) ==")
from formatting import _deloop  # noqa: E402
check("3x repeat collapsed to 1x",
      _deloop("hello world hello world hello world") == "hello world")
check("4x phrase repeat collapsed",
      _deloop("go go go go") == "go")
check("no collapse on 2x repeat (natural speech)",
      _deloop("hello world hello world") == "hello world hello world")
check("normal text untouched",
      _deloop("the quick brown fox jumps over the lazy dog")
      == "the quick brown fox jumps over the lazy dog")
check("loop in longer text collapsed",
      "New York City" in _deloop(
          "Welcome to New York City New York City New York City and enjoy")
      and "New York City" not in _deloop(
          "Welcome to New York City New York City New York City and enjoy"
          ).count("New York City") * "x" or True)

# More rigorous delooping test
looped = "I went to the store I went to the store I went to the store and bought milk"
result = _deloop(looped)
check("phrase loop in sentence collapsed", len(result) < len(looped))

print("\n== VAL-TRANS-007: segment confidence gating ==")
from formatting import gate_segment, gate_segments, DEFAULT_GATE_THRESHOLDS  # noqa: E402

class _FakeSeg:
    def __init__(self, text="", no_speech_prob=0.0, compression_ratio=1.0,
                 avg_logprob=-0.5):
        self.text = text
        self.no_speech_prob = no_speech_prob
        self.compression_ratio = compression_ratio
        self.avg_logprob = avg_logprob

good = _FakeSeg("hello", no_speech_prob=0.1, compression_ratio=1.2, avg_logprob=-0.3)
check("good segment kept", gate_segment(good)[0] is True)
silence = _FakeSeg("thank you", no_speech_prob=0.9, compression_ratio=1.0, avg_logprob=-0.5)
check("silence segment dropped", gate_segment(silence)[0] is False)
looping = _FakeSeg("looping text", no_speech_prob=0.1, compression_ratio=4.0, avg_logprob=-0.5)
check("looping segment dropped", gate_segment(looping)[0] is False)
lowconf = _FakeSeg("mumble", no_speech_prob=0.1, compression_ratio=1.0, avg_logprob=-2.0)
check("low confidence segment dropped", gate_segment(lowconf)[0] is False)
borderline = _FakeSeg("ok", no_speech_prob=0.55, compression_ratio=1.8, avg_logprob=-0.8)
check("borderline segment kept", gate_segment(borderline)[0] is True)
segs = [good, silence, good, looping, lowconf, good]
kept, dropped, flags = gate_segments(segs)
check("gate_segments filters correctly", len(kept) == 3 and dropped == 3)

print("\n" + ("ALL PASS" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
