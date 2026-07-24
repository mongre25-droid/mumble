#!/usr/bin/env python3
"""Performance profiling, diagnostics, and optimisation for Mumble v0.10.

This package provides three main components:

  • perf.profiler  — InferenceProfiler: measures latency, memory, CPU/GPU
                     per pipeline stage.  Provides context-manager hooks for
                     profiling STT, the pipeline, and STT-to-Stage1 handoff.

  • perf.diagnostics — Enhanced RTF (Real-Time Factor) monitor, memory
                     pressure detection, and bottleneck identification.
                     Extends the simpler RTF diagnostics in transcription.py
                     with per-stage breakdown and actionable recommendations.

  • perf.optimizer — PerfOptimizer: thread count tuning, quantization
                     selection, lazy loading schedule, memory pressure
                     handling → model unloading, single-resident policy
                     enforcement, startup optimisation, and hardware-tier
                     cascading to model selection + pipeline enablement.

All modules use import guards for optional dependencies (psutil, GPU tools)
and degrade gracefully when they are unavailable.  Profiling hooks are opt-in
and lightweight when disabled — the pipeline runs with zero profiling overhead
by default.

Usage:

    from perf import InferenceProfiler, Diagnostics, PerfOptimizer

    profiler = InferenceProfiler()
    diag = Diagnostics()

    # Profile a pipeline run:
    result, profile = profiler.profile_pipeline("hello world", lane="text")

    # Check system health:
    mem_status = diag.check_memory_pressure()
    rtf_report = diag.monitor_rtf(audio_duration_sec, total_processing_ms)

    # Optimize performance:
    opt = PerfOptimizer()
    threads = opt.stt_threads()
    quant = opt.recommended_quantization()
    schedule = opt.lazy_load_schedule()
"""

# ---------------------------------------------------------------------------
# Profiler
# ---------------------------------------------------------------------------

from .profiler import (
    InferenceProfiler,
    StageTiming,
    ProfileResult,
    profiling_enabled,
    set_profiling_enabled,
    get_profiler,
)

# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

from .diagnostics import (
    Diagnostics,
    get_diagnostics,
)

# ---------------------------------------------------------------------------
# Optimizer
# ---------------------------------------------------------------------------

from .optimizer import (
    PerfOptimizer,
    ThreadTuner,
    QuantizationSelector,
    LazyLoadScheduler,
    MemoryPressureHandler,
    get_optimizer,
)

__all__ = [
    # Profiler
    "InferenceProfiler",
    "StageTiming",
    "ProfileResult",
    "profiling_enabled",
    "set_profiling_enabled",
    "get_profiler",
    # Diagnostics
    "Diagnostics",
    "get_diagnostics",
    # Optimizer
    "PerfOptimizer",
    "ThreadTuner",
    "QuantizationSelector",
    "LazyLoadScheduler",
    "MemoryPressureHandler",
    "get_optimizer",
]
