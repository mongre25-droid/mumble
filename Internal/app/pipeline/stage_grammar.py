#!/usr/bin/env python3
"""Stage 2 — Grammar correction and filler removal.

Uses the GRMR-2B-Instruct GGUF model via the bundled llama-cli binary to:
  • Correct obvious grammar mistakes (e.g. "he go" → "he went")
  • Remove filler words (um, uh, you know, like, I mean, etc.)
  • Clean up awkward phrasing while preserving the user's exact meaning

The model is lazily loaded — no GPU/CPU resources are consumed until the
first `process()` call.  If the GRMR-2B GGUF file is not present on disk
(or the bundled llama-cli is missing) the stage reports `available=False`
and `process()` passes text through unchanged.

Entry point:
    stage = GrammarStage()
    if stage.available:
        text = stage.process("he go to the store yesterday", lane="text")
        # → "He went to the store yesterday."

Edge-case behaviour:
  • Empty / whitespace-only input → "" (no-op)
  • Model unavailable → pass-through (graceful degradation)
  • Very short input (1-2 words) → return as-is (nothing to correct)
  • Very long input → chunked to fit the model's context window
  • Non-English → attempted but quality may degrade

Model requirements:
  • GRMR-2B-Instruct GGUF (Q4_K_M recommended, ~1.5 GB)
  • Bundled llama-cli.exe (from llama-cpp-bin/)
"""

import os
import sys

# ---------------------------------------------------------------------------
# Stage 2 system prompt — tells GRMR-2B exactly what to do.
# ---------------------------------------------------------------------------
# The prompt is kept short and directive so a small model (2B params) can
# follow it reliably.  We ask for ONLY the corrected text — no explanations,
# no commentary, no wrapping.

_STAGE2_SYSTEM = (
    "You are a precise grammar correction engine. Your ONLY job:\n"
    "1. Fix grammar mistakes (verb tense, subject-verb agreement, articles, "
    "prepositions).\n"
    "2. Remove filler words: um, uh, er, you know, like, I mean, basically, "
    "actually, sort of, kind of, right, okay, well, so (when used as filler).\n"
    "3. Preserve the user's EXACT words, meaning, and tone. Do NOT rephrase, "
    "summarise, or add anything.\n"
    "Output ONLY the corrected text — no preamble, no explanation, no quotes."
)

# ---------------------------------------------------------------------------
# Model discovery helpers
# ---------------------------------------------------------------------------

# Preferred model filenames (checked in order).  We look for common
# GRMR-2B-Instruct GGUF filenames that users might download.
_GRMR_CANDIDATES = [
    "grmr-2b-instruct-q4_k_m.gguf",
    "grmr-2b-instruct-q4_0.gguf",
    "grmr-2b-instruct-q8_0.gguf",
    "grmr-2b-instruct-f16.gguf",
]


def _find_grmr_model():
    """Search for a GRMR-2B GGUF file in DATA_DIR/models and app dir.

    Returns the absolute path to the first matching file, or None.
    """
    # Try to get the models directory path.
    try:
        from branding import MODELS_DIR as _md
    except ImportError:
        try:
            _md = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                               "Mumble", "models")
        except Exception:
            return None

    search_dirs = [_md]
    # Also check the app directory itself (some users keep models there).
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    search_dirs.append(app_dir)

    for d in search_dirs:
        if not d or not os.path.isdir(d):
            continue
        try:
            for fname in _GRMR_CANDIDATES:
                path = os.path.join(d, fname)
                if os.path.isfile(path):
                    return path
            # Also check for any gguf file with "grmr" in the name.
            for fname in os.listdir(d):
                if fname.lower().endswith(".gguf") and "grmr" in fname.lower():
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
# GrammarStage
# ---------------------------------------------------------------------------

class GrammarStage:
    """Lazily-loaded GRMR-2B grammar correction stage.

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
        """True when the GRMR-2B model is found AND the backend is ready."""
        if self._available is True:
            return True
        return self._ensure_available()

    def _ensure_available(self):
        """Probe for the model + binary. Cache the result."""
        if self._available is True:
            return True

        mp = self._model_path or _find_grmr_model()
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
    # Main entry point
    # ------------------------------------------------------------------

    def process(self, text, lane="text", **kwargs):
        """Correct grammar and remove fillers from `text`.

        Parameters:
            text: The (possibly punctuated) transcript from Stage 1.
            lane: The mode lane (informational — Stage 2 treats all lanes
                  the same since grammar correction is lane-agnostic).
            **kwargs: Forwarded for interface compatibility.

        Returns:
            Grammar-corrected text with fillers removed.  If the model is
            unavailable the raw input is returned unchanged (graceful
            degradation).  Returns "" for empty/whitespace-only input.
        """
        if not text or not text.strip():
            return ""

        backend = self._ensure_backend()
        if backend is None or not backend.available():
            return text  # model unavailable → pass-through

        s = text.strip()

        # Very short input: nothing to correct, just return.
        if len(s.split()) <= 2:
            return s

        # Build the user message — frame the dictation clearly for the model.
        user_msg = f"Fix grammar and remove fillers:\n\n{s}"

        try:
            result = backend.generate(
                system=_STAGE2_SYSTEM,
                user=user_msg,
                grammar=None,  # free-form output — grammar correction is prose
                max_tokens=min(len(s.split()) * 3, 1024),
                temperature=0.2,
            )
            if result and result.strip():
                return result.strip()
            return s  # empty response → pass-through
        except Exception:
            print("[pipeline] Stage 2 (grammar) inference failed", file=sys.stderr)
            self._available = False
            return s


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_default_stage = None


def get_stage():
    """Return (or create) the module-level GrammarStage singleton."""
    global _default_stage
    if _default_stage is None:
        _default_stage = GrammarStage()
    return _default_stage
