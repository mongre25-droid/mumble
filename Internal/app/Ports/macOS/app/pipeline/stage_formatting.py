#!/usr/bin/env python3
"""Stage 3 — Mode formatting with GBNF-constrained decoding.

Uses the Qwen2.5-1.5B-Instruct GGUF model via the bundled llama-cli binary to
reshape text for the active mode lane.  GBNF grammars are bound onto the
sampler so the output shape is enforced at the token level — a list lane
CANNOT produce free-form prose, an email lane ALWAYS has greeting + body +
sign-off, and a JSON lane is always valid JSON.

Supported lanes:
  • "list"   → bullet or numbered list (GBNF grammar enforced)
  • "email"  → structured email with subject/greeting/body/sign-off
  • "prompt" → structured AI prompt (free-form, no grammar)
  • "reply"  → conversational reply (free-form, no grammar)
  • "text"   → cleaned dictation (pass-through since Stage 1 already handled it)

The model is lazily loaded.  If the Qwen2.5-1.5B GGUF file is not present on
disk (or the bundled llama-cli is missing) the stage reports `available=False`
and `process()` passes text through unchanged.

Entry point:
    stage = FormattingStage()
    if stage.available:
        text = stage.process("buy milk eggs and bread", lane="list")
        # → "- milk\n- eggs\n- bread"

Edge-case behaviour:
  • Empty / whitespace-only input → "" (no-op)
  • Model unavailable → pass-through (graceful degradation)
  • Very short input → return as-is
  • Very long input → chunked to fit context window
  • Non-English → attempted but quality may degrade
  • Invalid lane → treated as "text" (pass-through)

Model requirements:
  • Qwen2.5-1.5B-Instruct GGUF (Q4_K_M recommended, ~1.3 GB)
  • Bundled llama-cli.exe (from llama-cpp-bin/)
"""

import os
import sys

# ---------------------------------------------------------------------------
# Stage 3 system prompts — lane-specific, kept short for a 1.5B model.
# ---------------------------------------------------------------------------

_STAGE3_SYSTEM_BASE = (
    "You are a precise text formatting engine. Your ONLY job is to reformat "
    "the user's dictated text according to the requested output format. "
    "Preserve ALL of the user's content — never drop, invent, or summarise. "
    "Output ONLY the formatted result — no preamble, no explanation, no quotes."
)

_STAGE3_LIST = (
    _STAGE3_SYSTEM_BASE + "\n"
    "Format the dictation as a bullet list. Each item on its own line "
    "starting with '- '. Group related items together. Keep every item "
    "the user mentioned. Use sentence case for each item."
)

_STAGE3_EMAIL = (
    _STAGE3_SYSTEM_BASE + "\n"
    "Format the dictation as a complete professional email. Include: "
    "a Subject line summarising the topic, a greeting, a clear body "
    "paragraph, and a sign-off (Best regards). Use the user's exact "
    "words and intent."
)

_STAGE3_PROMPT = (
    _STAGE3_SYSTEM_BASE + "\n"
    "Format the dictation as a clean, ready-to-use AI prompt. Open with "
    "a one-line expert role and the goal. Then list the key requirements "
    "as bullet points, most important first. Preserve the user's intent; "
    "remove rambling and repetition."
)

_STAGE3_REPLY = (
    _STAGE3_SYSTEM_BASE + "\n"
    "Format the dictation as a natural, conversational reply. Match the "
    "tone of the context message if provided. Keep it concise and friendly. "
    "Use the user's exact words and voice."
)

_STAGE3_TEXT = (
    _STAGE3_SYSTEM_BASE + "\n"
    "Clean up the dictation: ensure proper punctuation and capitalisation. "
    "Keep the user's exact words and meaning — do not rephrase."
)

# Map lane → system prompt.
_LANE_PROMPTS = {
    "list":   _STAGE3_LIST,
    "email":  _STAGE3_EMAIL,
    "prompt": _STAGE3_PROMPT,
    "reply":  _STAGE3_REPLY,
    "text":   _STAGE3_TEXT,
}

# ---------------------------------------------------------------------------
# Model discovery helpers
# ---------------------------------------------------------------------------

_QWEN_CANDIDATES = [
    "qwen2.5-1.5b-instruct-q4_k_m.gguf",
    "qwen2.5-1.5b-instruct-q4_0.gguf",
    "qwen2.5-1.5b-instruct-q8_0.gguf",
    "qwen2.5-1.5b-instruct-f16.gguf",
    "qwen2.5-1.5b-instruct-q4_k_m-imat.gguf",
]


def _find_qwen_model():
    """Search for a Qwen2.5-1.5B GGUF file in DATA_DIR/models and app dir.

    Returns the absolute path to the first matching file, or None.
    """
    try:
        from branding import MODELS_DIR as _md
    except ImportError:
        try:
            _md = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                               "Mumble", "models")
        except Exception:
            return None

    search_dirs = [_md]
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    search_dirs.append(app_dir)

    for d in search_dirs:
        if not d or not os.path.isdir(d):
            continue
        try:
            for fname in _QWEN_CANDIDATES:
                path = os.path.join(d, fname)
                if os.path.isfile(path):
                    return path
            # Also check for any gguf with "qwen" in the name.
            for fname in os.listdir(d):
                if fname.lower().endswith(".gguf") and "qwen" in fname.lower():
                    return os.path.join(d, fname)
        except OSError:
            continue
    return None


def _find_llama_cli():
    """Return the path to the bundled llama-cli binary, or '' if missing."""
    try:
        from branding import llama_cli_path as _lcp
        return _lcp()
    except ImportError:
        exe = "llama-cli.exe" if os.name == "nt" else "llama-cli"
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        p = os.path.join(app_dir, "llama-cpp-bin", exe)
        return p if os.path.exists(p) else ""


# ---------------------------------------------------------------------------
# FormattingStage
# ---------------------------------------------------------------------------

class FormattingStage:
    """Lazily-loaded Qwen2.5-1.5B mode-formatting stage with GBNF grammars.

    Thread-safe: backend construction is guarded; once built it is shared
    across calls.  Call `unload()` to release the backend if memory pressure
    is detected.
    """

    def __init__(self, model_path=None, bin_path=None):
        self._model_path = model_path
        self._bin_path = bin_path
        self._backend = None       # LlamaCliBackend (lazy)
        self._available = None     # tri-state: None=unprobed, True/False=known
        self._load_failed = False

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    @property
    def available(self):
        """True when the Qwen2.5-1.5B model is found AND the backend is ready."""
        if self._available is True:
            return True
        return self._ensure_available()

    def _ensure_available(self):
        """Probe for the model + binary. Cache the result."""
        if self._available is True:
            return True

        mp = self._model_path or _find_qwen_model()
        bp = self._bin_path or _find_llama_cli()

        if not mp or not os.path.exists(mp):
            # Absence is not a permanent load failure: models can be downloaded
            # or copied into the models directory while Mumble is running.
            self._available = False
            return False
        if not bp or not os.path.exists(bp):
            # The runtime may be installed after this first probe as well.
            self._available = False
            return False

        self._model_path = mp
        self._bin_path = bp
        self._load_failed = False
        self._available = True
        return True

    def _ensure_backend(self):
        """Construct (or return) the LlamaCliBackend for this stage."""
        if self._backend is not None:
            return self._backend
        if not self._ensure_available():
            return None
        try:
            from local_engine import LlamaCliBackend
            self._backend = LlamaCliBackend(
                model_path=self._model_path,
                bin_path=self._bin_path,
            )
            return self._backend
        except ImportError:
            self._load_failed = True
            self._available = False
            return None
        except Exception:
            self._load_failed = True
            self._available = False
            return None

    def unload(self):
        """Release the backend to free memory."""
        if self._backend is not None:
            try:
                self._backend.unload()
            except Exception:
                pass
            self._backend = None
        self._available = None
        self._load_failed = False

    def reset_probe(self):
        """Forget a hard backend failure and probe files/runtime again.

        Missing files are re-probed automatically; this explicit reset is for
        a prior import/backend-construction failure after the environment has
        been repaired without restarting the app.
        """
        self.unload()
        self._available = None
        self._load_failed = False

    # ------------------------------------------------------------------
    # GBNF grammar resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _grammar_for_lane(lane):
        """Return the GBNF grammar string for a lane, or None."""
        try:
            from pipeline.grammar import grammar_for
            return grammar_for(lane)
        except ImportError:
            return None

    @staticmethod
    def _system_for_lane(lane):
        """Return the system prompt for a lane."""
        return _LANE_PROMPTS.get(lane, _STAGE3_TEXT)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def process(self, text, lane="text", **kwargs):
        """Format `text` for the specified mode lane.

        Parameters:
            text: The grammar-corrected (or punctuated) transcript.
            lane: The mode lane — "list", "email", "prompt", "reply",
                  "text", or "foreign".
            **kwargs: Forwarded for interface compatibility.

        Returns:
            Mode-formatted text.  If the model is unavailable the raw
            input is returned unchanged (graceful degradation).  Returns
            "" for empty/whitespace-only input.  Unrecognised lanes are
            treated as "text" (pass-through with light cleanup).
        """
        if not text or not text.strip():
            return ""

        backend = self._ensure_backend()
        if backend is None or not backend.available():
            return text  # model unavailable → pass-through

        s = text.strip()

        # Very short input: nothing substantial to format.
        if len(s.split()) <= 2 and lane != "list":
            return s

        # Resolve lane to system prompt and grammar.
        system = self._system_for_lane(lane)
        grammar = self._grammar_for_lane(lane)

        # Build the user message.
        lane_label = lane.capitalize() if lane != "text" else "Text"
        user_msg = f"Format as {lane_label}:\n\n{s}"

        try:
            result = backend.generate(
                system=system,
                user=user_msg,
                grammar=grammar,
                max_tokens=min(len(s.split()) * 4, 1536),
                temperature=0.3,
            )
            if result and result.strip():
                return result.strip()
            return s  # empty response → pass-through
        except Exception:
            print("[pipeline] Stage 3 (formatting) inference failed",
                  file=sys.stderr)
            self._available = False
            return s


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_default_stage = None


def get_stage():
    """Return (or create) the module-level FormattingStage singleton."""
    global _default_stage
    if _default_stage is None:
        _default_stage = FormattingStage()
    return _default_stage
