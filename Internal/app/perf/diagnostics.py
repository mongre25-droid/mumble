#!/usr/bin/env python3
"""Performance diagnostics — enhanced RTF monitor, memory pressure detection,
and bottleneck identification.

Extends the simpler RTF diagnostics in `transcription.py` with:
  • Per-stage RTF breakdown (which stage contributes how much to total latency)
  • Memory pressure detection with configurable thresholds
  • Bottleneck identification — which stage is the slowest?
  • Actionable recommendations based on profiling data

DESIGN
  • `Diagnostics` is the main class — instantiate with optional thresholds.
  • All methods return plain dicts suitable for logging, API responses, and UI.
  • Memory detection tries psutil first, then ctypes (Windows), then returns
    a safe "unknown" sentinel.
  • RTF thresholds mirror those in transcription.py for consistency.

USAGE

    from perf.diagnostics import Diagnostics

    d = Diagnostics()

    # Check system memory:
    mem = d.check_memory_pressure()
    if mem["under_pressure"]:
        print(f"Warning: only {mem['free_memory_mb']} MB free")

    # Enhanced RTF with per-stage breakdown:
    rtf = d.monitor_rtf(
        audio_duration_sec=5.0,
        total_processing_ms=2555,
        stage_breakdown_ms={
            "stt": 500, "stage1_punct": 50,
            "stage2_grammar": 1200, "stage3_format": 800,
            "stage4_cleanup": 5,
        },
    )

    # Find the bottleneck:
    bottleneck = d.identify_bottleneck(stage_breakdown_ms)
    print(f"Slowest: {bottleneck['slowest_stage']} at {bottleneck['slowest_ms']}ms")

    # Full pipeline analysis:
    analysis = d.analyze_pipeline_run(
        audio_duration_sec=5.0,
        stage_breakdown_ms=stage_breakdown_ms,
    )
    for rec in analysis["recommendations"]:
        print(f"  → {rec}")
"""

import os
import sys
import time

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


# ---------------------------------------------------------------------------
# RTF thresholds — mirror transcription.py
# ---------------------------------------------------------------------------

RTF_HEALTHY = 0.5       # below this is great
RTF_OK = 1.0            # below this is acceptable
RTF_MARGINAL = 1.5      # below this is a mild concern
RTF_SLOW = 2.0          # above this is a hardware-level concern

# Defaults for memory pressure.
DEFAULT_MEMORY_THRESHOLD_MB = 500     # warn when free RAM drops below this
DEFAULT_MEMORY_BUDGET_MB = 2048       # total pipeline budget (single-resident)
DEFAULT_MEMORY_WARN_PERCENT = 80      # warn when pipeline uses >80% of budget


# ===========================================================================
# Diagnostics
# ===========================================================================

class Diagnostics:
    """Real-time performance diagnostics for the Mumble pipeline.

    Parameters:
        memory_threshold_mb: Free RAM below which memory pressure is flagged.
        memory_budget_mb:    Total RAM budget for the pipeline (single-resident).
        memory_warn_percent: Percentage of budget that triggers a warning.
        use_ctypes_fallback: When psutil is absent, try Windows ctypes to read
                             free RAM.  Set False to suppress the fallback.
    """

    def __init__(self, memory_threshold_mb=None, memory_budget_mb=None,
                 memory_warn_percent=None, use_ctypes_fallback=True):
        self.memory_threshold_mb = (
            memory_threshold_mb or DEFAULT_MEMORY_THRESHOLD_MB
        )
        self.memory_budget_mb = (
            memory_budget_mb or DEFAULT_MEMORY_BUDGET_MB
        )
        self.memory_warn_percent = (
            memory_warn_percent or DEFAULT_MEMORY_WARN_PERCENT
        )
        self._use_ctypes_fallback = use_ctypes_fallback

    # ------------------------------------------------------------------
    # Memory pressure detection
    # ------------------------------------------------------------------

    def _get_free_memory_mb(self):
        """Return the approximate free system RAM in MB, or None if unknown.

        Tries (in order):
          1. psutil.virtual_memory().available
          2. Windows GlobalMemoryStatusEx via ctypes
          3. Returns None (unknown)
        """
        if _HAVE_PSUTIL:
            try:
                mem = _psutil.virtual_memory()
                return mem.available / (1024 * 1024)
            except Exception:
                pass

        if sys.platform == "win32" and self._use_ctypes_fallback:
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32

                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]

                mem_status = MEMORYSTATUSEX()
                mem_status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                if kernel32.GlobalMemoryStatusEx(ctypes.byref(mem_status)):
                    return mem_status.ullAvailPhys / (1024 * 1024)
            except Exception:
                pass

        return None

    def _get_process_memory_mb(self):
        """Return current process RSS in MB, or None if unavailable."""
        if _HAVE_PSUTIL:
            try:
                proc = _psutil.Process()
                return proc.memory_info().rss / (1024 * 1024)
            except Exception:
                pass
        return None

    def check_memory_pressure(self):
        """Check whether the system is under memory pressure.

        Returns a dict with:
          • under_pressure: bool — True when free RAM < threshold
          • free_memory_mb: float or None — approximate free RAM
          • process_memory_mb: float or None — current process RSS
          • threshold_mb: float — the configured threshold
          • budget_mb: float — the configured memory budget
          • budget_used_pct: float or None — percentage of budget used
          • budget_warning: bool — True when budget used > warn threshold
          • message: str — human-readable status
        """
        free_mb = self._get_free_memory_mb()
        proc_mb = self._get_process_memory_mb()

        under_pressure = (
            free_mb is not None and free_mb < self.memory_threshold_mb
        )

        budget_used_pct = None
        budget_warning = False
        if proc_mb is not None and self.memory_budget_mb > 0:
            budget_used_pct = (proc_mb / self.memory_budget_mb) * 100.0
            budget_warning = budget_used_pct > self.memory_warn_percent

        # Build message.
        if under_pressure:
            message = (
                f"Memory pressure: only {free_mb:.0f} MB free "
                f"(threshold: {self.memory_threshold_mb} MB). "
                "Consider unloading models or closing other apps."
            )
        elif budget_warning:
            message = (
                f"Pipeline using {budget_used_pct:.0f}% of memory budget "
                f"({proc_mb:.0f}/{self.memory_budget_mb} MB). "
                "Approaching single-resident limit."
            )
        elif free_mb is not None:
            message = (
                f"Memory OK: {free_mb:.0f} MB free, "
                f"process using {proc_mb:.0f} MB" if proc_mb is not None
                else f"Memory OK: {free_mb:.0f} MB free"
            )
        else:
            message = "Memory status unknown (psutil not available)"

        return {
            "under_pressure": under_pressure,
            "free_memory_mb": round(free_mb, 1) if free_mb is not None else None,
            "process_memory_mb": round(proc_mb, 1) if proc_mb is not None else None,
            "threshold_mb": self.memory_threshold_mb,
            "budget_mb": self.memory_budget_mb,
            "budget_used_pct": (
                round(budget_used_pct, 1) if budget_used_pct is not None else None
            ),
            "budget_warning": budget_warning,
            "message": message,
        }

    # ------------------------------------------------------------------
    # Enhanced RTF monitor (per-stage breakdown)
    # ------------------------------------------------------------------

    @staticmethod
    def _rtf_verdict(rtf):
        """Classify an RTF value into a label + warning flag."""
        if rtf <= RTF_HEALTHY:
            return "healthy", False
        if rtf <= RTF_OK:
            return "ok", False
        if rtf <= RTF_MARGINAL:
            return "marginal", True
        if rtf <= RTF_SLOW:
            return "slow", True
        return "very_slow", True

    def monitor_rtf(self, audio_duration_sec, total_processing_ms,
                    stage_breakdown_ms=None):
        """Produce an enhanced RTF report with per-stage breakdown.

        Args:
            audio_duration_sec: Length of the audio in seconds.
            total_processing_ms: Total wall-clock processing time in ms.
            stage_breakdown_ms: Dict of {stage_name: elapsed_ms} for each stage.
                                If provided, the report includes a per-stage
                                RTF contribution and the slowest stage.

        Returns a dict with:
          • rtf: float — overall RTF
          • verdict: str — "healthy" | "ok" | "marginal" | "slow" | "very_slow"
          • warning: bool — whether a UI warning should be shown
          • audio_duration_sec: float
          • total_processing_ms: float
          • stage_breakdown: dict — per-stage {name: {ms, rtf_contribution}}
            (only when stage_breakdown_ms is provided)
          • slowest_stage: str or None — name of the slowest stage
        """
        stage_breakdown_ms = stage_breakdown_ms or {}

        if audio_duration_sec <= 0:
            rtf = float("inf")
        else:
            rtf = total_processing_ms / (audio_duration_sec * 1000.0)

        verdict, warning = self._rtf_verdict(rtf)

        report = {
            "rtf": round(rtf, 3) if rtf != float("inf") else float("inf"),
            "verdict": verdict,
            "warning": warning,
            "audio_duration_sec": round(audio_duration_sec, 3),
            "total_processing_ms": round(total_processing_ms, 3),
        }

        # Per-stage breakdown: each stage's contribution to total RTF.
        if stage_breakdown_ms:
            per_stage = {}
            for name, ms in stage_breakdown_ms.items():
                contribution = ms / (audio_duration_sec * 1000.0) if audio_duration_sec > 0 else 0.0
                per_stage[name] = {
                    "ms": round(ms, 3),
                    "rtf_contribution": round(contribution, 4),
                    "pct_of_total": (
                        round(ms / total_processing_ms * 100, 1)
                        if total_processing_ms > 0 else 0.0
                    ),
                }
            report["stage_breakdown"] = per_stage

            # Slowest stage.
            slowest = max(stage_breakdown_ms, key=stage_breakdown_ms.get)
            report["slowest_stage"] = slowest
        else:
            report["slowest_stage"] = None

        return report

    # ------------------------------------------------------------------
    # Bottleneck identification
    # ------------------------------------------------------------------

    def identify_bottleneck(self, stage_breakdown_ms):
        """Identify the slowest pipeline stage and quantify its impact.

        Args:
            stage_breakdown_ms: Dict of {stage_name: elapsed_ms}.

        Returns a dict with:
          • slowest_stage: str or None
          • slowest_ms: float
          • total_ms: float — sum of all stage times
          • percentage_of_total: float — what fraction of total time the
            bottleneck consumes
          • all_stages: list of (name, ms, pct) sorted slowest-first
        """
        if not stage_breakdown_ms:
            return {
                "slowest_stage": None,
                "slowest_ms": 0.0,
                "total_ms": 0.0,
                "percentage_of_total": 0.0,
                "all_stages": [],
            }

        total = sum(stage_breakdown_ms.values())
        sorted_stages = sorted(stage_breakdown_ms.items(),
                               key=lambda kv: kv[1], reverse=True)
        slowest_name, slowest_ms = sorted_stages[0]

        all_stages = [
            {
                "name": name,
                "ms": round(ms, 3),
                "pct": round(ms / total * 100, 1) if total > 0 else 0.0,
            }
            for name, ms in sorted_stages
        ]

        return {
            "slowest_stage": slowest_name,
            "slowest_ms": round(slowest_ms, 3),
            "total_ms": round(total, 3),
            "percentage_of_total": (
                round(slowest_ms / total * 100, 1) if total > 0 else 0.0
            ),
            "all_stages": all_stages,
        }

    # ------------------------------------------------------------------
    # Recommendations engine
    # ------------------------------------------------------------------

    def _generate_recommendations(self, audio_duration_sec,
                                  stage_breakdown_ms, rtf_value,
                                  memory_status, model_name="unknown",
                                  device="cpu"):
        """Generate actionable recommendations based on profiling data.

        Returns a list of human-readable recommendation strings.
        """
        recs = []
        bottleneck = self.identify_bottleneck(stage_breakdown_ms)
        slowest = bottleneck.get("slowest_stage")

        # RTF-based recommendations.
        if rtf_value > RTF_SLOW:
            recs.append(
                "Overall RTF is very high ({}). Consider switching to a "
                "smaller STT model (tiny.en) or enabling cloud STT via Groq "
                "for instant transcription.".format(round(rtf_value, 2))
            )
        elif rtf_value > RTF_MARGINAL:
            recs.append(
                "RTF is above real-time ({}). Close CPU-heavy apps or "
                "reduce the STT model size in Settings → Transcription.".format(
                    round(rtf_value, 2)
                )
            )

        # Stage-specific recommendations.
        if slowest:
            if "stt" in slowest and (stage_breakdown_ms.get("stt", 0) >
                                     audio_duration_sec * 1000 * 1.5):
                recs.append(
                    "STT is the bottleneck ({}ms). Try switching to a "
                    "smaller model (tiny.en or small.en) for faster "
                    "transcription.".format(
                        round(stage_breakdown_ms["stt"])
                    )
                )
            elif "stage2_grammar" in slowest or "grammar" in slowest:
                recs.append(
                    "Stage 2 (grammar) is the slowest stage ({}ms). "
                    "Consider disabling grammar correction on weak hardware "
                    "or switching to a smaller model.".format(
                        round(stage_breakdown_ms.get(slowest, 0))
                    )
                )
            elif "stage3_format" in slowest or "formatting" in slowest:
                recs.append(
                    "Stage 3 (formatting) is the slowest stage ({}ms). "
                    "On weak hardware, skip mode formatting by using text "
                    "mode instead of prompt/email mode.".format(
                        round(stage_breakdown_ms.get(slowest, 0))
                    )
                )
            elif "stage1" in slowest:
                recs.append(
                    "Stage 1 (punctuation) is slower than expected ({}ms). "
                    "This is normally very fast; check if another process "
                    "is competing for CPU.".format(
                        round(stage_breakdown_ms.get(slowest, 0))
                    )
                )

        # Memory pressure recommendations.
        if memory_status.get("under_pressure"):
            recs.append(
                "System memory is low ({} MB free). The largest resident "
                "model will be unloaded to free RAM. Close other apps for "
                "better performance.".format(
                    memory_status.get("free_memory_mb", "?")
                )
            )
        elif memory_status.get("budget_warning"):
            recs.append(
                "Pipeline memory usage is at {}% of the {} MB budget. "
                "Consider reducing model size or disabling optional stages.".format(
                    memory_status.get("budget_used_pct", "?"),
                    memory_status.get("budget_mb", "?")
                )
            )

        # Device-specific.
        if device == "cpu" and rtf_value > RTF_OK:
            recs.append(
                "Running on CPU. If your machine has a GPU, enable GPU "
                "acceleration in Settings → Transcription for faster STT."
            )

        return recs

    # ------------------------------------------------------------------
    # Full pipeline analysis
    # ------------------------------------------------------------------

    def analyze_pipeline_run(self, audio_duration_sec, stage_breakdown_ms,
                             model_name="unknown", device="cpu",
                             compute_type="int8"):
        """Run a complete diagnostic analysis of a pipeline run.

        Combines RTF monitoring, memory pressure detection, bottleneck
        identification, and recommendations into a single report.

        Args:
            audio_duration_sec: Length of the source audio in seconds.
            stage_breakdown_ms: Dict of {stage_name: elapsed_ms} per stage.
            model_name: STT model used (for diagnostics context).
            device: Inference device ("cpu", "cuda", "dml", etc.).
            compute_type: Quantization type ("int8", "float16", etc.).

        Returns a dict with:
          • rtf_report — enhanced RTF breakdown
          • memory_status — memory pressure check
          • bottleneck — slowest stage analysis
          • recommendations — actionable advice list
          • metadata — model, device, compute_type, timestamp
        """
        total_ms = sum(stage_breakdown_ms.values()) if stage_breakdown_ms else 0.0

        rtf_report = self.monitor_rtf(
            audio_duration_sec=audio_duration_sec,
            total_processing_ms=total_ms,
            stage_breakdown_ms=stage_breakdown_ms,
        )

        memory_status = self.check_memory_pressure()

        bottleneck = self.identify_bottleneck(stage_breakdown_ms)

        recommendations = self._generate_recommendations(
            audio_duration_sec=audio_duration_sec,
            stage_breakdown_ms=stage_breakdown_ms,
            rtf_value=rtf_report["rtf"],
            memory_status=memory_status,
            model_name=model_name,
            device=device,
        )

        return {
            "rtf_report": rtf_report,
            "memory_status": memory_status,
            "bottleneck": bottleneck,
            "recommendations": recommendations,
            "metadata": {
                "model": model_name,
                "device": device,
                "compute_type": compute_type,
                "timestamp": time.time(),
            },
        }


# ===========================================================================
# Module-level convenience
# ===========================================================================

_default_diagnostics = None


def get_diagnostics():
    """Return (or create) the module-level Diagnostics singleton."""
    global _default_diagnostics
    if _default_diagnostics is None:
        _default_diagnostics = Diagnostics()
    return _default_diagnostics
