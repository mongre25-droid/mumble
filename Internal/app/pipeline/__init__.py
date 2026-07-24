#!/usr/bin/env python3
"""Mumble local post-processing pipeline.

Four stages, executed in fixed order:

    Stage 1  →  Stage 2  →  Stage 3  →  Stage 4
  (punctuation)  (grammar)   (formatting)  (cleanup)
     BERT          GGUF         GGUF         rules
     ~200MB       ~1.5GB*      ~1.3GB*      0MB

* Stages 2 and 3 are optional — the pipeline degrades gracefully when their
  models are unavailable.  Stage 4 is always available (stage_cleanup.py wrapping
  model_free.py).

Each stage exposes a `process(text, **kwargs)` → str interface and an
`available` property.  The orchestrator chains them in order, skipping
any stage whose model is not ready.

Entry point for callers:
    from pipeline import PipelineOrchestrator, process

    orchestrator = PipelineOrchestrator()
    result = orchestrator.process("hello world", lane="text")
    # → "Hello world."

Graceful degradation:
    • Missing Stage 1 (punctuation model failed) → skip, raw text passes through
    • Missing Stage 2/3 (no GGUF models)        → skip, output degrades
    • Only Stage 4 available                     → model_free surface cleanup
    • Empty input                                → "" (no-op)
    • Very short/long text                       → handled by each stage
    • Non-English                                → pass-through (stages attempt
                                                    but don't corrupt)

Cloud supremacy is enforced OUTSIDE the pipeline — the router in
local_engine.py decides whether to call the pipeline at all.  When a cloud
key is present the pipeline is bypassed entirely.
"""

import sys

# ---------------------------------------------------------------------------
# Stage 1 — punctuation restoration (BERT, lazy-loaded)
# ---------------------------------------------------------------------------
_HAVE_STAGE1 = False
_PunctuationStage = None

try:
    from .stage_punctuation import PunctuationStage as _PunctuationStage
    _HAVE_STAGE1 = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Stage 2 & 3 — grammar + formatting (GGUF via llama-cli)
# These are created by a later feature (pipeline-stage2-stage3).  We reserve
# the import slots now so the orchestrator can detect their absence and skip.
# ---------------------------------------------------------------------------
_HAVE_STAGE2 = False
_HAVE_STAGE3 = False
_GrammarStage = None
_FormattingStage = None

try:
    from .stage_grammar import GrammarStage as _GrammarStage
    _HAVE_STAGE2 = True
except ImportError:
    pass

try:
    from .stage_formatting import FormattingStage as _FormattingStage
    _HAVE_STAGE3 = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Stage 4 — surface cleanup (always available via model_free.py)
# ---------------------------------------------------------------------------
_HAVE_STAGE4 = False
_CleanupStage = None

try:
    from .stage_cleanup import CleanupStage as _CleanupStage
    _HAVE_STAGE4 = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

# Import UPGRADE_NOTICE for degradation notices.
_UPGRADE_NOTICE = None

try:
    from local_engine import UPGRADE_NOTICE as _UN
    _UPGRADE_NOTICE = _UN
except ImportError:
    _UPGRADE_NOTICE = (
        "For enhanced results on this task, enable AI Pro Mode or "
        "connect a cloud API key."
    )


class PipelineOrchestrator:
    """Manages stage sequencing with graceful degradation.

    Stages are discovered at construction time (lazy).  Each call to
    `process()` resolves available stages and chains them in fixed order:
    1 → 2 → 3 → 4.

    Degradation tracking: each call to `process()` records which stages
    were skipped, failed, or produced malformed output.  The
    `last_degradation` property provides a summary for callers that want
    to append an UPGRADE_NOTICE or surface degradation to the user.
    """

    def __init__(self):
        # Stage instances (None until first use)
        self._stage1 = None
        self._stage2 = None
        self._stage3 = None
        self._stage4 = None

        # Stage availability flags
        self._s1_available = _HAVE_STAGE1
        self._s2_available = _HAVE_STAGE2
        self._s3_available = _HAVE_STAGE3
        # Stage 4 is always available (model_free.py wrapper)
        self._s4_available = _HAVE_STAGE4

        # Degradation tracking for the most recent process() call.
        self._degradation_log = []  # list of (stage_num, event, detail) tuples
        self._upstream_output = None  # saved for malformed-output fallback
        self._config = None  # snapshot of config at process() start

    # ------------------------------------------------------------------
    # Stage accessors (lazy init)
    # ------------------------------------------------------------------

    @property
    def stage1(self):
        if self._stage1 is None and _HAVE_STAGE1:
            try:
                self._stage1 = _PunctuationStage()
            except Exception:
                self._s1_available = False
                self._stage1 = None
        return self._stage1

    @property
    def stage2(self):
        if self._stage2 is None and _HAVE_STAGE2:
            try:
                self._stage2 = _GrammarStage()
            except Exception:
                self._s2_available = False
                self._stage2 = None
        return self._stage2

    @property
    def stage3(self):
        if self._stage3 is None and _HAVE_STAGE3:
            try:
                self._stage3 = _FormattingStage()
            except Exception:
                self._s3_available = False
                self._stage3 = None
        return self._stage3

    @property
    def stage4(self):
        if self._stage4 is None and _HAVE_STAGE4:
            try:
                self._stage4 = _CleanupStage()
            except Exception:
                self._s4_available = False
                self._stage4 = None
        return self._stage4

    # ------------------------------------------------------------------
    # Availability introspection
    # ------------------------------------------------------------------

    def available_stages(self):
        """Return a list of stage numbers that are currently available."""
        stages = []
        if self._s1_available and self.stage1 is not None and self.stage1.available:
            stages.append(1)
        if self._s2_available and self.stage2 is not None and self.stage2.available:
            stages.append(2)
        if self._s3_available and self.stage3 is not None and self.stage3.available:
            stages.append(3)
        if self._s4_available:
            s4 = self.stage4
            if s4 is not None and s4.available:
                stages.append(4)
        return stages

    def stage_labels(self):
        """Human-readable labels for available stages."""
        labels = []
        if 1 in self.available_stages():
            labels.append("punctuation")
        if 2 in self.available_stages():
            labels.append("grammar")
        if 3 in self.available_stages():
            labels.append("formatting")
        if 4 in self.available_stages():
            labels.append("cleanup")
        return labels

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def process(self, text, lane="text", config=None, **kwargs):
        """Run the pipeline on `text` through all available stages.

        Parameters:
            text: The raw transcript to process.
            lane: The mode lane ("text", "prompt", "email", "reply", "foreign").
            config: Optional dict of configuration to snapshot for this run.
                    When provided, changes to settings during the run won't
                    affect this pipeline invocation (VAL-CROSS-020).
            profiler: Optional InferenceProfiler instance.  When provided
                    and enabled, each stage's execution is timed and
                    resource usage is recorded.  Pass as a keyword argument.
            **kwargs: Forwarded to each stage (future extension).

        Returns:
            The processed text after all available stages have been applied.
            Returns "" for empty/whitespace-only input.

        Stage ordering is FIXED: 1 → 2 → 3 → 4.  If a stage is unavailable
        it is silently skipped.  If a stage produces malformed output (empty
        or garbled), the pipeline falls back to the previous stage's output.
        If NO stages are available the raw input is returned unchanged.

        Degradation log lines are printed to stderr so integrators can see
        what happened without polluting the output stream.

        After calling process(), inspect `last_degradation` to see which
        stages were skipped, failed, or produced malformed output, and
        whether an UPGRADE_NOTICE should be appended to the result.
        """
        # Snapshot config at process start for isolation (VAL-CROSS-020).
        self._config = dict(config) if config else None

        # Profiler integration (opt-in, zero overhead when absent).
        profiler = kwargs.pop("profiler", None)
        _profile = (
            profiler is not None
            and getattr(profiler, "enabled", False)
        )

        # Reset degradation log for this run.
        self._degradation_log = []
        self._upstream_output = None

        if not text or not text.strip():
            return ""

        result = text.strip()

        # ---- Stage 1: punctuation + capitalisation (BERT) ----
        if self._s1_available:
            s1 = self.stage1
            if s1 is not None and s1.available:
                try:
                    if _profile:
                        with profiler.stage("pipeline_stage1_punctuation"):
                            stage_out = s1.process(result)
                    else:
                        stage_out = s1.process(result)
                    if self._is_output_usable(stage_out, result):
                        self._upstream_output = result
                        result = stage_out
                    else:
                        self._degradation_log.append(
                            (1, "malformed", "Stage 1 output unusable")
                        )
                        print("[pipeline] Stage 1 produced malformed output "
                              "— falling back to raw input", file=sys.stderr)
                except Exception:
                    self._degradation_log.append(
                        (1, "failed", "Stage 1 exception")
                    )
                    print("[pipeline] Stage 1 (punctuation) failed — skipped",
                          file=sys.stderr)
            else:
                self._degradation_log.append(
                    (1, "unavailable", "Stage 1 model not loaded")
                )
                print("[pipeline] Stage 1 (punctuation) unavailable — skipped",
                      file=sys.stderr)

        # ---- Stage 2: grammar correction + filler removal (GGUF) ----
        if self._s2_available:
            s2 = self.stage2
            if s2 is not None and s2.available:
                try:
                    if _profile:
                        with profiler.stage("pipeline_stage2_grammar"):
                            stage_out = s2.process(result, lane=lane)
                    else:
                        stage_out = s2.process(result, lane=lane)
                    if self._is_output_usable(stage_out, result):
                        self._upstream_output = result
                        result = stage_out
                    else:
                        self._degradation_log.append(
                            (2, "malformed", "Stage 2 output unusable")
                        )
                        print("[pipeline] Stage 2 produced malformed output "
                              "— falling back to Stage 1 output",
                              file=sys.stderr)
                except Exception:
                    self._degradation_log.append(
                        (2, "failed", "Stage 2 exception/crash")
                    )
                    self._s2_available = False
                    print("[pipeline] Stage 2 (grammar) failed — skipped",
                          file=sys.stderr)
            else:
                self._degradation_log.append(
                    (2, "unavailable", "Stage 2 model not found")
                )

        # ---- Stage 3: mode formatting (GGUF + GBNF) ----
        if self._s3_available:
            s3 = self.stage3
            if s3 is not None and s3.available:
                try:
                    if _profile:
                        with profiler.stage("pipeline_stage3_formatting"):
                            stage_out = s3.process(result, lane=lane)
                    else:
                        stage_out = s3.process(result, lane=lane)
                    if self._is_output_usable(stage_out, result):
                        self._upstream_output = result
                        result = stage_out
                    else:
                        self._degradation_log.append(
                            (3, "malformed", "Stage 3 output unusable")
                        )
                        print("[pipeline] Stage 3 produced malformed output "
                              "— falling back to Stage 2 output",
                              file=sys.stderr)
                except Exception:
                    self._degradation_log.append(
                        (3, "failed", "Stage 3 exception/crash")
                    )
                    self._s3_available = False
                    print("[pipeline] Stage 3 (formatting) failed — skipped",
                          file=sys.stderr)
            else:
                self._degradation_log.append(
                    (3, "unavailable", "Stage 3 model not found")
                )

        # ---- Stage 4: surface cleanup (always available) ----
        if self._s4_available:
            s4 = self.stage4
            if s4 is not None and s4.available:
                try:
                    if _profile:
                        with profiler.stage("pipeline_stage4_cleanup"):
                            stage_out = s4.process(result, lane=lane)
                    else:
                        stage_out = s4.process(result, lane=lane)
                    if self._is_output_usable(stage_out, result):
                        result = stage_out
                    else:
                        self._degradation_log.append(
                            (4, "malformed", "Stage 4 output unusable")
                        )
                        print("[pipeline] Stage 4 produced malformed output "
                              "— falling back to upstream output",
                              file=sys.stderr)
                except Exception:
                    self._degradation_log.append(
                        (4, "failed", "Stage 4 exception")
                    )
                    print("[pipeline] Stage 4 (cleanup) failed "
                          "— returning raw output", file=sys.stderr)
            else:
                self._degradation_log.append(
                    (4, "unavailable", "Stage 4 unavailable")
                )
                print("[pipeline] Stage 4 (cleanup) unavailable "
                      "— returning raw output", file=sys.stderr)

        # ---- Append UPGRADE_NOTICE if degraded ----
        if self.is_degraded and _UPGRADE_NOTICE:
            result = self._append_notice(result)

        return result

    # ------------------------------------------------------------------
    # Degradation tracking
    # ------------------------------------------------------------------

    @property
    def last_degradation(self):
        """Return a summary dict of degradation events from the most recent
        process() call, or None if no process() call has been made yet.

        Keys:
            degraded: bool — whether any stage was skipped, failed, or
                       produced malformed output
            events: list of (stage_num, event_type, detail) tuples
            should_notify: bool — whether an UPGRADE_NOTICE should be shown
        """
        if not self._degradation_log:
            return None
        return {
            "degraded": self.is_degraded,
            "events": list(self._degradation_log),
            "should_notify": self.is_degraded,
        }

    @property
    def is_degraded(self):
        """True when the most recent process() call was degraded.

        Degradation means at least one Stage 2 or Stage 3 was unavailable,
        failed, or produced malformed output.  Stage 1 and Stage 4 failures
        also count as degradation since they reduce output quality.
        """
        return len(self._degradation_log) > 0

    @staticmethod
    def _is_output_usable(output, upstream):
        """Check whether a stage's output is usable (not malformed).

        An output is considered malformed if:
          • It's empty or whitespace-only when the upstream was non-empty
          • It's drastically shorter than the upstream (< 10% of original
            character count for inputs > 20 chars) — suggests the model
            crashed mid-generation or returned a hallucinated summary
        """
        if not output or not output.strip():
            return False
        out_len = len(output.strip())
        up_len = len(upstream.strip()) if upstream else 0
        # If upstream has meaningful content (> 20 chars) and the output
        # is drastically shorter, it's probably malformed.
        if up_len > 20 and out_len < up_len * 0.1:
            return False
        # If output is just a single repeated character (garbled),
        # that's also malformed.
        if out_len > 3 and len(set(output.strip())) == 1:
            return False
        return True

    # ------------------------------------------------------------------
    # Degradation helpers
    # ------------------------------------------------------------------

    def degrade_to_stage4(self, text, mode=None):
        """Bypass all model stages and run Stage 4 only.

        Used when the pipeline detects that all LLM stages and the
        punctuation model have failed — this is the ultimate fallback.
        """
        self._degradation_log = []
        self._upstream_output = None
        if not text or not text.strip():
            return ""
        s4 = self.stage4
        if s4 is not None and s4.available:
            try:
                return s4.process(text, lane=mode)
            except Exception:
                pass
        # Absolute last resort: return text as-is.
        return text

    def _append_notice(self, result):
        """Append the UPGRADE_NOTICE to degraded output.

        The notice is separated by a blank line from the main output so
        it is visually distinct.  It is only appended when the pipeline
        has degraded (some stages were unavailable).
        """
        if not _UPGRADE_NOTICE:
            return result
        if not result or not result.strip():
            return result
        # Don't double-append.
        if _UPGRADE_NOTICE in result:
            return result
        return result + "\n\n" + _UPGRADE_NOTICE

    def process_with_notice(self, text, lane="text", **kwargs):
        """Run the pipeline and return (result, should_append_notice).

        This is a convenience for callers that want to add their own
        framing around the UPGRADE_NOTICE (e.g. a visual indicator in the
        UI) rather than having it appended to the text automatically.

        Returns:
            (result: str, degraded: bool) tuple.
        """
        result = self.process(text, lane=lane, **kwargs)
        return result, self.is_degraded

    # ------------------------------------------------------------------
    # Memory management
    # ------------------------------------------------------------------

    def unload_all(self):
        """Release all loaded models to free memory."""
        for stage_attr in ("_stage1", "_stage2", "_stage3", "_stage4"):
            stage = getattr(self, stage_attr, None)
            if stage is not None and hasattr(stage, "unload"):
                try:
                    stage.unload()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_default_orchestrator = None


def get_orchestrator():
    """Return (or create) the module-level PipelineOrchestrator singleton."""
    global _default_orchestrator
    if _default_orchestrator is None:
        _default_orchestrator = PipelineOrchestrator()
    return _default_orchestrator


def process(text, lane="text", **kwargs):
    """Convenience: run the default pipeline on `text`.

    Equivalent to:
        from pipeline import get_orchestrator
        return get_orchestrator().process(text, lane=lane)
    """
    return get_orchestrator().process(text, lane=lane, **kwargs)
