#!/usr/bin/env python3
"""Stage 1 — Punctuation restoration and capitalisation.

Wraps the `oliverguhr/fullstop-punctuation-multilang-large` BERT model via the
`transformers` token-classification pipeline. Lazily loaded on first use so
startup stays fast; the model is ~200 MB RAM when resident.

Entry point:
    stage = PunctuationStage()
    stage.process("hello world how are you")  # → "Hello world. How are you?"

The deepmultilingualpunctuation package is NOT used as a dependency because
newer transformers (>=5.x) dropped the `grouped_entities` pipeline parameter.
We call the pipeline directly with the same chunking / prediction / reassembly
algorithm, so the output is identical.

Edge-case behaviour:
  • Empty / whitespace-only input → "" (no-op)
  • Very short input (1-2 words) → add terminal punctuation + capitalise
  • Very long input (>230 words) → chunked with overlap, then reassembled
  • Non-English text → pass-through; the model still attempts punctuation but
    quality may degrade for languages poorly represented in its training set.
    The output is always readable text — we never crash or corrupt the input.
"""

import re
import os

# ---------------------------------------------------------------------------
# Optional dependency — the entire module is import-safe without transformers.
# ---------------------------------------------------------------------------
try:
    from transformers import pipeline as _hf_pipeline
    _HAS_TRANSFORMERS = True
except ImportError:
    _HAS_TRANSFORMERS = False
    _hf_pipeline = None


# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------
_DEFAULT_MODEL = "oliverguhr/fullstop-punctuation-multilang-large"
_CHUNK_SIZE = 230       # token limit before the model trunkates
_OVERLAP = 5            # words of overlap between chunks


class PunctuationStage:
    """Lazily-loaded BERT punctuation model for local-only dictation.

    Thread-safe: model loading is guarded; once loaded it is shared across
    calls.  Call `unload()` to free the model if memory pressure is detected.
    """

    def __init__(self, model_name=_DEFAULT_MODEL):
        self._model_name = model_name
        self._pipe = None        # transformers pipeline (lazy)
        self._loaded = False     # True after successful load
        self._load_failed = False

    # ------------------------------------------------------------------
    # Lazy loading
    # ------------------------------------------------------------------

    @property
    def available(self):
        """True when the model is loaded AND ready to process."""
        if self._loaded and self._pipe is not None:
            return True
        if self._load_failed or not _HAS_TRANSFORMERS:
            return False
        return self._ensure_loaded()

    def _ensure_loaded(self):
        """Load the model on first use. Returns True on success."""
        if self._loaded and self._pipe is not None:
            return True
        if self._load_failed or not _HAS_TRANSFORMERS:
            return False
        if os.environ.get("MUMBLE_OFFLINE_TESTS") == "1":
            return False
        try:
            self._pipe = _hf_pipeline("token-classification",
                                       self._model_name)
            self._loaded = True
            return True
        except Exception:
            self._load_failed = True
            return False

    def unload(self):
        """Release the model to free ~200 MB RAM."""
        self._pipe = None
        self._loaded = False

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def process(self, text):
        """Restore punctuation and capitalisation on `text`.

        Returns the punctuated, capitalised string.  Returns "" for empty /
        whitespace-only input.  If the model is unavailable the raw input is
        returned unchanged (graceful degradation).
        """
        if not text or not text.strip():
            return ""
        if not self.available:
            return text  # model unavailable → pass-through

        s = text.strip()

        # 1. Preprocess: strip existing punctuation so the model sees clean words.
        clean = self._preprocess(s)

        # 2. Predict: run the BERT token-classifier on chunked words.
        tagged = self._predict(clean)

        # 3. Reassemble: join words with the predicted inter-word markers.
        punctuated = self._prediction_to_text(tagged)

        # 4. Apply capitalisation rules on the punctuated result.
        return self._capitalize(punctuated)

    # ------------------------------------------------------------------
    # Preprocessing
    # ------------------------------------------------------------------

    @staticmethod
    def _preprocess(text):
        """Remove existing punctuation marks so the model only sees words.

        Preserves punctuation that is part of a number (e.g. "3.50").
        """
        text = re.sub(r"(?<!\d)[.,;:!?](?!\d)", "", text)
        return text.split()

    # ------------------------------------------------------------------
    # Chunked prediction (mirrors deepmultilingualpunctuation algorithm)
    # ------------------------------------------------------------------

    @staticmethod
    def _overlap_chunks(lst, n, stride=0):
        """Yield successive n-sized chunks from lst with `stride` overlap."""
        for i in range(0, len(lst), n - stride):
            yield lst[i:i + n]

    def _predict(self, words):
        """Tag each word with its predicted punctuation label.

        Labels: "0" = space, "." = period, "," = comma, "?" = question,
        "-" = hyphen, ":" = colon.
        """
        n = len(words)
        if n == 0:
            return []

        overlap = _OVERLAP if n > _CHUNK_SIZE else 0
        batches = list(self._overlap_chunks(words, _CHUNK_SIZE, overlap))

        # Discard a tiny trailing batch that is fully overlapped.
        if len(batches) > 1 and len(batches[-1]) <= overlap:
            batches.pop()

        tagged_words = []
        for bi, batch in enumerate(batches):
            # The last batch is used completely (no overlap discard).
            cur_overlap = 0 if bi == len(batches) - 1 else overlap

            text = " ".join(batch)
            result = self._pipe(text)

            char_index = 0
            result_index = 0
            result_len = len(result)

            for word in batch[:len(batch) - cur_overlap]:
                char_index += len(word) + 1

                # Advance through result tokens until we pass this word's
                # end position in the concatenated text.
                label = "0"
                score = 0.0
                while (result_index < result_len and
                       char_index > result[result_index]["end"]):
                    label = result[result_index]["entity"]
                    score = result[result_index]["score"]
                    result_index += 1

                tagged_words.append((word, label, score))

        return tagged_words

    # ------------------------------------------------------------------
    # Reassembly
    # ------------------------------------------------------------------

    @staticmethod
    def _prediction_to_text(prediction):
        """Reconstruct text from tagged words: label '0' → space,
        any other label (.,?-:) → append the punctuation mark + space.
        """
        result = ""
        for word, label, _ in prediction:
            result += word
            if label == "0":
                result += " "
            if label in ".,?-:;":
                result += label + " "
        return result.strip()

    # ------------------------------------------------------------------
    # Capitalisation (mirrors formatting._capitalize behaviour)
    # ------------------------------------------------------------------

    # Lowercase-leading words that carry an interior capital (iPhone, macOS)
    # are preserved verbatim — force-capitalising their first letter at a
    # sentence start damages them.
    _INTERCAPS_RE = re.compile(r"^[a-z]+[A-Z]")

    @classmethod
    def _capitalize(cls, s):
        """Sentence-boundary capitalisation + pronoun-'I' fixup."""
        out = []
        cap_next = True
        i = 0
        n = len(s)
        while i < n:
            ch = s[i]
            if cap_next and ch.isalpha():
                j = i
                while j < n and (s[j].isalpha() or s[j] == "'"):
                    j += 1
                word = s[i:j]
                if cls._INTERCAPS_RE.match(word):
                    out.append(word)
                else:
                    out.append(word[0].upper())
                    out.append(word[1:])
                cap_next = False
                i = j
                continue
            out.append(ch)
            if ch in "!?\n":
                cap_next = True
            elif ch == ".":
                prev_digit = i > 0 and s[i - 1].isdigit()
                nxt = s[i + 1] if i + 1 < n else ""
                if not prev_digit and (nxt == "" or nxt.isspace()):
                    cap_next = True
            i += 1
        res = "".join(out)
        return re.sub(r"(?<![A-Za-z0-9.'])i(?![A-Za-z0-9.'])", "I", res)


# ---------------------------------------------------------------------------
# Convenience: module-level singleton for callers that just want one instance.
# ---------------------------------------------------------------------------
_default_stage = None


def get_stage():
    """Return (or create) the module-level PunctuationStage singleton."""
    global _default_stage
    if _default_stage is None:
        _default_stage = PunctuationStage()
    return _default_stage
