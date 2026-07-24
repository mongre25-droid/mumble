#!/usr/bin/env python3
"""
Conversation Context Store — dedicated, persistent storage for AI conversation
turns, SEPARATE from the general clipboard. Entries are timestamped, tagged,
and retrieved with recency weighting for the "context" keyword system.

NOTE (2026-06-28): The write path from clipboard.py → classify_and_store()
has been removed (phantom writer — the read/retrieval API was already dead).
This module is deferred for full retirement in a future pass (touches shared
store + ports). The classify_and_store() method definition remains but is
unreachable from the live code path.

Original design (historical):
1. The clipboard monitor fed every copy into classify_and_store().
2. A heuristic classifier (no API cost) decided if the text looked like an AI
   reply: Markdown structures, AI response openers, length threshold.
3. If the user said "context" within 30s of copying, retroactive_promote()
   elevated it.
4. get_context(n) returned the last N conversation turns with recency weighting.

Data lives at conv_store.json alongside the rest of Mumble's app data.
"""

import hashlib
import json
import os
import re
import threading
import time
from collections import deque
from datetime import datetime


class ConversationStore:
    """Persistent store of AI conversation turns with recency-weighted retrieval."""

    # Heuristic signals that a copied text is likely an AI reply.
    # Each is a callable(text) -> bool.  Score >= 2  => auto-classify.
    AI_SIGNALS = [
        # Markdown code blocks (very strong signal)
        lambda t: t.count("```") >= 2,
        # Bold / emphasis markers
        lambda t: "**" in t and t.count("**") >= 2,
        # Structured numbered or bullet lists
        lambda t: t.count("\n- ") >= 3 or t.count("\n1. ") >= 3 or t.count("\n* ") >= 3,
        # Code constructs (any language)
        lambda t: any(
            kw in t
            for kw in (
                "def ",
                "function ",
                "import ",
                "class ",
                "const ",
                "let ",
                "var ",
                "public ",
                "private ",
                "export ",
                "return ",
                "async ",
                "await ",
                "```",
                "#include",
                "package ",
                "@Override",
            )
        ),
        # AI response openers — typical first words of a generated reply.
        # Must be followed by substantial content (≥200 chars) to avoid false
        # positives on short emails like "Great, thanks!" or "Sure, here you go."
        lambda t: (
            len(t) >= 200
            and any(
                t.strip().startswith(w)
                for w in (
                    "Sure",
                    "Here",
                    "Based on",
                    "I'd",
                    "Great",
                    "Good",
                    "That's",
                    "You're",
                    "Let me",
                    "Certainly",
                    "Absolutely",
                    "Thanks for",
                    "I'll",
                    "This is",
                    "Here's a",
                    "I hope",
                    "No problem",
                )
            )
        ),
        # Markdown headers
        lambda t: any(
            line.strip().startswith("##") or line.strip().startswith("# ")
            for line in t.split("\n")[:6]
        ),
        # Inline code spans
        lambda t: t.count("`") >= 4 and "`" not in t[:2],
        # Tables
        lambda t: t.count("|") >= 3 and "|---" in t,
    ]

    # If the user copies something and then says "context" within this window,
    # retroactively classify it as an AI reply even if the heuristic missed it.
    RETROACTIVE_WINDOW = 30  # seconds

    def __init__(self, path, max_entries=100):
        self.path = path
        self.max_entries = max_entries
        self.entries = deque(maxlen=max_entries)
        # Recent copies not yet classified: [(timestamp, text_hash, full_text), ...]
        self._recent_copies = []
        self._on_reply_callback = (
            None  # experimental hook: called when an AI reply is stored
        )
        self._lock = threading.RLock()
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                data = []
            for e in data[-self.max_entries :]:
                if isinstance(e, dict) and "text" in e and "stamp" in e:
                    e.setdefault("source", "unknown")
                    e.setdefault("confidence", 0)
                    e.setdefault("length", len(e.get("text", "")))
                    self.entries.append(e)
        except Exception as e:
            print(f"context_store load error ({type(e).__name__}): {e}")

    def _save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(list(self.entries), f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError as e:
            print(f"context_store save error ({type(e).__name__}): {e}")

    def classify_and_store(self, text):
        """Called by the clipboard monitor for every copied text. Returns True if
        the text was classified as an AI reply and stored."""
        text = (text or "").strip()
        if len(text) < 120:  # too short to be a meaningful AI reply
            with self._lock:
                self._track_copy(text)
            return False

        score = sum(1 for sig in self.AI_SIGNALS if sig(text))

        if score >= 2:  # strong confidence — auto-classify
            with self._lock:
                stored = self._add_entry(text, source="auto", confidence=score)
            # experimental: feed to compensation tracker
            try:
                if stored and self._on_reply_callback:
                    self._on_reply_callback(text)
            except Exception:
                pass
            return stored

        # Weak signal — keep it in the retroactive window in case the user
        # says "context" soon.
        with self._lock:
            self._track_copy(text)
        return False

    def _track_copy(self, text):
        """Remember a copy for possible retroactive classification."""
        if not text or not text.strip():
            return
        txt = text.strip()
        self._recent_copies.append(
            (
                time.time(),
                hashlib.sha256(txt.encode("utf-8", errors="replace")).hexdigest(),
                txt,
            )
        )
        # Prune entries older than the retroactive window
        cutoff = time.time() - self.RETROACTIVE_WINDOW
        self._recent_copies = [
            (ts, h, t) for ts, h, t in self._recent_copies if ts > cutoff
        ]

    def retroactive_promote(self):
        """Called when the user says 'context' — promotes recent copies that weren't
        auto-classified. Returns the number promoted."""
        with self._lock:
            promoted = 0
            for ts, h, text in list(self._recent_copies):
                # Same length floor as auto-classification (classify_and_store uses
                # >=120) — was 80, which let 80-119 char snippets slip in via the
                # retroactive path and contradicted the "only meaningful replies"
                # design.
                if len(text) >= 120:
                    if self._add_entry(
                            text, source="retroactive", confidence=1):
                        promoted += 1
            self._recent_copies.clear()
            return promoted

    def manual_add(self, text):
        """User explicitly marks text as conversation context (e.g. from the UI)."""
        text = (text or "").strip()
        if text:
            with self._lock:
                return self._add_entry(text, source="manual", confidence=10)
        return False

    # ---- ITEM 5 / refinement pass §6: whole-conversation capture --------------
    # A Select-All on a chat page grabs the WHOLE document — not just the messages,
    # but the surrounding UI chrome (nav, sidebar history, "Copy"/"Regenerate"
    # buttons, the "… can make mistakes" footer). Since a desktop app has no DOM to
    # query, the next best thing to "Ctrl+A but only the conversation" is to STRIP
    # that chrome from the captured text, leaving just the turns. These tables are
    # deliberately tight (exact short UI strings + a few distinctive footer phrases)
    # so a genuine message line is never mistaken for chrome.
    _CHROME_EXACT = {
        "copy", "copy code", "copied", "copied!", "edit", "edited", "regenerate",
        "regenerate response", "share", "delete", "save", "save & submit",
        "cancel", "continue", "continue generating", "stop generating",
        "send a message", "ask anything", "new chat", "new conversation",
        "clear chat", "today", "yesterday", "this week", "previous 7 days",
        "previous 30 days", "upgrade plan", "upgrade to plus", "get plus",
        "menu", "close", "back", "settings", "log out", "sign out", "you",
        "chatgpt", "claude", "gemini", "copilot", "assistant", "good response",
        "bad response", "like", "dislike", "read aloud", "more", "retry",
        "show more", "show less", "thumbs up", "thumbs down", "report",
        "feedback", "open sidebar", "close sidebar", "model", "temporary chat",
        "view plans", "search", "explore gpts", "voice", "attach", "send",
    }
    _CHROME_CONTAINS = (
        "can make mistakes", "check important info", "verify important",
        "free research preview", "message chatgpt", "message claude",
        "press enter to send", "shift + enter", "use shift + return",
        "see cookie preferences", "responses are generated by ai",
    )
    # Per-UI signatures so the capture can report which chat it came from.
    _UI_SIGNATURES = (
        ("ChatGPT", ("chatgpt said", "chatgpt can make mistakes", "message chatgpt")),
        ("Claude", ("claude can make mistakes", "message claude")),
        ("Gemini", ("gemini can make mistakes", "gemini said")),
        ("Copilot", ("microsoft copilot", "copilot can make")),
    )

    @classmethod
    def _clean_chrome(cls, text):
        """Drop pure chat-UI chrome lines from a whole-page capture, keeping the
        actual conversation. Paragraph breaks are preserved; runs of blank lines are
        collapsed. Returns the cleaned text (falls back to the input if everything
        looked like chrome, so a capture is never emptied)."""
        out = []
        for ln in (text or "").split("\n"):
            s = ln.strip()
            low = s.lower()
            if not s:
                out.append("")
                continue
            if low in cls._CHROME_EXACT:
                continue
            if any(p in low for p in cls._CHROME_CONTAINS):
                continue
            out.append(ln)
        cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
        return cleaned or (text or "").strip()

    @classmethod
    def detect_chat_ui(cls, text):
        """Best-effort name of the chat UI a capture came from (ChatGPT/Claude/…),
        or '' when unrecognised. Purely for user feedback — never changes storage."""
        low = (text or "").lower()
        for name, sigs in cls._UI_SIGNATURES:
            if any(s in low for s in sigs):
                return name
        return ""

    def capture_conversation(self, text):
        """Ingest a FULL conversation captured in one shot via Select-All → Copy —
        the ENTIRE thread, including turns scrolled out of view (Select-All reaches
        the whole document, not just the visible viewport). UI chrome is stripped so
        only the messages remain; the transcript is split into User/AI turns and
        stored as a coherent thread; a re-capture REPLACES the previous
        whole-conversation capture (a refresh, not an endless append), while any
        individually-classified replies are left untouched. Returns
        {'turns': n, 'chars': len, 'ui': name}."""
        text = (text or "").strip()
        if not text:
            return {"turns": 0, "chars": 0, "ui": ""}
        ui = self.detect_chat_ui(text)
        text = self._clean_chrome(text)
        turns = self._split_turns(text)
        now = datetime.now()
        with self._lock:
            # Refresh semantics: drop the prior full-conversation capture only.
            kept = [e for e in self.entries if e.get("source") != "conversation"]
            self.entries = deque(kept, maxlen=self.max_entries)
            for role, body in turns:
                body = body.strip()
                if not body:
                    continue
                self.entries.append({
                    "role": role,
                    "time": now.strftime("%H:%M"),
                    "stamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "source": "conversation",
                    "confidence": 10,
                    "text": body,
                    "length": len(body),
                })
            self._save()
        return {"turns": sum(1 for r, b in turns if b.strip()),
                "chars": len(text), "ui": ui}

    # Role markers seen at the start of a line in copied chat transcripts.
    _TURN_MARK = re.compile(
        r'^\s*(you said|user|you|me|prompt|chatgpt said|chatgpt|assistant|claude|'
        r'gemini|copilot|ai|bot)\s*[:\-]\s*', re.I)
    _USER_WORDS = {"you said", "user", "you", "me", "prompt"}

    def _split_turns(self, text):
        """Best-effort split of a pasted chat transcript into (role, body) turns.
        Recognises the common 'You said:' / 'ChatGPT said:' / 'User:' / 'Assistant:'
        line markers most web chats copy. When no markers are found the whole thing
        is kept as ONE 'ai' block — still useful as full-conversation context, never
        dropped. Pure string work (no network)."""
        lines = text.split("\n")
        turns = []
        cur_role, buf = None, []

        def _flush():
            if buf:
                turns.append((cur_role or "ai", "\n".join(buf).strip()))

        for ln in lines:
            m = self._TURN_MARK.match(ln)
            if m:
                _flush()
                buf = []
                word = m.group(1).strip().lower()
                cur_role = "user" if word in self._USER_WORDS else "ai"
                rest = ln[m.end():].strip()
                if rest:
                    buf.append(rest)
            else:
                buf.append(ln)
        _flush()
        # No markers at all → keep the entire capture as one AI-context block.
        if not turns:
            return [("ai", text.strip())]
        return [(r, b) for r, b in turns if b.strip()]

    def _add_entry(self, text, source="manual", confidence=0):
        now = datetime.now()
        entry = {
            "time": now.strftime("%H:%M"),
            "stamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "source": source,
            "confidence": confidence,
            "text": text,
            "length": len(text),
        }
        # Dedup against ALL stored entries, not just the most recent — a
        # retroactive promote can push the prior copy off the tail, so a
        # last-entry-only check would let the same text be stored twice.
        if any(e.get("text") == text for e in self.entries):
            return False
        self.entries.append(entry)
        self._save()
        return True

    def get_context(self, n=3):
        """Return the last N conversation entries as a formatted context string
        with recency weighting:
          - Most recent entry → full text (up to 4000 chars)
          - Older entries    → truncated (up to 1500 chars each)
        Entries are separated by '---' markers so the AI sees them as distinct turns.
        """
        with self._lock:
            items = list(self.entries)[-max(n, 1) :]
        if not items:
            return ""

        parts = []
        for i, item in enumerate(items):
            is_latest = i == len(items) - 1
            txt = item.get("text", "")
            ts = item.get("time", "")
            if is_latest:
                parts.append(f"[Most recent AI reply — {ts}]\n{txt[:4000]}")
            else:
                truncated = txt[:1500]
                if len(txt) > 1500:
                    truncated += "\n…[earlier in conversation]"
                parts.append(f"[Earlier AI reply — {ts}]\n{truncated}")

        return "\n\n---\n\n".join(parts)

    def get_conversation_context(self, n=12):
        """The CAPTURED conversation (Capture chat) formatted for Reply — the last N
        'conversation'-source turns, role-labelled, newest in full. Returns '' when
        no conversation has been captured, so Reply can fall back to the clipboard.
        Distinct from get_thread_context, which mixes in clipboard-classified replies;
        this is ONLY the deliberate whole-conversation capture."""
        with self._lock:
            items = [e for e in self.entries
                     if e.get("source") == "conversation"][-max(n, 1):]
        if not items:
            return ""
        parts = []
        for item in items:
            role = item.get("role", "ai")
            ts = item.get("time", "")
            txt = item.get("text", "")
            label = f"[You — {ts}]" if role == "user" else f"[AI — {ts}]"
            is_latest = item is items[-1]
            display = txt[:2500] if is_latest else txt[:900]
            if len(txt) > len(display):
                display += "\n…[truncated]"
            parts.append(f"{label}\n{display}")
        return "\n\n---\n\n".join(parts)

    def has_conversation(self):
        """True when a whole-conversation capture is currently stored."""
        with self._lock:
            return any(e.get("source") == "conversation" for e in self.entries)

    def get_thread(self):
        """Return the full conversation thread as a list of entry dicts (newest last)."""
        with self._lock:
            return list(self.entries)

    def clear(self):
        with self._lock:
            self.entries.clear()
            self._recent_copies.clear()
            self._save()

    def stats(self):
        with self._lock:
            items = list(self.entries)
        return {
            "total_turns": len(items),
            "auto_classified": sum(1 for e in items if e.get("source") == "auto"),
            "retroactive": sum(1 for e in items if e.get("source") == "retroactive"),
            "manual": sum(1 for e in items if e.get("source") == "manual"),
            "total_chars": sum(e.get("length", 0) for e in items),
            "last_turn": items[-1].get("time") if items else None,
        }

    # ---- Turn logging for AI chat tracking (reply/replyer mode) ----

    def log_user_turn(self, text):
        """Log what the user dictated as a reply. Called after a successful reply
        mode generation so we have a complete turn pair."""
        text = (text or "").strip()
        if not text:
            return False
        now = datetime.now()
        entry = {
            "role": "user",
            "time": now.strftime("%H:%M"),
            "stamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "mumble_reply",
            "confidence": 10,
            "text": text,
            "length": len(text),
        }
        with self._lock:
            if self.entries and self.entries[-1].get("text") == entry["text"]:
                return False
            self.entries.append(entry)
            self._save()
            return True

    def get_thread_context(self, n=3):
        """Return the last N turns formatted as a conversation thread for reply mode.
        Labels each turn by role so the AI can understand who said what."""
        with self._lock:
            items = list(self.entries)[-max(n, 1) :]
        if not items:
            return ""
        parts = []
        for item in items:
            role = item.get("role", "unknown")
            ts = item.get("time", "")
            txt = item.get("text", "")
            if role == "user":
                label = f"[You — {ts}]"
            elif item.get("source") in ("auto", "retroactive", "manual"):
                label = f"[AI reply — {ts}]"
            else:
                label = f"[{role} — {ts}]"
            # Truncate older turns for context window
            is_latest = item is items[-1]
            display = txt[:3000] if is_latest else txt[:1000]
            if len(txt) > len(display):
                display += "\n…[truncated]"
            parts.append(f"{label}\n{display}")
        return "\n\n---\n\n".join(parts)
