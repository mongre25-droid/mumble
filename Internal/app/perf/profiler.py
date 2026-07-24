#!/usr/bin/env python3
"""Inference Profiler — measures latency, memory, CPU/GPU per pipeline stage.

Provides `InferenceProfiler`, the main profiling tool for the Mumble v0.10
local AI pipeline.  It records wall-clock timing, optional memory usage,
optional CPU load, and optional GPU utilisation for every processing stage.

DESIGN
  • Profiling hooks are opt-in — the pipeline runs with zero overhead when
    no profiler is attached (profilers must be passed explicitly).
  • `StageTimer` is a context manager: `with profiler.stage("name"): ...`
  • `profile_pipeline(text, lane)` wraps a full pipeline run
  • `profile_transcription(audio)` wraps an STT run
  • All optional measurements (memory, CPU, GPU) degrade gracefully when
    their tools are unavailable — no hard dependency beyond the stdlib.
  • Thread-safe: stage timings are recorded in order; global profiling flag
    is guarded.

USAGE

    from perf.profiler import InferenceProfiler

    profiler = InferenceProfiler()

    # Context-manager per stage:
    with profiler.stage("stage1_punctuation"):
        result = punctuation_model.process(text)

    # Wrap a full pipeline:
    result, profile = profiler.profile_pipeline(text, lane="text")

    # Wrap transcription:
    profile = profiler.profile_transcription(audio, sample_rate=16000)

    # Get a summary report:
    report = profiler.summary()

INTEGRATION WITH THE PIPELINE
    The `PipelineOrchestrator.process()` method accepts an optional `profiler`
    keyword argument.  When provided and the profiler is enabled, each stage's
    execution is automatically timed and recorded.  See pipeline/__init__.py
    for the integration code.
"""

import os
import sys
import time
import threading

# ---------------------------------------------------------------------------
# Optional dependencies — import-guarded
# ---------------------------------------------------------------------------

_HAVE_PSUTIL = False
_psutil = None

try:
    import psutil as _psutil
    _HAVE_PSUTIL = True
except ImportError:
    pass


# Global profiling toggle — can be set via set_profiling_enabled().
# When False, all profiling hooks are no-ops (zero overhead for production).
_PROFILING_ENABLED = True
_PROFILING_LOCK = threading.Lock()


def profiling_enabled():
    """Return True when profiling is globally enabled."""
    return _PROFILING_ENABLED


def set_profiling_enabled(value):
    """Globally enable or disable all profiling hooks.

    When disabled, all context managers become no-ops and `profile_pipeline()`,
    `profile_transcription()` skip measurement.  This allows profiling code to
    be left in the pipeline without runtime overhead.
    """
    global _PROFILING_ENABLED
    with _PROFILING_LOCK:
        _PROFILING_ENABLED = bool(value)


# ===========================================================================
# StageTiming — one measurement point
# ===========================================================================

class StageTiming:
    """Timing and resource data for a single pipeline stage or phase.

    Attributes:
        stage_name:   Human-readable label (e.g. "stage1_punctuation").
        elapsed_ms:   Wall-clock duration in milliseconds.
        mem_before_mb: RSS in MB at stage start (0 if unavailable).
        mem_after_mb:  RSS in MB at stage end (0 if unavailable).
        mem_delta_mb:  Change in RSS during the stage.
        cpu_percent:   Average CPU utilisation during the stage (0 if unavail).
        gpu_info:      Dict with GPU stats (empty if unavailable).
    """

    __slots__ = ("stage_name", "elapsed_ms", "mem_before_mb",
                 "mem_after_mb", "mem_delta_mb", "cpu_percent", "gpu_info")

    def __init__(self, stage_name, elapsed_ms=0.0, mem_before_mb=0.0,
                 mem_after_mb=0.0, cpu_percent=0.0, gpu_info=None):
        self.stage_name = stage_name
        self.elapsed_ms = elapsed_ms
        self.mem_before_mb = mem_before_mb
        self.mem_after_mb = mem_after_mb
        self.mem_delta_mb = mem_after_mb - mem_before_mb
        self.cpu_percent = cpu_percent
        self.gpu_info = gpu_info or {}

    def to_dict(self):
        return {
            "stage_name": self.stage_name,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "mem_before_mb": round(self.mem_before_mb, 2),
            "mem_after_mb": round(self.mem_after_mb, 2),
            "mem_delta_mb": round(self.mem_delta_mb, 2),
            "cpu_percent": round(self.cpu_percent, 1),
            "gpu_info": dict(self.gpu_info),
        }

    def __repr__(self):
        mem = ""
        if self.mem_delta_mb != 0:
            sign = "+" if self.mem_delta_mb > 0 else ""
            mem = f" mem={self.mem_before_mb:.1f}→{self.mem_after_mb:.1f}MB ({sign}{self.mem_delta_mb:.1f})"
        return (f"StageTiming({self.stage_name!r}, "
                f"{self.elapsed_ms:.3f}ms{mem})")


# ===========================================================================
# ProfileResult — full profile report for a multi-stage run
# ===========================================================================

class ProfileResult:
    """Aggregated profiling data for a complete pipeline or transcription run.

    Attributes:
        run_label:      Human-readable label (e.g. "pipeline:text").
        total_ms:       Total wall-clock duration.
        stage_timings:  List of StageTiming objects in execution order.
        metadata:       Extra context dict (lane, model name, device, etc.).
    """

    __slots__ = ("run_label", "total_ms", "stage_timings", "metadata")

    def __init__(self, run_label="", total_ms=0.0, stage_timings=None,
                 metadata=None):
        self.run_label = run_label
        self.total_ms = total_ms
        self.stage_timings = list(stage_timings or [])
        self.metadata = dict(metadata or {})

    def slowest_stage(self):
        """Return the StageTiming with the highest elapsed_ms, or None."""
        if not self.stage_timings:
            return None
        return max(self.stage_timings, key=lambda s: s.elapsed_ms)

    def to_dict(self):
        return {
            "run_label": self.run_label,
            "total_ms": round(self.total_ms, 3),
            "stages": [s.to_dict() for s in self.stage_timings],
            "metadata": dict(self.metadata),
        }

    def __repr__(self):
        return (f"ProfileResult({self.run_label!r}, "
                f"{self.total_ms:.3f}ms, {len(self.stage_timings)} stages)")


# ===========================================================================
# InferenceProfiler
# ===========================================================================

class InferenceProfiler:
    """Profiles inference latency, memory, CPU, and GPU per pipeline stage.

    The profiler is designed to be attached to a pipeline run (passed as a
    `profiler=` kwarg to `PipelineOrchestrator.process()`) or used standalone
    via context managers.  When disabled, all methods are cheap no-ops.

    Parameters:
        enabled:       Master switch.  When False, all stage() calls are no-ops.
        track_memory:  Record RSS before/after each stage (requires psutil).
        track_cpu:     Record CPU utilisation per stage (requires psutil).
        track_gpu:     Record GPU utilisation per stage (requires nvidia-smi or
                       similar; gracefully degrades if unavailable).
    """

    def __init__(self, enabled=True, track_memory=True, track_cpu=True,
                 track_gpu=False):
        self._enabled = enabled
        self._track_memory = track_memory
        self._track_cpu = track_cpu
        self._track_gpu = track_gpu
        self._stage_timings = []   # list of StageTiming
        self._lock = threading.Lock()
        self._run_start = None     # perf_counter snapshot for total_ms

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def enabled(self):
        return self._enabled and profiling_enabled()

    @enabled.setter
    def enabled(self, value):
        self._enabled = bool(value)

    @property
    def stage_timings(self):
        """Return a snapshot of all StageTiming records (thread-safe)."""
        with self._lock:
            return list(self._stage_timings)

    # ------------------------------------------------------------------
    # Enable / disable
    # ------------------------------------------------------------------

    def enable(self):
        """Enable profiling."""
        self._enabled = True

    def disable(self):
        """Disable profiling (all hooks become no-ops)."""
        self._enabled = False

    def clear(self):
        """Reset all recorded stage timings."""
        with self._lock:
            self._stage_timings.clear()
            self._run_start = None

    # ------------------------------------------------------------------
    # Memory / CPU / GPU helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_memory_mb():
        """Return current process RSS in MB, or 0.0 if unavailable."""
        if not _HAVE_PSUTIL:
            return 0.0
        try:
            proc = _psutil.Process()
            return proc.memory_info().rss / (1024 * 1024)
        except Exception:
            return 0.0

    @staticmethod
    def _get_cpu_percent():
        """Return current process CPU utilisation (0.0 – 100.0 * cores), or 0.0."""
        if not _HAVE_PSUTIL:
            return 0.0
        try:
            proc = _psutil.Process()
            return proc.cpu_percent(interval=None)
        except Exception:
            return 0.0

    @staticmethod
    def _get_gpu_info():
        """Return a dict of GPU stats, or an empty dict if unavailable.

        Tries `nvidia-smi` first, then returns {} on any failure.
        The dict has keys like 'gpu_util_pct' and 'mem_used_mb'.
        """
        import subprocess as _sp
        try:
            cmd = [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used",
                "--format=csv,noheader,nounits",
            ]
            proc = _sp.run(cmd, capture_output=True, text=True, timeout=5)
            if proc.returncode != 0:
                return {}
            line = proc.stdout.strip().split("\n")[0]
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                return {
                    "gpu_util_pct": float(parts[0]) if parts[0] else 0.0,
                    "mem_used_mb": float(parts[1]) if parts[1] else 0.0,
                }
        except Exception:
            pass
        return {}

    # ------------------------------------------------------------------
    # Stage context manager
    # ------------------------------------------------------------------

    def stage(self, stage_name):
        """Return a context manager that times and profiles a stage.

        Usage:
            profiler = InferenceProfiler()
            with profiler.stage("stage1_punctuation") as ctx:
                result = do_work()
            print(f"Stage took {ctx.elapsed_ms:.1f}ms")
        """
        return _StageContext(self, stage_name)

    # ------------------------------------------------------------------
    # Profile pipeline
    # ------------------------------------------------------------------

    def profile_pipeline(self, text, lane="text", **kwargs):
        """Run the full pipeline on `text` with profiling.

        Returns a (result: str, profile: dict) tuple.  The dict has keys:
          • total_ms — wall-clock duration of the entire pipeline
          • stages   — list of per-stage timing dicts
          • metadata — lane, char_count, etc.

        If the profiler is disabled, returns (result, {}) and the pipeline
        runs with zero overhead.
        """
        if not self.enabled:
            try:
                from pipeline import process as pipeline_process
                return pipeline_process(text, lane=lane, **kwargs), {}
            except ImportError:
                return text, {}

        self.clear()
        char_count = len(text) if text else 0
        self._run_start = time.perf_counter()

        # Handoff: the moment raw STT text is handed to the pipeline.
        with self.stage("stt_to_stage1_handoff"):
            pass  # The actual handoff is the transition into process() below.

        try:
            from pipeline import get_orchestrator
            orch = get_orchestrator()
            result = orch.process(text, lane=lane, profiler=self, **kwargs)
        except ImportError:
            result = text

        total_ms = (time.perf_counter() - self._run_start) * 1000.0

        return result, {
            "total_ms": round(total_ms, 3),
            "stages": [s.to_dict() for s in self.stage_timings],
            "metadata": {
                "lane": lane,
                "char_count": char_count,
                "enabled": self.enabled,
            },
        }

    # ------------------------------------------------------------------
    # Profile transcription
    # ------------------------------------------------------------------

    def profile_transcription(self, audio, sample_rate=16000, **kwargs):
        """Profile a transcription run.

        `audio` is a float32 numpy array of audio samples.  This method does
        NOT actually transcribe — it returns a timing breakdown dict that the
        caller can use alongside a real STT run.  (The STT path in mumble.py
        is the one that records actual timing.)

        Returns a profile dict suitable for feeding into Diagnostics.
        """
        if not self.enabled:
            return {}

        audio_len = len(audio) if audio is not None else 0
        audio_duration_sec = audio_len / max(1, sample_rate)

        self.clear()
        self._run_start = time.perf_counter()

        # The caller records their own STT timing; we just provide the
        # audio metadata and an elapsed counter the caller can fill in.
        profile = {
            "audio_duration_sec": round(audio_duration_sec, 3),
            "sample_rate": sample_rate,
            "audio_samples": audio_len,
            "stt_elapsed_ms": 0.0,    # caller fills this in
            "timestamp": time.time(),
        }
        return profile

    # ------------------------------------------------------------------
    # Summary / reporting
    # ------------------------------------------------------------------

    def summary(self):
        """Return a dict summary of all recorded stage timings.

        Keys:
          • total_ms — sum of all stage elapsed times
          • num_stages — how many stages were recorded
          • stages — list of per-stage timing dicts
          • slowest — name + ms of the slowest stage (or None)
        """
        timings = self.stage_timings
        total = sum(t.elapsed_ms for t in timings)
        slowest = max(timings, key=lambda t: t.elapsed_ms) if timings else None
        return {
            "total_ms": round(total, 3),
            "num_stages": len(timings),
            "stages": [t.to_dict() for t in timings],
            "slowest": slowest.to_dict() if slowest else None,
        }

    def stage_breakdown_ms(self):
        """Return a {stage_name: elapsed_ms} dict for all recorded stages."""
        return {t.stage_name: round(t.elapsed_ms, 3) for t in self.stage_timings}


# ===========================================================================
# _StageContext — context manager returned by profiler.stage()
# ===========================================================================

class _StageContext:
    """Internal context manager that records timing around a block.

    Returned by `InferenceProfiler.stage()`.  After exiting, the recorded
    `StageTiming` is appended to the profiler's list.

    Attributes available after __exit__:
        stage_name, elapsed_ms, mem_before_mb, mem_after_mb, cpu_percent,
        gpu_info.
    """

    def __init__(self, profiler, stage_name):
        self._profiler = profiler
        self.stage_name = stage_name
        self.elapsed_ms = 0.0
        self.mem_before_mb = 0.0
        self.mem_after_mb = 0.0
        self.cpu_percent = 0.0
        self.gpu_info = {}

    def __enter__(self):
        if not self._profiler.enabled:
            return self
        self.mem_before_mb = (self._profiler._get_memory_mb()
                              if self._profiler._track_memory else 0.0)
        # Warm up the CPU measurement.
        if self._profiler._track_cpu:
            self._profiler._get_cpu_percent()
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args):
        if not self._profiler.enabled:
            return
        self.elapsed_ms = (time.perf_counter() - self._start) * 1000.0
        self.mem_after_mb = (self._profiler._get_memory_mb()
                             if self._profiler._track_memory else 0.0)
        self.cpu_percent = (self._profiler._get_cpu_percent()
                            if self._profiler._track_cpu else 0.0)
        self.gpu_info = (self._profiler._get_gpu_info()
                         if self._profiler._track_gpu else {})
        timing = StageTiming(
            stage_name=self.stage_name,
            elapsed_ms=self.elapsed_ms,
            mem_before_mb=self.mem_before_mb,
            mem_after_mb=self.mem_after_mb,
            cpu_percent=self.cpu_percent,
            gpu_info=self.gpu_info,
        )
        with self._profiler._lock:
            self._profiler._stage_timings.append(timing)


# ===========================================================================
# Module-level convenience
# ===========================================================================

_default_profiler = None


def get_profiler():
    """Return (or create) the module-level InferenceProfiler singleton."""
    global _default_profiler
    if _default_profiler is None:
        _default_profiler = InferenceProfiler()
    return _default_profiler
