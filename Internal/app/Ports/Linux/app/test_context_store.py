#!/usr/bin/env python3
"""Offline tests for context_store.py — the conversation-capture cleaning + Reply
context plumbing (refinement pass §6).

Standalone (no pytest, no network): run with the venv python; exit 0 = pass.
"""
import os
import sys
import tempfile

from context_store import ConversationStore

FAILS = []


def check(name, cond):
    print(f"  [{'ok ' if cond else 'FAIL'}] {name}")
    if not cond:
        FAILS.append(name)


def _store():
    return ConversationStore(os.path.join(tempfile.mkdtemp(), "conv.json"))


RAW = """New chat
Today
ChatGPT
You said:
How do I reverse a list in Python?
ChatGPT said:
Use the reversed() builtin or a slice with step -1:
reversed_list = my_list[::-1]
Copy code
Regenerate
ChatGPT can make mistakes. Check important info.
Send a message"""


def test_chrome_stripped_and_turns_split():
    cs = _store()
    res = cs.capture_conversation(RAW)
    check("two turns captured (user + ai)", res["turns"] == 2)
    check("chat UI detected as ChatGPT", res["ui"] == "ChatGPT")
    ctx = cs.get_conversation_context(6)
    check("nav/button chrome removed",
          "New chat" not in ctx and "Copy code" not in ctx
          and "Regenerate" not in ctx)
    check("disclaimer footer removed", "can make mistakes" not in ctx)
    check("real question kept", "How do I reverse a list" in ctx)
    check("real answer kept", "reversed()" in ctx and "[::-1]" in ctx)
    check("turns are role-labelled", "[You" in ctx and "[AI" in ctx)


def test_ui_detection():
    check("Claude detected",
          ConversationStore.detect_chat_ui("Claude can make mistakes.") == "Claude")
    check("Gemini detected",
          ConversationStore.detect_chat_ui("Gemini can make mistakes.") == "Gemini")
    check("unknown UI → empty", ConversationStore.detect_chat_ui("just text") == "")


def test_clean_never_empties():
    # A capture that is ALL chrome must not be emptied (fall back to the raw text).
    only_chrome = "Copy\nRegenerate\nNew chat"
    out = ConversationStore._clean_chrome(only_chrome)
    check("all-chrome capture is not emptied", bool(out.strip()))


def test_conversation_context_and_fallback():
    cs = _store()
    check("no conversation initially → empty context", cs.get_conversation_context() == "")
    check("has_conversation False initially", cs.has_conversation() is False)
    cs.capture_conversation(RAW)
    check("has_conversation True after capture", cs.has_conversation() is True)
    # A re-capture REPLACES the prior conversation (refresh, not append).
    cs.capture_conversation("You said:\nhi\nClaude said:\nhello there friend")
    convs = [e for e in cs.get_thread() if e.get("source") == "conversation"]
    check("re-capture replaces the prior conversation",
          all("reverse" not in e["text"].lower() for e in convs))


def test_individually_classified_replies_survive_capture():
    cs = _store()
    cs.manual_add("A manually-saved reply that should survive a later capture. " * 4)
    cs.capture_conversation(RAW)
    kept = [e for e in cs.get_thread() if e.get("source") == "manual"]
    check("manual replies are not dropped by a conversation capture", len(kept) == 1)


def main():
    print("context_store — capture cleaning + reply context")
    for fn in (test_chrome_stripped_and_turns_split, test_ui_detection,
               test_clean_never_empties, test_conversation_context_and_fallback,
               test_individually_classified_replies_survive_capture):
        fn()
    if FAILS:
        print(f"\n{len(FAILS)} FAILED:", ", ".join(FAILS))
        sys.exit(1)
    print("\nALL GREEN")


if __name__ == "__main__":
    main()
