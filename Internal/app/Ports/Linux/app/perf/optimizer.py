#!/usr/bin/env python3
"""Performance Optimizer — thread tuning, quantization selection, lazy loading,
memory pressure handling, single-resident enforcement, and hardware tier cascading.

Provides `PerfOptimizer`, the central performance-tuning authority for the Mumble
v0.10 local AI stack.  It answers:

  • How many threads should STT use?   → `optimizer.stt_threads()`
  • How many threads should LLM use?   → `optimizer.llm_threads()`
  • Which GGUF quant should I download? → `optimizer.recommended_quantization()`
  • When should models be loaded?      → `optimizer.lazy_load_schedule()`
  • Is the system under memory pressure? → `optimizer.check_memory_pressure()`
  • Should a model be unloaded now?     → `optimizer.handle_memory_pressure(mgr)`
  • What happens when the tier changes? → `optimizer.cascade_tier_change(old, new)`
  • How to optimise startup?           → `optimizer.startup_optimize()`

Thread counts are tuned to the CPU, leaving 2 cores free for the OS:
  • STT: `max(4, cpu_count - 2)` — transcription benefits from parallelism
  • LLM: `cpu_count - 2` — GGUF inference scales with threads up to core count

Quantization selection follows the hardware tier:
  • weak     → no LLM model recommended (skip Stages 2/3)
  • mid      → Q4_K_M  (balanced speed / quality, ~1.3-1.5 GB)
  • powerful → Q8_0    (higher quality, ~1.6-1.8 GB)

The lazy-loading schedule ensures fast startup:
  • At import time: nothing (zero model load)
  • On first STT request: punctuation model (BERT, ~200 MB)
  • On first LLM request: GGUF model (~1.3-1.5 GB), spawned via llama-cli
  • System scan for .gguf files: deferred to first use, cached thereafter

Memory pressure detection delegates to `perf.diagnostics.Diagnostics` for
free-RAM checks and triggers `ModelManager.force_unload()` when the system
drops below the configured threshold.

Single-resident policy enforcement: before a new model is loaded, any
currently resident model is explicitly unloaded + its subprocess terminated.
Only one model lives in RAM at any time.

Hardware tier cascading (VAL-CROSS-009):
  Changing `hardware_tier` from `"weak"` to `"powerful"` cascades to:
    (a) STT model → branding.resolve_model(tier) picks a larger model
    (b) pipeline enablement → Stages 2/3 become recommended/available
    (c) quantization → Q4_K_M → Q8_0 for downloaded models

All classes are pure stdlib + project imports (branding, models, perf.diagnostics).
"""

import os
import sys
import time
import threading

# ---------------------------------------------------------------------------
# Project imports — guarded so the module imports and tests even when some
# sub-packages are not yet built.
# ---------------------------------------------------------------------------

try:
    from branding import detect_hardware_tier, resolve_model, HARDWARE_TIERS
except ImportError:
    HARDWARE_TIERS = ("weak", "mid", "powerful")

    def detect_hardware_tier():
        cores = os.cpu_count() or 4
        return "mid" if cores >= 4 else "weak"

    def resolve_model(tier, english_only=True):
        return "small.en" if english_only else "small"

# We import Diagnostics lazily inside check_memory_pressure() to avoid a
# hard import dependency at module level.
_Diagnostics = None
_DiagnosticsLock = threading.Lock()


def _get_diagnostics():
    """Return the perf Diagnostics singleton, created on first access."""
    global _Diagnostics
    if _Diagnostics is None:
        with _DiagnosticsLock:
            if _Diagnostics is None:
                from perf.diagnostics import Diagnostics as _D
                _Diagnostics = _D()
    return _Diagnostics


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Thread counts: STT gets a minimum floor; LLM uses the full compute budget.
STT_THREAD_FLOOR = 4
OS_RESERVED_CORES = 2

# Quantization labels per tier.  "none" means no LLM quant should be downloaded.
TIER_QUANTIZATION = {
    "weak": "none",
    "mid": "Q4_K_M",
    "powerful": "Q8_0",
}

# Memory pressure thresholds (MB).  Mirrors models/backend.py constants.
MEMORY_BUDGET_MB = 2048      # single-resident pipeline budget
PRESSURE_THRESHOLD_MB = 500  # free RAM below which we unload

# Lazy-load stages.  The numbers are the order in which stages should be
# loaded; lower = earlier.  None means "never auto-load".
LAZY_LOAD_ORDER = {
    "punctuation": 0,   # first — BERT, ~200 MB, always useful
    "grammar": 1,       # second — GRMR-2B GGUF, ~1.5 GB, conditional on tier
    "formatting": 2,    # third — Qwen2.5-1.5B GGUF, ~1.3 GB, conditional on tier
    "cleanup": -1,      # always available — zero cost, no load needed
}

# Tier-gated pipeline stages: which stages are enabled per tier.
# Stage 1 (punctuation) and Stage 4 (cleanup) are always available.
# Stages 2 and 3 are gated behind mid+ tiers.
TIER_STAGE_ENABLEMENT = {
    "weak":     {1: True, 2: False, 3: False, 4: True},
    "mid":      {1: True, 2: True,  3: True,  4: True},
    "powerful": {1: True, 2: True,  3: True,  4: True},
}

# Startup optimization: which operations can be deferred.
# True = defer to first use; False = run at startup.
STARTUP_DEFER = {
    "scan_models":    True,   # deferred — scan %APPDATA%/Mumble/models/
    "load_bert":      True,   # deferred — load on first STT request
    "load_llm":       True,   # deferred — load on first LLM request
    "check_updates":  True,   # deferred — HuggingFace update check
    "warm_imports":   False,  # at startup — import ai, pipeline packages
}


# ===========================================================================
# ThreadTuner
# ===========================================================================

class ThreadTuner:
    """Optimal thread counts for STT and LLM inference.

    STT (faster-whisper / sherpa-onnx) benefits from parallelism but not
    hyper-threading — a floor of 4 threads and a ceiling of (cores - 2).
    LLM (llama-cli / GGUF) scales with threads roughly linearly until the
    core count is saturated, so we give it the full budget.

    Parameters:
        cpu_count: Number of logical CPUs to base calculations on.
                   Defaults to `os.cpu_count() or 4`.
        os_reserved: Number of cores reserved for the OS and other apps.
    """

    def __init__(self, cpu_count=None, os_reserved=None):
        self._cpu_count = cpu_count or (os.cpu_count() or 4)
        self._os_reserved = os_reserved if os_reserved is not None else OS_RESERVED_CORES

    def stt_threads(self):
        """Optimal threads for STT (faster-whisper / sherpa-onnx).

        Formula: `max(STT_THREAD_FLOOR, cpu_count - os_reserved)`.
        """
        candidate = self._cpu_count - self._os_reserved
        return max(STT_THREAD_FLOOR, max(1, candidate))

    def llm_threads(self):
        """Optimal threads for LLM (llama-cli / GGUF inference).

        Formula: `cpu_count - os_reserved`, clamped to [1, cpu_count].
        """
        return max(1, self._cpu_count - self._os_reserved)

    @property
    def cpu_count(self):
        return self._cpu_count

    @property
    def os_reserved(self):
        return self._os_reserved

    def recommend(self):
        """Return a dict with both thread counts."""
        return {
            "cpu_count": self._cpu_count,
            "os_reserved": self._os_reserved,
            "stt_threads": self.stt_threads(),
            "llm_threads": self.llm_threads(),
        }

    def __repr__(self):
        return (f"ThreadTuner(cpu={self._cpu_count}, "
                f"stt={self.stt_threads()}, llm={self.llm_threads()})")


# ===========================================================================
# QuantizationSelector
# ===========================================================================

class QuantizationSelector:
    """Selects the optimal GGUF quantization level per hardware tier.

    Rules:
      • weak     → "none" (no LLM model recommended)
      • mid      → "Q4_K_M" (balanced speed/quality)
      • powerful → "Q8_0" (higher quality)

    Also provides helper methods to compare quantizations (for tier change
    cascading) and to list available quantizations for a given model.
    """

    # All GGUF quantizations in increasing quality (and size) order.
    ALL_QUANTS = [
        "Q2_K", "Q3_K_S", "Q3_K_M", "Q3_K_L",
        "Q4_0", "Q4_1", "Q4_K_S", "Q4_K_M",
        "Q5_0", "Q5_1", "Q5_K_S", "Q5_K_M",
        "Q6_K", "Q8_0",
        "F16", "F32",
    ]

    # Per-stage quantization that may differ from the tier default.
    # Stage 2 (grammar) and Stage 3 (formatting) can use different quants.
    STAGE_QUANT_OVERRIDES = {
        "stage2_grammar": {
            "weak": "none",
            "mid": "Q4_K_M",
            "powerful": "Q8_0",
        },
        "stage3_formatting": {
            "weak": "none",
            "mid": "Q4_K_M",
            "powerful": "Q8_0",
        },
    }

    def __init__(self, tier=None):
        self._tier = tier or detect_hardware_tier()
        if self._tier not in HARDWARE_TIERS:
            self._tier = "mid"

    @property
    def tier(self):
        return self._tier

    @tier.setter
    def tier(self, value):
        if value not in HARDWARE_TIERS:
            raise ValueError(f"Invalid tier {value!r}. Must be one of {HARDWARE_TIERS}")
        self._tier = value

    def recommended(self, tier=None, stage=None):
        """Return the recommended quantization for a tier and optional stage.

        Args:
            tier: Override the configured tier (use instance tier if None).
            stage: If provided (e.g. "stage2_grammar"), return the stage-
                   specific override instead of the tier default.

        Returns a quantization string like "Q4_K_M", "Q8_0", or "none".
        """
        t = tier or self._tier
        if t not in HARDWARE_TIERS:
            t = "mid"
        if stage and stage in self.STAGE_QUANT_OVERRIDES:
            return self.STAGE_QUANT_OVERRIDES[stage].get(t, TIER_QUANTIZATION.get(t, "Q4_K_M"))
        return TIER_QUANTIZATION.get(t, "Q4_K_M")

    def should_download_llm(self, tier=None):
        """Whether an LLM model should be downloaded for the given tier."""
        t = tier or self._tier
        return t != "weak"

    def is_quant_upgrade(self, old_quant, new_quant):
        """Return True when `new_quant` is a higher-quality quant than `old_quant`.

        "none" is the lowest; F32 is the highest.
        """
        if old_quant == new_quant:
            return False
        all_q = ["none"] + self.ALL_QUANTS
        try:
            old_idx = all_q.index(old_quant)
            new_idx = all_q.index(new_quant)
        except ValueError:
            return False
        return new_idx > old_idx

    def recommend_all(self, tier=None):
        """Return a dict with quantization recommendations for all stages."""
        t = tier or self._tier
        return {
            "tier": t,
            "default": self.recommended(t),
            "stage1_punctuation": "none",  # BERT — no GGUF quant
            "stage2_grammar": self.recommended(t, stage="stage2_grammar"),
            "stage3_formatting": self.recommended(t, stage="stage3_formatting"),
            "stage4_cleanup": "none",  # rules — no model
            "should_download_llm": self.should_download_llm(t),
        }

    def __repr__(self):
        return (f"QuantizationSelector(tier={self._tier!r}, "
                f"recommended={self.recommended()!r})")


# ===========================================================================
# LazyLoadScheduler
# ===========================================================================

class LazyLoadScheduler:
    """Controls the order and timing of model loading.

    The schedule ensures fast startup by deferring heavy model loads:
      • At startup: zero model loading (just discovery)
      • On first STT request: punctuation model (BERT, ~200 MB)
      • On first LLM request: GGUF model (~1.3-1.5 GB)

    The schedule is tier-aware: on weak hardware, LLM stages are never
    scheduled for loading.  On mid/powerful hardware, they are queued
    for loading on first demand.

    The scheduler also tracks WHAT has been loaded and respects the
    single-resident policy: before loading a new model, the previous
    one is unloaded.
    """

    def __init__(self, tier=None):
        self._tier = tier or detect_hardware_tier()
        if self._tier not in HARDWARE_TIERS:
            self._tier = "mid"
        self._loaded = set()     # names of models currently loaded
        self._ever_loaded = set()  # models that were loaded at least once
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------------

    def schedule(self, tier=None):
        """Return the ordered list of models/stages to load and when.

        Returns a list of dicts, each with:
          • stage: str — pipeline stage name
          • model: str or None — the model name to load (None for built-ins)
          • order: int — load order (lower = earlier, -1 = always available)
          • load_trigger: str — "startup", "first_stt", "first_llm", "never"
          • enabled: bool — whether this stage should be loaded given the tier
        """
        t = tier or self._tier
        enablement = TIER_STAGE_ENABLEMENT.get(t, TIER_STAGE_ENABLEMENT["mid"])
        return [
            {
                "stage": "stage1_punctuation",
                "model": None,
                "order": 0,
                "load_trigger": "first_stt",
                "enabled": True,
            },
            {
                "stage": "stage2_grammar",
                "model": "grmr-2b-instruct",
                "order": 1,
                "load_trigger": "first_llm" if enablement.get(2) else "never",
                "enabled": enablement.get(2, False),
            },
            {
                "stage": "stage3_formatting",
                "model": "qwen2.5-1.5b-instruct",
                "order": 2,
                "load_trigger": "first_llm" if enablement.get(3) else "never",
                "enabled": enablement.get(3, False),
            },
            {
                "stage": "stage4_cleanup",
                "model": None,
                "order": -1,
                "load_trigger": "always",
                "enabled": True,
            },
        ]

    def stage_enabled(self, stage_name, tier=None):
        """Return True when a pipeline stage should be enabled for the tier."""
        t = tier or self._tier
        enablement = TIER_STAGE_ENABLEMENT.get(t, TIER_STAGE_ENABLEMENT["mid"])
        if stage_name == "stage1_punctuation":
            return enablement.get(1, True)
        if stage_name == "stage2_grammar":
            return enablement.get(2, False)
        if stage_name == "stage3_formatting":
            return enablement.get(3, False)
        if stage_name == "stage4_cleanup":
            return enablement.get(4, True)
        return False

    def should_load_now(self, stage_name, trigger_event, tier=None):
        """Return True when a stage should be loaded given the trigger event.

        Args:
            stage_name: Pipeline stage (e.g. "stage2_grammar").
            trigger_event: "startup", "first_stt", "first_llm", or "manual".
            tier: Optional tier override.

        Returns True when the stage's load trigger matches the event AND
        the stage is enabled for the current tier.
        """
        t = tier or self._tier
        schedule = {s["stage"]: s for s in self.schedule(t)}
        entry = schedule.get(stage_name)
        if entry is None:
            return False
        if not entry["enabled"]:
            return False
        if entry["load_trigger"] == "always":
            return True
        if entry["load_trigger"] == "never":
            return False
        return entry["load_trigger"] == trigger_event

    # ------------------------------------------------------------------
    # Load tracking
    # ------------------------------------------------------------------

    def mark_loaded(self, stage_name):
        """Record that a stage's model has been loaded into memory."""
        with self._lock:
            self._loaded.add(stage_name)
            self._ever_loaded.add(stage_name)

    def mark_unloaded(self, stage_name):
        """Record that a stage's model has been unloaded."""
        with self._lock:
            self._loaded.discard(stage_name)

    @property
    def loaded_stages(self):
        """Return a set of stage names currently loaded."""
        with self._lock:
            return set(self._loaded)

    @property
    def is_anything_loaded(self):
        """Return True when at least one model is loaded."""
        with self._lock:
            return len(self._loaded) > 0

    def reset(self):
        """Reset load tracking (e.g. on tier change)."""
        with self._lock:
            self._loaded.clear()
            # Keep _ever_loaded for diagnostics.

    # ------------------------------------------------------------------
    # Tier change
    # ------------------------------------------------------------------

    def on_tier_change(self, old_tier, new_tier):
        """Update the tier and return a dict of stages that changed enablement."""
        self._tier = new_tier
        old_enablement = TIER_STAGE_ENABLEMENT.get(old_tier, TIER_STAGE_ENABLEMENT["mid"])
        new_enablement = TIER_STAGE_ENABLEMENT.get(new_tier, TIER_STAGE_ENABLEMENT["mid"])
        changes = {}
        for stage_num in (1, 2, 3, 4):
            stage_map = {
                1: "stage1_punctuation",
                2: "stage2_grammar",
                3: "stage3_formatting",
                4: "stage4_cleanup",
            }
            sname = stage_map[stage_num]
            was = old_enablement.get(stage_num, False)
            now = new_enablement.get(stage_num, False)
            if was != now:
                changes[sname] = {"was_enabled": was, "now_enabled": now}
        return changes

    def __repr__(self):
        return (f"LazyLoadScheduler(tier={self._tier!r}, "
                f"loaded={sorted(self._loaded)})")


# ===========================================================================
# MemoryPressureHandler
# ===========================================================================

class MemoryPressureHandler:
    """Memory pressure detection and model unloading.

    Monitors system free RAM and the pipeline's combined memory budget.
    When free RAM drops below `pressure_threshold_mb`, triggers model
    unload via the attached ModelManager.

    Designed to be called before and after each pipeline run.  Lightweight
    when psutil is available; degrades to ctypes on Windows; returns safe
    "unknown" sentinels on other platforms (never crashes).

    Parameters:
        pressure_threshold_mb: Free RAM below which to trigger unload.
        memory_budget_mb: Combined pipeline RAM budget (single-resident).
        budget_warn_percent: Percentage of budget that triggers a warning.
        auto_unload: Whether to automatically call force_unload() when
                     pressure is detected.  Set False during active
                     dictation to avoid interrupting a session.
    """

    def __init__(self, pressure_threshold_mb=None, memory_budget_mb=None,
                 budget_warn_percent=None, auto_unload=True):
        self.pressure_threshold_mb = (
            pressure_threshold_mb or PRESSURE_THRESHOLD_MB
        )
        self.memory_budget_mb = (
            memory_budget_mb or MEMORY_BUDGET_MB
        )
        self.budget_warn_percent = (
            budget_warn_percent or 80
        )
        self.auto_unload = auto_unload
        self._model_manager = None
        self._pipeline_orchestrator = None
        self._last_pressure_check = 0.0
        self._pressure_check_interval = 5.0  # seconds between checks

    # ------------------------------------------------------------------
    # Model manager attachment
    # ------------------------------------------------------------------

    def attach_model_manager(self, model_manager):
        """Attach a ModelManager instance for automatic unloading.

        When attached, `handle_memory_pressure()` will call
        `model_manager.force_unload()` when under pressure.
        """
        self._model_manager = model_manager

    def attach_pipeline(self, pipeline_orchestrator):
        """Attach a PipelineOrchestrator for stage-level unloading."""
        self._pipeline_orchestrator = pipeline_orchestrator

    # ------------------------------------------------------------------
    # Core checks
    # ------------------------------------------------------------------

    def check(self):
        """Run a memory pressure check and return the status dict.

        Delegates to `perf.diagnostics.Diagnostics.check_memory_pressure()`.
        Returns a dict with keys: under_pressure, free_memory_mb,
        process_memory_mb, threshold_mb, budget_mb, budget_used_pct,
        budget_warning, message.
        """
        diag = _get_diagnostics()
        diag.memory_threshold_mb = self.pressure_threshold_mb
        diag.memory_budget_mb = self.memory_budget_mb
        diag.memory_warn_percent = self.budget_warn_percent
        return diag.check_memory_pressure()

    def is_under_pressure(self):
        """Return True when the system is under memory pressure.

        Throttled: only actually checks every `_pressure_check_interval`
        seconds to avoid expensive OS calls on every inference request.
        """
        now = time.time()
        if now - self._last_pressure_check < self._pressure_check_interval:
            # Return cached result from last check.
            return getattr(self, "_cached_pressure", False)
        self._last_pressure_check = now
        status = self.check()
        self._cached_pressure = status.get("under_pressure", False)
        return self._cached_pressure

    def handle_memory_pressure(self, model_manager=None, force=False):
        """Check memory pressure and unload models if needed.

        Args:
            model_manager: Optional ModelManager to use for unloading.
                           Falls back to the attached one.
            force: When True, bypass the check interval and run immediately.

        Returns a dict with:
          • was_under_pressure: bool — whether pressure was detected
          • unloaded: bool — whether a model was unloaded
          • free_memory_mb: float or None
          • message: str
        """
        mgr = model_manager or self._model_manager
        status = self.check()
        under = status.get("under_pressure", False)

        result = {
            "was_under_pressure": under,
            "unloaded": False,
            "free_memory_mb": status.get("free_memory_mb"),
            "message": status.get("message", ""),
        }

        if under and self.auto_unload:
            if mgr is not None:
                try:
                    mgr.force_unload()
                    result["unloaded"] = True
                    result["message"] = (
                        f"Memory pressure detected ({result['free_memory_mb']} MB free). "
                        "Model unloaded to free RAM."
                    )
                    print(f"[optimizer] {result['message']}", file=sys.stderr)
                except Exception as e:
                    print(f"[optimizer] Failed to unload model: {e}", file=sys.stderr)
            # Also tell the pipeline orchestrator to release models.
            if self._pipeline_orchestrator is not None:
                try:
                    self._pipeline_orchestrator.unload_all()
                except Exception:
                    pass

        return result

    def should_unload(self, model_size_mb_estimate=0):
        """Return True when the estimated model size would exceed the remaining budget.

        This is a proactive check: before loading a new model, call this to see
        if the current one needs to be unloaded first (single-resident policy).

        Args:
            model_size_mb_estimate: Estimated RAM needed for the new model.

        Returns True when the current model should be unloaded before loading
        a new one.  Always True when any model is currently loaded (strict
        single-resident enforcement).
        """
        # Under single-resident policy, ALWAYS unload the previous model
        # before loading a new one.
        if mgr := self._model_manager:
            if mgr.loaded_model is not None:
                return True
        return False

    # ------------------------------------------------------------------
    # Startup / shutdown
    # ------------------------------------------------------------------

    def startup_check(self):
        """Run a memory check at startup. Never unloads (no models loaded yet)."""
        self.auto_unload = False  # Don't unload at startup
        status = self.check()
        self.auto_unload = True   # Re-enable for runtime
        return status

    def reset(self):
        """Reset cached pressure state."""
        self._last_pressure_check = 0.0
        self._cached_pressure = False

    def __repr__(self):
        return (f"MemoryPressureHandler(threshold={self.pressure_threshold_mb}MB, "
                f"budget={self.memory_budget_mb}MB, auto_unload={self.auto_unload})")


# ===========================================================================
# PerfOptimizer — central orchestrator
# ===========================================================================

class PerfOptimizer:
    """Central performance optimizer for Mumble v0.10.

    Combines thread tuning, quantization selection, lazy loading, memory
    pressure handling, single-resident enforcement, and hardware tier
    cascading into a single, cohesive API.

    Usage:
        opt = PerfOptimizer()
        threads = opt.threads.recommend()
        quant   = opt.quant.recommended()
        schedule = opt.lazy.schedule()
        mem     = opt.memory.check()

        # After a tier change:
        cascade = opt.cascade_tier_change("weak", "powerful")
        # → dict with stt_model, quantization, pipeline_stages changes
    """

    def __init__(self, hardware_tier=None, cpu_count=None):
        """Create a PerfOptimizer.

        Args:
            hardware_tier: "weak", "mid", "powerful", or None (auto-detect).
            cpu_count: Logical CPU count for thread tuning.
        """
        tier = hardware_tier or detect_hardware_tier()
        if tier not in HARDWARE_TIERS:
            tier = "mid"

        self._tier = tier
        self._tier_lock = threading.Lock()

        # Sub-components.
        self._threads = ThreadTuner(cpu_count=cpu_count)
        self._quant = QuantizationSelector(tier=tier)
        self._lazy = LazyLoadScheduler(tier=tier)
        self._memory = MemoryPressureHandler()

        # External references set via setters (for cascading).
        self._model_manager = None
        self._model_registry = None
        self._pipeline_orchestrator = None
        self._settings = None  # Optional reference to settings module

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def tier(self):
        return self._tier

    @tier.setter
    def tier(self, value):
        if value not in HARDWARE_TIERS:
            raise ValueError(f"Invalid tier {value!r}. Must be one of {HARDWARE_TIERS}")
        with self._tier_lock:
            old = self._tier
            self._tier = value
            self._quant.tier = value
            self._lazy._tier = value

    @property
    def threads(self):
        return self._threads

    @property
    def quant(self):
        return self._quant

    @property
    def lazy(self):
        return self._lazy

    @property
    def memory(self):
        return self._memory

    # ------------------------------------------------------------------
    # Attach external references (for cascading)
    # ------------------------------------------------------------------

    def attach_model_manager(self, mgr):
        """Attach the ModelManager for memory pressure unloading."""
        self._model_manager = mgr
        self._memory.attach_model_manager(mgr)

    def attach_model_registry(self, registry):
        """Attach the ModelRegistry for tier-aware model lookups."""
        self._model_registry = registry

    def attach_pipeline(self, orchestrator):
        """Attach the PipelineOrchestrator for stage enablement and unloading."""
        self._pipeline_orchestrator = orchestrator
        self._memory.attach_pipeline(orchestrator)

    def attach_settings(self, settings_module):
        """Attach a reference to the settings module for reading/writing tier."""
        self._settings = settings_module

    # ------------------------------------------------------------------
    # Shortcuts — direct answers for callers
    # ------------------------------------------------------------------

    def stt_threads(self):
        """Optimal thread count for STT inference."""
        return self._threads.stt_threads()

    def llm_threads(self):
        """Optimal thread count for LLM inference."""
        return self._threads.llm_threads()

    def recommended_quantization(self, stage=None):
        """Recommended GGUF quantization for the current tier (and optional stage)."""
        return self._quant.recommended(stage=stage)

    def lazy_load_schedule(self):
        """Ordered list of what to load and when for the current tier."""
        return self._lazy.schedule()

    def check_memory_pressure(self):
        """Return the memory pressure status dict."""
        return self._memory.check()

    def handle_memory_pressure(self, model_manager=None, force=False):
        """Check and handle memory pressure (potentially unloads models)."""
        mgr = model_manager or self._model_manager
        return self._memory.handle_memory_pressure(mgr, force=force)

    def should_unload_before_load(self, model_size_mb=0):
        """Return True when the current model should be unloaded before loading a new one."""
        return self._memory.should_unload(model_size_mb)

    def enforce_single_resident(self):
        """Enforce the single-resident policy: unload any currently loaded model.

        This is called before loading a new model to ensure only one model
        resides in RAM at any time.  If no model is loaded, this is a no-op.

        Returns:
            dict with keys: unloaded (bool), previous_model (str or None)
        """
        result = {"unloaded": False, "previous_model": None}
        mgr = self._model_manager
        if mgr is not None:
            prev = mgr.loaded_model
            result["previous_model"] = prev
            if prev is not None:
                try:
                    mgr.force_unload()
                    result["unloaded"] = True
                    print(f"[optimizer] Single-resident: unloaded '{prev}'",
                          file=sys.stderr)
                except Exception as e:
                    print(f"[optimizer] Failed to enforce single-resident: {e}",
                          file=sys.stderr)
        return result

    # ------------------------------------------------------------------
    # Startup optimization
    # ------------------------------------------------------------------

    def startup_optimize(self):
        """Run startup performance optimization.

        Returns a dict describing what was deferred and what was run
        immediately.  The caller (typically mumble.py at boot) should:
          1. Call this once at startup.
          2. Follow the deferred actions: schedule them for execution after
             the UI is fully painted (idle callback / after_idle).

        Returns:
            dict with keys:
              • tier: str — detected hardware tier
              • threads: dict — thread count recommendations
              • quantization: str — recommended quantization
              • deferred: list of str — actions deferred to first use
              • immediate: list of str — actions run at startup
              • pipeline_stages: dict — which stages are enabled
              • memory_status: dict — free RAM, pressure status
        """
        tier = self._tier
        immediate = []
        deferred = []

        # Always run: thread tuning (cheap).
        immediate.append("thread_tuning")
        _ = self._threads.recommend()

        # Always run: quantization selection (cheap).
        immediate.append("quantization_selection")
        _ = self._quant.recommended()

        # Deferred: model directory scan (I/O).
        deferred.append("scan_models")

        # Deferred: BERT punctuation model load (~200 MB).
        deferred.append("load_punctuation_model")

        # Deferred: LLM model load (if tier allows).
        if self._quant.should_download_llm():
            deferred.append("load_llm_model")

        # Memory check at startup (cheap, does not unload).
        mem_status = self._memory.startup_check()

        # Pipeline stage enablement.
        stages = self._lazy.schedule()

        return {
            "tier": tier,
            "threads": self._threads.recommend(),
            "quantization": self._quant.recommend_all(),
            "deferred": deferred,
            "immediate": immediate,
            "pipeline_stages": stages,
            "memory_status": mem_status,
        }

    # ------------------------------------------------------------------
    # Tier cascading (VAL-CROSS-009)
    # ------------------------------------------------------------------

    def cascade_tier_change(self, old_tier, new_tier):
        """Propagate a hardware tier change to all dependent subsystems.

        This implements VAL-CROSS-009: when the hardware tier changes, the
        following MUST cascade:

        (a) STT model → branding.resolve_model(tier) picks a larger model
        (b) pipeline enablement → Stages 2/3 become enabled/disabled
        (c) quantization selection → Q4_K_M ↔ Q8_0

        Additionally, this method:
          - Updates the tier on all sub-components.
          - Returns a structured dict describing every change so the caller
            can apply them (reload STT model, re-download GGUF, etc.).

        Args:
            old_tier: The previous hardware tier.
            new_tier: The new hardware tier.

        Returns:
            dict with keys:
              • old_tier, new_tier
              • stt_model_change: {old, new, changed}
              • quantization_change: {old, new, is_upgrade}
              • pipeline_stage_changes: {stage_name: {was_enabled, now_enabled}}
              • recommendations: list of human-readable action strings
        """
        if old_tier not in HARDWARE_TIERS:
            old_tier = "mid"
        if new_tier not in HARDWARE_TIERS:
            new_tier = "mid"

        # Apply the tier change to all sub-components.
        with self._tier_lock:
            self._tier = new_tier
            self._quant.tier = new_tier
            stage_changes = self._lazy.on_tier_change(old_tier, new_tier)

        # (a) STT model change.
        old_stt = resolve_model(old_tier, english_only=True)
        new_stt = resolve_model(new_tier, english_only=True)
        stt_changed = old_stt != new_stt

        # (b) Pipeline stage changes already computed above.

        # (c) Quantization change.
        old_quant = self._quant.recommended(old_tier)
        new_quant = self._quant.recommended(new_tier)
        quant_changed = old_quant != new_quant
        is_upgrade = self._quant.is_quant_upgrade(old_quant, new_quant)

        # Build human-readable recommendations.
        recs = []
        if stt_changed:
            recs.append(
                f"STT model changed: {old_stt} → {new_stt}. "
                "The new model will be downloaded on next use."
            )
        if quant_changed and new_quant != "none":
            direction = "upgraded" if is_upgrade else "downgraded"
            recs.append(
                f"Quantization {direction}: {old_quant} → {new_quant}. "
                "Existing models may need re-download for the new quant level."
            )
        if quant_changed and new_quant == "none":
            recs.append(
                "Quantization disabled for weak tier. LLM pipeline stages "
                "will not be available."
            )
        for sname, change in stage_changes.items():
            if change["now_enabled"] and not change["was_enabled"]:
                recs.append(
                    f"Pipeline {sname} is now ENABLED. "
                    "Download the required model to activate this stage."
                )
            elif change["was_enabled"] and not change["now_enabled"]:
                recs.append(
                    f"Pipeline {sname} is now DISABLED. "
                    "The model will be unloaded to free memory."
                )

        return {
            "old_tier": old_tier,
            "new_tier": new_tier,
            "stt_model_change": {
                "old": old_stt,
                "new": new_stt,
                "changed": stt_changed,
            },
            "quantization_change": {
                "old": old_quant,
                "new": new_quant,
                "changed": quant_changed,
                "is_upgrade": is_upgrade,
            },
            "pipeline_stage_changes": stage_changes,
            "recommendations": recs,
        }

    # ------------------------------------------------------------------
    # Model availability → pipeline enablement (VAL-CROSS-005)
    # ------------------------------------------------------------------

    def reevaluate_pipeline_stages(self, model_manager=None):
        """Re-evaluate which pipeline stages should be available based on the
        currently discovered models and the hardware tier.

        This implements VAL-CROSS-005: after a model is downloaded, the
        pipeline should automatically enable the corresponding stage on
        the next run WITHOUT requiring a restart.

        The method scans available models via ModelManager and determines
        which pipeline stages can be activated.  It returns a dict that
        the caller can use to update the pipeline orchestrator.

        Args:
            model_manager: Optional ModelManager instance. Falls back to the
                          attached one.

        Returns:
            dict with keys:
              • tier: str — current hardware tier
              • stages: dict — {stage_name: {available, reason}}
              • newly_available: list — stages that just became available
              • recommendation: str — human-readable summary
        """
        mgr = model_manager or self._model_manager
        tier = self._tier
        tier_enablement = TIER_STAGE_ENABLEMENT.get(tier, TIER_STAGE_ENABLEMENT["mid"])
        result = {"tier": tier, "stages": {}, "newly_available": [],
                  "recommendation": ""}

        # Stage 1 (punctuation): always tier-enabled, check actual import.
        stage1_available = True
        try:
            from pipeline.stage_punctuation import PunctuationStage
            _ = PunctuationStage
        except ImportError:
            stage1_available = False
        result["stages"]["stage1_punctuation"] = {
            "available": stage1_available and tier_enablement.get(1, True),
            "reason": "BERT punctuation model" if stage1_available
                      else "deepmultilingualpunctuation not installed",
        }

        # Stage 2 (grammar): enabled by tier AND by model presence.
        stage2_tier_ok = tier_enablement.get(2, False)
        stage2_model_present = False
        if mgr is not None:
            try:
                mgr.discover()
                for m in mgr.available_models():
                    if "grmr" in (m.get("name", "")).lower():
                        stage2_model_present = True
                        break
            except Exception:
                pass
        stage2_available = stage2_tier_ok and stage2_model_present
        result["stages"]["stage2_grammar"] = {
            "available": stage2_available,
            "reason": (
                "GRMR-2B GGUF model found" if stage2_available
                else "GRMR-2B GGUF model not downloaded" if stage2_tier_ok
                else "Not recommended for your hardware tier"
            ),
        }

        # Stage 3 (formatting): enabled by tier AND by model presence.
        stage3_tier_ok = tier_enablement.get(3, False)
        stage3_model_present = False
        if mgr is not None:
            try:
                for m in mgr.available_models():
                    if "qwen" in (m.get("name", "")).lower():
                        stage3_model_present = True
                        break
            except Exception:
                pass
        stage3_available = stage3_tier_ok and stage3_model_present
        result["stages"]["stage3_formatting"] = {
            "available": stage3_available,
            "reason": (
                "Qwen2.5-1.5B GGUF model found" if stage3_available
                else "Qwen2.5-1.5B GGUF model not downloaded" if stage3_tier_ok
                else "Not recommended for your hardware tier"
            ),
        }

        # Stage 4 (cleanup): always available.
        result["stages"]["stage4_cleanup"] = {
            "available": True,
            "reason": "Built-in surface cleanup (always available)",
        }

        # Determine newly available stages.
        # We check the pipeline orchestrator's current state vs the new one.
        if self._pipeline_orchestrator is not None:
            current_available = set(self._pipeline_orchestrator.available_stages())
            stage_num_map = {
                "stage1_punctuation": 1,
                "stage2_grammar": 2,
                "stage3_formatting": 3,
                "stage4_cleanup": 4,
            }
            for sname, info in result["stages"].items():
                snum = stage_num_map.get(sname)
                if snum is not None and info["available"] and snum not in current_available:
                    result["newly_available"].append(sname)

        # Build recommendation.
        missing_stages = [s for s, info in result["stages"].items() if not info["available"]]
        if not missing_stages:
            result["recommendation"] = "All pipeline stages available."
        else:
            result["recommendation"] = (
                f"Some pipeline stages unavailable: {', '.join(missing_stages)}. "
                "Download the corresponding GGUF models to enable them."
            )

        return result

    # ------------------------------------------------------------------
    # Active dictation guard (VAL-CROSS-006)
    # ------------------------------------------------------------------

    def is_dictation_safe_for_model_switch(self):
        """Check whether it's safe to switch or load a model.

        Returns True when no active dictation is in progress.  Callers should
        check this before loading or unloading models to avoid interrupting
        a live dictation session (VAL-CROSS-006).

        The default implementation always returns True since we don't track
        dictation state directly.  Integrators should override this or pass
        the actual dictation status when calling methods that load/unload.
        """
        return True

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def full_recommendation(self):
        """Return a comprehensive recommendation dict for the current tier.

        This is a one-stop report: thread counts, quantization, lazy-load
        schedule, pipeline enablement, and memory status — everything a
        UI needs to show the current performance configuration.
        """
        return {
            "tier": self._tier,
            "threads": self._threads.recommend(),
            "quantization": self._quant.recommend_all(),
            "lazy_load_schedule": self._lazy.schedule(),
            "pipeline_stages": self.reevaluate_pipeline_stages(),
            "memory_status": self._memory.check(),
        }

    def __repr__(self):
        return (f"PerfOptimizer(tier={self._tier!r}, "
                f"threads={self._threads}, "
                f"quant={self._quant.recommended()!r})")


# ===========================================================================
# Module-level convenience
# ===========================================================================

_default_optimizer = None
_default_lock = threading.Lock()


def get_optimizer(tier=None):
    """Return (or create) the module-level PerfOptimizer singleton.

    Args:
        tier: Optional hardware tier override. Only used when creating the
              singleton for the first time.
    """
    global _default_optimizer
    if _default_optimizer is None:
        with _default_lock:
            if _default_optimizer is None:
                _default_optimizer = PerfOptimizer(hardware_tier=tier)
    return _default_optimizer
